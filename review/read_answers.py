#!/usr/bin/env python3
"""Decode reviewer answers back into the extraction's own vocabulary.

Label Studio does not store what a reviewer saw; it stores what the config told it
to store. For a dynamic `Choices` that is the **alias** -- "alias replaces the
choice value in the annotation results. Alias does not display in the interface"
-- and the same rule governs dynamic `Labels`, whose `selectedValues` reads
`alias ? alias : value`. So the encoding is a property of the config at the moment
the answer was written, and answers written under two different configs sit side
by side in one project.

That is exactly what happened here. The relationship grid originally carried
`{value: local_id, alias: descriptor}`, so the reviewer read a bare `acq_fmri_rest`
and the annotation recorded `acq_fmri_rest . modality=fMRI` -- a descriptor derived
at export time, which reconstruction cannot resolve and which changes whenever a
priority-0 field does. The exporter now carries `{value: descriptor, alias:
local_id}`. This module reads both, because the old answers are still real answers.

Resolution goes through the task's own `columns`, never through a rebuilt
descriptor: the task data is the record of what that answer was offered, so it
decodes its own answers even if the descriptor rule has since changed.

Rows are addressed the same way. `lm_3` names a control, and 3 is a position in
the `rows_multi` the task shipped -- meaningless after an instance is dropped. The
index is resolved against that array to a `local_id`, and an index with no row is
reported rather than guessed, because an off-by-one reassignment produces a valid
record with the wrong content.

Usage:
    export LABEL_STUDIO_URL=http://localhost:8080
    export LABEL_STUDIO_API_KEY=<token>
    python review/read_answers.py --project ns-review-relationship
    python review/read_answers.py --project ns-review-relationship --out review/links.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping

DEFAULT_URL = "http://localhost:8080"

#: `lm_0` (multivalued row) or `ls_0` (single-valued row). The prefix says which
#: array the index addresses, which is why the two families of control got
#: different prefixes in the first place.
ROW_CONTROL = re.compile(r"^(lm|ls)_(\d+)$")

#: The exporter's stand-in for "this row links to nothing". It is a column, so
#: that "no link" is an assertion a reviewer makes rather than an empty answer,
#: and it resolves to no target rather than to a target named "none".
NO_LINK = "none"


class Unresolved(Exception):
    """A stored token that no column in the task explains."""


def target_index(data: Mapping[str, Any]) -> dict[str, str]:
    """Every spelling a stored choice might use -> the local_id it means.

    Both directions of the value/alias swap land in one table: the alias maps to
    itself and the displayed value maps to the alias. An answer written under
    either config therefore resolves, and a token under neither raises instead of
    being passed through as if it were an id.
    """

    index: dict[str, str] = {}
    for column in data.get("columns") or []:
        alias = column.get("alias")
        value = column.get("value")
        if not alias:
            # A column with no alias stores its value, so the value IS the id.
            if value:
                index[value] = value
            continue
        index[alias] = alias
        if value:
            index[value] = alias
    return index


def row_ids(data: Mapping[str, Any]) -> dict[str, list[str]]:
    """Control prefix -> the local_ids its indices address, in order."""

    return {
        "lm": [row.get("local_id", "") for row in data.get("rows_multi") or []],
        "ls": [row.get("local_id", "") for row in data.get("rows_single") or []],
    }


def resolve_choices(tokens: Iterable[str], index: Mapping[str, str]) -> list[str]:
    """Stored choice tokens -> local_ids, dropping the explicit `no link`."""

    out: list[str] = []
    for token in tokens:
        if token == NO_LINK:
            continue
        target = index.get(token)
        if target is None:
            raise Unresolved(token)
        if target not in out:
            out.append(target)
    return out


def decode_relationship(task: Mapping[str, Any], result: list[Mapping[str, Any]]) -> dict[str, Any]:
    """One relationship annotation as `{row local_id: [target local_id]}`.

    Every row the task rendered appears in the output, including rows the
    reviewer left empty: an unticked row asserts "this links to nothing", and
    omitting it would make that indistinguishable from a row nobody was asked
    about.
    """

    data = task.get("data") or {}
    index = target_index(data)
    rows = row_ids(data)
    links: dict[str, list[str]] = {
        local_id: [] for prefix in rows for local_id in rows[prefix] if local_id
    }
    unresolved: list[str] = []

    for entry in result:
        match = ROW_CONTROL.match(entry.get("from_name") or "")
        if not match or entry.get("type") != "choices":
            continue
        prefix, position = match.group(1), int(match.group(2))
        addressable = rows[prefix]
        if position >= len(addressable) or not addressable[position]:
            unresolved.append(f"{entry['from_name']} addresses no row")
            continue
        try:
            links[addressable[position]] = resolve_choices(
                (entry.get("value") or {}).get("choices") or [], index
            )
        except Unresolved as error:
            unresolved.append(f"{entry['from_name']}: no column spells {error.args[0]!r}")

    return {
        "review_key": data.get("review_key"),
        "slot": data.get("rel_slot", "").split(" ")[0],
        "links": links,
        "unresolved": unresolved,
    }


def decode_spans(task: Mapping[str, Any], result: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Highlights, with each label resolved to the local_id it supports.

    The span layer carries the same value/alias split as the grid, so a highlight
    already records an id. A label under no column is kept with `local_id: None`
    rather than dropped -- the passage is still evidence a reviewer marked, and
    losing it silently would be worse than reporting it unresolved.
    """

    index = target_index(task.get("data") or {})
    spans = []
    for entry in result:
        value = entry.get("value") or {}
        if entry.get("type") != "labels" or "start" not in value:
            continue
        for label in value.get("labels") or []:
            spans.append(
                {
                    "local_id": index.get(label),
                    "label": label,
                    "start": value["start"],
                    "end": value["end"],
                    "text": value.get("text", ""),
                }
            )
    return spans


