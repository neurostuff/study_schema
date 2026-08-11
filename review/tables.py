"""Render a paper's coordinate tables, and say which analysis each row belongs to.

Analyses are read off tables, and until now no review task ever showed a reviewer one.
A contrast task named its source in prose -- "reported in: Table 3" -- and asked whether
the record said what the paper said, without showing the object the record was read off.
This module produces the missing artifact: the table as a grid, with each row attributed
to the analysis that claims it.

Ported from `analysis-schema/tools/build_review_app.py`, which built the same thing for a
standalone HTML reviewer. That is a separate git repo with no submodule entry here, so a
cross-repo import would be unversioned; the code is copied instead, with two changes that
real data forced.

**The id join.** ns-pond sanitizes table ids, so `processed/pubget/tables.jsonl` says
`t2` while the sibling `table_001_info.json` says `T2`, and the ids that flow through this
repo -- `stage1/table-map.json` keys, `Analysis.tables` -- are the sanitized ones. The
upstream join on `info["table_id"]` returns nothing for either coordinate table of
`4cRnHYtfSwuK`. The stable key is the CSV filename, which the manifest carries directly.

**The row match.** Upstream matched a row when its numbers *contained* the point's three
values anywhere, so cluster size, t and Brodmann-area columns produced false hits: on
`5Rw4BhGBShSR` Table 1 that is 107 claims over 77 rows. Matching only the columns headed
`x`/`y`/`z` fixes it, and resolving what remains inside section blocks leaves a clean
one-row-one-analysis attribution on five of the six real coordinate tables. The rows still
contested on the sixth are genuinely ambiguous -- seven analyses, no section rows, the
same peak repeated across contrasts -- so they are rendered as contested and counted,
never handed to the first claimant. Showing a wrong attribution confidently is worse than
showing none.

Standard library only, apart from reading the tint count out of `spec`, so the exporter
and any future decoder can both use it. That one import is the point: the number of row
tints this cycles through and the number of `.ns-aN` rules the stylesheet writes are one
constant, and they were two before -- a hardcoded `tints=4` against three rules, so a
fourth analysis was tinted with a class nothing styled.
"""

from __future__ import annotations

import csv
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import spec

#: Where `sync_texts.py` puts the per-table CSVs, relative to `<study>/source/pubget`.
TABLES_SUBDIR = "tables"

#: pandas' name for a header cell that was empty in the source.
PLACEHOLDER = re.compile(r"^Unnamed:\s*\d+(_level_\d+)?$")

#: Header text that means "this column holds a coordinate", used only for the tint.
COORDISH = re.compile(r"^[xyz]$|coordinate|mni|talairach", re.I)

#: Header text that means "this column holds ONE axis of a coordinate". Narrower than
#: COORDISH on purpose: this is what row matching compares, and a column headed
#: "MNI coordinates" spanning three is not itself an axis.
AXIS = {axis: re.compile(rf"^\(?\s*{axis}\s*\)?$", re.I) for axis in "xyz"}

NUMTOK = re.compile(r"[-−+]?\d+\.?\d*")

#: Cells are compared after this, because publishers use U+2212 MINUS SIGN for negative
#: coordinates and the parser emits ASCII.
_MINUS = str.maketrans({"−": "-", "–": "-", "—": "-"})


# -- reading ---------------------------------------------------------------


def _clean(cell: str | None) -> str:
    text = (cell or "").strip()
    return "" if PLACEHOLDER.match(text) else text


def _spans(cells: Sequence[str]) -> list[dict[str, Any]]:
    """Collapse runs of identical header cells into {text, span} for colspan rendering."""

    out: list[dict[str, Any]] = []
    for cell in cells:
        if out and out[-1]["text"] == cell:
            out[-1]["span"] += 1
        else:
            out.append({"text": cell, "span": 1})
    return out


