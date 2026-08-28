"""Ask one question across many records: which contrast is treatment against control?

A trial's record names its arms in its own words -- `active iTBS`, `REAL`, `MPH`,
`paroxetine 20mg` -- and its contrasts in terms of levels that are those arms. Asking
"what did treatment do relative to control" across a corpus therefore needs three things
lined up, and only one of them is hard.

  the role      already normalised. `ArmKind` is a schema enum and it splits cleanly:
                pharmacological / stimulation / behavioural_intervention /
                active_comparator are the intervention side, placebo / sham / usual_care /
                no_intervention the comparator side.
  the agent     free text, mapped onto ONVOC by `normalize.py` so `escitalopram` in one
                paper and `Escitalopram` in another are the same row.
  the link      a `Cell.level` is a string, and which arm it names is the open question.
                Matched on words, never on a similarity score, for the reason
                `derive_direction` gives: `men` is a substring of `women`.

An analysis qualifies only when one cell resolves to an intervention arm and another to a
comparator arm. An analysis contrasting two groups, or two timepoints, is not a treatment
contrast however much it mentions a drug.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator

#: The intervention side of a trial and its comparator, from the schema's own ArmKind.
#: `active_comparator` sits on the intervention side deliberately: it is a second active
#: treatment, and a head-to-head trial has no inert arm at all.
INTERVENTION = frozenset({"pharmacological", "stimulation", "behavioural_intervention",
                          "active_comparator"})
COMPARATOR = frozenset({"placebo", "sham", "usual_care", "no_intervention"})


def role(arm_kind: str | None) -> str | None:
    if arm_kind in INTERVENTION:
        return "intervention"
    if arm_kind in COMPARATOR:
        return "comparator"
    return None


def _value(node: Any) -> Any:
    return node.get("value") if isinstance(node, dict) and "value" in node else node


def _direction(node: Any) -> str | None:
    """A cell's direction as a string, or None.

    Coerced rather than passed through: a wrapper whose `value` is itself a wrapper, or a
    field the model left as an object, otherwise reaches a caller as a dict and breaks
    the first thing that tries to group by it.
    """
    seen = _value(node)
    if isinstance(seen, dict):
        seen = _value(seen)
    if isinstance(seen, list):
        seen = seen[0] if seen else None
    return seen if isinstance(seen, str) and seen else None


_WEAK = frozenset({"the", "a", "an", "of", "in", "and", "or", "for", "with", "group",
                   "groups", "arm", "condition", "patients", "participants", "subjects"})


def _words(text: str) -> frozenset[str]:
    every = frozenset(re.findall(r"[a-z0-9]+", str(text or "").lower()))
    return (every - _WEAK) or every


def same(a: str, b: str) -> bool:
    """Do these two strings name the same thing? Word-set containment, never a ratio."""
    left, right = _words(a), _words(b)
    return bool(left) and bool(right) and (left <= right or right <= left)


@dataclass(frozen=True)
class Arm:
    local_id: str
    name: str
    kind: str
    agent: str

    @property
    def role(self) -> str | None:
        return role(self.kind)

    #: The word a comparator arm is identified by when the paper names the level after
    #: the *kind* rather than the arm -- `placebo infusion group` against an arm called
    #: `normal saline placebo`. Word containment cannot bridge those two, and the kind
    #: can: an arm declared `placebo` and a level saying `placebo` are the same thing.
    KIND_WORDS = {"placebo": "placebo", "sham": "sham",
                  "no_intervention": "no intervention", "usual_care": "usual care"}

    def named_by(self, level: str) -> bool:
        """Would a cell level of this text be referring to this arm?"""
        return any(same(level, surface) for surface in (self.name, self.agent) if surface)

    def kind_word(self) -> str:
        return self.KIND_WORDS.get(self.kind, "")


@dataclass(frozen=True)
class TreatmentContrast:
    """One analysis that compares an intervention arm against a comparator arm.

    `direction` is the *intervention cell's* and it means which side of the contrast that
    arm sits on, not what the treatment did to a patient. `negative` says the
    intervention arm was the lower of the two -- so an escitalopram cell marked negative
    against a placebo cell marked positive means **placebo > escitalopram**, on whatever
    this analysis measured.

    Which is why `measure` and `held` are carried and not optional decoration. "Lower" is
    meaningless without them: lower BOLD response in a region and lower depression score
    are opposite in clinical direction, and a corpus-wide table that pools them without
    saying which is worse than no table.
    """

    study_id: str
    analysis: str
    analysis_name: str
    intervention: Arm
    comparator: Arm
    direction: str | None
    agent_concept: str = ""
    #: What was compared -- the analysis's measure -- and what was held constant across
    #: the two arms, which is usually the condition the contrast is within.
    measure: str = ""
    #: The measure's controlled type and family, which is what a meta-analysis must be
    #: homogeneous on. `source_label` is the paper's wording and fragments -- `bold
    #: activation`, `brain activity`, `BOLD percent signal change` and `neural responses`
    #: are one thing written four ways -- whereas `MeasureType` is a schema enum and
    #: needs no normalisation at all.
    measure_type: str = ""
    measure_family: str = ""
    held: tuple[str, ...] = ()
    #: The comparator cell's own direction, kept for the consistency check below.
    comparator_direction: str | None = None

    @property
    def consistent(self) -> bool:
        """Do the two cells actually oppose each other?

        Two arms of one contrast cannot both be the higher side. When they carry the same
        direction the record is saying something impossible, and the row should be read
        as a parse to check rather than a result to pool.
        """
        if self.direction is None or self.comparator_direction is None:
            return True
        if self.direction in ("undirected", "held"):
            return True
        return self.direction != self.comparator_direction

    @property
    def relation(self) -> str:
        """The comparison as an inequality, which is what the direction actually says."""
        if self.direction == "positive":
            return f"{self.intervention.name} > {self.comparator.name}"
        if self.direction == "negative":
            return f"{self.intervention.name} < {self.comparator.name}"
        return f"{self.intervention.name} ~ {self.comparator.name}"

    def render(self) -> str:
        agent = f" [{self.agent_concept}]" if self.agent_concept else ""
        on = f" on {self.measure}" if self.measure else ""
        holding = f", holding {'/'.join(self.held)}" if self.held else ""
        flag = "" if self.consistent else "   [INCONSISTENT: both cells same direction]"
        return (f"{self.study_id}  {self.relation}{agent}{on}{holding}"
                f" ({self.comparator.kind}){flag}")


def arms_of(record: dict) -> list[Arm]:
    found = []
    for arm in (record.get("design") or {}).get("arms") or []:
        if not isinstance(arm, dict):
            continue
        found.append(Arm(local_id=str(_value(arm.get("local_id")) or ""),
                         name=str(_value(arm.get("name")) or ""),
                         kind=str(_value(arm.get("arm_kind")) or ""),
                         agent=str(_value(arm.get("agent")) or "")))
    return found


def treatment_contrasts(record: dict,
                        agents: dict[str, str] | None = None
                        ) -> Iterator[TreatmentContrast]:
    """Every analysis in this record that contrasts intervention against comparator.

    The direction reported is the intervention cell's, so a positive contrast means the
    intervention arm was the higher one -- which is the only reading that survives
    pooling across papers, since each paper is free to name its contrast either way
    round.
    """

    study_id = str(_value(record.get("local_id")) or "")
    # An analysis names its measure by local_id; the label is what a reader needs.
    measures, kinds = {}, {}
    for measure in record.get("measures") or []:
        if isinstance(measure, dict) and _value(measure.get("local_id")):
            local = str(_value(measure["local_id"]))
            measures[local] = str(_value(measure.get("source_label"))
                                  or _value(measure.get("type")) or "")
            kinds[local] = (str(_value(measure.get("type")) or ""),
                            str(_value(measure.get("family")) or ""))
    arms = arms_of(record)
    if not any(a.role == "intervention" for a in arms):
        return
    if not any(a.role == "comparator" for a in arms):
        return
    agents = agents or {}

    for analysis in record.get("analyses") or []:
        if not isinstance(analysis, dict):
            continue
        cells = (analysis.get("effect") or {}).get("cells") or []
        placed: list[tuple[Arm, dict]] = []
        for cell in cells:
            level = str(_value((cell or {}).get("level")) or "")
            if not level:
                continue
            hits = [arm for arm in arms if arm.named_by(level)]
            if not hits:
                # Fall back to the arm's kind. Only when exactly one arm of that kind
                # exists and only one kind word appears in the level, so `sham` and
                # `placebo` arms in one trial still cannot be confused.
                words = _words(level)
                by_kind = [arm for arm in arms
                           if arm.kind_word() and _words(arm.kind_word()) <= words]
                if len({arm.kind for arm in by_kind}) == 1 and len(by_kind) == 1:
                    hits = by_kind
            # One arm or none. A level naming two arms identifies neither, and guessing
            # which is exactly the error a cross-corpus query would then propagate.
            if len(hits) == 1:
                placed.append((hits[0], cell))

        intervention = next((p for p in placed if p[0].role == "intervention"), None)
        comparator = next((p for p in placed if p[0].role == "comparator"), None)
        if not (intervention and comparator):
            continue
        # Levels the contrast holds constant -- the condition it is within. Without them
        # a pooled row loses what the comparison was even about.
        held = tuple(str(_value(cell.get("level")) or "") for cell in cells
                     if _direction(cell.get("direction")) == "held"
                     and _value(cell.get("level")))
        yield TreatmentContrast(
            study_id=study_id,
            analysis=str(_value(analysis.get("local_id")) or ""),
            analysis_name=str(_value(analysis.get("name")) or ""),
            intervention=intervention[0], comparator=comparator[0],
            direction=_direction((intervention[1] or {}).get("direction")),
            agent_concept=agents.get(intervention[0].agent, ""),
            measure=measures.get(str(_value(analysis.get("measure")) or ""), ""),
            measure_type=kinds.get(str(_value(analysis.get("measure")) or ""), ("", ""))[0],
            measure_family=kinds.get(str(_value(analysis.get("measure")) or ""), ("", ""))[1],
            held=held,
            comparator_direction=_direction((comparator[1] or {}).get("direction")))


# --- case-control contrasts --------------------------------------------------

#: A cohort is a patient group or a comparison group, and the group's own name is what
#: says which. Deliberately not the paper's topic: a schizophrenia study's control group is
#: still a control group, and a study of unaffected siblings has no patient group at all.
#: The order matters -- `control` is tested first, because "healthy controls" and
#: "schizophrenia" both appear in "controls matched to schizophrenia patients".
_CONTROL = re.compile(r"\b(healthy|controls?|comparison|normal|unaffected|\bHCs?\b|"
                      r"\bNCs?\b|\bCONs?\b)\b", re.I)
_PATIENT = re.compile(r"\b(schizophreni\w*|schizoaffective|psychosis|psychotic|\bSZ\b|"
                      r"\bSCZ\b|\bFESZ\b|\bFEP\b|patients?)\b", re.I)
#: Cohorts that are neither: a risk group is not a patient, and pooling it into a
#: patients-versus-controls map is the confound the corpus scan was built to separate.
#: Plurals spelled out: `\bsibling\b` does not match "siblings", and "unaffected
#: siblings" then reads as a control group -- which is the confound the corpus scan
#: separated 133 studies out to avoid.
_RISK = re.compile(r"\b(siblings?|relatives?|high[- ]risk|at[- ]risk|carriers?|prodrom\w+|"
                   r"\bCHR\b|\bUHR\b|\bARMS\b)\b", re.I)


def cohort_role(name: str) -> str | None:
    """`patient`, `control`, `risk`, or None from a group's own name."""
    if not name:
        return None
    if _RISK.search(name):
        return "risk"
    if _CONTROL.search(name):
        return "control"
    if _PATIENT.search(name):
        return "patient"
    return None


