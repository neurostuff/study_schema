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


def test_a_key_that_does_not_match_its_cells_is_caught():
    paper = PaperParse.read(EXAMPLES / "corpus" / STUDY / "parse")
    paper.coordinate_parse.analyses[0].key = "tbl1#000000000000"
    assert any("not the key its cells give" in p for p in check_paper(paper))


def test_a_different_text_is_caught(tmp_path):
    shutil.copytree(EXAMPLES / "corpus" / STUDY, tmp_path / STUDY)
    (tmp_path / STUDY / "processed" / "pubget" / "text.txt").write_text("edited")
    paper = PaperParse.read(tmp_path / STUDY / "parse")
    assert any("text_sha256" in p for p in check_paper(paper, tmp_path / STUDY))
