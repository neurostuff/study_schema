"""Stage 1: split each coordinate table into the analyses it reports.

The extraction passes never see the table rows -- the normalized paper text carries
captions but not cell values -- so this is the only place the reported effects are
enumerated. Everything downstream annotates the list this produces, which makes a
stage-1 regression a stage-2 outage rather than a degradation.

The parse itself is Autonima's `parse_single_table`: one LLM call per table. Nothing
about the splitting rules lives here, so the prompt version travels with autonima and is
recorded in the output.

What does live here is the transport. Autonima constrains the output with legacy function
calling, and the gateway refuses that for a reasoning model:

    Function tools with reasoning_effort are not supported for gpt-5.6-luna in
    /v1/chat/completions. To use function tools, use /v1/responses or set
    reasoning_effort to 'none'.

So `_StructuredCoordinateClient` below asks for the same Pydantic schema as a strict
`response_format` instead, which the same endpoint does accept alongside
`reasoning_effort`. Autonima is not modified: its other callers are on models where
function calling works, and the schema and the sanitizer are imported rather than copied
so this cannot drift from what `parse_single_table` expects back.

The pond corpus already holds a parse of these same tables under
`processed/pubget/analyses.jsonl`. It is not used as input -- it is diffed against, so
a change in the upstream prompt is visible rather than assumed.

    python review/parse_tables.py --pmids bench-baseline.pmids \
        --autonima .tmp_repos/autonima --key-file .env
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_texts import read_pmids  # noqa: E402

DEFAULT_MODEL = "@psyc-aid338-ope-333f18/gpt-5.6-luna"

#: Matches the extraction passes. The baseline run measured 220-410 reasoning tokens per
#: call at this setting and found nothing in the error profile that looked like a
#: reasoning shortfall, so the tables get the same budget the prose does.
DEFAULT_EFFORT = "low"


def strict_schema(model_class) -> dict:
    """A Pydantic model's JSON schema, tightened until the API will accept it as strict.

    Structured outputs are stricter than function parameters were: every property has to
    be listed in `required` and every object has to forbid extra keys. Pydantic omits a
    field from `required` as soon as it has a default, which is most of this schema, so
    the fields are made *nullable and required* rather than optional -- the same shape
    `Optional[X] = None` already meant, said in the way the API demands.
    """

    def tighten(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                tighten(item)
            return
        if not isinstance(node, dict):
            return

        # A default is advisory and strict mode rejects it outright.
        node.pop("default", None)

        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            properties = node["properties"]
            for name, sub in properties.items():
                # Required-but-nullable, so omitting the key stops being an option while
                # "there is no value here" stays one.
                if "$ref" not in sub and "anyOf" not in sub and "type" in sub:
                    if sub["type"] != "null" and name not in node.get("required", []):
                        sub["type"] = [sub["type"], "null"]
            node["required"] = list(properties)

        for value in node.values():
            tighten(value)

    schema = model_class.model_json_schema()
    tighten(schema)
    return schema


def build_client(effort: str):
    """An autonima coordinate client that speaks structured outputs instead of functions.

    Subclasses autonima's own client rather than reimplementing it: the api-key and
    `OPENAI_API_GATEWAY` base-url handling is already there and is not worth a second
    copy. Only the one call is replaced.
    """

    from autonima.coordinates.openai_client import (  # noqa: PLC0415
        CoordinateParsingClient, _sanitize_parse_result,
    )
    from autonima.coordinates.schema import ParseAnalysesOutput  # noqa: PLC0415

    class _StructuredCoordinateClient(CoordinateParsingClient):
        def parse_analyses(self, prompt: str, model: str = DEFAULT_MODEL):
            kwargs = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful assistant that parses neuroimaging results "
                            "tables into structured JSON for downstream analysis."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "parse_analyses",
                        "strict": True,
                        "schema": strict_schema(ParseAnalysesOutput),
                    },
                },
            }
            if effort:
                kwargs["reasoning_effort"] = effort

            response = self.client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
            if not content:
                # A refusal or a length stop arrives as an empty body; the caller counts
                # it as a failed table rather than an empty one, which is the difference
                # between "this table reports nothing" and "this table was not read".
                raise ValueError(
                    f"empty response from {model} "
                    f"(finish_reason={response.choices[0].finish_reason})"
                )
            return ParseAnalysesOutput(**_sanitize_parse_result(json.loads(content)))

    return _StructuredCoordinateClient()


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


def coordinate_tables(study_dir: Path) -> list[dict]:
    """The tables pubget found coordinates in, with the CSV text to parse.

    `contains_coordinates` is pubget's own determination, the same filter the
    corpus used; parsing the demographics tables would spend calls to produce
    analyses with no points.
    """

    manifest = study_dir / "processed" / "pubget" / "tables.jsonl"
    tables_dir = study_dir / "source" / "pubget" / "tables"
    out = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        table = json.loads(line)
        if not table.get("contains_coordinates"):
            continue
        metadata = table.get("metadata") or {}
        name = Path(metadata.get("data_path") or "").name
        csv_path = tables_dir / name
        if not name or not csv_path.is_file():
            print(f"    WARNING: no CSV for {table['table_id']} ({name or 'no data_path'})",
                  file=sys.stderr)
            continue
        out.append({
            "table_id": table["table_id"],
            "table_number": table.get("table_number"),
            "table_label": metadata.get("table_label"),
            "caption": table.get("caption") or "",
            "footer": table.get("footer") or "",
            "csv_path": csv_path,
            "csv_text": csv_path.read_text(encoding="utf-8"),
        })
    return out


def pond_analyses(study_dir: Path) -> list[dict]:
    """The corpus's own parse, for comparison only."""

    path = study_dir / "processed" / "pubget" / "analyses.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def diff_report(study: str, fresh: list[dict], pond: list[dict]) -> str:
    """Names and point counts, ours versus the corpus's."""

    def key(analysis):
        return (analysis.get("name") or "").strip()

    fresh_names = [key(a) for a in fresh]
    pond_names = [key(a) for a in pond]
    lines = [
        f"# Stage 1 re-parse vs pond — {study}",
        "",
        f"- fresh: **{len(fresh)}** analyses, {sum(len(a.get('points') or []) for a in fresh)} points",
        f"- pond:  **{len(pond)}** analyses, {sum(len(a.get('coordinates') or []) for a in pond)} points",
        "",
        "| # | fresh | pond |",
        "|---|---|---|",
    ]
    for index in range(max(len(fresh_names), len(pond_names))):
        left = fresh_names[index] if index < len(fresh_names) else "—"
        right = pond_names[index] if index < len(pond_names) else "—"
        flag = "" if left == right else "  ⚠"
        lines.append(f"| {index + 1} | {left}{flag} | {right} |")
    only_fresh = sorted(set(fresh_names) - set(pond_names))
    only_pond = sorted(set(pond_names) - set(fresh_names))
    if only_fresh or only_pond:
        lines += ["", "## Set difference", ""]
        for name in only_fresh:
            lines.append(f"- fresh only: `{name}`")
        for name in only_pond:
            lines.append(f"- pond only: `{name}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmids", type=Path, default=REPO / "bench-baseline.pmids")
    parser.add_argument("--texts", type=Path, default=REPO / "review" / "texts")
    parser.add_argument("--autonima", type=Path, default=REPO / ".tmp_repos" / "autonima")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default=DEFAULT_EFFORT,
                        help="reasoning effort; empty string to send none at all")
    parser.add_argument("--key-file", type=Path, default=REPO / ".env")
    parser.add_argument("--dry-run", action="store_true", help="list tables, make no calls")
    args = parser.parse_args()

    sys.path.insert(0, str(args.autonima.resolve()))
    from autonima.coordinates.parser import parse_single_table
    from autonima.coordinates.prompts import COORDINATE_PARSING_PROMPT_VERSION

    client = None
    if not args.dry_run:
        if args.key_file and args.key_file.is_file():
            load_key_file(args.key_file)
        if not os.environ.get("OPENAI_API_KEY"):
            print("no OPENAI_API_KEY; pass --key-file", file=sys.stderr)
            return 2
        client = build_client(args.effort)

    print(f"autonima prompt version {COORDINATE_PARSING_PROMPT_VERSION}, "
          f"model {args.model}, effort {args.effort or 'unset'}\n")

    failures = 0
    for pmid, study, _axis in read_pmids(args.pmids):
        study_dir = args.texts / study
        tables = coordinate_tables(study_dir)
        print(f"{study} (pmid {pmid}): {len(tables)} coordinate tables")

        analyses: list[dict] = []
        for table in tables:
            if args.dry_run:
                print(f"  {table['table_id']}: {len(table['csv_text']):,} ch (dry run)")
                continue
            try:
                result = parse_single_table(
                    table["table_id"], table["caption"], table["footer"],
                    table["csv_text"], client, args.model,
                )
                parsed = result["parsed_json"].get("analyses") or []
            except Exception as exc:                  # one table must not sink the study
                print(f"  {table['table_id']}: FAILED {type(exc).__name__}: {exc}"[:200],
                      file=sys.stderr)
                failures += 1
                continue
            for analysis in parsed:
                # Table identity is what disambiguates repeated analysis names, so it
                # is attached here rather than left to the caller to reconstruct.
                analysis["table_id"] = table["table_id"]
                analysis["table_number"] = table["table_number"]
                analysis["table_label"] = table["table_label"]
                analysis["table_caption"] = table["caption"]
                analysis["table_footer"] = table["footer"]
            analyses.extend(parsed)
            print(f"  {table['table_id']}: {len(parsed)} analyses, "
                  f"{sum(len(a.get('points') or []) for a in parsed)} points")

        if args.dry_run:
            continue

        out_dir = study_dir / "stage1"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "analyses.json").write_text(json.dumps({
            "study": study, "pmid": pmid, "model": args.model,
            "effort": args.effort,
            "prompt_version": COORDINATE_PARSING_PROMPT_VERSION,
            "analyses": analyses,
        }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

        pond = pond_analyses(study_dir)
        (out_dir / "diff-vs-pond.md").write_text(diff_report(study, analyses, pond),
                                                 encoding="utf-8")
        mark = "same count" if len(analyses) == len(pond) else f"DIFFERS (pond {len(pond)})"
        print(f"  -> {len(analyses)} analyses, {mark}\n")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
