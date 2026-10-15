"""The JSON Schemas say which slots together identify one item of a class."""

import pytest

from study_schema import jsonschema as js

# A golden check of the YAML, not a sync with neurostore: these pins are neurostore's
# DECLARED_KEYS in neurostore.study_schema_tools on its review branch. Each key matches a
# unique_keys declaration in neuroimaging-study-storage/analysis.yaml, with its slots in
# declared order.
NEUROSTORE_DECLARED_KEYS = {
    "Cell": (("term", "level"),),
    "AnalysisGroup": (("group",),),
    "ModelTerm": (("name",),),
    "FactorLevel": (("level",),),
}


def test_storage_study_unique_keys_are_neurostore_declared_keys():
    assert js.unique_keys("storage-study") == NEUROSTORE_DECLARED_KEYS


def test_unique_keys_are_sorted_by_class_with_keys_as_tuples(monkeypatch):
    document = {
        "$defs": {
            "Zed": {"x-unique-keys": [["b", "a"], ["c"]]},
            "Keyless": {"properties": {}},
            "Alpha": {"x-unique-keys": [["d"]]},
        }
    }
    monkeypatch.setattr(js, "load", lambda name: document)
    result = js.unique_keys("storage-study")
    assert list(result) == ["Alpha", "Zed"]
    assert result == {"Alpha": (("d",),), "Zed": (("b", "a"), ("c",))}


def test_every_unique_key_slot_is_a_property_of_its_class():
    keyed = {name: js.unique_keys(name) for name in js.NAMES}
    assert any(keyed.values())
    for name, classes in keyed.items():
        schema = js.load(name)
        for cls, keys in classes.items():
            properties = schema["$defs"][cls]["properties"]
            for key in keys:
                assert key, f"{name}: {cls} has an empty key"
                for slot in key:
                    assert slot in properties, f"{name}: {cls}.{slot}"
