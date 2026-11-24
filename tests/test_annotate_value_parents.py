"""The generator writes the `is_a` of an enum's permissible values onto the enum's definition."""

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
  Scan:
    attributes:
      kind:
        range: Kind
      flavour:
        range: Flavour
enums:
  Kind:
    permissible_values:
      coarse:
      fine:
        is_a: coarse
      finer:
        is_a: coarse
      alone:
  Flavour:
    permissible_values:
      sweet:
      sour:
"""


def test_annotate_value_parents_writes_each_refining_value(tmp_path, generator):
    document, view = annotated_document(tmp_path, TINY_SCHEMA, "Scan")

    generator._annotate_value_parents(document, view)

    assert document["$defs"]["Kind"]["x-parents"] == {"fine": "coarse", "finer": "coarse"}
    # An enum none of whose values refines another gets no annotation.
    assert "x-parents" not in document["$defs"]["Flavour"]
