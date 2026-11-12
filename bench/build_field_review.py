#!/usr/bin/env python3
"""Field-level adjudication app for stage 2-3 accuracy (Tier A of bench/EVAL-PLAN.md).

One row per extracted field-instance: the value, where it came from in the paper, and four
verdict keys. Designed so a reviewer never has to open the paper separately:

  * the value is located in the source text and shown with its surrounding sentence highlighted
  * for analyses, the source table is rendered with the parsed analysis's own rows marked
  * links go to PMC (with a direct table anchor) or the publisher DOI — never PubMed, which only
    shows the abstract

Ordering puts informative rows first: fields where the four effort cells disagree, then
`value_source: generated` fields (inference rather than quotation, which evidence spans cannot
validate), then the rest.

    python3 bench/build_field_review.py                       # priority 0, all papers
    python3 bench/build_field_review.py --priority 0,1 --papers 25
    python3 bench/build_field_review.py --out review/field-review.html
"""
from __future__ import annotations

import argparse
import csv
import glob
import html
import json
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
AR = Path("/home/zorro/repos/autonima-results")
ANALYSIS_SCHEMA = Path("/home/zorro/repos/analysis-schema")
PUBGET_GLOB = ("/tmp/claude-1000/-home-zorro-repos-analysis-schema/"
               "23240987-a4ff-44f2-be31-7e4582783f7f/scratchpad/pubget_data/*/articles/*/pmcid_*")

PRIMARY = "luna-low__linked_noev"                     # the recommended configuration
CELLS = ["luna-low__linked_noev", "luna-low__linked_noev_p1highp2low",
         "luna-low__linked_noev_p1lowp2high", "luna-low__linked_noev_p1highp2high"]

ENTITY_LISTS = {"groups": "Group", "experiments": "Experiment", "acquisitions": "Acquisition",
                "preprocessings": "Preprocessing", "model_estimations": "ModelEstimation",
                "conditions": "Condition", "tables": "Table", "terms": "Term",
                "assessments": "Assessment", "analyses": "Analysis"}
PLACEHOLDER = re.compile(r"^(Unnamed: \d+_level_\d+|nan|NaN|None)$")
COORDISH = re.compile(r"\b(x|y|z|mni|tal|coord)\b", re.I)


# ------------------------------------------------------------------ identifiers

def doi_map(pmids):
    out = {}
    for f in glob.glob(str(AR / "**/metadata.csv"), recursive=True):
        try:
            for r in csv.DictReader(open(f)):
                if r.get("pmid") in pmids and r.get("doi"):
                    out[r["pmid"]] = r["doi"]
        except Exception:
            continue
    return out


def local_html_map(pmids):
    out = {}
    for f in glob.glob(str(AR / "articles/**/html/**/*.html"), recursive=True):
        stem = Path(f).stem
        if stem in pmids:
            out[stem] = f
    return out


# ------------------------------------------------------------------ tables

def _clean(c):
    c = (c or "").strip()
    return "" if PLACEHOLDER.match(c) else c


def _spans(cells):
    out = []
    for c in cells:
        if out and out[-1]["text"] == c:
            out[-1]["span"] += 1
        else:
            out.append({"text": c, "span": 1})
    return out


def pubget_tables(pmcid):
    """Every extracted table for a PMC article, as renderable structure."""
    out = []
    for d in glob.glob(PUBGET_GLOB):
        if not d.endswith(f"pmcid_{pmcid}"):
            continue
        for info_f in sorted(Path(d).glob("tables/table_*_info.json")):
            info = json.loads(info_f.read_text())
            if not info.get("table_data_file"):
                continue
            rows = [[_clean(c) for c in r]
                    for r in csv.reader(open(Path(d) / "tables" / info["table_data_file"]))]
            if not rows:
                continue
            n_hdr = min(int(info.get("n_header_rows") or 1), len(rows))
            width = max(len(r) for r in rows)
            coord_cols = [i for i in range(width)
                          if any(COORDISH.search(r[i]) for r in rows[:n_hdr] if i < len(r))]
            body = []
            for r in rows[n_hdr:]:
                cells = r + [""] * (width - len(r))
                filled = [c for c in cells if c]
                if not filled:
                    continue
                uniq = set(filled)
                sect = (len(uniq) == 1 and len(filled) >= 2) or \
                       (len(filled) == 1 and not re.search(r"\d", filled[0]))
                body.append({"type": "section", "text": filled[0]} if sect
                            else {"type": "data", "cells": cells})
            out.append({"table_id": info["table_id"],
                        "label": info.get("table_label") or info["table_id"],
                        "caption": info.get("table_caption") or "",
                        "header": [_spans(r) for r in rows[:n_hdr]],
                        "body": body, "width": width, "coord_cols": coord_cols})
        break
    return out


