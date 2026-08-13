"""The two extraction passes partition the schema.

`extract_record.py` renders its prompts from the schema rather than from a written
list, so adding a class silently changes what each pass is asked for. Three ways
that goes wrong, none of which any other check would notice:

  * a class in **both** passes is described twice and minted twice, and the two
    copies collide in `check_local_ids` as "declared 2 times"
  * a class in **neither** is never rendered, so a slot ranging on it asks the
    model for a `local_id` of something it was never shown
  * a `Study` list offered as a payload key whose class is not rendered is the
    same failure with a friendlier symptom -- the model invents the shape

The split is decided entirely by `inlined`: `nested_closure` follows ownership and
stops at references. So a new entity belongs in the entities pass iff `Study` owns
a list of it and every path from `Analysis` to it is a reference.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
REVIEW = ROOT / "review"
sys.path.insert(0, str(REVIEW))
sys.path.insert(0, str(ROOT))

import build_record  # noqa: E402
import extract_record  # noqa: E402
import schema_utils  # noqa: E402

#: Rendered separately or filled by the builder, so neither pass describes them.
#: `ExtractedValue` subclasses and the evidence types are the wrapper vocabulary,
#: emitted by `render_schema` from its own branch; the scaffolding and
#: deterministic classes never reach a model at all.
NOT_A_PASS_CLASS = (
    extract_record.SCAFFOLDING_CLASSES
    | extract_record.DETERMINISTIC_CLASSES
    | {"Study", "Evidence", "EvidenceSet", "EvidenceSpan"}
)


@pytest.fixture(scope="module")
def classes() -> dict:
    return schema_utils.load_imported_classes(extract_record.EXTRACTION_SCHEMA)


@pytest.fixture(scope="module")
def passes(classes: dict) -> dict:
    entities, entity_keep = extract_record.mode_classes(classes, "entities")
    analyses, analysis_keep = extract_record.mode_classes(classes, "analyses")
    return {
        "entities": entities, "analyses": analyses,
        "entity_keep": entity_keep, "analysis_keep": analysis_keep,
    }


def _wrapper(classes: dict, name: str) -> bool:
    return name.startswith("Extracted") or schema_utils.resolves_to(
        classes, name, "ExtractedValue"
    )


def test_the_passes_are_disjoint(passes: dict) -> None:
    """A class in both is minted by both, and the copies collide by local_id."""

    assert passes["entities"] & passes["analyses"] == set()


def test_every_described_class_lands_in_a_pass(classes: dict, passes: dict) -> None:
    """Nothing owned by Study may go unrendered: a reference to an undescribed
    class asks the model for the id of something it has never been shown."""

    covered = passes["entities"] | passes["analyses"] | NOT_A_PASS_CLASS
    orphaned = {
        name for name in classes
        if name not in covered and not _wrapper(classes, name)
    }

    assert orphaned == set(), f"described by neither pass: {sorted(orphaned)}"


def test_every_payload_key_has_its_class_rendered(classes: dict, passes: dict) -> None:
    """`Study.regions` offered as a payload key while `Region` is rendered in the
    other pass is the failure this whole file exists to catch."""

    study = schema_utils.attributes_for(classes, "Study")
    for attr in passes["entity_keep"]:
        spec = study.get(attr, {})
        if schema_utils.classify_slot(classes, attr, spec) != "nested":
            continue
        for target in schema_utils.attribute_ranges(spec):
            assert target in passes["entities"], (
                f"the entities pass is asked for {attr!r} but {target} is not rendered in it"
            )


def test_entity_lists_are_offered_to_exactly_one_pass(passes: dict) -> None:
    assert "analyses" in passes["analysis_keep"]
    assert "analyses" not in passes["entity_keep"]
    # `tables` comes from the pubget manifest, so no pass asks for it.
    assert "tables" not in passes["entity_keep"]
    assert "tables" not in passes["analysis_keep"]


def test_region_is_an_entity_not_an_analysis_part(passes: dict) -> None:
    """Region is referenced from Analysis, ModelTerm, FactorLevel and
    ConnectivityDetails -- the last of which is itself owned by Analysis. Every one
    of those is `inlined: false`, which is what keeps Region on the entities side,
    where the analyses pass can reach it by local_id."""

    assert "Region" in passes["entities"]
    assert "Region" not in passes["analyses"]
    assert "regions" in passes["entity_keep"]


def test_the_worked_models_survive_the_slice() -> None:
    """`worked_models` cuts §5 out of representing-models.md by heading, so a
    renumbered heading would otherwise send an announced section that is empty."""

    section = extract_record.worked_models()

    assert section.startswith(extract_record.WORKED_MODELS_SECTION)
    # The example that would have caught TgcHKMRfrVog: a factor over occasions in a
    # study with no paradigm, and the levels that name them.
    assert "5.6 A pre–post change with no paradigm" in section
    assert "timepoints: [tp-baseline]" in section
    assert "5.12" in section, "the slice stops short of the last worked model"
    # §6 asks whether a paper fits the schema at all, which is not this pass's call.
    assert "\n## 6." not in section


def test_both_passes_are_sent_the_worked_models() -> None:
    for mode in ("entities", "analyses"):
        _, user = extract_record.build_prompt("PAPER TEXT", mode, False, "")
        assert "# Worked models" in user
        assert "5.6 A pre–post change with no paradigm" in user


def test_the_entities_pass_is_told_a_level_may_name_an_occasion() -> None:
    """The prompt used to name `conditions` and nothing else, so a resting-state
    pre/post study read as having no factor at all -- TgcHKMRfrVog's defect, from
    the instruction that produced it."""

    note = extract_record.MODE_NOTE["entities"]

    for slot in ("conditions", "cohorts", "occasions", "arms", "regions"):
        assert slot in note
    assert "FactorLevel.timepoints" in note


def test_payload_keys_split_cleanly(classes: dict) -> None:
    """Every direct `Study` list is a payload key of exactly one mode, except
    `tables`, which is nobody's."""

    direct = {k for k, v in build_record._entity_lists().items() if "." not in v}
    offered = {
        mode: {
            k for k, v in build_record._entity_lists().items()
            if "." not in v and v != "tables" and (v == "analyses") == (mode == "analyses")
        }
        for mode in ("entities", "analyses")
    }

    assert offered["entities"] & offered["analyses"] == set()
    assert offered["entities"] | offered["analyses"] | {"tables"} == direct
    assert "regions" in offered["entities"]


