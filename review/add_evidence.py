"""Stage 4: ask for a supporting quote for every value the extraction passes emitted.

Evidence is extracted separately because carrying it inline makes the extraction
worse, not merely more expensive. Measured on the pipeline_eval benchmark: evidence
was 57% of output tokens, and stripping it took analysis recall from 94% to 98%,
unparseable records from 6 to 0, and cost from $0.0110 to $0.0084 per paper. It was
crowding out the values it was meant to support.

So the extraction passes emit values with no `evidence` key at all, and this pass
adds one to every field. The model returns quotes, never offsets -- it cannot count
characters -- and `build_record.py` locates them in the normalized text, which is
what lets the integrity gate assert `text == source[start_char:end_char]`.

Fields are addressed by the dotted path `build_record` already uses in its reports,
so a quote that fails to resolve names the same field in both tools.

    python review/add_evidence.py --paper HU6mqxmtySg3 \
        --text review/texts/HU6mqxmtySg3/processed/pubget/text.txt \
        --payloads review/payloads/HU6mqxmtySg3 --key-file .env
"""

from __future__ import annotations

import argparse
import json
import re
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_retrieval  # noqa: E402
from extract_record import DEFAULT_MODEL, load_key_file, strip_fence  # noqa: E402

#: Paths per call. Large enough that a paper is a handful of calls, small enough
#: that one malformed reply costs a batch rather than the paper.
BATCH = 50

SYSTEM = """You locate supporting quotes in a scientific paper.

You are given a paper and a list of facts already extracted from it, each with an id
and the value that was recorded. For each id, return the single shortest span of the
paper that supports that value.

Rules:
1. Emit ONE JSON object mapping id -> quote. No prose, no markdown fence.
2. A quote MUST be copied character-for-character from the paper text given to you.
   It is located by exact match and a paraphrase is discarded, taking the evidence
   for that field with it.
3. Prefer one sentence. Never return a whole paragraph when a clause will do.
4. If the paper does not state the fact anywhere, OMIT that id entirely. Do not
   guess, do not return an approximate sentence, and do not invent one. An omitted
   id is recorded honestly as unsupported; a fabricated quote is a false citation.
5. Some values are classifications the paper never words that way (a controlled
   term such as "between_subject"). Quote the sentence the classification was read
   from, not a sentence containing the term."""


def iter_fields(node: Any, path: str = ""):
    """Every ExtractedValue in a payload, with the dotted path build_record reports."""

    if isinstance(node, dict):
        if "extraction_status" in node:
            yield path, node
            return
        for key, value in node.items():
            yield from iter_fields(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_fields(value, f"{path}[{index}]")


def owners(node: Any, path: str = "", owner: str = "") -> dict[str, str]:
    """path -> the name of the entity the field hangs off.

    The retriever scores a unit higher when it names the entity, and an entity's name is
    not recoverable from a dotted path. Cheap to collect on the way past.
    """

    found: dict[str, str] = {}
    if isinstance(node, dict):
        if "extraction_status" in node:
            return {path: owner}
        mine = owner
        for key in ("name", "title", "source_label", "modality"):
            value = (node.get(key) or {}).get("value") if isinstance(node.get(key), dict) else None
            if isinstance(value, str) and 3 < len(value) < 80:
                mine = value
                break
        for key, value in node.items():
            found |= owners(value, f"{path}.{key}" if path else str(key), mine)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found |= owners(value, f"{path}[{index}]", owner)
    return found


def rendered_value(field: dict) -> str:
    value = field.get("value")
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return "" if value in (None, "", []) else str(value)


def union_span(reranker, units, path: str, field: dict, owner: str,
               quote: str | None) -> str | None:
    """A second supporting passage for this field, or None.

    Only fires when the retriever clears its own gate, and never when it lands on the
    passage the model already quoted -- a duplicate set is not a second warrant. Worth
    +6.3 points of located evidence over the quote pass alone, measured over 173 fields
    with human evidence; see docs/evidence-union-design.md.
    """

    value = rendered_value(field)
    if not value:
        return None
    unit = evidence_retrieval.locate(reranker, units,
                                     re.sub(r"\[\d+\]", "", path), value, owner)
    if unit is None:
        return None
    # `unit.text` and not `unit.rendered`: build_record resolves a quote by exact match,
    # and a table row's rendered sentence appears nowhere in the paper.
    if quote and (quote in unit.text or unit.text in quote):
        return None
    return unit.text


def describe(path: str, field: dict) -> str:
    """One line for the model: where the field sits and what was recorded."""

    value = field.get("value")
    if isinstance(value, list):
        rendered = "; ".join(str(item) for item in value)[:300]
    else:
        rendered = str(value)[:300]
    return f"{path} = {rendered}"


def ask(client, model: str, text: str, batch: list[tuple[str, dict]],
        effort: str = "") -> dict[str, str]:
    listing = "\n".join(describe(path, field) for path, field in batch)
    user = (f"# Paper\n\n{text}\n\n# Facts needing a supporting quote\n\n{listing}\n\n"
            "Return the JSON object mapping each id to its quote now.")
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }
    # Without this the pass runs at the model's default, which on a reasoning model is
    # not the low setting the other three passes were measured at.
    if effort:
        kwargs["reasoning_effort"] = effort
    import usage_log
    response, row = usage_log.call(client, **kwargs)
    parsed = json.loads(strip_fence(response.choices[0].message.content or "{}"))
    LAST_USAGE.append(row)
    return ({k: v for k, v in parsed.items() if isinstance(v, str) and v.strip()},
            row["prompt_tokens"], row["completion_tokens"])