def stage1_analyses(pmcid):
    """Autonima's parsed analyses per table: the names and point counts stage 1 produced."""
    for f in glob.glob(str(ANALYSIS_SCHEMA / "review/parsed/*.json")):
        j = json.loads(Path(f).read_text())
        if j.get("pmcid") != pmcid:
            continue
        out = []
        for t in j.get("tables", []):
            for a in ((t.get("parsed") or {}).get("analyses") or []):
                out.append({"table_id": t.get("table_id"), "table_label": t.get("table_label"),
                            "name": a.get("name"), "n_points": len(a.get("points") or [])})
        return out
    return []


# ------------------------------------------------------------------ excerpts

def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def excerpts(value, source, want=3, pad=240):
    """Where in the paper this value appears, with the match marked.

    Three matching strategies, because three kinds of value fail differently:

      exact      most verbatim strings.
      numeric    a count like "42" is 2 characters and lives in a table cell, so it needs
                 word-boundary anchoring rather than a substring scan. 95% of
                 Group.enrolled_count looked "missing" before this case existed.
      by part    a composed label such as "positive (detach-attend), MDD<control" is assembled
                 from table headers and never appears as contiguous prose. Split on separators
                 and locate the parts, so the reviewer sees each piece in context.

    A value that still has no excerpt is either an inference (flagged separately by
    value_source) or unsupported - which is exactly what the reviewer is there to decide.
    """
    v, src = norm(value), source
    low = src.lower()
    out, seen = [], set()

    def add(a, b):
        if any(abs(a - s) < 40 for s in seen):
            return
        seen.add(a)
        s, e = max(0, a - pad), min(len(src), b + pad)
        out.append({"before": src[s:a], "match": src[a:b], "after": src[b:e]})

    def find_all(probe, anchor=False):
        if not probe:
            return
        if anchor:
            for m in re.finditer(rf"(?<![\w.]){re.escape(probe)}(?![\w.])", low):
                add(m.start(), m.end())
                if len(out) >= want:
                    return
            return
        start = 0
        while len(out) < want:
            i = low.find(probe, start)
            if i < 0:
                return
            add(i, i + len(probe))
            start = i + len(probe)

    if re.fullmatch(r"[\d.,]+", v):                       # numeric: anchor on word boundaries
        find_all(v.lower(), anchor=True)
    elif len(v) >= 4:
        find_all(v.lower())

    if not out:                                           # composed label: locate the parts
        parts = [norm(x) for x in re.split(r"[,;()\[\]]|\s*[<>]\s*|\s+vs\.?\s+|\s+minus\s+", v)]
        for part in sorted({p for p in parts if len(p) >= 4}, key=len, reverse=True):
            find_all(part.lower())
            if len(out) >= want:
                break

    if not out and len(v) >= 4:                            # last resort: longest token run
        toks = [x for x in re.split(r"[^A-Za-z0-9.\-]+", v) if len(x) > 3]
        for n in (5, 4, 3, 2):
            for j in range(len(toks) - n + 1):
                probe = " ".join(toks[j:j + n]).lower()
                i = low.find(probe)
                if i >= 0:
                    add(i, i + len(probe))
                    break
            if out:
                break
    return out[:want]


# ------------------------------------------------------------------ assembly

def priorities():
    p = yaml.safe_load((REPO / "storage-parameter-priorities.yaml").read_text())
    out = {}
    for cls, fs in (p or {}).items():
        for f, pr in (fs or {}).items():
            out[f"{cls}.{f}"] = str(pr)
    return out


def entity_label(e):
    for cand in ("name", "task_name"):
        v = e.get(cand)
        if isinstance(v, dict) and isinstance(v.get("value"), str):
            return v["value"]
        if isinstance(v, str):
            return v
    return e.get("local_id") or "?"