def read_manifest(study_dir: Path) -> dict[str, dict[str, Any]]:
    """`{sanitized table_id: record}` from `<study>/processed/pubget/tables.jsonl`.

    `data_file` is lifted out of `metadata.data_path` because that basename -- not the
    id -- is what locates the CSV. Absent manifest returns {} rather than raising: a
    study with no pubget source is a degraded task, not a crash.
    """

    path = Path(study_dir) / "processed" / "pubget" / "tables.jsonl"
    if not path.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        metadata = record.get("metadata") or {}
        out[record["table_id"]] = {
            "table_id": record["table_id"],
            "table_number": record.get("table_number"),
            "table_label": metadata.get("table_label") or "",
            "caption": record.get("caption") or "",
            "footer": record.get("footer") or "",
            "contains_coordinates": bool(record.get("contains_coordinates")),
            "data_file": Path(metadata.get("data_path") or "").name,
        }
    return out


def _axis_columns(header_rows: Sequence[Sequence[str]], width: int) -> list[int] | None:
    """Column indices of the x, y and z axes, or None if all three are not found.

    Resolved one header row at a time, bottom-up, and only from a row that names all
    three. Both rules are load-bearing, and both were forced by real headers.

    `5Rw4BhGBShSR` Table 1 has "Peak Coordinate" spanning three columns on the first
    header row, "x y z" beneath them on the second, and a "Z" *statistic* column at the
    far right of the first. Accumulating across rows top-down bound z to the statistic
    and produced [3, 4, 7], which matches nothing and silently attributed zero rows.
    Requiring one row to name all three rejects the first row, which has only the Z.

    Within a row, a consecutive triple wins over the leftmost match, so a header reading
    "Region, Z, x, y, z" binds z to the axis rather than to the statistic.
    """

    for row in reversed(list(header_rows)):
        candidates: dict[str, list[int]] = {axis: [] for axis in "xyz"}
        for index in range(min(width, len(row))):
            for axis, pattern in AXIS.items():
                if pattern.match(row[index] or ""):
                    candidates[axis].append(index)
        if not all(candidates.values()):
            continue
        for ix in candidates["x"]:
            if ix + 1 in candidates["y"] and ix + 2 in candidates["z"]:
                return [ix, ix + 1, ix + 2]
        return [candidates["x"][0], candidates["y"][0], candidates["z"][0]]
    return None


def read_table(
    pubget_dir: Path,
    data_file: str,
    *,
    label: str | None = None,
    caption: str | None = None,
) -> dict[str, Any] | None:
    """One table as structured data, ready to render.

    `pubget_dir` is `<study>/source/pubget`; `data_file` is the CSV basename from
    `read_manifest`. `label` and `caption` override the info file, because the manifest's
    values are the ones the record's `Table.table_number` and `Table.caption` were copied
    from and the reviewer should see the same strings twice.
    """

    tables = Path(pubget_dir) / TABLES_SUBDIR
    csv_path = tables / data_file
    info_path = tables / (Path(data_file).stem + "_info.json")
    if not csv_path.is_file() or not info_path.is_file():
        return None

    info = json.loads(info_path.read_text(encoding="utf-8"))
    with csv_path.open(encoding="utf-8", newline="") as handle:
        raw = [[_clean(cell) for cell in row] for row in csv.reader(handle)]
    if not raw:
        return None

    n_header = min(int(info.get("n_header_rows") or 1), len(raw))
    width = max(len(row) for row in raw)
    header_rows = [row + [""] * (width - len(row)) for row in raw[:n_header]]

    body: list[dict[str, Any]] = []
    for row in raw[n_header:]:
        cells = row + [""] * (width - len(row))
        filled = [cell for cell in cells if cell]
        if not filled:
            continue
        # A section row is the table's own heading for the block beneath it -- usually
        # the contrast name, which is exactly what stage 1 splits on. Two shapes occur:
        # the same text repeated across every column, or one cell with no digits in it.
        is_section = (len(set(filled)) == 1 and len(filled) >= 2) or (
            len(filled) == 1 and not re.search(r"\d", filled[0])
        )
        if is_section:
            body.append({"type": "section", "text": filled[0]})
        else:
            body.append({"type": "data", "cells": cells})

    return {
        "table_id": info.get("table_id") or "",
        "label": label or info.get("table_label") or info.get("table_id") or "",
        "caption": caption if caption is not None else (info.get("table_caption") or ""),
        "footer": info.get("table_foot") or "",
        "header": [_spans(row) for row in header_rows],
        # The header before the runs are collapsed. `header` is for rendering colspans;
        # this is for anything that needs one label per column, which markdown does --
        # it admits a single header row, so the levels have to be joined per column.
        "header_cells": header_rows,
        "body": body,
        "width": width,
        "coord_cols": [
            index
            for index in range(width)
            if any(COORDISH.search(row[index]) for row in header_rows)
        ],
        "axis_cols": _axis_columns(header_rows, width),
    }


