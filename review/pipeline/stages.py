"""The steps, each one an object that knows what it needs and what it leaves behind.

A stage is not just a function to call. It has a precondition, an artefact that proves it
already ran, a cost, and a postcondition worth checking before the next stage trusts it.
Those four things were previously an `if "name" in args.stages:` block, a subprocess call
and a printed line, which meant "why did this stage not run" and "what did it cost" were
answered by reading a log with a regular expression.

Model passes still run as subprocesses. That is deliberate and not a compromise: each is a
separate process with its own retry budget, and a pass that exhausts its attempts should
not be able to take the driver down with it. What changed is that the subprocess is
wrapped in an object that declares the contract, so the driver never has to know how any
particular pass is invoked.
"""

from __future__ import annotations

import re
import subprocess
import zlib
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .kinds import DONE, FAILED, SKIPPED, Cost, Paper, StageOutcome, TableParse

REVIEW = Path(__file__).resolve().parent.parent

#: `84rGLhCbUJTh/demands: 38471->2444 tok (reasoning 512) in 18s`. Every model pass prints
#: this shape, which is the only reason a run can be priced at all -- `usage.jsonl` is
#: written by the evidence stage alone.
TOKEN_LINE = re.compile(r"^\s*\S+/(\w+): (\d+)->(\d+) tok.*?in ([\d.]+)s", re.M)


@dataclass
class Settings:
    """Everything the stages need that is not the paper."""

    payloads: Path
    records: Path
    key_file: Path
    model: str
    effort: str = "low"
    max_attempts: int = 3
    #: Devices the evidence stage may use, cycled per paper. One device shared by nine
    #: workers exhausted an 8GB card; a list lets the driver spread them without every
    #: stage having to know how many workers there are.
    reranker_device: str = "cpu"
    reranker_devices: tuple[str, ...] = ()
    union: bool = True
    zero_foci_rule: bool = True
    redo: bool = False

    def device_for(self, paper: Paper) -> str:
        """A device for this paper, spread deterministically over those available.

        `crc32` and not `hash`: Python randomises string hashing per process, so `hash`
        would give a resumed run a different assignment from the one that wrote the
        payloads -- and a spread that cannot be reproduced cannot be debugged.
        """
        pool = self.reranker_devices or (self.reranker_device,)
        return pool[zlib.crc32(paper.study_id.encode()) % len(pool)]

    def payload_dir(self, paper: Paper) -> Path:
        return self.payloads / paper.study_id

    def record_path(self, paper: Paper) -> Path:
        return self.records / f"{paper.study_id}.extraction.json"


class Stage:
    """One step. Subclasses say what it produces, whether it is done, and how to run it."""

    name = "stage"
    #: What this stage needs from its predecessors, for the driver's own error message.
    needs: tuple[str, ...] = ()

    def produces(self, paper: Paper, settings: Settings) -> Path:
        raise NotImplementedError

    def is_done(self, paper: Paper, settings: Settings) -> bool:
        return self.produces(paper, settings).exists()

    def perform(self, paper: Paper, settings: Settings) -> StageOutcome:
        raise NotImplementedError

    def run(self, paper: Paper, settings: Settings) -> StageOutcome:
        if not settings.redo and self.is_done(paper, settings):
            return StageOutcome(self.name, paper.study_id, SKIPPED,
                                notes=[f"{self.produces(paper, settings)} exists"])
        try:
            return self.perform(paper, settings)
        except Exception as error:  # noqa: BLE001 -- one paper must not end the run
            return StageOutcome(self.name, paper.study_id, FAILED,
                                error=f"{type(error).__name__}: {error}")


