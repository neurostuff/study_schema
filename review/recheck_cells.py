#!/usr/bin/env python3
"""Re-ask one question per analysis: is this contrast's sign on the right term?

The analyses pass has the whole schema to satisfy and answers `Effect.cells` in passing.
This pass asks about nothing else. It is given one analysis, the model terms it may use,
the cells already emitted, and the paper, and it returns the cells it thinks are right.

Aimed at one measured defect: a correlation contrast that takes a held level plus a signed
slope gets one cell instead of two, reproducibly, and the sign lands on whichever term the
analysis was named after. Both are decidable from a sentence, which is why a targeted
re-ask is the right shape and self-consistency is not -- voting over a consistent error
returns the error.

    python review/recheck_cells.py --paper xevP8UDRAVh9 --text <text> \\
        --payloads payloads/xevP8UDRAVh9 --key-file .env
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

REVIEW = Path(__file__).resolve().parent
sys.path.insert(0, str(REVIEW))

from extract_record import (  # noqa: E402
    DEFAULT_MODEL, extract, load_key_file, strip_fence,
)

SYSTEM = """You check one thing: whether a reported contrast's `Effect.cells` name the right
model terms and the right directions. Answer with JSON and nothing else.

A Cell is one side of the tested comparison: a model term, which level of it (for a
categorical term), and which way that level entered.

  positive    this level is on the plus side, or this slope came out positive
  negative    this level is on the minus side, or this slope came out negative
  held        this level was on BOTH sides at once -- it was fixed while something else
              varied -- so no report could have signed it
  undirected  the test yields no per-level sign at all (F, chi-square)

Two rules decide most cases.

1. A result of the form "WITHIN X, Y went this way" takes TWO cells, not one. X was fixed
   rather than compared, so its cell is `held`; Y carries the sign. Read "amygdala response
   rose with trait anxiety, within the faces condition" as a `held` cell on the stimulus
   term at level "faces", plus a `positive` cell on the trait-anxiety term. Putting the sign
   on X instead says the study compared faces against something, which it did not. This
   applies whatever X and Y are -- a condition and a covariate, an occasion and a slope, a
   cohort and a measure.

2. A COMPARISON BETWEEN two levels of one term takes two signed cells on that term: the
   greater level `positive`, the lesser `negative`. "A > B" is one positive and one
   negative cell, never a single positive one.

Those examples are from a different study than the one you are reading. Take their shape
and none of their content: no label, level or identifier from them belongs in your answer.

The sign belongs to whichever term the paper says the effect is *of*. An analysis named
after a condition is not thereby a contrast between conditions.

Return: {"cells": [{"term": "<term local_id>", "level": "<level label or null>",
"direction": "positive|negative|held|undirected", "why": "<the clause you read it off>"}],
"changed": true|false}

