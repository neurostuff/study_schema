"""Assemble an extraction record from extractor payloads, resolving quotes to offsets.

The extractor emits verbatim quotes rather than character offsets, because a
language model cannot count characters reliably. This module locates each quote
in the normalized source text and rewrites

    {"evidence": {"status": "present", "sets": [{"quotes": ["..."]}]}}

into the schema's shape

    {"evidence": {"status": "present", "sets": [{"spans": [{text, start_char, end_char}]}]}}

Every emitted span is verified against the source, so a record that builds is a
record whose offsets are correct by construction. Quotes that cannot be located
are reported and their field is downgraded rather than silently dropped.

Usage:
    python review/build_record.py --paper 2abntY3hQSyq \
        --text review/texts/2abntY3hQSyq/processed/pubget/text.txt \
        --payloads review/payloads/2abntY3hQSyq \
        --out review/examples/2abntY3hQSyq.extraction.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import spans as span_tools
import text_index

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import schema_utils  # noqa: E402  (repo root is added above)

EXTRACTION_SCHEMA = Path(__file__).resolve().parent.parent / "neuroimaging-study-extraction.yaml"

def _entity_lists() -> dict[str, str]:
    """Payload key -> dotted path to the Study attribute holding the inlined list.

    Derived from the schema rather than written out, because a hardcoded list
    silently drops whatever it has not caught up with: `arms` and `timepoints`
    were added to Study and every intervention and longitudinal paper's payload
    lost them, reported only as an "unexpected payload key" note.

    An extractor emits one file per entity kind, so the payload key is a bare
    entity name however deep the schema puts the list. Most sit directly on Study
    and the mapping is the identity; `arms` and `timepoints` sit one level down
    under `design`, so a single level of inlined singleton is followed. Deeper
    nesting is not: the payload would need a path of its own to be unambiguous.
    """

    classes = schema_utils.load_imported_classes(EXTRACTION_SCHEMA)
    study = schema_utils.attributes_for(classes, "Study")

    def lists_on(attributes: Mapping[str, Any], prefix: str = "") -> dict[str, str]:
        return {
            name: f"{prefix}{name}"
            for name, attribute in attributes.items()
            if isinstance(attribute, Mapping) and attribute.get("multivalued")
        }

    found = lists_on(study)
    for name, attribute in study.items():
        if not isinstance(attribute, Mapping) or attribute.get("multivalued"):
            continue
        if attribute.get("inlined") is not True:
            continue
        nested = attribute.get("range")
        if nested not in classes:
            continue
        for key, path in lists_on(
            schema_utils.attributes_for(classes, nested), f"{name}."
        ).items():
            found.setdefault(key, path)
    return found


_ENTITY_LISTS = _entity_lists()

# Keys that are extractor scaffolding, not schema content.
_SCAFFOLDING = {"cross_reference_notes"}

# Payload filename holding local_id reconciliation, excluded from the merge.
_ALIAS_FILE = "aliases.json"


@dataclass
class BuildReport:
    resolved_exact: int = 0
    resolved_tolerant: int = 0
    failures: list[str] = field(default_factory=list)
    fields_total: int = 0
    fields_extracted: int = 0
    fields_not_reported: int = 0
    fields_evidence_present: int = 0
    fields_evidence_not_found: int = 0
    downgraded: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"fields={self.fields_total} "
            f"(extracted={self.fields_extracted}, not_reported={self.fields_not_reported})\n"
            f"evidence: present={self.fields_evidence_present}, "
            f"not_found={self.fields_evidence_not_found}\n"
            f"spans: exact={self.resolved_exact}, whitespace-tolerant={self.resolved_tolerant}, "
            f"unresolved={len(self.failures)}\n"
            f"downgraded fields={len(self.downgraded)}"
        )


def _is_field(node: Any) -> bool:
    return isinstance(node, dict) and "extraction_status" in node


def _resolve_field(
    node: dict[str, Any], normalized: str, folded: str, path: str, report: BuildReport
) -> None:
    """Rewrite one FIELD object's evidence quotes into verified spans, in place."""

    report.fields_total += 1
    status = node.get("extraction_status")
    if status == "extracted":
        report.fields_extracted += 1
    elif status == "not_reported":
        report.fields_not_reported += 1

    evidence = node.get("evidence")
    if not isinstance(evidence, dict):
        return

    raw_sets = evidence.get("sets")
    if not isinstance(raw_sets, list):
        if evidence.get("status") == "not_found":
            report.fields_evidence_not_found += 1
        return

    rebuilt: list[dict[str, Any]] = []
    for index, evidence_set in enumerate(raw_sets):
        quotes = evidence_set.get("quotes") if isinstance(evidence_set, dict) else None
        if not isinstance(quotes, list):
            continue
        resolved: list[dict[str, object]] = []
        for quote in quotes:
            try:
                found = span_tools.resolve(normalized, quote, folded_text=folded)
            except span_tools.SpanResolutionError as error:
                report.failures.append(f"{path} set[{index}]: {error}")
                continue
            if found.exact:
                report.resolved_exact += 1
            else:
                report.resolved_tolerant += 1
            resolved.append(found.as_record())

        # An EvidenceSet requires at least one span (minimum_cardinality: 1), so a
        # set whose every quote failed to resolve cannot be emitted at all.
        if resolved:
            rebuilt.append({"spans": resolved})

    if rebuilt:
        evidence["sets"] = rebuilt
        evidence["status"] = "present"
        report.fields_evidence_present += 1
    else:
        # No usable evidence survived. The value may still be right, so keep it
        # and record that support was not located rather than deleting the field.
        evidence.pop("sets", None)
        if status == "extracted":
            evidence["status"] = "not_found"
            report.fields_evidence_not_found += 1
            report.downgraded.append(path)
        else:
            evidence["status"] = "not_applicable"


