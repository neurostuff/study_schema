"""Coordinate parses stored as Parquet: one table each of parses, analyses and points.

The models say what a coordinate parse means; this is how many of them are stored. Three
tables, joined on `(article_id, parse_id)` and, for points, `analysis_key`:

    parses.parquet     one row per CoordinateParse, everything but its analyses
    analyses.parquet   one row per ParsedAnalysis, everything but its points
    points.parquet     one row per ParsedPoint

The columns are derived from the generated models, not written out, so a slot added to the
schema becomes a column on the next regeneration, and a slot this module cannot store fails
at import rather than vanishing. Reading gives back the same models that were written --
`read(write(parses)) == parses` is the contract, and the tests hold it over the examples.

The points table follows NIMADS and neurostore's `Point` where the two mean the same thing,
so a point reads the same in all three places:

    NIMADS point                 points.parquet               CoordinateParse
    coordinates [x, y, z]        x, y, z                      ParsedPoint.coordinates
    space                        space                        space
    values [{kind, value}]       values [{kind, value, ...}]  values (+ level, correction)
    (neurostore) subpeak         subpeak                      is_subpeak
    (neurostore) cluster_size    cluster_size                 cluster_size
    (neurostore) cluster_measurement_unit
                                 cluster_measurement_unit     cluster_measure
    (neurostore) order           order                        position in `points`
    analysis                     analysis_key                 ParsedAnalysis.key

and an analysis's `name` and `description` are NIMADS's. NIMADS's `label_id` (the value a
point takes in an image) and `kind` (how the point was derived) have no counterpart in a
parse, and `ParsedPoint.label` is the region name the paper printed, not `label_id`.

Every table also carries `article_id`, the paper's catalog key, so one file answers a query
without a join; Parquet stores a repeated value in a few bytes. `analyses.point_count` says
how many points an analysis has -- zero for a null result, absent where the parse did not
say -- so a reader can count null analyses without touching the points.

Needs the `parquet` extra (pyarrow). Compressed with zstd by default.
"""

from __future__ import annotations

import datetime
import enum
import types
import typing
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Union

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel

from study_schema.models.paper_parse import CoordinateParse, ParsedAnalysis, ParsedPoint

__all__ = [
    "ANALYSES",
    "PARSES",
    "POINTS",
    "from_tables",
    "read",
    "to_tables",
    "write",
]

FILES = {"parses": "parses.parquet", "analyses": "analyses.parquet", "points": "points.parquet"}

#: Point slots renamed to what NIMADS or neurostore's `Point` call them.
POINT_RENAMES = {"is_subpeak": "subpeak", "cluster_measure": "cluster_measurement_unit"}


# -- models to Arrow types ------------------------------------------------------


def _unwrap(annotation: Any) -> Any:
    """Optional[T] -> T; Union[Enum, str] -> str. Anything else is returned as it is."""
    origin = typing.get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _unwrap(args[0])
        if all(a is str or (isinstance(a, type) and issubclass(a, enum.Enum)) for a in args):
            return str
        raise TypeError(f"no Parquet column for the union {annotation}")
    return annotation


def arrow_type(annotation: Any, _seen: tuple = ()) -> pa.DataType:
    """The Arrow type a model field is stored as."""
    tp = _unwrap(annotation)
    if typing.get_origin(tp) is list:
        (item,) = typing.get_args(tp)
        return pa.list_(arrow_type(item, _seen))
    if isinstance(tp, type):
        if issubclass(tp, BaseModel):
            if tp in _seen:
                raise TypeError(f"{tp.__name__} contains itself; Parquet cannot store it")
            return pa.struct(_model_fields(tp, _seen=_seen + (tp,)))
        if issubclass(tp, enum.Enum) or tp is str:
            return pa.string()
        if tp is bool:
            return pa.bool_()
        if tp is int:
            return pa.int64()
        if tp is float:
            return pa.float64()
        if tp in (datetime.datetime, datetime.date, datetime.time):
            # Kept as the ISO text the JSON form carries, so a time zone or its absence
            # comes back exactly as it was written.
            return pa.string()
    raise TypeError(f"no Parquet column for {annotation}")


def _model_fields(model: type[BaseModel], exclude=(), _seen: tuple = ()) -> list[pa.Field]:
    return [
        pa.field(name, arrow_type(info.annotation, _seen), nullable=True)
        for name, info in model.model_fields.items()
        if name not in exclude
    ]


_KEYS = [pa.field("article_id", pa.string(), nullable=False)]

PARSES = pa.schema(_KEYS + _model_fields(CoordinateParse, exclude={"analyses"}))

