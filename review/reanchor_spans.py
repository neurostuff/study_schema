#!/usr/bin/env python3
"""Move human-drawn spans onto the text currently served, by re-finding their quotes.

A `<Text>` region is stored as `{start, end, text}` -- integer offsets into the
staged paper. Restage that paper with anything inserted ahead of a span and the
offsets are wrong while the entry still looks well formed: Label Studio highlights
whatever now sits at those numbers. Inlining the coordinate tables moved every
offset in every paper by a few dozen characters, so 44 of 45 spans a reviewer had
drawn came to point at neighbouring prose and one at a table.

Nothing else repairs them. `sync_tasks.replace_predictions` rewrites *predictions*
from the export, but an annotation is the reviewer's own work and no export
contains it. `prune_orphan_answers` prunes by `from_name`, and the control is still
declared -- only its offsets rotted. So this is the third repair tool, and the one
that runs after a text change.

The quote is the durable part of the entry and the offsets are derived, so this
re-derives them: `spans.resolve(served, entry.text, near=entry.start)`. The `near`
bias matters -- a short quote ("general linear model", a region name, a coordinate)
occurs many times, and the old offset is a good prior for which one was meant. An
entry whose quote no longer occurs at all is reported and left exactly as it is;
dropping a reviewer's work to fix its address would be the worse error.

Drafts are re-anchored too, unlike in `prune_orphan_answers`, and deliberately.
That tool leaves drafts alone because an orphaned entry clears itself when the
reviewer next saves. A stale offset does not: saving promotes it into an
annotation. The self-healing argument does not transfer.

Dry run by default. With --apply it snapshots every annotation and draft it is
about to rewrite, under review/backup/, before the first PATCH.

Usage:
    export LABEL_STUDIO_URL=http://localhost:8080
    export LABEL_STUDIO_API_KEY=<token>
    python review/reanchor_spans.py --files-root review/ls_files
    python review/reanchor_spans.py --files-root review/ls_files --apply
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

import spans as span_tools  # noqa: E402

DEFAULT_URL = "http://localhost:8080"

#: Every project whose config carries a `<Text>` object tag, so every project that
#: can hold a drawn span. Matches setup_project.py.
PROJECTS = (
    "ns-review-value",
    "ns-review-relationship",
    "ns-review-structure",
    "ns-review-contrast",
    "ns-adjudication",
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
                f"{error.read()[:400].decode('utf-8', 'replace')}"
            ) from error

    def projects(self) -> dict[str, dict[str, Any]]:
        page = self.call("/api/projects?page_size=1000")
        return {p["title"]: p for p in (page.get("results") or page)}

    def task_ids(self, project_id: int) -> list[int]:
        """Paged, stopping on the reported total.

        Asking for the page after the last one is an HTTP 404 ("Invalid page"),
        not an empty list, so a loop that waits for an empty batch dies on a
        project whose task count is an exact multiple of the page size -- and on
        every project once `total` is reached. `sync_tasks.tasks` stops the same
        way for the same reason.
        """

        out: list[int] = []
        page = 1
        while True:
            body = self.call(f"/api/tasks?project={project_id}&page={page}&page_size=200")
            batch = body.get("tasks") or body.get("results") or []
            if not batch:
                return out
            out += [t["id"] for t in batch]
            if len(out) >= (body.get("total") or body.get("count") or len(out)):
                return out
            page += 1


def load_texts(files_root: Path) -> dict[str, str]:
    """paper_id -> the exact bytes Label Studio serves for it.

    Read with newline="" for the same reason `to_labelstudio.stage_text` writes
    with it: universal-newline translation would shorten the document and every
    offset computed here would be wrong by the number of line endings before it.
    """

    texts = {}
    for path in sorted((files_root / "texts").glob("*.txt")):
        with path.open(encoding="utf-8", newline="") as stream:
            texts[path.stem] = stream.read()
    return texts


def anchored_entries(result: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The result entries that carry integer offsets into the paper.

    Keyed on the offsets rather than on `type == "labels"`: what makes an entry
    repairable is that it addresses the text by number, and any control that does
    so rots the same way.
    """

    out = []
    for entry in result:
        value = entry.get("value") or {}
        if isinstance(value.get("start"), int) and isinstance(value.get("end"), int):
            if isinstance(value.get("text"), str) and value["text"].strip():
                out.append(entry)
    return out


