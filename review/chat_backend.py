#!/usr/bin/env python3
"""An ML backend that answers a reviewer's questions about the paper in front of them.

Label Studio's open-source edition has no way to put a chat widget in the labeling
interface -- custom JavaScript there is an Enterprise feature. What it does have is
the interactive-preannotation round trip, and that is enough:

    reviewer submits `chat_q`
      -> TextArea.onChange -> notifyDrawingFinished (tags/control/TextArea:227)
      -> `regionFinishedDrawing` (mixins/Regions.js:266)
      -> POST /api/ml/<pk>/interactive-annotating  {task, context: {result: [...]}}
         (DataManager.jsx:157-191; `result` is every textarea region on `paper`)
      -> LS POSTs /predict here with that context (ml/api_connector.py:209-226)
      -> we answer, and return a `chat_a` textarea result
      -> LS takes `results[0]` as suggestions (ml/models.py:396-409), and because
         `<Text>` has supportSuggestions=false it accepts them without a click
         (Annotation.js:1183-1192)

So the answer lands in the annotation itself. That is the reason to do it this way
rather than in a side panel: what a reviewer had to ask before deciding is part of
the provenance of the decision, and it exports with the annotation.

## The two invariants this depends on

`chat_q` is the ONLY control in these configs with `smart="true"`. Every other
TextArea is generated with `smart="false"` (`config_gen._textarea`), because `smart`
defaults to true and an unmarked `comment` would fire the same round trip.

A TextArea's serialized result holds ALL of its submissions in one list
(`TextArea.jsx:144, selectedValues()`), and accepting a suggestion REPLACES the
control's whole area (`Annotation.js:1164-1195`). So the reply must carry the full
answer log, not just the new answer -- returning one answer would erase the rest.

## Caching

Two kinds, both worth it because the paper is ~100k tokens and the question is ~20:

  in-process   the staged text is read once per (path, mtime, size)
  prefix       the model prompt is built instructions -> paper -> task -> history
               -> question, most stable part first, so every turn after the first
               reuses the paper prefix, and every task on the same paper reuses it
               too. `prompt_cache_key` is sent so the gateway routes a paper's
               traffic consistently; a gateway that rejects the parameter is
               detected once and it is dropped for the rest of the run.

Run it on the host, next to the other scripts in this directory:

    python3 review/chat_backend.py --key-file .env

then in each project: Settings > Model > Connect Model,
`http://host.docker.internal:9090`, with **Interactive preannotations** on. The
`extra_hosts` line in `review/docker-compose.override.yml` is what makes that
hostname resolve from the app container on Linux. Reviewers must also switch on
Auto-Annotation in the labeling view -- without it `smartEnabled` is false and
nothing is sent (tags/control/Base.js:62-66).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_gen import CHAT_ANSWER, CHAT_QUESTION  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
REVIEW = Path(__file__).resolve().parent

#: The extractor's model, on purpose. A reviewer asking "why did it say that?"
#: should be asking the model that said it.
DEFAULT_MODEL = "@psyc-aid338-ope-333f18/gpt-5.6-luna"
DEFAULT_EFFORT = "low"

#: Label Studio gives a /predict call 100s (`ML_TIMEOUT_PREDICT`, api_connector:27)
#: and shows the reviewer nothing if that passes. Answering "the call timed out" is
#: better than that, and it keeps the question/answer lists in step, so the default
#: sits under LS's and the override raises LS's to leave room.
DEFAULT_TIMEOUT = 150.0

#: Subdirectory of the files root holding one text per paper. Kept in step with
#: `to_labelstudio._TEXT_SUBDIR`; not imported from there because that module pulls
#: in the schema stack and this one is standard library plus `openai`.
_TEXT_SUBDIR = "texts"

#: Task-data keys that say nothing about the object under review. Everything else
#: is rendered, whatever shape it has -- the arrays ARE the object for two of the
#: four task kinds, and a filter that kept only short scalars left a structure task
#: telling the model nothing but the paper id and the word "contrast".
#:
#:   paper_url                 the text is sent whole; the URL is plumbing
#:   content_hash, review_key  addresses, not content
#:   *_labels                  the span layer's label chips; the objects they name
#:                             are already rendered from the rows
#:   columns                   the relationship grid's headers, whose descriptors
#:                             the rendered rows already carry
#:   entity_table              the legend; `entity_rows` is the same data with the
#:                             local_id kept
#:
#: A key not named here is rendered even if it is unrecognised: an exporter that
#: grows a field should reach the model by default and be excluded on purpose.
#: `table_html` is the one entry here that is not plumbing: it is the object under
#: review on a contrast task. It is skipped because `_inline` does not truncate, and a
#: rendered coordinate table runs to ~20 KB of markup -- 255 KB across one paper's
#: contrast tasks -- which would be pasted into the prompt of every chat turn in the
#: project. The same table reaches the model in readable form through `sibling_rows`
#: and `contrast[].parsed`, which is what a question about it would actually cite.
_SKIP_CONTEXT_KEYS = frozenset({
    "paper_url", "content_hash", "review_key", "paper_text_hash",
    "span_labels", "structure_labels", "link_labels", "columns", "entity_table",
    "table_html",
})

SYSTEM = """You are helping a curator review a structured extraction of one \
neuroimaging paper. They have the paper open and a single extracted field, link or \
analysis in front of them, and they will ask you about it.

