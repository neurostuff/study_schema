"""The things this pipeline is made of, named.

The extraction workflow has always had these kinds in it -- a paper, the analyses parsed
off its coordinate tables, a stage that either ran or was skipped, the tokens that cost.
They were spread across argument lists, dictionary keys and printed lines, so a question
like "which analyses were withheld from the model, and did their mirrors get built?"
could only be answered by reading a log. Here they are objects, so it can be answered by
asking one.

Nothing in this module calls a model or writes a record. It is the vocabulary the rest of
the package is written in, and it is deliberately inert so it can be built in a test
without a paper on disk.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterator

#: Text flavours, best first. `local` carries the result tables rendered inline and is
#: what the extraction passes read; the others are fallbacks for a paper the local build
#: never covered. A locator searching a table-free flavour cannot find the sentence a
#: group size was read from, so the order here is not cosmetic.
TEXT_FLAVOURS: tuple[tuple[str, str], ...] = (
    ("local", "text.tables.txt"),
    ("pubget", "text.txt"),
    ("ace", "text.txt"),
    ("elsevier", "text.txt"),
)


@dataclass(frozen=True)
class Paper:
    """One study, and where everything about it lives on disk.

    Constructed from a corpus id and a texts root, so every path the pipeline touches is
    derived in one place rather than rebuilt at each call site with a slightly different
    guess about the flavour.
    """

    study_id: str
    texts_root: Path

    @property
    def directory(self) -> Path:
        return self.texts_root / self.study_id

    @property
    def text_path(self) -> Path:
        """The best available text, preferring the flavour that carries tables."""
        for flavour, name in TEXT_FLAVOURS:
            candidate = self.directory / "processed" / flavour / name
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(f"{self.study_id}: no text under {self.directory}")

    @property
    def flavour(self) -> str:
        chosen = self.text_path
        return chosen.parent.name

    @property
    def stage1_path(self) -> Path:
        return self.directory / "stage1" / "analyses.json"

    @property
    def table_map_path(self) -> Path:
        return self.directory / "stage1" / "table-map.json"

    def text(self) -> str:
        return self.text_path.read_text(encoding="utf-8")

    def is_ready(self) -> tuple[bool, str]:
        """Whether this paper can be run at all, and why not if it cannot.

        Checked up front because the alternative is a stage failing four calls in with a
        missing-file traceback, and the paper that cannot be run is worth knowing about
        before any tokens are spent on its neighbours.
        """
        try:
            self.text_path
        except FileNotFoundError as missing:
            return False, str(missing)
        if not self.stage1_path.is_file():
            return False, f"{self.study_id}: no table parse at {self.stage1_path}"
        return True, ""


@dataclass
class ParsedAnalysis:
    """One entry from the coordinate-table parse, before any model has seen it.

    The sign split lives here rather than in a loose dict because it is the one place the
    pipeline deliberately hides work from the model: a table reporting both signs is two
    contrasts, the paper's prose describes one of them, and the other is rebuilt by
    arithmetic. `is_withheld` and `mirror_of` are what make that visible to a reader
    instead of implied by the presence of a key.
    """

    raw: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.raw.get("name") or "")

    @property
    def table_id(self) -> str:
        return str(self.raw.get("table_id") or "")

    @property
    def points(self) -> list[dict[str, Any]]:
        return self.raw.get("points") or self.raw.get("coordinates") or []

    @property
    def is_withheld(self) -> bool:
        """Kept out of the extraction prompt because the paper does not describe it."""
        return bool(self.raw.get("withhold"))

    @property
    def mirror_of(self) -> str | None:
        return self.raw.get("mirror_of")

    @property
    def split_direction(self) -> str | None:
        return self.raw.get("split_direction")

    def __repr__(self) -> str:
        mark = " [withheld]" if self.is_withheld else ""
        return f"<ParsedAnalysis {self.name!r} {len(self.points)} point(s){mark}>"


@dataclass
class TableParse:
    """Every analysis parsed from one paper's coordinate tables.

    Loaded and saved as one document so the sign-split flag lives with the analyses it
    describes: a file partitioned before that rule existed is distinguishable from one
    the rule found nothing to do in, and only the second should be left alone.
    """

    path: Path
    document: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "TableParse":
        return cls(path, json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self.document, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8")

    @property
    def analyses(self) -> list[ParsedAnalysis]:
        return [ParsedAnalysis(entry) for entry in self.document.get("analyses") or []]

    @property
    def sign_split_applied(self) -> bool:
        return bool(self.document.get("sign_split_applied"))

    def described(self) -> list[ParsedAnalysis]:
        """The analyses the extraction pass is allowed to see."""
        return [a for a in self.analyses if not a.is_withheld]

    def withheld(self) -> list[ParsedAnalysis]:
        """The reversed halves, to be rebuilt from the record after extraction."""
        return [a for a in self.analyses if a.is_withheld]

    def replace_analyses(self, entries: list[dict[str, Any]]) -> None:
        self.document["analyses"] = entries
        self.document["sign_split_applied"] = True


@dataclass(frozen=True)
class Cost:
    """What a stage spent. Addable, so a run's total is a sum and not a tally by hand."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    calls: int = 0
    seconds: float = 0.0

    def __add__(self, other: "Cost") -> "Cost":
        return Cost(self.prompt_tokens + other.prompt_tokens,
                    self.completion_tokens + other.completion_tokens,
                    self.cached_tokens + other.cached_tokens,
                    self.calls + other.calls,
                    self.seconds + other.seconds)

    def __bool__(self) -> bool:
        return bool(self.calls or self.prompt_tokens)

    def render(self) -> str:
        if not self:
            return "free"
        return (f"{self.prompt_tokens:,}->{self.completion_tokens:,} tok "
                f"in {self.seconds:.0f}s [{self.calls} call(s)]")


