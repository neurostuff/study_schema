"""Checks for the Label Studio review layer: the registry, the configs, the export.

Almost every rule here exists because breaking it fails *silently*. Label Studio
validates the unexpanded config, accepts a prediction naming a label it will not
render, stores a view whose operator the browser cannot parse, and answers a task
whose text URL 404s with an empty pane. So the checks are less about correctness in
the abstract and more about the specific things that go wrong without saying so.

`test_review_foundation.py` covers what this is built on -- text, spans, records,
tables -- and knows nothing about Label Studio.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parent
REVIEW = ROOT / "review"
sys.path.insert(0, str(REVIEW))

import answers  # noqa: E402
import blocks  # noqa: E402
import chat  # noqa: E402
import config  # noqa: E402
import lint  # noqa: E402
import record as record_module  # noqa: E402
import spec  # noqa: E402
import staging  # noqa: E402
import style  # noqa: E402
import tables  # noqa: E402
import tasks as tasks_module  # noqa: E402
import text_index  # noqa: E402
import xmlbuild  # noqa: E402

PAPER = "HU6mqxmtySg3"
RECORD = REVIEW / "examples" / f"{PAPER}.extraction.json"
TEXT = REVIEW / "texts" / PAPER / "processed" / "local" / "text.tables.txt"

requires_paper = pytest.mark.skipif(
    not (RECORD.is_file() and TEXT.is_file()),
    reason="the example paper's record or text is not present",
)

VARIANTS = [(project, kind) for project, kind in lint.variants()]
VARIANT_IDS = [f"{project.name}/{kind.name}" for project, kind in VARIANTS]


@pytest.fixture(scope="module", params=spec.PROJECTS, ids=[p.name for p in spec.PROJECTS])
def project_config(request):
    project = request.param
    return project, ElementTree.fromstring(config.build(project))


@pytest.fixture(scope="module")
def ls_schema():
    schema = lint.load_schema()
    if schema is None:
        pytest.skip(f"no Label Studio checkout at {lint.SCHEMA}")
    return schema


@pytest.fixture(scope="module")
def exported():
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    normalized = text_index.normalize(TEXT.read_text(encoding="utf-8"))
    exporter = tasks_module.Exporter(
        record, normalized, PAPER, {}, staging.url_for(PAPER)
    )
    root = REVIEW / "texts" / PAPER
    exporter.load_tables(
        root / "source/pubget", root / "stage1/analyses.json", root / "stage1/table-map.json"
    )
    exporter.run()
    return exporter


def controls(root: ElementTree.Element) -> list[str]:
    return [node.get("name") for node in root.iter() if node.get("toName")]


# -- the registry -----------------------------------------------------------


def test_every_kind_is_answered_in_exactly_one_project() -> None:
    """The registry is the only place this is written down, so it has to be total.

    Six copies of it drifted before: a Data Manager view was built for a task kind
    that no longer existed, and the per-family overlap was written twice under a
    comment claiming it was derived once.
    """

    placed = [name for project in spec.PROJECTS for name in project.kinds]
    assert sorted(placed) == sorted(kind.name for kind in spec.KINDS)
    assert len(placed) == len(set(placed))
    for kind in spec.KINDS:
        assert spec.PROJECT_OF[kind.name].name in {p.name for p in spec.PROJECTS}


def test_a_control_name_survives_a_round_trip() -> None:
    """Generation and decoding are one grammar, or answers decode into the wrong row."""

    for kind in spec.KINDS:
        for depth in (1, 2, 3):
            declared = spec.control(kind.name, "row", depth)
            expanded = declared
            for index, flag in enumerate(spec.FLAGS[:depth]):
                expanded = expanded.replace(flag, str(index))
            parsed = spec.parse_control(expanded)
            assert parsed is not None, expanded
            assert parsed.kind == kind.name
            assert parsed.role == "row"
            assert parsed.indices == tuple(range(depth))


def test_a_name_the_grammar_did_not_generate_reads_as_none() -> None:
    """`chat_q` splits into a kind and a role and is neither, which is why the kind
    is checked against the register rather than merely matched."""

    assert spec.parse_control(spec.CHAT_QUESTION) is None
    assert spec.parse_control(spec.CHAT_ANSWER) is None
    assert spec.parse_control("comment") is None
    assert spec.parse_control(spec.PAPER) is None
    assert spec.parse_control("") is None


def test_a_role_that_could_not_be_parsed_back_is_refused_at_generation() -> None:
    """An underscore in a kind or a role would make the index split ambiguous."""

    with pytest.raises(ValueError):
        spec.control("value", "corrected_value")
    with pytest.raises(ValueError):
        spec.control("my_kind", "verdict")


def test_the_stages_are_the_invalidation_order() -> None:
    """Stage 0 changes nodes, stage 1 edges or leaves, stage 2 reads both.

    The order is derived from what a correction can invalidate, not chosen, and it
    is the import order, the invalidation order and the decoder's replay order at
    once. See staged-validation.md.
    """

    stages = {kind.name: kind.stage for kind in spec.KINDS}
    assert stages["entities"] == 0 and stages["table"] == 0
    assert stages["value"] == 1 and stages["relationship"] == 1
    assert stages["model"] == 2 and stages["contrast"] == 2


def test_only_the_relationship_grid_asks_no_question() -> None:
    """Its ticks arrive pre-filled from the extraction, so submitting unchanged is
    already an assertion and a verdict could only ask the reviewer to restate it.
    Everywhere else a task can be submitted untouched, and the verdict is what
    separates "checked and correct" from "never looked at"."""

    verdictless = {kind.name for kind in spec.KINDS if not kind.question}
    assert verdictless == {"relationship"}


def test_every_downstream_kind_can_kick_a_task_back_upstream() -> None:
    """Stages are advisory, so a reviewer who reaches a stage-2 task before its
    stage-0 one must be able to park it rather than invent a repair that the
    inventory will undo."""

    for name in ("model", "contrast"):
        values = {value for value, _hint in spec.BY_NAME[name].verdicts}
        assert "upstream_wrong" in values


def test_a_cell_can_say_the_record_does_not_say() -> None:
    """Both F-test main effects in the baseline corpus carry `direction: unstated`.
    A grid offering only positive/negative/absent folded them onto `absent`, which
    renders an omnibus test as "every term adjusted for, none tested"."""

    directions = {value for value, _hint in spec.DIRECTIONS}
    assert {"unstated", "not_applicable"} <= directions


def test_every_verdict_names_a_failure_and_explains_itself() -> None:
    """A vocabulary of correct/wrong pushes the diagnosis into free text, where
    nothing can count it."""

    for kind in spec.KINDS:
        for value, hint in kind.verdicts:
            assert value.islower() and " " not in value, value
            assert hint and hint[0].isupper(), (value, hint)


def test_predictions_are_ours_by_the_separator_not_the_model() -> None:
    """Re-running the extractor under a different model made every live prediction
    unrecognisable: the stale rows were kept as another producer's, the new ones
    written beside them, and each task ended up with two sets of spans at two sets
    of offsets."""

    assert spec.ours(spec.model_version("gpt-5.6-luna", "0.1.0"))
    assert spec.ours(spec.model_version("claude-opus-5", "0.1.0"))
    assert not spec.ours("ns-chat gpt-5.6-luna effort=low")
    assert not spec.ours(None)
    assert not spec.ours("")


def test_the_chat_backend_never_stamps_a_prediction_as_ours() -> None:
    """The sync deletes our predictions before rewriting them. If the chat's stamp
    read as ours, the exchange the annotation is supposed to carry would go with
    them."""

    backend = chat.Chat("a/model", "low", REVIEW / "ls_files", 1.0, 10)
    assert not spec.ours(backend.model_version)


def test_a_filter_the_browser_cannot_parse_is_refused_here() -> None:
    """The server does not check the operator at all: a view carrying an unknown one
    is stored with HTTP 201 and then fails the Data Manager's union type at init,
    before any view is selected, hanging the whole project with nothing in the log.
    `in_list` shipped that way and took the structure project down."""

    with pytest.raises(ValueError, match="not a Data Manager operator"):
        spec.Filter("priority", "in_list", [0, 1])
    assert spec.Filter("priority", "equal", 0, "Number").as_payload() == {
        "filter": "filter:tasks:data.priority",
        "operator": "equal",
        "type": "Number",
        "value": 0,
    }


def test_every_generated_view_filters_on_a_key_every_task_carries() -> None:
    """A view on a key only some tasks have is silently empty, which reads as "no
    work here" rather than "wrong filter"."""

    for project in spec.PROJECTS:
        contract = config.contract(project)
        for view in spec.views(project):
            for entry in view.filters:
                assert entry.key in contract, (project.name, view.title, entry.key)
            for column in view.columns:
                assert column.removeprefix("tasks:data.") in contract


