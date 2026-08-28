"""Read the allowlist of findings a reviewer has judged acceptable, per paper.

Both `build_record.py` and `validate_record.py` gate on it, so the matching rule lives here
once: a second copy would be a second definition of what "known" means.

The point of the file is to make a check switchable-on before every paper satisfies it.
Without one the choice is between a check nobody can turn on and a pipeline that fails on
every paper -- and the second teaches people to pass `--no-strict` permanently, which
retires the check just as thoroughly and less visibly.

See `known-gaps.yaml` for the entries and the discipline they are written to.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

#: Alongside this module, so `--known-gaps` needs no path in the common case.
DEFAULT = Path(__file__).resolve().parent / "known-gaps.yaml"

#: A finding's path, with list indices removed, so one entry covers
#: `analyses[0].details.seed_regions` through `analyses[3]`.
_INDEX = re.compile(r"\[\d+\]")


def load(path: Path | None, paper: str) -> list[tuple[str, str]]:
    """`(index-stripped path, message substring)` pairs tolerated for this paper.

    A missing file is not an error: an empty allowlist is the goal state, not a
    misconfiguration.
    """

    if path is None or not Path(path).is_file():
        return []
    import yaml  # noqa: PLC0415  (only needed when an allowlist is supplied)

    document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [
        (str(entry.get("path") or ""), str(entry.get("message") or ""))
        for entry in document.get("gaps") or []
        if entry.get("paper") == paper
    ]


def covers(finding: str, gaps: Sequence[tuple[str, str]]) -> bool:
    """Whether an entry covers this finding.

    Both halves are matched as substrings of the finding with its list indices stripped,
    rather than against a parsed path. The findings this reads come from three different
    formatters and wear three different shapes -- `path: message`, `path -> message`, and a
    bare `local_id 'x' is declared twice` with no path at all -- so parsing a path out means
    knowing all three, and getting one wrong means silently suppressing nothing.

    An entry matching on neither path nor message would suppress everything, so it matches
    nothing instead: an allowlist that quietly swallows the whole report is the one failure
    mode here worth being paranoid about.
    """

    stripped = _INDEX.sub("", finding)
    for wanted_path, wanted_message in gaps:
        if not wanted_path and not wanted_message:
            continue
        if wanted_path and wanted_path not in stripped:
            continue
        if wanted_message and wanted_message not in stripped:
            continue
        return True
    return False


def partition(
    findings: Sequence[str], gaps: Sequence[tuple[str, str]]
) -> tuple[list[str], list[str]]:
    """`(still reported, suppressed)`, in the order given."""

    reported = [finding for finding in findings if not covers(finding, gaps)]
    suppressed = [finding for finding in findings if covers(finding, gaps)]
    return reported, suppressed
