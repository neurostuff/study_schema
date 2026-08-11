#!/usr/bin/env python3
"""Assemble a project's labeling config, and derive what its tasks must carry.

A config is a stylesheet and two columns: the paper on the left with a chat box
under it, and on the right one gated block per task kind the project holds, then a
notes box. Everything specific to a kind is in `blocks.py`; everything here is the
frame every kind shares.

## The contract is derived, not declared

`contract(project)` reads the generated XML and reports every task-data key it
interpolates, and whether that key must hold an array or a string. The exporter is
checked against it, so the two cannot drift.

That replaces a hand-written dictionary of prose descriptions that had to be
edited in lockstep with both the config and the exporter, and which had gone stale
in both directions. It also makes one platform rule enforceable rather than
remembered: Label Studio records a key's data type the first time a config using
it is saved, so `table_html` must be a string on *every* task in its project --
including the ones with no grid, and including the PATCH the sync issues.

## The sample task

`sample_task(kind)` builds a complete task of that kind from the same block
functions that emit the XML. It is what the tests expand the Repeaters against, so
they can check the form a reviewer actually gets -- which Label Studio's own
validation cannot do, because the server only ever sees the unexpanded config.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import blocks
import spec
import style
import xmlbuild as x

#: Keys every task carries whatever its kind, none of which the XML interpolates.
#: Identity for the sync and the decoder, triage for the Data Manager. They are
#: uniform across projects on purpose: a view filtering on `data.priority` must not
#: break when it is pointed at a project whose tasks happen not to have one.
SHARED: dict[str, str] = {
    # identity
    "paper_id": "string",
    "review_key": "string",
    "content_hash": "string",
    "stage": "number",
    "task_kind": "string",
    "paper_text_hash": "string",
    # triage
    "priority": "any",
    "coordinate_status": "string",
    "entity_class": "string",
    "local_id": "string",
    "field_path": "string",
    "table_id": "string",
    "row_count": "number",
    "llm_status": "string",
    "evidence_status": "string",
    "rel_slot": "string",
    "dispute_kind": "string",
}

#: Tags whose `value` attribute names an array of options or rows rather than a
#: string to render. Everything else that interpolates is a string.
_ARRAY_TAGS = {"Labels", "Choices", "Table", "Taxonomy"}

_INTERPOLATION = re.compile(r"\$[\w\[\].{}]+")


def _paper_pane(row: ElementTree.Element) -> None:
    """The paper, and the chat box pinned under it.

    Three attributes on the `<Text>` are load-bearing:

      valueType="url"       keeps the text out of the task JSON. One paper is
                            25-60 KB and carries hundreds of tasks; inlining it
                            would produce ~18 MB of task JSON per paper, and the
                            browser fetches the URL once and serves the rest of
                            that paper's tasks from cache.
      saveTextResult="yes"  `RichTextRegion` only emits `value.text` when it is
                            set (`regions/RichTextRegion.js:116`) and it defaults
                            to "none" (`RichText/model.js:62`). Omit it and drawn
                            spans come back with no text -- which is the half of a
                            span that survives the text being restaged.
      granularity="symbol"  character-exact selection instead of word-snapped.
    """

    pane = x.sub(row, "View", className="ns-paper")
    x.header(pane, "$paper_title")
    x.meta(pane, "$paper_citation")
    body = x.sub(pane, "View", className="ns-paper-body")
    x.sub(
        body,
        "Text",
        name=spec.PAPER,
        value="$paper_url",
        valueType="url",
        saveTextResult="yes",
        granularity="symbol",
    )
    _chat_pane(pane)


def _chat_pane(pane: ElementTree.Element) -> None:
    """The question box, and the log of answers it has produced.

    Two TextAreas rather than a chat widget, because a chat widget needs
    JavaScript in the labeling interface and that is an Enterprise feature. What
    open source gives instead is the interactive-preannotation round trip, and
    these two controls are its ends:

      chat_q  the only `smart` control in any of these configs. Submitting one
              fires `regionFinishedDrawing`, which the Data Manager turns into
              POST /api/ml/<pk>/interactive-annotating carrying every textarea
              region on the paper as context (`DataManager.jsx:157-191`).
      chat_a  written only by the backend's reply. `<Text>` has
              `supportSuggestions: false`, and a suggestion an object tag cannot
              display is accepted immediately rather than waiting for a click
              (`Annotation.js:1183-1192`), so the answer lands in the annotation
              with no reviewer action. That is the point: what a curator had to ask
              before deciding exports with the decision instead of evaporating in a
              browser tab.

    `maxSubmissions="0"` on the answer log hides its own input box
    (`TextArea.jsx:133-140`: `submissionsNum < 0` is never true) without blocking
    deserialization, which does not go through the submit path. The reviewer can
    delete an entry; they cannot type one.

    A TextArea renders as one block -- its input, then everything submitted to it
    -- so declaration order alone gives answers, then the box, then the questions.
    The exchange has to read question, answer, question, answer with the typing bar
    at the bottom, which means splitting the question box from the question log.
    The two wrappers exist to be flattened by `display: contents`; see `style.py`.
    Declared question-first so the DOM order matches the reading order for anything
    that ignores `order`.

    `Collapse`/`Panel` is a visual tag: it holds no name and produces no result, so
    the open/closed state stays out of the annotation, which a `Choices` gate with
    `visibleWhen` would not.
    """

    box = x.sub(pane, "View", className="ns-chat")
    collapse = x.sub(box, "Collapse", accordion="true")
    panel = x.sub(collapse, "Panel", value="Ask about this paper", open="true")
    body = x.sub(panel, "View", className="ns-chat-body")

    asked = x.sub(body, "View", className="ns-chat-q")
    x.sub(
        asked,
        "TextArea",
        name=spec.CHAT_QUESTION,
        toName=spec.PAPER,
        rows="2",
        editable="true",
        smart="true",
        showSubmitButton="false",
        placeholder="Ask a question, then Shift+Enter. Saved with your review.",
    )
    answered = x.sub(body, "View", className="ns-chat-a")
    x.sub(
        answered,
        "TextArea",
        name=spec.CHAT_ANSWER,
        toName=spec.PAPER,
        rows="6",
        maxSubmissions="0",
        placeholder="Answers appear here, oldest first.",
    )


def _grid(form: ElementTree.Element) -> None:
    """The rendered coordinate table, declared once for the whole project.

    `inline="true"` is load-bearing rather than cosmetic: without it the value
    renders into an iframe with its own document, which the stylesheet cannot
    reach, and the table comes back unstyled with nothing to say why.

    Nothing points at it with a `toName`, so it draws no regions: it is evidence to
    read beside the paper, and the paper pane stays the only place a span is drawn.
    """

    host = x.sub(form, "View", className="ns-tbl-host")
    x.sub(
        host,
        "HyperText",
        name="table_html",
        value="$table_html",
        inline="true",
        selectionEnabled="false",
        clickableLinks="false",
    )


def build(project: spec.Project) -> str:
    """Render one project's labeling config."""

    root = ElementTree.Element("View")
    stylesheet = x.sub(root, "Style")
    stylesheet.text = style.stylesheet()

    row = x.sub(root, "View", className="ns-row")
    _paper_pane(row)
    form = x.sub(row, "View", className="ns-form")

    if project.grid:
        _grid(form)
    for kind in project.blocks:
        blocks.block(form, kind)

    x.header(form, "Notes")
    x.textarea(form, "comment", "")

    x.mute_smart(root)
    return x.render(root)