# -- the labeling config ----------------------------------------------------


def test_the_config_is_well_formed_with_unique_names(project_config, ls_schema) -> None:
    project, _root = project_config
    assert lint.check(config.build(project), ls_schema) == []


@pytest.mark.parametrize("project, kind", VARIANTS, ids=VARIANT_IDS)
def test_the_expanded_config_still_passes_the_schema(project, kind, ls_schema) -> None:
    """Label Studio only ever validates the unexpanded form, so this is the half of
    the config nothing upstream checks."""

    expanded = ElementTree.tostring(lint.expanded(project, kind), encoding="unicode")
    assert lint.check(expanded, ls_schema) == []


def test_the_paper_is_never_inlined(project_config) -> None:
    """One paper is 25-60 KB and carries hundreds of tasks. Three attributes are
    load-bearing: valueType keeps the text out of the task, saveTextResult is what
    makes a drawn span carry its quote, granularity makes selection character-exact
    rather than word-snapped."""

    _project, root = project_config
    texts = [node for node in root.iter() if node.tag == "Text"]
    assert len(texts) == 1
    (paper,) = texts
    assert paper.get("name") == spec.PAPER
    assert paper.get("value") == "$paper_url"
    assert paper.get("valueType") == "url"
    assert paper.get("saveTextResult") == "yes"
    assert paper.get("granularity") == "symbol"