class Subprocess(Stage):
    """A stage that runs one of the review scripts and reads its cost off its output."""

    script = ""

    def command(self, paper: Paper, settings: Settings) -> list[str]:
        raise NotImplementedError

    def perform(self, paper: Paper, settings: Settings) -> StageOutcome:
        argv = [sys.executable, str(REVIEW / self.script), *self.command(paper, settings)]
        started = time.time()
        finished = subprocess.run([str(a) for a in argv], capture_output=True, text=True)
        output = finished.stdout + finished.stderr
        cost = self.price(output, time.time() - started)
        if finished.returncode != 0:
            tail = "\n".join(output.strip().splitlines()[-4:])
            return StageOutcome(self.name, paper.study_id, FAILED, cost,
                                error=f"exit {finished.returncode}\n{tail}")
        return StageOutcome(self.name, paper.study_id, DONE, cost,
                            notes=self.notes(output))

    def price(self, output: str, seconds: float) -> Cost:
        total = Cost(seconds=seconds)
        for _stage, prompt, completion, _took in TOKEN_LINE.findall(output):
            total = total + Cost(int(prompt), int(completion), calls=1)
        return Cost(total.prompt_tokens, total.completion_tokens, 0, total.calls, seconds)

    def notes(self, output: str) -> list[str]:
        return []


class SignSplit(Stage):
    """Partition any table parse that reports both signs, and withhold the reversed half.

    Runs before anything reads the parse, because it changes what the extraction pass is
    shown. A table holding effects of both signs is two contrasts and only one of them has
    prose in the paper: the positive half keeps the parsed name and is extracted, the
    negative half is marked `withhold` and rebuilt afterwards by `mirror_withheld`.

    Idempotent. A parse already carrying the flag is left alone, so a resumed run does not
    re-partition parts that each hold one sign.
    """

    name = "split"

    def produces(self, paper: Paper, settings: Settings) -> Path:
        return paper.stage1_path

    def is_done(self, paper: Paper, settings: Settings) -> bool:
        """Both the partition and the withholding, not just the flag.

        `sign_split_applied` only says the sign rule ran. A corpus partitioned before the
        mirror existed carries that flag and still holds both halves as ordinary entries,
        so trusting the flag alone skips the very papers the mirror was built for.
        """
        if not paper.stage1_path.is_file():
            return False
        import copy  # noqa: PLC0415
        import sys as _sys  # noqa: PLC0415

        _sys.path.insert(0, str(REVIEW))
        from parse_tables import adopt_withholding  # noqa: PLC0415

        parse = TableParse.load(paper.stage1_path)
        if not parse.sign_split_applied:
            return False
        _analyses, converted = adopt_withholding(
            copy.deepcopy(parse.document.get("analyses") or []))
        return not converted

    def perform(self, paper: Paper, settings: Settings) -> StageOutcome:
        sys.path.insert(0, str(REVIEW))
        from parse_tables import (adopt_withholding,  # noqa: PLC0415
                                  split_opposite_signs)

        parse = TableParse.load(paper.stage1_path)
        before = parse.document.get("analyses") or []
        after, notes = split_opposite_signs(before)
        # A corpus partitioned before the mirror existed holds both halves as ordinary
        # entries. Re-splitting cannot reach them -- each part already holds one sign --
        # so the pair is converted from what the parts themselves record.
        after, adopted = adopt_withholding(after)
        parse.replace_analyses(after)
        parse.save()
        withheld = len([a for a in after if a.get("withhold")])
        return StageOutcome(self.name, paper.study_id, DONE, Cost(),
                            notes=notes + [f"{len(before)} -> {len(after)} analyses, "
                                           f"{withheld} withheld from the model"])


