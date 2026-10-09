"""Where the contract files sit on disk, as pyarty layouts that read and write the models.

Two trees cross a repository boundary as files, and this module declares the contract part
of each. Everything else in them -- the downloaded sources, ingestion's legacy `stage1/` and
`processed/` files, pondie's per-stage payloads and usage log -- is left undeclared, so a
read ignores it and the owning repository stays free to change it.

The corpus, which ingestion writes and pondie and the neurostore ingester read::

    <corpus>/<study_id>/parse/parsed_paper.json        ParsedPaper
    <corpus>/<study_id>/parse/coordinate_parse.json    CoordinateParse (the original)

An extraction run, which pondie writes and the ingester reads::

    <run>/records/<study_id>.extraction.json           extraction Study
    <run>/revisions/<study_id>.coordinate_parse.json   CoordinateParse (a revision)

pondie never writes a revision into the corpus: a reader does not write its inputs, so the
original parse stays as ingestion wrote it and the revision lives with the run that made it.

A layout checks each file against its model; it cannot see how two files relate. The
`check_*` functions do that: the fingerprints a parse must share with its paper, and the
verdicts a revision owes the parse it revises.

Reading a whole corpus loads every paper, so read one paper at a time where that is all
that is needed:

    paper = PaperParse.read(corpus / study_id / "parse")
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import pyarty.codecs
from pyarty import Dir, File, Files, at, bundle

if not hasattr(pyarty.codecs, "is_pydantic_model"):
    raise ImportError(
        "study_schema.layouts needs a pyarty that reads and writes pydantic models "
        "(File[Model]); this one predates it. Install pyarty from main."
    )

from study_schema.keys import key_for
from study_schema.models import paper_parse
from study_schema.models.extraction import Study as ExtractionRecord
from study_schema.models.paper_parse import CoordinateParse, ParsedPaper

__all__ = [
    "Corpus",
    "CorpusPaper",
    "ExtractionRecord",
    "PaperParse",
    "Run",
    "check_paper",
    "check_revision",
    "check_run",
]


# -- the corpus ---------------------------------------------------------------


@bundle
class PaperParse:
    """`<study_id>/parse/`: one paper as ingestion hands it on.

    A paper triage turned away still has both files; its coordinate parse has no analyses
    and says why in `tables`.
    """

    parsed_paper: File[ParsedPaper] = at("parsed_paper.json")
    coordinate_parse: File[CoordinateParse] = at("coordinate_parse.json")


@bundle
class CorpusPaper:
    """`<study_id>/`. Only `parse/` is the contract; the rest is ingestion's own."""

    study_id: str
    parse: Dir[PaperParse] = at("parse")


@bundle
class Corpus:
    papers: Dir[list[CorpusPaper]] = at("{study_id:alnum}")


# -- an extraction run --------------------------------------------------------


@bundle
class Run:
    """`<run>/`: what one pondie run hands to the ingester, keyed by study id.

    A paper has a revision only where pondie disagreed with the parse it read.
    """

    records: Files[dict[str, ExtractionRecord]] = at(
        "records/{study_id:alnum}.extraction.json", key="study_id"
    )
    revisions: Files[dict[str, CoordinateParse]] = at(
        "revisions/{study_id:alnum}.coordinate_parse.json", key="study_id"
    )


# -- what a layout cannot check -----------------------------------------------


