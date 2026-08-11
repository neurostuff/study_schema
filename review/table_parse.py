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
AXIS = {axis: re.compile(rf"^\(?\s*{axis}\s*\)?$", re.I) for axis in "xyz"}

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
