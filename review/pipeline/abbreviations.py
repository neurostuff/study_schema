"""Expand a paper's abbreviations before asking a vocabulary about its words.

ONVOC spells things out. Papers do not: a record says `ADOS`, `dlPFC`, `MADRS`, and no
amount of string matching bridges four letters to four words. The bridge exists, though,
and it is usually in the paper -- almost every abbreviation is defined on first use, in
the one form that makes it recoverable:

    the Autism Diagnostic Observation Schedule (ADOS)

Finding those is Schwartz & Hearst's algorithm (PSB 2003), and scispacy's
`AbbreviationDetector` is the implementation to use. A hand-written one is kept below as
a fallback for hosts without scispacy, and it is a fallback rather than the primary for a
demonstrated reason: on four definitions it missed `MADRS`, whose letters are plainly
there in `Montgomery Asberg Depression Rating Scale`. Thirty lines of character walking
looks simple and is not.

scispacy needs no trained model for this -- a blank English pipeline with a sentencizer is
enough -- so the dependency is spacy, not a 100MB download.

What the paper does not define is covered by a curated file. Both are written to one
referenceable JSON so an expansion can be looked up, argued with, and corrected in one
place -- an abbreviation resolved differently in two records is a normalization bug that
is invisible unless the mapping is a file.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

STORE = Path(__file__).resolve().parents[3] / "data" / "vocab" / "abbreviations.json"

#: `long form (SF)` -- the short form parenthesised, the long form immediately before.
#: Bounded at 3..8 characters: shorter is usually a unit and longer is usually a phrase,
#: and both produce expansions that are not abbreviations at all.
_CANDIDATE = re.compile(r"\(([A-Za-z][A-Za-z0-9./-]{1,7})\)")
_MIN_SHORT, _MAX_SHORT = 2, 8


def _words_before(text: str, at: int, count: int) -> str:
    """The `count` words ending at `at`, which is where the long form must be."""
    before = text[:at].rstrip()
    words = re.findall(r"[^\s]+", before)
    return " ".join(words[-count:]) if words else ""


def _matches(short: str, long: str) -> str | None:
    """The shortest suffix of `long` that `short` abbreviates, or None.

    Schwartz & Hearst, walked from the ends: every character of the short form must be
    found in the long form in reverse order, and the short form's first character must
    align with the start of a word. That last condition is what keeps `rat` from
    abbreviating `separate`.
    """

    short_folded = short.lower().replace(".", "").replace("-", "")
    long_folded = long.lower()
    if not short_folded:
        return None

    s = len(short_folded) - 1
    l = len(long_folded) - 1
    while s >= 0:
        char = short_folded[s]
        if not char.isalnum():
            s -= 1
            continue
        while l >= 0 and long_folded[l] != char:
            l -= 1
        # The first character has the extra duty of starting a word.
        if l < 0 or (s == 0 and l > 0 and long_folded[l - 1].isalnum()):
            return None
        s -= 1
        l -= 1
    start = l + 1
    while start > 0 and long_folded[start - 1].isalnum():
        start -= 1
    found = long[start:].strip()
    # A long form far longer than its abbreviation is usually a sentence that happens to
    # contain the letters.
    return found if len(found.split()) <= min(len(short_folded) + 2, 8) else None


_DETECTOR = None
_DETECTOR_TRIED = False


def detector():
    """scispacy's abbreviation pipeline, or None if it is not installed.

    Built once and reused: the pipeline is cheap to run and not cheap to construct, and a
    corpus pass would otherwise rebuild it per paper.
    """

    global _DETECTOR, _DETECTOR_TRIED
    if _DETECTOR_TRIED:
        return _DETECTOR
    _DETECTOR_TRIED = True
    try:
        import spacy  # noqa: PLC0415
        from scispacy.abbreviation import AbbreviationDetector  # noqa: F401, PLC0415

        nlp = spacy.blank("en")
        nlp.add_pipe("sentencizer")
        nlp.add_pipe("abbreviation_detector")
        _DETECTOR = nlp
    except Exception:  # noqa: BLE001 -- absence is expected, not exceptional
        _DETECTOR = None
    return _DETECTOR


def mine(text: str) -> dict[str, str]:
    """Every `long form (SF)` definition this text makes, short form -> long form."""
    nlp = detector()
    if nlp is not None:
        found = {}
        # spacy's parser is quadratic in some pathological documents; a paper is well
        # under this and a corpus dump is not.
        for chunk in (text[i:i + 200_000] for i in range(0, len(text), 200_000)):
            for abbreviation in nlp(chunk)._.abbreviations:
                short = str(abbreviation).strip()
                long = " ".join(str(abbreviation._.long_form).split())
                if short and long and long.lower() != short.lower():
                    found.setdefault(short, long)
        return found
    return mine_builtin(text)


def mine_builtin(text: str) -> dict[str, str]:
    """The fallback. Correct on the common shape and demonstrably not on all of them."""
    found: dict[str, str] = {}
    for match in _CANDIDATE.finditer(text):
        short = match.group(1)
        if not (_MIN_SHORT <= len(short.replace(".", "")) <= _MAX_SHORT):
            continue
        if short.isdigit() or not any(c.isupper() for c in short):
            continue
        window = _words_before(text, match.start(), min(len(short) + 5, 12))
        long = _matches(short, window)
        if long and long.lower() != short.lower():
            found.setdefault(short, long)
    return found


@dataclass
class Abbreviations:
    """Short form -> expansion, with where each expansion came from.

    Provenance is kept because the two sources are not equally strong. A definition the
    paper itself makes is a fact about that paper; a curated entry is a claim about the
    field, and the two should not be indistinguishable when one of them turns out wrong.
    """

    #: short form (folded) -> {"expansion": ..., "source": "mined"|"curated",
    #:                         "papers": [...], "count": n, "by_paper": {paper: expansion}}
    #:
    #: `by_paper` is what makes a polysemous short form usable. `AD` is axial diffusivity in
    #: a DTI paper and Alzheimer's disease in a dementia one, and a corpus-wide store that
    #: keeps one winner is wrong for whichever paper lost. Measured over 848 records: 21.4%
    #: of short forms are expanded differently by different papers, while only 0.1% are
    #: expanded two ways inside ONE paper -- and none of those are real polysemy. So the
    #: paper is the unit at which an abbreviation is unambiguous, and resolving against the
    #: paper that used it needs no disambiguation model.
    entries: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Abbreviations":
        path = path or STORE
        if not path.is_file():
            return cls()
        return cls(json.loads(path.read_text(encoding="utf-8")).get("entries") or {})

    def save(self, path: Path | None = None) -> None:
        path = path or STORE
        path.parent.mkdir(parents=True, exist_ok=True)
        ordered = dict(sorted(self.entries.items()))
        path.write_text(json.dumps({
            "about": "Abbreviation expansions used before vocabulary lookup. "
                     "`mined` entries were defined by a paper in `long form (SF)` shape; "
                     "`curated` entries were added by hand for abbreviations papers use "
                     "without defining.",
            "entries": ordered}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    def key(self, short: str) -> str:
        return short.lower().replace(".", "").replace("-", "").strip()

    @staticmethod
    def plausible(expansion: str) -> bool:
        """Is this the expansion of an abbreviation, or something else in brackets?

        A detector looking for `long form (SF)` also finds `(Philips Medical Systems,
        Best, The Netherlands)`, whose letters happen to fit. Manufacturer and address
        strings are the common false positive and they share a shape: several commas, or
        a trailing place name.
        """
        if expansion.count(",") >= 2 or "”" in expansion or '"' in expansion:
            return False
        return 1 <= len(expansion.split()) <= 12

    def canonical(self, short: str) -> str | None:
        """The expansion to use when a short form has been seen written several ways.

        Orthographic variants are the common case -- `echo planar imaging`,
        `echo-planar imaging`, `echoplanar imaging` -- and picking between them is not a
        judgement. The most-seen wins, and the longest breaks a tie, because a longer
        expansion gives a vocabulary more to match on.
        """
        slot = self.entries.get(self.key(short))
        if not slot:
            return None
        variants = slot.get("variants") or [slot["expansion"]]
        counts = Counter(variants)
        return max(variants, key=lambda v: (counts[v], len(v)))

    def add(self, short: str, expansion: str, source: str, paper: str = "") -> None:
        """Record an expansion. A curated entry is never overwritten by a mined one."""
        slot = self.entries.setdefault(
            self.key(short), {"expansion": expansion, "source": source,
                              "papers": [], "count": 0})
        if slot["source"] == "mined" and source == "curated":
            slot["expansion"], slot["source"] = expansion, "curated"
        slot["count"] += 1
        if paper and paper not in slot["papers"]:
            slot["papers"].append(paper)
        if paper and source == "mined":
            slot.setdefault("by_paper", {})[paper] = expansion

    def expand(self, short: str, paper: str = "") -> str | None:
        """The paper's own definition where it made one, the corpus consensus otherwise."""
        if paper:
            slot = self.entries.get(self.key(short))
            own = (slot or {}).get("by_paper", {}).get(paper)
            if own:
                return own
        return self.canonical(short)

    def disagreements(self) -> list[tuple[str, list[str]]]:
        """Short forms expanded to genuinely different things, not merely spelled apart.

        `echo planar imaging` and `echo-planar imaging` are one expansion written twice
        and are not a disagreement; `MDD` as a depressive disorder and as a dysregulation
        disorder is. Compared on content words so the first kind stops being reported and
        the second keeps being.
        """
        from .normalize import stems  # noqa: PLC0415

        out = []
        for short, slot in self.entries.items():
            variants = slot.get("variants") or []
            # Stems, not words: `Brodmann Area` and `Brodmann Areas` are one expansion,
            # and reporting them as a conflict buries the ones that are.
            distinct = {stems(v) for v in variants}
            if len(distinct) > 1:
                out.append((short, variants))
        return out

    def for_paper(self, text: str) -> "Abbreviations":
        """This store with the paper's own definitions taking precedence.

        Necessary, not a refinement. `FA` is fractional anisotropy in a diffusion paper
        and flip angle in an acquisition section; `AD` is axial diffusivity or Alzheimer's
        disease. A corpus-wide expansion picks whichever was commoner and is then wrong
        for every paper that meant the other. A paper that defines its own abbreviation
        has settled the question for itself, and that answer wins.
        """

        layered = Abbreviations({k: dict(v) for k, v in self.entries.items()})
        for short, long in mine(text).items():
            if self.plausible(long):
                layered.entries[layered.key(short)] = {
                    "expansion": long, "source": "paper", "papers": [], "count": 1}
        return layered

    def learn(self, text: str, paper: str = "") -> int:
        """Mine one paper's definitions into the store. Returns how many were new."""
        before = len(self.entries)
        for short, long in mine(text).items():
            if not self.plausible(long):
                continue
            existing = self.entries.get(self.key(short))
            if existing and existing["expansion"].lower() != long.lower():
                existing.setdefault("variants", [existing["expansion"]])
                if long not in existing["variants"]:
                    existing["variants"].append(long)
            self.add(short, long, "mined", paper)
        return len(self.entries) - before


def expansions_in(text: str, store: Abbreviations, paper: str = "") -> Iterator[tuple[str, str]]:
    """(short form, expansion) for every abbreviation this phrase uses."""
    for token in re.findall(r"[A-Za-z][A-Za-z0-9.-]{1,7}", str(text or "")):
        if not any(c.isupper() for c in token):
            continue
        expansion = store.expand(token, paper)
        if expansion:
            yield token, expansion
