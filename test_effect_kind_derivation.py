"""Pin the derived-kind rule against §5's worked models, and measure where it declines.

`representing-models.md` §3 states the derivation in six ordered steps, `extraction-readme.md` §3
restates it, and `schema-tutorial.md` §9.6 teaches it -- three prose statements of one rule that
nothing executed. `EffectKind` is bound by no slot on purpose: the kind follows from the cells, so
storing it would put one fact in two places. That makes a test the only thing that can hold the
rule to account. The implementation is `review/derive_effect.py`; this pins it against every worked
model in §5 and measures the rate of the two cases where it declines to answer.

The two non-answers are not failures and must not be collapsed into one:

- `UNDETERMINED_VARIATION` -- a cell sits on a continuous term whose `variation_level` is unset or
  is free text, so step 2 cannot choose between a modulation and a regression. `analysis.yaml`
  already says this is "undetermined for that record rather than wrong". Since the slot is
  optional, this also fires whenever an extractor skipped it, which is why the rate matters.
- `NO_LABEL` -- the cell pattern matches no step. `EffectKind`'s own description reads "a pattern
  yielding none is a record whose cells do not describe a test", so this is a defect signal about
  the record rather than a gap in the rule.

Run as a script for the corpus rates; run under pytest for the fixtures.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "review"))

from derive_effect import (  # noqa: E402
    NO_LABEL,
    UNDETERMINED_VARIATION,
    _val,
    derive_effect_kind,
    terms_in_scope,
)


# --------------------------------------------------------------------------------------
# Fixtures: every worked model in representing-models.md §5, by its section number.
# --------------------------------------------------------------------------------------

def _term(local_id: str, type_: str, **kwargs: Any) -> dict[str, Any]:
    return {"local_id": local_id, "type": type_, **kwargs}


def _cell(term: str, direction: Any, level: Any = None) -> dict[str, Any]:
    return {"term": term, "level": level, "direction": direction}


_CONDITION = _term("term-condition", "categorical", variation_level="within_subject")
_MOTION = _term("term-motion", "continuous", variation_level="within_subject")

WORKED_MODELS: list[tuple[str, list[dict], dict[str, Mapping], str]] = [
    (
        "5.1 simple contrast",
        [_cell("term-condition", "positive", "emotion labeling"),
         _cell("term-condition", "negative", "emotion matching")],
        {"term-condition": _CONDITION, "term-motion": _MOTION},
        "contrast",
    ),
    (
        "5.2 against the implicit baseline",
        [_cell("term-condition", "positive", "emotion labeling")],
        {"term-condition": _CONDITION, "term-motion": _MOTION},
        "simple_effect",
    ),
    (
        "5.3 brain-behaviour correlation",
        [_cell("term-perceived-stress", "positive")],
        {"term-perceived-stress": _term(
            "term-perceived-stress", "continuous", variation_level="between_subject")},
        "cross_subject_regression",
    ),
    (
        "5.4 moderation -- continuous crossed with a cohort factor",
        [_cell("term-age-x-group", "positive")],
        {"term-age-x-group": _term(
            "term-age-x-group", "continuous",
            interaction_with=["term-age", "term-group"])},
        "interaction",
    ),
    (
        "5.5 main effect of group (F)",
        [_cell("term-group", "undirected", "RA patients"),
         _cell("term-group", "undirected", "healthy controls")],
        {"term-group": _term("term-group", "categorical",
                             variation_level="between_subject")},
        "omnibus",
    ),
    (
        "5.5 group x task F-test",
        [_cell("term-group", "undirected", "RA patients"),
         _cell("term-group", "undirected", "healthy controls"),
         _cell("term-task", "undirected", "rotation"),
         _cell("term-task", "undirected", "comparison")],
        {"term-group": _term("term-group", "categorical",
                             variation_level="between_subject"),
         "term-task": _term("term-task", "categorical",
                            variation_level="within_subject")},
        "omnibus",
    ),
    (
        "5.5 rotation > comparison, within RA (simple effect)",
        [_cell("term-task", "positive", "rotation"),
         _cell("term-task", "negative", "comparison"),
         _cell("term-group", "held", "RA patients")],
        {"term-group": _term("term-group", "categorical",
                             variation_level="between_subject"),
         "term-task": _term("term-task", "categorical",
                            variation_level="within_subject")},
        "contrast",
    ),
    (
        "5.6 pre-post change",
        [_cell("term-vbm-time", "positive", "after practice"),
         _cell("term-vbm-time", "negative", "before practice")],
        {"term-vbm-time": _term("term-vbm-time", "categorical",
                                variation_level="within_subject"),
         "term-vbm-group": _term("term-vbm-group", "categorical",
                                 variation_level="between_subject")},
        "contrast",
    ),
    (
        "5.7 ordered factor at its extremes",
        [_cell("term-condition", "positive", "2-back"),
         _cell("term-condition", "negative", "0-back")],
        {"term-condition": _CONDITION},
        "contrast",
    ),
    (
        "5.8 omnibus F over a three-level factor",
        [_cell("term-condition", "undirected", "0-back"),
         _cell("term-condition", "undirected", "1-back"),
         _cell("term-condition", "undirected", "2-back")],
        {"term-condition": _CONDITION},
        "omnibus",
    ),
    (
        "5.9 decoding above chance",
        [_cell("term-task-mvpa", "positive", "vowel imagery")],
        {"term-task-mvpa": _term("term-task-mvpa", "categorical",
                                 variation_level="within_subject")},
        "simple_effect",
    ),
    (
        "5.10 double dissociation between regions",
        [_cell("term-seed", "positive", "posterior right dlPFC"),
         _cell("term-seed", "negative", "anterior right dlPFC"),
         _cell("term-group", "positive", "healthy controls"),
         _cell("term-group", "negative", "Parkinson's disease patients")],
        {"term-seed": _term("term-seed", "categorical",
                            variation_level="within_subject"),
         "term-group": _term("term-group", "categorical",
                             variation_level="between_subject")},
        "interaction",
    ),
    (
        "5.11 mediated path",
        [_cell("term-age-med", "positive")],
        {"term-age-med": _term("term-age-med", "continuous",
                               variation_level="between_subject"),
         "term-gmd": _term("term-gmd", "continuous",
                           variation_level="between_subject")},
        "cross_subject_regression",
    ),
    (
        "5.12 two-stage, group contrast of a seed map",
        [_cell("term-diagnosis", "positive", "HCs"),
         _cell("term-diagnosis", "negative", "ET patients")],
        {"term-diagnosis": _term("term-diagnosis", "categorical",
                                 variation_level="between_subject"),
         "term-vim-timecourse": _term("term-vim-timecourse", "continuous",
                                      variation_level="within_subject"),
         "term-trs": _term("term-trs", "continuous",
                           variation_level="between_subject")},
        "contrast",
    ),
]

# §3 steps 5 and 6 are the two ways to have no signs, and §4 turns on their not being the same
# result. These pin the pair, plus the two non-answers.
EDGE_CASES: list[tuple[str, list[dict], dict[str, Mapping], str]] = [
    (
        "step 6: a two-level comparison whose direction the paper withheld",
        [_cell("term-condition", None, "2-back"),
         _cell("term-condition", None, "0-back")],
        {"term-condition": _CONDITION},
        "contrast",
    ),
    (
        "step 6: withheld direction on two crossed factors",
        [_cell("term-group", None, "patients"), _cell("term-group", None, "controls"),
         _cell("term-task", None, "hard"), _cell("term-task", None, "easy")],
        {"term-group": _term("term-group", "categorical"),
         "term-task": _term("term-task", "categorical")},
        "interaction",
    ),
    (
        "undetermined: continuous term with no variation_level",
        [_cell("term-score", "positive")],
        {"term-score": _term("term-score", "continuous")},
        UNDETERMINED_VARIATION,
    ),
    (
        "undetermined: continuous term whose variation_level is free text",
        [_cell("term-score", "positive")],
        {"term-score": _term("term-score", "continuous",
                             variation_level="across scanning sessions")},
        UNDETERMINED_VARIATION,
    ),
    (
        "no label: a held cell and nothing else",
        [_cell("term-group", "held", "patients")],
        {"term-group": _term("term-group", "categorical")},
        NO_LABEL,
    ),
    (
        "no label: an effect that compared nothing",
        [],
        {},
        NO_LABEL,
    ),
    (
        "wrapped values: extraction records go through the same path",
        [{"term": "term-condition",
          "level": {"extraction_status": "extracted", "value": "faces"},
          "direction": {"extraction_status": "extracted", "value": "positive"}},
         {"term": "term-condition",
          "level": {"extraction_status": "extracted", "value": "houses"},
          "direction": {"extraction_status": "extracted", "value": "negative"}}],
        {"term-condition": {"local_id": "term-condition",
                            "type": {"extraction_status": "extracted",
                                     "value": "categorical"},
                            "variation_level": {"extraction_status": "extracted",
                                                "value": "within_subject"}}},
        "contrast",
    ),
    (
        "wrapped values: a not_reported direction is a withheld sign, not an absent cell",
        [{"term": "term-condition",
          "level": {"extraction_status": "extracted", "value": "faces"},
          "direction": {"extraction_status": "not_reported"}},
         {"term": "term-condition",
          "level": {"extraction_status": "extracted", "value": "houses"},
          "direction": {"extraction_status": "not_reported"}}],
        {"term-condition": {"local_id": "term-condition",
                            "type": {"extraction_status": "extracted",
                                     "value": "categorical"}}},
        "contrast",
    ),
]


def test_worked_models_derive_their_stated_kind() -> None:
    for label, cells, terms, expected in WORKED_MODELS:
        kind, why = derive_effect_kind(cells, terms)
        assert kind == expected, f"{label}: expected {expected}, got {kind} ({why})"


def test_edge_cases_including_the_two_non_answers() -> None:
    for label, cells, terms, expected in EDGE_CASES:
        kind, why = derive_effect_kind(cells, terms)
        assert kind == expected, f"{label}: expected {expected}, got {kind} ({why})"


def test_step_five_and_step_six_are_not_the_same_result() -> None:
    """§4: an F over a factor and a withheld sign differ in what a better source would fix."""

    terms = {"term-condition": _CONDITION}
    f_test = [_cell("term-condition", "undirected", "0-back"),
              _cell("term-condition", "undirected", "1-back")]
    withheld = [_cell("term-condition", None, "0-back"),
                _cell("term-condition", None, "1-back")]
    assert derive_effect_kind(f_test, terms)[0] == "omnibus"
    assert derive_effect_kind(withheld, terms)[0] == "contrast"


def test_a_term_signed_once_is_not_crossed() -> None:
    """§3 step 3: 'crossed' rather than 'signed' is the load-bearing word.

    A cohort comparison of an activation map signs the condition term once and crosses the
    cohort term, so it is a contrast. Read as 'signed', it would derive an interaction.
    """

    terms = {"term-group": _term("term-group", "categorical",
                                 variation_level="between_subject"),
             "term-condition": _CONDITION}
    cells = [_cell("term-condition", "positive", "faces"),
             _cell("term-group", "positive", "patients"),
             _cell("term-group", "negative", "controls")]
    kind, _why = derive_effect_kind(cells, terms)
    assert kind == "contrast"


# --------------------------------------------------------------------------------------
# Corpus scan
# --------------------------------------------------------------------------------------

CORPORA = {
    "data/records": "../data/records",
    "data/records-demand-driven": "../data/records-demand-driven",
    "data/gold": "../data/gold",
    "review/examples": "review/examples",
}


def scan_corpus(root: Path) -> tuple[Counter, list[tuple[str, str, str]]]:
    """Derive the kind of every analysis under `root`. Returns counts and the non-answers."""

    counts: Counter = Counter()
    flagged: list[tuple[str, str, str]] = []
    if not root.is_dir():
        return counts, flagged

    for path in sorted(root.glob("*.extraction.json")):
        if path.name.endswith(".raw.json"):
            continue
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            counts["unreadable"] += 1
            flagged.append((path.name, "unreadable", str(exc)))
            continue

        models = {
            model["local_id"]: model
            for model in record.get("model_estimations") or []
            if isinstance(model, Mapping) and isinstance(model.get("local_id"), str)
        }
        for analysis in record.get("analyses") or []:
            if not isinstance(analysis, Mapping):
                continue
            terms = terms_in_scope(analysis.get("model_estimation"), models)
            effect = analysis.get("effect")
            cells = (effect.get("cells") if isinstance(effect, Mapping) else None) or []
            kind, why = derive_effect_kind(cells, terms)
            counts[kind] += 1
            if kind in {UNDETERMINED_VARIATION, NO_LABEL}:
                name = _val(analysis.get("name")) or analysis.get("local_id") or "?"
                flagged.append((f"{path.name}::{name}", kind, why))
    return counts, flagged


def main() -> int:
    here = Path(__file__).resolve().parent
    total: Counter = Counter()
    print("Derived EffectKind across the extraction corpora\n")
    for label, relative in CORPORA.items():
        counts, flagged = scan_corpus((here / relative).resolve())
        analyses = sum(counts.values())
        if not analyses:
            print(f"{label}: no records found")
            continue
        total.update(counts)
        print(f"{label}: {analyses} analyses")
        for kind, n in counts.most_common():
            print(f"    {kind:34s} {n:4d}  ({n / analyses:5.1%})")
        for name, kind, why in flagged:
            print(f"      ! {kind}: {name}")
            print(f"        {why}")
        print()

    analyses = sum(total.values())
    if analyses:
        print(f"ALL CORPORA: {analyses} analyses")
        for kind, n in total.most_common():
            print(f"    {kind:34s} {n:4d}  ({n / analyses:5.1%})")
        undetermined = total[UNDETERMINED_VARIATION] + total[NO_LABEL]
        print(f"\n  derivation declines to answer: {undetermined}/{analyses} "
              f"({undetermined / analyses:.1%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