def test_no_span_layer_declares_its_labels_statically(project_config) -> None:
    """The structure under review IS the label set, so it comes from task data. It
    also removes a silent failure: the exporter could emit a label the config had
    not declared, which the server accepted with HTTP 201 and the editor then failed
    to render, with no error and no highlight."""

    _project, root = project_config
    for node in root.iter():
        if node.tag in ("Labels", "Taxonomy"):
            assert (node.get("value") or "").startswith("$"), node.get("name")
            assert list(node) == [], f"{node.get('name')} declares static children"


def test_a_kind_that_can_be_missing_something_can_name_it(project_config) -> None:
    """The one way to add an object, everywhere it is possible. A `<Labels>` can only
    offer what the exporter put in the task, and a single `+ new ...` pseudo-label
    cannot represent two missing things: both spans come back wearing it,
    indistinguishable. A Taxonomy in labeling mode draws the same regions and lets
    the reviewer type the name."""

    project, root = project_config
    layers = {node.get("name"): node for node in root.iter() if node.tag in ("Labels", "Taxonomy")}
    for kind in project.blocks:
        if not kind.span_prompt:
            continue
        node = layers[kind.named("spans")]
        if kind.naming:
            assert node.tag == "Taxonomy"
            assert node.get("labeling") == "true"
            # What exposes the add control; it costs only `apiUrl`, which nothing uses.
            assert node.get("legacy") == "true"
        else:
            assert node.tag == "Labels"


def test_nothing_is_smart_but_the_chat_question(project_config) -> None:
    """`smart` defaults to true on every control, and with Auto-Annotation on -- which
    the chat requires -- any region whose results include a smart control fires an
    interactive round trip. That includes drawing a span and deleting one, so leaving
    the span layer smart means an LLM call per highlight."""

    _project, root = project_config
    smart = [
        node.get("name")
        for node in root.iter()
        if node.get("toName") and node.get("smart") != "false"
    ]
    assert smart == [spec.CHAT_QUESTION]


def test_the_answer_log_cannot_be_typed_into(project_config) -> None:
    """`maxSubmissions="0"` hides the input box without blocking deserialization,
    which does not go through the submit path."""

    _project, root = project_config
    (answer,) = [n for n in root.iter() if n.get("name") == spec.CHAT_ANSWER]
    assert answer.get("maxSubmissions") == "0"
    assert answer.get("editable") is None
    (question,) = [n for n in root.iter() if n.get("name") == spec.CHAT_QUESTION]
    assert question.get("showSubmitButton") == "false"


def test_no_header_carries_a_classname(project_config) -> None:
    """`Header` supports inline `style` but not `className`; a className there is
    dropped and the text renders full-weight."""

    _project, root = project_config
    for node in root.iter():
        if node.tag == "Header":
            assert node.get("className") is None, node.get("value")


def test_no_control_is_per_region(project_config) -> None:
    """A perRegion control reads `annotation.highlightedNode` and stays hidden until a
    span is *clicked* -- drawing one is not enough. The judgements here are made while
    drawing, so they ride on the label instead."""

    _project, root = project_config
    assert not [node for node in root.iter() if node.get("perRegion") == "true"]


def test_every_required_question_points_the_reviewer_at_itself(project_config) -> None:
    """On failure Label Studio shows the requiredMessage and does nothing else: there
    is no scroll-to and no highlight for a whole-object control. A generic "answer
    this first" leaves the reviewer hunting a long form for whatever is blank."""

    _project, root = project_config
    prompts = {node.get("value") for node in root.iter() if node.tag == "Header"}
    for node in root.iter():
        if node.get("required") != "true":
            continue
        message = node.get("requiredMessage") or ""
        quoted = re.findall(r"'([^']+)'", message)
        assert quoted, message
        assert quoted[0] in prompts, message


def test_every_editor_is_gated_on_the_verdict_above_it(project_config) -> None:
    """`visibleWhen` reads only choices and regions, never task data, so this is the
    only conditional available -- and the whole reason a reviewer can answer a
    correct task in one click."""

    project, root = project_config
    verdicts = {kind.verdict for kind in project.blocks}
    for node in root.iter():
        if not node.get("visibleWhen"):
            continue
        assert node.get("visibleWhen") in ("choice-selected", "choice-unselected")
        assert node.get("whenChoiceValue")
        # The statistic block gates on its own dynamic Choices, which is not a verdict.
        assert node.get("whenTagName") in verdicts or "stat" in (node.get("whenTagName") or "")
        assert list(node), "an empty gated block renders as nothing at all"


def test_the_rendered_table_is_declared_once_outside_every_gate() -> None:
    """Both of the contrast project's kinds want it, so gating it would render it
    twice; and Label Studio records `table_html` as a HyperText data type the moment
    the config is saved, after which it admits only a string for that key -- on
    import and on the PATCH the sync issues. A key present on only one kind's tasks
    would fail the other's."""

    project = spec.BY_PROJECT["contrast"]
    root = ElementTree.fromstring(config.build(project))
    hypertexts = [node for node in root.iter() if node.tag == "HyperText"]
    assert len(hypertexts) == 1
    (grid,) = hypertexts
    # Without inline="true" the value renders into an iframe with its own document,
    # which the stylesheet cannot reach.
    assert grid.get("inline") == "true"
    assert grid.get("value") == "$table_html"

    for repeater in root.iter("Repeater"):
        assert grid not in list(repeater.iter())
    # Nothing points at it, so it draws no regions: the paper pane stays the only
    # place a span is drawn.
    assert not [n for n in root.iter() if n.get("toName") == "table_html"]

    for other in spec.PROJECTS:
        if other.name != "contrast":
            assert "HyperText" not in config.build(other)


