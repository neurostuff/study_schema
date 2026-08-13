"""The foundation the review layer stands on: text, spans, records, tables.

Nothing here knows about Label Studio. What it checks is that an offset addresses
what it claims to, that a record is well formed against the schema, that a
coordinate table parses into rows and each row is attributed to one analysis, and
that the text a reviewer is shown is the text the corpus pipeline produced plus
the tables it omitted. `test_review_layer.py` checks what is built on top of this.

The example records under review/examples are built from CC-BY papers by
review/build_record.py, so these exercise the real pipeline rather than a fixture
invented for the tests.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
REVIEW = ROOT / "review"
sys.path.insert(0, str(REVIEW))

import build_record  # noqa: E402
import known_gaps  # noqa: E402
import spans as span_tools  # noqa: E402
import table_parse as tables  # noqa: E402
import text_index  # noqa: E402
import validate_record  # noqa: E402

import schema_utils  # noqa: E402

#: A paper whose text and record are both present -- *discovered*, not named.
#:
#: `review/texts` is gitignored bulk material (`review/sync_texts.py`), so which papers
#: a checkout has is a property of that checkout: a name pinned here is one sync away
#: from being wrong, and the skip then never lifts -- as it did not for twenty-five
#: tests. Any paper serves, since none of these asserts a fact about a particular study.
def _example_paper() -> str:
    for record in sorted((REVIEW / "examples").glob("*.extraction.json")):
        paper = record.name.removesuffix(".extraction.json")
        if (REVIEW / "texts" / paper / "processed" / "local" / "text.tables.txt").is_file():
            return paper
    return ""


PAPER = _example_paper()
TEXT = REVIEW / "texts" / PAPER / "processed" / "local" / "text.tables.txt"
RECORD = REVIEW / "examples" / f"{PAPER}.extraction.json"
PAYLOADS = REVIEW / "payloads" / PAPER
IDENTIFIERS = REVIEW / "texts" / PAPER / "identifiers.json"

requires_paper = pytest.mark.skipif(
    not PAPER,
    reason=(
        "no paper under review/examples has a synced text under review/texts; "
        "run review/sync_texts.py to populate one"
    ),
)


def _record_matches_schema() -> bool:
    """Whether the example record was extracted against the schema now in the tree.

    The extraction schema became a projection of the storage schema, which moved several
    things -- Study.terms became ModelTerm under ModelEstimation, arms and timepoints
    moved under Study.design, the per-method Analysis payloads collapsed into
    Analysis.details. The committed example predates all of it. Rather than migrate a
    record by hand and call the result extracted, the tests that read it skip until a
    fresh extraction run replaces it, and light up again on their own when one does.
    """

    if not RECORD.is_file():
        return False
    body = json.loads(RECORD.read_text(encoding="utf-8"))
    study = schema_utils.attributes_for(
        schema_utils.load_imported_classes(ROOT / "neuroimaging-study-extraction.yaml"),
        "Study",
    )
    return not (set(body) - set(study))


requires_current_record = pytest.mark.skipif(
    not _record_matches_schema(),
    reason=(
        f"review/examples/{PAPER}.extraction.json predates the projected extraction "
        "schema; re-extract the paper to re-enable"
        if PAPER else
        "no example paper to check against the projected extraction schema"
    ),
)


@pytest.fixture(scope="module")
def normalized() -> str:
    return text_index.normalize(TEXT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def record() -> dict:
    return json.loads(RECORD.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def classes() -> dict:
    return schema_utils.load_imported_classes(ROOT / "neuroimaging-study-extraction.yaml")


@pytest.fixture(scope="module")
def enums() -> dict:
    """The vocabularies, which `Validator` takes separately and defaults to empty.

    A validator built without them silently checks no vocabulary at all -- neither the
    closed ones it should reject on nor the open ones it should warn on -- so a test of
    either has to pass this.
    """

    return schema_utils.load_imported_classes(
        ROOT / "neuroimaging-study-extraction.yaml", key="enums")


# -- text_index ------------------------------------------------------------


def test_normalize_is_idempotent_and_folds_crlf() -> None:
    raw = "a\r\nb\rc\nd"
    once = text_index.normalize(raw)
    assert once == "a\nb\nc\nd"
    assert text_index.normalize(once) == once


def test_normalize_preserves_length_for_lf_only_text() -> None:
    raw = "already\nnormalized\ntext"
    assert len(text_index.normalize(raw)) == len(raw)


def test_sections_nest_and_stay_in_bounds() -> None:
    document = "## Methods\nbody\n### Participants\nmore\n## Results\ntail\n"
    sections = text_index.build_sections(document)

    assert [(s.title, s.level, s.parent_section) for s in sections] == [
        ("Methods", 1, None),
        ("Participants", 2, "Methods"),
        ("Results", 1, None),
    ]
    assert all(0 <= s.start_char < s.end_char <= len(document) for s in sections)
    # A section ends where the next same-or-higher-level heading begins.
    assert sections[0].end_char == sections[2].start_char
    assert sections[2].end_char == len(document)


def test_section_path_returns_deepest_breadcrumb() -> None:
    document = "## Methods\nbody\n### Participants\nmore text here\n"
    sections = text_index.build_sections(document)
    offset = document.index("more text here")
    assert text_index.section_path(sections, offset) == "Methods > Participants"


def test_text_hash_changes_with_content() -> None:
    assert text_index.text_hash("a") != text_index.text_hash("b")
    assert text_index.text_hash("a") == text_index.text_hash("a")


# -- spans -----------------------------------------------------------------


def test_fold_preserves_length() -> None:
    tricky = "don’t “quote” me — 4–5 units here"
    assert len(span_tools.fold(tricky)) == len(tricky)


def test_resolve_exact_quote() -> None:
    document = "The mean age was 32.4 years in total."
    found = span_tools.resolve(document, "mean age was 32.4 years")
    assert found.exact
    assert document[found.start_char : found.end_char] == found.text


def test_resolve_tolerates_whitespace_and_curly_punctuation() -> None:
    document = "Participants’ responses were\nrecorded reliably."
    found = span_tools.resolve(document, "Participants' responses were recorded")
    assert not found.exact
    # text always comes from the document, never from the model's quote
    assert found.text == document[found.start_char : found.end_char]
    assert "’" in found.text and "\n" in found.text


def test_resolve_prefers_occurrence_near_hint() -> None:
    document = "the effect" + " filler" * 20 + " the effect"
    second = document.rindex("the effect")
    found = span_tools.resolve(document, "the effect", near=second)
    assert found.start_char == second


def test_resolve_raises_for_absent_quote() -> None:
    with pytest.raises(span_tools.SpanResolutionError):
        span_tools.resolve("some document text", "a phrase that is not present")


def test_resolve_raises_for_empty_quote() -> None:
    with pytest.raises(span_tools.SpanResolutionError):
        span_tools.resolve("some document text", "   ")


def test_verify_rejects_shifted_offsets() -> None:
    document = "The mean age was 32.4 years."
    good = span_tools.resolve(document, "32.4 years").as_record()
    span_tools.verify(document, good)

    shifted = {**good, "start_char": good["start_char"] + 1}
    with pytest.raises(span_tools.SpanResolutionError):
        span_tools.verify(document, shifted)


def test_verify_rejects_out_of_range_offsets() -> None:
    with pytest.raises(span_tools.SpanResolutionError):
        span_tools.verify("short", {"text": "short", "start_char": 0, "end_char": 999})


# -- schema-driven slot classification ------------------------------------


def test_classify_slot_separates_references_from_pipeline_scalars(classes: dict) -> None:
    analysis = schema_utils.attributes_for(classes, "Analysis")
    metadata = schema_utils.attributes_for(classes, "ExtractionMetadata")
    analysis_group = schema_utils.attributes_for(classes, "AnalysisGroup")

    def kind(attrs: dict, name: str) -> str:
        return schema_utils.classify_slot(classes, name, attrs[name])

    # Both range on a class; only the inlined one is owned rather than pointed at.
    assert kind(analysis, "model_estimation") == "reference"
    assert kind(analysis, "effect") == "nested"

    assert kind(metadata, "extractor_model") == "native"
    assert kind(analysis, "local_id") == "identifier"
    assert kind(analysis, "name") == "evidence"

    # Sibling slots on one class differ in kind.
    assert kind(analysis_group, "group") == "reference"
    assert kind(analysis_group, "n") == "evidence"


def test_attributes_for_includes_is_a_ancestors_and_slot_usage(classes: dict) -> None:
    extracted_string = schema_utils.attributes_for(classes, "ExtractedString")
    # inherited from ExtractedValue
    assert "extraction_status" in extracted_string
    # narrowed by slot_usage from Any to string
    assert extracted_string["value"]["range"] == "string"
    assert schema_utils.attributes_for(classes, "ExtractedValue")["value"]["range"] == "Any"


def test_entity_lists_cover_every_study_entity_list(classes: dict) -> None:
    """The payload merge has to accept every entity list Study declares.

    A hardcoded list does not fail loudly when the schema grows: an unlisted key
    is reported as an "unexpected payload key" note and the entities are dropped
    from the record. `arms` and `timepoints` were lost that way, which cost every
    intervention and longitudinal paper its arms and occasions.
    """

    study = schema_utils.attributes_for(classes, "Study")
    declared = {name for name, attribute in study.items() if attribute.get("multivalued")}
    assert declared, "Study should declare multivalued entity lists"
    assert declared <= set(build_record._ENTITY_LISTS)
    # A list directly on Study maps to itself.
    assert all(build_record._ENTITY_LISTS[name] == name for name in declared)

    # A list one level down keeps its bare payload key and gains a dotted path, so an
    # extractor that emits arms.json does not have to know where the schema puts them.
    nested = schema_utils.attributes_for(classes, study["design"]["range"])
    for name in (n for n, a in nested.items() if a.get("multivalued")):
        assert build_record._ENTITY_LISTS[name] == f"design.{name}"


def test_merge_payloads_keeps_arms_and_timepoints(tmp_path: Path) -> None:
    """The two lists the hardcoded mapping missed, end to end through the merge.

    They now live under Study.design rather than on Study, so this also covers the
    payload key staying flat while the path it lands at does not.
    """

    (tmp_path / "trial.json").write_text(
        json.dumps(
            {
                "arms": [{"local_id": "active", "name": {"extraction_status": "extracted"}}],
                "timepoints": [{"local_id": "baseline", "name": {"extraction_status": "extracted"}}],
            }
        ),
        encoding="utf-8",
    )

    body = build_record.merge_payloads(tmp_path)

    assert [arm["local_id"] for arm in body["design"]["arms"]] == ["active"]
    assert [tp["local_id"] for tp in body["design"]["timepoints"]] == ["baseline"]


def test_resolves_to_follows_is_a(classes: dict) -> None:
    assert schema_utils.resolves_to(classes, "ExtractedInteger", "ExtractedValue")
    assert schema_utils.resolves_to(classes, "ExtractedValue", "ExtractedValue")
    assert not schema_utils.resolves_to(classes, "Group", "ExtractedValue")


# -- product columns and the crossings they record -------------------------

#: Fixtures rather than a real record, because the defect being checked is one a
#: real record wears invisibly: every reference resolves and every level agrees with
#: its term, so the only way to test it is to build the shape by hand.
def _text(value: str) -> dict:
    return {"extraction_status": "extracted", "value": value}


def _cell(term: str, direction: str, level: str | None = None) -> dict:
    cell = {"term": term, "direction": _text(direction)}
    if level is not None:
        cell["level"] = _text(level)
    return cell


GROUP = {"local_id": "t_group", "type": _text("categorical")}
STAGE = {"local_id": "t_stage", "type": _text("categorical")}
PRODUCT = {"local_id": "t_gxs", "type": _text("categorical"),
           "interaction_with": ["t_group", "t_stage"]}

#: The two group cells of an interaction reported as an unsigned chi-square: the test
#: yields no per-level sign, which crosses nothing.
UNSIGNED_GROUP = [_cell("t_group", "undirected", "patients"),
                  _cell("t_group", "undirected", "controls")]


def _record(terms: list[dict], analyses: list[tuple[str, list[dict]]],
            model: str = "m1") -> dict:
    return {
        "model_estimations": [{"local_id": model, "terms": terms}],
        "analyses": [
            {"local_id": f"a{index}", "name": _text(name), "definition": _text(name),
             "model_estimation": model, "effect": {"cells": cells}}
            for index, (name, cells) in enumerate(analyses)
        ],
    }


def _flags(record: dict, classes: dict) -> list[str]:
    validator = validate_record.Validator(classes, None)
    validator.check_crossings(record)
    validator.check_product_columns(record)
    validator.check_unsigned_cells(record)
    validator.check_occasion_factors(record)
    validator.check_arm_reachability(record)
    validator.check_derived_columns(record)
    assert validator.errors == []  # these checks route to review, never reject
    return validator.warnings


def test_reduplication_is_not_a_crossing() -> None:
    assert validate_record.names_a_crossing(_text("Group-by-stage interaction"))
    assert validate_record.names_a_crossing(_text("age × diagnosis"))
    assert validate_record.names_a_crossing(_text("the moderated slope"))
    assert not validate_record.names_a_crossing(_text("voxel-by-voxel comparison"))
    assert not validate_record.names_a_crossing(_text("main effect of group"))
    assert not validate_record.names_a_crossing(None, {"extraction_status": "not_reported"})


def test_interaction_without_a_product_column_is_flagged(classes: dict) -> None:
    """QQCjAAT6SwwQ's defect: an unsigned interaction test with nowhere to sit."""

    flags = _flags(_record([GROUP, STAGE],
                           [("Group-by-stage interaction", UNSIGNED_GROUP)]), classes)

    assert len(flags) == 1
    assert "interaction_with" in flags[0]
    assert "Study.analyses[0].effect.cells" in flags[0]


