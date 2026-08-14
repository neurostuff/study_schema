"""Assemble an extraction record from extractor payloads, resolving quotes to offsets.

The extractor emits verbatim quotes rather than character offsets, because a
language model cannot count characters reliably. This module locates each quote
in the normalized source text and rewrites

    {"evidence": {"status": "present", "sets": [{"quotes": ["..."]}]}}

into the schema's shape

    {"evidence": {"status": "present", "sets": [{"spans": [{text, start_char, end_char}]}]}}

Every emitted span is verified against the source, so a record that builds is a
record whose offsets are correct by construction. Quotes that cannot be located
are reported and their field is downgraded rather than silently dropped.

Usage:
    python review/build_record.py --paper 2abntY3hQSyq \
        --text review/texts/2abntY3hQSyq/processed/pubget/text.txt \
        --payloads review/payloads/2abntY3hQSyq \
        --out review/examples/2abntY3hQSyq.extraction.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import known_gaps
import spans as span_tools
import text_index

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import schema_utils  # noqa: E402  (repo root is added above)

EXTRACTION_SCHEMA = Path(__file__).resolve().parent.parent / "neuroimaging-study-extraction.yaml"

def _entity_lists() -> dict[str, str]:
    """Payload key -> dotted path to the Study attribute holding the inlined list.

    Derived from the schema rather than written out, because a hardcoded list
    silently drops whatever it has not caught up with: `arms` and `timepoints`
    were added to Study and every intervention and longitudinal paper's payload
    lost them, reported only as an "unexpected payload key" note.

    An extractor emits one file per entity kind, so the payload key is a bare
    entity name however deep the schema puts the list. Most sit directly on Study
    and the mapping is the identity; `arms` and `timepoints` sit one level down
    under `design`, so a single level of inlined singleton is followed. Deeper
    nesting is not: the payload would need a path of its own to be unambiguous.
    """

    classes = schema_utils.load_imported_classes(EXTRACTION_SCHEMA)
    study = schema_utils.attributes_for(classes, "Study")

    def lists_on(attributes: Mapping[str, Any], prefix: str = "") -> dict[str, str]:
        return {
            name: f"{prefix}{name}"
            for name, attribute in attributes.items()
            if isinstance(attribute, Mapping) and attribute.get("multivalued")
        }

    found = lists_on(study)
    for name, attribute in study.items():
        if not isinstance(attribute, Mapping) or attribute.get("multivalued"):
            continue
        if attribute.get("inlined") is not True:
            continue
        nested = attribute.get("range")
        if nested not in classes:
            continue
        for key, path in lists_on(
            schema_utils.attributes_for(classes, nested), f"{name}."
        ).items():
            found.setdefault(key, path)
    return found


_ENTITY_LISTS = _entity_lists()

# Keys that are extractor scaffolding, not schema content.
_SCAFFOLDING = {"cross_reference_notes"}

# Payload filename holding local_id reconciliation, excluded from the merge.
_ALIAS_FILE = "aliases.json"


@dataclass
class BuildReport:
    resolved_exact: int = 0
    resolved_tolerant: int = 0
    failures: list[str] = field(default_factory=list)
    fields_total: int = 0
    fields_extracted: int = 0
    fields_not_reported: int = 0
    fields_evidence_present: int = 0
    fields_evidence_not_found: int = 0
    downgraded: list[str] = field(default_factory=list)

    #: The repairs, one list each, and the two hard faults. Counted rather than printed and
    #: forgotten: the counts are how a prompt regression becomes visible, and nothing
    #: downstream could read them while `build()` wrote them straight to stdout.
    repaired_wrappers: list[str] = field(default_factory=list)
    derived_acquisition_types: list[str] = field(default_factory=list)
    derived_spaces: list[str] = field(default_factory=list)
    listified: list[str] = field(default_factory=list)
    listified_scalars: list[str] = field(default_factory=list)
    aligned_levels: list[str] = field(default_factory=list)
    dangling: list[str] = field(default_factory=list)
    payload_notes: list[str] = field(default_factory=list)

    #: Every repair the builder performed, for the one-line report and the threshold.
    @property
    def repairs(self) -> int:
        return (len(self.repaired_wrappers) + len(self.derived_acquisition_types)
                + len(self.listified) + len(self.listified_scalars) + len(self.aligned_levels))

    def summary(self) -> str:
        return (
            f"fields={self.fields_total} "
            f"(extracted={self.fields_extracted}, not_reported={self.fields_not_reported})\n"
            f"evidence: present={self.fields_evidence_present}, "
            f"not_found={self.fields_evidence_not_found}\n"
            f"spans: exact={self.resolved_exact}, whitespace-tolerant={self.resolved_tolerant}, "
            f"unresolved={len(self.failures)}\n"
            f"downgraded fields={len(self.downgraded)}\n"
            f"repairs={self.repairs} (wrappers={len(self.repaired_wrappers)}, "
            f"acquisition_type={len(self.derived_acquisition_types)}, "
            f"listified={len(self.listified)}, scalars={len(self.listified_scalars)}, "
            f"levels={len(self.aligned_levels)})"
        )


def _is_field(node: Any) -> bool:
    return isinstance(node, dict) and "extraction_status" in node


#: The only two things `extraction_status` may say.
_STATUSES = ("extracted", "not_reported")


def repair_wrappers(node: Any, path: str = "") -> list[str]:
    """Put a collapsed ExtractedValue back together, and report every one.

    The wrapper is `{"extraction_status": "extracted", "value": X}`, and the model
    intermittently writes X into the status slot instead -- `"extraction_status":
    "undirected"` for a direction, `"extraction_status": 0.05` for an alpha level. Every
    such field is invalid against the schema, and the numeric ones additionally break
    `ls.py export`, whose task contract requires `llm_status` to be a string.

    The repair is unambiguous, which is why it is done rather than reported: the status
    slot has exactly two legal strings, so anything else in it was never a status. A
    value already sitting in `value` is kept and only the status is corrected; otherwise
    the misplaced payload is moved into `value`.

    Deliberately here and not in `extract_record.normalize`: it has to hold for payloads
    already on disk, so a rebuild fixes them without paying for the extraction again.
    """

    repaired: list[str] = []
    if isinstance(node, dict):
        status = node.get("extraction_status") if "extraction_status" in node else None

        if "extraction_status" in node and status not in _STATUSES:
            if "value" not in node or node["value"] in (None, ""):
                node["value"] = status
            node["extraction_status"] = "extracted"
            node.setdefault("value_source", "reported")
            repaired.append(f"{path or '<root>'}: status held {status!r}")
        for key, value in node.items():
            repaired += repair_wrappers(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            repaired += repair_wrappers(value, f"{path}[{index}]")
    return repaired


def derive_acquisition_types(body: dict[str, Any]) -> list[str]:
    """Fill `Acquisition.acquisition_type` from each record's modality.

    The schema says of this slot: "Derived by the mapper from the modality value's
    `instantiates`, never extracted" (`acquisition.yaml`) -- so the builder owes it, and
    an extraction that leaves it out is not wrong, it is being taken at its word. Without
    it the record resolves only to the base `Acquisition`, where every modality-specific
    parameter the model *did* extract is undeclared: one omission, seven errors.

    The mapping is read out of the `Modality` enum rather than written down here, so a
    new permissible value arrives with its own subclass already attached.
    """

    enums = schema_utils.load_imported_classes(EXTRACTION_SCHEMA, key="enums")
    modality = (enums.get("Modality") or {}).get("permissible_values") or {}
    instantiates = {
        name: str(spec["instantiates"][0]).split(":")[-1]
        for name, spec in modality.items()
        if isinstance(spec, Mapping) and spec.get("instantiates")
    }

    filled: list[str] = []
    for index, acquisition in enumerate(body.get("acquisitions") or []):
        if not isinstance(acquisition, dict) or acquisition.get("acquisition_type"):
            continue
        value = acquisition.get("modality")
        if isinstance(value, Mapping):
            value = value.get("value")
        target = instantiates.get(value)
        if target:
            acquisition["acquisition_type"] = target
            filled.append(f"acquisitions[{index}]: {value} -> {target}")
    return filled


def _resolve_field(
    node: dict[str, Any], normalized: str, folded: str, path: str, report: BuildReport
) -> None:
    """Rewrite one FIELD object's evidence quotes into verified spans, in place."""

    report.fields_total += 1
    status = node.get("extraction_status")
    if status == "extracted":
        report.fields_extracted += 1
    elif status == "not_reported":
        report.fields_not_reported += 1

    evidence = node.get("evidence")
    if not isinstance(evidence, dict):
        return

    raw_sets = evidence.get("sets")
    if not isinstance(raw_sets, list):
        # An extracted field may not claim its evidence is not_applicable: the value is
        # asserted, so support for it was either found or not. The branch below enforces
        # this once a set has been tried; a field that arrived with no `sets` at all has
        # never been through it, and used to keep the contradiction all the way into the
        # record.
        if status == "extracted" and evidence.get("status") == "not_applicable":
            evidence["status"] = "not_found"
            report.downgraded.append(path)
        if evidence.get("status") == "not_found":
            report.fields_evidence_not_found += 1
        return

    rebuilt: list[dict[str, Any]] = []
    for index, evidence_set in enumerate(raw_sets):
        quotes = evidence_set.get("quotes") if isinstance(evidence_set, dict) else None
        if not isinstance(quotes, list):
            continue
        resolved: list[dict[str, object]] = []
        for quote in quotes:
            try:
                found = span_tools.resolve(normalized, quote, folded_text=folded)
            except span_tools.SpanResolutionError as error:
                report.failures.append(f"{path} set[{index}]: {error}")
                continue
            if found.exact:
                report.resolved_exact += 1
            else:
                report.resolved_tolerant += 1
            resolved.append(found.as_record())

        # An EvidenceSet requires at least one span (minimum_cardinality: 1), so a
        # set whose every quote failed to resolve cannot be emitted at all.
        if resolved:
            rebuilt.append({"spans": resolved})

    if rebuilt:
        evidence["sets"] = rebuilt
        evidence["status"] = "present"
        report.fields_evidence_present += 1
    else:
        # No usable evidence survived. The value may still be right, so keep it
        # and record that support was not located rather than deleting the field.
        evidence.pop("sets", None)
        if status == "extracted":
            evidence["status"] = "not_found"
            report.fields_evidence_not_found += 1
            report.downgraded.append(path)
        else:
            evidence["status"] = "not_applicable"


