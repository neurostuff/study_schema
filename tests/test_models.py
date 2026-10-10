"""The examples satisfy the models and the JSON Schemas, and the two agree."""

import pytest
from pydantic import ValidationError

from conftest import load
from study_schema import jsonschema
from study_schema.keys import cell_locator, key_for, normalize_name, span_key, span_locator, table_key
from study_schema.models import extraction, paper_parse, storage
from study_schema.models.paper_parse import CoordinateParse, ParsedPaper


def test_models_carry_their_schema_version():
    for module in (paper_parse, extraction, storage):
        assert module.version.count(".") == 2


def test_examples_validate(example_paths):
    paper = ParsedPaper.model_validate(load(example_paths["parsed_paper"]))
    original = CoordinateParse.model_validate(load(example_paths["coordinate_parse"]))
    revision = CoordinateParse.model_validate(load(example_paths["revision"]))
    extraction.Study.model_validate(load(example_paths["record"]))
    assert original.revision_of is None and revision.revision_of == original.parse_id
    assert paper.text_sha256 == original.text_sha256 == revision.text_sha256


def test_an_undeclared_field_is_refused(example_paths):
    data = load(example_paths["coordinate_parse"])
    data["analyses"][0]["is_deactivation"] = False  # retired in 0.5.0
    with pytest.raises(ValidationError, match="is_deactivation"):
        CoordinateParse.model_validate(data)


def test_references_are_ids_not_nested_objects():
    # Group.arm is `inlined: false`: a record holds the arm's local_id.
    assert extraction.Group.model_fields["arm"].annotation == (str | None)


@pytest.mark.parametrize(
    "name, example",
    [
        ("parsed-paper", "parsed_paper"),
        ("coordinate-parse", "coordinate_parse"),
        ("coordinate-parse", "revision"),
        ("extraction-record", "record"),
    ],
)
def test_json_schema_agrees(name, example, example_paths):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    validator.validate(load(example_paths[example]), jsonschema.load(name))


def test_unknown_json_schema():
    with pytest.raises(KeyError):
        jsonschema.load("studyset")


def test_key_rule():
    # sha1("0:0,1:0")[:12], computed by hand from the rule in coordinates.yaml
    assert table_key("tbl1", [(1, 0), (0, 0), (1, 0)], "A") == "tbl1#" + __import__("hashlib").sha1(b"0:0,1:0|a").hexdigest()[:12]
    assert span_key("text", [(5, 9)], "x").startswith("text#")
    with pytest.raises(ValueError):
        table_key("tbl1", [], "A")
    with pytest.raises(ValueError):
        span_key("table", [(0, 1)], "x")


def test_keys_hash_their_locators():
    sha1 = __import__("hashlib").sha1
    assert cell_locator([(4, 0), (3, 0), (4, 0)]) == "3:0,4:0"
    assert span_locator([(1288, 1300), (1200, 1288)]) == "1200-1288,1288-1300"
    assert table_key("tbl2", [(4, 0), (3, 0)], "PO > Sil") == "tbl2#" + sha1(b"3:0,4:0|po>sil").hexdigest()[:12]
    assert span_key("text", [(5, 9)], "PO > Sil") == "text#" + sha1(b"5-9|po>sil").hexdigest()[:12]
    assert cell_locator([]) == span_locator([]) == ""


def test_example_keys_are_derived(example_paths):
    for which in ("coordinate_parse", "revision"):
        parse = CoordinateParse.model_validate(load(example_paths[which]))
        for analysis in parse.analyses:
            assert key_for(analysis) == analysis.key


def test_one_sentence_two_names_two_keys():
    spans = [(100, 180)]
    assert span_key("text", spans, "PO > Sil") != span_key("text", spans, "PC > Sil")
    assert span_key("text", spans, "PO > Sil") == span_key("text", list(spans), "PO > Sil")


def test_name_variants_share_a_key():
    spans = [(100, 180)]
    base = span_key("text", spans, "PO > Sil")
    assert span_key("text", spans, "po  >\tSIL ") == base
    assert span_key("text", spans, "PO > S\u200bil") == base
    assert span_key("figure", spans, "A \u2013 B") == span_key("figure", spans, "a - b")
    assert span_key("figure", spans, "A \u2014 B") == span_key("figure", spans, "a-b".replace("-", " - "))
    for dash in ("\u2212", "\u2010", "\u2011"):
        assert span_key("text", spans, f"PO {dash} Sil") == span_key("text", spans, "PO - Sil")
        assert normalize_name(f"A{dash}B") == "a-b"
    assert normalize_name("\uff21\u2003\u2013\u2003B") == "a-b"


def test_names_differing_only_in_whitespace_share_a_key():
    spans = [(1200, 1288)]
    assert normalize_name("PO > Sil") == normalize_name("PO>Sil") == "po>sil"
    assert span_key("text", spans, "PO > Sil") == span_key("text", spans, "PO>Sil")
    assert table_key("t", [(3, 0)], "PO > Sil") == table_key("t", [(3, 0)], " PO>\u00a0Sil")
    assert span_key("text", spans, "PO > Sil") != span_key("text", spans, "PO < Sil")


def test_table_key_carries_the_name():
    cells = [(3, 0), (4, 0)]
    assert table_key("t", cells, "PO > Sil") != table_key("t", cells, "PC > Sil")
    assert table_key("t", cells, "PO > Sil") == table_key("t", cells, "po \u2212 sil".replace("\u2212", ">"))
    assert table_key("t", cells, "A \u2013 B") == table_key("t", cells, "a \u2212 b") == table_key("t", cells, "A - B")


def test_table_keys_are_fixed():
    assert table_key("tbl1", [(0, 0), (1, 0)], "PO > Sil") == "tbl1#3115db5b9c60"
    assert table_key("tbl2", [(3, 0), (4, 0)], "PO > Sil") == "tbl2#0635c18b082d"
    assert table_key("t3", [(7, 2)], "PO > Sil") == "t3#755050223603"