def test_an_unsigned_cell_on_the_product_column_satisfies_it(classes: dict) -> None:
    flags = _flags(_record([GROUP, STAGE, PRODUCT],
                           [("Group-by-stage interaction",
                             [_cell("t_gxs", "unstated")])]), classes)

    assert flags == []


def test_crossed_levels_need_no_product_column(classes: dict) -> None:
    """extraction-readme.md's converse: two crossed categorical factors say it themselves."""

    cells = [_cell("t_group", "positive", "patients"), _cell("t_group", "negative", "controls"),
             _cell("t_stage", "positive", "wake"), _cell("t_stage", "negative", "n3")]

    assert _flags(_record([GROUP, STAGE], [("Group-by-stage interaction", cells)]),
                  classes) == []


def test_a_simple_effect_within_one_level_is_not_flagged(classes: dict) -> None:
    """representing-models.md §5.5's last row, named after the interaction it came from."""

    cells = UNSIGNED_GROUP + [_cell("t_stage", "held", "wake")]

    assert _flags(_record([GROUP, STAGE],
                          [("Group-by-stage interaction at wake", cells)]), classes) == []


#: A factor whose levels are declared, which is what the held-constant reading is read
#: against: `held` says one of these sat on both sides and the rest were weighted out,
#: so a record celling all of them that way is claiming something else.
LOAD = {"local_id": "t_load", "type": _text("categorical"),
        "levels": [{"level": _text("high")}, {"level": _text("low")}]}


def test_a_levelless_cell_may_not_be_held(classes: dict) -> None:
    """§4's first corollary: a product column or a slope has no level, so it has nothing
    to put on both sides of the comparison. An undirected test of one is `undirected`."""

    flags = _flags(_record([GROUP, STAGE, PRODUCT],
                           [("Group-by-stage interaction",
                             [_cell("t_gxs", "held")])]), classes)

    assert len(flags) == 1
    assert "names no level" in flags[0]
    assert "undirected" in flags[0]


def test_a_factor_held_at_every_level_is_flagged(classes: dict) -> None:
    """An omnibus F miscoded. Celling every level `held` says the factor was held on
    both sides of its own test."""

    cells = [_cell("t_load", "held", "high"), _cell("t_load", "held", "low")]

    flags = _flags(_record([LOAD], [("Main effect of load", cells)]), classes)

    assert len(flags) == 1
    assert "every declared level" in flags[0]


def test_the_same_factor_undirected_at_every_level_is_not(classes: dict) -> None:
    """Which is the shape that replaced it, and the one §5.8 now writes down."""

    cells = [_cell("t_load", "undirected", "high"), _cell("t_load", "undirected", "low")]

    assert _flags(_record([LOAD], [("Main effect of load", cells)]), classes) == []


def test_a_held_level_leaves_the_others_absent_and_is_not_flagged(classes: dict) -> None:
    """The one shape `held` has: one level celled, the rest weighted out."""

    cells = [_cell("t_group", "positive", "patients"), _cell("t_group", "negative", "controls"),
             _cell("t_load", "held", "high")]

    assert _flags(_record([GROUP, LOAD], [("Group effect at high load", cells)]), classes) == []


def test_identical_cells_disagreeing_about_a_crossing_is_flagged(classes: dict) -> None:
    """The visible cost: an interaction and a main effect became the same record."""

    flags = _flags(
        _record([GROUP, STAGE, PRODUCT],
                [("Group-by-stage interaction", UNSIGNED_GROUP),
                 ("Group effect", list(UNSIGNED_GROUP))]),
        classes,
    )

    # All three fire, which is the raw QQCjAAT6SwwQ record in miniature: the cells
    # record no crossing, the two analyses are therefore indistinguishable, and the
    # column that would have separated them carries nothing.
    assert [flag for flag in flags if "identical to Study.analyses[1]" in flag]
    assert [flag for flag in flags if "interaction_with" in flag]
    assert [flag for flag in flags if "carries no cell" in flag]


def test_a_product_column_may_name_a_lower_stage_term(classes: dict) -> None:
    """A group stage crossing a cohort factor with a first-level condition."""

    record = {
        "model_estimations": [
            {"local_id": "first", "terms": [{"local_id": "t_cond", "type": _text("categorical")}]},
            {"local_id": "group", "inputs_from": ["first"],
             "terms": [GROUP, {"local_id": "t_x", "type": _text("categorical"),
                               "interaction_with": ["t_group", "t_cond"]}]},
        ],
        "analyses": [{"local_id": "a0", "name": _text("Group × condition"),
                      "definition": _text("Group × condition"), "model_estimation": "group",
                      "effect": {"cells": [_cell("t_x", "positive")]}}],
    }

    assert _flags(record, classes) == []


