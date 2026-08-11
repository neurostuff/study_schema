#!/usr/bin/env python3
"""Report how reviewers changed the extractor's evidence spans.

Predictions and annotations are separate rows in Label Studio: the extractor's
spans live in `Prediction.result` and stay there untouched when a reviewer deletes
a highlight, because deleting acts on the copy in `Annotation.result`. So the
original is always recoverable and the change is a set difference.

Each span is classified against the prediction it overlaps:

  kept      same start and end
  adjusted  overlaps a predicted span but with different bounds -- the reviewer
            agreed about the passage and disagreed about where it begins or ends
  added     no overlap with any predicted span
  removed   a predicted span with no overlapping annotated span

`adjusted` is separated from `added`/`removed` on purpose: a boundary nudge and a
different sentence are different findings about the extractor, and lumping them
reports one as the other.

Usage:
    export LABEL_STUDIO_URL=http://localhost:8080
    export LABEL_STUDIO_API_KEY=<token>
    python review/evidence_diff.py --project ns-review-value
    python review/evidence_diff.py --project ns-review-value --out review/evidence-diff.json
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://localhost:8080"


def get(base: str, token: str, path: str) -> Any:
    request = urllib.request.Request(
        f"{base}{path}", headers={"Authorization": f"Token {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise SystemExit(f"GET {path} -> HTTP {error.code}: {error.read()[:300]!r}") from error


def spans(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "start": r["value"]["start"],
            "end": r["value"]["end"],
            "text": r["value"].get("text", ""),
            "labels": r["value"].get("labels", []),
        }
        for r in results
        if r.get("type") == "labels" and "start" in (r.get("value") or {})
    ]


def overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def diff(predicted: list[dict[str, Any]], annotated: list[dict[str, Any]]) -> dict[str, list]:
    out: dict[str, list] = {"kept": [], "adjusted": [], "added": [], "removed": []}
    matched: set[int] = set()

    for span in annotated:
        exact = next(
            (
                i
                for i, p in enumerate(predicted)
                if i not in matched and p["start"] == span["start"] and p["end"] == span["end"]
            ),
            None,
        )
        if exact is not None:
            matched.add(exact)
            out["kept"].append(span)
            continue
        near = next(
            (i for i, p in enumerate(predicted) if i not in matched and overlaps(p, span)),
            None,
        )
        if near is not None:
            matched.add(near)
            out["adjusted"].append({"from": predicted[near], "to": span})
        else:
            out["added"].append(span)

    out["removed"] = [p for i, p in enumerate(predicted) if i not in matched]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="ns-review-value")
    parser.add_argument("--url", default=os.environ.get("LABEL_STUDIO_URL", DEFAULT_URL))
    parser.add_argument("--token", default=os.environ.get("LABEL_STUDIO_API_KEY", ""))
    parser.add_argument("--out", type=Path, help="write the per-task diff as JSON")
    parser.add_argument("--show", type=int, default=12, help="how many changes to print")
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("no API token: set LABEL_STUDIO_API_KEY or pass --token")

    page = get(args.url, args.token, "/api/projects?page_size=1000")
    projects = {p["title"]: p for p in (page.get("results") or page)}
    if args.project not in projects:
        raise SystemExit(f"no project titled {args.project!r}")
    project_id = projects[args.project]["id"]

    listing = get(args.url, args.token, f"/api/tasks?project={project_id}&page_size=1000")
    stubs = listing.get("tasks") or listing.get("results") or []

    totals = Counter()
    reviewed = 0
    records = []
    for stub in stubs:
        if not stub.get("total_annotations"):
            continue
        task = get(args.url, args.token, f"/api/tasks/{stub['id']}")
        predicted = spans([r for p in task.get("predictions") or [] for r in p["result"]])
        annotated = spans([r for a in task.get("annotations") or [] for r in a["result"]])
        changes = diff(predicted, annotated)
        reviewed += 1
        for kind, items in changes.items():
            totals[kind] += len(items)
        if any(changes[k] for k in ("adjusted", "added", "removed")):
            records.append(
                {
                    "task": task["id"],
                    "review_key": task["data"]["review_key"],
                    "verdict": next(
                        (
                            r["value"]["choices"][0]
                            for a in task.get("annotations") or []
                            for r in a["result"]
                            if r.get("from_name") == "verdict"
                        ),
                        None,
                    ),
                    **changes,
                }
            )

    print(f"{args.project}: {reviewed} reviewed task(s)")
    for kind in ("kept", "adjusted", "added", "removed"):
        print(f"  {kind:9s} {totals[kind]:4d}")
    changed = len(records)
    print(f"  {changed} task(s) where the evidence changed")

    for record in records[: args.show]:
        print(f"\n{record['review_key']}   verdict={record['verdict']}")
        for span in record["removed"]:
            print(f"   - removed  [{span['start']}:{span['end']}] {span['text'][:70]!r}")
        for span in record["added"]:
            print(f"   + added    [{span['start']}:{span['end']}] {span['text'][:70]!r}")
        for change in record["adjusted"]:
            print(
                f"   ~ adjusted [{change['from']['start']}:{change['from']['end']}]"
                f" -> [{change['to']['start']}:{change['to']['end']}]"
                f" {change['to']['text'][:52]!r}"
            )
    if changed > args.show:
        print(f"\n... {changed - args.show} more; pass --out to write them all")

    if args.out:
        args.out.write_text(json.dumps(records, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