def reanchor(result: list[dict[str, Any]], text: str) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Return (new result, per-move descriptions, per-failure descriptions).

    The input is never mutated: a failure part-way through must not leave an
    annotation half-moved, since the half already written would be indistinguishable
    from a span the reviewer drew there.
    """

    updated = json.loads(json.dumps(result))
    moves: list[str] = []
    failures: list[str] = []

    for entry in anchored_entries(updated):
        value = entry["value"]
        quote, start, end = value["text"], value["start"], value["end"]
        if text[start:end] == quote:
            continue
        if end - start != len(quote) and text.count(quote) != 1:
            # Label Studio stores `text` as the slice `[start, end)`, so their
            # lengths always agree in an entry it wrote. One where they disagree was
            # already broken before the text moved, which means the old offset is
            # not the prior `near` needs it to be.
            #
            # Only refused when the quote is also ambiguous, because that is the
            # only case where anything is being guessed. A draft here holds
            # `scanner` -- 12 occurrences -- against a 38-character range, and
            # resolving it picked one 13,000 characters away; another holds a
            # 128-character sentence that occurs exactly once, where there is no
            # choice to get wrong and the reviewer's work is simply recoverable.
            failures.append(
                f"{entry.get('from_name')} [{start}:{end}] spans {end - start} characters "
                f"but quotes {len(quote)}, and that quote occurs {text.count(quote)} times: "
                "the entry was already inconsistent and its position cannot be inferred"
            )
            continue
        try:
            found = span_tools.resolve(text, quote, near=start)
        except span_tools.SpanResolutionError as error:
            failures.append(f"{entry.get('from_name')} [{start}:{end}] {error}")
            continue
        value["start"], value["end"] = found.start_char, found.end_char
        # The tolerant matcher can land on text that differs from the quote in
        # whitespace or punctuation. Store what the document actually says, or the
        # entry goes on failing `offsets_hold` for a new reason.
        note = ""
        if found.text != quote:
            value["text"] = found.text
            note = "  (quote rewritten to match the document)"
        moves.append(
            f"{entry.get('from_name')} {start}:{end} -> "
            f"{found.start_char}:{found.end_char}{note}"
        )
    return updated, moves, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files-root", required=True, type=Path)
    parser.add_argument("--backup-dir", type=Path, default=Path("review/backup"))
    parser.add_argument("--url", default=os.environ.get("LABEL_STUDIO_URL", DEFAULT_URL))
    parser.add_argument("--token", default=os.environ.get("LABEL_STUDIO_API_KEY", ""))
    parser.add_argument("--apply", action="store_true", help="write; without it this is a dry run")
    parser.add_argument("--verbose", action="store_true", help="print every span that moves")
    args = parser.parse_args()

    if not args.token:
        print("set LABEL_STUDIO_API_KEY or pass --token", file=sys.stderr)
        return 2

    texts = load_texts(args.files_root)
    if not texts:
        print(f"no staged texts under {args.files_root / 'texts'}", file=sys.stderr)
        return 2

    client = Client(args.url, args.token)
    live = client.projects()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"Label Studio at {args.url}{'' if args.apply else ' (dry run)'}")
    print(f"{len(texts)} staged text(s): {', '.join(sorted(texts))}\n")

    moved = failed = 0
    unknown_papers: set[str] = set()

    for title in PROJECTS:
        project = live.get(title)
        if project is None:
            continue

        # (endpoint, id, new result, description) -- collected before any write so
        # that a dry run and an apply walk exactly the same list.
        pending: list[tuple[str, int, list[dict[str, Any]], str]] = []
        snapshot: list[dict[str, Any]] = []
        project_failures: list[str] = []
        checked = 0

        for task_id in client.task_ids(project["id"]):
            task = client.call(f"/api/tasks/{task_id}")
            paper = (task.get("data") or {}).get("paper_id")
            text = texts.get(paper)
            if text is None:
                if paper:
                    unknown_papers.add(paper)
                continue

            # A draft is PATCHed at /api/drafts/<id>/, not /api/annotation-drafts/;
            # the wrong route answers 404 with an HTML page, which reads as a
            # missing record rather than a missing endpoint.
            for kind, endpoint in (("annotations", "annotations"), ("drafts", "drafts")):
                for item in task.get(kind) or []:
                    result = item.get("result") or []
                    checked += len(anchored_entries(result))
                    new_result, moves, failures = reanchor(result, text)
                    project_failures += [f"task {task_id}: {f}" for f in failures]
                    if not moves:
                        continue
                    label = f"{kind[:-1]} {item['id']} (task {task_id})"
                    pending.append((endpoint, item["id"], new_result, label))
                    snapshot.append(item)
                    print(f"  {label}: {len(moves)} span(s) move")
                    if args.verbose:
                        for move in moves:
                            print(f"      {move}")

        print(f"\n{title}: {checked} anchored span(s), "
              f"{len(pending)} annotation(s)/draft(s) to rewrite")
        for failure in project_failures:
            print(f"  UNRESOLVED, left as it is: {failure}")
        moved += len(pending)
        failed += len(project_failures)

        if pending and args.apply:
            args.backup_dir.mkdir(parents=True, exist_ok=True)
            path = args.backup_dir / f"{title}-reanchor-{stamp}.json"
            path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
            print(f"  saved {len(snapshot)} item(s) to {path} before rewriting")
            for endpoint, item_id, new_result, label in pending:
                # Trailing slash: without it the PATCH redirects and the body is
                # dropped, which reads as a silent no-op.
                client.call(f"/api/{endpoint}/{item_id}/", "PATCH", {"result": new_result})
            print(f"  rewrote {len(pending)} item(s)")
        print()

    if unknown_papers:
        print(f"no staged text for paper(s): {', '.join(sorted(unknown_papers))}; skipped")
    print(f"{moved} annotation(s)/draft(s) {'rewritten' if args.apply else 'would be rewritten'}, "
          f"{failed} span(s) could not be re-found")
    if not args.apply and moved:
        print("nothing written; re-run with --apply")
    # A span whose quote vanished is a real finding, not a warning to scroll past.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
