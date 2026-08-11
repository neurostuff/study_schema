#!/usr/bin/env python3
"""Read reviewer answers back into the record's own vocabulary.

Label Studio does not store what a reviewer saw; it stores what the config told it
to store. Three things follow, and each one is a place a decoder can quietly lie.

**The control name is the address.** A result entry carries `from_name` and
nothing else, so the name has to say which question was answered and about which
row. `spec.control` builds those names and `spec.parse_control` reads them back,
which is the whole reason a single decoder can serve every kind rather than one
kind having a decoder and the rest having none.

**A dynamic option stores its alias, not its value.** "Alias replaces the choice
value in the annotation results. Alias does not display in the interface", and the
same rule governs dynamic `Labels`, whose `selectedValues` reads
`alias ? alias : value`. So the encoding is a property of the config at the moment
the answer was written, and resolution goes through the task's own `columns` --
the task data is the record of what that answer was offered.

**A row index is a position, not an identity.** `relationship_row_0_3` names
position 3 of the `rows` array the task shipped, which means nothing once an
instance is dropped. The index is resolved against that array to a `local_id`, and
an index with no row is reported rather than guessed: an off-by-one reassignment
produces a valid record with the wrong content, which is the failure that would
never be noticed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import lsapi
import spec

#: The exporter's stand-in for "this row links to nothing". It is a column, so that
#: "no link" is an assertion a reviewer makes rather than an empty answer, and it
#: resolves to no target rather than to a target named "none".
NO_LINK = "none"


class Unresolved(Exception):
    """A stored token that no column in the task explains."""


# -- resolving what a stored token meant ------------------------------------


def target_index(data: Mapping[str, Any]) -> dict[str, str]:
    """Every spelling a stored choice might use -> the local_id it means.

    Both directions of the value/alias swap land in one table: the alias maps to
    itself and the displayed value maps to the alias. An answer written under
    either encoding therefore resolves, and a token under neither raises instead of
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


def resolve(tokens: Iterable[str], index: Mapping[str, str]) -> list[str]:
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


def _row_identity(data: Mapping[str, Any], control: spec.Control) -> tuple[str, str | None]:
    """(the array this control's index addresses, the row's own id).

    `one` addresses the single-valued rows, everything else the main `rows` array.
    A row with no `local_id` of its own -- a contrast cell, a table sibling -- falls
    back to its label, which is what the reviewer was looking at.
    """

    key = "rows_single" if control.role == "one" else "rows"
    rows = data.get(key) or []
    position = control.row
    if position is None:
        return key, None
    if position >= len(rows):
        return key, None
    row = rows[position]
    return key, row.get("local_id") or row.get("label") or str(position)


def _value_of(entry: Mapping[str, Any]) -> Any:
    """One result entry's answer, whatever control produced it."""

    value = entry.get("value") or {}
    for key in ("choices", "text", "number", "taxonomy", "labels"):
        if key in value:
            found = value[key]
            return found[0] if key == "choices" and len(found) == 1 else found
    return None


# -- one annotation ---------------------------------------------------------


def decode(task: Mapping[str, Any], result: list[Mapping[str, Any]]) -> dict[str, Any]:
    """One annotation, read into the vocabulary the record uses.

    Every rendered row appears in `rows`, including rows the reviewer left
    untouched: an unanswered row and a row nobody was asked about have to stay
    distinguishable, and only the task can say which is which.
    """

    data = task.get("data") or {}
    kind = data.get("task_kind", "")
    index = target_index(data)
    decoded: dict[str, Any] = {
        "review_key": data.get("review_key"),
        "task_kind": kind,
        "local_id": data.get("local_id"),
        "verdict": None,
        "rows": {},
        "links": {},
        "notes": {},
        "spans": [],
        "chat": [],
        "unresolved": [],
    }

    for key in ("rows", "rows_single"):
        for position, row in enumerate(data.get(key) or []):
            identity = row.get("local_id") or row.get("label") or str(position)
            decoded["rows"].setdefault(identity, {})
            if kind == "relationship":
                decoded["links"].setdefault(identity, [])

    for entry in result:
        name = entry.get("from_name") or ""
        if name in (spec.CHAT_QUESTION, spec.CHAT_ANSWER):
            decoded["chat"].append({"control": name, "text": _value_of(entry)})
            continue
        if name == "comment":
            decoded["notes"]["comment"] = _value_of(entry)
            continue
        if entry.get("type") == "labels" or "start" in (entry.get("value") or {}):
            decoded["spans"] += _spans_of(entry, index)
            continue

        control = spec.parse_control(name)
        if control is None:
            decoded["unresolved"].append(f"{name}: no control of that name is generated")
            continue
        if control.kind != kind and kind:
            decoded["unresolved"].append(
                f"{name}: belongs to the {control.kind} block, but this is a {kind} task"
            )
            continue

        if control.role == "verdict":
            decoded["verdict"] = _value_of(entry)
            continue

        array, identity = _row_identity(data, control)
        if control.row is not None and identity is None:
            decoded["unresolved"].append(f"{name} addresses no row of {array}")
            continue
        if identity is None:
            decoded["notes"][control.role] = _value_of(entry)
            continue

        if kind == "relationship" and control.role in ("row", "one"):
            try:
                decoded["links"][identity] = resolve(
                    (entry.get("value") or {}).get("choices") or [], index
                )
            except Unresolved as error:
                decoded["unresolved"].append(f"{name}: no column spells {error.args[0]!r}")
            continue

        slot = decoded["rows"].setdefault(identity, {})
        if len(control.indices) > 2:
            # A nested row -- a factor level inside a term. Keyed by position,
            # because a level's identity is its position in its own term.
            slot.setdefault(control.role, {})[control.indices[2]] = _value_of(entry)
        else:
            slot[control.role] = _value_of(entry)

    return decoded


