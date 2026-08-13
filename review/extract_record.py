"""Run an LLM extractor over one paper and emit a payload for build_record.py.

This is the first half of the review pipeline. `build_record.py` consumes extractor
payloads and resolves their verbatim quotes to character offsets; this module
produces those payloads.

The prompt is rendered from the schema itself rather than restated in a string, so
the instructions cannot drift from the YAML. What the schema cannot say -- gates,
the multivalued-wrapper convention, the evidence rules, the direction vocabulary --
comes from `extraction-readme.md`, which is sent alongside it, and the shapes whole
encodings take come from `representing-models.md` §5 (see `worked_models`).

Two modes, because a single call puts the analyses behind thirty-odd entity classes
and drops them (`bench/RESULTS.md` on the pipeline_eval branch: 19% of papers
returned no analyses at all):

    entities   pass 1 -- everything the analyses point at, and nothing else
    analyses   pass 2 -- one Analysis per pre-parsed table analysis, linked by
               local_id to what pass 1 emitted

The class split is computed from the schema, not listed here: `Analysis`'s nested
closure is the analyses prompt and the rest of `Study`'s is the entities prompt. The
two are asserted disjoint by `test_extraction_prompt.py`, so a new class cannot land
in neither.

    python review/extract_record.py --paper HU6mqxmtySg3 --mode entities \
        --text review/texts/HU6mqxmtySg3/processed/pubget/text.txt \
        --out-dir review/payloads --key-file .env --no-evidence
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_record  # noqa: E402  (shares the payload contract; see ENTITY_LISTS)
import schema_utils  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
EXTRACTION_SCHEMA = REPO / "neuroimaging-study-extraction.yaml"
README = REPO / "extraction-readme.md"
MODELS = REPO / "representing-models.md"

#: The heading of the section of `representing-models.md` the prompt carries, and
#: the sub-heading `test_extraction_prompt.py` asserts survives the slice.
WORKED_MODELS_SECTION = "## 5. Worked models"

#: Payload keys merge_payloads() accepts, taken from the schema through the same
#: function build_record uses. Hardcoding this list is how `conditions` and `terms`
#: survived here for a schema version after Condition moved under Task and Term
#: became ModelTerm under ModelEstimation -- both would have been merged as
#: "unexpected payload key" and dropped.
ENTITY_LISTS = build_record._entity_lists()

#: Filled by the builder from the source text, never by the model.
SCAFFOLDING_CLASSES = {"ExtractionMetadata", "PaperSection"}

#: Supplied deterministically from the pubget table manifest by run_extraction.py:
#: table_number, caption and footer are literal source strings, so asking a model
#: to retype them can only introduce error.
DETERMINISTIC_CLASSES = {"Table"}

DEFAULT_MODEL = "@psyc-aid338-ope-333f18/gpt-5.6-luna"


def load_key_file(path: Path) -> list[str]:
    """Read a shell-style env file into os.environ. Values are never printed."""

    names = []
    for raw in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip("'\"")
        os.environ[name] = value
        names.append(name)
    return names


# --------------------------------------------------------------- class selection

def nested_closure(classes: Mapping[str, Any], roots: list[str]) -> set[str]:
    """Every class reachable from `roots` through slots the record owns.

    Ownership is the boundary: a nested slot holds the record and has to be
    described in the same prompt, a reference slot holds only a local_id and its
    target can be described in the other one.
    """

    seen: set[str] = set()
    stack = list(roots)
    while stack:
        name = stack.pop()
        if name in seen or name not in classes:
            continue
        seen.add(name)
        stack.extend(schema_utils.subclasses_of(classes, name))
        for attr, spec in schema_utils.attributes_for(classes, name).items():
            if schema_utils.classify_slot(classes, attr, spec) == "nested":
                stack.extend(schema_utils.attribute_ranges(spec))
    return seen


def mode_classes(classes: Mapping[str, Any], mode: str) -> tuple[set[str], list[str]]:
    """(classes to render, Study attributes to keep) for one pass."""

    analysis_side = nested_closure(classes, ["Analysis"])
    study = schema_utils.attributes_for(classes, "Study")

    if mode == "analyses":
        keep = ["analyses"]
        return analysis_side - DETERMINISTIC_CLASSES, keep

    roots: list[str] = []
    keep = []
    for attr, spec in study.items():
        if attr in ("analyses", "tables", "extraction_metadata", "local_id"):
            continue
        keep.append(attr)
        if schema_utils.classify_slot(classes, attr, spec) == "nested":
            roots.extend(schema_utils.attribute_ranges(spec))
    entity_side = nested_closure(classes, roots)
    return entity_side - analysis_side - SCAFFOLDING_CLASSES - DETERMINISTIC_CLASSES, keep


# ------------------------------------------------------------------- rendering

def _wrap(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def enum_of(classes: Mapping[str, Any], enums: Mapping[str, Any], range_name: str):
    """(permissible values, closed, multivalued) if `range_name` wraps a vocabulary.

    The wrappers are generated one per vocabulary and keep storage's own range, so
    whether a field is closed is readable here rather than guessable: a bare range
    is closed, an `any_of: [<Enum>, string]` keeps the escape hatch. Getting this
    wrong in the prompt is expensive in both directions -- a closed field filled
    with free text is rejected by storage, and an open field forced to the nearest
    permissible value destroys the evidence that the vocabulary is short a value.
    """

    definition = classes.get(range_name)
    if not isinstance(definition, Mapping):
        return None
    value = ((definition.get("slot_usage") or {}).get("value") or {})
    ranges = schema_utils.attribute_ranges(value)
    named = [r for r in ranges if r in enums]
    if not named:
        return None
    values = list((enums[named[0]] or {}).get("permissible_values") or {})
    closed = value.get("range") in enums
    return values, closed, bool(value.get("multivalued"))


def render_schema(classes, enums, names: set[str], study_keep: list[str]) -> str:
    """One block per class: its description, then one line per attribute.

    Every class is rendered in schema declaration order so the reading order
    matches the YAML, and `Study` comes first because it is the record's shape.
    """

    out: list[str] = []
    order = ["Study"] + [n for n in classes if n in names and n != "Study"]

    for name in order:
        definition = classes.get(name)
        if not isinstance(definition, Mapping):
            continue
        attributes = schema_utils.attributes_for(classes, name)
        if name == "Study":
            attributes = {k: v for k, v in attributes.items() if k in study_keep}
        if not attributes:
            continue

        header = name
        if definition.get("is_a") and definition["is_a"] in names:
            header += f" (is_a: {definition['is_a']})"
        out.append(f"\n### {header}")
        if definition.get("description"):
            out.append(_wrap(definition["description"]))

        for attr, spec in attributes.items():
            spec = spec or {}
            kind = schema_utils.classify_slot(classes, attr, spec)
            ranges = schema_utils.attribute_ranges(spec) or ["string"]
            bits = [ranges[0]]
            if spec.get("multivalued"):
                bits.append("multivalued")
            if spec.get("required"):
                bits.append("REQUIRED")
            # Which of the three shapes a slot takes is the single most confusable
            # thing in this schema, and the model gets it wrong silently: pass 1
            # emitted `terms` as {"extraction_status": ..., "value": [ModelTerm]},
            # wrapping a nested record list as though it were a multivalued scalar.
            # So the shape is stated on the line rather than left to rule 4.
            if kind == "reference":
                bits.append(f"local_id of {ranges[0]}"
                            + (" — plain list of id strings" if spec.get("multivalued")
                               else " — plain id string"))
            elif kind == "nested":
                bits.append(f"nested {ranges[0]} record"
                            + ("s — a plain JSON LIST of objects, NOT an ExtractedValue wrapper"
                               if spec.get("multivalued")
                               else " — a plain JSON object, NOT an ExtractedValue wrapper"))

            line = f"- `{attr}` ({', '.join(bits)}): {_wrap(spec.get('description', ''))}"
            vocabulary = enum_of(classes, enums, ranges[0])
            if vocabulary:
                values, closed, multivalued = vocabulary
                joined = " | ".join(values)
                if closed:
                    line += (f"\n    value MUST be one of: {joined}"
                             " -- there is no other permitted answer.")
                else:
                    line += (f"\n    value is one of: {joined}"
                             " -- or the paper's own wording when none of them fits.")
                if multivalued:
                    line += " `value` is a LIST of these."
            out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------- pass-2 context

def entity_digest(classes: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    """Every local_id pass 1 assigned, at any depth, with the name it carries.

    Depth is the point. `Cell.term` points at a ModelTerm that lives under
    `model_estimations[].terms`, and `DecodingClass.condition` at a Condition under
    `tasks[].conditions`; a digest of the top-level lists alone leaves pass 2 with
    nothing to reference and it invents ids instead.

    Only ids and names: pass 1's full records would double the input for no gain,
    since pass 2 only needs something to point at.
    """

    found: dict[str, list[str]] = {}

    def name_of(entity: Mapping[str, Any]) -> str:
        for key in ("name", "label", "description", "source_label"):
            value = entity.get(key)
            if isinstance(value, Mapping) and isinstance(value.get("value"), str):
                return value["value"]
            if isinstance(value, str):
                return value
        return ""

    def visit(node: Any, class_name: str) -> None:
        if not isinstance(node, dict) or "extraction_status" in node:
            return
        if isinstance(node.get("local_id"), str):
            found.setdefault(class_name, []).append(
                f"{node['local_id']}: {name_of(node)}".rstrip(": "))
        # Resolve the payload's own class before reading its slots, or an entity nested
        # under a self-naming payload is missing from the digest and pass 2 is told to
        # emit `not_reported` for a reference that could have resolved.
        class_name = schema_utils.designated_type(classes, node, class_name)
        attributes = schema_utils.attributes_for(classes, class_name)
        for key, value in node.items():
            spec = attributes.get(key)
            if spec is None or schema_utils.classify_slot(classes, key, spec) != "nested":
                continue
            target = schema_utils.attribute_ranges(spec)
            if not target:
                continue
            for item in (value if isinstance(value, list) else [value]):
                visit(item, target[0])

    study_attributes = schema_utils.attributes_for(classes, "Study")
    body = dict(payload.get("study") or {})
    for key, value in payload.items():
        if key != "study":
            body[key] = value
    for attr, spec in study_attributes.items():
        if schema_utils.classify_slot(classes, attr, spec) != "nested":
            continue
        target = schema_utils.attribute_ranges(spec)
        value = body.get(attr)
        if not target or value is None:
            continue
        for item in (value if isinstance(value, list) else [value]):
            visit(item, target[0])

    if not found:
        return ""
    lines = ["\n## Entities already extracted",
             "Refer to these `local_id`s. Do NOT re-emit these records. If this list offers",
             "nothing suitable for a reference slot, emit `not_reported` for it -- never",
             "invent a local_id.\n"]
    for class_name in sorted(found):
        lines.append(f"{class_name}:")
        lines += [f"  {entry}" for entry in found[class_name]]
    return "\n".join(lines) + "\n"


def stage1_block(stage1: Mapping[str, Any], table_ids: Mapping[str, str]) -> str:
    """The analyses parsed from the result tables, grouped by the table reporting them.

    Grouping is not decoration: the same analysis name recurs across tables in the
    same paper (an ROI table and a whole-brain table reporting one contrast), and
    the table is the only thing that tells those apart.

    Space and statistic type come from the parser as normalized codes. They are
    offered as hints to confirm, not values to copy, because `coordinate_space`
    wants the paper's own wording.
    """

    analyses = stage1.get("analyses") or []
    if not analyses:
        return ""

    grouped: dict[str, list[tuple[int, dict]]] = {}
    for index, analysis in enumerate(analyses, start=1):
        grouped.setdefault(analysis.get("table_id") or "", []).append((index, analysis))

    lines = [
        "\n## Analyses already parsed from the result tables (stage 1)",
        f"These {len(analyses)} entries are a first pass over the coordinate tables, made",
        "without seeing the tables' rows. Work through them in order and emit one `analyses`",
        "entry for each, keeping the given name verbatim in `name.value` -- unless one of the",
        "two departures below applies. Never invent an entry for an effect no listing names.",
        "",
        "SPLIT one entry into several when the table distinguishes the rows it covers by a",
        "column the entry's name does not mention -- a frequency band, a diffusion parameter,",
        "a session, an occasion. The parse had the contrast name and not the rows, so a column",
        "can carry a factor it never saw. Each part is its own entry, named",
        "`<given name> (<level>)`, and every part keeps the same `tables`. The signal that this",
        "is needed: one entry would otherwise hold effects of opposite sign, forcing a single",
        "`unstated` cell where the paper reports a direction for each.",
        "",
        "OMIT an entry when its table reports no tested effect at all: an ROI or component",
        "definition, an atlas listing, coordinates cited from other papers, a stimulus list,",
        "demographics, descriptive means with no test. Such a table has no comparison, so",
        "`Effect.cells` cannot be filled honestly, and inventing a cell to satisfy it is worse",
        "than emitting no analysis. Say what the table is in that Table's",
        "`non_analysis_content` instead, and put the coordinates on the entity they locate --",
        "a Region's `description` -- rather than on a contrast that never produced them.",
        "Omitting is not for an effect that is merely awkward to encode: an effect the paper",
        "tested belongs in `analyses` however hard its shape.",
        "",
        "`tables` is REQUIRED on every entry you emit here. It is the bracketed",
        "`[table local_id: ...]` of the heading the entry sits under, copied verbatim, and it",
        "is the only link between the record and the rows the result was read off. Rule 4c",
        "does not apply: under one of these headings there is always something to point at.",
        "",
        "The `space` and `statistic` notes are what the results table showed -- confirm them",
        "against the paper's own wording rather than copying the code.\n",
    ]
    for table_id, entries in grouped.items():
        first = entries[0][1]
        label = first.get("table_label") or f"Table {first.get('table_number')}"
        caption = _wrap(first.get("table_caption") or "")[:160]
        lines.append(f'{label} — "{caption}"   [table local_id: {table_ids.get(table_id, table_id)}]')
        for number, analysis in entries:
            points = analysis.get("points") or []
            spaces = sorted({p.get("space") for p in points if p.get("space")})
            kinds = sorted({v.get("kind") for p in points for v in (p.get("values") or [])
                            if v.get("kind")})
            notes = [f"{len(points)} foci"]
            if spaces:
                notes.append("/".join(spaces))
            if kinds:
                notes.append("/".join(kinds))
            lines.append(f"  {number}. {analysis.get('name')}   · {' · '.join(notes)}")
            if analysis.get("description"):
                lines.append(f"       ({_wrap(analysis['description'])[:150]})")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- prompts

SYSTEM_HEAD = """You extract structured records from neuroimaging papers.