def test_a_component_in_a_sibling_model_is_flagged(classes: dict) -> None:
    """What check_local_ids cannot see: the reference resolves, to the wrong model."""

    record = {
        "model_estimations": [
            {"local_id": "other", "terms": [GROUP]},
            {"local_id": "mine", "terms": [
                {"local_id": "t_mi", "type": _text("continuous")},
                {"local_id": "t_x", "type": _text("continuous"),
                 "interaction_with": ["t_group", "t_mi"]},
            ]},
        ],
        "analyses": [{"local_id": "a0", "name": _text("Group × MI"),
                      "definition": _text("Group × MI"), "model_estimation": "mine",
                      "effect": {"cells": [_cell("t_x", "positive")]}}],
    }

    flags = _flags(record, classes)

    assert len(flags) == 1
    assert "'t_group' is not a term of 'mine'" in flags[0]


def test_a_product_column_no_cell_names_is_flagged(classes: dict) -> None:
    """A declared crossing whose analysis was never extracted."""

    flags = _flags(_record([GROUP, STAGE, PRODUCT],
                           [("Group effect", UNSIGNED_GROUP)]), classes)

    assert len(flags) == 1
    assert "carries no cell" in flags[0]


def test_the_checks_survive_a_cyclic_stage_chain(classes: dict) -> None:
    """Invariant 6's violation is an error elsewhere; here it must not hang."""

    record = {
        "model_estimations": [
            {"local_id": "a", "inputs_from": ["b"], "terms": [GROUP]},
            {"local_id": "b", "inputs_from": ["a"], "terms": [STAGE]},
        ],
        "analyses": [],
    }

    assert _flags(record, classes) == []


# -- occasions, and the factors that should carry them ---------------------

#: representing-models.md §5.6: one categorical term whose levels name the occasions.
#: Only the slots these checks read are populated, per the fixture note above.
TIME = {"local_id": "t_time", "type": _text("categorical"),
        "variation_level": _text("within_subject"),
        "levels": [{"level": _text("pre"), "timepoints": ["tp_base"]},
                   {"level": _text("post"), "timepoints": ["tp_post"]}]}

#: TgcHKMRfrVog's defect: the same axis collapsed into one column named for the
#: contrast it was the subject of, so nothing says which occasions were compared.
COLLAPSED = {"local_id": "t_prepost", "type": _text("continuous"),
             "name": _text("pre > post rsFC change"),
             "variation_level": _text("within_subject")}

#: The exception the term half must not flag: one number per participant, named for the
#: subtraction it came from, varying across the sample rather than within anyone. Sourced,
#: so it is correct in every respect but the one under test.
DIFFERENCE_SCORE = {"local_id": "t_dbdi", "type": _text("continuous"),
                    "name": _text("percent change in BDI"),
                    "variation_level": _text("between_subject"),
                    "source_definition": _text("Percent reduction in BDI, (post-pre)/pre.")}

TWO_OCCASIONS = {"timepoints": [{"local_id": "tp_base"}, {"local_id": "tp_post"}]}


def test_names_a_comparison_reads_contrast_syntax() -> None:
    assert validate_record.names_a_comparison(_text("pre > post rsFC change"))
    assert validate_record.names_a_comparison(_text("patients versus controls"))
    assert validate_record.names_a_comparison(_text("faces vs houses"))
    assert validate_record.names_a_comparison(_text("difference between sessions"))
    # A threshold is not an axis: the operator wants a word character on both sides.
    assert not validate_record.names_a_comparison(_text("p < .001 uncorrected"))
    assert not validate_record.names_a_comparison(_text("aSCC seed connectivity"))
    assert not validate_record.names_a_comparison(_text("age"))
    assert not validate_record.names_a_comparison(None, {"extraction_status": "not_reported"})


def test_a_contrast_shaped_continuous_term_is_flagged(classes: dict) -> None:
    """TgcHKMRfrVog's defect: the occasion axis recorded as one continuous column."""

    record = _record([COLLAPSED],
                     [("CBT change: rsFC with aSCC, pre > post",
                       [_cell("t_prepost", "positive")])])

    flags = _flags(record, classes)

    assert len(flags) == 1
    assert "Study.model_estimations[0].terms[0].name" in flags[0]
    assert "states a comparison" in flags[0]


def test_an_occasion_factor_satisfies_it(classes: dict) -> None:
    """§5.6's encoding of the same result raises nothing."""

    record = _record([TIME], [("CBT change: rsFC with aSCC, pre > post",
                              [_cell("t_time", "positive", "pre"),
                               _cell("t_time", "negative", "post")])])
    record["design"] = TWO_OCCASIONS

    assert _flags(record, classes) == []


def test_a_per_participant_difference_score_is_not_flagged(classes: dict) -> None:
    """ModelTerm.type's stated exception. Its name says `change in` and it is right:
    one number per participant, entered across the sample, is a slope."""

    record = _record([DIFFERENCE_SCORE],
                     [("CBT change: rsFC with aSCC, percent reduction in BDI",
                       [_cell("t_dbdi", "positive")])])

    assert _flags(record, classes) == []


def test_a_product_column_named_for_its_crossing_is_not_flagged(classes: dict) -> None:
    """A product column has no levels either, and is named for what it multiplies."""

    term = {"local_id": "t_x", "type": _text("continuous"),
            "name": _text("age × diagnosis"), "interaction_with": ["t_group"]}
    record = _record([GROUP, term], [("Age × diagnosis", [_cell("t_x", "positive")])])

    assert [flag for flag in _flags(record, classes) if "states a comparison" in flag] == []


def test_declared_occasions_that_no_level_names_are_flagged(classes: dict) -> None:
    """The defect from the design end: the scans are recorded, the comparison is not."""

    record = _record([GROUP], [("CBT change in rsFC, pre > post", UNSIGNED_GROUP)])
    record["design"] = TWO_OCCASIONS

    flags = _flags(record, classes)

    assert len(flags) == 1
    assert "Study.design.timepoints" in flags[0]
    assert "the comparison between them is not" in flags[0]


def test_a_baseline_only_record_is_not_flagged(classes: dict) -> None:
    """A study that scanned twice and reported once is the legitimate reading, which
    is why the trigger needs prose claiming a change and `baseline` is not it."""

    record = _record([GROUP], [("Baseline rsFC with aSCC", UNSIGNED_GROUP)])
    record["design"] = TWO_OCCASIONS

    assert _flags(record, classes) == []


def test_one_declared_occasion_cannot_be_compared(classes: dict) -> None:
    """Nothing to flag: a single occasion has no second side to have lost."""

    record = _record([GROUP], [("Change in rsFC after treatment", UNSIGNED_GROUP)])
    record["design"] = {"timepoints": [{"local_id": "tp_base"}]}

    assert _flags(record, classes) == []


# -- derived columns, and where they came from -----------------------------

def _derived(**over) -> dict:
    """A percent-change covariate, complete, with `over` knocking pieces out."""

    term = {"local_id": "t_dbdi", "type": _text("continuous"),
            "name": _text("percent change in BDI"),
            "variation_level": _text("between_subject"),
            "assessment": "as_bdi",
            "source_definition": _text("Percent reduction in BDI, (post-pre)/pre, from "
                                       "baseline to post-treatment.")}
    term.update(over)
    return {k: v for k, v in term.items() if v is not None}


def _derived_record(term: dict) -> dict:
    record = _record([term], [("CBT change: rsFC and percent reduction in BDI",
                               [_cell("t_dbdi", "positive")])])
    record["assessments"] = [{"local_id": "as_bdi", "name": _text("Beck Depression Inventory")}]
    return record


def test_names_a_derivation_reads_construction_not_measurement() -> None:
    assert validate_record.names_a_derivation(_text("percent change in BDI"))
    assert validate_record.names_a_derivation(_text("difference in reaction time"))
    assert validate_record.names_a_derivation(_text("improvement in HDRS"))
    # A measurement that merely contains "percent" is not a construction from several.
    assert not validate_record.names_a_derivation(
        _text("percentage methylation at CpG sites 11-12 around AKT1 rs1130233"))
    # A collapsed occasion factor is check_occasion_factors' finding, not this one.
    assert not validate_record.names_a_derivation(_text("pre > post rsFC change"))
    assert not validate_record.names_a_derivation(_text("BDI"))


def test_a_fully_sourced_derived_column_is_not_flagged(classes: dict) -> None:
    assert _flags(_derived_record(_derived()), classes) == []


def test_a_derived_column_with_no_derivation_recorded_is_flagged(classes: dict) -> None:
    """TgcHKMRfrVog's `term_bdi_percent_change`: the occasions it spans are nowhere."""

    flags = _flags(_derived_record(_derived(source_definition=None)), classes)

    assert len(flags) == 1
    assert "Study.model_estimations[0].terms[0].source_definition" in flags[0]
    assert "derivation is not recorded" in flags[0]


