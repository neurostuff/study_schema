"""Rebuild a paper's review text with its tables inline, from the article XML.

The corpus text carries table *captions* at the position each table occupied and nothing
else -- pubget's `text_extraction.xsl` deletes `table`, `thead`, `tbody`, `tr`, `td` and
`th`. So a reviewer cannot draw a span on a coordinate, and `parse_tables.py` is right
that stage 1 "is the only place the reported effects are enumerated".

The `enh/keep_references_option` branch of pubget fixes that: `keep-tables` leaves a
placeholder where each table sat, and `_insert_tables` replaces it with the table's label,
its grid as tab-separated values, and its footer. This script runs that transform locally
and writes the result beside the synced text.

    python review/build_text.py --pmids bench-baseline.pmids --check-only
    python review/build_text.py --pmids bench-baseline.pmids

**It never overwrites `processed/pubget/text.txt`.** That file is the only thing the
equivalence check below can be checked against, and `sync_texts.py` states the contract it
lives under -- "nothing here is generated: this is a copy". It also runs `rsync -a`
without `--delete`, so an overwritten copy would come back on the next sync depending on
size and mtime. Both variants go under `processed/local/` instead, and which one the
pipeline consumes is a `--text` argument the downstream scripts already take.

The equivalence check is the safety net and it is on by default: rebuilding with
`keep_tables=False` must reproduce the corpus text byte for byte. It does today on all
three baseline papers. If it ever does not, the offsets in every existing record were
computed against a text this code cannot reproduce, and that has to be understood before
anything is regenerated -- so a mismatch writes nothing and exits non-zero.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import table_render  # noqa: E402
from sync_texts import read_pmids  # noqa: E402

#: Left by `text_extraction.xsl` where each table sat. pubget's own `_insert_tables`
#: replaces these with tab-separated values; we substitute markdown instead, because the
#: paper pane is a `<Text>` tag and renders plain text -- so the grid has to *read* as a
#: table without being rendered as one, which pipes and a delimiter row do and tabs do
#: not. The number is the table's rank in the article, which is also how
#: `pubget.extract_articles` numbered the files it wrote.
_PLACEHOLDER = re.compile(r"\[pubget-table-(\d+)\]")

#: A gitignored checkout, the same shape `parse_tables.py` uses for autonima. Not an
#: install: pubget's `__init__` pulls in neuroquery, scikit-learn and scipy for a module
#: that needs only lxml and pandas.
DEFAULT_PUBGET = REPO / ".tmp_repos" / "pubget"

#: The corpus was built with `preserve-crossrefs` on -- its text keeps "Table 1" and
#: "[12]" -- so reproducing it means matching that, not taking pubget's older default.
#: Verified: upstream `56d4e50` drops xref text and does not reproduce the corpus.
PRESERVE_CROSSREFS = True

#: pubget joins these with a blank line, each stripped, empties dropped. Reproduced here
#: rather than calling `TextExtractor.extract`, which returns the parts raw and adds a
#: pmcid the corpus text does not carry.
PARTS = ("title", "keywords", "abstract", "body")


class BuildError(RuntimeError):
    pass


def load_pubget(checkout: Path) -> tuple[Any, Any, str]:
    """Import `pubget._text` and `pubget._utils` without running `pubget/__init__.py`.

    `__init__` imports `_fit_neuroquery`, which needs `neuroquery` -- absent here, and
    not worth installing for two modules. Registering a stub package with the right
    `__path__` first means `import pubget._text` resolves through it and `__init__` never
    runs.

    The checkout is then checked rather than trusted, because the failure mode of a wrong
    one is silent: it would regenerate exactly the text that already exists.
    """

    source = Path(checkout) / "src"
    if not (source / "pubget" / "_text.py").is_file():
        raise BuildError(
            f"no pubget checkout at {checkout}. Either\n"
            f"  git clone -b enh/keep_references_option "
            f"https://github.com/jdkent/pubget {checkout}\n"
            f"or point --pubget at an existing one (e.g. ~/projects/pubget)."
        )

    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    stub = types.ModuleType("pubget")
    stub.__path__ = [str(source / "pubget")]  # type: ignore[attr-defined]
    sys.modules["pubget"] = stub
    text_module = importlib.import_module("pubget._text")
    utils_module = importlib.import_module("pubget._utils")

    if not hasattr(text_module, "_insert_tables"):
        raise BuildError(
            f"the checkout at {checkout} predates the table insertion "
            "(commit 70c50b7). Rebuilding from it would silently reproduce the text "
            "that already exists."
        )
    stylesheet_source = (
        source / "pubget" / "_data" / "stylesheets" / "text_extraction.xsl"
    ).read_text(encoding="utf-8")
    for parameter in ("preserve-crossrefs", "keep-tables"):
        if parameter not in stylesheet_source:
            raise BuildError(f"the stylesheet at {checkout} declares no {parameter!r}")

    try:
        commit = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        commit = "unknown"
    return text_module, utils_module, commit


def _markdown_tables(article_dir: Path) -> dict[int, str]:
    """`{rank in the article: markdown}` for every table pubget managed to parse.

    Keyed on the `table_NNN` file number, which is what the XSLT's
    `count(preceding::table-wrap)` counts -- so a table pubget failed to parse leaves a
    gap here and its placeholder is dropped, exactly as pubget's own insertion does.
    """

    out: dict[int, str] = {}
    for info_file in sorted(
        Path(article_dir).glob(f"{table_render.TABLES_SUBDIR}/table_*_info.json")
    ):
        match = re.match(r"table_(\d+)_info\.json", info_file.name)
        if not match:
            continue
        info = json.loads(info_file.read_text(encoding="utf-8"))
        data_file = info.get("table_data_file")
        if not data_file:
            continue
        table = table_render.read_table(article_dir, data_file)
        if table:
            out[int(match.group(1))] = table_render.markdown_table(table)
    return out


def insert_tables(body: str, article_dir: Path) -> str:
    """Replace each placeholder with its table as markdown.

    Deliberately not `pubget._text._insert_tables`: that emits tab-separated values, and
    a TSV grid in a plain-text pane is a wall of numbers with nothing marking the
    columns. Same placeholders, same file numbering, different rendering.
    """

    tables = _markdown_tables(article_dir)
    return _PLACEHOLDER.sub(
        lambda m: ("\n\n" + tables[int(m.group(1))] + "\n\n") if int(m.group(1)) in tables else "",
        body,
    )


#: `## Results` in a pane that renders plain text shows the hashes, not a heading. The
#: pane is a `<Text>` tag and has to stay one: it is the only region-bearing tag whose
#: offsets serialize as plain `{start, end}` integers, which is what `EvidenceSpan`
#: round-trips on. `<HyperText>` renders markup but indexes its offsets into
#: `String(selection)` over the rendered DOM -- browser- and CSS-dependent, and not
#: reproducible outside a browser. So the formatting goes into the text itself.
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$", re.M)

#: One rule character per level, so depth is legible without counting anything.
_RULES = {1: "=", 2: "=", 3: "-", 4: "."}


def style_headings(text: str) -> str:
    """Turn markdown headings into headings a plain-text pane can show.

    Levels 1 and 2 are set in capitals over a double rule, 3 over a light one, 4 and
    below over a dotted one. Nothing is removed but the hashes and the space after them.
    """

    def one(match):
        level = len(match.group(1))
        title = match.group(2).strip()
        if not title:
            return ""
        shown = title.upper() if level <= 2 else title
        return shown + "\n" + _RULES.get(level, ".") * max(len(shown), 3)

    return _HEADING.sub(one, text)


def build(
    article_xml: Path,
    article_dir: Path,
    text_module: Any,
    *,
    keep_tables: bool,
    style: bool = True,
) -> str:
    """One article's text, assembled exactly as the corpus pipeline assembles it.

    `style` is what the equivalence check turns off. Heading styling is a presentation
    choice made here, not something the corpus pipeline did, so checking a styled build
    against the corpus text would only ever measure whether the styling is a no-op --
    which it is not, and is not meant to be. The check has to see the transform alone.
    """

    from lxml import etree

    from pubget._utils import load_stylesheet  # noqa: PLC0415

    stylesheet = load_stylesheet("text_extraction.xsl")
    transformed = stylesheet(
        etree.parse(str(article_xml)),
        **{
            "preserve-crossrefs": etree.XSLT.strparam(
                "true" if PRESERVE_CROSSREFS else "false"
            ),
            "keep-tables": etree.XSLT.strparam("true" if keep_tables else "false"),
        },
    )
    parts = []
    for name in PARTS:
        element = transformed.find(name)
        value = element.text if element is not None else None
        if name == "body" and keep_tables and value:
            value = insert_tables(value, article_dir)
        if value and value.strip():
            parts.append(value.strip())
    text = "\n\n".join(parts)
    # Applied to the assembled document rather than per part, so a heading that opens a
    # part is treated the same as one in the middle of it.
    return style_headings(text) if style else text


def first_difference(left: str, right: str) -> str:
    """Where two texts diverge, with enough context to recognise the cause."""

    limit = min(len(left), len(right))
    index = next((i for i in range(limit) if left[i] != right[i]), limit)
    window = 90
    return (
        f"first difference at offset {index} "
        f"(rebuilt {len(left)} chars, corpus {len(right)} chars)\n"
        f"  rebuilt: ...{left[max(0, index - window):index + window]!r}...\n"
        f"  corpus : ...{right[max(0, index - window):index + window]!r}..."
    )


def check_equivalence(rebuilt: str, corpus: str) -> str | None:
    """None when they agree, else a message that names the likely cause.

    One cause deserves its own wording. ns-pond's extractor falls back to
    `" ".join(article.xpath(".//text()"))` when the transform raises, and no stylesheet
    setting reproduces that -- reporting it as stylesheet drift would send someone
    looking in the wrong place.
    """

    if rebuilt == corpus:
        return None
    if "## " not in corpus:
        return (
            "the corpus text for this study has no markdown headings, so it is the "
            "XSLT-failure fallback (a flat xpath text join) rather than stylesheet "
            "output. No stylesheet setting reproduces it.\n" + first_difference(rebuilt, corpus)
        )
    return first_difference(rebuilt, corpus)


def build_one(study_dir: Path, text_module: Any, commit: str, *, allow_drift: bool) -> dict:
    """Both variants for one study, gated on the equivalence check."""

    article_dir = study_dir / "source" / "pubget"
    article_xml = article_dir / "article.xml"
    corpus_path = study_dir / "processed" / "pubget" / "text.txt"

    if not article_xml.is_file():
        raise BuildError(
            f"no {article_xml}. Add 'source/pubget/article.xml' to sync_texts.WANTED "
            "and re-sync; a missing article is a failure, not a skip, because the "
            "rebuilt text is what every offset will address."
        )
    if not corpus_path.is_file():
        raise BuildError(f"no corpus text at {corpus_path} to check against")

    corpus = corpus_path.read_text(encoding="utf-8")
    raw = build(article_xml, article_dir, text_module, keep_tables=False, style=False)
    problem = check_equivalence(raw, corpus)
    if problem and not allow_drift:
        raise BuildError(
            "the rebuilt plain text does not reproduce the corpus text.\n" + problem
        )

    plain = build(article_xml, article_dir, text_module, keep_tables=False)
    tables = build(article_xml, article_dir, text_module, keep_tables=True)
    leftover = tables.count("[pubget-table-")
    if leftover:
        raise BuildError(
            f"{leftover} table placeholder(s) survived. Their numbering comes from "
            "count(preceding::table-wrap) and has to line up with the table_NNN files; "
            "a leftover means it did not."
        )

    out_dir = study_dir / "processed" / "local"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, body in (("text.plain.txt", plain), ("text.tables.txt", tables)):
        # newline="" so Python never translates line endings, matching how the staged
        # text is written and read.
        with (out_dir / name).open("w", encoding="utf-8", newline="") as handle:
            handle.write(body)

    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    provenance = {
        "pubget_commit": commit,
        "preserve_crossrefs": PRESERVE_CROSSREFS,
        "article_xml_sha256": digest(article_xml.read_text(encoding="utf-8")),
        "corpus_text_sha256": digest(corpus),
        "equivalent": problem is None,
        "equivalence_override": bool(problem and allow_drift),
        "variants": {
            "plain": {"sha256": digest(plain), "chars": len(plain)},
            "tables": {"sha256": digest(tables), "chars": len(tables)},
        },
    }
    (out_dir / "build.json").write_text(
        json.dumps(provenance, indent=1) + "\n", encoding="utf-8"
    )
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmids", type=Path, default=REPO / "bench-baseline.pmids")
    parser.add_argument("--texts", type=Path, default=REPO / "review" / "texts")
    parser.add_argument("--pubget", type=Path, default=DEFAULT_PUBGET)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="run the equivalence check and write nothing",
    )
    parser.add_argument(
        "--allow-drift",
        action="store_true",
        help="build even when the plain rebuild does not reproduce the corpus text, "
        "and record that it was overridden",
    )
    args = parser.parse_args()

    try:
        text_module, _utils, commit = load_pubget(args.pubget)
    except BuildError as error:
        print(error, file=sys.stderr)
        return 2
    print(f"pubget {commit[:9]} from {args.pubget}, preserve_crossrefs={PRESERVE_CROSSREFS}\n")

    failures = []
    for pmid, study, _axis in read_pmids(args.pmids):
        study_dir = args.texts / study
        try:
            if args.check_only:
                article_dir = study_dir / "source" / "pubget"
                corpus_path = study_dir / "processed" / "pubget" / "text.txt"
                if not (article_dir / "article.xml").is_file():
                    raise BuildError(f"no {article_dir / 'article.xml'}")
                raw = build(
                    article_dir / "article.xml", article_dir, text_module,
                    keep_tables=False, style=False,
                )
                problem = check_equivalence(
                    raw, corpus_path.read_text(encoding="utf-8")
                )
                if problem:
                    raise BuildError(problem)
                print(f"  {study}  pmid {pmid}  reproduces the corpus text ({len(raw):,} ch)")
            else:
                info = build_one(study_dir, text_module, commit, allow_drift=args.allow_drift)
                mark = "" if info["equivalent"] else "  DRIFT OVERRIDDEN"
                print(
                    f"  {study}  pmid {pmid}  "
                    f"plain {info['variants']['plain']['chars']:,} ch, "
                    f"tables {info['variants']['tables']['chars']:,} ch{mark}"
                )
        except BuildError as error:
            print(f"  {study}  pmid {pmid}  FAILED\n    {error}", file=sys.stderr)
            failures.append(study)

    if failures:
        print(f"\n{len(failures)} failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("\nchecked, nothing written" if args.check_only else f"\nwrote to {args.texts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
