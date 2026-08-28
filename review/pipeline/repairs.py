"""The deterministic transformations applied to a record, in order, with their reasons.

`build_record` performs nine of these and its ordering carries real constraints -- the
direction fill matches a level against a contrast name and so must run after levels are
aligned; the mirror is taken from the corrected record and so must run last. Those
constraints lived in comments beside consecutive statements, which is a fine place to
state them and a bad place to enforce them: nothing stopped a tenth repair being inserted
in the wrong place, and nothing could report which ones fired without parsing a summary
line.

Here the sequence is data. Each repair carries what it does, why it runs where it does,
and what it changed on this record, so `RepairLog.explain()` answers "what did the builder
do to this paper" without a log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class Context:
    """What a repair may need beyond the record itself."""

    classes: Mapping[str, Any]
    stage1: Path | None = None
    table_map: Path | None = None


#: A repair reports what it changed, one line per change, and mutates the record in
#: place. An empty list means it found nothing to do, which is different from not running.
Apply = Callable[[dict, Context], "list[str]"]


@dataclass(frozen=True)
class Repair:
    """One named deterministic fix, and why it sits where it does in the order."""

    name: str
    what: str
    apply: Apply
    #: Empty when the repair may run anywhere. Stated when it may not, because an
    #: ordering constraint that is only a comment is one an edit can silently break.
    after: str = ""


@dataclass
class RepairLog:
    """What each repair did to one record."""

    entries: list[tuple[str, list[str]]] = field(default_factory=list)

    def record(self, name: str, changes: list[str]) -> None:
        self.entries.append((name, changes))

    def changes(self, name: str) -> list[str]:
        return [line for entry, lines in self.entries if entry == name for line in lines]

    @property
    def total(self) -> int:
        return sum(len(lines) for _name, lines in self.entries)

    def fired(self) -> list[str]:
        return [name for name, lines in self.entries if lines]

    def explain(self) -> str:
        if not self.total:
            return "no repairs fired"
        lines = []
        for name, changed in self.entries:
            if not changed:
                continue
            lines.append(f"{name} ({len(changed)}):")
            lines += [f"    {line}" for line in changed[:5]]
            if len(changed) > 5:
                lines.append(f"    ... and {len(changed) - 5} more")
        return "\n".join(lines)


def build_sequence() -> tuple[Repair, ...]:
    """The order, with each constraint stated next to the repair it binds.

    Imported lazily so this module can be read, and its ordering checked, without pulling
    in the schema loader.
    """

    import build_record as br  # noqa: PLC0415

    return (
        Repair("wrappers", "put a malformed ExtractedValue back into wrapper shape",
               lambda body, ctx: br.repair_wrappers(body)),
        Repair("unwrapped", "unwrap a wrapper the model put in a bare-scalar slot",
               lambda body, ctx: br.unwrap_plain_slots(body, ctx.classes),
               after="wrappers"),
        Repair("numbers", "turn a numeric string into the number its slot declares",
               lambda body, ctx: br.coerce_numeric_values(body, ctx.classes),
               after="unwrapped"),
        Repair("stray_tables", "move a Table written as a Study attribute into tables[]",
               lambda body, ctx: br.rehome_stray_tables(body, ctx.classes)),
        Repair("acquisition_type", "fill an acquisition's type from its own modality",
               lambda body, ctx: br.derive_acquisition_types(body)),
        Repair("coordinate_space", "fill the space stage 1 already read off the table",
               lambda body, ctx: br.derive_coordinate_spaces(body, ctx.stage1, ctx.table_map)),
        Repair("listified", "unwrap a nested slot the model wrote as an object",
               lambda body, ctx: br.listify_nested(body, ctx.classes)),
        Repair("listified_scalars", "wrap a lone scalar the slot declares multivalued",
               lambda body, ctx: br.listify_scalars(body, ctx.classes),
               after="listified"),
        Repair("cell_levels", "rewrite a cell's level to the declared level it folds to",
               lambda body, ctx: br.align_cell_levels(body),
               after="listified"),
        Repair("scoped_terms", "scope two models' identically-named terms by their model",
               lambda body, ctx: br.scope_duplicate_terms(body)),
        Repair("references", "repoint a dangling reference where the choice is forced",
               lambda body, ctx: br.repair_references(body, ctx.classes),
               after="scoped_terms"),
        Repair("cell_terms", "repoint a cell at the same-named term its model reaches",
               lambda body, ctx: br.repoint_out_of_scope_terms(body),
               after="listified"),
        Repair("source_links", "verify or fill each analysis's link to its parsed rows",
               lambda body, ctx: br.resolve_source_table_analysis(body, ctx.stage1)),
        Repair("derived_ids", "rename each analysis to an id the parse determines",
               lambda body, ctx: br.derive_analysis_ids(body),
               after="source_links"),
        Repair("directions", "fill a cell's direction from the contrast's own name",
               lambda body, ctx: br.fill_directions(body),
               after="cell_levels"),
        Repair("mirrored", "rebuild the reversed half of every sign-split contrast",
               lambda body, ctx: br.mirror_withheld(body, ctx.stage1),
               after="directions"),
    )


def check_order(sequence: tuple[Repair, ...]) -> list[str]:
    """Every declared `after` is satisfied by the sequence as written."""
    seen: set[str] = set()
    problems = []
    for repair in sequence:
        if repair.after and repair.after not in seen:
            problems.append(f"{repair.name} must run after {repair.after}, and does not")
        seen.add(repair.name)
    return problems


def apply_all(body: dict, ctx: Context,
              sequence: tuple[Repair, ...] | None = None) -> RepairLog:
    """Run the sequence in order, recording what each one changed."""
    sequence = build_sequence() if sequence is None else sequence
    broken = check_order(sequence)
    if broken:
        raise ValueError("; ".join(broken))
    log = RepairLog()
    for repair in sequence:
        log.record(repair.name, list(repair.apply(body, ctx)))
    return log
