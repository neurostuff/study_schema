"""A licence belongs to a copy; whether a text may be passed on is derived from the copy's
licence and how we obtained it, never stored."""

import pytest
import yaml
from pydantic import ValidationError

from conftest import EXAMPLES, load
from study_schema import jsonschema, linkml
from study_schema.licensing import (
    REDISTRIBUTABLE_ACCESS,
    REDISTRIBUTABLE_LICENSES,
    abstract_may_be_redistributed,
    text_may_be_redistributed,
)
from study_schema.models import extraction, paper_parse, storage

LICENSES = EXAMPLES / "licenses"
LINKML = linkml.STORAGE.parent
# name -> (text may be passed on, abstract may be passed on)
VALID = {
    "tdm_manuscript_of_open_article": (False, True),
    "preprint_only": (True, True),
    "assumed_all_rights_reserved": (False, False),
}
# Refused by the models and the JSON Schema alike.
INVALID = {
    "publisher_tdm_code": "license_id",
    "license_as_string": "license",
    "held_copy_without_access": "access",
}
# LinkML rules: the JSON Schema states them as if/then/else; the pydantic models do not
# evaluate rules.
INVALID_BY_RULE = [
    "assumed_with_stated_by",
    "abstract_without_provider",
    "preprint_copy_without_server",
    "server_named_off_a_preprint_server",
]

CC_BY = {"license_id": "cc-by", "determination": "stated", "stated_by": "jats", "stated_as": "https://creativecommons.org/licenses/by/4.0/"}
ARR = {"license_id": "all-rights-reserved", "determination": "assumed"}


def _values(path, enum):
    return yaml.safe_load(path.read_text())["enums"][enum]["permissible_values"]


def _paper_with(example: dict, example_paths) -> dict:
    paper = load(example_paths["parsed_paper"])
    paper["header"]["identifiers"] = example["identifiers"]
    paper["text_copy"] = example["text_copy"]
    paper["reported_copies"] = example.get("reported_copies")
    paper["bibliography"].update(example["bibliography"])
    return paper


def test_the_sets_match_the_schema_annotations():
    codes = _values(LINKML / "neuroimaging-study-storage" / "license.yaml", "LicenseId")
    access = _values(LINKML / "neuroimaging-paper-parse" / "parse_enums.yaml", "CopyAccess")
    assert {c for c, spec in codes.items() if (spec.get("annotations") or {}).get("redistributable")} == REDISTRIBUTABLE_LICENSES
    assert {a for a, spec in access.items() if spec["annotations"]["redistribution"] == "by_license"} == REDISTRIBUTABLE_ACCESS
    assert {spec["annotations"]["redistribution"] for spec in access.values()} == {"by_license", "never"}


@pytest.mark.parametrize(
    "license, access, expected",
    [
        (CC_BY, "public", True),
        (CC_BY, "tdm_grant", False),  # a CC-licensed Elsevier article fetched with our key
        (CC_BY, "unrecorded", False),  # a supplied PDF nobody recorded the origin of
        (CC_BY, None, True),  # the abstract of a CC-licensed paper
        ({**CC_BY, "license_id": "cc-by-nc-nd"}, "public", True),
        ({**CC_BY, "license_id": "other", "stated_as": "Elsevier user license"}, "public", False),
        ({**CC_BY, "license_id": "all-rights-reserved", "stated_as": "© 2005 Elsevier Inc."}, "public", False),
        (ARR, "public", False),
        (ARR, None, False),
    ],
)
def test_redistribution(license, access, expected):
    if access is None:
        assert abstract_may_be_redistributed(license) is expected
        assert abstract_may_be_redistributed(storage.License.model_validate(license)) is expected
    else:
        copy = {"route": "pmc", "license": license, "access": access}
        assert text_may_be_redistributed(copy) is expected
        assert text_may_be_redistributed(paper_parse.HeldCopy.model_validate(copy)) is expected


def test_a_cc_by_copy_obtained_under_a_tdm_grant_is_not_redistributed():
    copy = paper_parse.HeldCopy.model_validate({"route": "publisher", "version": "vor", "access": "tdm_grant", "license": CC_BY})
    assert abstract_may_be_redistributed(copy.license)
    assert not text_may_be_redistributed(copy)


def test_every_code_and_access_is_decided():
    for code in paper_parse.LicenseId:
        license = {**CC_BY, "license_id": code.value}
        for access in paper_parse.CopyAccess:
            expected = code.value in REDISTRIBUTABLE_LICENSES and access.value == "public"
            copy = {"route": "pmc", "license": license, "access": access}
            assert text_may_be_redistributed(copy) is expected
        assert abstract_may_be_redistributed(license) is (code.value in REDISTRIBUTABLE_LICENSES)


