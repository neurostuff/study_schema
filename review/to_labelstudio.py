#!/usr/bin/env python3
"""Turn an extraction record into one Label Studio task per reviewable attribute.

The paper text is deliberately absent from every task. A task carries only a URL
that Label Studio's local-files endpoint serves, so one paper's ~700 tasks share
a single cached copy of the text instead of embedding ~25-60 KB each.

The text is staged by this script rather than assumed, and staging asserts that
sha256(staged text) equals the record's source_text_hash. That closes the loop:
the bytes reviewers see are the bytes the offsets were computed against.

Extraction spans are emitted as Label Studio *predictions*, so a reviewer opens a
task with the LLM's evidence already highlighted and adjusts rather than
annotating from scratch.

Usage:
    python review/to_labelstudio.py \
        --record review/examples/2abntY3hQSyq.extraction.json \
        --text review/texts/2abntY3hQSyq/processed/pubget/text.txt \
        --identifiers review/texts/2abntY3hQSyq/identifiers.json \
        --files-root review/ls_files \
        --out-dir review/ls_tasks
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config_gen
import spans as span_tools
import text_index

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import schema_utils  # noqa: E402  (repo root is added above)

import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXTRACTION_SCHEMA = ROOT / "neuroimaging-study-extraction.yaml"
PRIORITIES = ROOT / "storage-parameter-priorities.yaml"

#: Label Studio serves LOCAL_FILES_DOCUMENT_ROOT under this endpoint.
_LOCAL_FILES_URL = "/data/local-files/?d={relative}"

#: Subdirectory of the files root holding one text per paper.
_TEXT_SUBDIR = "texts"

_NOT_REPORTED = "(not reported by the extractor)"
_NO_EVIDENCE = "no evidence recorded"

#: Characters of surrounding context shown either side of a span in the excerpt.
#: Enough to see the sentence it sits in without reproducing the paragraph.
_CONTEXT = 140

#: The exact span is delimited so a reviewer can see where the extractor drew the
#: boundary, which is what distinguishes wrong_evidence from a boundary problem.
_OPEN, _CLOSE = "《", "》"  # 《 》


def _excerpt(normalized: str, sets: list[Any]) -> str:
    """Render evidence with surrounding context, so verifying needs no scrolling.

    The paper pane still holds the full text for correction and re-spanning; this
    is a reading aid for the common case where the reviewer only has to decide
    whether the quoted passage supports the value.
    """

    if not sets:
        return _NO_EVIDENCE

    lines: list[str] = []
    for set_index, evidence_set in enumerate(sets):
        spans = evidence_set.get("spans") or []
        rendered = []
        for span in spans:
            start, end = span["start_char"], span["end_char"]
            left = normalized[max(0, start - _CONTEXT) : start].replace("\n", " ")
            right = normalized[end : min(len(normalized), end + _CONTEXT)].replace("\n", " ")
            middle = normalized[start:end].replace("\n", " ")

            # Trim ragged context back to a word boundary rather than mid-token.
            if start - _CONTEXT > 0 and " " in left:
                left = "…" + left.split(" ", 1)[1]
            if end + _CONTEXT < len(normalized) and " " in right:
                right = right.rsplit(" ", 1)[0] + "…"

            rendered.append(f"{left.strip()} {_OPEN}{middle}{_CLOSE} {right.strip()}")

        joined = "\n   + ".join(rendered)
        prefix = f"set {set_index + 1}" if len(sets) > 1 else "evidence"
        detail = f" ({len(spans)} spans, all required)" if len(spans) > 1 else ""
        lines.append(f"{prefix}{detail}: {joined}")
    return "\n".join(lines)


@dataclass
class ExportReport:
    evidence_tasks: int = 0
    reference_tasks: int = 0
    predicted_spans: int = 0
    tasks_without_priority: list[str] = field(default_factory=list)
    unresolved_targets: list[str] = field(default_factory=list)
    clamped_sets: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"evidence tasks={self.evidence_tasks}, reference tasks={self.reference_tasks}, "
            f"total={self.evidence_tasks + self.reference_tasks}\n"
            f"predicted spans={self.predicted_spans}"
        )


def load_priorities() -> dict[tuple[str, str], object]:
    document = yaml.safe_load(PRIORITIES.read_text(encoding="utf-8")) or {}
    return {
        (class_name, field_name): value
        for class_name, fields in document.items()
        if isinstance(fields, Mapping)
        for field_name, value in fields.items()
    }


def _display(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _entity_label(entity: Mapping[str, Any]) -> str:
    """A human-readable handle for an entity, from whichever name-ish slot it has."""

    for slot in ("name", "title", "map_type"):
        candidate = entity.get(slot)
        if isinstance(candidate, Mapping) and candidate.get("extraction_status") == "extracted":
            value = candidate.get("value")
            if value not in (None, ""):
                return _display(value)
    return ""


def _target_class(classes: Mapping[str, Any], attribute: Mapping[str, Any]) -> str | None:
    """Which class a cross-reference points at.

    The range says it. This used to guess from the description -- the longest class name
    mentioned in "local_ids of Acquisition records" -- because the hand-written extraction
    schema declared every reference as `range: string`. The projected schema keeps the
    real range, so there is nothing left to infer.
    """

    attribute_range = attribute.get("range")
    return attribute_range if attribute_range in classes else None


class Exporter:
    def __init__(
        self,
        record: Mapping[str, Any],
        normalized: str,
        paper_id: str,
        identifiers: Mapping[str, Any],
        paper_title: str,
        paper_url: str,
        max_sets: int = config_gen.DEFAULT_MIN_SETS,
    ) -> None:
        self.record = record
        self.normalized = normalized
        self.paper_id = paper_id
        self.identifiers = identifiers
        self.paper_title = paper_title
        self.paper_url = paper_url
        self.max_sets = max_sets

        self.classes = schema_utils.load_imported_classes(EXTRACTION_SCHEMA)
        self.priorities = load_priorities()
        self.sections = text_index.build_sections(normalized)
        self.report = ExportReport()

        metadata = record.get("extraction_metadata") or {}
        self.model_version = (
            f"{metadata.get('extractor_model', 'unknown')}"
            f"@{metadata.get('extractor_version', 'unknown')}"
        )
        self.declared_ids = self._collect_declared_ids()

        self.evidence_tasks: list[dict[str, Any]] = []
        self.reference_tasks: list[dict[str, Any]] = []

    # -- entity inventory --------------------------------------------------

    def _collect_declared_ids(self) -> dict[str, list[tuple[str, str]]]:
        """Map class name -> [(local_id, label)] so reference tasks can list candidates."""

        study_attributes = schema_utils.attributes_for(self.classes, "Study")
        inventory: dict[str, list[tuple[str, str]]] = {}
        for attr, attribute in study_attributes.items():
            if schema_utils.classify_slot(self.classes, attr, attribute) != "nested":
                continue
            target = attribute.get("range")
            if not isinstance(target, str):
                continue
            for entity in self.record.get(attr, []) or []:
                if isinstance(entity, Mapping) and isinstance(entity.get("local_id"), str):
                    inventory.setdefault(target, []).append(
                        (entity["local_id"], _entity_label(entity))
                    )
        return inventory

    # -- traversal ---------------------------------------------------------

    def run(self) -> None:
        study_attributes = schema_utils.attributes_for(self.classes, "Study")
        for attr, attribute in study_attributes.items():
            if attr == "extraction_metadata" or attr not in self.record:
                continue
            kind = schema_utils.classify_slot(self.classes, attr, attribute)
            value = self.record[attr]

            if kind == "evidence":
                self._emit_evidence("Study", "", "", attr, attribute, value)
            elif kind == "reference":
                self._emit_reference("Study", "", "", attr, attribute, value)
            elif kind == "nested":
                target = attribute.get("range")
                if not isinstance(target, str):
                    continue
                for entity in value if isinstance(value, list) else [value]:
                    if isinstance(entity, Mapping):
                        self._visit_entity(entity, target, "")

    def _visit_entity(self, entity: Mapping[str, Any], class_name: str, prefix: str) -> None:
        local_id = entity.get("local_id") if isinstance(entity.get("local_id"), str) else ""
        label = _entity_label(entity)
        attributes = schema_utils.attributes_for(self.classes, class_name)

        for name, attribute in attributes.items():
            if name not in entity:
                continue
            kind = schema_utils.classify_slot(self.classes, name, attribute)
            value = entity[name]
            path = f"{prefix}{name}" if prefix else name
            multivalued = bool(attribute.get("multivalued"))

            if kind == "evidence":
                if multivalued and isinstance(value, list):
                    for index, item in enumerate(value):
                        self._emit_evidence(
                            class_name, local_id, label, f"{path}[{index}]", attribute, item
                        )
                else:
                    self._emit_evidence(class_name, local_id, label, path, attribute, value)
            elif kind == "reference":
                self._emit_reference(class_name, local_id, label, path, attribute, value)
            elif kind == "nested":
                target = attribute.get("range")
                if not isinstance(target, str):
                    continue
                items = value if isinstance(value, list) else [value]
                for index, item in enumerate(items):
                    if not isinstance(item, Mapping):
                        continue
                    suffix = f"[{index}]" if isinstance(value, list) else ""
                    # Nested value objects have no local_id of their own, so they
                    # stay attached to the owning entity and keep its handle.
                    self._visit_nested(item, target, local_id, label, f"{path}{suffix}.")

    def _visit_nested(
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
            path = f"{prefix}{name}"

            if kind == "evidence":
                if attribute.get("multivalued") and isinstance(value, list):
                    for index, item in enumerate(value):
                        self._emit_evidence(
                            class_name, owner_id, owner_label, f"{path}[{index}]", attribute, item
                        )
                else:
                    self._emit_evidence(class_name, owner_id, owner_label, path, attribute, value)
            elif kind == "reference":
                self._emit_reference(class_name, owner_id, owner_label, path, attribute, value)
            elif kind == "nested":
                target = attribute.get("range")
                if isinstance(target, str):
                    items = value if isinstance(value, list) else [value]
                    for index, item in enumerate(items):
                        if isinstance(item, Mapping):
                            suffix = f"[{index}]" if isinstance(value, list) else ""
                            self._visit_nested(
                                item, target, owner_id, owner_label, f"{path}{suffix}."
                            )

    # -- task construction -------------------------------------------------

    def _base_data(
        self, class_name: str, local_id: str, entity_label: str, field_path: str,
        attribute: Mapping[str, Any], kind: str,
    ) -> dict[str, Any]:
        # Priority is keyed on the leaf slot of the owning class, matching how
        # storage-parameter-priorities.yaml is written.
        leaf = field_path.split(".")[-1].split("[")[0]
        priority = self.priorities.get((class_name, leaf), "unranked")
        if priority == "unranked":
            self.report.tasks_without_priority.append(f"{class_name}.{leaf}")

        handle = f'{class_name} "{local_id}"' if local_id else class_name
        if entity_label:
            handle = f"{handle} — {entity_label}"

        return {
            "paper_id": self.paper_id,
            "paper_url": self.paper_url,
            "paper_title": self.paper_title,
            "paper_citation": self._citation(),
            "pmid": str(self.identifiers.get("pmid", "")),
            "doi": str(self.identifiers.get("doi", "")),
            "entity_class": class_name,
            "local_id": local_id,
            "entity_label": entity_label,
            "field_path": field_path,
            "field_kind": kind,
            "priority": priority,
            "field_label": f"{handle}  ·  {field_path}  ·  priority {priority}",
            "field_description": attribute.get("description") or "(no description in schema)",
            "review_key": f"{self.paper_id}|{class_name}|{local_id}|{field_path}",
        }

    def _citation(self) -> str:
        parts = [self.paper_id]
        if self.identifiers.get("pmid"):
            parts.append(f"pmid {self.identifiers['pmid']}")
        if self.identifiers.get("doi"):
            parts.append(str(self.identifiers["doi"]))
        return "  ·  ".join(parts)

    def _emit_evidence(
        self, class_name: str, local_id: str, entity_label: str, field_path: str,
        attribute: Mapping[str, Any], node: Any,
    ) -> None:
        if not isinstance(node, Mapping) or "extraction_status" not in node:
            return

        data = self._base_data(
            class_name, local_id, entity_label, field_path, attribute, "evidence"
        )
        status = node.get("extraction_status")
        evidence = node.get("evidence") or {}
        sets = evidence.get("sets") or []

        data["llm_status"] = status
        data["value_source"] = node.get("value_source", "")
        data["evidence_status"] = evidence.get("status", "")
        data["llm_value_display"] = (
            f"LLM value: {_display(node['value'])}" if "value" in node else f"LLM: {_NOT_REPORTED}"
        )

        span_count = sum(len(s.get("spans", [])) for s in sets)
        if sets:
            first = sets[0]["spans"][0]["start_char"]
            section = text_index.section_path(self.sections, first) or "unindexed region"
            shape = f"{len(sets)} set(s), {span_count} span(s)"
        else:
            section = ""
            shape = _NO_EVIDENCE
        data["section_hint"] = section
        data["evidence_excerpt"] = _excerpt(self.normalized, sets)
        data["llm_meta"] = "  ·  ".join(
            filter(None, [f"status {status}", f"source {data['value_source']}", shape, section])
        )

        task: dict[str, Any] = {"data": data}
        predictions = self._predictions(sets, data["review_key"])
        if predictions:
            task["predictions"] = predictions
        self.evidence_tasks.append(task)
        self.report.evidence_tasks += 1

    def _predictions(self, sets: list[Any], review_key: str) -> list[dict[str, Any]]:
        """Build span predictions, bounded by the labels the config declares.

        A prediction naming an undeclared label is accepted by the server (HTTP
        201) but has no label to bind to in the editor, so the span silently
        fails to render -- the worst failure mode available, since the reviewer
        sees a task with no highlight and no error. Overflow sets are therefore
        folded into the last declared label and reported, never emitted as-is.
        """

        results: list[dict[str, Any]] = []
        if len(sets) > self.max_sets:
            self.report.clamped_sets.append(f"{review_key} ({len(sets)} sets)")
        for set_index, evidence_set in enumerate(sets):
            label_index = min(set_index, self.max_sets - 1)
            for span_index, span in enumerate(evidence_set.get("spans", [])):
                # Guard again at export time: a task must never ship an offset
                # that does not address the text we are about to serve.
                span_tools.verify(self.normalized, span)
                results.append(
                    {
                        "id": f"s{set_index}_{span_index}",
                        "from_name": "ev",
                        "to_name": "paper",
                        "type": "labels",
                        "value": {
                            "start": span["start_char"],
                            "end": span["end_char"],
                            "text": span["text"],
                            "labels": [f"set {label_index + 1}"],
                        },
                    }
                )
                self.report.predicted_spans += 1
        if not results:
            return []
        return [{"model_version": self.model_version, "result": results}]

    def _emit_reference(
        self, class_name: str, local_id: str, entity_label: str, field_path: str,
        attribute: Mapping[str, Any], value: Any,
    ) -> None:
        refs = [value] if isinstance(value, str) else [r for r in value or [] if isinstance(r, str)]

        data = self._base_data(
            class_name, local_id, entity_label, field_path, attribute, "reference"
        )
        data["llm_status"] = "extracted" if refs else "not_reported"
        data["evidence_status"] = "not_applicable"
        data["value_source"] = ""
        data["section_hint"] = ""
        data["evidence_excerpt"] = "cross-references carry no evidence in the schema"
        data["llm_value_display"] = (
            f"LLM links to: {', '.join(refs)}" if refs else f"LLM: {_NOT_REPORTED}"
        )

        target = _target_class(self.classes, attribute)
        if target is None:
            self.report.unresolved_targets.append(f"{class_name}.{field_path}")
            candidates = "the slot's range does not name a class in the schema"
        else:
            listed = self.declared_ids.get(target, [])
            candidates = (
                f"Candidate {target} local_ids: "
                + ("; ".join(f"{cid} ({label})" if label else cid for cid, label in listed))
                if listed
                else f"No {target} entities were extracted from this paper"
            )
        data["candidates_display"] = candidates
        data["llm_meta"] = f"cross-reference to {target or 'unknown'}  ·  no evidence in schema"

        self.reference_tasks.append({"data": data})
        self.report.reference_tasks += 1


def stage_text(files_root: Path, paper_id: str, normalized: str, expected_hash: str | None) -> str:
    """Write the normalized text where Label Studio can serve it; return its URL."""

    if expected_hash:
        actual = text_index.text_hash(normalized)
        if actual != expected_hash:
            raise SystemExit(
                f"refusing to stage: text hash {actual[:12]}... does not match the record's "
                f"source_text_hash {expected_hash[:12]}..."
            )

    destination = files_root / _TEXT_SUBDIR / f"{paper_id}.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    # newline="" so Python does not translate \n on write; the served bytes must
    # be exactly the bytes that were hashed and that offsets address.
    with destination.open("w", encoding="utf-8", newline="") as stream:
        stream.write(normalized)

    return _LOCAL_FILES_URL.format(relative=f"{_TEXT_SUBDIR}/{paper_id}.txt")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--text", required=True, type=Path)
    parser.add_argument("--identifiers", type=Path)
    parser.add_argument("--files-root", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--paper-id", help="defaults to the record filename stem")
    parser.add_argument(
        "--max-sets",
        type=int,
        default=config_gen.DEFAULT_MIN_SETS,
        help="evidence-set labels the config declares; must match config_gen",
    )
    args = parser.parse_args()

    record = json.loads(args.record.read_text(encoding="utf-8"))
    normalized = text_index.normalize(args.text.read_text(encoding="utf-8"))
    paper_id = args.paper_id or args.record.name.split(".")[0]
    identifiers = (
        json.loads(args.identifiers.read_text(encoding="utf-8")) if args.identifiers else {}
    )

    metadata = record.get("extraction_metadata") or {}
    paper_url = stage_text(
        args.files_root, paper_id, normalized, metadata.get("source_text_hash")
    )

    title_field = record.get("title") or {}
    paper_title = _display(title_field.get("value", paper_id)) if title_field else paper_id

    exporter = Exporter(
        record, normalized, paper_id, identifiers, paper_title, paper_url, args.max_sets
    )
    exporter.run()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, tasks in (
        ("tasks_evidence", exporter.evidence_tasks),
        ("tasks_reference", exporter.reference_tasks),
    ):
        path = args.out_dir / f"{paper_id}.{name}.json"
        path.write_text(json.dumps(tasks, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append((path, len(tasks)))

    print(f"staged text -> {args.files_root / _TEXT_SUBDIR / f'{paper_id}.txt'}")
    print(f"task url    -> {paper_url}")
    for path, count in written:
        print(f"wrote {path} ({count} tasks, {path.stat().st_size // 1024} KB)")
    print(exporter.report.summary())

    if exporter.report.tasks_without_priority:
        unique = sorted(set(exporter.report.tasks_without_priority))
        print(f"\nslots with no priority entry ({len(unique)}): {', '.join(unique)}")
    if exporter.report.clamped_sets:
        print(
            f"\nWARNING: {len(exporter.report.clamped_sets)} field(s) had more evidence sets "
            f"than the {args.max_sets} labels the config declares; overflow folded into the "
            f"last set. Raise --min-sets on config_gen.py and --max-sets here:"
        )
        for entry in exporter.report.clamped_sets:
            print(f"  - {entry}")
    if exporter.report.unresolved_targets:
        print(f"\nreference slots with unresolved target class: {exporter.report.unresolved_targets}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