def test_a_panel_never_gets_a_child_it_silently_drops(project_config) -> None:
    """`Panel` admits `view` but not `pagedview` or `markdown`. Neither failure
    raises: the block is simply absent from the rendered form."""

    _project, root = project_config
    for panel in root.iter("Panel"):
        for child in panel:
            assert child.tag.lower() not in lint.PANEL_FORBIDS
            assert not (child.tag == "Repeater" and child.get("mode") == "pagination")
    for collapse in root.iter("Collapse"):
        assert collapse.get("accordion") == "true"


# -- what the reviewer actually gets ----------------------------------------


@pytest.mark.parametrize("project, kind", VARIANTS, ids=VARIANT_IDS)
def test_the_expanded_form_asks_exactly_one_question(project, kind) -> None:
    """The property the whole gating arrangement exists for. A `required` control in a
    block the task does not carry is never instantiated, so several kinds can share a
    project without one blocking another's submission."""

    assert lint.check_expanded(project, kind) == []


@pytest.mark.parametrize("project, kind", VARIANTS, ids=VARIANT_IDS)
def test_no_two_rows_collide_on_a_control_name(project, kind) -> None:
    """A name that collides across two iterations validates server-side and then drops
    a control in the editor, because the server only sees `{{i}}`."""

    root = lint.expanded(project, kind, size=3)
    names = controls(root)
    assert names and len(names) == len(set(names))
    assert not [name for name in names if "{{" in name]


def test_the_kinds_of_one_project_share_only_the_frame() -> None:
    """Two kinds in one config must not answer each other's questions."""

    for project in spec.PROJECTS:
        if len(project.kinds) < 2:
            continue
        sets = [set(controls(lint.expanded(project, kind))) for kind in project.blocks]
        shared = set.intersection(*sets)
        # The object tag is not in here: it carries no `toName`, being what everything
        # else points at.
        assert shared == {spec.CHAT_QUESTION, spec.CHAT_ANSWER, "comment"}


# -- the contract -----------------------------------------------------------


def test_the_contract_is_the_config_plus_the_shared_keys(project_config) -> None:
    """Derived, not declared. The hand-written version had gone stale in both
    directions: keys the config no longer read, and keys it read that were absent."""

    project, _root = project_config
    contract = config.contract(project)
    assert set(contract) == set(config.interpolated(config.build(project))) | set(config.SHARED)


@pytest.mark.parametrize("kind", [k.name for k in spec.KINDS])
def test_a_sample_task_populates_the_whole_contract(kind: str) -> None:
    task = config.sample_task(kind)
    contract = config.contract(spec.PROJECT_OF[kind])
    assert set(task) == set(contract)
    for key, shape in contract.items():
        if shape == "array":
            assert isinstance(task[key], list), key
        elif shape == "string":
            assert isinstance(task[key], str), key


@pytest.mark.parametrize("kind", [k.name for k in spec.KINDS])
def test_a_sample_task_carries_exactly_one_gate(kind: str) -> None:
    task = config.sample_task(kind)
    filled = [key for key in task if key.startswith("gate_") and task[key]]
    assert filled == [spec.BY_NAME[kind].gate]


@pytest.mark.parametrize("kind", [k.name for k in spec.KINDS])
def test_a_sample_task_never_inlines_the_paper(kind: str) -> None:
    """The cap is what would catch someone pasting a real 20 KB rendered table in."""

    task = config.sample_task(kind)
    assert task["paper_url"].startswith("/data/local-files/")
    assert len(json.dumps(task)) < 4000


# -- theming ----------------------------------------------------------------


def _rules(css: str) -> list[tuple[str, str]]:
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", without_comments)


def test_the_stylesheet_uses_tokens_not_hex() -> None:
    """`tokens.prefix.css` redefines every --color token under the dark scheme, so a
    token-built panel inverts with the theme and a hex one does not."""

    for selector, body in _rules(style.stylesheet()):
        for declaration in body.split(";"):
            name, _, value = declaration.partition(":")
            if name.strip() in ("background", "color") or name.strip().startswith("border"):
                assert "#" not in value, f"{selector} {{{declaration}}}"


def test_every_rule_sets_background_and_foreground_together() -> None:
    """The specific trap: a hardcoded light background keeps the theme's light *text*
    colour in dark mode, leaving invisible light-on-light. Setting only one of the
    pair does the same."""

    for selector, body in _rules(style.stylesheet()):
        declarations = {
            name.strip(): value.strip()
            for name, _, value in (d.partition(":") for d in body.split(";"))
        }
        if "background" in declarations or "color" in declarations:
            assert "background" in declarations and "color" in declarations, selector


def test_the_stylesheet_has_no_character_the_sanitizer_escapes() -> None:
    """Style content goes through `sanitizeHtml`, and one mangled selector invalidates
    its whole comma-separated rule. A single `.ant-table-tbody > tr > td` silently
    voided the neighbouring `.ant-table` declarations and the panel went on rendering
    white with no error."""

    assert not set("<>&") & set(style.stylesheet())