def test_a_derived_column_still_names_its_instrument(classes: dict) -> None:
    """Deriving a column does not break the link to what supplied it."""

    flags = _flags(_derived_record(_derived(assessment=None)), classes)

    assert len(flags) == 1
    assert "Study.model_estimations[0].terms[0].assessment" in flags[0]
    assert "names no assessment" in flags[0]


def test_a_derived_column_with_no_assessment_to_name_is_not_flagged(classes: dict) -> None:
    """A record declaring no instrument has none for the column to have dropped, so
    the assessment half stays quiet and only the derivation is asked for."""

    record = _record([_derived(assessment=None, source_definition=None)],
                     [("Change score", [_cell("t_dbdi", "positive")])])

    flags = _flags(record, classes)

    assert len(flags) == 1
    assert "derivation is not recorded" in flags[0]


def test_a_factor_over_occasions_is_not_a_derived_column(classes: dict) -> None:
    """`TIME` compares occasions rather than being computed across them, so it needs
    no source_definition however its levels are labelled."""

    record = _record([TIME], [("Change in rsFC, pre > post",
                               [_cell("t_time", "positive", "pre"),
                                _cell("t_time", "negative", "post")])])
    record["design"] = TWO_OCCASIONS

    assert _flags(record, classes) == []


# -- arms, and the analyses that cannot say which one they are -------------

#: xevP8UDRAVh9's design: a crossover whose two arms are the whole of what its
#: analyses differ by, so an analysis that cannot reach one states its subject in
#: prose alone.
TWO_ARMS = {
    "arms": [
        {"local_id": "arm_heroin", "name": _text("heroin"), "agent": _text("heroin")},
        {"local_id": "arm_placebo", "name": _text("placebo"), "agent": _text("saline")},
    ]
}

#: The factor a crossover compares its arms with. Its levels are worded as the
#: analysis section words them, which is what a cell has to match.
ARM_FACTOR = {
    "local_id": "t_arm",
    "type": _text("categorical"),
    "levels": [
        {"level": _text("heroin-associated perfusion"), "arms": ["arm_heroin"]},
        {"level": _text("placebo-associated perfusion"), "arms": ["arm_placebo"]},
    ],
}


def _arm_record(analyses: list[tuple[str, list[dict]]], **extra) -> dict:
    record = _record([ARM_FACTOR], analyses)
    record["design"] = TWO_ARMS
    record.update(extra)
    return record


def test_an_analysis_naming_an_arm_it_cannot_reach_is_flagged(classes: dict) -> None:
    """xevP8UDRAVh9's defect: the cell says `heroin`, the level says
    `heroin-associated perfusion`, and the join to the arm breaks on the string."""

    flags = _flags(_arm_record([("Positive correlation with heroin-associated perfusion",
                                 [_cell("t_arm", "positive", "heroin")])]), classes)

    assert len(flags) == 1
    assert "arm_heroin" in flags[0]


def test_a_cell_reaching_the_level_that_names_the_arm_satisfies_it(classes: dict) -> None:
    flags = _flags(_arm_record([("Positive correlation with heroin-associated perfusion",
                                 [_cell("t_arm", "positive", "heroin-associated perfusion")])]),
                   classes)

    assert flags == []


def test_an_analysed_cohort_assigned_to_the_arm_satisfies_it(classes: dict) -> None:
    """The parallel-group route: no cell names the arm, but the cohort was assigned
    to it, so `Group.arm` carries what the contrast does not."""

    record = _arm_record([("Perfusion under heroin", [_cell("t_arm", "positive", "heroin")])],
                         groups=[{"local_id": "g1", "arm": "arm_heroin"}])
    record["analyses"][0]["groups"] = [{"group": "g1"}]

    assert _flags(record, classes) == []


def test_an_analysis_naming_no_arm_is_left_alone(classes: dict) -> None:
    """A baseline contrast in a study that has arms is not about either of them,
    which is what keeps 84rGLhCbUJTh's four pre-medication analyses silent."""

    flags = _flags(_arm_record([("Areas of abnormal FA before medication",
                                 [_cell("t_arm", "positive", "heroin")])]), classes)

    assert flags == []


def test_a_short_arm_name_does_not_match_everything(classes: dict) -> None:
    """A two-character arm name would appear inside unrelated prose, so it is not
    vocabulary. The arm is then unreachable in the same way and silently so."""

    record = _arm_record([("Positive correlation in the striatum",
                           [_cell("t_arm", "positive", "heroin")])])
    record["design"] = {"arms": [{"local_id": "arm_iv", "name": _text("IV")}]}

    assert _flags(record, classes) == []

# -- the storage schema's class rules --------------------------------------

#: `rules` is dropped by the projection to extraction, so a rule is only ever
#: evaluated against an extraction record. These check that it is evaluated at all:
#: the failure mode is a rule that reads correctly and never fires.


def _rule_errors(node: dict, classes: dict) -> list[str]:
    validator = validate_record.Validator(classes, None)
    validator.check_rules(node, "Analysis", "Study.analyses[0]")
    return validator.errors


def test_the_storage_rules_are_found(classes: dict) -> None:
    """The inventory is pinned because `rules` is the one thing the projection drops: a rule
    added to storage and not reaching `check_rules` is a constraint that reads correctly and
    never fires, which is the failure this whole section exists to catch."""

    found = validate_record.storage_rules()
    assert sorted(found) == ["Analysis", "Effect"]
    assert len(found["Analysis"]) == 2, "the two spatial_scope/regions rules"
    assert len(found["Effect"]) == 1, "cells cannot be empty"


def test_an_effect_with_no_cells_is_rejected(classes: dict) -> None:
    """`required: true` on `cells` catches an absent key and nothing else -- LinkML has no
    minimum cardinality here, so an effect that compared nothing used to validate."""

    validator = validate_record.Validator(classes, None)
    validator.check_rules({"cells": []}, "Effect", "Study.analyses[0].effect")
    assert len(validator.errors) == 1 and "cells cannot be empty" in validator.errors[0]

    ok = validate_record.Validator(classes, None)
    ok.check_rules({"cells": [{"term": "t1"}]}, "Effect", "Study.analyses[0].effect")
    assert ok.errors == []


@pytest.mark.parametrize(
    "scope,regions,fails",
    [
        ("roi", ["r1"], False),
        ("roi", [], True),
        ("roi", None, True),
        ("whole_brain", None, False),
        ("whole_brain", ["r1"], True),
        ("searchlight", ["r1"], True),
        # No rule has `unstated` as a precondition, so neither shape is constrained.
        ("unstated", ["r1"], False),
        ("unstated", None, False),
    ],
)
def test_spatial_scope_and_regions_agree(
    classes: dict, scope: str, regions: list | None, fails: bool
) -> None:
    node = {"spatial_scope": {"extraction_status": "extracted", "value": scope}}
    if regions is not None:
        node["regions"] = regions

    assert bool(_rule_errors(node, classes)) is fails


def test_a_rule_construct_the_evaluator_cannot_read_is_reported(
    classes: dict, monkeypatch
) -> None:
    """Silently skipping one turns the rule into a check that always passes."""

    monkeypatch.setattr(
        validate_record, "_RULES",
        {"Analysis": [{
            "description": "invented",
            "preconditions": {"slot_conditions": {"spatial_scope": {"equals_string": "roi"}}},
            "postconditions": {"slot_conditions": {"regions": {"maximum_cardinality": 3}}},
        }]},
    )
    errors = _rule_errors(
        {"spatial_scope": {"extraction_status": "extracted", "value": "roi"},
         "regions": ["r1"]},
        classes,
    )

    assert any("maximum_cardinality" in error and "not implemented" in error
               for error in errors)


# -- the real record -------------------------------------------------------


@requires_current_record
@requires_paper
def test_example_record_validates(record: dict, normalized: str, classes: dict) -> None:
    validator = validate_record.Validator(classes, normalized)
    validator.check_record(record)
    assert validator.errors == []
    assert validator.fields > 0
    assert validator.spans > 0


@requires_paper
def test_every_span_addresses_the_source_text(record: dict, normalized: str) -> None:
    checked = 0
    for evidence_set in build_record._iter_sets(record):
        for span in evidence_set["spans"]:
            span_tools.verify(normalized, span)
            checked += 1
    assert checked > 0


@requires_paper
def test_recorded_hash_matches_the_text(record: dict, normalized: str) -> None:
    declared = record["extraction_metadata"]["source_text_hash"]
    assert declared == text_index.text_hash(normalized)


@requires_paper
def test_no_dangling_cross_references(record: dict, classes: dict) -> None:
    assert build_record.check_local_ids(record, classes) == []


@requires_paper
def test_section_index_covers_every_span(record: dict, normalized: str) -> None:
    """Every span must fall inside an indexed section, or reviewers get no hint."""

    sections = text_index.build_sections(normalized)
    for evidence_set in build_record._iter_sets(record):
        for span in evidence_set["spans"]:
            assert text_index.section_path(sections, span["start_char"]) is not None


# -- negative cases: the validator must actually reject bad records --------


