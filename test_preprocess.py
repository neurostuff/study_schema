"""What `review/preprocess.py` must not get wrong.

Two classes of check, and the second is the one that matters. The first is that each
transform does what it says -- zones classified, sections dropped, sentences split. The
second is the invariant every arm of the preprocessing experiment rests on: a *text*
strategy adds nothing, a *digest* strategy removes nothing, and neither ever changes the
file `build_record.py` resolves offsets against. If a transform could invent a sentence,
a value read out of the reduced prompt would have no warrant in the paper, and every
number in docs/text-preprocessing-experiments.md would be measuring something else.

The gold-derived expectations are all from `xevP8UDRAVh9`, the one human-verified record:
its two `Region`s are `frontal lobe` and `temporal lobe`, from a Methods sentence naming
both with one head noun, and its two text-only VBM analyses come from a single Results
sentence that reports a null result.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "review"))

sys.path.insert(0, str(REPO))

import preprocess  # noqa: E402

#: A miniature paper with one of every zone, so the zone assertions do not depend on a
#: corpus file being present.
PAPER = """Title of the paper

An abstract sentence about gray matter (GM) volume in 14 patients.

## Introduction

Earlier work showed decreased GM in the prefrontal cortex ( 14 ).

## Materials and Methods

### Study sample

Fourteen (eight male, six female; mean age 40.7 +/- 6.8 years) non-left-handed patients
were recruited. Each patient was scanned twice.

### Image acquisition

Scanning used a 3T MRI scanner (Magnetom Verio, Siemens), with a repetition time of 2000
ms and an echo time of 3.4 ms. Images were smoothed with an 8 mm FWHM kernel using SPM8.
We used an explicit mask of the frontal and temporal lobe by WFU PickAtlas.

## Results

### Voxel-based morphometry analyses

Comparison of the heroin and placebo conditions found no significant difference in either
direction.

### Correlation analyses

There was a significant positive correlation between perfusion and GM volume (Table 1).

Table 1 - Correlation between gray matter and perfusion.

| Area             | MNI coordinates | Pearson r |
|------------------|-----------------|-----------|
| Precentral gyrus | 60, 16, 40      | 0.91      |

## Discussion

We found that perfusion correlated positively with GM ( 41 ).

## Conflict of Interest Statement