You are given a LinkML schema, the conventions document that governs it, and worked
encodings of twelve reported results. The schema's own `description:` fields are the
extraction instructions -- follow them exactly, including every statement about what must
not be inferred.

Rules that decide whether a record is usable:

1. Emit ONE JSON object and nothing else. No prose, no markdown fence.
2. These keys go at the TOP LEVEL of the object and nowhere else: {lists}.
   Do NOT also nest them inside "study" -- a list in both places is a list emitted twice.
   Everything else the Study class holds -- `description`, `design` -- goes inside a
   "study" object, and `arms`/`timepoints` go inside `study.design`. Do NOT emit
   `extraction_metadata`; the builder adds it from the source text.
3. {value_rule}
4. Three kinds of field look alike and are not. The schema line for each says which it is:
   a. a source-derived value -> an ExtractedValue wrapper (rule 3). When it is
      multivalued, that is ONE wrapper whose `value` is a list -- never a list of wrappers.
   b. a NESTED RECORD ("nested <Class> record" on its schema line) -> a plain JSON object,
      or a plain JSON list of objects when multivalued. It is NOT wrapped, has no
      `extraction_status`, and its own fields follow these same rules.
      `ModelEstimation.terms`, `ModelTerm.levels`, `Task.conditions`, `Effect.cells` and
      `Analysis.groups` are all of this kind.
   c. a CROSS-REFERENCE ("local_id of <Class>") -> a bare string, or a plain list of
      bare strings. Never wrapped. When there is nothing to point at, OMIT the key
      entirely: a reference is not an ExtractedValue, so it has no `not_reported` form,
      and neither `null` nor a wrapper is a valid value for one. Rule 5 does not apply
      to these.
