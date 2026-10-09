from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
STUDY = "x7Ex4mpleA01"


@pytest.fixture
def example_paths() -> dict[str, Path]:
    paper = EXAMPLES / "corpus" / STUDY
    return {
        "bundle": paper,
        "parsed_paper": paper / "parse" / "parsed_paper.json",
        "coordinate_parse": paper / "parse" / "coordinate_parse.json",
        "record": EXAMPLES / "run" / "records" / f"{STUDY}.extraction.json",
        "revision": EXAMPLES / "run" / "revisions" / f"{STUDY}.coordinate_parse.json",
    }


def load(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))
