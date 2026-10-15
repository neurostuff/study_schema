"""The generator marks a property as a reference by the rules of its induced LinkML slot."""

import pytest

from conftest import annotated_document

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


def test_annotate_references_follows_the_linkml_rules(tmp_path, generator):
    document, view = annotated_document(tmp_path, TINY_SCHEMA, "Study")

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
