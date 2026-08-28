"""Run papers through the stages, and say exactly what happened.

The driver's whole job is sequencing and accounting. It knows nothing about prompts,
schemas or retrieval; it knows that a stage may be skipped because its artefact exists,
that a paper whose stage failed should not continue to the next stage, and that the run
is worth reporting as an object rather than as the text a stage happened to print.

    python -m pipeline.driver --pmids papers.pmids --texts data/texts \\
        --payloads runs/x/payloads --records runs/x/records --key-file .env

    python -m pipeline.driver ... --explain      # what would run, and why, no calls
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.kinds import (FAILED, NOT_REQUESTED, Paper, PaperOutcome,  # noqa: E402
                            RunReport, StageOutcome)
from pipeline.stages import BASELINE, Settings, Stage  # noqa: E402


def read_pmids(path: Path) -> list[str]:
    """The study ids, from the tab-separated pmids file the corpus uses."""
    studies = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.startswith("#"):
            studies.append(parts[1])
    return studies


def run_paper(paper: Paper, settings: Settings,
              stages: tuple[Stage, ...] = BASELINE) -> PaperOutcome:
    """One paper through every stage, stopping at the first that fails.

    Stopping matters: `satisfy` reads what `demands` wrote, and running it against a
    missing requirements file produces a second, more confusing failure that hides the
    first. The stages that never ran are recorded as such rather than omitted, so the
    report shows the shape of the whole intended run.
    """

    outcome = PaperOutcome(paper.study_id)
    ready, why = paper.is_ready()
    if not ready:
        outcome.stages.append(StageOutcome("input", paper.study_id, FAILED, error=why))
        return outcome

    stopped = False
    for stage in stages:
        if stopped:
            outcome.stages.append(
                StageOutcome(stage.name, paper.study_id, NOT_REQUESTED,
                             notes=["an earlier stage failed"]))
            continue
        result = stage.run(paper, settings)
        outcome.stages.append(result)
        if not result.ok:
            stopped = True
    outcome.record_path = settings.record_path(paper)
    return outcome


def plan(papers: list[Paper], settings: Settings,
         stages: tuple[Stage, ...] = BASELINE) -> str:
    """What would run and what would be skipped, without spending anything.

    The question this answers -- "why did that stage not run" -- previously required
    starting the run and reading the log.
    """

    lines = []
    for paper in papers:
        ready, why = paper.is_ready()
        if not ready:
            lines.append(f"{paper.study_id}: CANNOT RUN -- {why}")
            continue
        marks = []
        for stage in stages:
            done = stage.is_done(paper, settings) and not settings.redo
            marks.append(f"{stage.name}={'skip' if done else 'run'}")
        lines.append(f"{paper.study_id} [{paper.flavour}]  " + "  ".join(marks))
    return "\n".join(lines)


def run(papers: list[Paper], settings: Settings,
        stages: tuple[Stage, ...] = BASELINE, workers: int = 1) -> RunReport:
    """Every paper, optionally several at once.

    Papers are independent -- each writes only under its own payload directory -- so the
    parallelism is across papers and never within one. A paper is a handful of sequential
    API calls, so the wall clock is latency and threads are the right shape for it.
    """

    report = RunReport()
    if workers <= 1:
        report.papers = [run_paper(paper, settings, stages) for paper in papers]
        return report
    with ThreadPoolExecutor(max_workers=workers) as pool:
        report.papers = list(pool.map(lambda p: run_paper(p, settings, stages), papers))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pmids", required=True, type=Path)
    parser.add_argument("--texts", required=True, type=Path)
    parser.add_argument("--payloads", required=True, type=Path)
    parser.add_argument("--records", required=True, type=Path)
    parser.add_argument("--key-file", type=Path, default=Path(".env"))
    parser.add_argument("--model", default="@psyc-aid338-ope-333f18/gpt-5.6-luna")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--reranker-device", default="cpu",
                        help="comma-separated devices, cycled per paper: cuda:0,cuda:1")
    parser.add_argument("--no-union", dest="union", action="store_false")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--redo", action="store_true")
    parser.add_argument("--explain", action="store_true",
                        help="print what would run and why, and make no calls")
    args = parser.parse_args()

    settings = Settings(payloads=args.payloads, records=args.records,
                        key_file=args.key_file, model=args.model, effort=args.effort,
                        max_attempts=args.max_attempts,
                        reranker_device=args.reranker_device.split(",")[0],
                        reranker_devices=tuple(d.strip() for d in
                                               args.reranker_device.split(",") if d.strip()),
                        union=args.union, redo=args.redo)
    papers = [Paper(study, args.texts) for study in read_pmids(args.pmids)]

    if args.explain:
        print(plan(papers, settings))
        return 0

    report = run(papers, settings, BASELINE, workers=args.workers)
    print(report.explain())
    return 1 if report.failures() else 0


if __name__ == "__main__":
    raise SystemExit(main())
