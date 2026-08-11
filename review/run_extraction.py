"""Drive the full extraction workflow over a pmids file.

The shape is the one `bench/RESULTS.md` recommends, adapted to the current schema:

    1  tables -> analyses          review/parse_tables.py (run separately; costs money)
    2  entities                    one call, no evidence
    3  analyses                    one call, annotating stage 1's list, no evidence
    4  evidence                    review/add_evidence.py, quotes only
    5  build + validate            review/build_record.py, review/validate_record.py

Stage 1 is not re-run here: it is the load-bearing input, so it is versioned on disk
and a rerun is an explicit act.

`tables` is not extracted by a model at all. `table_number`, `caption` and `footer`
are literal strings in the pubget manifest, so retyping them through an LLM can only
introduce error; they are copied, and the analyses pass is told the local_ids.

    python review/run_extraction.py --pmids bench-baseline.pmids --key-file .env
    python review/run_extraction.py --pmids bench-baseline.pmids --stages build
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REVIEW = Path(__file__).resolve().parent
sys.path.insert(0, str(REVIEW))
from sync_texts import read_pmids  # noqa: E402

STAGES = ["tables", "entities", "analyses", "evidence", "build"]
DEFAULT_MODEL = "@psyc-aid338-ope-333f18/gpt-5.6-luna"


def field(value: str | None, source: str = "reported") -> dict:
    """One ExtractedValue. Evidence is left to the evidence pass, as everywhere else."""

    if value is None or not str(value).strip():
        return {"extraction_status": "not_reported"}
    return {"extraction_status": "extracted", "value": value, "value_source": source}


def build_tables_payload(study_dir: Path) -> tuple[dict, dict[str, str]]:
    """Copy the pubget table manifest into Table records, plus the id map.

    Local ids are positional rather than derived from `table_number`, because the
    manifest is not guaranteed to number tables uniquely -- 4cRnHYtfSwuK carries two
    tables numbered 1, and keying on that would collapse them into one record.
    """

    manifest = study_dir / "processed" / "pubget" / "tables.jsonl"
    tables, id_map = [], {}
    for index, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        source = json.loads(line)
        local_id = f"tbl{index}"
        id_map[source["table_id"]] = local_id
        metadata = source.get("metadata") or {}
        label = metadata.get("table_label") or (
            f"Table {source['table_number']}" if source.get("table_number") else None)
        tables.append({
            "local_id": local_id,
            "table_number": field(label),
            "caption": field(source.get("caption")),
            "footer": field(source.get("footer")),
        })
    return {"tables": tables}, id_map


def run(command: list[str]) -> int:
    print("  $ " + " ".join(str(part) for part in command[1:]), flush=True)
    return subprocess.run([str(part) for part in command]).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmids", type=Path, default=REPO / "bench-baseline.pmids")
    parser.add_argument("--texts", type=Path, default=REVIEW / "texts")
    parser.add_argument("--payloads", type=Path, default=REVIEW / "payloads")
    parser.add_argument("--examples", type=Path, default=REVIEW / "examples")
    parser.add_argument("--stages", nargs="*", default=STAGES, choices=STAGES)
    parser.add_argument("--key-file", type=Path, default=REPO / ".env")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default="low")
    parser.add_argument("--redo", action="store_true", help="rerun stages already written")
    args = parser.parse_args()

    python = sys.executable
    failures = 0

    for pmid, study, axis in read_pmids(args.pmids):
        print(f"\n=== {study} (pmid {pmid}) — {axis}")
        study_dir = args.texts / study
        text = study_dir / "processed" / "pubget" / "text.txt"
        payload_dir = args.payloads / study
        payload_dir.mkdir(parents=True, exist_ok=True)
        stage1 = study_dir / "stage1" / "analyses.json"
        table_map = study_dir / "stage1" / "table-map.json"

        if "tables" in args.stages:
            payload, id_map = build_tables_payload(study_dir)
            (payload_dir / "tables.json").write_text(
                json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            table_map.parent.mkdir(parents=True, exist_ok=True)
            table_map.write_text(json.dumps(id_map, indent=1) + "\n", encoding="utf-8")
            print(f"  tables: {len(payload['tables'])} records (deterministic)")

        common = ["--paper", study, "--text", text, "--out-dir", args.payloads,
                  "--key-file", args.key_file, "--model", args.model,
                  "--effort", args.effort, "--no-evidence"]

        if "entities" in args.stages:
            if (payload_dir / "entities.json").is_file() and not args.redo:
                print("  entities: already done")
            else:
                failures += bool(run([python, REVIEW / "extract_record.py",
                                      "--mode", "entities", *common]))

        if "analyses" in args.stages:
            if (payload_dir / "analyses.json").is_file() and not args.redo:
                print("  analyses: already done")
            else:
                failures += bool(run([python, REVIEW / "extract_record.py",
                                      "--mode", "analyses", *common,
                                      "--entities", payload_dir / "entities.json",
                                      "--stage1", stage1, "--tables", table_map]))

        if "evidence" in args.stages:
            failures += bool(run([python, REVIEW / "add_evidence.py",
                                  "--paper", study, "--text", text,
                                  "--payloads", payload_dir,
                                  "--key-file", args.key_file, "--model", args.model,
                                  *(["--redo"] if args.redo else [])]))

        if "build" in args.stages:
            args.examples.mkdir(parents=True, exist_ok=True)
            record = args.examples / f"{study}.extraction.json"
            failures += bool(run([python, REVIEW / "build_record.py",
                                  "--paper", study, "--text", text,
                                  "--payloads", payload_dir, "--out", record,
                                  "--extractor-model", args.model,
                                  "--extraction-date", date.today().isoformat()]))
            raw = args.examples / f"{study}.extraction.raw.json"
            if record.is_file() and (args.redo or not raw.is_file()):
                raw.write_text(record.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"  kept untouched model output at {raw.name}")
            failures += bool(run([python, REVIEW / "validate_record.py",
                                  "--record", record, "--text", text]))

    print(f"\n{'FAILURES: ' + str(failures) if failures else 'all stages clean'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