#: A stage did its work, found its work already done, was not asked for, or broke. Kept
#: as four states rather than a boolean because "skipped because it was already done" and
#: "failed" are the two a reader most needs to tell apart in a resumed run, and a boolean
#: collapses them.
DONE, SKIPPED, FAILED, NOT_REQUESTED = "done", "skipped", "failed", "not requested"


@dataclass
class StageOutcome:
    """What one stage did to one paper."""

    stage: str
    study_id: str
    status: str
    cost: Cost = field(default_factory=Cost)
    notes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (DONE, SKIPPED, NOT_REQUESTED)

    def render(self) -> str:
        head = f"  {self.stage:<10} {self.status:<13} {self.cost.render()}"
        if self.error:
            head += f"\n    ERROR {self.error}"
        return "\n".join([head] + [f"    {note}" for note in self.notes])


@dataclass
class PaperOutcome:
    """Every stage's outcome for one paper, in the order they ran."""

    study_id: str
    stages: list[StageOutcome] = field(default_factory=list)
    record_path: Path | None = None

    @property
    def cost(self) -> Cost:
        total = Cost()
        for outcome in self.stages:
            total = total + outcome.cost
        return total

    @property
    def ok(self) -> bool:
        return all(outcome.ok for outcome in self.stages)

    def failures(self) -> list[StageOutcome]:
        return [outcome for outcome in self.stages if not outcome.ok]

    def render(self) -> str:
        head = f"{self.study_id}  {'ok' if self.ok else 'FAILED'}  {self.cost.render()}"
        return "\n".join([head] + [outcome.render() for outcome in self.stages])


@dataclass
class RunReport:
    """The whole run: what each paper did, what it cost, and what went wrong.

    One object rather than a printed log, because every question asked of the last three
    benchmark runs -- which stage dominates cost, did this repair ever fire, which papers
    resolved every quote -- was answered by grepping stdout, and each answer had to be
    re-derived with a different regular expression.
    """

    papers: list[PaperOutcome] = field(default_factory=list)

    def __iter__(self) -> Iterator[PaperOutcome]:
        return iter(self.papers)

    @property
    def cost(self) -> Cost:
        total = Cost()
        for paper in self.papers:
            total = total + paper.cost
        return total

    def by_stage(self) -> dict[str, Cost]:
        totals: dict[str, Cost] = {}
        for paper in self.papers:
            for outcome in paper.stages:
                totals[outcome.stage] = totals.get(outcome.stage, Cost()) + outcome.cost
        return totals

    def failures(self) -> list[StageOutcome]:
        return [outcome for paper in self.papers for outcome in paper.failures()]

    def explain(self) -> str:
        """The whole run, per paper and then per stage. This is the debugging view."""
        lines = [paper.render() for paper in self.papers]
        n = max(len(self.papers), 1)
        lines += ["", f"{len(self.papers)} paper(s), "
                      f"{sum(1 for p in self.papers if p.ok)} clean", "",
                  f"{'stage':<12} {'calls':>6} {'in/paper':>11} {'out/paper':>11}"]
        for stage, cost in self.by_stage().items():
            lines.append(f"{stage:<12} {cost.calls:6d} "
                         f"{cost.prompt_tokens / n:11,.0f} {cost.completion_tokens / n:11,.0f}")
        total = self.cost
        lines.append(f"{'TOTAL':<12} {total.calls:6d} "
                     f"{total.prompt_tokens / n:11,.0f} {total.completion_tokens / n:11,.0f}")
        if self.failures():
            lines += ["", "failures:"]
            lines += [f"  {f.study_id}/{f.stage}: {f.error or f.status}"
                      for f in self.failures()]
        return "\n".join(lines)
