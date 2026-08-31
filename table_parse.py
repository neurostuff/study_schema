"""Parse one of a paper's coordinate tables out of pubget's CSV, and render it as markdown.

`build_text.py` inlines each table into the review text with this: pubget's
`text_extraction.xsl` deletes `table`, `thead`, `tbody`, `tr`, `td` and `th`, so the
corpus text carries a table's *caption* at the position it occupied and nothing else --
and a reviewer cannot draw a span on a coordinate that is not in the document.

Markdown rather than the tab-separated values pubget's own `_insert_tables` emits,
because the paper pane is a `<Text>` tag rendering plain text: the grid has to *read* as
a table without being rendered as one, which pipes and a delimiter row do and tabs do
not.

**The id join.** ns-pond sanitizes table ids, so `processed/pubget/tables.jsonl` says
`t2` while the sibling `table_001_info.json` says `T2`, and every id that flows through
this repo is the sanitized one. Joining on `info["table_id"]` returns nothing for either
coordinate table of `4cRnHYtfSwuK`. The stable key is the CSV filename, which the
manifest carries directly.

This is the parsing half. Attributing each row to the analysis that reports it, and
rendering the result as a reviewable grid, belong to the review layer and live in
`ns-validate`, which has its own superset of this module.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

#: Where `sync_texts.py` puts the per-table CSVs, relative to `<study>/source/pubget`.
TABLES_SUBDIR = "tables"

#: pandas' name for a header cell that was empty in the source.
PLACEHOLDER = re.compile(r"^Unnamed:\s*\d+(_level_\d+)?$")

#: Header text that means "this column holds a coordinate", used only for the tint.
COORDISH = re.compile(r"^[xyz]$|coordinate|mni|talairach", re.I)

#: Header text that means "this column holds ONE axis of a coordinate". Narrower than
#: COORDISH on purpose: this is what row matching compares, and a column headed
#: "MNI coordinates" spanning three is not itself an axis.
#:
#: The optional trailing unit is what `X (mm)  Y (mm)  Z (mm)` needs -- a spelling that
#: matches neither the bare form nor PAREN_AXIS, because there the parenthesis holds the
#: unit rather than the axis letter. Restricted to length units so a statistic column can
#: never win an axis this way: `Peak (Z)` is still PAREN_AXIS's problem and `Z (p<.001)`
#: matches nothing.
_AXIS_UNIT = r"(?:\s*[(\[]\s*(?:mm|cm)\s*[)\]])?"
AXIS = {axis: re.compile(rf"^\(?\s*{axis}\s*\)?{_AXIS_UNIT}$", re.I) for axis in "xyz"}

#: `Tal(x)`, `Peak(y)`. Tried only after AXIS fails, and the parenthesis must enclose the
#: axis letter and nothing else -- so "Peak coordinates (x,y,z)" is not a match here and
#: falls through to AXIS_TRIPLE, and a "Peak (Z)" statistic column cannot win an axis on
#: its own because all three still have to match in one header row.
PAREN_AXIS = {axis: re.compile(rf"\(\s*{axis}\s*\)\s*$", re.I) for axis in "xyz"}

#: A header cell naming all three axes at once. Two unrelated tables produce it and the run
#: length tells them apart: pandas de-duplicates a repeated header with a ".N" suffix, so
#: three consecutive matches sharing one base are three axis columns wearing one label,
#: while a single match is one column whose cells hold the whole triple.
AXIS_TRIPLE = re.compile(r"[(\[]?\s*x\s*[,;/]\s*y\s*[,;/]\s*z\s*[)\]]?", re.I)

#: pandas' de-duplication suffix on a repeated header cell.
DEDUP = re.compile(r"^(?P<base>.*?)(?:\.(?P<n>\d+))?$")

#: The three numbers of a coordinate, as a cell that holds all of them. Applied to
#: `normalize_number` output, so the dash is ASCII and no space follows a sign.
#:
#: The leading class excludes a sign as well as a digit, and that is the whole trick: it
#: exists to skip an opening bracket, and written as `\D{0,3}` it greedily ate the minus of
#: `-34, 10, 22` instead, capturing 34 and quietly relocating the peak to the other
#: hemisphere.
#: The separator between two of the three: a comma, semicolon or slash, or plain space.
#: `xevP8UDRAVh9` Table 2 uses commas for thirteen rows and spaces for the last four.
_TRIPLE_GAP = r"(?:\s*[,;/]\s*|\s+)"
TRIPLE_CELL = re.compile(
    rf"^[^\d+\-]{{0,3}}(-?\d+\.?\d*){_TRIPLE_GAP}(-?\d+\.?\d*){_TRIPLE_GAP}(-?\d+\.?\d*)\D{{0,3}}$"
)

#: Dash and space variants a publisher sets inside a coordinate.
_COORD_EQUIVALENT = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ", "​": " ",
})


def normalize_number(cell: str | None) -> str:
    """A cell's text with dash variants folded to ASCII and a sign-digit gap closed.

    Both halves matter and the second is easy to miss. Publishers set a thin or
    non-breaking space between a minus sign and its digits -- `SULKxviGFurw` Table 1 reads
    `- 52,- 42,56` -- and a number pattern applied to that finds the magnitude
    and leaves the sign behind, so a peak at -52 matches one at +52. A wrong attribution
    with nothing on the face of it to say so is worse than an unattributed row.
    """

    return re.sub(r"([-+])\s+", r"\1", (cell or "").translate(_COORD_EQUIVALENT)).strip()

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

def _numeric_triple_somewhere(body: Sequence[Mapping[str, Any]], cols: Sequence[int]) -> bool:
    """Whether at least one data row parses as three numbers in these columns.

    A header can name an axis in a column that does not hold one. `kzMj26hGWacQ` t0015 has
    "X,Y,Z" as the second level of a colspan covering columns 4-6, and pandas left-aligns
    it to 0-2 -- so the header says the axes are the columns headed `Brain regions`,
    `Voxels in cluster` and `Hem.`. That answer is worse than none: row matching took the
    strict path, parsed region names as floats, and attributed zero of 34 rows. Confirming
    a candidate against the data is what makes the header's claim checkable.
    """

    for row in body:
        if row.get("type") != "data":
            continue
        cells = row.get("cells") or []
        if max(cols) >= len(cells):
            continue
        if all(_NUMBER.match(normalize_number(cells[col])) for col in cols):
            return True
    return False


#: One number, after the dash variants COORDISH-adjacent headers bring with them.
_NUMBER = re.compile(r"^[-+−‐-―]?\s?\d+\.?\d*$")


def _axis_columns(
    header_rows: Sequence[Sequence[str]], width: int, body: Sequence[Mapping[str, Any]] = ()
) -> list[int] | None:
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

    Every candidate is then confirmed against the body, and two looser header shapes are
    tried only once the strict one has failed -- so a table that resolved before resolves
    the same way, and the corpus's eight unresolved coordinate tables get an answer that
    the data agrees with rather than one the header merely asserts.
    """

    def confirmed(cols: list[int]) -> bool:
        return not body or _numeric_triple_somewhere(body, cols)

    for patterns in (AXIS, PAREN_AXIS):
        for row in reversed(list(header_rows)):
            candidates: dict[str, list[int]] = {axis: [] for axis in "xyz"}
            for index in range(min(width, len(row))):
                for axis, pattern in patterns.items():
                    if pattern.search(row[index] or "") if patterns is PAREN_AXIS \
                            else pattern.match(row[index] or ""):
                        candidates[axis].append(index)
            if not all(candidates.values()):
                continue
            for ix in candidates["x"]:
                if ix + 1 in candidates["y"] and ix + 2 in candidates["z"] \
                        and confirmed([ix, ix + 1, ix + 2]):
                    return [ix, ix + 1, ix + 2]
            leftmost = [candidates["x"][0], candidates["y"][0], candidates["z"][0]]
            if confirmed(leftmost):
                return leftmost

    # A colspan of three columns sharing one coordinate label. Two forms, and the label is
    # the only thing that differs: pandas hands a repeated header over as `... (x,y,z)`,
    # `... (x,y,z).1`, `... (x,y,z).2`, while a colspan whose axis letters live on the row
    # below reads `Peak coordinates`, `Peak coordinates`, `Peak coordinates` -- and on
    # `kzMj26hGWacQ` t0015 pandas left-aligns that lower row to columns 0-2, so the letters
    # are no use where they sit. Either label plus the numeric confirmation is enough: the
    # colspan says these three columns are a coordinate and the data says which they are.
    for row in reversed(list(header_rows)):
        bases = [DEDUP.match(row[index] or "").group("base")
                 for index in range(min(width, len(row)))]
        for index in range(len(bases) - 2):
            base = bases[index]
            if not base or not (AXIS_TRIPLE.search(base) or COORDISH.search(base)):
                continue
            if bases[index + 1] == base and bases[index + 2] == base \
                    and confirmed([index, index + 1, index + 2]):
                return [index, index + 1, index + 2]
    return None


