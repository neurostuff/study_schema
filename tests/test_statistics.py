from pathlib import Path

import yaml

from study_schema.statistics import (
    DIRECTIONAL_KINDS,
    NON_DIRECTIONAL_KINDS,
    is_directional,
    point_side,
)

ENUMS = Path(__file__).parent.parent / "neuroimaging-paper-parse" / "parse_enums.yaml"


def test_kind_sets_match_the_schema_annotations():
    kinds = yaml.safe_load(ENUMS.read_text())["enums"]["StatisticKind"]["permissible_values"]
    annotated = {
        kind
        for kind, spec in kinds.items()
        if ((spec or {}).get("annotations") or {}).get("directional")
    }
    assert annotated == DIRECTIONAL_KINDS
    assert set(kinds) - annotated - {"other"} == NON_DIRECTIONAL_KINDS


def test_point_side_reads_the_sign_of_the_values():
    assert point_side([{"kind": "p", "value": 0.001}, {"kind": "t", "value": -3.2}]) == "negative"
    assert point_side([{"kind": "z", "value": 0.0}]) == "positive"
    assert point_side([{"kind": "f", "value": 12.0}, {"kind": "p", "value": 0.01}]) is None
    assert point_side([{"kind": "p", "value": -0.0}]) is None
    assert point_side([{"kind": "p", "value": 0.01}, {"kind": "z", "value": 2.0}]) == "positive"
    assert point_side([{"kind": "chi_square", "value": 4.1}]) is None
    assert point_side([]) is None
    assert point_side(None) is None
    assert not is_directional("chi_square")


def test_a_value_of_unknown_kind_is_read_by_its_sign():
    # An unlabelled column (3YzBcF24AgZG tbl2 "MH 11"): -4.0 cannot be a p, F or chi-square.
    assert point_side([{"kind": None, "value": -4.0}]) == "negative"
    assert point_side([{"kind": None, "value": 3.1}]) == "positive"
    assert point_side([{"kind": "other", "value": -2.5}]) == "negative"
    assert point_side([{"kind": "pseudo-t", "value": -2.5}]) == "negative"
    assert point_side([{"value": 0.0}]) == "positive"
    assert point_side([{"kind": "p", "value": 0.01}, {"kind": None, "value": -4.0}]) == "negative"


def test_a_negative_value_is_negative_whatever_its_kind():
    # p, F and chi-square are never negative: a negative one is a signed or mislabelled column.
    assert point_side([{"kind": "p", "value": -2.3}]) == "negative"
    assert point_side([{"kind": "f", "value": -1.0}]) == "negative"
    assert point_side([{"kind": "chi_square", "value": -0.5}]) == "negative"
    assert point_side([{"kind": None, "value": 3.0}, {"kind": "t", "value": -2.0}]) == "negative"


def test_a_point_without_a_value_is_unsigned():
    assert point_side([{"kind": None, "value": None}]) is None
    assert point_side([{"kind": "t", "value": float("nan")}]) is None
