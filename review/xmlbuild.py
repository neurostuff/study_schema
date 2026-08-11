#!/usr/bin/env python3
"""Element helpers for building a labeling config.

Thin on purpose: these are the tags with a rule attached. Anything without a rule
is one `sub()` call at the site that needs it, because a wrapper that only renames
`SubElement` hides where the tree is being built.

The rules encoded here are the ones whose violation is silent in the editor:

  * `Header` supports inline `style` but **not** `className`
    (`tags/visual/Header.jsx` documents value/size/style/underline only). A
    className there is dropped and the text renders full-weight.
  * `Markdown` is not a legal `Panel` child, so it is always wrapped in a `View`.
  * A required control needs a `requiredMessage` naming its question. On failure
    Label Studio calls `requiredModal()` and shows that string, and does nothing
    else -- there is no scroll-to and no highlight for a whole-object control
    (`mixins/Required.js:93-97`). A generic "answer this first" leaves the
    reviewer hunting a long form for whatever is blank.
  * `smart` defaults to **true** on every control (`tags/control/Base.js:16`), so
    it is swept off at the end rather than set per tag.
"""

from __future__ import annotations

from xml.etree import ElementTree

import spec
from spec import Editor, Vocabulary

#: Secondary text under a heading. Inline because `Header` has no className.
#: `Tree.cssConverter` splits on ";" then the first ":", so a `var()` survives
#: intact -- a value containing a semicolon would not.
META_STYLE = "color: var(--color-neutral-content-subtler); font-weight:400; margin:4px 0"


def sub(parent: ElementTree.Element, tag: str, **attrs: str) -> ElementTree.Element:
    return ElementTree.SubElement(parent, tag, dict(attrs))


def header(parent: ElementTree.Element, value: str, size: str = "5") -> ElementTree.Element:
    return sub(parent, "Header", value=value, size=size)


def meta(parent: ElementTree.Element, value: str) -> ElementTree.Element:
    return sub(parent, "Header", value=value, size="5", style=META_STYLE)


def panel(parent: ElementTree.Element, css_class: str, value: str) -> ElementTree.Element:
    """A markdown block in a styled card.

    `Markdown` carries no `name` of its own (`tags/visual/Markdown.jsx` has only an
    auto id), so repeated copies need no index flag.
    """

    box = sub(parent, "View", className=css_class)
    sub(box, "Markdown", value=value)
    return box


def textarea(
    parent: ElementTree.Element,
    name: str,
    placeholder: str,
    rows: int = 2,
    **attrs: str,
) -> ElementTree.Element:
    return sub(
        parent,
        "TextArea",
        name=name,
        toName=spec.PAPER,
        rows=str(rows),
        editable="true",
        maxSubmissions="1",
        placeholder=placeholder,
        **attrs,
    )


def choices(
    parent: ElementTree.Element,
    name: str,
    vocabulary: Vocabulary,
    *,
    question: str = "",
    inline: bool = False,
    layout: str = "",
) -> ElementTree.Element:
    """A single-select control, required exactly when it carries a question.

    The question doubles as the requiredMessage, so the two cannot drift: a
    reviewer told to "Answer 'Is this right?'" is pointed at wording that appears
    verbatim above the control.
    """

    attrs = {
        "name": name,
        "toName": spec.PAPER,
        "choice": "single-radio",
        "showInline": "true" if inline else "false",
    }
    if layout:
        attrs["layout"] = layout
    if question:
        attrs["required"] = "true"
        attrs["requiredMessage"] = f"Answer '{question}'"
    node = sub(parent, "Choices", **attrs)
    for value, hint in vocabulary:
        sub(node, "Choice", value=value, hint=hint)
    return node


def dynamic_choices(
    parent: ElementTree.Element,
    name: str,
    value: str,
    *,
    multiple: bool = False,
    inline: bool = False,
    layout: str = "",
) -> ElementTree.Element:
    """A select whose *options* come from the task, not the config.

    For a question whose answers depend on what the record holds rather than only
    its subject -- the statistic block, where offering `df_wrong` on a record with
    no degrees of freedom asks about a value that was never there. `Choices`
    carries DynamicChildrenMixin and a `value` attribute for exactly this, and the
    option's `alias` is what the annotation stores.
    """

    attrs = {
        "name": name,
        "toName": spec.PAPER,
        "choice": "multiple" if multiple else "single-radio",
        "value": value,
    }
    if layout:
        attrs["layout"] = layout
    else:
        attrs["showInline"] = "true" if inline else "false"
    return sub(parent, "Choices", **attrs)


def number(parent: ElementTree.Element, name: str, minimum: str = "1") -> ElementTree.Element:
    return sub(parent, "Number", name=name, toName=spec.PAPER, min=minimum)


