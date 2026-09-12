#!/usr/bin/env python3
"""Score whole-Study extraction runs.

There is no gold in the extraction schema's own format, so this scores the four things that can
be measured objectively today, and says plainly what each one does and does not cover.

  1. ANALYSIS ENUMERATION — the question "does it find every analysis at once?"
     Matched against NiMADS gold names (neurometabench) or Autonima's parser output (pmc20).
     Recall is the meaningful number. Precision is NOT, for the nmb sample: the gold holds only
     the contrasts a meta-analysis selected, so a correct extra analysis looks like a false
     positive. Reported anyway, labelled.

  2. EVIDENCE GROUNDING — every `evidence.sets[].spans[].text` must occur in the source text the
     model was given. Needs no gold at all and catches invented quotations, which is the failure
     mode that matters most for an evidence-first schema.

  3. CONFORMANCE — output is `json_object` because strict mode cannot express this schema
     (nesting 13 vs a cap of 5), so structure is checked here: unknown fields, wrapper shape,
     dangling local_id references.

  4. FILL RATE by review priority — of the fields `storage-parameter-priorities.yaml` marks
     priority 0, how many came back `extracted` rather than `not_reported`?

Cost is computed per paper from measured usage, at the flex short-context rates.

    python3 bench/score_study.py --dir bench/runs
    python3 bench/score_study.py --dir bench/runs --per-sample
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import statistics as st
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

# flex tier, short context: (input, cached input, output) $ per 1M tokens
PRICES = {
    "gpt-5.6-luna":          (0.10, 0.01, 0.60),
    "gpt-5.6-terra":         (1.00, 0.10, 6.00),
    "gpt-5.6-sol":           (2.50, 0.25, 15.00),
    "gpt-5.4-mini":          (0.375, 0.0375, 2.25),
    "gpt-5-mini-2025-08-07": (0.25, 0.025, 2.00),      # no flex listing; standard rate
}
NO_FLEX = {"gpt-5-mini-2025-08-07"}

DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−⁃"), "-")
OPPOSED = [("increase", "decrease"), ("positive", "negative"), ("greater", "less"),
           ("activation", "deactivation"), ("high", "low"), ("more", "fewer")]


def norm(s):
    s = (s or "").translate(DASHES).lower()
    s = re.sub(r"[^a-z0-9<>+\- ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def sides(name):
    """Split 'A > B' into its two sides so a contrast and its reverse do not match."""
    for op in (">", "<", " vs ", " versus ", " minus ", "-"):
        if op in name:
            a, _, b = name.partition(op)
            if a.strip() and b.strip():
                return a.strip(), b.strip(), op
    return None


def name_score(a, b):
    """Token overlap, penalised when the two names are reversed or semantically opposed.

    A contrast and its reverse share every token, so plain overlap scores them identically -
    which is exactly the mistake that matters when 15% of papers report both directions.
    """
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    base = len(ta & tb) / max(len(ta | tb), 1)
    sa, sb = sides(na), sides(nb)
    if sa and sb:
        fwd = (len(set(sa[0].split()) & set(sb[0].split()))
               + len(set(sa[1].split()) & set(sb[1].split())))
        rev = (len(set(sa[0].split()) & set(sb[1].split()))
               + len(set(sa[1].split()) & set(sb[0].split())))
        if rev > fwd:
            base *= 0.4                                   # reversed contrast
    for x, y in OPPOSED:
        if (x in na) != (x in nb) and (y in na) != (y in nb):
            base *= 0.5
    return base


def greedy_match(gold_names, pred_names, thresh=0.5):
    """One-to-one greedy match on best score first."""
    pairs = sorted(((name_score(g, p), gi, pi)
                    for gi, g in enumerate(gold_names)
                    for pi, p in enumerate(pred_names)), reverse=True)
    gused, pused, hits = set(), set(), []
    for sc, gi, pi in pairs:
        if sc < thresh or gi in gused or pi in pused:
            continue
        gused.add(gi); pused.add(pi); hits.append((gi, pi, sc))
    return hits


# ------------------------------------------------------------------ record walking

def ev_value(node):
    """A wrapper's extracted value, or None if absent/not_reported."""
    if isinstance(node, dict):
        if node.get("extraction_status") == "not_reported":
            return None
        if "value" in node:
            return node["value"]
    return None