5. A field the paper does not report takes
   {{"extraction_status": "not_reported"{absent_evidence}}}.
   Use it rather than omitting a REQUIRED field, and never invent a value to fill one.
6. `local_id` is a bare string you assign, unique within its class, referenced by other
   records. Every local_id referenced must exist.
7. Set `value_source` to "reported" when the value is the paper's own wording or number,
   and "generated" when you had to phrase it (a summary, a label the paper implies but
   never writes). A field whose schema line gives a closed vocabulary is almost always
   "generated": no paper writes "not_applicable".
8. Where a schema line states a closed vocabulary, no other answer is accepted. Where it
   offers the paper's own wording as a fallback, use it only when no listed value fits.
9. Two rules in the conventions document decide more of this record than any other, so
   read them before you start: the self-naming method payload
   (`AnalysisDetails.details_type`, `Acquisition.acquisition_type`) and what
   `Cell.direction` means, including when a level takes no cell at all.
10. A shape the schema alone does not settle is settled by the worked models. A
    comparison is a term with levels and a sign on each side -- never one column named
    after the comparison it was the subject of.
"""

VALUE_RULE_EVIDENCE = """Every source-derived value is an ExtractedValue wrapper:
   {"extraction_status": "extracted", "value": <value>, "value_source": "reported",
    "evidence": {"status": "present", "sets": [{"quotes": ["<verbatim span>"]}]}}
   A quote MUST be copied character-for-character from the paper. It is located in the
   source text by exact match; a paraphrased or reconstructed quote is dropped."""

VALUE_RULE_NO_EVIDENCE = """Every source-derived value is an ExtractedValue wrapper:
   {"extraction_status": "extracted", "value": <value>, "value_source": "reported"}
   DO NOT emit an `evidence` key anywhere. Supporting spans are added by a separate later
   pass. Spend your output on getting the values right and complete, not on quotation."""

MODE_NOTE = {
    "entities": """
