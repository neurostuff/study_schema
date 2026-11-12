#!/usr/bin/env python3
"""Render neuroimaging-study-extraction.yaml as a compact instruction block for a model.

Why not ship the JSON Schema. `gen-json-schema` produces 71 KB (~18k tokens) and, more
importantly, OpenAI strict structured output caps `$ref`-expanded nesting at 5 levels while this
schema reaches **13** — the ExtractedValue -> Evidence -> EvidenceSet -> EvidenceSpan wrapper is
6 levels on its own, so every leaf field already exceeds the cap. Constrained decoding is
therefore unavailable for this schema as written, and output has to be validated after the fact.

That makes the prompt the only place the contract can be stated, so it is generated from the
schema rather than hand-written: change the YAML and the prompt follows.

    python3 bench/schema_prompt.py --report          # sizes, in tokens
    python3 bench/schema_prompt.py --out bench/schema-prompt.txt
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

# Classes whose shape is better stated once, in prose, than repeated on every field.
WRAPPERS = {"ExtractedValue", "ExtractedString", "ExtractedInteger", "ExtractedNumber",
            "Evidence", "EvidenceSet", "EvidenceSpan"}

PREAMBLE = """\
OUTPUT CONTRACT

Return a single JSON object: one Study extraction record for the paper. No prose, no markdown.

Every source-derived field is an "extracted value" object, not a bare scalar:

  {"extraction_status": "extracted",           // or "not_reported"
   "value": "<verbatim text from the paper>",  // omit entirely when not_reported
   "value_source": "reported",                 // or "generated" if you had to compose it
   "evidence": {"status": "present",           // "not_found" | "not_applicable"
                "sets": [{"spans": [{"text": "<exact sentence from the paper>"}]}]}}

Rules for those objects:
  - `value` is VERBATIM or a close paraphrase of the paper's own words. Do not normalise to a
    controlled vocabulary — say "whole brain" or "small volume correction", not an enum code.
    Normalisation happens downstream.
  - When the paper does not report the fact: {"extraction_status": "not_reported",
    "evidence": {"status": "not_applicable"}} and no `value`.
  - `evidence.sets[].spans[].text` MUST be a span copied exactly from the paper text given to
    you. It is checked automatically against the source. If you cannot find a supporting span,
    use "evidence": {"status": "not_found"} rather than inventing one.
  - Multivalued extracted fields are a LIST of these objects, one per item.

Fields NOT wrapped this way, because they are not read out of the paper: `local_id` on every
entity, and any field whose description says it holds local_ids of other records. Those are
plain strings you assign yourself.

Cross-references use `local_id`. Give every Group, Experiment, Acquisition, Preprocessing,
ModelEstimation, Condition, Term, Assessment and Table a short stable `local_id` (e.g. "g1",
"exp_nback", "acq_bold", "tbl3"), then refer to it from the analyses.

EXTRACT EVERY ANALYSIS THE PAPER REPORTS. A paper normally reports several — one per contrast,
per group comparison, per ROI set, per direction. Enumerate them all; do not summarise or keep
only the headline result. Each distinct reported effect is its own Analysis record.
"""

PREAMBLE_NO_EVIDENCE = """\
OUTPUT CONTRACT

Return a single JSON object: one Study extraction record for the paper. No prose, no markdown.

Every source-derived field is an "extracted value" object, not a bare scalar:

  {"extraction_status": "extracted",           // or "not_reported"
   "value": "<verbatim text from the paper>",  // omit entirely when not_reported
   "value_source": "reported"}                 // or "generated" if you had to compose it

DO NOT emit an `evidence` key anywhere. Supporting spans are added by a separate later pass.
Spend your output on getting the values right and complete, not on quotation.

Rules:
  - `value` is VERBATIM or a close paraphrase of the paper's own words. Do not normalise to a
    controlled vocabulary — say "whole brain" or "small volume correction", not an enum code.
    Normalisation happens downstream.
  - When the paper does not report the fact: {"extraction_status": "not_reported"} and no `value`.
  - Multivalued extracted fields are a LIST of these objects, one per item.

Fields NOT wrapped this way, because they are not read out of the paper: `local_id` on every
entity, and any field whose description says it holds local_ids of other records. Those are
plain strings you assign yourself.

Cross-references use `local_id`. Give every Group, Experiment, Acquisition, Preprocessing,
ModelEstimation, Condition, Term, Assessment and Table a short stable `local_id` (e.g. "g1",
"exp_nback", "acq_bold", "tbl3"), then refer to it from the analyses.

