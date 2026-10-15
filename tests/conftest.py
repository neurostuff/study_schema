from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
STUDY = "x7Ex4mpleA01"


@pytest.fixture
def example_paths() -> dict[str, Path]:
    paper = EXAMPLES / "corpus" / STUDY
    return {
        "bundle": paper,
        "parsed_paper": paper / "parse" / "parsed_paper.json",
        "coordinate_parse": paper / "parse" / "coordinate_parse.json",
        "record": EXAMPLES / "run" / "records" / f"{STUDY}.extraction.json",
        "revision": EXAMPLES / "run" / "revisions" / f"{STUDY}.coordinate_parse.json",
    }


@pytest.fixture
def generator():
    path = ROOT / "tools" / "generate_models.py"
    spec = importlib.util.spec_from_file_location("generate_models", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_models"] = module
    spec.loader.exec_module(module)
    return module


def annotated_document(tmp_path: Path, yaml_text: str, top_class: str):
    """The JSON Schema for `top_class` from a tiny LinkML schema, with its SchemaView."""
    from linkml.generators.jsonschemagen import JsonSchemaGenerator
    from linkml_runtime.utils.schemaview import SchemaView

    path = tmp_path / "tiny.yaml"
    path.write_text(yaml_text, "utf-8")
    view = SchemaView(str(path))
    view.merge_imports()
    document = json.loads(JsonSchemaGenerator(view.schema, top_class=top_class).serialize())
    return document, view


def load(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))