This pass extracts the STUDY ENTITIES only. Do NOT emit `analyses` or `tables`: a separate
pass extracts the analyses and will refer to the `local_id`s you assign here, so every
entity needs one. Describe the model the authors estimated -- its terms, their levels, and
which conditions, cohorts, occasions, arms or regions those levels name -- even though the
contrasts themselves come later.

Occasions and cohorts are factors in exactly the sense conditions are: a study with no
paradigm still has a categorical term if it measured the same people twice, its levels
being the occasions, which `FactorLevel.timepoints` names. Do not let the absence of a
task decide that there is no factor. Each level's label is the source's own wording.

A Region is an entity in the sense a Group or a Task is, and THIS PASS IS THE ONLY PLACE
ONE CAN BE CREATED. Emit a Region for each place the study delimited: every ROI or mask an
analysis was restricted to, every connectivity seed and target, every atlas parcel used by
name, every component or cluster reused as a node, and every sphere whose centre the paper
gives. Each carries its own `definition_method` -- how *that* region was delimited -- and
its coordinates, radius or atlas belong in its `description`.

A paper that ran any ROI, seed, mask or parcel analysis and emits no `regions` leaves the
analyses pass with nothing to point `Analysis.regions` at. The ROI information is then not
misplaced but lost: there is no slot on Analysis for how a region was defined, so an
analysis restricted to a region it cannot name has no way to say it was restricted at all.
""",
    "analyses": """