def test_every_class_the_table_renderer_emits_is_styled() -> None:
    """The renderer and the stylesheet are one artifact split across two files: a
    class emitted and not styled renders as unstyled markup with nothing to say why."""

    sheet = style.stylesheet()
    for name in style.TABLE_CLASSES:
        assert f".{name}" in sheet, name

    emitted = set()
    for paper, table_id in (("HU6mqxmtySg3", "brb3829-tbl-0003"),):
        entry = tables.read_manifest(REVIEW / "texts" / paper).get(table_id)
        if not entry:
            pytest.skip("the rendered-table fixture is not present")
        table = tables.read_table(
            REVIEW / "texts" / paper / "source/pubget", entry["data_file"]
        )
        markup = tables.render_table_html(table, owner={0: 0, 1: 1}, contested={2: [0, 1]})
        for value in re.findall(r'class="([^"]+)"', markup):
            emitted.update(value.split())
    for name in emitted:
        assert f".{name}" in sheet, name


def test_one_tint_rule_per_tint_and_none_of_them_red() -> None:
    """The count has to agree with what the renderer cycles through, so both read the
    same constant. An analysis is not an error for being the fourth in its table."""

    rules = style.tint_rules()
    for index in range(len(spec.TINTS)):
        assert f"tr.ns-a{index} td" in rules
    assert f"tr.ns-a{len(spec.TINTS)} td" not in rules
    assert "negative" not in rules


def test_the_chat_pairs_every_turn_it_declares() -> None:
    """CSS has no arithmetic on `order`, so a pair costs two written-out rules and the
    count is fixed at generation time. Position is the only thing tying a question to
    its answer -- the payload carries no ids linking them."""

    rules = style.chat_order_rules()
    for turn in range(1, spec.CHAT_TURNS + 1):
        assert f".ns-chat-q .lsf-row:nth-of-type({turn}) {{ order: {2 * turn - 1}; }}" in rules
        assert f".ns-chat-a .lsf-row:nth-of-type({turn}) {{ order: {2 * turn}; }}" in rules


def test_every_inline_style_survives_the_css_converter(project_config) -> None:
    """`Tree.cssConverter` splits on ";" then the first ":", so a `var()` survives and
    a value containing a semicolon would be silently truncated."""

    _project, root = project_config
    for node in root.iter():
        declared = node.get("style")
        if not declared:
            continue
        for declaration in declared.split(";"):
            if not declaration.strip():
                continue
            name, separator, value = declaration.partition(":")
            assert separator and name.strip() and value.strip(), declared
            if name.strip() == "color":
                assert value.strip().startswith("var(")


def test_every_colour_token_exists_in_both_themes() -> None:
    """A token defined only at the root keeps its light value in dark mode."""

    source = ROOT / "label-studio/web/libs/ui/src/tokens/tokens.prefix.css"
    if not source.is_file():
        pytest.skip("no Label Studio checkout")
    css = source.read_text(encoding="utf-8")
    dark = css[css.index('[data-color-scheme="dark"]') :]
    for token in sorted(set(re.findall(r"var\((--color-[\w-]+)\)", style.stylesheet()))):
        assert f"{token}:" in css.replace(" ", ""), token
        assert f"{token}:" in dark.replace(" ", ""), f"{token} is not redefined for dark"


# -- the export -------------------------------------------------------------


@requires_paper
def test_the_staged_text_is_the_text_the_offsets_address(tmp_path) -> None:
    normalized = text_index.normalize(TEXT.read_text(encoding="utf-8"))
    url = staging.stage(tmp_path, PAPER, normalized, text_index.text_hash(normalized))
    assert url == staging.url_for(PAPER)
    with staging.staged_path(tmp_path, PAPER).open(encoding="utf-8", newline="") as stream:
        assert stream.read() == normalized


def test_staging_refuses_a_text_the_record_was_not_built_against(tmp_path) -> None:
    """Serving a different text does not fail; it silently highlights whatever now
    sits at those numbers."""

    with pytest.raises(staging.TextMismatch):
        staging.stage(tmp_path, "x", "some text", "0" * 64)
    assert not staging.staged_path(tmp_path, "x").exists()


@requires_paper
def test_every_task_satisfies_its_project_contract(exported) -> None:
    assert exported.contract_problems() == []


@requires_paper
def test_no_task_carries_the_paper(exported) -> None:
    """Inlining would produce ~18 MB of task JSON per paper against ~1 MB of URLs,
    and the browser would refetch the text for every task instead of once."""

    normalized = exported.normalized
    inlined = 0
    for items in exported.tasks.values():
        for task in items:
            blob = json.dumps(task["data"])
            assert normalized[:200] not in blob
            inlined += len(normalized)
    assert sum(len(json.dumps(t)) for i in exported.tasks.values() for t in i) < inlined / 5


@requires_paper
def test_review_keys_are_unique(exported) -> None:
    """The address is what the sync matches on; two tasks sharing one would make the
    reconciliation non-deterministic."""

    keys = [t["data"]["review_key"] for i in exported.tasks.values() for t in i]
    assert len(keys) == len(set(keys))
    for key in keys:
        assert key.startswith(f"{PAPER}|")


