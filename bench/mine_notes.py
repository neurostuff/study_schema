#!/usr/bin/env python3
"""Cluster the schema's own expressivity complaints into a named gap list.

The extraction schema asks the model to record what it could not represent:

  Analysis.model_representation_notes  — "Model components this schema could not represent,
                                          verbatim -- ra[ndom effects, ...]"
  Analysis.not_structurable            — the effect is grounded but has no structural home

Across the benchmark runs, 43 of 60 papers populated the first. That is a free expressivity study
sitting in the output, and this reads it out.

The distinction that makes the report actionable: for each gap, could an EXISTING field have held
it? If yes it is a prompt or guidance problem. If no it is a schema change. The clustering pass is
given the full field inventory and has to name a candidate field or say `none`.

    python3 bench/mine_notes.py --dir bench/runs2 --dry-run     # extract and dedupe only
    python3 bench/mine_notes.py --dir bench/runs2 --key-file ~/.keys/portkey.key \
        --out bench/EXPRESSIVITY-GAPS.md
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SLUG = "@psyc-aid338-ope-333f18/"

CLUSTER_SCHEMA = {
    "name": "gap_clusters", "strict": True,
    "schema": {
        "type": "object", "additionalProperties": False,
        "required": ["clusters"],
        "properties": {"clusters": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "what_the_papers_report", "note_indices",
                         "existing_field", "verdict", "suggested_change"],
            "properties": {
                "name": {"type": "string"},
                "what_the_papers_report": {"type": "string"},
                "note_indices": {"type": "array", "items": {"type": "integer"}},
                "existing_field": {
                    "type": "string",
                    "description": "Class.field that could hold this, or 'none'"},
                "verdict": {"type": "string",
                            "enum": ["guidance_problem", "schema_gap", "out_of_scope"]},
                "suggested_change": {"type": "string"},
            }}}},
    },
}


def field_inventory():
    d = yaml.safe_load((REPO / "neuroimaging-study-extraction.yaml").read_text())
    lines = []
    for cls, c in d["classes"].items():
        for f, a in (c.get("attributes") or {}).items():
            if f == "local_id":
                continue
            desc = (a.get("description") or "").split(".")[0][:90]
            lines.append(f"  {cls}.{f}: {desc}")
    return "\n".join(lines)


def collect(rundir):
    """Unique (case_id, note) pairs, plus not_structurable payloads."""
    notes, nostruct = collections.defaultdict(set), []
    for f in glob.glob(f"{rundir}/*__*/*.json"):
        d = json.loads(Path(f).read_text())
        r = d.get("record")
        if not r or d.get("parse_error"):
            continue
        cid = d["case_id"]
        for a in (r.get("analyses") or []):
            if not isinstance(a, dict):
                continue
            v = a.get("model_representation_notes")
            if isinstance(v, dict) and isinstance(v.get("value"), str) and v["value"].strip():
                notes[cid].add(re.sub(r"\s+", " ", v["value"]).strip())
            if a.get("not_structurable"):
                nostruct.append((cid, a["not_structurable"]))
    return notes, nostruct


def dedupe_near(pairs):
    """Drop notes that are a prefix-or-superset of another from the same paper.

    The four effort cells produce near-identical notes per paper; keeping the longest of each
    overlapping family stops one paper's phrasing from dominating a cluster by repetition.
    """
    out = []
    by_case = collections.defaultdict(list)
    for c, n in pairs:
        by_case[c].append(n)
    for c, ns in by_case.items():
        ns.sort(key=len, reverse=True)
        kept = []
        for n in ns:
            head = " ".join(n.lower().split()[:8])
            if any(head in k.lower() for k in kept):
                continue
            kept.append(n)
        out += [(c, n) for n in kept]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="bench/runs2")
    ap.add_argument("--out", default="bench/EXPRESSIVITY-GAPS.md")
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--model", default="gpt-5.6-luna")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    notes, nostruct = collect(args.dir)
    pairs = dedupe_near([(c, n) for c, v in notes.items() for n in v])
    n_papers = len({c for c, _ in pairs})
    total_papers = len({Path(f).stem for f in glob.glob(f"{args.dir}/*__*/*.json")})
    print(f"{len(pairs)} notes after near-dedupe, from {n_papers} of {total_papers} papers")
    print(f"{len(nostruct)} not_structurable payloads")
    if args.dry_run:
        for i, (c, n) in enumerate(pairs):
            print(f"  [{i}] {c}: {n[:120]}")
        return 0

    if args.key_file:
        for raw in Path(args.key_file).expanduser().read_text().splitlines():
            line = raw.strip().removeprefix("export ").strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip().strip("'\"")
    if not os.environ.get("OPENAI_API_KEY"):
        print("no OPENAI_API_KEY; pass --key-file or use --dry-run", file=sys.stderr)
        return 2
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"],
                    base_url=os.environ.get("OPENAI_API_GATEWAY"))

    listing = "\n".join(f"[{i}] {n}" for i, (_, n) in enumerate(pairs))
    prompt = f"""These are notes an extraction model wrote in the field