def repeater(parent: ElementTree.Element, on: str, depth: int) -> ElementTree.Element:
    """A block repeated once per element of `on`, indexed by this depth's flag.

    `on` resolves through `parseValue`, and a missing key yields "" which `|| []`
    turns into zero iterations (`core/Tree.tsx:70`) -- which is what makes an empty
    array a gate rather than an empty form.
    """

    return sub(parent, "Repeater", on=on, indexFlag=spec.FLAGS[depth])


def item(key: str, depth: int) -> str:
    """`$key[{{flag}}]` -- one element of a repeated task-data array."""

    return f"${key}[{spec.FLAGS[depth]}]"


def when(
    parent: ElementTree.Element, editor: Editor, verdict: str
) -> ElementTree.Element:
    """The correction form, shown or hidden by the verdict above it.

    `visibleWhen` reads only choices and regions, never task data
    (`tags/visual/View.jsx:62-65`), so this is the only conditional available.
    `whenChoiceValue` is split on commas (`mixins/Visibility.js:57`).
    """

    if editor.mode == "always":
        return sub(parent, "View")
    return sub(
        parent,
        "View",
        visibleWhen=editor.visible_when,
        whenTagName=verdict,
        whenChoiceValue=",".join(editor.values),
    )


def span_layer(parent: ElementTree.Element, name: str, value: str, prompt: str) -> None:
    """Dynamic labels over the paper text.

    The label set comes from task data, so the structure under review *is* the
    label set: one label per field, row or object. A warrant becomes visible in the
    text rather than described beside it -- drawing a span and picking a label is
    one gesture, deleting a highlight is how you deny one.

    Declaring no static `<Label>` children also removes a whole class of silent
    failure: the exporter used to be able to emit a label the config had not
    declared, which the server accepted with HTTP 201 and the editor then failed to
    render, with no error and no highlight.

    Alt+. cycles the highlights, which is how a span near the end of a 25-60 KB
    document is reached (`region:cycle` -> `selectNext` -> `scrollIntoView`).
    """

    header(parent, prompt)
    sub(parent, "Labels", name=name, toName=spec.PAPER, value=value, showInline="false")


def naming_layer(parent: ElementTree.Element, name: str, value: str, prompt: str) -> None:
    """A span layer whose label set the reviewer can extend while annotating.

    `<Labels>` can only offer what the exporter put in the task, which is right
    when the label set is the structure under review and fatally limiting when the
    point is to report what the structure *lacks*. A single `+ new ...` pseudo-label
    cannot represent two missing things at all: both spans come back wearing it,
    indistinguishable.

    A Taxonomy in labeling mode draws regions exactly as Labels does -- the result
    attaches to an area, so the region still serialises with `start`, `end` and
    `text` and every span tool goes on working. What it adds is `userLabels`: the
    reviewer types the name of the thing they found, as many times as the paper
    needs, and the name they typed is what lands in the result.

    `legacy="true"` is what exposes the add control, and it costs only `apiUrl`,
    which nothing here uses. `value=` still supplies the known names through
    DynamicChildrenMixin, so picking an existing one stays a single click.
    """

    header(parent, prompt)
    sub(
        parent,
        "Taxonomy",
        name=name,
        toName=spec.PAPER,
        value=value,
        labeling="true",
        legacy="true",
        leafsOnly="true",
        showFullPath="false",
        placeholder="Pick one, or type a name to add one",
    )


def mute_smart(root: ElementTree.Element) -> None:
    """Turn off `smart` on every control that has not asked for it.

    `smart` defaults to true on every control tag (`tags/control/Base.js:16`) and
    `smartEnabled` is `smart && store.autoAnnotation` (`:62-66`). With
    Auto-Annotation on -- which the chat requires -- any region whose results
    include a smart control fires an interactive round trip. That is not only the
    comment box: drawing a span on the paper notifies too
    (`RichText/model.js:427`), and so does deleting one. Highlighting evidence is
    the most frequent thing a reviewer does here, so leaving the span layer smart
    means an LLM call per highlight, each arriving with no question to answer.

    A sweep over the finished tree rather than an argument on each helper, because
    the failure is silent and the cost is per click: a control added later without
    the attribute is muted by default, and turning it on has to be deliberate.
    `toName` is what identifies a control -- an object tag carries `value`, a
    visual tag carries neither -- so this needs no list of tag names to keep in step.
    """

    for node in root.iter():
        if node.get("toName") and "smart" not in node.attrib:
            node.set("smart", "false")


def render(root: ElementTree.Element) -> str:
    ElementTree.indent(root, space="  ")
    return ElementTree.tostring(root, encoding="unicode") + "\n"
