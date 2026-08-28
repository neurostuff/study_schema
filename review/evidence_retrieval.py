"""Candidate selection for evidence: what the retriever is allowed to look at, and how.

A reranker scoring raw field-name-plus-value against raw sentences fails in ways that
have nothing to do with ranking, measured over 70 hand-judged picks in
docs/evidence-top1-judgements.md. This module fixes the four that are fixable before any
model runs:

  aliases      the paper writes "TR = 2 s", the schema field is `repetition_time_seconds`.
               Nothing bridges those two strings, so the query carries both.
  units        `echo_time_seconds = 0.015` is written "TE = 15 ms". The value is expanded
               into the surface forms a paper would actually print.
  literal      an assessment named character-for-character in one sentence was answered
               with a grant number. Exact match is checked first and only falls through
               to the reranker when it is absent or too common to disambiguate.
  section      "DTI is a non-invasive method that maps diffusivity" outranked the Methods
               sentence stating what this study did. Sections are scored as a prior, not
               a filter, so a fact stated outside its usual home is still reachable.

The section prior is a bonus rather than a hard restriction on purpose: heading detection
fails on perhaps a fifth of papers, and a hard filter turns those into guaranteed misses.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# --- sections ---------------------------------------------------------------

#: Heading text -> canonical section. Matched against the lowercased heading with
#: punctuation and numbering stripped, longest pattern first.
_SECTION_PATTERNS: list[tuple[str, str]] = [
    (r"materials?\s+and\s+methods?|methods?\s+and\s+materials?", "methods"),
    (r"^methods?$|^methodology$|^experimental\s+", "methods"),
    (r"participants?|subjects?|procedures?|acquisition|data\s+analysis|"
     r"statistical\s+analys|image\s+processing|preprocessing|pre-processing|"
     r"pharmacotherapy|stimulation|paradigm|task|apparatus|measures?|instruments?", "methods"),
    (r"^results?$|^findings?$|^imaging\s+results", "results"),
    (r"^discussion|^conclusions?|^limitations?|^general\s+discussion", "discussion"),
    (r"^abstract|^summary|^objectives?|^background\s+and\s+aims", "abstract"),
    (r"^introduction|^background$", "intro"),
    (r"^tables?\b|^figures?\b", "tables"),
    (r"acknowledg|funding|conflict|competing\s+interest|references|"
     r"supplementary|author\s+contribution|ethics|data\s+availability", "back"),
]

_HEADING = re.compile(r"^(#{1,4})\s*(.+?)\s*$", re.MULTILINE)


def _canon_heading(raw: str) -> str:
    """Strip numbering and punctuation so '2.3. Statistical analysis' matches."""
    text = re.sub(r"^[\d.\s]+", "", raw).strip(" .:").lower()
    return re.sub(r"\s+", " ", text)


def classify_heading(raw: str) -> str | None:
    text = _canon_heading(raw)
    if not text:
        return None
    for pattern, label in _SECTION_PATTERNS:
        if re.search(pattern, text):
            return label
    return None


def sectionize(text: str) -> list[tuple[int, int, str]]:
    """(start, end, label) spans covering the whole text.

    A subsection heading that does not itself name a section (`### Participants` does,
    `### Pretreatment rsFC` does not) inherits the enclosing section rather than
    resetting it, which is what keeps a Results subsection from being read as Methods.
    """

    marks: list[tuple[int, str, int]] = []
    for match in _HEADING.finditer(text):
        level = len(match.group(1))
        label = classify_heading(match.group(2))
        if label:
            marks.append((match.start(), label, level))

    if not marks:
        return [(0, len(text), "unknown")]

    spans: list[tuple[int, int, str]] = []
    if marks[0][0] > 0:
        # Everything before the first heading is title + abstract in this corpus.
        spans.append((0, marks[0][0], "abstract"))
    for index, (start, label, _level) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        spans.append((start, end, label))
    return spans


def section_of(spans: list[tuple[int, int, str]], offset: int) -> str:
    for start, end, label in spans:
        if start <= offset < end:
            return label
    return "unknown"


# --- field priors -----------------------------------------------------------

#: Dotted path -> the sections that field's evidence actually lands in, best first.
#: Measured over 4,193 reviewer evidence spans across 21 papers; see
#: docs/evidence-top1-judgements.md. Keyed by exact path because the leaf is not
#: enough -- bare `description` is 66% abstract, `design.description` is 88% methods.
FIELD_SECTIONS: dict[str, tuple[str, ...]] = {
    "description": ("abstract", "intro", "methods"),
    "hypothesis": ("intro", "abstract"),
    "analyses.prespecification": ("intro", "discussion", "results"),
    "analyses.name": ("results", "tables", "methods"),
    "analyses.definition": ("results", "methods", "tables"),
    "analyses.interpretations": ("results", "methods", "discussion"),
    "analyses.spatial_scope": ("methods", "results"),
    "analyses.effect.cells.direction": ("results", "methods", "tables"),
    "analyses.effect.cells.level": ("methods", "results", "abstract"),
    "analyses.effect.statistic.family": ("methods", "results", "tables"),
    "analyses.groups.n": ("methods", "tables", "results"),
    "tables.caption": ("tables", "results", "methods"),
    "tables.footer": ("results", "tables", "methods"),
    "tables.table_number": ("results", "methods", "tables"),
    "regions.name": ("results", "methods"),
    "regions.description": ("results", "methods"),
    "measures.specific_metric": ("methods", "results", "intro"),
    "measures.family": ("methods", "intro", "abstract"),
    "measures.type": ("methods", "abstract", "intro"),
    "groups.sex_distribution.category": ("methods", "results", "tables"),
    "groups.age_mean": ("methods", "tables", "results"),
    "groups.age_unit": ("methods", "tables", "results"),
    "groups.acquired_count": ("methods", "results", "tables"),
    "design.arms.agent": ("abstract", "methods", "intro"),
    "design.arms.name": ("methods", "abstract", "intro"),
    "design.timepoints.order": ("methods", "results", "intro"),
    "model_estimations.spatial_unit": ("methods", "results", "intro"),
    "model_estimations.terms.levels.level": ("methods", "abstract", "results"),
    "inference_settings.inference_level": ("methods", "results"),
    "inference_settings.multiple_comparison_method": ("methods", "results"),
    "acquisitions.modality": ("methods", "abstract"),
    "tasks.conditions.name": ("methods", "abstract", "intro"),
}

#: Methods first everywhere else. That is not a hedge: 61% of all reviewer evidence
#: spans are in Methods, and every unlisted acquisition, preprocessing and model field
#: measured above 90% there.
DEFAULT_SECTIONS: tuple[str, ...] = ("methods", "results", "abstract")

#: Points added to a reranker logit. Small relative to the score spread so a strongly
#: matching sentence in the wrong section still wins over a weak one in the right one.
SECTION_BONUS: tuple[float, ...] = (1.5, 0.8, 0.4)
#: Discussion holds 0.7% of evidence spans but is full of sentences that restate the
#: findings in the extractor's own vocabulary, so it out-ranks Methods on term overlap
#: while supporting nothing. Back matter is never evidence. Neither is excluded, only
#: pushed down, because a handful of real spans do land in both.
SECTION_PENALTY: dict[str, float] = {"discussion": -1.0, "back": -2.0}


def section_prior(field_path: str, label: str) -> float:
    # Exact path only. A leaf-name fallback would give `design.description` the prior
    # measured for the study-level `description`, which is 88% methods against 66%
    # abstract -- the opposite ranking.
    ranked = FIELD_SECTIONS.get(field_path, DEFAULT_SECTIONS)
    if label in ranked:
        return SECTION_BONUS[ranked.index(label)]
    return SECTION_PENALTY.get(label, 0.0)


# --- aliases ----------------------------------------------------------------

#: Schema leaf -> the words a paper uses instead. The schema name is a stable
#: identifier, not the phrase an author writes; a query built from the identifier
#: alone cannot match "TR = 2 s".
ALIASES: dict[str, tuple[str, ...]] = {
    "repetition_time_seconds": ("TR", "repetition time"),
    "echo_time_seconds": ("TE", "echo time"),
    "inversion_time_seconds": ("TI", "inversion time"),
    "magnetic_field_strength_tesla": ("Tesla", "T scanner", "field strength"),
    "flip_angle_degrees": ("flip angle",),
    "slice_thickness_mm": ("slice thickness", "thickness"),
    "number_of_slices": ("slices",),
    "number_of_volumes": ("volumes", "time points", "images", "TRs"),
    "voxel_size_mm": ("voxel size", "resolution", "voxels"),
    "field_of_view_mm": ("FoV", "FOV", "field of view"),
    "pulse_sequence_type": ("sequence", "EPI", "echo planar", "MPRAGE", "gradient echo"),
    "acquisition_duration_seconds": ("scan duration", "minutes", "run length"),
    "manufacturer": ("scanner", "Siemens", "Philips", "GE"),
    "scanner_model": ("scanner", "MR system"),
    "acquired_count": ("n", "N", "number of participants", "subjects", "patients"),
    "analyzed_count": ("n", "N", "included in the analysis", "final sample"),
    "n": ("N", "number of participants", "subjects"),
    "age_mean": ("age", "mean age", "years"),
    "age_sd": ("age", "SD", "standard deviation"),
    "age_range": ("age range", "aged", "years"),
    "male_count": ("male", "men", "males"),
    "female_count": ("female", "women", "females"),
    "handedness": ("handed", "right-handed", "Edinburgh"),
    "smoothing_fwhm_mm": ("FWHM", "smoothing", "smoothed", "Gaussian kernel"),
    "motion_correction": ("realignment", "realigned", "motion correction", "head motion"),
    "spatial_normalization": ("normalized", "normalised", "MNI", "Talairach", "template"),
    "slice_timing_correction": ("slice timing", "slice-timing"),
    "software": ("SPM", "FSL", "AFNI", "FreeSurfer", "toolbox", "software"),
    "software_version": ("version", "SPM", "FSL"),
    "family": ("t-test", "F-test", "statistic", "test"),
    "correction_method": ("corrected", "FWE", "FDR", "Bonferroni", "cluster"),
    "threshold_value": ("p <", "threshold", "significance"),
    "connectivity_method": ("correlation", "connectivity", "coherence"),
    "url": ("http", "available at", "www"),
    "doi": ("doi",),
    "allocation": ("randomized", "randomised", "assigned", "allocated"),
    "blinding": ("blind", "double-blind", "masked"),
    "assignment_structure": ("group", "within-subject", "between-subject", "crossover"),
    "arm_kind": ("treatment", "placebo", "sham", "control"),
    "species": ("participants", "patients", "human", "subjects"),
}


def alias_terms(field_path: str) -> tuple[str, ...]:
    return ALIASES.get(field_path.rsplit(".", 1)[-1], ())


# --- value surface forms ----------------------------------------------------

def _fmt(number: float) -> str:
    """`2.0` -> `2`, `0.015` -> `0.015`. Papers do not print trailing zeros."""
    text = f"{number:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def value_variants(field_path: str, value: str) -> list[str]:
    """Surface forms of a value as a paper would print it, most plausible first.

    A schema field carries its unit in its name and stores the SI magnitude, so
    `echo_time_seconds = 0.015` never appears in a paper -- "TE = 15 ms" does. Without
    this expansion neither exact match nor a lexical reranker can reach that sentence.

    Order matters downstream: build_query takes the head of this list, so the forms a
    paper is most likely to print come first.
    """

    text = str(value).strip()
    if not text:
        return []
    leaf = field_path.rsplit(".", 1)[-1]

    try:
        number = float(text)
    except ValueError:
        return [text]

    plain = _fmt(number)
    ordered: list[str] = []

    if leaf.endswith("_seconds"):
        milli = _fmt(number * 1000)
        # Sub-second quantities are printed in ms; whole seconds are printed in s.
        if abs(number) < 1:
            ordered += [f"{milli} ms", f"{milli}ms", milli]
        else:
            ordered += [f"{plain} s", f"{plain}s", f"{milli} ms"]
        if leaf.startswith("acquisition_duration"):
            ordered.append(f"{_fmt(number / 60)} min")
    elif leaf.endswith("_mm"):
        ordered += [f"{plain} mm", f"{plain}mm"]
    elif leaf.endswith("_tesla"):
        ordered += [f"{plain} T", f"{plain}T", f"{plain}-T"]
    elif leaf.endswith("_degrees"):
        ordered += [f"{plain}\u00b0", f"{plain} deg"]

    ordered.append(plain)
    if 0 < abs(number) < 1:
        ordered.append(plain.lstrip("0"))  # ".015" as well as "0.015"
    if text not in ordered:
        ordered.insert(0, text)

    seen: set[str] = set()
    return [v for v in ordered if v and not (v in seen or seen.add(v))]


# --- literal match ----------------------------------------------------------

_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Fold the differences that stop an exact match: unicode dashes, case, spacing."""
    folded = unicodedata.normalize("NFKD", text)
    folded = folded.replace("−", "-").replace("–", "-").replace("—", "-")
    folded = folded.replace("’", "'").replace("“", '"').replace("”", '"')
    return _WS.sub(" ", folded).strip().lower()