The authors declare no conflict.
"""


@pytest.fixture(scope="module")
def zones() -> dict[str, str]:
    return {section.heading: section.zone for section in preprocess.split_sections(PAPER)}


# ------------------------------------------------------------------------------- zones

def test_every_imrad_zone_is_found(zones):
    assert zones["Introduction"] == "intro"
    assert zones["Materials and Methods"] == "methods"
    assert zones["Results"] == "results"
    assert zones["Discussion"] == "discussion"
    assert zones["Conflict of Interest Statement"] == "back"


def test_front_matter_becomes_its_own_section():
    first = preprocess.split_sections(PAPER)[0]
    assert first.zone == "front" and "abstract sentence" in first.body


def test_a_weak_heading_inherits_its_parent_rather_than_its_own_keyword(zones):
    """"Voxel-based morphometry analyses" is a Results subsection.

    A flat keyword match puts it in Methods on the word "analyses", and `sections` then
    drops the only place two of gold's six analyses are reported.
    """

    assert zones["Voxel-based morphometry analyses"] == "results"
    assert zones["Correlation analyses"] == "results"
    assert zones["Image acquisition"] == "methods"


def test_a_structured_abstract_label_is_front_matter_and_not_its_own_zone():
    labelled = "Title\n\n## Background:\n\nx\n\n## Results:\n\ny\n\n## Introduction\n\nz\n"
    zones = {s.heading: s.zone for s in preprocess.split_sections(labelled)}
    assert zones["Background:"] == "front" and zones["Results:"] == "front"
    assert zones["Introduction"] == "intro"


# -------------------------------------------------------------------------- invariants

@pytest.mark.parametrize("name", sorted(preprocess.STRATEGIES))
def test_a_text_strategy_only_removes_and_reorders(name):
    """No transform may put a sentence in the prompt that the paper does not contain.

    Checked as a multiset of prose sentences: `reorder` moves them, `sections` and
    `retrieval` drop some, and none of the three may add one. A strategy that could add a
    sentence would let a value be read from text no reviewer can find in the paper.
    """

    strategy = preprocess.STRATEGIES[name]
    if strategy.kind == "digest":
        pytest.skip("digest strategies do not touch the text")
    original = set(preprocess.sentences(PAPER))
    produced = preprocess.sentences(strategy.apply(PAPER, "demands").text)
    # The two strings a transform is allowed to add, and the only two: `retrieval`'s
    # omission marker and `reorder`'s note that the order changed. Both are named
    # constants in the module, so a third addition fails this test rather than being
    # whitelisted here after the fact.
    allowed = set(preprocess.sentences(preprocess.REORDER_NOTE))
    cleaned = {re.sub(re.escape(preprocess.OMISSION), " ", s).strip() for s in produced}
    invented = {s for s in cleaned - original - allowed if s}
    assert not invented, f"{name} invented: {invented}"


@pytest.mark.parametrize("name", sorted(preprocess.STRATEGIES))
def test_a_digest_strategy_leaves_the_paper_alone(name):
    strategy = preprocess.STRATEGIES[name]
    if strategy.kind != "digest":
        pytest.skip("not a pure digest strategy")
    assert strategy.apply(PAPER, "demands").text == PAPER


@pytest.mark.parametrize("name", sorted(preprocess.STRATEGIES))
def test_the_tables_survive_every_strategy(name):
    """A coordinate exists in the table and nowhere else.

    `sections`, `retrieval` and `combo` all drop text, and a reduction that took the
    pipe rows with it would cap analysis recall at zero however good the prompt was.
    """

    produced = preprocess.apply_strategy(name, PAPER, "demands").text
    assert "60, 16, 40" in produced and "Precentral gyrus" in produced


def test_a_digest_is_labelled_as_a_candidate_list():
    """The caution is what stops a regex false positive becoming a record value."""

    digest = preprocess.apply_strategy("methods", PAPER, "satisfy").digest
    assert "confirm every entry against the paper" in digest


def test_the_null_result_sentence_reaches_the_contrast_digest():
    """Gold's two text-only VBM analyses come from this one sentence.

    It is reported in prose and in no coordinate table, so the stage-1 table parse
    cannot see it -- which is the ceiling the `contrasts` arm exists to test.
    """

    rows = preprocess.contrast_candidates(PAPER)
    nulls = [sentence for sentence, groups, _ in rows if "null" in groups]
    assert any("no significant difference in either direction" in s for s in nulls)


# ----------------------------------------------------------------------- the extractors

def test_a_shared_head_noun_becomes_two_regions():
    """"the frontal and temporal lobe" is gold's two Regions, not one."""

    delimited, _ = preprocess.region_mentions(PAPER)
    lowered = {name.lower() for name in delimited}
    assert "frontal lobe" in lowered and "temporal lobe" in lowered


def test_a_result_table_label_is_not_offered_as_a_delimited_region():
    delimited, reported = preprocess.region_mentions(PAPER)
    assert "precentral gyrus" in {name.lower() for name in reported}
    assert "precentral gyrus" not in {name.lower() for name in delimited}


def test_an_abbreviation_does_not_run_back_over_a_sentence_boundary():
    text = "We saw acute effects. Arterial spin labeling (ASL) was used."
    assert dict((s, l) for s, l, _ in preprocess.abbreviations(text)) == {
        "ASL": "Arterial spin labeling"}


def test_the_shortest_satisfying_long_form_wins():
    text = "This was measured by Biological Parametric Mapping (BPM) in every subject."
    assert ("BPM", "Biological Parametric Mapping") in [
        (s, l) for s, l, _ in preprocess.abbreviations(text)]


def test_a_two_letter_acronym_survives_but_a_two_letter_word_does_not():
    """`GM` is load-bearing in this corpus and was being filtered out with `In` and `we`."""

    found = {s for s, _, _ in preprocess.abbreviations(PAPER)}
    assert "GM" in found