@requires_paper
@pytest.mark.parametrize(
    "mutate, expected",
    [
        pytest.param(
            lambda r: r["groups"][0].update({"not_a_real_attribute": 1}),
            "is not declared",
            id="undeclared-attribute",
        ),
        pytest.param(
            lambda r: r["groups"][0].pop("local_id"),
            "required attribute 'local_id' is missing",
            id="missing-required",
        ),
        pytest.param(
            lambda r: r["groups"][0]["age_mean"].update({"value": ["not", "a", "number"]}),
            "must be a float, got a list",
            id="list-in-scalar",
        ),
        pytest.param(
            lambda r: r["groups"][0]["age_mean"].update({"extraction_status": "maybe"}),
            "extraction_status must be one of",
            id="bad-enum",
        ),
        pytest.param(
            # Any slot the extractor marked not_reported: the rule is about the
            # wrapper, not about which field it happens to wrap.
            lambda r: next(
                node
                for node in r["groups"][0].values()
                if isinstance(node, dict) and node.get("extraction_status") == "not_reported"
            ).update({"value": "smuggled in"}),
            "not_reported fields must omit value",
            id="not-reported-with-value",
        ),
        pytest.param(
            lambda r: r["extraction_metadata"].update({"source_text_hash": "0" * 64}),
            "does not match the supplied text",
            id="wrong-hash",
        ),
        pytest.param(
            lambda r: r["extraction_metadata"]["paper_sections"][0].update({"ordinal": -1}),
            "must be >= 0",
            id="negative-minimum",
        ),
        pytest.param(
            lambda r: r["extraction_metadata"]["paper_sections"][0].update({"level": "one"}),
            "must be a integer, got str",
            id="wrong-native-type",
        ),
    ],
)
def test_validator_rejects_corrupted_record(
    record: dict, normalized: str, classes: dict, mutate, expected: str
) -> None:
    broken = copy.deepcopy(record)
    mutate(broken)

    validator = validate_record.Validator(classes, normalized)
    validator.check_record(broken)

    assert validator.errors, f"expected an error containing {expected!r}"
    assert any(expected in error for error in validator.errors), validator.errors


@requires_paper
def test_validator_rejects_shifted_span_offset(
    record: dict, normalized: str, classes: dict
) -> None:
    broken = copy.deepcopy(record)
    for evidence_set in build_record._iter_sets(broken):
        evidence_set["spans"][0]["start_char"] += 3
        break

    validator = validate_record.Validator(classes, normalized)
    validator.check_record(broken)
    assert any("disagrees with source" in error for error in validator.errors), validator.errors


@requires_paper
def test_validator_rejects_evidence_set_without_spans(
    record: dict, normalized: str, classes: dict
) -> None:
    broken = copy.deepcopy(record)
    for evidence_set in build_record._iter_sets(broken):
        evidence_set["spans"] = []
        break

    validator = validate_record.Validator(classes, normalized)
    validator.check_record(broken)
    assert any("at least one span" in error for error in validator.errors), validator.errors


# -- the builder's own guarantees -----------------------------------------


@requires_paper
def test_build_is_reproducible_and_gated_on_offsets() -> None:
    """Rebuilding from the same payloads yields the same record, every span verified.

    `build` verifies every span it emits before returning, so reaching the assertions
    at all is most of the guarantee. What it does NOT promise is that a given paper's
    payloads resolve completely: a quote the extractor paraphrased is reported as a
    failure and left out, and the record beside it carries a hand correction for
    exactly those. That is a fact about the payloads, not about the builder, so it is
    checked as "reported with a reason" rather than as "never happens".
    """

    if not PAYLOADS.is_dir():
        pytest.skip("payloads are not present")

    first, report = build_record.build(
        PAPER, TEXT, PAYLOADS, "test-model", "test-version", "2026-08-02"
    )
    second, _ = build_record.build(
        PAPER, TEXT, PAYLOADS, "test-model", "test-version", "2026-08-02"
    )
    assert first == second
    assert report.resolved_exact + report.resolved_tolerant > 0
    for failure in report.failures:
        assert ":" in failure and "quote" in failure, failure


@requires_paper
def test_aliases_only_rewrite_reference_slots(classes: dict) -> None:
    """An alias must never touch an extracted value that shares a string with an id."""

    body = {
        "analyses": [
            {
                "local_id": "a1",
                "model_estimation": "old_id",
                "name": {
                    "extraction_status": "extracted",
                    "value": "old_id",
                    "evidence": {"status": "not_found"},
                },
            }
        ]
    }
    rewrites = build_record.apply_aliases(body, classes, {"old_id": "new_id"})

    assert rewrites == 1
    assert body["analyses"][0]["model_estimation"] == "new_id"
    assert body["analyses"][0]["name"]["value"] == "old_id"



# -- rendering the coordinate tables ----------------------------------------
#
# Anchored on the three papers under review/texts/ rather than on invented
# fixtures, because every defect these guard against was found by running the
# code over real tables and none of them would have occurred to me otherwise.

TABLE_PAPERS = ("4cRnHYtfSwuK", "5Rw4BhGBShSR", "HU6mqxmtySg3")

requires_tables = pytest.mark.skipif(
    not all(
        (REVIEW / "texts" / paper / "processed" / "pubget" / "tables.jsonl").is_file()
        for paper in TABLE_PAPERS
    ),
    reason="the synced pubget tables are not present",
)


def _table_fixture(paper: str, table_id: str):
    """One paper's table, straight off disk.

    Attributing its rows to analyses, and rendering the result, belong to the review
    layer -- `ns-validate` owns those and tests them against its own superset of this
    module. What is left here is the parse: the grid, its header, and the markdown
    `build_text.py` inlines into the paper.
    """

    root = REVIEW / "texts" / paper
    record = tables.read_manifest(root)[table_id]
    return tables.read_table(
        root / "source" / "pubget",
        record["data_file"],
        label=record["table_label"],
        caption=record["caption"],
    )


def _data_rows(table) -> int:
    return sum(1 for row in table["body"] if row["type"] == "data")


@requires_tables
def test_read_table_joins_on_the_csv_filename_not_the_table_id() -> None:
    """ns-pond sanitizes the id, so an id-equality join finds nothing.

    `tables.jsonl` calls this table `t2`; its own `table_001_info.json` calls it `T2`.
    The ids that flow through this repo -- `stage1/table-map.json` keys, and
    `Analysis.tables` via that map -- are the sanitized ones, so joining on
    `info["table_id"]` (which is what the upstream app did) returns None for both
    coordinate tables of this paper and the reviewer is shown no grid at all.
    """

    root = REVIEW / "texts" / "4cRnHYtfSwuK"
    manifest = tables.read_manifest(root)
    assert manifest["t2"]["data_file"] == "table_001.csv"

    info = json.loads(
        (root / "source" / "pubget" / "tables" / "table_001_info.json").read_text("utf-8")
    )
    assert info["table_id"] == "T2", "the premise of this test changed"

    table = _table_fixture("4cRnHYtfSwuK", "t2")
    assert table is not None and _data_rows(table) == 14


@requires_tables
def test_header_runs_collapse_into_colspans() -> None:
    table = _table_fixture("4cRnHYtfSwuK", "t2")
    first = [(cell["text"], cell["span"]) for cell in table["header"][0]]
    assert ("MNI coordinate", 3) in first
    assert sum(cell["span"] for cell in table["header"][0]) == table["width"]


@requires_tables
def test_axis_columns_are_not_captured_by_a_z_statistic_column() -> None:
    """`5Rw4BhGBShSR` Table 1 has a "Z" statistic column beside its x/y/z axes.

    Accumulating axis matches across header rows top-down bound z to the statistic at
    index 7 and produced [3, 4, 7], which matches no coordinate: every one of the 77
    rows went unattributed and the table rendered with no colour at all. Resolving one
    header row at a time, bottom-up, and only from a row that names all three, rejects
    the first row -- which has the Z but neither x nor y.
    """

    table = _table_fixture("5Rw4BhGBShSR", "t0005")
    assert table["axis_cols"] == [3, 4, 5]
    # The statistic column is real and still there; it is simply not an axis. Read off
    # the collapsed header, where "Peak Coordinate" occupies one cell of span 3.
    assert [cell["text"] for cell in table["header"][0]] == [
        "Contrast", "No of voxels", "Region (s)", "Peak Coordinate", "F/T", "Z",
    ]


@requires_tables
def test_a_consecutive_axis_triple_beats_a_leftward_statistic() -> None:
    header = [["Region", "Z", "x", "y", "z", "p"]]
    assert tables._axis_columns(header, 6) == [2, 3, 4]


@requires_tables
def test_section_rows_are_recognised_as_headings_not_data() -> None:
    table = _table_fixture("HU6mqxmtySg3", "brb3829-tbl-0003")
    # Whitespace is folded before comparing: this publisher sets the contrast names with
    # U+00A0 around the ">", and a retyped literal would differ invisibly.
    sections = [
        " ".join(row["text"].split()) for row in table["body"] if row["type"] == "section"
    ]
    assert sections == [
        "Proverbs > Literal sentences",
        "Transparent proverbs > Literal sentences",
        "Opaque proverbs > Literal sentences",
    ]


# -- rebuilding the text with its tables inline -----------------------------