def load_stage1(path: Path) -> dict[str, list[dict[str, Any]]]:
    """`{pubget table_id: [analysis]}` from `<study>/stage1/analyses.json`, in file order.

    The inner point list is normalized to `points`: a fresh parse writes `points` and the
    pond's own `analyses.jsonl` writes `coordinates`, and a caller should not have to know
    which file it was handed.
    """

    path = Path(path)
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    analyses = payload.get("analyses") if isinstance(payload, Mapping) else payload
    out: dict[str, list[dict[str, Any]]] = {}
    for analysis in analyses or []:
        if not isinstance(analysis, Mapping):
            continue
        entry = dict(analysis)
        entry["points"] = analysis.get("points") or analysis.get("coordinates") or []
        out.setdefault(str(analysis.get("table_id") or ""), []).append(entry)
    return out


# -- attributing rows to analyses ------------------------------------------


def _nums(cells: Iterable[str]) -> list[float]:
    values: list[float] = []
    for cell in cells:
        for token in NUMTOK.findall((cell or "").translate(_MINUS)):
            try:
                values.append(float(token))
            except ValueError:
                pass
    return values


def _point_triples(points: Iterable[Mapping[str, Any]]) -> set[tuple[float, float, float]]:
    wanted: set[tuple[float, float, float]] = set()
    for point in points or []:
        coordinates = point.get("coordinates") or []
        if len(coordinates) != 3:
            continue
        try:
            wanted.add(tuple(round(float(value), 1) for value in coordinates))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return wanted


def match_rows(table: Mapping[str, Any] | None, points: Iterable[Mapping[str, Any]]) -> list[int]:
    """Body-row indices reporting one of `points`.

    Compares the x, y and z columns only. The whole-row fallback is reached solely when
    the header names no axes, and it is the behaviour that over-attributes -- so a caller
    that cares can tell which path ran from `table["axis_cols"]`.
    """

    wanted = _point_triples(points)
    if not table or not wanted:
        return []

    axis_cols = table.get("axis_cols")
    hits: list[int] = []
    for index, row in enumerate(table["body"]):
        if row["type"] != "data":
            continue
        cells = row["cells"]
        if axis_cols:
            try:
                triple = tuple(
                    round(float((cells[column] or "").translate(_MINUS)), 1)
                    for column in axis_cols
                )
            except (ValueError, IndexError):
                continue
            if triple in wanted:
                hits.append(index)
        else:
            have = {round(value, 1) for value in _nums(cells)}
            if any(all(value in have for value in triple) for triple in wanted):
                hits.append(index)
    return hits


def _section_blocks(body: Sequence[Mapping[str, Any]]) -> list[list[int]]:
    """Data-row indices grouped by the section heading above them."""

    blocks: list[list[int]] = [[]]
    for index, row in enumerate(body):
        if row["type"] == "section":
            blocks.append([])
        else:
            blocks[-1].append(index)
    return [block for block in blocks if block]


def _row_triple(row: Mapping[str, Any], axis_cols: Sequence[int]):
    """One data row's (x, y, z), or None if any axis cell is not a number."""

    cells = row["cells"]
    try:
        return tuple(
            round(float((cells[column] or "").translate(_MINUS)), 1) for column in axis_cols
        )
    except (ValueError, IndexError):
        return None