def test_a_citation_run_is_not_read_as_a_coordinate():
    """pubget renders a reference list as "( 14 , 15 , 34 )", which is not a location."""

    text = "GM was reduced in heroin dependence ( 14 , 15 , 34 ).\n"
    kinds = [kinds for _, kinds in preprocess.statistic_sentences(text)]
    assert not any("coordinate" in k for k in kinds)


def test_a_coordinate_with_a_cue_is_read_as_one():
    text = "The peak was located at MNI coordinates 60, 16, 40 in this analysis.\n"
    kinds = [kinds for _, kinds in preprocess.statistic_sentences(text)]
    assert any("coordinate" in k for k in kinds)


def test_a_p_value_threshold_does_not_become_a_cluster_extent():
    """"extent threshold of p < 0.05" is not a 0-voxel cluster."""

    text = "## Methods\n\nAn extent threshold of p < 0.05 was applied.\n"
    assert "cluster_extent" not in preprocess.method_parameters(text)


def _values(found: dict, label: str) -> list[str]:
    return found.get(label, ("", []))[1]


def test_a_tool_name_that_is_also_a_word_needs_its_capitals():
    lower = "## Methods\n\nThe first step was realignment.\n"
    upper = "## Methods\n\nSubcortical volumes came from FIRST.\n"
    assert "FIRST" not in " ".join(_values(preprocess.method_parameters(lower), "software"))
    assert "FIRST" in _values(preprocess.method_parameters(upper), "software")


def test_method_parameters_are_labelled_with_extraction_field_names():
    found = preprocess.method_parameters(PAPER)
    assert _values(found, "field strength") == ["3T"]
    assert found["field strength"][0] == "MRI.magnetic_field_strength_tesla"
    assert {"Siemens", "Magnetom"} <= set(_values(found, "scanner"))
    assert _values(found, "repetition time") == ["repetition time of 2000 ms"]
    assert "8 mm FWHM" in _values(found, "smoothing")


def test_a_methods_number_quoted_in_the_discussion_is_out_of_scope():
    """Zone-scoped, so a protocol described in the Discussion is not this study's."""

    text = ("## Methods\n\nScanning used a 3T scanner.\n\n"
            "## Discussion\n\nAn earlier study used a 7T scanner.\n")
    assert _values(preprocess.method_parameters(text), "field strength") == ["3T"]


def test_the_cohort_digest_finds_the_sample_the_sex_split_and_the_occasions():
    found = preprocess.cohort_parameters(PAPER)
    # The whole phrase, not a fragment: a decimal point in the demographics parenthetical
    # used to stop the match dead and report "8 years) ... patients" as the sample.
    assert any(v.startswith("Fourteen") and v.endswith("patients")
               for v in _values(found, "count phrase"))
    assert {"eight male", "six female"} <= set(_values(found, "sex"))
    assert any("scanned twice" in v for v in _values(found, "timepoint"))


def test_retrieval_keeps_the_front_matter_whole():
    """The abstract states the design in two hundred words; scoring it could only lose."""

    reduced = preprocess.bm25_select(PAPER, preprocess.RETRIEVAL_QUERY, budget=0.1)
    assert "An abstract sentence about gray matter (GM) volume in 14 patients." in reduced


def test_retrieval_marks_where_it_cut():
    reduced = preprocess.bm25_select(PAPER, preprocess.RETRIEVAL_QUERY, budget=0.2)
    assert preprocess.OMISSION in reduced, \
        "a reduced section must not read as a complete one"


def test_sections_drops_the_argument_and_keeps_the_evidence():
    reduced = preprocess.apply_strategy("sections", PAPER, "demands").text
    assert "Earlier work showed decreased GM" not in reduced
    assert "found no significant difference in either" in reduced
    assert "3T MRI scanner" in reduced