def _walk(node: Any, normalized: str, folded: str, path: str, report: BuildReport) -> None:
    if _is_field(node):
        _resolve_field(node, normalized, folded, path, report)
        return
    if isinstance(node, dict):
        for key, value in node.items():
            _walk(value, normalized, folded, f"{path}.{key}" if path else str(key), report)
        return
    if isinstance(node, list):
        for index, value in enumerate(node):
            _walk(value, normalized, folded, f"{path}[{index}]", report)


def merge_payloads(payload_dir: Path) -> dict[str, Any]:
    """Merge extractor payloads into a single Study body.

    Each agent covers a disjoint set of classes, so entity lists concatenate and
    the `study` mapping is a shallow union. Overlapping study attributes are a
    prompt bug; the later payload wins and the collision is reported.
    """

    study: dict[str, Any] = {}
    lists: dict[str, list[Any]] = {}
    collisions: list[str] = []

    for path in sorted(payload_dir.glob("*.json")):
        if path.name == _ALIAS_FILE:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key, value in payload.items():
            if key in _SCAFFOLDING:
                continue
            if key == "study" and isinstance(value, dict):
                for attr, attr_value in value.items():
                    if attr in study:
                        collisions.append(f"{attr} (from {path.name})")
                    study[attr] = attr_value
            elif key in _ENTITY_LISTS and isinstance(value, list):
                lists.setdefault(_ENTITY_LISTS[key], []).extend(value)
            else:
                collisions.append(f"unexpected payload key {key!r} in {path.name}")

    if collisions:
        print("payload merge notes:")
        for note in collisions:
            print(f"  - {note}")

    body = dict(study)
    for attr, items in lists.items():
        if not items:
            continue
        holder = body
        *ancestors, leaf = attr.split(".")
        for step in ancestors:
            holder = holder.setdefault(step, {})
        holder[leaf] = items
    return body


