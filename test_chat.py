"""Checks for the in-task chat: the config's two controls, and the backend behind them.

The whole feature rests on behaviour of Label Studio that is documented nowhere but
in its source, so these tests pin the parts we would otherwise only discover were
wrong by watching a reviewer lose an answer:

  * a TextArea's result carries ALL of its submissions in one list, and accepting a
    suggestion REPLACES the control's area -- so a reply must resend the whole log
  * the context of an interactive call is every textarea region on `paper`, not just
    the chat's -- so the reviewer's comment arrives here and must be ignored
  * `smart` defaults to true on every control -- so every other TextArea in these
    configs must turn it off or typing a note would spend an LLM call

Nothing here calls the model: `Chat.ask` is the seam.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parent
REVIEW = ROOT / "review"
sys.path.insert(0, str(REVIEW))

import chat as backend  # noqa: E402
import config  # noqa: E402
import spec  # noqa: E402

Q = spec.CHAT_QUESTION
A = spec.CHAT_ANSWER


def result(from_name: str, texts: list[str]) -> dict:
    """One serialized TextArea result, as `context.result` carries it."""

    return {
        "id": f"id_{from_name}",
        "from_name": from_name,
        "to_name": "paper",
        "type": "textarea",
        "value": {"text": texts},
    }


@pytest.fixture
def chat(tmp_path: Path) -> backend.Chat:
    (tmp_path / "texts").mkdir()
    (tmp_path / "texts" / "PAPER1.txt").write_text("The mean age was 32.4 years.\n",
                                                   encoding="utf-8")
    return backend.Chat(
        model="test-model", effort="low", files_root=tmp_path,
        timeout=5.0, max_chars=100_000)


def task(**data) -> dict:
    base = {"paper_id": "PAPER1", "paper_url": "/data/local-files/?d=texts/PAPER1.txt"}
    return {"id": 1, "data": {**base, **data}}


# -- reading the context ---------------------------------------------------


def test_texts_for_reads_one_control_and_ignores_the_rest() -> None:
    """The group is filtered by region type and to_name, so the notes box comes too."""

    results = [
        result("comment", ["a note the reviewer typed"]),
        result(Q, ["what is the sample size?"]),
        result("corrected_value", ["42"]),
        result(A, ["The paper reports 28 participants."]),
    ]
    assert backend.texts_for(results, Q) == ["what is the sample size?"]
    assert backend.texts_for(results, A) == ["The paper reports 28 participants."]
    assert backend.texts_for(results, "chat_nothing") == []


def test_texts_for_accepts_a_bare_string() -> None:
    """`value.text` is a list for a TextArea, but a single-submission result may not be."""

    assert backend.texts_for([result(Q, "just one")], Q) == ["just one"]


def test_texts_for_survives_junk_in_the_context() -> None:
    assert backend.texts_for([{"from_name": Q}, {"from_name": Q, "value": []}, 7], Q) == []


# -- pairing ---------------------------------------------------------------


def test_align_finds_the_one_unanswered_question() -> None:
    answers, question = backend.align(["q1", "q2"], ["a1"])
    assert question == "q2"
    assert answers == ["a1"]


def test_align_ignores_a_notification_that_is_not_a_new_question() -> None:
    """Editing an old entry re-fires the same event; answering again would double the log."""

    assert backend.align(["q1"], ["a1"]) == (["a1"], None)
    assert backend.align([], []) == ([], None)


def test_align_pads_a_lost_turn_so_pairs_do_not_shift() -> None:
    """A call that never returned must not make q2's answer look like q3's.

    Positional pairing is all the payload supports -- there are no timestamps -- so
    a hole has to be filled rather than closed up.
    """

    answers, question = backend.align(["q1", "q2", "q3"], ["a1"])
    assert question == "q3"
    assert answers == ["a1", "(unanswered)"]
    assert list(zip(["q1", "q2", "q3"], answers)) == [("q1", "a1"), ("q2", "(unanswered)")]


# -- what the model is told about the task ---------------------------------


def test_task_context_renders_the_rows_that_are_the_object_under_review() -> None:
    """For a relationship or structure task the object under review IS an array.

    The first version of this kept short scalars only, which left a contrast task
    telling the model `task_kind: contrast` and nothing about the contrast -- and it
    answered from the paper alone, confidently, with no idea what it was judging.
    """

    block = backend.task_context({
        "task_kind": "contrast",
        "cell_count": 5,
        "contrast": [{"label": "HC > MDD", "paraphrase": "diagnostic group = HC\nvs MDD"}],
        "cell_rows": [{"label": "diagnostic group : MDD", "term": "term_group"},
                      {"label": "age (continuous)", "term": "term_age"}],
    })
    assert "task_kind: contrast" in block
    assert "label=HC > MDD" in block
    assert "paraphrase=diagnostic group = HC vs MDD" in block      # newlines folded
    assert "- label=diagnostic group : MDD · term=term_group" in block
    assert "- label=age (continuous) · term=term_age" in block


def test_task_context_descends_into_a_record_that_holds_an_array() -> None:
    """`terms[].levels` is the one shape that is not flat, and it is load-bearing."""

    block = backend.task_context({
        "terms": [{"heading": "diagnostic group", "local_id": "term_group",
                   "levels": [{"label": "MDD"}, {"label": "HC"}]}],
    })
    assert "- heading=diagnostic group · local_id=term_group" in block
    assert "  levels:" in block
    assert "    - label=MDD" in block
    assert "    - label=HC" in block


def test_task_context_drops_only_the_plumbing() -> None:
    """The skip list is spec.CHAT_SKIP_KEYS, so the exporter and the backend cannot
    disagree about which keys are content and which are addressing."""

    block = backend.task_context({
        "entity_class": "Group",
        "priority": 0,
        "paper_url": "/data/local-files/?d=texts/PAPER1.txt",
        "review_key": "PAPER1|value|Group|age_mean",
        "content_hash": "4c6225318cfc7ec0",
        "paper_text_hash": "0" * 64,
        "labels": [{"value": "direct support"}],
        "columns": [{"value": "acq_1", "alias": "acq_1 . fMRI"}],
        "legend": [{"id": "grp_mdd"}],
        "table_html": "<div>a 20 KB grid</div>",
    })
    assert "entity_class: Group" in block
    assert "priority: 0" in block
    for gone in spec.CHAT_SKIP_KEYS:
        assert gone not in block


def test_task_context_skips_an_unfilled_repeater_gate() -> None:
    """One config serves three task kinds, so most gates are [] on any given task."""

    block = backend.task_context({
        "task_kind": "entities", "gate_model": [], "rows": [], "statistic": [],
        "local_id": "",
    })
    assert block == "task_kind: entities"


def test_task_context_renders_a_key_it_has_never_seen() -> None:
    """The exporter's contract grows. A new field should reach the model by default.

    This is the opposite of the previous rule, which admitted a value only if it
    was a short scalar, so every new array was dropped in silence.
    """

    block = backend.task_context({"invented_later": [{"a": 1, "b": "two"}]})
    assert "invented_later:" in block
    assert "- a=1 · b=two" in block


# -- finding the paper -----------------------------------------------------


def test_text_path_reads_the_url_the_config_renders(chat: backend.Chat) -> None:
    assert chat.text_path(task()["data"]).name == "PAPER1.txt"


def test_text_path_falls_back_to_the_paper_id(chat: backend.Chat) -> None:
    assert chat.text_path({"paper_id": "PAPER1"}).name == "PAPER1.txt"


def test_text_path_refuses_to_leave_the_files_root(chat: backend.Chat) -> None:
    """`d` arrives from task data, and this reads the same tree LS serves."""

    with pytest.raises(ValueError, match="escapes"):
        chat.text_path({"paper_url": "/data/local-files/?d=../../../etc/passwd"})


def test_text_path_needs_something_to_go_on(chat: backend.Chat) -> None:
    with pytest.raises(ValueError, match="neither"):
        chat.text_path({})


def test_paper_is_cached_but_not_past_a_restage(chat: backend.Chat,
                                                tmp_path: Path) -> None:
    """Re-running the exporter rewrites the text in place; the cache must notice."""

    staged = tmp_path / "texts" / "PAPER1.txt"
    assert "32.4" in chat.paper(task()["data"])

    staged.write_text("The mean age was 41.0 years.\n" + " " * 40, encoding="utf-8")
    assert "41.0" in chat.paper(task()["data"])


# -- the prompt ------------------------------------------------------------


def test_the_system_message_is_the_instructions_and_the_paper_and_nothing_else(
        chat: backend.Chat) -> None:
    """The caching contract, and it was measured rather than assumed.

    Anything task-specific up here makes the system message differ between two
    tasks on the same paper, and the gateway then reports `cached_tokens: 0` on the
    first question of every task -- it only rewards an exact continuation of a
    previous prompt. Keeping this message identical across a paper's ~50 tasks is
    what the second-and-later tasks hit.
    """

    head = chat.messages("PAPER BODY", "entity_class: Group", [("q1", "a1")], "q2")[0]

    assert head["role"] == "system"
    assert head["content"] == f"{backend.SYSTEM}\n\n# Paper\n\nPAPER BODY"
    assert head["content"] == chat.messages("PAPER BODY", "field_path: age_mean",
                                            [], "other")[0]["content"]


def test_messages_carry_the_task_on_the_live_question(chat: backend.Chat) -> None:
    """~40 tokens, below the paper, where it changes nothing upstream of it."""

    messages = chat.messages("PAPER BODY", "entity_class: Group", [("q1", "a1")], "q2")

    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "q1"          # history stays verbatim
    assert messages[-1]["content"].endswith("q2")
    assert "entity_class: Group" in messages[-1]["content"]


def test_messages_omit_an_empty_task_block(chat: backend.Chat) -> None:
    assert chat.messages("PAPER BODY", "", [], "q1")[-1]["content"] == "q1"


# -- one round trip --------------------------------------------------------


def test_answer_resends_the_whole_log(chat: backend.Chat, monkeypatch) -> None:
    """Accepting a suggestion REPLACES the control's area (Annotation.js:1164-1195).

    Returning only the new answer would therefore erase every earlier one. This is
    the single most destructive way this feature can be wrong, and the least
    visible: the reviewer sees the new answer appear and the history quietly gone.
    """

    monkeypatch.setattr(chat, "ask", lambda *_: ("a2", {"prompt_tokens": 1,
                                                        "cached_tokens": 0,
                                                        "completion_tokens": 1}))
    reply = chat.answer(task(), [result(Q, ["q1", "q2"]), result(A, ["a1"])])

    assert reply["result"][0]["value"]["text"] == ["a1", "a2"]
    assert reply["result"][0]["from_name"] == A
    assert reply["result"][0]["to_name"] == "paper"
    assert reply["result"][0]["type"] == "textarea"


def test_answer_sends_the_earlier_turns_as_history(chat: backend.Chat,
                                                   monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(chat, "ask", lambda paper_id, messages: (
        seen.update(messages=messages) or ("a2", {"prompt_tokens": 1, "cached_tokens": 0,
                                                  "completion_tokens": 1})))
    chat.answer(task(), [result(Q, ["q1", "q2"]), result(A, ["a1"])])

    earlier = [m["content"] for m in seen["messages"][1:-1]]
    assert earlier == ["q1", "a1"]                      # verbatim, so the prefix is stable
    assert seen["messages"][-1]["content"].endswith("q2")


def test_answer_does_nothing_when_no_question_is_pending(chat: backend.Chat,
                                                         monkeypatch) -> None:
    """Every other textarea in the config is smart=false, but the guard is here too."""

    monkeypatch.setattr(chat, "ask", lambda *_: pytest.fail("should not have been called"))
    reply = chat.answer(task(), [result("comment", ["a note"]), result(Q, ["q1"]),
                                 result(A, ["a1"])])
    assert reply["result"] == []


def test_answer_records_a_failure_instead_of_raising(chat: backend.Chat,
                                                     monkeypatch) -> None:
    """A 500 reaches the reviewer as a bare Data Manager error and leaves the log short.

    Short means every later pair shifts by one, so the failure has to be written
    down where the answer would have gone.
    """

    def boom(*_):
        raise TimeoutError("gateway took too long")

    monkeypatch.setattr(chat, "ask", boom)
    reply = chat.answer(task(), [result(Q, ["q1"])])

    texts = reply["result"][0]["value"]["text"]
    assert len(texts) == 1
    assert "gateway took too long" in texts[0]


def test_predict_without_context_writes_nothing(chat: backend.Chat,
                                                monkeypatch) -> None:
    """LS also calls /predict to pre-annotate a task list; there is nothing to offer.

    Answering there would put an unprompted `chat_a` on every one of a paper's
    ~50 tasks.
    """

    monkeypatch.setattr(chat, "ask", lambda *_: pytest.fail("should not have been called"))
    reply = chat.predict({"tasks": [task(), task()], "params": {"context": None}})

    assert [r["result"] for r in reply["results"]] == [[], []]


def test_predict_answers_the_interactive_call(chat: backend.Chat, monkeypatch) -> None:
    monkeypatch.setattr(chat, "ask", lambda *_: ("a1", {"prompt_tokens": 1,
                                                        "cached_tokens": 0,
                                                        "completion_tokens": 1}))
    reply = chat.predict({
        "tasks": [task()],
        "params": {"context": {"result": [result(Q, ["q1"])]}},
    })
    assert reply["results"][0]["result"][0]["value"]["text"] == ["a1"]


# -- the config side -------------------------------------------------------


@pytest.mark.parametrize("project", spec.PROJECTS, ids=[p.name for p in spec.PROJECTS])
def test_the_question_box_is_the_only_smart_control(project) -> None:
    """`smart` defaults to TRUE (tags/control/Base.js:16).

    With Auto-Annotation on, `smartEnabled` is `smart && autoAnnotation`, and any
    smart control that finishes a region fires the round trip. So a reviewer typing
    a `comment` would spend a call on a note nobody asked a question about, and the
    backend would be handed a context with no pending question. Every control that
    is not the chat box has to say so.
    """

    root = ElementTree.fromstring(config.build(project))
    smart = [node.get("name") for node in root.iter()
             if node.get("name") and node.get("smart") != "false"
             and node.tag in {"TextArea", "Choices", "Labels", "Number", "Rating"}]
    assert smart == [Q], f"{project.name}: {smart}"


@pytest.mark.parametrize("project", spec.PROJECTS, ids=[p.name for p in spec.PROJECTS])
def test_the_chat_never_blocks_a_submission(project) -> None:
    """A required chat box would make asking a question a condition of finishing."""

    root = ElementTree.fromstring(config.build(project))
    for node in root.iter("TextArea"):
        if node.get("name") in (Q, A):
            assert node.get("required") != "true"


@pytest.mark.parametrize("project", spec.PROJECTS, ids=[p.name for p in spec.PROJECTS])
def test_the_answer_log_is_not_typed_into(project) -> None:
    """It is a record of what the model said. `maxSubmissions="0"` hides its input.

    (`TextArea.jsx:133-140`: showSubmit is `submissionsNum < 0`, never true. It does
    not touch deserialization, which is how the backend's reply still arrives.)
    """

    root = ElementTree.fromstring(config.build(project))
    answers = [n for n in root.iter("TextArea") if n.get("name") == A]
    assert len(answers) == 1
    assert answers[0].get("maxSubmissions") == "0"
    assert answers[0].get("editable") != "true"


@pytest.mark.parametrize("project", spec.PROJECTS, ids=[p.name for p in spec.PROJECTS])
def test_no_config_carries_a_control_character(project) -> None:
    """_STYLE is not a raw string, so a CSS escape in it is a Python escape first.

    `content: "\\2191"` written with one backslash is read as octal \\21 plus "91",
    which puts a literal control character in the XML and makes the whole config
    unparseable -- every task in the project, not just the chat. It is caught by
    `check_label_config.py` but only once the file is written, so this asserts the
    property at the source.
    """

    label_config = config.build(project)
    bad = {c for c in label_config if ord(c) < 32 and c not in "\t\n\r"}
    assert not bad, f"{project.name} carries {[hex(ord(c)) for c in bad]}"
    ElementTree.fromstring(label_config)          # and it parses


@pytest.mark.parametrize("project", spec.PROJECTS, ids=[p.name for p in spec.PROJECTS])
def test_the_question_box_has_no_submit_button(project) -> None:
    """Shift+Enter is the whole submit path; the button only got in the way.

    `showSubmitButton="false"` is a documented TextArea attribute
    (TextArea.jsx:75), so LSF renders no button at all -- which beats the CSS that
    tried to reposition one: absolutely positioned, it landed on top of the
    submitted question, because the region list renders after the form and outside
    it and there is nothing to anchor to that clears it.
    """

    root = ElementTree.fromstring(config.build(project))
    question = [n for n in root.iter("TextArea") if n.get("name") == Q]
    assert len(question) == 1
    assert question[0].get("showSubmitButton") == "false"

    style = root.find("Style").text
    assert ".ns-chat form textarea" in style
    assert "position: absolute" not in style, "nothing here needs taking out of flow"
    assert 'button[type="submit"]' not in style

    selectors = re.sub(r"/\*.*?\*/", "", style, flags=re.S)   # the comments name them
    for brittle in ("lsf-text-area", "lsf-textarea-tag", "ant-btn"):
        assert brittle not in selectors, f"{brittle} is version-specific; key on the element"


@pytest.mark.parametrize("project", spec.PROJECTS, ids=[p.name for p in spec.PROJECTS])
def test_the_chat_collapses_without_storing_that_it_did(project) -> None:
    """`Collapse`/`Panel` is visual: no `name`, no result, nothing in the record.

    The alternative -- a `Choices` gate with `visibleWhen`, as the verdict forms
    use -- would put "was the chat open" into every annotation and out through
    every export.
    """

    root = ElementTree.fromstring(config.build(project))
    # `structure` already collapses the term list, so scope to the chat's own.
    chat = [p for p in root.iter("Panel") if p.get("value") == "Ask about this paper"]
    assert len(chat) == 1
    assert chat[0].get("open") == "true", "it should start open, not hidden"
    assert list(chat[0])[0].get("className") == "ns-chat-body"
    for node in list(root.iter("Collapse")) + list(root.iter("Panel")):
        assert node.get("name") is None, "a visual tag with a name would be a control"


@pytest.mark.parametrize("project", spec.PROJECTS, ids=[p.name for p in spec.PROJECTS])
def test_the_exchange_reads_question_answer_then_the_typing_bar(project) -> None:
    """A TextArea renders its input above its own history, so the two are split.

    `display: contents` promotes the form and the submitted-question list to flex
    items of one column, and `order` interleaves them with the answers. Without
    the flattening the wrappers stay opaque boxes and `order` can only move whole
    controls, which is the layout being fixed here.
    """

    root = ElementTree.fromstring(config.build(project))
    body = [n for n in root.iter("View") if n.get("className") == "ns-chat-body"]
    assert len(body) == 1
    classes = [n.get("className") for n in body[0] if n.get("className")]
    assert classes == ["ns-chat-q", "ns-chat-a"], "declare question-first"

    style = root.find("Style").text
    assert ".ns-chat-body { display: flex; flex-direction: column; }" in style
    # Both lists and their containers flatten, so the entries themselves -- not the
    # controls -- are what `order` interleaves.
    for flattened in (".ns-chat-q, .ns-chat-a,", ".ns-chat-q div:has(form),",
                      ".ns-chat-q div:has(.lsf-row),", ".ns-chat-a div:has(.lsf-row)"):
        assert flattened in style

    # question N directly above answer N
    for turn in range(1, spec.CHAT_TURNS + 1):
        assert f".ns-chat-q .lsf-row:nth-of-type({turn}) {{ order: {2 * turn - 1}; }}" in style
        assert f".ns-chat-a .lsf-row:nth-of-type({turn}) {{ order: {2 * turn}; }}" in style

    # an unpaired entry lands after the pairs, and the typing bar after everything
    assert ".ns-chat-q .lsf-row, .ns-chat-a .lsf-row { order: 998; }" in style
    assert ".ns-chat-q form { order: 999;" in style
    assert 2 * spec.CHAT_TURNS < 998, "the fallback must sort below every pair"


@pytest.mark.parametrize("project", spec.PROJECTS, ids=[p.name for p in spec.PROJECTS])
def test_both_chat_controls_point_at_the_paper(project) -> None:
    """`getConnectedDynamicRegions` groups by `to_name` (mixins/Regions.js:88).

    If the two sat on different object tags the answer log would not be in the
    context of the next question, and every turn would start from nothing.
    """

    root = ElementTree.fromstring(config.build(project))
    chat_controls = [n for n in root.iter("TextArea") if n.get("name") in (Q, A)]
    assert len(chat_controls) == 2
    assert {n.get("toName") for n in chat_controls} == {"paper"}