def _walk(node: Any, normalized: str, folded: str, path: str, report: BuildReport) -> None:
    if _is_field(node):
        _resolve_field(node, normalized, folded, path, report)
        return
    if isinstance(node, dict):
        for key, value in node.items():
            _walk(value, normalized, folded, f"{path}.{key}" if path else str(key), report)
        return
    if isinstance(node, list):
        for index, value in enumerate(node):
            _walk(value, normalized, folded, f"{path}[{index}]", report)


def merge_payloads(payload_dir: Path) -> dict[str, Any]:
    """Merge extractor payloads into a single Study body.

    Each agent covers a disjoint set of classes, so entity lists concatenate and
    the `study` mapping is a shallow union. Overlapping study attributes are a
    prompt bug; the later payload wins and the collision is reported.
    """

    study: dict[str, Any] = {}
    lists: dict[str, list[Any]] = {}
    collisions: list[str] = []

    for path in sorted(payload_dir.glob("*.json")):
        if path.name == _ALIAS_FILE:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key, value in payload.items():
            if key in _SCAFFOLDING:
                continue
            if key == "study" and isinstance(value, dict):
                for attr, attr_value in value.items():
                    if attr in study:
                        collisions.append(f"{attr} (from {path.name})")
                    study[attr] = attr_value
            elif key in _ENTITY_LISTS and isinstance(value, list):
                lists.setdefault(_ENTITY_LISTS[key], []).extend(value)
            else:
                collisions.append(f"unexpected payload key {key!r} in {path.name}")

    if collisions:
        print("payload merge notes:")
        for note in collisions:
            print(f"  - {note}")

    body = dict(study)
    for attr, items in lists.items():
        if not items:
            continue
        holder = body
        *ancestors, leaf = attr.split(".")
        for step in ancestors:
            holder = holder.setdefault(step, {})
        holder[leaf] = items
    return body


def load_aliases(payload_dir: Path) -> dict[str, str]:
    path = payload_dir / _ALIAS_FILE
    if not path.is_file():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    aliases = document.get("aliases", {})
    return {k: v for k, v in aliases.items() if isinstance(k, str) and isinstance(v, str)}


def apply_aliases(
    body: dict[str, Any], classes: Mapping[str, Any], aliases: dict[str, str]
) -> int:
    """Rewrite cross-reference slots through the alias map, in place.

    Only slots the schema classifies as references are touched, so an alias can
    never corrupt an extracted value that happens to share a string with an id.
    """

    if not aliases:
        return 0
    rewrites = 0

    def visit(node: Any, class_name: str) -> None:
        nonlocal rewrites
        if not isinstance(node, dict) or _is_field(node):
            return
        attributes = schema_utils.attributes_for(classes, class_name)
        for key, value in list(node.items()):
            attribute = attributes.get(key)
            if attribute is None:
                continue
            kind = schema_utils.classify_slot(classes, key, attribute)
            if kind == "reference":
                if isinstance(value, str) and value in aliases:
                    node[key] = aliases[value]
                    rewrites += 1
                elif isinstance(value, list):
                    for index, ref in enumerate(value):
                        if isinstance(ref, str) and ref in aliases:
                            value[index] = aliases[ref]
                            rewrites += 1
            elif kind == "nested":
                target = attribute.get("range")
                if isinstance(target, str):
                    for item in value if isinstance(value, list) else [value]:
                        visit(item, target)

    study_attributes = schema_utils.attributes_for(classes, "Study")
    for attr in _ENTITY_LISTS.values():
        attribute = study_attributes.get(attr)
        target = attribute.get("range") if isinstance(attribute, Mapping) else None
        if isinstance(target, str):
            for entity in body.get(attr, []) or []:
                visit(entity, target)
    return rewrites