def sequential_partition(
    table: Mapping[str, Any] | None, analyses: Sequence[Mapping[str, Any]]
) -> dict[int, int] | None:
    """`{row: analysis index}` read off the parse's own ordering, or None.

    The parser walks a table top to bottom and hands each analysis a run of consecutive
    rows, so the split it produced *is* a partition of the data rows -- the information
    is already there and does not need rediscovering. Coordinate matching threw it away
    and asked instead "which analyses report this triple?", which on a paper that reports
    one peak under several contrasts answers "four of them" and marks the row contested.
    On the corpus that meant 22 of 77 rows on one table shown as unresolved when the
    parse had in fact assigned every one of them.

    Returned only when the reading is checkable and checks out: the header must name the
    axis columns, the point counts must account for every data row exactly, and each
    row's own x/y/z must equal the point the sequence predicts. Any disagreement means
    the rows and the parse are not in step and the caller falls back to matching. So this
    never *guesses* an ordering -- it confirms one, against the coordinates in the table.
    """

    if not table:
        return None
    axis_cols = table.get("axis_cols")
    if not axis_cols:
        return None

    data_rows = [index for index, row in enumerate(table["body"]) if row["type"] == "data"]
    expected: list[tuple[int, tuple[float, float, float]]] = []
    for position, analysis in enumerate(analyses):
        for point in analysis.get("points") or []:
            coordinates = point.get("coordinates") or []
            if len(coordinates) != 3:
                return None
            try:
                expected.append(
                    (position, tuple(round(float(value), 1) for value in coordinates))
                )
            except (TypeError, ValueError):
                return None

    if len(expected) != len(data_rows) or not expected:
        return None

    owner: dict[int, int] = {}
    for row_index, (position, triple) in zip(data_rows, expected):
        if _row_triple(table["body"][row_index], axis_cols) != triple:
            return None
        owner[row_index] = position
    return owner


def attribute_rows(
    table: Mapping[str, Any] | None, analyses: Sequence[Mapping[str, Any]]
) -> tuple[dict[int, int], dict[int, list[int]]]:
    """`(owner, contested)` over the table's data rows.

    `owner[row] = analysis index` where exactly one analysis claims the row, or where a
    section block's unambiguous claims settle it. `contested[row] = [indices]` for the
    rest -- the same peak reported under several contrasts, which is a real finding and
    must stay visible rather than being assigned to whoever matched first.

    The parse's own row ordering is tried first and settles every row when it verifies;
    everything below is the fallback for a table it cannot be checked against.
    """

    if not table:
        return {}, {}

    ordered = sequential_partition(table, analyses)
    if ordered is not None:
        return ordered, {}

    claims: dict[int, list[int]] = {}
    for position, analysis in enumerate(analyses):
        for row in match_rows(table, analysis.get("points") or []):
            claims.setdefault(row, []).append(position)

    owner = {row: holders[0] for row, holders in claims.items() if len(holders) == 1}
    contested = {row: list(holders) for row, holders in claims.items() if len(holders) > 1}

    # A block whose unambiguous rows all belong to one analysis settles its contested
    # ones too: a table that names the contrast in a section heading has already said
    # which analysis the block is, and a repeated peak inside it is not evidence against
    # that.
    for block in _section_blocks(table["body"]):
        anchors = {owner[row] for row in block if row in owner}
        if len(anchors) != 1:
            continue
        (holder,) = anchors
        for row in block:
            if row in contested and holder in contested[row]:
                owner[row] = holder
                del contested[row]

    return owner, contested


# -- linking a parsed analysis to an encoded one ---------------------------

WORD = re.compile(r"[a-z0-9+<>-]+")
STOP = {
    "the", "of", "and", "in", "for", "with", "a", "table", "results", "region", "regions",
    "cluster", "clusters", "brain", "significant", "analysis", "coordinates", "mni",
}
OPPOSED = (
    ({"left", "l"}, {"right", "r"}),
    ({"increased", "greater", "higher"}, {"decreased", "lower", "reduced"}),
)
DASHES = str.maketrans(
    {"−": "-", "–": "-", "—": "-", " ": " ", " ": " ", "→": ">"}
)


def _tokens(name: str | None) -> set[str]:
    """Normalize dashes first: otherwise 'CS-' and 'CS+' both collapse to 'cs' and a
    direction-reversed sibling scores the same as the correct one."""

    return {w for w in WORD.findall((name or "").translate(DASHES).lower()) if w not in STOP}


