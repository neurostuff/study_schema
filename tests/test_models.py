"""The examples satisfy the models and the JSON Schemas, and the two agree."""

import pytest
from pydantic import ValidationError

from conftest import load
from study_schema import jsonschema
from study_schema.keys import analysis_key, key_for, normalize_name
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


@pytest.mark.parametrize(
    "broken, field",
    [
        ({"occurrences": [{"found_by": "text", "start_char": 0, "end_char": 5}]}, "url"),
        ({"url": "https://osf.io/abcde"}, "occurrences"),
        ({"url": "https://osf.io/abcde", "occurrences": []}, "occurrences"),
        ({"url": "https://osf.io/abcde", "occurrences": [{"found_by": "footnote"}]}, "found_by"),
    ],
)
def test_a_link_needs_its_url_and_where_it_appears(example_paths, broken, field):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    data = load(example_paths["parsed_paper"])
    data["links"] = [
        {
            "url": "https://openneuro.org/datasets/ds000001",
            "has_nifti": True,
            "occurrences": [
                {"found_by": "markup", "start_char": 10, "end_char": 52},
                {"found_by": "text", "start_char": 900, "end_char": 942},
            ],
        },
        {
            "url": "https://doi.org/10.17605/osf.io/abcde",
            "occurrences": [{"found_by": "reference", "reference_id": "ref12"}],
        },
    ]
    data["sections"].append({"kind": "data_availability", "start_char": 0, "end_char": 60})
    paper = ParsedPaper.model_validate(data)
    validator.validate(data, jsonschema.load("parsed-paper"))
    assert [link.has_nifti for link in paper.links] == [True, None]
    assert paper.links[1].occurrences[0].start_char is None

    data["links"].append(broken)
    with pytest.raises(ValidationError, match=field):
        ParsedPaper.model_validate(data)
    with pytest.raises(validator.ValidationError):
        validator.validate(data, jsonschema.load("parsed-paper"))


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


def _point(x, y, z, *values, space=None, **where):
    return {"coordinates": [x, y, z], "space": space, "values": [{"kind": k, "value": v} for k, v in values], **where}


def _analysis(name, points, space="MNI", **where):
    return paper_parse.ParsedAnalysis.model_validate(
        {"key": "unset", "origin": "table", "table_id": "tbl1", "name": name, "role": "result",
         "coordinate_space": space, "points": points, **where}
    )


GAIN = [_point(12, 10, -6, ("t", 5.8), ("p", 0.0001)), _point(34, 22, 2, ("t", 4.9))]


def test_key_rule():
    # The canonical form written out by hand from the rule in coordinates.yaml.
    canonical = '["po>sil","MNI",[["-4.00","10.25","2.50","",[["p","3.2e-12"],["t","5.8"]]],["12.00","0.00","0.00","TAL",[]]]]'
    expected = "analysis#" + __import__("hashlib").sha256(canonical.encode()).hexdigest()[:16]
    points = [_point(12, -0.0, 0.004, space="TAL"), _point(-4, 10.25, 2.5, ("t", 5.8), ("p", 3.2e-12))]
    assert key_for(_analysis("PO > Sil", points)) == expected
    assert analysis_key("PO > Sil", "MNI", _analysis("x", points).points) == expected


def test_keys_are_fixed():
    assert key_for(_analysis("Gain > Neutral", GAIN)) == "analysis#6d50d24d17f8bc7a"
    assert key_for(_analysis("Gain > Neutral", [])) == "analysis#f90716ec7b6e2688"
    assert key_for(_analysis("Gain > Neutral", [], space=None)) == "analysis#c54b9336230e6424"


def test_example_keys_are_derived(example_paths):
    for which in ("coordinate_parse", "revision"):
        parse = CoordinateParse.model_validate(load(example_paths[which]))
        for analysis in parse.analyses:
            assert key_for(analysis) == analysis.key


def test_where_an_analysis_was_read_is_not_in_its_key():
    table = _analysis(
        "Gain > Neutral",
        [_point(12, 10, -6, ("t", 5.8), ("p", 0.0001), row=0, column_group=0), _point(34, 22, 2, ("t", 4.9), row=1, column_group=0)],
        cells=[{"row": 0, "column_group": 0}, {"row": 1, "column_group": 0}],
    )
    html_copy = _analysis(
        "Gain > Neutral",
        [_point(12, 10, -6, ("t", 5.8), ("p", 0.0001), row=3, column_group=1), _point(34, 22, 2, ("t", 4.9), row=4, column_group=1)],
        table_id="html-table-1",
        cells=[{"row": 3, "column_group": 1}, {"row": 4, "column_group": 1}],
    )
    span = {"start_char": 1200, "end_char": 1288}
    text = _analysis(
        "Gain > Neutral",
        [_point(12, 10, -6, ("t", 5.8), ("p", 0.0001), text_span=span), _point(34, 22, 2, ("t", 4.9), text_span=span)],
        origin="text",
        table_id=None,
        text_spans=[span],
    )
    assert key_for(table) == key_for(html_copy) == key_for(text) == key_for(_analysis("Gain > Neutral", GAIN))


def test_point_and_value_order_do_not_matter():
    shuffled = [_point(34, 22, 2, ("t", 4.9)), _point(12, 10, -6, ("p", 0.0001), ("t", 5.8))]
    assert key_for(_analysis("Gain > Neutral", shuffled)) == key_for(_analysis("Gain > Neutral", GAIN))


