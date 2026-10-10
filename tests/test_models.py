"""The examples satisfy the models and the JSON Schemas, and the two agree."""

import pytest
from pydantic import ValidationError

from conftest import load
from study_schema import jsonschema
from study_schema.keys import cell_locator, key_for, span_key, span_locator, table_key
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
    assert table_key("tbl1", [(1, 0), (0, 0), (1, 0)]) == "tbl1#" + __import__("hashlib").sha1(b"0:0,1:0").hexdigest()[:12]
    assert span_key("text", [(5, 9)]).startswith("text#")
    with pytest.raises(ValueError):
        table_key("tbl1", [])
    with pytest.raises(ValueError):
        span_key("table", [(0, 1)])


def test_keys_hash_their_locators():
    sha1 = __import__("hashlib").sha1
    assert cell_locator([(4, 0), (3, 0), (4, 0)]) == "3:0,4:0"
    assert span_locator([(1288, 1300), (1200, 1288)]) == "1200-1288,1288-1300"
    assert table_key("tbl2", [(4, 0), (3, 0)]) == "tbl2#" + sha1(b"3:0,4:0").hexdigest()[:12]
    assert span_key("text", [(5, 9)]) == "text#" + sha1(b"5-9").hexdigest()[:12]
    assert cell_locator([]) == span_locator([]) == ""


def test_example_keys_are_derived(example_paths):
    for which in ("coordinate_parse", "revision"):
        parse = CoordinateParse.model_validate(load(example_paths[which]))
        for analysis in parse.analyses:
            assert key_for(analysis) == analysis.key
