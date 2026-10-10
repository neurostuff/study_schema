"""Which statistic kinds are signed, and the side of a point they give.

A coordinate parse stores no per-point sign: a directional value's sign gives the point's
side, and a point with none is unsigned and takes the side of its analysis's `split` half.
`DIRECTIONAL_KINDS` is the StatisticKind values annotated `directional` in
neuroimaging-paper-parse/parse_enums.yaml; tests/test_statistics.py keeps the two equal.
"""

from typing import Iterable, Mapping, Optional

DIRECTIONAL_KINDS = frozenset({"t", "z", "d", "g", "r", "beta"})


def is_directional(kind: Optional[str]) -> bool:
    """True for a StatisticKind whose value's sign is the direction of the effect."""
    return kind in DIRECTIONAL_KINDS


def point_side(values: Iterable[Mapping]) -> Optional[str]:
    """"negative" or "positive" from a point's first directional value; None if unsigned.

    ``values`` are PointValue dicts or models (``kind``, ``value``).
    """
    for value in values or ():
        kind = value["kind"] if isinstance(value, Mapping) else value.kind
        number = value["value"] if isinstance(value, Mapping) else value.value
        if is_directional(getattr(kind, "value", kind)):
            return "negative" if number < 0 else "positive"
    return None
