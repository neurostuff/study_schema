"""Resolve verbatim quotes emitted by an extractor into EvidenceSpan offsets.

An LLM cannot count characters reliably, so it is asked for verbatim quotes and
this module locates them in the normalized source text. The offsets are computed
here, deterministically, which is what lets the integrity gate assert
normalized[start_char:end_char] == span.text for every span.

EvidenceSpan.text is always set to the document substring rather than to the
quote the model produced, so a whitespace-tolerant match can never introduce a
span whose text disagrees with the source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Characters that publishers and models substitute for each other freely; a model
# routinely straightens curly quotes and dashes when echoing a quote. Written as
# explicit codepoints because several of these are visually indistinguishable.
#
# Every mapping must be single-character to single-character: folding has to
# preserve string length, or the offsets it produces would not address the
# original document. Unicode NFC/NFD normalization is deliberately NOT applied
# for the same reason -- composing "e" + U+0301 into U+00E9 would shorten the
# text and shift every following offset.
_EQUIVALENT = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ",
    "​": " ", "﻿": " ",
}


class SpanResolutionError(ValueError):
    """Raised when a quote cannot be located unambiguously in the source text."""


@dataclass(frozen=True)
class ResolvedSpan:
    start_char: int
    end_char: int
    text: str
    exact: bool

    def as_record(self) -> dict[str, object]:
        return {"text": self.text, "start_char": self.start_char, "end_char": self.end_char}


def fold(value: str) -> str:
    """Length-preserving character folding. len(fold(v)) == len(v) always."""

    return value.translate(str.maketrans(_EQUIVALENT))


def fold_label(value: str) -> str:
    """Fold a label for joining, where length does not have to be preserved.

    `Cell.level` joins to `FactorLevel.level` on the string, and extraction-readme.md §3
    invariant 3 asks that the comparison use the same normalization the mapper applies. That
    is this: `fold`, then collapse whitespace runs, then casefold. Deliberately narrow --
    only differences that cannot be semantic. `Healthy controls` and `healthy controls` are
    the same level; `AD` and `AD group` are not, and calling them equal here would hide the
    join failure rather than report it.

    Not `fold`, and it must not be used where an offset survives the call: collapsing
    whitespace changes the length, which is the one thing `fold` promises never to do.
    """

    return re.sub(r"\s+", " ", fold(value)).strip().casefold()


def _tolerant_pattern(quote: str) -> re.Pattern[str]:
    """Build a regex matching the quote with any whitespace run between tokens."""

    tokens = [re.escape(token) for token in fold(quote).split()]
    if not tokens:
        raise SpanResolutionError("quote is empty")
    return re.compile(r"\s+".join(tokens))


def resolve(
    normalized: str,
    quote: str,
    *,
    near: int | None = None,
    folded_text: str | None = None,
) -> ResolvedSpan:
    """Locate one quote in the normalized text.

    near biases selection when a quote occurs more than once; pass the start of
    the enclosing section to disambiguate a phrase that repeats across the paper.
    """

    if not quote or not quote.strip():
        raise SpanResolutionError("quote is empty")

    exact = [match.start() for match in re.finditer(re.escape(quote), normalized)]
    if exact:
        start = _pick(exact, near)
        return ResolvedSpan(start, start + len(quote), normalized[start : start + len(quote)], True)

    haystack = folded_text if folded_text is not None else fold(normalized)
    matches = list(_tolerant_pattern(quote).finditer(haystack))
    if not matches:
        raise SpanResolutionError(f"quote not found in source text: {quote[:80]!r}")

    starts = [match.start() for match in matches]
    chosen = matches[starts.index(_pick(starts, near))]
    return ResolvedSpan(
        chosen.start(), chosen.end(), normalized[chosen.start() : chosen.end()], False
    )


def _pick(starts: list[int], near: int | None) -> int:
    if near is None:
        return starts[0]
    return min(starts, key=lambda start: abs(start - near))


def resolve_all(
    normalized: str, quotes: list[str], *, near: int | None = None
) -> tuple[list[ResolvedSpan], list[str]]:
    """Resolve many quotes against one document, reporting failures rather than raising."""

    folded_text = fold(normalized)
    resolved: list[ResolvedSpan] = []
    failures: list[str] = []
    for quote in quotes:
        try:
            resolved.append(resolve(normalized, quote, near=near, folded_text=folded_text))
        except SpanResolutionError as error:
            failures.append(str(error))
    return resolved, failures


def verify(normalized: str, span: dict[str, object]) -> None:
    """Assert the schema invariant for one serialized EvidenceSpan."""

    start, end, text = span["start_char"], span["end_char"], span["text"]
    if not isinstance(start, int) or not isinstance(end, int):
        raise SpanResolutionError(f"offsets must be integers: {span!r}")
    if not 0 <= start < end <= len(normalized):
        raise SpanResolutionError(f"offsets outside document: {start}-{end}")
    actual = normalized[start:end]
    if actual != text:
        raise SpanResolutionError(
            f"span text disagrees with source at {start}-{end}: {text!r} != {actual!r}"
        )