PUBGET = ROOT / ".tmp_repos" / "pubget"
if not (PUBGET / "src" / "pubget" / "_text.py").is_file():
    PUBGET = Path.home() / "projects" / "pubget"

TEXT_PAPER = "HU6mqxmtySg3"
ARTICLE_XML = REVIEW / "texts" / TEXT_PAPER / "source" / "pubget" / "article.xml"

requires_pubget = pytest.mark.skipif(
    not (PUBGET / "src" / "pubget" / "_text.py").is_file() or not ARTICLE_XML.is_file(),
    reason="the pubget checkout or the synced article.xml is not present",
)


@pytest.fixture(scope="module")
def pubget_text():
    import build_text

    module, _utils, _commit = build_text.load_pubget(PUBGET)
    return build_text, module


@requires_pubget
@pytest.mark.parametrize("paper", TABLE_PAPERS)
def test_the_plain_rebuild_reproduces_the_corpus_text(pubget_text, paper: str) -> None:
    """The load-bearing assumption of the whole rebuild, and the one that must scream.

    ns-pond built the corpus with this stylesheet at commit 987fc2d and cross-references
    preserved; this runs one commit later. If the two ever disagree, the offsets in every
    existing record were computed against a text this code cannot reproduce, and nothing
    should be regenerated until that is understood.
    """

    build_text, module = pubget_text
    root = REVIEW / "texts" / paper
    article = root / "source" / "pubget" / "article.xml"
    if not article.is_file():
        pytest.skip(f"no article.xml for {paper}")

    rebuilt = build_text.build(article, root / "source" / "pubget", module, keep_tables=False)
    corpus = (root / "processed" / "pubget" / "text.txt").read_text(encoding="utf-8")
    assert build_text.check_equivalence(rebuilt, corpus) is None


@requires_pubget
def test_the_checkout_is_refused_when_it_predates_the_table_insertion(pubget_text) -> None:
    """The failure mode is silent: an older checkout regenerates the text that already
    exists, and every offset would be recomputed against it as though it had changed."""

    build_text, module = pubget_text
    assert hasattr(module, "_insert_tables")
    with pytest.raises(build_text.BuildError, match="no pubget checkout"):
        build_text.load_pubget(ROOT / "does" / "not" / "exist")


@requires_pubget
def test_the_tables_variant_leaves_no_placeholder(pubget_text) -> None:
    """Placeholder numbering is count(preceding::table-wrap) and has to line up with the
    table_NNN files; a leftover means it did not."""

    build_text, module = pubget_text
    root = REVIEW / "texts" / TEXT_PAPER / "source" / "pubget"
    built = build_text.build(root / "article.xml", root, module, keep_tables=True)
    assert "[pubget-table-" not in built


@requires_pubget
def test_the_tables_variant_carries_the_cell_values_the_corpus_text_lacks(
    pubget_text,
) -> None:
    """The whole point. Before this, a coordinate could not be highlighted because it
    was not in the text at all -- pubget's stylesheet deletes td and th."""

    build_text, module = pubget_text
    root = REVIEW / "texts" / TEXT_PAPER / "source" / "pubget"
    corpus = (
        REVIEW / "texts" / TEXT_PAPER / "processed" / "pubget" / "text.txt"
    ).read_text(encoding="utf-8")
    built = build_text.build(root / "article.xml", root, module, keep_tables=True)

    peak = "−58"  # a coordinate from Table 3, with the publisher's minus sign
    assert peak not in corpus
    assert peak in built


@requires_pubget
def test_the_tables_variant_only_adds(pubget_text) -> None:
    """Every content line of the plain text survives, in order.

    Cheap proof that the flag adds the grid rather than rewriting the prose the existing
    spans address. Blank and whitespace-only lines are excluded deliberately: inserting a
    table changes how many of them sit around it, and measured on this paper that is the
    only difference -- 70 non-blank lines survive unchanged while the blank count moves
    from 88 to 96.
    """

    build_text, module = pubget_text
    root = REVIEW / "texts" / TEXT_PAPER / "source" / "pubget"
    plain = build_text.build(root / "article.xml", root, module, keep_tables=False)
    tables = build_text.build(root / "article.xml", root, module, keep_tables=True)

    haystack = iter([line for line in tables.splitlines() if line.strip()])
    for line in plain.splitlines():
        if not line.strip():
            continue
        assert any(candidate == line for candidate in haystack), f"lost: {line[:60]!r}"


@requires_pubget
def test_each_table_follows_its_own_caption(pubget_text) -> None:
    """"At the position it appears in the article" is the claim; this is it, checked."""

    build_text, module = pubget_text
    root = REVIEW / "texts" / TEXT_PAPER / "source" / "pubget"
    built = build_text.build(root / "article.xml", root, module, keep_tables=True)

    manifest = tables.read_manifest(REVIEW / "texts" / TEXT_PAPER)
    for record in manifest.values():
        caption = (record["caption"] or "").strip()
        label = (record["table_label"] or "").strip()
        if not caption or not label or caption not in built or label not in built:
            continue
        assert built.index(caption) < built.rindex(label) or built.count(label) > 1


@requires_pubget
def test_the_rebuilt_text_needs_no_further_normalisation(pubget_text) -> None:
    """Offsets are computed against the normalized text, so a build that normalizes to
    something else would put every span one step away from the file on disk."""

    build_text, module = pubget_text
    root = REVIEW / "texts" / TEXT_PAPER / "source" / "pubget"
    built = build_text.build(root / "article.xml", root, module, keep_tables=True)
    assert text_index.normalize(built) == built


@requires_pubget
def test_the_build_is_deterministic(pubget_text) -> None:
    build_text, module = pubget_text
    root = REVIEW / "texts" / TEXT_PAPER / "source" / "pubget"
    first = build_text.build(root / "article.xml", root, module, keep_tables=True)
    second = build_text.build(root / "article.xml", root, module, keep_tables=True)
    assert first == second


def test_sync_texts_wants_the_article_xml() -> None:
    """One line, and every rebuild depends on it. rsync skips a missing source without
    complaint, so its absence would surface much later as an unexplained build failure."""

    import sync_texts

    assert "source/pubget/article.xml" in sync_texts.WANTED


LS_TOKENS = ROOT / "label-studio" / "web" / "libs" / "ui" / "src" / "tokens" / "tokens.prefix.css"


def test_only_a_markdown_heading_is_a_heading() -> None:
    """The paper is served as markdown, so `#` is the whole of the heading grammar.

    A second spelling lived in the indexer while `build_text` restyled headings as a
    title over a rule, and it needed a heuristic -- the rule must be exactly as long as
    the title -- to keep a table's delimiter row or a rule under a paragraph from
    reading as a section. Neither the spelling nor the heuristic exists now.
    """

    fooled = "Some ordinary sentence about the data\n-----\n\nmore prose"
    assert not text_index.build_sections(text_index.normalize(fooled))

    real = "## Results\n\nprose\n\n### Whole brain\n\nmore"
    sections = text_index.build_sections(text_index.normalize(real))
    assert [(s.title, s.level) for s in sections] == [("Results", 1), ("Whole brain", 2)]


@requires_tables
def test_a_section_name_does_not_set_a_column_width() -> None:
    """A forty-character contrast name in the first column pushed every coordinate off
    the right of the pane, because the section row was measured with the data rows."""

    table = _table_fixture("HU6mqxmtySg3", "brb3829-tbl-0003")
    markdown = tables.markdown_table(table)
    header = next(line for line in markdown.splitlines() if line.startswith("| kE"))
    assert len(header.split("|")[1]) <= 6, header

    # The section text is still there in full; it simply overruns its cell. Read from
    # the table rather than retyped: this publisher sets the names with U+00A0.
    sections = [row["text"] for row in table["body"] if row["type"] == "section"]
    assert sections, "the premise of this test changed"
    for text in sections:
        assert text in markdown


@requires_tables
def test_the_markdown_table_columns_line_up() -> None:
    """Padding is the whole point: unpadded pipes are not a table anyone can scan."""

    table = _table_fixture("4cRnHYtfSwuK", "t2")
    lines = [
        line for line in tables.markdown_table(table).splitlines()
        if line.startswith("|") and not line.startswith("|-")
    ]
    data = [line for line in lines if line.count("|") == lines[0].count("|")]
    assert len({len(line) for line in data}) == 1, "rows are not a common width"





# -- the type designator, and the four walkers that need it -----------------
#
# `Analysis.details` ranges on the abstract AnalysisDetails, whose only attribute is
# `details_type`; `seed_regions` is declared on ConnectivityDetails. A walker recursing on
# the declared range therefore never sees it, which on the corpus hid 40 shape errors.


def test_designated_type_follows_the_declaration(classes: dict) -> None:
    payload = {"details_type": "ConnectivityDetails"}
    assert schema_utils.designated_type(classes, payload, "AnalysisDetails") == \
        "ConnectivityDetails"
    assert schema_utils.type_designator(classes, "AnalysisDetails") == "details_type"
    assert schema_utils.type_designator(classes, "Group") is None


@pytest.mark.parametrize("named", [None, "", "NotAClass", "Group", 7])
def test_designated_type_falls_back_rather_than_raising(classes: dict, named) -> None:
    """Silent by contract: a repair pass wants the best available answer, and `Group` is
    not an AnalysisDetails so naming it must not smuggle Group's slots in."""

    assert schema_utils.designated_type(
        classes, {"details_type": named}, "AnalysisDetails") == "AnalysisDetails"


