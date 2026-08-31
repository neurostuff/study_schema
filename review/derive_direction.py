"""Read a contrast's polarity off its own name, and give each cell its direction.

The statistic cannot do this. 76% of reviewed analyses carry a statistic with an
unambiguous sign, but the sign is the same either way round: a table of "FESZ > NC" and
a table of "NC > FESZ" both print positive t-values, and in the gold two analyses with
identical `sign=+1` assign opposite directions to the same two groups. Sign is enough to
split a mixed-sign table -- which is what `parse_tables.split_opposite_signs` uses it for
-- and not enough to direct a cell.

The polarity is written in the contrast's name: `FESZ>NC`, `AD < HC reduced GM volume`,
`greater activation in patients than controls`. That is a regex, and this is it.

Levels are matched to the sides of the comparison by word-set containment and never by a
similarity ratio: `men` is a substring of `women`, and `synchronous` scores 0.96 against
`asynchronous`.
"""

from __future__ import annotations

import json
import re

#: Ordered: the first pattern that matches wins, so the explicit operators are tried
#: before the prose forms that could also match inside them.
_COMPARISONS: tuple[tuple[re.Pattern[str], int], ...] = (
    # The sides stop at a sentence or clause boundary. Without that the right-hand side
    # runs to the end of the definition and swallows a group name held constant across
    # the contrast -- "7d > 28d . ALFF differences in the CCD group" matched CCD to the
    # right side and directed a `held` cell.
    (re.compile(r"(?P<a>[^<>.,;]+?)\s*>\s*(?P<b>[^<>.,;]+)"), +1),
    (re.compile(r"(?P<a>[^<>.,;]+?)\s*<\s*(?P<b>[^<>.,;]+)"), -1),
    (re.compile(r"(?P<a>[^.,;]+?)\s+(?:greater|higher|larger|stronger|increased|more)\s+"
                r"than\s+(?P<b>[^.,;]+)", re.I), +1),
    (re.compile(r"(?P<a>[^.,;]+?)\s+(?:less|lower|smaller|weaker|decreased|reduced)\s+"
                r"than\s+(?P<b>[^.,;]+)", re.I), -1),
    (re.compile(r"(?:greater|higher|increased|stronger|more)\s+(?:\w+\s+){0,4}?in\s+"
                r"(?P<a>[^.,;]+?)\s+(?:compared (?:with|to)|relative to|versus|vs\.?|than)"
                r"\s+(?P<b>[^.,;]+)", re.I), +1),
    (re.compile(r"(?:lower|reduced|decreased|weaker|less)\s+(?:\w+\s+){0,4}?in\s+"
                r"(?P<a>[^.,;]+?)\s+(?:compared (?:with|to)|relative to|versus|vs\.?|than)"
                r"\s+(?P<b>[^.,;]+)", re.I), -1),
)

#: Dropped when comparing, so `ASD` matches `ASD group`. Never used to decide whether a
#: side of a comparison exists: a contrast named "patients than controls" is made
#: entirely of these words, and treating it as empty loses the commonest phrasing there
#: is.
_STOP = frozenset({"the", "a", "an", "of", "in", "for", "and", "group", "groups",
                   "patients", "subjects", "participants", "children", "adults"})


def _tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _words(text: str) -> frozenset[str]:
    """The content words, or every word when the phrase is nothing but stopwords."""
    tokens = _tokens(text)
    return (tokens - _STOP) or tokens


def same_level(a: str, b: str) -> bool:
    """Do these two strings name the same level?

    Word-set containment, never a graded ratio. The failure this prevents is real: a
    0.85 similarity threshold matches `men` to `women` and `synchronous` to
    `asynchronous`, and a cell given the wrong level is a wrong direction.

    Compared on content words when both sides have them, so `ASD` reaches `ASD group`,
    and on every word when one side is built only from stopwords, so `patients` does not
    silently match everything.
    """

    left_all, right_all = _tokens(a), _tokens(b)
    if not left_all or not right_all:
        return False
    left, right = left_all - _STOP, right_all - _STOP
    if not left or not right:
        left, right = left_all, right_all
    return left <= right or right <= left


def polarity(text: str) -> tuple[str, str, int] | None:
    """(left side, right side, +1 or -1) for a contrast named as a comparison."""

    for pattern, sign in _COMPARISONS:
        match = pattern.search(text or "")
        if not match:
            continue
        left, right = match.group("a").strip(" .,:;"), match.group("b").strip(" .,:;")
        if _words(left) and _words(right):
            return left, right, sign
    return None