class Tables(Stage):
    """Copy the table manifest into Table records. No model, and first.

    `table_number`, `caption` and `footer` are literal strings in the parse manifest, so
    retyping them through a model can only introduce error. It runs first because the
    analyses pass is told the local_ids it assigns, and every `Analysis.tables` reference
    points at one of them.

    Omitting this stage is the regression that motivated writing it down: the rewritten
    pipeline dropped it, and 155 of 156 schizophrenia records ended up with no tables
    declared while 1,076 of 1,084 analyses referenced one. Direction scoring never
    noticed -- polarity needs the parse, not the Table entity -- so the fault was
    invisible until a coordinate query asked for the join.

    The manifest is read from the same flavour the text came from. The earlier
    implementation hardcoded `processed/pubget/tables.jsonl`, which no paper in a corpus
    staged from `ace` or `elsevier` has.
    """

    name = "tables"

    def produces(self, paper: Paper, settings: Settings) -> Path:
        return settings.payload_dir(paper) / "tables.json"

    def perform(self, paper: Paper, settings: Settings) -> StageOutcome:
        import json  # noqa: PLC0415

        manifest = paper.text_path.parent / "tables.jsonl"
        if not manifest.is_file():
            return StageOutcome(self.name, paper.study_id, DONE, Cost(),
                                notes=[f"no tables.jsonl beside {paper.flavour} text; "
                                       f"no Table records to copy"])
        tables, id_map = [], {}
        for index, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(),
                                     start=1):
            if not line.strip():
                continue
            source = json.loads(line)
            # Keyed on the manifest's own table_id so the identity map the staging wrote
            # keeps holding, and positionally only when it has none. `table_number` is
            # not an identifier: one paper in the corpus carries two tables numbered 1.
            local_id = str(source.get("table_id") or f"tbl{index}")
            id_map[str(source.get("table_id") or local_id)] = local_id
            metadata = source.get("metadata") or {}
            label = metadata.get("table_label") or (
                f"Table {source['table_number']}" if source.get("table_number") else None)

            def wrap(text):
                if text in (None, ""):
                    return {"extraction_status": "not_reported",
                            "evidence": {"status": "not_applicable"}}
                return {"extraction_status": "extracted", "value": text,
                        "value_source": "reported",
                        "evidence": {"status": "not_applicable"}}

            tables.append({"local_id": local_id, "table_number": wrap(label),
                           "caption": wrap(source.get("caption")),
                           "footer": wrap(source.get("footer"))})

        target = self.produces(paper, settings)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"tables": tables}, indent=1, ensure_ascii=False)
                          + "\n", encoding="utf-8")
        paper.table_map_path.parent.mkdir(parents=True, exist_ok=True)
        paper.table_map_path.write_text(json.dumps(id_map, indent=1) + "\n",
                                        encoding="utf-8")
        return StageOutcome(self.name, paper.study_id, DONE, Cost(),
                            notes=[f"{len(tables)} Table record(s) copied from "
                                   f"{paper.flavour}/tables.jsonl (deterministic)"])


class Demands(Subprocess):
    """Analyses first: each declares the entities it needs, before any exist."""

    name = "demands"
    script = "extract_record.py"
    needs = ("tables",)

    def produces(self, paper: Paper, settings: Settings) -> Path:
        return settings.payload_dir(paper) / "demands" / "requirements.json"

    def command(self, paper: Paper, settings: Settings) -> list[str]:
        return ["--mode", "demands", "--paper", paper.study_id,
                "--text", paper.text_path, "--out-dir", settings.payloads,
                "--stage1", paper.stage1_path, "--tables", paper.table_map_path,
                "--key-file", settings.key_file, "--model", settings.model,
                "--effort", settings.effort, "--max-attempts", settings.max_attempts,
                "--no-evidence", *(["--zero-foci-rule"] if settings.zero_foci_rule else [])]


class Satisfy(Subprocess):
    """Build exactly the entities the demands pass asked for, and nothing else."""

    name = "satisfy"
    script = "extract_record.py"
    needs = ("demands",)

    def produces(self, paper: Paper, settings: Settings) -> Path:
        return settings.payload_dir(paper) / "entities.json"

    def command(self, paper: Paper, settings: Settings) -> list[str]:
        return ["--mode", "satisfy", "--paper", paper.study_id,
                "--text", paper.text_path, "--out-dir", settings.payloads,
                "--key-file", settings.key_file, "--model", settings.model,
                "--effort", settings.effort, "--max-attempts", settings.max_attempts,
                "--no-evidence", "--requirements",
                settings.payload_dir(paper) / "demands" / "requirements.json"]