# -- what the passes are told beyond the schema descriptions ----------------
#
# Three instruction gaps, each measured on the 16-record corpus rather than imagined.


def test_the_entities_pass_is_told_to_emit_regions() -> None:
    """Eleven of sixteen papers emitted zero `regions`, and those eleven are exactly the
    ones throwing `roi_definition` (61 errors), `roi_labels` (9) and the LinkML rule "an ROI
    analysis must name the regions it ran over" (17) -- 87 of 143. This pass is the only
    place a Region can be created and it never mentioned one.
    """

    note = extract_record.MODE_NOTE["entities"]
    assert "Region" in note
    assert "ONLY PLACE" in note.upper()
    assert "definition_method" in note


def _unwrapped(text: str) -> str:
    """The block as one line. Its prose is hard-wrapped, so a phrase spanning a line break
    is present in the prompt and absent from a naive substring test."""

    return " ".join(text.split())


def test_the_analyses_pass_may_split_and_decline_a_stage_one_entry() -> None:
    """Stage 1 is frozen, so the only place its two failure modes can be compensated is
    here: a table splitting one parsed entry by a column the parse never saw, and a
    coordinate table that reports no tested effect at all."""

    block = extract_record.stage1_block(
        {"analyses": [{"table_id": "t1", "name": "Encoding", "table_label": "Table 1",
                       "table_caption": "Age correlation clusters",
                       "points": [{"space": "TAL", "values": [{"kind": "correlation"}]}]}]},
        {"t1": "tbl1"},
    )
    assert "SPLIT" in block and "OMIT" in block
    assert "do not drop any" not in block, "the instruction that forbade both must be gone"
    assert "non_analysis_content" in block, "a declined table has somewhere to say what it is"


def test_the_stage_one_block_requires_the_table_local_id() -> None:
    """`Analysis.tables` is emitted by the model alone, and nothing used to tell it to copy
    the bracket. It was right on 88/88 raw analyses only because one-entry-per-listing made
    the bracket unambiguous -- permitting a split or a decline removes that guarantee, so
    the requirement has to be stated in the same change."""

    block = extract_record.stage1_block(
        {"analyses": [{"table_id": "t1", "name": "A > B", "table_label": "Table 1",
                       "table_caption": "", "points": []}]},
        {"t1": "tbl1"},
    )
    assert "[table local_id: tbl1]" in block
    assert "`tables` is REQUIRED" in _unwrapped(block)
    assert "Rule 4c does not apply" in _unwrapped(block), (
        "rule 4c tells the model to omit a reference key when there is nothing to point at, "
        "which is exactly wrong here and has to be excepted explicitly"
    )
