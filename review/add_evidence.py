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
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
    response = client.chat.completions.create(**kwargs)
    parsed = json.loads(strip_fence(response.choices[0].message.content or "{}"))
    usage = response.usage
    return ({k: v for k, v in parsed.items() if isinstance(v, str) and v.strip()},
            usage.prompt_tokens, usage.completion_tokens)


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
    args = parser.parse_args()

    # The pre-evidence payloads are what the extraction passes actually produced, and
    # the only way to rerun this stage from a clean state. Kept, not overwritten.
    backup = args.payloads / "noev"
    targets = sorted(p for p in args.payloads.glob("*.json") if p.name != "aliases.json")
    if backup.is_dir() and not args.redo:
        print(f"  evidence: already done ({backup}/ exists; --redo to rerun)")
        return 0
    backup.mkdir(parents=True, exist_ok=True)
    if args.redo:
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
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"],
                    base_url=os.environ.get("OPENAI_API_GATEWAY"))

    text = args.text.read_text(encoding="utf-8")
    started = time.time()
    total_in = total_out = 0
    filled = unsupported = not_reported = 0

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
        for path, field in fields:
            if field.get("extraction_status") != "extracted":
                field.pop("value", None)
                field["evidence"] = {"status": "not_applicable"}
                not_reported += 1
            elif path in quotes:
                field["evidence"] = {"status": "present",
                                     "sets": [{"quotes": [quotes[path]]}]}
                filled += 1
            else:
                field["evidence"] = {"status": "not_found"}
                unsupported += 1

        target.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n",
                          encoding="utf-8")

    total = filled + unsupported
    rate = f"{filled / total:.0%}" if total else "n/a"
    print(f"  evidence: {filled}/{total} quoted ({rate}), {unsupported} unsupported, "
          f"{not_reported} not_reported · {total_in}->{total_out} tok "
          f"in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