#: Above this many hits a literal is a common word, not a locator, and the reranker
#: decides instead. Below it the exact match is trusted over any learned score.
LITERAL_MAX_HITS = 4
#: A bare number matches too much to be trusted alone.
LITERAL_MIN_LEN = 3


def literal_hits(units: list[str], variants: list[str]) -> list[int]:
    """Indices of units containing any surface form of the value, best variant first.

    Longer variants are tried first and the search stops at the first one that is
    present and rare, so `"WASI-IV"` is preferred over the digit it also contains.
    """

    folded = [normalize(unit) for unit in units]
    # Longest first: 'WASI-IV' locates, the '4' inside it does not.
    for variant in sorted(variants, key=len, reverse=True):
        needle = normalize(variant)
        if len(needle) < LITERAL_MIN_LEN:
            continue
        hits = [index for index, unit in enumerate(folded) if needle in unit]
        if 0 < len(hits) <= LITERAL_MAX_HITS:
            return hits
    return []


#: Added to a reranker logit for a unit containing the value verbatim. Deliberately
#: large: over the judged sample every literal hit was a correct citation and the
#: reranker's misses on those fields were severe (a grant number for an assessment name).
LITERAL_BONUS = 4.0


#: Added to a reranker logit for a unit that names the entity the field hangs off.
#: The entity belongs here and not in the query. Concatenated into the query string it
#: scored *below* the entity-free baseline (14.9% against 21.7%) on exactly the
#: instances where sibling entities share a value and the entity is the only possible
#: discriminator -- the cross-encoder dilutes on the extra terms rather than using
#: them. Scored separately it is worth +0.4 top-1 and +1.5 recall@12.
ENTITY_BONUS = 1.0