@requires_paper
def test_every_predicted_span_addresses_the_staged_text(exported) -> None:
    normalized = exported.normalized
    seen = 0
    for items in exported.tasks.values():
        for task in items:
            for prediction in task.get("predictions") or []:
                for entry in prediction["result"]:
                    value = entry["value"]
                    if "start" not in value:
                        continue
                    seen += 1
                    assert normalized[value["start"] : value["end"]] == value["text"]
                    assert entry["to_name"] == spec.PAPER
                    assert spec.parse_control(entry["from_name"]) is not None
    assert seen > 0


@requires_paper
def test_every_prediction_names_a_control_the_task_declares(exported) -> None:
    """A prediction naming a control the config does not render is accepted by the
    server and then has nothing to bind to: no error, no highlight, no pre-filled
    radio."""

    for project in spec.PROJECTS:
        label_config = ElementTree.fromstring(config.build(project))
        for task in exported.tasks[project.name]:
            declared = answers.declared_controls(label_config, task["data"])
            for prediction in task.get("predictions") or []:
                for entry in prediction["result"]:
                    assert entry["from_name"] in declared, entry["from_name"]


@requires_paper
def test_every_predicted_label_is_one_the_task_offers(exported) -> None:
    for items in exported.tasks.values():
        for task in items:
            offered = {
                option.get("alias") or option.get("value") for option in task["data"]["labels"]
            }
            for prediction in task.get("predictions") or []:
                for entry in prediction["result"]:
                    for label in (entry["value"].get("labels") or []):
                        assert label in offered, label


@requires_paper
def test_the_value_family_is_one_task_per_reviewable_field(exported) -> None:
    """Per field rather than per entity: an entity task bundles 13-25 judgements
    behind a single verdict. The count comes from the schema walk, so a slot that
    changes family shows up here."""

    body = json.loads(RECORD.read_text(encoding="utf-8"))
    walked = record_module.Record(body)
    expected = [field for field in walked.fields if not field.structural]
    assert len(exported.tasks["value"]) == len(expected)


@requires_paper
def test_a_table_task_is_asked_before_what_is_drawn_from_it(exported) -> None:
    """`over_split` invalidates the contrast, model, value and relationship tasks of
    every analysis drawn from the table, which is the same cascade the entity
    inventory guards against and the same reason to ask it first."""

    stages = {
        task["data"]["task_kind"]: task["data"]["stage"]
        for items in exported.tasks.values()
        for task in items
    }
    assert stages["table"] == 0 and stages["entities"] == 0
    assert stages["contrast"] == 2

    for items in exported.tasks.values():
        assert [t["data"]["stage"] for t in items] == sorted(
            t["data"]["stage"] for t in items
        )


@requires_paper
def test_the_content_hash_covers_the_question_not_the_rendering(exported) -> None:
    """The distinction the whole regeneration protocol rests on. Correcting a
    `Group.name` changes the descriptor shown in a dozen tasks; if that moved the
    hash, every one of them would be re-asked though its substance did not move."""

    before = {
        t["data"]["review_key"]: t["data"]["content_hash"]
        for t in exported.tasks["contrast"]
    }
    same = tasks_module.Exporter(
        json.loads(RECORD.read_text(encoding="utf-8")),
        exported.normalized,
        PAPER,
        {"pmid": "999", "doi": "10.0/different"},
        "/data/local-files/?d=texts/elsewhere.txt",
    )
    root = REVIEW / "texts" / PAPER
    same.load_tables(
        root / "source/pubget", root / "stage1/analyses.json", root / "stage1/table-map.json"
    )
    same.run()
    after = {
        t["data"]["review_key"]: t["data"]["content_hash"] for t in same.tasks["contrast"]
    }
    assert before == after


@requires_paper
def test_the_text_hash_rides_outside_the_content_hash(exported) -> None:
    """Without it a re-staged text is invisible to the sync: offsets are not in the
    content hash, so `data` would stay byte-identical, the sync would take its
    unchanged branch, and every stored prediction would keep addressing the old
    text."""

    for items in exported.tasks.values():
        for task in items:
            assert task["data"]["paper_text_hash"] == exported.text_hash
    assert tasks_module.digest(("a", "b")) != exported.text_hash


@requires_paper
def test_the_grid_is_a_string_on_every_task_of_its_project(exported) -> None:
    """Label Studio records the key as HyperText on the first save and then admits
    only `str` for it -- on import and on the PATCH the sync issues."""

    for task in exported.tasks["contrast"]:
        assert isinstance(task["data"]["table_html"], str)
        assert task["data"]["table_html"]


@requires_paper
def test_a_contrast_marks_the_rows_its_table_task_attributed_to_it(exported) -> None:
    """Both views of one table have to agree. Raw coordinate matching claims rows the
    section-block resolution does not, and a reviewer shown both would have no way to
    tell which was lying."""

    marked = [
        t for t in exported.tasks["contrast"]
        if t["data"]["task_kind"] == "contrast" and "ns-hit" in t["data"]["table_html"]
    ]
    assert marked, "the premise of this test changed: no contrast marks a row"
    for task in marked:
        assert "this analysis" in task["data"]["table_html"]


