#!/usr/bin/env python3
"""Export an extraction record as Label Studio tasks for the five review projects.

Replaces the per-field export in `to_labelstudio.py`. What changes is the grouping,
not the machinery: text staging, span verification, the section index and the
《》-delimited excerpt are all reused from there.

    value         one task per entity instance, carrying every populated field
    relationship  one task per association slot, as a grid over the paper's
                  candidate targets
    structure     stage 0 instance inventories, one per class; then one task per
                  ModelEstimation
    contrast      stage 0 one task per coordinate table, judging the split stage 1
                  made of it; then one task per Analysis, judging its cells. Both
                  render the table the analyses were read off, so a reviewer sees
                  the object the record came from rather than a prose reference

Emits `config_gen.DATA_CONTRACT` exactly -- `--check-contract` asserts it -- so the
configs and the tasks cannot drift apart.

Usage:
    python review/export_tasks.py \\
        --record review/examples/HU6mqxmtySg3.extraction.json \\
        --text review/texts/HU6mqxmtySg3/processed/pubget/text.txt \\
        --identifiers review/texts/HU6mqxmtySg3/identifiers.json \\
        --files-root review/ls_files --out-dir review/ls_tasks
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config_gen
import spans as span_tools
import table_render
import text_index
import to_labelstudio as legacy

import schema_utils  # noqa: E402

#: Classes whose fields belong to the structure family rather than the value
#: family: their correctness depends on another object's identity, so they are
#: reviewed as a subtree. `Analysis` itself is not here -- its own scalars
#: (spatial_scope, prespecification, definition) are ordinary values, and the
#: contrast task shows `definition` read-only as the paper's own wording.
STRUCTURAL_CLASSES = {
    "Effect",
    "Cell",
    "Statistic",
    "Mediation",
    "ModelTerm",
    "FactorLevel",
    "AnalysisGroup",
}

#: Classes the entity inventory does not cover, because another task already
#: settles their instance set: `Analysis` through the table tasks, which judge the split
#: the analyses came out of, and `ModelEstimation` through the model tasks. `Table` is
#: filled by the table parser rather than the model, so it is not a judgement about the
#: paper.
ENTITY_INVENTORY_EXCLUDED = {"Analysis", "ModelEstimation", "Table"}

#: Stage each family is imported in. Derived from what a correction can
#: invalidate; see staged-validation.md.
#:
#: `table` is stage 0 alongside the entity inventory, not stage 2 with the contrast it
#: is rendered beside. `over_split` on a table invalidates the contrast, model, value and
#: relationship tasks of every analysis drawn from it, which is the same cascade the
#: entity inventory guards against and the same reason to ask it first.
STAGES = {
    "entities": 0, "table": 0, "value": 1, "relationship": 1, "model": 2, "contrast": 2,
}

_NOT_REPORTED = "not reported"

#: Below this, a contrast task says so: the record and the parsed analysis it was
#: matched to do not agree on every token of their names, so the marked rows may be
#: a different contrast's. All 18 links across the three baseline papers score
#: exactly 1.0, which is why the score is printed only when it is not -- a line
#: reading "Name match 1.00" on every task is a diagnostic nobody can act on.
_WEAK_MATCH = 1.0


def _digest(payload: Any) -> str:
    """Content hash over the answer-bearing payload only.

    Descriptors and rendered prose are excluded by the callers: if the hash
    covered them, correcting `Group.name` would change the descriptor shown in
    every contrast task and re-ask a dozen structural questions whose substance
    did not move.
    """

    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class FieldRecord:
    class_name: str
    local_id: str
    entity_label: str
    field_path: str
    attribute: Mapping[str, Any]
    node: Mapping[str, Any]
    priority: object


@dataclass
class RefRecord:
    class_name: str
    local_id: str
    entity_label: str
    slot: str
    attribute: Mapping[str, Any]
    targets: list[str]


@dataclass
class Report:
    value: int = 0
    relationship: int = 0
    entities: int = 0
    model: int = 0
    table: int = 0
    contrast: int = 0
    predicted_spans: int = 0
    predicted_choices: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            self.value + self.relationship + self.entities
            + self.model + self.table + self.contrast
        )


class TaskExporter:
    def __init__(
        self,
        record: Mapping[str, Any],
        normalized: str,
        paper_id: str,
        identifiers: Mapping[str, Any],
        paper_url: str,
    ) -> None:
        self.record = record
        self.normalized = normalized
        self.paper_id = paper_id
        self.identifiers = identifiers
        self.paper_url = paper_url

        self.text_hash = text_index.text_hash(normalized)
        self.classes = schema_utils.load_imported_classes(legacy.EXTRACTION_SCHEMA)
        self.priorities = legacy.load_priorities()
        self.sections = text_index.build_sections(normalized)
        self.report = Report()

        metadata = record.get("extraction_metadata") or {}
        self.model_version = (
            f"{metadata.get('extractor_model', 'unknown')}"
            f"@{metadata.get('extractor_version', 'unknown')}"
        )

        #: Table local_id -> "Table N". Two analyses can share a contrast, a
        #: measure and a scope and differ only in which table reports them -- one
        #: per seed, say -- so the table is often the only thing that tells them
        #: apart. Shown in the contrast paraphrase for that reason.
        #:
        #: The number alone, not "Table N — <caption>": the caption is what
        #: distinguishes tables from each other, but a contrast task renders the
        #: table's own caption directly above the grid, so carrying it here printed
        #: a four-line methods paragraph twice on one screen. The number is what
        #: does the distinguishing work.
        self.tables: dict[str, str] = {}
        #: Table local_id -> number of coordinate rows, from the table parser.
        #: `Table.coordinate_count` is storage-only -- the projection drops it
        #: because code fills it, not the model -- so it arrives as an argument
        #: rather than out of the extraction record.
        self.coordinate_counts: dict[str, int] = {}
        self._reachable: set[str] | None = None

        self.fields: list[FieldRecord] = []
        self.refs: list[RefRecord] = []
        #: Every Study-level entity class the schema defines, in schema order.
        #: Taken from the schema rather than the record: a class the extractor
        #: found nothing for is exactly the one worth asking about, and keying off
        #: the record would emit no task for it.
        self.entity_classes: list[str] = []
        #: class -> [local_id], in record order
        self.instances: dict[str, list[str]] = {}
        #: (class, local_id) -> the entity mapping
        self.entities: dict[tuple[str, str], Mapping[str, Any]] = {}
        self._walk()

        for table in record.get("tables") or []:
            if not isinstance(table, Mapping):
                continue
            number = self._field_value(table.get("table_number"))
            local_id = table.get("local_id", "")
            self.tables[local_id] = number or local_id

        # How many references point at each local_id. The inventory shows this so
        # a reviewer choosing `drop` can see what it costs.
        self.inbound: dict[str, int] = {}
        for ref in self.refs:
            for target in ref.targets:
                self.inbound[target] = self.inbound.get(target, 0) + 1

        self.tasks: dict[str, list[dict[str, Any]]] = {
            "value": [],
            "relationship": [],
            "structure": [],
            "contrast": [],
        }

        #: Filled by `load_tables`, which is optional: a paper with no synced pubget
        #: source still exports every other family. Absent, the table tasks are simply
        #: not emitted and the contrast tasks carry a warning in place of the grid.
        self.pubget_dir: Path | None = None
        self.manifest: dict[str, dict[str, Any]] = {}
        self.parsed: dict[str, list[dict[str, Any]]] = {}
        #: pubget table_id -> record Table local_id, and back.
        self.local_of: dict[str, str] = {}
        self.pubget_of: dict[str, list[str]] = {}
        self._rendered: dict[str, Any] = {}
        self.links = table_render.Links()

    # -- traversal ---------------------------------------------------------

    def _walk(self) -> None:
        study = schema_utils.attributes_for(self.classes, "Study")

        # The class list comes from the schema and is built first, independently
        # of what this record happens to hold. Deriving it from the record would
        # skip exactly the classes the extractor found nothing for.
        for attr, attribute in study.items():
            if attr == "extraction_metadata":
                continue
            if schema_utils.classify_slot(self.classes, attr, attribute) != "nested":
                continue
            target = attribute.get("range")
            if (
                isinstance(target, str)
                and target not in self.entity_classes
                and "local_id" in schema_utils.attributes_for(self.classes, target)
            ):
                self.entity_classes.append(target)

        for attr, attribute in study.items():
            if attr == "extraction_metadata" or attr not in self.record:
                continue
            kind = schema_utils.classify_slot(self.classes, attr, attribute)
            value = self.record[attr]
            if kind != "nested":
                continue
            target = attribute.get("range")
            if not isinstance(target, str):
                continue
            for entity in value if isinstance(value, list) else [value]:
                if not isinstance(entity, Mapping):
                    continue
                local_id = entity.get("local_id")
                if isinstance(local_id, str):
                    self.instances.setdefault(target, []).append(local_id)
                    self.entities[(target, local_id)] = entity
                self._visit(entity, target, local_id or "", legacy._entity_label(entity), "")

    def _visit(
        self,
        node: Mapping[str, Any],
        class_name: str,
        owner_id: str,
        owner_label: str,
        prefix: str,
    ) -> None:
        attributes = schema_utils.attributes_for(self.classes, class_name)
        for name, attribute in attributes.items():
            if name not in node:
                continue
            kind = schema_utils.classify_slot(self.classes, name, attribute)
            value = node[name]
            path = f"{prefix}{name}" if prefix else name

            if kind == "evidence":
                items = value if attribute.get("multivalued") and isinstance(value, list) else [value]
                for index, item in enumerate(items):
                    if not isinstance(item, Mapping) or "extraction_status" not in item:
                        continue
                    suffix = f"[{index}]" if len(items) > 1 or (
                        attribute.get("multivalued") and isinstance(value, list)
                    ) else ""
                    leaf = name
                    self.fields.append(
                        FieldRecord(
                            class_name=class_name,
                            local_id=owner_id,
                            entity_label=owner_label,
                            field_path=f"{path}{suffix}",
                            attribute=attribute,
                            node=item,
                            priority=self.priorities.get((class_name, leaf), "unranked"),
                        )
                    )
            elif kind == "reference":
                targets = (
                    [value]
                    if isinstance(value, str)
                    else [r for r in value or [] if isinstance(r, str)]
                )
                self.refs.append(
                    RefRecord(class_name, owner_id, owner_label, path, attribute, targets)
                )
            elif kind == "nested":
                target = attribute.get("range")
                if not isinstance(target, str):
                    continue
                items = value if isinstance(value, list) else [value]
                for index, item in enumerate(items):
                    if not isinstance(item, Mapping):
                        continue
                    suffix = f"[{index}]" if isinstance(value, list) else ""
                    self._visit(item, target, owner_id, owner_label, f"{path}{suffix}.")

    # -- shared task frame -------------------------------------------------

    def _frame(self, family: str, review_key: str, content: Any) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "review_key": review_key,
            "content_hash": _digest(content),
            "stage": STAGES[family],
            "paper_url": self.paper_url,
            "paper_title": self.paper_id,
            "paper_citation": self._citation(),
            # Deliberately outside content_hash: a re-staged text does not change the
            # question, it changes where the answer's evidence lives. Carrying it makes
            # `data` differ so sync_tasks takes its `refreshed` branch -- answers kept,
            # predictions rewritten -- instead of the `unchanged` short-circuit that
            # leaves every stored offset addressing a text that is no longer served.
            "paper_text_hash": self.text_hash,
        }

    def load_tables(
        self,
        pubget_dir: Path | None,
        stage1: Path | None,
        table_map: Path | None,
    ) -> None:
        """The coordinate tables and the stage-1 split, for the contrast family.

        Every failure is reported and none raises. A paper whose pubget source was never
        synced still exports value, relationship and structure tasks; it simply gets no
        table tasks, and its contrast tasks say so on the face of the task rather than
        rendering an empty grid that reads like an analysis with no results.
        """

        if pubget_dir is None or not Path(pubget_dir).is_dir():
            self.report.skipped.append(
                f"no pubget source at {pubget_dir}: no table tasks, and contrast tasks "
                "carry no grid"
            )
            return
        self.pubget_dir = Path(pubget_dir)
        self.manifest = table_render.read_manifest(self.pubget_dir.parent.parent)
        if not self.manifest:
            self.report.skipped.append(
                f"no tables.jsonl under {self.pubget_dir.parent.parent}: no table tasks"
            )

        if stage1 and Path(stage1).is_file():
            self.parsed = table_render.load_stage1(Path(stage1))
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
                f"no table-map at {table_map}: a contrast cannot be tied to its table"
            )

        record = {}
        for analysis in self.record.get("analyses") or []:
            if isinstance(analysis, Mapping):
                record[analysis.get("local_id", "")] = (
                    self._field_value(analysis.get("name")),
                    [t for t in analysis.get("tables") or [] if isinstance(t, str)],
                )
        self.links = table_render.link_analyses(record, self.parsed, self.local_of)
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
            record = self.manifest.get(table_id)
            self._rendered[table_id] = (
                table_render.read_table(
                    self.pubget_dir,
                    record["data_file"],
                    label=record["table_label"],
                    caption=record["caption"],
                )
                if record and self.pubget_dir and record.get("data_file")
                else None
            )
        return self._rendered[table_id]

    def load_coordinate_counts(self, counts: Mapping[str, Any] | None) -> None:
        """Optional narrowing from the table parser.

        The rule needs nothing: extraction is driven by results, so an analysis
        that reports one references the table it is reported in, and an analysis
        with no table reports nothing. Parser counts only add the case a link
        cannot show -- a referenced table that turns out to hold zero coordinate
        rows. `Table.coordinate_count` is storage-only, so it arrives here as an
        argument rather than out of the extraction record.
        """

        if not counts:
            return
        scoped = counts.get(self.paper_id, counts)
        self.coordinate_counts = {
            k: int(v) for k, v in scoped.items() if isinstance(v, (int, float))
        }

    def coordinate_status(self, analysis: Mapping[str, Any]) -> str:
        """Whether this contrast is worth validating.

        `no_table` is the whole rule: extraction is driven by the existence of a
        result, so a contrast with no table is a contrast with no reported result.
        `no_coordinates` is the refinement, and only available when the table
        parser has supplied counts.
        """

        tables = [t for t in analysis.get("tables") or [] if isinstance(t, str)]
        if not tables:
            return "no_table"
        if self.coordinate_counts:
            counts = [self.coordinate_counts.get(t) for t in tables]
            if all(isinstance(c, int) for c in counts) and not any(c > 0 for c in counts):
                return "no_coordinates"
        return "yes"

    def coordinate_reachable(self) -> set[str]:
        """local_ids reachable from a coordinate-bearing analysis.

        Everything a reported result rests on: the model it came from, that
        model's terms, the cohorts, paradigms, acquisitions, assessments and
        tables it names, and whatever those point at in turn. An object nothing
        coordinate-bearing reaches is not worth a reviewer's time -- which is what
        lets one filter serve every family rather than only the contrast tasks.

        Built from `self.refs`, whose owner is always the top-level entity, so
        `Cell.term` already reads as analysis -> term.
        """

        if self._reachable is not None:
            return self._reachable

        edges: dict[str, set[str]] = {}
        for ref in self.refs:
            if ref.local_id:
                edges.setdefault(ref.local_id, set()).update(ref.targets)

        frontier = {
            a.get("local_id")
            for a in self.record.get("analyses") or []
            if isinstance(a, Mapping) and self.coordinate_status(a) == "yes"
        }
        frontier.discard(None)
        seen: set[str] = set()
        while frontier:
            node = frontier.pop()
            if node in seen:
                continue
            seen.add(node)
            frontier |= edges.get(node, set()) - seen
        self._reachable = seen
        return seen

    def object_coordinate_status(self, local_id: str) -> str:
        """`yes` when this object supports a reported result, `unrelated` otherwise."""

        return "yes" if local_id and local_id in self.coordinate_reachable() else "unrelated"

    def model_coordinate_status(self, model_id: str) -> str:
        """A model is worth validating if any contrast taken from it is.

        A model no analysis references -- a first-level model whose output only
        feeds a group model -- is tied to no reported result and takes
        `no_contrast`.
        """

        statuses = [
            self.coordinate_status(a)
            for a in self.record.get("analyses") or []
            if isinstance(a, Mapping) and a.get("model_estimation") == model_id
        ]
        if not statuses:
            return "no_contrast"
        return "yes" if "yes" in statuses else statuses[0]

    def _citation(self) -> str:
        parts = [self.paper_id]
        if self.identifiers.get("pmid"):
            parts.append(f"pmid {self.identifiers['pmid']}")
        if self.identifiers.get("doi"):
            parts.append(str(self.identifiers["doi"]))
        return "  ·  ".join(parts)

    def descriptor_for(self, class_name: str, local_id: str) -> str:
        """`local_id -- name . fact . fact`, from the class's priority-0 scalars.

        Derived here and never stored: a stored descriptor is a second copy of the
        entity that drifts from the first. The facts come from
        storage-parameter-priorities.yaml, already this project's answer to what
        matters about an object.
        """

        entity = self.entities.get((class_name, local_id))
        if entity is None:
            return local_id
        name = legacy._entity_label(entity) or None
        facts = []
        for slot, node in entity.items():
            if slot == "local_id" or not isinstance(node, Mapping):
                continue
            if node.get("extraction_status") != "extracted" or "value" not in node:
                continue
            if self.priorities.get((class_name, slot)) != 0:
                continue
            if slot in ("name", "title"):
                continue
            rendered = legacy._display(node["value"])
            if len(rendered) <= 40:
                facts.append(f"{slot}={rendered}")
        return config_gen.descriptor(local_id, name, facts)

    def _span_predictions(self, from_name: str, sets: list[Any], label: str) -> list[dict[str, Any]]:
        results = []
        for set_index, evidence_set in enumerate(sets):
            for span_index, span in enumerate(evidence_set.get("spans", [])):
                # A task must never ship an offset that does not address the text
                # we are about to serve.
                span_tools.verify(self.normalized, span)
                results.append(
                    {
                        "id": f"{from_name}_{set_index}_{span_index}",
                        "from_name": from_name,
                        "to_name": "paper",
                        "type": "labels",
                        "value": {
                            "start": span["start_char"],
                            "end": span["end_char"],
                            "text": span["text"],
                            "labels": [label],
                        },
                    }
                )
                self.report.predicted_spans += 1
        return results

    #: Extraction stores the source's wording; these controls offer a fixed
    #: vocabulary. Matching is on a normalized stem so "between subjects",
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

    def _choice(self, from_name: str, values: list[str]) -> dict[str, Any]:
        self.report.predicted_choices += 1
        return {
            "from_name": from_name,
            "to_name": "paper",
            "type": "choices",
            "value": {"choices": values},
        }

    # -- value family ------------------------------------------------------

    def emit_value_tasks(self) -> None:
        """One task per field of one entity, including fields marked not_reported.

        Per field rather than per entity: an entity task bundled 13-25 judgements
        behind a single verdict, so the reviewer either accepted all of them at
        once or opened a long form, and the answer needed an index path to
        address. A `not_reported` field gets a task of its own because "the paper
        does state this" is a finding the extractor cannot make about itself.

        No evidence is quoted. The spans, if any, are pre-highlighted in the paper
        pane under a `set N` label.
        """

        for record in self.fields:
            if record.class_name in STRUCTURAL_CLASSES:
                continue

            node = record.node
            status = node.get("extraction_status")
            evidence = node.get("evidence") or {}
            sets = evidence.get("sets") or []
            value_text = legacy._display(node["value"]) if "value" in node else _NOT_REPORTED

            section = ""
            if sets:
                first = sets[0]["spans"][0]["start_char"]
                section = text_index.section_path(self.sections, first) or ""
            span_total = sum(len(s.get("spans", [])) for s in sets)
            shape = f"{span_total} span(s)" if sets else "no evidence"

            # Two labels, one per kind of support, rather than one per evidence
            # set. Direct vs inferred is a property of the passage and the
            # reviewer decides it while drawing, so it belongs on the label; a
            # perRegion control would stay hidden until the span was clicked.
            #
            # `value_source` pre-fills the label: `reported` means the paper states
            # the value, `generated` means the extractor composed it from what the
            # passage implies. The reviewer changes the label if that is wrong.
            labels = [
                {
                    "value": kind,
                    "background": config_gen._SET_COLOURS[i % len(config_gen._SET_COLOURS)],
                }
                for i, (kind, _hint) in enumerate(config_gen._SUPPORT_KINDS)
            ]
            predicted_label = (
                "direct support"
                if node.get("value_source") == "reported"
                else "inferred support"
            )
            spans = []
            for evidence_set in sets:
                spans.extend(self._span_predictions("ev", [evidence_set], predicted_label))

            handle = f"{record.class_name} {record.local_id}" if record.local_id else record.class_name
            data = self._frame(
                "value",
                f"{self.paper_id}|value|{record.class_name}|{record.local_id}|{record.field_path}",
                (value_text, status, evidence.get("status")),
            )
            data.update(
                entity_class=record.class_name,
                local_id=record.local_id,
                field_path=record.field_path,
                priority=record.priority,
                llm_status=status,
                evidence_status=evidence.get("status", ""),
                field_label=f"{handle}  ·  {record.field_path}",
                field_description=(record.attribute.get("description") or "")
                .strip()
                .split(". ")[0][:90],
                llm_value=value_text,
                llm_meta="  ·  ".join(filter(None, [shape, section])),
                span_labels=labels,
                coordinate_status=self.object_coordinate_status(record.local_id),
            )
            task: dict[str, Any] = {"data": data}
            if spans:
                task["predictions"] = [{"model_version": self.model_version, "result": spans}]
            self.tasks["value"].append(task)
            self.report.value += 1

    # -- relationship family -----------------------------------------------

    def emit_relationship_tasks(self) -> None:
        """One grid per association slot: rows are source objects, columns targets.

        Judging the whole assignment at once is what makes an unused target
        visible as an empty column, which no per-source-object task can show.
        """

        by_slot: dict[tuple[str, str], list[RefRecord]] = {}
        for ref in self.refs:
            if ref.class_name in STRUCTURAL_CLASSES:
                continue
            by_slot.setdefault((ref.class_name, ref.slot), []).append(ref)

        for (class_name, slot), refs in by_slot.items():
            attribute = refs[0].attribute
            target_class = legacy._target_class(self.classes, attribute)
            if not target_class:
                continue
            candidates = self.instances.get(target_class) or []
            if not candidates:
                self.report.skipped.append(
                    f"{class_name}.{slot} -> {target_class}: no candidates extracted"
                )
                continue

            multivalued = bool(attribute.get("multivalued"))
            columns = [
                {"value": self.descriptor_for(target_class, cid), "alias": cid}
                for cid in candidates
            ]
            if not multivalued:
                # A single-select needs somewhere to say "none of these". Without it
                # an unlinked source object is indistinguishable from one nobody got
                # to, and the radio cannot be cleared once clicked.
                columns.append({"value": "no link", "alias": "none"})

            rows: list[dict[str, Any]] = []
            predictions: list[dict[str, Any]] = []
            anomalies: list[str] = []
            for index, ref in enumerate(refs):
                # The name half of the descriptor, which is what a reviewer scans;
                # the id goes underneath, where it can be matched to a span.
                heading = self.descriptor_for(class_name, ref.local_id).split(" -- ")[-1]
                control = f"{'lm' if multivalued else 'ls'}_{index}"
                chosen = [t for t in ref.targets if t in candidates]
                if chosen:
                    predictions.append(self._choice(control, chosen))
                if attribute.get("required") and not ref.targets:
                    anomalies.append(f"- **{heading}** is required to link but has none")
                for target in ref.targets:
                    if target not in candidates:
                        anomalies.append(
                            f"- **{heading}** links to `{target}`, "
                            "which was never extracted"
                        )
                rows.append(
                    {"label": heading, "meta": ref.local_id, "local_id": ref.local_id}
                )

            digest_of = [(r.local_id, sorted(r.targets)) for r in refs]
            data = self._frame(
                "relationship", f"{self.paper_id}|rel|{class_name}.{slot}", digest_of
            )
            data.update(
                rel_slot=f"{class_name}.{slot} → {target_class}  ·  "
                f"{'many' if multivalued else 'one'} per {class_name}",
                target_class=target_class,
                anomaly_count=len(anomalies),
                rel_label=f"Which {target_class}{'s' if multivalued else ''} "
                f"does each {class_name} use?",
                # Deliberately empty, as `entities`' meta/guidance and `model`'s
                # guidance/summary are: the contract declares the key, the config
                # reserves the line, and nothing fills it yet. The slot's schema
                # description is available on `attribute` and would read well here --
                # a change worth making on purpose, not while restoring.
                rel_description="",
                # Gated by presence: an empty list renders no copies at all, so a
                # clean slot carries no warning panel rather than an empty one.
                anomaly_gate=[{"text": "\n".join(anomalies)}] if anomalies else [],
                columns=columns,
                rows_multi=rows if multivalued else [],
                rows_single=[] if multivalued else rows,
                # The span layer's labels, which are not the grid's columns. "no link"
                # is a column but not a label -- there is no passage in the paper that
                # warrants an absence -- and a label's text becomes a button, so a
                # 77-character descriptor is clipped to something that fits one.
                link_labels=[
                    {"value": column["value"][:60], "alias": column["alias"]}
                    for column in columns
                    if column["alias"] != "none"
                ],
                coordinate_status="yes"
                if any(self.object_coordinate_status(r.local_id) == "yes" for r in refs)
                else "unrelated",
            )
            task = {"data": data}
            if predictions:
                task["predictions"] = [
                    {"model_version": self.model_version, "result": predictions}
                ]
            self.tasks["relationship"].append(task)
            self.report.relationship += 1

    # -- structure family --------------------------------------------------

    def _existence_evidence(self, class_name: str, local_id: str) -> tuple[str, list[Any]]:
        """The spans that warrant an instance existing, and which field they came from.

        "Is this a real cohort?" is answerable from the sentence that introduces
        it, which is the evidence on its identifying field. Preference order is
        name, then title, then any priority-0 field that carries evidence, then
        anything evidenced at all -- so an instance whose name was never evidenced
        still gets a highlight rather than none.
        """

        def evidence_of(record: FieldRecord) -> list[Any]:
            return (record.node.get("evidence") or {}).get("sets") or []

        def rank(record: FieldRecord) -> tuple[int, str]:
            leaf = record.field_path.split(".")[-1].split("[")[0]
            if leaf in ("name", "title"):
                return (0, leaf)
            if record.priority == 0:
                return (1, leaf)
            return (2, leaf)

        candidates = [
            record
            for record in self.fields
            if record.class_name == class_name
            and record.local_id == local_id
            and evidence_of(record)
        ]
        if not candidates:
            return "", []
        record = sorted(candidates, key=rank)[0]
        return record.field_path, evidence_of(record)

    def emit_entity_inventories(self) -> None:
        """Stage 0. One task per class: is this the right set of instances?

        The only place an invented Group can be rejected -- a value task judges its
        fields and a relationship task judges its links, and both presuppose it
        exists.
        """

        for class_name in self.entity_classes:
            if class_name in ENTITY_INVENTORY_EXCLUDED:
                continue
            ids = self.instances.get(class_name) or []

            rows, spans, labels = [], [], []
            for index, local_id in enumerate(ids):
                source, sets = self._existence_evidence(class_name, local_id)
                rows.append(
                    {
                        "label": local_id,
                        "descriptor": self.descriptor_for(class_name, local_id),
                        "referenced_by": f"referenced by {self.inbound.get(local_id, 0)} link(s)"
                        + (f"  ·  evidence from {source}" if source else "  ·  no evidence"),
                        "local_id": local_id,
                    }
                )
                labels.append(
                    {
                        "value": local_id,
                        "background": config_gen._SET_COLOURS[
                            index % len(config_gen._SET_COLOURS)
                        ],
                    }
                )
                spans.extend(self._span_predictions("st_e_0", sets, local_id))

            data = self._frame(
                "entities", f"{self.paper_id}|entities|{class_name}", sorted(ids)
            )
            data.update(
                task_kind="entities",
                coordinate_status="yes"
                if any(self.object_coordinate_status(i) == "yes" for i in ids)
                else "unrelated",
                local_id="",
                cell_count=len(ids),
                structure_labels=labels,
                entities=[
                    {
                        "label": f"{class_name}  ·  "
                        + (f"{len(ids)} extracted" if ids else "none extracted"),
                        "meta": "",
                        "guidance": "",
                    }
                ],
                # `entity_table` feeds a `<Table>`, which renders every key it is
                # given as a row -- so the local_id the Repeater needs to address a
                # control has to be absent here and present in `entity_rows`, or it
                # shows up as a duplicate line under each entity.
                entity_table=[
                    {k: v for k, v in row.items() if k != "local_id"} for row in rows
                ],
                entity_rows=rows,
                model=[],
                terms=[],
            )
            task = {"data": data}
            if spans:
                task["predictions"] = [{"model_version": self.model_version, "result": spans}]
            self.tasks["structure"].append(task)
            self.report.entities += 1

    def _field_value(self, node: Any) -> str:
        if isinstance(node, Mapping) and "value" in node:
            return legacy._display(node["value"])
        return ""

    def _entity_name(self, cls: str, local_id: Any) -> str:
        """An entity's own name, falling back to the id when it has none.

        Never empty: the id is a worse label than the name but a far better one
        than nothing, and a paraphrase line that silently loses its subject reads
        as a record that failed to record one.
        """

        if not isinstance(local_id, str) or not local_id:
            return ""
        entity = self.entities.get((cls, local_id))
        return (self._field_value(entity.get("name")) if entity else "") or local_id

    def _models(self) -> dict[str, Mapping[str, Any]]:
        return {
            m.get("local_id"): m
            for m in self.record.get("model_estimations") or []
            if isinstance(m, Mapping)
        }

    def _stage_terms(self, model: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        """This stage's terms, then those of the stages it was fitted on.

        `ModelEstimation.inputs_from` makes a multi-stage analysis one model: a contrast taken
        from the group stage may cell a first-level column -- a seed's time series, a condition
        the first level fitted -- and is adjusted by every first-level column it does not cell.
        Offering only the top stage's rows made those contrasts unrecordable.

        Breadth-first, so the stage the analysis names comes first in the grid and the ones
        beneath it follow. Cycle-safe, since a malformed chain must still produce a task.
        """

        models = self._models()
        seen: set[str] = set()
        terms: list[Mapping[str, Any]] = []
        queue = [model]
        while queue:
            current = queue.pop(0)
            key = current.get("local_id", "")
            if key in seen:
                continue
            seen.add(key)
            for t in current.get("terms") or []:
                if isinstance(t, Mapping):
                    terms.append(t)
            for input_id in current.get("inputs_from") or []:
                upstream = models.get(input_id)
                if upstream is not None:
                    queue.append(upstream)
        return terms

    def _term_rows(self, model: Mapping[str, Any]) -> list[dict[str, Any]]:
        """One grid row per term, or per level of a categorical term.

        The row inventory comes from the model's stage chain, so a cell can only
        ever name a term that chain declares -- the foreign key is enforced by the
        widget.
        """

        rows: list[dict[str, Any]] = []
        for term in self._stage_terms(model):
            if not isinstance(term, Mapping):
                continue
            name = self._field_value(term.get("name")) or term.get("local_id", "?")
            levels = term.get("levels") or []
            if levels:
                for level in levels:
                    label = self._field_value(level.get("level")) if isinstance(level, Mapping) else ""
                    rows.append(
                        {
                            "label": f"{name} : {label}" if label else name,
                            "term": term.get("local_id", ""),
                            "level": label,
                        }
                    )
                continue
            kind = self._field_value(term.get("type"))
            rows.append(
                {
                    "label": f"{name}  ({kind})" if kind else f"{name}  (no levels)",
                    "term": term.get("local_id", ""),
                    "level": "",
                }
            )
        return rows

    def emit_model_tasks(self) -> None:
        for model in self.record.get("model_estimations") or []:
            if not isinstance(model, Mapping):
                continue
            local_id = model.get("local_id", "")

            terms, labels, spans, choices = [], [], [], []
            for index, term in enumerate(model.get("terms") or []):
                if not isinstance(term, Mapping):
                    continue
                name = self._field_value(term.get("name")) or term.get("local_id", "?")
                kind = self._field_value(term.get("type")) or "type unstated"
                scope = self._field_value(term.get("variation_level")) or "scope unstated"
                node = term.get("name") if isinstance(term.get("name"), Mapping) else {}
                sets = ((node.get("evidence") or {}).get("sets")) or []

                levels = [lv for lv in term.get("levels") or [] if isinstance(lv, Mapping)]
                facts = []
                if self._field_value(term.get("source_definition")):
                    facts.append(self._field_value(term.get("source_definition")))
                if self._field_value(term.get("unit")):
                    facts.append(f"unit {self._field_value(term.get('unit'))}")
                if levels:
                    facts.append(f"{len(levels)} level(s) declared")
                terms.append(
                    {
                        "heading": name,
                        "summary": "  ·  ".join(facts)
                        or "no definition, unit or levels recorded",
                        "local_id": term.get("local_id", ""),
                        "levels": [
                            {
                                "label": self._field_value(lv.get("level")) or "(unnamed)",
                                "level": self._field_value(lv.get("level")) or "(unnamed)",
                            }
                            for lv in levels
                        ],
                    }
                )
                labels.append(
                    {
                        "value": f"term: {name}",
                        "background": config_gen._SET_COLOURS[
                            index % len(config_gen._SET_COLOURS)
                        ],
                    }
                )
                spans.extend(self._span_predictions("st_m_0", sets, f"term: {name}"))

                matched = self._match(kind, self._TERM_TYPES)
                if matched:
                    choices.append(self._choice(f"ttype_0_{index}", [matched]))
                matched = self._match(scope, self._VARIATION)
                if matched:
                    choices.append(self._choice(f"tvar_0_{index}", [matched]))
                for level_index, lv in enumerate(levels):
                    # `order` arrives as an extraction node, not a bare number, so it
                    # has to be unwrapped before it can be pre-filled -- and a
                    # non-numeric one is left unselected rather than guessed at.
                    order = self._field_value(lv.get("order"))
                    try:
                        number = int(float(order))
                    except (TypeError, ValueError):
                        continue
                    choices.append(
                        {
                            "from_name": f"lo_0_{index}_{level_index}",
                            "to_name": "paper",
                            "type": "number",
                            "value": {"number": number},
                        }
                    )
                    self.report.predicted_choices += 1

            digest_of = [
                (t["local_id"], t["heading"], [lv["level"] for lv in t["levels"]])
                for t in terms
            ]

            data = self._frame("model", f"{self.paper_id}|model|{local_id}", digest_of)
            family = self._field_value(model.get("model_family"))
            # The stage this model was fitted on belongs on the card: its terms are this
            # model's too, so a reviewer judging whether the term list is complete has to
            # know which columns are already accounted for below.
            inputs = [i for i in model.get("inputs_from") or [] if isinstance(i, str)]
            data.update(
                task_kind="model",
                coordinate_status=self.model_coordinate_status(local_id),
                local_id=local_id,
                cell_count=len(terms),
                structure_labels=labels,
                model=[
                    {
                        "label": f"{local_id}  ·  {len(terms)} terms",
                        "meta": "  ·  ".join(
                            filter(
                                None,
                                [
                                    family,
                                    self._field_value(model.get("model_type")),
                                    self._field_value(model.get("estimator")),
                                    self._field_value(model.get("software")),
                                    self._field_value(model.get("level")),
                                    f"fitted on {', '.join(inputs)}" if inputs else "",
                                ],
                            )
                        ),
                        "guidance": "",
                        "summary": "",
                    }
                ],
                terms=terms,
                entities=[],
                entity_table=[],
                entity_rows=[],
            )
            task = {"data": data}
            result = spans + choices
            if result:
                task["predictions"] = [
                    {"model_version": self.model_version, "result": result}
                ]
            self.tasks["structure"].append(task)
            self.report.model += 1

    def _term_names(self, analysis: Mapping[str, Any]) -> dict[str, str]:
        """term local_id -> the name its stage gives it, across the whole chain."""

        model = self._models().get(analysis.get("model_estimation")) or {}
        return {
            term.get("local_id", ""): self._field_value(term.get("name"))
            or term.get("local_id", "")
            for term in self._stage_terms(model)
        }

    def _paraphrase(self, analysis: Mapping[str, Any], rows: list[dict[str, Any]]) -> str:
        """The record rendered back into one sentence.

        The primary judgement: accepting is one click, which is what makes 5-9
        analyses per paper affordable. Writing it also surfaces every place the
        record cannot be read back as a claim.
        """

        effect = analysis.get("effect") or {}
        cells = effect.get("cells") or []
        # Resolve term ids to the model's own names. A paraphrase reading
        # "term_sentence_condition:opaque proverb" is no more reviewable than a
        # bare id, which is the whole point of descriptors.
        names = self._term_names(analysis)
        sides: dict[str, list[str]] = {"positive": [], "negative": []}
        for cell in cells:
            if not isinstance(cell, Mapping):
                continue
            direction = self._field_value(cell.get("direction")).lower()
            term = cell.get("term", "")
            level = self._field_value(cell.get("level"))
            name = names.get(term, term)
            handle = f"{name} = {level}" if level else name
            if "pos" in direction:
                sides["positive"].append(handle)
            elif "neg" in direction:
                sides["negative"].append(handle)

        # Bold the two sides, not the whole line. Wrapping a line that already
        # contains `**vs**` in another pair of asterisks nests emphasis inside
        # emphasis, which no two markdown renderers agree on -- and when it happens
        # to work it bolds the separator too, so the eye gets no help finding where
        # one side ends.
        comparison = " vs ".join(
            f"**{side}**"
            for side in (" + ".join(sides["positive"]), " + ".join(sides["negative"]))
            if side
        ) or "_(no signed cell)_"

        measure = analysis.get("measure") or {}
        statistic = effect.get("statistic") or {}
        # The group's own name, falling back to the local_id. Same reasoning as
        # `_term_names` above: a line reading `group_healthy_adults n=18` makes the
        # reviewer decode an identifier to check a fact the record states in words.
        groups = [
            f"{self._entity_name('Group', g.get('group'))} "
            f"n={self._field_value(g.get('n')) or '?'}"
            for g in analysis.get("groups") or []
            if isinstance(g, Mapping)
        ]
        tested = {c.get("term") for c in cells if isinstance(c, Mapping)}
        adjusted = sorted({names.get(r["term"], r["term"]) for r in rows if r["term"] not in tested})

        # What distinguishes this analysis from its near-twins. Two contrasts can
        # agree on cells, measure, scope and statistic and still be different
        # analyses -- one per seed, one per hemisphere -- and then the reporting
        # table and the region are the only things that separate them.
        tables = [
            self.tables.get(t, t) for t in analysis.get("tables") or [] if isinstance(t, str)
        ]
        region = self._field_value(analysis.get("roi_label"))
        payloads = [
            name
            for name in (
                "connectivity",
                "decoding",
                "similarity",
                "conjunction",
                "latent_decomposition",
                "component_decomposition",
                "partial_least_squares",
                "other_method",
                "not_structurable",
            )
            if analysis.get(name)
        ]

        # Only lines the record actually has something for. Printing "(unstated)"
        # or "none recorded" for every empty slot made the block twice as long and
        # said nothing -- an absent line already says the record is silent.
        facts = [
            ("measure", self._field_value(measure.get("source_label"))),
            (
                "scope",
                ", ".join(
                    filter(
                        None,
                        [
                            self._field_value(analysis.get("spatial_scope")),
                            self._field_value(analysis.get("spatial_unit")),
                        ],
                    )
                ),
            ),
            ("region", region),
            ("method", ", ".join(payloads)),
            ("statistic", self._field_value(statistic.get("family"))),
            ("sample", ", ".join(groups)),
            ("adjusted for", ", ".join(adjusted[:8])),
            ("prespecification", self._field_value(analysis.get("prespecification"))),
            ("reported in", "; ".join(tables)),
        ]
        # `comparison` bolds each side itself; wrapping it again is the nesting.
        lines = [comparison, ""]
        lines += [f"- {label}: {value}" for label, value in facts if value]
        return "\n".join(lines)

    def _statistic_block(
        self, statistic: Mapping[str, Any], analysis: Mapping[str, Any]
    ) -> dict[str, Any]:
        """The statistic panel and the verdicts that have something to judge.

        Both are derived from what the record holds. An analysis with no family and no
        degrees of freedom gets an empty gate, so the block does not render at all --
        a radio group over an empty summary reads as a question the reviewer skipped
        rather than one that never applied. And an option is offered only when its
        subject exists: on this corpus every analysis records a family and none records
        a df, so `family_wrong` is always askable and `df_wrong` never is.
        """

        family = self._field_value(statistic.get("family"))
        numerator = self._field_value(statistic.get("degrees_of_freedom_numerator"))
        denominator = self._field_value(statistic.get("degrees_of_freedom_denominator"))
        has_df = bool(denominator or numerator)

        if not family and not has_df:
            return {"statistic": [], "statistic_options": []}

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
        wanted = ["statistic_correct"]
        if family:
            wanted.append("family_wrong")
        # Exactly one of these is answerable, and which one depends on the record.
        wanted.append("df_wrong" if has_df else "df_absent")
        return {
            "statistic": [{"summary": summary}],
            "statistic_options": [
                {"value": value, "hint": config_gen._STATISTIC_VERDICTS[value]}
                for value in wanted
            ],
        }

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

    def emit_table_tasks(self) -> None:
        """One task per coordinate table: is this the right set of analyses?

        Emitted for every table any analysis references as well as every table stage 1
        parsed, so a coordinate table the parser found nothing in still gets asked about
        -- that is the `not_analyses` and `missed_analysis` case, and skipping it would
        make the two indistinguishable from a table nobody looked at.
        """

        referenced = {
            pubget_id
            for analysis in self.record.get("analyses") or []
            if isinstance(analysis, Mapping)
            for local_id in analysis.get("tables") or []
            for pubget_id in self.pubget_of.get(local_id, [])
        }
        for table_id in sorted(set(self.parsed) | referenced):
            table = self._table(table_id)
            siblings = self.parsed.get(table_id, [])
            owner, contested = table_render.attribute_rows(table, siblings)
            names = [s.get("name") or "(unnamed)" for s in siblings]
            local_id = self.local_of.get(table_id, table_id)

            # The split and the rows it rests on, and nothing else. Re-rendering the
            # grid, fixing a caption or correcting a Group.name elsewhere must not
            # re-ask a segmentation question whose substance did not move.
            digest_of = [
                (name, sorted(row for row, holder in owner.items() if holder == index))
                for index, name in enumerate(names)
            ]
            data = self._frame("table", f"{self.paper_id}|table|{local_id}", digest_of)
            manifest = self.manifest.get(table_id) or {}
            data.update(
                task_kind="table",
                coordinate_status=(
                    "yes" if any(s.get("points") for s in siblings)
                    else "no_coordinates" if siblings else "no_contrast"
                ),
                local_id=local_id,
                table_id=table_id,
                cell_count=len(siblings),
                table_html=table_render.render_table_html(
                    table,
                    owner=owner,
                    contested=contested,
                    tints=len(config_gen._ANALYSIS_TINTS),
                    missing=local_id,
                ),
                # No trailing "+ new analysis" slot. One label cannot stand for two
                # missed analyses -- both spans came back wearing it, and nothing
                # downstream could tell them apart. The control is a Taxonomy in
                # labeling mode now, so a reviewer who finds one types its name, as
                # many times as the paper needs.
                structure_labels=[
                    {
                        "value": f"analysis: {name}",
                        "background": config_gen._SET_COLOURS[
                            index % len(config_gen._SET_COLOURS)
                        ],
                    }
                    for index, name in enumerate(names)
                ],
                table=[
                    {
                        "label": f"{manifest.get('table_label') or local_id}"
                        f"  ·  {len(siblings)} analys{'is' if len(siblings) == 1 else 'es'}"
                        " parsed",
                        # Not the caption: the grid below prints it, and printing it
                        # twice on one screen is most of what made this task long.
                        # What a reviewer needs before scanning is what the
                        # attribution could not settle.
                        "meta": table_render.attribution_note(
                            table, siblings, owner, contested
                        ),
                        "guidance": "Judge the SPLIT, not the encoding: is each numbered "
                        "block one real analysis, and does the table report one this list "
                        "misses? The encoding of each is reviewed separately.",
                    }
                ],
                sibling_rows=[
                    {
                        "label": f"#{index + 1}  ·  {name}",
                        "meta": self._sibling_meta(table_id, index, owner),
                    }
                    for index, name in enumerate(names)
                ],
                contrast=[],
                cell_rows=[],
                statistic=[],
                statistic_options=[],
            )
            self.tasks["contrast"].append({"data": data})
            self.report.table += 1

    def _contrast_table(self, analysis: Mapping[str, Any]) -> tuple[str, str, str]:
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
            # table must agree: this paper reports the same peak under two contrasts, so
            # raw matching claims five rows here while the table task -- which resolves
            # inside section blocks -- attributes three. A reviewer shown both would have
            # no way to tell which was lying.
            owner, contested = table_render.attribute_rows(
                table, self.parsed.get(table_id) or []
            )
            rows = [row for row, holder in owner.items() if holder == position]
            shared = {
                row: holders for row, holders in contested.items() if position in holders
            }
            total = (
                sum(1 for row in table["body"] if row["type"] == "data") if table else 0
            )
            # The legend above the grid carries both the encoding and the counts, so
            # the note is left for the cases a reviewer has to act on. A row count
            # restated underneath a 77-row table is read after it was needed, if at
            # all.
            note = "" if table else "The table could not be read."
            if table and not rows:
                note = (
                    "No row was attributed to this analysis. Either the parser missed "
                    "them or this contrast is reported somewhere other than this table."
                )
            label = (self.manifest.get(table_id) or {}).get("table_label") or table_id
            # A weak name match is the one thing here a reviewer must act on -- it
            # means the highlighted rows may belong to a different contrast -- so it
            # is printed only when it is weak. Printing "Name match 1.00" on every
            # task spent a line on a diagnostic that was almost always fine.
            parsed_line = (
                f"{label} · analysis {position + 1} of {len(self.parsed[table_id])} · "
                f"{len(sibling.get('points') or [])} points"
            )
            if score < _WEAK_MATCH:
                parsed_line += f" · weak name match {score:.2f}, check the rows are this contrast's"
            return (
                table_id,
                table_render.render_table_html(
                    table,
                    highlight=rows,
                    contested=shared,
                    note=note,
                    missing=local_id,
                ),
                parsed_line,
            )

        tables = [t for t in analysis.get("tables") or [] if isinstance(t, str)]
        pubget_ids = [pid for t in tables for pid in self.pubget_of.get(t, [])]
        table_id = pubget_ids[0] if pubget_ids else ""
        table = self._table(table_id) if table_id else None
        return (
            table_id,
            table_render.render_table_html(
                table,
                note="No parsed analysis matched this record, so no row is marked.",
                missing=tables[0] if tables else "this analysis",
            ),
            "No stage-1 analysis matched this record: the rows it rests on are unknown, "
            "which is itself worth reporting on the table task.",
        )

    def emit_contrast_tasks(self) -> None:
        models = self._models()
        for analysis in self.record.get("analyses") or []:
            if not isinstance(analysis, Mapping):
                continue
            local_id = analysis.get("local_id", "")
            model = models.get(analysis.get("model_estimation")) or {}
            rows = self._term_rows(model)

            effect = analysis.get("effect") or {}
            cells = effect.get("cells") or []
            existing = {}
            for cell in cells:
                if isinstance(cell, Mapping):
                    key = (cell.get("term", ""), self._field_value(cell.get("level")))
                    existing[key] = self._field_value(cell.get("direction")).lower()

            predictions = []
            for index, row in enumerate(rows):
                direction = existing.get((row["term"], row["level"]))
                if direction and "pos" in direction:
                    choice = "positive"
                elif direction and "neg" in direction:
                    choice = "negative"
                else:
                    choice = "absent"
                # Gate index is 0: the `contrast` array holds one element.
                predictions.append(self._choice(f"cell_0_{index}", [choice]))

            definition = analysis.get("definition") if isinstance(
                analysis.get("definition"), Mapping
            ) else {}
            sets = ((definition.get("evidence") or {}).get("sets")) or []
            labels = []
            spans = self._span_predictions("st_c_0", sets, "definition")
            if sets:
                labels.insert(0, {"value": "definition", "background": "#03a9f4"})

            statistic = effect.get("statistic") or {}
            digest_of = sorted(
                (t, lv, d) for (t, lv), d in existing.items()
            ) + [self._field_value(statistic.get("family"))]
            data = self._frame("contrast", f"{self.paper_id}|contrast|{local_id}", digest_of)
            status = self.coordinate_status(analysis)
            table_id, table_html, parsed_line = self._contrast_table(analysis)
            data.update(
                task_kind="contrast",
                coordinate_status=status,
                local_id=local_id,
                table_id=table_id,
                cell_count=len(rows),
                table_html=table_html,
                structure_labels=labels,
                table=[],
                sibling_rows=[],
                contrast=[
                    {
                        "label": f"{local_id}  ·  "
                        f"{self._field_value(analysis.get('name')) or '(unnamed)'}",
                        # No quoted evidence. The task highlights it in the paper
                        # instead, which is where it can be read against the sentences
                        # either side of it. What this line adds is the case the
                        # highlight cannot express: 4 of the 18 analyses across the
                        # baseline papers have a definition with no evidence at all, and
                        # without a word here their reviewer would hunt the pane for a
                        # highlight that was never drawn.
                        "parsed": parsed_line
                        + ("" if sets else "  ·  no evidence highlighted: the record's "
                           "definition carries no span"),
                        "paraphrase": self._paraphrase(analysis, rows),
                    }
                ],
                cell_rows=rows,
                **self._statistic_block(statistic, analysis),
            )
            task: dict[str, Any] = {"data": data}
            result = spans + predictions
            if result:
                task["predictions"] = [{"model_version": self.model_version, "result": result}]
            self.tasks["contrast"].append(task)
            self.report.contrast += 1

    # -- driver -------------------------------------------------------------

    #: Statuses that mean "not tied to a reported result", used by
    #: --coordinates-only.
    WITHOUT_COORDINATES = {"no_table", "no_contrast", "no_coordinates"}

    coordinates_only = False

    def run(self) -> None:
        self.emit_entity_inventories()
        self.emit_value_tasks()
        self.emit_relationship_tasks()
        self.emit_model_tasks()
        self.emit_table_tasks()
        self.emit_contrast_tasks()

        if self.coordinates_only:
            dropped = []
            # `table` is deliberately not droppable: a coordinate table nothing rests on
            # is the `not_analyses` finding, and skipping it makes that indistinguishable
            # from a table nobody looked at.
            for family in ("structure", "contrast"):
                kept = []
                for task in self.tasks[family]:
                    status = task["data"].get("coordinate_status")
                    if (
                        task["data"]["task_kind"] in ("model", "contrast")
                        and status in self.WITHOUT_COORDINATES
                    ):
                        dropped.append(f"{task['data']['review_key']} ({status})")
                    else:
                        kept.append(task)
                self.tasks[family] = kept
            # Never a silent cap: what was left out is reported.
            for note in dropped:
                self.report.skipped.append(f"--coordinates-only skipped {note}")

    def check_contract(self) -> list[str]:
        """Every task must carry exactly the keys its config reads."""

        problems = []
        for family, tasks in self.tasks.items():
            contract = set(config_gen.DATA_CONTRACT[family])
            for task in tasks:
                keys = set(task["data"])
                missing = contract - keys
                extra = keys - contract
                if missing:
                    problems.append(f"{family} {task['data']['review_key']}: missing {sorted(missing)}")
                if extra:
                    problems.append(f"{family} {task['data']['review_key']}: extra {sorted(extra)}")
        return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--text", required=True, type=Path)
    parser.add_argument("--identifiers", type=Path)
    parser.add_argument("--files-root", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--coordinate-counts",
        type=Path,
        help="JSON from the table parser: {table_local_id: n_coordinates}, or "
        "{paper_id: {table_local_id: n}}. Table.coordinate_count is storage-only, "
        "so it cannot come out of the extraction record.",
    )
    parser.add_argument(
        "--texts-root",
        type=Path,
        help="review/texts; the pubget source, stage-1 parse and table map for this "
        "paper are derived from it unless given explicitly",
    )
    parser.add_argument("--pubget-dir", type=Path)
    parser.add_argument("--stage1", type=Path)
    parser.add_argument("--table-map", type=Path)
    parser.add_argument(
        "--coordinates-only",
        action="store_true",
        help="emit model and contrast tasks only where the result is reported as "
        "coordinates; skipped tasks are counted and reported, never dropped silently",
    )
    args = parser.parse_args()

    record = json.loads(args.record.read_text(encoding="utf-8"))
    normalized = text_index.normalize(args.text.read_text(encoding="utf-8"))
    paper_id = args.record.name.split(".")[0]
    identifiers = (
        json.loads(args.identifiers.read_text(encoding="utf-8"))
        if args.identifiers and args.identifiers.is_file()
        else {}
    )

    expected = (record.get("extraction_metadata") or {}).get("source_text_hash")
    url = legacy.stage_text(args.files_root, paper_id, normalized, expected)

    exporter = TaskExporter(record, normalized, paper_id, identifiers, url)
    exporter.load_coordinate_counts(
        json.loads(args.coordinate_counts.read_text(encoding="utf-8"))
        if args.coordinate_counts
        else None
    )
    root = args.texts_root / paper_id if args.texts_root else None
    exporter.load_tables(
        args.pubget_dir or (root / "source" / "pubget" if root else None),
        args.stage1 or (root / "stage1" / "analyses.json" if root else None),
        args.table_map or (root / "stage1" / "table-map.json" if root else None),
    )
    exporter.coordinates_only = args.coordinates_only
    exporter.run()

    problems = exporter.check_contract()
    if problems:
        print("CONTRACT VIOLATIONS:", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for family, tasks in exporter.tasks.items():
        path = args.out_dir / f"{paper_id}.tasks_{family}.json"
        path.write_text(json.dumps(tasks, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {path} ({len(tasks)} tasks, {path.stat().st_size / 1024:.0f} KB)")

    report = exporter.report
    print(
        f"  {paper_id}: {report.total} tasks "
        f"(value {report.value}, relationship {report.relationship}, "
        f"entities {report.entities}, model {report.model}, "
        f"table {report.table}, contrast {report.contrast})"
    )
    print(f"  predicted: {report.predicted_spans} spans, {report.predicted_choices} choices")
    for note in report.skipped:
        print(f"  skipped {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