def entity_hits(units: list[str], entity: str) -> list[int]:
    """Indices of units naming the entity, by full name or by its acronym.

    Papers introduce a group once by name and then use initials throughout, so a
    name-only test finds the definition sentence and nothing else. The acronym is not
    always taken from every word -- "typical development children" is abbreviated TD --
    so every prefix of the name is tried.
    """

    if not entity or len(entity) < 3:
        return []
    needles = [normalize(entity)]
    words = [w for w in re.split(r"[\s/-]+", entity) if w]
    for length in range(2, len(words) + 1):
        needles.append("".join(w[0] for w in words[:length]).lower())
    folded = [normalize(unit) for unit in units]
    hits = {index for index, unit in enumerate(folded)
            for needle in needles
            if re.search(rf"(?<![a-z]){re.escape(needle)}(?![a-z])", unit)}
    # An entity named throughout the paper is not a locator. The floor keeps the guard
    # from firing on a handful of units, where a third of the text is one sentence.
    return sorted(hits) if len(hits) <= max(3, len(units) // 3) else []


def build_query(field_path: str, value: str) -> str:
    """The string handed to the reranker.

    Deliberately short and deliberately entity-free. `ms-marco-MiniLM` is trained on
    web queries, and terms added past the field and its value cost accuracy: over 4,074
    instances, dropping the entity from the query was worth +2.2 points of top-1 on its
    own (34.6% to 36.8%).

    The aliases are the one part of this that does not pay for itself at top-1 -- 36.8%
    with them against 37.0% without -- and they are kept only for the +0.7 they are
    worth at recall@12, where a two-stage pipeline actually reads. Capped at two for
    the same dilution reason. See docs/evidence-top1-judgements.md.
    """

    leaf = field_path.rsplit(".", 1)[-1].replace("_", " ")
    aliases = " ".join(alias_terms(field_path)[:2])
    forms = " ".join(value_variants(field_path, value)[:2])
    return " ".join(part for part in (leaf, aliases, forms) if part).strip()


# --- units ------------------------------------------------------------------

@dataclass(frozen=True)
class Unit:
    """One candidate passage.

    `text` is the raw slice of the paper and is what any quote must be, because
    `build_record.py` resolves a quote by exact match against the source. `rendered` is
    what the reranker scores, and the two differ for table rows: a row is scored as the
    sentence "For TD: N is 44" but quoted as the pipe-delimited line that actually
    appears in the text. Emitting the rendered form as a quote would produce evidence
    that resolves nowhere.
    """

    start: int
    end: int
    text: str
    rendered: str
    section: str = "unknown"


_ROW = re.compile(r"[|]")
_SEPARATOR = re.compile(r"^[\s|:-]+$")
#: Below this a unit is a fragment, above it a page. Both are useless as evidence.
MIN_UNIT, MAX_UNIT = 15, 900


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table_units(block: list[tuple[int, str]]) -> list[Unit]:
    """A markdown table's data rows, each rendered as a sentence.

    A reranker scores `| TD | 44 | 0.17 |` as noise; it scores "For TD: N is 44" as a
    claim. The header supplies the column names that make the row readable.
    """

    header, start_at = None, 0
    for index, (_offset, line) in enumerate(block):
        if not _SEPARATOR.match(line):
            header, start_at = _cells(line), index
            break
    if not header:
        return []
    units = []
    for offset, line in block[start_at + 1:]:
        if _SEPARATOR.match(line):
            continue
        row = _cells(line)
        if not row:
            continue
        pairs = [f"{column} is {cell}" for column, cell in zip(header[1:], row[1:])
                 if cell and column]
        if pairs:
            units.append(Unit(offset, offset + len(line), line,
                              f"For {row[0] or 'row'}: " + "; ".join(pairs) + "."))
    return units


def sentence_units(text: str) -> list[Unit]:
    """Every passage a locator is allowed to return, tagged with its section."""

    units: list[Unit] = []
    cursor = 0
    for match in re.finditer(r"(?<=[.;!?])\s+|\n\n+", text):
        chunk = text[cursor:match.start()]
        if chunk.strip() and len(_ROW.findall(chunk)) < 3:
            units.append(Unit(cursor, cursor + len(chunk), chunk, chunk))
        cursor = match.end()
    if text[cursor:].strip():
        units.append(Unit(cursor, len(text), text[cursor:], text[cursor:]))

    position, block = 0, []
    for line in text.split("\n"):
        offset, position = position, position + len(line) + 1
        if len(_ROW.findall(line)) >= 3:
            block.append((offset, line))
        else:
            units += _table_units(block)
            block = []
    units += _table_units(block)

    spans = sectionize(text)
    return [Unit(u.start, u.end, u.text, u.rendered, section_of(spans, u.start))
            for u in units if MIN_UNIT < len(u.rendered) < MAX_UNIT]


# --- scoring ----------------------------------------------------------------

#: The reranker. Small enough to run on a CPU in the seconds a paper already costs.
RERANKER = "cross-encoder/ms-marco-MiniLM-L12-v2"

#: A pick is trusted when it contains the value verbatim, or when it beats the runner-up
#: by this much. Measured over 173 slots with human evidence: a literal hit is 80.9%
#: confirmed-correct against 27.8% without, and a margin of 2.62 keeps 40% of picks at
#: 80%. Below the gate the retriever contributes nothing, which is the point -- it has no
#: way to abstain otherwise, and unioning an always-answering locator imports its errors.
MARGIN_GATE = 2.62


def load_reranker(model: str = RERANKER, device: str = "cpu"):
    """The cross-encoder, or None if it cannot be loaded here.

    Returning None rather than raising is the whole contract: the union is an enhancement
    to a pipeline that works without it, and nothing about loading a 130MB scorer should
    be able to cost a paper its evidence.

    Every failure is caught, not just a missing import. Nine workers sharing one 8GB card
    exhausted it, the load raised `torch.OutOfMemoryError`, and the evidence stage died
    taking the model's quotes with it -- an optional second locator sank the primary one.
    A device that will not hold the model falls back to CPU before it gives up, because
    slow evidence beats none.
    """

    try:
        import torch  # noqa: PLC0415
        from transformers import (AutoModelForSequenceClassification,  # noqa: PLC0415
                                  AutoTokenizer)
    except ImportError:
        return None

    for attempt in (device, "cpu") if device != "cpu" else ("cpu",):
        try:
            tokenizer = AutoTokenizer.from_pretrained(model)
            scorer = (AutoModelForSequenceClassification
                      .from_pretrained(model).to(attempt).eval())
        except Exception as error:  # noqa: BLE001 -- any failure means "run without it"
            print(f"  reranker on {attempt}: unavailable ({type(error).__name__}); "
                  f"{'falling back to cpu' if attempt != 'cpu' else 'quote pass only'}",
                  file=__import__("sys").stderr)
            continue
        return {"tokenizer": tokenizer, "model": scorer, "device": attempt,
                "torch": torch}
    return None


def score_units(reranker, query: str, units: list[Unit], batch: int = 64) -> list[float]:
    torch = reranker["torch"]
    scores: list[float] = []
    for start in range(0, len(units), batch):
        chunk = [u.rendered for u in units[start:start + batch]]
        encoded = reranker["tokenizer"]([query] * len(chunk), chunk, return_tensors="pt",
                                        truncation=True, max_length=256, padding=True)
        encoded = {k: v.to(reranker["device"]) for k, v in encoded.items()}
        with torch.no_grad():
            scores += reranker["model"](**encoded).logits[:, 0].tolist()
    return scores


def locate(reranker, units: list[Unit], field_path: str, value: str,
           entity: str = "") -> Unit | None:
    """The one unit that warrants this value, or None when nothing clears the gate."""

    if reranker is None or not units or not value:
        return None
    variants = value_variants(field_path, value)
    literal = set(literal_hits([u.rendered for u in units], variants))
    named = set(entity_hits([u.rendered for u in units], entity)) if entity else set()

    base = score_units(reranker, build_query(field_path, value), units)
    total = [score + section_prior(field_path, unit.section)
             + (LITERAL_BONUS if index in literal else 0.0)
             + (ENTITY_BONUS if index in named else 0.0)
             for index, (score, unit) in enumerate(zip(base, units))]

    order = sorted(range(len(total)), key=lambda i: -total[i])
    best = order[0]
    margin = total[best] - total[order[1]] if len(order) > 1 else float("inf")
    if best in literal or margin >= MARGIN_GATE:
        return units[best]
    return None