def analyses_of(rec):
    return [a for a in (rec.get("analyses") or []) if isinstance(a, dict)]


def analysis_names(rec):
    out = []
    for a in analyses_of(rec):
        n = a.get("name")
        v = ev_value(n) if isinstance(n, dict) else n
        out.append(v if isinstance(v, str) else "")
    return out


def walk_wrappers(node, path="$"):
    """Yield (path, wrapper_dict) for every ExtractedValue-shaped object in the record."""
    if isinstance(node, dict):
        if "extraction_status" in node or ("evidence" in node and "value" in node):
            yield path, node
        for k, v in node.items():
            yield from walk_wrappers(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_wrappers(v, f"{path}[{i}]")


def spans_of(wrapper):
    ev = wrapper.get("evidence")
    if not isinstance(ev, dict):
        return []
    out = []
    for s in (ev.get("sets") or []):
        if isinstance(s, dict):
            for sp in (s.get("spans") or []):
                if isinstance(sp, dict) and isinstance(sp.get("text"), str):
                    out.append(sp["text"])
    return out


def norm_ws(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


# ------------------------------------------------------------------ conformance

def known_fields():
    d = yaml.safe_load((REPO / "neuroimaging-study-extraction.yaml").read_text())
    return {c: set((v.get("attributes") or {})) for c, v in d["classes"].items()}


ENTITY_LISTS = {"groups": "Group", "experiments": "Experiment", "acquisitions": "Acquisition",
                "preprocessings": "Preprocessing", "model_estimations": "ModelEstimation",
                "conditions": "Condition", "tables": "Table", "terms": "Term",
                "assessments": "Assessment", "analyses": "Analysis"}


def conformance(rec, KF):
    """Unknown attribute names, and analysis local_id references that point nowhere."""
    unknown, dangling = [], []
    defined = set()
    for key, cls in ENTITY_LISTS.items():
        for e in (rec.get(key) or []):
            if isinstance(e, dict):
                if e.get("local_id"):
                    defined.add(str(e["local_id"]))
                for f in e:
                    if f not in KF.get(cls, set()) and f != "local_id":
                        unknown.append(f"{cls}.{f}")
    for f in rec:
        if f not in KF["Study"]:
            unknown.append(f"Study.{f}")
    for a in analyses_of(rec):
        for field in ("acquisitions", "experiments", "terms", "tables", "assessments"):
            for ref in (a.get(field) or []):
                if isinstance(ref, str) and ref not in defined:
                    dangling.append(f"{field}:{ref}")
        for field in ("model_estimation", "preprocessing"):
            ref = a.get(field)
            if isinstance(ref, str) and ref and ref not in defined:
                dangling.append(f"{field}:{ref}")
    return unknown, dangling


def priority_zero_fields():
    p = yaml.safe_load((REPO / "storage-parameter-priorities.yaml").read_text())
    out = collections.defaultdict(set)
    for cls, fields in (p or {}).items():
        for f, pr in (fields or {}).items():
            if str(pr) == "0":
                out[cls].add(f)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="bench/runs")
    ap.add_argument("--per-sample", action="store_true")
    ap.add_argument("--configs", default=None)
    args = ap.parse_args()

    root = Path(args.dir)
    gold = json.loads((root / "_gold.json").read_text())
    KF, P0 = known_fields(), priority_zero_fields()
    configs = sorted(d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith("_"))
    if args.configs:
        want = set(args.configs.split(","))
        configs = [c for c in configs if c in want]
    if not configs:
        print(f"no config dirs under {root}", file=sys.stderr)
        return 2

    rows = {}
    for cfg in configs:
        R = {"n": 0, "err": 0, "trunc": 0, "recall": [], "prec": [], "npred": [], "ngold": [],
             "spans": 0, "spans_ok": 0, "papers_span_clean": 0, "papers_with_spans": 0,
             "unknown": collections.Counter(), "dangling": 0, "cost": [], "usage": collections.defaultdict(list),
             "p0_extracted": 0, "p0_total": 0, "wrappers": 0, "wrappers_bad": 0,
             "by_sample": collections.defaultdict(lambda: {"recall": [], "npred": [], "ngold": []}),
             "model": None}
        for f in sorted((root / cfg).glob("*.json")):
            d = json.loads(f.read_text())
            cid = d["case_id"]
            g = gold.get(cid)
            if not g:
                continue
            R["model"] = d.get("model")
            if d.get("error"):
                R["err"] += 1
                continue
            if d.get("parse_error"):
                R["trunc"] += 1
                continue
            rec = d.get("record") or {}
            R["n"] += 1
            u = d.get("usage") or {}
            for k, v in u.items():
                if v is not None:
                    R["usage"][k].append(v)
            pr = PRICES.get(d.get("model"))
            if pr and u.get("prompt"):
                pin, pc, pout = pr
                cached = u.get("cached") or 0
                R["cost"].append(((u["prompt"] - cached) * pin + cached * pc
                                  + (u.get("completion") or 0) * pout) / 1e6)

            # 1. analysis enumeration
            gnames = g["gold"].get("names") or []
            pnames = [n for n in analysis_names(rec) if n]
            hits = greedy_match(gnames, pnames)
            rec_r = len(hits) / len(gnames) if gnames else None
            if rec_r is not None:
                R["recall"].append(rec_r)
                R["by_sample"][g["sample"]]["recall"].append(rec_r)
            if pnames:
                R["prec"].append(len(hits) / len(pnames))
            R["npred"].append(len(analyses_of(rec)))
            R["ngold"].append(g["gold"]["n_analyses"])
            R["by_sample"][g["sample"]]["npred"].append(len(analyses_of(rec)))
            R["by_sample"][g["sample"]]["ngold"].append(g["gold"]["n_analyses"])

            # 2. evidence grounding
            src = norm_ws((root / "_sources" / f"{cid}.txt").read_text())
            n_sp = n_ok = 0
            for _, w in walk_wrappers(rec):
                R["wrappers"] += 1
                if "extraction_status" not in w:
                    R["wrappers_bad"] += 1
                for s in spans_of(w):
                    n_sp += 1
                    if norm_ws(s) and norm_ws(s) in src:
                        n_ok += 1
            R["spans"] += n_sp
            R["spans_ok"] += n_ok
            if n_sp:
                R["papers_with_spans"] += 1
                if n_ok == n_sp:
                    R["papers_span_clean"] += 1

            # 3. conformance
            unk, dang = conformance(rec, KF)
            R["unknown"].update(unk)
            R["dangling"] += len(dang)

            # 4. priority-0 fill rate
            for key, cls in ENTITY_LISTS.items():
                for e in (rec.get(key) or []):
                    if not isinstance(e, dict):
                        continue
                    for field in P0.get(cls, ()):
                        R["p0_total"] += 1
                        if ev_value(e.get(field)) not in (None, "", []):
                            R["p0_extracted"] += 1
            for field in P0.get("Study", ()):
                R["p0_total"] += 1
                if ev_value(rec.get(field)) not in (None, "", []):
                    R["p0_extracted"] += 1
        rows[cfg] = R

    w = max(len(c) for c in configs) + 3
    pct = lambda v: f"{100*v:.0f}%" if v is not None else "-"          # noqa: E731
    line = lambda lab, vals: print(f"  {lab:<40}" + "".join(f"{v:>{w}}" for v in vals))  # noqa

    print("whole-Study extraction · one call per paper · every analysis at once\n")
    print(f"  {'':<40}" + "".join(f"{c:>{w}}" for c in configs))
    print("  " + "-" * (40 + w * len(configs)))
    line("papers scored", [rows[c]["n"] for c in configs])
    line("api errors", [rows[c]["err"] for c in configs])
    line("truncated / unparseable JSON", [rows[c]["trunc"] for c in configs])
    print()
    print("  1. ANALYSIS ENUMERATION")
    line("analysis recall vs gold", [pct(st.mean(rows[c]["recall"]) if rows[c]["recall"] else None)
                                     for c in configs])
    line("  precision (nmb gold is a lower bound)",
         [pct(st.mean(rows[c]["prec"]) if rows[c]["prec"] else None) for c in configs])
    line("analyses emitted / paper (mean)",
         [f"{st.mean(rows[c]['npred']):.1f}" if rows[c]["npred"] else "-" for c in configs])
    line("  gold analyses / paper (mean)",
         [f"{st.mean(rows[c]['ngold']):.1f}" if rows[c]["ngold"] else "-" for c in configs])
    print()
    print("  2. EVIDENCE GROUNDING")
    line("evidence spans emitted / paper",
         [f"{rows[c]['spans']/max(rows[c]['n'],1):.0f}" for c in configs])
    line("spans found verbatim in the source",
         [pct(rows[c]["spans_ok"]/rows[c]["spans"]) if rows[c]["spans"] else "-" for c in configs])
    line("papers with every span verified",
         [f"{rows[c]['papers_span_clean']}/{rows[c]['papers_with_spans']}" for c in configs])
    print()
    print("  3. CONFORMANCE (json_object; strict mode impossible, nesting 13 > cap 5)")
    line("unknown field names (total)", [sum(rows[c]["unknown"].values()) for c in configs])
    line("dangling local_id references", [rows[c]["dangling"] for c in configs])
    line("wrappers missing extraction_status",
         [f"{rows[c]['wrappers_bad']}/{rows[c]['wrappers']}" for c in configs])
    print()
    print("  4. FILL RATE, priority-0 fields")
    line("extracted rather than not_reported",
         [pct(rows[c]["p0_extracted"]/rows[c]["p0_total"]) if rows[c]["p0_total"] else "-"
          for c in configs])
    print()
    print("  TOKENS AND COST (flex short-context rates, per paper)")
    for key, lab in (("prompt", "input"), ("completion", "output incl. reasoning"),
                     ("reasoning", "  of which reasoning"), ("cached", "  cached input")):
        line(lab, [f"{int(st.mean(rows[c]['usage'][key])):,}" if rows[c]["usage"][key] else "-"
                   for c in configs])
    line("$ per paper", [f"{st.mean(rows[c]['cost']):.4f}" if rows[c]["cost"] else "-"
                         for c in configs])
    line("$ per 1,000 papers", [f"{st.mean(rows[c]['cost'])*1000:,.0f}" if rows[c]["cost"] else "-"
                                for c in configs])
    line("$ per analysis emitted",
         [f"{st.mean(rows[c]['cost'])/max(st.mean(rows[c]['npred']),1e-9):.4f}"
          if rows[c]["cost"] and rows[c]["npred"] else "-" for c in configs])
    line("model", [(rows[c]["model"] or "?")[:w-1] for c in configs])
    nf = sorted({rows[c]["model"] for c in configs} & NO_FLEX)
    if nf:
        print(f"  note: no flex listing for {', '.join(nf)} — priced at standard")

    if args.per_sample:
        print("\n  BY SAMPLE")
        samples = sorted({g["sample"] for g in gold.values()})
        for s in samples:
            print(f"  {s}")
            line("    analysis recall",
                 [pct(st.mean(rows[c]["by_sample"][s]["recall"])
                      if rows[c]["by_sample"][s]["recall"] else None) for c in configs])
            line("    emitted / gold per paper",
                 [f"{st.mean(rows[c]['by_sample'][s]['npred']):.1f}/"
                  f"{st.mean(rows[c]['by_sample'][s]['ngold']):.1f}"
                  if rows[c]["by_sample"][s]["npred"] else "-" for c in configs])

    top = collections.Counter()
    for c in configs:
        top.update(rows[c]["unknown"])
    if top:
        print("\n  most common unknown fields (model inventing structure):")
        for k, v in top.most_common(8):
            print(f"    {k:<44} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