def load_aliases(payload_dir: Path) -> dict[str, str]:
    path = payload_dir / _ALIAS_FILE
    if not path.is_file():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    aliases = document.get("aliases", {})
    return {k: v for k, v in aliases.items() if isinstance(k, str) and isinstance(v, str)}


def apply_aliases(
    body: dict[str, Any], classes: Mapping[str, Any], aliases: dict[str, str]
) -> int:
    """Rewrite cross-reference slots through the alias map, in place.

    Only slots the schema classifies as references are touched, so an alias can
    never corrupt an extracted value that happens to share a string with an id.
    """

    if not aliases:
        return 0
    rewrites = 0

    def visit(node: Any, class_name: str) -> None:
        nonlocal rewrites
        if not isinstance(node, dict) or _is_field(node):
            return
        # A reference inside a self-naming payload -- ConnectivityDetails.seed_regions --
        # is declared on the subclass, so recursing on the declared range leaves it
        # unrewritten and a merge silently keeps pointing at the absorbed local_id.
        class_name = schema_utils.designated_type(classes, node, class_name)
        attributes = schema_utils.attributes_for(classes, class_name)
        for key, value in list(node.items()):
            attribute = attributes.get(key)
            if attribute is None:
                continue
            kind = schema_utils.classify_slot(classes, key, attribute)
            if kind == "reference":
                if isinstance(value, str) and value in aliases:
                    node[key] = aliases[value]
                    rewrites += 1
                elif isinstance(value, list):
                    for index, ref in enumerate(value):
                        if isinstance(ref, str) and ref in aliases:
                            value[index] = aliases[ref]
                            rewrites += 1
            elif kind == "nested":
                target = attribute.get("range")
                if isinstance(target, str):
                    for item in value if isinstance(value, list) else [value]:
                        visit(item, target)

    study_attributes = schema_utils.attributes_for(classes, "Study")
    for attr in _ENTITY_LISTS.values():
        attribute = study_attributes.get(attr)
        target = attribute.get("range") if isinstance(attribute, Mapping) else None
        if isinstance(target, str):
            for entity in body.get(attr, []) or []:
                visit(entity, target)
    return rewrites


