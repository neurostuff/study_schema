"""A header's identifiers tell the version of record from the preprint, and say which the
paper has."""

import pytest
from pydantic import ValidationError

from conftest import EXAMPLES, STUDY, load
from study_schema import jsonschema
from study_schema.models import paper_parse, storage

IDENTIFIERS = EXAMPLES / "identifiers"
VALID = [
    "preprint_only",
    "published_with_preprint",
    "elife_reviewed_preprint",
    "elife_reviewed_preprint_only",
    "duplicate_pubmed_record",
]
INVALID = {
    "missing_publication_status": "publication_status",
    "preprint_doi_without_server": "server",
}


def _paper_with(identifiers: dict, example_paths) -> dict:
    paper = load(example_paths["parsed_paper"])
    paper["header"]["identifiers"] = identifiers
    return paper


def _status(identifiers: dict) -> str:
    """The rule `ArticleIdentifiers.publication_status` documents."""
    record = any(identifiers.get(k) for k in ("doi", "pmid", "pmcid"))
    preprint = any(identifiers.get(k) for k in ("preprint_dois", "preprint_pmid", "preprint_pmcid"))
    return "preprint" if preprint and not record else "published"


@pytest.mark.parametrize("name", VALID)
def test_identifier_examples_validate(name, example_paths):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    identifiers = load(IDENTIFIERS / f"{name}.json")
    paper = _paper_with(identifiers, example_paths)

    parsed = paper_parse.ParsedPaper.model_validate(paper)
    validator.validate(paper, jsonschema.load("parsed-paper"))
    assert parsed.header.identifiers.publication_status == _status(identifiers)


def test_a_preprint_only_paper_holds_no_version_of_record_id():
    ids = paper_parse.ArticleIdentifiers.model_validate(load(IDENTIFIERS / "preprint_only.json"))
    assert (ids.doi, ids.pmid, ids.pmcid) == (None, None, None)
    assert ids.preprint_pmid and ids.preprint_pmcid
    assert [d.server for d in ids.preprint_dois] == ["biorxiv"]
    assert ids.publication_status == "preprint"


def test_a_published_paper_keeps_its_preprint_doi_beside_its_own():
    ids = paper_parse.ArticleIdentifiers.model_validate(load(IDENTIFIERS / "published_with_preprint.json"))
    assert ids.doi == "10.1111/desc.12933"
    assert [(d.doi, d.server) for d in ids.preprint_dois] == [
        ("10.31234/osf.io/5m87x", "psyarxiv")
    ]
    assert ids.publication_status == "published"


@pytest.mark.parametrize("name, field", INVALID.items())
def test_invalid_identifier_examples_are_refused(name, field, example_paths):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    paper = _paper_with(load(IDENTIFIERS / "invalid" / f"{name}.json"), example_paths)

    with pytest.raises(ValidationError, match=field):
        paper_parse.ParsedPaper.model_validate(paper)
    with pytest.raises(validator.ValidationError, match=field):
        validator.validate(paper, jsonschema.load("parsed-paper"))


def test_a_preprint_server_outside_the_vocabulary_is_refused():
    ids = load(IDENTIFIERS / "published_with_preprint.json")
    ids["preprint_dois"][0]["server"] = "osf"
    with pytest.raises(ValidationError, match="server"):
        paper_parse.ArticleIdentifiers.model_validate(ids)


def test_header_and_storage_share_one_publication_status_vocabulary():
    assert {v.value for v in paper_parse.PublicationStatus} == {
        v.value for v in storage.PublicationStatus
    } == {"published", "preprint"}


def _study_with(identifiers: dict) -> dict:
    bibliography = {
        "title": "An example",
        "publication_types": ["Preprint"],
        "license": {"license_id": "all-rights-reserved", "determination": "assumed"},
    }
    return {"id": "x7Ex4mpleA01", "bibliography": bibliography, "identifiers": identifiers}


@pytest.mark.parametrize("status", ["published", "preprint"])
def test_storage_study_carries_the_publication_status(status):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    study = _study_with({"publication_status": status})

    assert storage.Study.model_validate(study).identifiers.publication_status == status
    validator.validate(study, jsonschema.load("storage-study"))


def test_storage_refuses_an_unknown_publication_status():
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    study = _study_with({"publication_status": "in_press"})

    with pytest.raises(ValidationError, match="publication_status"):
        storage.Study.model_validate(study)
    with pytest.raises(validator.ValidationError):
        validator.validate(study, jsonschema.load("storage-study"))


def test_the_upload_pointer_is_the_headers_not_an_identifier(example_paths):
    paper = load(example_paths["parsed_paper"])
    assert paper_parse.ParsedPaper.model_validate(paper).header.neurostore_base_study_id == "x7Ex4mpleA01"
    paper["header"]["identifiers"]["neurostore_base_study_id"] = "x7Ex4mpleA01"
    with pytest.raises(ValidationError, match="neurostore_base_study_id"):
        paper_parse.ParsedPaper.model_validate(paper)


