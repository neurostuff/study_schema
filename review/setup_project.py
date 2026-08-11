#!/usr/bin/env python3
"""Create and populate the Label Studio projects for extraction review.

Three projects:

  review-evidence   attribute tasks that carry a value and evidence spans
  review-reference  cross-reference (local_id) tasks, which have no evidence
  adjudication      third pass, seeded later by adjudicate.py from disagreements

The first two get maximum_annotations=2 so two curators independently review each
attribute. That field exists on the open-source Project model
(projects/models.py:259) but is not exposed in the community settings UI, so it
has to be set over the API -- which is exactly what this script is for.

Data Manager views are created per project so reviewers can work a paper at a
time and start with the priority-0 fields. Cross-reviewer visibility comes from
the built-in "Annotated by" column (data_manager/functions.py:153), which is on
by default in the Explore view.

Uses only the standard library, matching the repo's dependency-free scripts.

Usage:
    export LABEL_STUDIO_URL=http://localhost:8080
    export LABEL_STUDIO_API_KEY=<your token from Account & Settings>
    python review/setup_project.py --tasks-dir review/ls_tasks --config-dir review/ls_config
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

import config_gen  # noqa: E402

DEFAULT_URL = "http://localhost:8080"

#: Import in chunks: one paper is ~600 evidence tasks and a single request
#: carrying every paper at once is slow and hard to diagnose when it fails.
_CHUNK = 250


class LabelStudio:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, method: str, path: str, payload: Any = None) -> Any:
        url = f"{self.base_url}{path}"
        body = None
        headers = {"Authorization": f"Token {self.token}"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:600]
            raise SystemExit(f"{method} {path} failed: HTTP {error.code}\n{detail}") from error
        except urllib.error.URLError as error:
            raise SystemExit(
                f"cannot reach Label Studio at {self.base_url}: {error.reason}\n"
                "Is the container up, and is LABEL_STUDIO_URL correct?"
            ) from error
        return json.loads(raw) if raw else None

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, payload: Any) -> Any:
        return self._request("POST", path, payload)

    def patch(self, path: str, payload: Any) -> Any:
        return self._request("PATCH", path, payload)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # -- higher level ------------------------------------------------------

    def find_project(self, title: str) -> dict[str, Any] | None:
        page = self.get("/api/projects?page_size=1000")
        results = page.get("results", page) if isinstance(page, dict) else page
        for project in results or []:
            if project.get("title") == title:
                return project
        return None

    def take_answers(self, project_id: int, snapshot: Path) -> list[dict[str, Any]]:
        """Lift every annotation and draft out of a project, then reset its summary.

        A config change is refused while answers reference Repeater-generated
        control names: Label Studio validates the *unexpanded* config, where the
        control is literally `ttype_{{gdx}}_{{tdx}}`, and the regex that would
        match `ttype_0_0` only engages when the index flag appears in `toName` --
        which it cannot here, since one `<Text name="paper">` is shared by every
        row. So the answers have to come out for the push to go through.

        Two rules, both learned the hard way. **Everything is written to
        `snapshot` before the first delete**, so a crash between the delete and
        the restore leaves the work on disk instead of destroying it -- Label
        Studio hard-deletes annotations and `core_deletedrow` is not populated for
        them, so there is no recovering from memory. And a single failing delete
        is reported and stepped over rather than raised, because aborting halfway
        strands every answer already removed.
        """

        saved: list[dict[str, Any]] = []
        page = self.get(f"/api/tasks?project={project_id}&page_size=1000")
        stubs = page.get("tasks") or page.get("results") or []

        # Every task is fetched, with no stub-based shortcut. The stub cannot be
        # trusted for drafts: it exposes `drafts` as a list that comes back EMPTY
        # even when the task detail carries one, and there is no `total_drafts` at
        # all. Filtering on either left draft-only tasks untouched, so they were
        # never snapshotted or cleared and the config push stayed blocked with
        # nothing to show why. Slow on a 609-task project, but this only runs when
        # a push is already blocked.
        for stub in stubs:
            task = self.get(f"/api/tasks/{stub['id']}")
            for annotation in task.get("annotations") or []:
                saved.append(
                    {
                        "task": task["id"],
                        "review_key": (task.get("data") or {}).get("review_key", ""),
                        "kind": "annotation",
                        "annotation_id": annotation["id"],
                        "completed_by": (
                            annotation["completed_by"].get("id")
                            if isinstance(annotation.get("completed_by"), dict)
                            else annotation.get("completed_by")
                        ),
                        "result": annotation.get("result") or [],
                        "was_cancelled": annotation.get("was_cancelled", False),
                    }
                )
            for draft in task.get("drafts") or []:
                saved.append(
                    {
                        "task": task["id"],
                        "review_key": (task.get("data") or {}).get("review_key", ""),
                        "kind": "draft",
                        "result": draft.get("result") or [],
                        "draft_id": draft["id"],
                    }
                )

        if saved:
            # On disk before anything is destroyed. Losing the snapshot means
            # losing the work, and Label Studio hard-deletes annotations.
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(json.dumps(saved, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"  --force: saved {len(saved)} answer(s) to {snapshot} before touching them")

            for item in saved:
                if item["kind"] == "annotation":
                    self._try_delete(f"/api/annotations/{item['annotation_id']}/")
                else:
                    # Trailing slash: the route is `api/drafts/<int:pk>/`
                    # (tasks/urls.py:33-44). Without it this 404s.
                    self._try_delete(f"/api/drafts/{item['draft_id']}/")

        # Always, even when nothing was found. `created_labels_drafts` keeps
        # counting control names from answers that are already gone, and that stale
        # count is itself enough to refuse the config change.
        self.post(f"/api/projects/{project_id}/summary/reset/", {})
        return saved

    def _try_delete(self, path: str) -> None:
        """Delete, reporting rather than raising: an abort strands the rest."""

        try:
            self.delete(path)
        except SystemExit as error:
            print(f"  could not delete {path}: {error}")

    def give_answers_back(self, saved: list[dict[str, Any]]) -> int:
        """Re-create the answers taken out for the config push.

        Creating an annotation does not run the config-compatibility check that
        blocked the change, so answers that were legal before the push are legal
        after it. Anything a config change genuinely invalidated is caught later
        by sync_tasks, which compares content hashes and flags the task as needing
        re-review rather than silently keeping a stale answer.
        """

        restored = 0
        for item in saved:
            payload = {"result": item["result"]}
            try:
                if item["kind"] == "annotation":
                    payload["was_cancelled"] = item.get("was_cancelled", False)
                    # Keep the author. Without this the API defaults to the
                    # requesting user (tasks/api.py:819-820) and an AI reviewer's
                    # annotations silently come back as the curator's, which
                    # destroys the very attribution overlap depends on.
                    if item.get("completed_by") is not None:
                        payload["completed_by"] = item["completed_by"]
                    self.post(f"/api/tasks/{item['task']}/annotations/", payload)
                else:
                    # No trailing slash: the route is `<int:pk>/drafts`
                    # (tasks/urls.py:18), and `/drafts/` 404s.
                    self.post(f"/api/tasks/{item['task']}/drafts", payload)
                restored += 1
            except SystemExit as error:
                # The answers are already out of the project by this point, so a
                # failure here must not abort the run and strand the rest. Report
                # the payload so it can be re-entered by hand.
                print(f"  could not restore {item['kind']} on task {item['task']}: {error}")
                print(f"    {json.dumps(item['result'])[:400]}")
        return restored

    def ensure_project(
        self,
        title: str,
        description: str,
        label_config: str,
        maximum_annotations: int,
        force: bool = False,
    ) -> dict[str, Any]:
        existing = self.find_project(title)
        if existing:
            # maximum_annotations and the config are the two things worth keeping
            # in sync on re-runs; tasks are left alone.
            body = {
                "label_config": label_config,
                "maximum_annotations": maximum_annotations,
                "description": description,
            }
            try:
                self.patch(f"/api/projects/{existing['id']}", body)
            except SystemExit:
                if not force:
                    raise
                # Never overwrite a previous snapshot. A fixed filename protected
                # against a crash inside one run and then destroyed that protection
                # on the next -- which is how an earlier snapshot holding five
                # answers was replaced by a later one holding a draft.
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                snapshot = Path("review/backup") / f"{title}-{stamp}.json"
                saved = self.take_answers(existing["id"], snapshot)
                self.patch(f"/api/projects/{existing['id']}", body)
                restored = self.give_answers_back(saved)
                print(
                    f"  --force: moved {restored} of {len(saved)} answer(s) aside and "
                    f"put them back (snapshot: {snapshot})"
                )
                if restored < len(saved):
                    print(f"  WARNING: {len(saved) - restored} not restored; they are in {snapshot}")
            print(f"  updated existing project {title!r} (id={existing['id']})")
            return self.get(f"/api/projects/{existing['id']}")

        project = self.post(
            "/api/projects",
            {
                "title": title,
                "description": description,
                "label_config": label_config,
                "maximum_annotations": maximum_annotations,
                # Show the whole queue to everyone rather than hiding submitted
                # work: seeing each other's coverage is a requirement here.
                "show_skip_button": True,
                "enable_empty_annotation": False,
                "show_annotation_history": True,
            },
        )
        print(f"  created project {title!r} (id={project['id']})")
        return project

    def ensure_local_storage(self, project_id: int, path: str) -> None:
        """Register the staged-text directory as a local files import storage.

        This is required, not optional. Setting LOCAL_FILES_SERVING_ENABLED and
        LOCAL_FILES_DOCUMENT_ROOT is NOT sufficient: the serving view filters on
        LocalFilesImportStorage rows whose `path` prefixes the requested file's
        directory, and returns 404 when none match
        (io_storages/localfiles/views.py:104-119). Without this call every
        `/data/local-files/?d=...` request 404s and reviewers see an empty pane.

        The storage is deliberately never synced. A sync would walk the directory
        and import each .txt as a new task; we only need the row to exist so the
        endpoint will serve, and so the project's members inherit access.
        """

        existing = self.get(f"/api/storages/localfiles?project={project_id}") or []
        for storage in existing if isinstance(existing, list) else []:
            if storage.get("path") == path:
                print(f"  local files storage already registered for {path}")
                return

        self.post(
            "/api/storages/localfiles/",
            {
                "project": project_id,
                "path": path,
                "title": "staged paper texts",
                "use_blob_urls": False,
            },
        )
        print(f"  registered local files storage at {path} (not synced)")

    def import_tasks(self, project_id: int, tasks: list[dict[str, Any]]) -> int:
        imported = 0
        for start in range(0, len(tasks), _CHUNK):
            chunk = tasks[start : start + _CHUNK]
            result = self.post(f"/api/projects/{project_id}/import", chunk)
            imported += (result or {}).get("task_count", len(chunk))
        return imported

    def create_view(
        self,
        project_id: int,
        title: str,
        filters: list[dict[str, Any]],
        ordering: list[str],
    ) -> None:
        data: dict[str, Any] = {"title": title, "ordering": ordering}
        if filters:
            data["filters"] = {"conjunction": "and", "items": filters}
        self.post("/api/dm/views", {"project": project_id, "data": data})
        print(f"    view {title!r}")


#: The Data Manager's filter operators, as its store declares them
#: (`dm/src/stores/Filters/*`, the union that `store.js:490` parses a saved view
#: against). There is no "one of a set" operator for a string column: `in` is a
#: numeric or date RANGE, taking {min, max}. To select several values of a string
#: column, filter on the one that matters with `equal` / `not_equal`.
_DM_OPERATORS = frozenset({
    "equal", "not_equal", "less", "greater", "less_or_equal", "greater_or_equal",
    "in", "not_in", "empty", "contains", "not_contains", "regex",
})


def _filter(key: str, operator: str, value: Any, type_name: str) -> dict[str, Any]:
    """Build one Data Manager filter. Column format per serializers.py:156.

    The operator is checked here because the server does not check it at all: a
    view with an unknown operator is stored with HTTP 201 and only fails in the
    browser, where mobx-state-tree cannot match it against the union and throws
    during Data Manager init. That happens before any view is selected, so ONE bad
    filter in ONE view makes the whole project's Data Manager hang on the loading
    animation, with nothing in the server log to say why. `in_list` shipped that
    way and took `ns-review-structure` down.
    """

    if operator not in _DM_OPERATORS:
        raise ValueError(
            f"{operator!r} is not a Data Manager operator, and a view carrying it "
            f"would be accepted by the server and then break the project's Data "
            f"Manager in the browser. Use one of: {', '.join(sorted(_DM_OPERATORS))}"
        )
    return {
        "filter": f"filter:tasks:data.{key}",
        "operator": operator,
        "type": type_name,
        "value": value,
    }


def papers_in(tasks: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for task in tasks:
        paper = task.get("data", {}).get("paper_id")
        if isinstance(paper, str):
            seen.setdefault(paper, None)
    return list(seen)


def load_tasks(tasks_dir: Path, suffix: str) -> list[dict[str, Any]]:
    """Load and concatenate task files, keeping paper order stable.

    Import order sets task id order, which sets the labeling-stream order. Sorting
    by filename then keeping each file's internal order means a reviewer walks one
    paper's attributes consecutively -- the closest thing to task grouping that
    the open-source edition offers.
    """

    tasks: list[dict[str, Any]] = []
    for path in sorted(tasks_dir.glob(f"*.{suffix}.json")):
        tasks.extend(json.loads(path.read_text(encoding="utf-8")))
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-dir", required=True, type=Path)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--url", default=os.environ.get("LABEL_STUDIO_URL", DEFAULT_URL))
    parser.add_argument("--token", default=os.environ.get("LABEL_STUDIO_API_KEY", ""))
    parser.add_argument("--reviewers", type=int, default=2, help="annotations per task")
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="if a config change is refused because answers reference its controls, "
        "delete those answers and retry (see Client.clear_answers)",
    )
    parser.add_argument(
        "--only",
        action="append",
        metavar="KIND",
        help="restrict to these families (repeatable), e.g. --only contrast. Pairs with "
        "--force: a config push is refused whenever the project holds answers, so "
        "forcing every family to change one family's stylesheet moves answers that "
        "had no reason to move",
    )
    parser.add_argument(
        "--reimport",
        action="store_true",
        help="import even when the project already has tasks (duplicates them)",
    )
    parser.add_argument(
        "--storage-path",
        default="/label-studio/files/texts",
        help="path to the staged texts as seen INSIDE the Label Studio container",
    )
    args = parser.parse_args()

    if not args.token:
        print(
            "no API token: set LABEL_STUDIO_API_KEY or pass --token.\n"
            "Find it in Label Studio under Account & Settings > Access Token.",
            file=sys.stderr,
        )
        return 2

    client = LabelStudio(args.url, args.token)

    # One project per task kind, because a project holds one labeling config
    # (projects/models.py:198) and one `maximum_annotations` (:259). Papers are
    # grouped by Data Manager view inside each, never by project.
    #
    # Overlap differs on purpose. The value family is ~95% of the volume and one
    # reviewer plus a priority-0 second pass is the right spend; the structure
    # family is 7-13 tasks per paper and is where a second opinion is informative.
    families = [
        (
            "value",
            "ns-review-value",
            "One task per entity instance: every populated field, its evidence "
            "excerpt, and a span layer labelled by field.",
            1,
        ),
        (
            "relationship",
            "ns-review-relationship",
            "One task per association slot per paper. Rows are source objects, "
            "columns the candidate targets.",
            1,
        ),
        (
            "structure",
            "ns-review-structure",
            "Per-class instance inventories and one task per ModelEstimation. Two task "
            "kinds in one config, selected by which gate key the task carries.",
            args.reviewers,
        ),
        (
            "contrast",
            "ns-review-contrast",
            "One task per coordinate table (is this the right set of analyses?) and one "
            "per Analysis (does the record say what the paper says?), both over the "
            "rendered table the analyses were read off.",
            args.reviewers,
        ),
    ]

    if args.only:
        wanted = set(args.only)
        unknown = wanted - {kind for kind, _, _, _ in families} - {t for _, t, _, _ in families}
        if unknown:
            print(f"unknown --only value(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        families = [f for f in families if f[0] in wanted or f[1] in wanted]

    plan = []
    all_tasks: list[dict[str, Any]] = []
    for kind, title, description, maximum in families:
        config_path = args.config_dir / f"{kind}.xml"
        if not config_path.is_file():
            print(
                f"missing config {config_path}. Run:\n"
                f"  python review/config_gen.py --out-dir {args.config_dir}",
                file=sys.stderr,
            )
            return 2
        tasks = load_tasks(args.tasks_dir, f"tasks_{kind}")
        all_tasks.extend(tasks)
        plan.append(
            (title, description, config_path.read_text(encoding="utf-8"), maximum, tasks)
        )

    papers = papers_in(all_tasks)

    print(f"Label Studio at {args.url}")
    print(
        "  "
        + ", ".join(f"{len(tasks)} {kind}" for (kind, *_), (*_, tasks) in zip(families, plan))
        + f" tasks over {len(papers)} paper(s)"
    )

    for title, description, config, maximum, tasks in plan:
        print(f"\n{title}")
        project = client.ensure_project(title, description, config, maximum, args.force)
        project_id = project["id"]

        confirmed = client.get(f"/api/projects/{project_id}").get("maximum_annotations")
        print(f"  maximum_annotations = {confirmed}")
        if confirmed != maximum:
            print(f"  WARNING: expected {maximum}; overlap will not behave as intended")

        # Must happen for every project that renders paper text, including the
        # adjudication project, or its text pane 404s.
        client.ensure_local_storage(project_id, args.storage_path)

        if tasks and not args.skip_import:
            # Importing is not idempotent: Label Studio has no upsert, so a second
            # run would double every task. Skip unless explicitly asked.
            already = client.get(f"/api/projects/{project_id}").get("task_number") or 0
            if already and not args.reimport:
                print(
                    f"  {already} tasks already present, skipping import "
                    f"(pass --reimport to add {len(tasks)} more)"
                )
            else:
                count = client.import_tasks(project_id, tasks)
                print(f"  imported {count} tasks")

        if not tasks:
            continue

        existing_views = client.get(f"/api/dm/views?project={project_id}") or []
        if len(existing_views) > 1:
            print(f"  {len(existing_views)} views already exist, not recreating")
            continue

        print("  views:")
        # Triage axes differ per family because the task data does. Each entry is
        # (title, filters, columns) and the columns must name keys the exporter
        # populates -- config_gen.FILTER_KEYS is the list.
        # Coordinate-bearing is the DEFAULT, not a tab. Every working view
        # carries `coordinate_status = yes`, and each project gets one view for
        # the remainder so nothing is hidden -- an object no reported result
        # rests on is deprioritised, never dropped.
        #
        # Split on `yes` rather than enumerating the negatives. The Data Manager
        # has no "one of a set" operator for a string column -- `in` means a
        # numeric/date RANGE -- and an unknown operator is not ignored: it fails
        # the store's union type at load and takes the whole Data Manager down
        # before any view is chosen. Naming only the positive value also keeps
        # this from going stale as the negative vocabulary grows.
        bearing = _filter("coordinate_status", "equal", "yes", "String")
        unrelated = _filter("coordinate_status", "not_equal", "yes", "String")

        triage: dict[str, list[tuple[str, list[dict[str, Any]], list[str]]]] = {
            "ns-review-value": [
                (
                    "Priority 0 first",
                    [bearing, _filter("priority", "equal", 0, "Number")],
                    ["tasks:data.paper_id", "tasks:data.entity_class", "tasks:data.local_id"],
                ),
                (
                    "Value with no evidence found",
                    [bearing, _filter("evidence_status", "equal", "not_found", "String")],
                    ["tasks:data.paper_id", "tasks:data.entity_class", "tasks:data.field_path"],
                ),
                (
                    "Marked not reported",
                    [bearing, _filter("llm_status", "equal", "not_reported", "String")],
                    ["tasks:data.paper_id", "tasks:data.entity_class", "tasks:data.field_path"],
                ),
                (
                    "No reported result rests on this",
                    [unrelated],
                    ["tasks:data.paper_id", "tasks:data.entity_class", "tasks:data.field_path"],
                ),
            ],
            "ns-review-relationship": [
                # Unfiltered, and first, so the project opens on its whole queue.
                # This replaced a "Flagged anomalies first" tab on `anomaly_count
                # > 0`, which was a permanently empty tab: measured against the
                # live project, that filter pair matches 0 of 18 tasks, because
                # every relationship task across all three papers exports
                # `anomaly_count = 0`.
                #
                # It was empty because what the exporter counts there is not a
                # triage signal. A `required` slot with no link, and a link to a
                # `local_id` that was never extracted, are both claims that the
                # RECORD is malformed -- a reviewer cannot act on either. They
                # belong in validate_record.py, which already catches the
                # missing-required case (:111) but has no dangling-reference
                # check.
                #
                # What made it look like a *populated* tab is a separate trap
                # worth remembering: the live view had only the
                # `coordinate_status` half, because it was created before the
                # `anomaly_count` filter was added to this dict and the
                # `len(existing_views) > 1` guard below means views are created
                # once per project and never refreshed. So it matched all 18
                # tasks under a heading asserting every one was anomalous. Any
                # edit to `triage` needs the views deleted to take effect.
                (
                    "All tasks",
                    [],
                    ["tasks:data.paper_id", "tasks:data.rel_slot"],
                ),
                (
                    "No reported result rests on this",
                    [unrelated],
                    ["tasks:data.paper_id", "tasks:data.rel_slot"],
                ),
            ],
            # Import order already walks inventory -> models -> contrasts per
            # paper; these views are for picking one kind out across papers.
            "ns-review-structure": [
                (
                    f"{kind.capitalize()} tasks",
                    [bearing, _filter("task_kind", "equal", kind, "String")],
                    ["tasks:data.paper_id", "tasks:data.local_id", "tasks:data.cell_count"],
                )
                # Read from config_gen rather than restated: this is the same map the
                # config gates on and the exporter writes into data.task_kind, and a
                # view for a kind that no longer exists is silently empty.
                for kind in config_gen.GATES["structure"]
            ]
            + [
                (
                    "No reported result rests on this",
                    [unrelated],
                    [
                        "tasks:data.paper_id",
                        "tasks:data.task_kind",
                        "tasks:data.coordinate_status",
                    ],
                ),
            ],
            # One table and everything drawn from it, which is the axis this project
            # exists for: the segmentation task and the contrasts that depend on it are
            # answered together or not at all.
            "ns-review-contrast": [
                (
                    f"{kind.capitalize()} tasks",
                    [bearing, _filter("task_kind", "equal", kind, "String")],
                    [
                        "tasks:data.paper_id",
                        "tasks:data.table_id",
                        "tasks:data.local_id",
                        "tasks:data.cell_count",
                    ],
                )
                for kind in config_gen.GATES["contrast"]
            ]
            + [
                (
                    "By table",
                    [bearing],
                    [
                        "tasks:data.paper_id",
                        "tasks:data.table_id",
                        "tasks:data.task_kind",
                        "tasks:data.local_id",
                    ],
                ),
            ],
        }

        for view_title, filters, columns in triage.get(title, []):
            client.create_view(project_id, view_title, filters, columns)

        for paper in papers:
            client.create_view(
                project_id,
                f"Paper {paper}",
                [_filter("paper_id", "equal", paper, "String")],
                ["tasks:data.review_key"],
            )

    print(
        "\nNext: add a second user under Organization, then confirm the Data Manager\n"
        "shows the 'Annotated by' column so reviewers can see each other's coverage."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