def derive_coordinate_spaces(body: dict[str, Any], stage1: Path | None,
                             table_map: Path | None) -> list[str]:
    """Fill `Analysis.coordinate_space` from the space stage 1 read off the table.

    Stage 1 parses a space for every coordinate it extracts and is right about it: across
    ten papers it returned MNI for all 197 points and disagreed with itself on none. The
    stage-3 prompt already injects that space, but as a hint "to confirm, not values to
    copy" -- and the model declines to confirm, leaving the slot unreported on 50 of 57
    analyses. A fact this deterministic should not be routed through a model that has been
    told to distrust it, any more than `Table.caption` is.

    Only fills what the model left empty, and only when every point behind the analysis
    agrees, so a genuinely mixed-space paper still reaches a human.
    """

    if not (stage1 and stage1.is_file() and table_map and table_map.is_file()):
        return []

    parsed = json.loads(stage1.read_text(encoding="utf-8")).get("analyses") or []
    mapping = json.loads(table_map.read_text(encoding="utf-8"))

    spaces_by_table: dict[str, set[str]] = {}
    for analysis in parsed:
        local = mapping.get(analysis.get("table_id"))
        if not local:
            continue
        seen = {p.get("space") for p in analysis.get("points") or [] if p.get("space")}
        spaces_by_table.setdefault(local, set()).update(seen)

    filled: list[str] = []
    for index, analysis in enumerate(body.get("analyses") or []):
        slot = analysis.get("coordinate_space")
        if isinstance(slot, Mapping) and slot.get("extraction_status") == "extracted":
            continue
        spaces = set()
        for table in analysis.get("tables") or []:
            spaces |= spaces_by_table.get(table, set())
        if len(spaces) != 1:
            continue
        space = spaces.pop()
        # `not_found` and not `not_applicable`, which is the state this used to synthesise.
        # An extracted field claiming `not_applicable` is one of the shape errors
        # `_resolve_field` repairs, so the walk rewrote every one of these to `not_found`
        # anyway -- and counted it in `report.downgraded`, which meant that number was
        # dominated by the builder's own output and could not be thresholded on. There is no
        # quote to find for a value read off the table parse, so `not_found` is simply true.
        analysis["coordinate_space"] = {
            "extraction_status": "extracted", "value": space, "value_source": "reported",
            "evidence": {"status": "not_found"},
        }
        filled.append(f"analyses[{index}] -> {space}")
    return filled


