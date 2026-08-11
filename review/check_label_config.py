#!/usr/bin/env python3
"""Validate a labeling config against Label Studio's own label_config_schema.json.

Label Studio's server-side `validate_label_config` pulls in Django, jsonschema,
xmljson, numpy and pandas. This reproduces its substantive checks with the
standard library only, so a config can be verified before it is ever POSTed:

  1. badgerfish XML -> JSON, then the schema's constraints (core/label_config.py:110)
  2. unique `name=` attributes (core/label_config.py:124)
  3. every `toName=` resolves to a declared name (core/label_config.py:130)

Only the JSON-Schema keywords the Label Studio schema actually uses are
implemented: $ref, anyOf, oneOf, required, properties, items, type.

The schema ships inside the Label Studio checkout, which is not vendored here, so
pass its path explicitly:

    python review/check_label_config.py \
        --schema label-studio/label_studio/core/utils/schema/label_config_schema.json \
        review/ls_config/*.xml
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

DEFAULT_SCHEMA = Path("label-studio/label_studio/core/utils/schema/label_config_schema.json")


def badgerfish(element: ElementTree.Element) -> dict[str, Any]:
    """Convert XML the way xmljson.badgerfish does: @attrs, $ text, repeats as lists."""

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


class SchemaChecker:
    def __init__(self, schema: dict[str, Any]) -> None:
        self.schema = schema

    def deref(self, node: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in node:
            target: Any = self.schema
            for part in node["$ref"].lstrip("#/").split("/"):
                target = target[part]
            node = target
        return node

    def validate(self, instance: Any, schema: dict[str, Any], path: str) -> list[str]:
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
            matched = sum(1 for option in schema["oneOf"] if not self.validate(instance, option, path))
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


#: Tags `Panel` accepts as children (tags/visual/Collapse.jsx, PanelModel.children).
#: The two omissions that bite: `pagedview` -- what a `mode="pagination"` Repeater
#: becomes (core/Tree.tsx:91-97) -- and `markdown`. `View` admits both
#: (tags/visual/View.jsx:124), so the fix is always a wrapping View. Neither
#: failure raises: the block is simply absent from the rendered form.
_PANEL_FORBIDS = {"pagedview", "markdown"}

_INDEX_FLAG = re.compile(r"\{\{\w+\}\}")


def check_repeaters(root: ElementTree.Element) -> list[str]:
    """Checks for the Repeater expansion rules, none of which fail loudly.

    Repeater is expanded at config-parse time (core/Tree.tsx:69-99) and the
    substitution has two sharp edges: it uses String.replace with a string
    pattern, so only the FIRST occurrence of the flag in an attribute is replaced
    (`:48`), and it touches attributes only -- `recursiveClone` returns early on
    nodes with no attributes (`:41`), so an index flag in element text survives
    into the rendered label.

    The name rule is what guarantees uniqueness after expansion: a named tag
    inside N enclosing Repeaters must carry all N of their flags, or two
    iterations collide on one name and the editor drops a control.
    """

    errors: list[str] = []

    def walk(node: ElementTree.Element, flags: tuple[str, ...], in_panel: bool) -> None:
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
                errors.append(f'Repeater on={on!r} must reference task data with a $key')
            if node.get("mode") == "pagination":
                # PagedView recomputes the page from the *object tag's* name every
                # time a region is selected:
                #   parseFloat(last.object.name.split("_")[1]) + 1
                # (tags/object/PagedView.jsx:138-146). With a shared object tag the
                # split yields undefined, the page becomes NaN, and the NaN lands
                # in the `view_page` query param where getQueryPage keeps returning
                # it -- so the pager reads "NaN of 3" until the URL is cleaned. It
                # only breaks once someone draws a span, which is what makes it
                # worth a static check.
                indexed = any(
                    flag in (obj.get("name") or "")
                    for obj in node.iter()
                    if obj.tag.lower() in {"text", "hypertext", "image", "audio", "paragraphs"}
                    for flag in (node.get("indexFlag", "{{idx}}"),)
                )
                if not indexed:
                    errors.append(
                        'Repeater mode="pagination" needs an object tag named with its '
                        "indexFlag inside it; with a shared object tag, selecting a region "
                        "sets the page to NaN (PagedView.jsx:138-146)"
                    )
            flag = node.get("indexFlag", "{{idx}}")
            if flag in flags:
                errors.append(f"nested Repeaters share indexFlag {flag}; the inner one shadows it")
            flags = flags + (flag,)

        for key, value in node.attrib.items():
            for flag in set(_INDEX_FLAG.findall(value)):
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

        if node.text and _INDEX_FLAG.search(node.text):
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
            walk(child, flags, in_panel or tag == "panel")

    walk(root, (), False)
    return errors


def _get(data: Any, path: str) -> Any:
    """lodash `get` for the subset of paths parseValue produces.

    `parseValue` strips the `$` and hands the rest to lodash get
    (`utils/data.js:12-23`), so `fields[0].label` has to resolve the same way.
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


def expand_repeaters(element: ElementTree.Element, data: dict[str, Any]) -> ElementTree.Element:
    """Expand every Repeater against task data, the way the editor does.

    Mirrors `tagIntoObject`/`deepReplaceAttributes` (core/Tree.tsx:57-99),
    including the two behaviours that surprise: the index flag is replaced with
    String.replace and a string pattern, so only the FIRST occurrence in each
    attribute changes, and only attributes are touched, never element text.

    Label Studio validates the *unexpanded* config, so this is the only way to
    see the form a reviewer actually gets -- in particular whether two iterations
    collide on one name.
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


def check(config: str, schema: dict[str, Any]) -> list[str]:
    """Return every problem found in one labeling config."""

    try:
        root = ElementTree.fromstring(config)
    except ElementTree.ParseError as error:
        return [f"XML is not well formed: {error}"]

    errors = SchemaChecker(schema).validate({root.tag: badgerfish(root)}, schema, "")

    # From the parsed tree, not a regex over the source. `name="..."` also occurs
    # inside the `<Style>` block -- `input[name="taxonomy__search"]` is how the legacy
    # Taxonomy's search field is reached, since its class carries a build hash -- and
    # scanning the text read those CSS selectors as tags and reported a name collision
    # between a stylesheet rule and nothing at all.
    names = [
        node.get("name") for node in root.iter()
        if node.get("name") is not None and node.tag != "Style"
    ]
    if len(set(names)) != len(names):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        errors.append(f"non-unique names: {duplicates}")
    for attribute in re.findall(r'toName="([^"]*)"', config):
        for target in attribute.split(","):
            if target not in set(names):
                errors.append(f'toName="{target}" does not match any declared name')

    errors.extend(check_repeaters(root))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configs", nargs="+", type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args()

    if not args.schema.is_file():
        print(f"schema not found: {args.schema}\nPass --schema to point at the Label Studio checkout.")
        return 2

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    failed = False
    for path in args.configs:
        errors = check(path.read_text(encoding="utf-8"), schema)
        if errors:
            failed = True
            print(f"{path}: INVALID")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"{path}: valid")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
