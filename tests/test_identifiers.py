"""A header's identifiers tell the version of record from the preprint, and say which the
paper has."""

import pytest
from pydantic import ValidationError

from conftest import EXAMPLES, load
from study_schema import jsonschema
from study_schema.models import paper_parse, storage

IDENTIFIERS = EXAMPLES / "identifiers"
VALID = ["preprint_only", "published_with_preprint"]
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