def listify_nested(body: dict[str, Any], classes: Mapping[str, Any]) -> list[str]:
    """Wrap a lone object in a list wherever the schema declares a multivalued slot.

    `model_estimations[].terms` is the one that recurs: an estimation with a single
    ModelTerm comes back as the object rather than a list of one. Which of the three
    shapes a slot takes is the most confusable thing in this schema -- the prompt states
    it per line for that reason -- and this is the benign half of getting it wrong, so it
    is repaired here instead of costing a re-extraction.

    Schema-driven, and reference slots are included: a multivalued reference given one
    bare id has the same shape problem.

    Two things the walk has to get right to reach `ConnectivityDetails.seed_regions`, which
    is where 40 of the corpus's shape errors sat. A single-valued nested slot is descended
    into even though it needs no repair itself, because the slots that do are inside it --
    `Analysis.details`, `.effect` and `.inference_settings` are all single-valued. And the
    recursion resolves the type designator, because `details` ranges on the abstract
    AnalysisDetails, whose only attribute is `details_type`; recursing on the declared range
    finds no `seed_regions` to repair and reports nothing.
    """

    fixed: list[str] = []

    def visit(node: Any, class_name: str, path: str) -> None:
        if not isinstance(node, dict) or _is_field(node):
            return
        class_name = schema_utils.designated_type(classes, node, class_name)
        attributes = schema_utils.attributes_for(classes, class_name)
        for key, value in list(node.items()):
            attribute = attributes.get(key)
            if attribute is None:
                continue
            kind = schema_utils.classify_slot(classes, key, attribute)
            if kind not in ("nested", "reference"):
                continue
            if not attribute.get("multivalued"):
                # Nothing to repair on the slot itself, but its contents may need it.
                if kind == "nested" and isinstance(attribute.get("range"), str):
                    visit(value, attribute["range"], f"{path}.{key}")
                continue
            if _is_field(value):
                # A nested record list is not a multivalued scalar and has no
                # `not_reported` form: "no terms" is the empty list, not a wrapper
                # saying so. Where the model wrapped one anyway, take its `value` if it
                # carried the list and drop to empty if it only carried the excuse.
                inner = value.get("value")
                node[key] = inner if isinstance(inner, list) else (
                    [inner] if isinstance(inner, dict) else []
                )
                fixed.append(f"{path}.{key} (unwrapped {value.get('extraction_status')})")
            elif isinstance(value, dict) or (
                kind == "reference" and isinstance(value, str)
            ):
                node[key] = [value]
                fixed.append(f"{path}.{key}")
            if kind == "nested":
                target = attribute.get("range")
                if isinstance(target, str):
                    for index, item in enumerate(node[key] if isinstance(node[key], list)
                                                 else []):
                        visit(item, target, f"{path}.{key}[{index}]")

    study_attributes = schema_utils.attributes_for(classes, "Study")
    for attr in _ENTITY_LISTS.values():
        attribute = study_attributes.get(attr)
        target = attribute.get("range") if isinstance(attribute, Mapping) else None
        if isinstance(target, str):
            for index, entity in enumerate(body.get(attr, []) or []):
                visit(entity, target, f"{attr}[{index}]")
    return fixed


def align_cell_levels(body: dict[str, Any]) -> list[str]:
    """Rewrite a `Cell.level` to the declared `FactorLevel.level` it folds to.

    The join is on the string (extraction-readme.md §3 invariant 3), so `Healthy controls`
    against a declared `healthy controls` is a broken join that no reader would call a
    disagreement. Repaired only where exactly one declared level folds to it: two would make
    the choice a guess, and a guess about which condition was compared is the one thing this
    field must not contain.

    Deliberately narrow. `AD` against a declared `AD group` does *not* fold, and is left for
    `Validator.check_cell_terms` to report -- shortening a level is a claim about the paper,
    not a transcription slip.
    """

    fixed: list[str] = []
    models = {model.get("local_id"): model for model in body.get("model_estimations") or []
              if isinstance(model, Mapping)}

    def terms_in_scope(model_id: Any, seen: set[str] | None = None) -> dict[str, Any]:
        seen = set() if seen is None else seen
        if not isinstance(model_id, str) or model_id in seen:
            return {}
        seen.add(model_id)
        model = models.get(model_id)
        if not isinstance(model, Mapping):
            return {}
        found: dict[str, Any] = {}
        for lower in model.get("inputs_from") or []:
            found.update(terms_in_scope(lower, seen))
        for term in model.get("terms") or []:
            if isinstance(term, Mapping) and isinstance(term.get("local_id"), str):
                found[term["local_id"]] = term
        return found

    for index, analysis in enumerate(body.get("analyses") or []):
        if not isinstance(analysis, Mapping):
            continue
        scope = terms_in_scope(analysis.get("model_estimation"))
        effect = analysis.get("effect")
        cells = effect.get("cells") if isinstance(effect, Mapping) else None
        for position, cell in enumerate(cells or []):
            if not isinstance(cell, Mapping) or not _is_field(cell.get("level")):
                continue
            level = cell["level"].get("value")
            term = scope.get(cell.get("term"))
            if not isinstance(level, str) or not isinstance(term, Mapping):
                continue
            declared = [_field_value(entry.get("level")) for entry in (term.get("levels") or [])
                        if isinstance(entry, Mapping)]
            declared = [name for name in declared if isinstance(name, str)]
            if level in declared:
                continue
            folded = span_tools.fold_label(level)
            matches = [name for name in declared if span_tools.fold_label(name) == folded]
            if len(matches) == 1:
                cell["level"]["value"] = matches[0]
                fixed.append(f"analyses[{index}].effect.cells[{position}].level: "
                             f"{level!r} -> {matches[0]!r}")
    return fixed


