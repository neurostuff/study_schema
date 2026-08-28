"""What `review/evidence_retrieval.py` must not get wrong.

Every case here is a failure that actually happened, taken from the seventy hand-judged
retrievals in docs/evidence-top1-judgements.md. The module exists only to prevent them,
so a test that stops failing means the fix was undone, not that the case got easier.

The section priors are load-bearing in one direction only: they must never turn a
section into a hard filter. 61% of reviewer evidence is in Methods, but the other 39%
is not, and a filter would make those unreachable rather than merely lower-ranked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "review"))

import evidence_retrieval as er  # noqa: E402


# --- unit expansion ---------------------------------------------------------

def test_sub_second_time_is_offered_in_milliseconds():
    # The extracted value was 0.015 and the paper says "TE = 15 ms". Neither exact
    # match nor a lexical reranker reaches that sentence without this.
    assert "15 ms" in er.value_variants("acquisitions.echo_time_seconds", "0.015")


def test_whole_second_time_leads_with_seconds():
    # "TR = 2 s" is what a paper prints; 2000 ms is offered too but must not lead,
    # because build_query only takes the head of the list.
    variants = er.value_variants("acquisitions.repetition_time_seconds", "2")
    assert variants[0] == "2 s"
    assert "2000 ms" in variants


def test_minutes_are_offered_only_for_durations():
    # A 15 ms echo time is not "0.00025 min"; offering that wastes a query slot.
    assert not any("min" in v for v in
                   er.value_variants("acquisitions.echo_time_seconds", "0.015"))
    assert any("min" in v for v in
               er.value_variants("acquisitions.acquisition_duration_seconds", "360"))


@pytest.mark.parametrize("field,value,expected", [
    ("acquisitions.magnetic_field_strength_tesla", "3", "3 T"),
    ("preprocessings.smoothing_fwhm_mm", "6", "6 mm"),
    ("acquisitions.flip_angle_degrees", "90", "90°"),
])
def test_unit_suffix_drives_the_surface_form(field, value, expected):
    assert expected in er.value_variants(field, value)


def test_non_numeric_values_pass_through_untouched():
    assert er.value_variants("groups.name", "typical development children") == \
        ["typical development children"]


# --- aliases ----------------------------------------------------------------

def test_query_carries_the_papers_word_not_the_schemas():
    # The schema says `repetition_time_seconds`; no paper does.
    query = er.build_query("acquisitions.repetition_time_seconds", "2")
    assert "TR" in query


def test_count_fields_reach_a_bare_n_column():
    # groups.acquired_count missed a table row reading "For N: TD is 44" that the
    # identically-sourced analyses.groups.n retrieved, purely on field wording.
    assert "N" in er.build_query("groups.acquired_count", "44").split()


def test_the_entity_stays_out_of_the_query():
    # Concatenating it in scored below the entity-free baseline on exactly the
    # instances where sibling entities share a value -- 14.9% against 21.7%. The
    # entity is a separate signal, scored by entity_hits, not a query term.
    assert "typical" not in er.build_query("groups.age_mean", "12.46")


def test_entity_is_found_by_name_and_by_acronym():
    units = ["Typical development children were recruited locally.",
             "TD showed a longer dwell time than the patients.",
             "Scanning used a 3 T magnet."]
    assert er.entity_hits(units, "typical development children") == [0, 1]


def test_an_entity_named_everywhere_is_not_a_locator():
    units = ["The ASD group did a thing."] * 5
    assert er.entity_hits(units, "ASD group") == []


def test_acronym_match_respects_word_boundaries():
    # "TD" must not fire on "STDs" or "trend".
    assert er.entity_hits(["Rates of STDs were unrelated.", "TD scored higher.",
                           "A trend emerged.", "Nothing here.", "Nor here.",
                           "Nor here either."], "typical development") == [1]


# --- literal match ----------------------------------------------------------

def test_exact_name_beats_a_reranker_that_returned_a_grant_number():
    units = ["2013DFA11140, to BH).",
             "The criteria of included subjects are: male scores of full intelligence "
             "quotient (FIQ, estimated by the fourth subtests of the Wechsler "
             "Abbreviated Scale of Intelligence, WASI-IV) above 85.",
             "Data were obtained from an open access dataset."]
    hits = er.literal_hits(units, ["Wechsler Abbreviated Scale of Intelligence, WASI-IV"])
    assert hits == [1]


def test_a_common_literal_is_left_to_the_reranker():
    # A value present everywhere locates nothing, so the bonus must not fire.
    units = [f"The group had {i} of them and also 2 more." for i in range(10)]
    assert er.literal_hits(units, ["2"]) == []


def test_longer_variants_are_tried_first():
    # "WASI-IV" locates; the "4" inside it does not.
    units = ["Scores on 4 scales were collected.", "We used the WASI-IV.",
             "4 patients withdrew.", "4 sites contributed.", "4 runs were acquired."]
    assert er.literal_hits(units, ["4", "WASI-IV"]) == [1]


def test_unicode_dashes_do_not_break_an_exact_match():
    assert er.literal_hits(["The contrast was delay − immediate."],
                           ["delay - immediate"]) == [0]


# --- sections ---------------------------------------------------------------

SAMPLE = """# A study