def _sides(name: str | None) -> tuple[set[str], set[str]] | None:
    """The token sets either side of the first '>' in a contrast-style name."""

    text = (name or "").translate(DASHES).lower()
    if ">" not in text:
        return None
    lhs, _, rhs = text.partition(">")
    return (
        {w for w in WORD.findall(lhs) if w not in STOP},
        {w for w in WORD.findall(rhs) if w not in STOP},
    )


def name_score(a: str | None, b: str | None) -> float:
    """Token overlap between a parsed analysis name and an encoded record's name.

    Two guards, both learned from real output: names differing only in laterality or in
    direction word would otherwise score high enough to match the wrong sibling, and a
    reversed contrast ("CS- > CS+" against "CS+ > CS-") would tie with the correct one.
    Attaching an encoding to the wrong sibling silently hides a missed-analysis case.
    """

    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    score = len(ta & tb) / min(len(ta), len(tb))
    for left, right in OPPOSED:
        if (ta & left and tb & right) or (ta & right and tb & left):
            score *= 0.5
    sa, sb = _sides(a), _sides(b)
    if sa and sb:
        # Upstream counted raw intersections here. That misses a reversal whenever the
        # two names share tokens on the same side: "Averted > Direct & Downward (T-test)"
        # against "Direct > Averted & Downward (T-test)" scores straight=2, crossed=2 on
        # raw counts, because "downward" and "t-test" sit on the right of both and drown
        # out the two tokens that actually moved. The pair then matched at 1.00 and the
        # two analyses swapped rows.
        #
        # Comparing each side by Jaccard instead makes the small distinguishing side
        # count for as much as the long shared one, which is what a reader does: the
        # left of a contrast name is short and is where the direction lives.
        straight = _jaccard(sa[0], sb[0]) + _jaccard(sa[1], sb[1])
        crossed = _jaccard(sa[0], sb[1]) + _jaccard(sa[1], sb[0])
        if crossed > straight:  # the contrast is reversed
            score *= 0.4
    return score


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    return len(left & right) / len(left | right)


def name_overlap(a: str | None, b: str | None) -> float:
    """Symmetric token overlap, used only to break ties in `name_score`.

    `name_score` divides by the *smaller* token set, deliberately, so a short name can
    still match a longer one. The side effect is that a strict subset scores a perfect
    1.0: on `HU6mqxmtySg3`, "Proverbs > Literal sentences" scores 1.0 against all three of
    "Proverbs", "Transparent proverbs" and "Opaque proverbs > Literal sentences". Three
    ties at 1.0 broken by name ordering rotated the whole assignment by one, giving every
    analysis its neighbour's rows -- silently, and with a perfect score to vouch for it.

    Jaccard has no such blind spot, so it is what separates a subset from an identity
    once `name_score` has already decided both are plausible.
    """

    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class Links:
    """Which parsed analysis each encoded one came from, and both sets of gaps."""

    #: record local_id -> (pubget table_id, sibling index, score)
    matched: dict[str, tuple[str, int, float]] = field(default_factory=dict)
    #: record local_ids no parsed sibling explains -- a name that drifted, or a parser miss
    unmatched_records: list[str] = field(default_factory=list)
    #: (table_id, sibling index) nobody encoded -- the `missed_analysis` signal
    unmatched_siblings: list[tuple[str, int]] = field(default_factory=list)