@pytest.mark.parametrize(
    "license, access",
    [
        (None, None),  # no licence at all
        (CC_BY, "bogus"),  # an access outside CopyAccess
        (CC_BY, ""),
        ({**CC_BY, "license_id": "cc-by-4.0"}, "public"),  # an id outside LicenseId
        ({"license_id": "cc-by", "determination": "assumed"}, None),  # the rules make an assumed licence ARR
        ({**CC_BY, "determination": "assumed"}, "public"),
        ({**CC_BY, "determination": "inferred"}, "public"),
        ({**CC_BY, "stated_by": None}, "public"),  # stated by nobody
        ({**CC_BY, "stated_as": ""}, "public"),  # stated in no words
        ({**CC_BY, "stated_as": "  "}, None),
        ({"determination": "stated", "stated_by": "jats", "stated_as": "x"}, "public"),  # no licence id
    ],
)
def test_malformed_input_is_never_permitted(license, access):
    if access is None:
        assert abstract_may_be_redistributed(license) is False
    assert text_may_be_redistributed({"route": "pmc", "license": license, "access": access}) is False


@pytest.mark.parametrize(
    "copy",
    [
        None,
        {"route": "pmc", "license": CC_BY},  # a held copy without its access
        {"route": "pmc", "license": CC_BY, "access": None},
        {"route": "pmc", "access": "public"},  # without its licence
    ],
)
def test_a_malformed_held_copy_is_never_permitted(copy):
    assert text_may_be_redistributed(copy) is False


@pytest.mark.parametrize("name, expected", VALID.items())
def test_licence_examples_validate_and_decide(name, expected, example_paths):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    paper = _paper_with(load(LICENSES / f"{name}.json"), example_paths)

    parsed = paper_parse.ParsedPaper.model_validate(paper)
    validator.validate(paper, jsonschema.load("parsed-paper"))
    # Written by the models, nulls and all, the file still validates.
    validator.validate(parsed.model_dump(mode="json"), jsonschema.load("parsed-paper"))
    text = text_may_be_redistributed(parsed.text_copy)
    abstract = abstract_may_be_redistributed(parsed.bibliography.license)
    assert (text, abstract) == expected


def test_the_open_pmc_example_may_be_passed_on(example_paths):
    paper = paper_parse.ParsedPaper.model_validate(load(example_paths["parsed_paper"]))
    assert (paper.text_copy.route, paper.text_copy.access) == ("pmc", "public")
    assert text_may_be_redistributed(paper.text_copy)
    assert abstract_may_be_redistributed(paper.bibliography.license)


def test_a_tdm_manuscript_keeps_the_articles_own_licence_beside_it():
    # PMC10518222: the held copy is a text-mining-only NIH manuscript of a CC BY-NC article.
    example = load(LICENSES / "tdm_manuscript_of_open_article.json")
    copy = paper_parse.HeldCopy.model_validate(example["text_copy"])
    bibliography = example["bibliography"]
    assert (copy.version, copy.access, copy.license.license_id) == ("am", "tdm_grant", "all-rights-reserved")
    assert bibliography["license"]["license_id"] == "cc-by-nc"
    assert bibliography["abstract_provider"] != bibliography["provider"]


def test_a_preprint_only_paper_takes_the_preprints_licence():
    example = load(LICENSES / "preprint_only.json")
    assert example["identifiers"]["publication_status"] == "preprint"
    [server_copy] = example["reported_copies"]
    assert (server_copy["route"], server_copy["preprint_server"]) == ("preprint_server", "biorxiv")
    assert example["bibliography"]["license"]["license_id"] == server_copy["license"]["license_id"]


@pytest.mark.parametrize("name, field", INVALID.items())
def test_invalid_licence_examples_are_refused(name, field, example_paths):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    paper = _paper_with(load(LICENSES / "invalid" / f"{name}.json"), example_paths)

    with pytest.raises(ValidationError, match=field):
        paper_parse.ParsedPaper.model_validate(paper)
    with pytest.raises(validator.ValidationError):
        validator.validate(paper, jsonschema.load("parsed-paper"))


def test_a_paper_without_its_text_copy_is_refused(example_paths):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    paper = load(example_paths["parsed_paper"])
    del paper["text_copy"]
    with pytest.raises(ValidationError, match="text_copy"):
        paper_parse.ParsedPaper.model_validate(paper)
    with pytest.raises(validator.ValidationError, match="text_copy"):
        validator.validate(paper, jsonschema.load("parsed-paper"))


def test_a_paper_without_a_bibliography_licence_is_refused(example_paths):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    paper = load(example_paths["parsed_paper"])
    del paper["bibliography"]["license"]
    with pytest.raises(ValidationError, match="license"):
        paper_parse.ParsedPaper.model_validate(paper)
    with pytest.raises(validator.ValidationError, match="license"):
        validator.validate(paper, jsonschema.load("parsed-paper"))


