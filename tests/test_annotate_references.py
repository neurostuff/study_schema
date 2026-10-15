"""The generator marks a property as a reference by the rules of its induced LinkML slot."""

import importlib.util
import json
import sys

import pytest

from conftest import ROOT

pytest.importorskip("linkml", reason="regeneration needs the `generate` extra")

TINY_SCHEMA = """\
id: https://example.org/tiny
name: tiny
default_prefix: tiny
prefixes:
  tiny: https://example.org/tiny/
  linkml: https://w3id.org/linkml/
default_range: string
classes:
  Device:
    attributes:
      local_id:
        identifier: true
  Keyless:
    attributes:
      label:
  Base:
    attributes:
      inherited_device:
        range: Device
      pointer:
        range: Keyless
  Study:
    is_a: Base
    attributes:
      device:
        range: Device
      keyless:
        range: Keyless
      inlined_device:
        range: Device
        inlined: true
      listed_devices:
        range: Device
        multivalued: true
        inlined_as_list: true
      device_list:
        range: Device
        multivalued: true
    slot_usage:
      pointer:
        range: Device
"""


@pytest.fixture
def generator():
    path = ROOT / "tools" / "generate_models.py"
    spec = importlib.util.spec_from_file_location("generate_models", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_models"] = module
    spec.loader.exec_module(module)
    return module


def test_annotate_references_follows_the_linkml_rules(tmp_path, generator):
    from linkml.generators.jsonschemagen import JsonSchemaGenerator
    from linkml_runtime.utils.schemaview import SchemaView

    path = tmp_path / "tiny.yaml"
    path.write_text(TINY_SCHEMA, "utf-8")
    view = SchemaView(str(path))
    view.merge_imports()
    document = json.loads(JsonSchemaGenerator(view.schema, top_class="Study").serialize())

    generator._annotate_references(document, view)

    properties = document["$defs"]["Study"]["properties"]
    assert {slot: prop.get("x-reference") for slot, prop in properties.items()} == {
        "device": "Device",
        "inherited_device": "Device",
        "keyless": None,
        "inlined_device": None,
        "listed_devices": None,
        "device_list": "Device",
        "pointer": "Device",
    }
