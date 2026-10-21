"""Every extraction entity carries an optional stable_id, a UUID the store mints."""

import pytest
from pydantic import BaseModel, ValidationError

from conftest import load
from study_schema import jsonschema
from study_schema.models import extraction

STABLE_ID = "3f2b8c1e-9a4d-4e7b-8c2a-1d5e6f7a8b90"


def _entity_classes():
    return [
        name
        for name, model in vars(extraction).items()
        if isinstance(model, type)
        and issubclass(model, BaseModel)
        and "local_id" in model.model_fields
    ]


def test_record_with_stable_id_on_its_entity_validates(example_paths):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    record = load(example_paths["record"])
    record["stable_id"] = STABLE_ID

    extraction.Study.model_validate(record)
    validator.validate(record, jsonschema.load("extraction-record"))


def test_record_without_stable_id_still_validates(example_paths):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    record = load(example_paths["record"])
    record.pop("stable_id", None)

    extraction.Study.model_validate(record)
    validator.validate(record, jsonschema.load("extraction-record"))


@pytest.mark.parametrize(
    "value",
    [
        "not-a-uuid",
        "3f2b8c1e9a4d4e7b8c2a1d5e6f7a8b90",
        "3F2B8C1E-9A4D-4E7B-8C2A-1D5E6F7A8B90",
        "3F2B8C1E-9A4D-4E7B-8C2A-1D5E6F7A8B90x",
    ],
)
def test_malformed_stable_id_is_refused(value, example_paths):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    record = load(example_paths["record"])
    record["stable_id"] = value

    with pytest.raises(ValidationError, match="Invalid stable_id format"):
        extraction.Study.model_validate(record)
    with pytest.raises(validator.ValidationError, match="does not match"):
        validator.validate(record, jsonschema.load("extraction-record"))


def test_each_entity_class_has_the_slot_in_its_json_schema():
    document = jsonschema.load("extraction-record")
    definitions = dict(document.get("$defs", {}))
    definitions.setdefault(document["title"], document)
    classes = _entity_classes()

    assert len(classes) > 1
    for name in classes:
        properties = definitions[name]["properties"]
        assert "stable_id" in properties, name
        assert "pattern" in properties["stable_id"], name
        assert "string" in properties["stable_id"]["type"], name
