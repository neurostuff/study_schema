#!/usr/bin/env python3
"""Check the extraction-to-storage map against both schemas.

Two checks. The first is that every path the map names resolves -- a target field on storage, a
source field on extraction. The second is *completeness*: that no extraction field is left
dangling, meaning neither mapped by a class_derivation nor listed in `absorbed_sources`.

The second check exists because `class_derivations` is keyed by storage path, so it structurally
cannot record an extraction field that has no storage field to land in. Those fields used to be
documented in prose comments, which meant a storage field could be removed and its extraction
counterpart would quietly stop going anywhere. `absorbed_sources` makes each one explicit and this
check makes the set exhaustive.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys
import warnings

import yaml

from schema_utils import load_imported_classes


ROOT = Path(__file__).resolve().parent
EXTRACTION_SCHEMA = ROOT / "neuroimaging-study-extraction.yaml"
EXTRACTION_EVIDENCE_SCHEMA = ROOT / "extraction-evidence.yaml"
STORAGE_SCHEMA = ROOT / "neuroimaging-study-storage.yaml"
MAPPING_SCHEMA = ROOT / "extraction-to-storage.map.yaml"


def load_yaml(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        contents = yaml.safe_load(stream)
    if not isinstance(contents, Mapping):
        raise ValueError(f"{path.name} must contain a YAML mapping.")
    return contents


def classes(schema: Mapping[str, object], schema_name: str) -> Mapping[str, object]:
    class_definitions = schema.get("classes", {})
    if not isinstance(class_definitions, Mapping):
        raise ValueError(f"{schema_name} must define a classes mapping.")
    return class_definitions


def resolve_path(
    class_definitions: Mapping[str, object], class_name: str, path: str
) -> bool:
    def attributes_for(class_name: str) -> Mapping[str, object]:
        """Return a class's attributes, including LinkML is_a ancestors."""
        collected: dict[str, object] = {}
        current_name: str | None = class_name
        seen: set[str] = set()
        while current_name and current_name not in seen:
            seen.add(current_name)
            definition = class_definitions.get(current_name)
            if not isinstance(definition, Mapping):
                return {}
            attributes = definition.get("attributes", {})
            if isinstance(attributes, Mapping):
                collected.update(attributes)
            parent = definition.get("is_a")
            current_name = parent if isinstance(parent, str) else None
        return collected

    current_class = class_name
    for index, field_name in enumerate(path.split(".")):
        attributes = attributes_for(current_class)
        attribute = attributes.get(field_name)
        if not isinstance(attribute, Mapping):
            return False
        if index < len(path.split(".")) - 1:
            attribute_range = attribute.get("range", "string")
            if not isinstance(attribute_range, str) or attribute_range not in class_definitions:
                return False
            current_class = attribute_range
    return True


def warn_paths(message: str, paths: list[str]) -> None:
    warnings.warn(f"{message}:\n  - " + "\n  - ".join(sorted(paths)), stacklevel=2)


