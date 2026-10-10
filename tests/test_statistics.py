from pathlib import Path

import yaml

from study_schema.statistics import DIRECTIONAL_KINDS, is_directional, point_side

ENUMS = Path(__file__).parent.parent / "neuroimaging-paper-parse" / "parse_enums.yaml"


def test_directional_kinds_match_the_schema_annotations():
    kinds = yaml.safe_load(ENUMS.read_text())["enums"]["StatisticKind"]["permissible_values"]
    annotated = {
        kind
        for kind, spec in kinds.items()
        if ((spec or {}).get("annotations") or {}).get("directional")
    }
    assert annotated == DIRECTIONAL_KINDS


def test_point_side_reads_the_first_directional_value():
    assert point_side([{"kind": "p", "value": 0.001}, {"kind": "t", "value": -3.2}]) == "negative"
    assert point_side([{"kind": "z", "value": 0.0}]) == "positive"
    assert point_side([{"kind": "f", "value": 12.0}, {"kind": "p", "value": 0.01}]) is None
    assert point_side([]) is None
    assert not is_directional("chi_square")
