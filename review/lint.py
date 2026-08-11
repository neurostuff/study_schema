#!/usr/bin/env python3
"""Check a labeling config the way Label Studio would, and the ways it would not.

Three layers, in increasing order of what they catch:

  1. **What the server checks.** `validate_label_config` pulls in Django,
     jsonschema, xmljson, numpy and pandas; this reproduces its substantive checks
     with the standard library, so a config is verified before it is ever POSTed.
     Badgerfish XML to JSON against `label_config_schema.json`, unique `name=`,
     every `toName=` resolving.

  2. **What the server cannot check.** Repeater expansion happens client-side
     against task data, so the server only ever sees `name="value_verdict_{{i}}"`
     -- which is unique as written. A name that collides across two iterations
     validates and then drops a control in the editor, with no error anywhere.
     `repeater_rules` is that check, plus the substitution's sharp edges.

  3. **What only the expanded form shows.** `expand` mirrors the editor's own
     algorithm, so the tests can assert what a reviewer actually gets: unique
     control names, no surviving index flags, exactly one required question.

The schema ships inside the Label Studio checkout, which is not vendored here.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import config
import spec

#: Ships inside the Label Studio checkout, which is gitignored here because it has
#: its own history. Absent, the schema layer is skipped and the other two still run.
SCHEMA = (
    Path(__file__).resolve().parent.parent
    / "label-studio/label_studio/core/utils/schema/label_config_schema.json"
)

#: Tags `Panel` accepts as children (`tags/visual/Collapse.jsx`, PanelModel.children).
#: The two omissions that bite: `pagedview` -- what a `mode="pagination"` Repeater
#: becomes (`core/Tree.tsx:91-97`) -- and `markdown`. `View` admits both, so the fix
#: is always a wrapping View. Neither failure raises: the block is simply absent
#: from the rendered form.
PANEL_FORBIDS = {"pagedview", "markdown"}

INDEX_FLAG = re.compile(r"\{\{\w+\}\}")

_OBJECT_TAGS = {"text", "hypertext", "image", "audio", "paragraphs"}


def badgerfish(element: ElementTree.Element) -> dict[str, Any]:
    """XML to JSON the way xmljson.badgerfish does: @attrs, $ text, repeats as lists."""

    node: dict[str, Any] = {f"@{key}": value for key, value in element.attrib.items()}
    if element.text and element.text.strip():
        node["$"] = element.text.strip()
    for child in element:
        converted = badgerfish(child)
        existing = node.get(child.tag)
        if existing is None:
            node[child.tag] = converted
        elif isinstance(existing, list):
            existing.append(converted)
        else:
            node[child.tag] = [existing, converted]
    return node


class Schema:
    """The JSON-Schema subset Label Studio's own schema uses: $ref, anyOf, oneOf,
    required, properties, items, type. Nothing else is implemented, because
    nothing else appears in it."""

    def __init__(self, schema: dict[str, Any]) -> None:
        self.schema = schema

    def deref(self, node: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in node:
            target: Any = self.schema
            for part in node["$ref"].lstrip("#/").split("/"):
                target = target[part]
            node = target
        return node

    def validate(self, instance: Any, schema: dict[str, Any], path: str = "") -> list[str]:
        schema = self.deref(schema)
        errors: list[str] = []

        if "anyOf" in schema:
            branch_errors = []
            for option in schema["anyOf"]:
                found = self.validate(instance, option, path)
                if not found:
                    return []
                branch_errors.extend(found)
            return [f"{path or '/'}: no anyOf branch matched ({branch_errors[:2]})"]

        if "oneOf" in schema:
            matched = sum(
                1 for option in schema["oneOf"] if not self.validate(instance, option, path)
            )
            if matched != 1:
                return [f"{path or '/'}: expected exactly one oneOf branch, matched {matched}"]
            return []

        expected = schema.get("type")
        if expected == "object" and not isinstance(instance, dict):
            return [f"{path}: expected object, got {type(instance).__name__}"]
        if expected == "array" and not isinstance(instance, list):
            return [f"{path}: expected array, got {type(instance).__name__}"]
        if expected == "string" and not isinstance(instance, str):
            return [f"{path}: expected string, got {type(instance).__name__}"]

        for key in schema.get("required", []):
            if not isinstance(instance, dict) or key not in instance:
                errors.append(f"{path or '/'}: missing required {key!r}")

        if isinstance(instance, list) and "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(self.validate(item, schema["items"], f"{path}[{index}]"))

        if isinstance(instance, dict):
            for key, subschema in schema.get("properties", {}).items():
                if key in instance:
                    errors.extend(self.validate(instance[key], subschema, f"{path}/{key}"))

        return errors


def repeater_rules(root: ElementTree.Element) -> list[str]:
    """The expansion rules, none of which fail loudly.

    Repeater is expanded at config-parse time (`core/Tree.tsx:69-99`) and the
    substitution has two sharp edges: it uses `String.replace` with a string
    pattern, so only the FIRST occurrence of a flag in an attribute is replaced
    (`:48`), and it touches attributes only -- `recursiveClone` returns early on
    nodes with no attributes (`:41`), so an index flag in element text survives
    into the rendered label.

    The name rule is what guarantees uniqueness after expansion: a named tag
    inside N enclosing Repeaters must carry all N of their flags, or two iterations
    collide on one name and the editor drops a control.
    """

    errors: list[str] = []

    def walk(node: ElementTree.Element, flags: tuple[str, ...]) -> None:
        tag = node.tag.lower()

        if tag == "panel":
            for child in node:
                child_tag = child.tag.lower()
                if child_tag == "markdown":
                    errors.append("Markdown is not a legal Panel child; wrap it in a View")
                if child_tag == "repeater" and child.get("mode") == "pagination":
                    errors.append(
                        'a mode="pagination" Repeater becomes a pagedview, which Panel '
                        "does not accept; paginate outside the Collapse"
                    )

        if tag == "repeater":
            on = node.get("on") or ""
            if not on.startswith("$"):
                errors.append(f"Repeater on={on!r} must reference task data with a $key")
            flag = node.get("indexFlag", "{{idx}}")
            if node.get("mode") == "pagination":
                # PagedView recomputes the page from the *object tag's* name every
                # time a region is selected:
                #     parseFloat(last.object.name.split("_")[1]) + 1
                # (`tags/object/PagedView.jsx:138-146`). With a shared object tag the
                # split yields undefined, the page becomes NaN, and the NaN lands in
                # the `view_page` query param where getQueryPage keeps returning it,
                # so the pager reads "NaN of 3" until the URL is cleaned. It only
                # breaks once someone draws a span, which is what makes it worth a
                # static check.
                indexed = any(
                    flag in (obj.get("name") or "")
                    for obj in node.iter()
                    if obj.tag.lower() in _OBJECT_TAGS
                )
                if not indexed:
                    errors.append(
                        'Repeater mode="pagination" needs an object tag named with its '
                        "indexFlag inside it; with a shared object tag, selecting a "
                        "region sets the page to NaN (PagedView.jsx:138-146)"
                    )
            if flag in flags:
                errors.append(
                    f"nested Repeaters share indexFlag {flag}; the inner one shadows it"
                )
            flags = flags + (flag,)

        for key, value in node.attrib.items():
            for flag in set(INDEX_FLAG.findall(value)):
                if value.count(flag) > 1:
                    errors.append(
                        f"{node.tag} {key}={value!r} repeats {flag}; Tree.tsx replaces "
                        "only the first occurrence"
                    )
                if flag not in flags:
                    errors.append(
                        f"{node.tag} {key}={value!r} uses {flag}, which no enclosing "
                        "Repeater declares"
                    )

        if node.text and INDEX_FLAG.search(node.text):
            errors.append(
                f"{node.tag} has an index flag in its text; substitution touches "
                "attributes only, so it would render literally"
            )

        name = node.get("name")
        if name and flags:
            missing = [flag for flag in flags if flag not in name]
            if missing:
                errors.append(
                    f'{node.tag} name="{name}" omits {missing} from its enclosing '
                    "Repeaters, so iterations would collide on one name"
                )

        for child in node:
            walk(child, flags)

    walk(root, ())
    return errors


def _get(data: Any, path: str) -> Any:
    """lodash `get` for the subset of paths parseValue produces.

    `parseValue` strips the `$` and hands the rest to lodash get
    (`utils/data.js:12-23`), so `rows[0].label` has to resolve the same way.
    """

    for part in re.findall(r"[^.\[\]]+", path):
        if isinstance(data, list):
            if not part.isdigit() or int(part) >= len(data):
                return None
            data = data[int(part)]
        elif isinstance(data, dict):
            if part not in data:
                return None
            data = data[part]
        else:
            return None
    return data


def expand(element: ElementTree.Element, data: dict[str, Any]) -> ElementTree.Element:
    """Expand every Repeater against task data, the way the editor does.

    Mirrors `tagIntoObject`/`deepReplaceAttributes` (`core/Tree.tsx:57-99`),
    including the two behaviours that surprise: the flag is replaced with
    `String.replace` and a string pattern, so only the FIRST occurrence in each
    attribute changes, and only attributes are touched, never element text.

    An empty or missing array yields zero copies. That is the gate: a block whose
    `on` key the task does not carry is not in the reviewer's form at all, so a
    `required` control inside it cannot block submission.
    """

    def replace_first(node: ElementTree.Element, flag: str, index: int) -> None:
        for key, value in list(node.attrib.items()):
            node.set(key, value.replace(flag, str(index), 1))
        for child in node:
            replace_first(child, flag, index)

    def walk(node: ElementTree.Element) -> list[ElementTree.Element]:
        if node.tag.lower() == "repeater":
            items = _get(data, (node.get("on") or "$").lstrip("$")) or []
            flag = node.get("indexFlag", "{{idx}}")
            out = []
            for index in range(len(items)):
                holder = ElementTree.Element("View")
                for child in node:
                    clone = copy.deepcopy(child)
                    replace_first(clone, flag, index)
                    holder.extend(walk(clone))
                out.append(holder)
            return out

        rebuilt = ElementTree.Element(node.tag, dict(node.attrib))
        rebuilt.text = node.text
        for child in node:
            rebuilt.extend(walk(child))
        return [rebuilt]

    expanded = walk(copy.deepcopy(element))
    if len(expanded) != 1:
        holder = ElementTree.Element("View")
        holder.extend(expanded)
        return holder
    return expanded[0]


def names(root: ElementTree.Element) -> list[str]:
    """Every declared `name=`, from the parsed tree rather than the source.

    `name="..."` also occurs inside the `<Style>` block -- `[class*="taxonomy__search"]`
    is how the legacy Taxonomy's search field is reached, since its own class carries
    a build hash -- and scanning the text read those CSS selectors as tags and
    reported a collision between a stylesheet rule and nothing at all.
    """

    return [
        node.get("name")
        for node in root.iter()
        if node.get("name") is not None and node.tag != "Style"
    ]


def check(label_config: str, schema: dict[str, Any] | None = None) -> list[str]:
    """Every problem in one labeling config, unexpanded."""

    try:
        root = ElementTree.fromstring(label_config)
    except ElementTree.ParseError as error:
        return [f"XML is not well formed: {error}"]

    errors: list[str] = []
    if schema:
        errors += Schema(schema).validate({root.tag: badgerfish(root)}, schema)

    declared = names(root)
    if len(set(declared)) != len(declared):
        duplicates = sorted({name for name in declared if declared.count(name) > 1})
        errors.append(f"non-unique names: {duplicates}")
    for attribute in re.findall(r'toName="([^"]*)"', label_config):
        for target in attribute.split(","):
            if target not in set(declared):
                errors.append(f'toName="{target}" does not match any declared name')

    errors += repeater_rules(root)
    return errors


def variants() -> list[tuple[spec.Project, spec.Kind]]:
    """Every (project, kind) pair a reviewer can be shown."""

    return [(project, kind) for project in spec.PROJECTS for kind in project.blocks]


def expanded(project: spec.Project, kind: spec.Kind, size: int = 2) -> ElementTree.Element:
    """One variant's config as the editor would build it, against a sample task."""

    return expand(
        ElementTree.fromstring(config.build(project)), config.sample_task(kind.name, size)
    )


def check_expanded(project: spec.Project, kind: spec.Kind) -> list[str]:
    """What only the expanded form can show: collisions, leftovers, two questions."""

    root = expanded(project, kind)
    errors: list[str] = []

    declared = names(root)
    duplicates = sorted({name for name in declared if declared.count(name) > 1})
    if duplicates:
        errors.append(f"{kind.name}: names collide after expansion: {duplicates}")

    leftover = sorted(
        {
            f"{node.tag} {key}={value}"
            for node in root.iter()
            for key, value in node.attrib.items()
            if INDEX_FLAG.search(value)
        }
    )
    if leftover:
        errors.append(f"{kind.name}: index flags survived expansion: {leftover[:3]}")

    required = sorted(
        node.get("name") for node in root.iter() if node.get("required") == "true"
    )
    wanted = [kind.verdict.replace(spec.FLAGS[0], "0")] if kind.question else []
    if required != wanted:
        errors.append(
            f"{kind.name}: expected required controls {wanted}, got {required}. "
            "A task must ask exactly one question, or a kind sharing the project "
            "blocks another kind's submission."
        )
    return errors


def load_schema(path: Path = SCHEMA) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