def main() -> int:
    extraction_classes = {
        **classes(load_yaml(EXTRACTION_EVIDENCE_SCHEMA), EXTRACTION_EVIDENCE_SCHEMA.name),
        **classes(load_yaml(EXTRACTION_SCHEMA), EXTRACTION_SCHEMA.name),
    }
    storage_classes = load_imported_classes(STORAGE_SCHEMA)
    mapping = load_yaml(MAPPING_SCHEMA)
    derivations = mapping.get("class_derivations", {})
    if not isinstance(derivations, Mapping):
        raise ValueError("extraction-to-storage.map.yaml must define class_derivations.")

    issues: list[str] = []
    mapped_fields = 0

    for target_class, class_derivation in derivations.items():
        if target_class not in storage_classes:
            issues.append(f"{target_class} (target class is absent from storage)")
            continue
        if not isinstance(class_derivation, Mapping):
            issues.append(f"{target_class} (class derivation must be a mapping)")
            continue

        source_class = class_derivation.get("populated_from")
        if not isinstance(source_class, str) or source_class not in extraction_classes:
            issues.append(f"{target_class} (source class {source_class!r} is absent from extraction)")
            continue

        slot_derivations = class_derivation.get("slot_derivations", {})
        if not isinstance(slot_derivations, Mapping):
            issues.append(f"{target_class} (slot_derivations must be a mapping)")
            continue

        for target_field, derivation in slot_derivations.items():
            mapped_fields += 1
            target_path = f"{target_class}.{target_field}"
            if not resolve_path(storage_classes, target_class, target_field):
                issues.append(f"{target_path} (target field is absent from storage)")
            if not isinstance(derivation, Mapping):
                issues.append(f"{target_path} (derivation must be a mapping)")
                continue

            for key in ("populated_from", "source_evidence", "status_field", "source_reference"):
                source_path = derivation.get(key)
                if source_path is not None and (
                    not isinstance(source_path, str)
                    or not resolve_path(extraction_classes, source_class, source_path)
                ):
                    issues.append(f"{target_path}.{key} -> {source_path!r} (source path is absent from extraction)")

            source_fields = derivation.get("source_fields", {})
            if source_fields and not isinstance(source_fields, Mapping):
                issues.append(f"{target_path}.source_fields (must be a mapping)")
            elif isinstance(source_fields, Mapping):
                for source_name, source_path in source_fields.items():
                    if not isinstance(source_path, str) or "." not in source_path:
                        issues.append(f"{target_path}.source_fields.{source_name} -> {source_path!r} (expected Class.field)")
                        continue
                    referenced_class, referenced_path = source_path.split(".", 1)
                    if not resolve_path(extraction_classes, referenced_class, referenced_path):
                        issues.append(f"{target_path}.source_fields.{source_name} -> {source_path!r} (source path is absent from extraction)")

    if issues:
        warn_paths("Invalid extraction-to-storage map references", issues)
        return 1

    coverage_issues = check_coverage(extraction_classes, storage_classes, mapping, derivations)
    if coverage_issues:
        warn_paths("Extraction fields with no destination", coverage_issues)
        return 1

    print(
        f"Extraction-to-storage map references are valid across {len(derivations)} classes "
        f"and {mapped_fields} fields."
    )
    print(
        f"Every extraction field has a destination: mapped by a derivation, or listed in "
        f"absorbed_sources ({len(mapping.get('absorbed_sources') or {})} entries)."
    )
    return 0


#: Extraction machinery that describes the extraction run rather than the study. None of it is
#: storage-bound, and none of it needs an absorbed_sources entry.
INFRASTRUCTURE = frozenset({
    "StudyExtraction", "ExtractionMetadata", "PaperSection",
    "Evidence", "EvidenceSet", "EvidenceSpan",
    "ExtractedValue", "ExtractedNumber", "ExtractedString", "ExtractedFloat",
    "ExtractedInteger", "ExtractedBoolean", "ExtractedStringList",
})

#: Present on every extraction record and consumed by local_id_to_id rather than mapped.
INFRASTRUCTURE_FIELDS = frozenset({"local_id"})


