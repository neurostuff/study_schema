#!/usr/bin/env python3
"""One entry point for the Label Studio review layer.

    python review/ls.py config      write the labeling configs
    python review/ls.py lint        check them, offline and against a server
    python review/ls.py export      turn extraction records into tasks
    python review/ls.py deploy      create the projects and import the tasks
    python review/ls.py sync        reconcile live tasks with a fresh export
    python review/ls.py verify      prove the deployment actually works
    python review/ls.py decode      read reviewer answers back out
    python review/ls.py chat        run the ML backend that answers questions

Every path has a default that points where this repo keeps things, so the common
case takes no arguments at all. `--url` and `--token` fall back to
LABEL_STUDIO_URL and LABEL_STUDIO_API_KEY.

This was seven scripts with seven copies of the argument parsing, six of the HTTP
client and six of the project list. The commands are thin because the work is in
the libraries: `config`, `tasks`, `answers`, `lsapi`, and the registry in `spec`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))

import answers as answers_module  # noqa: E402
import config as config_module  # noqa: E402
import lint  # noqa: E402
import lsapi  # noqa: E402
import spec  # noqa: E402
import staging  # noqa: E402
import tasks as tasks_module  # noqa: E402
import text_index  # noqa: E402

REVIEW = Path(__file__).resolve().parent
CONFIG_DIR = REVIEW / "ls_config"
TASKS_DIR = REVIEW / "ls_tasks"
FILES_ROOT = REVIEW / "ls_files"
TEXTS_ROOT = REVIEW / "texts"
EXAMPLES = REVIEW / "examples"
BACKUP = REVIEW / "backup"

#: Where the staged texts are as seen INSIDE the Label Studio container.
STORAGE_PATH = "/label-studio/files/texts"


def _projects(only: list[str] | None) -> list[spec.Project]:
    if not only:
        return list(spec.PROJECTS)
    wanted = set(only)
    unknown = wanted - {p.name for p in spec.PROJECTS} - {p.title for p in spec.PROJECTS}
    if unknown:
        raise SystemExit(f"unknown project(s): {', '.join(sorted(unknown))}")
    return [p for p in spec.PROJECTS if p.name in wanted or p.title in wanted]


def _load_tasks(tasks_dir: Path, project: spec.Project) -> list[dict[str, Any]]:
    """This project's exported tasks, in filename then file order.

    Import order sets task id order, which sets the labeling-stream order, so a
    reviewer walks one paper's tasks consecutively -- the closest thing to task
    grouping the open-source edition offers, and what makes the shared text URL a
    cache hit rather than a refetch.
    """

    found: list[dict[str, Any]] = []
    for path in sorted(tasks_dir.glob(f"*.{project.task_suffix}.json")):
        found.extend(json.loads(path.read_text(encoding="utf-8")))
    return found


def _by_key(tasks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for task in tasks:
        key = (task.get("data") or {}).get("review_key")
        if not key:
            continue
        if key in by_key:
            raise SystemExit(f"duplicate review_key across exports: {key}")
        by_key[key] = task
    return by_key


def _papers(tasks_dir: Path) -> list[str]:
    seen: dict[str, None] = {}
    for project in spec.PROJECTS:
        for task in _load_tasks(tasks_dir, project):
            paper = (task.get("data") or {}).get("paper_id")
            if paper:
                seen.setdefault(paper, None)
    return list(seen)


def _client(args: argparse.Namespace) -> lsapi.Client:
    try:
        return lsapi.Client(args.url, args.token, dry_run=getattr(args, "dry_run", False))
    except lsapi.LabelStudioError as error:
        raise SystemExit(str(error)) from error


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# -- config -----------------------------------------------------------------


def command_config(args: argparse.Namespace) -> int:
    for path, keys in config_module.write(args.config_dir):
        print(f"wrote {path} ({keys} task-data keys)")
    return 0


# -- lint -------------------------------------------------------------------


def command_lint(args: argparse.Namespace) -> int:
    schema = lint.load_schema()
    if schema is None:
        print(
            "note: no Label Studio checkout found, so the schema layer is skipped. "
            "The Repeater and expansion rules still run."
        )

    failed = False
    for project in _projects(args.only):
        label_config = (
            (args.config_dir / project.config_file).read_text(encoding="utf-8")
            if (args.config_dir / project.config_file).is_file()
            else config_module.build(project)
        )
        problems = lint.check(label_config, schema)
        for kind in project.blocks:
            problems += lint.check_expanded(project, kind)
        print(f"{project.title}: {'INVALID' if problems else 'valid'}")
        for problem in problems:
            print(f"  - {problem}")
        failed = failed or bool(problems)

        if args.against_server:
            client = _client(args)
            for kind in project.blocks:
                expanded = ElementTree.tostring(
                    lint.expanded(project, kind), encoding="unicode"
                )
                status, detail = client.validate_config(expanded)
                print(f"  server, expanded as {kind.name}: HTTP {status}")
                if status != 204:
                    failed = True
                    print(f"    {detail[:300]}")
    return 1 if failed else 0


# -- export -----------------------------------------------------------------


def _paper_text(texts_root: Path, paper_id: str, override: Path | None) -> str:
    """The text this paper's offsets address.

    `processed/local/text.tables.txt` is the built variant with the coordinate
    tables inlined -- the one a reviewer needs, because a table's rows cannot be
    spanned if they are not in the pane. `--text` overrides it for a paper whose
    record was built against something else.
    """

    if override:
        return text_index.normalize(override.read_text(encoding="utf-8"))
    for candidate in (
        texts_root / paper_id / "processed/local/text.tables.txt",
        texts_root / paper_id / "processed/pubget/text.txt",
    ):
        if candidate.is_file():
            return text_index.normalize(candidate.read_text(encoding="utf-8"))
    raise SystemExit(f"no text found for {paper_id} under {texts_root / paper_id}")


def command_export(args: argparse.Namespace) -> int:
    records = (
        [args.record]
        if args.record
        else sorted(args.examples_dir.glob("*.extraction.json"))
    )
    if not records:
        raise SystemExit(f"no records found in {args.examples_dir}")

    args.tasks_dir.mkdir(parents=True, exist_ok=True)
    failed = False
    for path in records:
        paper_id = path.name.split(".")[0]
        body = json.loads(path.read_text(encoding="utf-8"))
        normalized = _paper_text(args.texts_root, paper_id, args.text)
        identifiers_path = args.texts_root / paper_id / "identifiers.json"
        identifiers = (
            json.loads(identifiers_path.read_text(encoding="utf-8"))
            if identifiers_path.is_file()
            else {}
        )

        expected = (body.get("extraction_metadata") or {}).get("source_text_hash")
        try:
            url = staging.stage(args.files_root, paper_id, normalized, expected)
        except staging.TextMismatch as error:
            print(f"{paper_id}: {error}", file=sys.stderr)
            failed = True
            continue

        exporter = tasks_module.Exporter(
            body,
            normalized,
            paper_id,
            identifiers,
            url,
            coordinate_counts=(
                json.loads(args.coordinate_counts.read_text(encoding="utf-8")).get(
                    paper_id, {}
                )
                if args.coordinate_counts
                else None
            ),
            coordinates_only=args.coordinates_only,
        )
        root = args.texts_root / paper_id
        exporter.load_tables(
            root / "source/pubget", root / "stage1/analyses.json", root / "stage1/table-map.json"
        )
        exported = exporter.run()

        problems = exporter.contract_problems()
        if problems:
            failed = True
            print(f"{paper_id}: CONTRACT VIOLATIONS", file=sys.stderr)
            for problem in problems[:20]:
                print(f"  - {problem}", file=sys.stderr)
            continue

        print(f"\n{paper_id}: {exporter.report.summary()}")
        for project in spec.PROJECTS:
            items = exported[project.name]
            if not items and project.name == "adjudication":
                continue
            out = args.tasks_dir / project.task_file(paper_id)
            out.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"  wrote {out.name} ({len(items)} tasks, {out.stat().st_size / 1024:.0f} KB)")
        for note in exporter.report.skipped:
            print(f"  skipped {note}")
    return 1 if failed else 0


# -- deploy -----------------------------------------------------------------


def _take_answers(client: lsapi.Client, project_id: int, snapshot: Path) -> list[dict[str, Any]]:
    """Lift every annotation and draft out of a project, then reset its summary.

    A config change is refused while answers reference Repeater-generated control
    names. Label Studio validates the *unexpanded* config, where the control is
    literally `model_type_{{i}}_{{j}}`, and the regex that would match `model_type_0_1`
    only engages when the index flag appears in `toName` -- which it cannot here,
    since one `<Text name="paper">` is shared by every row. So the answers have to
    come out for the push to go through.

    Two rules, both learned the hard way. Everything is written to `snapshot`
    before the first delete, because Label Studio hard-deletes annotations and
    `core_deletedrow` is not populated for them -- a crash between the delete and
    the restore would destroy the work outright. And a single failing delete is
    reported and stepped over rather than raised, because aborting halfway strands
    every answer already removed.
    """

    saved: list[dict[str, Any]] = []
    # Every task is fetched, with no stub-based shortcut: the stub exposes `drafts`
    # as a list that comes back empty even when the task detail carries one, and
    # there is no `total_drafts` at all. Filtering on either left draft-only tasks
    # untouched, so they were never snapshotted or cleared and the push stayed
    # blocked with nothing to show why.
    for stub in client.iter_tasks(project_id):
        task = client.task(stub["id"])
        key = (task.get("data") or {}).get("review_key", "")
        for annotation in task.get("annotations") or []:
            completed = annotation.get("completed_by")
            saved.append(
                {
                    "task": task["id"],
                    "review_key": key,
                    "kind": "annotation",
                    "id": annotation["id"],
                    "completed_by": completed.get("id")
                    if isinstance(completed, dict)
                    else completed,
                    "result": annotation.get("result") or [],
                    "was_cancelled": annotation.get("was_cancelled", False),
                }
            )
        for draft in task.get("drafts") or []:
            saved.append(
                {
                    "task": task["id"],
                    "review_key": key,
                    "kind": "draft",
                    "id": draft["id"],
                    "result": draft.get("result") or [],
                }
            )

    if saved:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(json.dumps(saved, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  saved {len(saved)} answer(s) to {snapshot} before touching them")
        for item in saved:
            try:
                if item["kind"] == "annotation":
                    client.delete_annotation(item["id"])
                else:
                    client.delete_draft(item["id"])
            except lsapi.LabelStudioError as error:
                print(f"  could not delete {item['kind']} {item['id']}: {error}")

    # Always, even when nothing was found: `created_labels_drafts` goes on counting
    # control names from answers that are already gone, and that stale count alone
    # is enough to refuse the config change.
    client.reset_summary(project_id)
    return saved


def _give_answers_back(client: lsapi.Client, saved: list[dict[str, Any]]) -> int:
    """Re-create the answers taken out for the config push.

    Creating an annotation does not run the config-compatibility check that blocked
    the change, so answers that were legal before the push are legal after it.
    Anything a config change genuinely invalidated is caught later by `sync`, which
    compares content hashes and flags the task as needing re-review rather than
    silently keeping a stale answer.
    """

    restored = 0
    for item in saved:
        payload: dict[str, Any] = {"result": item["result"]}
        try:
            if item["kind"] == "annotation":
                payload["was_cancelled"] = item.get("was_cancelled", False)
                # Keep the author. Without this the API defaults to the requesting
                # user (`tasks/api.py:819-820`) and a second reviewer's annotations
                # silently come back as the curator's, which destroys the very
                # attribution overlap exists for.
                if item.get("completed_by") is not None:
                    payload["completed_by"] = item["completed_by"]
                client.create_annotation(item["task"], payload)
            else:
                client.create_draft(item["task"], payload)
            restored += 1
        except lsapi.LabelStudioError as error:
            # The answers are already out of the project by this point, so a failure
            # here must not abort the run and strand the rest. Report the payload so
            # it can be re-entered by hand.
            print(f"  could not restore {item['kind']} on task {item['task']}: {error}")
            print(f"    {json.dumps(item['result'])[:400]}")
    return restored


def _ensure_project(
    client: lsapi.Client,
    project: spec.Project,
    label_config: str,
    live: dict[str, dict[str, Any]],
    force: bool,
) -> dict[str, Any]:
    body = {
        "label_config": label_config,
        "maximum_annotations": project.overlap,
        "description": project.description,
    }
    existing = live.get(project.title)
    if existing is None:
        created = client.create_project(
            {
                "title": project.title,
                **body,
                # Show the whole queue to everyone rather than hiding submitted work:
                # seeing each other's coverage is a requirement here, and open source
                # has no project-membership gate anyway.
                "show_skip_button": True,
                "enable_empty_annotation": False,
                "show_annotation_history": True,
            }
        )
        print(f"  created (id={created['id']})")
        return created

    try:
        client.update_project(existing["id"], body)
    except lsapi.LabelStudioError:
        if not force:
            print(
                "  config push refused -- the project holds answers referencing "
                "generated control names. Re-run with --force to move them aside "
                "and put them back."
            )
            raise
        # Never overwrite a previous snapshot: a fixed filename protected against a
        # crash inside one run and then destroyed that protection on the next.
        snapshot = BACKUP / f"{project.title}-{_stamp()}.json"
        saved = _take_answers(client, existing["id"], snapshot)
        client.update_project(existing["id"], body)
        restored = _give_answers_back(client, saved)
        print(f"  --force: moved {restored} of {len(saved)} answer(s) aside and back")
        if restored < len(saved):
            print(f"  WARNING: {len(saved) - restored} not restored; they are in {snapshot}")
    print(f"  updated (id={existing['id']})")
    return client.project(existing["id"])


def command_deploy(args: argparse.Namespace) -> int:
    client = _client(args)
    live = client.projects()
    papers = _papers(args.tasks_dir)
    print(f"Label Studio at {client.base_url}: {len(papers)} paper(s) exported")

    for project in _projects(args.only):
        print(f"\n{project.title}")
        path = args.config_dir / project.config_file
        if not path.is_file():
            raise SystemExit(f"missing config {path}. Run: python review/ls.py config")

        if args.recreate and project.title in live:
            client.delete_project(live[project.title]["id"])
            print("  deleted the existing project and everything in it")
            live = client.projects()

        created = _ensure_project(
            client, project, path.read_text(encoding="utf-8"), live, args.force
        )
        project_id = created["id"]

        confirmed = client.project(project_id).get("maximum_annotations")
        print(f"  maximum_annotations = {confirmed}")
        if confirmed != project.overlap:
            print(f"  WARNING: expected {project.overlap}; overlap will not behave as intended")

        if not any(s.get("path") == args.storage_path for s in client.local_storages(project_id)):
            client.create_local_storage(project_id, args.storage_path)
            print(f"  registered local files storage at {args.storage_path} (never synced)")

        exported = _load_tasks(args.tasks_dir, project)
        if exported and not args.skip_import:
            # Importing is not idempotent -- Label Studio has no upsert -- so a
            # second run would double every task. `sync` is what updates in place.
            already = client.project(project_id).get("task_number") or 0
            if already and not args.reimport:
                print(f"  {already} tasks already present, skipping import "
                      f"(--reimport to add {len(exported)} more, or use sync)")
            else:
                print(f"  imported {client.import_tasks(project_id, exported)} tasks")

        if not exported:
            continue
        existing_views = client.views(project_id)
        if len(existing_views) > 1 and not args.recreate_views:
            print(f"  {len(existing_views)} views already exist, not recreating "
                  "(--recreate-views to replace them)")
            continue
        for view in existing_views:
            if view.get("id") and args.recreate_views:
                client.delete_view(view["id"])
        print("  views:")
        for view in spec.views(project):
            client.create_view(
                project_id,
                view.title,
                [f.as_payload() for f in view.filters],
                list(view.columns),
            )
            print(f"    {view.title}")
        for paper in papers:
            client.create_view(
                project_id,
                f"Paper {paper}",
                [spec.Filter("paper_id", "equal", paper).as_payload()],
                ["tasks:data.review_key"],
            )

    print(
        "\nNext: add the second reviewer under Organization, then check the Data "
        "Manager shows the 'Annotated by' column so reviewers can see each other's "
        "coverage."
    )
    return 0


# -- sync -------------------------------------------------------------------


def _replace_predictions(client: lsapi.Client, task_id: int, task: dict[str, Any]) -> int:
    """Swap a task's predictions for the exported ones.

    Predictions are the pre-filled state a reviewer edits, so they have to be
    replaceable independently of the task data -- adding a pre-selected radio
    changes what is shown without changing what is asked. Existing ones are deleted
    first because there is no upsert, and only our own: this instance's chat backend
    keeps one on every task it has answered, and deleting those would destroy the
    exchange the annotation is supposed to carry.
    """

    for prediction in client.predictions(task_id):
        if spec.ours(prediction.get("model_version")):
            client.delete_prediction(prediction["id"])
    written = 0
    for prediction in task.get("predictions") or []:
        client.create_prediction(
            task_id, prediction.get("model_version"), prediction["result"]
        )
        written += 1
    return written


def command_sync(args: argparse.Namespace) -> int:
    client = _client(args)
    live_projects = client.projects()
    print(f"Label Studio at {client.base_url}{'' if args.apply else ' (dry run)'}")
    exit_code = 0

    for project in _projects(args.only):
        exported = _by_key(_load_tasks(args.tasks_dir, project))
        print(f"\n{project.title}: {len(exported)} exported")
        found = live_projects.get(project.title)
        if found is None:
            print("  project missing; run deploy first")
            exit_code = 1
            continue
        project_id = found["id"]

        live: dict[str, dict[str, Any]] = {}
        for task in client.iter_tasks(project_id):
            key = (task.get("data") or {}).get("review_key")
            if key:
                live.setdefault(key, task)

        unchanged = refreshed = moved = added = repredicted = pruned = 0
        stale: list[str] = []
        orphaned: list[str] = []

        for key, task in exported.items():
            existing = live.get(key)
            if existing is None:
                added += 1
                if args.apply:
                    client.import_tasks(project_id, [task])
                continue

            current = existing.get("data") or {}
            if current == task["data"]:
                # Predictions can change without the data changing at all -- adding a
                # pre-selected radio alters what the reviewer sees, not what is asked
                # -- so they are reconciled independently. Compare like with like and
                # only our own rows: counting another producer's is where a
                # 388-versus-609 discrepancy came from.
                wanted = len(task.get("predictions") or [])
                mine = [p for p in client.predictions(existing["id"]) if spec.ours(p.get("model_version"))]
                if wanted != len(mine) and args.apply:
                    repredicted += _replace_predictions(client, existing["id"], task)
                else:
                    unchanged += 1
                continue

            # Two ways the data can differ, needing opposite treatment. A changed
            # hash means the question moved, so an existing answer is stale. An
            # unchanged hash with different data is display-only -- a better
            # paraphrase, a new descriptor -- and the answer still stands. This is
            # the whole point of hashing the answer-bearing payload rather than the
            # rendered task.
            if current.get("content_hash") == task["data"]["content_hash"]:
                refreshed += 1
            else:
                moved += 1
                detail = client.task(existing["id"])
                answered = len(detail.get("annotations") or []) + len(detail.get("drafts") or [])
                if answered:
                    stale.append(f"{key} ({answered} answer(s))")
            if args.apply:
                client.update_task_data(existing["id"], task["data"])
                repredicted += _replace_predictions(client, existing["id"], task)

        for key, task in live.items():
            if key in exported:
                continue
            # The task detail, never the stub: `total_annotations` under-reports and
            # the stub's `drafts` comes back empty even when the task carries one, so
            # a guard built on either would delete answered tasks believing they were
            # empty. Deleting a task destroys its answers with it.
            detail = client.task(task["id"])
            answered = len(detail.get("annotations") or []) + len(detail.get("drafts") or [])
            orphaned.append(f"{key} ({answered} answer(s))")
            if not args.prune:
                continue
            if answered and not args.prune_answered:
                print(f"  refusing to prune {key}: it has {answered} answer(s)")
                continue
            if args.apply:
                client.delete_task(task["id"])
            pruned += 1

        print(
            f"  unchanged {unchanged}, display refreshed {refreshed} (answers kept), "
            f"question changed {moved}, imported {added}, "
            f"predictions rewritten {repredicted}, pruned {pruned}"
        )
        if stale:
            print(f"  {len(stale)} task(s) already had answers and the question moved -- "
                  "these need re-review:")
            for note in stale[:10]:
                print(f"    - {note}")
        if orphaned:
            verb = ("pruned" if args.apply else "would prune") if args.prune else "left in place"
            print(f"  {len(orphaned)} live task(s) are no longer exported ({verb}):")
            for note in orphaned[:10]:
                print(f"    - {note}")

        if args.prune_answers:
            exit_code = _prune_orphan_answers(client, project, project_id, args) or exit_code

    if not args.apply:
        print("\nnothing written; re-run with --apply")
    return exit_code


def _prune_orphan_answers(
    client: lsapi.Client, project: spec.Project, project_id: int, args: argparse.Namespace
) -> int:
    """Drop result entries whose control the config no longer declares.

    Removing a control from a config does not touch the answers already given to
    it. Label Studio keeps the old entry in `Annotation.result` and simply stops
    rendering it, so an annotation goes on asserting a verdict to a question that
    has been deleted. Nothing surfaces it: the editor shows the current form, the
    API returns the stale entry, and a decoder reading by `from_name` skips it
    silently -- which means the record quietly disagrees with itself.

    What counts as orphaned is derived from the config, never from a list of names:
    the config is expanded against each task's own data, so the valid control set is
    exactly what that task would render.
    """

    path = args.config_dir / project.config_file
    if not path.is_file():
        return 0
    declared_for = ElementTree.fromstring(path.read_text(encoding="utf-8"))
    pending: list[tuple[int, list[dict[str, Any]], list[str]]] = []
    drafts = 0

    for stub in client.iter_tasks(project_id):
        task = client.task(stub["id"])
        declared = {
            node.get("name")
            for node in lint.expand(declared_for, task.get("data") or {}).iter()
            if node.get("name")
        }
        for annotation in task.get("annotations") or []:
            result = annotation.get("result") or []
            gone = sorted(
                {
                    entry.get("from_name")
                    for entry in result
                    if entry.get("from_name") and entry.get("from_name") not in declared
                }
            )
            if gone:
                kept = [e for e in result if e.get("from_name") in declared]
                pending.append((annotation["id"], kept, gone))
                print(f"  annotation {annotation['id']} drops {', '.join(gone)} "
                      f"-- {len(result)} -> {len(kept)} entries")
        for draft in task.get("drafts") or []:
            if any(
                entry.get("from_name") and entry.get("from_name") not in declared
                for entry in draft.get("result") or []
            ):
                drafts += 1

    if drafts:
        # Left alone deliberately: a draft is rewritten wholesale the next time the
        # reviewer saves, so the stale entry clears itself, and editing someone's
        # in-progress work to fix a cosmetic inconsistency is the worse trade.
        print(f"  {drafts} draft(s) also carry orphans, left alone")
    if not pending:
        return 0
    if args.apply:
        BACKUP.mkdir(parents=True, exist_ok=True)
        snapshot = BACKUP / f"{project.title}-orphans-{_stamp()}.json"
        snapshot.write_text(
            json.dumps([{"id": i, "kept": k, "dropped": g} for i, k, g in pending], indent=1),
            encoding="utf-8",
        )
        print(f"  saved {len(pending)} annotation(s) to {snapshot} before rewriting")
        for annotation_id, kept, _ in pending:
            client.update_annotation(annotation_id, {"result": kept})
    return 0


# -- verify -----------------------------------------------------------------


class Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.notes: list[str] = []

    def check(self, condition: bool, message: str) -> bool:
        print(f"  {'ok  ' if condition else 'FAIL'}  {message}")
        if not condition:
            self.failures.append(message)
        return condition


def command_verify(args: argparse.Namespace) -> int:
    client = _client(args)
    checks = Checks()
    live = client.projects()
    print(f"Label Studio at {client.base_url}")

    print("\ntext serving")
    served: dict[str, str] = {}
    for paper in _papers(args.tasks_dir):
        staged = staging.staged_path(args.files_root, paper)
        if not staged.is_file():
            checks.check(False, f"staged text missing: {staged}")
            continue
        url = staging.url_for(paper)
        status, body = client.fetch(url)
        if not checks.check(status == 200, f"{url} serves HTTP 200 (got {status})"):
            if status == 404:
                checks.notes.append(
                    "A 404 here almost always means no LocalFilesImportStorage is "
                    "registered for the project. LOCAL_FILES_SERVING_ENABLED alone is not "
                    "enough -- the serving view filters on storage rows "
                    "(io_storages/localfiles/views.py:104). Re-run deploy, which "
                    "registers it."
                )
            continue
        expected = staged.read_bytes()
        if checks.check(
            body == expected,
            f"{paper}: served bytes identical to the staged file "
            f"({len(body)} vs {len(expected)})",
        ):
            served[paper] = body.decode("utf-8")

    for project in _projects(args.only):
        print(f"\n{project.title}")
        found = live.get(project.title)
        if not checks.check(found is not None, f"{project.title} exists"):
            continue
        exported = _load_tasks(args.tasks_dir, project)
        checks.check(
            found.get("maximum_annotations") == project.overlap,
            f"maximum_annotations is {project.overlap} "
            f"(got {found.get('maximum_annotations')})",
        )
        checks.check(
            found.get("task_number") == len(exported),
            f"{len(exported)} tasks, no phantom imports (got {found.get('task_number')})",
        )
        if served:
            _offsets_hold(client, found["id"], served, args.sample, checks)

    print()
    for note in checks.notes:
        print(f"note: {note}")
    if checks.failures:
        print(f"\n{len(checks.failures)} check(s) failed")
        return 1
    print("deployment looks correct")
    return 0


def _offsets_hold(
    client: lsapi.Client,
    project_id: int,
    served: dict[str, str],
    sample: int,
    checks: Checks,
) -> None:
    """Every stored offset must address the text served for *its own* paper.

    Annotations and drafts are sampled alongside predictions, and that is the point
    of the check rather than a completeness flourish. A prediction with stale
    offsets is rewritten from the export by the next sync; a span a reviewer drew
    exists nowhere but the database, so nothing restores it and nothing else
    notices.
    """

    checked = mismatched = incoherent = 0
    examples: list[str] = []
    for index, stub in enumerate(client.iter_tasks(project_id)):
        if index >= sample:
            break
        task = client.task(stub["id"])
        text = served.get((task.get("data") or {}).get("paper_id"))
        if text is None:
            continue
        for kind in ("predictions", "annotations", "drafts"):
            for item in task.get(kind) or []:
                for entry in item.get("result") or []:
                    value = entry.get("value") or {}
                    if "start" not in value:
                        continue
                    checked += 1
                    quote = value.get("text")
                    if text[value["start"] : value["end"]] == quote:
                        continue
                    # An entry whose range and quote disagree in length was never a
                    # valid address, so no resync can repair it. Counted apart, or one
                    # malformed draft fails this check forever and trains everyone to
                    # ignore it.
                    if isinstance(quote, str) and value["end"] - value["start"] != len(quote):
                        incoherent += 1
                        continue
                    mismatched += 1
                    if len(examples) < 3:
                        examples.append(
                            f"{kind[:-1]} [{value['start']}:{value['end']}] "
                            f"served={text[value['start']:value['end']]!r} stored={quote!r}"
                        )

    if not checked:
        checks.notes.append(f"project {project_id} ships no spans; nothing to check")
        return
    checks.check(
        mismatched == 0,
        f"all {checked} sampled spans address the served text ({mismatched} mismatched)",
    )
    for example in examples:
        checks.notes.append(f"offset mismatch: {example}")
    if incoherent:
        checks.notes.append(
            f"project {project_id}: {incoherent} entr(ies) whose range and quote disagree "
            "in length; never a valid address, left as they are"
        )


# -- decode -----------------------------------------------------------------


def command_decode(args: argparse.Namespace) -> int:
    client = _client(args)
    live = client.projects()
    decoded: list[dict[str, Any]] = []

    for project in _projects(args.only):
        found = live.get(project.title)
        if found is None:
            continue
        entries = answers_module.read_project(client, found["id"])
        decoded += entries
        print(f"\n{project.title}: {len(entries)} annotation(s)")
        for entry in entries:
            print(f"  {entry['review_key']}  verdict={entry['verdict']}")
            for row, answer in entry["rows"].items():
                if answer:
                    print(f"    {row}: {answer}")
            for row, targets in entry["links"].items():
                print(f"    {row} -> {', '.join(targets) if targets else '(none)'}")
            changed = {k: len(v) for k, v in entry["evidence"].items() if v and k != "kept"}
            if changed:
                print(f"    evidence: {changed}")
            for note in entry["unresolved"]:
                print(f"    UNRESOLVED {note}")

    unresolved = sum(len(entry["unresolved"]) for entry in decoded)
    print(f"\n{len(decoded)} annotation(s), {unresolved} unresolved token(s)")
    if args.out:
        args.out.write_text(json.dumps(decoded, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.out}")
    # An unresolved token is a decode failure, not a review finding: reconstruction
    # would silently drop whatever it names.
    return 1 if unresolved else 0


# -- chat -------------------------------------------------------------------


def command_chat(args: argparse.Namespace) -> int:
    import chat

    return chat.serve(
        port=args.port,
        files_root=args.files_root,
        key_file=args.key_file,
        model=args.model,
        effort=args.effort,
        timeout=args.timeout,
    )


# -- argument parsing -------------------------------------------------------


def _add_server(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", default=None, help="defaults to LABEL_STUDIO_URL")
    parser.add_argument("--token", default=None, help="defaults to LABEL_STUDIO_API_KEY")
    parser.add_argument(
        "--only",
        action="append",
        metavar="PROJECT",
        help="restrict to these projects (repeatable), by name or title",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ls.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    write = commands.add_parser("config", help="write the labeling configs")
    write.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    write.set_defaults(run=command_config)

    check = commands.add_parser("lint", help="check the configs")
    check.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    check.add_argument(
        "--against-server",
        action="store_true",
        help="also POST each expanded variant to /api/projects/validate/ (204 = valid). "
        "Needs no project and mutates nothing",
    )
    _add_server(check)
    check.set_defaults(run=command_lint)

    export = commands.add_parser("export", help="turn extraction records into tasks")
    export.add_argument("--record", type=Path, help="one record; default is every example")
    export.add_argument("--examples-dir", type=Path, default=EXAMPLES)
    export.add_argument("--texts-root", type=Path, default=TEXTS_ROOT)
    export.add_argument("--text", type=Path, help="override the paper text for --record")
    export.add_argument("--files-root", type=Path, default=FILES_ROOT)
    export.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
    export.add_argument(
        "--coordinate-counts",
        type=Path,
        help="JSON from the table parser, {paper: {table_local_id: n}}. "
        "Table.coordinate_count is storage-only, so it cannot come out of the record",
    )
    export.add_argument(
        "--coordinates-only",
        action="store_true",
        help="emit model and contrast tasks only where the result is reported as "
        "coordinates; what is skipped is counted and named, never dropped silently",
    )
    export.set_defaults(run=command_export)

    deploy = commands.add_parser("deploy", help="create the projects and import the tasks")
    deploy.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    deploy.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
    deploy.add_argument("--storage-path", default=STORAGE_PATH,
                        help="the staged texts as seen INSIDE the container")
    deploy.add_argument("--skip-import", action="store_true")
    deploy.add_argument("--reimport", action="store_true",
                        help="import even when the project already has tasks (duplicates them)")
    deploy.add_argument("--recreate", action="store_true",
                        help="DELETE each project and everything in it first")
    deploy.add_argument("--recreate-views", action="store_true",
                        help="replace the Data Manager views, which are otherwise "
                        "created once and never refreshed")
    deploy.add_argument("--force", action="store_true",
                        help="if a config push is refused because answers reference its "
                        "controls, snapshot them, delete them, push, and put them back")
    _add_server(deploy)
    deploy.set_defaults(run=command_deploy)

    sync = commands.add_parser("sync", help="reconcile live tasks with a fresh export")
    sync.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
    sync.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    sync.add_argument("--apply", action="store_true", help="write; without it this is a dry run")
    sync.add_argument("--prune", action="store_true",
                      help="delete live tasks the exporter no longer produces")
    sync.add_argument("--prune-answered", action="store_true",
                      help="prune even tasks that carry answers")
    sync.add_argument("--prune-answers", action="store_true",
                      help="also drop result entries whose control the config no "
                      "longer declares")
    _add_server(sync)
    sync.set_defaults(run=command_sync, dry_run=False)

    verify = commands.add_parser("verify", help="prove the deployment actually works")
    verify.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
    verify.add_argument("--files-root", type=Path, default=FILES_ROOT)
    verify.add_argument("--sample", type=int, default=60,
                        help="tasks per project to sample for offset checks")
    _add_server(verify)
    verify.set_defaults(run=command_verify)

    decode = commands.add_parser("decode", help="read reviewer answers back out")
    decode.add_argument("--out", type=Path, help="write the decoded answers as JSON")
    _add_server(decode)
    decode.set_defaults(run=command_decode)

    chat = commands.add_parser("chat", help="run the ML backend that answers questions")
    chat.add_argument("--port", type=int, default=9090)
    chat.add_argument("--files-root", type=Path, default=FILES_ROOT)
    chat.add_argument("--key-file", type=Path, default=REVIEW.parent / ".env")
    chat.add_argument("--model", default=None)
    chat.add_argument("--effort", default=None)
    chat.add_argument("--timeout", type=float, default=None)
    chat.set_defaults(run=command_chat)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except lsapi.LabelStudioError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
