"""Assemble the documentation site sources into `build/docs/` for mkdocs.

The reference pages are generated from the LinkML schemas with `gen-doc`, and the guides are
staged from the repository root, so nothing here is committed:

    python tools/build_docs.py
    mkdocs build --strict

The guides link to each other, to the schema directories and to files that are not part of
the site. Their links are rewritten while staging so the markdown on disk keeps working on
GitHub: schema directories point at the reference and other repository files at GitHub. A
link to a file that does not exist is left alone, so `mkdocs build --strict` fails on it.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from linkml.generators.docgen import DocGenerator

from study_schema import linkml

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "docs"
BLOB = "https://github.com/neurostuff/study_schema/blob/main/"
TREE = "https://github.com/neurostuff/study_schema/tree/main/"

#: Top-level schemas; the evidence schema is imported by extraction and documented with it.
SCHEMAS = (linkml.STORAGE, linkml.EXTRACTION, linkml.PAPER_PARSE)

GUIDES = (
    "schema-tutorial.md",
    "analysis-entities.md",
    "representing-models.md",
    "storage-schema-design-notes.md",
    "extraction-readme.md",
    "standards-crosswalk.md",
    "ars-crosswalk.md",
)

LINK = re.compile(r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(([^)\s#]*)(#[^)\s]*)?\)")


def rewrite_links(markdown: str, source: Path, staged: set[str]) -> str:
    def replace(match: re.Match) -> str:
        text, target, anchor = match.group(1), match.group(2), match.group(3) or ""
        if not target or re.match(r"[a-z]+:", target):
            return match.group(0)
        path = (source.parent / target).resolve()
        try:
            relative = path.relative_to(ROOT).as_posix()
        except ValueError:
            relative = None
        if relative == "README.md":
            return f"[{text}](index.md{anchor})"
        if relative in staged:
            return match.group(0)
        if relative and relative.startswith("src/study_schema/linkml/") and path.is_dir():
            return f"[{text}](reference/{path.name}/index.md{anchor})"
        if relative and path.exists():
            return f"[{text}]({TREE if path.is_dir() else BLOB}{relative})"
        return match.group(0)

    return LINK.sub(replace, markdown)


def main() -> None:
    if BUILD.parent.exists():
        shutil.rmtree(BUILD.parent)
    BUILD.mkdir(parents=True)

    for schema in SCHEMAS:
        DocGenerator(str(schema), mergeimports=True).serialize(
            directory=str(BUILD / "reference" / schema.stem)
        )

    sources = {"README.md": "index.md", **{name: name for name in GUIDES}}
    for name, target in sources.items():
        text = rewrite_links((ROOT / name).read_text(), ROOT / name, set(sources))
        (BUILD / target).write_text(text)


if __name__ == "__main__":
    main()
