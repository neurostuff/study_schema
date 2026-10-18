"""JSON Schema properties follow the order the LinkML declares its slots."""

import json

import pytest

pytest.importorskip("linkml", reason="regeneration needs the `generate` extra")


def _documents(generator):
    rendered = generator.render()
    for contract in generator.CONTRACTS:
        view = generator._view(contract)
        for name in contract.roots:
            path = generator.PACKAGE / "jsonschema" / f"{name}.schema.json"
            yield name, view, json.loads(rendered[path])


def test_group_properties_follow_declared_order(generator):
    documents = {name: (view, doc) for name, view, doc in _documents(generator)}
    view, document = documents["extraction-record"]
    declared = [slot.name for slot in view.class_induced_slots("Group")]
    assert list(document["$defs"]["Group"]["properties"]) == declared


def test_every_class_follows_declared_order(generator):
    checked = 0
    for name, view, document in _documents(generator):
        for cls, definition in generator._class_definitions(document, view):
            properties = definition.get("properties")
            if not properties:
                continue
            declared = [slot.name for slot in view.class_induced_slots(cls)]
            assert list(properties) == declared, f"{name}: {cls}"
            checked += 1
    assert checked


def test_generation_is_deterministic(generator):
    assert generator.render() == generator.render()