def check_local_ids(body: dict[str, Any], classes: Mapping[str, Any]) -> list[str]:
    """Verify that every cross-reference resolves to a declared local_id.

    Which slots are cross-references is derived from the schema rather than
    hardcoded: a slot is a reference when its range is a native string and it is
    not local_id. That distinction matters because sibling slots differ --
    PredictorSource.group is a local_id string while PredictorSource.other is an
    ExtractedString, and Predictor.source is a nested object, not a reference.
    """

    # Collected by walking, not by iterating the Study lists: ModelTerm lives under
    # `model_estimations[].terms` and Condition under `tasks[].conditions`, so a
    # top-level sweep declares neither and every `Cell.term` and
    # `FactorLevel.conditions` reference reads as dangling when it is in fact fine.
    declared: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            if _is_field(node):
                return
            if isinstance(node.get("local_id"), str):
                declared.add(node["local_id"])
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(body)
    problems: list[str] = []

    def visit(node: Any, class_name: str, path: str) -> None:
        if not isinstance(node, dict) or _is_field(node):
            return
        attributes = schema_utils.attributes_for(classes, class_name)
        for key, value in node.items():
            attribute = attributes.get(key)
            if attribute is None:
                continue
            here = f"{path}.{key}"
            kind = schema_utils.classify_slot(classes, key, attribute)
            if kind == "reference":
                refs = [value] if isinstance(value, str) else value if isinstance(value, list) else []
                for ref in refs:
                    if isinstance(ref, str) and ref and ref not in declared:
                        problems.append(f"{here} -> unknown local_id {ref!r}")
            elif kind == "nested":
                target = attribute.get("range")
                if isinstance(target, str):
                    for index, item in enumerate(value if isinstance(value, list) else [value]):
                        suffix = f"[{index}]" if isinstance(value, list) else ""
                        visit(item, target, f"{here}{suffix}")

    # From Study rather than from the entity lists, so that references living under a
    # non-list slot -- `design.arms[].`, and anything added there later -- are checked
    # too. `_ENTITY_LISTS` holds dotted paths that `body.get()` cannot resolve.
    visit(body, "Study", "Study")
    return problems


def build(
    paper_id: str,
    text_path: Path,
    payload_dir: Path,
    extractor_model: str,
    extractor_version: str,
    extraction_date: str,
) -> tuple[dict[str, Any], BuildReport]:
    normalized, digest, sections = text_index.load(text_path)
    folded = span_tools.fold(normalized)
    report = BuildReport()

    classes = schema_utils.load_imported_classes(EXTRACTION_SCHEMA)
    body = merge_payloads(payload_dir)
    rewrites = apply_aliases(body, classes, load_aliases(payload_dir))
    if rewrites:
        print(f"reconciled {rewrites} cross-reference(s) through aliases.json")
    _walk(body, normalized, folded, "", report)

    record: dict[str, Any] = {
        # Required on Study, and not something a model reads off the page: the paper's
        # corpus id is what the record is keyed by, so the builder supplies it.
        "local_id": paper_id,
        "extraction_metadata": {
            "extractor_model": extractor_model,
            "extractor_version": extractor_version,
            "source_text_hash": digest,
            "extraction_date": extraction_date,
            "paper_sections": [section.as_record() for section in sections],
        }
    }
    record.update(body)

    # Integrity gate: nothing leaves this function unless every span addresses the
    # document it claims to.
    for evidence_set in _iter_sets(record):
        for span in evidence_set.get("spans", []):
            span_tools.verify(normalized, span)

    return record, report


def _iter_sets(node: Any):
    if isinstance(node, dict):
        if "spans" in node and isinstance(node["spans"], list):
            yield node
        for value in node.values():
            yield from _iter_sets(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_sets(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", required=True, help="neurostore id")
    parser.add_argument("--text", required=True, type=Path)
    parser.add_argument("--payloads", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--extractor-model", default="claude-opus-5")
    parser.add_argument("--extractor-version", default="review-bootstrap-0.1.0")
    parser.add_argument("--extraction-date", default="2026-08-02")
    args = parser.parse_args()

    record, report = build(
        args.paper,
        args.text,
        args.payloads,
        args.extractor_model,
        args.extractor_version,
        args.extraction_date,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\n{args.paper}: wrote {args.out}")
    print(report.summary())

    classes = schema_utils.load_imported_classes(EXTRACTION_SCHEMA)
    dangling = check_local_ids(record, classes)
    if dangling:
        print(f"\ndangling cross-references ({len(dangling)}):")
        for problem in dangling:
            print(f"  - {problem}")
    if report.failures:
        print(f"\nunresolved quotes ({len(report.failures)}):")
        for failure in report.failures:
            print(f"  - {failure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