We did a thing.

## Introduction

DTI is a non-invasive method that maps the diffusivity of water molecules.

## Materials and methods

### Participants

Twenty patients were recruited.

### MRI data acquisition

MRIs were acquired with TR = 2 s.

## Results

### Imaging results

Volume was reduced in patients.

## Discussion

Our findings suggest a mechanism.

## Acknowledgements

Grant 2013DFA11140, to BH.
"""


def test_sections_cover_the_whole_text():
    spans = er.sectionize(SAMPLE)
    assert spans[0][0] == 0
    assert spans[-1][1] == len(SAMPLE)
    for (_, end, _), (start, _, _) in zip(spans, spans[1:]):
        assert end == start


@pytest.mark.parametrize("needle,label", [
    ("DTI is a non-invasive", "intro"),
    ("Twenty patients were recruited", "methods"),
    ("MRIs were acquired", "methods"),
    ("Volume was reduced", "results"),
    ("Our findings suggest", "discussion"),
    ("Grant 2013DFA11140", "back"),
])
def test_offsets_land_in_the_right_section(needle, label):
    spans = er.sectionize(SAMPLE)
    assert er.section_of(spans, SAMPLE.index(needle)) == label


def test_an_unrecognised_subsection_inherits_its_parent():
    # "### Imaging results" is under Results and must not reset the section; the
    # opposite bug reads a Results subsection as Methods.
    spans = er.sectionize(SAMPLE)
    assert er.section_of(spans, SAMPLE.index("Volume was reduced")) == "results"


def test_a_paper_with_no_headings_is_still_fully_searchable():
    spans = er.sectionize("One sentence. Another sentence.")
    assert spans == [(0, 31, "unknown")]
    assert er.section_prior("groups.name", "unknown") == 0.0


def test_numbered_headings_are_recognised():
    assert er.classify_heading("2.3. Statistical analysis") == "methods"
    assert er.classify_heading("3 RESULTS") == "results"


# --- section priors ---------------------------------------------------------

def test_leaf_name_does_not_leak_across_paths():
    # Bare `description` is 66% abstract; `design.description` is 88% methods. A
    # leaf-name fallback gives the second the first's ranking, which is inverted.
    assert er.section_prior("description", "abstract") > \
        er.section_prior("description", "methods")
    assert er.section_prior("design.description", "methods") > \
        er.section_prior("design.description", "abstract")


def test_unlisted_fields_default_to_methods():
    assert er.section_prior("preprocessings.smoothing_fwhm_mm", "methods") == \
        max(er.SECTION_BONUS)


def test_discussion_is_demoted_but_not_excluded():
    # It holds 0.7% of spans and restates findings in the extractor's own words, so it
    # wins on term overlap while supporting nothing -- but those 0.7% must stay reachable.
    assert er.section_prior("groups.name", "discussion") < 0
    assert er.section_prior("groups.name", "discussion") > float("-inf")


def test_the_section_prior_cannot_outweigh_a_strong_match():
    # The prior is a tiebreak. A sentence that genuinely matches must still be able to
    # win from the wrong section, or the 39% of evidence outside Methods is lost.
    worst = min(er.SECTION_PENALTY.values())
    assert max(er.SECTION_BONUS) - worst < er.LITERAL_BONUS


# --- units ------------------------------------------------------------------

def test_a_table_row_is_scored_as_a_sentence_but_quoted_as_a_row():
    # The reranker scores `| TD | 44 |` as noise and "For TD: N is 44" as a claim, but
    # build_record resolves a quote by exact match, so the quote must be the raw line.
    text = ("Methods\n\n| Group | N | Age |\n| --- | --- | --- |\n| TD | 44 | 12.4 |\n\n"
            "The scan was short.")
    units = er.sentence_units(text)
    rows = [u for u in units if u.text.count("|") >= 3]
    assert rows, "no table row was extracted"
    row = rows[0]
    assert "N is 44" in row.rendered
    assert text[row.start:row.end] == row.text
    assert row.text.count("|") >= 3


def test_every_unit_resolves_at_its_own_offsets():
    text = ("## Materials and methods\n\nTwenty patients were recruited. "
            "MRIs used TR = 2 s.\n\n## Results\n\nVolume was reduced.")
    for unit in er.sentence_units(text):
        assert text[unit.start:unit.end] == unit.text


def test_units_carry_their_section():
    text = ("## Introduction\n\nDTI maps diffusivity of water.\n\n"
            "## Materials and methods\n\nTwenty patients were recruited here.")
    sections = {u.section for u in er.sentence_units(text)}
    assert "methods" in sections and "intro" in sections


def test_a_missing_reranker_is_not_an_error():
    # The union is an enhancement; a missing optional dependency must not take the
    # evidence stage down with it.
    assert er.locate(None, [], "groups.name", "controls") is None
