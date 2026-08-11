#!/usr/bin/env python3
"""Turn one extraction record into the Label Studio tasks that review it.

One emitter per task kind, all producing the same envelope: every task in a
project carries every key that project's config reads, with exactly one gate
non-empty. The envelope is built from `config.contract()` rather than assembled by
hand, so a key the config adds appears here as an empty array rather than as a
task the editor cannot render.

Two identifiers do the regeneration work, and the distinction between them is what
makes review survive a corrected record:

    review_key    the ADDRESS   paper|kind|class|local_id|slot
    content_hash  WHAT WAS ASKED, a digest of the answer-bearing payload only

Same address and hash means the answer stands. A changed hash re-asks. A vanished
address orphans the answer. The hash deliberately excludes descriptors, rendered
prose and offsets, so correcting a `Group.name` does not re-ask a dozen questions
whose substance did not move.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
import record as record_module
import spans as span_tools
import spec
import tables
import text_index
from record import Record

#: Below this, a contrast task says so: the record and the parsed analysis it was
#: matched to do not agree on every token of their names, so the marked rows may be
#: another contrast's. Every link across the baseline papers scores exactly 1.0,
#: which is why the score is printed only when it is not -- a line reading
#: "name match 1.00" on every task is a diagnostic nobody can act on.
WEAK_MATCH = 1.0

#: Statuses meaning "no reported result rests on this".
WITHOUT_COORDINATES = frozenset({"no_table", "no_contrast", "no_coordinates"})


def digest(payload: Any) -> str:
    """The content hash: what was asked, not how it was rendered."""

    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class Report:
    counts: dict[str, int] = field(default_factory=dict)
    spans: int = 0
    choices: int = 0
    skipped: list[str] = field(default_factory=list)

    def add(self, kind: str) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def summary(self) -> str:
        per_kind = ", ".join(f"{kind} {n}" for kind, n in sorted(self.counts.items()))
        return f"{self.total} tasks ({per_kind}); {self.spans} spans, {self.choices} choices"


class Exporter:
    """Every task for one paper, in project order."""

    def __init__(
        self,
        body: Mapping[str, Any],
        normalized: str,
        paper_id: str,
        identifiers: Mapping[str, Any],
        paper_url: str,
        *,
        coordinate_counts: Mapping[str, int] | None = None,
        coordinates_only: bool = False,
    ) -> None:
        self.record = Record(body)
        self.normalized = normalized
        self.paper_id = paper_id
        self.identifiers = identifiers or {}
        self.paper_url = paper_url
        self.counts = dict(coordinate_counts or {})
        self.coordinates_only = coordinates_only

        self.text_hash = text_index.text_hash(normalized)
        self.sections = text_index.build_sections(normalized)
        self.report = Report()

        metadata = body.get("extraction_metadata") or {}
        self.model_version = spec.model_version(
            metadata.get("extractor_model", ""), metadata.get("extractor_version", "")
        )

        self.tasks: dict[str, list[dict[str, Any]]] = {p.name: [] for p in spec.PROJECTS}

        # Filled by `load_tables`, which is optional: a paper with no synced pubget
        # source still exports every other family.
        self.pubget_dir: Path | None = None
        self.manifest: dict[str, dict[str, Any]] = {}
        self.parsed: dict[str, list[dict[str, Any]]] = {}
        self.local_of: dict[str, str] = {}
        self.pubget_of: dict[str, list[str]] = {}
        self.links = tables.Links()
        self._rendered: dict[str, Any] = {}

    # -- the envelope ------------------------------------------------------

    def _blank(self, kind: spec.Kind) -> dict[str, Any]:
        """A task of this kind with every contracted key present and empty.

        Building outwards from the contract rather than listing keys per emitter is
        what keeps the two in step: the previous exporter had to remember to write
        `entities=[], entity_table=[], entity_rows=[], model=[], terms=[]` into
        every structure task, and a key it forgot rendered as a missing block with
        no error.
        """

        project = spec.PROJECT_OF[kind.name]
        data = {
            key: config.default_for(shape) for key, shape in config.contract(project).items()
        }
        data.update(
            paper_id=self.paper_id,
            stage=kind.stage,
            task_kind=kind.name,
            paper_url=self.paper_url,
            paper_title=self.paper_id,
            paper_citation=self._citation(),
            # Deliberately outside the content hash: a re-staged text does not
            # change the question, it changes where the answer's evidence lives.
            # Carrying it makes `data` differ, so the sync takes its "display
            # refreshed" branch -- answers kept, predictions rewritten -- instead of
            # the unchanged short-circuit that would leave every stored offset
            # addressing a text that is no longer served.
            paper_text_hash=self.text_hash,
            priority="n/a",
            coordinate_status="not_applicable",
        )
        return data

    def _emit(
        self,
        kind: spec.Kind,
        address: str,
        asked: Any,
        data: dict[str, Any],
        results: list[dict[str, Any]] | None = None,
    ) -> None:
        data["review_key"] = f"{self.paper_id}|{kind.name}|{address}"
        data["content_hash"] = digest(asked)
        task: dict[str, Any] = {"data": data}
        if results:
            task["predictions"] = [
                {"model_version": self.model_version, "result": results}
            ]
        self.tasks[spec.PROJECT_OF[kind.name].name].append(task)
        self.report.add(kind.name)

    def _citation(self) -> str:
        parts = [self.paper_id]
        if self.identifiers.get("pmid"):
            parts.append(f"pmid {self.identifiers['pmid']}")
        if self.identifiers.get("doi"):
            parts.append(str(self.identifiers["doi"]))
        return "  ·  ".join(parts)

    # -- predictions -------------------------------------------------------

    def _spans(self, control: str, sets: list[Any], label: str) -> list[dict[str, Any]]:
        results = []
        for set_index, evidence_set in enumerate(sets):
            for span_index, span in enumerate(evidence_set.get("spans", [])):
                # A task must never ship an offset that does not address the text we
                # are about to serve.
                span_tools.verify(self.normalized, span)
                results.append(
                    {
                        "id": f"{control}_{set_index}_{span_index}",
                        "from_name": control,
                        "to_name": spec.PAPER,
                        "type": "labels",
                        "value": {
                            "start": span["start_char"],
                            "end": span["end_char"],
                            "text": span["text"],
                            "labels": [label],
                        },
                    }
                )
                self.report.spans += 1
        return results

    def _choice(self, control: str, values: list[str]) -> dict[str, Any]:
        self.report.choices += 1
        return {
            "from_name": control,
            "to_name": spec.PAPER,
            "type": "choices",
            "value": {"choices": values},
        }

    def _number(self, control: str, value: int) -> dict[str, Any]:
        self.report.choices += 1
        return {
            "from_name": control,
            "to_name": spec.PAPER,
            "type": "number",
            "value": {"number": value},
        }

    @staticmethod
    def _palette(index: int) -> str:
        return spec.PALETTE[index % len(spec.PALETTE)]

    #: Extraction stores the source's wording; these controls offer a fixed
    #: vocabulary. Matching is on a normalized stem, so "between subjects",
    #: "between-subject" and "Between Subject" all land on one value. A wording the
    #: map cannot place is left unselected rather than guessed -- a wrong
    #: pre-selection is worse than none, because the reviewer may not re-read it.
    _TERM_TYPES = {"categor": "categorical", "continu": "continuous"}
    _VARIATION = {
        "within": "within_subject",
        "between": "between_subject",
        "both": "both",
        "unstated": "unstated",
        "not_reported": "unstated",
    }

    @staticmethod
    def _match(value: str, table: dict[str, str]) -> str | None:
        folded = value.strip().lower().replace("-", " ").replace("_", " ")
        for stem, canonical in table.items():
            if stem.replace("_", " ") in folded:
                return canonical
        return None

    # -- value -------------------------------------------------------------

    def emit_value(self) -> None:
        """One task per field of one entity, including fields marked not_reported.

        Per field rather than per entity: an entity task bundles 13-25 judgements
        behind a single verdict, so a reviewer either accepts all of them at once or
        opens a long form, and the answer needs an index path to address. One field
        is one decision.

        A `not_reported` field gets a task of its own because "the paper does state
        this" is a finding the extractor cannot make about itself.

        No evidence is quoted. Whatever the extractor found is already highlighted
        in the paper pane, where the sentences either side of it are what settle
        whether it supports the value -- and an excerpt hides exactly those.
        """

        kind = spec.BY_NAME["value"]
        for found in self.record.fields:
            if found.structural:
                continue

            value = (
                record_module.display(found.node["value"])
                if "value" in found.node
                else record_module.NOT_REPORTED
            )
            section = ""
            if found.sets:
                first = found.sets[0]["spans"][0]["start_char"]
                section = text_index.section_path(self.sections, first) or ""
            span_total = sum(len(s.get("spans", [])) for s in found.sets)

            # Two labels, one per kind of support, rather than one per evidence set.
            # Direct vs inferred is a property of the passage and the reviewer
            # decides it while drawing, so it belongs on the label; a perRegion
            # control stays hidden until a span is clicked, which is too late.
            predicted = (
                "direct support"
                if found.node.get("value_source") == "reported"
                else "inferred support"
            )
            results = self._spans(
                spec.instance(kind.name, "spans"), found.sets, predicted
            )

            handle = (
                f"{found.class_name} {found.local_id}" if found.local_id else found.class_name
            )
            description = (found.attribute.get("description") or "").strip().split(". ")[0][:90]
            data = self._blank(kind)
            data.update(
                entity_class=found.class_name,
                local_id=found.local_id,
                field_path=found.path,
                priority=found.priority,
                llm_status=found.status,
                evidence_status=found.evidence_status,
                row_count=span_total,
                coordinate_status=self.record.object_status(found.local_id, self.counts),
                labels=[
                    {"value": name, "background": self._palette(index)}
                    for index, (name, _hint) in enumerate(spec.SUPPORT_KINDS)
                ],
                **{
                    kind.gate: [
                        {
                            "label": f"{handle}  ·  {found.path}",
                            "meta": "  ·  ".join(
                                filter(
                                    None,
                                    [
                                        description,
                                        f"{span_total} span(s)" if found.sets else "no evidence",
                                        section,
                                    ],
                                )
                            ),
                            "body": value,
                        }
                    ]
                },
            )
            self._emit(
                kind,
                f"{found.class_name}|{found.local_id}|{found.path}",
                (value, found.status, found.evidence_status),
                data,
                results,
            )

    # -- relationship ------------------------------------------------------

    def emit_relationship(self) -> None:
        """One grid per association slot: rows are source objects, columns targets.

        Judging the whole assignment at once is what makes an unused target visible
        as an empty column, which no per-source-object task can show.
        """

        kind = spec.BY_NAME["relationship"]
        by_slot: dict[tuple[str, str], list[record_module.Reference]] = {}
        for reference in self.record.references:
            if reference.structural:
                continue
            by_slot.setdefault((reference.class_name, reference.slot), []).append(reference)

        for (class_name, slot), references in by_slot.items():
            attribute = references[0].attribute
            target = record_module.target_class(self.record.classes, attribute)
            if not target:
                continue
            candidates = self.record.instances.get(target) or []
            if not candidates:
                # Reported, never silently absent: a slot with no candidates is a
                # finding about the record, and a task asserting emptiness is not.
                self.report.skipped.append(
                    f"{class_name}.{slot} -> {target}: no candidates extracted"
                )
                continue

            multivalued = bool(attribute.get("multivalued"))
            columns = [
                {"value": self.record.descriptor(target, candidate), "alias": candidate}
                for candidate in candidates
            ]
            if not multivalued:
                # A single-select needs somewhere to say "none of these". Without it
                # an unlinked source object is indistinguishable from one nobody got
                # to, and a radio cannot be cleared once clicked.
                columns.append({"value": "no link", "alias": "none"})

            rows: list[dict[str, Any]] = []
            results: list[dict[str, Any]] = []
            anomalies: list[str] = []
            for index, reference in enumerate(references):
                # The name half of the descriptor, which is what a reviewer scans;
                # the id goes underneath, where it can be matched to a highlight.
                heading = self.record.descriptor(class_name, reference.local_id).split(" -- ")[-1]
                control = spec.instance(kind.name, "row" if multivalued else "one", index)
                chosen = [t for t in reference.targets if t in candidates]
                if chosen:
                    results.append(self._choice(control, chosen))
                if attribute.get("required") and not reference.targets:
                    anomalies.append(f"- **{heading}** is required to link but has none")
                for missing in reference.targets:
                    if missing not in candidates:
                        anomalies.append(
                            f"- **{heading}** links to `{missing}`, which was never extracted"
                        )
                rows.append(
                    {"label": heading, "meta": reference.local_id, "local_id": reference.local_id}
                )

            data = self._blank(kind)
            data.update(
                rel_slot=f"{class_name}.{slot}",
                entity_class=class_name,
                row_count=len(rows),
                coordinate_status="yes"
                if any(
                    self.record.object_status(r.local_id, self.counts) == "yes"
                    for r in references
                )
                else "unrelated",
                rows=rows if multivalued else [],
                rows_single=[] if multivalued else rows,
                columns=columns,
                # Only hard anomalies, and only when there are any: an empty list
                # renders no panel rather than an empty one.
                anomalies=[{"text": "\n".join(anomalies)}] if anomalies else [],
                # The span layer's labels are not the grid's columns. "no link" is a
                # column but not a label -- no passage warrants an absence -- and a
                # label's text becomes a button, so a 77-character descriptor is
                # clipped to something that fits one.
                labels=[
                    {"value": column["value"][:60], "alias": column["alias"]}
                    for column in columns
                    if column["alias"] != "none"
                ],
                **{
                    kind.gate: [
                        {
                            "label": f"Which {target}{'s' if multivalued else ''} "
                            f"does each {class_name} use?",
                            "meta": f"{class_name}.{slot} -> {target}  ·  "
                            f"{'many' if multivalued else 'one'} per {class_name}",
                            "body": (attribute.get("description") or "").strip(),
                        }
                    ]
                },
            )
            self._emit(
                kind,
                f"{class_name}.{slot}",
                [(r.local_id, sorted(r.targets)) for r in references],
                data,
                results,
            )

    # -- entities ----------------------------------------------------------

    def emit_entities(self) -> None:
        """Stage 0. One task per class: is this the right set of instances?

        The only place an invented Group can be rejected -- a value task judges its
        fields and a relationship task judges its links, and both presuppose it
        exists.
        """

        kind = spec.BY_NAME["entities"]
        for class_name in self.record.entity_classes:
            if class_name in record_module.INVENTORY_EXCLUDED:
                continue
            ids = self.record.instances.get(class_name) or []

            legend, rows, labels, results = [], [], [], []
            for index, local_id in enumerate(ids):
                source, sets = self.record.existence_evidence(class_name, local_id)
                descriptor = self.record.descriptor(class_name, local_id)
                references = (
                    f"referenced by {self.record.inbound.get(local_id, 0)} link(s)"
                    + (f"  ·  evidence from {source}" if source else "  ·  no evidence")
                )
                legend.append(
                    {"id": local_id, "descriptor": descriptor, "references": references}
                )
                rows.append({"label": local_id, "meta": f"{descriptor}  ·  {references}"})
                labels.append({"value": local_id, "background": self._palette(index)})
                results += self._spans(
                    spec.instance(kind.name, "spans"), sets, local_id
                )

            bearing = sum(
                1 for i in ids if self.record.object_status(i, self.counts) == "yes"
            )
            data = self._blank(kind)
            data.update(
                entity_class=class_name,
                row_count=len(ids),
                coordinate_status="yes" if bearing else "unrelated",
                legend=legend,
                rows=rows,
                labels=labels,
                **{
                    kind.gate: [
                        {
                            "label": f"{class_name}  ·  "
                            + (f"{len(ids)} extracted" if ids else "none extracted"),
                            "meta": f"{bearing} tied to a reported result",
                            "body": "",
                        }
                    ]
                },
            )
            self._emit(kind, class_name, sorted(ids), data, results)

    # -- model -------------------------------------------------------------

    def emit_model(self) -> None:
        """One task per ModelEstimation: is this the right term list?

        Per model rather than per analysis. On the measured records a model serves
        up to four analyses, so reviewing its terms per analysis would review them
        four times.
        """

        kind = spec.BY_NAME["model"]
        for local_id, model in self.record.models().items():
            rows, labels, results = [], [], []
            for index, term in enumerate(model.get("terms") or []):
                if not isinstance(term, Mapping):
                    continue
                name = record_module.unwrap(term.get("name")) or term.get("local_id", "?")
                levels = [lv for lv in term.get("levels") or [] if isinstance(lv, Mapping)]

                # Only the facts no control on the card repeats. The card carries the
                # name, the type and the scope as controls, so restating them here
                # said the same thing three ways.
                facts = [
                    record_module.unwrap(term.get("source_definition")),
                    f"unit {record_module.unwrap(term.get('unit'))}"
                    if record_module.unwrap(term.get("unit"))
                    else "",
                    f"{len(levels)} level(s) declared" if levels else "",
                ]
                rows.append(
                    {
                        "label": name,
                        "meta": "  ·  ".join(filter(None, facts))
                        or "no definition, unit or levels recorded",
                        "local_id": term.get("local_id", ""),
                        "levels": [
                            {"label": record_module.unwrap(lv.get("level")) or "(unnamed)",
                             "level": record_module.unwrap(lv.get("level")) or "(unnamed)"}
                            for lv in levels
                        ],
                    }
                )
                labels.append({"value": f"term: {name}", "background": self._palette(index)})

                node = term.get("name") if isinstance(term.get("name"), Mapping) else {}
                results += self._spans(
                    spec.instance(kind.name, "spans"),
                    ((node.get("evidence") or {}).get("sets")) or [],
                    f"term: {name}",
                )

                matched = self._match(record_module.unwrap(term.get("type")), self._TERM_TYPES)
                if matched:
                    results.append(
                        self._choice(spec.instance(kind.name, "type", index), [matched])
                    )
                matched = self._match(
                    record_module.unwrap(term.get("variation_level")), self._VARIATION
                )
                if matched:
                    results.append(
                        self._choice(spec.instance(kind.name, "scope", index), [matched])
                    )
                for level_index, level in enumerate(levels):
                    # `order` arrives as an extraction node, not a bare number, so it
                    # is unwrapped before it can be pre-filled -- and a non-numeric
                    # one is left unselected rather than guessed at.
                    try:
                        number = int(float(record_module.unwrap(level.get("order"))))
                    except (TypeError, ValueError):
                        continue
                    results.append(
                        self._number(
                            spec.instance(kind.name, "order", index, level_index), number
                        )
                    )

            # The stage this model was fitted on belongs on the card: its terms are
            # this model's too, so a reviewer judging whether the list is complete
            # has to know which columns are already accounted for below.
            inputs = [i for i in model.get("inputs_from") or [] if isinstance(i, str)]
            facts = [
                record_module.unwrap(model.get("model_family")),
                record_module.unwrap(model.get("model_type")),
                record_module.unwrap(model.get("estimator")),
                record_module.unwrap(model.get("software")),
                record_module.unwrap(model.get("level")),
                f"fitted on {', '.join(inputs)}" if inputs else "",
            ]
            data = self._blank(kind)
            data.update(
                local_id=local_id,
                entity_class="ModelEstimation",
                row_count=len(rows),
                coordinate_status=self.record.model_status(local_id, self.counts),
                rows=rows,
                labels=labels,
                **{
                    kind.gate: [
                        {
                            "label": f"{local_id}  ·  {len(rows)} terms",
                            "meta": "  ·  ".join(filter(None, facts)),
                            "body": "",
                        }
                    ]
                },
            )
            self._emit(
                kind,
                local_id,
                [(r["local_id"], r["label"], [lv["level"] for lv in r["levels"]]) for r in rows],
                data,
                results,
            )

    # -- the coordinate tables ---------------------------------------------

    def load_tables(
        self, pubget_dir: Path | None, stage1: Path | None, table_map: Path | None
    ) -> None:
        """The coordinate tables and the stage-1 split, for the contrast project.

        Every failure is reported and none raises. A paper whose pubget source was
        never synced still exports value, relationship and structure tasks; it
        simply gets no table tasks, and its contrast tasks say so on the face of the
        task rather than rendering an empty grid that reads like an analysis with no
        results.
        """

        if pubget_dir is None or not Path(pubget_dir).is_dir():
            self.report.skipped.append(
                f"no pubget source at {pubget_dir}: no table tasks, and contrast tasks "
                "carry no grid"
            )
            return
        self.pubget_dir = Path(pubget_dir)
        self.manifest = tables.read_manifest(self.pubget_dir.parent.parent)
        if not self.manifest:
            self.report.skipped.append(
                f"no tables.jsonl under {self.pubget_dir.parent.parent}: no table tasks"
            )

        if stage1 and Path(stage1).is_file():
            self.parsed = tables.load_stage1(Path(stage1))
        else:
            self.report.skipped.append(
                f"no stage-1 parse at {stage1}: no table tasks, and no rows can be "
                "attributed to a contrast"
            )

        if table_map and Path(table_map).is_file():
            self.local_of = json.loads(Path(table_map).read_text(encoding="utf-8"))
            for pubget_id, local_id in self.local_of.items():
                self.pubget_of.setdefault(local_id, []).append(pubget_id)
        elif self.parsed:
            self.report.skipped.append(
                f"no table map at {table_map}: a contrast cannot be tied to its table"
            )

        encoded = {
            analysis.get("local_id", ""): (
                record_module.unwrap(analysis.get("name")),
                [t for t in analysis.get("tables") or [] if isinstance(t, str)],
            )
            for analysis in self.record.analyses()
        }
        self.links = tables.link_analyses(encoded, self.parsed, self.local_of)
        for local_id in self.links.unmatched_records:
            self.report.skipped.append(
                f"contrast {local_id}: no parsed analysis matched, so no rows are marked"
            )
        for table_id, position in self.links.unmatched_siblings:
            name = self.parsed[table_id][position].get("name") or "(unnamed)"
            self.report.skipped.append(
                f"table {table_id} analysis #{position + 1} ({name}): parsed but never "
                "encoded -- the missed_analysis case"
            )

    def _table(self, table_id: str):
        """One rendered table, cached: a contrast task and its table task share it."""

        if table_id not in self._rendered:
            entry = self.manifest.get(table_id)
            self._rendered[table_id] = (
                tables.read_table(
                    self.pubget_dir,
                    entry["data_file"],
                    label=entry["table_label"],
                    caption=entry["caption"],
                )
                if entry and self.pubget_dir and entry.get("data_file")
                else None
            )
        return self._rendered[table_id]

    def _sibling_meta(self, table_id: str, position: int, owner: Mapping[int, int]) -> str:
        rows = sum(1 for holder in owner.values() if holder == position)
        points = len(self.parsed[table_id][position].get("points") or [])
        encoded = [
            local_id
            for local_id, (tid, pos, _score) in self.links.matched.items()
            if tid == table_id and pos == position
        ]
        return "  ·  ".join(
            [
                f"{points} point(s)",
                f"{rows} row(s) attributed",
                f"encoded as {encoded[0]}" if encoded else "NOT ENCODED as any analysis",
            ]
        )

    def emit_table(self) -> None:
        """Stage 0 over one coordinate table: is this the right set of analyses?

        The judgement nothing else in the pipeline can make. Stage 1 splits each
        table into analyses with one model call, and that split is where
        over-splitting, merging, missed analyses and misattributed rows happen --
        but every downstream task addresses an analysis that already exists, so none
        of them can say the set is wrong.

        Emitted for every table any analysis references as well as every table stage
        1 parsed, so a coordinate table the parser found nothing in still gets asked
        about. That is the `not_analyses` and `missed_analysis` case, and skipping it
        would make the two indistinguishable from a table nobody looked at.
        """

        kind = spec.BY_NAME["table"]
        referenced = {
            pubget_id
            for analysis in self.record.analyses()
            for local_id in analysis.get("tables") or []
            for pubget_id in self.pubget_of.get(local_id, [])
        }
        for table_id in sorted(set(self.parsed) | referenced):
            table = self._table(table_id)
            siblings = self.parsed.get(table_id, [])
            owner, contested = tables.attribute_rows(table, siblings)
            names = [s.get("name") or "(unnamed)" for s in siblings]
            local_id = self.local_of.get(table_id, table_id)
            entry = self.manifest.get(table_id) or {}

            data = self._blank(kind)
            data.update(
                local_id=local_id,
                entity_class="Table",
                table_id=table_id,
                row_count=len(siblings),
                coordinate_status=(
                    "yes" if any(s.get("points") for s in siblings)
                    else "no_coordinates" if siblings else "no_contrast"
                ),
                table_html=tables.render_table_html(
                    table, owner=owner, contested=contested, missing=local_id
                ),
                # No trailing "+ new analysis" slot: one label cannot stand for two
                # missed analyses, and both spans came back wearing it. The control
                # is a naming layer, so a reviewer types the name instead.
                labels=[
                    {"value": f"analysis: {name}", "background": self._palette(index)}
                    for index, name in enumerate(names)
                ],
                rows=[
                    {
                        "label": f"#{index + 1}  ·  {name}",
                        "meta": self._sibling_meta(table_id, index, owner),
                    }
                    for index, name in enumerate(names)
                ],
                **{
                    kind.gate: [
                        {
                            "label": f"{entry.get('table_label') or local_id}"
                            f"  ·  {len(siblings)} "
                            f"analys{'is' if len(siblings) == 1 else 'es'} parsed",
                            # Not the caption: the grid below prints it, and printing
                            # it twice on one screen is most of what made this task
                            # long. What a reviewer needs before scanning is what the
                            # attribution could not settle.
                            "meta": tables.attribution_note(table, siblings, owner, contested),
                            "body": "",
                        }
                    ]
                },
            )
            # The split and the rows it rests on, and nothing else. Re-rendering the
            # grid or fixing a caption must not re-ask a segmentation question whose
            # substance did not move.
            self._emit(
                kind,
                local_id,
                [
                    (name, sorted(row for row, holder in owner.items() if holder == index))
                    for index, name in enumerate(names)
                ],
                data,
            )

    def _contrast_grid(self, analysis: Mapping[str, Any]) -> tuple[str, str, str]:
        """(table_id, rendered grid, the line naming which parse this came off).

        An analysis with no matched parse still gets its table, unhighlighted, and a
        line saying so. Rendering nothing would be indistinguishable from an analysis
        whose rows simply were not found.
        """

        local_id = analysis.get("local_id", "")
        matched = self.links.matched.get(local_id)
        if matched:
            table_id, position, score = matched
            table = self._table(table_id)
            sibling = self.parsed[table_id][position]
            # The resolved attribution, not raw coordinate matching. Both views of one
            # table must agree: a paper reporting the same peak under two contrasts
            # makes raw matching claim five rows here while the table task -- which
            # resolves inside section blocks -- attributes three, and a reviewer shown
            # both would have no way to tell which was lying.
            owner, contested = tables.attribute_rows(table, self.parsed.get(table_id) or [])
            rows = [row for row, holder in owner.items() if holder == position]
            shared = {row: holders for row, holders in contested.items() if position in holders}

            note = "" if table else "The table could not be read."
            if table and not rows:
                note = (
                    "No row was attributed to this analysis. Either the parser missed "
                    "them or this contrast is reported somewhere other than this table."
                )
            label = (self.manifest.get(table_id) or {}).get("table_label") or table_id
            line = (
                f"{label} · analysis {position + 1} of {len(self.parsed[table_id])} · "
                f"{len(sibling.get('points') or [])} points"
            )
            if score < WEAK_MATCH:
                line += f" · weak name match {score:.2f}, check the rows are this contrast's"
            return (
                table_id,
                tables.render_table_html(
                    table, highlight=rows, contested=shared, note=note, missing=local_id
                ),
                line,
            )

        referenced = [t for t in analysis.get("tables") or [] if isinstance(t, str)]
        pubget_ids = [pid for t in referenced for pid in self.pubget_of.get(t, [])]
        table_id = pubget_ids[0] if pubget_ids else ""
        return (
            table_id,
            tables.render_table_html(
                self._table(table_id) if table_id else None,
                note="No parsed analysis matched this record, so no row is marked.",
                missing=referenced[0] if referenced else "this analysis",
            ),
            "No stage-1 analysis matched this record: the rows it rests on are unknown, "
            "which is itself worth reporting on the table task.",
        )

    def _term_rows(self, model: Mapping[str, Any]) -> list[dict[str, Any]]:
        """One grid row per term, or per level of a categorical term.

        The inventory comes from the model's whole stage chain, so a cell can only
        ever name a term that chain declares -- the foreign key is enforced by the
        widget rather than checked afterwards.
        """

        rows: list[dict[str, Any]] = []
        for term in self.record.stage_terms(model):
            name = record_module.unwrap(term.get("name")) or term.get("local_id", "?")
            levels = term.get("levels") or []
            if levels:
                for level in levels:
                    label = record_module.unwrap(level.get("level")) if isinstance(level, Mapping) else ""
                    rows.append(
                        {
                            "label": f"{name} : {label}" if label else name,
                            "term": term.get("local_id", ""),
                            "level": label,
                        }
                    )
                continue
            kind = record_module.unwrap(term.get("type"))
            rows.append(
                {
                    "label": f"{name}  ({kind})" if kind else f"{name}  (no levels)",
                    "term": term.get("local_id", ""),
                    "level": "",
                }
            )
        return rows

    def _paraphrase(self, analysis: Mapping[str, Any], rows: list[dict[str, Any]]) -> str:
        """The record rendered back into one sentence.

        The primary judgement: accepting is one click, which is what makes 5-9
        analyses a paper affordable. Writing it also surfaces every place the record
        cannot be read back as a claim.
        """

        effect = analysis.get("effect") or {}
        cells = [c for c in effect.get("cells") or [] if isinstance(c, Mapping)]
        # Term ids resolved to the model's own names. A paraphrase reading
        # "term_sentence_condition:opaque proverb" is no more reviewable than a bare
        # id, which is the whole point of descriptors.
        names = {
            term.get("local_id", ""): record_module.unwrap(term.get("name"))
            or term.get("local_id", "")
            for term in self.record.stage_terms(
                self.record.models().get(analysis.get("model_estimation")) or {}
            )
        }
        sides: dict[str, list[str]] = {"positive": [], "negative": []}
        for cell in cells:
            direction = record_module.unwrap(cell.get("direction")).lower()
            level = record_module.unwrap(cell.get("level"))
            name = names.get(cell.get("term", ""), cell.get("term", ""))
            handle = f"{name} = {level}" if level else name
            if "pos" in direction:
                sides["positive"].append(handle)
            elif "neg" in direction:
                sides["negative"].append(handle)

        # Each side is bolded, not the whole line: wrapping a line that already
        # contains `**vs**` in another pair nests emphasis inside emphasis, which no
        # two markdown renderers agree on.
        comparison = " vs ".join(
            f"**{side}**"
            for side in (" + ".join(sides["positive"]), " + ".join(sides["negative"]))
            if side
        ) or "_(no signed cell)_"

        tested = {cell.get("term") for cell in cells}
        adjusted = sorted({names.get(r["term"], r["term"]) for r in rows if r["term"] not in tested})
        groups = [
            f"{self.record.name_of('Group', g.get('group'))} "
            f"n={record_module.unwrap(g.get('n')) or '?'}"
            for g in analysis.get("groups") or []
            if isinstance(g, Mapping)
        ]
        table_names = self.record.tables()
        reported_in = [
            table_names.get(t, t) for t in analysis.get("tables") or [] if isinstance(t, str)
        ]
        methods = [
            name
            for name in (
                "connectivity", "decoding", "similarity", "conjunction",
                "latent_decomposition", "component_decomposition",
                "partial_least_squares", "other_method", "not_structurable",
            )
            if analysis.get(name)
        ]

        # Only lines the record has something for. Printing "(unstated)" for every
        # empty slot made the block twice as long and said nothing -- an absent line
        # already says the record is silent.
        facts = [
            ("measure", record_module.unwrap((analysis.get("measure") or {}).get("source_label"))),
            (
                "scope",
                ", ".join(
                    filter(
                        None,
                        [
                            record_module.unwrap(analysis.get("spatial_scope")),
                            record_module.unwrap(analysis.get("spatial_unit")),
                        ],
                    )
                ),
            ),
            ("region", record_module.unwrap(analysis.get("roi_label"))),
            ("method", ", ".join(methods)),
            ("statistic", record_module.unwrap((effect.get("statistic") or {}).get("family"))),
            ("sample", ", ".join(groups)),
            ("adjusted for", ", ".join(adjusted[:8])),
            ("prespecification", record_module.unwrap(analysis.get("prespecification"))),
            ("reported in", "; ".join(reported_in)),
        ]
        return "\n".join(
            [comparison, ""] + [f"- {label}: {value}" for label, value in facts if value]
        )

    def _statistic(self, statistic: Mapping[str, Any], analysis: Mapping[str, Any]) -> dict[str, Any]:
        """The statistic panel, and the verdicts that have something to judge.

        Both derived from what the record holds. An analysis with no family and no
        degrees of freedom gets an empty gate, so the block does not render at all --
        a radio group over an empty summary reads as a question the reviewer skipped
        rather than one that never applied. And an option is offered only when its
        subject exists: on this corpus every analysis records a family and none
        records a df, so `family_wrong` is always askable and `df_wrong` never is.
        """

        family = record_module.unwrap(statistic.get("family"))
        numerator = record_module.unwrap(statistic.get("degrees_of_freedom_numerator"))
        denominator = record_module.unwrap(statistic.get("degrees_of_freedom_denominator"))
        has_df = bool(numerator or denominator)
        if not family and not has_df:
            return {"statistic": [], "options": []}

        wanted = ["statistic_correct"]
        if family:
            wanted.append("family_wrong")
        # Exactly one of these is answerable, and which one depends on the record.
        wanted.append("df_wrong" if has_df else "df_absent")
        summary = "  ·  ".join(
            filter(
                None,
                [
                    f"**{family}**" if family else "",
                    f"df {numerator}/{denominator}" if has_df else "",
                    str(analysis.get("model_estimation") or ""),
                ],
            )
        )
        return {
            "statistic": [{"summary": summary}],
            "options": [
                {"value": value, "hint": spec.STATISTIC_VERDICTS[value]} for value in wanted
            ],
        }

    #: Which direction control value a recorded cell means. `unstated` is a real
    #: answer, not a missing one: both F-test main effects in the baseline corpus
    #: carry it, and folding it onto `absent` rendered an omnibus test as "every
    #: term adjusted for, none tested".
    _DIRECTIONS = {"pos": "positive", "neg": "negative", "not_applicable": "not_applicable"}

    def _direction(self, recorded: str | None) -> str:
        if recorded is None:
            return "absent"
        lowered = recorded.lower()
        for stem, value in self._DIRECTIONS.items():
            if stem in lowered:
                return value
        return "unstated"

    def emit_contrast(self) -> None:
        kind = spec.BY_NAME["contrast"]
        models = self.record.models()
        for analysis in self.record.analyses():
            local_id = analysis.get("local_id", "")
            model = models.get(analysis.get("model_estimation")) or {}
            rows = self._term_rows(model)

            effect = analysis.get("effect") or {}
            recorded = {
                (cell.get("term", ""), record_module.unwrap(cell.get("level"))):
                    record_module.unwrap(cell.get("direction"))
                for cell in effect.get("cells") or []
                if isinstance(cell, Mapping)
            }
            results = [
                self._choice(
                    spec.instance(kind.name, "row", index),
                    [self._direction(recorded.get((row["term"], row["level"])))],
                )
                for index, row in enumerate(rows)
            ]

            definition = (
                analysis.get("definition")
                if isinstance(analysis.get("definition"), Mapping)
                else {}
            )
            sets = ((definition.get("evidence") or {}).get("sets")) or []
            results = self._spans(
                spec.instance(kind.name, "spans"), sets, "definition"
            ) + results

            table_id, table_html, parsed_line = self._contrast_grid(analysis)
            statistic = effect.get("statistic") or {}
            data = self._blank(kind)
            data.update(
                local_id=local_id,
                entity_class="Analysis",
                table_id=table_id,
                row_count=len(rows),
                coordinate_status=self.record.coordinate_status(analysis, self.counts),
                table_html=table_html,
                rows=rows,
                labels=[{"value": "definition", "background": self._palette(1)}] if sets else [],
                **self._statistic(statistic, analysis),
                **{
                    kind.gate: [
                        {
                            "label": f"{local_id}  ·  "
                            f"{record_module.unwrap(analysis.get('name')) or '(unnamed)'}",
                            # What the highlight cannot express: four of the eighteen
                            # analyses across the baseline papers have a definition
                            # with no evidence at all, and without a word here their
                            # reviewer would hunt the pane for a highlight that was
                            # never drawn.
                            "meta": parsed_line
                            + (
                                ""
                                if sets
                                else "  ·  no evidence highlighted: the record's "
                                "definition carries no span"
                            ),
                            "body": self._paraphrase(analysis, rows),
                        }
                    ]
                },
            )
            self._emit(
                kind,
                local_id,
                sorted((t, lv, d) for (t, lv), d in recorded.items())
                + [record_module.unwrap(statistic.get("family"))],
                data,
                results,
            )

    # -- driver ------------------------------------------------------------

    def run(self) -> dict[str, list[dict[str, Any]]]:
        """Every task for this paper, in stage order within each project.

        Stage order is the import order, which is the labeling-stream order, so a
        reviewer walks a paper's inventory before the models drawn from it.
        """

        self.emit_entities()
        self.emit_value()
        self.emit_relationship()
        self.emit_model()
        self.emit_table()
        self.emit_contrast()

        if self.coordinates_only:
            self._drop_unrelated()
        for project in self.tasks:
            self.tasks[project].sort(key=lambda task: task["data"]["stage"])
        return self.tasks

    def _drop_unrelated(self) -> None:
        """Keep only what a reported result rests on -- except the table tasks.

        A coordinate table nothing rests on is the `not_analyses` finding, and
        dropping it would make that indistinguishable from a table nobody looked at.
        Never a silent cap: what was left out is counted and named.
        """

        for project, tasks in self.tasks.items():
            kept = []
            for task in tasks:
                data = task["data"]
                if (
                    data["task_kind"] in ("model", "contrast")
                    and data["coordinate_status"] in WITHOUT_COORDINATES
                ):
                    self.report.skipped.append(
                        f"--coordinates-only skipped {data['review_key']} "
                        f"({data['coordinate_status']})"
                    )
                else:
                    kept.append(task)
            self.tasks[project] = kept

    def contract_problems(self) -> list[str]:
        """Every task must carry exactly the keys its config reads, in the right shape."""

        problems = []
        for project in spec.PROJECTS:
            wanted = config.contract(project)
            for task in self.tasks[project.name]:
                data = task["data"]
                key = data.get("review_key", "?")
                for missing in sorted(set(wanted) - set(data)):
                    problems.append(f"{project.name} {key}: missing {missing}")
                for extra in sorted(set(data) - set(wanted)):
                    problems.append(f"{project.name} {key}: extra {extra}")
                for name, shape in wanted.items():
                    if name not in data:
                        continue
                    if shape == "array" and not isinstance(data[name], list):
                        problems.append(f"{project.name} {key}: {name} must be a list")
                    # A string key is typed on first save and Label Studio then
                    # admits only str for it, on import and on every later PATCH.
                    if shape == "string" and not isinstance(data[name], str):
                        problems.append(f"{project.name} {key}: {name} must be a string")
        return problems