def field_rows(rec, wanted, prio):
    """Every filled field-instance, with its entity context."""
    rows = []
    for key, cls in ENTITY_LISTS.items():
        for ei, e in enumerate(rec.get(key) or []):
            if not isinstance(e, dict):
                continue
            lbl = entity_label(e)
            for fld, v in e.items():
                if fld == "local_id":
                    continue
                path = f"{cls}.{fld}"
                if wanted and prio.get(path) not in wanted:
                    continue
                if isinstance(v, dict) and v.get("extraction_status") == "extracted" \
                        and v.get("value") not in (None, "", []):
                    rows.append({"cls": cls, "field": fld, "path": path,
                                 "priority": prio.get(path, "?"),
                                 "entity": lbl, "entity_key": key, "entity_idx": ei,
                                 "local_id": e.get("local_id"),
                                 "value": v["value"],
                                 "generated": v.get("value_source") == "generated"})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="bench/runs2")
    ap.add_argument("--out", default="review/field-review.html")
    ap.add_argument("--priority", default="0", help="comma-separated priority tiers to include")
    ap.add_argument("--papers", type=int, default=0, help="cap number of papers")
    args = ap.parse_args()

    root = Path(args.dir)
    gold = json.loads((root / "_gold.json").read_text())
    prio = priorities()
    wanted = {p.strip() for p in args.priority.split(",") if p.strip()}

    # cross-cell values, for the disagreement signal
    alt = {}
    for cell in CELLS:
        for f in glob.glob(str(root / cell / "*.json")):
            d = json.loads(Path(f).read_text())
            if d.get("record") and not d.get("parse_error"):
                alt.setdefault(d["case_id"], {})[cell] = d["record"]

    pmids = {v["pmid"] for v in gold.values() if v.get("pmid")}
    dois, lhtml = doi_map(pmids), local_html_map(pmids)

    papers = []
    for f in sorted(glob.glob(str(root / PRIMARY / "*.json"))):
        d = json.loads(Path(f).read_text())
        cid = d["case_id"]
        rec, g = d.get("record"), gold.get(cid)
        if not rec or d.get("parse_error") or not g:
            continue
        src = (root / "_sources" / f"{cid}.txt").read_text()
        rows = field_rows(rec, wanted, prio)

        # disagreement: does any other cell hold a different value at the same path+entity?
        for r in rows:
            others = []
            for cell, orec in (alt.get(cid) or {}).items():
                if cell == PRIMARY:
                    continue
                lst = (orec.get(r["entity_key"]) or [])
                cand = None
                for e in lst:
                    if isinstance(e, dict) and e.get("local_id") == r["local_id"]:
                        cand = e
                        break
                if cand is None and r["entity_idx"] < len(lst) and isinstance(lst[r["entity_idx"]], dict):
                    cand = lst[r["entity_idx"]]
                v = (cand or {}).get(r["field"])
                val = v.get("value") if isinstance(v, dict) else None
                if val not in (None, "", []):
                    others.append({"cell": cell.replace("luna-low__linked_noev", "base"), "value": val})
            r["others"] = others
            r["disagree"] = any(norm(str(o["value"])) != norm(str(r["value"])) for o in others)
            r["excerpts"] = excerpts(str(r["value"]), src)
            r["located"] = bool(r["excerpts"])

        # informative first
        rows.sort(key=lambda r: (not r["disagree"], not r["generated"], r["located"],
                                 r["cls"], r["field"]))
        pmcid, pmid = g.get("pmcid"), g.get("pmid")
        papers.append({
            "case_id": cid, "sample": g["sample"], "pmcid": pmcid, "pmid": pmid,
            "doi": dois.get(pmid or ""), "local_html": lhtml.get(pmid or ""),
            "tables": pubget_tables(pmcid) if pmcid else [],
            "stage1": stage1_analyses(pmcid) if pmcid else [],
            "gold_names": g["gold"].get("names") or [],
            "rows": rows,
            "n_disagree": sum(1 for r in rows if r["disagree"]),
            "n_unlocated": sum(1 for r in rows if not r["located"]),
        })
        if args.papers and len(papers) >= args.papers:
            break

    papers.sort(key=lambda p: (-p["n_disagree"], -p["n_unlocated"]))
    tot = sum(len(p["rows"]) for p in papers)
    print(f"{len(papers)} papers · {tot} field-instances (priority {sorted(wanted)}) · "
          f"{sum(p['n_disagree'] for p in papers)} with cross-cell disagreement · "
          f"{sum(p['n_unlocated'] for p in papers)} not locatable in source")
    print(f"  linkable: {sum(1 for p in papers if p['pmcid'])} PMC · "
          f"{sum(1 for p in papers if p['doi'])} DOI · "
          f"{sum(1 for p in papers if p['local_html'])} local publisher HTML · "
          f"{sum(1 for p in papers if p['tables'])} with rendered tables")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PAGE.replace("__DATA__", json.dumps(papers)))
    print(f"wrote {out}  ({out.stat().st_size/1e6:.1f} MB)")
    return 0


