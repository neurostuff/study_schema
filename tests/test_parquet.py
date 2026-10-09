"""Coordinate parses survive Parquet exactly, and the points table reads like NIMADS."""

import pytest

pa = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq  # noqa: E402

from conftest import load  # noqa: E402
from study_schema import parquet  # noqa: E402
from study_schema.models.paper_parse import (  # noqa: E402
    CoordinateParse,
    ParsedAnalysis,
    ParsedPoint,
)


@pytest.fixture
def parses(example_paths):
    return [
        CoordinateParse.model_validate(load(example_paths["coordinate_parse"])),
        CoordinateParse.model_validate(load(example_paths["revision"])),
    ]


def test_round_trip(parses, tmp_path):
    parquet.write(parses, tmp_path)
    assert parquet.read(tmp_path) == parses


def test_every_slot_has_a_column():
    analyses = set(parquet.ANALYSES.names)
    assert set(ParsedAnalysis.model_fields) - {"points"} <= analyses
    points = set(parquet.POINTS.names)
    for name in set(ParsedPoint.model_fields) - {"coordinates"}:
        assert parquet.POINT_RENAMES.get(name, name) in points
    assert set(CoordinateParse.model_fields) - {"analyses"} <= set(parquet.PARSES.names)


def test_points_use_nimads_and_neurostore_names():
    names = set(parquet.POINTS.names)
    assert {"x", "y", "z", "space", "values", "subpeak", "cluster_size",
            "cluster_measurement_unit", "order", "analysis_key"} <= names
    value = parquet.POINTS.field("values").type.value_type
    assert {"kind", "value"} <= {value.field(i).name for i in range(value.num_fields)}
    assert {"name", "description"} <= set(parquet.ANALYSES.names)


def test_null_results_are_counted_not_lost(parses, tmp_path):
    revision = parses[1]
    null = next(a for a in revision.analyses if a.origin == "text")
    assert null.points == []
    revision.analyses[0].points = None  # a parse that did not say
    parquet.write([revision], tmp_path)
    counts = dict(zip(*pq.read_table(tmp_path / "analyses.parquet", columns=["key", "point_count"]).to_pydict().values()))
    assert counts[null.key] == 0 and counts[revision.analyses[0].key] is None
    assert parquet.read(tmp_path) == [revision]


def test_inconsistent_tables_are_refused(parses):
    tables = parquet.to_tables(parses)
    short = dict(tables, points=tables["points"].slice(1))
    with pytest.raises(ValueError, match="point_count"):
        parquet.from_tables(short)
    orphaned = dict(tables, analyses=tables["analyses"].slice(0, 0))
    with pytest.raises(ValueError, match="no analysis"):
        parquet.from_tables(orphaned)


def test_coordinates_read_without_the_models(parses, tmp_path):
    parquet.write(parses, tmp_path)
    table = pq.read_table(tmp_path / "points.parquet", columns=["analysis_key", "x", "y", "z"])
    assert table.num_rows == sum(len(a.points or []) for p in parses for a in p.analyses)
