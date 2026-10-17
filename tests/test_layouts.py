"""The layouts read the examples into models, write them back byte for byte, and the
cross-file checks catch what a single file's model cannot."""

import shutil

import pytest

pyarty = pytest.importorskip("pyarty")
if not hasattr(pyarty.codecs, "is_pydantic_model"):
    pytest.skip("pyarty predates pydantic payloads", allow_module_level=True)

from conftest import EXAMPLES, STUDY  # noqa: E402
from pyarty.errors import ReadError  # noqa: E402
from study_schema.layouts import (  # noqa: E402
    Corpus,
    PaperParse,
    Run,
    check_paper,
    check_revision,
    check_run,
)
from study_schema.models.paper_parse import CoordinateParse  # noqa: E402


def _files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_corpus_reads_into_models():
    corpus = Corpus.read(EXAMPLES / "corpus")
    [paper] = corpus.papers
    assert paper.study_id == STUDY
    assert isinstance(paper.parse.coordinate_parse, CoordinateParse)
    assert check_paper(paper.parse, EXAMPLES / "corpus" / STUDY) == []


def test_run_checks_clean():
    run = Run.read(EXAMPLES / "run")
    assert set(run.records) == set(run.revisions) == {STUDY}
    assert check_run(run, EXAMPLES / "corpus") == {}


def test_round_trip_is_byte_for_byte(tmp_path):
    for layout, name in ((PaperParse, f"corpus/{STUDY}/parse"), (Run, "run")):
        layout.read(EXAMPLES / name).write(tmp_path / name)
        assert _files(tmp_path / name) == _files(EXAMPLES / name)


def test_a_broken_file_fails_on_read(tmp_path):
    shutil.copytree(EXAMPLES / "corpus" / STUDY / "parse", tmp_path / "parse")
    (tmp_path / "parse" / "coordinate_parse.json").write_text('{"parse_id": "x"}')
    with pytest.raises(ReadError, match="CoordinateParse"):
        PaperParse.read(tmp_path / "parse")


def _pair():
    original = PaperParse.read(EXAMPLES / "corpus" / STUDY / "parse").coordinate_parse
    revision = Run.read(EXAMPLES / "run").revisions[STUDY]
    return original, revision


def test_a_dropped_verdict_is_caught():
    original, revision = _pair()
    revision.verdicts = [v for v in revision.verdicts if v.verdict != "split"]
    problems = check_revision(original, revision)
    assert any("has no verdict" in p for p in problems)
    assert any("neither added nor a replacement" in p for p in problems)


def test_a_wrong_revision_of_is_caught():
    original, revision = _pair()
    revision.revision_of = "something-else"
    assert any("revision_of" in p for p in check_revision(original, revision))


def test_a_key_that_does_not_match_its_content_is_caught():
    paper = PaperParse.read(EXAMPLES / "corpus" / STUDY / "parse")
    paper.coordinate_parse.analyses[0].key = "analysis#0000000000000000"
    assert any("not the key its content gives" in p for p in check_paper(paper))


def test_a_different_text_is_caught(tmp_path):
    shutil.copytree(EXAMPLES / "corpus" / STUDY, tmp_path / STUDY)
    (tmp_path / STUDY / "processed" / "pubget" / "text.txt").write_text("edited")
    paper = PaperParse.read(tmp_path / STUDY / "parse")
    assert any("text_sha256" in p for p in check_paper(paper, tmp_path / STUDY))


def test_an_edit_names_exactly_one_replacement():
    original, revision = _pair()
    [split] = [v for v in revision.verdicts if v.verdict == "split"]
    split.verdict = "edit"
    assert any("edit" in p and "exactly one" in p for p in check_revision(original, revision))


@pytest.mark.parametrize("replaced_by", [[], None])
def test_an_edit_with_no_replacement_is_caught(replaced_by):
    original, revision = _pair()
    [split] = [v for v in revision.verdicts if v.verdict == "split"]
    split.verdict = "edit"
    split.replaced_by = replaced_by
    assert any("edit" in p and "exactly one" in p for p in check_revision(original, revision))


def test_an_edit_that_names_its_one_replacement_is_accepted():
    original, revision = _pair()
    [split] = [v for v in revision.verdicts if v.verdict == "split"]
    split.verdict = "edit"
    kept, dropped = split.replaced_by
    split.replaced_by = [kept]
    revision.analyses = [a for a in revision.analyses if a.key != dropped]
    assert check_revision(original, revision) == []


def test_an_edit_that_names_its_own_key_is_refused():
    original, revision = _pair()
    [split] = [v for v in revision.verdicts if v.verdict == "split"]
    split.verdict = "edit"
    split.replaced_by = [split.key]
    assert (
        f"edit {split.key}: replaced_by names the analysis itself; unchanged content is an accept"
        in check_revision(original, revision)
    )


def test_the_example_revision_has_no_problems():
    assert check_revision(*_pair()) == []


def test_a_revision_with_the_originals_parse_id_is_caught():
    original, revision = _pair()
    revision.parse_id = original.parse_id
    assert "the revision has the original's parse_id" in check_revision(original, revision)


def test_a_revision_of_a_different_article_is_caught():
    original, revision = _pair()
    revision.header.article_id = "art-000002"
    assert "the revision is of a different article" in check_revision(original, revision)


def test_a_revision_of_a_different_text_is_caught():
    original, revision = _pair()
    revision.text_sha256 = "0" * 64
    assert "the revision addresses a different text" in check_revision(original, revision)


def test_two_revision_analyses_with_one_key_are_caught():
    original, revision = _pair()
    revision.analyses.append(revision.analyses[0].model_copy(deep=True))
    key = revision.analyses[0].key
    assert f"the revision has two analyses keyed {key}" in check_revision(original, revision)


