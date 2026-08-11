#!/usr/bin/env python3
"""Drop result entries whose control the config no longer declares.

Removing a control from a config does not touch the answers already given to it.
Label Studio keeps the old entry in `Annotation.result` and simply stops rendering
it, so an annotation goes on asserting `relationship_verdict = links_correct` after
that question has been deleted. Nothing surfaces it: the editor shows the current
form, the API returns the stale entry, and a decoder that reads by `from_name`
skips it silently -- which means the record quietly disagrees with itself.

What counts as orphaned is derived from the config, never from a list of names.
`expand_repeaters` mirrors `core/Tree.tsx` against each task's own data, so the
valid control set is exactly what that task would render -- including the
`lm_0`/`inv_verdict_0` names that only exist after expansion, and excluding the
gated blocks that task does not carry. A hardcoded list would need editing every
time a config changes, which is the moment it would be forgotten.

Dry run by default. With --apply it snapshots every annotation it is about to
rewrite, under review/backup/, before the first PATCH.

Usage:
    export LABEL_STUDIO_URL=http://localhost:8080
    export LABEL_STUDIO_API_KEY=<token>
    python review/prune_orphan_answers.py --config-dir review/ls_config
    python review/prune_orphan_answers.py --config-dir review/ls_config --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_label_config  # noqa: E402

DEFAULT_URL = "http://localhost:8080"

#: Config file stem -> project title, matching setup_project.py.
PROJECTS = (
    ("value", "ns-review-value"),
    ("relationship", "ns-review-relationship"),
    ("structure", "ns-review-structure"),
    ("contrast", "ns-review-contrast"),
    ("adjudication", "ns-adjudication"),
)


class Client:
    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
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
                f"{method} {path} -> HTTP {error.code}: "
                f"{error.read()[:300].decode('utf-8', 'replace')}"
            ) from error

    def projects(self) -> dict[str, dict[str, Any]]:
        page = self.call("/api/projects?page_size=1000")
        return {p["title"]: p for p in (page.get("results") or page)}

    def tasks(self, project_id: int) -> list[int]:
        page = self.call(f"/api/tasks?project={project_id}&page_size=1000")
        return [t["id"] for t in (page.get("tasks") or page.get("results") or [])]


def declared_controls(config: ElementTree.Element, data: dict[str, Any]) -> set[str]:
    """Every `from_name` this task would render, after Repeater expansion."""

    expanded = check_label_config.expand_repeaters(config, data)
    return {node.get("name") for node in expanded.iter() if node.get("name")}


def orphans(result: list[dict[str, Any]], declared: set[str]) -> list[str]:
    """The from_names in this result that the task no longer declares."""

    return sorted(
        {
            entry.get("from_name")
            for entry in result
            if entry.get("from_name") and entry.get("from_name") not in declared
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--backup-dir", type=Path, default=Path("review/backup"))
    parser.add_argument("--url", default=os.environ.get("LABEL_STUDIO_URL", DEFAULT_URL))
    parser.add_argument("--token", default=os.environ.get("LABEL_STUDIO_API_KEY", ""))
    parser.add_argument("--apply", action="store_true", help="write; without it this is a dry run")
    args = parser.parse_args()

    if not args.token:
        print("set LABEL_STUDIO_API_KEY or pass --token", file=sys.stderr)
        return 2

    client = Client(args.url, args.token)
    live = client.projects()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"Label Studio at {args.url}{'' if args.apply else ' (dry run)'}")

    total = 0
    for stem, title in PROJECTS:
        project = live.get(title)
        config_path = args.config_dir / f"{stem}.xml"
        if project is None or not config_path.is_file():
            continue
        config = ElementTree.fromstring(config_path.read_text(encoding="utf-8"))

        print(f"\n{title}")
        pending: list[tuple[dict[str, Any], list[dict[str, Any]], list[str]]] = []
        stale_drafts: list[str] = []

        for task_id in client.tasks(project["id"]):
            task = client.call(f"/api/tasks/{task_id}")
            declared = declared_controls(config, task.get("data") or {})

            for annotation in task.get("annotations") or []:
                result = annotation.get("result") or []
                gone = orphans(result, declared)
                if not gone:
                    continue
                kept = [e for e in result if e.get("from_name") in declared]
                pending.append((annotation, kept, gone))
                print(
                    f"  annotation {annotation['id']} (task {task_id}): "
                    f"drops {', '.join(gone)} -- {len(result)} -> {len(kept)} entries"
                )

            for draft in task.get("drafts") or []:
                if orphans(draft.get("result") or [], declared):
                    stale_drafts.append(f"draft {draft['id']} (task {task_id})")

        if stale_drafts:
            # Left alone deliberately: a draft is rewritten wholesale the next
            # time the reviewer saves, so the stale entry clears itself, and
            # editing someone's in-progress work to fix a cosmetic
            # inconsistency is a worse trade than waiting.
            print(f"  {len(stale_drafts)} draft(s) also carry orphans, left alone: "
                  f"{', '.join(stale_drafts[:5])}")

        if not pending:
            print("  nothing to prune")
            continue

        if args.apply:
            args.backup_dir.mkdir(parents=True, exist_ok=True)
            snapshot = args.backup_dir / f"{title}-orphans-{stamp}.json"
            snapshot.write_text(
                json.dumps([annotation for annotation, _, _ in pending], indent=2),
                encoding="utf-8",
            )
            print(f"  saved {len(pending)} annotation(s) to {snapshot} before rewriting")
            for annotation, kept, _ in pending:
                # Trailing slash: /api/annotations/{id} without it redirects and
                # the PATCH body is dropped.
                client.call(f"/api/annotations/{annotation['id']}/", "PATCH", {"result": kept})
        total += len(pending)

    print(f"\n{total} annotation(s) {'rewritten' if args.apply else 'would be rewritten'}")
    if not args.apply and total:
        print("nothing written; re-run with --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