Answer only from the paper text below. When you assert something the paper says, \
quote the sentence verbatim so they can find it. When the paper does not report \
what they asked about, say exactly that -- "the paper does not report X" is the \
most useful answer you can give a curator, and guessing is the least. Do not \
propose a value for the field; the judgement is theirs.

Be brief. A few sentences and a quote, not an essay."""


def load_key_file(path: Path) -> list[str]:
    """Read a shell-style env file into os.environ. Values are never printed.

    A copy of `extract_record.load_key_file`, for the reason `parse_tables.py`
    keeps its own: importing that module pulls in build_record, schema_utils and
    PyYAML, and this server has no other use for any of them.
    """

    names = []
    for raw in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip("'\"")
        os.environ[name] = value
        names.append(name)
    return names


# ------------------------------------------------------------------ the context

def texts_for(results: list[dict[str, Any]], from_name: str) -> list[str]:
    """Every submission of one TextArea control, oldest first.

    The control's whole history is one result whose `value.text` is the list
    (`TextArea.jsx:144`), so this is a lookup and not an accumulation -- but the
    context also carries every OTHER textarea on `paper`, because the group is
    filtered by region type and `to_name` only (`mixins/Regions.js:77-91`). The
    reviewer's `comment` arrives here too and is not ours to read.
    """

    for result in results:
        if not isinstance(result, dict) or result.get("from_name") != from_name:
            continue
        value = result.get("value")
        if not isinstance(value, dict):
            continue
        text = value.get("text")
        if isinstance(text, str):
            return [text]
        if isinstance(text, list):
            return [str(item) for item in text]
    return []


def align(questions: list[str], answers: list[str]) -> tuple[list[str], str | None]:
    """(answers padded so answers[i] answers questions[i], the live question).

    The pairing is positional and nothing in the payload timestamps it, so it only
    holds while every question has exactly one answer. A call that never returned
    -- LS gave up at 100s, the process was restarted mid-question -- would shift
    every later pair by one and the model would be shown someone else's answer to
    its own question. Padding the gap keeps the alignment true and says out loud
    what happened, which also lets the reviewer see the turn that was lost.
    """

    if len(questions) <= len(answers):
        return answers, None
    padded = list(answers) + ["(unanswered)"] * (len(questions) - len(answers) - 1)
    return padded, questions[-1]


def _inline(value: Any) -> str:
    """One value on one line. Newlines fold to spaces; a paraphrase is prose, not layout."""

    return " ".join(str(value).split())


def _render(value: Any, indent: int = 0) -> list[str]:
    """Any exporter value as lines, without needing to know which key it came from.

    The exporter emits three shapes -- a scalar, a ROWS array of flat records, and
    a record whose own field is another array (`terms[].levels`). One recursive
    walk covers all three and whatever it grows next, which is the point: the
    previous version tested `isinstance(value, (str, int, float, bool))` and so
    dropped every ROWS array in the contract.
    """

    pad = "  " * indent
    if isinstance(value, dict):
        flat = [(k, _inline(v)) for k, v in value.items()
                if not isinstance(v, (list, dict))]
        head = " · ".join(f"{k}={text}" for k, text in flat if text)
        lines = [f"{pad}- {head}"] if head else []
        for key, nested in value.items():
            if isinstance(nested, (list, dict)) and nested:
                lines.append(f"{pad}  {key}:")
                lines += _render(nested, indent + 2)
        return lines
    if isinstance(value, list):
        if not any(isinstance(item, (list, dict)) for item in value):
            joined = ", ".join(_inline(item) for item in value if _inline(item))
            return [f"{pad}{joined}"] if joined else []
        lines = []
        for item in value:
            lines += _render(item, indent)
        return lines
    text = _inline(value)
    return [f"{pad}{text}"] if text else []


def task_context(data: dict[str, Any]) -> str:
    """What the reviewer is being asked to judge.

    Everything the task carries except the plumbing in _SKIP_CONTEXT_KEYS, because
    for a relationship or structure task the object under review IS an array --
    the candidate targets, the model's terms, the contrast's cells. Judged by shape
    instead, those tasks reached the model as little more than a paper id, and it
    answered from the paper alone with no idea what it was being asked about.

    It sits below the paper in the prompt, so the paper's prefix still caches
    across a paper's tasks however much of this there turns out to be. Measured on
    a real record it is 0.2-2 KB against a ~40 KB paper.
    """

    lines: list[str] = []
    for key, value in data.items():
        if key in _SKIP_CONTEXT_KEYS:
            continue
        rendered = _render(value)
        if not rendered:
            continue                     # empty string, or a Repeater gate that is []
        if len(rendered) == 1 and not rendered[0].startswith(("-", " ")):
            lines.append(f"{key}: {rendered[0]}")
        else:
            lines.append(f"{key}:")
            lines += rendered
    return "\n".join(lines)


# -------------------------------------------------------------------- the model

class Chat:
    """Holds the client, the paper cache, and what the gateway has been found to accept."""

    def __init__(self, model: str, effort: str, files_root: Path, timeout: float,
                 max_chars: int) -> None:
        self.model = model
        self.effort = effort
        self.files_root = files_root
        self.timeout = timeout
        self.max_chars = max_chars
        self.model_version = f"ns-chat {model.rsplit('/', 1)[-1]} effort={effort}"
        self._papers: dict[str, tuple[tuple[float, int], str]] = {}
        self._lock = threading.Lock()
        self._send_cache_key = True
        self._client: Any = None

    @property
    def client(self) -> Any:
        """Built on first use, so the parts that shape a prompt are testable without a key.

        `main()` touches this once at startup, so a missing `openai` or a bad key
        is still a startup failure rather than a failed question.
        """

        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=os.environ["OPENAI_API_KEY"],
                base_url=os.environ.get("OPENAI_API_GATEWAY"),
                timeout=self.timeout,
            )
        return self._client

    # -- the paper ----------------------------------------------------------

    def text_path(self, data: dict[str, Any]) -> Path:
        """Where the staged text for this task lives, from the URL the config renders.

        `paper_url` is `/data/local-files/?d=texts/<id>.txt` -- the same file the
        browser fetches, read off disk here instead of through Label Studio, so no
        token is needed and the bytes are the ones the offsets address. Falling
        back to `paper_id` covers a task exported before the URL was staged.
        """

        url = str(data.get("paper_url") or "")
        relative = ""
        if url:
            query = urllib.parse.urlparse(url).query
            relative = (urllib.parse.parse_qs(query).get("d") or [""])[0]
        if not relative:
            paper_id = str(data.get("paper_id") or "")
            if not paper_id:
                raise ValueError("task carries neither paper_url nor paper_id")
            relative = f"{_TEXT_SUBDIR}/{paper_id}.txt"

        path = (self.files_root / relative).resolve()
        # LOCAL_FILES_SERVING_ENABLED serves anything under the document root and
        # this reads from the same tree; `d` arrives from task data, so it is
        # confined here rather than trusted.
        if not path.is_relative_to(self.files_root.resolve()):
            raise ValueError(f"{relative!r} escapes the files root")
        return path

    def paper(self, data: dict[str, Any]) -> str:
        """The staged text, read once per (path, mtime, size).

        Keyed on the stat rather than the id alone: re-running the exporter
        restages the text in place, and a cache that survived that would answer
        from the version the reviewer is no longer looking at.
        """

        path = self.text_path(data)
        stat = path.stat()
        stamp = (stat.st_mtime, stat.st_size)
        key = str(path)
        with self._lock:
            cached = self._papers.get(key)
            if cached and cached[0] == stamp:
                return cached[1]
        text = path.read_text(encoding="utf-8")[: self.max_chars]
        with self._lock:
            self._papers[key] = (stamp, text)
        return text

    # -- the call -----------------------------------------------------------

    def messages(self, paper: str, context: str, history: list[tuple[str, str]],
                 question: str) -> list[dict[str, str]]:
        """Most stable content first, so the prefix cache has something to hit.

        The system message is instructions and paper and NOTHING else, which makes
        it byte-identical for every one of a paper's ~50 tasks. Which task is open
        rides on the live question instead, where it costs ~40 tokens and changes
        nothing upstream of it.

        That split was measured, not assumed. With the task block in the system
        message the gateway reported `cached_tokens: 0` on the first question of
        every new task -- three for three -- because the prefix diverged before the
        paper ended. It only rewarded an exact continuation of a previous prompt,
        which is the second-turn case. Moving the block below the paper is what
        gives the first question on the next task something to hit.
        """

        messages = [{"role": "system", "content": f"{SYSTEM}\n\n# Paper\n\n{paper}"}]
        for asked, answered in history:
            messages.append({"role": "user", "content": asked})
            messages.append({"role": "assistant", "content": answered})
        if context:
            question = f"# The task open in front of me\n\n{context}\n\n{question}"
        messages.append({"role": "user", "content": question})
        return messages

    def ask(self, paper_id: str, messages: list[dict[str, str]]) -> tuple[str, dict]:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.effort:
            kwargs["reasoning_effort"] = self.effort
        if self._send_cache_key and paper_id:
            kwargs["prompt_cache_key"] = paper_id

        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as error:  # noqa: BLE001 -- narrowed by inspection below
            if self._send_cache_key and "prompt_cache_key" in str(error):
                # An older or stricter gateway. Learn it once rather than per call.
                self._send_cache_key = False
                kwargs.pop("prompt_cache_key", None)
                response = self.client.chat.completions.create(**kwargs)
            else:
                raise

        usage = response.usage
        details = getattr(usage, "prompt_tokens_details", None)
        return response.choices[0].message.content or "", {
            "prompt_tokens": usage.prompt_tokens,
            "cached_tokens": getattr(details, "cached_tokens", None) if details else None,
            "completion_tokens": usage.completion_tokens,
        }

    # -- one interactive round trip ------------------------------------------

    def answer(self, task: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
        """One /predict, as the reply Label Studio will accept as suggestions."""

        data = task.get("data") or {}
        paper_id = str(data.get("paper_id") or "")
        questions = texts_for(results, CHAT_QUESTION)
        answers, question = align(questions, texts_for(results, CHAT_ANSWER))

        if question is None:
            # Every question already has an answer, so this notification came from
            # something that is not a new question -- an edit, a deletion, another
            # textarea. Returning no result clears the suggestions and changes
            # nothing (Annotation.js:1167).
            self.log(f"{paper_id}: no unanswered question ({len(questions)}q); ignored")
            return {"model_version": self.model_version, "score": 0.0, "result": []}

        started = time.time()
        try:
            paper = self.paper(data)
            messages = self.messages(
                paper, task_context(data), list(zip(questions, answers)), question)
            reply, usage = self.ask(paper_id, messages)
            self.log(
                f"{paper_id}: turn {len(questions)} "
                f"{usage['prompt_tokens']}->{usage['completion_tokens']} tok "
                f"(cached {usage['cached_tokens']}) in {time.time() - started:.0f}s")
        except Exception as error:  # noqa: BLE001
            # Reported as the answer, not as a 500. A 500 reaches the reviewer as a
            # bare Data Manager error with the question still unanswered, which then
            # shifts every later pair; this keeps the log in step and says what
            # happened where they are looking.
            reply = f"(the assistant failed to answer: {type(error).__name__}: {error})"
            self.log(f"{paper_id}: FAILED after {time.time() - started:.0f}s: {error}")

        return {
            "model_version": self.model_version,
            "score": 1.0,
            # The whole log: accepting a suggestion replaces the control's area.
            "result": [{
                "from_name": CHAT_ANSWER,
                "to_name": "paper",
                "type": "textarea",
                "value": {"text": answers + [reply]},
            }],
        }

    def predict(self, body: dict[str, Any]) -> dict[str, Any]:
        tasks = body.get("tasks") or []
        context = ((body.get("params") or {}).get("context")) or {}
        results = context.get("result") or []

        if not results:
            # A batch /predict: LS asking for pre-annotations over a task list
            # rather than a reviewer asking a question. There is nothing to
            # pre-annotate here, and answering would write an unprompted `chat_a`
            # onto every task.
            return {"results": [{"model_version": self.model_version, "score": 0.0,
                                 "result": []} for _ in tasks]}
        return {"results": [self.answer(task, results) for task in tasks]}

    @staticmethod
    def log(message: str) -> None:
        print(message, flush=True)


# ---------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    """The four endpoints `ml/api_connector.py` reaches for, and no more."""

    protocol_version = "HTTP/1.1"

    @property
    def chat(self) -> Chat:
        return self.server.chat  # type: ignore[attr-defined]

    def _send(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's spelling
        route = urllib.parse.urlparse(self.path).path.rstrip("/")
        if route == "/health":
            # Answered from memory: TIMEOUT_HEALTH is one second.
            self._send({"status": "UP", "model_version": self.chat.model_version})
        elif route == "/versions":
            self._send({"versions": [self.chat.model_version]})
        else:
            self._send({"error": f"no route {route}"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        route = urllib.parse.urlparse(self.path).path.rstrip("/")
        try:
            if route == "/predict":
                self._send(self.chat.predict(self._body()))
            elif route == "/setup":
                self._send({"model_version": self.chat.model_version})
            elif route == "/validate":
                self._send({"status": "ok"})
            elif route in ("/train", "/webhook"):
                self._send({"status": "ok"})
            else:
                self._send({"error": f"no route {route}"}, status=404)
        except Exception as error:  # noqa: BLE001
            # Only the framing gets here; a model failure is answered, not raised.
            self.chat.log(f"{route}: {type(error).__name__}: {error}")
            self._send({"error": f"{type(error).__name__}: {error}"}, status=500)

    def log_message(self, fmt: str, *args: Any) -> None:
        """Quiet: every request is a health check until it is not, and predict logs itself."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9090)
    parser.add_argument("--files-root", type=Path, default=REVIEW / "ls_files",
                        help="the LOCAL_FILES_DOCUMENT_ROOT the exporter staged texts into")
    parser.add_argument("--key-file", type=Path, default=REPO / ".env")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default=DEFAULT_EFFORT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--max-chars", type=int, default=200_000,
                        help="matches extract_record.py, so the reviewer and the "
                             "extractor were shown the same paper")
    args = parser.parse_args()

    if args.key_file and args.key_file.is_file():
        load_key_file(args.key_file)
    if not os.environ.get("OPENAI_API_KEY"):
        print(f"no OPENAI_API_KEY; expected it in {args.key_file}", file=sys.stderr)
        return 2
    if not args.files_root.is_dir():
        print(f"no files root at {args.files_root}", file=sys.stderr)
        return 2

    chat = Chat(args.model, args.effort, args.files_root, args.timeout, args.max_chars)
    chat.client  # noqa: B018 -- fail here rather than on a reviewer's first question
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.chat = chat  # type: ignore[attr-defined]
    print(f"{chat.model_version} on http://{args.host}:{args.port} "
          f"serving papers from {args.files_root}", flush=True)
    print("connect it at Settings > Model with Interactive preannotations on, "
          "then switch on Auto-Annotation in the labeling view", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
