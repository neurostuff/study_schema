"""The vocabulary manifest: every entry loads, and the legacy strings records carry read back."""

import pytest

from study_schema import vocabularies as v
from study_schema.models import extraction

NAMES = [
    "mondo",
    "onvoc",
    "canonical_task",
    "task_family",
    "condition_atom",
    "condition_phase",
    "contrast_key",
    "contrast_set",
    "contrast_set_orientation",
    "medical_role",
]


def test_the_manifest_loads_with_one_entry_per_name():
    assert [x.name for x in v.vocabularies()] == NAMES


@pytest.mark.parametrize("name", NAMES)
def test_each_entry_is_pinned(name):
    entry = v.vocabulary(name)
    # an external ontology is pinned by its download; a pipeline vocabulary by its source files
    assert entry.download or entry.sources
    assert entry.release
    h = entry.hierarchy
    if h is None:
        return
    assert h.parent_vocabulary is None or h.parent_vocabulary in NAMES
    assert (h.separator is not None) == (h.kind == "prefix")
    assert (h.field is not None) == (h.kind == "field")


def test_the_ontologies_are_downloads():
    assert v.vocabulary("mondo").release == "2026-09-01"
    assert "/releases/2026-09-01/" in v.vocabulary("mondo").download.url
    assert v.vocabulary("onvoc").release == "1.0.0"


def test_a_contrast_set_names_its_family():
    families = v.vocabulary("task_family").values
    sep = v.vocabulary("contrast_set").hierarchy.separator
    assert "perceptual_judgment#1589".split(sep)[0] in families


@pytest.mark.parametrize(
    "legacy, name",
    [
        ("MONDO", "mondo"),
        ("ONVOC", "onvoc"),
        ("astra canonical task", "canonical_task"),
        ("astra task family", "task_family"),
        ("astra condition atom: congruency", "condition_atom"),
        ("astra condition atom", "condition_atom"),
        ("astra condition phase", "condition_phase"),
        ("astra contrast key", "contrast_key"),
        ("ns-pond contrast set fit-ed5ac7b08c20", "contrast_set"),
        ("ns-pond contrast set orientation fit-ed5ac7b08c20", "contrast_set_orientation"),
        ("astra medical role", "medical_role"),
    ],
)
def test_legacy_strings_read_back(legacy, name):
    assert v.for_legacy(legacy).name == name


@pytest.mark.parametrize("other", ["AgeUnit", "astra condition atom: ", "ns-pond contrast set"])
def test_other_strings_are_not_legacy(other):
    assert v.for_legacy(other) is None


def test_an_unknown_name_is_a_key_error():
    with pytest.raises(KeyError):
        v.vocabulary("MONDO")


def test_the_schema_lists_every_name():
    description = extraction.Normalization.model_fields["vocabulary"].description
    assert all(name in description for name in NAMES)