This pass emits `analyses` and nothing else. The supporting entities were extracted
separately and are listed below with their local_ids; refer to them, do not re-emit them.

Two jobs, in this order. First settle the SET of analyses. The stage-1 listing below is a
first pass over the coordinate tables made without seeing their rows, so it is the starting
point and not the answer; the rules there say when one of its entries is really two and
when it is none. Then annotate each analysis you kept: its scope, measure, statistic,
effect cells, inference settings, method payload, and its links by local_id -- `tables`
among them, and `regions` where the analysis was restricted to any.
""",
}


def worked_models() -> str:
    """`representing-models.md` §5 -- the worked encodings -- for the prompt.

    The conventions document states the rules a term and a cell obey; §5 is the only
    place a whole encoding is shown end to end, and the only place shapes no rule
    reaches on its own appear: a factor over occasions in a study with no paradigm
    (§5.6), an ordered factor contrasted at its extremes (§5.7), a model split across
    stages (§5.12).

    Sliced rather than sent whole. §1-§4 restate what the conventions and the rendered
    `description:` fields already say, and §6 asks whether a paper fits the schema at
    all, which this pass does not decide.

    Raises rather than returning "" when the heading moves. The file is committed here,
    so an empty slice is a repo error, and announcing a section the prompt does not
    carry is worse than failing loudly.
    """

    text = MODELS.read_text(encoding="utf-8")
    # Up to the next `## ` heading. `### 5.1` and friends do not match it -- the
    # character after `##` is `#`, not a space -- so the subsections stay in.
    match = re.search(rf"^{re.escape(WORKED_MODELS_SECTION)}$.*?(?=^## |\Z)",
                      text, re.MULTILINE | re.DOTALL)
    if match is None:
        raise RuntimeError(
            f"{MODELS.name} has no {WORKED_MODELS_SECTION!r} heading: the worked models "
            "cannot be sliced out for the prompt. Renumbering the section means updating "
            "WORKED_MODELS_SECTION with it."
        )
    return match.group(0).rstrip()


def build_prompt(text: str, mode: str, evidence: bool, context: str) -> tuple[str, str]:
    classes = schema_utils.load_imported_classes(EXTRACTION_SCHEMA)
    enums = schema_utils.load_imported_classes(EXTRACTION_SCHEMA, "enums")
    names, study_keep = mode_classes(classes, mode)

    # Only the lists that sit directly on Study are offered as top-level payload keys.
    # `design.arms` and `design.timepoints` are reachable that way too, but naming them
    # here would contradict rule 2, and merge_payloads resolves a top-level `arms` by
    # assigning over `design.arms` -- so a payload carrying both silently loses one.
    payload_keys = [k for k, v in ENTITY_LISTS.items()
                    if "." not in v and v != "tables"
                    and (v == "analyses") == (mode == "analyses")]

    system = SYSTEM_HEAD.format(
        lists=", ".join(sorted(payload_keys)),
        value_rule=VALUE_RULE_EVIDENCE if evidence else VALUE_RULE_NO_EVIDENCE,
        absent_evidence=', "evidence": {"status": "not_applicable"}' if evidence else "",
    ) + MODE_NOTE[mode]

    user = (
        "# Conventions (extraction-readme.md)\n\n" + README.read_text(encoding="utf-8")
        + "\n\n# Worked models (representing-models.md)\n\n"
        + "Twelve reported results and the encoding each takes. Follow the shape of the\n"
        + "one this paper's result is closest to; do not invent a third when its wording\n"
        + "sits between two of them.\n\n"
        + worked_models()
        + "\n\n# Schema\n" + render_schema(classes, enums, names, study_keep)
        + context
        + "\n\n# Paper\n\n" + text
        + "\n\nEmit the JSON object now."
    )
    return system, user


def strip_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n", "", raw)
        raw = re.sub(r"\n```$", "", raw.strip())
    return raw.strip()


def extract(client, model: str, system: str, user: str, effort: str, max_out: int):
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }
    if effort:
        kwargs["reasoning_effort"] = effort
    if max_out:
        kwargs["max_completion_tokens"] = max_out
    response = client.chat.completions.create(**kwargs)
    choice = response.choices[0]
    raw = choice.message.content or ""
    usage = response.usage
    detail = getattr(usage, "completion_tokens_details", None)
    return json.loads(strip_fence(raw) or "{}"), {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "reasoning_tokens": getattr(detail, "reasoning_tokens", None) if detail else None,
        "finish_reason": choice.finish_reason,
    }


def normalize(payload: dict[str, Any], mode: str) -> tuple[dict[str, Any], list[str]]:
    """Move stray Study attributes under `study` so merge_payloads accepts them.

    Reported rather than silently corrected: a key landing here is a prompt problem
    worth seeing, not a quirk of the model to paper over.
    """

    notes: list[str] = []
    payload.pop("extraction_metadata", None)
    study = payload.get("study")
    if not isinstance(study, dict):
        study = {}

    # An entity list nested under `study` survives merge_payloads, but only until a
    # sibling empty list at the top level shadows it. Hoist it and say so: the model
    # emitting both shapes at once is a prompt problem worth seeing.
    for key in list(study):
        if key in ENTITY_LISTS and isinstance(study[key], list):
            hoisted = study.pop(key)
            if hoisted:
                if payload.get(key):
                    notes.append(f"collision: {key!r} emitted both top-level and under study")
                payload[key] = hoisted
                notes.append(f"hoisted {key!r} out of study to the top level")

    for key in list(payload):
        if key in ENTITY_LISTS or key == "study":
            continue
        study[key] = payload.pop(key)
        notes.append(f"moved top-level {key!r} under study")

    # arms/timepoints are accepted as top-level payload keys by merge_payloads, which
    # writes them to design.arms by assignment -- so a payload carrying both forms
    # loses one. Keep the nested form, which is where the prompt asks for them.
    design = study.get("design")
    if isinstance(design, dict):
        for key in ("arms", "timepoints"):
            if key in payload and design.get(key):
                payload.pop(key)
                notes.append(f"dropped top-level {key!r} in favour of study.design.{key}")

    if mode == "analyses":
        for key in list(payload):
            if key not in ("analyses", "study"):
                payload.pop(key)
                notes.append(f"dropped {key!r}: not this pass's output")
        payload.pop("study", None)
    if study and mode != "analyses":
        payload["study"] = study
    return payload, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", required=True)
    parser.add_argument("--text", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--mode", default="entities", choices=["entities", "analyses"])
    parser.add_argument("--no-evidence", action="store_true",
                        help="drop the quote contract; review/add_evidence.py adds spans")
    parser.add_argument("--stage1", type=Path, help="stage1/analyses.json (analyses mode)")
    parser.add_argument("--entities", type=Path, help="pass 1 payload (analyses mode)")
    parser.add_argument("--tables", type=Path,
                        help="stage1/table-map.json: {source table_id: Table local_id}")
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default="low")
    parser.add_argument("--max-out", type=int, default=48_000)
    parser.add_argument("--max-chars", type=int, default=200_000)
    parser.add_argument("--print-prompt", action="store_true", help="no API call")
    args = parser.parse_args()

    classes = schema_utils.load_imported_classes(EXTRACTION_SCHEMA)

    context = ""
    if args.mode == "analyses":
        if args.entities and args.entities.is_file():
            context += entity_digest(
                classes, json.loads(args.entities.read_text(encoding="utf-8")))
        table_ids: dict[str, str] = {}
        if args.tables and args.tables.is_file():
            # Flat {source table_id: local_id}. Kept out of the payload because
            # `source_table_id` is not a schema slot and the validator would reject it.
            table_ids = json.loads(args.tables.read_text(encoding="utf-8"))
        if args.stage1 and args.stage1.is_file():
            context += stage1_block(
                json.loads(args.stage1.read_text(encoding="utf-8")), table_ids)

    text = args.text.read_text(encoding="utf-8")[: args.max_chars]
    system, user = build_prompt(text, args.mode, not args.no_evidence, context)

    if args.print_prompt:
        print(system)
        print("=" * 70)
        print(user[: len(user) - len(text) - 40])
        print(f"\n[paper text omitted: {len(text):,} chars]")
        print(f"\nprompt ~{(len(system) + len(user)) // 4:,} tokens", file=sys.stderr)
        return 0

    if args.key_file:
        load_key_file(args.key_file)
    if not os.environ.get("OPENAI_API_KEY"):
        print("no OPENAI_API_KEY; pass --key-file", file=sys.stderr)
        return 2

    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"],
                    base_url=os.environ.get("OPENAI_API_GATEWAY"))

    started = time.time()
    payload, usage = extract(client, args.model, system, user, args.effort, args.max_out)
    payload, notes = normalize(payload, args.mode)

    destination = args.out_dir / args.paper
    destination.mkdir(parents=True, exist_ok=True)
    (destination / f"{args.mode}.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    counts = {k: len(payload.get(k) or []) for k in ENTITY_LISTS if payload.get(k)}
    print(f"{args.paper}/{args.mode}: {usage['prompt_tokens']}->{usage['completion_tokens']} tok "
          f"(reasoning {usage['reasoning_tokens']}) in {time.time() - started:.0f}s "
          f"[{usage['finish_reason']}]  {counts}")
    for note in notes:
        print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