def check_paper(paper: PaperParse, bundle_dir: Optional[Path] = None) -> list[str]:
    """How the two files of one paper fail to agree. Empty when they do.

    `bundle_dir` is the paper's directory, `<corpus>/<study_id>`; given it, the text the
    parsed paper names is found and its fingerprint recomputed.
    """
    problems: list[str] = []
    text, parse = paper.parsed_paper, paper.coordinate_parse

    for name, artifact, kind in (
        ("parsed_paper", text, "parsed_paper"),
        ("coordinate_parse", parse, "coordinate_parse"),
    ):
        if artifact.header.artifact_kind != kind:
            problems.append(
                f"{name}.json says it is a {artifact.header.artifact_kind}, not a {kind}"
            )
        if artifact.header.schema_version != paper_parse.version:
            problems.append(
                f"{name}.json was written against neuroimaging-paper-parse "
                f"{artifact.header.schema_version}; these models are {paper_parse.version}"
            )

    if parse.header.article_id != text.header.article_id:
        problems.append(
            f"the parse is of article {parse.header.article_id}, "
            f"the paper is {text.header.article_id}"
        )
    if parse.text_sha256 != text.text_sha256:
        problems.append(
            "the parse addresses a different text than the parsed paper: "
            f"{parse.text_sha256[:12]} != {text.text_sha256[:12]}"
        )
    if parse.revision_of is not None or parse.verdicts:
        problems.append(
            "the corpus holds a revision; revisions belong to the run that made them"
        )
    problems += _check_keys(parse, "the parse")

    if bundle_dir is not None:
        path = Path(bundle_dir) / text.text_path
        if not path.is_file():
            problems.append(f"text_path {text.text_path} does not exist under {bundle_dir}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != text.text_sha256:
            problems.append(f"{text.text_path} does not match text_sha256")
    return problems


def check_revision(original: CoordinateParse, revision: CoordinateParse) -> list[str]:
    """How a revision fails to account for the parse it revises. Empty when it does.

    A revision gives one verdict for every analysis of the original and an `add` for
    every analysis it introduces, so nothing is dropped without a reason on record.
    """
    problems: list[str] = []
    if revision.revision_of != original.parse_id:
        problems.append(
            f"revision_of is {revision.revision_of!r}, not the parse it is checked "
            f"against ({original.parse_id})"
        )
    if revision.parse_id == original.parse_id:
        problems.append("the revision has the original's parse_id")
    if revision.header.article_id != original.header.article_id:
        problems.append("the revision is of a different article")
    if revision.text_sha256 != original.text_sha256:
        problems.append("the revision addresses a different text")
    problems += _check_keys(revision, "the revision")

    before = {analysis.key for analysis in original.analyses}
    after = {analysis.key for analysis in revision.analyses}
    judged: set[str] = set()
    added: set[str] = set()
    for verdict in revision.verdicts or []:
        if verdict.verdict == "add":
            added.add(verdict.key)
            if verdict.key in before:
                problems.append(f"add {verdict.key}: the original already has it")
            if verdict.key not in after:
                problems.append(f"add {verdict.key}: the revision does not have it")
            continue
        if verdict.key not in before:
            problems.append(f"{verdict.verdict} {verdict.key}: not in the original")
        if verdict.key in judged:
            problems.append(f"{verdict.key} has more than one verdict")
        judged.add(verdict.key)
        for key in verdict.replaced_by or []:
            if key not in after:
                problems.append(
                    f"{verdict.verdict} {verdict.key}: replaced_by {key} is not in the revision"
                )
        if verdict.verdict in ("accept", "relabel") and verdict.key not in after:
            problems.append(
                f"{verdict.verdict} {verdict.key}: the key must survive into the revision"
            )

    for key in sorted(before - judged):
        problems.append(f"{key} has no verdict")
    replacements = {
        key
        for verdict in revision.verdicts or []
        for key in verdict.replaced_by or []
    }
    for key in sorted(after - before - added - replacements):
        problems.append(f"{key} is new but neither added nor a replacement")
    return problems


def check_run(run: Run, corpus_root: Path) -> dict[str, list[str]]:
    """Each revision in a run checked against the original it names. Study id -> problems."""
    report: dict[str, list[str]] = {}
    for study_id, revision in sorted(run.revisions.items()):
        original = PaperParse.read(Path(corpus_root) / study_id / "parse").coordinate_parse
        problems = check_revision(original, revision)
        if study_id not in run.records:
            problems.append("a revision with no extraction record beside it")
        if problems:
            report[study_id] = problems
    return report


def _check_keys(parse: CoordinateParse, what: str) -> list[str]:
    """Keys unique, and each the one its own cells or spans derive."""
    seen: set[str] = set()
    problems = []
    for analysis in parse.analyses:
        if analysis.key in seen:
            problems.append(f"{what} has two analyses keyed {analysis.key}")
        seen.add(analysis.key)
        derived = key_for(analysis)
        if derived is None:
            problems.append(f"{analysis.key} has no cells or spans to derive a key from")
        elif derived != analysis.key:
            problems.append(f"{analysis.key} is not the key its cells give: {derived}")
    return problems
