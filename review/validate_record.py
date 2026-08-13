#!/usr/bin/env python3
"""Validate an extraction record against the extraction schema.

Enforces LinkML structure (declared attributes, required slots, ranges,
multivalued shape), the class `rules` the storage schema states, and the
invariants in the extraction schema header:

  * extraction_status: not_reported  =>  value omitted, evidence.status not_applicable
  * evidence.status: present         =>  at least one set, each with at least one span
  * every span satisfies text == source[start_char:end_char]

Structure is read from the YAML directly; only the rules need linkml, imported
where they are loaded so the rest runs without it.

Usage:
    python review/validate_record.py \
        --record review/examples/2abntY3hQSyq.extraction.json \
        --text review/texts/2abntY3hQSyq/processed/pubget/text.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import known_gaps
import spans as span_tools
import text_index

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import schema_utils  # noqa: E402  (repo root is added above)

ROOT = Path(__file__).resolve().parent.parent
EXTRACTION_SCHEMA = ROOT / "neuroimaging-study-extraction.yaml"
#: Rules are read from storage: `gen_extraction_schema.py` drops `rules` on the way
#: to extraction, so this is the only statement of them.
STORAGE_SCHEMA = ROOT / "neuroimaging-study-storage.yaml"

_RULES: dict[str, list[Mapping[str, Any]]] | None = None


def storage_rules() -> Mapping[str, list[Mapping[str, Any]]]:
    """Class name -> its rules, resolved through linkml so imports are followed.

    Cached: SchemaView takes about a second to walk the eleven modules, and a record
    has hundreds of instances to check.
    """

    global _RULES
    if _RULES is None:
        from linkml_runtime.dumpers import json_dumper
        from linkml_runtime.utils.schemaview import SchemaView

        view = SchemaView(str(STORAGE_SCHEMA))
        _RULES = {
            name: [json_dumper.to_dict(rule) for rule in definition.rules]
            for name, definition in view.all_classes().items()
            if definition.rules
        }
    return _RULES

_EXTRACTION_STATUS = {"extracted", "not_reported"}
_VALUE_SOURCE = {"reported", "generated"}
_EVIDENCE_STATUS = {"present", "not_found", "not_applicable"}

# LinkML native ranges of the ExtractedValue subclasses, and the Python types
# that satisfy them. bool is excluded from integer deliberately: True would
# otherwise pass as 1.
_SCALAR_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "float": (int, float),
    "date": (str,),
    "boolean": (bool,),
}

#: What makes an analysis's own text a claim about a crossing. Short on purpose:
#: `interaction` and `moderation` are the words papers use for one, and `×` is how a
#: term name spells it. `-by-` is a crossing in "group-by-stage" and a reduplication
#: in "voxel-by-voxel", so it counts only when the words it joins differ.
_CROSSING_WORDS = ("interaction", "moderat", "×")
_BY_CROSSING = re.compile(r"([a-z]+)-by-([a-z]+)")


#: Comparison syntax in a `ModelTerm.name`. A term is the *axis* of a comparison, never
#: the comparison, so a name stating one is a factor written down from its contrast's
#: side -- the shape `check_occasion_factors` looks for. The operator pattern requires a
#: word character on both sides so that a threshold such as "p < .001" is not an axis.
_COMPARISON_WORDS = ("versus", " vs ", " vs. ", "greater than", "less than",
                     "difference between", "change in", "change from",
                     "pre-post", "pre/post", "prepost")
_COMPARISON_OPERATOR = re.compile(r"[a-z0-9)\]]\s*[<>]\s*[a-z0-9(\[]")

#: Derivation language in a `ModelTerm.name` -- a column computed from several of an
#: instrument's measurements rather than being one of them. Multi-word on purpose. A
#: bare "percent" catches "percentage methylation at CpG sites 11-12", which is a
#: measurement and not a difference; a bare "change" catches "pre > post rsFC change",
#: which is a collapsed occasion factor and `check_occasion_factors`' finding rather
#: than this one.
_DERIVED_WORDS = ("change in", "change from", "change over", "percent change",
                  "percentage change", "percent reduction", "difference between",
                  "difference in", "improvement in", "delta ")

#: Prose claiming a result is a change across occasions, read off an analysis's `name`
#: and `definition`. Deliberately not "baseline": a record whose analyses are all
#: baseline-only is the legitimate reading of a design that scanned twice and reported
#: once, and it is not what this looks for.
_CHANGE_WORDS = ("change", "longitudinal", "over time", "follow-up", "followup",
                 "pre > post", "post > pre", "pre-post", "following treatment",
                 "after treatment")


def _unwrap(node: Any) -> Any:
    """The value inside an ExtractedValue wrapper, or None for a not_reported one."""

    return node.get("value") if isinstance(node, Mapping) else None


def _prose(*fields: Any) -> str:
    values = [_unwrap(node) for node in fields]
    return " ".join(str(value) for value in values if value is not None).lower()


def names_a_comparison(*fields: Any) -> bool:
    """Does this term's own name state the comparison it was the subject of?"""

    text = _prose(*fields)
    return (any(word in text for word in _COMPARISON_WORDS)
            or _COMPARISON_OPERATOR.search(text) is not None)


def names_a_change_over_time(*fields: Any) -> bool:
    """Does this prose claim a result is a change from one occasion to another?"""

    text = _prose(*fields)
    return any(word in text for word in _CHANGE_WORDS)


def names_a_derivation(*fields: Any) -> bool:
    """Does this term's name say its values were computed from several measurements?"""

    text = _prose(*fields)
    return any(word in text for word in _DERIVED_WORDS)


def names_a_crossing(*fields: Any) -> bool:
    """Does this prose claim a crossing was tested?

    Read off the analysis's own `name` and `definition` rather than off its cells,
    because the whole point is to compare the two: the cells are what the record
    says, and this is what the paper said.
    """

    text = _prose(*fields)
    if any(word in text for word in _CROSSING_WORDS):
        return True
    return any(left != right for left, right in _BY_CROSSING.findall(text))