`Analysis.model_representation_notes`, whose description is "Model components this schema could not
represent, verbatim". Each note is something a neuroimaging paper reported that the extractor could
not put anywhere in the schema.

Cluster them into distinct, named gaps. Merge notes describing the same underlying gap even when
worded differently. Ignore notes that are not really gaps — some models write a general methods
summary here instead of a genuine complaint; put those in an `out_of_scope` cluster.

For each cluster:
  - `existing_field`: the ONE `Class.field` from the inventory below that could already hold this,
    or the literal string "none". Be strict: a field whose description does not cover the content
    is not a candidate.
  - `verdict`: `guidance_problem` if an existing field could hold it and the extractor simply did
    not use it; `schema_gap` if no field can; `out_of_scope` if it is not a real gap.
  - `suggested_change`: one sentence. For a guidance problem say what the prompt should say; for a
    schema gap name the field you would add and where.

SCHEMA FIELD INVENTORY
{field_inventory()}

NOTES
{listing}
"""
    r = client.chat.completions.create(
        model=SLUG + args.model, reasoning_effort="high", service_tier="flex",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_schema", "json_schema": CLUSTER_SCHEMA},
        max_completion_tokens=16000)
    u = r.usage.model_dump()
    print(f"clustering call: in={u['prompt_tokens']} out={u['completion_tokens']} "
          f"reas={(u.get('completion_tokens_details') or {}).get('reasoning_tokens')}")
    clusters = json.loads(r.choices[0].message.content)["clusters"]

    # frequency by distinct papers, not by note count: one voluble paper should not rank a gap
    for cl in clusters:
        idx = [i for i in cl["note_indices"] if 0 <= i < len(pairs)]
        cl["_papers"] = sorted({pairs[i][0] for i in idx})
        cl["_examples"] = [pairs[i][1] for i in idx[:3]]
    clusters.sort(key=lambda c: (-len(c["_papers"]), c["verdict"]))

    order = {"schema_gap": 0, "guidance_problem": 1, "out_of_scope": 2}
    lines = [
        "# Expressivity gaps, mined from the schema's own complaints", "",
        f"**Source:** `Analysis.model_representation_notes` across `{args.dir}` — "
        f"**{len(pairs)} notes from {n_papers} of {total_papers} papers ({100*n_papers/max(total_papers,1):.0f}%)**, "
        f"plus {len(nostruct)} `not_structurable` payloads.", "",
        "The schema asks the extractor to record what it could not represent, so this is a free "
        "expressivity study. Frequency is counted in **distinct papers**, not notes, so one "
        "voluble paper cannot rank a gap.", "",
        "`schema_gap` = no existing field can hold it. `guidance_problem` = a field exists and "
        "was not used, so fix the prompt, not the schema.", "",
        "| gap | papers | verdict | existing field | change |",
        "|---|---:|---|---|---|",
    ]
    for cl in sorted(clusters, key=lambda c: (order.get(c["verdict"], 3), -len(c["_papers"]))):
        lines.append(f"| **{cl['name']}** | {len(cl['_papers'])} | `{cl['verdict']}` | "
                     f"`{cl['existing_field']}` | {cl['suggested_change']} |")
    lines += ["", "---", "", "## Detail", ""]
    for cl in sorted(clusters, key=lambda c: (order.get(c["verdict"], 3), -len(c["_papers"]))):
        lines += [f"### {cl['name']} — {len(cl['_papers'])} papers · `{cl['verdict']}`", "",
                  cl["what_the_papers_report"], "",
                  f"- **Could an existing field hold it?** `{cl['existing_field']}`",
                  f"- **Change:** {cl['suggested_change']}",
                  f"- **Papers:** {', '.join(cl['_papers'][:10])}"
                  + (" …" if len(cl["_papers"]) > 10 else ""), "", "Examples:", ""]
        for e in cl["_examples"]:
            lines.append(f"> {e}")
            lines.append("")
    if nostruct:
        lines += ["---", "", "## `not_structurable` payloads", "",
                  "Effects the extractor declined to structure at all — rare, and worth reading "
                  "individually rather than clustering.", ""]
        for cid, p in nostruct:
            lines.append(f"- **{cid}**: `{json.dumps(p)[:260]}`")
    Path(args.out).write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out} — {len(clusters)} clusters")
    for cl in clusters:
        print(f"   {len(cl['_papers']):>3} papers  {cl['verdict']:<18} {cl['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
