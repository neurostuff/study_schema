#!/usr/bin/env python3
"""Turn the LinkML-generated JSON Schema into one OpenAI strict structured output accepts.

`gen-json-schema` emits an idiomatic JSON Schema; strict mode wants a narrower dialect:

  * every key in `properties` must also appear in `required`
  * anything genuinely optional must therefore be nullable instead
  * `additionalProperties: false` on every object
  * no `$ref` siblings, no `default`, no unsupported keywords

Doing this by transformation rather than by hand means the extraction schema stays the single
source of truth: change neuroimaging-study-extraction.yaml and the model's output contract
follows.

    python3 bench/strict_schema.py --out bench/extraction.strict.json --report
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA_YAML = REPO / "neuroimaging-study-extraction.yaml"

# Keywords strict mode rejects outright.
DROP = {"default", "additionalItems", "patternProperties", "propertyNames",
        "unevaluatedProperties", "unevaluatedItems", "dependentSchemas",
        "dependentRequired", "if", "then", "else", "not", "const",
        "minLength", "maxLength", "pattern", "format", "minimum", "maximum",
        "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
        "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties",
        "metamodel_version", "version"}


def nullable(node: dict) -> dict:
    """Make a leaf schema accept null, so a strict-required key can still be absent in spirit.

    A `$ref` cannot take a type, so it is wrapped in an anyOf with a null branch instead.
    """
    if "$ref" in node and len(node) == 1:
        return {"anyOf": [node, {"type": "null"}]}
    t = node.get("type")
    if t is None:
        if "anyOf" in node:
            branches = node["anyOf"]
            if not any(b.get("type") == "null" for b in branches):
                node["anyOf"] = branches + [{"type": "null"}]
        return node
    if isinstance(t, str):
        if t != "null":
            node["type"] = [t, "null"]
    elif "null" not in t:
        node["type"] = list(t) + ["null"]
    return node


def strictify(node, depth=0, stats=None):
    if isinstance(node, list):
        return [strictify(x, depth, stats) for x in node]
    if not isinstance(node, dict):
        return node

    node = {k: v for k, v in node.items() if k not in DROP}
    for k, v in list(node.items()):
        if k in ("properties", "$defs", "definitions"):
            node[k] = {pk: strictify(pv, depth + 1, stats) for pk, pv in v.items()}
        elif k in ("items", "additionalProperties"):
            node[k] = strictify(v, depth + 1, stats)
        elif k in ("anyOf", "oneOf", "allOf"):
            node[k] = [strictify(x, depth + 1, stats) for x in v]

    if node.get("type") == "object" or "properties" in node:
        props = node.get("properties")
        if props is None:
            # an object with no declared properties is not expressible in strict mode; the
            # schema has none of these today, but fail loudly rather than emit something the
            # API will reject with a less obvious message
            if node.get("type") == "object":
                raise ValueError("object with no properties cannot be strict")
        else:
            node["additionalProperties"] = False
            was_required = set(node.get("required") or [])
            for name, sub in props.items():
                if name not in was_required:
                    props[name] = nullable(sub)
                    if stats is not None:
                        stats["made_nullable"] += 1
            node["required"] = list(props)
            if stats is not None:
                stats["objects"] += 1
                stats["properties"] += len(props)
                stats["max_depth"] = max(stats["max_depth"], depth)
    if "oneOf" in node:                      # strict mode understands anyOf, not oneOf
        node["anyOf"] = node.pop("oneOf")

    # A `$ref` may not carry sibling keywords. Move it inside an `anyOf` so the siblings -
    # above all `description`, which is where the schema says "verbatim" and is real signal
    # for the model - survive instead of being discarded.
    if "$ref" in node and len(node) > 1:
        ref = node.pop("$ref")
        node["anyOf"] = [{"$ref": ref}] + [b for b in node.pop("anyOf", [])
                                           if b.get("type") == "null"]
        if stats is not None:
            stats["refs_wrapped"] += 1
    return node


def build(report=False):
    gen = subprocess.run([sys.executable.replace("python3", "gen-json-schema")
                          if Path(sys.executable.replace("python3", "gen-json-schema")).exists()
                          else "gen-json-schema", "--closed", str(SCHEMA_YAML)],
                         capture_output=True, text=True)
    if gen.returncode != 0:
        print(gen.stderr[-800:], file=sys.stderr)
        raise SystemExit("gen-json-schema failed; install linkml or pass --from-json")
    raw = json.loads(gen.stdout)
    return finish(raw, report)


def finish(raw, report=False):
    stats = {"objects": 0, "properties": 0, "made_nullable": 0, "max_depth": 0,
             "refs_wrapped": 0}
    out = strictify(copy.deepcopy(raw), 0, stats)
    out.pop("$schema", None)
    out.pop("$id", None)
    if report:
        print(f"  objects {stats['objects']}  properties {stats['properties']}  "
              f"made nullable {stats['made_nullable']}  refs wrapped {stats['refs_wrapped']}  "
              f"defs {len(out.get('$defs') or {})}  chars {len(json.dumps(out)):,}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="bench/extraction.strict.json")
    ap.add_argument("--from-json", default=None,
                    help="use an already-generated JSON Schema instead of running gen-json-schema")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    schema = finish(json.loads(Path(args.from_json).read_text()), args.report) \
        if args.from_json else build(args.report)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(schema, indent=1))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
