"""The contracts as JSON Schema, generated alongside the models.

    from study_schema.jsonschema import load
    load("coordinate-parse")   # -> dict

Names: parsed-paper, coordinate-parse, extraction-record, storage-study.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

NAMES = ("parsed-paper", "coordinate-parse", "extraction-record", "storage-study")


def load(name: str) -> dict[str, Any]:
    """The JSON Schema for one contract."""
    if name not in NAMES:
        raise KeyError(f"no JSON Schema named {name!r}; one of {', '.join(NAMES)}")
    text = resources.files(__name__).joinpath(f"{name}.schema.json").read_text("utf-8")
    return json.loads(text)


__all__ = ["NAMES", "load"]
