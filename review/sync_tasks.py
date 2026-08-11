#!/usr/bin/env python3
"""Sync exported tasks into a project in place, preserving annotations.

Importing is not idempotent and deleting tasks destroys their annotations, so
neither is usable once review has started. This implements the regeneration
protocol from staged-validation.md instead, matching on the two keys every task
carries:

  review_key    the ADDRESS  -- paper|family|class|local_id
  content_hash  WHAT WAS ASKED -- a digest of the answer-bearing payload

    address same, hash same        -> nothing to do
    address same, hash changed     -> PATCH the data; flag if already annotated,
                                      because the question moved under the answer
    address gone                   -> report as orphaned; never deleted here
    address new                    -> import

The hash deliberately excludes descriptors and rendered prose, so a cosmetic
change to how a task reads does not invalidate an answer to it. That is what
makes this safe to run after a stage-0 correction: only tasks whose substance
moved get re-asked.

Usage:
    export LABEL_STUDIO_URL=http://localhost:8080
    export LABEL_STUDIO_API_KEY=<token>
    python review/sync_tasks.py --tasks-dir review/ls_tasks            # dry run
    python review/sync_tasks.py --tasks-dir review/ls_tasks --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://localhost:8080"

#: Exported suffix -> project title, matching setup_project.py.
FAMILIES = (
    ("tasks_value", "ns-review-value"),
    ("tasks_relationship", "ns-review-relationship"),
    ("tasks_structure", "ns-review-structure"),
    ("tasks_contrast", "ns-review-contrast"),
)


def ours(model_version: str | None) -> bool:
    """Was this prediction produced by the extraction pipeline?

    Both exporters stamp `f"{extractor_model}@{extractor_version}"`, and no other
    producer on this instance uses an `@` -- `chat_backend` writes
    `"ns-chat <model> effort=<n>"`. `verify_deployment.prediction_count` has
    always audited by this rule; it is defined here, next to the code that
    *deletes* by it, and imported there so the two cannot drift apart.

    Identity deliberately ignores which model did the extracting. Matching the
    exported `model_version` exactly -- what this used to do -- meant that
    re-running the extractor under a different model made every live prediction
    unrecognisable: the stale rows were kept as "another producer's", the new
    ones written beside them, and each task ended up with two sets of spans at
    two different sets of offsets. `verify_deployment.offsets_hold` then failed
    on the older set with no indication of where the duplicates came from.
    """

    return "@" in (model_version or "")


class Client:
    def __init__(self, base_url: str, token: str) -> None:
        self.base = base_url.rstrip("/")
        self.token = token

    def call(self, path: str, method: str = "GET", body: Any = None) -> Any:
        request = urllib.request.Request(
            f"{self.base}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raise SystemExit(
                f"{method} {path} -> HTTP {error.code}: {error.read()[:400].decode('utf-8', 'replace')}"
            ) from error

    def projects(self) -> dict[str, dict[str, Any]]:
        page = self.call("/api/projects?page_size=1000")
        return {p["title"]: p for p in (page.get("results") or page)}

    def replace_predictions(self, task_id: int, task: dict[str, Any]) -> int:
        """Swap a task's predictions for the exported ones.

        Predictions are the pre-filled state a reviewer edits, so they have to be
        replaceable independently of the task data -- adding a pre-selected radio
        changes what is shown without changing what is asked. Existing ones are
        deleted first because there is no upsert.
        """

        current = self.call(f"/api/predictions?task={task_id}") or []
        if isinstance(current, dict):
            current = current.get("results") or []
        for prediction in current:
            # Only our own. Another producer's predictions are not ours to remove --
            # this Label Studio's `ns-chat` feature keeps one on every task.
            if not ours(prediction.get("model_version")):
                continue
            self.call(f"/api/predictions/{prediction['id']}/", "DELETE")
        written = 0
        for prediction in task.get("predictions") or []:
            self.call(
                "/api/predictions",
                "POST",
                {
                    "task": task_id,
                    "model_version": prediction.get("model_version"),
                    "result": prediction["result"],
                },
            )
            written += 1
        return written

    def tasks(self, project_id: int) -> list[dict[str, Any]]:
        """Every task with its data and annotation count, paged."""

        out: list[dict[str, Any]] = []
        page = 1
        while True:
            body = self.call(f"/api/tasks?project={project_id}&page={page}&page_size=200")
            batch = body.get("tasks") or body.get("results") or []
            if not batch:
                break
            out.extend(batch)
            if len(out) >= (body.get("total") or body.get("count") or len(out)):
                break
            page += 1
        return out


def load(tasks_dir: Path, suffix: str) -> dict[str, dict[str, Any]]:
    """Exported tasks by review_key, refusing duplicates."""

    by_key: dict[str, dict[str, Any]] = {}
    for path in sorted(tasks_dir.glob(f"*.{suffix}.json")):
        for task in json.loads(path.read_text(encoding="utf-8")):
            key = task["data"]["review_key"]
            if key in by_key:
                raise SystemExit(f"duplicate review_key across exports: {key}")
            by_key[key] = task
    return by_key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-dir", required=True, type=Path)
    parser.add_argument("--url", default=os.environ.get("LABEL_STUDIO_URL", DEFAULT_URL))
    parser.add_argument("--token", default=os.environ.get("LABEL_STUDIO_API_KEY", ""))
    parser.add_argument(
        "--apply", action="store_true", help="write changes; without it this is a dry run"
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="delete live tasks the exporter no longer produces; skips any that "
        "carry annotations unless --prune-with-answers is also given",
    )
    parser.add_argument("--prune-with-answers", action="store_true")
    args = parser.parse_args()

    if not args.token:
        print("no API token: set LABEL_STUDIO_API_KEY or pass --token", file=sys.stderr)
        return 2

    client = Client(args.url, args.token)
    projects = client.projects()
    verb = "" if args.apply else " (dry run)"
    print(f"Label Studio at {args.url}{verb}")
    exit_code = 0

    for suffix, title in FAMILIES:
        project = projects.get(title)
        exported = load(args.tasks_dir, suffix)
        print(f"\n{title}: {len(exported)} exported")
        if project is None:
            print("  project missing; run setup_project.py first")
            exit_code = 1
            continue

        live = client.tasks(project["id"])
        by_key: dict[str, dict[str, Any]] = {}
        for task in live:
            key = (task.get("data") or {}).get("review_key")
            if key:
                by_key.setdefault(key, task)

        unchanged = refreshed = updated = added = repredicted = pruned = 0
        stale_answers: list[str] = []
        orphaned: list[str] = []

        for key, task in exported.items():
            existing = by_key.get(key)
            if existing is None:
                added += 1
                if args.apply:
                    client.call(f"/api/projects/{project['id']}/import", "POST", [task])
                continue

            current = existing.get("data") or {}
            if current == task["data"]:
                unchanged += 1
                continue

            # Two ways the data can differ, and they need opposite treatment.
            # A changed hash means the question moved, so an existing answer is
            # stale. An unchanged hash with different data is display-only -- a
            # better paraphrase, a new descriptor -- and the answer still stands.
            # This is the whole point of hashing the answer-bearing payload
            # rather than the rendered task.
            if current.get("content_hash") == task["data"]["content_hash"]:
                refreshed += 1
            else:
                updated += 1
                detail = client.call(f"/api/tasks/{existing['id']}")
                answers = len(detail.get("annotations") or []) + len(detail.get("drafts") or [])
                if answers:
                    stale_answers.append(f"{key} ({answers} answer(s))")
            if args.apply:
                client.call(f"/api/tasks/{existing['id']}", "PATCH", {"data": task["data"]})
                repredicted += client.replace_predictions(existing["id"], task)

        # Predictions can change without the data changing at all -- adding a
        # pre-selected radio alters what the reviewer sees, not what is asked --
        # so they are reconciled independently of the data comparison above.
        for key, task in exported.items():
            existing = by_key.get(key)
            if existing is None or existing.get("data") != task["data"]:
                continue
            # Compare like with like, and only our own rows. Two bugs lived here:
            # exported *results* were compared against the live *object* count, so
            # every task looked mismatched and had its predictions rewritten every
            # run; and other producers' predictions were counted as ours -- this
            # Label Studio has an `ns-chat` feature that adds an empty prediction to
            # every task, which is where a 388-vs-609 discrepancy came from.
            wanted = len(task.get("predictions") or [])
            live_preds = client.call(f"/api/predictions?task={existing['id']}") or []
            if isinstance(live_preds, dict):
                live_preds = live_preds.get("results") or []
            mine = [p for p in live_preds if ours(p.get("model_version"))]
            if wanted != len(mine) and args.apply:
                repredicted += client.replace_predictions(existing["id"], task)

        for key, task in by_key.items():
            if key in exported:
                continue

            # The task detail, never the stub. `total_annotations` under-reports and
            # the stub's `drafts` comes back empty even when the task carries one,
            # so a guard built on either would delete answered tasks believing they
            # were empty. Deleting a task destroys its answers with it, so this
            # refuses whenever it cannot prove the task is empty.
            detail = client.call(f"/api/tasks/{task['id']}")
            answers = len(detail.get("annotations") or []) + len(detail.get("drafts") or [])
            orphaned.append(f"{key} ({answers} answer(s))")
            if not args.prune:
                continue
            if answers and not args.prune_with_answers:
                print(f"  refusing to prune {key}: it has {answers} answer(s)")
                continue
            # --apply gates every write, prune included: a dry run never deletes.
            if args.apply:
                client.call(f"/api/tasks/{task['id']}", "DELETE")
            pruned += 1

        print(
            f"  unchanged {unchanged}, display refreshed {refreshed} (answers kept), "
            f"question changed {updated}, imported {added}, predictions rewritten {repredicted}"
        )
        if stale_answers:
            print(
                f"  {len(stale_answers)} updated task(s) already had answers -- the question "
                "moved, so these need re-review:"
            )
            for note in stale_answers[:10]:
                print(f"    - {note}")
        if orphaned:
            verb = ("pruned" if args.apply else "would prune") if args.prune else "left in place"
            print(f"  {len(orphaned)} live task(s) are no longer exported ({verb}):")
            for note in orphaned[:10]:
                print(f"    - {note}")

    if not args.apply:
        print("\nnothing written; re-run with --apply")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
