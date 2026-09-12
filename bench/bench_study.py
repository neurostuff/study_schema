#!/usr/bin/env python3
"""Benchmark whole-Study extraction: one call per paper, every analysis at once.

This is the shape production will use and the shape the previous benchmark did NOT test. The
alpha.11 harness issued one call per (paper, analysis) with the analysis label handed to it,
which isolated annotation accuracy but told us nothing about whether a model finds all the
analyses on its own — and it made cost look like one analysis per paper. Papers report several
(5.40 mean in the pmc20 parser reference, max 29), so both numbers were wrong in the same
direction.

Output is `json_object`, not a constrained schema: OpenAI strict mode caps `$ref`-expanded
nesting at 5 levels and this schema reaches 13, because the ExtractedValue -> Evidence ->
EvidenceSet -> EvidenceSpan wrapper is 6 on its own. Conformance therefore has to be scored
rather than guaranteed — which `bench/score_study.py` does, alongside checking every evidence
span against the source text it was supposed to come from.

    python3 bench/bench_study.py --mock --limit 3
    python3 bench/bench_study.py --key-file ~/.keys/portkey.key --samples pmc20 --limit 6
    python3 bench/bench_study.py --key-file ~/.keys/portkey.key --configs luna-high --workers 6
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources                                                       # noqa: E402
from schema_prompt import render                                     # noqa: E402

SLUG = "@psyc-aid338-ope-333f18/"

CONFIGS = {
    "luna-low":   ("gpt-5.6-luna", "low"),
    "luna-high":  ("gpt-5.6-luna", "high"),
    "terra-low":  ("gpt-5.6-terra", "low"),
    "terra-high": ("gpt-5.6-terra", "high"),
    "mini-low":   ("gpt-5-mini-2025-08-07", "low"),
}
DEFAULT_CONFIGS = ["luna-low", "luna-high"]

SYSTEM = ("You are a neuroimaging methods curator. You read one paper and return one structured "
          "extraction record as JSON, following the contract exactly.")


def build_messages(text: str, schema_text: str, entities: str = ""):
    user = f"""{schema_text}

PAPER TEXT
<<<
{text}
>>>
{entities}
Return the JSON extraction record for this paper now."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def entity_digest(rec: dict) -> str:
    """The local_ids and names pass 1 assigned, so pass 2 can reference them instead of guessing.

    Only ids and names: sending pass 1's full records back would double the input for no gain,
    since pass 2 only needs something to point at.
    """
    if not isinstance(rec, dict):
        return ""
    lines = []
    for key in ("groups", "experiments", "acquisitions", "preprocessings", "model_estimations",
                "conditions", "terms", "assessments"):
        for e in (rec.get(key) or []):
            if not isinstance(e, dict) or not e.get("local_id"):
                continue
            nm = ""
            for cand in ("name", "task_name"):
                v = e.get(cand)
                if isinstance(v, dict) and isinstance(v.get("value"), str):
                    nm = v["value"]
                    break
                if isinstance(v, str):
                    nm = v
                    break
            lines.append(f"  {key[:-1]} {e['local_id']}: {nm}")
    if not lines:
        return ""
    return ("\nENTITIES ALREADY EXTRACTED — refer to these local_ids, do not re-emit them\n"
            + "\n".join(lines) + "\n")


def mock_record(case):
    """Plumbing check with no API call. Echoes the gold analysis names so the scorer has
    something to match, and marks itself so it can never be mistaken for a result."""
    names = (case["gold"].get("names") or [])[:3]
    ev = {"status": "not_found"}
    return {"_mock": True,
            "extraction_metadata": {"schema_version": "mock"},
            "analyses": [{"local_id": f"a{i}",
                          "name": {"extraction_status": "extracted", "value": n,
                                   "value_source": "reported", "evidence": ev}}
                         for i, n in enumerate(names)],
            "groups": [], "experiments": [], "conditions": [], "tables": []}