ANALYSES = pa.schema(
    _KEYS
    + [
        pa.field("parse_id", pa.string(), nullable=False),
        pa.field("order", pa.int32(), nullable=False),
        pa.field("point_count", pa.int32(), nullable=True),
    ]
    + _model_fields(ParsedAnalysis, exclude={"points"})
)

POINTS = pa.schema(
    _KEYS
    + [
        pa.field("parse_id", pa.string(), nullable=False),
        pa.field("analysis_key", pa.string(), nullable=False),
        pa.field("order", pa.int32(), nullable=False),
        pa.field("x", pa.float64(), nullable=False),
        pa.field("y", pa.float64(), nullable=False),
        pa.field("z", pa.float64(), nullable=False),
    ]
    + [
        field.with_name(POINT_RENAMES.get(field.name, field.name))
        for field in _model_fields(ParsedPoint, exclude={"coordinates"})
    ]
)


# -- models to tables and back --------------------------------------------------


def to_tables(parses: Iterable[CoordinateParse]) -> dict[str, pa.Table]:
    """The three tables for a set of parses, originals and revisions alike."""
    rows: dict[str, list[dict]] = {"parses": [], "analyses": [], "points": []}
    for parse in parses:
        data = parse.model_dump(mode="json")
        article = data["header"]["article_id"]
        analyses = data.pop("analyses")
        rows["parses"].append({"article_id": article, **data})
        for a_order, analysis in enumerate(analyses):
            points = analysis.pop("points")
            rows["analyses"].append(
                {
                    "article_id": article,
                    "parse_id": data["parse_id"],
                    "order": a_order,
                    "point_count": None if points is None else len(points),
                    **analysis,
                }
            )
            for p_order, point in enumerate(points or []):
                x, y, z = point.pop("coordinates")  # the schema holds it to exactly three
                rows["points"].append(
                    {
                        "article_id": article,
                        "parse_id": data["parse_id"],
                        "analysis_key": analysis["key"],
                        "order": p_order,
                        "x": x,
                        "y": y,
                        "z": z,
                        **{POINT_RENAMES.get(k, k): v for k, v in point.items()},
                    }
                )
    schemas = {"parses": PARSES, "analyses": ANALYSES, "points": POINTS}
    return {name: pa.Table.from_pylist(rows[name], schema=schemas[name]) for name in rows}


def from_tables(tables: dict[str, pa.Table]) -> list[CoordinateParse]:
    """The parses the three tables hold, in the order they were written."""
    restored = {v: k for k, v in POINT_RENAMES.items()}
    points: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    for row in tables["points"].to_pylist():
        group = (row.pop("article_id"), row.pop("parse_id"), row.pop("analysis_key"))
        order = row.pop("order")
        coordinates = [row.pop("x"), row.pop("y"), row.pop("z")]
        point = {restored.get(k, k): v for k, v in row.items()}
        points[group].append((order, {"coordinates": coordinates, **point}))

    analyses: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    for row in tables["analyses"].to_pylist():
        group = (row.pop("article_id"), row.pop("parse_id"))
        order, count = row.pop("order"), row.pop("point_count")
        found = [p for _, p in sorted(points.pop(group + (row["key"],), []), key=lambda t: t[0])]
        if count is None and found:
            raise ValueError(f"{row['key']} has points but no point_count")
        if count is not None and count != len(found):
            raise ValueError(f"{row['key']}: point_count {count}, {len(found)} points stored")
        analyses[group].append((order, {**row, "points": None if count is None else found}))
    if points:
        raise ValueError(f"points with no analysis: {sorted(points)[:3]}")

    parses = []
    for row in tables["parses"].to_pylist():
        article = row.pop("article_id")
        group = (article, row["parse_id"])
        found = [a for _, a in sorted(analyses.pop(group, []), key=lambda t: t[0])]
        parse = CoordinateParse.model_validate({**row, "analyses": found})
        if parse.header.article_id != article:
            raise ValueError(f"{parse.parse_id}: article_id column disagrees with its header")
        parses.append(parse)
    if analyses:
        raise ValueError(f"analyses with no parse: {sorted(analyses)[:3]}")
    return parses


def write(
    parses: Iterable[CoordinateParse], root: Path | str, compression: str = "zstd"
) -> dict[str, Path]:
    """Write the three tables under `root`. Returns where each went."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, table in to_tables(parses).items():
        path = root / FILES[name]
        pq.write_table(table, path, compression=compression)
        written[name] = path
    return written


def read(root: Path | str) -> list[CoordinateParse]:
    """The parses stored under `root`."""
    root = Path(root)
    schemas = {"parses": PARSES, "analyses": ANALYSES, "points": POINTS}
    return from_tables(
        {name: pq.read_table(root / file, schema=schemas[name]) for name, file in FILES.items()}
    )
