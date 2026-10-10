"""Analysis keys, computed one way for everyone who mints or checks one.

`ParsedAnalysis.key` is derived from where an analysis was read, never from its points, so
a re-run that reads the same place reaches the same key and an "n.s." contrast with no
points still has one. Ingestion mints keys, pondie mints them for the analyses a revision
adds or the replacements of a split, and the ingester stores them as `Analysis.source_id`:
three producers, one rule, here.

    table_key("tbl2", [(3, 0), (4, 0)])      -> "tbl2#<12 hex>"
    span_key("text", [(1200, 1288)])          -> "text#<12 hex>"
    key_for(analysis)                         -> the key its cells or spans give
    cell_locator([(4, 0), (3, 0)])            -> "3:0,4:0", the string a table key hashes
    span_locator([(1200, 1288)])              -> "1200-1288", the string a span key hashes

Use the locators when a hash must cover the same cells or spans a key does (neurostore's
entity hashes), so the canonical form lives here once.
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional

__all__ = ["cell_locator", "key_for", "span_key", "span_locator", "table_key"]


def _digest(locator: str) -> str:
    return hashlib.sha1(locator.encode("utf-8")).hexdigest()[:12]


def cell_locator(cells: Iterable[tuple[int, int]]) -> str:
    """Each cell's `<row>:<column_group>`, deduplicated, sorted and comma-joined; '' for none."""
    unique = sorted({(int(row), int(group)) for row, group in cells})
    return ",".join(f"{row}:{group}" for row, group in unique)


def span_locator(spans: Iterable[tuple[int, int]]) -> str:
    """Each span's `<start_char>-<end_char>`, deduplicated, sorted and comma-joined; '' for none."""
    unique = sorted({(int(start), int(end)) for start, end in spans})
    return ",".join(f"{start}-{end}" for start, end in unique)


def table_key(table_id: str, cells: Iterable[tuple[int, int]]) -> str:
    """`<table_id>#<h>`, `h` the first 12 hex of the sha1 of `cell_locator(cells)`."""
    locator = cell_locator(cells)
    if not locator:
        raise ValueError(f"a table analysis of {table_id} needs at least one cell")
    return f"{table_id}#{_digest(locator)}"


def span_key(origin: str, spans: Iterable[tuple[int, int]]) -> str:
    """`text#<h>` or `figure#<h>`, `h` the first 12 hex of the sha1 of `span_locator(spans)`."""
    if origin not in ("text", "figure"):
        raise ValueError(f"span keys are for text and figure analyses, not {origin!r}")
    locator = span_locator(spans)
    if not locator:
        raise ValueError(f"a {origin} analysis needs at least one span")
    return f"{origin}#{_digest(locator)}"


def key_for(analysis) -> Optional[str]:
    """The key a `ParsedAnalysis`'s own cells or spans give it; None if it has neither."""
    if analysis.origin == "table":
        if not analysis.table_id or not analysis.cells:
            return None
        return table_key(analysis.table_id, ((c.row, c.column_group) for c in analysis.cells))
    if not analysis.text_spans:
        return None
    return span_key(analysis.origin, ((s.start_char, s.end_char) for s in analysis.text_spans))
