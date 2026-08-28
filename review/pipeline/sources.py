"""Bring a paper from another corpus into the layout the pipeline reads.

The pipeline expects a text under `processed/<flavour>/` and a stage-1 document at
`stage1/analyses.json` with a table map beside it. The ns-pond corpus has the text in the
same place and the parse somewhere else in another shape: one JSON object per line in
`processed/<flavour>/analyses.jsonl`, with the coordinates as records rather than as the
`points` the stage-1 document uses.

Staging is a conversion, not a re-parse. Nothing here reads a table or calls a model; it
rewrites what the pond parse already found into the shape stage 1 would have written, and
symlinks the text rather than copying 39,000 papers' worth of it.

The pond corpus is shared and is never written to. Everything this creates lives in the
workspace it is given.
"""

from __future__ import annotations

import json
from pathlib import Path

from .kinds import Paper

#: The pond records a statistic's type as a bare letter. `parse_tables._point_sign` reads
#: the kind to decide whether a value carries a direction at all, and it excludes
#: `p-value` by that name, so the letters have to be spelled out or every p-value would
#: be read as a signed statistic.
STATISTIC_KINDS = {
    "T": "t-statistic", "Z": "z-statistic", "F": "f-statistic",
    "P": "p-value", "R": "correlation", "D": "cohens-d", "B": "beta",
}


def _point(coordinate: dict) -> dict:
    """One pond coordinate record as a stage-1 point."""
    values = []
    magnitude = coordinate.get("statistic_value")
    if isinstance(magnitude, (int, float)):
        letter = str(coordinate.get("statistic_type") or "").strip().upper()
        values.append({"value": magnitude,
                       "kind": STATISTIC_KINDS.get(letter, letter.lower() or "statistic")})
    point: dict = {
        "coordinates": [coordinate.get("x"), coordinate.get("y"), coordinate.get("z")],
        "space": coordinate.get("space"),
    }
    if values:
        point["values"] = values
    for extra in ("cluster_size", "cluster_measure", "is_subpeak", "is_deactivation",
                  "is_seed"):
        if coordinate.get(extra) not in (None, False):
            point[extra] = coordinate[extra]
    return point


def _analysis(entry: dict) -> dict:
    """One pond analysis as a stage-1 analysis."""
    return {
        "name": entry.get("name"),
        "description": entry.get("description"),
        "points": [_point(c) for c in entry.get("coordinates") or []],
        "table_id": entry.get("table_id"),
        "table_number": entry.get("table_number"),
        "table_caption": entry.get("table_caption"),
        "table_footer": entry.get("table_footer"),
    }


def read_pond_parse(study: Path) -> tuple[list[dict], str]:
    """(analyses, flavour) from the first parse this paper has, or ([], "")."""
    for flavour in ("pubget", "ace", "elsevier"):
        path = study / "processed" / flavour / "analyses.jsonl"
        if not path.is_file():
            continue
        entries = [json.loads(line) for line in
                   path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if entries:
            return [_analysis(e) for e in entries], flavour
    return [], ""


def stage(study_id: str, pond_root: Path, workspace: Path) -> tuple[Paper | None, str]:
    """Materialise one pond paper into the workspace, or say why it cannot be.

    Returns the Paper and an empty reason on success. A paper with no parsed analyses is
    refused rather than staged with an empty stage 1: the table parse is the analysis
    inventory, and a run without it produces a record with no contrasts at all, which is
    worse than a paper that visibly did not run.
    """

    source = pond_root / study_id
    if not source.is_dir():
        return None, f"{study_id}: not in the corpus"

    analyses, flavour = read_pond_parse(source)
    if not analyses:
        return None, f"{study_id}: no parsed analyses in any flavour"

    target = workspace / study_id
    (target / "stage1").mkdir(parents=True, exist_ok=True)
    link = target / "processed"
    if not link.exists():
        # Symlinked, never copied: the corpus is 27GB and read-only to this pipeline.
        link.symlink_to(source / "processed")

    (target / "stage1" / "analyses.json").write_text(
        json.dumps({"study": study_id, "source": f"ns-pond/{flavour}",
                    "analyses": analyses}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")
    # The pond already names its tables with the ids the extraction pass will use, so the
    # map is an identity over the tables that actually carry an analysis.
    tables = {a["table_id"]: a["table_id"] for a in analyses if a.get("table_id")}
    (target / "stage1" / "table-map.json").write_text(
        json.dumps(tables, indent=1) + "\n", encoding="utf-8")

    paper = Paper(study_id, workspace)
    ready, why = paper.is_ready()
    return (paper, "") if ready else (None, why)


def stage_all(study_ids: list[str], pond_root: Path,
              workspace: Path) -> tuple[list[Paper], list[str]]:
    staged, refused = [], []
    for study_id in study_ids:
        paper, why = stage(study_id, pond_root, workspace)
        (staged.append(paper) if paper else refused.append(why))
    return staged, refused