Use only the term local_ids offered. If the cells you were given are already right, return
them unchanged with "changed": false."""


def terms_block(payload: Mapping[str, Any]) -> str:
    lines = ["Model terms available:"]
    for model in payload.get("model_estimations") or []:
        for term in model.get("terms") or []:
            kind = (term.get("type") or {}).get("value", "?")
            name = (term.get("name") or {}).get("value", "?")
            levels = [(level.get("level") or {}).get("value")
                      if isinstance(level.get("level"), dict) else level.get("level")
                      for level in (term.get("levels") or [])]
            levels = [level for level in levels if level]
            lines.append(f"  {term.get('local_id')}  \"{name}\"  {kind}"
                         + (f"  levels: {levels}" if levels else "  (no levels: a slope)"))
    return "\n".join(lines)


def cells_of(analysis: Mapping[str, Any]) -> list[dict]:
    out = []
    for cell in (analysis.get("effect") or {}).get("cells") or []:
        level = cell.get("level")
        out.append({
            "term": cell.get("term"),
            "level": level.get("value") if isinstance(level, dict) else level,
            "direction": (cell.get("direction") or {}).get("value")
            if isinstance(cell.get("direction"), dict) else cell.get("direction"),
        })
    return out


def wrap(value: Any) -> dict:
    return {"extraction_status": "extracted", "value": value, "value_source": "reported"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", required=True)
    parser.add_argument("--text", required=True, type=Path)
    parser.add_argument("--payloads", required=True, type=Path)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default="low")
    parser.add_argument("--max-out", type=int, default=16_000)
    args = parser.parse_args()

    analyses_path = args.payloads / "analyses.json"
    entities_path = args.payloads / "entities.json"
    if not analyses_path.is_file():
        print("no analyses payload to recheck", file=sys.stderr)
        return 0
    analyses_payload = json.loads(analyses_path.read_text(encoding="utf-8"))
    entities = (json.loads(entities_path.read_text(encoding="utf-8"))
                if entities_path.is_file() else {})
    analyses = analyses_payload.get("analyses") or []
    if not analyses:
        return 0

    if args.key_file:
        load_key_file(args.key_file)
    if not os.environ.get("OPENAI_API_KEY"):
        print("no OPENAI_API_KEY; pass --key-file", file=sys.stderr)
        return 2
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"],
                    base_url=os.environ.get("OPENAI_API_GATEWAY"))

    text = args.text.read_text(encoding="utf-8")[:200_000]
    terms = terms_block(entities)
    valid = {term.get("local_id")
             for model in entities.get("model_estimations") or []
             for term in model.get("terms") or []}

    changed = 0
    started = time.time()
    # One call per analysis, each carrying the whole paper, so this pass costs roughly
    # (analyses x text) and can exceed every other stage combined. It reported nothing at
    # all until now, which made the workflow that includes it look free.
    total_in = total_out = calls = 0
    for analysis in analyses:
        name = (analysis.get("name") or {}).get("value", analysis.get("local_id"))
        definition = (analysis.get("definition") or {}).get("value", "")
        user = (f"{terms}\n\nAnalysis: {name}\nDefinition: {definition}\n"
                f"Cells currently emitted: {json.dumps(cells_of(analysis))}\n\n"
                f"# Paper\n\n{text}\n\nReturn the JSON now.")
        try:
            payload, usage = extract(client, args.model, SYSTEM, user,
                                     args.effort, args.max_out)
        except Exception as error:  # noqa: BLE001 -- a recheck must never lose the record
            print(f"  recheck failed for {name}: {type(error).__name__}: {error}",
                  file=sys.stderr)
            continue
        calls += 1
        # `extract` returns usage_log's row, which is a mapping, not the SDK object.
        total_in += (usage or {}).get("prompt_tokens") or 0
        total_out += (usage or {}).get("completion_tokens") or 0
        if isinstance(payload, str):
            try:
                payload = json.loads(strip_fence(payload))
            except ValueError:
                continue
        cells = payload.get("cells") if isinstance(payload, Mapping) else None
        if not isinstance(cells, list) or not cells:
            continue
        # A recheck that names a term the model never declared would break the record;
        # keeping the original is the safe failure, and the sweep sees it as no change.
        if any(cell.get("term") not in valid for cell in cells):
            print(f"  recheck for {name} named an unknown term; kept the original",
                  file=sys.stderr)
            continue
        if cells_of(analysis) == [{"term": c.get("term"), "level": c.get("level"),
                                   "direction": c.get("direction")} for c in cells]:
            continue
        analysis.setdefault("effect", {})["cells"] = [
            {"term": cell["term"],
             **({"level": wrap(cell["level"])} if cell.get("level") else {}),
             "direction": wrap(cell.get("direction"))}
            for cell in cells
        ]
        changed += 1

    analyses_path.write_text(json.dumps(analyses_payload, indent=1, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    # The same shape every other stage prints, so one parser reads them all.
    print(f"{args.paper}/recheck: {total_in}->{total_out} tok in "
          f"{time.time() - started:.0f}s [{calls} call(s)]")
    print(f"{args.paper}/recheck_cells: {changed} of {len(analyses)} analyses rewritten "
          f"in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