def link_analyses(
    record: Mapping[str, tuple[str, Sequence[str]]],
    parsed: Mapping[str, Sequence[Mapping[str, Any]]],
    table_map: Mapping[str, str] | None = None,
    threshold: float = 0.5,
) -> Links:
    """Join encoded analyses to the parsed ones they were read off.

    `record` is `{local_id: (name, [record Table local_ids])}`, `parsed` is
    `load_stage1`'s output, `table_map` is `{pubget table_id: record Table local_id}`.

    Two departures from the upstream per-record argmax. Candidates are restricted to the
    table the record already links to, which is information the standalone app did not
    have. And assignment is globally greedy rather than per record, because a per-record
    argmax can hand one parsed analysis to two encodings -- and "two encodings off one
    parse" is precisely the over-split finding, so it must not be papered over.
    """

    pubget_of: dict[str, list[str]] = {}
    for pubget_id, local_id in (table_map or {}).items():
        pubget_of.setdefault(local_id, []).append(pubget_id)

    candidates: list[tuple[float, float, str, str, int]] = []
    for local_id, (name, tables) in record.items():
        allowed = [t for local in tables or [] for t in pubget_of.get(local, [])]
        for table_id in allowed or list(parsed):
            for position, sibling in enumerate(parsed.get(table_id) or []):
                score = name_score(name, sibling.get("name"))
                if score >= threshold:
                    candidates.append(
                        (score, name_overlap(name, sibling.get("name")), local_id, table_id, position)
                    )

    links = Links()
    taken_records: set[str] = set()
    taken_siblings: set[tuple[str, int]] = set()
    # Score first, then symmetric overlap to separate an identity from a subset, then the
    # ids so the result never depends on dict ordering. Dropping the overlap term rotates
    # the assignment on any paper whose analysis names nest -- see `name_overlap`.
    for score, _overlap, local_id, table_id, position in sorted(
        candidates, key=lambda c: (-c[0], -c[1], c[2], c[3], c[4])
    ):
        if local_id in taken_records or (table_id, position) in taken_siblings:
            continue
        links.matched[local_id] = (table_id, position, score)
        taken_records.add(local_id)
        taken_siblings.add((table_id, position))

    links.unmatched_records = sorted(set(record) - taken_records)
    links.unmatched_siblings = sorted(
        (table_id, position)
        for table_id, siblings in parsed.items()
        for position in range(len(siblings))
        if (table_id, position) not in taken_siblings
    )
    return links


# -- rendering -------------------------------------------------------------

_NUMERIC = re.compile(r"^[<>=~+−-]*\s*\d")


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


#: Gutter mark for a row this analysis reports, and for one it might.
_MINE = "✓"
_MAYBE = "?"


def _legend(entries: Sequence[tuple[str, str, str]]) -> str:
    """The key for the gutter column, printed against the table it explains.

    Stating the encoding in a sentence under a 77-row grid means reading the grid
    first and finding out what it meant afterwards. Only the marks a given table
    actually uses are listed, so a table with nothing contested does not raise the
    idea of contested rows at all.
    """

    if not entries:
        return ""
    chips = "".join(
        f'<span class="ns-key"><span class="ns-key-mark {cls}">{_esc(mark)}</span>{_esc(text)}</span>'
        for cls, mark, text in entries
    )
    return '<div class="ns-tbl-key">' + chips + "</div>"


