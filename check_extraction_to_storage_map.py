#!/usr/bin/env python3
"""Check that the extraction-to-storage map is still an identity map.

The extraction schema is generated from the storage schema, so the two describe the same
records with the same slots and the map has almost nothing to say. That is the point, and
it is also the thing that can quietly stop being true. What this script asserts is the
"almost": that the correspondence really is one-to-one everywhere the map does not
explicitly say otherwise, and that the few things it does say still hold.

Four checks, each failing in its own way:

  identity        Every storage field a model fills has an extraction field of the same
                  name on the same class, and every extraction field has a storage field
                  to land in. Catches a rename on either side, and a shape difference
                  applied through extraction-deviations.yaml without a matching entry
                  here.

  derivations     `derivations` names exactly the storage fields no extraction field
                  feeds -- the ones marked `in_subset: [deterministic]`. A field that
                  changes hands, from an API lookup to something a model reads, shows up
                  as an entry with nothing to derive or a mark with no entry.

  vocabularies    Each enum-ranged storage field's extraction counterpart wraps that same
                  vocabulary, with the same range: closed where storage is closed, and
                  keeping its `any_of: [<Enum>, string]` escape hatch where storage left
                  one. This is what replaced the map's synonym tables. Extraction used to
                  flatten every vocabulary to text and 316 table entries were the only
                  route back to a permissible value; now the extractor emits a value
                  storage already accepts, and what has to hold instead is that the
                  projection did not quietly open or close a vocabulary on the way.

Run gen_extraction_schema.py --check alongside this: that asserts the extraction schema is
the projection it claims to be, and this asserts the map over it is honest.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path
import sys

import yaml

from schema_utils import (
    LOCAL_ID,
    attribute_ranges,
    is_marked,
    is_structural,
    load_imported_classes,
    own_attributes,
)


ROOT = Path(__file__).resolve().parent
STORAGE_SCHEMA = ROOT / "neuroimaging-study-storage.yaml"
EXTRACTION_SCHEMA = ROOT / "neuroimaging-study-extraction.yaml"
MAP = ROOT / "extraction-to-storage.map.yaml"
DEVIATIONS = ROOT / "extraction-deviations.yaml"

EXTRACTED_SUBSET = "model_extracted"
DETERMINISTIC_SUBSET = "deterministic"


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, Mapping):
        raise ValueError(f"{path.name} must contain a YAML mapping.")
    return dict(document)


def extraction_name(attribute_name: str, attribute: Mapping[str, object]) -> str:
    """What the projection calls a storage attribute."""

    return LOCAL_ID if attribute.get("identifier") is True else attribute_name


def declared_extras() -> tuple[set[str], set[str]]:
    """Classes and slots extraction is allowed to have without a storage counterpart.

    Read from extraction-deviations.yaml rather than hardcoded, so that the one file
    declaring how the schemas may differ is also the one this check trusts. Anything not
    declared there is drift.
    """

    if not DEVIATIONS.is_file():
        return set(), set()
    document = load_yaml(DEVIATIONS)
    additions = document.get("required_additions") or {}

    classes = set(additions.get("classes") or {})
    slots = {
        f"{target.partition('.')[2]}.{slot}"
        for target, added in (additions.get("slots") or {}).items()
        for slot in added
    }

    for deviation in document.get("deviations") or ():
        operation = deviation.get("operation")
        if operation in ("add_slot", "replace_slot"):
            classes_name = str(deviation.get("class", "")).partition(".")[2]
            slots.add(f"{classes_name}.{deviation.get('slot')}")
        elif operation == "add_class":
            classes.add(str(deviation.get("class")))
    return classes, slots


# --------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------


def check_identity(
    storage: Mapping[str, object], extraction: Mapping[str, object]
) -> list[str]:
    """Assert the two schemas name the same things in the same places."""

    problems: list[str] = []

    for class_name in sorted(storage):
        target = extraction.get(class_name)
        for attribute_name, attribute in own_attributes(storage, class_name).items():
            if not (is_structural(attribute) or is_marked(attribute, EXTRACTED_SUBSET)):
                continue
            expected = extraction_name(attribute_name, attribute)
            if target is None:
                # A whole class can be absent legitimately: nothing marked
                # model_extracted points at it, so extraction never builds one.
                if is_structural(attribute):
                    continue
                problems.append(
                    f"storage {class_name}.{attribute_name} is extracted, but extraction "
                    f"has no {class_name} class"
                )
                continue
            if expected not in (target.get("attributes") or {}):
                problems.append(
                    f"storage {class_name}.{attribute_name} has no extraction "
                    f"counterpart {class_name}.{expected}"
                )

    extra_classes, extra_slots = declared_extras()
    storage_names = {
        class_name: {
            extraction_name(name, attribute)
            for name, attribute in own_attributes(storage, class_name).items()
        }
        for class_name in storage
    }
    for class_name in sorted(extraction):
        if class_name not in storage:
            # Evidence types and pipeline provenance have no storage counterpart by
            # design; extraction-deviations.yaml is what puts them there.
            if class_name not in extra_classes and not class_name.startswith(
                ("Extracted", "Evidence")
            ):
                problems.append(
                    f"extraction has a {class_name} class that storage does not, and "
                    f"extraction-deviations.yaml does not declare it"
                )
            continue
        for attribute_name in own_attributes(extraction, class_name):
            if attribute_name in storage_names[class_name]:
                continue
            if f"{class_name}.{attribute_name}" in extra_slots:
                continue
            problems.append(
                f"extraction {class_name}.{attribute_name} has nowhere to land in storage"
            )
    return problems


def check_derivations(
    storage: Mapping[str, object], extraction: Mapping[str, object], mapping: Mapping
) -> list[str]:
    """Assert `derivations` covers exactly the storage fields code fills."""

    problems: list[str] = []
    derivations = mapping.get("derivations") or {}

    needed: set[str] = set()
    for class_name in storage:
        if class_name not in extraction:
            # Storage-only classes are populated wholesale by the pipeline; the map does
            # not itemize them and neither does this check.
            continue
        for attribute_name, attribute in own_attributes(storage, class_name).items():
            if attribute.get("designates_type") is True:
                # Extraction supplies the discriminator, so it is an identity slot.
                continue
            if is_marked(attribute, DETERMINISTIC_SUBSET):
                needed.add(f"{class_name}.{attribute_name}")

    for path in sorted(needed - set(derivations)):
        problems.append(f"{path} is deterministic but the map does not say how it is filled")
    for path in sorted(set(derivations) - needed):
        class_name, _, attribute_name = path.rpartition(".")
        attribute = own_attributes(storage, class_name).get(attribute_name)
        if attribute is None:
            problems.append(f"derivations names a storage field that does not exist: {path}")
        else:
            problems.append(
                f"derivations covers {path}, but storage does not mark it deterministic"
            )
    return problems


def value_declaration(
    extraction: Mapping[str, object], wrapper_name: str
) -> Mapping[str, object] | None:
    """The `value` slot of an ExtractedValue subtype, as the wrapper narrows it."""

    definition = extraction.get(wrapper_name)
    if not isinstance(definition, Mapping):
        return None
    usage = definition.get("slot_usage") or {}
    value = usage.get("value") if isinstance(usage, Mapping) else None
    return value if isinstance(value, Mapping) else None


def check_vocabularies(
    storage: Mapping[str, object],
    extraction: Mapping[str, object],
    enums: Mapping[str, object],
) -> list[str]:
    """Assert each vocabulary reaches extraction with storage's own range.

    Storage decides per field whether a vocabulary is closed or has a free-text escape
    hatch, and that decision is a real one -- a closed field is one where any other answer
    is wrong, an open one is where the paper's own wording is worth keeping and the gap in
    the vocabulary is worth seeing. The projection has to carry it across intact; opening a
    closed vocabulary lets a value through that storage will reject, and closing an open
    one makes the extractor coerce and hides that the vocabulary was short a value.
    """

    problems: list[str] = []
    for class_name in sorted(storage):
        target = extraction.get(class_name)
        if not isinstance(target, Mapping):
            continue
        for attribute_name, attribute in own_attributes(storage, class_name).items():
            if is_structural(attribute) or not is_marked(attribute, EXTRACTED_SUBSET):
                continue
            ranges = attribute_ranges(attribute)
            enum_ranges = [item for item in ranges if item in enums]
            if not enum_ranges:
                continue

            path = f"{class_name}.{attribute_name}"
            projected = (target.get("attributes") or {}).get(attribute_name) or {}
            wrapper = projected.get("range")
            value = value_declaration(extraction, wrapper)
            if value is None:
                problems.append(
                    f"{path} ranges on {enum_ranges[0]}, but its extraction range "
                    f"{wrapper!r} is not a wrapper narrowing `value`"
                )
                continue

            storage_open = "string" in ranges
            extraction_open = "any_of" in value
            if storage_open != extraction_open:
                became = "opened" if extraction_open else "closed"
                problems.append(
                    f"{path}: storage declares {enum_ranges[0]} "
                    f"{'with' if storage_open else 'without'} a free-text escape hatch, "
                    f"but the projection {became} it in {wrapper}"
                )
                continue

            reached = {
                item.get("range")
                for item in (value.get("any_of") or [])
                if isinstance(item, Mapping)
            } or {value.get("range")}
            if enum_ranges[0] not in reached:
                problems.append(
                    f"{path}: {wrapper}.value reaches {sorted(reached)}, not "
                    f"{enum_ranges[0]}"
                )
                continue

            if (attribute.get("multivalued") is True) != (
                value.get("multivalued") is True
            ):
                problems.append(
                    f"{path}: storage is "
                    f"{'multivalued' if attribute.get('multivalued') else 'single-valued'}, "
                    f"but {wrapper}.value is not"
                )
    return problems


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    storage = load_imported_classes(STORAGE_SCHEMA)
    extraction = load_imported_classes(EXTRACTION_SCHEMA)
    enums = load_imported_classes(STORAGE_SCHEMA, key="enums")
    mapping = load_yaml(MAP)

    sections = [
        ("identity", check_identity(storage, extraction)),
        ("derivations", check_derivations(storage, extraction, mapping)),
        ("vocabularies", check_vocabularies(storage, extraction, enums)),
    ]

    failed = False
    for name, problems in sections:
        if not problems:
            print(f"{name}: ok")
            continue
        failed = True
        print(f"{name}: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")

    derivations = mapping.get("derivations") or {}
    free_text = mapping.get("free_text_normalizations") or {}
    print(
        f"\nThe map holds {len(derivations)} derivations and {len(free_text)} free-text "
        "tables. Everything else is identity."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
