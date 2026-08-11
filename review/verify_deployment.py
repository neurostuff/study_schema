#!/usr/bin/env python3
"""Check that a running Label Studio deployment will actually work for review.

Written because the failure that matters most is silent from the UI's point of
view: if `/data/local-files/` 404s, every task still opens, the form still
renders, and the paper pane is simply blank. This asserts the whole chain instead.

Checks, in order of how badly each one bites:

  1. the text URL serves 200 and is byte-identical to the staged file
  2. every stored prediction offset addresses the text the server serves
  3. maximum_annotations is really 2, so two reviewers get each task
  4. the imported prediction count matches the exported task files
  5. registering the local files storage did not sync phantom tasks

Usage:
    export LABEL_STUDIO_URL=http://localhost:8080
    export LABEL_STUDIO_API_KEY=<token>
    python review/verify_deployment.py --tasks-dir review/ls_tasks --files-root review/ls_files
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_tasks import ours  # noqa: E402

DEFAULT_URL = "http://localhost:8080"
#: Derived from TASK_FAMILIES so this cannot drift from the exporter. Overlap
#: differs per family, so the expected maximum_annotations comes with it.
REVIEW_PROJECTS = {
    "ns-review-value": 1,
    "ns-review-relationship": 1,
    "ns-review-structure": 2,
    "ns-review-contrast": 2,
}


class Checker:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.failures: list[str] = []
        self.notes: list[str] = []

    def _open(self, path: str) -> tuple[int, bytes]:
        request = urllib.request.Request(
            f"{self.base_url}{path}", headers={"Authorization": f"Token {self.token}"}
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except urllib.error.URLError as error:
            raise SystemExit(
                f"cannot reach Label Studio at {self.base_url}: {error.reason}\n"
                "Is the container running, and is LABEL_STUDIO_URL correct?"
            ) from error

    def get(self, path: str) -> Any:
        status, raw = self._open(path)
        if status != 200:
            raise SystemExit(f"GET {path} -> HTTP {status}: {raw[:300].decode('utf-8', 'replace')}")
        return json.loads(raw)

    def check(self, condition: bool, message: str) -> bool:
        if condition:
            print(f"  ok    {message}")
        else:
            print(f"  FAIL  {message}")
            self.failures.append(message)
        return condition

    # -- individual checks -------------------------------------------------

    def text_serves(self, paper_id: str, staged: Path) -> bytes | None:
        url = f"/data/local-files/?d=texts/{paper_id}.txt"
        status, body = self._open(url)

        if not self.check(status == 200, f"{url} serves HTTP 200 (got {status})"):
            if status == 404:
                self.notes.append(
                    "A 404 here almost always means no LocalFilesImportStorage is registered for "
                    "the project. LOCAL_FILES_SERVING_ENABLED alone is not enough -- the serving "
                    "view filters on storage rows (io_storages/localfiles/views.py:104). "
                    "Re-run setup_project.py, which registers it."
                )
            return None

        expected = staged.read_bytes()
        self.check(
            body == expected,
            f"served bytes are identical to {staged} ({len(body)} vs {len(expected)})",
        )
        return body

    def offsets_hold(self, project_id: int, served: dict[str, bytes], sample: int) -> None:
        """Every stored offset must address the text served for *its own* paper.

        Resolved per paper rather than against one document: with several papers in
        a project, checking every span against the first paper's text reports
        mismatches that are an artefact of the check.

        Annotations and drafts are sampled alongside predictions, and that is the
        point of the check rather than a completeness flourish. A prediction with
        stale offsets is rewritten from the export by the next sync; a span a
        reviewer drew exists nowhere but the database, so nothing restores it and
        nothing else notices. When restaging the papers with the coordinate tables
        inline moved every offset, this checked predictions only -- it reported the
        rot in the half that repairs itself and stayed silent about the half that
        does not. `reanchor_spans.py` is what fixes what this now finds.
        """

        texts = {paper: body.decode("utf-8") for paper, body in served.items()}
        checked = mismatched = incoherent = 0
        examples: list[str] = []

        for task_id in self._task_ids(project_id, sample):
            task = self.get(f"/api/tasks/{task_id}")
            paper = (task.get("data") or {}).get("paper_id")
            text = texts.get(paper)
            if text is None:
                continue
            sources = [
                ("prediction", task.get("predictions") or []),
                ("annotation", task.get("annotations") or []),
                ("draft", task.get("drafts") or []),
            ]
            for kind, items in sources:
                for item in items:
                    for result in item.get("result") or []:
                        value = result.get("value") or {}
                        if "start" not in value:
                            continue
                        checked += 1
                        if text[value["start"] : value["end"]] == value.get("text"):
                            continue
                        # An entry whose range and quote disagree in length was
                        # never a valid address, so re-anchoring cannot repair it
                        # and neither can a resync. Counted apart, or a single
                        # malformed draft fails this check forever and trains
                        # everyone to ignore it.
                        quote = value.get("text")
                        if isinstance(quote, str) and value["end"] - value["start"] != len(quote):
                            incoherent += 1
                            continue
                        mismatched += 1
                        if len(examples) < 3:
                            examples.append(
                                f"{kind} {paper} [{value['start']}:{value['end']}] "
                                f"served={text[value['start']:value['end']]!r} "
                                f"stored={quote!r}"
                            )

        if checked == 0:
            self.notes.append(f"project {project_id} ships no spans; nothing to check")
            return
        self.check(
            mismatched == 0,
            f"all {checked} sampled spans address the served text ({mismatched} mismatched)",
        )
        for example in examples:
            self.notes.append(f"offset mismatch: {example}")
        if incoherent:
            self.notes.append(
                f"project {project_id}: {incoherent} entr(ies) whose range and quote disagree "
                "in length; never a valid address, not re-anchorable, left as they are"
            )

    def _task_ids(self, project_id: int, limit: int) -> list[int]:
        page = self.get(f"/api/tasks?project={project_id}&page_size={limit}")
        tasks = page.get("tasks") or page.get("results") or []
        return [task["id"] for task in tasks]

    def project_settings(self, project: dict[str, Any], expected_overlap: int) -> None:
        title = project["title"]
        self.check(
            project.get("maximum_annotations") == expected_overlap,
            f"{title}: maximum_annotations is {expected_overlap} "
            f"(got {project.get('maximum_annotations')})",
        )

    def prediction_count(self, project: dict[str, Any], expected: int, sample: int) -> None:
        """Count only the predictions this pipeline produced.

        `total_predictions_number` counts every producer's rows. This Label Studio
        has an `ns-chat` feature that attaches an empty prediction to every task, so
        the project counter read 609 against 388 exported and the check failed on
        someone else's data. Ours are identified by `model_version`, which is
        exactly what that field is for -- by `sync_tasks.ours`, so that the rule
        this audits by is the same one `replace_predictions` deletes by. When the
        two disagreed, this check was the only thing that noticed.
        """

        mine = foreign = 0
        for task_id in self._task_ids(project["id"], sample):
            for prediction in self.get(f"/api/tasks/{task_id}").get("predictions") or []:
                if ours(prediction.get("model_version")):
                    mine += 1
                else:
                    foreign += 1
        if foreign:
            self.notes.append(
                f"{project['title']}: {foreign} prediction(s) from another producer "
                "in the sample, not counted"
            )
        # Sampled, so the assertion is a bound rather than an equality.
        self.check(
            mine <= expected,
            f"{project['title']}: {mine} of our predictions in a {sample}-task sample, "
            f"within the {expected} exported",
        )

    def task_count(self, project: dict[str, Any], expected: int) -> None:
        actual = project.get("task_number")
        self.check(
            actual == expected,
            f"{project['title']}: {expected} tasks, no phantom imports (got {actual})",
        )


#: Exported task-file suffix -> project title. One entry per reviewed family;
#: adjudication is seeded separately and has no exported file.
TASK_FAMILIES = (
    ("tasks_value", "ns-review-value"),
    ("tasks_relationship", "ns-review-relationship"),
    ("tasks_structure", "ns-review-structure"),
    ("tasks_contrast", "ns-review-contrast"),
)


def local_expectations(tasks_dir: Path) -> dict[str, dict[str, int]]:
    """Count tasks and predictions per project kind from the exported files."""

    counts: dict[str, dict[str, int]] = {}
    for kind, project in TASK_FAMILIES:
        tasks = []
        for path in sorted(tasks_dir.glob(f"*.{kind}.json")):
            tasks.extend(json.loads(path.read_text(encoding="utf-8")))
        counts[project] = {
            "tasks": len(tasks),
            "predictions": sum(len(task.get("predictions", [])) for task in tasks),
        }
    return counts


def papers(tasks_dir: Path) -> list[str]:
    found: dict[str, None] = {}
    for kind, _ in TASK_FAMILIES:
        for path in sorted(tasks_dir.glob(f"*.{kind}.json")):
            for task in json.loads(path.read_text(encoding="utf-8")):
                paper = task.get("data", {}).get("paper_id")
                if paper:
                    found.setdefault(paper, None)
    return list(found)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-dir", required=True, type=Path)
    parser.add_argument("--files-root", required=True, type=Path)
    parser.add_argument("--url", default=os.environ.get("LABEL_STUDIO_URL", DEFAULT_URL))
    parser.add_argument("--token", default=os.environ.get("LABEL_STUDIO_API_KEY", ""))
    parser.add_argument("--sample", type=int, default=60, help="tasks to sample for offset checks")
    args = parser.parse_args()

    if not args.token:
        print("set LABEL_STUDIO_API_KEY or pass --token")
        return 2

    checker = Checker(args.url, args.token)
    expectations = local_expectations(args.tasks_dir)

    print(f"Label Studio at {args.url}")
    page = checker.get("/api/projects?page_size=1000")
    projects = {p["title"]: p for p in (page.get("results") or page)}

    print("\ntext serving")
    served: dict[str, bytes] = {}
    paper_ids = papers(args.tasks_dir)
    for paper_id in paper_ids:
        staged = args.files_root / "texts" / f"{paper_id}.txt"
        if not staged.is_file():
            checker.check(False, f"staged text missing: {staged}")
            continue
        body = checker.text_serves(paper_id, staged)
        if body is not None:
            served[paper_id] = body

    for title, overlap in REVIEW_PROJECTS.items():
        project = projects.get(title)
        print(f"\n{title}")
        if not checker.check(project is not None, f"{title} exists"):
            continue
        checker.project_settings(project, overlap)
        checker.task_count(project, expectations[title]["tasks"])
        checker.prediction_count(project, expectations[title]["predictions"], args.sample)

    # Every project that ships spans, not just the value one. The structure
    # project used to carry only choice predictions and was skipped; it now
    # pre-highlights the evidence for each instance and term, so a shifted offset
    # there would have gone unnoticed.
    if served:
        print("\nstored offsets")
        for title in REVIEW_PROJECTS:
            if title in projects:
                checker.offsets_hold(projects[title]["id"], served, args.sample)

    print()
    for note in checker.notes:
        print(f"note: {note}")
    if checker.failures:
        print(f"\n{len(checker.failures)} check(s) failed")
        return 1
    print("deployment looks correct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
