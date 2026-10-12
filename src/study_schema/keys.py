"""Analysis keys, computed one way for everyone who mints or checks one.

`ParsedAnalysis.key` is derived from what was extracted -- the analysis's name, its space
and its points -- never from where it was read, so the same analysis read from two sources
(a table and its HTML copy, a table and the sentence restating it) reaches the same key.
Ingestion mints keys, pondie mints them for the analyses a revision adds or the
replacements of a split, and the ingester stores them as `Analysis.source_id`: three
producers, one rule, here.

    analysis_key("PO > Sil", "MNI", points)   -> "analysis#<16 hex>"
    key_for(analysis)                         -> the key its name, space and points give
    normalize_name("PO \u2013  Sil")           -> "po-sil", the name part of the key

The key hashes the compact JSON `[<normalized name>, <space>, [<point>, ...]]`, each point
`[<x>, <y>, <z>, <its own space>, [[<kind>, <value>], ...]]` with its numbers written as
strings, values and points sorted by their JSON; `ParsedAnalysis.key` in coordinates.yaml
spells out every step. 16 hex characters (64 bits) keep a collision among one paper's
analyses out of reach.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Iterable, Optional

__all__ = ["analysis_key", "key_for", "normalize_name"]


_ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")
_DASHES = {
    ord(c): "-"
    for c in map(chr, range(0x110000))
    if unicodedata.category(c) == "Pd" or c == "−"
}


def normalize_name(name: str) -> str:
    """NFKC, casefold, every dash (Unicode Pd, and U+2212 minus) to '-', zero-width characters and all whitespace dropped.

    Whitespace is removed rather than collapsed because names that differ only in spacing
    never denote different analyses: two reads of one sentence give `PO > Sil` and `PO>Sil`.
    """
    text = unicodedata.normalize("NFKC", name).casefold().translate(_DASHES)
    return "".join(_ZERO_WIDTH.sub("", text).split())


def _json(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _point(point, space: str) -> list:
    # Papers print millimetres to at most two decimals, so 0.01 mm keeps every printed
    # coordinate apart while absorbing float noise from parsing and unit conversion.
    xyz = [f"{round(c, 2) + 0.0:.2f}" for c in point.coordinates]
    own_space = point.space if point.space and point.space != space else ""
    # Six significant digits keep every printed digit of a statistic or a p value
    # (p = 3.2e-12 stays apart from 3.3e-12) and drop binary float noise.
    values = sorted(([v.kind, f"{v.value + 0.0:.6g}"] for v in point.values or ()), key=_json)
    return [*xyz, own_space, values]


def analysis_key(name: str, space: Optional[str], points: Iterable) -> str:
    """`analysis#<16 hex>` over the normalized name, the space and the sorted points.

    `points` are `ParsedPoint`s, or anything with their `coordinates`, `space` and
    `values` (each with `kind` and `value`). The name is part of the key because
    differently named analyses can print identical points.
    """
    space = space or ""
    canonical = [normalize_name(name), space, sorted((_point(p, space) for p in points), key=_json)]
    return "analysis#" + hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()[:16]


def key_for(analysis) -> str:
    """The key a `ParsedAnalysis`'s own name, space and points give it."""
    return analysis_key(analysis.name, analysis.coordinate_space, analysis.points or ())
