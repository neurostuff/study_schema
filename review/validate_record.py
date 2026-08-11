#!/usr/bin/env python3
"""Validate an extraction record against the extraction schema.

Written in the same pure-YAML style as the repo's other check scripts, so it adds
no linkml runtime dependency. Enforces both LinkML structure (declared
attributes, required slots, ranges, multivalued shape) and the invariants stated
in the extraction schema header:

  * extraction_status: not_reported  =>  value omitted, evidence.status not_applicable
  * evidence.status: present         =>  at least one set, each with at least one span
  * every span satisfies text == source[start_char:end_char]

Usage:
    python review/validate_record.py \
        --record review/examples/2abntY3hQSyq.extraction.json \
        --text review/texts/2abntY3hQSyq/processed/pubget/text.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import spans as span_tools
import text_index

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import schema_utils  # noqa: E402  (repo root is added above)

ROOT = Path(__file__).resolve().parent.parent
EXTRACTION_SCHEMA = ROOT / "neuroimaging-study-extraction.yaml"

_EXTRACTION_STATUS = {"extracted", "not_reported"}
_VALUE_SOURCE = {"reported", "generated"}
_EVIDENCE_STATUS = {"present", "not_found", "not_applicable"}

# LinkML native ranges of the ExtractedValue subclasses, and the Python types
# that satisfy them. bool is excluded from integer deliberately: True would
# otherwise pass as 1.
_SCALAR_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "float": (int, float),
    "date": (str,),
    "boolean": (bool,),
}


class Validator:
    def __init__(self, classes: Mapping[str, Any], normalized: str | None,
                 enums: Mapping[str, Any] | None = None) -> None:
        self.classes = classes
        self.enums = enums or {}
        self.normalized = normalized
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.fields = 0
        self.spans = 0

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def warn(self, path: str, message: str) -> None:
        self.warnings.append(f"{path}: {message}")

    # -- class instances ---------------------------------------------------

    #: Slot naming the concrete subclass, per class that carries a self-naming payload.
    #: `Analysis.details` declares range AnalysisDetails and `details_type` says which of
    #: the eight it really is; the fields that make the payload useful live on the
    #: subclass, so validating against the declared range rejects every one of them.
    _TYPE_DESIGNATOR = {"AnalysisDetails": "details_type", "Acquisition": "acquisition_type"}

    def resolve_type(self, node: Mapping[str, Any], class_name: str, path: str) -> str:
        """Follow a type designator to the subclass the record says it is."""

        designator = self._TYPE_DESIGNATOR.get(class_name)
        if designator is None:
            return class_name
        named = node.get(designator)
        if not isinstance(named, str):
            return class_name
        if named not in self.classes:
            self.error(path, f"{designator} names unknown class {named!r}")
            return class_name
        if not schema_utils.resolves_to(self.classes, named, class_name):
            self.error(path, f"{designator} {named!r} is not a {class_name}")
            return class_name
        return named

    def check_instance(self, node: Any, class_name: str, path: str) -> None:
        if not isinstance(node, dict):
            self.error(path, f"expected an object for {class_name}, got {type(node).__name__}")
            return

        class_name = self.resolve_type(node, class_name, path)
        attributes = schema_utils.attributes_for(self.classes, class_name)
        if not attributes:
            self.error(path, f"class {class_name!r} is not defined in the schema")
            return

        for key in node:
            if key not in attributes:
                self.error(path, f"attribute {key!r} is not declared on {class_name}")

        for name, attribute in attributes.items():
            if attribute.get("required") and name not in node:
                self.error(path, f"required attribute {name!r} is missing on {class_name}")

        for key, value in node.items():
            attribute = attributes.get(key)
            if attribute is None:
                continue
            self.check_slot(value, key, attribute, f"{path}.{key}")

    def check_slot(
        self, value: Any, name: str, attribute: Mapping[str, Any], path: str
    ) -> None:
        kind = schema_utils.classify_slot(self.classes, name, attribute)
        multivalued = bool(attribute.get("multivalued"))

        if multivalued and not isinstance(value, list):
            self.error(path, f"{name!r} is multivalued but got {type(value).__name__}")
            return
        if not multivalued and isinstance(value, list) and kind in {"evidence", "nested"}:
            self.error(path, f"{name!r} is not multivalued but got a list")
            return

        items = value if multivalued and isinstance(value, list) else [value]
        for index, item in enumerate(items):
            here = f"{path}[{index}]" if multivalued else path
            if kind == "identifier":
                if not isinstance(item, str) or not item:
                    self.error(here, "local_id must be a non-empty string")
            elif kind == "reference":
                if not isinstance(item, str):
                    self.error(here, f"cross-reference must be a string, got {type(item).__name__}")
            elif kind == "native":
                self.check_native(item, attribute, here)
            elif kind == "nested":
                target = attribute.get("range")
                if isinstance(target, str):
                    self.check_instance(item, target, here)
            elif kind == "evidence":
                target = attribute.get("range")
                self.check_field(item, target if isinstance(target, str) else "ExtractedValue", here)

    def check_native(self, value: Any, attribute: Mapping[str, Any], path: str) -> None:
        """Type-check a pipeline scalar. Enum ranges are checked by their callers."""

        declared = attribute.get("range", "string")
        if not isinstance(declared, str) or declared not in _SCALAR_TYPES:
            return
        if declared == "integer" and isinstance(value, bool):
            self.error(path, "must be an integer, got a boolean")
            return
        if not isinstance(value, _SCALAR_TYPES[declared]):
            self.error(path, f"must be a {declared}, got {type(value).__name__}")
            return
        minimum = attribute.get("minimum_value")
        if isinstance(minimum, int) and isinstance(value, (int, float)) and value < minimum:
            self.error(path, f"must be >= {minimum}, got {value}")

    # -- ExtractedValue fields --------------------------------------------

    def check_field(self, node: Any, class_name: str, path: str) -> None:
        if not isinstance(node, dict):
            self.error(path, f"expected an ExtractedValue object, got {type(node).__name__}")
            return

        self.fields += 1
        attributes = schema_utils.attributes_for(self.classes, class_name)

        for key in node:
            if key not in attributes:
                self.error(path, f"attribute {key!r} is not declared on {class_name}")

        status = node.get("extraction_status")
        if status not in _EXTRACTION_STATUS:
            self.error(path, f"extraction_status must be one of {sorted(_EXTRACTION_STATUS)}, got {status!r}")

        source = node.get("value_source")
        if source is not None and source not in _VALUE_SOURCE:
            self.error(path, f"value_source must be one of {sorted(_VALUE_SOURCE)}, got {source!r}")

        evidence = node.get("evidence")
        if evidence is None:
            self.error(path, "evidence is required")
        else:
            self.check_evidence(evidence, status, f"{path}.evidence")

        # Header invariant: not_reported means no value at all.
        if status == "not_reported":
            if "value" in node:
                self.error(path, "not_reported fields must omit value")
        elif status == "extracted":
            if "value" not in node:
                self.error(path, "extracted fields must carry a value")
            else:
                self.check_value_type(node["value"], class_name, attributes, path)

    def vocabulary_of(self, value_slot: Mapping[str, Any]) -> tuple[set[str] | None, bool]:
        """(permissible values, closed) for a wrapper's `value` slot, or (None, False).

        Closed is readable off the range: a bare enum admits nothing else, while
        `any_of: [<Enum>, string]` is storage's declared escape hatch.
        """

        for name in schema_utils.attribute_ranges(value_slot):
            enum = self.enums.get(name)
            if isinstance(enum, Mapping):
                values = set((enum.get("permissible_values") or {}))
                return values, value_slot.get("range") == name
        return None, False

    def check_value_type(
        self, value: Any, class_name: str, attributes: Mapping[str, Any], path: str
    ) -> None:
        value_slot = attributes.get("value", {})

        # A vocabulary is checked before the scalar branch, because an enum range is not in
        # _SCALAR_TYPES and would otherwise fall straight through -- which is how a
        # `statistic.family` of "T" passed while the vocabulary says "t". A closed field is
        # enforced; an open one (`any_of: [<Enum>, string]`) keeps its escape hatch and the
        # off-vocabulary value is reported as a warning, since those accumulating are the
        # evidence for whether the vocabulary is short a value.
        vocabulary, closed = self.vocabulary_of(value_slot)
        if vocabulary is not None:
            for item in (value if value_slot.get("multivalued")
                         and isinstance(value, list) else [value]):
                if isinstance(item, str) and item not in vocabulary:
                    message = (f"{item!r} is not a permissible value "
                               f"({', '.join(sorted(vocabulary))})")
                    if closed:
                        self.error(path, message)
                    else:
                        self.warn(path, message + "; open vocabulary, kept as free text")

        declared = value_slot.get("range", "Any")
        if declared in {"Any", None} or declared not in _SCALAR_TYPES:
            return  # ExtractedValue holds lists and free-form structures by design.
        expected = _SCALAR_TYPES[declared]

        # An Extracted<T>List declares `multivalued: true` on its own `value`, and a list
        # there is the whole point: extraction-readme.md §2 makes "one wrapper holding a
        # list" the headline convention, so rejecting it rejected every correctly shaped
        # inclusion_criteria, preprocessing step and echo time in the record.
        if value_slot.get("multivalued"):
            if not isinstance(value, list):
                self.error(path, f"{class_name}.value must be a list of {declared}, "
                                 f"got {type(value).__name__}")
                return
            for index, item in enumerate(value):
                self.check_value_type(item, class_name,
                                      {"value": {k: v for k, v in value_slot.items()
                                                 if k != "multivalued"}},
                                      f"{path}.value[{index}]")
            return

        # A multivalued concept expressed as a list inside a scalar ExtractedValue
        # subtype is a real shape problem: the mapper would fail to parse it.
        if isinstance(value, list):
            self.error(path, f"{class_name}.value must be a {declared}, got a list")
            return
        if declared == "integer" and isinstance(value, bool):
            self.error(path, f"{class_name}.value must be an integer, got a boolean")
            return
        if not isinstance(value, expected):
            self.error(
                path, f"{class_name}.value must be a {declared}, got {type(value).__name__}"
            )

    def check_evidence(self, node: Any, status: Any, path: str) -> None:
        if not isinstance(node, dict):
            self.error(path, f"expected an Evidence object, got {type(node).__name__}")
            return

        evidence_status = node.get("status")
        if evidence_status not in _EVIDENCE_STATUS:
            self.error(path, f"status must be one of {sorted(_EVIDENCE_STATUS)}, got {evidence_status!r}")

        if status == "not_reported" and evidence_status != "not_applicable":
            self.error(path, "not_reported fields must have evidence.status not_applicable")
        if status == "extracted" and evidence_status == "not_applicable":
            self.error(path, "extracted fields must not have evidence.status not_applicable")

        sets = node.get("sets")
        if evidence_status == "present":
            if not isinstance(sets, list) or not sets:
                self.error(path, "evidence.status present requires at least one set")
                return
        elif sets:
            self.error(path, f"evidence.status {evidence_status} must not carry sets")
            return

        for index, evidence_set in enumerate(sets or []):
            self.check_set(evidence_set, f"{path}.sets[{index}]")

    def check_set(self, node: Any, path: str) -> None:
        if not isinstance(node, dict):
            self.error(path, f"expected an EvidenceSet object, got {type(node).__name__}")
            return
        for key in node:
            if key != "spans":
                self.error(path, f"attribute {key!r} is not declared on EvidenceSet")
        spans = node.get("spans")
        if not isinstance(spans, list) or not spans:
            self.error(path, "EvidenceSet requires at least one span (minimum_cardinality: 1)")
            return
        for index, span in enumerate(spans):
            self.check_span(span, f"{path}.spans[{index}]")

    def check_span(self, node: Any, path: str) -> None:
        if not isinstance(node, dict):
            self.error(path, f"expected an EvidenceSpan object, got {type(node).__name__}")
            return
        self.spans += 1
        for key in node:
            if key not in {"text", "start_char", "end_char"}:
                self.error(path, f"attribute {key!r} is not declared on EvidenceSpan")
        for key in ("text", "start_char", "end_char"):
            if key not in node:
                self.error(path, f"required attribute {key!r} is missing on EvidenceSpan")
                return
        if self.normalized is not None:
            try:
                span_tools.verify(self.normalized, node)
            except span_tools.SpanResolutionError as error:
                self.error(path, str(error))

    # -- entry point -------------------------------------------------------

    def check_record(self, record: Any) -> None:
        self.check_instance(record, "Study", "Study")

        metadata = record.get("extraction_metadata") if isinstance(record, dict) else None
        if isinstance(metadata, dict) and self.normalized is not None:
            declared_hash = metadata.get("source_text_hash")
            actual = text_index.text_hash(self.normalized)
            if declared_hash and declared_hash != actual:
                self.error(
                    "Study.extraction_metadata.source_text_hash",
                    f"does not match the supplied text ({declared_hash[:12]}... != {actual[:12]}...)",
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--text", type=Path, help="normalized source text; enables offset checks")
    args = parser.parse_args()

    normalized = None
    if args.text:
        normalized = text_index.normalize(args.text.read_text(encoding="utf-8"))

    classes = schema_utils.load_imported_classes(EXTRACTION_SCHEMA)
    enums = schema_utils.load_imported_classes(EXTRACTION_SCHEMA, "enums")
    validator = Validator(classes, normalized, enums)
    validator.check_record(json.loads(args.record.read_text(encoding="utf-8")))

    print(f"{args.record.name}: {validator.fields} fields, {validator.spans} spans checked")
    if validator.warnings:
        print(f"\nwarnings ({len(validator.warnings)}):")
        for warning in validator.warnings:
            print(f"  - {warning}")
    if validator.errors:
        print(f"\nerrors ({len(validator.errors)}):")
        for error in validator.errors:
            print(f"  - {error}")
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
