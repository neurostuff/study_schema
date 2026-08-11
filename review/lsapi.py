#!/usr/bin/env python3
"""The one HTTP client for Label Studio.

There were six: one per script, each with its own paging, its own error handling
and its own idea of which routes need a trailing slash. Two of them disagreed
about how paging terminates, and the disagreement was invisible until a project's
task count hit an exact multiple of the page size.

Standard library only, matching the rest of the repo.

## Routes that are fussy, and why

Django's `APPEND_SLASH` redirects a slashless URL to the canonical one, and a
redirect drops the request body -- so a PATCH to `/api/annotations/<id>` reads as a
silent no-op rather than an error. The rule is not uniform across the API, so the
methods below encode it per route rather than leaving it to the caller:

    /api/annotations/<id>/   trailing slash   (annotations/urls.py)
    /api/drafts/<id>/        trailing slash   (tasks/urls.py:33-44)
    /api/tasks/<id>/drafts   NO slash         (tasks/urls.py:18)

## Paging

`GET /api/tasks?page=N` answers HTTP 404 ("Invalid page") for the page after the
last, not an empty list. So a loop that waits for an empty batch dies on every
project once the total is reached. `iter_tasks` stops on the reported total.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

DEFAULT_URL = "http://localhost:8080"

#: Import in chunks. One paper is ~600 value tasks, and a single request carrying
#: every paper at once is slow and hard to diagnose when it fails.
CHUNK = 250

#: Tasks per page when listing. The server's own maximum is higher, but a large
#: page is a large JSON parse for no gain -- the detail fetch is what costs.
PAGE = 200

_WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


class LabelStudioError(RuntimeError):
    """A request the server refused, or a server that could not be reached."""


class Client:
    """A Label Studio REST client scoped to what the review layer needs.

    `dry_run` makes every write a no-op that prints what it would have done and
    returns None. Read paths are unaffected, so a dry run walks exactly the same
    task list as the real one -- which is the only way a dry run's report can be
    trusted to describe the run that follows it.
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        dry_run: bool = False,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("LABEL_STUDIO_URL", DEFAULT_URL)).rstrip("/")
        self.token = token or os.environ.get("LABEL_STUDIO_API_KEY", "")
        self.dry_run = dry_run
        self.timeout = timeout
        if not self.token:
            raise LabelStudioError(
                "no API token: set LABEL_STUDIO_API_KEY or pass --token.\n"
                "Find it in Label Studio under Account & Settings > Access Token."
            )

    # -- transport ---------------------------------------------------------

    def fetch(self, path: str) -> tuple[int, bytes]:
        """A raw GET returning (status, body), never raising on an HTTP status.

        For the text-serving check, where a 404 is the finding rather than an
        error: `/data/local-files/` answers 404 when no storage row covers the
        directory, and that is exactly the condition worth reporting.
        """

        request = urllib.request.Request(
            f"{self.base_url}{path}", headers={"Authorization": f"Token {self.token}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except urllib.error.URLError as error:
            raise LabelStudioError(
                f"cannot reach Label Studio at {self.base_url}: {error.reason}\n"
                "Is the container up, and is LABEL_STUDIO_URL correct?"
            ) from error

    def request(self, method: str, path: str, payload: Any = None) -> Any:
        if self.dry_run and method in _WRITE_METHODS:
            print(f"  would {method} {path}")
            return None

        body = None
        headers = {"Authorization": f"Token {self.token}"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:600]
            raise LabelStudioError(f"{method} {path} -> HTTP {error.code}\n{detail}") from error
        except urllib.error.URLError as error:
            raise LabelStudioError(
                f"cannot reach Label Studio at {self.base_url}: {error.reason}\n"
                "Is the container up, and is LABEL_STUDIO_URL correct?"
            ) from error
        return json.loads(raw) if raw else None

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: Any = None) -> Any:
        return self.request("POST", path, payload if payload is not None else {})

    def patch(self, path: str, payload: Any) -> Any:
        return self.request("PATCH", path, payload)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    # -- projects ----------------------------------------------------------

    def projects(self) -> dict[str, dict[str, Any]]:
        """Every project, by title.

        `page_size=1000` is a ceiling, and a real one: it is why one project per
        paper does not scale. It is well clear of one project per task-kind family.
        """

        page = self.get("/api/projects?page_size=1000")
        listing = page.get("results", page) if isinstance(page, dict) else page
        return {project["title"]: project for project in listing or []}

    def project(self, project_id: int) -> dict[str, Any]:
        return self.get(f"/api/projects/{project_id}")

    def create_project(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.post("/api/projects", body)

    def update_project(self, project_id: int, body: dict[str, Any]) -> Any:
        return self.patch(f"/api/projects/{project_id}", body)

    def delete_project(self, project_id: int) -> Any:
        return self.delete(f"/api/projects/{project_id}/")

    def reset_summary(self, project_id: int) -> Any:
        """Clear the project's cached control-name census.

        `created_labels_drafts` goes on counting names from answers that are
        already deleted, and that stale count alone is enough for the server to
        refuse a config change.
        """

        return self.post(f"/api/projects/{project_id}/summary/reset/")

    # -- tasks -------------------------------------------------------------

    def iter_tasks(self, project_id: int) -> Iterator[dict[str, Any]]:
        """Task stubs, paged.

        A stub carries `data` and the annotation counters but **not** a usable
        `drafts` list: it comes back empty even when the task detail holds one, and
        there is no `total_drafts` at all. Anything that must know about drafts has
        to call `task()` per task -- see the note there.
        """

        page = 1
        seen = 0
        while True:
            body = self.get(
                f"/api/tasks?project={project_id}&page={page}&page_size={PAGE}"
            )
            batch = body.get("tasks") or body.get("results") or []
            if not batch:
                return
            yield from batch
            seen += len(batch)
            if seen >= (body.get("total") or body.get("count") or seen):
                return
            page += 1

    def tasks(self, project_id: int) -> list[dict[str, Any]]:
        return list(self.iter_tasks(project_id))

    def task(self, task_id: int) -> dict[str, Any]:
        """One task with its annotations, drafts and predictions.

        Slow per task and unavoidable wherever the answer matters: deleting or
        pruning on the strength of a stub's counters deletes answered tasks
        believing they were empty.
        """

        return self.get(f"/api/tasks/{task_id}")

    def import_tasks(self, project_id: int, tasks: list[dict[str, Any]]) -> int:
        imported = 0
        for start in range(0, len(tasks), CHUNK):
            chunk = tasks[start : start + CHUNK]
            result = self.post(f"/api/projects/{project_id}/import", chunk)
            imported += (result or {}).get("task_count", len(chunk))
        return imported

    def update_task_data(self, task_id: int, data: dict[str, Any]) -> Any:
        return self.patch(f"/api/tasks/{task_id}", {"data": data})

    def delete_task(self, task_id: int) -> Any:
        return self.delete(f"/api/tasks/{task_id}")

    # -- predictions -------------------------------------------------------

    def predictions(self, task_id: int) -> list[dict[str, Any]]:
        found = self.get(f"/api/predictions?task={task_id}") or []
        return found.get("results") or [] if isinstance(found, dict) else found

    def create_prediction(self, task_id: int, model_version: str, result: list) -> Any:
        return self.post(
            "/api/predictions",
            {"task": task_id, "model_version": model_version, "result": result},
        )

    def delete_prediction(self, prediction_id: int) -> Any:
        return self.delete(f"/api/predictions/{prediction_id}/")

    # -- answers -----------------------------------------------------------
    #
    # Annotations are hard-deleted and `core_deletedrow` is not populated for
    # them, so anything that removes one snapshots it to disk first.

    def create_annotation(self, task_id: int, payload: dict[str, Any]) -> Any:
        return self.post(f"/api/tasks/{task_id}/annotations/", payload)

    def update_annotation(self, annotation_id: int, payload: dict[str, Any]) -> Any:
        return self.patch(f"/api/annotations/{annotation_id}/", payload)

    def delete_annotation(self, annotation_id: int) -> Any:
        return self.delete(f"/api/annotations/{annotation_id}/")

    def create_draft(self, task_id: int, payload: dict[str, Any]) -> Any:
        return self.post(f"/api/tasks/{task_id}/drafts", payload)

    def delete_draft(self, draft_id: int) -> Any:
        return self.delete(f"/api/drafts/{draft_id}/")

    # -- storage and views -------------------------------------------------

    def local_storages(self, project_id: int) -> list[dict[str, Any]]:
        found = self.get(f"/api/storages/localfiles?project={project_id}") or []
        return found if isinstance(found, list) else []

    def create_local_storage(self, project_id: int, path: str) -> Any:
        """Register a directory as a local files import storage.

        Required, not optional. `LOCAL_FILES_SERVING_ENABLED` and
        `LOCAL_FILES_DOCUMENT_ROOT` are not sufficient: the serving view filters on
        `LocalFilesImportStorage` rows whose `path` prefixes the requested file's
        directory and 404s when none match (`io_storages/localfiles/views.py:104-119`).

        Never synced. A sync walks the directory and imports every `.txt` as a
        task; the row exists only so the endpoint will serve and project members
        inherit access.
        """

        return self.post(
            "/api/storages/localfiles/",
            {
                "project": project_id,
                "path": path,
                "title": "staged paper texts",
                "use_blob_urls": False,
            },
        )

    def views(self, project_id: int) -> list[dict[str, Any]]:
        return self.get(f"/api/dm/views?project={project_id}") or []

    def create_view(
        self,
        project_id: int,
        title: str,
        filters: list[dict[str, Any]],
        columns: list[str],
    ) -> Any:
        data: dict[str, Any] = {"title": title, "ordering": list(columns)}
        if filters:
            data["filters"] = {"conjunction": "and", "items": filters}
        return self.post("/api/dm/views", {"project": project_id, "data": data})

    def delete_view(self, view_id: int) -> Any:
        return self.delete(f"/api/dm/views/{view_id}/")

    # -- validation --------------------------------------------------------

    def validate_config(self, label_config: str) -> tuple[int, str]:
        """Ask the server's own validator about a config. 204 means valid.

        Needs no project and mutates nothing, so it is safe to run against a live
        instance -- and it is the only way to check a config against the exact
        version that will render it.
        """

        body = json.dumps({"label_config": label_config}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/projects/validate/",
            data=body,
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", "replace")[:600]
        except urllib.error.URLError as error:
            raise LabelStudioError(
                f"cannot reach Label Studio at {self.base_url}: {error.reason}"
            ) from error