EXTRACT EVERY ANALYSIS THE PAPER REPORTS. A paper normally reports several — one per contrast,
per group comparison, per ROI set, per direction. Enumerate them all; do not summarise or keep
only the headline result. Each distinct reported effect is its own Analysis record.
"""


def load():
    return yaml.safe_load((REPO / "neuroimaging-study-extraction.yaml").read_text())


# Analysis-side classes, in the order a reader needs them. Used by mode="analyses_first" to put
# the analyses ahead of the participant and acquisition detail.
ANALYSIS_SIDE = ["Analysis", "GroupTerm", "ConditionTerm", "FactorLevel", "Measure", "Statistic",
                 "InferenceSettings", "StatisticalMap", "DecodingDetails", "PerformanceMetric",
                 "DecodingClass", "SimilarityDetails", "ConnectivityDetails", "ConnectivityEdge",
                 "ConjunctionDetails", "ConjunctionComponent", "ComponentDecompositionDetails",
                 "OtherAnalysisDetails", "NotStructurableDetails", "Table"]

ANALYSES_FIRST_NOTE = """
EMIT `analyses` FIRST in the JSON object, before groups, experiments, acquisitions or
preprocessings. The analyses are the point of the record; the participant and acquisition detail
supports them. Write every analysis the paper reports, then fill in the supporting entities and
link them by local_id.
"""

STUDY_ORDER_ANALYSES_FIRST = [
    "extraction_metadata", "analyses", "tables", "conditions", "terms", "groups",
    "experiments", "acquisitions", "preprocessings", "model_estimations", "assessments",
    "description", "design", "external_datasets",
]


def render(brief=False, mode="schema", evidence=True) -> str:
    """One line per attribute: name, range, and the description that says what 'verbatim' means.

    mode:
      schema         — classes in schema order (Study first, then the rest as declared)
      analyses_first — Study attributes and classes reordered so the analyses lead
      entities_only  — everything except Analysis and its payload classes (two-pass, pass 1)
      analyses_only  — Analysis and its payload classes only (two-pass, pass 2)
      annotate_only  — annotate an analysis list stage 1 already parsed

    evidence=False drops the evidence contract entirely. Measured at 57% of output tokens, and
    the pass that carries it is the one where high reasoning effort ran away, so the two are
    worth separating rather than testing together.
    """
    d = load()
    C = d["classes"]
    order = ["Study"] + [k for k in C if k not in WRAPPERS and k != "Study"]
    if mode == "analyses_first":
        order = ["Study"] + ANALYSIS_SIDE + [k for k in order
                                             if k not in ANALYSIS_SIDE and k != "Study"]
    elif mode == "entities_only":
        order = [k for k in order if k not in ANALYSIS_SIDE]
    elif mode in ("analyses_only", "annotate_only"):
        order = ["Study"] + ANALYSIS_SIDE
    out = [PREAMBLE if evidence else PREAMBLE_NO_EVIDENCE]
    if mode == "analyses_first":
        out.append(ANALYSES_FIRST_NOTE)
    elif mode == "entities_only":
        out.append("\nDo NOT emit `analyses` in this pass. Only the entities below. A separate "
                   "pass extracts the analyses and will refer to the local_ids you assign here.\n")
    elif mode == "analyses_only":
        out.append("\nEmit ONLY `analyses` (and `tables` if the paper has result tables). The "
                   "supporting entities were extracted separately and are listed below with "
                   "their local_ids — refer to them, do not re-emit them.\n")
    elif mode == "annotate_only":
        out.append("\nThe analyses have ALREADY been identified from the paper's result tables "
                   "and are listed below. Emit ONE `analyses` entry for each one, in the same "
                   "order, keeping its given name verbatim in `name.value`. Your job is to "
                   "annotate each one — scope, measure, statistic, baseline, inference settings, "
                   "method payload — and to link it by local_id to the groups, conditions, "
                   "experiments, acquisitions, preprocessing and terms listed below. Do not add "
                   "analyses that are not in the list and do not drop any.\n")
    out.append("\nSCHEMA — classes and their fields\n")
    for name in order:
        c = C.get(name)
        if c is None or name in WRAPPERS:
            continue
        atts = c.get("attributes") or {}
        if not atts:
            continue
        if name == "Study":
            keep = {"entities_only": lambda k: k not in ("analyses", "tables"),
                    "analyses_only": lambda k: k in ("extraction_metadata", "analyses", "tables"),
                    "annotate_only": lambda k: k in ("extraction_metadata", "analyses", "tables"),
                    }.get(mode)
            if keep:
                atts = {k: v for k, v in atts.items() if keep(k)}
            elif mode == "analyses_first":
                atts = {k: atts[k] for k in STUDY_ORDER_ANALYSES_FIRST if k in atts}
        desc = (c.get("description") or "").split(". ")[0]
        out.append(f"\n{name}" + (f"  — {desc}" if desc and not brief else ""))
        for an, a in atts.items():
            rng = a.get("range") or "string"
            mv = "[]" if a.get("multivalued") else ""
            req = " REQUIRED" if a.get("required") else ""
            ad = (a.get("description") or "").strip()
            if brief:
                ad = ad.split(".")[0]
            line = f"  {an}: {rng}{mv}{req}"
            if ad:
                line += f" — {ad}"
            out.append(line)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None)
    ap.add_argument("--brief", action="store_true", help="first sentence of each description only")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    full, brief = render(False), render(True)
    if args.report:
        try:
            import tiktoken
            enc = tiktoken.get_encoding("o200k_base")
            tok = lambda s: len(enc.encode(s))                      # noqa: E731
        except ImportError:
            tok = lambda s: len(s) // 4                             # noqa: E731
            print("  (tiktoken unavailable; token counts are chars/4 estimates)")
        js = REPO / "bench" / "extraction.strict.json"
        print(f"  full JSON Schema      {tok(js.read_text()) if js.exists() else 0:>7,} tokens")
        print(f"  rendered prompt       {tok(full):>7,} tokens")
        print(f"  rendered --brief      {tok(brief):>7,} tokens")
        print(f"  preamble alone        {tok(PREAMBLE):>7,} tokens")
    text = brief if args.brief else full
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
    elif not args.report:
        print(text)


if __name__ == "__main__":
    main()
