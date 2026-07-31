#!/usr/bin/env python3
"""Check that every storage-schema field has exactly one priority entry."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys
import warnings

import yaml


ROOT = Path(__file__).resolve().parent
STORAGE_SCHEMA = ROOT / "neuroimaging-study-storage.yaml"
PRIORITIES = ROOT / "storage-parameter-priorities.yaml"
VALID_PRIORITIES = {0, 1, 2, 3, "n/a"}


def load_yaml(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as stream:
        contents = yaml.safe_load(stream)
    if not isinstance(contents, Mapping):
        raise ValueError(f"{path.name} must contain a YAML mapping.")
    return contents


def field_paths(class_definitions: Mapping[str, object]) -> set[str]:
    paths: set[str] = set()
    for class_name, class_definition in class_definitions.items():
        if not isinstance(class_definition, Mapping):
            continue
        attributes = class_definition.get("attributes", {})
        if not isinstance(attributes, Mapping):
            continue
        paths.update(f"{class_name}.{field_name}" for field_name in attributes)
    return paths


def priority_paths(priorities: Mapping[str, object]) -> tuple[set[str], list[str]]:
    paths: set[str] = set()
    invalid: list[str] = []
    for class_name, fields in priorities.items():
        if not isinstance(fields, Mapping):
            invalid.append(f"{class_name} (expected a field-to-priority mapping)")
            continue
        for field_name, priority in fields.items():
            path = f"{class_name}.{field_name}"
            paths.add(path)
            if (type(priority) is not int and priority != "n/a") or priority not in VALID_PRIORITIES:
                invalid.append(f"{path} (expected priority 0, 1, 2, 3, or 'n/a'; got {priority!r})")
    return paths, invalid


def warn_paths(message: str, paths: list[str]) -> None:
    warnings.warn(f"{message}:\n  - " + "\n  - ".join(paths), stacklevel=2)


def main() -> int:
    storage = load_yaml(STORAGE_SCHEMA)
    class_definitions = storage.get("classes", {})
    if not isinstance(class_definitions, Mapping):
        raise ValueError("neuroimaging-study-storage.yaml must define a classes mapping.")

    priorities = load_yaml(PRIORITIES)
    storage_fields = field_paths(class_definitions)
    priority_fields, invalid_priorities = priority_paths(priorities)

    missing = sorted(storage_fields - priority_fields)
    extra = sorted(priority_fields - storage_fields)
    has_issues = False

    if missing:
        warn_paths("Storage fields missing from storage-parameter-priorities.yaml", missing)
        has_issues = True
    if extra:
        warn_paths("Extra fields in storage-parameter-priorities.yaml", extra)
        has_issues = True
    if invalid_priorities:
        warn_paths("Invalid priority entries", invalid_priorities)
        has_issues = True

    if has_issues:
        return 1

    print(f"Priority inventory matches {len(storage_fields)} storage fields.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