#: Rows the ask() helper produced, drained by main() once the payload directory is known.
LAST_USAGE: list = []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", required=True)
    parser.add_argument("--text", required=True, type=Path)
    parser.add_argument("--payloads", required=True, type=Path)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default="low",
                        help="reasoning effort; empty string to send none at all")
    parser.add_argument("--batch", type=int, default=BATCH)
    parser.add_argument("--redo", action="store_true")
    parser.add_argument("--no-union", dest="union", action="store_false",
                        help="skip the retriever; quote pass only")
    parser.add_argument("--reranker-device", default="cpu")
    args = parser.parse_args()

    # The pre-evidence payloads are what the extraction passes actually produced, and
    # the only way to rerun this stage from a clean state. Kept, not overwritten.
    backup = args.payloads / "noev"
    targets = sorted(p for p in args.payloads.glob("*.json") if p.name != "aliases.json")

    # `noev/` is made *before* any evidence is written, so its presence proves this stage
    # started and not that it finished. Reading it as "done" cost seventeen papers their
    # evidence: the stage died loading its reranker, the backup was already on disk, and
    # every resume skipped it and built records with no evidence at all. What finished
    # looks like is evidence in the payloads.
    wrote_evidence = any('"evidence"' in target.read_text(encoding="utf-8")
                         for target in targets)
    if backup.is_dir() and wrote_evidence and not args.redo:
        print(f"  evidence: already done ({backup}/ exists; --redo to rerun)")
        return 0

    resuming = backup.is_dir() and not wrote_evidence
    if resuming:
        print(f"  evidence: {backup}/ exists but no payload carries evidence -- an "
              f"interrupted run; restoring the pre-evidence payloads and starting over")
    backup.mkdir(parents=True, exist_ok=True)
    if args.redo or resuming:
        for saved in backup.glob("*.json"):
            shutil.copy(saved, args.payloads / saved.name)
    else:
        for target in targets:
            shutil.copy(target, backup / target.name)

    if args.key_file:
        load_key_file(args.key_file)
    if not os.environ.get("OPENAI_API_KEY"):
        print("no OPENAI_API_KEY; pass --key-file", file=sys.stderr)
        return 2
    from openai import OpenAI
    import usage_log
    client = usage_log.build_client(args.paper, "evidence")

    text = args.text.read_text(encoding="utf-8")
    started = time.time()
    total_in = total_out = 0
    filled = unsupported = not_reported = unioned = recovered = 0

    # Optional by design: the union is an enhancement, and a missing torch must not take
    # the evidence stage down with it.
    reranker = evidence_retrieval.load_reranker(device=args.reranker_device) if args.union else None
    units = evidence_retrieval.sentence_units(text) if reranker else []
    if args.union and reranker is None:
        print("  evidence: no reranker available (install torch); quote pass only")

    for target in targets:
        payload = json.loads(target.read_text(encoding="utf-8"))
        fields = list(iter_fields(payload))
        wanted = [(path, field) for path, field in fields
                  if field.get("extraction_status") == "extracted"]

        quotes: dict[str, str] = {}
        for start in range(0, len(wanted), args.batch):
            batch = wanted[start:start + args.batch]
            try:
                found, tin, tout = ask(client, args.model, text, batch, args.effort)
            except Exception as exc:
                print(f"  {target.name} batch {start // args.batch}: "
                      f"FAILED {type(exc).__name__}: {exc}"[:200], file=sys.stderr)
                continue
            quotes.update(found)
            total_in += tin
            total_out += tout

        # Every field gets an evidence block, because `evidence` is REQUIRED on
        # ExtractedValue and build_record leaves a field without one untouched --
        # it would fail validation later rather than here.
        owner_of = owners(payload)
        for path, field in fields:
            if field.get("extraction_status") != "extracted":
                field.pop("value", None)
                field["evidence"] = {"status": "not_applicable"}
                not_reported += 1
                continue

            quote = quotes.get(path)
            sets = [{"quotes": [quote]}] if quote else []
            second = (union_span(reranker, units, path, field, owner_of.get(path, ""), quote)
                      if reranker else None)
            if second:
                sets.append({"quotes": [second]})
                unioned += 1
                if not quote:
                    recovered += 1

            if sets:
                field["evidence"] = {"status": "present", "sets": sets}
                filled += 1
            else:
                field["evidence"] = {"status": "not_found"}
                unsupported += 1

        target.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n",
                          encoding="utf-8")

    total = filled + unsupported
    rate = f"{filled / total:.0%}" if total else "n/a"
    for _row in LAST_USAGE:
        usage_log.record(args.payloads / "usage.jsonl", args.paper, "evidence", _row)
    LAST_USAGE.clear()
    union_note = (f", {unioned} retrieved ({recovered} otherwise unsupported)"
                  if reranker else "")
    print(f"  evidence: {filled}/{total} quoted ({rate}), {unsupported} unsupported, "
          f"{not_reported} not_reported{union_note} · {total_in}->{total_out} tok "
          f"in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