@requires_paper
def test_a_parse_nobody_encoded_is_reported_rather_than_dropped(exported) -> None:
    for task in exported.tasks["contrast"]:
        if task["data"]["task_kind"] != "table":
            continue
        for row in task["data"]["rows"]:
            assert "encoded as" in row["meta"] or "NOT ENCODED" in row["meta"]


@requires_paper
def test_a_table_task_offers_the_parsed_analyses_and_nothing_else(exported) -> None:
    """No placeholder for a thing the reviewer must name: the naming control takes a
    typed name, so a `+ new analysis` slot would be a second way to say the same
    thing and could not tell two missed analyses apart."""

    for task in exported.tasks["contrast"]:
        if task["data"]["task_kind"] != "table":
            continue
        assert len(task["data"]["labels"]) == len(task["data"]["rows"])
        for label in task["data"]["labels"]:
            assert label["value"].startswith("analysis: ")
            assert "new" not in label["value"].split(":")[0]


@requires_paper
def test_a_verdict_is_offered_only_when_its_subject_exists(exported) -> None:
    """No analysis in the corpus records degrees of freedom, so a fixed `df_wrong`
    asked every reviewer about a value that was never there. `df_absent` is the half
    that IS answerable when the record has none, because it asks about the paper."""

    for task in exported.tasks["contrast"]:
        data = task["data"]
        if data["task_kind"] != "contrast" or not data["statistic"]:
            continue
        offered = {option["value"] for option in data["options"]}
        assert "statistic_correct" in offered
        assert ("df_wrong" in offered) != ("df_absent" in offered)
        summary = data["statistic"][0]["summary"]
        assert ("df " in summary) == ("df_wrong" in offered)
        for option in data["options"]:
            assert option["hint"] == spec.STATISTIC_VERDICTS[option["value"]]


@requires_paper
def test_a_paraphrase_names_things_rather_than_addressing_them(exported) -> None:
    """A line reading `group_healthy_adults n=18` makes the reviewer decode an
    identifier to check a fact the record states in words."""

    bodies = [
        t["data"]["gate_contrast"][0]["body"]
        for t in exported.tasks["contrast"]
        if t["data"]["task_kind"] == "contrast"
    ]
    assert bodies
    for body in bodies:
        assert re.match(r"\*\*[^*]+\*\*( vs \*\*[^*]+\*\*)?|_\(no signed cell\)_", body)
        for line in body.splitlines():
            if line.startswith("- reported in:"):
                assert re.search(r"Table \d", line), line


def test_a_recorded_direction_is_never_read_as_an_absence() -> None:
    """The defect this fixed: `unstated` folded onto `absent` renders an omnibus F as
    "every term adjusted for, none tested" -- the opposite of what the paper says."""

    exporter = tasks_module.Exporter.__new__(tasks_module.Exporter)
    assert exporter._direction("positive") == "positive"
    assert exporter._direction("negative weight") == "negative"
    assert exporter._direction("not_applicable") == "not_applicable"
    assert exporter._direction("unstated") == "unstated"
    assert exporter._direction("something the enum grew later") == "unstated"
    # Only a row with no cell at all is an absence.
    assert exporter._direction(None) == "absent"


@requires_paper
def test_a_slot_with_no_candidates_is_reported_not_asserted_empty(exported) -> None:
    """A task saying "none of these" about a class the paper has none of is a
    judgement nobody can make. Skipping it silently would be worse: the slot would
    read as reviewed."""

    for note in exported.report.skipped:
        assert note
    for task in exported.tasks["relationship"]:
        assert task["data"]["columns"]


# -- decoding ---------------------------------------------------------------


def _relationship_task(**overrides):
    data = {
        "review_key": f"{PAPER}|relationship|Analysis.acquisitions",
        "task_kind": "relationship",
        "local_id": "",
        "rows": [
            {"label": "one", "meta": "ana_1", "local_id": "ana_1"},
            {"label": "two", "meta": "ana_2", "local_id": "ana_2"},
        ],
        "rows_single": [],
        "columns": [
            {"value": "acq_1 -- rest . fMRI", "alias": "acq_1"},
            {"value": "acq_2 -- task . fMRI", "alias": "acq_2"},
            {"value": "no link", "alias": "none"},
        ],
    }
    data.update(overrides)
    return {"data": data}


def _choice(from_name, choices):
    return {"from_name": from_name, "type": "choices", "value": {"choices": choices}}


def test_an_answer_decodes_through_the_task_that_offered_it() -> None:
    """The encoding is a property of the config at the moment the answer was written,
    so resolution goes through the task's own columns rather than a rebuilt
    descriptor -- which would change whenever a priority-0 field did."""

    decoded = answers.decode(
        _relationship_task(),
        [_choice(spec.instance("relationship", "row", 0), ["acq_2"])],
    )
    assert decoded["links"] == {"ana_1": ["acq_2"], "ana_2": []}
    assert decoded["unresolved"] == []


def test_an_answer_written_under_the_other_encoding_still_decodes() -> None:
    """The grid once stored the descriptor and displayed the id. Those answers are
    still real answers."""

    decoded = answers.decode(
        _relationship_task(),
        [_choice(spec.instance("relationship", "row", 1), ["acq_1 -- rest . fMRI"])],
    )
    assert decoded["links"]["ana_2"] == ["acq_1"]