def test_two_parse_analyses_with_one_key_are_caught():
    paper = PaperParse.read(EXAMPLES / "corpus" / STUDY / "parse")
    parse = paper.coordinate_parse
    parse.analyses.append(parse.analyses[0].model_copy(deep=True))
    assert f"the parse has two analyses keyed {parse.analyses[0].key}" in check_paper(paper)


def test_an_add_the_original_already_has_is_caught():
    original, revision = _pair()
    [add] = [v for v in revision.verdicts if v.verdict == "add"]
    add.key = original.analyses[0].key
    assert f"add {add.key}: the original already has it" in check_revision(original, revision)


def test_an_add_missing_from_the_revision_is_caught():
    original, revision = _pair()
    [add] = [v for v in revision.verdicts if v.verdict == "add"]
    revision.analyses = [a for a in revision.analyses if a.key != add.key]
    assert f"add {add.key}: the revision does not have it" in check_revision(original, revision)


def test_a_verdict_for_a_key_not_in_the_original_is_caught():
    original, revision = _pair()
    [split] = [v for v in revision.verdicts if v.verdict == "split"]
    split.key = "analysis#ffffffffffffffff"
    problems = check_revision(original, revision)
    assert "split analysis#ffffffffffffffff: not in the original" in problems
    assert f"{original.analyses[0].key} has no verdict" in problems


def test_a_key_with_two_verdicts_is_caught():
    original, revision = _pair()
    [split] = [v for v in revision.verdicts if v.verdict == "split"]
    revision.verdicts.append(split.model_copy(deep=True))
    assert f"{split.key} has more than one verdict" in check_revision(original, revision)


def test_a_replacement_missing_from_the_revision_is_caught():
    original, revision = _pair()
    [split] = [v for v in revision.verdicts if v.verdict == "split"]
    _, dropped = split.replaced_by
    revision.analyses = [a for a in revision.analyses if a.key != dropped]
    assert (
        f"split {split.key}: replaced_by {dropped} is not in the revision"
        in check_revision(original, revision)
    )


@pytest.mark.parametrize("verdict", ["accept", "relabel"])
def test_an_accepted_key_must_survive_into_the_revision(verdict):
    original, revision = _pair()
    [split] = [v for v in revision.verdicts if v.verdict == "split"]
    split.verdict = verdict
    split.replaced_by = None
    assert (
        f"{verdict} {split.key}: the key must survive into the revision"
        in check_revision(original, revision)
    )


def test_a_new_analysis_that_is_neither_added_nor_a_replacement_is_caught():
    original, revision = _pair()
    revision.verdicts = [v for v in revision.verdicts if v.verdict != "add"]
    [added] = [a for a in revision.analyses if a.name == "Loss > Neutral"]
    assert (
        f"{added.key} is new but neither added nor a replacement"
        in check_revision(original, revision)
    )


def test_check_paper_catches_a_wrong_artifact_kind():
    paper = PaperParse.read(EXAMPLES / "corpus" / STUDY / "parse")
    paper.parsed_paper.header.artifact_kind = "coordinate_parse"
    assert "parsed_paper.json says it is a coordinate_parse, not a parsed_paper" in check_paper(paper)


def test_check_paper_catches_a_schema_version_mismatch():
    paper = PaperParse.read(EXAMPLES / "corpus" / STUDY / "parse")
    paper.coordinate_parse.header.schema_version = "0.0.1"
    [problem] = check_paper(paper)
    assert problem.startswith("coordinate_parse.json was written against neuroimaging-paper-parse 0.0.1")


def test_check_paper_catches_files_of_different_articles():
    paper = PaperParse.read(EXAMPLES / "corpus" / STUDY / "parse")
    paper.coordinate_parse.header.article_id = "art-000002"
    assert "the parse is of article art-000002, the paper is art-000001" in check_paper(paper)


def test_check_paper_catches_a_parse_of_a_different_text():
    paper = PaperParse.read(EXAMPLES / "corpus" / STUDY / "parse")
    paper.coordinate_parse.text_sha256 = "0" * 64
    [problem] = check_paper(paper)
    assert problem.startswith("the parse addresses a different text than the parsed paper: 000000000000 != ")


def test_check_paper_refuses_a_corpus_holding_a_revision():
    paper = PaperParse.read(EXAMPLES / "corpus" / STUDY / "parse")
    paper.coordinate_parse.revision_of = paper.coordinate_parse.parse_id
    assert (
        "the corpus holds a revision; revisions belong to the run that made them"
        in check_paper(paper)
    )


def test_check_paper_catches_a_missing_text_file(tmp_path):
    shutil.copytree(EXAMPLES / "corpus" / STUDY, tmp_path / STUDY)
    (tmp_path / STUDY / "processed" / "pubget" / "text.txt").unlink()
    paper = PaperParse.read(tmp_path / STUDY / "parse")
    assert (
        f"text_path processed/pubget/text.txt does not exist under {tmp_path / STUDY}"
        in check_paper(paper, tmp_path / STUDY)
    )


def test_check_run_reports_a_revision_with_no_record():
    run = Run.read(EXAMPLES / "run")
    del run.records[STUDY]
    assert check_run(run, EXAMPLES / "corpus") == {
        STUDY: ["a revision with no extraction record beside it"]
    }


def test_check_run_reports_the_problems_of_each_study():
    run = Run.read(EXAMPLES / "run")
    run.revisions[STUDY].revision_of = "something-else"
    report = check_run(run, EXAMPLES / "corpus")
    assert list(report) == [STUDY]
    assert report[STUDY][0].startswith("revision_of is 'something-else'")