def test_float_noise_and_a_restated_space_do_not_change_the_key():
    noisy = [_point(12.000000001, 10, -6, ("t", 5.8000000001), ("p", 0.0001), space="MNI"), _point(34, 22, 2.0, ("t", 4.9))]
    assert key_for(_analysis("Gain > Neutral", noisy)) == key_for(_analysis("Gain > Neutral", GAIN))
    assert key_for(_analysis("A", [_point(0.0, 0, 0)])) == key_for(_analysis("A", [_point(-0.0, 0, 0)]))


def test_any_change_of_content_is_a_new_key():
    base = key_for(_analysis("Gain > Neutral", GAIN))
    moved = [_point(12, 10, -8, ("t", 5.8), ("p", 0.0001)), GAIN[1]]
    other_kind = [_point(12, 10, -6, ("z", 5.8), ("p", 0.0001)), GAIN[1]]
    other_value = [_point(12, 10, -6, ("t", 5.9), ("p", 0.0001)), GAIN[1]]
    own_space = [_point(12, 10, -6, ("t", 5.8), ("p", 0.0001), space="TAL"), GAIN[1]]
    changed = [
        key_for(_analysis("Loss > Neutral", GAIN)),
        key_for(_analysis("Gain > Neutral", moved)),
        key_for(_analysis("Gain > Neutral", other_kind)),
        key_for(_analysis("Gain > Neutral", other_value)),
        key_for(_analysis("Gain > Neutral", GAIN, space="TAL")),
        key_for(_analysis("Gain > Neutral", own_space)),
        key_for(_analysis("Gain > Neutral", GAIN[:1])),
    ]
    assert base not in changed and len(set(changed)) == len(changed)


def test_an_analysis_with_no_points_is_keyed_by_name_and_space():
    canonical = '["gain>neutral","MNI",[]]'
    assert key_for(_analysis("Gain > Neutral", [])) == "analysis#" + __import__("hashlib").sha256(canonical.encode()).hexdigest()[:16]
    assert key_for(_analysis("Gain > Neutral", [])) != key_for(_analysis("Gain > Neutral", [], space="TAL"))
    assert key_for(_analysis("Gain > Neutral", [])) != key_for(_analysis("Gain > Neutral", [], space=None))
    assert key_for(_analysis("Gain > Neutral", [])) != key_for(_analysis("Loss > Neutral", []))


def test_the_halves_of_a_sign_split_have_their_own_keys():
    original = _analysis("Gain > Neutral", GAIN, split={"half": "original", "rule": "sign_of_directional_statistic"})
    # The inverse half's printed negatives are negated so they read positive.
    inverse_points = [_point(-4, -48, 28, ("t", 4.4)), _point(-44, -64, 30, ("t", 4.1))]
    inverse = _analysis("Neutral > Gain", inverse_points, split={"half": "inverse", "rule": "sign_of_directional_statistic"})
    printed = [_point(-4, -48, 28, ("t", -4.4)), _point(-44, -64, 30, ("t", -4.1))]
    keys = {key_for(original), key_for(inverse), key_for(_analysis("Neutral > Gain", printed)), key_for(_analysis("Gain > Neutral", inverse_points))}
    assert len(keys) == 4


def test_name_variants_share_a_key():
    base = analysis_key("PO > Sil", "MNI", [])
    assert analysis_key("po  >\tSIL ", "MNI", []) == base
    assert analysis_key("PO > S\u200bil", "MNI", []) == base
    assert analysis_key("A \u2013 B", "MNI", []) == analysis_key("a - b", "MNI", [])
    assert analysis_key("A \u2014 B", "MNI", []) == analysis_key("a-b".replace("-", " - "), "MNI", [])
    for dash in ("\u2212", "\u2010", "\u2011"):
        assert analysis_key(f"PO {dash} Sil", "MNI", []) == analysis_key("PO - Sil", "MNI", [])
        assert normalize_name(f"A{dash}B") == "a-b"
    assert normalize_name("\uff21\u2003\u2013\u2003B") == "a-b"


def test_names_differing_only_in_whitespace_share_a_key():
    points = _analysis("x", GAIN).points
    assert normalize_name("PO > Sil") == normalize_name("PO>Sil") == "po>sil"
    assert analysis_key("PO > Sil", "MNI", points) == analysis_key(" PO>\u00a0Sil", "MNI", points)
    assert analysis_key("PO > Sil", "MNI", points) != analysis_key("PO < Sil", "MNI", points)
    assert analysis_key("PO > Sil", "MNI", points) != analysis_key("PC > Sil", "MNI", points)


def _with_edit_verdict(example_paths):
    data = load(example_paths["revision"])
    [split] = [v for v in data["verdicts"] if v["verdict"] == "split"]
    data["verdicts"] = [
        {
            "key": split["key"],
            "verdict": "edit",
            "replaced_by": split["replaced_by"][:1],
            "reason": "A minus sign was dropped from one point of the analysis.",
        }
    ]
    return data


def test_an_edit_verdict_validates(example_paths):
    data = _with_edit_verdict(example_paths)
    assert CoordinateParse.model_validate(data).verdicts[0].verdict == "edit"
    real = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    real.validate(data, jsonschema.load("coordinate-parse"))