@pytest.mark.parametrize("year, valid", [(1600, True), (2026, True), (1599, False)])
def test_a_publication_year_before_1600_is_refused(year, valid, example_paths):
    # The parse and the storage Study share the bounds: one Bibliography.
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    paper = load(example_paths["parsed_paper"])
    paper["bibliography"]["publication_year"] = year
    study = {"id": "x7Ex4mpleA01", "bibliography": paper["bibliography"]}
    for model, document, schema in (
        (paper_parse.ParsedPaper, paper, "parsed-paper"),
        (storage.Study, study, "storage-study"),
    ):
        if valid:
            model.model_validate(document)
            validator.validate(document, jsonschema.load(schema))
        else:
            with pytest.raises(ValidationError, match="publication_year"):
                model.model_validate(document)
            with pytest.raises(validator.ValidationError):
                validator.validate(document, jsonschema.load(schema))


def test_an_unlisted_publication_type_is_kept_verbatim(example_paths):
    # StudyType lists known types only; PubMed's vocabulary is open.
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    types = ["Journal Article", "Research Support, N.I.H., Extramural"]
    paper = load(example_paths["parsed_paper"])
    paper["bibliography"]["publication_types"] = types
    study = {"id": "x7Ex4mpleA01", "bibliography": paper["bibliography"]}

    assert paper_parse.ParsedPaper.model_validate(paper).bibliography.publication_types == types
    assert storage.Study.model_validate(study).bibliography.publication_types == types
    validator.validate(paper, jsonschema.load("parsed-paper"))
    validator.validate(study, jsonschema.load("storage-study"))


def test_an_elife_reviewed_preprint_keeps_its_versions_apart_from_the_version_of_record():
    ids = paper_parse.ArticleIdentifiers.model_validate(load(IDENTIFIERS / "elife_reviewed_preprint.json"))
    assert ids.doi == "10.7554/eLife.106070"
    assert ids.alternative_dois == ["10.7554/eLife.106070.3"]
    assert ids.version_dois == ["10.7554/eLife.106070.1", "10.7554/eLife.106070.2"]
    assert [d.server for d in ids.preprint_dois] == ["biorxiv"]
    assert ids.publication_status == "published"


def test_an_elife_paper_with_no_version_of_record_is_a_preprint():
    ids = paper_parse.ArticleIdentifiers.model_validate(
        load(IDENTIFIERS / "elife_reviewed_preprint_only.json")
    )
    assert ids.doi is None
    assert [(d.doi, d.server) for d in ids.preprint_dois] == [
        ("10.7554/eLife.112197", "elife"),
        ("10.31234/osf.io/edaxh_v3", "psyarxiv"),
    ]
    assert ids.version_dois == ["10.7554/eLife.112197.1"]
    assert ids.publication_status == "preprint"


def test_a_duplicate_pubmed_record_is_an_alternative_not_a_second_paper():
    ids = paper_parse.ArticleIdentifiers.model_validate(load(IDENTIFIERS / "duplicate_pubmed_record.json"))
    assert (ids.pmid, ids.pmcid) == ("32341955", "PMC7181890")
    assert ids.alternative_pmids == ["32341954"]
    assert ids.publication_status == "published"


def _problems(identifiers: dict) -> list[str]:
    pytest.importorskip("pyarty", reason="optional: layouts")
    from study_schema.layouts import PaperParse, check_paper

    paper = PaperParse.read(EXAMPLES / "corpus" / STUDY / "parse")
    ids = paper_parse.ArticleIdentifiers.model_validate(identifiers)
    paper.coordinate_parse.header.identifiers = ids
    return [p for p in check_paper(paper) if "belongs to one slot" in p]


def test_the_header_of_a_parsed_paper_and_of_a_revision_are_checked_too():
    pytest.importorskip("pyarty", reason="optional: layouts")
    from study_schema.layouts import PaperParse, Run, check_revision, check_paper

    twice = paper_parse.ArticleIdentifiers(
        pmid="1", alternative_pmids=["1"], publication_status="published"
    )
    paper = PaperParse.read(EXAMPLES / "corpus" / STUDY / "parse")
    paper.parsed_paper.header.identifiers = twice
    assert any("parsed_paper lists 1" in p for p in check_paper(paper))

    revision = Run.read(EXAMPLES / "run").revisions[STUDY]
    revision.header.identifiers = twice
    original = PaperParse.read(EXAMPLES / "corpus" / STUDY / "parse").coordinate_parse
    assert any("the revision lists 1" in p for p in check_revision(original, revision))


@pytest.mark.parametrize("name", VALID)
def test_no_identifier_example_repeats_an_id_across_slots(name):
    assert _problems(load(IDENTIFIERS / f"{name}.json")) == []


@pytest.mark.parametrize(
    "slot, value",
    [
        ("alternative_dois", ["10.7554/eLife.106070.1"]),
        ("alternative_dois", ["10.7554/ELIFE.106070.1"]),
        ("alternative_dois", ["10.7554/eLife.106070"]),
        ("preprint_pmcid", "PMC13524378"),
        ("alternative_pmids", ["42663321"]),
        ("alternative_pmcids", ["pmc13524378"]),
        ("preprint_pmid", "42663321"),
        ("version_dois", ["10.1101/2025.02.05.636581"]),
    ],
)
def test_an_id_in_two_slots_is_reported(slot, value):
    ids = load(IDENTIFIERS / "elife_reviewed_preprint.json")
    ids[slot] = value
    assert _problems(ids)
