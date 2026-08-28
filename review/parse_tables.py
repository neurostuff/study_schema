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

#: The only parsed value kind that cannot carry a sign. Everything else -- `t-statistic`,
#: `z-statistic`, `correlation`, `beta`, and the `other` catch-all -- is a quantity whose
#: sign means something when the table prints one.
#:
#: `other` is included deliberately. It holds statistic-like values the parser could not
#: label (one study contributes 124 of them in the 0.61-3.75 range, which is a t or z that
#: lost its heading), so excluding it would discard real directions. And no kind is judged
#: non-directional because this corpus happens to show no negatives for it: most tables
#: print |t|, so an all-positive column is evidence about the table's conventions and not
#: about the quantity.
NON_DIRECTIONAL_KINDS = frozenset({"p-value"})


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


def _point_sign(point: dict) -> int | None:
    """A row's direction from its statistics: +1, -1, or None when it has no sign to give.

    None covers two different silences that must not be split on. A row carrying only a
    p-value or a cluster extent has no direction printed, and a row whose statistics
    disagree in sign -- a positive t beside a negative correlation -- is a parse to look at
    rather than a row to file.
    """

    signs = {
        1 if value > 0 else -1
        for entry in (point.get("values") or [])
        if entry.get("kind") not in NON_DIRECTIONAL_KINDS
        and isinstance(value := entry.get("value"), (int, float))
        and value != 0
    }
    return signs.pop() if len(signs) == 1 else None


def split_opposite_signs(analyses: list[dict]) -> tuple[list[dict], list[str]]:
    """Split any analysis whose rows report both directions into one analysis per direction.

    The schema requires a separate Analysis per normalized direction, and this is the only
    stage that can see the row values: the extraction passes are shown captions, never
    cells. So the pass downstream is asked to split on "effects of opposite sign" using a
    signal it cannot observe. Doing it here makes the partition arithmetic instead.

    Splitting only, never merging, and only on a total partition -- if any row has no sign
    the analysis is reported and left whole, because a partial split files some rows and
    silently strands the rest. Group sizes are not weighed: one surviving cluster in the
    minority direction is an ordinary result of thresholding, and 11% of parsed analyses
    have a single point already, so a lone row is no evidence of a bad parse.

    Only the positive-sign part is offered to the extraction pass, and it keeps the
    parsed name unchanged. A paper that reports "FESZ > NC" prints positive statistics
    for the effects it describes and negative ones for the same contrast read the other
    way; the reversed half is almost never written down, so asking a model to name and
    define it invites invention. The negative part is emitted withheld, carrying
    `mirror_of`, and `derive_direction.mirror_analysis` rebuilds it after extraction by
    reversing the directions the model assigned to the half that was described.
    """

    out: list[dict] = []
    notes: list[str] = []

    for analysis in analyses:
        points = analysis.get("points") or []
        signs = [_point_sign(point) for point in points]
        present = {s for s in signs if s is not None}

        if len(present) < 2:
            out.append(analysis)
            continue

        name = analysis.get("name") or "(unnamed)"
        if None in signs:
            unsigned = sum(1 for s in signs if s is None)
            notes.append(f"FLAG {name}: both directions present but {unsigned} of "
                         f"{len(points)} rows carry no sign -- left whole")
            out.append(analysis)
            continue

        for sign, label in ((1, "positive"), (-1, "negative")):
            part = dict(analysis)
            part["points"] = [p for p, s in zip(points, signs) if s == sign]
            #: The parent's identity, kept so the split is auditable and so a reviewer can
            #: see that two entries came from one parse rather than from two table rows.
            part["split_from"] = name
            part["split_direction"] = label
            part["split_rule"] = "sign-of-directional-statistic"
            if sign > 0:
                # The half the paper describes. Its name is the paper's.
                part["name"] = name
            else:
                part["name"] = f"{name} (reversed)"
                part["mirror_of"] = name
                #: Never shown to the extraction pass. The reversed contrast has no prose
                #: in the paper to quote, so a model asked to define it can only guess.
                part["withhold"] = True
            out.append(part)

        counts = f"{signs.count(1)}+/{signs.count(-1)}-"
        notes.append(f"SPLIT {name} -> ({counts}) on statistic sign; "
                     f"the negative half is withheld and mirrored after extraction")

    return out, notes