@dataclass(frozen=True)
class GroupContrast:
    """One analysis comparing a patient cohort against a comparison cohort.

    `direction` is the *patient* cell's, so `positive` means patients were the higher
    side. Each paper names its contrast whichever way round it likes and only one reading
    survives pooling, so the reading is fixed here rather than left to the caller.
    """

    study_id: str
    analysis: str
    analysis_name: str
    patient: str
    control: str
    direction: str | None
    measure: str = ""
    measure_type: str = ""
    source_key: str = ""

    @property
    def relation(self) -> str:
        if self.direction == "positive":
            return "patients > controls"
        if self.direction == "negative":
            return "patients < controls"
        return "patients ~ controls"


def group_contrasts(record: dict) -> Iterator[GroupContrast]:
    """Every analysis whose cells put a patient cohort against a comparison cohort.

    The link from a cell to a cohort is `FactorLevel.groups` where the model filled it and
    the level string otherwise -- measured at 33 of 462 levels for `arms`, so the
    structural link cannot be relied on alone. A cell placed on a risk cohort disqualifies
    the analysis rather than being ignored: a three-group contrast of patients, siblings
    and controls is not a patients-versus-controls comparison.
    """

    study_id = str(_value(record.get("local_id")) or "")
    names = {str(_value(g.get("local_id"))): str(_value(g.get("name")) or "")
             for g in record.get("groups") or [] if isinstance(g, dict)}

    levels: dict[str, list[str]] = {}
    for model in record.get("model_estimations") or []:
        for term in model.get("terms") or []:
            for level in term.get("levels") or []:
                label = str(_value(level.get("level")) or "")
                linked = [g if isinstance(g, str) else str(_value(g))
                          for g in (level.get("groups") or [])]
                if label and linked:
                    levels.setdefault(label, []).extend(linked)

    measures, kinds = {}, {}
    for measure in record.get("measures") or []:
        if isinstance(measure, dict) and _value(measure.get("local_id")):
            local = str(_value(measure["local_id"]))
            measures[local] = str(_value(measure.get("source_label"))
                                  or _value(measure.get("type")) or "")
            kinds[local] = str(_value(measure.get("type")) or "")

    for analysis in record.get("analyses") or []:
        if not isinstance(analysis, dict):
            continue
        placed: dict[str, tuple[str, str | None]] = {}
        disqualified = False
        for cell in (analysis.get("effect") or {}).get("cells") or []:
            label = str(_value((cell or {}).get("level")) or "")
            if not label:
                continue
            # The structural link first, the level's own words second.
            candidates = [names.get(g, "") for g in levels.get(label, [])] or [label]
            roles = {cohort_role(c) for c in candidates if c}
            roles.discard(None)
            if roles == {"risk"}:
                disqualified = True
                break
            if len(roles) != 1:
                continue
            role = roles.pop()
            if role in ("patient", "control") and role not in placed:
                placed[role] = (label, _direction(cell.get("direction")))
        if disqualified or not ("patient" in placed and "control" in placed):
            continue
        measure_ref = str(_value(analysis.get("measure")) or "")
        yield GroupContrast(
            study_id=study_id,
            analysis=str(_value(analysis.get("local_id")) or ""),
            analysis_name=str(_value(analysis.get("name")) or ""),
            patient=placed["patient"][0], control=placed["control"][0],
            direction=placed["patient"][1],
            measure=measures.get(measure_ref, ""), measure_type=kinds.get(measure_ref, ""),
            source_key=str(_value(analysis.get("source_table_analysis")) or ""))