def interpolated(config: str) -> dict[str, str]:
    """Every task-data key the config reads, and what shape it must hold.

    Mirrors `parseValue`'s own regex (`utils/data.js:13`) and then strips the index
    path, so `$rows[{{j}}].label` reports as `rows` -- the unit the exporter
    populates. A key is an array when it is repeated over, when it feeds a tag that
    takes its children from data, or when anything indexes into it; a string
    otherwise.
    """

    root = ElementTree.fromstring(config)
    shapes: dict[str, str] = {}
    for node in root.iter():
        for attribute, value in node.attrib.items():
            for match in _INTERPOLATION.findall(value or ""):
                token = match[1:]
                key = re.split(r"[\[.]", token, maxsplit=1)[0]
                array = (
                    attribute == "on"
                    or token != key
                    or (node.tag in _ARRAY_TAGS and attribute == "value")
                )
                if array or key not in shapes:
                    shapes[key] = "array" if array else "string"
    return shapes


def contract(project: spec.Project) -> dict[str, str]:
    """Every key a task of this project must carry, and its shape."""

    return {**SHARED, **interpolated(build(project))}


def default_for(shape: str) -> Any:
    return [] if shape == "array" else "" if shape == "string" else 0


def sample_task(kind_name: str, size: int = 2) -> dict[str, Any]:
    """A complete task of one kind, populating every contracted key.

    Built from the contract outwards: every key the project's config reads gets an
    empty value of the right shape, then the identity keys, then the kind's own
    payload. A key the block forgot therefore shows up as an empty form rather than
    a crash, and a key the block invents shows up as a contract violation.
    """

    kind = spec.BY_NAME[kind_name]
    project = spec.PROJECT_OF[kind_name]
    task: dict[str, Any] = {
        key: default_for(shape) for key, shape in contract(project).items()
    }
    task.update(
        paper_id="HU6mqxmtySg3",
        review_key=f"HU6mqxmtySg3|{kind.name}|sample",
        content_hash="0" * 16,
        stage=kind.stage,
        task_kind=kind.name,
        paper_text_hash="0" * 64,
        paper_url="/data/local-files/?d=texts/HU6mqxmtySg3.txt",
        paper_title="HU6mqxmtySg3",
        paper_citation="HU6mqxmtySg3  ·  pmid 0  ·  10.0/0",
        coordinate_status="yes",
        priority=0,
        row_count=size,
    )
    if project.grid:
        # Deliberately tiny. A sample task is capped at 4 KB by the test that keeps
        # the paper out of task JSON, and that cap is what would catch someone
        # pasting a real 20 KB rendered table in here.
        task["table_html"] = (
            '<div class="ns-table"><table class="ns-tbl">'
            "<thead><tr><th></th><th>x</th></tr></thead>"
            '<tbody><tr class="ns-a0"><td class="ns-gut">1</td>'
            '<td class="ns-coord ns-num">-58</td></tr></tbody></table></div>'
        )
    task.update(blocks.sample(kind, size))

    missing = set(contract(project)) - set(task)
    extra = set(task) - set(contract(project))
    if missing or extra:
        raise AssertionError(
            f"sample_task({kind_name!r}) does not match the contract: "
            f"missing {sorted(missing)}, extra {sorted(extra)}"
        )
    return task


def write(out_dir: Path) -> list[tuple[Path, int]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for project in spec.PROJECTS:
        path = out_dir / project.config_file
        path.write_text(build(project), encoding="utf-8")
        written.append((path, len(contract(project))))
    return written