def test_listify_reaches_a_slot_declared_on_a_payload_subclass(classes: dict) -> None:
    """The 40-error regression guard. `seed_regions` is multivalued and lives on
    ConnectivityDetails, two hops down through a single-valued nested slot."""

    body = {"analyses": [{
        "local_id": "a1",
        "details": {"details_type": "ConnectivityDetails", "seed_regions": "reg_1"},
    }]}
    fixed = build_record.listify_nested(body, classes)
    assert body["analyses"][0]["details"]["seed_regions"] == ["reg_1"]
    assert any("seed_regions" in line for line in fixed)


def test_a_scalar_in_a_multivalued_wrapper_is_listified(classes: dict) -> None:
    """`interpretations` is an ExtractedStringList: one wrapper holding a list."""

    body = {"analyses": [{"local_id": "a1", "interpretations": {
        "extraction_status": "extracted", "value": "one finding", "value_source": "reported",
        "evidence": {"status": "not_found"}}}]}
    fixed = build_record.listify_scalars(body, classes)
    assert body["analyses"][0]["interpretations"]["value"] == ["one finding"]
    assert fixed == ["Study.analyses[0].interpretations"]


def test_a_missing_value_is_left_for_the_validator(classes: dict) -> None:
    """`extracted` with no value is a different fault and stays visible as one."""

    body = {"analyses": [{"local_id": "a1", "interpretations": {
        "extraction_status": "extracted", "value": None,
        "evidence": {"status": "not_found"}}}]}
    assert build_record.listify_scalars(body, classes) == []


def test_a_scalar_where_an_enum_list_belongs_is_an_error(classes: dict) -> None:
    """`ExtractedResponseModeList` declares its `value` with `any_of` and no `range`, so
    the shape check used to be unreachable and a bare string passed silently."""

    validator = validate_record.Validator(classes, None)
    validator.check_field(
        {"extraction_status": "extracted", "value": "button_press",
         "value_source": "reported", "evidence": {"status": "not_found"}},
        "ExtractedResponseModeList", "Study.tasks[0].response_mode")
    assert [e for e in validator.errors if "must be a list of ResponseMode" in e]


# -- §3 invariants 2, 3 and 4: a cell's term and level ----------------------
#
# The join is on the string, and nothing checked it. On the 16-record corpus 55 of 140
# levelled cells named a level their term does not declare, across 8 papers -- and 45 of
# those survived a careful hand review, so this is the class a reader cannot see.


def _cell_errors(record: dict, classes: dict) -> list[str]:
    validator = validate_record.Validator(classes, None)
    validator.check_cell_terms(record)
    return validator.errors


def _levelled(*names: str) -> dict:
    return {"local_id": "t_group", "type": _text("categorical"),
            "levels": [{"level": _text(name)} for name in names]}


def test_a_cell_level_naming_a_declared_level_is_accepted(classes: dict) -> None:
    record = _record([_levelled("patients", "controls")],
                     [("dx", [_cell("t_group", "positive", "patients")])])
    assert _cell_errors(record, classes) == []


def test_a_cell_level_naming_no_declared_level_is_an_error(classes: dict) -> None:
    """`AD` against a declared `AD group`: the mapper's join finds nothing, and the record
    looks like it recorded which cohort was compared."""

    record = _record([_levelled("AD group", "HC group")],
                     [("dx", [_cell("t_group", "positive", "AD")])])
    errors = _cell_errors(record, classes)
    assert len(errors) == 1 and "matches none of term" in errors[0]
    assert "'AD group'" in errors[0], "the declared levels are offered, not just refused"


def test_a_cell_naming_a_term_of_another_model_is_an_error(classes: dict) -> None:
    """Invariant 2. The term exists, so `check_local_ids` is satisfied and the record is
    structurally fine -- it is the *scope* that is wrong, and the message says whose."""

    record = _record([_levelled("patients", "controls")],
                     [("dx", [_cell("t_elsewhere", "positive", "patients")])])
    record["model_estimations"].append(
        {"local_id": "m2", "terms": [{"local_id": "t_elsewhere", "type": _text("categorical")}]})
    errors = _cell_errors(record, classes)
    assert len(errors) == 1 and "'m2'" in errors[0] and "inputs_from" in errors[0]


def test_a_cell_naming_a_term_of_a_lower_stage_is_accepted(classes: dict) -> None:
    """The converse, and the reason the walk follows `inputs_from`: a group contrast of a
    first-level column is a cell on that stage's term, not a copy hoisted upward."""

    record = _record([], [("dx", [_cell("t_first", "positive", "task")])])
    record["model_estimations"][0]["inputs_from"] = ["m_first"]
    record["model_estimations"].append({"local_id": "m_first", "terms": [
        {"local_id": "t_first", "type": _text("categorical"),
         "levels": [{"level": _text("task")}]}]})
    assert _cell_errors(record, classes) == []


def test_a_term_naming_nothing_at_all_is_an_error(classes: dict) -> None:
    record = _record([], [("dx", [_cell("t_missing", "positive", "patients")])])
    errors = _cell_errors(record, classes)
    assert len(errors) == 1 and "names no ModelTerm anywhere" in errors[0]


def test_a_level_differing_only_in_case_is_repaired_not_reported(classes: dict) -> None:
    """A transcription slip, not a claim about the paper, so the builder settles it and
    says so -- and `check_cell_terms` then has nothing to report."""

    record = _record([_levelled("healthy controls")],
                     [("dx", [_cell("t_group", "positive", "Healthy controls")])])
    fixed = build_record.align_cell_levels(record)
    assert len(fixed) == 1 and "'Healthy controls' -> 'healthy controls'" in fixed[0]
    assert record["analyses"][0]["effect"]["cells"][0]["level"]["value"] == "healthy controls"
    assert _cell_errors(record, classes) == []


def test_a_level_that_merely_shortens_a_declared_one_is_not_repaired(classes: dict) -> None:
    """`AD` is not a folding of `AD group`. Shortening a level is a claim, and guessing
    which cohort was meant is the one thing this field must not contain."""

    record = _record([_levelled("AD group", "HC group")],
                     [("dx", [_cell("t_group", "positive", "AD")])])
    assert build_record.align_cell_levels(record) == []


def test_an_ambiguous_fold_is_left_alone(classes: dict) -> None:
    """Two declared levels folding to the same string makes the rewrite a coin toss."""

    record = _record([_levelled("Controls", "controls")],
                     [("dx", [_cell("t_group", "positive", "CONTROLS")])])
    assert build_record.align_cell_levels(record) == []


# -- resolving the coordinate columns --------------------------------------
#
# Eight of the corpus's 37 coordinate tables named their axes in a shape `AXIS` could not
# read, and one named them in columns that do not hold them. Where the columns go
# unresolved, row matching falls back to comparing any number in the row, which the review
# layer's own docstring calls the behaviour that over-attributes.


def _body(*rows: list[str]) -> list[dict]:
    return [{"type": "data", "cells": row} for row in rows]


def test_a_pandas_suffixed_colspan_is_three_axis_columns() -> None:
    """`84rGLhCbUJTh` Table 2: one merged header over three columns, which pandas
    de-duplicates into `.1` and `.2`."""

    header = [["Diffusion parameter", "Region", "Peak coordinates (x,y,z)",
               "Peak coordinates (x,y,z).1", "Peak coordinates (x,y,z).2", "t value"]]
    body = _body(["FA", "L SFG", "-10", "52", "16", "3.79"])
    assert tables._axis_columns(header, 6, body) == [2, 3, 4]


def test_a_colspan_naming_no_axis_letter_still_resolves() -> None:
    """`kzMj26hGWacQ` t0015 heads the run `Peak coordinates` and puts `X Y Z` on the row
    below, where pandas left-aligns them to columns 0-2. The label plus the numbers is
    enough; the misplaced letters are no help."""

    header = [["Brain regions", "Voxels", "Hem.", "Voxels in region",
               "Peak coordinates", "Peak coordinates", "Peak coordinates", "Peak t"],
              ["X", "Y", "Z", "", "", "", "", ""]]
    body = _body(["Cluster 1", "2971.0", "", "", "24", "-54", "51.0", "3.891"])
    assert tables._axis_columns(header, 8, body) == [4, 5, 6]


def test_an_axis_triple_no_row_supports_is_rejected() -> None:
    """The same table's misplaced `X Y Z`, on its own. Returning [0,1,2] is worse than
    returning nothing: row matching took the strict path and attributed zero of 34 rows."""

    header = [["Brain regions", "Voxels", "Hem."], ["X", "Y", "Z"]]
    body = _body(["Superior parietal gyrus", "468.0", "B"])
    assert tables._axis_columns(header, 3, body) is None


def test_a_parenthesised_axis_letter_resolves() -> None:
    header = [["Tal(x)", "Tal(y)", "Tal(z)", "Cerebral Region"]]
    body = _body(["-42", "34", "38", "L IFG"])
    assert tables._axis_columns(header, 4, body) == [0, 1, 2]


