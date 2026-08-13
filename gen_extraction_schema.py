#!/usr/bin/env python3
"""Project the storage schema into the extraction schema.

The two schemas describe the same records. Storage holds typed, normalized values; the
extraction schema holds what a language model read off the page, each value wrapped in an
`ExtractedValue` carrying its evidence. Everything else -- which classes exist, what they
are called, which slots they have, how they nest -- is meant to be identical, so that the
extraction-to-storage map is an identity map with a short list of exceptions rather than a
second description of the domain.

Identical is easier to assert than to maintain, so the extraction schema is generated:

    neuroimaging-study-storage.yaml    ->  neuroimaging-study-extraction.yaml
    neuroimaging-study-storage/*.yaml  ->  neuroimaging-study-extraction/*.yaml

The projection is mechanical:

  * attributes marked `model_extracted` are kept; `deterministic` ones are dropped,
    because code fills them -- an API lookup, a derivation, a generated identifier
  * `id` becomes `local_id`: extraction assigns document-local keys, and the storage
    identifier is minted at ingestion
  * a scalar or enum range becomes the matching `ExtractedValue` subtype. The enum
    wrappers are generated here, one per vocabulary, and keep the storage range exactly:
    a bare enum stays closed, an `any_of: [<Enum>, string]` keeps its escape hatch
  * a multivalued scalar becomes one wrapper holding a list, not a list of wrappers --
    the list usually comes from one sentence and one evidence record covers it
  * a class range is left alone. Inlined stays inlined and recurses; a plain reference
    resolves through LinkML's identifier, which is the `local_id` string
  * scalar constraints, rules, and unique keys do not survive the wrapper and are
    reported rather than silently dropped

Anything that is not mechanical lives in extraction-deviations.yaml, in two parts.
`required_additions` holds what extraction has and storage cannot -- pipeline provenance,
the document section index. `deviations` holds shape changes made because they measurably
help extraction, and starts empty: the baseline is the projection, and every departure
from it has to earn its place against data.

The generated tree is committed. Run with --check to fail when it is out of date.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path
import sys

import yaml

from schema_utils import (
    IN_SUBSET,
    LOCAL_ID,
    attribute_ranges,
    is_marked,
    is_structural,
    load_imported_classes,
    own_attributes,
    subclasses_of,
)


ROOT = Path(__file__).resolve().parent
SOURCE_ENTRYPOINT = ROOT / "neuroimaging-study-storage.yaml"
SOURCE_MODULES = ROOT / "neuroimaging-study-storage"
SOURCE_STEM = "neuroimaging-study-storage"
TARGET_STEM = "neuroimaging-study-extraction"
DEVIATIONS = ROOT / "extraction-deviations.yaml"
EVIDENCE_SCHEMA = ROOT / "extraction-evidence.yaml"

#: The subset naming attributes a language model reads out of the paper.
EXTRACTED_SUBSET = "model_extracted"

#: Declares the subsets themselves, so it is never projected.
SUBSETS_MODULE = "subsets"

#: Scalar range -> the ExtractedValue subtype that wraps it, declared in
#: extraction-evidence.yaml. Anything not listed wraps as text.
SCALAR_WRAPPERS = {
    "integer": "ExtractedInteger",
    "float": "ExtractedNumber",
    "double": "ExtractedNumber",
    "decimal": "ExtractedNumber",
    "boolean": "ExtractedBoolean",
}
DEFAULT_WRAPPER = "ExtractedString"

#: Suffix for the variant whose `value` is a list. A multivalued fact is one wrapper over
#: a list rather than a list of wrappers, so the cardinality moves inside the wrapper.
LIST_SUFFIX = "List"

#: The schema's own prefix, renamed by the projection. CURIEs in the body follow it.
SOURCE_PREFIX = "neurostudy"
TARGET_PREFIX = "neurostudy_ex"

#: Prefix for the generated per-vocabulary wrappers, e.g. Modality -> ExtractedModality.
ENUM_WRAPPER_PREFIX = "Extracted"

#: Attribute keys that constrain a scalar and cannot reach through the wrapper.
UNWRAPPABLE_CONSTRAINTS = ("minimum_value", "maximum_value", "pattern", "equals_string")

#: Class keys that constrain slot values and so meet the same fate.
UNWRAPPABLE_CLASS_KEYS = ("rules", "unique_keys")

BANNER = """\
# GENERATED FILE -- DO NOT EDIT.
#
# Produced by gen_extraction_schema.py from {source} and
# extraction-deviations.yaml. Change the storage schema or the deviations and
# regenerate:
#
#     python3 gen_extraction_schema.py
#
"""


# --------------------------------------------------------------------------------------
# YAML output
# --------------------------------------------------------------------------------------


class _Dumper(yaml.SafeDumper):
    """SafeDumper that keeps long prose readable instead of one long quoted line."""

    def increase_indent(self, flow: bool = False, indentless: bool = False):
        return super().increase_indent(flow, False)


def _represent_str(dumper: yaml.SafeDumper, data: str):
    blockable = (
        len(data) > 88
        and " " in data
        and data.strip() == data
        and not data.startswith(("&", "*", "!", "%", "@", "`", "-", "?", ":", "#"))
    )
    if blockable:
        style = "|" if "\n" in data else ">"
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _represent_str)


def dump_schema(document: Mapping[str, object], source: str) -> str:
    """Serialize a schema document, verifying the result reads back unchanged."""

    body = yaml.dump(
        document,
        Dumper=_Dumper,
        sort_keys=False,
        default_flow_style=False,
        width=88,
        allow_unicode=True,
    )
    text = BANNER.format(source=source) + body
    if yaml.safe_load(text) != document:
        raise AssertionError(f"dump of {source} does not round-trip; refusing to write it")
    return text


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, Mapping):
        raise ValueError(f"{path.name} must contain a YAML mapping.")
    return dict(document)


# --------------------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------------------


def is_kept(attribute: Mapping[str, object]) -> bool:
    """Whether an attribute survives the projection.

    Identifiers and type designators are structural: a record cannot be assembled without
    them and no subset decision applies. Everything else has to be marked
    `model_extracted` -- a `deterministic` field is filled by code on the storage side and
    has nothing for a model to read.
    """

    return is_structural(attribute) or is_marked(attribute, EXTRACTED_SUBSET)


def tree_root_of(classes: Mapping[str, object]) -> str:
    roots = [
        name
        for name, definition in classes.items()
        if isinstance(definition, Mapping) and definition.get("tree_root") is True
    ]
    if len(roots) != 1:
        raise ValueError(f"expected exactly one tree_root class, found {roots}")
    return roots[0]


def kept_own_attributes(
    classes: Mapping[str, object], class_name: str
) -> dict[str, Mapping]:
    return {
        name: attribute
        for name, attribute in own_attributes(classes, class_name).items()
        if is_kept(attribute)
    }


def reachable_classes(classes: Mapping[str, object]) -> set[str]:
    """Every class reachable from the tree root through kept attributes.

    A class with no kept attribute other than its identifier still survives if something
    points at it: unlike the MVP subset, the projection is not deciding what to represent,
    only how to represent it. Dropping a class here would mean the extraction schema
    cannot express a record storage accepts.
    """

    seen: set[str] = set()
    queue = [tree_root_of(classes)]
    while queue:
        class_name = queue.pop()
        if class_name in seen or class_name not in classes:
            continue
        seen.add(class_name)

        definition = classes[class_name]
        parent = definition.get("is_a") if isinstance(definition, Mapping) else None
        if isinstance(parent, str):
            queue.append(parent)

        for attribute in kept_own_attributes(classes, class_name).values():
            for attribute_range in attribute_ranges(attribute):
                if attribute_range not in classes:
                    continue
                queue.append(attribute_range)
                # A slot may hold any subclass of its declared range, so the variants
                # come along too. This is not only the abstract case: Acquisition is
                # concrete and MRI, EEG, PET, and FNIRS all specialize it, and an
                # extraction record that cannot say which one is useless.
                queue.extend(subclasses_of(classes, attribute_range))
    return seen


# --------------------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------------------


class Report:
    """What the projection could not carry across, gathered for the run summary."""

    def __init__(self) -> None:
        self.dropped_deterministic: list[str] = []
        self.dropped_constraints: list[str] = []
        self.dropped_class_keys: list[str] = []
        self.dropped_enums: list[str] = []
        self.wrapped: dict[str, int] = {}
        self.references: list[str] = []
        self.applied_deviations: list[str] = []


def naturalize(node: object) -> object:
    """Strip storage-only annotation from a projected definition, recursively.

    Two things do not survive the move. `in_subset` marks membership of subsets the
    extraction schema does not declare -- and it is not a subset of anything, it is a
    projection of the whole. CURIEs against the `neurostudy` prefix have to follow the
    prefix rename, or `instantiates: neurostudy:MRI` on a Modality value points at a
    namespace this schema does not define.

    The CURIE rewrite is a substring replace rather than a prefix match, because the
    prefix is named in prose as well as in values -- Modality's own description explains
    what `neurostudy:MRI` resolves to. Every occurrence is a self-reference, so every
    occurrence has to follow the rename.
    """

    if isinstance(node, Mapping):
        return {
            key: naturalize(value) for key, value in node.items() if key != IN_SUBSET
        }
    if isinstance(node, list):
        return [naturalize(item) for item in node]
    if isinstance(node, str):
        return node.replace(f"{SOURCE_PREFIX}:", f"{TARGET_PREFIX}:")
    return node


def enum_wrapper_name(enum_name: str, multivalued: bool) -> str:
    return f"{ENUM_WRAPPER_PREFIX}{enum_name}{LIST_SUFFIX if multivalued else ''}"


def wrapper_for(
    classes: Mapping[str, object],
    enums: Mapping[str, object],
    attribute: Mapping[str, object],
) -> str | None:
    """The ExtractedValue subtype for an attribute, or None if its range is a class.

    A slot declaring `any_of: [{range: SpatialScope}, {range: string}]` -- a closed
    vocabulary with an escape hatch -- has no `range` of its own. It still wraps as that
    vocabulary's wrapper; the escape hatch is carried inside, on the wrapper's `value`.
    """

    ranges = attribute_ranges(attribute)
    if any(item in classes for item in ranges):
        return None

    multivalued = attribute.get("multivalued") is True
    enum_ranges = [item for item in ranges if item in enums]
    if enum_ranges:
        return enum_wrapper_name(enum_ranges[0], multivalued)

    scalar = SCALAR_WRAPPERS.get(ranges[0]) if len(ranges) == 1 else None
    base = scalar or DEFAULT_WRAPPER
    return f"{base}{LIST_SUFFIX}" if multivalued else base


def build_enum_wrappers(
    storage: Mapping[str, object],
    enums: Mapping[str, object],
    extraction: Mapping[str, object],
) -> dict[str, dict]:
    """One wrapper class per vocabulary an extracted field reaches.

    The wrapper's `value` keeps the storage slot's range verbatim -- the bare enum where
    storage is closed, the `any_of` where it left an escape hatch -- so the projection
    cannot quietly open or close a vocabulary. That fidelity is the whole point: a field
    storage will only accept a permissible value for is a field the extractor should be
    constrained to, and a field storage lets fall through to text is one where the
    paper's own wording is the honest answer.
    """

    wanted: dict[tuple[str, bool], object] = {}
    for class_name in storage:
        if class_name not in extraction:
            continue
        for attribute in own_attributes(storage, class_name).values():
            if not is_kept(attribute) or is_structural(attribute):
                continue
            ranges = attribute_ranges(attribute)
            enum_ranges = [item for item in ranges if item in enums]
            if not enum_ranges:
                continue
            key = (enum_ranges[0], attribute.get("multivalued") is True)
            # The value declaration is copied, not rebuilt, so an `any_of` gains nothing
            # and loses nothing in translation.
            wanted[key] = (
                {"any_of": attribute["any_of"]}
                if "any_of" in attribute
                else {"range": enum_ranges[0]}
            )

    wrappers: dict[str, dict] = {}
    for (enum_name, multivalued), value in sorted(wanted.items()):
        closed = "range" in value
        value_slot = dict(value)
        if multivalued:
            value_slot["multivalued"] = True
        wrappers[enum_wrapper_name(enum_name, multivalued)] = {
            "is_a": "ExtractedValue",
            "description": (
                f"An ExtractedValue whose value is "
                + ("a list of " if multivalued else "")
                + f"{enum_name}"
                + (
                    "."
                    if closed
                    else ", or the source's own wording when the vocabulary does not "
                    "cover it."
                )
            ),
            "slot_usage": {"value": value_slot},
        }
    return wrappers


def project_attribute(
    classes: Mapping[str, object],
    enums: Mapping[str, object],
    class_name: str,
    attribute_name: str,
    attribute: Mapping[str, object],
    report: Report,
) -> tuple[str, dict]:
    """Rewrite one storage attribute into its extraction counterpart."""

    path = f"{class_name}.{attribute_name}"
    output: dict = {}

    for key, value in attribute.items():
        if key == IN_SUBSET:
            continue
        if key in UNWRAPPABLE_CONSTRAINTS:
            report.dropped_constraints.append(f"{path}: {key}: {value!r}")
            continue
        if key in ("range", "any_of"):
            continue
        output[key] = naturalize(value)

    if attribute.get("identifier") is True:
        # Storage mints the identifier at ingestion; extraction assigns a key that is
        # only meaningful inside the one document, so the name changes with the meaning.
        # It is not an extracted value: nothing in the paper supplies it.
        attribute_name = LOCAL_ID
        output.pop("identifier", None)
        output["range"] = "string"
        output["required"] = True
    elif attribute.get("designates_type") is True:
        # The discriminator of an inlined union: how the record says which variant it is.
        # A model can state that, and nothing downstream can guess it.
        output["range"] = "string"
    else:
        wrapper = wrapper_for(classes, enums, attribute)
        if wrapper is None:
            # A class range is carried through unchanged. Inlined means the parent owns
            # the child and the child is projected too; a bare reference resolves through
            # the target's identifier, which is its local_id.
            class_ranges = [item for item in attribute_ranges(attribute) if item in classes]
            output["range"] = class_ranges[0]
            if not (attribute.get("inlined") or attribute.get("inlined_as_list")):
                report.references.append(f"{path} -> {class_ranges[0]}")
        else:
            output["range"] = wrapper
            report.wrapped[wrapper] = report.wrapped.get(wrapper, 0) + 1
            if attribute.get("multivalued") is True:
                # The cardinality moved inside the wrapper: one ExtractedValue holding a
                # list, under one evidence record.
                output.pop("multivalued", None)

    ordered = {}
    for key in ("range", "multivalued", "required", "inlined", "inlined_as_list"):
        if key in output:
            ordered[key] = output.pop(key)
    ordered.update(output)
    return attribute_name, ordered


def project_class(
    classes: Mapping[str, object],
    enums: Mapping[str, object],
    class_name: str,
    report: Report,
) -> dict:
    definition = classes[class_name]
    assert isinstance(definition, Mapping)

    output: dict = {}
    for key, value in definition.items():
        if key == IN_SUBSET:
            continue
        if key in UNWRAPPABLE_CLASS_KEYS:
            report.dropped_class_keys.append(f"{class_name}: {key}")
            continue
        if key == "attributes":
            continue
        output[key] = naturalize(value)

    attributes: dict[str, dict] = {}
    for attribute_name, attribute in own_attributes(classes, class_name).items():
        if not is_kept(attribute):
            if not is_structural(attribute):
                report.dropped_deterministic.append(f"{class_name}.{attribute_name}")
            continue
        name, projected = project_attribute(
            classes, enums, class_name, attribute_name, attribute, report
        )
        attributes[name] = projected
    output["attributes"] = attributes
    return output


# --------------------------------------------------------------------------------------
# Deviations
# --------------------------------------------------------------------------------------


def load_deviations() -> dict:
    """Read the file holding everything the projection is not."""

    if not DEVIATIONS.is_file():
        return {"required_additions": {}, "deviations": []}
    document = load_yaml(DEVIATIONS)
    document.setdefault("required_additions", {})
    document.setdefault("deviations", [])
    return document


def apply_required_additions(
    documents_classes: dict[str, dict], additions: Mapping[str, object], report: Report
) -> None:
    """Add the classes and slots extraction needs that storage has no place for.

    Pipeline provenance and the document section index describe the extraction run, not
    the study, so they can never come from a projection. They are not up for debate the
    way `deviations` entries are, and are kept separate for that reason.
    """

    module = additions.get("module")
    new_classes = additions.get("classes") or {}
    if new_classes:
        documents_classes.setdefault(module, {}).update(new_classes)
        report.applied_deviations.append(
            f"required_additions: {len(new_classes)} classes into {module}"
        )

    for target, slots in (additions.get("slots") or {}).items():
        target_module, _, target_class = target.partition(".")
        holder = documents_classes.get(target_module, {}).get(target_class)
        if holder is None:
            raise ValueError(f"required_additions names an unknown class: {target}")
        # Prepend: provenance reads first, the way it does in the current schema.
        holder["attributes"] = {**slots, **holder["attributes"]}
        report.applied_deviations.append(
            f"required_additions: {len(slots)} slots onto {target_class}"
        )


def apply_deviations(
    documents_classes: dict[str, dict], deviations: Sequence[Mapping], report: Report
) -> None:
    """Apply the earned shape changes, one operation each.

    Every entry needs a `why` -- the observation that justified it -- because the whole
    point of starting from a projection is that a difference between the schemas is a
    claim about extraction, and a claim should say what it rests on.
    """

    for index, deviation in enumerate(deviations):
        label = deviation.get("id") or f"deviation {index}"
        if not deviation.get("why"):
            raise ValueError(f"{label} has no `why`; a deviation must say what it rests on")
        operation = deviation.get("operation")

        if operation == "add_slot":
            module, _, class_name = str(deviation["class"]).partition(".")
            target = documents_classes.get(module, {}).get(class_name)
            if target is None:
                raise ValueError(f"{label} names an unknown class: {deviation['class']}")
            target["attributes"][deviation["slot"]] = deviation["definition"]

        elif operation == "drop_slot":
            module, _, class_name = str(deviation["class"]).partition(".")
            target = documents_classes.get(module, {}).get(class_name)
            if target is None:
                raise ValueError(f"{label} names an unknown class: {deviation['class']}")
            if deviation["slot"] not in target["attributes"]:
                raise ValueError(f"{label} drops a slot that is not there: {deviation['slot']}")
            del target["attributes"][deviation["slot"]]

        elif operation == "replace_slot":
            module, _, class_name = str(deviation["class"]).partition(".")
            target = documents_classes.get(module, {}).get(class_name)
            if target is None:
                raise ValueError(f"{label} names an unknown class: {deviation['class']}")
            if deviation["slot"] not in target["attributes"]:
                raise ValueError(
                    f"{label} replaces a slot that is not there: {deviation['slot']}"
                )
            target["attributes"][deviation["slot"]] = deviation["definition"]

        elif operation == "describe_slot":
            # The narrow case `replace_slot` handles badly. A description *is* the
            # instruction -- `extract_record.render_schema` sends the `description:` fields
            # and nothing else -- so wording that only makes sense while reading a paper is
            # an extraction concern and belongs here rather than in storage. But
            # `replace_slot` assigns the whole definition, so using it for wording means
            # restating range, required and in_subset, which then pin whatever storage said
            # on the day the deviation was written.
            #
            # `append` is the form to reach for, and the only one that cannot go stale: it
            # adds a paragraph to whatever storage currently says. `description` replaces
            # outright, for the rare case where storage's wording is actively misleading to
            # a model rather than merely incomplete.
            module, _, class_name = str(deviation["class"]).partition(".")
            target = documents_classes.get(module, {}).get(class_name)
            if target is None:
                raise ValueError(f"{label} names an unknown class: {deviation['class']}")
            slot = deviation.get("slot")
            if slot is not None and slot not in target["attributes"]:
                raise ValueError(f"{label} describes a slot that is not there: {slot}")
            node = target if slot is None else target["attributes"][slot]
            if "description" in deviation:
                node["description"] = deviation["description"]
            elif "append" in deviation:
                existing = str(node.get("description") or "").strip()
                node["description"] = f"{existing}\n\n{deviation['append'].strip()}".strip()
            else:
                raise ValueError(f"{label} has neither `description` nor `append`")

        elif operation == "add_class":
            module = str(deviation["module"])
            documents_classes.setdefault(module, {})[deviation["class"]] = deviation[
                "definition"
            ]

        elif operation == "drop_class":
            module, _, class_name = str(deviation["class"]).partition(".")
            if class_name not in documents_classes.get(module, {}):
                raise ValueError(f"{label} drops a class that is not there: {class_name}")
            del documents_classes[module][class_name]

        else:
            raise ValueError(f"{label} has an unknown operation: {operation!r}")

        report.applied_deviations.append(f"{label}: {operation}")


# --------------------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------------------


def rewrite_header(document: Mapping[str, object]) -> dict:
    """Re-point a storage schema header at the extraction tree, preserving key order."""

    header: dict = {}
    for key, value in document.items():
        if key in ("classes", "enums", "subsets", "slots", "types", "imports"):
            continue
        if key in ("id", "name") and isinstance(value, str):
            value = value.replace(SOURCE_STEM, TARGET_STEM)
        elif key == "title" and isinstance(value, str):
            value = value.replace("Storage", "Extraction")
        elif key == "prefixes" and isinstance(value, Mapping):
            value = {
                (TARGET_PREFIX if prefix == SOURCE_PREFIX else prefix): (
                    uri.replace(SOURCE_STEM, TARGET_STEM) if isinstance(uri, str) else uri
                )
                for prefix, uri in value.items()
            }
        elif key == "default_prefix" and value == SOURCE_PREFIX:
            value = TARGET_PREFIX
        header[key] = value
    return header


def load_source() -> tuple[dict, dict[str, dict], dict]:
    """Return the entrypoint, its module documents in import order, and merged classes."""

    entrypoint = load_yaml(SOURCE_ENTRYPOINT)
    modules: dict[str, dict] = {}
    for item in entrypoint.get("imports") or ():
        if not isinstance(item, str) or ":" in item:
            continue
        module_name = Path(item).name
        if module_name == SUBSETS_MODULE:
            continue
        modules[module_name] = load_yaml(SOURCE_MODULES / f"{module_name}.yaml")
    return entrypoint, modules, load_imported_classes(SOURCE_ENTRYPOINT)


def local_imports(document: Mapping[str, object]) -> list[str]:
    """The sibling module names a storage module imports, stripped of their directory."""

    return [
        Path(item).name
        for item in document.get("imports") or ()
        if isinstance(item, str) and ":" not in item
    ]


def build_output() -> tuple[dict[str, dict], Report]:
    entrypoint, modules, classes = load_source()
    report = Report()
    survivors = reachable_classes(classes)
    enums = load_imported_classes(SOURCE_ENTRYPOINT, key="enums")

    module_classes: dict[str, dict] = {}
    for module_name, document in modules.items():
        projected = {}
        for class_name in document.get("classes") or {}:
            if class_name in survivors:
                projected[class_name] = project_class(classes, enums, class_name, report)
        module_classes[module_name] = projected

    # The wrappers go in the module that defines their vocabulary, so anything that can
    # already see the enum can see its wrapper without a new import edge.
    wrappers = build_enum_wrappers(classes, enums, {name: True for name in survivors})
    module_enums: dict[str, dict] = {}
    for module_name, document in modules.items():
        declared = document.get("enums") or {}
        used = {
            name: naturalize(definition)
            for name, definition in declared.items()
            if any(
                key.startswith(f"{ENUM_WRAPPER_PREFIX}{name}")
                and key[len(ENUM_WRAPPER_PREFIX) + len(name) :] in ("", LIST_SUFFIX)
                for key in wrappers
            )
        }
        module_enums[module_name] = used
        for name in used:
            for suffix in ("", LIST_SUFFIX):
                key = f"{ENUM_WRAPPER_PREFIX}{name}{suffix}"
                if key in wrappers:
                    module_classes[module_name][key] = wrappers[key]
    report.dropped_enums = sorted(set(enums) - {e for m in module_enums.values() for e in m})

    deviations = load_deviations()
    apply_required_additions(module_classes, deviations["required_additions"], report)
    apply_deviations(module_classes, deviations["deviations"], report)

    live_modules = [
        name for name in module_classes if module_classes[name] or module_enums[name]
    ]

    documents: dict[str, dict] = {}
    for module_name in live_modules:
        document = rewrite_header(modules[module_name])
        document["imports"] = ["linkml:types", "../extraction-evidence"] + [
            name for name in local_imports(modules[module_name]) if name in live_modules
        ]
        if module_enums[module_name]:
            document["enums"] = module_enums[module_name]
        if module_classes[module_name]:
            document["classes"] = module_classes[module_name]
        documents[f"{TARGET_STEM}/{module_name}.yaml"] = document

    root_document = rewrite_header(entrypoint)
    # The storage header describes a record the mapper writes, which is the opposite of
    # what this schema holds, so the deviations file supplies its own prose.
    for key, value in (deviations.get("header") or {}).items():
        root_document[key] = value
        report.applied_deviations.append(f"header: {key}")
    root_document["imports"] = ["linkml:types", "extraction-evidence"] + [
        f"{TARGET_STEM}/{name}" for name in live_modules
    ]
    documents[f"{TARGET_STEM}.yaml"] = root_document
    return documents, report


# --------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------


def describe(documents: Mapping[str, dict], report: Report) -> list[str]:
    lines: list[str] = []
    total_classes = total_fields = 0
    for path, document in documents.items():
        emitted = document.get("classes") or {}
        if not emitted:
            continue
        fields = sum(len(c.get("attributes") or {}) for c in emitted.values())
        total_classes += len(emitted)
        total_fields += fields
        lines.append(f"  {path}: {len(emitted)} classes, {fields} fields")
    lines.insert(0, f"Projected {total_classes} classes, {total_fields} fields.")

    wrapped = ", ".join(f"{name}: {count}" for name, count in sorted(report.wrapped.items()))
    lines.append(f"  wrapped values -- {wrapped}")
    lines.append(f"  local_id references kept as class ranges: {len(report.references)}")
    lines.append(
        f"  dropped as deterministic ({len(report.dropped_deterministic)}): "
        + ", ".join(report.dropped_deterministic)
    )
    if report.dropped_enums:
        lines.append(
            f"  enums no extracted field reaches ({len(report.dropped_enums)}): "
            + ", ".join(report.dropped_enums)
        )
    if report.dropped_constraints:
        lines.append(
            f"  scalar constraints the wrapper hides ({len(report.dropped_constraints)}):"
        )
        lines.extend(f"    - {item}" for item in report.dropped_constraints)
    if report.dropped_class_keys:
        lines.append("  class constructs that constrain slot values:")
        lines.extend(f"    - {item}" for item in report.dropped_class_keys)
    if report.applied_deviations:
        lines.append("  applied from extraction-deviations.yaml:")
        lines.extend(f"    - {item}" for item in report.applied_deviations)
    else:
        lines.append("  applied from extraction-deviations.yaml: none")
    return lines


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def generated_paths() -> list[Path]:
    directory = ROOT / TARGET_STEM
    paths = [ROOT / f"{TARGET_STEM}.yaml"]
    if directory.is_dir():
        paths.extend(sorted(directory.glob("*.yaml")))
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the committed tree differs from what would be generated.",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress the projection report."
    )
    arguments = parser.parse_args(argv)

    documents, report = build_output()
    texts = {path: dump_schema(document, SOURCE_ENTRYPOINT.name) for path, document in documents.items()}

    if not arguments.quiet:
        print("\n".join(describe(documents, report)))

    expected = {ROOT / path: text for path, text in texts.items()}
    if arguments.check:
        stale = [
            path
            for path, text in expected.items()
            if not path.is_file() or path.read_text(encoding="utf-8") != text
        ]
        orphaned = [path for path in generated_paths() if path not in expected]
        if stale or orphaned:
            for path in stale:
                print(f"out of date: {path.relative_to(ROOT)}", file=sys.stderr)
            for path in orphaned:
                print(f"no longer generated: {path.relative_to(ROOT)}", file=sys.stderr)
            print("Run: python3 gen_extraction_schema.py", file=sys.stderr)
            return 1
        print("Generated extraction schema is up to date.")
        return 0

    for path in generated_paths():
        if path not in expected:
            path.unlink()
    for path, text in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(f"Wrote {len(expected)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