PAGE = r"""<!doctype html><meta charset="utf-8"><title>field adjudication</title>
<style>
*{box-sizing:border-box}
body{margin:0;font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     color:#1a1a1a;background:#f6f7f9}
#top{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid #d8dce2;
     padding:8px 14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
#top h1{font-size:14px;margin:0;font-weight:600}
.sp{flex:1}
button,select{font:inherit;padding:3px 9px;border:1px solid #c3c9d2;background:#fff;
       border-radius:5px;cursor:pointer}
button:hover{background:#eef1f5}button.on{background:#1f6feb;color:#fff;border-color:#1f6feb}
#wrap{display:flex;height:calc(100vh - 44px)}
#list{width:250px;overflow:auto;border-right:1px solid #d8dce2;background:#fff;flex:none}
#list div.c{padding:6px 10px;border-bottom:1px solid #eef0f3;cursor:pointer;font-size:12px}
#list div.c:hover{background:#f0f4fa}#list div.c.sel{background:#dce8fb;font-weight:600}
.badge{display:inline-block;min-width:18px;text-align:center;border-radius:9px;padding:0 5px;
       font-size:10px;font-weight:700;color:#fff;background:#adb5bd}
.badge.hot{background:#d1242f}.badge.done{background:#1a7f37}
#main{flex:1;overflow:auto;padding:12px 16px}
.panel{background:#fff;border:1px solid #d8dce2;border-radius:7px;padding:10px 12px;margin-bottom:10px}
.panel h3{margin:0 0 7px;font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:#57606a}
.mono{font-family:ui-monospace,Menlo,monospace;font-size:11px;background:#eef1f5;
      border:1px solid #dde1e6;border-radius:3px;padding:0 4px}
.sub{color:#57606a;font-size:12px;margin-bottom:8px}
.row{border:1px solid #dde1e6;border-radius:6px;padding:8px 10px;margin-bottom:8px;background:#fff}
.row.focus{border-color:#1f6feb;box-shadow:0 0 0 2px #dce8fb}
.row.done{opacity:.5}
.rhead{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.fpath{font-family:ui-monospace,Menlo,monospace;font-size:11px;font-weight:700}
.ent{color:#57606a;font-size:11px}
.tag{font-size:10px;border-radius:3px;padding:0 5px;font-weight:700}
.tag.gen{background:#fff4d6;color:#9a6700;border:1px solid #e5c66a}
.tag.dis{background:#ffe9e9;color:#8c1c13;border:1px solid #f0b0b0}
.tag.noloc{background:#f0e6ff;color:#5a2ca0;border:1px solid #cdb6f0}
.val{font-family:ui-monospace,Menlo,monospace;font-size:12px;background:#f6f8fa;
     border-left:3px solid #1f6feb;padding:4px 8px;margin:5px 0;white-space:pre-wrap}
.ex{font-size:12px;background:#fbfcfd;border-left:3px solid #d0d7de;padding:5px 8px;margin:4px 0}
.ex mark{background:#fff3a3;font-weight:600}
.alt{font-size:11px;color:#57606a;font-family:ui-monospace,Menlo,monospace}
.vb{display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin-top:5px}
.vb button{font-size:11px;padding:1px 7px}
.vb button.c.on{background:#1a7f37;border-color:#1a7f37;color:#fff}
.vb button.w.on{background:#d1242f;border-color:#d1242f;color:#fff}
.vb button.u.on{background:#9a6700;border-color:#9a6700;color:#fff}
.vb button.a.on{background:#8250df;border-color:#8250df;color:#fff}
.vb button.i.on{background:#0969da;border-color:#0969da;color:#fff}
.vb input{font:11px ui-monospace,Menlo,monospace;padding:2px 5px;border:1px solid #c3c9d2;
       border-radius:4px;width:230px}
table.tbl{border-collapse:collapse;font-size:11px;width:100%}
table.tbl th,table.tbl td{border:1px solid #d8dce2;padding:2px 5px}
table.tbl th{background:#eef1f5}
table.tbl tr.sec td{background:#fff4d6;font-weight:600}
table.tbl td.co{background:#f0f7ff}
#help{display:none;position:fixed;right:16px;bottom:16px;z-index:40;background:#fff;
      border:1px solid #c3c9d2;border-radius:8px;padding:10px 14px;box-shadow:0 6px 24px rgba(0,0,0,.18)}
#help table{border-collapse:collapse;font-size:12px}#help td{padding:2px 8px 2px 0}
#help td:first-child{text-align:right;white-space:nowrap}
details summary{cursor:pointer;color:#57606a;font-size:11px}
</style>
<div id="top">
  <h1>Field adjudication</h1><span id="counts"></span><span class="sp"></span>
  <button id="bD" onclick="onlyDis=!onlyDis;this.classList.toggle('on');render()">disagreements only</button>
  <button id="bG" onclick="onlyGen=!onlyGen;this.classList.toggle('on');render()">generated only</button>
  <button id="bH" onclick="hideDone=!hideDone;this.classList.toggle('on');render()">hide done</button>
  <button onclick="exportJSON()">export</button><button onclick="toggleHelp()">keys ?</button>
</div>
<div id="wrap"><div id="list"></div><div id="main"></div></div>
<div id="help"></div>
<script>const PAPERS = __DATA__;</script>
<script>
const V = JSON.parse(localStorage.getItem('fieldVerdicts') || '{}');
let cur = 0, curRow = 0, onlyDis = false, onlyGen = false, hideDone = false, VIS = [];
const key = (c, r) => `${c}||${r.cls}.${r.field}||${r.local_id||r.entity_idx}`;
const save = () => localStorage.setItem('fieldVerdicts', JSON.stringify(V));
const esc = s => { const d = document.createElement('div'); d.textContent = s==null?'':String(s); return d.innerHTML; };

const HELP = [['1 / c','correct'],['2 / w','wrong — then type the right value'],
  ['3 / u','unsupported (paper does not say it)'],['4 / a','should be absent (paper is silent)'],
  ['5 / x','inexpressible (schema cannot hold it)'],['0','clear verdict'],
  ['↓ ↑ / n p','next / previous field'],['→ ← / j k','next / previous paper'],
  ['Enter','edit the correction box'],['t','open source (PMC or publisher)'],
  ['e','export'],['?','this list']];
function toggleHelp(f){const el=document.getElementById('help');
  const s = f!==undefined?f:el.style.display!=='block';
  el.innerHTML='<table>'+HELP.map(([k,d])=>`<tr><td class="mono">${k}</td><td>${d}</td></tr>`).join('')+'</table>';
  el.style.display=s?'block':'none';}

function visRows(p){return p.rows.filter(r => (!onlyDis||r.disagree) && (!onlyGen||r.generated)
  && (!hideDone || !V[key(p.case_id,r)]));}

function renderList(){
  const el=document.getElementById('list'); el.innerHTML='';
  PAPERS.forEach((p,i)=>{
    const rows=visRows(p); const done=p.rows.every(r=>V[key(p.case_id,r)]);
    const d=document.createElement('div'); d.className='c'+(i===cur?' sel':'');
    d.innerHTML=`<span class="badge ${done?'done':(p.n_disagree>=3?'hot':'')}">${done?'✓':rows.length}</span>
      <span class="mono">${esc(p.case_id)}</span>
      <div style="color:#6a737d;font-size:11px">${esc(p.sample)} · ${p.n_disagree} disagree · ${p.n_unlocated} unlocated</div>`;
    d.onclick=()=>{cur=i;curRow=0;render();}; el.appendChild(d);
  });
}

function srcLinks(p){
  const L=[];
  if(p.pmcid) L.push(`<a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC${p.pmcid}/" target="_blank">PMC full text</a>`);
  if(p.doi) L.push(`<a href="https://doi.org/${esc(p.doi)}" target="_blank">publisher (doi)</a>`);
  if(p.local_html) L.push(`<a href="file://${esc(p.local_html)}" target="_blank">local publisher HTML</a>`);
  return L.length?L.join(' · '):'<i>no link available</i>';
}
function primaryUrl(p){
  if(p.pmcid) return `https://pmc.ncbi.nlm.nih.gov/articles/PMC${p.pmcid}/`;
  if(p.doi) return `https://doi.org/${p.doi}`;
  if(p.local_html) return `file://${p.local_html}`;
  return null;
}

function renderTable(p,t){
  let h=`<details ${p.tables.length===1?'open':''}><summary>${esc(t.label)} — ${esc(t.caption.slice(0,90))}`
   +(p.pmcid?` · <a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC${p.pmcid}/table/${esc(t.table_id)}/" target="_blank">at PMC ↗</a>`:'')
   +`</summary><table class="tbl">`;
  t.header.forEach(r=>{h+='<tr>'+r.map(c=>`<th colspan="${c.span}">${esc(c.text)}</th>`).join('')+'</tr>';});
  t.body.forEach(r=>{ h += r.type==='section'
     ? `<tr class="sec"><td colspan="${t.width}">${esc(r.text)}</td></tr>`
     : '<tr>'+r.cells.map((c,i)=>`<td class="${t.coord_cols.includes(i)?'co':''}">${esc(c)}</td>`).join('')+'</tr>';});
  return h+'</table></details>';
}

function verdictBar(p,r){
  const k=key(p.case_id,r), v=V[k]||{};
  const b=(code,cls,lab)=>`<button class="${cls} ${v.verdict===code?'on':''}" onclick="setV('${k}','${code}')">${lab}</button>`;
  return `<div class="vb">${b('correct','c','correct')}${b('wrong','w','wrong')}
    ${b('unsupported','u','unsupported')}${b('should_be_absent','a','absent')}
    ${b('inexpressible','i','inexpressible')}
    <input placeholder="correct value / note" value="${esc(v.note||'')}"
      oninput="setNote('${k}',this.value)"></div>`;
}
function setV(k,code){V[k]=Object.assign({},V[k],{verdict:V[k]&&V[k].verdict===code?null:code});
  if(!V[k].verdict&&!V[k].note) delete V[k]; save(); advance();}
function setNote(k,t){V[k]=Object.assign({},V[k],{note:t});
  if(!V[k].verdict&&!V[k].note) delete V[k]; save(); counts();}
function advance(){ if(curRow<VIS.length-1) moveRow(1);
  else if(cur<PAPERS.length-1){cur++;curRow=0;render();document.getElementById('main').scrollTop=0;}
  else render(); }
function moveRow(d){curRow=Math.max(0,Math.min(curRow+d,VIS.length-1));render();
  const el=document.getElementById('frow'); if(el) el.scrollIntoView({block:'nearest'});}

function render(){
  renderList();
  const p=PAPERS[cur]; if(!p){document.getElementById('main').innerHTML='<p>none</p>';return;}
  VIS=visRows(p); if(curRow>=VIS.length) curRow=Math.max(0,VIS.length-1);
  let h=`<div class="sub"><b class="mono">${esc(p.case_id)}</b> · ${esc(p.sample)} · ${srcLinks(p)}
     · ${VIS.length} of ${p.rows.length} fields shown</div>`;
  if(p.stage1.length) h+=`<div class="panel"><h3>stage 1 — analyses parsed from the tables</h3>`
    +p.stage1.map(a=>`<div class="alt">${esc(a.table_label||a.table_id)} · <b>${esc(a.name)}</b> · ${a.n_points} points</div>`).join('')+`</div>`;
  h+=`<div class="panel"><h3>fields to adjudicate — 1 correct · 2 wrong · 3 unsupported · 4 absent · 5 inexpressible · ? keys</h3>`;
  VIS.forEach((r,i)=>{
    const v=V[key(p.case_id,r)];
    h+=`<div class="row ${i===curRow?'focus':''} ${v?'done':''}" ${i===curRow?'id="frow"':''}>
      <div class="rhead"><span class="fpath">${esc(r.path)}</span>
        <span class="ent">${esc(r.cls)} “${esc(r.entity)}”</span>
        <span class="mono">p${esc(r.priority)}</span>
        ${r.generated?'<span class="tag gen">inferred</span>':''}
        ${r.disagree?'<span class="tag dis">cells differ</span>':''}
        ${!r.located?'<span class="tag noloc">not in source</span>':''}</div>
      <div class="val">${esc(typeof r.value==='object'?JSON.stringify(r.value):r.value)}</div>`;
    r.excerpts.forEach(x=>{h+=`<div class="ex">…${esc(x.before)}<mark>${esc(x.match)}</mark>${esc(x.after)}…</div>`;});
    if(r.others.length) h+=`<div class="alt">other cells: `+r.others.map(o=>`${esc(o.cell)}=${esc(typeof o.value==='object'?JSON.stringify(o.value):o.value)}`).join(' · ')+`</div>`;
    h+=verdictBar(p,r)+`</div>`;
  });
  h+=`</div>`;
  if(p.tables.length) h+=`<div class="panel"><h3>source tables (${p.tables.length})</h3>`
    +p.tables.map(t=>renderTable(p,t)).join('')+`</div>`;
  if(p.gold_names.length) h+=`<div class="panel"><h3>gold analysis names</h3><div class="alt">`
    +p.gold_names.map(esc).join(' · ')+`</div></div>`;
  document.getElementById('main').innerHTML=h; counts();
}

function counts(){
  const t={}; Object.values(V).forEach(v=>{if(v.verdict)t[v.verdict]=(t[v.verdict]||0)+1;});
  const tot=PAPERS.reduce((s,p)=>s+p.rows.length,0);
  const done=Object.values(V).filter(v=>v.verdict).length;
  document.getElementById('counts').textContent=
    `${done}/${tot} · correct ${t.correct||0} · wrong ${t.wrong||0} · unsupported ${t.unsupported||0} · absent ${t.should_be_absent||0} · inexpr ${t.inexpressible||0}`;
}
function exportJSON(){
  const out=[]; PAPERS.forEach(p=>p.rows.forEach(r=>{const v=V[key(p.case_id,r)];
    if(v&&(v.verdict||v.note)) out.push({case_id:p.case_id,sample:p.sample,pmid:p.pmid,pmcid:p.pmcid,
      path:r.path,entity:r.entity,local_id:r.local_id,value:r.value,generated:r.generated,
      disagree:r.disagree,located:r.located,verdict:v.verdict||null,note:v.note||null});}));
  const b=new Blob([JSON.stringify({verdicts:out},null,1)],{type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(b);
  a.download='field-verdicts.json'; a.click();
}
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'){ if(e.key==='Escape'||e.key==='Enter') e.target.blur(); return; }
  if(e.metaKey||e.ctrlKey||e.altKey) return;
  const m={'1':'correct','c':'correct','2':'wrong','w':'wrong','3':'unsupported','u':'unsupported',
           '4':'should_be_absent','a':'should_be_absent','5':'inexpressible','x':'inexpressible'};
  const p=PAPERS[cur], r=VIS[curRow];
  if(m[e.key]&&r) setV(key(p.case_id,r),m[e.key]);
  else if(e.key==='0'&&r){const k=key(p.case_id,r); if(V[k]){delete V[k];save();render();}}
  else if(e.key==='ArrowDown'||e.key==='n') moveRow(1);
  else if(e.key==='ArrowUp'||e.key==='p') moveRow(-1);
  else if(e.key==='ArrowRight'||e.key==='j'){cur=Math.min(cur+1,PAPERS.length-1);curRow=0;render();}
  else if(e.key==='ArrowLeft'||e.key==='k'){cur=Math.max(cur-1,0);curRow=0;render();}
  else if(e.key==='Enter'){const el=document.getElementById('frow');
    const i=el&&el.querySelector('input'); if(i){i.focus();i.select();}}
  else if(e.key==='t'){const u=primaryUrl(PAPERS[cur]); if(u) window.open(u,'_blank');}
  else if(e.key==='e') exportJSON();
  else if(e.key==='?') toggleHelp();
  else return;
  e.preventDefault();
});
render();
</script>
"""

if __name__ == "__main__":
    sys.exit(main())