class Evidence(Subprocess):
    """A supporting quote for every value, from the model and from the retriever.

    Two locators, unioned. The model reads the whole paper -- handing it a retrieved
    shortlist instead was measured and cost 21 points -- and the retriever contributes a
    second span when it clears its own gate, at no marginal cost because it runs locally.
    """

    name = "evidence"
    script = "add_evidence.py"
    needs = ("satisfy",)

    def produces(self, paper: Paper, settings: Settings) -> Path:
        return settings.payload_dir(paper) / "noev"

    def is_done(self, paper: Paper, settings: Settings) -> bool:
        """Did the evidence actually get written, not merely started?

        `noev/` is the pre-evidence backup and it is created *before* the work, so its
        presence proves the stage began. Seventeen papers crashed after that point when
        nine workers exhausted one GPU, and a resume skipped every one of them and built
        records with no evidence at all -- a marker that means "started" read as "done".
        The payloads themselves are the only honest signal.
        """

        import json  # noqa: PLC0415

        if not self.produces(paper, settings).exists():
            return False
        for payload in settings.payload_dir(paper).glob("*.json"):
            if payload.name == "aliases.json":
                continue
            try:
                body = payload.read_text(encoding="utf-8")
            except OSError:
                return False
            if '"evidence"' in body:
                return True
        return False

    def command(self, paper: Paper, settings: Settings) -> list[str]:
        return ["--paper", paper.study_id, "--text", paper.text_path,
                "--payloads", settings.payload_dir(paper),
                "--key-file", settings.key_file, "--model", settings.model,
                "--effort", settings.effort,
                "--reranker-device", settings.device_for(paper),
                *([] if settings.union else ["--no-union"])]

    def price(self, output: str, seconds: float) -> Cost:
        # The evidence pass prints one summary line rather than one per call.
        found = re.search(r"(\d+)->(\d+) tok", output)
        if not found:
            return Cost(seconds=seconds)
        return Cost(int(found.group(1)), int(found.group(2)), calls=1, seconds=seconds)

    def notes(self, output: str) -> list[str]:
        return [line.strip() for line in output.splitlines() if "evidence:" in line]


class Build(Stage):
    """Merge the payloads, repair, mirror the withheld halves, resolve quotes to offsets.

    Run in-process rather than as a subprocess: it is deterministic, it returns a report
    worth inspecting, and its repair sequence is the part of the pipeline most often
    asked questions about.
    """

    name = "build"
    needs = ("evidence",)

    def produces(self, paper: Paper, settings: Settings) -> Path:
        return settings.record_path(paper)

    def perform(self, paper: Paper, settings: Settings) -> StageOutcome:
        sys.path.insert(0, str(REVIEW))
        import json  # noqa: PLC0415
        from datetime import date  # noqa: PLC0415

        import build_record  # noqa: PLC0415

        started = time.time()
        record, report = build_record.build(
            paper.study_id, paper.text_path, settings.payload_dir(paper),
            extractor_model=settings.model, extractor_version="pipeline-1",
            extraction_date=date.today().isoformat(),
            stage1=paper.stage1_path, table_map=paper.table_map_path)
        settings.records.mkdir(parents=True, exist_ok=True)
        settings.record_path(paper).write_text(
            json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

        notes = [f"repairs: {', '.join(report.repair_log.fired()) or 'none fired'}"]
        if report.failures:
            notes.append(f"{len(report.failures)} quote(s) did not resolve")
        if report.dangling:
            notes.append(f"{len(report.dangling)} cross-reference(s) need a human")
        # A defect is a note, never a failure. The record is written either way, and a
        # dangling reference is a field for a reviewer rather than a paper to discard --
        # treating it as a failure is what made five of sixteen papers read as lost when
        # all sixteen had been built and scored.
        return StageOutcome(self.name, paper.study_id, DONE,
                            Cost(seconds=time.time() - started), notes=notes)


#: The baseline, in order. Reading this list is meant to be the whole explanation of what
#: the pipeline does.
BASELINE: tuple[Stage, ...] = (Tables(), SignSplit(), Demands(), Satisfy(), Evidence(),
                               Build())
