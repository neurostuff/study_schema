"""Pull paper sources for a pmids file from the ns-pond corpus into review/texts/.

The corpus lives on beast-proxy and is the authority for both the text the offsets
are computed against and the tables stage 1 parses. Nothing here is generated: this
is a copy, so a paper that changes upstream changes here on the next sync rather
than drifting silently.

Layout mirrors the corpus so paths in the record and in the pond agree:

    review/texts/<neurostore_id>/
      identifiers.json
      processed/pubget/{text.txt,tables.jsonl,analyses.jsonl,coordinates.csv,metadata.json}
      source/pubget/tables/          <- CSVs + *_info.json + tables.xml, for the re-parse
      source/ace/<pmid>.html         <- for reading the paper

`review/texts/` is gitignored: it is bulk source material, not schema.

    python review/sync_texts.py --pmids bench-baseline.pmids
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_HOST = "beast-proxy"
DEFAULT_ROOT = "/data/alejandro/projects/ns-pond/data"

#: Copied verbatim per study. Directories end in "/" so rsync recurses into them.
WANTED = [
    "identifiers.json",
    "processed/pubget/text.txt",
    "processed/pubget/tables.jsonl",
    "processed/pubget/analyses.jsonl",
    "processed/pubget/coordinates.csv",
    "processed/pubget/metadata.json",
    # The input to review/build_text.py, which rebuilds the text with each table inline.
    # Without it that script cannot run at all, and rsync tolerates missing sources
    # silently -- so `report()` below names a study that arrived without one.
    "source/pubget/article.xml",
    "source/pubget/tables/",
    "source/ace/",
]


def read_pmids(path: Path) -> list[tuple[str, str, str]]:
    """Parse `pmid<TAB>neurostore_id<TAB>axis`, ignoring comments and blank lines."""

    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            print(f"  skipping unparseable line: {line!r}", file=sys.stderr)
            continue
        pmid, study = parts[0].strip(), parts[1].strip()
        axis = parts[2].strip() if len(parts) > 2 else ""
        rows.append((pmid, study, axis))
    return rows


def sync_one(host: str, root: str, study: str, out_root: Path, dry_run: bool) -> bool:
    """Rsync one study's wanted files. Returns False if rsync reported a failure.

    One rsync call per study with --files-from would need a manifest file on the
    remote; --include/--exclude over the whole tree is simpler and the trees are
    ~1 MB each. Missing sources are tolerated: not every study has an ace render.
    """

    destination = out_root / study
    destination.mkdir(parents=True, exist_ok=True)

    includes: list[str] = []
    for item in WANTED:
        # rsync needs every parent directory included before the leaf.
        parts = item.rstrip("/").split("/")
        for depth in range(1, len(parts)):
            includes += ["--include", "/".join(parts[:depth]) + "/"]
        if item.endswith("/"):
            # The directory itself has to be included before its contents match.
            includes += ["--include", item, "--include", item + "**"]
        else:
            includes += ["--include", item]

    command = [
        "rsync", "-a", "--prune-empty-dirs",
        *includes, "--exclude", "*",
        f"{host}:{root}/{study}/", str(destination) + "/",
    ]
    if dry_run:
        command.insert(1, "--dry-run")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  rsync failed for {study}: {result.stderr.strip()[:300]}", file=sys.stderr)
        return False
    return True


def report(destination: Path) -> str:
    text = destination / "processed" / "pubget" / "text.txt"
    tables = destination / "source" / "pubget" / "tables"
    article = destination / "source" / "pubget" / "article.xml"
    size = f"{len(text.read_text(encoding='utf-8')):,} ch" if text.is_file() else "NO TEXT"
    csvs = len(list(tables.glob("*.csv"))) if tables.is_dir() else 0
    # Named rather than implied: rsync skips a missing source without complaint, and the
    # absence only surfaces later as a build_text.py failure with no obvious cause.
    missing = "" if article.is_file() else ", NO article.xml"
    return f"{size}, {csvs} table csv{missing}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmids", type=Path, default=REPO / "bench-baseline.pmids")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, default=REPO / "review" / "texts")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = read_pmids(args.pmids)
    print(f"{len(rows)} studies from {args.pmids.name}")

    failed = []
    for pmid, study, axis in rows:
        ok = sync_one(args.host, args.root, study, args.out, args.dry_run)
        if not ok:
            failed.append(study)
            continue
        detail = "(dry run)" if args.dry_run else report(args.out / study)
        print(f"  {study}  pmid {pmid}  {detail}   {axis}")

    if failed:
        print(f"\n{len(failed)} failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"\nwrote to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