def direction_of(level: str, contrast: str) -> str | None:
    """`positive`, `negative`, or None when the contrast does not name this level.

    None is the common answer and must stay distinguishable from a direction: a cell the
    name does not mention is one the model still has to be asked about.
    """

    read = polarity(contrast)
    if read is None:
        return None
    left, right, sign = read
    on_left, on_right = same_level(level, left), same_level(level, right)
    if on_left == on_right:
        # Named on both sides or neither -- no answer, rather than a coin flip.
        return None
    if on_left:
        return "positive" if sign > 0 else "negative"
    return "negative" if sign > 0 else "positive"


#: Directions that survive a reversal unchanged. `undirected` has no sign to flip;
#: `held` marks a level the contrast holds constant, which is true from either side.
_FIXED = frozenset({"undirected", "held", "absent"})
_OPPOSITE = {"positive": "negative", "negative": "positive"}


def reverse(direction: str) -> str:
    return _OPPOSITE.get(direction, direction) if direction not in _FIXED else direction


#: Statistics with no sign to reverse. A p-value is positive in either reading of a
#: contrast, and flipping it would make a number the paper never printed.
_UNSIGNED_STATISTICS = frozenset({"p", "p-value", "pvalue", "cluster_size", "voxels", "k"})


def mirror_analysis(described: dict, withheld: dict, parse_key: str = "") -> dict:
    """Rebuild the half of a sign-split contrast the paper never describes.

    `parse_tables.split_opposite_signs` partitions a mixed-sign table and hands the
    extraction pass only the positive half, because that is the half the paper's prose is
    about: "FESZ > NC" prints positive statistics for the effects it names. The negative
    rows are the same contrast read the other way, and asking a model to name and define
    a contrast with no prose behind it produces invention rather than extraction.

    So the reversed analysis is arithmetic, not extraction: the described half's cells
    with their directions flipped, addressing the withheld half's own row group.
    """

    mirrored = json.loads(json.dumps(described))
    mirrored["local_id"] = f"{described.get('local_id', 'analysis')}-reversed"
    mirrored["mirror_of"] = described.get("local_id")

    # The withheld entry's name, not the described half's. Copying the name wholesale left
    # an analysis called "FESZ > NC" whose cells say NC > FESZ -- a name contradicting its
    # own content, colliding with the real "FESZ > NC" on the same table. Every one of the
    # 36 mirrors in the schizophrenia corpus carried a colliding name, and it is what made
    # the direction bench score a correct extraction as a sign flip: two candidates share
    # a name and the matcher took the reversed one.
    #
    # The parse's label -- "<described name> (reversed)" -- is used rather than an inverted
    # operator because a described name is usually not a contrast expression at all: 46 of
    # 50 are labels like "GM Spatial Map" or "Seed: Right anterior cingulate cortex", which
    # have no operator to invert.
    reversed_name = (withheld or {}).get("name")
    if reversed_name:
        mirrored["name"] = {
            "extraction_status": "extracted", "value": str(reversed_name),
            "value_source": "generated",
            "evidence": {"status": "not_applicable"}}

    # The reversed half's coordinates are reached the way every other analysis reaches
    # its own -- by the parse key -- and not by carrying flipped rows inline. The schema
    # stores no coordinates, so an inline `points` list is an attribute no class declares:
    # the validator reported it on 21 of 299 records, all of them this function's output.
    # The key must be the WITHHELD entry's, because that is the row group holding the
    # rows this half is about.
    mirrored.pop("points", None)
    mirrored.pop("coordinates", None)
    if parse_key:
        # A wrapper and not a bare string: the slot is `model_extracted`, so it projects
        # into the extraction schema as an ExtractedString. `generated` because the key is
        # the parse's, and `not_applicable` because no sentence of the paper warrants a
        # reversal it never describes.
        mirrored["source_table_analysis"] = {
            "extraction_status": "extracted", "value": parse_key,
            "value_source": "generated",
            "evidence": {"status": "not_applicable"}}
    else:
        mirrored.pop("source_table_analysis", None)

    effect = mirrored.get("effect") or {}
    for cell in effect.get("cells") or []:
        node = cell.get("direction")
        if isinstance(node, dict) and isinstance(node.get("value"), str):
            flipped = reverse(node["value"])
            if flipped != node["value"]:
                node["value"] = flipped
                # Only a flipped direction is generated. A `held` level is held from
                # either side of the contrast and keeps the warrant it was read from.
                node["value_source"] = "generated"
        elif isinstance(node, str):
            cell["direction"] = reverse(node)
    return mirrored
