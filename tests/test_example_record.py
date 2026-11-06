"""The worked example record is a finished extraction of the example paper's text.

The models and the JSON Schema accept a record whose every slot is null, so
`test_examples_validate` alone cannot tell a finished record from the skeleton. These
tests read the record against the text it was extracted from, which only pondie's
validator does otherwise: evidence invariants, span offsets, source hash, references.
Rules that read across classes (a cell's level against its term's levels, the effect
kind a set of cells derives) stay with pondie's record rules.
"""

import hashlib
import json

import pytest

from conftest import load
from study_schema.models import extraction
from study_schema.models.paper_parse import CoordinateParse

# Slots whose value is the local_id of an entity declared elsewhere in the record.
REFERENCE_SLOTS = {
    "analysis", "conditions", "group", "inference_settings", "measure",
    "model_estimation", "tables", "tasks", "term",
}


@pytest.fixture
def record(example_paths):
    return load(example_paths["record"])


@pytest.fixture
def text(example_paths):
    return (example_paths["bundle"] / "processed" / "pubget" / "text.txt").read_text(encoding="utf-8")


def walk(node, path="record"):
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}[{index}]")


def wrappers(record):
    return [(p, n) for p, n in walk(record) if isinstance(n, dict) and "extraction_status" in n]


def test_no_slot_is_null(record):
    # An unknown is `extraction_status: not_reported` or an absent key, never null.
    assert [p for p, n in walk(record) if n is None] == []


def test_every_entity_list_is_filled_or_declared_empty(record):
    assert set(record) >= set(extraction.Study.model_fields) - {"stable_id"}
    assert record["analyses"] and record["tables"] and record["groups"]


def test_not_reported_and_extracted_carry_what_the_status_requires(record):
    found = wrappers(record)
    assert found
    for path, node in found:
        evidence = node["evidence"]
        if node["extraction_status"] == "not_reported":
            assert "value" not in node and evidence == {"status": "not_applicable"}, path
        else:
            assert node["value_source"] in {"reported", "generated"}, path
            assert evidence["status"] == "present" and evidence["sets"], path
            assert "unreported_reason" not in node, path


def test_every_span_is_the_text_at_its_offsets(record, text):
    spans = [n for p, n in walk(record) if p.endswith("]") and isinstance(n, dict) and set(n) == {"text", "start_char", "end_char"}]
    assert len(spans) > 20
    for span in spans:
        assert text[span["start_char"]:span["end_char"]] == span["text"]


def test_a_reported_value_is_quoted_in_its_own_evidence(record):
    for path, node in wrappers(record):
        if node.get("value_source") != "reported" or not isinstance(node["value"], str):
            continue
        quoted = " ".join(s["text"] for e in node["evidence"]["sets"] for s in e["spans"])
        assert node["value"].lower() in quoted.lower(), path


def test_source_text_hash_is_the_text_the_parse_hashes(record, text, example_paths):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    parse = CoordinateParse.model_validate(load(example_paths["coordinate_parse"]))
    assert record["extraction_metadata"]["source_text_hash"] == digest == parse.text_sha256


def test_every_reference_names_a_declared_entity(record):
    declared = {n["local_id"] for _, n in walk(record) if isinstance(n, dict) and "local_id" in n}
    for path, node in walk(record):
        if not isinstance(node, dict):
            continue
        for slot in REFERENCE_SLOTS & set(node):
            value = node[slot]
            for ref in value if isinstance(value, list) else [value]:
                if isinstance(ref, str):
                    assert ref in declared, f"{path}.{slot} -> {ref}"


def test_each_parsed_analysis_is_an_analysis_of_the_record(record, example_paths):
    parse = CoordinateParse.model_validate(load(example_paths["coordinate_parse"]))
    names = {a["name"]["value"] for a in record["analyses"]}
    assert {a.name for a in parse.analyses} <= names
    assert {a.table_id for a in parse.analyses} <= {t["local_id"] for t in record["tables"]}


def test_a_null_result_is_an_analysis_with_no_table(record):
    outcomes = {a["name"]["value"]: a["outcome"]["value"] for a in record["analyses"]}
    assert outcomes == {"Gain > Neutral": "significant_effect", "Loss > Neutral": "no_significant_effect"}
    loss = next(a for a in record["analyses"] if a["name"]["value"] == "Loss > Neutral")
    assert "tables" not in loss


def test_each_cell_names_a_level_its_term_declares(record):
    terms = {t["local_id"]: {lv["level"]["value"] for lv in t["levels"]}
             for m in record["model_estimations"] for t in m["terms"]}
    for analysis in record["analyses"]:
        for cell in analysis["effect"]["cells"]:
            assert cell["level"]["value"] in terms[cell["term"]]
