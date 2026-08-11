#!/usr/bin/env python3
"""One judgement block per task kind, and the sample task that exercises it.

Every kind is the same five-part form:

    subject     what is being judged: a heading, a meta line, standing guidance
    extras      whatever this kind shows beyond that -- a legend, a paraphrase,
                a grid of checkboxes
    question    the one required verdict, whose values name failures
    spans       where in the paper the answer is warranted
    editor      the correction form, shut until the verdict says otherwise

The frame is written once, in `block()`. What differs per kind is two small
functions -- what goes above the question, and what goes in the editor -- and they
are the only place a kind's layout is decided.

Each kind also declares its own sample payload, right beside the block that reads
it. That is deliberate: the previous layer kept the contract, the sample and the
exporter as three parallel structures that had to be edited in lockstep, and the
sample drifted into carrying a gate the config no longer had.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree

import spec
import xmlbuild as x
from spec import Kind


def subject(kind: Kind, label: str, meta: str = "", body: str = "") -> list[dict[str, str]]:
    """The one-element array that renders this kind's block.

    A gate holds exactly one element by contract, which is why `{{i}}` inside it
    always resolves to 0 and a `whenTagName` can name a control declared in the
    same block without hardcoding an index.
    """

    return [{"label": label, "meta": meta, "body": body}]


def _gate_field(kind: Kind, name: str) -> str:
    return f"{x.item(kind.gate, 0)}.{name}"


def block(form: ElementTree.Element, kind: Kind) -> None:
    """Add one kind's gated block to a config's form column."""

    gate = x.repeater(form, f"${kind.gate}", 0)
    x.header(gate, _gate_field(kind, "label"), size="4")
    x.meta(gate, _gate_field(kind, "meta"))
    if kind.guidance:
        # Standing text, identical for every task of this kind, so it is baked into
        # the config rather than shipped on every task. It was task data once, and
        # every task of a family carried the same three sentences.
        x.panel(gate, "ns-card", kind.guidance)

    _EXTRAS[kind.name](gate, kind)

    if kind.question:
        x.header(gate, kind.question)
        x.choices(gate, kind.verdict, kind.verdicts, question=kind.question)

    if kind.span_prompt:
        # Directly under the verdict, so the question and the tool for answering it
        # are together. One layer at the foot of the form put them an editor apart.
        layer = x.naming_layer if kind.naming else x.span_layer
        layer(gate, kind.named("spans"), "$labels", kind.span_prompt)

    _EDITOR[kind.name](x.when(gate, kind.editor, kind.verdict), kind)


# -- value ------------------------------------------------------------------


def _value_extras(gate: ElementTree.Element, kind: Kind) -> None:
    """The extractor's answer, and how much evidence stands behind it.

    A Header rather than Markdown: the body is an extracted value, and a value
    containing an asterisk or an underscore is not markup.
    """

    box = x.sub(gate, "View", className="ns-llm")
    x.header(box, _gate_field(kind, "body"))


def _value_editor(editor: ElementTree.Element, kind: Kind) -> None:
    x.textarea(editor, kind.named("fix"), "corrected value")


def _value_sample(size: int) -> dict[str, Any]:
    return {
        "gate_value": subject(
            "Group pd  ·  age_mean", "Mean age of the cohort", "64.5"
        ),
        "labels": [
            {"value": name, "background": spec.PALETTE[index]}
            for index, (name, _hint) in enumerate(spec.SUPPORT_KINDS)
        ],
    }


# -- relationship -----------------------------------------------------------


def _relationship_extras(gate: ElementTree.Element, kind: Kind) -> None:
    """The grid: one row per source object, one column per candidate target.

    Judging the whole assignment at once is what makes an unused target visible as
    an empty column, which no per-source-object task can show.

    Two row Repeaters, gated on which one the task supplies, because `choice` is
    fixed in the config and a multivalued slot needs `multiple` while a
    single-valued one needs a radio. The single-valued rows also carry an explicit
    `no link` column, so "this links to nothing" is an assertion rather than an
    unanswered row -- and a radio cannot be cleared once clicked.
    """

    for role, key, multiple in (("row", "rows", True), ("one", "rows_single", False)):
        rows = x.repeater(gate, f"${key}", 1)
        card = x.sub(rows, "View", className="ns-card")
        x.header(card, f"{x.item(key, 1)}.label", size="4")
        x.meta(card, f"{x.item(key, 1)}.meta")
        x.dynamic_choices(
            card,
            kind.named(role, 2),
            "$columns",
            multiple=multiple,
            layout="inline" if multiple else "select",
        )

    # Only hard anomalies, and only when there are any. This was an unconditional
    # panel whose commonest line restated the grid directly above it. What is left
    # is the class of finding the grid cannot show: a link to an id that was never
    # extracted, or a required row with nothing in it.
    warn = x.repeater(gate, "$anomalies", 1)
    x.panel(warn, "ns-warn", f"{x.item('anomalies', 1)}.text")


def _relationship_editor(editor: ElementTree.Element, kind: Kind) -> None:
    """Nothing. The grid above is the answer.

    This kind declares no verdict and no correction form. Its ticks arrive
    pre-filled from the extraction, so submitting unchanged is already an
    assertion, and a verdict could only ask the reviewer to restate what they had
    just set. The two verdicts that said something the grid could not -- a target
    missing, a target that should not exist -- were claims about which objects
    exist, and that is the entity inventory's question.
    """


def _relationship_sample(size: int) -> dict[str, Any]:
    columns = [
        {"value": f"acq_{i} -- run {i} . fMRI", "alias": f"acq_{i}"} for i in range(size)
    ]
    return {
        "gate_relationship": subject(
            "Which Acquisitions does each Analysis use?",
            "Analysis.acquisitions -> Acquisition  ·  many per Analysis",
        ),
        "rows": [
            {"label": f"analysis {i}", "meta": f"ana_{i}", "local_id": f"ana_{i}"}
            for i in range(size)
        ],
        "rows_single": [],
        "columns": columns + [{"value": "no link", "alias": "none"}],
        "anomalies": [{"text": "- **analysis 0** links to `acq_9`, which was never extracted"}],
        "labels": [{"value": column["value"][:60], "alias": column["alias"]} for column in columns],
    }


# -- entities ---------------------------------------------------------------


def _entities_extras(gate: ElementTree.Element, kind: Kind) -> None:
    """The instance inventory, read-only.

    Each row carries a descriptor and how many things reference it, so "is this a
    real cohort?" and "what breaks if I drop it?" are both answerable here rather
    than in another task.
    """

    x.sub(gate, "Table", name=kind.named("legend"), value="$legend")


def _dispositions(editor: ElementTree.Element, kind: Kind, vocabulary) -> None:
    """One disposition per row, plus somewhere to say what it should have been."""

    x.header(editor, kind.rows)
    rows = x.repeater(editor, "$rows", 1)
    card = x.sub(rows, "View", className="ns-cell")
    x.header(card, f"{x.item('rows', 1)}.label")
    x.meta(card, f"{x.item('rows', 1)}.meta")
    x.choices(card, kind.named("row", 2), vocabulary, inline=True)
    x.textarea(card, kind.named("note", 2), "note", rows=1)


def _entities_editor(editor: ElementTree.Element, kind: Kind) -> None:
    _dispositions(editor, kind, spec.DISPOSITIONS)


def _entities_sample(size: int) -> dict[str, Any]:
    return {
        "gate_entities": subject("Group  ·  2 extracted", "2 tied to a reported result"),
        "legend": [
            {
                "id": f"grp_{i}",
                "descriptor": f"grp_{i} -- cohort {i} . n={20 - i}",
                "references": "2 links",
            }
            for i in range(size)
        ],
        "rows": [
            {
                "label": f"grp_{i}",
                "meta": f"grp_{i} -- cohort {i} . n={20 - i}  ·  referenced by 2 link(s)",
                "local_id": f"grp_{i}",
            }
            for i in range(size)
        ],
        "labels": [
            {"value": f"grp_{i}", "background": spec.PALETTE[i % len(spec.PALETTE)]}
            for i in range(size)
        ],
    }


# -- model ------------------------------------------------------------------


def _model_extras(gate: ElementTree.Element, kind: Kind) -> None:
    """Nothing above the question: the term list is the editor, and it is not gated."""


def _model_editor(editor: ElementTree.Element, kind: Kind) -> None:
    """One accordion panel per term, with its levels nested inside.

    Not hidden behind the verdict, unlike every other editor. Answering
    `terms_correct` would hide the very thing being called correct, and unlike a
    contrast -- whose cells are restated in its paraphrase -- a model's terms
    appear nowhere else on the task. The accordion keeps each to one line until
    opened, which is what makes a 16-term model readable.

    Not `mode="pagination"` either: PagedView derives its page from the object
    tag's name when a region is selected, and one shared `<Text name="paper">`
    makes that NaN.
    """

    x.header(editor, kind.rows)
    terms = x.repeater(editor, "$rows", 1)
    collapse = x.sub(terms, "Collapse", accordion="true")
    panel = x.sub(collapse, "Panel", value=f"{x.item('rows', 1)}.label")
    # The heading is the term's name and nothing else. `meta` holds only the facts
    # no control on this card repeats: the source's own definition, the unit, and
    # how many levels the factor declares.
    x.meta(panel, f"{x.item('rows', 1)}.meta")

    x.choices(panel, kind.named("row", 2), spec.TERM_VERDICTS)
    x.textarea(panel, kind.named("name", 2), "corrected name", rows=1)
    x.header(panel, "Type")
    x.choices(panel, kind.named("type", 2), spec.TERM_TYPES, layout="inline")
    x.header(panel, "Scope")
    x.choices(panel, kind.named("scope", 2), spec.VARIATION_LEVELS, layout="inline")

    levels = x.repeater(panel, f"{x.item('rows', 1)}.levels", 2)
    level = x.sub(levels, "View", className="ns-cell")
    x.header(level, f"{x.item('rows', 1)}.levels[{spec.FLAGS[2]}].label")
    x.textarea(level, kind.named("level", 3), "corrected level", rows=1)
    # `Number` takes no hint (`tags/control/Number.jsx` documents min/max/step/
    # hotkey/required/perRegion/slider only), so an unlabelled input would give the
    # reviewer no idea what it is for. The Header is the label.
    x.header(level, "Order")
    x.number(level, kind.named("order", 3))


def _model_sample(size: int) -> dict[str, Any]:
    return {
        "gate_model": subject(
            "glm_0  ·  2 terms", "GLM  ·  ordinary least squares  ·  SPM  ·  group"
        ),
        "rows": [
            {
                "label": f"term {i}",
                "meta": f"definition of term {i}  ·  2 level(s) declared",
                "local_id": f"trm_{i}",
                "levels": [{"label": f"level {j}", "level": f"level {j}"} for j in range(size)],
            }
            for i in range(size)
        ],
        "labels": [
            {"value": f"term: term {i}", "background": spec.PALETTE[i % len(spec.PALETTE)]}
            for i in range(size)
        ],
    }


# -- table ------------------------------------------------------------------


def _table_extras(gate: ElementTree.Element, kind: Kind) -> None:
    """Nothing. The rendered grid is declared once for the whole project, above."""


def _table_editor(editor: ElementTree.Element, kind: Kind) -> None:
    """A disposition per parsed analysis, numbered to match the grid's gutter.

    No per-row question. Restating a 77-row grid as 77 radio groups underneath it
    asks the reviewer to hold two views in their head and match them by
    coordinate, which is more work than the correction is worth -- and the answer
    it collects is one the grid already displays. A row-level correction is worth
    having only when it can be made *on* the grid, which needs a control Label
    Studio does not offer over a HyperText.

    No "missing analyses" box either. A missed analysis is reported by drawing its
    span above and typing the name, which carries the warrant with it; asking for
    the name a second time in prose is double entry that can only disagree with
    itself.
    """

    _dispositions(editor, kind, spec.ANALYSIS_DISPOSITIONS)


def _table_sample(size: int) -> dict[str, Any]:
    return {
        "gate_table": subject(
            "Table 3  ·  2 analyses parsed",
            "3 of 9 rows could not be attributed to one analysis",
        ),
        "rows": [
            {
                "label": f"#{i + 1}  ·  PwPD . HC",
                "meta": "3 point(s)  ·  3 row(s) attributed  ·  encoded as ana_0",
            }
            for i in range(size)
        ],
        "labels": [
            {"value": f"analysis: PwPD . HC {i}", "background": spec.PALETTE[i]}
            for i in range(size)
        ],
    }


# -- contrast ---------------------------------------------------------------


def _contrast_extras(gate: ElementTree.Element, kind: Kind) -> None:
    """The record rendered back into one sentence.

    The primary judgement: accepting is one click, which is what makes 5-9
    analyses a paper affordable. No "the paper says" panel beside it -- the
    evidence is pre-highlighted in the paper pane and Alt+. cycles to it, so
    quoting it here would print the same passage twice, once out of context. The
    sentences either side are what settle whether it supports the record, and an
    excerpt hides exactly those.
    """

    x.meta(gate, "The record says")
    x.panel(gate, "ns-para", _gate_field(kind, "body"))


def _contrast_editor(editor: ElementTree.Element, kind: Kind) -> None:
    """A direction per term, then the statistic if the record holds one.

    The rows are the model's term-and-level inventory, so a cell can only ever name
    a term the model declares -- the foreign key is enforced by the widget. Every
    row carries `absent`, which makes a term adjusted for rather than tested an
    assertion instead of an oversight, and `unstated`, which is what an omnibus F
    reports.
    """

    x.header(editor, kind.rows)
    rows = x.repeater(editor, "$rows", 1)
    card = x.sub(rows, "View", className="ns-cell")
    x.header(card, f"{x.item('rows', 1)}.label")
    x.choices(card, kind.named("row", 2), spec.DIRECTIONS, layout="inline")
    x.textarea(card, kind.named("label", 2), "source wording", rows=1)

    # Gated, because an analysis whose record holds no statistic has nothing here
    # to judge, and a radio group over an empty summary reads as a question the
    # reviewer skipped rather than one that never applied.
    stat = x.repeater(editor, "$statistic", 1)
    x.panel(stat, "ns-card", f"{x.item('statistic', 1)}.summary")
    x.dynamic_choices(stat, kind.named("stat", 2), "$options", inline=True)
    # Only once something has been called wrong, and it says what to write rather
    # than naming the field again: a statistic is a family and two degrees of
    # freedom, so "corrected statistic" would not say which of them was wanted.
    # `choice-selected` over the wrong verdicts rather than `choice-unselected`
    # over the right one, because the latter is true before anything is answered
    # and the box would greet the reviewer empty and unexplained.
    wrong = tuple(v for v in spec.STATISTIC_VERDICTS if v != "statistic_correct")
    fix = x.when(stat, spec.Editor("when", wrong), kind.named("stat", 2))
    x.textarea(fix, kind.named("statnote", 2), "what the paper reports, e.g. F(1,57) or t(33)", rows=1)


def _contrast_sample(size: int) -> dict[str, Any]:
    return {
        "gate_contrast": subject(
            "ana_0  ·  PwPD vs HC",
            "Table 3 · analysis 2 of 4 · 9 points",
            "**PwPD** vs **HC**\n\n- measure: contrast estimate\n- reported in: Table 3",
        ),
        "rows": [{"label": f"group : cohort {i}"} for i in range(size)],
        "statistic": [{"summary": "**t**  ·  df 33  ·  glm_0"}],
        "options": [
            {"value": value, "hint": hint} for value, hint in spec.STATISTIC_VERDICTS.items()
        ],
        "labels": [{"value": "definition", "background": spec.PALETTE[1]}],
    }


# -- adjudication -----------------------------------------------------------


def _adjudication_extras(gate: ElementTree.Element, kind: Kind) -> None:
    """The diff, then the two readings side by side.

    Canonical forms, not raw results: agreement on a structural task is computed on
    the sorted cell set, or control ordering reads as disagreement. The adjudicator
    sees two readings, not two result blobs.
    """

    x.panel(gate, "ns-warn", _gate_field(kind, "body"))
    side = x.sub(gate, "View", className="ns-side")
    for name, key in (("Reviewer A", "$left_md"), ("Reviewer B", "$right_md")):
        half = x.sub(side, "View", className="ns-half")
        x.meta(half, name)
        x.sub(half, "Markdown", value=key)


def _adjudication_editor(editor: ElementTree.Element, kind: Kind) -> None:
    x.textarea(editor, kind.named("fix"), "Corrected form", rows=4)


def _adjudication_sample(size: int) -> dict[str, Any]:
    return {
        "gate_adjudication": subject(
            "Analysis . ana_0 . effect.cells",
            "contrast  ·  2 cells disputed",
            "- direction inverted on both cells",
        ),
        "left_md": "group:PwPD **+**, group:HC **-**",
        "right_md": "group:PwPD **-**, group:HC **+**",
    }


# -- the register -----------------------------------------------------------

_EXTRAS = {
    "value": _value_extras,
    "relationship": _relationship_extras,
    "entities": _entities_extras,
    "model": _model_extras,
    "table": _table_extras,
    "contrast": _contrast_extras,
    "adjudication": _adjudication_extras,
}

_EDITOR = {
    "value": _value_editor,
    "relationship": _relationship_editor,
    "entities": _entities_editor,
    "model": _model_editor,
    "table": _table_editor,
    "contrast": _contrast_editor,
    "adjudication": _adjudication_editor,
}

_SAMPLE = {
    "value": _value_sample,
    "relationship": _relationship_sample,
    "entities": _entities_sample,
    "model": _model_sample,
    "table": _table_sample,
    "contrast": _contrast_sample,
    "adjudication": _adjudication_sample,
}


def sample(kind: Kind, size: int = 2) -> dict[str, Any]:
    """The kind-specific half of a sample task.

    `size` is how many repeated rows to build. Two is enough for two iterations to
    collide on a name if the config gets that wrong.
    """

    return _SAMPLE[kind.name](size)
