"""Fail if a built page still shows Markdown source, which means it was not rendered.

    python tools/check_site.py        # after `mkdocs build`
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "build" / "site"

#: Markdown that survives only when a page was left unprocessed.
RAW_MARKDOWN = ("| --- |", "](")
CODE = re.compile(r"<(pre|code)\b.*?</\1>", re.S)
TAG = re.compile(r"<[^>]+>")


def unrendered(page: Path) -> list[str]:
    text = TAG.sub("", CODE.sub("", page.read_text()))
    return [marker for marker in RAW_MARKDOWN if marker in text]


def main() -> int:
    pages = sorted(SITE.rglob("index.html"))
    if not pages:
        sys.exit(f"no pages under {SITE}; run mkdocs build first")
    bad = {str(page.relative_to(SITE)): found for page in pages if (found := unrendered(page))}
    for page, found in bad.items():
        print(f"unrendered Markdown {found} in {page}")
    print(f"checked {len(pages)} pages, {len(bad)} unrendered")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