def _axis_cell(
    header_rows: Sequence[Sequence[str]], width: int, body: Sequence[Mapping[str, Any]]
) -> int | None:
    """Index of a single column whose cells hold the whole (x, y, z) triple.

    Six of the corpus's coordinate tables are this shape -- `MNI coordinates (x, y, z)`
    over cells reading `- 52,- 42,56`. Reported separately from `axis_cols` rather than
    folded into it, because that key is a list of three indices at four call sites and
    widening its type there is how a `cells[column]` becomes a silent IndexError.

    Confirmed against the data like any other candidate, and by majority rather than by one
    row: a single triple-looking cell in a column of region names would otherwise carry it.
    """

    for row in reversed(list(header_rows)):
        for index in range(min(width, len(row))):
            if not AXIS_TRIPLE.search(row[index] or ""):
                continue
            cells = [(r.get("cells") or [None] * (index + 1))[index]
                     for r in body if r.get("type") == "data"]
            present = [cell for cell in cells if cell]
            if present and sum(bool(TRIPLE_CELL.match(normalize_number(cell))) for cell in present) \
                    >= len(present) / 2:
                return index
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
        "axis_cols": _axis_columns(header_rows, width, body),
        # Set only when the three axes share one column, and then `axis_cols` is None.
        # A consumer wanting a row's triple has to consult both.
        "axis_cell": _axis_cell(header_rows, width, body),
    }






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
