#!/usr/bin/env python3
"""Check that every storage field declares exactly one provenance subset.

`deterministic` and `model_extracted` are the two ways a field can be filled --
by the pipeline, or by reading the paper -- and `gen_extraction_schema.py` keys
the whole projection on the distinction: a `model_extracted` field becomes an
`ExtractedValue` wrapper in the extraction schema and a `deterministic` one is
dropped from it and owed a `derivations:` entry in the map instead.

A field marked neither is therefore invisible to extraction *and* unclaimed by the
pipeline, and nothing else notices: the identity check only compares the fields
that survive the projection, so an unmarked field survives nowhere and is
compared against nothing. A field marked both would be generated and asked for.

Identifiers and type designators are exempt (`schema_utils.is_structural`): the
mapper mints them and no model could supply them, so no provenance decision
applies. Marking them anyway is allowed and several classes do.

Usage:
    python3 check_field_provenance.py
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys
import warnings

from schema_utils import is_marked, is_structural, load_imported_classes

ROOT = Path(__file__).resolve().parent
STORAGE_SCHEMA = ROOT / "neuroimaging-study-storage.yaml"

DETERMINISTIC = "deterministic"
MODEL_EXTRACTED = "model_extracted"


def classify(class_definitions: Mapping[str, object]) -> tuple[list[str], list[str], int]:
    """(fields marked neither, fields marked both, fields checked)."""

    neither: list[str] = []
    both: list[str] = []
    checked = 0

    for class_name, class_definition in sorted(class_definitions.items()):
        if not isinstance(class_definition, Mapping):
            continue
        attributes = class_definition.get("attributes", {})
        if not isinstance(attributes, Mapping):
            continue
        for field_name, attribute in attributes.items():
            if not isinstance(attribute, Mapping) or is_structural(attribute):
                continue
            checked += 1
            path = f"{class_name}.{field_name}"
            marks = [
                subset for subset in (DETERMINISTIC, MODEL_EXTRACTED)
                if is_marked(attribute, subset)
            ]
            if not marks:
                neither.append(path)
            elif len(marks) > 1:
                both.append(path)

    return neither, both, checked


def warn_paths(message: str, paths: list[str]) -> None:
    warnings.warn(f"{message}:\n  - " + "\n  - ".join(paths), stacklevel=2)


def main() -> int:
    neither, both, checked = classify(load_imported_classes(STORAGE_SCHEMA))

    if neither:
        warn_paths(
            f"Storage fields marked neither {DETERMINISTIC} nor {MODEL_EXTRACTED} "
            "(they reach neither the extraction schema nor the derivations map)",
            neither,
        )
    if both:
        warn_paths(
            f"Storage fields marked both {DETERMINISTIC} and {MODEL_EXTRACTED} "
            "(they would be generated and asked for)",
            both,
        )
    if neither or both:
        return 1

    print(f"Provenance is declared exactly once on {checked} storage fields.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