def _spans_of(entry: Mapping[str, Any], index: Mapping[str, str]) -> list[dict[str, Any]]:
    """A highlight, with each label resolved to the object it supports.

    A label under no column is kept with `local_id: None` rather than dropped: the
    passage is still evidence a reviewer marked, and losing it silently would be
    worse than reporting it unresolved. A label a reviewer *typed* -- the
    select-or-create control -- is exactly that case, and it is the finding, not an
    error.
    """

    value = entry.get("value") or {}
    if "start" not in value:
        return []
    labels = value.get("labels") or value.get("taxonomy") or []
    flat = [label[-1] if isinstance(label, list) and label else label for label in labels]
    return [
        {
            "local_id": index.get(label),
            "label": label,
            "start": value["start"],
            "end": value["end"],
            "text": value.get("text", ""),
        }
        for label in flat or [None]
    ]


# -- what the reviewer changed about the evidence ---------------------------


def spans_in(results: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "start": entry["value"]["start"],
            "end": entry["value"]["end"],
            "text": entry["value"].get("text", ""),
            "labels": entry["value"].get("labels", []),
        }
        for entry in results
        if "start" in (entry.get("value") or {})
    ]


def _overlaps(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def diff(predicted: list[dict[str, Any]], annotated: list[dict[str, Any]]) -> dict[str, list]:
    """How the reviewer changed the extractor's spans.

    Predictions and annotations are separate rows: the extractor's spans stay in
    `Prediction.result` untouched when a reviewer deletes a highlight, because
    deleting acts on the copy in `Annotation.result`. So the original is always
    recoverable and the change is a set difference.

    `adjusted` is separated from `added`/`removed` on purpose. A boundary nudge and
    a different sentence are different findings about the extractor, and lumping
    them reports one as the other.
    """

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
            (i for i, p in enumerate(predicted) if i not in matched and _overlaps(p, span)),
            None,
        )
        if near is not None:
            matched.add(near)
            out["adjusted"].append({"from": predicted[near], "to": span})
        else:
            out["added"].append(span)

    out["removed"] = [p for i, p in enumerate(predicted) if i not in matched]
    return out


# -- a whole project --------------------------------------------------------


def read_project(client: lsapi.Client, project_id: int) -> list[dict[str, Any]]:
    """Every submitted annotation in a project, decoded, with its span diff.

    Drafts are excluded: an unsubmitted answer is work in progress, and feeding it
    to reconstruction would build a record from a half-made decision.
    """

    out = []
    for stub in client.iter_tasks(project_id):
        if not stub.get("total_annotations"):
            continue
        task = client.task(stub["id"])
        predicted = spans_in(
            entry
            for prediction in task.get("predictions") or []
            if spec.ours(prediction.get("model_version"))
            for entry in prediction.get("result") or []
        )
        for annotation in task.get("annotations") or []:
            if annotation.get("was_cancelled"):
                continue
            result = annotation.get("result") or []
            entry = decode(task, result)
            entry.update(
                task_id=task["id"],
                annotation_id=annotation["id"],
                by=annotation.get("completed_by_email")
                or annotation.get("completed_by"),
                evidence=diff(predicted, spans_in(result)),
            )
            out.append(entry)
    return out
