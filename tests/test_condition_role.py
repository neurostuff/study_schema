"""ConditionRole and RiskBasis: how a group stands to each condition it names, and why it is at risk.

Both are vocabularies for optional `normalized` entries, never slots: a record that carries none is
valid, and the normalizer writes them where it can read them.
"""

import typing

from study_schema.models import extraction, storage


def test_the_roles_and_the_bases():
    assert [r.value for r in storage.ConditionRole] == ["none", "primary", "comorbid", "at_risk"]
    assert [b.value for b in storage.RiskBasis] == [
        "familial", "genetic", "clinical", "biomarker", "condition",
        "exposure", "behavioral", "trait",
    ]


def test_no_field_requires_them():
    """They name `Normalization.vocabulary` values, so no model field ranges over them."""
    for module in (storage, extraction):
        for model in vars(module).values():
            if isinstance(model, type) and hasattr(model, "model_fields"):
                for name, field in model.model_fields.items():
                    text = str(typing.get_type_hints(model).get(name, field.annotation))
                    assert "ConditionRole" not in text and "RiskBasis" not in text, (model, name)


def test_a_risk_and_its_bases_are_normalization_entries():
    entries = [
        {"vocabulary": "MONDO", "status": "mapped", "value": "MONDO:0004975", "mapping_tool": "t"},
        {"vocabulary": "ConditionRole", "status": "mapped", "value": "at_risk", "mapping_tool": "t"},
        {"vocabulary": "RiskBasis", "status": "mapped", "value": "familial", "mapping_tool": "t"},
        {"vocabulary": "RiskBasis", "status": "mapped", "value": "genetic", "mapping_tool": "t"},
    ]
    for e in entries:
        extraction.Normalization.model_validate(e)
    assert {e["value"] for e in entries[1:]} <= {v.value for v in (*storage.ConditionRole, *storage.RiskBasis)}
