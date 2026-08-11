#!/usr/bin/env python3
"""Put a paper's text where Label Studio can serve it, and refuse if it is the wrong text.

Tasks carry a URL, never the text. One paper is 25-60 KB and hundreds of tasks;
inlining would produce ~18 MB of task JSON per paper, where a URL costs 50 bytes
and the browser fetches it once and serves the rest of that paper's tasks from
cache.

The gate is the whole point of this module. Evidence offsets are integers into a
specific text, and a `<Text>` region stores `{start, end, text}` -- so serving a
different text than the offsets were computed against does not fail, it silently
highlights whatever now sits at those numbers. Staging therefore refuses to write
unless `sha256(text)` equals the record's `source_text_hash`, and the exporter
re-verifies every span against the same bytes before shipping it.
"""

from __future__ import annotations

from pathlib import Path

import spec
import text_index


class TextMismatch(RuntimeError):
    """The text on disk is not the text the record's offsets address."""


def url_for(paper_id: str) -> str:
    return spec.LOCAL_FILES_URL.format(relative=f"{spec.TEXT_SUBDIR}/{paper_id}.txt")


def staged_path(files_root: Path, paper_id: str) -> Path:
    return Path(files_root) / spec.TEXT_SUBDIR / f"{paper_id}.txt"


def stage(files_root: Path, paper_id: str, normalized: str, expected_hash: str | None) -> str:
    """Write the text where the serving endpoint will find it; return its URL."""

    if expected_hash:
        actual = text_index.text_hash(normalized)
        if actual != expected_hash:
            raise TextMismatch(
                f"refusing to stage {paper_id}: text hash {actual[:12]}... does not match "
                f"the record's source_text_hash {expected_hash[:12]}...\n"
                "The record's offsets address a different text. Rebuild the record "
                "against this text rather than serving a text it was not built from."
            )

    destination = staged_path(files_root, paper_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # newline="" so Python does not translate \n on write: the served bytes must be
    # exactly the bytes that were hashed and that offsets address.
    with destination.open("w", encoding="utf-8", newline="") as stream:
        stream.write(normalized)
    return url_for(paper_id)


def read_staged(files_root: Path) -> dict[str, str]:
    """paper_id -> the exact bytes Label Studio serves for it.

    Read with `newline=""` for the same reason `stage` writes with it: universal
    newline translation would shorten the document, and every offset computed
    against it would be wrong by the number of line endings before it.
    """

    texts = {}
    for path in sorted((Path(files_root) / spec.TEXT_SUBDIR).glob("*.txt")):
        with path.open(encoding="utf-8", newline="") as stream:
            texts[path.stem] = stream.read()
    return texts
