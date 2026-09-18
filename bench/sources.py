#!/usr/bin/env python3
"""Source text and gold for the two benchmark samples.

pmc20  — 20 stratified PubMed Central OA fMRI papers (2015-2025), all coordinate-bearing.
         Methods + Results sliced from the JATS XML, plus table captions. Reference analysis
         counts come from Autonima's coordinate parser: 33 tables -> 108 analyses.

nmb    — neurometabench. Papers included in curated meta-analyses, with NiMADS gold giving the
         analysis names and coordinate counts. Full text from the Autonima corpus index.

         The gold is a LOWER BOUND on analyses per paper: a meta-analysis keeps only the
         contrasts it needed, so a paper reporting eight effects may appear with two. Recall
         against it is meaningful; precision is not, and the scorer says so.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ANALYSIS_SCHEMA = Path("/home/zorro/repos/analysis-schema")
AUTONIMA_RESULTS = Path("/home/zorro/repos/autonima-results")
NMB = Path("/home/zorro/repos/neurometabench")
PUBGET_GLOB = ("/tmp/claude-1000/-home-zorro-repos-analysis-schema/"
               "23240987-a4ff-44f2-be31-7e4582783f7f/scratchpad/pubget_data/*/articles/*/pmcid_*")

MAX_CHARS = 90_000

_DROP = re.compile(r"introduct|background|discussion|conclusion|limitation|acknowledg|"
                   r"reference|funding|conflict|ethic|availab|supplement", re.I)


# ------------------------------------------------------------------ pmc20

def pmc_dirs():
    return {d.rsplit("_", 1)[1]: d for d in glob.glob(PUBGET_GLOB)}


def pmc_text(pmcid, dirs=None):
    d = (dirs or pmc_dirs()).get(pmcid)
    if not d or not Path(d, "article.xml").exists():
        return None
    root = ET.parse(Path(d, "article.xml")).getroot()
    ti = root.find(".//article-title")
    out = [" ".join(ti.itertext()).strip() if ti is not None else ""]
    body = root.find(".//body")
    if body is not None:
        for sec in body.findall("sec"):
            t = sec.find("title")
            if t is not None and _DROP.search(" ".join(t.itertext())):
                continue
            out.append(" ".join(sec.itertext()))
    for tw in root.iter("table-wrap"):
        lab, cap = tw.find("label"), tw.find("caption")
        out.append(" ".join(filter(None, [
            " ".join(lab.itertext()).strip() if lab is not None else "",
            " ".join(cap.itertext()).strip() if cap is not None else ""])))
    return re.sub(r"[ \t]+", " ", "\n\n".join(x for x in out if x.strip()))[:MAX_CHARS]


def pmc_reference_counts():
    """{pmcid: n_analyses} from the real Autonima parser run - the best available estimate of
    how many coordinate-bearing analyses a paper actually reports."""
    out = {}
    for f in glob.glob(str(ANALYSIS_SCHEMA / "review/parsed/*.json")):
        j = json.loads(Path(f).read_text())
        n = sum(len((t.get("parsed") or {}).get("analyses") or []) for t in j.get("tables", []))
        names = [a.get("name") for t in j.get("tables", [])
                 for a in ((t.get("parsed") or {}).get("analyses") or []) if a.get("name")]
        out[j["pmcid"]] = {"n_analyses": n, "names": names,
                           "n_tables": len(j.get("tables", []))}
    return out


def pmc20_cases():
    dirs = pmc_dirs()
    ref = pmc_reference_counts()
    cases = []
    for pmcid in sorted(ref):
        t = pmc_text(pmcid, dirs)
        if not t:
            continue
        cases.append({"case_id": f"pmc_{pmcid}", "sample": "pmc20", "pmcid": pmcid,
                      "text": t, "gold": ref[pmcid], "gold_kind": "parser_reference"})
    return cases


# ------------------------------------------------------------------ neurometabench

_FT = None


def pmid_text(pmid):
    global _FT
    if _FT is None:
        import sys
        sys.path.insert(0, str(ANALYSIS_SCHEMA / "tools"))
        import fulltext_index as fi
        _FT = (fi, json.loads((ANALYSIS_SCHEMA / "fulltext_index.json").read_text()))
    fi, idx = _FT
    cwd = os.getcwd()
    try:
        os.chdir(AUTONIMA_RESULTS)                 # index paths are corpus-relative
        t = fi.read_text(str(pmid), idx)
    finally:
        os.chdir(cwd)
    return re.sub(r"[ \t]+", " ", t)[:MAX_CHARS] if t else None


def nmb_gold():
    """{pmid: {analyses: [{name, n_points}], topic}} - the deepest studyset per study wins."""
    best, topic = {}, {}
    for f in glob.glob(str(NMB / "data/nimads/**/*.json"), recursive=True):
        try:
            j = json.loads(Path(f).read_text())
        except Exception:
            continue
        if not isinstance(j, dict) or not isinstance(j.get("studies"), list):
            continue
        t = f.split("/nimads/")[1].split("/")[0]
        for s in j["studies"]:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or "")
            an = [a for a in (s.get("analyses") or []) if isinstance(a, dict) and a.get("name")]
            if not sid.isdigit() or not an or len(an) <= len(best.get(sid, [])):
                continue
            best[sid] = [{"name": a["name"], "n_points": len(a.get("points") or [])} for a in an]
            topic[sid] = t
    return {k: {"analyses": v, "topic": topic[k]} for k, v in best.items()}


def nmb_cases(limit=0, seed=20260804):
    """Stratified by topic, then by how many analyses the gold holds, so the sample is not all
    single-contrast papers - the whole point is to test multi-analysis extraction."""
    import random
    gold = nmb_gold()
    idx = set(json.loads((ANALYSIS_SCHEMA / "fulltext_index.json").read_text()))
    pool = [(p, g) for p, g in gold.items() if p in idx]
    buckets = {}
    for p, g in pool:
        n = len(g["analyses"])
        buckets.setdefault((g["topic"], min(n, 4)), []).append((p, g))
    rng = random.Random(seed)
    for v in buckets.values():
        rng.shuffle(v)
    picked, keys = [], sorted(buckets)
    while len(picked) < (limit or len(pool)):
        added = False
        for k in keys:
            if buckets[k]:
                picked.append(buckets[k].pop())
                added = True
                if limit and len(picked) >= limit:
                    break
        if not added:
            break
    cases = []
    for p, g in picked:
        t = pmid_text(p)
        if not t:
            continue
        cases.append({"case_id": f"nmb_{p}", "sample": "nmb", "pmid": p, "text": t,
                      "gold": {"n_analyses": len(g["analyses"]),
                               "names": [a["name"] for a in g["analyses"]],
                               "n_points": [a["n_points"] for a in g["analyses"]],
                               "topic": g["topic"]},
                      "gold_kind": "nimads_lower_bound"})
    return cases


def stage1_analyses(case):
    """The analysis list stage 1 hands to stage 2.

    pmc20 uses the real Autonima parser output. nmb has no parser run, so the NiMADS gold names
    stand in for it — an *idealised* stage 1. That is defensible because stage 1 is settled
    (33/33 tables, 0 failures) and because the point of this mode is to remove the enumeration
    confound, but it does mean name recall is true by construction here and must not be reported
    as an accuracy result for this mode.
    """
    names = case["gold"].get("names") or []
    return [n for n in names if n]


def load_cases(samples, limit_nmb=0):
    out = []
    if "pmc20" in samples:
        out += pmc20_cases()
    if "nmb" in samples:
        out += nmb_cases(limit_nmb)
    return out


if __name__ == "__main__":
    import statistics as st
    for name, cs in (("pmc20", pmc20_cases()), ("nmb (60)", nmb_cases(60))):
        if not cs:
            print(f"{name}: none"); continue
        chars = sorted(len(c["text"]) for c in cs)
        gn = [c["gold"]["n_analyses"] for c in cs]
        print(f"{name}: {len(cs)} papers · text chars median {chars[len(chars)//2]:,} "
              f"max {chars[-1]:,} · gold analyses/paper mean {st.mean(gn):.2f} "
              f"median {int(st.median(gn))} max {max(gn)}")
