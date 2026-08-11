#!/usr/bin/env python3
"""One reading of an extraction record, for everything that reviews it.

The record is a tree of entities, each holding wrapped values, cross-references
and nested objects. Three different views of it are wanted -- every reviewable
field, every association, every instance of every class -- and they all come from
one traversal, done once here.

There were three traversals before, in three files, with three notions of what
counts as a field. This is the one the review layer uses; `build_record.py` and
`validate_record.py` keep their own because they answer different questions
(building the record, and proving it well formed).

## What the schema decides, not this file

`schema_utils.classify_slot` says whether a slot is a wrapped value, a
cross-reference, a nested object or scaffolding, and that classification is what
assigns a slot to a review family. It reads the generated schema, so a slot that
moves between families -- as `Task.conditions` did, from reference to composition
-- moves without anything here changing.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import schema_utils  # noqa: E402
import yaml  # noqa: E402

EXTRACTION_SCHEMA = ROOT / "neuroimaging-study-extraction.yaml"
PRIORITIES = ROOT / "storage-parameter-priorities.yaml"

#: Slots whose owning class is reviewed as a subtree rather than field by field:
#: their correctness depends on another object's identity, which a per-field
#: verdict cannot express. `Analysis` itself is not here -- its own scalars
#: (spatial_scope, prespecification, definition) are ordinary values.
STRUCTURAL_CLASSES = frozenset({
    "Effect", "Cell", "Statistic", "Mediation", "ModelTerm", "FactorLevel",
    "AnalysisGroup",
})

#: Classes the instance inventory does not cover, because another task already
#: settles their instance set: `Analysis` through the table tasks, which judge the
#: split the analyses came out of, and `ModelEstimation` through the model tasks.
#: `Table` is filled by the table parser rather than by the extractor, so its
#: instance set is not a judgement about the paper.
INVENTORY_EXCLUDED = frozenset({"Analysis", "ModelEstimation", "Table"})

#: How many identifying facts a descriptor carries beyond the name. Four keeps a
#: `Group` descriptor to about a line while still telling two cohorts apart.
DESCRIPTOR_FACTS = 4

#: Longer than this and a fact is prose, not an identifier.
_FACT_CHARS = 40

NOT_REPORTED = "not reported"


@dataclass(frozen=True)
class Field:
    """One wrapped value: a thing with a value, a status and maybe evidence."""

    class_name: str
    local_id: str
    path: str
    attribute: Mapping[str, Any]
    node: Mapping[str, Any]
    priority: Any

    @property
    def leaf(self) -> str:
        return self.path.split(".")[-1].split("[")[0]

    @property
    def status(self) -> str:
        return self.node.get("extraction_status", "")

    @property
    def sets(self) -> list[Any]:
        return (self.node.get("evidence") or {}).get("sets") or []

    @property
    def evidence_status(self) -> str:
        return (self.node.get("evidence") or {}).get("status", "")

    @property
    def structural(self) -> bool:
        return self.class_name in STRUCTURAL_CLASSES


@dataclass(frozen=True)
class Reference:
    """One association slot on one instance: which objects it points at."""

    class_name: str
    local_id: str
    slot: str
    attribute: Mapping[str, Any]
    targets: tuple[str, ...]

    @property
    def structural(self) -> bool:
        return self.class_name in STRUCTURAL_CLASSES


def load_priorities(path: Path = PRIORITIES) -> dict[tuple[str, str], Any]:
    """(class, slot) -> priority rank, from the storage priority inventory.

    Already this project's answer to what matters about an object, which is why
    descriptors and triage both read it rather than keeping their own lists.
    """

    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        (class_name, slot): rank
        for class_name, slots in document.items()
        if isinstance(slots, Mapping)
        for slot, rank in slots.items()
    }


def display(value: Any) -> str:
    """A value as a reviewer should read it."""

    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def unwrap(node: Any) -> str:
    """The value inside an ExtractedValue wrapper, or "" if it holds none."""

    if isinstance(node, Mapping) and "value" in node:
        return display(node["value"])
    return ""


def label_of(entity: Mapping[str, Any]) -> str:
    """A human-readable handle, from whichever name-ish slot the class has."""

    for slot in ("name", "title", "map_type"):
        candidate = entity.get(slot)
        if isinstance(candidate, Mapping) and candidate.get("extraction_status") == "extracted":
            value = candidate.get("value")
            if value not in (None, ""):
                return display(value)
    return ""


def target_class(classes: Mapping[str, Any], attribute: Mapping[str, Any]) -> str | None:
    """Which class a cross-reference points at. The range says it."""

    declared = attribute.get("range")
    return declared if declared in classes else None


class Record:
    """An extraction record, walked once and indexed every way the layer needs."""

    def __init__(
        self,
        body: Mapping[str, Any],
        *,
        classes: Mapping[str, Any] | None = None,
        priorities: Mapping[tuple[str, str], Any] | None = None,
    ) -> None:
        self.body = body
        self.classes = classes or schema_utils.load_imported_classes(EXTRACTION_SCHEMA)
        self.priorities = priorities if priorities is not None else load_priorities()

        #: Every Study-level entity class the schema defines, in schema order.
        #: Taken from the schema rather than the record: a class the extractor
        #: found nothing for is exactly the one worth asking about, and keying off
        #: the record would emit no task for it.
        self.entity_classes: list[str] = []
        self.instances: dict[str, list[str]] = {}
        self.entities: dict[tuple[str, str], Mapping[str, Any]] = {}
        self.fields: list[Field] = []
        self.references: list[Reference] = []
        self._reachable: set[str] | None = None

        self._walk()

        #: How many references point at each local_id. The inventory shows this so
        #: a reviewer choosing `drop` can see what it costs.
        self.inbound: dict[str, int] = {}
        for reference in self.references:
            for target in reference.targets:
                self.inbound[target] = self.inbound.get(target, 0) + 1

    # -- traversal ---------------------------------------------------------

    def _walk(self) -> None:
        study = schema_utils.attributes_for(self.classes, "Study")

        for slot, attribute in study.items():
            if slot == "extraction_metadata":
                continue
            if schema_utils.classify_slot(self.classes, slot, attribute) != "nested":
                continue
            target = attribute.get("range")
            if (
                isinstance(target, str)
                and target not in self.entity_classes
                and "local_id" in schema_utils.attributes_for(self.classes, target)
            ):
                self.entity_classes.append(target)

        for slot, attribute in study.items():
            if slot == "extraction_metadata" or slot not in self.body:
                continue
            if schema_utils.classify_slot(self.classes, slot, attribute) != "nested":
                continue
            target = attribute.get("range")
            if not isinstance(target, str):
                continue
            value = self.body[slot]
            for entity in value if isinstance(value, list) else [value]:
                if not isinstance(entity, Mapping):
                    continue
                local_id = entity.get("local_id")
                if isinstance(local_id, str):
                    self.instances.setdefault(target, []).append(local_id)
                    self.entities[(target, local_id)] = entity
                self._visit(entity, target, local_id or "", "")

    def _visit(
        self, node: Mapping[str, Any], class_name: str, owner: str, prefix: str
    ) -> None:
        """Collect one object's fields and references, then recurse into its parts.

        Nested value objects carry no `local_id` of their own, so they stay
        attached to the owning entity: `Cell.term` reads as analysis -> term, which
        is what makes the reachability walk below work on one flat edge list.
        """

        for name, attribute in schema_utils.attributes_for(self.classes, class_name).items():
            if name not in node:
                continue
            kind = schema_utils.classify_slot(self.classes, name, attribute)
            value = node[name]
            path = f"{prefix}{name}" if prefix else name

            if kind == "evidence":
                listed = bool(attribute.get("multivalued")) and isinstance(value, list)
                items = value if listed else [value]
                for index, item in enumerate(items):
                    if not isinstance(item, Mapping) or "extraction_status" not in item:
                        continue
                    self.fields.append(
                        Field(
                            class_name=class_name,
                            local_id=owner,
                            path=f"{path}[{index}]" if listed else path,
                            attribute=attribute,
                            node=item,
                            priority=self.priorities.get((class_name, name), "unranked"),
                        )
                    )
            elif kind == "reference":
                targets = (
                    (value,)
                    if isinstance(value, str)
                    else tuple(t for t in value or [] if isinstance(t, str))
                )
                self.references.append(
                    Reference(class_name, owner, path, attribute, targets)
                )
            elif kind == "nested":
                target = attribute.get("range")
                if not isinstance(target, str):
                    continue
                items = value if isinstance(value, list) else [value]
                for index, item in enumerate(items):
                    if not isinstance(item, Mapping):
                        continue
                    suffix = f"[{index}]" if isinstance(value, list) else ""
                    self._visit(item, target, owner, f"{path}{suffix}.")

    # -- reading the record ------------------------------------------------

    def entity(self, class_name: str, local_id: Any) -> Mapping[str, Any] | None:
        if not isinstance(local_id, str) or not local_id:
            return None
        return self.entities.get((class_name, local_id))

    def name_of(self, class_name: str, local_id: Any) -> str:
        """An entity's own name, falling back to its id.

        Never empty for a real id: the id is a worse label than the name but a far
        better one than nothing, and a paraphrase that silently loses its subject
        reads as a record that failed to record one.
        """

        if not isinstance(local_id, str) or not local_id:
            return ""
        entity = self.entity(class_name, local_id)
        return (label_of(entity) if entity else "") or local_id

    def descriptor(self, class_name: str, local_id: str) -> str:
        """`local_id -- name . fact . fact`, from the class's priority-0 scalars.

        A bare `local_id` is unreviewable: "does this analysis use the right
        group?" cannot be answered from `grp_1`. Every place a reference appears --
        an inventory row, a grid column, a paraphrase -- shows this instead.

        Derived here and never stored. A stored descriptor is a second copy of the
        entity that drifts from the first, and it would put a rendered string
        inside the content hash, so correcting a name would re-ask every question
        that happened to mention it.
        """

        entity = self.entity(class_name, local_id)
        if entity is None:
            return local_id
        facts = []
        for slot, node in entity.items():
            if slot in ("local_id", "name", "title") or not isinstance(node, Mapping):
                continue
            if node.get("extraction_status") != "extracted" or "value" not in node:
                continue
            if self.priorities.get((class_name, slot)) != 0:
                continue
            rendered = display(node["value"])
            if len(rendered) <= _FACT_CHARS:
                facts.append(f"{slot}={rendered}")

        name = label_of(entity)
        head = f"{local_id} -- {name}" if name else local_id
        kept = [fact for fact in facts if fact][:DESCRIPTOR_FACTS]
        return f"{head} . {' . '.join(kept)}" if kept else head

    def fields_of(self, class_name: str, local_id: str) -> list[Field]:
        return [
            found
            for found in self.fields
            if found.class_name == class_name and found.local_id == local_id
        ]

    def existence_evidence(self, class_name: str, local_id: str) -> tuple[str, list[Any]]:
        """The spans that warrant an instance existing, and which field they came from.

        "Is this a real cohort?" is answerable from the sentence that introduces it,
        which is the evidence on its identifying field. Preference order is name,
        then title, then any priority-0 field carrying evidence, then anything
        evidenced at all -- so an instance whose name was never evidenced still gets
        a highlight rather than none.
        """

        def rank(found: Field) -> tuple[int, str]:
            if found.leaf in ("name", "title"):
                return (0, found.leaf)
            return (1 if found.priority == 0 else 2, found.leaf)

        candidates = [f for f in self.fields_of(class_name, local_id) if f.sets]
        if not candidates:
            return "", []
        best = sorted(candidates, key=rank)[0]
        return best.path, best.sets

    # -- analyses, models, tables ------------------------------------------

    def analyses(self) -> list[Mapping[str, Any]]:
        return [a for a in self.body.get("analyses") or [] if isinstance(a, Mapping)]

    def models(self) -> dict[str, Mapping[str, Any]]:
        return {
            m.get("local_id", ""): m
            for m in self.body.get("model_estimations") or []
            if isinstance(m, Mapping)
        }

    def tables(self) -> dict[str, str]:
        """Table local_id -> "Table N".

        Two analyses can share a contrast, a measure and a scope and differ only in
        which table reports them -- one per seed, say -- so the table is often the
        only thing that tells them apart.
        """

        found = {}
        for table in self.body.get("tables") or []:
            if isinstance(table, Mapping):
                local_id = table.get("local_id", "")
                found[local_id] = unwrap(table.get("table_number")) or local_id
        return found

    def stage_terms(self, model: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        """This model's terms, then those of the stages it was fitted on.

        `ModelEstimation.inputs_from` makes a multi-stage analysis one model: a
        contrast taken from the group stage may cell a first-level column -- a
        seed's time series, a condition the first level fitted -- and is adjusted by
        every first-level column it does not cell. Offering only the top stage's
        rows made those contrasts unrecordable.

        Breadth-first, so the stage the analysis names comes first and the ones
        beneath it follow. Cycle-safe, because a malformed chain must still produce
        a task.
        """

        models = self.models()
        seen: set[str] = set()
        terms: list[Mapping[str, Any]] = []
        queue = [model]
        while queue:
            current = queue.pop(0)
            key = current.get("local_id", "")
            if key in seen:
                continue
            seen.add(key)
            terms += [t for t in current.get("terms") or [] if isinstance(t, Mapping)]
            for upstream in current.get("inputs_from") or []:
                if upstream in models:
                    queue.append(models[upstream])
        return terms

    # -- is a reported result resting on this? -----------------------------

    def coordinate_status(self, analysis: Mapping[str, Any], counts: Mapping[str, int]) -> str:
        """Whether this contrast is worth validating.

        `no_table` is the whole rule: extraction is driven by the existence of a
        result, so a contrast with no table is a contrast with no reported result.
        `no_coordinates` is the refinement, available only when the table parser has
        supplied row counts -- `Table.coordinate_count` is storage-only, because
        code fills it rather than the model.
        """

        tables = [t for t in analysis.get("tables") or [] if isinstance(t, str)]
        if not tables:
            return "no_table"
        if counts:
            found = [counts.get(t) for t in tables]
            if all(isinstance(c, int) for c in found) and not any(c > 0 for c in found):
                return "no_coordinates"
        return "yes"

    def reachable(self, counts: Mapping[str, int]) -> set[str]:
        """local_ids reachable from a coordinate-bearing analysis.

        Everything a reported result rests on: the model it came from, that model's
        terms, the cohorts, paradigms, acquisitions, assessments and tables it
        names, and whatever those point at in turn. An object nothing
        coordinate-bearing reaches is not worth a reviewer's time -- which is what
        lets one filter serve every family rather than only the contrast tasks.
        """

        if self._reachable is not None:
            return self._reachable

        edges: dict[str, set[str]] = {}
        for reference in self.references:
            if reference.local_id:
                edges.setdefault(reference.local_id, set()).update(reference.targets)

        frontier = {
            analysis.get("local_id")
            for analysis in self.analyses()
            if self.coordinate_status(analysis, counts) == "yes"
        }
        frontier.discard(None)
        seen: set[str] = set()
        while frontier:
            node = frontier.pop()
            if node in seen:
                continue
            seen.add(node)
            frontier |= edges.get(node, set()) - seen
        self._reachable = seen
        return seen

    def object_status(self, local_id: str, counts: Mapping[str, int]) -> str:
        return "yes" if local_id and local_id in self.reachable(counts) else "unrelated"

    def model_status(self, model_id: str, counts: Mapping[str, int]) -> str:
        """A model is worth validating if any contrast taken from it is.

        A model no analysis references -- a first-level model whose output only
        feeds a group model -- is tied to no reported result.
        """

        statuses = [
            self.coordinate_status(analysis, counts)
            for analysis in self.analyses()
            if analysis.get("model_estimation") == model_id
        ]
        if not statuses:
            return "no_contrast"
        return "yes" if "yes" in statuses else statuses[0]