def check_coverage(
    extraction_classes: Mapping[str, object],
    storage_classes: Mapping[str, object],
    mapping: Mapping[str, object],
    derivations: Mapping[str, object],
) -> list[str]:
    """Every extraction class and field must have somewhere to go, and say where."""

    absorbed = mapping.get("absorbed_sources") or {}
    if not isinstance(absorbed, Mapping):
        return ["absorbed_sources (must be a mapping)"]

    issues: list[str] = []

    # An absorbed_sources key is either an extraction class or an extraction Class.field, and its
    # absorbed_by entries are storage Class.field paths -- so the section cannot rot either.
    for key, entry in absorbed.items():
        source_class, _, source_field = str(key).partition(".")
        if source_class not in extraction_classes:
            issues.append(f"absorbed_sources.{key} (no such extraction class)")
            continue
        if source_field and not resolve_path(extraction_classes, source_class, source_field):
            issues.append(f"absorbed_sources.{key} (no such extraction field)")
        if not isinstance(entry, Mapping) or "reason" not in entry:
            issues.append(f"absorbed_sources.{key} (needs a reason)")
            continue
        for target in entry.get("absorbed_by") or ():
            target_class, _, target_field = str(target).partition(".")
            if target_class not in storage_classes or (
                target_field and not resolve_path(storage_classes, target_class, target_field)
            ):
                issues.append(f"absorbed_sources.{key}.absorbed_by -> {target!r} (not a storage path)")

    # Which extraction classes a derivation reads, and which of their fields it names.
    read_classes: set[str] = set()
    named_fields: dict[str, set[str]] = {}
    for class_derivation in derivations.values():
        if not isinstance(class_derivation, Mapping):
            continue
        source_class = class_derivation.get("populated_from")
        if not isinstance(source_class, str):
            continue
        read_classes.add(source_class)
        slots = class_derivation.get("slot_derivations")
        if not isinstance(slots, Mapping):
            continue
        for derivation in slots.values():
            if not isinstance(derivation, Mapping):
                continue
            for key in ("populated_from", "source_evidence", "status_field", "source_reference"):
                path = derivation.get(key)
                if isinstance(path, str):
                    named_fields.setdefault(source_class, set()).add(path.split(".")[0])
            for path in (derivation.get("source_fields") or {}).values():
                if isinstance(path, str) and "." in path:
                    referenced_class, referenced_field = path.split(".", 1)
                    read_classes.add(referenced_class)
                    named_fields.setdefault(referenced_class, set()).add(referenced_field.split(".")[0])

    # A slot the map reads carries its whole object when its range is a class: `transform: verbatim`
    # on a slot of range Statistic copies the Statistic. So reachability follows slot ranges,
    # transitively, and those copies are only lossless if the field names agree -- which is checked
    # below rather than assumed.
    copied: dict[str, str] = {}  # extraction class -> the extraction field path that copies it
    frontier = list(read_classes)
    while frontier:
        class_name = frontier.pop()
        for field_name, spec in attributes_of(extraction_classes, class_name).items():
            if class_name in read_classes and field_name not in named_fields.get(class_name, frozenset()):
                # Not named by any derivation; only reachable if a same-named storage field copies it.
                pass
            range_name = spec.get("range") if isinstance(spec, Mapping) else None
            if not isinstance(range_name, str) or range_name in INFRASTRUCTURE:
                continue
            if range_name in extraction_classes and range_name not in read_classes:
                read_classes.add(range_name)
                copied[range_name] = f"{class_name}.{field_name}"
                frontier.append(range_name)

    for class_name, definition in sorted(extraction_classes.items()):
        if class_name in INFRASTRUCTURE or not isinstance(definition, Mapping):
            continue
        if definition.get("abstract"):
            continue
        if class_name in absorbed:
            continue  # the whole class is accounted for
        if class_name not in read_classes:
            issues.append(f"{class_name} (no class_derivation reads it, no absorbed_sources entry)")
            continue

        # Where this class's fields can land: a storage class a derivation targets from it, or --
        # for a class copied wholesale through a slot -- the storage class of the same name.
        storage_names: set[str] = set()
        for target_class, class_derivation in derivations.items():
            if isinstance(class_derivation, Mapping) and class_derivation.get("populated_from") == class_name:
                if target_class in storage_classes:
                    storage_names |= set(attributes_of(storage_classes, target_class))
        if class_name in copied:
            if class_name not in storage_classes:
                issues.append(
                    f"{class_name} (copied through {copied[class_name]}, but storage has no such class)"
                )
                continue
            storage_names |= set(attributes_of(storage_classes, class_name))

        for field_name in attributes_of(extraction_classes, class_name):
            if field_name in INFRASTRUCTURE_FIELDS:
                continue
            if field_name in named_fields.get(class_name, frozenset()):
                continue
            if field_name in storage_names:
                continue
            if f"{class_name}.{field_name}" in absorbed:
                continue
            where = f" (copied through {copied[class_name]})" if class_name in copied else ""
            issues.append(
                f"{class_name}.{field_name} mapped nowhere{where}, and not in absorbed_sources"
            )

    return issues


def attributes_of(class_definitions: Mapping[str, object], class_name: str) -> dict[str, object]:
    """A class's attributes including LinkML is_a ancestors."""
    collected: dict[str, object] = {}
    current: str | None = class_name
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        definition = class_definitions.get(current)
        if not isinstance(definition, Mapping):
            break
        attributes = definition.get("attributes")
        if isinstance(attributes, Mapping):
            for name, spec in attributes.items():
                collected.setdefault(name, spec)
        parent = definition.get("is_a")
        current = parent if isinstance(parent, str) else None
    return collected


if __name__ == "__main__":
    sys.exit(main())