def _field_value(node: Any) -> Any:
    return node.get("value") if _is_field(node) else node


def listify_scalars(body: dict[str, Any], classes: Mapping[str, Any]) -> list[str]:
    """Wrap a lone scalar in a list inside an `Extracted<T>List` wrapper.

    The other half of the shape confusion `listify_nested` repairs, one level down. A
    multivalued *source-derived* field is one wrapper whose `value` is a list -- the
    convention extraction-readme.md §2 leads with -- and a model that has just been told
    a wrapper holds "the value" writes the string. `interpretations` is where it recurs.

    Distinct from `listify_nested`, which repairs the slot: here the slot is right and its
    `value` is not, so the wrapper class's own `value` declaration is what decides. Walks
    from Study rather than the entity lists so a wrapper under `design.arms[]` is reached.
    """

    fixed: list[str] = []

    def visit(node: Any, class_name: str, path: str) -> None:
        if not isinstance(node, dict) or _is_field(node):
            return
        class_name = schema_utils.designated_type(classes, node, class_name)
        attributes = schema_utils.attributes_for(classes, class_name)
        for key, value in node.items():
            attribute = attributes.get(key)
            if attribute is None:
                continue
            kind = schema_utils.classify_slot(classes, key, attribute)
            if kind == "evidence" and _is_field(value):
                wrapper = attribute.get("range")
                declared = (schema_utils.attributes_for(classes, wrapper) or {}).get("value", {}) \
                    if isinstance(wrapper, str) else {}
                if not declared.get("multivalued") or "value" not in value:
                    continue
                inner = value["value"]
                # A missing value is a different fault and stays visible as one; only a
                # present scalar is the shape this repairs.
                if inner is not None and not isinstance(inner, list):
                    value["value"] = [inner]
                    fixed.append(f"{path}.{key}")
            elif kind == "nested":
                target = attribute.get("range")
                if isinstance(target, str):
                    for index, item in enumerate(value if isinstance(value, list) else [value]):
                        suffix = f"[{index}]" if isinstance(value, list) else ""
                        visit(item, target, f"{path}.{key}{suffix}")

    visit(body, "Study", "Study")
    return fixed


