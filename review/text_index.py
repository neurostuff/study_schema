"""Normalize paper text and build the deterministic PaperSection index.

Evidence offsets in an extraction record are relative to the normalized source
text identified by ExtractionMetadata.source_text_hash, using the half-open
interval convention [start_char, end_char). Everything that produces or consumes
those offsets must agree on the normalization performed here.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# pubget/ace text renders section headings as markdown ATX headings. Markdown
# depth 2 ("## Introduction") is a top-level paper section, which the schema
# calls level 1; level 0 is reserved for the document title, which the text
# files do not carry.
_HEADING = re.compile(r"^(#+)[ \t]+(.*?)[ \t]*$", re.MULTILINE)
_MARKDOWN_DEPTH_OFFSET = 1

def _headings(normalized: str) -> list[tuple[int, int, int, str]]:
    """`(start, end, level, title)` for every heading, in document order.

    One spelling, because the text is served as the markdown it already is. A second
    spelling lived here while `build_text.py` restyled headings as a title over a
    rule, for a pane that renders plain text; the transform is gone, and with it the
    heuristic that had to decide whether a row of dashes under a line of prose was a
    heading or a table.
    """

    return [
        (
            match.start(),
            match.end(),
            len(match.group(1)) - _MARKDOWN_DEPTH_OFFSET,
            " ".join(match.group(2).split()),
        )
        for match in _HEADING.finditer(normalized)
    ]


@dataclass(frozen=True)
class Section:
    """One entry of ExtractionMetadata.paper_sections."""

    ordinal: int
    title: str
    level: int
    parent_section: str | None
    start_char: int
    end_char: int

    def as_record(self) -> dict[str, object]:
        record: dict[str, object] = {
            "ordinal": self.ordinal,
            "title": self.title,
            "level": self.level,
            "start_char": self.start_char,
            "end_char": self.end_char,
        }
        if self.parent_section is not None:
            record["parent_section"] = self.parent_section
        return record


def normalize(raw: str) -> str:
    """Return the canonical source text that offsets are measured against.

    Only newline endings are canonicalized. Nothing else is touched: stripping
    or collapsing whitespace here would shift every downstream offset, and the
    Label Studio editor counts characters exactly as they appear, so the text we
    hash must be the text we serve.
    """

    return raw.replace("\r\n", "\n").replace("\r", "\n")


def text_hash(normalized: str) -> str:
    """Return the SHA-256 used as ExtractionMetadata.source_text_hash."""

    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_sections(normalized: str) -> list[Section]:
    """Index every markdown heading in the normalized text.

    A section runs from its heading to the next heading of the same or higher
    rank, or to end of file, matching the schema's description of end_char as
    the offset "immediately before the next same-or-higher-level heading".
    """

    matches = _headings(normalized)
    sections: list[Section] = []
    open_ancestors: list[Section] = []

    for ordinal, (match_start, match_end, level, title) in enumerate(matches):
        if not title:
            continue

        while open_ancestors and open_ancestors[-1].level >= level:
            open_ancestors.pop()

        section = Section(
            ordinal=ordinal,
            title=title,
            level=level,
            parent_section=open_ancestors[-1].title if open_ancestors else None,
            start_char=match_start,
            end_char=_section_end(normalized, matches, ordinal, level),
        )
        sections.append(section)
        open_ancestors.append(section)

    return sections


def _section_end(
    normalized: str,
    matches: list[tuple[int, int, int, str]],
    ordinal: int,
    level: int,
) -> int:
    for start, _end, following_level, _title in matches[ordinal + 1 :]:
        if following_level <= level:
            return start
    return len(normalized)


def section_path(sections: list[Section], offset: int) -> str | None:
    """Return a breadcrumb such as "Methods > Participants" for a character offset.

    EvidenceSpan records no section reference, so the only way to tell a
    reviewer where a span came from is to locate the offset in this index. The
    deepest containing section wins.
    """

    containing = [s for s in sections if s.start_char <= offset < s.end_char]
    if not containing:
        return None
    deepest = max(containing, key=lambda s: s.level)
    trail = [s.title for s in containing if s.level <= deepest.level]
    return " > ".join(trail)


def load(path: Path) -> tuple[str, str, list[Section]]:
    """Return (normalized_text, sha256, sections) for a text file on disk."""

    normalized = normalize(path.read_text(encoding="utf-8"))
    return normalized, text_hash(normalized), build_sections(normalized)
