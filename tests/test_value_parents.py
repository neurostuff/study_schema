"""An enum value's parent, the coarser value its LinkML `is_a` names, read from the
generated JSON Schemas."""

import pytest

from study_schema.jsonschema import load, value_children, value_parents
from study_schema.models import extraction, storage

MODALITY_PARENTS = {
    "fMRI": "MRI",
    "sMRI": "MRI",
    "dMRI": "MRI",
    "MRS": "MRI",
    "iEEG": "other",
    "CT": "other",
    "physiological": "other",
}


@pytest.mark.parametrize("name", ["storage-study", "extraction-record"])
def test_modality_values_name_their_parents(name):
    assert value_parents(name, "Modality") == MODALITY_PARENTS
    assert value_children(name, "Modality") == {
        "MRI": ("fMRI", "sMRI", "dMRI", "MRS"),
        "other": ("iEEG", "CT", "physiological"),
    }


def test_a_parent_and_its_children_are_values_of_the_enum():
    values = set(load("storage-study")["$defs"]["Modality"]["enum"])
    assert set(MODALITY_PARENTS) | set(MODALITY_PARENTS.values()) <= values


def test_the_extraction_and_storage_modalities_are_one_vocabulary():
    assert [m.value for m in extraction.Modality] == [m.value for m in storage.Modality]


def test_an_enum_whose_values_refine_nothing_has_no_parents():
    assert value_parents("storage-study", "LicenseId") == {}
    assert value_children("storage-study", "LicenseId") == {}


def test_an_unknown_enum_is_an_error():
    with pytest.raises(KeyError):
        value_parents("storage-study", "NoSuchEnum")