def load_key_file(path):
    """Read a shell-style env file into os.environ. Values are never printed."""
    names = []
    for raw in Path(path).expanduser().read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip("'\"")
        if k and v:
            os.environ[k] = v
            names.append(k)
    return names


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="bench/runs")
    ap.add_argument("--samples", default="pmc20,nmb")
    ap.add_argument("--configs", default=",".join(DEFAULT_CONFIGS))
    ap.add_argument("--limit", type=int, default=0, help="cap total papers (per sample for nmb)")
    ap.add_argument("--nmb-limit", type=int, default=60)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--service-tier", default="flex",
                    choices=["auto", "default", "flex", "priority"])
    ap.add_argument("--mode", default="schema",
                    choices=["schema", "analyses_first", "two_pass", "linked"],
                    help="schema = Study fields in declared order (analyses 12th of 14); "
                         "analyses_first = reordered so the analyses lead; "
                         "two_pass = entities call then an analyses call given those "
                         "local_ids; linked = entities call then an ANNOTATE call given stage 1's "
                         "already-parsed analysis list (the production shape)")
    ap.add_argument("--no-evidence", action="store_true",
                    help="drop the evidence contract; a later cheap pass adds spans. Evidence is "
                         "57%% of output tokens and is the payload high reasoning choked on.")
    ap.add_argument("--effort-pass1", default=None,
                    help="override reasoning effort for pass 1 (the schema/entity extraction) "
                         "only. This is the one pass worth testing at high effort.")
    ap.add_argument("--effort-pass2", default=None, help="override reasoning effort for pass 2")
    ap.add_argument("--abort-after", type=int, default=4,
                    help="stop the run after this many consecutive empty/unparseable responses; "
                         "a broken configuration burns the full token cap on every call")
    ap.add_argument("--brief-schema", action="store_true",
                    help="first sentence of each field description only (~1.7k fewer tokens)")
    ap.add_argument("--max-out", type=int, default=32000)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--redo", action="store_true")
    args = ap.parse_args()

    configs = [c for c in args.configs.split(",") if c]
    bad = [c for c in configs if c not in CONFIGS]
    if bad:
        print(f"unknown config(s) {bad}; known {list(CONFIGS)}", file=sys.stderr)
        return 2

    cases = sources.load_cases(args.samples.split(","), args.nmb_limit)
    if args.limit:
        cases = cases[:args.limit]
    ev = not args.no_evidence
    if args.mode in ("two_pass", "linked"):
        schema_text = render(brief=args.brief_schema, mode="entities_only", evidence=ev)
        schema_text2 = render(brief=args.brief_schema, evidence=ev,
                              mode="annotate_only" if args.mode == "linked" else "analyses_only")
    else:
        schema_text = render(brief=args.brief_schema, mode=args.mode, evidence=ev)
        schema_text2 = None
    tag = args.mode + ("_noev" if args.no_evidence else "")
    if args.effort_pass1 or args.effort_pass2:
        tag += f"_p1{args.effort_pass1 or '-'}p2{args.effort_pass2 or '-'}"
    outdir = Path(args.out)

    # The exact text each model saw is needed to verify evidence spans later, so it is written
    # once per case rather than re-derived (section slicing could change between runs).
    txtdir = outdir / "_sources"
    txtdir.mkdir(parents=True, exist_ok=True)
    for c in cases:
        f = txtdir / f"{c['case_id']}.txt"
        if not f.exists():
            f.write_text(c["text"])
    (outdir / "_gold.json").write_text(json.dumps(
        {c["case_id"]: {"gold": c["gold"], "gold_kind": c["gold_kind"], "sample": c["sample"],
                        "pmid": c.get("pmid"), "pmcid": c.get("pmcid")} for c in cases}, indent=1))

    chars = sorted(len(c["text"]) for c in cases)
    print(f"{len(cases)} papers ({args.samples}) · text chars median "
          f"{chars[len(chars)//2]:,} max {chars[-1]:,} · schema prompt "
          f"{len(schema_text):,} chars")

    client = None
    if not args.mock:
        if args.key_file:
            print(f"loaded from {args.key_file}: {', '.join(load_key_file(args.key_file))}")
        if not os.environ.get("OPENAI_API_KEY"):
            print("no OPENAI_API_KEY; pass --key-file or use --mock", file=sys.stderr)
            return 2
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"],
                        base_url=os.environ.get("OPENAI_API_GATEWAY"))
        print(f"tier={args.service_tier} gateway="
              f"{os.environ.get('OPENAI_API_GATEWAY','').split('//')[-1].split('/')[0]}")

    jobs = [(cfg, c) for cfg in configs for c in cases]
    todo = [(cfg, c) for cfg, c in jobs
            if args.redo or not (outdir / f"{cfg}__{tag}" /
                                 f"{c['case_id']}.json").exists()]
    print(f"{len(jobs)} jobs, {len(jobs)-len(todo)} done, running {len(todo)}\n")
    lock, done, streak, stop = threading.Lock(), [0], [0], [False]

    def run(job):
        if stop[0]:
            return
        cfg, case = job
        model, effort = CONFIGS[cfg]
        d = outdir / f"{cfg}__{tag}"
        d.mkdir(parents=True, exist_ok=True)
        rec = {"case_id": case["case_id"], "sample": case["sample"], "config": cfg,
               "mode": args.mode, "tag": tag, "no_evidence": args.no_evidence,
               "model": model, "effort": effort,
               "service_tier": args.service_tier,
               "gold_n_analyses": case["gold"]["n_analyses"]}
        try:
            if args.mock:
                rec["record"] = mock_record(case)
                rec["usage"] = {}
            else:
                def call(stext, entities="", eff=None):
                    r = client.chat.completions.create(
                        model=SLUG + model,
                        messages=build_messages(case["text"], stext, entities),
                        reasoning_effort=eff or effort,
                        response_format={"type": "json_object"},
                        max_completion_tokens=args.max_out,
                        service_tier=args.service_tier,
                    )
                    ch = r.choices[0]
                    raw = ch.message.content or ""
                    u = r.usage.model_dump()
                    use = {"prompt": u.get("prompt_tokens"),
                           "completion": u.get("completion_tokens"),
                           "reasoning": (u.get("completion_tokens_details") or {})
                           .get("reasoning_tokens"),
                           "cached": (u.get("prompt_tokens_details") or {}).get("cached_tokens")}
                    try:
                        return json.loads(raw), use, ch.finish_reason, len(raw), None, \
                            getattr(r, "service_tier", None)
                    except json.JSONDecodeError as e:
                        return None, use, ch.finish_reason, len(raw), \
                            f"{type(e).__name__}: {e}", getattr(r, "service_tier", None)

                if args.mode in ("two_pass", "linked"):
                    r1, u1, fr1, rc1, pe1, tier = call(schema_text, eff=args.effort_pass1)
                    extra = entity_digest(r1 or {})
                    if args.mode == "linked":
                        names = sources.stage1_analyses(case)
                        extra += ("\nANALYSES ALREADY PARSED FROM THE RESULT TABLES (stage 1) — "
                                  "annotate exactly these, in this order\n"
                                  + "\n".join(f"  {i+1}. {n}" for i, n in enumerate(names)) + "\n")
                        rec["stage1_names"] = names
                    r2, u2, fr2, rc2, pe2, _ = call(schema_text2, extra,
                                                    eff=args.effort_pass2)
                    merged = dict(r1 or {})
                    for k in ("analyses", "tables"):
                        if (r2 or {}).get(k):
                            merged[k] = r2[k]
                    rec["record"] = merged if (r1 or r2) else None
                    rec["usage"] = {k: (u1.get(k) or 0) + (u2.get(k) or 0)
                                    for k in ("prompt", "completion", "reasoning", "cached")}
                    rec["usage_pass1"], rec["usage_pass2"] = u1, u2
                    rec["finish_reason"] = f"{fr1}/{fr2}"
                    rec["raw_chars"] = rc1 + rc2
                    if pe1 or pe2:
                        rec["parse_error"] = f"pass1={pe1} pass2={pe2}"
                        if not (r1 or r2):
                            rec["record"] = None
                    rec["service_tier_echo"] = tier
                else:
                    r1, u1, fr1, rc1, pe1, tier = call(schema_text)
                    rec["record"], rec["usage"] = r1, u1
                    rec["finish_reason"], rec["raw_chars"] = fr1, rc1
                    rec["service_tier_echo"] = tier
                    if pe1:
                        rec["parse_error"] = pe1
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        (d / f"{case['case_id']}.json").write_text(json.dumps(rec, indent=1))
        with lock:
            done[0] += 1
            empty = bool(rec.get("parse_error")) and not rec.get("record")
            streak[0] = streak[0] + 1 if empty else 0
            if streak[0] >= args.abort_after and not stop[0]:
                stop[0] = True
                print(f"\n  ABORTING: {streak[0]} consecutive empty responses. This "
                      f"configuration is emitting reasoning up to the token cap and no content; "
                      f"raise --max-out or lower the reasoning effort.\n", flush=True)
            u = rec.get("usage") or {}
            n = len(((rec.get("record") or {}).get("analyses")) or []) \
                if rec.get("record") else "-"
            status = ("ERR " + rec["error"][:60]) if rec.get("error") else \
                ("TRUNC/PARSE " + rec.get("parse_error", "")[:40]) if rec.get("parse_error") else \
                f"in={u.get('prompt')} out={u.get('completion')} reas={u.get('reasoning')} " \
                f"analyses={n}/{rec['gold_n_analyses']}"
            print(f"[{done[0]}/{len(todo)}] {cfg}/{args.mode:<14} "
                  f"{case['case_id']:<18} {status}", flush=True)

    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(run, todo))

    print(f"\nwrote to {outdir}")
    print("score with: python3 bench/score_study.py --dir " + str(outdir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
