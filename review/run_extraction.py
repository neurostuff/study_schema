"""Drive an extraction workflow over a pmids file.

Two orderings, chosen with `--workflow`. Both start from the coordinate-table parse and
end in a built, validated record; what differs is which pass decides the entities exist.

    demand-driven (default recommendation)      entity-first (the older shape)
      tables    copy the pubget manifest          tables
      demands   analyses first; each declares     entities  guess the inventory
                the entities it needs
      satisfy   build exactly those entities      analyses  link to whatever pass 1 made
      evidence  quotes for the filled values      evidence
      build     merge, then validate              build

`demand-driven` exists because the entity pass cannot know what the contrasts will need.
Asked to guess, it modelled a crossover's condition as a continuous covariate, and a cell
cannot be righter than the term it points at. Letting the analyses declare their terms
first fixed that; the measurements are in docs/extraction-workflow-experiments.md.

Stage 1 -- `review/parse_tables.py` -- is not re-run here. It is the load-bearing input:
without it analysis recall goes to zero. It is versioned on disk and a rerun is explicit.

`tables` is not extracted by a model at all. `table_number`, `caption` and `footer` are
literal strings in the pubget manifest, so retyping them through an LLM can only introduce
error; they are copied, and the analyses pass is told the local_ids.

Every model pass carries a post-condition and retries when it fails -- an empty payload, a
declared entity it did not emit, a design no model term can express. Raise it with
`--max-attempts`; 1 disables it.

    python review/run_extraction.py --pmids papers.pmids --workflow demand-driven \\
        --zero-foci-rule --max-attempts 3 --key-file .env
    python review/run_extraction.py --pmids papers.pmids --stages build
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
from pipeline import kinds as pipeline_kinds  # noqa: E402  (one text-flavour list)

STAGES = ["tables", "entities", "analyses", "demands", "satisfy", "recheck",
          "evidence", "build"]

#: Named stage orderings, so a workflow is a name rather than a remembered set of flags.
#:
#: `entity-first` is the shape `baseline-run.md` settled on: guess the entity inventory,
#: then link analyses to it. `demand-driven` reverses it -- the analyses decide what
#: entities exist and the entity pass is held to that list -- which is what stopped the
#: model being built before anything had said what it must express. See
#: docs/extraction-workflow-experiments.md for the measurements behind the default.
WORKFLOWS = {
    "entity-first": ["tables", "entities", "analyses", "evidence", "build"],
    "demand-driven": ["tables", "demands", "satisfy", "evidence", "build"],
    "demand-driven+recheck": ["tables", "demands", "satisfy", "recheck", "evidence",
                              "build"],
}
DEFAULT_MODEL = "@psyc-aid338-ope-333f18/gpt-5.6-luna"

#: In the order `ls.py:_paper_text` in ns-validate tries them, and that agreement is the
#: point. The record's `source_text_hash` is the sha256 of whichever of these the
#: extraction ran against, and `ls.py export` refuses to stage a text whose hash does not
#: match it. Extracting from the corpus text while the reviewer is shown the built one is
#: not a degradation, it is a hard export failure -- and the built one is the only variant
#: with the coordinate tables in it, so it is also the one worth extracting from.
#:
#: Derived from `pipeline.kinds.TEXT_FLAVOURS` rather than restated. The two lists were
#: written separately and drifted: this one stopped at `local` and `pubget`, so a paper
#: whose only text is `ace` built fine under the pipeline driver and failed here with "no
#: text" -- a corpus-wide rebuild losing papers the extraction had already processed.
TEXT_VARIANTS = tuple(f"processed/{flavour}/{name}"
                      for flavour, name in pipeline_kinds.TEXT_FLAVOURS)


def paper_text(study_dir: Path) -> Path:
    """The text every stage of this run addresses."""

    for relative in TEXT_VARIANTS:
        candidate = study_dir / relative
        if candidate.is_file():
            return candidate
    raise SystemExit(
        f"no text for {study_dir.name} under {study_dir}: tried "
        + ", ".join(TEXT_VARIANTS)
        + ".\nRun review/sync_texts.py, then review/build_text.py to inline the tables."
    )


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

    # Searched over the same flavours as the text, and absent is a legal answer: a paper
    # with no coordinate tables has no manifest, and reading one unconditionally made a
    # single such paper abort the whole shard it was in -- 293 of 300 papers went
    # unrebuilt because one had no `tables.jsonl`.
    manifest = next((study_dir / "processed" / flavour / "tables.jsonl"
                     for flavour, _name in pipeline_kinds.TEXT_FLAVOURS
                     if (study_dir / "processed" / flavour / "tables.jsonl").is_file()),
                    None)
    tables, id_map = [], {}
    if manifest is None:
        return {"tables": tables}, id_map
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
    parser.add_argument("--examples", type=Path, default=REVIEW / "examples",
                        help="where built records are written")
    parser.add_argument("--no-union", dest="union", action="store_false",
                        help="evidence: skip the retriever, quote pass only")
    parser.add_argument("--reranker-device", default="cpu",
                        help="evidence: device for the union retriever, e.g. cuda:0")
    parser.add_argument("--workflow", choices=sorted(WORKFLOWS),
                        help="a named stage ordering; --stages overrides it")
    parser.add_argument("--stages", nargs="*", choices=STAGES,
                        help="explicit stages, overriding --workflow")
    parser.add_argument("--key-file", type=Path, default=REPO / ".env")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default="low")
    # Effort is per stage because the two passes are not the same kind of work: one fills
    # descriptive slots, the other decides which analyses exist and which term carries a
    # sign. Buying reasoning for both when only one needs it is what --effort alone does.
    parser.add_argument("--entities-effort", help="overrides --effort for the entities pass")
    parser.add_argument("--analyses-effort", help="overrides --effort for the analyses pass")
    # Reasoning tokens come out of the same budget as the answer, so a high-effort call at
    # the default ceiling can spend it all thinking and return an empty payload.
    parser.add_argument("--max-out", type=int, default=48_000)
    parser.add_argument("--max-attempts", type=int, default=1,
                        help="retries per pass when its post-condition fails")
    parser.add_argument("--no-stage1", action="store_true",
                        help="withhold the stage-1 analysis listing from the analyses pass")
    parser.add_argument("--table-rows", action="store_true",
                        help="give the analyses pass stage 1's per-analysis detail in full")
    parser.add_argument("--preprocess", default="none",
                        help="deterministic text transform for every model pass; see "
                             "review/preprocess.py --list")
    parser.add_argument("--zero-foci-rule", action="store_true",
                        help="tell the analyses pass that a stage-1 entry with no "
                             "coordinates is a tested effect that found nothing")
    parser.add_argument("--redo", action="store_true", help="rerun stages already written")
    parser.add_argument("--strict", action="store_true",
                        help="pass --strict to build_record, so unresolved quotes and "
                             "builder repairs above their thresholds fail the paper")
    args = parser.parse_args()
    if args.stages is None:
        args.stages = WORKFLOWS[args.workflow] if args.workflow else STAGES
        if args.workflow:
            print(f"workflow {args.workflow}: {' -> '.join(args.stages)}")

    python = sys.executable
    failures = 0
    # Per paper, per stage, so a run over sixteen papers ends with a table rather than one
    # number. `FAILURES: 5` says nothing about which five or where.
    verdicts: dict[str, dict[str, str]] = {}

    for pmid, study, axis in read_pmids(args.pmids):
        print(f"\n=== {study} (pmid {pmid}) — {axis}")
        verdicts[study] = {}
        study_dir = args.texts / study
        text = paper_text(study_dir)
        print(f"  text: {text.relative_to(study_dir)} ({text.stat().st_size:,} bytes)")
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
                  "--max-out", str(args.max_out),
                  "--max-attempts", str(args.max_attempts), "--no-evidence",
                  # Every model pass, so an arm is one preprocessing decision and not a
                  # different decision per stage. `build_record` is not given it: the
                  # record is assembled against the original text.
                  "--preprocess", args.preprocess]

        if "entities" in args.stages:
            if (payload_dir / "entities.json").is_file() and not args.redo:
                print("  entities: already done")
            else:
                failures += bool(run([python, REVIEW / "extract_record.py",
                                      "--mode", "entities", *common,
                                      "--effort", args.entities_effort or args.effort]))

        if "analyses" in args.stages:
            if (payload_dir / "analyses.json").is_file() and not args.redo:
                print("  analyses: already done")
            else:
                stage1_args = [] if args.no_stage1 else ["--stage1", stage1]
                if args.table_rows:
                    stage1_args.append("--stage1-detail")
                if args.zero_foci_rule:
                    stage1_args.append("--zero-foci-rule")
                failures += bool(run([python, REVIEW / "extract_record.py",
                                      "--mode", "analyses", *common,
                                      "--effort", args.analyses_effort or args.effort,
                                      "--entities", payload_dir / "entities.json",
                                      *stage1_args, "--tables", table_map]))

        # Demand-driven ordering: the analyses decide what entities exist, then the entity
        # pass is held to that list. Writes the same two payload files as the entity-first
        # pair, so build_record is unchanged and the two orderings are directly comparable.
        if "demands" in args.stages:
            if (payload_dir / "analyses.json").is_file() and not args.redo:
                print("  demands: already done")
            else:
                stage1_args = [] if args.no_stage1 else ["--stage1", stage1]
                if args.table_rows:
                    stage1_args.append("--stage1-detail")
                if args.zero_foci_rule:
                    stage1_args.append("--zero-foci-rule")
                failures += bool(run([python, REVIEW / "extract_record.py",
                                      "--mode", "demands", *common,
                                      "--effort", args.analyses_effort or args.effort,
                                      *stage1_args, "--tables", table_map]))

        if "satisfy" in args.stages:
            if (payload_dir / "entities.json").is_file() and not args.redo:
                print("  satisfy: already done")
            else:
                failures += bool(run([python, REVIEW / "extract_record.py",
                                      "--mode", "satisfy", *common,
                                      "--effort", args.entities_effort or args.effort,
                                      "--requirements",
                                      payload_dir / "demands" / "requirements.json"]))

        if "recheck" in args.stages:
            failures += bool(run([python, REVIEW / "recheck_cells.py",
                                  "--paper", study, "--text", text,
                                  "--payloads", payload_dir,
                                  "--key-file", args.key_file, "--model", args.model,
                                  "--effort", args.analyses_effort or args.effort]))

        if "evidence" in args.stages:
            failures += bool(run([python, REVIEW / "add_evidence.py",
                                  "--paper", study, "--text", text,
                                  "--payloads", payload_dir,
                                  "--key-file", args.key_file, "--model", args.model,
                                  "--effort", args.effort,
                                  "--reranker-device", args.reranker_device,
                                  *([] if args.union else ["--no-union"]),
                                  *(["--redo"] if args.redo else [])]))

        if "build" in args.stages:
            args.examples.mkdir(parents=True, exist_ok=True)
            record = args.examples / f"{study}.extraction.json"
            built = run([python, REVIEW / "build_record.py",
                         "--paper", study, "--text", text,
                         "--payloads", payload_dir, "--out", record,
                         "--stage1", stage1, "--tables", table_map,
                         "--extractor-model", args.model,
                         "--extraction-date", date.today().isoformat(),
                         *(["--strict"] if args.strict else [])])
            verdicts[study]["build"] = "ok" if built == 0 else "FAILED"
            failures += bool(built)

            # Written even when the build failed: this is the regression corpus, and the
            # payload that produced a bad record is the one worth keeping.
            raw = args.examples / f"{study}.extraction.raw.json"
            if record.is_file() and (args.redo or not raw.is_file()):
                raw.write_text(record.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"  kept untouched model output at {raw.name}")

            # Not run after a failed build. It would validate whatever `--out` last held --
            # a stale record from a previous run -- and report it as this run's result.
            if built != 0:
                verdicts[study]["validate"] = "skipped"
                print("  validate: skipped, the build failed")
            else:
                validated = run([python, REVIEW / "validate_record.py",
                                 "--record", record, "--text", text, "--paper", study])
                verdicts[study]["validate"] = "ok" if validated == 0 else "FAILED"
                failures += bool(validated)

    if verdicts:
        # `validate` is not one of `STAGES` -- it is not selectable and always follows a
        # build -- but it is a column here, because "the record built and then failed
        # validation" is the outcome a reader most needs to see.
        columns = [stage for stage in [*STAGES, "validate"]
                   if any(stage in verdict for verdict in verdicts.values())]
        print("\nper paper:")
        print("  " + "study".ljust(16) + "".join(column.ljust(10) for column in columns))
        for study, verdict in verdicts.items():
            print("  " + study.ljust(16)
                  + "".join(verdict.get(column, "-").ljust(10) for column in columns))

    print(f"\n{'FAILURES: ' + str(failures) if failures else 'all stages clean'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