def test_a_token_no_column_explains_is_reported_not_passed_through() -> None:
    """Passing it through would put a string that is not an id where an id belongs,
    and reconstruction would build a record around it."""

    decoded = answers.decode(
        _relationship_task(),
        [_choice(spec.instance("relationship", "row", 0), ["acq_9"])],
    )
    assert decoded["links"]["ana_1"] == []
    assert "acq_9" in decoded["unresolved"][0]


def test_a_row_index_that_addresses_nothing_is_reported_not_guessed() -> None:
    """An off-by-one reassignment produces a VALID record with the wrong content,
    which is the failure that would never be noticed."""

    decoded = answers.decode(
        _relationship_task(),
        [_choice(spec.instance("relationship", "row", 5), ["acq_1"])],
    )
    assert "addresses no row" in decoded["unresolved"][0]
    assert all(targets == [] for targets in decoded["links"].values())


def test_an_unticked_row_is_an_answer_and_the_none_column_is_too() -> None:
    """An unticked row asserts "this links to nothing"; omitting it would make that
    indistinguishable from a row nobody was asked about."""

    decoded = answers.decode(
        _relationship_task(),
        [_choice(spec.instance("relationship", "row", 0), ["none"])],
    )
    assert decoded["links"] == {"ana_1": [], "ana_2": []}
    assert decoded["unresolved"] == []


def test_one_decoder_reads_every_kind() -> None:
    """The point of the naming grammar. Before it, one family had a decoder and the
    rest had none."""

    task = {
        "data": {
            "task_kind": "model",
            "review_key": f"{PAPER}|model|glm_0",
            "local_id": "glm_0",
            "rows": [
                {"label": "group", "local_id": "trm_1", "levels": [{"label": "patients"}]},
            ],
            "columns": [],
        }
    }
    decoded = answers.decode(
        task,
        [
            {"from_name": spec.instance("model", "verdict"), "type": "choices",
             "value": {"choices": ["term_wrong"]}},
            {"from_name": spec.instance("model", "name", 0), "type": "textarea",
             "value": {"text": ["diagnosis"]}},
            {"from_name": spec.instance("model", "level", 0, 0), "type": "textarea",
             "value": {"text": ["MDD"]}},
            {"from_name": "comment", "type": "textarea", "value": {"text": ["looked odd"]}},
            {"from_name": spec.CHAT_QUESTION, "type": "textarea", "value": {"text": ["why?"]}},
        ],
    )
    assert decoded["verdict"] == "term_wrong"
    assert decoded["rows"]["trm_1"]["name"] == ["diagnosis"]
    assert decoded["rows"]["trm_1"]["level"] == {0: ["MDD"]}
    assert decoded["notes"]["comment"] == ["looked odd"]
    assert decoded["chat"] and decoded["unresolved"] == []


def test_a_name_the_reviewer_typed_comes_back_as_a_finding() -> None:
    """The select-or-create control is how a missing object is reported, so a label
    under no column is the answer rather than an error."""

    task = {"data": {"task_kind": "table", "review_key": "k", "columns": [], "rows": []}}
    decoded = answers.decode(
        task,
        [
            {
                "from_name": spec.instance("table", "spans"),
                "type": "taxonomy",
                "value": {
                    "start": 10, "end": 20, "text": "a missed contrast",
                    "taxonomy": [["conjunction of both tasks"]],
                },
            }
        ],
    )
    assert decoded["spans"] == [
        {
            "local_id": None,
            "label": "conjunction of both tasks",
            "start": 10,
            "end": 20,
            "text": "a missed contrast",
        }
    ]


def test_the_evidence_diff_separates_a_nudge_from_a_different_sentence() -> None:
    """A boundary adjustment and a different passage are different findings about the
    extractor, and lumping them reports one as the other."""

    predicted = [
        {"start": 10, "end": 20, "text": "one", "labels": []},
        {"start": 50, "end": 60, "text": "two", "labels": []},
    ]
    annotated = [
        {"start": 10, "end": 20, "text": "one", "labels": []},
        {"start": 52, "end": 64, "text": "two-ish", "labels": []},
        {"start": 90, "end": 99, "text": "new", "labels": []},
    ]
    diff = answers.diff(predicted, annotated)
    assert len(diff["kept"]) == 1
    assert len(diff["adjusted"]) == 1 and diff["adjusted"][0]["to"]["start"] == 52
    assert len(diff["added"]) == 1 and not diff["removed"]


# -- orphaned answers -------------------------------------------------------


def test_what_counts_as_orphaned_is_read_from_the_config() -> None:
    """A hardcoded list would need editing every time a config changes, which is the
    moment it would be forgotten."""

    project = spec.BY_PROJECT["relationship"]
    label_config = ElementTree.fromstring(config.build(project))
    task = config.sample_task("relationship")
    declared = answers.declared_controls(label_config, task)

    assert spec.instance("relationship", "row", 0) in declared
    assert spec.instance("relationship", "row", 9) not in declared
    assert spec.CHAT_QUESTION in declared

    result = [
        _choice(spec.instance("relationship", "row", 0), ["acq_1"]),
        _choice("relationship_verdict_0", ["links_correct"]),
    ]
    assert answers.orphans(result, declared) == ["relationship_verdict_0"]
