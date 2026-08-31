#!/usr/bin/env python3
"""Pull ace-rendered papers from the pond and convert them to the pubget contract.

`sync_texts.py` is the path for a study pubget rendered. Only 12,390 of the pond's
39,270 studies have one; the rest carry an ace render instead, under `processed/ace/`
and `source/ace/`. The two layouts hold the same five artifacts under different names,
so the difference is an ingest problem rather than a pipeline one -- everything
downstream (`parse_tables.py`, `run_extraction.py`, `table_parse.read_manifest`) keeps
reading the pubget paths, and this writes them.

    python review/sync_texts_ace.py --pmids representing-models.pmids

What is copied, verbatim, under `processed/ace/` and `source/ace/`:

    identifiers.json, text.txt, tables.jsonl, analyses.jsonl, coordinates.csv,
    metadata.json, the article HTML, and one HTML file per table

What is *converted*, and therefore written under the pubget paths the pipeline reads:

    source/pubget/tables/<n>.csv        the table grid, colspans expanded
    source/pubget/tables/<n>_info.json  n_header_rows, which read_table needs
    processed/pubget/tables.jsonl       the ace manifest, data_path repointed
    processed/local/text.tables.txt     the text with the tables inlined

**Two things are worse here than on the pubget path, and both are structural.**

An ace text carries no `[pubget-table-N]` placeholder -- `build_text.py` reproduces the
corpus text by re-running pubget's own XSL over `source/pubget/article.xml`, and an ace
study has no article.xml to run it over. So the tables cannot be put back where they
sat; they are appended under a `## Tables` heading instead. Spans into them resolve and
the coordinates are addressable, but a reviewer reading top to bottom meets each table
at the end rather than in its section.

The second is that no equivalence check is possible. `build_text.py` earns its output by
rebuilding with `keep_tables=False` and requiring the corpus text back byte for byte.
There is nothing to rebuild here, so the CSV grid this writes is trusted rather than
checked, and a colspan mis-expanded is a wrong grid that nothing downstream will catch.
Prefer a pubget study when the choice exists.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
from pathlib import Path

import lxml.html

REPO = Path(__file__).resolve().parent.parent
REVIEW = Path(__file__).resolve().parent
sys.path.insert(0, str(REVIEW))
import table_parse  # noqa: E402
from sync_texts import read_pmids  # noqa: E402

DEFAULT_HOST = "beast"
DEFAULT_ROOT = "/data/alejandro/projects/ns-pond/data"

WANTED = [
    "identifiers.json",
    "processed/ace/text.txt",
    "processed/ace/tables.jsonl",
    "processed/ace/analyses.jsonl",
    "processed/ace/coordinates.csv",
    "processed/ace/metadata.json",
    "processed/db/metadata.json",
    "source/ace/",
]


def sync_one(host: str, root: str, study: str, out_root: Path, dry_run: bool) -> bool:
    destination = out_root / study
    destination.mkdir(parents=True, exist_ok=True)
    includes: list[str] = []
    for item in WANTED:
        parts = item.rstrip("/").split("/")
        for depth in range(1, len(parts)):
            includes += ["--include", "/".join(parts[:depth]) + "/"]
        if item.endswith("/"):
            includes += ["--include", item, "--include", item + "**"]
        else:
            includes += ["--include", item]
    command = ["rsync", "-a", "--prune-empty-dirs", *includes, "--exclude", "*",
               f"{host}:{root}/{study}/", str(destination) + "/"]
    if dry_run:
        command.insert(1, "--dry-run")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  rsync failed for {study}: {result.stderr.strip()[:300]}", file=sys.stderr)
        return False
    return True


def html_grid(html: str) -> tuple[list[list[str]], int]:
    """One HTML table as a rectangular grid, plus how many rows are header.

    A cell spanning n columns is written n times rather than once padded, because
    `markdown_table` joins a two-level header per column and skips a value already
    present -- so a repeated "MNI coordinate" produces "MNI coordinate x" over each
    axis column, and a single one would leave two of the three unlabelled.
    """

    root = lxml.html.fromstring(html)
    rows = root.xpath(".//tr")
    header_rows = len(root.xpath(".//thead//tr"))
    grid: list[list[str]] = []
    # (row, column) -> text, filled ahead of the cursor by rowspans above.
    pending: dict[tuple[int, int], str] = {}
    for r, tr in enumerate(rows):
        line: list[str] = []
        column = 0
        for cell in tr.xpath("./td|./th"):
            while (r, column) in pending:
                line.append(pending.pop((r, column)))
                column += 1
            text = " ".join(cell.text_content().split())
            try:
                colspan = max(1, int(cell.get("colspan") or 1))
                rowspan = max(1, int(cell.get("rowspan") or 1))
            except ValueError:
                colspan = rowspan = 1
            for c in range(colspan):
                line.append(text)
                for extra in range(1, rowspan):
                    pending[(r + extra, column + c)] = text
            column += colspan
        while (r, column) in pending:
            line.append(pending.pop((r, column)))
            column += 1
        grid.append(line)
    width = max((len(row) for row in grid), default=0)
    return [row + [""] * (width - len(row)) for row in grid], max(1, header_rows)


def convert(study_dir: Path) -> int:
    """Write the pubget-shaped artifacts. Returns the number of tables converted."""

    ace_manifest = study_dir / "processed" / "ace" / "tables.jsonl"
    ace_tables = study_dir / "source" / "ace" / "tables"
    out_tables = study_dir / "source" / "pubget" / "tables"
    out_manifest = study_dir / "processed" / "pubget" / "tables.jsonl"
    out_manifest.parent.mkdir(parents=True, exist_ok=True)
    out_tables.mkdir(parents=True, exist_ok=True)

    records = []
    if ace_manifest.is_file():
        for line in ace_manifest.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))

    converted = 0
    lines = []
    for record in records:
        table_id = str(record["table_id"])
        html_path = ace_tables / f"{table_id}.html"
        metadata = dict(record.get("metadata") or {})
        if html_path.is_file():
            grid, n_header = html_grid(html_path.read_text(encoding="utf-8", errors="replace"))
            if grid:
                data_file = f"{table_id}.csv"
                buffer = io.StringIO()
                csv.writer(buffer).writerows(grid)
                (out_tables / data_file).write_text(buffer.getvalue(), encoding="utf-8")
                (out_tables / f"{table_id}_info.json").write_text(
                    json.dumps({"n_header_rows": n_header}, indent=1) + "\n", encoding="utf-8")
                metadata["data_path"] = data_file
                converted += 1
        lines.append(json.dumps({**record, "metadata": metadata}, ensure_ascii=False))
    out_manifest.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return converted


def build_text(study_dir: Path) -> Path | None:
    """`processed/local/text.tables.txt`: the ace text with every table appended."""

    source = study_dir / "processed" / "ace" / "text.txt"
    if not source.is_file():
        return None
    manifest = table_parse.read_manifest(study_dir)
    pubget_dir = study_dir / "source" / "pubget"

    parts = [source.read_text(encoding="utf-8").rstrip("\n")]
    rendered = 0
    for entry in manifest.values():
        table = table_parse.read_table(
            pubget_dir, entry["data_file"],
            label=entry["table_label"], caption=entry["caption"])
        grid = table_parse.markdown_table(table)
        if not grid:
            continue
        heading = entry["caption"] or entry["table_label"] or f"Table {entry['table_number']}"
        parts.append(f"### {heading}\n\n{grid}")
        if entry["footer"]:
            parts.append(entry["footer"])
        rendered += 1

    out = study_dir / "processed" / "local" / "text.tables.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    body = parts[0] if rendered == 0 else parts[0] + "\n\n## Tables\n\n" + "\n\n".join(parts[1:])
    out.write_text(body + "\n", encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmids", type=Path, default=REPO / "representing-models.pmids")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, default=REPO / "review" / "texts")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = read_pmids(args.pmids)
    print(f"{len(rows)} studies from {args.pmids.name}")
    failed = []
    for pmid, study, axis in rows:
        if not sync_one(args.host, args.root, study, args.out, args.dry_run):
            failed.append(study)
            continue
        if args.dry_run:
            print(f"  {study}  pmid {pmid}  (dry run)   {axis}")
            continue
        study_dir = args.out / study
        converted = convert(study_dir)
        text = build_text(study_dir)
        size = f"{text.stat().st_size:,}b" if text else "NO TEXT"
        note = "" if converted else ", NO TABLES"
        print(f"  {study}  pmid {pmid}  {size}, {converted} table csv{note}   {axis}")
        if text is None:
            failed.append(study)

    if failed:
        print(f"\n{len(failed)} failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"\nwrote to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