def adopt_withholding(analyses: list[dict]) -> tuple[list[dict], list[str]]:
    """Convert a pair split by the earlier rule into a described half and a withheld one.

    A corpus partitioned before the mirror existed holds both halves as ordinary entries,
    `<name> (positive)` and `<name> (negative)`, and both were sent to the extraction
    pass. The negative half has no prose in the paper to quote, so what came back for it
    was invention -- and it cost a full analysis's worth of tokens to obtain.

    Re-splitting cannot reach these: each part already holds one sign, so
    `split_opposite_signs` correctly finds nothing to do. The conversion is done from the
    parts themselves, which carry `split_from` and `split_direction` and so record
    everything needed. Nothing is re-parsed and no statistic is re-read.

    Only a clean pair converts. Three parts sharing a parent means the entry was also
    split on something else -- a band, a session -- and which of them the paper describes
    is not answerable from the sign alone.
    """

    from collections import defaultdict

    families: dict[str, list[dict]] = defaultdict(list)
    for analysis in analyses:
        if analysis.get("split_rule") == "sign-of-directional-statistic":
            parent = analysis.get("split_from")
            if parent:
                families[parent].append(analysis)

    converted: list[str] = []
    for parent, parts in families.items():
        directions = [p.get("split_direction") for p in parts]
        if sorted(directions) != ["negative", "positive"]:
            continue
        # Already in the described/withheld shape. Reporting a conversion here would
        # make the stage that calls this look permanently unfinished, so a resumed run
        # would re-enter it forever.
        if any(part.get("withhold") for part in parts):
            continue
        for part in parts:
            if part["split_direction"] == "positive":
                part["name"] = parent
                part.pop("withhold", None)
                part.pop("mirror_of", None)
            else:
                part["name"] = f"{parent} (reversed)"
                part["mirror_of"] = parent
                part["withhold"] = True
        converted.append(f"WITHHOLD {parent}: the reversed half is no longer extracted")
    return analyses, converted


def parse_keys(analyses: list[dict]) -> list[str]:
    """A stable address per parsed entry, positionally aligned with `analyses`.

    `Analysis.source_table_analysis` holds one of these, and it is the only exact route
    from an analysis to the coordinate rows it was read off. Both sides of that contract
    must number identically: `extract_record.stage1_block` prints the key to the model and
    `build_record.resolve_source_table_analysis` resolves what comes back.

    Numbered over EVERY entry, including the withheld half of a sign-split. The prompt
    hides withheld entries -- the paper has no prose for them -- and numbering only what
    is shown makes hiding one renumber its siblings, so the model is told `t1#2` and the
    builder resolves `t1#2` to a different row group. A wrong key that exists is worse
    than a missing one: it passes the join and attaches the analysis to another
    contrast's coordinates.
    """

    ordinals: dict[str, int] = {}
    keys: list[str] = []
    for entry in analyses:
        table_id = str((entry or {}).get("table_id") or "")
        ordinals[table_id] = ordinals.get(table_id, 0) + 1
        keys.append(f"{table_id}#{ordinals[table_id]}")
    return keys


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


def resplit(pmids: Path, texts: Path) -> int:
    """Re-partition stage-1 output already on disk, without re-parsing the tables.

    The split reads only the parsed statistics, so a corpus parsed before this rule existed
    does not have to be re-parsed to get it -- which matters because re-parsing is a model
    call per table and would resample every other decision the parse makes at the same time.
    Already-split entries are left alone: `split_rule` marks them, and the second pass over
    a part that holds one direction finds one sign and does nothing.
    """

    changed = 0
    for pmid, study, _axis in read_pmids(pmids):
        path = texts / study / "stage1" / "analyses.json"
        if not path.is_file():
            print(f"{study}: no stage1/analyses.json", file=sys.stderr)
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        before = doc.get("analyses") or []
        after, notes = split_opposite_signs(before)
        if not notes:
            print(f"{study}: unchanged ({len(before)} analyses)")
            continue
        for note in notes:
            print(f"{study}: {note}")
        if len(after) != len(before):
            doc["analyses"] = after
            #: Recorded on the document rather than inferred from the parts, so a reader can
            #: tell a file the rule has been applied to from one parsed before it existed.
            doc["sign_split_applied"] = True
            path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
                            encoding="utf-8")
            print(f"{study}: {len(before)} -> {len(after)} analyses, rewrote {path}")
            changed += 1
    print(f"\n{changed} study file(s) rewritten")
    return 0


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
    parser.add_argument("--resplit", action="store_true",
                        help="apply the sign split to the stage1/analyses.json already on "
                             "disk and rewrite it; the partition is arithmetic, so this "
                             "needs no model call and no autonima checkout")
    args = parser.parse_args()

    if args.resplit:
        return resplit(args.pmids, args.texts)

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
            parsed, notes = split_opposite_signs(parsed)
            for note in notes:
                print(f"    {note}")
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
