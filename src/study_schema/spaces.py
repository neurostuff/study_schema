"""Coordinate spaces, normalized one way for everyone who reads or stores one.

NiMARE transforms between spaces only when a point's space is exactly ``"MNI"`` or
``"TAL"``; any other spelling goes into a meta-analysis untransformed. So every space a
paper, a parser or a model states is folded to ``MNI``, ``TAL`` or ``OTHER`` (a stated
space that is neither), or to None when no space is stated. None is never defaulted to
MNI. These are `ReportedSpace`'s values, and the values neurostore stores on a point.

    normalize_space("Talairach & Tournoux 1988")  -> "TAL"
    normalize_space("MNI152 2mm")                 -> "MNI"
    normalize_space("mni2tal")                    -> None   (names both)
    normalize_space("n.a.")                       -> None   (states nothing)
    normalize_space("Paxinos and Watson")         -> "OTHER"

Ingestion, pondie and neurostore import this rather than keep their own alias tables.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

__all__ = ["MNI", "OTHER", "SPACES", "TAL", "Space", "normalize_space"]

MNI = "MNI"
TAL = "TAL"
OTHER = "OTHER"
SPACES = (MNI, TAL, OTHER)
Space = Literal["MNI", "TAL", "OTHER"]

#: The alias table: each family's spellings, first match wins. The digit forms (mni2tal,
#: tal2mni, icbm2tal) match both MNI and TAL, which `normalize_space` reads as undecidable.
RULES = (
    (
        MNI,
        re.compile(
            r"\bmni|\bnmi\b|\bicbm|2(?:mni|icbm)|montreal\s+neurolog|"
            r"international\s+consortium\s+for\s+brain\s+mapping|\bcolin\s*27",
            re.IGNORECASE,
        ),
    ),
    (
        TAL,
        re.compile(
            r"\btal\b|t[ao]l[ai]+r[ai]+ch|tournoux|\bt\s*&\s*t\b|"
            r"\btal88\b|\btt(?:88)?\b|\bt88\b|tlrc|\btt_n27|2tal\b|\btal2",
            re.IGNORECASE,
        ),
    ),
    # Anything unmatched is OTHER too; these are the stated non-MNI/TAL spaces seen in
    # the corpus, listed so that one of them alongside an MNI word still reads as MNI.
    (
        OTHER,
        re.compile(
            r"^\s*other\s*$|\bsurface\b|\bfsaverage|\bfsLR\b|\bnative\b|"
            r"\bdartel\b|\bsuit\b|\bfmrib58|custom(?:i[sz]ed)?\b|in-house|"
            r"\w+[\s-]specific\b|\bspm\s?\d+\b",
            re.IGNORECASE,
        ),
    ),
)

#: Strings that say no space was stated; they normalize to None, like a blank.
NOT_STATED = re.compile(
    r"^\s*(?:unknown(?:\s+space)?|not\s+(?:reported|stated|specified|applicable|available)|"
    r"n\.?\s*/?\s*a\.?|none(?:\s+reported)?|missing|null|unspecified|[\W_]+)\s*$",
    re.IGNORECASE,
)


def normalize_space(value: object) -> Optional[Space]:
    """Fold a stated space to ``"MNI"``, ``"TAL"``, ``"OTHER"`` or None.

    - Missing or blank input returns None.
    - "unknown", "not reported", "n.a.", "?" and the like return None.
    - A spelling of MNI or TAL ("MNI152 2mm", "Talairach & Tournoux 1988")
      returns that space.
    - A string naming both ("MNI converted to Talairach", "mni2tal", "tal2mni")
      returns None: which space the numbers are in is not decidable.
    - Anything else returns ``"OTHER"``.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or NOT_STATED.match(text):
        return None
    hits = [space for space, pattern in RULES if pattern.search(text)]
    if MNI in hits and TAL in hits:
        return None
    return hits[0] if hits else OTHER