# -- reading a live project -------------------------------------------------


def get(base: str, token: str, path: str) -> Any:
    request = urllib.request.Request(
        f"{base}{path}", headers={"Authorization": f"Token {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise SystemExit(
            f"GET {path} -> HTTP {error.code}: {error.read()[:300].decode('utf-8', 'replace')}"
        ) from error


def project_answers(base: str, token: str, title: str) -> list[dict[str, Any]]:
    """Every submitted annotation in the project, decoded.

    Drafts are excluded: an unsubmitted answer is work in progress, and feeding
    it to reconstruction would build a record from a half-made decision.
    """

    page = get(base, token, "/api/projects?page_size=1000")
    projects = {p["title"]: p for p in (page.get("results") or page)}
    if title not in projects:
        raise SystemExit(f"no project titled {title!r}")

    decoded = []
    listing = get(base, token, f"/api/tasks?project={projects[title]['id']}&page_size=1000")
    for stub in listing.get("tasks") or listing.get("results") or []:
        task = get(base, token, f"/api/tasks/{stub['id']}")
        for annotation in task.get("annotations") or []:
            if annotation.get("was_cancelled"):
                continue
            result = annotation.get("result") or []
            entry = decode_relationship(task, result)
            entry.update(
                task_id=task["id"],
                annotation_id=annotation["id"],
                by=annotation.get("completed_by_email")
                or (annotation.get("completed_by") if isinstance(annotation.get("completed_by"), str) else None),
                spans=decode_spans(task, result),
            )
            decoded.append(entry)
    return decoded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="ns-review-relationship")
    parser.add_argument("--url", default=os.environ.get("LABEL_STUDIO_URL", DEFAULT_URL))
    parser.add_argument("--token", default=os.environ.get("LABEL_STUDIO_API_KEY", ""))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    if not args.token:
        print("set LABEL_STUDIO_API_KEY or pass --token")
        return 2

    decoded = project_answers(args.url.rstrip("/"), args.token, args.project)
    unresolved = sum(len(entry["unresolved"]) for entry in decoded)

    for entry in decoded:
        print(f"\n{entry['review_key']}  (annotation {entry['annotation_id']})")
        for row, targets in entry["links"].items():
            print(f"  {row} -> {', '.join(targets) if targets else '(none)'}")
        for note in entry["unresolved"]:
            print(f"  UNRESOLVED {note}")

    print(f"\n{len(decoded)} annotation(s), {unresolved} unresolved token(s)")
    if args.out:
        args.out.write_text(json.dumps(decoded, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    # Unresolved tokens are a decode failure, not a review finding: reconstruction
    # would silently drop those links.
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