@pytest.mark.parametrize(
    "where, value",
    [
        (("bibliography", "abstract"), ""),
        (("bibliography", "abstract"), " \n"),
        (("bibliography", "license", "stated_as"), ""),
        (("text_copy", "license", "stated_as"), "   "),
    ],
)
def test_an_empty_statement_or_abstract_is_refused(where, value, example_paths):
    # A missing abstract or statement is null or absent, never an empty string.
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    paper = load(example_paths["parsed_paper"])
    holder = paper
    for key in where[:-1]:
        holder = holder[key]
    holder[where[-1]] = value
    if where[-1] == "abstract":
        paper["bibliography"]["abstract_provider"] = "pubmed"
    with pytest.raises(ValidationError, match=where[-1]):
        paper_parse.ParsedPaper.model_validate(paper)
    with pytest.raises(validator.ValidationError):
        validator.validate(paper, jsonschema.load("parsed-paper"))


@pytest.mark.parametrize("name", INVALID_BY_RULE)
def test_the_json_schema_refuses_what_the_rules_forbid(name, example_paths):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    paper = _paper_with(load(LICENSES / "invalid" / f"{name}.json"), example_paths)
    with pytest.raises(validator.ValidationError):
        validator.validate(paper, jsonschema.load("parsed-paper"))


def test_a_field_written_as_null_counts_as_absent(example_paths):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    schema = jsonschema.load("parsed-paper")
    paper = _paper_with(load(LICENSES / "assumed_all_rights_reserved.json"), example_paths)
    paper["bibliography"]["license"] = {**ARR, "stated_by": None, "stated_as": None}
    validator.validate(paper, schema)
    paper["bibliography"]["license"] = {**CC_BY, "stated_by": None}
    with pytest.raises(validator.ValidationError):
        validator.validate(paper, schema)
    paper["bibliography"]["license"] = dict(CC_BY)
    paper["bibliography"]["abstract"] = "An abstract."
    paper["bibliography"]["abstract_provider"] = None
    with pytest.raises(validator.ValidationError):
        validator.validate(paper, schema)


def test_parse_and_storage_share_one_licence_vocabulary():
    for enum in ("LicenseId", "LicenseDetermination", "MetadataSource", "OaStatus", "PublicationStatus", "PreprintServer", "CorrectionKind", "StudyType"):
        assert {v.value for v in getattr(paper_parse, enum)} == {v.value for v in getattr(storage, enum)}
    assert "publisher-tdm" not in {v.value for v in storage.LicenseId}


@pytest.mark.parametrize("name", VALID)
def test_the_storage_study_holds_the_parsed_papers_bibliography_unchanged(name, example_paths):
    # One class on both sides: the parse's dump is a storage Study's bibliography as it is.
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    parsed = paper_parse.ParsedPaper.model_validate(_paper_with(load(LICENSES / f"{name}.json"), example_paths))
    study = {
        "id": "x7Ex4mpleA01",
        "bibliography": parsed.bibliography.model_dump(mode="json"),
        "identifiers": parsed.header.identifiers.model_dump(mode="json"),
    }

    stored = storage.Study.model_validate(study)
    validator.validate(study, jsonschema.load("storage-study"))
    assert stored.bibliography.model_dump(mode="json") == study["bibliography"]
    assert stored.identifiers.model_dump(mode="json") == study["identifiers"]
    assert abstract_may_be_redistributed(stored.bibliography.license) is VALID[name][1]


def test_the_shared_classes_have_the_same_fields_in_every_contract():
    for cls in ("Bibliography", "ArticleIdentifiers", "PreprintDoi", "Correction", "License"):
        assert set(getattr(paper_parse, cls).model_fields) == set(getattr(storage, cls).model_fields), cls


def test_the_record_carries_no_bibliographic_fact():
    assert not {"bibliography", "identifiers", "title", "language", "study_type", "license"} & set(extraction.Study.model_fields)


def test_storage_refuses_a_licence_outside_the_contract():
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    def study(license):
        return {"id": "x7Ex4mpleA01", "bibliography": {"title": "An example", "license": license}}
    tdm_code = study({"license_id": "publisher-tdm", "determination": "stated", "stated_by": "jats", "stated_as": "TDM"})
    with pytest.raises(ValidationError, match="license_id"):
        storage.Study.model_validate(tdm_code)
    with pytest.raises(validator.ValidationError):
        validator.validate(tdm_code, jsonschema.load("storage-study"))
    with pytest.raises(validator.ValidationError):
        validator.validate(study({**ARR, "stated_by": "crossref"}), jsonschema.load("storage-study"))


def test_storage_requires_the_bibliography():
    with pytest.raises(ValidationError, match="bibliography"):
        storage.Study.model_validate({"id": "x7Ex4mpleA01"})
