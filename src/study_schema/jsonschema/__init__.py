"""The contracts as JSON Schema, generated alongside the models.

    from study_schema.jsonschema import load
    load("coordinate-parse")   # -> dict

Names: parsed-paper, coordinate-parse, extraction-record, storage-study.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, NamedTuple

NAMES = ("parsed-paper", "coordinate-parse", "extraction-record", "storage-study")


def load(name: str) -> dict[str, Any]:
    """The JSON Schema for one contract."""
    if name not in NAMES:
        raise KeyError(f"no JSON Schema named {name!r}; one of {', '.join(NAMES)}")
    text = resources.files(__name__).joinpath(f"{name}.schema.json").read_text("utf-8")
    return json.loads(text)


class Reference(NamedTuple):
    """A property holding another entity's local_id, with the class and slot it sits on.

    `multivalued` is true when the property is an array of identifiers rather than one.
    """

    cls: str
    slot: str
    target: str
    multivalued: bool


def references(name: str) -> list[Reference]:
    """The reference properties of one contract, sorted by class and slot."""
    rows = [
        Reference(cls, slot, prop["x-reference"], "items" in prop)
        for cls, definition in load(name)["$defs"].items()
        for slot, prop in definition.get("properties", {}).items()
        if "x-reference" in prop
    ]
    return sorted(rows, key=lambda row: (row.cls, row.slot))


def unique_keys(name: str) -> dict[str, tuple[tuple[str, ...], ...]]:
    """The unique keys of each class that declares one, sorted by class; keys are tuples."""
    return dict(
        sorted(
            (cls, tuple(tuple(key) for key in definition["x-unique-keys"]))
            for cls, definition in load(name)["$defs"].items()
            if "x-unique-keys" in definition
        )
    )


__all__ = ["NAMES", "Reference", "load", "references", "unique_keys"]