class Validator:
    def __init__(self, classes: Mapping[str, Any], normalized: str | None,
                 enums: Mapping[str, Any] | None = None) -> None:
        self.classes = classes
        self.enums = enums or {}
        self.normalized = normalized
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.fields = 0
        self.spans = 0

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def warn(self, path: str, message: str) -> None:
        self.warnings.append(f"{path}: {message}")

    # -- class instances ---------------------------------------------------

    def resolve_type(self, node: Mapping[str, Any], class_name: str, path: str) -> str:
        """Follow a type designator to the subclass the record says it is.

        `Analysis.details` declares range AnalysisDetails and `details_type` says which of
        the eight it really is; the fields that make the payload useful live on the
        subclass, so validating against the declared range rejects every one of them.

        Which slot designates the type comes from `schema_utils.type_designator`, read off
        `designates_type: true`, rather than from a list here -- the same resolution four
        walkers in `build_record` and `extract_record` need, and a list in five places is
        how they came to disagree.
        """

        designator = schema_utils.type_designator(self.classes, class_name)
        if designator is None:
            return class_name
        named = node.get(designator)
        if not isinstance(named, str):
            return class_name
        if named not in self.classes:
            self.error(path, f"{designator} names unknown class {named!r}")
            return class_name
        if not schema_utils.resolves_to(self.classes, named, class_name):
            self.error(path, f"{designator} {named!r} is not a {class_name}")
            return class_name
        return named

    def check_instance(self, node: Any, class_name: str, path: str) -> None:
        if not isinstance(node, dict):
            self.error(path, f"expected an object for {class_name}, got {type(node).__name__}")
            return

        class_name = self.resolve_type(node, class_name, path)
        attributes = schema_utils.attributes_for(self.classes, class_name)
        if not attributes:
            self.error(path, f"class {class_name!r} is not defined in the schema")
            return

        for key in node:
            if key not in attributes:
                self.error(path, f"attribute {key!r} is not declared on {class_name}")

        for name, attribute in attributes.items():
            if attribute.get("required") and name not in node:
                self.error(path, f"required attribute {name!r} is missing on {class_name}")

        for key, value in node.items():
            attribute = attributes.get(key)
            if attribute is None:
                continue
            self.check_slot(value, key, attribute, f"{path}.{key}")

        self.check_rules(node, class_name, path)

    # -- class rules -------------------------------------------------------

    def check_rules(self, node: Mapping[str, Any], class_name: str, path: str) -> None:
        """Apply the storage schema's `rules` for this class.

        They are read from storage because the projection drops them: an extraction
        record is the only thing there is to check them against, so a rule stated on
        storage and never evaluated is prose. Reading them rather than restating them
        is what keeps a rule added later enforced without code.
        """

        for rule in storage_rules().get(class_name, []):
            if not self.conditions_hold(node, rule.get("preconditions"), path):
                continue
            if self.conditions_hold(node, rule.get("postconditions"), path):
                continue
            self.error(path, rule.get("description") or f"violates a rule on {class_name}")

    def conditions_hold(
        self, node: Mapping[str, Any], conditions: Any, path: str
    ) -> bool:
        """Whether a pre- or postcondition block holds of this instance."""

        if not isinstance(conditions, Mapping):
            return True
        for keyword, body in conditions.items():
            if keyword == "slot_conditions":
                if not all(
                    self.slot_condition_holds(node.get(slot), condition, f"{path}.{slot}")
                    for slot, condition in body.items()
                ):
                    return False
            elif keyword == "any_of":
                if not any(self.conditions_hold(node, one, path) for one in body):
                    return False
            elif keyword == "none_of":
                if any(self.conditions_hold(node, one, path) for one in body):
                    return False
            elif keyword == "all_of":
                if not all(self.conditions_hold(node, one, path) for one in body):
                    return False
            else:
                self.unsupported(path, keyword)
                return True
        return True

    def slot_condition_holds(self, value: Any, condition: Any, path: str) -> bool:
        if not isinstance(condition, Mapping):
            return True
        for keyword, body in condition.items():
            if keyword == "name":
                continue
            if keyword == "equals_string":
                if _unwrap(value) != body:
                    return False
            elif keyword == "value_presence":
                present = value not in (None, "", [], {})
                if present != (str(body).upper() == "PRESENT"):
                    return False
            elif keyword == "any_of":
                if not any(self.slot_condition_holds(value, one, path) for one in body):
                    return False
            elif keyword == "none_of":
                if any(self.slot_condition_holds(value, one, path) for one in body):
                    return False
            elif keyword == "all_of":
                if not all(self.slot_condition_holds(value, one, path) for one in body):
                    return False
            else:
                self.unsupported(path, keyword)
        return True

    def unsupported(self, path: str, keyword: str) -> None:
        """A construct this evaluator cannot read is reported, never skipped.

        Silently ignoring one turns the rule into a check that always passes, which
        is worse than the prose it replaced.
        """

        self.error(path, f"rule construct {keyword!r} is not implemented; the rule was not checked")

    def check_slot(
        self, value: Any, name: str, attribute: Mapping[str, Any], path: str
    ) -> None:
        kind = schema_utils.classify_slot(self.classes, name, attribute)
        multivalued = bool(attribute.get("multivalued"))

        if multivalued and not isinstance(value, list):
            self.error(path, f"{name!r} is multivalued but got {type(value).__name__}")
            return
        if not multivalued and isinstance(value, list) and kind in {"evidence", "nested"}:
            self.error(path, f"{name!r} is not multivalued but got a list")
            return

        items = value if multivalued and isinstance(value, list) else [value]
        for index, item in enumerate(items):
            here = f"{path}[{index}]" if multivalued else path
            if kind == "identifier":
                if not isinstance(item, str) or not item:
                    self.error(here, "local_id must be a non-empty string")
            elif kind == "reference":
                if not isinstance(item, str):
                    self.error(here, f"cross-reference must be a string, got {type(item).__name__}")
            elif kind == "native":
                self.check_native(item, attribute, here)
            elif kind == "nested":
                target = attribute.get("range")
                if isinstance(target, str):
                    self.check_instance(item, target, here)
            elif kind == "evidence":
                target = attribute.get("range")
                self.check_field(item, target if isinstance(target, str) else "ExtractedValue", here)

    def check_native(self, value: Any, attribute: Mapping[str, Any], path: str) -> None:
        """Type-check a pipeline scalar. Enum ranges are checked by their callers."""

        declared = attribute.get("range", "string")
        if not isinstance(declared, str) or declared not in _SCALAR_TYPES:
            return
        if declared == "integer" and isinstance(value, bool):
            self.error(path, "must be an integer, got a boolean")
            return
        if not isinstance(value, _SCALAR_TYPES[declared]):
            self.error(path, f"must be a {declared}, got {type(value).__name__}")
            return
        minimum = attribute.get("minimum_value")
        if isinstance(minimum, int) and isinstance(value, (int, float)) and value < minimum:
            self.error(path, f"must be >= {minimum}, got {value}")

    # -- ExtractedValue fields --------------------------------------------

    def check_field(self, node: Any, class_name: str, path: str) -> None:
        if not isinstance(node, dict):
            self.error(path, f"expected an ExtractedValue object, got {type(node).__name__}")
            return

        self.fields += 1
        attributes = schema_utils.attributes_for(self.classes, class_name)

        for key in node:
            if key not in attributes:
                self.error(path, f"attribute {key!r} is not declared on {class_name}")

        status = node.get("extraction_status")
        if status not in _EXTRACTION_STATUS:
            self.error(path, f"extraction_status must be one of {sorted(_EXTRACTION_STATUS)}, got {status!r}")

        source = node.get("value_source")
        if source is not None and source not in _VALUE_SOURCE:
            self.error(path, f"value_source must be one of {sorted(_VALUE_SOURCE)}, got {source!r}")

        evidence = node.get("evidence")
        if evidence is None:
            self.error(path, "evidence is required")
        else:
            self.check_evidence(evidence, status, f"{path}.evidence")

        # Header invariant: not_reported means no value at all.
        if status == "not_reported":
            if "value" in node:
                self.error(path, "not_reported fields must omit value")
        elif status == "extracted":
            if "value" not in node:
                self.error(path, "extracted fields must carry a value")
            else:
                self.check_value_type(node["value"], class_name, attributes, path)

    def vocabulary_of(self, value_slot: Mapping[str, Any]) -> tuple[set[str] | None, bool]:
        """(permissible values, closed) for a wrapper's `value` slot, or (None, False).

        Closed is readable off the range: a bare enum admits nothing else, while
        `any_of: [<Enum>, string]` is storage's declared escape hatch.
        """

        for name in schema_utils.attribute_ranges(value_slot):
            enum = self.enums.get(name)
            if isinstance(enum, Mapping):
                values = set((enum.get("permissible_values") or {}))
                return values, value_slot.get("range") == name
        return None, False

    def check_value_type(
        self, value: Any, class_name: str, attributes: Mapping[str, Any], path: str
    ) -> None:
        value_slot = attributes.get("value", {})

        # A vocabulary is checked before the scalar branch, because an enum range is not in
        # _SCALAR_TYPES and would otherwise fall straight through -- which is how a
        # `statistic.family` of "T" passed while the vocabulary says "t". A closed field is
        # enforced; an open one (`any_of: [<Enum>, string]`) keeps its escape hatch and the
        # off-vocabulary value is reported as a warning, since those accumulating are the
        # evidence for whether the vocabulary is short a value.
        vocabulary, closed = self.vocabulary_of(value_slot)
        if vocabulary is not None:
            for item in (value if value_slot.get("multivalued")
                         and isinstance(value, list) else [value]):
                if isinstance(item, str) and item not in vocabulary:
                    message = (f"{item!r} is not a permissible value "
                               f"({', '.join(sorted(vocabulary))})")
                    if closed:
                        self.error(path, message)
                    else:
                        self.warn(path, message + "; open vocabulary, kept as free text")

        declared = value_slot.get("range", "Any")

        # The shape assertion comes before the scalar branch, for the same reason the
        # vocabulary check does. An Extracted<Enum>List declares its `value` with `any_of`
        # and no `range`, so `declared` is "Any" and the early return below skips it --
        # which let a `Task.response_mode` of "button_press" pass where the schema wants
        # ["button_press"]. Only the per-item recursion needs a native range.
        if value_slot.get("multivalued") and not isinstance(value, list):
            # `declared` is "Any" for an any_of slot, which reads as nonsense in the
            # message, so name what the slot actually accepts.
            accepts = declared if declared in _SCALAR_TYPES else " or ".join(
                r for r in schema_utils.attribute_ranges(value_slot) if r != "Any") or "value"
            self.error(path, f"{class_name}.value must be a list of {accepts}, "
                             f"got {type(value).__name__}")
            return

        if declared in {"Any", None} or declared not in _SCALAR_TYPES:
            return  # ExtractedValue holds lists and free-form structures by design.
        expected = _SCALAR_TYPES[declared]

        # An Extracted<T>List declares `multivalued: true` on its own `value`, and a list
        # there is the whole point: extraction-readme.md §2 makes "one wrapper holding a
        # list" the headline convention, so rejecting it rejected every correctly shaped
        # inclusion_criteria, preprocessing step and echo time in the record.
        if value_slot.get("multivalued"):
            for index, item in enumerate(value):
                self.check_value_type(item, class_name,
                                      {"value": {k: v for k, v in value_slot.items()
                                                 if k != "multivalued"}},
                                      f"{path}.value[{index}]")
            return

        # A multivalued concept expressed as a list inside a scalar ExtractedValue
        # subtype is a real shape problem: the mapper would fail to parse it.
        if isinstance(value, list):
            self.error(path, f"{class_name}.value must be a {declared}, got a list")
            return
        if declared == "integer" and isinstance(value, bool):
            self.error(path, f"{class_name}.value must be an integer, got a boolean")
            return
        if not isinstance(value, expected):
            self.error(
                path, f"{class_name}.value must be a {declared}, got {type(value).__name__}"
            )

    def check_evidence(self, node: Any, status: Any, path: str) -> None:
        if not isinstance(node, dict):
            self.error(path, f"expected an Evidence object, got {type(node).__name__}")
            return

        evidence_status = node.get("status")
        if evidence_status not in _EVIDENCE_STATUS:
            self.error(path, f"status must be one of {sorted(_EVIDENCE_STATUS)}, got {evidence_status!r}")

        if status == "not_reported" and evidence_status != "not_applicable":
            self.error(path, "not_reported fields must have evidence.status not_applicable")
        if status == "extracted" and evidence_status == "not_applicable":
            self.error(path, "extracted fields must not have evidence.status not_applicable")

        sets = node.get("sets")
        if evidence_status == "present":
            if not isinstance(sets, list) or not sets:
                self.error(path, "evidence.status present requires at least one set")
                return
        elif sets:
            self.error(path, f"evidence.status {evidence_status} must not carry sets")
            return

        for index, evidence_set in enumerate(sets or []):
            self.check_set(evidence_set, f"{path}.sets[{index}]")

    def check_set(self, node: Any, path: str) -> None:
        if not isinstance(node, dict):
            self.error(path, f"expected an EvidenceSet object, got {type(node).__name__}")
            return
        for key in node:
            if key != "spans":
                self.error(path, f"attribute {key!r} is not declared on EvidenceSet")
        spans = node.get("spans")
        if not isinstance(spans, list) or not spans:
            self.error(path, "EvidenceSet requires at least one span (minimum_cardinality: 1)")
            return
        for index, span in enumerate(spans):
            self.check_span(span, f"{path}.spans[{index}]")

    def check_span(self, node: Any, path: str) -> None:
        if not isinstance(node, dict):
            self.error(path, f"expected an EvidenceSpan object, got {type(node).__name__}")
            return
        self.spans += 1
        for key in node:
            if key not in {"text", "start_char", "end_char"}:
                self.error(path, f"attribute {key!r} is not declared on EvidenceSpan")
        for key in ("text", "start_char", "end_char"):
            if key not in node:
                self.error(path, f"required attribute {key!r} is missing on EvidenceSpan")
                return
        if self.normalized is not None:
            try:
                span_tools.verify(self.normalized, node)
            except span_tools.SpanResolutionError as error:
                self.error(path, str(error))

    # -- crossings and the columns that carry them -------------------------

    def _model_index(self, record: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        models: dict[str, Mapping[str, Any]] = {}
        for model in record.get("model_estimations") or []:
            if isinstance(model, Mapping) and isinstance(model.get("local_id"), str):
                models[model["local_id"]] = model
        return models

    def _terms_in_scope(
        self,
        model_id: Any,
        models: Mapping[str, Mapping[str, Any]],
        seen: set[str] | None = None,
    ) -> dict[str, Mapping[str, Any]]:
        """local_id -> ModelTerm for a model and every stage below it.

        The chain rather than the one record, per §3 invariant 2: a group stage
        reaches a first-level column through `inputs_from`, and a cell or a product
        column may name it. Own terms are collected last so a column refitted at this
        stage shadows the lower stage's, which is the reading §3 invariant 7 assumes.

        Guarded against the cycle invariant 6 forbids: this walk would otherwise hang
        on a record that violates it, and a validator has to survive bad input in
        order to report it.
        """

        seen = set() if seen is None else seen
        if not isinstance(model_id, str) or model_id in seen:
            return {}
        seen.add(model_id)
        model = models.get(model_id)
        if not isinstance(model, Mapping):
            return {}

        terms: dict[str, Mapping[str, Any]] = {}
        for lower in model.get("inputs_from") or []:
            terms.update(self._terms_in_scope(lower, models, seen))
        for term in model.get("terms") or []:
            if isinstance(term, Mapping) and isinstance(term.get("local_id"), str):
                terms[term["local_id"]] = term
        return terms

    @staticmethod
    def _cell_signature(model_id: Any, cells: Any) -> tuple:
        """What an effect compared: its model, and its cells as a set.

        Stringified because a malformed record can put a list where a level belongs,
        and an unhashable signature would raise inside a check whose job is to report.
        """

        parts = sorted(
            (str(cell.get("term")), str(_unwrap(cell.get("level"))),
             str(_unwrap(cell.get("direction"))))
            for cell in (cells or []) if isinstance(cell, Mapping)
        )
        return (str(model_id), tuple(parts))

    def check_crossings(self, record: Mapping[str, Any]) -> None:
        """Flag interactions the cells do not actually record.

        The defect this catches is a product column that was never declared, so the
        cell that should sit on it had nowhere to go. It is invisible to every check
        above: each cell resolves, each level agrees with its term, and the record is
        structurally perfect while an interaction and a main effect have become the
        same record. `representing-models.md` §5.5 is where the shape is written down
        -- an interaction reported as an unsigned F or chi-square has nowhere to sit
        but a `ModelTerm` with `interaction_with`, because a factor that was crossed
        rather than averaged over carries no sign to cross it with.

        Warnings, not errors. The trigger reads prose, so it routes a record to review
        rather than rejecting it; a paper whose interaction really was reported as a
        directional per-level comparison needs no product column
        (`extraction-readme.md`, "the converse is a reporting habit worth naming")
        and is expected to answer for itself under review.
        """

        models = self._model_index(record)
        # signature -> the analyses sharing it. Two analyses of one model with the
        # same cells are the same estimand, so if their prose disagrees about what was
        # tested, at most one of them can be right.
        signatures: dict[tuple, list[tuple[str, bool]]] = {}

        for index, analysis in enumerate(record.get("analyses") or []):
            if not isinstance(analysis, Mapping):
                continue
            path = f"Study.analyses[{index}]"
            model_id = analysis.get("model_estimation")
            terms = self._terms_in_scope(model_id, models)
            effect = analysis.get("effect")
            cells = (effect.get("cells") if isinstance(effect, Mapping) else None) or []
            claimed = names_a_crossing(analysis.get("name"), analysis.get("definition"))

            signed: dict[str, set[str]] = {}
            products: list[str] = []
            held = False
            for cell in cells:
                if not isinstance(cell, Mapping):
                    continue
                term_id = cell.get("term")
                if not isinstance(term_id, str):
                    continue
                term = terms.get(term_id)
                if isinstance(term, Mapping) and (term.get("interaction_with") or []):
                    products.append(term_id)
                direction = _unwrap(cell.get("direction"))
                if direction in {"positive", "negative"}:
                    signed.setdefault(term_id, set()).add(direction)
                # `held` on a named level is a factor held constant and nothing else: an
                # undirected test is `undirected` and a withheld sign is `unstated`, so this
                # cannot catch an omnibus F by accident. An analysis reported within one level
                # of the crossing is §5.5's last row -- a legitimate simple effect, whose prose
                # names the interaction it came from and whose cells are not supposed to
                # record it.
                if _unwrap(cell.get("level")) is not None and direction == "held":
                    held = True
            # A term signed once has not been compared against itself.
            crossed = [term_id for term_id, sides in signed.items() if len(sides) == 2]

            if claimed and not products and not held and len(crossed) < 2:
                self.warn(
                    f"{path}.effect.cells",
                    "names a crossing the cells do not record: no cell sits on a product "
                    f"column and {len(crossed)} term(s) are crossed, so the derived kind is "
                    "the one a main effect of the same terms would get. Either the crossing "
                    "needs its sides on both factors' levels, or the model is missing the "
                    "ModelTerm with interaction_with that carries an unsigned interaction test",
                )

            signatures.setdefault(self._cell_signature(model_id, cells), []).append(
                (path, claimed)
            )

        for (model_id, _), members in signatures.items():
            if len(members) < 2:
                continue
            crossing = [path for path, claimed in members if claimed]
            plain = [path for path, claimed in members if not claimed]
            if crossing and plain:
                self.warn(
                    f"{crossing[0]}.effect.cells",
                    f"identical to {', '.join(plain)} on {model_id}, which names no crossing: "
                    "the same cells over the same model are the same estimand, so an "
                    "interaction and a main effect cannot both be what these record",
                )

    def check_product_columns(self, record: Mapping[str, Any]) -> None:
        """Flag product columns that nothing can reach.

        Two ways a declared column fails to do its job. Its components may name a term
        outside its own stage chain -- which `check_local_ids` cannot see, because the
        reference resolves, just to a sibling model's column of the same name. And no
        cell anywhere may name it, which is legal (a design-matrix column that only
        ever adjusted something, per §5.5's main-effect rows) but is also what a paper
        looks like when its interaction table was never extracted at all.
        """

        models = self._model_index(record)
        celled = {
            cell.get("term")
            for analysis in record.get("analyses") or []
            if isinstance(analysis, Mapping)
            for cell in ((analysis.get("effect") or {}).get("cells")
                         if isinstance(analysis.get("effect"), Mapping) else None) or []
            if isinstance(cell, Mapping)
        }

        for m_index, model in enumerate(record.get("model_estimations") or []):
            if not isinstance(model, Mapping):
                continue
            model_id = model.get("local_id")
            terms = self._terms_in_scope(model_id, models)
            for t_index, term in enumerate(model.get("terms") or []):
                if not isinstance(term, Mapping):
                    continue
                components = term.get("interaction_with") or []
                if not components:
                    continue
                path = f"Study.model_estimations[{m_index}].terms[{t_index}]"
                for c_index, component in enumerate(components):
                    if isinstance(component, str) and component not in terms:
                        self.warn(
                            f"{path}.interaction_with[{c_index}]",
                            f"{component!r} is not a term of {model_id!r} or of a stage it "
                            "reaches through inputs_from, so this column names a component "
                            "it cannot be a product of",
                        )
                if term.get("local_id") not in celled:
                    self.warn(
                        path,
                        f"product column {term.get('local_id')!r} carries no cell in any "
                        "analysis. Legal if it only adjusted the effects that were reported, "
                        "but it is also what a missing interaction analysis looks like",
                    )

    # -- the two unsigned values --------------------------------------------

    def check_unsigned_cells(self, record: Mapping[str, Any]) -> None:
        """Flag the two shapes `held` cannot have.

        `representing-models.md` §4 cuts the three unsigned values by two questions.
        Was the level on both sides of the comparison at once? That is `held`, and it
        is the whole of what `held` says on a `Cell`. Otherwise, does the test yield a
        per-level sign at all -- no for an F or chi-square, which is `undirected`; yes
        but unprinted, which is `unstated`.

        Both halves of the first question are checkable:

        * a cell naming **no level** -- on a slope or a product column -- has no level
          to put on both sides, so it can only be an undirected test miscoded;
        * a factor **all** of whose declared levels are celled `held` is an undirected
          test of that factor, since holding a level constant is a claim about one
          level and leaves the others absent.

        The partial case is deliberately not flagged. A contrast taken within two of a
        factor's three levels holds both of them, and reads as two `held` cells with
        the third absent -- so the trigger is *every declared level celled*, not *more
        than one*.

        Warnings, for `check_crossings`' reason: this is what a record extracted under
        the old reading looks like, and it routes to review rather than rejecting.
        """

        models = self._model_index(record)

        for index, analysis in enumerate(record.get("analyses") or []):
            if not isinstance(analysis, Mapping):
                continue
            path = f"Study.analyses[{index}].effect.cells"
            terms = self._terms_in_scope(analysis.get("model_estimation"), models)
            effect = analysis.get("effect")
            cells = (effect.get("cells") if isinstance(effect, Mapping) else None) or []

            # term -> the levels it celled, and which of those were unsigned this way.
            celled: dict[str, list[Any]] = {}
            unsigned: dict[str, list[Any]] = {}
            for cell in cells:
                if not isinstance(cell, Mapping):
                    continue
                term_id = cell.get("term")
                if not isinstance(term_id, str):
                    continue
                level = _unwrap(cell.get("level"))
                celled.setdefault(term_id, []).append(level)
                if _unwrap(cell.get("direction")) != "held":
                    continue
                if level is None:
                    self.warn(
                        path,
                        f"cell on {term_id!r} is held and names no level. A slope or a product "
                        "column has no level to sit on both sides of the comparison, which is "
                        "the only thing held says on a cell; an undirected test of such a "
                        "column is undirected (representing-models.md 4)",
                    )
                    continue
                unsigned.setdefault(term_id, []).append(level)

            for term_id, levels in unsigned.items():
                term = terms.get(term_id)
                declared = [
                    _unwrap(level.get("level"))
                    for level in (term.get("levels") if isinstance(term, Mapping) else None) or []
                    if isinstance(level, Mapping)
                ]
                if len(declared) < 2 or set(declared) - set(celled.get(term_id, [])):
                    continue
                if len(levels) < len(declared):
                    continue
                self.warn(
                    path,
                    f"every declared level of {term_id!r} is celled held, which says the "
                    "factor was held on both sides of its own test. An undirected test over a "
                    "factor is undirected on each level; held marks one level and leaves the "
                    "rest absent (representing-models.md 4)",
                )

    # -- occasions, and the factors that should carry them ------------------

    def check_occasion_factors(self, record: Mapping[str, Any]) -> None:
        """Flag a comparison the record collapsed into a single column.

        Invisible to every check above, in the way `check_crossings`' defect is: each
        cell resolves, each level agrees with its term, and the comparison the paper
        reported is gone. Two halves, from the two ends.

        The term half is a column named after the comparison it was the *subject* of --
        `pre > post change`, continuous, no levels. The design matrix distinguished two
        occasions; the paper labelled the difference and the axis went unnamed. One cell
        on a continuous term then derives a regression where a contrast belongs.

        The design half is the same defect from the other end: several occasions
        declared, analyses reporting change over time, and no `FactorLevel.timepoints`
        naming any of them. That slot is the only route to a `Timepoint`, so when it is
        empty the scans are recorded and the comparison between them is not.

        `ModelTerm.type` and `representing-models.md` §5.6 state the shape. Warnings,
        for `check_crossings`' reason -- the trigger reads prose. Left alone: a genuine
        per-participant covariate, which is continuous, named for its subtraction, and
        `between_subject`, an occasion factor varying within a participant by definition.
        """

        for m_index, model in enumerate(record.get("model_estimations") or []):
            if not isinstance(model, Mapping):
                continue
            for t_index, term in enumerate(model.get("terms") or []):
                if not isinstance(term, Mapping):
                    continue
                if _unwrap(term.get("type")) != "continuous" or term.get("levels"):
                    continue
                # A product column has no levels either, and is legitimately named
                # for the crossing it is a product of.
                if term.get("interaction_with"):
                    continue
                # A column an instrument or a place in the brain supplies is a real
                # measurement whatever the source called it.
                if term.get("assessment") or term.get("region"):
                    continue
                if _unwrap(term.get("variation_level")) == "between_subject":
                    continue
                if not names_a_comparison(term.get("name")):
                    continue
                self.warn(
                    f"Study.model_estimations[{m_index}].terms[{t_index}].name",
                    f"{_unwrap(term.get('name'))!r} states a comparison while the term is "
                    "continuous with no levels, so nothing records which occasions, "
                    "cohorts or conditions were on each side. A comparison is a "
                    "categorical term with a level per side and the sign on the cells "
                    "(representing-models.md 5.6)",
                )

        design = record.get("design")
        timepoints = (design.get("timepoints") if isinstance(design, Mapping) else None) or []
        declared = [timepoint for timepoint in timepoints if isinstance(timepoint, Mapping)]
        if len(declared) < 2:
            return

        referenced = {
            local_id
            for model in record.get("model_estimations") or [] if isinstance(model, Mapping)
            for term in model.get("terms") or [] if isinstance(term, Mapping)
            for level in term.get("levels") or [] if isinstance(level, Mapping)
            for local_id in level.get("timepoints") or []
        }
        if referenced:
            return

        reporting = [
            f"analyses[{index}]"
            for index, analysis in enumerate(record.get("analyses") or [])
            if isinstance(analysis, Mapping)
            and names_a_change_over_time(analysis.get("name"), analysis.get("definition"))
        ]
        if not reporting:
            return

        shown = ", ".join(reporting[:3]) + (" and others" if len(reporting) > 3 else "")
        self.warn(
            "Study.design.timepoints",
            f"{len(declared)} occasions are declared and no FactorLevel.timepoints "
            f"names any of them, while {len(reporting)} analysis(es) report change over "
            f"time ({shown}). That slot is the only route to a Timepoint, so as "
            "recorded the scans are here and the comparison between them is not",
        )

    # -- derived columns and where they came from ---------------------------

    def check_derived_columns(self, record: Mapping[str, Any]) -> None:
        """Flag a derived column whose origin the record does not state.

        A change score or percent change is one number per participant computed from
        several of an instrument's administrations, and two slots make it interpretable:
        `assessment` names the instrument, `source_definition` says what the derivation
        was and over which occasions.

        Neither is optional here. Deriving a column does not break the link to its
        instrument -- `region` says as much by example, an ROI mean and a PPI regressor
        both naming their region. And `source_definition` is the *only* place the
        occasions can go, since a column with no levels has no `FactorLevel.timepoints`:
        in a study with several post-intervention occasions it is what separates a change
        to the endpoint from a change to a later follow-up.

        Warnings, for the reason above: the trigger reads a name. The vocabulary is
        narrow deliberately, so a column named for what it measures rather than for how
        it was built is left alone.
        """

        assessments = len(record.get("assessments") or [])

        for m_index, model in enumerate(record.get("model_estimations") or []):
            if not isinstance(model, Mapping):
                continue
            for t_index, term in enumerate(model.get("terms") or []):
                if not isinstance(term, Mapping):
                    continue
                if _unwrap(term.get("type")) != "continuous" or term.get("levels"):
                    continue
                if not names_a_derivation(term.get("name")):
                    continue

                path = f"Study.model_estimations[{m_index}].terms[{t_index}]"
                name = _unwrap(term.get("name"))

                if not _unwrap(term.get("source_definition")):
                    self.warn(
                        f"{path}.source_definition",
                        f"{name!r} is a derived column and its derivation is not "
                        "recorded. Nothing else can say what was subtracted from what, "
                        "or over which occasions: a column with no levels has no "
                        "FactorLevel.timepoints to name them",
                    )

                if term.get("assessment") is None and assessments:
                    self.warn(
                        f"{path}.assessment",
                        f"{name!r} reads as derived from an instrument's measurements "
                        f"but names no assessment, while the record declares "
                        f"{assessments}. Deriving a column does not break the link to "
                        "the instrument it came from",
                    )

    # -- entry point -------------------------------------------------------

    def check_cell_terms(self, record: Mapping[str, Any]) -> None:
        """§3 invariants 2, 3 and 4: a cell names a term of its own stage chain, and a
        level it names is one that term declares.

        The three travel together because they are one join failed at different depths. A
        `Cell.term` that resolves nowhere, or to a term of a model this analysis does not
        reach through `inputs_from`, makes the cell a sign of nothing. A `Cell.level` that
        matches no declared `FactorLevel.level` is worse than absent: the record looks like
        it recorded which condition was compared, and the mapper's string join will not find
        it, so the comparison is unrecoverable from the entity side.

        `check_crossings` deliberately stays silent on an unresolvable term -- it is asking
        a different question and cannot answer it for a broken cell -- which is why this
        reports instead.
        """

        models = self._model_index(record)
        for index, analysis in enumerate(record.get("analyses") or []):
            if not isinstance(analysis, Mapping):
                continue
            model_id = analysis.get("model_estimation")
            terms = self._terms_in_scope(model_id, models)
            effect = analysis.get("effect")
            if not isinstance(effect, Mapping):
                continue
            local = analysis.get("local_id") or f"analyses[{index}]"

            pointers = [(f"analyses[{index}].effect.cells[{n}]", cell.get("term"), cell)
                        for n, cell in enumerate(effect.get("cells") or [])
                        if isinstance(cell, Mapping)]
            mediation = effect.get("mediation")
            if isinstance(mediation, Mapping):
                pointers.append(
                    (f"analyses[{index}].effect.mediation", mediation.get("mediator"), None))

            for path, term_id, cell in pointers:
                if not isinstance(term_id, str):
                    continue
                term = terms.get(term_id)
                if term is None:
                    owner = next((m.get("local_id") for m in models.values()
                                  for t in (m.get("terms") or [])
                                  if isinstance(t, Mapping) and t.get("local_id") == term_id),
                                 None)
                    if owner is None:
                        self.error(path, f"term {term_id!r} names no ModelTerm anywhere")
                    else:
                        self.error(path, f"term {term_id!r} belongs to {owner!r}, which "
                                         f"{local!r}'s model ({model_id!r}) does not reach "
                                         "through inputs_from")
                    continue
                if cell is None:
                    continue

                level = _unwrap(cell.get("level"))
                if not isinstance(level, str):
                    continue
                declared = [_unwrap(entry.get("level")) for entry in (term.get("levels") or [])
                            if isinstance(entry, Mapping)]
                declared = [name for name in declared if isinstance(name, str)]
                if not declared:
                    self.error(f"{path}.level", f"is {level!r} but term {term_id!r} declares "
                                                "no levels to match it against")
                elif level not in declared:
                    self.error(
                        f"{path}.level",
                        f"{level!r} matches none of term {term_id!r}'s declared levels "
                        f"({', '.join(repr(name) for name in declared)}); the mapper joins "
                        "these on the string",
                    )

    def check_model_stages(self, record: Mapping[str, Any]) -> None:
        """§3 invariants 6 and 7: `inputs_from` is acyclic, and a term name is unique across
        a whole stage chain.

        Both are already *survived* rather than reported. `_terms_in_scope` carries a `seen`
        set so the walk terminates on a cycle, and collects own terms last so a same-named
        lower-stage term is shadowed -- which is the right reading when the name is a
        deliberate refit and silently absorbs the violation when it is not. A validator that
        merely does not hang on bad input has not reported it.

        Neither has ever fired on the corpus. They are in because a cycle is a hang as well
        as a falsehood, and because a first-level `motion` shadowing a group-level `motion`
        makes two columns indistinguishable in one term list -- a reader cannot tell a column
        refitted at the stage above from one restated there by mistake.
        """

        models = self._model_index(record)

        for model_id, model in models.items():
            path = f"model_estimations[{model_id}]"

            def walk(current: Any, trail: tuple[str, ...]) -> None:
                if not isinstance(current, str):
                    return
                if current in trail:
                    cycle = " -> ".join(trail[trail.index(current):] + (current,))
                    self.error(f"{path}.inputs_from",
                               f"inputs_from is cyclic: {cycle}. A model fitted on its own "
                               "output is not a stage order")
                    return
                lower = models.get(current)
                if isinstance(lower, Mapping):
                    for below in lower.get("inputs_from") or []:
                        walk(below, trail + (current,))

            for start in model.get("inputs_from") or []:
                walk(start, (model_id,))

            # Names across the chain, which is where the collision matters -- `unique_keys`
            # scopes per record and the projection drops it anyway.
            seen: dict[str, str] = {}
            for owner_id, term in self._chain_terms(model_id, models):
                name = _unwrap(term.get("name"))
                if not isinstance(name, str) or not name.strip():
                    continue
                folded = span_tools.fold_label(name)
                if folded in seen and seen[folded] != owner_id:
                    self.error(
                        f"{path}.terms",
                        f"term name {name!r} appears on both {seen[folded]!r} and "
                        f"{owner_id!r} in one stage chain, so a reader cannot tell a column "
                        "refitted at the stage above from one restated there by mistake",
                    )
                seen[folded] = owner_id

    def _chain_terms(
        self, model_id: Any, models: Mapping[str, Mapping[str, Any]],
        seen: set[str] | None = None,
    ) -> list[tuple[str, Mapping[str, Any]]]:
        """`(owning model local_id, term)` for a model and every stage below it.

        Unlike `_terms_in_scope`, which keys by term local_id and so *loses* the duplicate
        this reports, this keeps every term with the stage that declared it.
        """

        seen = set() if seen is None else seen
        if not isinstance(model_id, str) or model_id in seen:
            return []
        seen.add(model_id)
        model = models.get(model_id)
        if not isinstance(model, Mapping):
            return []
        found: list[tuple[str, Mapping[str, Any]]] = []
        for lower in model.get("inputs_from") or []:
            found += self._chain_terms(lower, models, seen)
        found += [(model_id, term) for term in model.get("terms") or []
                  if isinstance(term, Mapping)]
        return found

    def check_table_purpose(self, record: Mapping[str, Any]) -> None:
        """A coordinate table either reports an analysis or says what it does instead.

        `Table.non_analysis_content` is the only field that can say a table's rows are
        locations rather than findings -- ROI definitions, atlas parcels, the peaks of an
        ICA's components. Two things follow, and the second is the one worth having:

        - a table marked as non-analysis that an `Analysis.tables` nonetheless names is a
          contradiction, and one of the two is wrong;
        - a table **no** analysis names and that carries no marking is the missed-analysis
          case. Before this field existed that was indistinguishable from a table
          deliberately left unencoded, and both read as the same silence. `spec.py`'s
          `not_analyses` and `missed_analysis` verdicts have always been separate; this is
          what lets the record tell them apart before a reviewer does.

        A warning rather than an error on the second, because an unencoded table is a
        judgement to review and not a malformed record.
        """

        referenced = {name for analysis in record.get("analyses") or []
                      if isinstance(analysis, Mapping)
                      for name in (analysis.get("tables") or []) if isinstance(name, str)}
        for index, table in enumerate(record.get("tables") or []):
            if not isinstance(table, Mapping):
                continue
            local_id = table.get("local_id")
            path = f"tables[{index}]"
            marked = _unwrap(table.get("non_analysis_content"))
            if marked and local_id in referenced:
                self.error(
                    f"{path}.non_analysis_content",
                    f"says this table reports {marked!r} rather than an effect, but an "
                    "analysis names it in `tables`",
                )
            elif not marked and local_id not in referenced:
                self.warn(
                    path,
                    "no analysis names this table and non_analysis_content is empty, so "
                    "nothing says whether it was deliberately not encoded or missed",
                )

    def check_record(self, record: Any) -> None:
        self.check_instance(record, "Study", "Study")

        if isinstance(record, dict):
            self.check_cell_terms(record)
            self.check_model_stages(record)
            self.check_table_purpose(record)
            self.check_crossings(record)
            self.check_product_columns(record)
            self.check_unsigned_cells(record)
            self.check_occasion_factors(record)
            self.check_derived_columns(record)

        metadata = record.get("extraction_metadata") if isinstance(record, dict) else None
        if isinstance(metadata, dict) and self.normalized is not None:
            declared_hash = metadata.get("source_text_hash")
            actual = text_index.text_hash(self.normalized)
            if declared_hash and declared_hash != actual:
                self.error(
                    "Study.extraction_metadata.source_text_hash",
                    f"does not match the supplied text ({declared_hash[:12]}... != {actual[:12]}...)",
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--text", type=Path, help="normalized source text; enables offset checks")
    parser.add_argument("--paper", help="neurostore id, for matching --known-gaps entries")
    parser.add_argument("--known-gaps", type=Path, default=known_gaps.DEFAULT,
                        help="allowlist of findings a reviewer has accepted; see "
                             "review/known-gaps.yaml")
    args = parser.parse_args()

    normalized = None
    if args.text:
        normalized = text_index.normalize(args.text.read_text(encoding="utf-8"))

    classes = schema_utils.load_imported_classes(EXTRACTION_SCHEMA)
    enums = schema_utils.load_imported_classes(EXTRACTION_SCHEMA, "enums")
    validator = Validator(classes, normalized, enums)
    validator.check_record(json.loads(args.record.read_text(encoding="utf-8")))

    # The paper defaults to the record's stem, so `--record <id>.extraction.json` matches
    # its allowlist entries without the caller repeating the id.
    paper = args.paper or args.record.name.split(".")[0]
    gaps = known_gaps.load(args.known_gaps, paper)
    errors, suppressed = known_gaps.partition(validator.errors, gaps)

    print(f"{args.record.name}: {validator.fields} fields, {validator.spans} spans checked")
    if validator.warnings:
        print(f"\nwarnings ({len(validator.warnings)}):")
        for warning in validator.warnings:
            print(f"  - {warning}")
    if suppressed:
        print(f"\n{len(suppressed)} error(s) suppressed by {args.known_gaps.name}:")
        for finding in suppressed:
            print(f"  - {finding}")
    if errors:
        print(f"\nerrors ({len(errors)}):")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
