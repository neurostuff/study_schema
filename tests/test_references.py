"""The JSON Schemas say which string properties hold another entity's local_id."""

import pytest

from study_schema import jsonschema as js

COORDINATE_SET_TABLE = ("CoordinateSet", "table", "Table", False)


def test_references_are_sorted_by_class_and_slot(monkeypatch):
    def prop(target):
        return {"type": "string", "x-reference": target}

    document = {
        "$defs": {
            "Zed": {"properties": {"b": prop("Device"), "a": prop("Device")}},
            "Alpha": {"properties": {"c": prop("Device")}},
        }
    }
    monkeypatch.setattr(js, "load", lambda name: document)
    assert [(row.cls, row.slot) for row in js.references("parsed-paper")] == [
        ("Alpha", "c"),
        ("Zed", "a"),
        ("Zed", "b"),
    ]


@pytest.mark.parametrize("name", ["extraction-record", "parsed-paper", "coordinate-parse"])
def test_references_match_pinned_rows(name):
    assert set(js.references(name)) == PINNED_REFERENCES[name]


def test_storage_study_references_are_the_extraction_rows_plus_one():
    rows = set(js.references("storage-study"))
    assert NEUROSTORE_REFERENCES <= rows
    assert rows - NEUROSTORE_REFERENCES == {COORDINATE_SET_TABLE}


@pytest.mark.parametrize("name", js.NAMES)
def test_reference_targets_are_classes_of_the_same_schema(name):
    schema = js.load(name)
    for cls, slot, target, _ in js.references(name):
        assert target in schema["$defs"], f"{name}: {cls}.{slot} -> {target}"


@pytest.mark.parametrize("name", js.NAMES)
def test_no_property_inlining_an_object_carries_a_reference(name):
    schema = js.load(name)
    for cls, definition in schema["$defs"].items():
        for slot, prop in definition.get("properties", {}).items():
            ref = prop.get("$ref") or prop.get("items", {}).get("$ref")
            if ref and schema["$defs"][ref.rsplit("/", 1)[-1]].get("type") == "object":
                assert "x-reference" not in prop, f"{name}: {cls}.{slot}"


# (class, slot, target, multivalued)
# A frozen snapshot of neurostore's manifest,
# store/backend/neurostore/review/schema/references.json at study_schema 2cc7bec.
# Update it only when the schema's references change.
NEUROSTORE_REFERENCES = {
    ("Acquisition", "device", "Device", False),
    ("Analysis", "acquisitions", "Acquisition", True),
    ("Analysis", "assessments", "Assessment", True),
    ("Analysis", "defines_regions", "Region", True),
    ("Analysis", "inference_settings", "InferenceSettings", False),
    ("Analysis", "measure", "Measure", False),
    ("Analysis", "mirror_of", "Analysis", False),
    ("Analysis", "model_estimation", "ModelEstimation", False),
    ("Analysis", "regions", "Region", True),
    ("Analysis", "tables", "Table", True),
    ("Analysis", "tasks", "Task", True),
    ("AnalysisGroup", "group", "Group", False),
    ("Cell", "term", "ModelTerm", False),
    ("ConnectivityDetails", "seed_regions", "Region", True),
    ("ConnectivityDetails", "target_regions", "Region", True),
    ("ConnectivityEdge", "source_region", "Region", False),
    ("ConnectivityEdge", "target_region", "Region", False),
    ("CoordinateSet", "analysis", "Analysis", False),
    ("CoordinateSet", "regions", "Region", True),
    ("DecodingClass", "condition", "Condition", False),
    ("EEG", "device", "Device", False),
    ("FNIRS", "device", "Device", False),
    ("FactorLevel", "arms", "Arm", True),
    ("FactorLevel", "conditions", "Condition", True),
    ("FactorLevel", "groups", "Group", True),
    ("FactorLevel", "regions", "Region", True),
    ("FactorLevel", "timepoints", "Timepoint", True),
    ("Group", "arm", "Arm", False),
    ("Group", "diagnostic_instrument", "Assessment", True),
    ("InferenceSettings", "correction_regions", "Region", True),
    ("LatentDecompositionDetails", "second_block_assessments", "Assessment", True),
    ("MRI", "device", "Device", False),
    ("Mediation", "mediator", "ModelTerm", False),
    ("ModelEstimation", "inputs_from", "ModelEstimation", True),
    ("ModelEstimation", "preprocessing", "Preprocessing", True),
    ("ModelTerm", "assessment", "Assessment", False),
    ("ModelTerm", "interaction_with", "ModelTerm", True),
    ("ModelTerm", "region", "Region", False),
    ("OtherModality", "device", "Device", False),
    ("PET", "device", "Device", False),
    ("Task", "acquisitions", "Acquisition", True),
}

CITATION_REFERENCES = {("Citation", "references", "Reference", True)}

PINNED_REFERENCES = {
    "extraction-record": NEUROSTORE_REFERENCES,
    "parsed-paper": CITATION_REFERENCES,
    "coordinate-parse": CITATION_REFERENCES,
}