def render_table_html(
    table: Mapping[str, Any] | None,
    *,
    owner: Mapping[int, int] | None = None,
    contested: Mapping[int, Sequence[int]] | None = None,
    highlight: Iterable[int] = (),
    tints: int = len(spec.TINTS),
    note: str = "",
    missing: str = "",
) -> str:
    """The table as HTML, for a `<HyperText inline="true">`.

    Never returns an empty string. `extract_data_types` records the key as HyperText and
    Label Studio's task validation admits only `str` for it -- on import and on the PATCH
    the task sync issues -- so a table that cannot be read has to render as a visible
    warning rather than as nothing.

    Two colouring modes, and each uses **one** vocabulary rather than borrowing the
    other's. `owner=` numbers and tints every row by the analysis that claims it, for the
    table task, where a numbered sibling list sits directly below and the numbers are how
    the grid and that list are one artifact. `highlight=` marks one analysis's rows, for
    the contrast task, where there is no numbered list and only one analysis is in
    question -- so a row is this analysis's (✓), possibly this analysis's (?), or another
    analysis's (blank), and an ordinal would name siblings the reviewer has no key for.

    Mixing the two is what made the first version unreadable: a contrast task showed green
    checkmarks beside red rows reading "1,4", which asked the reviewer to hold a binary
    question and a seven-way identity in the same column.
    """

    if not table:
        return (
            '<div class="ns-warn">No coordinate table could be read'
            + (f" for {_esc(missing)}" if missing else "")
            + ". The split cannot be reviewed against the grid.</div>"
        )

    contested = contested or {}
    highlight = set(highlight)
    width = int(table["width"])
    out: list[str] = []

    caption = table.get("caption") or ""
    out.append(
        '<div class="ns-tbl-cap"><b>'
        + _esc(table.get("label") or "")
        + "</b>"
        + (" &mdash; " + _esc(caption) if caption else "")
        + "</div>"
    )

    # The counts belong in the legend, not in a note under the grid. The pane scrolls
    # at 42vh: this paper's Table 1 puts all three of a contrast's rows below the fold
    # behind 22 contested ones, so a reviewer who reads only what is on screen sees no
    # marked row at all and a note they have to scroll past the table to reach.
    total = sum(1 for row in table["body"] if row["type"] == "data")
    keys: list[tuple[str, str, str]] = []
    if owner is None:
        if highlight:
            keys.append(("ns-key-hit", _MINE, f"this analysis — {len(highlight)} of {total} rows"))
        if contested:
            keys.append(("ns-key-maybe", _MAYBE, f"may be this analysis — {len(contested)}"))
    else:
        if owner:
            keys.append(("ns-key-own", "1", f"the numbered analysis below — {len(owner)} rows"))
        if contested:
            keys.append(("ns-key-maybe", _MAYBE, f"claimed by more than one — {len(contested)}"))
    out.append(_legend(keys))

    out.append('<div class="ns-table"><table class="ns-tbl"><thead>')
    for row in table["header"]:
        cells = "".join(
            "<th" + (f' colspan="{int(cell["span"])}"' if int(cell["span"]) > 1 else "") + ">"
            + _esc(cell["text"])
            + "</th>"
            for cell in row
        )
        out.append("<tr><th></th>" + cells + "</tr>")
    out.append("</thead><tbody>")

    coord_cols = set(table.get("coord_cols") or [])
    for index, row in enumerate(table["body"]):
        if row["type"] == "section":
            out.append(
                f'<tr class="ns-sec"><td colspan="{width + 1}">' + _esc(row["text"]) + "</td></tr>"
            )
            continue

        classes: list[str] = []
        gutter = ""
        # Highlight first. A row that is both this analysis's and contested is a
        # settled question for the contrast reviewer -- it is one of theirs -- and
        # showing it as contested asked them to re-adjudicate the split from a task
        # that has no control for saying so.
        if index in highlight:
            classes.append("ns-hit")
            gutter = _MINE
        elif index in contested:
            classes.append("ns-maybe")
            # Ordinals only where a numbered sibling list gives them a referent.
            gutter = (
                ",".join(str(holder + 1) for holder in contested[index])
                if owner is not None
                else _MAYBE
            )
        elif owner is not None and index in owner:
            classes.append(f"ns-a{owner[index] % max(tints, 1)}")
            gutter = str(owner[index] + 1)

        cells = "".join(
            "<td"
            + _cell_class(position in coord_cols, value)
            + ">"
            + _esc(value)
            + "</td>"
            for position, value in enumerate(row["cells"])
        )
        opening = f'<tr class="{" ".join(classes)}">' if classes else "<tr>"
        out.append(opening + f'<td class="ns-gut">{_esc(gutter)}</td>' + cells + "</tr>")

    out.append("</tbody></table></div>")
    if note:
        out.append('<div class="ns-tbl-note">' + _esc(note) + "</div>")
    return "".join(out)


def is_numeric_cell(value: str | None) -> bool:
    """Does this cell hold a measurement rather than a name?

    Public because the exporter needs the same judgement when it picks a row's
    human-readable handle out of its non-coordinate cells, and two spellings of
    "looks like a number" would disagree on exactly the cells that matter -- `<0.001`,
    `−58`, `4/5`.
    """

    return bool(_NUMERIC.match((value or "").strip()))


def _cell_class(is_coord: bool, value: str) -> str:
    names = []
    if is_coord:
        names.append("ns-coord")
    if is_numeric_cell(value):
        names.append("ns-num")
    return f' class="{" ".join(names)}"' if names else ""