def check_local_ids(body: dict[str, Any], classes: Mapping[str, Any]) -> list[str]:
    """Verify that every cross-reference resolves to a declared local_id.

    Which slots are cross-references is derived from the schema rather than
    hardcoded: a slot is a reference when its range is a native string and it is
    not local_id. That distinction matters because sibling slots differ --
    PredictorSource.group is a local_id string while PredictorSource.other is an
    ExtractedString, and Predictor.source is a nested object, not a reference.
    """

    # Collected by walking, not by iterating the Study lists: ModelTerm lives under
    # `model_estimations[].terms` and Condition under `tasks[].conditions`, so a
    # top-level sweep declares neither and every `Cell.term` and
    # `FactorLevel.conditions` reference reads as dangling when it is in fact fine.
    declared: set[str] = set()
    # Counted as well as collected: a reference resolves to a *set* membership, so two
    # entities sharing a local_id both "resolve" and nothing downstream can tell which
    # one a Cell.term meant. Sibling model estimations that share covariate names --
    # term_age, term_sex -- produce this without anything looking wrong.
    times: dict[str, int] = {}

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            if _is_field(node):
                return
            if isinstance(node.get("local_id"), str):
                declared.add(node["local_id"])
                times[node["local_id"]] = times.get(node["local_id"], 0) + 1
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(body)
    problems: list[str] = [
        f"local_id {name!r} is declared {count} times; every reference to it is ambiguous"
        for name, count in sorted(times.items()) if count > 1
    ]

    def visit(node: Any, class_name: str, path: str) -> None:
        if not isinstance(node, dict) or _is_field(node):
            return
        # Without resolving the designator, every reference declared on a payload subclass
        # -- the seed and target regions of a ConnectivityDetails -- is never visited, so a
        # dangling one reads as fine.
        class_name = schema_utils.designated_type(classes, node, class_name)
        attributes = schema_utils.attributes_for(classes, class_name)
        for key, value in node.items():
            attribute = attributes.get(key)
            if attribute is None:
                continue
            here = f"{path}.{key}"
            kind = schema_utils.classify_slot(classes, key, attribute)
            if kind == "reference":
                refs = [value] if isinstance(value, str) else value if isinstance(value, list) else []
                for ref in refs:
                    if isinstance(ref, str) and ref and ref not in declared:
                        problems.append(f"{here} -> unknown local_id {ref!r}")
            elif kind == "nested":
                target = attribute.get("range")
                if isinstance(target, str):
                    for index, item in enumerate(value if isinstance(value, list) else [value]):
                        suffix = f"[{index}]" if isinstance(value, list) else ""
                        visit(item, target, f"{here}{suffix}")

    # From Study rather than from the entity lists, so that references living under a
    # non-list slot -- `design.arms[].`, and anything added there later -- are checked
    # too. `_ENTITY_LISTS` holds dotted paths that `body.get()` cannot resolve.
    visit(body, "Study", "Study")
    return problems


def build(
    paper_id: str,
    text_path: Path,
    payload_dir: Path,
    extractor_model: str,
    extractor_version: str,
    extraction_date: str,
    stage1: Path | None = None,
    table_map: Path | None = None,
) -> tuple[dict[str, Any], BuildReport]:
    normalized, digest, sections = text_index.load(text_path)
    folded = span_tools.fold(normalized)
    report = BuildReport()

    classes = schema_utils.load_imported_classes(EXTRACTION_SCHEMA)
    body = merge_payloads(payload_dir)
    # Nothing here prints. Every repair is the model getting the wrapper shape wrong or the
    # builder paying a debt the schema assigned it, and the counts are how a prompt
    # regression becomes visible -- which they cannot be while they go straight to stdout
    # for a reader to notice or not. `main()` is the only formatter, and `--strict`
    # thresholds on these same numbers.
    rewrites = apply_aliases(body, classes, load_aliases(payload_dir))
    if rewrites:
        report.payload_notes.append(
            f"reconciled {rewrites} cross-reference(s) through aliases.json")

    report.repaired_wrappers += repair_wrappers(body)
    report.derived_acquisition_types += derive_acquisition_types(body)
    report.derived_spaces += derive_coordinate_spaces(body, stage1, table_map)
    report.listified += listify_nested(body, classes)

    # After listify_nested, so a wrapper that had to be unwrapped out of a nested slot is
    # already gone and cannot be mistaken for a multivalued scalar.
    report.listified_scalars += listify_scalars(body, classes)

    # After listify_nested, which is what makes `terms` a list to walk.
    report.aligned_levels += align_cell_levels(body)

    _walk(body, normalized, folded, "", report)

    record: dict[str, Any] = {
        # Required on Study, and not something a model reads off the page: the paper's
        # corpus id is what the record is keyed by, so the builder supplies it.
        "local_id": paper_id,
        "extraction_metadata": {
            "extractor_model": extractor_model,
            "extractor_version": extractor_version,
            "source_text_hash": digest,
            "extraction_date": extraction_date,
            "paper_sections": [section.as_record() for section in sections],
        }
    }
    record.update(body)

    # Integrity gate: nothing leaves this function unless every span addresses the
    # document it claims to.
    for evidence_set in _iter_sets(record):
        for span in evidence_set.get("spans", []):
            span_tools.verify(normalized, span)

    return record, report


