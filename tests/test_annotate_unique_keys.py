"""The generator writes a class's LinkML unique keys, its own and inherited, onto its definition."""

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
    unique_keys:
      by_serial:
        unique_key_slots:
          - serial
    attributes:
      serial:
      model:
  Sub:
    is_a: Device
    unique_keys:
      by_extra:
        unique_key_slots:
          - extra
    attributes:
      extra:
  # Same key name as Device, so keys are deduplicated by name and the subclass wins.
  Redeclared:
    is_a: Device
    unique_keys:
      by_serial:
        unique_key_slots:
          - model
    attributes:
      model:
  Stamp:
    unique_keys:
      by_stamp:
        unique_key_slots:
          - stamp
    attributes:
      stamp:
  Mixed:
    mixins:
      - Stamp
    attributes:
      a:
  Pair:
    unique_keys:
      by_b_then_a:
        unique_key_slots:
          - b
          - a
      by_a:
        unique_key_slots:
          - a
    attributes:
      a:
      b:
  Plain:
    attributes:
      a:
"""


def test_annotate_unique_keys_writes_own_and_inherited_keys(tmp_path, generator):
    document, view = annotated_document(tmp_path, TINY_SCHEMA, "Device")

    generator._annotate_unique_keys(document, view)

    assert {
        cls: definition.get("x-unique-keys")
        for cls, definition in document["$defs"].items()
        if cls in {"Device", "Sub", "Pair", "Plain", "Redeclared", "Stamp", "Mixed"}
    } == {
        "Device": [["serial"]],
        "Sub": [["extra"], ["serial"]],
        "Redeclared": [["model"]],
        "Stamp": [["stamp"]],
        "Mixed": [["stamp"]],
        "Pair": [["b", "a"], ["a"]],
        "Plain": None,
    }
