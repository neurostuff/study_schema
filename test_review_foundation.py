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
import spans as span_tools  # noqa: E402
import table_parse as tables  # noqa: E402
import text_index  # noqa: E402
import validate_record  # noqa: E402

import schema_utils  # noqa: E402

#: A paper whose text, record and payloads are all present. It was `2abntY3hQSyq`,
#: whose text was never synced, so every test that reads a record skipped -- and
#: went on skipping, twenty-five of them, for as long as it took to notice.
PAPER = "HU6mqxmtySg3"
TEXT = REVIEW / "texts" / PAPER / "processed" / "local" / "text.tables.txt"
RECORD = REVIEW / "examples" / f"{PAPER}.extraction.json"
PAYLOADS = REVIEW / "payloads" / PAPER
IDENTIFIERS = REVIEW / "texts" / PAPER / "identifiers.json"

requires_paper = pytest.mark.skipif(
    not TEXT.is_file() or not RECORD.is_file(),
    reason="example paper text or record is not present",
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
    assert kind(analysis, "preprocessing") == "reference"
    assert kind(analysis, "inference_settings") == "nested"

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
                "preprocessing": "old_id",
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
    assert body["analyses"][0]["preprocessing"] == "new_id"
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