def attribution_note(
    table: Mapping[str, Any] | None,
    analyses: Sequence[Mapping[str, Any]],
    owner: Mapping[int, int],
    contested: Mapping[int, Sequence[int]],
) -> str:
    """What the colouring does not claim, for a reviewer about to judge the split.

    Only what the task does not already show. The analysis count is in the task's own
    label and the attributed and contested counts are in the legend above the grid, so
    stating them again cost three clauses and told nobody anything -- and buried the
    two facts here that are genuinely invisible: rows no analysis claimed, and a table
    with no x/y/z columns whose attribution is therefore a guess.

    What remains is still reported in full, including the counts that reflect badly on
    the attribution: a silent partial match reads as a complete one.
    """

    if not table:
        return ""
    data_rows = sum(1 for row in table["body"] if row["type"] == "data")
    sections = sum(1 for row in table["body"] if row["type"] == "section")
    parts: list[str] = []
    unclaimed = data_rows - len(owner) - len(contested)
    if unclaimed:
        parts.append(f"{unclaimed} row(s) are claimed by no analysis.")
    if not table.get("axis_cols"):
        parts.append(
            "This table names no x/y/z columns, so rows were matched on any number in "
            "the row and may be over-attributed."
        )
    parts.append(
        f"{sections} section heading(s) in the table."
        if sections
        else "No section headings, so the split rests on the caption and the text alone."
    )
    return " ".join(parts)


# -- markdown ---------------------------------------------------------------


def _md_cell(value: str) -> str:
    """A pipe inside a cell would end it, and a newline would end the row."""

    return (value or "").replace("|", "\\|").replace("\n", " ").strip()


def markdown_table(table: Mapping[str, Any] | None) -> str:
    """One table as a GitHub-flavoured pipe table.

    Written for the paper pane, which is a `<Text>` tag and so renders plain text: this
    has to *read* as a table without being rendered as one, which pipes and a delimiter
    row do and tab-separated values do not.

    Markdown allows exactly one header row, so a two-level header is joined per column --
    "MNI coordinate" over "x" becomes "MNI coordinate x". Section rows have no colspan to
    span with, so their text goes in the first cell, bolded, with the rest empty; that is
    what keeps a contrast heading visually distinct from the peaks under it.
    """

    if not table:
        return ""
    width = int(table["width"])

    columns = [""] * width
    for row in table.get("header_cells") or []:
        for index in range(min(width, len(row))):
            cell = _md_cell(row[index])
            if cell and cell not in columns[index]:
                columns[index] = f"{columns[index]} {cell}".strip()
    columns = [c or " " for c in columns]

    rows: list[tuple[bool, list[str]]] = [(False, columns)]
    for row in table["body"]:
        if row["type"] == "section":
            rows.append((True, [_md_cell(row["text"])] + [""] * (width - 1)))
        else:
            cells = [_md_cell(cell) for cell in row["cells"]][:width]
            rows.append((False, cells + [""] * (width - len(cells))))

    # Padded to a common width per column. The pane renders plain text, so this is the
    # difference between a table a reader can scan down and a run of pipes they cannot;
    # it is still valid markdown, just markdown that already looks like the thing it
    # describes.
    #
    # Section rows are excluded from the measurement. A contrast name runs to forty
    # characters and would set the width of whatever column it lands in -- on this
    # corpus, a `kE` column of cluster sizes rendered forty wide, which pushes every
    # coordinate off the right of the pane. They keep their text and simply overrun
    # their cell, which reads as the heading they are.
    measured = [row for section, row in rows if not section]
    widths = [max(len(row[i]) for row in measured) for i in range(width)]

    def line(cells: Sequence[str]) -> str:
        return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) + " |"

    body = [line(rows[0][1]), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    body += [line(row) for _section, row in rows[1:]]

    head = " — ".join(filter(None, [table.get("label") or "", table.get("caption") or ""]))
    parts = [head, "\n".join(body)]
    if table.get("footer"):
        parts.append(_md_cell(table["footer"]))
    return "\n\n".join(p for p in parts if p)
