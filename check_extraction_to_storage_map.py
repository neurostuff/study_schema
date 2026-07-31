#!/usr/bin/env python3
"""Check that extraction-to-storage map references resolve in their schemas."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys
import warnings

import yaml


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
    storage_classes = classes(load_yaml(STORAGE_SCHEMA), STORAGE_SCHEMA.name)
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

    print(f"Extraction-to-storage map references are valid across {len(derivations)} classes and {mapped_fields} fields.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