def test_combo_routes_a_different_digest_to_each_pass():
    """The entity side gets the Methods and cohort blocks, the analysis side the results.

    Sending all six blocks to both passes would double the added context to serve one.
    """

    analyses = preprocess.apply_strategy("combo", PAPER, "demands").digest
    entities = preprocess.apply_strategy("combo", PAPER, "satisfy").digest
    assert "Candidate tested comparisons" in analyses
    assert "Candidate tested comparisons" not in entities
    assert "Method parameters" in entities
    assert "Method parameters" not in analyses
    assert "Abbreviations defined in this paper" in analyses + entities


def test_an_unknown_strategy_is_an_error_and_not_a_silent_pass_through():
    """A typo in a sweep flag would otherwise be reported as the control arm's result."""

    with pytest.raises(KeyError):
        preprocess.apply_strategy("setcions", PAPER, "demands")


def test_none_is_the_identity():
    assert preprocess.apply_strategy("none", PAPER, "demands").text == PAPER
    assert preprocess.apply_strategy("none", PAPER, "demands").digest == ""


# ------------------------------------------------- the digest's slot names must be real

def _named_slots() -> set[tuple[str, str]]:
    """(class, field) for every schema slot the two labelled digests name."""

    pairs = set()
    for _, slots, _ in [*preprocess._METHOD_PATTERNS, *preprocess._COHORT_PATTERNS]:
        for token in re.findall(r"\b([A-Z]\w+)\.(\w+)", slots):
            pairs.add(token)
        # `Group.age_mean, .age_standard_deviation` -- the bare continuations belong to
        # the last class named before them.
        current = None
        for token in re.findall(r"\b[A-Z]\w+\.\w+|(?<![\w.])\.\w+", slots):
            if token.startswith("."):
                if current:
                    pairs.add((current, token[1:]))
            else:
                current = token.split(".")[0]
    return pairs


def test_every_slot_the_digest_names_exists_in_the_schema():
    """A digest that labels a value with a field name the schema does not have is worse
    than one that labels nothing.

    The model takes the label as the destination, emits it, and `validate_record.py`
    rejects the whole entity with "attribute is not declared". The label has to be the
    real slot or it has to be absent, and this is what keeps it real as the schema moves.
    """

    import schema_utils

    classes = schema_utils.load_imported_classes(REPO / "neuroimaging-study-extraction.yaml")
    unknown = []
    for owner, field in sorted(_named_slots()):
        if owner not in classes:
            unknown.append(f"{owner} (no such class)")
        elif field not in schema_utils.attributes_for(classes, owner):
            unknown.append(f"{owner}.{field}")
    assert not unknown, "digest names slots the schema does not have: " + ", ".join(unknown)


def test_a_value_with_no_slot_is_marked_as_having_none():
    """Inversion time has no field. Listing it unlabelled invites an invented one."""

    digest = preprocess.method_block(PAPER)
    assert "No extraction field holds the values below" in digest or \
        "inversion time" not in digest


def test_every_digest_preamble_is_a_named_literal():
    """A block's static text must be a constant in `PROMPT_LITERALS`, not an inline string.

    `tests/test_prompt_leakage.py` enumerates that tuple to check no gold phrase reaches
    the prompt. An inline literal inside a function is invisible to it, so a worked
    example written into a new digest heading would leak exactly the way the one in
    `recheck_cells.py` did -- and score for it.
    """

    source = (REPO / "review" / "preprocess.py").read_text(encoding="utf-8")
    calls = re.findall(r"return _block\((.*?)\)\n", source, re.DOTALL)
    assert len(calls) == 6, f"expected six digest blocks, found {len(calls)}"
    # A separator like "\n\n" is not prompt text. A literal containing a letter is.
    inline = [call for call in calls
              if re.search(r"[\"'](?:[^\"'\\]|\\.)*[A-Za-z]{2,}(?:[^\"'\\]|\\.)*[\"']", call)]
    assert not inline, ("a digest passes an inline string to _block; move it to a constant "
                        "and add it to PROMPT_LITERALS: " + "; ".join(inline))
    for literal in preprocess.PROMPT_LITERALS:
        assert literal.strip(), "PROMPT_LITERALS holds an empty string"
