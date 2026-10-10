"""Which statistic kinds are signed, and the side of a point they give.

A coordinate parse stores no per-point sign; readers derive it from the point's values.
A negative value puts the point on the negative side whatever its kind: p, F and chi-square
are never negative, so a negative one means the column is signed or mislabelled. Otherwise a
point with a value whose kind is directional or unknown (none, `other`, or a string outside
the enum) is positive, and a point whose values are all non-directional, or that has none,
is unsigned. `DIRECTIONAL_KINDS` is the StatisticKind values annotated `directional` in
neuroimaging-paper-parse/parse_enums.yaml and `NON_DIRECTIONAL_KINDS` the rest except
`other`; tests/test_statistics.py keeps them equal.
"""

import math
from typing import Iterable, Mapping, Optional

DIRECTIONAL_KINDS = frozenset({"t", "z", "d", "g", "r", "beta"})
NON_DIRECTIONAL_KINDS = frozenset({"f", "p", "chi_square"})


def is_directional(kind: Optional[str]) -> bool:
    """True for a StatisticKind whose value's sign is the direction of the effect."""
    return kind in DIRECTIONAL_KINDS


def point_side(values: Iterable[Mapping]) -> Optional[str]:
    """"negative", "positive", or None (unsigned) for a point.

    Negative if any value is below zero; positive if any value of a directional or unknown
    kind is zero or above; otherwise unsigned. Missing (None or NaN) numbers are skipped.
    ``values`` are PointValue dicts or models (``kind``, ``value``).
    """
    side = None
    for value in values or ():
        kind = value.get("kind") if isinstance(value, Mapping) else value.kind
        number = value.get("value") if isinstance(value, Mapping) else value.value
        if number is None or math.isnan(number):
            continue
        if number < 0:
            return "negative"
        if getattr(kind, "value", kind) not in NON_DIRECTIONAL_KINDS:
            side = "positive"
    return side