def test_a_statistic_column_named_z_is_not_an_axis() -> None:
    """The guard `AXIS` was written for, still holding once PAREN_AXIS is in the chain."""

    header = [["Region", "x", "y", "z", "Peak (Z)"]]
    body = _body(["L IFG", "-42", "34", "38", "6.85"])
    assert tables._axis_columns(header, 5, body) == [1, 2, 3]


def test_one_column_holding_the_whole_triple_is_reported_separately() -> None:
    """Reported as `axis_cell`, never as `axis_cols`: that key is three indices at four
    call sites and widening its type there is how `cells[column]` becomes an IndexError."""

    header = [["Region", "Z score", "MNI coordinates (x, y, z)"]]
    body = _body(["L IPL", "4.4", "-52,-42,56"], ["L MCC", "3.71", "-4,-26,36"])
    assert tables._axis_columns(header, 3, body) is None
    assert tables._axis_cell(header, 3, body) == 2


def test_a_triple_column_is_confirmed_by_majority_not_by_one_row() -> None:
    """One triple-looking cell in a column of region names must not carry it."""

    header = [["MNI coordinates (x, y, z)", "Region"]]
    body = _body(["-52,-42,56", "L IPL"], ["not a coordinate", "L MCC"],
                 ["also not one", "R STG"])
    assert tables._axis_cell(header, 2, body) is None


@pytest.mark.parametrize("cell,expected", [
    ("-52,-42,56", (-52.0, -42.0, 56.0)),
    ("(-30, -84, 22)", (-30.0, -84.0, 22.0)),
    ("46 -8 -38", (46.0, -8.0, -38.0)),
    ("− 52,− 42,56", (-52.0, -42.0, 56.0)),
])
def test_a_triple_cell_keeps_every_sign(cell: str, expected: tuple) -> None:
    """The sign is the whole risk. A pattern that skips a leading bracket by consuming any
    non-digit eats the minus with it and relocates the peak to the other hemisphere."""

    found = tables.TRIPLE_CELL.match(tables.normalize_number(cell))
    assert found is not None, cell
    assert tuple(float(value) for value in found.groups()) == expected


def test_normalize_number_closes_the_sign_digit_gap() -> None:
    assert tables.normalize_number("− 54") == "-54"
    assert tables.normalize_number("- 54") == "-54"


# -- a coordinate table that is not an analysis -----------------------------
#
# `Table.non_analysis_content` is the only field that can say a table's rows are locations
# rather than findings. Without it, a table deliberately not encoded and a table the
# extraction missed are the same silence -- and `6oTrCJA43Jcd`'s ICA component peaks were
# encoded as an analysis with a fabricated cell rather than left unowned.


def _purpose_flags(record: dict, classes: dict) -> tuple[list[str], list[str]]:
    validator = validate_record.Validator(classes, None)
    validator.check_table_purpose(record)
    return validator.errors, validator.warnings


def test_a_table_an_analysis_names_needs_no_purpose(classes: dict) -> None:
    record = {"tables": [{"local_id": "tbl1"}],
              "analyses": [{"local_id": "a1", "tables": ["tbl1"]}]}
    assert _purpose_flags(record, classes) == ([], [])


def test_a_table_nobody_names_and_nothing_explains_is_flagged(classes: dict) -> None:
    """The missed-analysis case, and the one this field exists to separate."""

    record = {"tables": [{"local_id": "tbl4"}], "analyses": []}
    errors, warnings = _purpose_flags(record, classes)
    assert errors == []
    assert len(warnings) == 1 and "deliberately not encoded or missed" in warnings[0]


def test_a_table_that_says_what_it_reports_is_accepted(classes: dict) -> None:
    record = {"tables": [{"local_id": "tbl4",
                          "non_analysis_content": _text("component_peaks")}],
              "analyses": []}
    assert _purpose_flags(record, classes) == ([], [])


def test_a_table_cannot_both_be_an_analysis_and_not_one(classes: dict) -> None:
    record = {"tables": [{"local_id": "tbl4",
                          "non_analysis_content": _text("component_peaks")}],
              "analyses": [{"local_id": "a1", "tables": ["tbl4"]}]}
    errors, warnings = _purpose_flags(record, classes)
    assert warnings == []
    assert len(errors) == 1 and "an analysis names it" in errors[0]


def test_the_purpose_vocabulary_is_open(classes: dict, enums: dict) -> None:
    """An unanticipated purpose is written down rather than forced into the nearest value,
    which is what `any_of: [TableContent, string]` buys."""

    validator = validate_record.Validator(classes, None, enums)
    validator.check_field(
        {"extraction_status": "extracted", "value": "a genotyping panel",
         "value_source": "reported", "evidence": {"status": "not_found"}},
        "ExtractedTableContent", "Study.tables[0].non_analysis_content")
    assert validator.errors == [], "an open vocabulary must not reject a free-text answer"
    assert any("open vocabulary" in w for w in validator.warnings), (
        "and it must still be reported, because off-vocabulary answers accumulating are "
        "the evidence for whether the vocabulary is short a value"
    )


# -- the allowlist, and the stage-chain invariants --------------------------


def test_an_allowlist_entry_covers_every_index_of_one_path() -> None:
    """One entry has to cover `analyses[0..3].details.seed_regions`. Naming four is how an
    allowlist becomes stale the moment a paper gains a fifth analysis."""

    gaps = [("Study.analyses.details.seed_regions", "-> unknown local_id")]
    findings = [f"Study.analyses[{i}].details.seed_regions -> unknown local_id 'x'"
                for i in range(4)]
    reported, suppressed = known_gaps.partition(findings, gaps)
    assert reported == [] and len(suppressed) == 4


def test_an_allowlist_entry_matches_a_finding_with_no_path() -> None:
    """`local_id 'term_age' is declared 2 times` carries no path, and an entry keyed only
    on the message has to reach it."""

    gaps = [("", "local_id 'term_")]
    reported, suppressed = known_gaps.partition(
        ["local_id 'term_age' is declared 2 times; every reference to it is ambiguous"], gaps)
    assert reported == [] and len(suppressed) == 1


def test_an_empty_allowlist_entry_suppresses_nothing() -> None:
    """The one failure mode worth being paranoid about: an entry with neither a path nor a
    message would otherwise swallow the whole report."""

    reported, suppressed = known_gaps.partition(["anything at all"], [("", "")])
    assert reported == ["anything at all"] and suppressed == []


def test_an_absent_allowlist_is_not_an_error() -> None:
    """An empty allowlist is the goal state, not a misconfiguration."""

    assert known_gaps.load(Path("/nonexistent/known-gaps.yaml"), "anyPaper") == []
    assert known_gaps.load(None, "anyPaper") == []


def test_the_shipped_allowlist_parses_and_every_entry_says_why() -> None:
    """Same discipline `corrections/*.json` imposes: an allowlist without reasons is a place
    findings go to be forgotten."""

    import yaml

    document = yaml.safe_load(known_gaps.DEFAULT.read_text(encoding="utf-8")) or {}
    entries = document.get("gaps") or []
    assert entries, "the shipped allowlist is empty; delete it rather than ship an empty one"
    for entry in entries:
        assert entry.get("paper"), entry
        assert (entry.get("why") or "").strip(), entry
        assert entry.get("path") or entry.get("message"), (
            f"{entry.get('paper')}: an entry with neither path nor message matches nothing"
        )


def test_a_cyclic_inputs_from_is_reported_and_not_merely_survived(classes: dict) -> None:
    """`_terms_in_scope` already guards against the hang. Surviving bad input is not
    reporting it, and a model fitted on its own output is not a stage order."""

    record = {"model_estimations": [
        {"local_id": "m1", "inputs_from": ["m2"], "terms": []},
        {"local_id": "m2", "inputs_from": ["m1"], "terms": []},
    ], "analyses": []}
    validator = validate_record.Validator(classes, None)
    validator.check_model_stages(record)
    assert any("cyclic" in error for error in validator.errors)


def test_one_term_name_twice_in_a_stage_chain_is_reported(classes: dict) -> None:
    """A first-level `motion` and a group-level `motion` are two columns with one name in
    one term list, and a reader cannot tell a refit from a mistake."""

    record = {"model_estimations": [
        {"local_id": "m_group", "inputs_from": ["m_first"],
         "terms": [{"local_id": "t_a", "name": _text("motion")}]},
        {"local_id": "m_first",
         "terms": [{"local_id": "t_b", "name": _text("Motion")}]},
    ], "analyses": []}
    validator = validate_record.Validator(classes, None)
    validator.check_model_stages(record)
    assert any("appears on both" in error for error in validator.errors)


def test_the_same_name_on_one_model_is_not_a_chain_collision(classes: dict) -> None:
    """The invariant is about a *chain*. Two same-named terms on one record are a different
    fault, and `unique_keys` is what would catch it."""

    record = {"model_estimations": [
        {"local_id": "m1", "terms": [{"local_id": "t_a", "name": _text("motion")},
                                     {"local_id": "t_b", "name": _text("motion")}]},
    ], "analyses": []}
    validator = validate_record.Validator(classes, None)
    validator.check_model_stages(record)
    assert validator.errors == []