def _iter_sets(node: Any):
    if isinstance(node, dict):
        if "spans" in node and isinstance(node["spans"], list):
            yield node
        for value in node.values():
            yield from _iter_sets(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_sets(value)


def _report_lines(label: str, lines: Sequence[str], show: int = 5) -> None:
    """One block per repair or fault, headed by its count.

    Truncated because a paper can carry forty of one kind and the count is the part that
    matters; the rest are in the record.
    """

    if not lines:
        return
    plural = "" if len(lines) == 1 else "s"
    print(f"\n{len(lines)} {label}{plural}:")
    for line in lines[:show]:
        print(f"  - {line}")
    if len(lines) > show:
        print(f"  ... and {len(lines) - show} more")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", required=True, help="neurostore id")
    parser.add_argument("--text", required=True, type=Path)
    parser.add_argument("--payloads", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--extractor-model", default="claude-opus-5")
    parser.add_argument("--extractor-version", default="review-bootstrap-0.1.0")
    parser.add_argument("--extraction-date", default="2026-08-02")
    parser.add_argument("--stage1", type=Path,
                        help="stage1/analyses.json, for the coordinate space")
    parser.add_argument("--tables", type=Path,
                        help="stage1/table-map.json, pairing pubget tables to Table ids")
    parser.add_argument("--known-gaps", type=Path, default=known_gaps.DEFAULT,
                        help="allowlist of findings a reviewer has accepted; see "
                             "review/known-gaps.yaml")
    parser.add_argument("--strict", action="store_true",
                        help="also fail on the soft class: unresolved quotes, evidence "
                             "downgrades and repairs above --max-unresolved/--max-repaired")
    parser.add_argument("--max-unresolved", type=float, default=0.10,
                        help="fraction of extracted fields whose quote may go unresolved")
    parser.add_argument("--max-repaired", type=float, default=0.10,
                        help="fraction of extracted fields the builder may repair")
    args = parser.parse_args()

    record, report = build(
        args.paper,
        args.text,
        args.payloads,
        args.extractor_model,
        args.extractor_version,
        args.extraction_date,
        args.stage1,
        args.tables,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    classes = schema_utils.load_imported_classes(EXTRACTION_SCHEMA)
    gaps = known_gaps.load(args.known_gaps, args.paper)
    report.dangling, suppressed = known_gaps.partition(
        check_local_ids(record, classes), gaps)

    print(f"\n{args.paper}: wrote {args.out}")
    for note in report.payload_notes:
        print(note)
    print(report.summary())
    _report_lines("repaired collapsed ExtractedValue wrapper", report.repaired_wrappers)
    _report_lines("derived acquisition_type", report.derived_acquisition_types)
    _report_lines("derived coordinate_space", report.derived_spaces)
    _report_lines("wrapped a lone object in a list", report.listified)
    _report_lines("wrapped a lone scalar in a list", report.listified_scalars)
    _report_lines("aligned a cell level to its declared FactorLevel", report.aligned_levels)
    _report_lines("dangling cross-reference", report.dangling)
    _report_lines("unresolved quote", report.failures)
    if suppressed:
        print(f"\n{len(suppressed)} finding(s) suppressed by {args.known_gaps.name}:")
        for finding in suppressed[:5]:
            print(f"  - {finding}")
        if len(suppressed) > 5:
            print(f"  ... and {len(suppressed) - 5} more")

    # Two severity classes. A dangling or duplicated local_id means the record is
    # internally broken -- a reference resolves to nothing, or to two things, and nothing
    # downstream can use it -- so it fails whatever the flags say. Unresolved quotes,
    # downgraded evidence and repairs are quality signals on a usable record, so they fail
    # only under --strict and only past a threshold: two unresolved quotes is normal and
    # forty is a regression. Expressed as a fraction of the fields actually extracted, so
    # the bound scales with the paper rather than with its length.
    if report.dangling:
        print(f"\nFAILED: {len(report.dangling)} broken cross-reference(s)", file=sys.stderr)
        return 1
    if args.strict:
        denominator = max(report.fields_extracted, 1)
        for label, count, bound in (
            ("unresolved quotes", len(report.failures), args.max_unresolved),
            ("builder repairs", report.repairs, args.max_repaired),
        ):
            if count / denominator > bound:
                print(f"\nFAILED --strict: {count} {label} is "
                      f"{count / denominator:.0%} of {denominator} extracted fields, "
                      f"over the {bound:.0%} bound", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
