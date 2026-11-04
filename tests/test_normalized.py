"""The normalization pass's sidecar: `normalized` on every extracted value, checked by both artefacts."""

import copy
import inspect

import pytest
from pydantic import ValidationError

from conftest import load
from study_schema import jsonschema
from study_schema.models import extraction, storage

MONDO_AD = {
    "vocabulary": "MONDO",
    "status": "mapped",
    "value": "MONDO:0004975",
    "label": "Alzheimer disease",
    "item": 0,
    "method": "lexical",
    "mapping_tool": "pondie.normalization.medical_condition 16a42d8",
    "confidence": 1.0,
}


def _record_with(example_paths, *entries):
    data = load(example_paths["record"])
    body = data.get("study") or data
    body["description"] = {
        "extraction_status": "extracted",
        "value": "Probable Alzheimer's disease; healthy older adults; amnestic MCI",
        "value_source": "reported",
        "evidence": {"status": "not_found"},
        "normalized": [copy.deepcopy(e) for e in entries],
    }
    return data


def test_every_extracted_value_carries_normalized():
    wrappers = [
        cls
        for _, cls in inspect.getmembers(extraction, inspect.isclass)
        if issubclass(cls, extraction.ExtractedValue)
    ]
    assert len(wrappers) > 10
    assert [cls.__name__ for cls in wrappers if "normalized" not in cls.model_fields] == []


def test_a_record_with_normalizations_validates(example_paths):
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    entries = [
        MONDO_AD,
        {**MONDO_AD, "vocabulary": "ONVOC", "value": "ONVOC:0000176", "label": "Alzheimer's Disease"},
        {"vocabulary": "MONDO", "status": "absent", "item": 1, "mapping_tool": "m"},
        {"vocabulary": "MONDO", "status": "unmapped", "item": 2, "head": "mild cognitive impairment",
         "qualifiers": ["amnestic"], "confidence": 0.88, "mapping_tool": "m"},
        {"vocabulary": "canonical task", "status": "mapped", "value": "hedtsk_oddball", "rollup": False,
         "also_read": ["description", "conditions"], "mapping_tool": "m"},
    ]
    data = _record_with(example_paths, *entries)
    extraction.Study.model_validate(data.get("study") or data)
    validator.validate(data, jsonschema.load("extraction-record"))


@pytest.mark.parametrize(
    "change, field",
    [
        ({"status": "guessed"}, "status"),
        ({"confidence": 1.5}, "confidence"),
        ({"item": -1}, "item"),
        ({"mapping_tool": None}, "mapping_tool"),
        ({"vocabulary": None}, "vocabulary"),
    ],
)
def test_a_malformed_normalization_is_refused(example_paths, change, field):
    entry = {k: v for k, v in {**MONDO_AD, **change}.items() if v is not None}
    data = _record_with(example_paths, entry)
    with pytest.raises(ValidationError, match=field):
        extraction.Study.model_validate(data.get("study") or data)
    validator = pytest.importorskip("jsonschema", reason="optional: JSON Schema validation")
    with pytest.raises(validator.ValidationError):
        validator.validate(data, jsonschema.load("extraction-record"))


def test_the_sibling_slots_are_gone_from_extraction_and_kept_in_storage():
    assert "age_unit_normalized" not in extraction.Group.model_fields
    assert "category_normalized" not in extraction.CategoryDistribution.model_fields
    assert "age_unit_normalized" in storage.Group.model_fields
    assert "category_normalized" in storage.CategoryDistribution.model_fields
