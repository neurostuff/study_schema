"""Deterministic transforms of the paper text before it reaches the extractor.

Two kinds of transform, and the distinction matters for what a result can mean:

    text     the paper the prompt carries is a *different string* -- sections dropped,
             sections reordered, sentences selected. Nothing is added.
    digest   the paper is untouched and a derived block is inserted ahead of it: an
             abbreviation table, an inventory of reported statistics, a list of
             candidate contrasts. Nothing is removed.

A digest is a candidate list and is labelled as one in the prompt. Regex over prose
over-generates, and a block the model is told to trust would import every false positive
into the record; a block it is told to confirm can only save it a search. That framing is
not decoration -- it is the only thing that makes a noisy extractor safe to feed in.

Every offset the record carries still addresses the original text. `build_record.py` is
handed the untransformed file, so a strategy can reorder or drop whatever it likes without
moving a single `EvidenceSpan.start_char`. Preprocessing changes what the model reads, not
what the record points at.

Standard library only, and deliberately: the measured wins here have to survive being
adopted by a repo whose dependency list is three packages. `check_against_spacy.py`
compares the sentence splitter and the abbreviation finder against scispaCy.

    python review/preprocess.py --text texts/xevP8UDRAVh9/processed/local/text.tables.txt \
        --strategy contrasts --show
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------- section segmentation

#: `build_text.py` writes the corpus text with its headings as `## `/`### ` markdown and
#: inlines each coordinate table under a `Table N — caption` line, so segmentation here is
#: a line scan and not a layout model.
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)

#: Zone per heading, in two tiers. A STRONG pattern is a heading that names its own zone
#: ("Results", "Participants") and overrides whatever it is nested under; a WEAK one names
#: a kind of work that happens in more than one zone ("Univariate analysis" is Methods in
#: one paper and Results in another) and is used only when the parent heading said nothing.
#: Without the tiers "Voxel-based morphometry analyses" -- a Results subsection -- lands in
#: Methods on the word "analyses", and dropping the Introduction takes the results with it.
_STRONG_ZONES: list[tuple[str, re.Pattern[str]]] = [
    ("back", re.compile(
        r"\b(reference|bibliograph|acknowledg|conflict|competing interest|funding|"
        r"author contribution|data availability|supplement|appendix|abbreviation|"
        r"disclosure|declaration|footnote|highlight)", re.I)),
    ("tables", re.compile(r"^tables?\b", re.I)),
    # A structured abstract is a run of `## Background:` / `## Methods:` / `## Results:`
    # headings, and taken at face value its Conclusion is a Discussion and its Results a
    # Results. The trailing colon is what distinguishes the label from the section.
    ("front", re.compile(r"^\s*abstract\b|:\s*$", re.I)),
    ("results", re.compile(r"\b(results?|findings?)\b", re.I)),
    ("discussion", re.compile(
        r"\b(discussion|conclusions?|limitations?|implications?|interpretation)\b", re.I)),
    ("intro", re.compile(r"\b(introduction|background|rationale)\b", re.I)),
    ("methods", re.compile(
        r"\b(methods?|materials?|procedures?|participants?|subjects?|sample|cohort|"
        r"acquisitions?|apparatus|preprocess\w*|pre-process\w*|ethics?|recruit\w*)\b",
        re.I)),
]

_WEAK_ZONES: list[tuple[str, re.Pattern[str]]] = [
    ("methods", re.compile(
        r"\b(analys[ei]s|modell?ing|statistic\w*|design|tasks?|stimul\w*|measures?|"
        r"mask\w*|roi|region of interest|segmentation|normali[sz]ation|pipeline|"
        r"experiment\w*|questionnaire|assessment|drug|dose|administration|scann?\w*|"
        r"imaging|connectivity|quantification|parcellation|registration)\b", re.I)),
]


@dataclass
class Section:
    """One heading and the text under it, up to the next heading of any level."""

    level: int
    heading: str
    body: str
    zone: str

    @property
    def text(self) -> str:
        # Level 0 is the front matter, whose heading is this module's own label for it.
        # Emitting it would put a sentence in the prompt the paper does not contain.
        if not self.level:
            return self.body
        return f"{'#' * self.level} {self.heading}\n{self.body}"


def classify(heading: str) -> tuple[str, str]:
    """(zone, "strong" | "weak" | "none") for one heading."""

    for zone, pattern in _STRONG_ZONES:
        if pattern.search(heading):
            return zone, "strong"
    for zone, pattern in _WEAK_ZONES:
        if pattern.search(heading):
            return zone, "weak"
    return "other", "none"


def split_sections(text: str) -> list[Section]:
    """Front matter first, then one Section per heading.

    A subsection inherits its parent's zone when its own heading says nothing -- "Study
    sample" under "Materials and Methods" is Methods -- but overrides it when it does.
    That is what keeps a Results subsection called "Voxel-based morphometry analyses"
    out of Methods, which a flat keyword match on "analyses" gets wrong.
    """

    marks = list(_HEADING.finditer(text))
    sections: list[Section] = []
    front = text[: marks[0].start()] if marks else text
    if front.strip():
        # Title, keywords and abstract, which carry no heading in the pubget text. The
        # abstract states the design and every headline result in two hundred words and
        # is the densest part of the paper; it is never a candidate for dropping.
        sections.append(Section(0, "Front matter (title, keywords, abstract)",
                                front.strip(), "front"))

    stack: list[tuple[int, str]] = []
    for index, mark in enumerate(marks):
        level, heading = len(mark.group(1)), mark.group(2)
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        body = text[mark.end(): end].strip("\n")
        while stack and stack[-1][0] >= level:
            stack.pop()
        zone, strength = classify(heading)
        parent = stack[-1][1] if stack else "other"
        if strength != "strong" and parent != "other":
            zone = parent
        elif strength == "none":
            zone = parent
        stack.append((level, zone))
        sections.append(Section(level, heading, body, zone))
    return sections


#: A pipe table `build_text.py` inlined, with the `Table N — caption` line above it. The
#: rows are the only place some directions are stated, so a strategy that drops prose
#: must not drop these.
_TABLE_BLOCK = re.compile(
    r"(?:^[^\n]*Table[^\n]*\n\n)?^\|[^\n]*\n(?:^\|[^\n]*(?:\n|\Z))+", re.MULTILINE)


def table_blocks(text: str) -> list[str]:
    return [match.group(0) for match in _TABLE_BLOCK.finditer(text)]


# ------------------------------------------------------------------ sentence splitting

#: Tokens whose trailing period does not end a sentence. Mostly citation and unit
#: furniture; `et al.` and `Fig.` alone account for most bad splits in this corpus. A
#: single capital is in the list because an initial is the other common case.
_NON_TERMINAL = frozenset("""
et al e.g i.e cf vs etc approx ca resp viz fig figs tab tabs eq ref refs no nos dr prof
mr mrs ms st inc ltd co univ dept min sec ms mm cm ml mg kg vol ed eds pp al s.d s.e
i.v p.o a.m p.m
""".split()) | frozenset(chr(c) for c in range(ord("a"), ord("z") + 1))

#: A period, question or exclamation mark followed by space and something that could
#: start a sentence. Python's `re` will not take a variable-width lookbehind, so the
#: "unless the word before it is an abbreviation" half is a token check on the match and
#: not part of the pattern.
_BOUNDARY = re.compile(r"[.!?][\"')\]]?\s+(?=[\"'(\[]?[A-Z0-9])")
_LAST_WORD = re.compile(r"([A-Za-z][A-Za-z.]*)\.?$")


def _split_sentences(line: str) -> list[str]:
    pieces, start = [], 0
    for boundary in _BOUNDARY.finditer(line):
        head = line[start: boundary.start() + 1]
        word = _LAST_WORD.search(head.rstrip(".!?"))
        if word and word.group(1).lower().rstrip(".") in _NON_TERMINAL:
            continue
        pieces.append(head)
        start = boundary.end()
    pieces.append(line[start:])
    return pieces


def paragraphs(text: str) -> list[str]:
    """Prose paragraphs, with headings and table rows left out.

    Consecutive prose lines are one paragraph. The corpus text keeps a paragraph on a
    single line, but a hard-wrapped one would otherwise be split at every line end, and
    a claim whose subject and verdict land in different "sentences" loses both. A pipe
    row ends a paragraph rather than joining it, or a coordinate gets quoted as part of
    the Methods claim above it.
    """

    found, current = [], []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|")):
            if current:
                found.append(" ".join(current))
                current = []
            continue
        current.append(stripped)
    if current:
        found.append(" ".join(current))
    return found


def sentences_of(paragraph: str) -> list[str]:
    # A decimal point is not a sentence end. Protecting the digits is cheaper and more
    # reliable than adding a case for every numeric shape in a Methods section
    # (p = 0.05, 4.6 mm, 1.5 T, r > 0.7).
    guarded = re.sub(r"(\d)\.(\d)", lambda m: f"{m[1]}\x00{m[2]}", paragraph)
    out = []
    for piece in _split_sentences(guarded):
        piece = piece.replace("\x00", ".").strip()
        if len(piece) > 2:
            out.append(re.sub(r"\s+", " ", piece))
    return out


def sentences(text: str) -> list[str]:
    """Every prose sentence, in document order."""

    return [s for paragraph in paragraphs(text) for s in sentences_of(paragraph)]


# --------------------------------------------------------------- abbreviation glossary

#: A parenthesis holding one to three tokens, at most ten characters, with an upper-case
#: letter or a digit in it. The Schwartz & Hearst (2003) candidate condition, which is
#: what keeps "(see Methods)" and "(p < 0.05)" out.
_SHORT_FORM = re.compile(r"\(([^()]{1,30})\)")
#: A two-character short form is kept only when both characters are upper case or digits.
#: "GM" for gray matter and "WM" for white matter are load-bearing in this corpus, and
#: rejecting every two-letter candidate to be rid of "In" and "we" threw them away --
#: found by comparing against scispaCy, which had them and this did not.
_STOP_SHORT = re.compile(
    r"^(?:[\d\s.,;:%±<>=+\-–—]+|fig(?:ure)?s?\.?\s*\d*|tab(?:le)?s?\.?\s*\d*|"
    r"see|and|or|but|not|n\.s\.?|ns|all|both|each|either|i\.e\.?|e\.g\.?|"
    r"[A-Za-z]|Ref\.?\s*\d*)$", re.I)
_TWO_CHAR = re.compile(r"^[A-Z0-9]{2}$")


def _is_candidate(short: str) -> bool:
    short = short.strip()
    if not (2 <= len(short) <= 10) or " " in short.strip() and len(short.split()) > 2:
        return False
    if _STOP_SHORT.match(short):
        return False
    if len(short) == 2 and not _TWO_CHAR.match(short):
        return False
    if not re.search(r"[A-Z0-9]", short):
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9\-/]*$", short))


def _match_long_form(short: str, left: str) -> str | None:
    """Schwartz & Hearst's shortest-match scan, right to left.

    Every character of the short form must occur in the long form in order, and the
    first character of the short form must begin a word of it. The window is bounded at
    `min(len(short) + 5, len(short) * 2)` words, which is the bound in the paper and the
    reason "(GM)" resolves to "gray matter" and not to the whole preceding clause.

    Two departures from a naive implementation, both from reading the output: the window
    is cut at the previous sentence terminator, or "Arterial spin labeling (ASL)" resolves
    to "acute short-term effects. Arterial spin labeling"; and the SHORTEST satisfying
    candidate wins, or "Biological Parametric Mapping (BPM)" keeps the "by" in front of it.
    """

    left = re.split(r"(?<=[.!?;:])\s+", left)[-1]
    words = left.split()
    if not words:
        return None
    window = words[-min(len(short) + 5, len(short) * 2):]
    letters = [c.lower() for c in short if c.isalnum()]
    if not letters:
        return None
    for start in range(len(window) - 1, -1, -1):
        candidate = " ".join(window[start:])
        chars = [c.lower() for c in candidate if c.isalnum() or c == " "]
        index = 0
        for char in chars:
            if index < len(letters) and char == letters[index]:
                index += 1
        if index < len(letters):
            continue
        if candidate[0].lower() != letters[0]:
            continue
        return candidate.strip(" ,;:.-")
    return None


def abbreviations(text: str) -> list[tuple[str, str, int]]:
    """(short form, long form, occurrences), longest definition kept per short form."""

    found: dict[str, str] = {}
    for match in _SHORT_FORM.finditer(text):
        short = match.group(1).strip()
        if not _is_candidate(short):
            continue
        long_form = _match_long_form(short, text[max(0, match.start() - 200): match.start()])
        if long_form and len(long_form) > len(short):
            found.setdefault(short, long_form)
    counts = Counter()
    for short in found:
        counts[short] = len(re.findall(rf"\b{re.escape(short)}\b", text))
    return sorted(((s, l, counts[s]) for s, l in found.items()), key=lambda r: -r[2])


# ------------------------------------------------------------------- statistic digest

#: Signed values in this corpus use U+2212 as often as the hyphen, and a coordinate
#: parsed with the wrong sign is a coordinate in the other hemisphere.
_MINUS = r"[-‐‑‒–—−]"
_NUM = rf"{_MINUS}?\d+(?:\.\d+)?"

#: The APA shapes `statcheck` recognises (t, F, r, chi-square, Z, with exact or inexact
#: p), plus the neuroimaging-specific ones it has no reason to: a coordinate triple, a
#: cluster extent, a corrected threshold. The last three are what say an analysis
#: happened at all.
_STATISTIC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("t", re.compile(rf"\bt\s*\(\s*\d+(?:\.\d+)?\s*\)\s*[=<>]\s*{_NUM}")),
    ("F", re.compile(rf"\bF\s*\(\s*\d+(?:\.\d+)?\s*,\s*\d+(?:\.\d+)?\s*\)\s*[=<>]\s*{_NUM}")),
    ("r", re.compile(rf"\br\s*(?:\(\s*\d+\s*\))?\s*[=<>]\s*{_MINUS}?\.?\d+(?:\.\d+)?")),
    ("chi2", re.compile(rf"(?:χ|chi)\s*2?\s*\([^)]*\)\s*[=<>]\s*{_NUM}", re.I)),
    ("Z", re.compile(rf"\b[Zz]\s*[=<>]\s*{_NUM}")),
    ("beta", re.compile(rf"(?:β|beta)\s*[=<>]\s*{_NUM}", re.I)),
    ("d", re.compile(rf"\b(?:Cohen's\s*)?d\s*[=<>]\s*{_NUM}")),
    ("eta2", re.compile(rf"(?:η|eta)\s*p?\s*2?\s*[=<>]\s*{_NUM}", re.I)),
    ("p", re.compile(rf"\bp\s*(?:-?\s*value)?\s*[=<>≤≥]\s*\.?\d+(?:\.\d+)?"
                     r"(?:\s*[eE]\s*-\s*\d+)?", re.I)),
    ("CI", re.compile(rf"\d+\s*%\s*CI[^.;]{{0,40}}{_NUM}")),
    # Guarded by _COORDINATE_CUE below. pubget renders a citation list as "( 14 , 15 ,
    # 34 , 35 )", which is three comma-separated numbers and is not a location.
    ("coordinate", re.compile(rf"(?<![\d.]){_NUM}\s*,\s*{_NUM}\s*,\s*{_NUM}(?![\d.])")),
    ("cluster", re.compile(r"\b(?:k\s*[=>]\s*\d+|\d+\s*(?:contiguous\s*)?voxels?|"
                           r"cluster[- ](?:size|extent)[^.;]{0,30}\d+)", re.I)),
    ("correction", re.compile(r"\b(?:FWE|FDR|Bonferroni|TFCE|"
                              r"family[- ]wise|false discovery|small volume correction|"
                              r"uncorrected|whole[- ]brain corrected)\b", re.I)),
]


#: What has to be in the sentence before a comma-separated number triple is read as a
#: location. Prose that gives a coordinate says so; a reference list does not.
_COORDINATE_CUE = re.compile(
    r"\b(?:MNI|Talairach|ICBM|coordinate|co-ordinate|\bx\s*,\s*y\s*,\s*z\b|"
    r"peak|maxim(?:um|a)|centre of mass|center of mass|voxel|cluster|"
    r"located at|centred? (?:at|on)|centered? (?:at|on))", re.I)


def statistic_sentences(text: str) -> list[tuple[str, list[str]]]:
    """(sentence, kinds of statistic in it) for every sentence reporting a number.

    A sentence and not a value: `Cell.direction` is decided by the words around the
    statistic ("negatively correlated", "no significant"), and a table of bare numbers
    throws exactly the part that decides it away.
    """

    out = []
    for sentence in sentences(text):
        kinds = [name for name, pattern in _STATISTIC_PATTERNS if pattern.search(sentence)]
        if "coordinate" in kinds and not _COORDINATE_CUE.search(sentence):
            kinds.remove("coordinate")
        # A bare correction keyword is a Methods threshold, not a reported result. It is
        # kept only when a number sits beside it, or the digest fills with boilerplate.
        if kinds and kinds != ["correction"]:
            out.append((sentence, kinds))
    return out


# ---------------------------------------------------------------- contrast candidates

#: Cues that a sentence states a tested comparison. Grouped because the group is the
#: useful label: "compared with" says a contrast exists, "no significant" says which
#: kind, and the pair together is what the zero-foci rule is about.
_CONTRAST_CUES: list[tuple[str, re.Pattern[str]]] = [
    ("comparison", re.compile(
        r"\b(?:compared (?:with|to)|(?:as )?compared|versus|vs\.?|relative to|"
        r"contrast(?:ed|s)? (?:with|to|between)|difference[s]? (?:between|in)|"
        r"differ(?:ed|ence)? (?:from|between)|greater (?:in|than|for)|"
        r"higher (?:in|than|for)|lower (?:in|than|for)|larger|smaller|"
        r"more than|less than|exceed)", re.I)),
    ("correlation", re.compile(
        r"\b(?:correlat\w+|associat\w+|covar\w+|predict\w+|relationship between|"
        r"related to|regress\w+ on)", re.I)),
    ("factorial", re.compile(
        r"\b(?:main effect(?:s)? of|interaction (?:between|of|with|effect)|"
        r"ANOVA|ANCOVA|repeated[- ]measures|within[- ]subject|between[- ]subject|"
        r"factorial|moderat(?:ion|or|ors|ing|ed|es)|mediat(?:ion|or|ors|ing|ed|es))\b",
        re.I)),
    ("change", re.compile(
        r"\b(?:increase[ds]?|decrease[ds]?|reduction|reduced|elevat\w+|"
        r"hypo\w+|hyper\w+|pre[- ]to[- ]post|before and after|"
        r"following (?:treatment|administration|intervention))", re.I)),
    ("direction", re.compile(
        r"\b(?:positive(?:ly)?|negative(?:ly)?|inverse(?:ly)?|"
        r"greater|less|stronger|weaker)\b", re.I)),
    ("null", re.compile(
        r"\b(?:no significant|not significant|non[- ]significant|"
        r"did not (?:differ|reach|survive|show|reveal)|failed to|"
        r"no (?:difference|effect|correlation|association|cluster)|"
        r"no (?:such )?(?:region|area)s? (?:survived|showed)|absen\w+)", re.I)),
    ("hedge", re.compile(r"\b(?:trend(?:ing)? (?:towards?|for)|marginal\w*|"
                         r"at trend level|approach\w+ significance)", re.I)),
]

#: A contrast sentence has to be about a tested effect, not about the literature. A
#: results claim carries a statistic, a table pointer, a significance word or a first
#: person; a sentence with a citation marker and none of those is somebody else's finding.
#:
#: "showed", "found" and "revealed" are deliberately NOT here. They are what a Discussion
#: sentence uses to report a cited study ("Studies ... showed a decreased amplitude"), so
#: including them admits the literature review this guard exists to exclude.
_TESTED = re.compile(
    r"\b(?:significan\w+|p\s*[=<>]|Table\s*\d|Figure\s*\d|survived|threshold|"
    r"we\s+(?:found|observed|examined|tested|compared|calculated|correlated|assessed)|"
    r"our\s+(?:analys[ei]s|results|data|sample|patients|study))\b", re.I)
_CITATION = re.compile(r"\(\s*\d+\s*(?:[,–-]\s*\d+\s*)*\)|\bet al\b|\(\d{4}\)")


def contrast_candidates(text: str) -> list[tuple[str, list[str], str]]:
    """(sentence, cue groups, zone) for every sentence that states a tested comparison.

    This is the deterministic half of the dual-anchor idea. The coordinate-table parse
    enumerates the analyses a table reported and cannot see one reported only in prose,
    which on the verified paper caps analysis recall at 67% before extraction starts.
    A cue sweep over the Results text finds those, at the cost of finding other things
    too -- hence a candidate list the model is asked to confirm.
    """

    out = []
    for section in split_sections(text):
        if section.zone in ("back", "intro"):
            continue
        for sentence in sentences(section.text):
            groups = [name for name, pattern in _CONTRAST_CUES if pattern.search(sentence)]
            if not groups or groups == ["direction"] or groups == ["hedge"]:
                continue
            if not _TESTED.search(sentence) and _CITATION.search(sentence):
                continue
            out.append((sentence, groups, section.zone))
    # A table caption and the figure caption beside it are the same sentence twice, and a
    # duplicated candidate reads as two analyses.
    seen: set[str] = set()
    unique = []
    for sentence, groups, zone in out:
        key = re.sub(r"[^a-z0-9]+", " ", sentence.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        unique.append((sentence, groups, zone))
    return unique


# ------------------------------------------------------------------- method parameters

#: One entry per Methods value stated in a fixed form: (label, schema slots, pattern).
#:
#: The slots are the real field names on the extraction classes, checked against the
#: schema by `test_preprocess.py`. That is the point of the digest and not a nicety: a
#: block that labels "3T" with an invented slot name teaches the model a field that does
#: not exist, and the validator then rejects the entity it lands on. An empty slot string
#: means the schema has no field for that value; those are rendered under their own
#: heading so the model is not left to guess a home for them.
_METHOD_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("field strength", "MRI.magnetic_field_strength_tesla",
     re.compile(r"\b(\d(?:\.\d)?)\s*[- ]?(?:T|Tesla)\b(?!\w)")),
    ("scanner", "Device.manufacturer, Device.model", re.compile(
        r"\b(Siemens|Philips|General Electric|\bGE\b|Bruker|Canon|Toshiba|Hitachi|"
        r"Magnetom|Verio|Trio|TrioTim|Prisma|Skyra|Avanto|Allegra|Sonata|Vida|"
        r"Achieva|Ingenia|Intera|Gyroscan|Signa|Discovery|Premier|Elekta|Neuromag|"
        r"BioSemi|Brain Products|EGI|Neuroscan)\b")),
    ("pulse sequence", "MRI.pulse_sequence_type, MRI.mr_acquisition_type", re.compile(
        r"\b(MPRAGE|SPGR|FLAIR|EPI|GRE|GRAPPA|SENSE|MP2RAGE|DTI|DWI|DSI|NODDI|"
        r"arterial spin labeling|ASL|pCASL|PASL|BOLD|resting[- ]state|T1[- ]weighted|"
        r"T2\*?[- ]weighted|diffusion[- ]weighted|multi[- ]?band|multi[- ]?echo)\b", re.I)),
    ("repetition time", "MRI.repetition_time_seconds", re.compile(
        r"\b(?:TR|repetition time)\b[^.;)\n]{0,25}?(\d+(?:[.,]\d+)?)\s*(m?s)", re.I)),
    ("echo time", "MRI.echo_time_seconds", re.compile(
        r"\b(?:TE|echo time)\b[^.;)\n]{0,25}?(\d+(?:[.,]\d+)?)\s*(m?s)", re.I)),
    ("voxel size", "MRI.acquisition_voxel_size_mm", re.compile(
        r"(\d+(?:\.\d+)?)\s*(?:×|x|\*)\s*(\d+(?:\.\d+)?)\s*(?:×|x|\*)\s*"
        r"(\d+(?:\.\d+)?)\s*mm", re.I)),
    ("volumes", "MRI.number_of_volumes", re.compile(
        r"\b(\d+)\s+(?:volumes|time points|dynamics|frames)\b", re.I)),
    ("modality", "Acquisition.modality, Acquisition.acquisition_type", re.compile(
        r"\b(fMRI|functional MRI|structural MRI|sMRI|MRI|PET|SPECT|EEG|MEG|fNIRS|"
        r"NIRS|DTI|dMRI|ASL|perfusion imaging)\b")),
    ("software", "Preprocessing.software, ModelEstimation.software", re.compile(
        r"\b(SPM\s*\d*|FSL\s*[\d.]*|FreeSurfer|AFNI|ANTs|CAT\s*1?2|VBM\s*[58]?|"
        r"CONN|DPARSF|DPABI|BrainVoyager|fMRIPrep|Nilearn|MRtrix|Camino|EEGLAB|"
        r"Brainstorm|FieldTrip|MATLAB|Marsbar|WFU PickAtlas|"
        r"Biological Parametric Mapping|BPM)\b", re.I)),
    # Case-sensitive, because these tool names are ordinary words: an insensitive `FIRST`
    # reports "first" as the segmentation tool in every Methods section that has one.
    ("software", "Preprocessing.software, ModelEstimation.software",
     re.compile(r"\b(FEAT|MELODIC|FIRST|FLIRT|FNIRT|TBSS|MNE|FIX)\b")),
    ("smoothing", "Preprocessing.smoothing_fwhm_mm", re.compile(
        r"(?:FWHM|full[- ]width)[^.;)\n]{0,30}?(\d+(?:\.\d+)?)\s*mm|"
        r"(\d+(?:\.\d+)?)\s*mm[^.;)\n]{0,20}?(?:FWHM|full[- ]width)", re.I)),
    ("preprocessing step", "Preprocessing.steps", re.compile(
        r"\b(realign\w+|motion correct\w+|slice[- ]tim\w+|coregist\w+|co-regist\w+|"
        r"segment\w+|normali[sz]\w+|smooth\w+|bias correct\w+|skull[- ]strip\w+|"
        r"despik\w+|band[- ]pass|high[- ]pass|nuisance regress\w+|"
        r"DARTEL|unified segmentation|scrubbing|ICA[- ]AROMA)\b", re.I)),
    ("model family", "ModelEstimation.model_family, ModelEstimation.model_type", re.compile(
        r"\b(general linear model|GLM|mixed[- ]effects?|random[- ]effects?|"
        r"fixed[- ]effects?|ANOVA|ANCOVA|MANOVA|t[- ]test|paired t test|"
        r"regression|correlation analysis|ICA|independent component analysis|"
        r"seed[- ]based|PPI|psychophysiological interaction|DCM|SEM|"
        r"support vector|searchlight|MVPA|multivariate pattern|"
        r"k[- ]means|LASSO|mediation|moderation)\b", re.I)),
    ("height threshold", "InferenceSettings.height_threshold_value, .height_threshold_type",
     re.compile(r"\b(?:p\s*[<=≤]\s*\.?\d+(?:\.\d+)?)\s*(?:,|\s)*"
                r"(?:FWE|FDR|Bonferroni|uncorrected|corrected|TFCE|cluster[- ]level|"
                r"voxel[- ]level|peak[- ]level)[- \w]{0,20}", re.I)),
    # The unit is required. Without it the pattern reads "extent threshold of p < 0.05"
    # as a 0-voxel extent, which is a value the paper does not state.
    ("cluster extent", "InferenceSettings.cluster_extent_threshold", re.compile(
        r"\b(?:cluster (?:extent|size|threshold)|extent threshold|minimum cluster)"
        r"[^.;)\n]{0,25}?\d+\s*(?:voxels?|mm3)|\bk\s*[=>]\s*\d+", re.I)),
    ("correction", "InferenceSettings.multiple_comparison_method, .correction_scope",
     re.compile(r"\b(FWE|family[- ]wise error|FDR|false discovery rate|Bonferroni|"
                r"permutation|TFCE|small volume correction|uncorrected)\b", re.I)),
    ("search volume", "InferenceSettings.search_volume, Region.definition_method",
     re.compile(r"\b(whole[- ]brain|explicit mask|small volume correction|"
                r"region of interest|ROI|search volume|gray matter mask|"
                r"grey matter mask|anatomical mask|functional mask)\b", re.I)),
    ("design", "StudyDesign.allocation, .assignment_structure, .blinding, Task.design_type",
     re.compile(r"\b(block(?:ed)?[- ]design|event[- ]related|mixed design|"
                r"resting[- ]state|task[- ]free|cross[- ]over|crossover|"
                r"double[- ]blind|single[- ]blind|placebo[- ]controlled|"
                r"randomi[sz]ed|counterbalanced|within[- ]subject|"
                r"between[- ]subject|longitudinal)\b", re.I)),
    # No slot holds these. They are listed because a reader of the digest would otherwise
    # assume the paper did not state them, and because they belong in a description.
    ("inversion time", "", re.compile(
        r"\b(?:TI|inversion time)\b[^.;)\n]{0,25}?(\d+(?:[.,]\d+)?)\s*(m?s)", re.I)),
    ("flip angle", "", re.compile(
        r"\bflip angle\b[^.;)\n]{0,20}?(\d+(?:\.\d+)?)\s*(?:°|deg)", re.I)),
    ("slices", "", re.compile(
        r"\b(\d+)\s+(?:contiguous\s+|axial\s+|transverse\s+)?slices\b", re.I)),
    ("runs or sessions", "", re.compile(r"\b(\d+)\s+(?:runs|sessions|blocks)\b", re.I)),
    ("coordinate space", "on the ANALYSES pass, not this one", re.compile(
        r"\b(MNI\s*\d*|ICBM\s*\d*|Talairach(?:\s*(?:and|&)\s*Tournoux)?|"
        r"fsaverage|Colin\s*27|standard (?:space|template))\b", re.I)),
]


def _sweep(text: str, patterns: list[tuple[str, str, re.Pattern[str]]], zones: set[str],
           cap: int) -> dict[str, tuple[str, list[str]]]:
    """label -> (schema slots, distinct matched strings), over the named zones only.

    Zone-scoped because the same numbers recur in the Results and the Discussion with a
    different referent: a TR quoted in the Discussion is somebody else's protocol.

    Accumulated per label rather than assigned, so one label can be filled by more than
    one pattern -- a case-sensitive one for acronyms that are also words, and an
    insensitive one for names that are not.
    """

    scope = "\n".join(s.text for s in split_sections(text) if s.zone in zones)
    found: dict[str, tuple[str, list[str]]] = {}
    for label, slots, pattern in patterns:
        _, seen = found.setdefault(label, (slots, []))
        for match in pattern.finditer(scope):
            value = re.sub(r"\s+", " ", match.group(0)).strip(" ,;:.")
            if value.lower() not in {v.lower() for v in seen}:
                seen.append(value)
        del seen[cap:]
    return {label: (slots, values) for label, (slots, values) in found.items() if values}


def method_parameters(text: str) -> dict[str, tuple[str, list[str]]]:
    return _sweep(text, _METHOD_PATTERNS, {"methods", "front"}, cap=12)


# ----------------------------------------------------------------------- cohort digest

_NUMBER_WORD = (r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
                r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred")
#: A run of characters inside one sentence. A bare `[^.\n]` cannot cross a decimal
#: point, so it stops dead in "mean age 40.7" -- which is exactly where the sentence
#: stating the sample size puts its demographics.
_INSIDE_SENTENCE = r"(?:[^.\n]|\.(?=\d))"

#: Nouns a count phrase can end on. Deliberately without `male`/`female`/`men`/`women`:
#: those have their own pattern, and leaving them here makes "Fourteen (eight male, six
#: female; mean age 40.7) non-left-handed patients" match as far as "male" and report
#: three fragments where the sentence states one sample.
_PERSON = (r"patients?|participants?|subjects?|volunteers?|controls?|adults?|children|"
           r"adolescents?|infants?|individuals?|cases?|"
           r"smokers?|non[- ]smokers?|athletes?|students?|twins?")

#: A cohort is the entity the extractor most often fills from the wrong sentence: the
#: sample size in the abstract, the sex split in the Methods, the exclusions in a third
#: paragraph. (label, schema slots, pattern), as in `_METHOD_PATTERNS`.
_COHORT_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    # The gap tolerates a parenthetical, because the sentence that states the sample
    # usually puts the demographics between the count and the noun: "Fourteen (eight
    # male, six female; mean age 40.7) non-left-handed patients". A word-only gap misses
    # every cohort described that way, which is most of them.
    ("count phrase", "Group.enrolled_count, Group.acquired_count", re.compile(
        rf"\b(?:(?:{_NUMBER_WORD})|\d+)\s+{_INSIDE_SENTENCE}{{0,120}}?(?:{_PERSON})\b",
        re.I)),
    ("n =", "Group.enrolled_count, Group.acquired_count",
     re.compile(r"\bn\s*=\s*\d+", re.I)),
    ("sex", "Group.sex_distribution (CategoryDistribution: category, count)", re.compile(
        rf"\b(?:(?:{_NUMBER_WORD})|\d+)\s*(?:male|female|men|women|boys|girls)\b", re.I)),
    ("age", "Group.age_mean, .age_standard_deviation, .age_minimum, .age_maximum",
     re.compile(r"\b(?:mean\s+)?age[d]?\b[^.;)\n]{0,40}?\d+(?:\.\d+)?"
                r"(?:\s*(?:±|\+/?-|SD|SEM)\s*\d+(?:\.\d+)?)?(?:\s*years?)?|"
                r"\b\d+(?:\.\d+)?\s*(?:±|\+/?-)\s*\d+(?:\.\d+)?\s*years?\b", re.I)),
    ("handedness", "Group.handedness_distribution",
     re.compile(r"\b(?:right|left|non[- ]left|mixed)[- ]handed\b", re.I)),
    ("diagnosis", "Group.medical_condition, .diagnostic_system, .diagnostic_instrument",
     re.compile(r"\b(?:DSM[- ]?(?:IV|5|V)(?:-TR)?|ICD[- ]?\d+|SCID(?:-\w+)?|MINI|"
                r"diagnos\w+ (?:of|with)\s+[\w \-]{3,40}|"
                r"met criteria for\s+[\w \-]{3,40})", re.I)),
    ("health status", "Group.is_healthy", re.compile(
        r"\b(?:healthy (?:controls?|volunteers?|participants?|subjects?)|"
        r"no (?:history of|psychiatric|neurological))\b", re.I)),
    ("criteria", "Group.inclusion_criteria, Group.exclusion_criteria",
     re.compile(r"\b(?:inclusion|exclusion) criteria\b", re.I)),
    ("arm", "StudyDesign.arms (Arm.name, .arm_kind, .agent)", re.compile(
        r"\b(?:placebo|sham|saline|vehicle|control group|treatment group|"
        r"active (?:treatment|condition)|verum|drug condition|"
        r"randomi[sz]ed to|assigned to)\b", re.I)),
    ("timepoint", "StudyDesign.timepoints (Timepoint.name, .relation_to_intervention)",
     re.compile(r"\b(?:baseline|follow[- ]up|pre[- ](?:treatment|test|intervention|scan)|"
                r"post[- ](?:treatment|test|intervention|scan)|"
                r"session\s*[12]|day\s*\d+|week\s*\d+|month\s*\d+|"
                r"time ?point\s*\d*|visit\s*\d*|"
                r"scanned (?:twice|two times|on (?:two|both) (?:occasions|days))|"
                r"(?:two|both) (?:sessions|scans|visits|occasions|measurements)|"
                r"each session|(?:on|at) (?:both|either) points? of the measurement|"
                r"repeated (?:after|at))\b", re.I)),
    ("attrition", "Group.excluded_count", re.compile(
        r"\b(?:excluded|dropped out|discontinued|withdrew|lost to follow[- ]up|"
        r"did not complete|failed quality|motion artefact\w*)\b", re.I)),
    ("medication", "Group.medications, Group.medication_status", re.compile(
        r"\b(?:medication[- ]free|drug[- ]na(?:i|ï)ve|unmedicated|"
        r"on (?:stable )?(?:medication|antidepressant|antipsychotic)|"
        r"treated with|maintained on)\b", re.I)),
]


def cohort_parameters(text: str) -> dict[str, tuple[str, list[str]]]:
    return _sweep(text, _COHORT_PATTERNS, {"methods", "front", "results"}, cap=14)


# --------------------------------------------------------------------- region gazetteer

#: The head nouns anatomy is named with, and the modifiers that may precede one. A
#: whitelist and not "up to four words", because the greedy version reads "restrict our
#: analyses to fronto-temporal regions" as a region called "analyses to fronto-temporal
#: regions". A pattern rather than an atlas lookup, because the label a paper uses is its
#: own ("Paracingulate Gyrus/ACC") and `Region.name` wants the paper's wording.
_ANATOMY_MODIFIER = (
    r"left|right|bilateral|unilateral|contralateral|ipsilateral|"
    r"anterior|posterior|superior|inferior|middle|mid|medial|lateral|dorsal|ventral|"
    r"rostral|caudal|dorsolateral|ventrolateral|ventromedial|dorsomedial|"
    r"subgenual|pregenual|perigenual|supragenual|"
    r"frontal|temporal|parietal|occipital|insular|cingulate|limbic|striatal|"
    r"prefrontal|orbitofrontal|precentral|postcentral|paracentral|paracingulate|"
    r"fusiform|lingual|entorhinal|perirhinal|parahippocampal|retrosplenial|"
    r"supramarginal|angular|calcarine|cuneal|precuneal|opercular|orbital|polar|"
    r"fronto[- ]temporal|fronto[- ]parietal|temporo[- ]parietal|occipito[- ]temporal|"
    r"sensorimotor|somatosensory|premotor|motor|visual|auditory|olfactory|gustatory|"
    r"default mode|salience|executive|fronto[- ]?striatal|cortico[- ]?striatal|"
    r"gray matter|grey matter|white matter|whole[- ]brain|sub[- ]?cortical|cortical|"
    r"heteromodal|association|primary|secondary|higher[- ]order"
)
_ANATOMY_HEAD_NOUN = (
    r"gyrus|gyri|sulcus|sulci|cortex|cortices|lobe|lobes|lobule|lobules|"
    r"nucleus|nuclei|area|areas|region|regions|pole|operculum|"
    r"striatum|cerebellum|vermis|amygdala|hippocampus|thalamus|insula|"
    r"putamen|caudate|pallidum|accumbens|claustrum|precuneus|cuneus|"
    r"colliculus|brainstem|midbrain|pons|medulla|hypothalamus|habenula|"
    r"corpus callosum|network|seed|seeds|parcel|parcels|cluster|clusters"
)
_ANATOMY_HEAD = re.compile(
    rf"\b((?:(?:{_ANATOMY_MODIFIER})[- ]){{1,4}}(?:{_ANATOMY_HEAD_NOUN}))\b", re.I)

#: `frontal and temporal lobe` is two regions sharing one head noun, and gold for the
#: verified paper contains exactly that pair. Read as one string it is a region the paper
#: does not have; split, it is the two it does.
_SHARED_HEAD = re.compile(
    rf"\b((?:{_ANATOMY_MODIFIER})(?:[- ](?:{_ANATOMY_MODIFIER}))*)\s*"
    rf"(?:,\s*)?(?:and|or|&)\s+"
    rf"((?:{_ANATOMY_MODIFIER})(?:[- ](?:{_ANATOMY_MODIFIER}))*)\s+"
    rf"({_ANATOMY_HEAD_NOUN})\b", re.I)

#: The acronyms that name a region without any of the head nouns above.
_ANATOMY_ACRONYM = re.compile(
    r"\b(ACC|dACC|pgACC|sgACC|PCC|mPFC|vmPFC|dmPFC|dlPFC|vlPFC|OFC|IFG|MFG|SFG|"
    r"STG|MTG|ITG|TPJ|IPL|SPL|IPS|FEF|SMA|preSMA|M1|S1|V1|V2|V4|MT|FFA|PPA|EBA|"
    r"NAcc|VTA|SN|BNST|PAG|LC|DRN|VS|VP|PHC|ERC|RSC|AI|PI|SMG|AG|"
    r"BA\s*\d+|Brodmann area \d+)\b")

#: A region mention is only a `Region` entity when the study *delimited* it. These are
#: the words that say it did.
_ROI_CONTEXT = re.compile(
    r"\b(?:region[s]? of interest|ROI[s]?|mask(?:ed|ing)?|seed[s]?|"
    r"sphere[s]?|radius|atlas|parcel\w*|template|search volume|"
    r"anatomically defined|functionally defined|small volume|"
    r"restricted? (?:to|our)|confined to|limited to|explicit mask|"
    r"target[s]?|node[s]?|component[s]?)\b", re.I)


def _anatomy_in(sentence: str) -> list[str]:
    """Region names in one sentence, with a shared head noun distributed over both."""

    names: list[str] = []
    consumed: list[tuple[int, int]] = []
    for match in _SHARED_HEAD.finditer(sentence):
        head = match.group(3)
        names += [f"{match.group(1)} {head}", f"{match.group(2)} {head}"]
        consumed.append(match.span())
    for match in list(_ANATOMY_HEAD.finditer(sentence)) + \
            list(_ANATOMY_ACRONYM.finditer(sentence)):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        names.append(match.group(1))
    out: list[str] = []
    for name in names:
        name = re.sub(r"\s+", " ", name).strip(" ,;:.-")
        if 3 <= len(name) <= 60 and name.lower() not in {o.lower() for o in out}:
            out.append(name)
    return out


def region_mentions(text: str) -> tuple[list[str], list[str]]:
    """(regions the study delimited, regions a result table reported).

    The split is the point. `Region` exists for places the study *defined* -- an ROI, a
    mask, a seed -- and the entity pass on the verified paper emitted none, leaving
    `Analysis.regions` with nothing to point at. A table's anatomy column is a different
    thing: it labels a coordinate, and it belongs to a focus and not to a Region. Both
    are listed so the model can tell them apart; merging them is what puts a result
    cluster in the ROI inventory.
    """

    delimited: list[str] = []
    for section in split_sections(text):
        if section.zone not in ("methods", "front"):
            continue
        for sentence in sentences(section.text):
            if not _ROI_CONTEXT.search(sentence):
                continue
            for name in _anatomy_in(sentence):
                if name.lower() not in {d.lower() for d in delimited}:
                    delimited.append(name)

    reported: list[str] = []
    for block in table_blocks(text):
        for line in block.split("\n"):
            if not line.startswith("|") or set(line) <= set("|- "):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells or not cells[0]:
                continue
            head = cells[0]
            if (_ANATOMY_HEAD.search(head) or _ANATOMY_ACRONYM.search(head)) and \
                    head.lower() not in {r.lower() for r in reported}:
                reported.append(head)
    return delimited[:40], reported[:40]


# ------------------------------------------------------------- BM25 sentence retrieval

_STOPWORDS = set("""a an the and or but if then than that this these those of to in on at
by for with without from as is are was were be been being it its into over under about
we our us they their he she his her not no nor also such which who whom whose can could
may might will would shall should must have has had do does did done more most less
least very much many few both each other same all any some one two three there here when
where while during after before between within across per each using used use based
respectively however therefore thus moreover furthermore addition""".split())


def _terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9_\-]{2,}", text.lower())
            if w not in _STOPWORDS]


#: What stands where `retrieval` dropped a sentence. No terminal punctuation before the
#: bracket, or the sentence splitter reads the marker itself as a sentence end.
OMISSION = "[text omitted]"

#: Prepended by `reorder`, and a named constant because `test_preprocess.py` has to be
#: able to tell this note apart from a sentence of the paper.
REORDER_NOTE = (
    "[The sections below are NOT in the paper's order. Front matter, Methods and\n"
    "Results come first, the Introduction and Discussion after them, the result\n"
    "tables last. Nothing has been removed. Section headings are unchanged, so a\n"
    "reference to 'the Methods' still means the section with that heading.]\n\n")


def bm25_select(text: str, query: str, budget: float = 0.45,
                k1: float = 1.5, b: float = 0.75) -> str:
    """Keep the highest-scoring prose sentences, in document order.

    Retrieval and not summarisation: every kept sentence is the paper's own, so a value
    read out of the reduced text is still a value the paper states. Headings, front
    matter and every table block survive whatever their score -- the abstract states the
    design in two hundred words and the tables are the only source of a coordinate, so
    scoring them against a keyword query could only lose.
    """

    sections = split_sections(text)
    keep_whole = {"front", "tables"}
    # (section, paragraph, sentence). The paragraph is carried so the reduction can put a
    # break back where the paper had one: two kept sentences from different paragraphs
    # run together assert an adjacency the paper does not have, and unlike a dropped
    # sentence there is no marker to say so.
    pool: list[tuple[int, int, str]] = []
    for index, section in enumerate(sections):
        if section.zone in keep_whole:
            continue
        for number, paragraph in enumerate(paragraphs(section.body)):
            for sentence in sentences_of(paragraph):
                pool.append((index, number, sentence))
    if not pool:
        return text

    documents = [_terms(sentence) for *_, sentence in pool]
    lengths = [len(d) for d in documents]
    average = sum(lengths) / len(lengths) or 1.0
    frequency = Counter(term for document in documents for term in set(document))
    total = len(documents)
    query_terms = set(_terms(query))

    scores = []
    for document, length in zip(documents, lengths):
        counts = Counter(document)
        score = 0.0
        for term in query_terms & counts.keys():
            idf = math.log(1 + (total - frequency[term] + 0.5) / (frequency[term] + 0.5))
            score += idf * counts[term] * (k1 + 1) / (
                counts[term] + k1 * (1 - b + b * length / average))
        scores.append(score)

    target = budget * sum(len(s) for *_, s in pool)
    order = sorted(range(len(pool)), key=lambda i: -scores[i])
    kept, size = set(), 0
    for index in order:
        if size >= target:
            break
        kept.add(index)
        size += len(pool[index][-1])

    # Rendered in document order with a marker for every run of dropped sentences. The
    # marker is load-bearing: without it a reduced section reads as a complete one, and
    # "the paper does not say" becomes indistinguishable from "the sentence that said it
    # scored below the cut".
    by_section: dict[int, list[tuple[int, str, bool]]] = {}
    for position, (index, number, sentence) in enumerate(pool):
        by_section.setdefault(index, []).append((number, sentence, position in kept))

    out = []
    for index, section in enumerate(sections):
        if section.zone in keep_whole:
            out.append(section.text)
            continue
        entries = by_section.get(index, [])
        tables = table_blocks(section.body)
        if not any(keep for *_, keep in entries) and not tables:
            continue
        rendered, current, number, dropped = [], [], None, False
        for paragraph, sentence, keep in entries:
            if paragraph != number:
                if current:
                    rendered.append(" ".join(current))
                current, number = [], paragraph
            if keep:
                if dropped:
                    current.append(OMISSION)
                    dropped = False
                current.append(sentence)
            else:
                dropped = True
        if dropped:
            current.append(OMISSION)
        if current:
            rendered.append(" ".join(current))
        head = f"{'#' * section.level} {section.heading}" if section.level else ""
        out.append("\n\n".join(part for part in [head, *rendered, *tables] if part))
    return "\n\n".join(out)


#: The retrieval query, built from what the schema asks for rather than from what the
#: paper happens to say. Slot names and vocabulary words, one bag: BM25 needs no
#: structure and a per-class query would select the same sentences several times over.
RETRIEVAL_QUERY = """
participants patients subjects sample size age sex female male handedness diagnosis
inclusion exclusion criteria group arm placebo treatment randomised crossover blinding
timepoint baseline session scan scanner tesla siemens philips sequence mprage epi asl
bold diffusion repetition echo inversion time flip angle voxel slices volumes runs
software spm fsl freesurfer afni preprocessing realignment normalisation segmentation
smoothing fwhm template mni talairach space model estimation glm regression anova
ancova term covariate factor level contrast comparison correlation association
interaction main effect analysis whole brain region of interest roi mask seed sphere
atlas parcel measure signal metric threshold corrected uncorrected fwe fdr bonferroni
cluster extent significant positive negative increased decreased greater lower
hypothesis task condition stimuli instructions response block event related
"""


# ------------------------------------------------------------------- digest rendering

_CAUTION = (
    "Derived from the paper by regular expression, not read. It over-generates: some\n"
    "entries are not what they look like and some are duplicates of one fact. Treat it as\n"
    "a list of places to look, confirm every entry against the paper text below, and drop\n"
    "whatever the paper does not support. It adds nothing the paper does not contain, so\n"
    "it can never be the source for a value -- the paper is."
)


def _block(title: str, body: str) -> str:
    if not body.strip():
        return ""
    return f"\n## {title}\n\n{_CAUTION}\n\n{body.rstrip()}\n"


#: The heading of each digest, and the static preamble under it. Named constants and not
#: inline literals because
#: `tests/test_prompt_leakage.py` has to enumerate every string that reaches a prompt
#: without varying with the paper, and an f-string buried in a function is invisible to it.
ABBREV_TITLE = "Abbreviations defined in this paper"
STATS_TITLE = "Sentences reporting a statistic"
CONTRAST_TITLE = "Candidate tested comparisons"
METHOD_TITLE = "Method parameters found in the Methods section"
COHORT_TITLE = "Sample and design phrases"
REGION_TITLE = "Anatomical mentions"

ABBREV_NOTE = (
    "Every short form the paper expands, with the expansion and how often the short\n"
    "form occurs. Where a field wants a name, prefer the paper's own wording; where two\n"
    "passes must agree on one entity, this is what they should agree on.")
STATS_NOTE = (
    "Every sentence carrying a test statistic, a p-value, a coordinate triple or a\n"
    "cluster extent. A result reported only in prose appears here and in no coordinate\n"
    "table, which is the case the table parse cannot reach.")
CONTRAST_NOTE = (
    "Sentences whose wording states a comparison, a correlation, a factorial effect or\n"
    "the absence of one. `null` marks a tested effect the paper says found nothing --\n"
    "still an analysis. `hedge` marks a claim the paper does not assert. This list is\n"
    "wider than the set of analyses: a sentence restating the hypothesis or summarising\n"
    "the discussion matches the same cues as the result it refers to.")
METHOD_NOTE = (
    "Each line is a candidate value and the extraction field it is a candidate for.\n"
    "Several values under one label usually means several entities -- two acquisitions,\n"
    "two preprocessing pipelines -- and not a contradiction to resolve.")
COHORT_NOTE = (
    "Candidates for Group, CategoryDistribution, Arm and Timepoint, with the field each\n"
    "is for. A count that appears twice with different numbers is the enrolled-versus-\n"
    "analysed distinction, and the analysed one is what an analysis was run on.")
REGION_DELIMITED_NOTE = (
    "Named in a Methods sentence that also mentions an ROI, mask, seed, sphere, atlas or\n"
    "parcel -- so these are candidates for a `Region` entity, which THIS PASS is the only\n"
    "place that can create one:")
REGION_REPORTED_NOTE = (
    "\nAnatomy labels in the first column of a result table. These label a coordinate the\n"
    "analysis found; they are NOT Regions unless the study also delimited them:")
ORPHAN_NOTE = (
    "\n  No extraction field holds the values below. Do not invent one for them; they\n"
    "  belong in the owning entity's `description` if anywhere.")


def abbreviation_block(text: str) -> str:
    rows = abbreviations(text)
    if not rows:
        return ""
    body = "\n".join(f"  {short:<12} {long_form}   [{count}x]"
                     for short, long_form, count in rows[:40])
    return _block(ABBREV_TITLE, ABBREV_NOTE + "\n\n" + body)


def statistic_block(text: str) -> str:
    rows = statistic_sentences(text)
    if not rows:
        return ""
    body = "\n".join(f"  [{'+'.join(kinds)}] {sentence[:400]}" for sentence, kinds in rows[:60])
    return _block(STATS_TITLE, STATS_NOTE + "\n\n" + body)


def contrast_block(text: str) -> str:
    rows = contrast_candidates(text)
    if not rows:
        return ""
    body = "\n".join(f"  [{zone}: {'+'.join(groups)}] {sentence[:400]}"
                     for sentence, groups, zone in rows[:70])
    return _block(CONTRAST_TITLE, CONTRAST_NOTE + "\n\n" + body)


def _slotted(found: dict[str, tuple[str, list[str]]]) -> str:
    """Rendered with the schema field beside each label, unslotted values kept apart.

    The slot name is what makes this a list of candidate *values for fields* rather than
    a bag of strings. Values the schema has no field for are grouped under their own
    heading, because a labelled value with nowhere to go is an invitation to invent a
    field name -- and the validator rejects the entity that carries one.
    """

    lines, orphans = [], []
    for label, (slots, values) in found.items():
        rendered = " | ".join(values)
        if slots:
            lines.append(f"  {label:<20} -> {slots}\n      {rendered}")
        else:
            orphans.append(f"  {label:<20} {rendered}")
    if orphans:
        lines.append(ORPHAN_NOTE)
        lines += orphans
    return "\n".join(lines)


def method_block(text: str) -> str:
    found = method_parameters(text)
    if not found:
        return ""
    return _block(METHOD_TITLE,
                  METHOD_NOTE + "\n\n" + _slotted(found))


def cohort_block(text: str) -> str:
    found = cohort_parameters(text)
    if not found:
        return ""
    return _block(COHORT_TITLE, COHORT_NOTE + "\n\n" + _slotted(found))


def region_block(text: str) -> str:
    delimited, reported = region_mentions(text)
    if not delimited and not reported:
        return ""
    parts = []
    if delimited:
        parts.append(REGION_DELIMITED_NOTE + "\n"
                     + "\n".join(f"  {name}" for name in delimited))
    if reported:
        parts.append(REGION_REPORTED_NOTE + "\n"
                     + "\n".join(f"  {name}" for name in reported))
    return _block(REGION_TITLE, "\n".join(parts))


# ------------------------------------------------------------------------- strategies

@dataclass
class Prepared:
    """What a strategy hands the prompt builder."""

    text: str
    digest: str = ""


@dataclass
class Strategy:
    """One preprocessing arm.

    `modes` is which extractor passes receive the digest, and it is part of the design
    rather than a convenience: a cohort digest handed to the analyses pass is noise in a
    prompt that is already 120k characters, and an arm that adds noise to one pass to
    help another cannot be attributed. Text transforms apply to every pass.
    """

    name: str
    kind: str
    describe: str
    modes: tuple[str, ...] = ("entities", "analyses", "demands", "satisfy")
    text_fn: object = None
    digest_fn: object = None
    #: Build the digest from the pass rather than from the text alone, so one arm can
    #: send the analyses side different blocks from the entity side.
    routed: bool = False
    aimed_at: str = ""

    def apply(self, text: str, mode: str) -> Prepared:
        prepared = Prepared(self.text_fn(text) if self.text_fn else text)
        if self.routed:
            prepared.digest = _combined_digest(text, mode)
        elif self.digest_fn and mode in self.modes:
            prepared.digest = self.digest_fn(text)
        return prepared


def _zones(text: str, keep: set[str]) -> str:
    return "\n\n".join(s.text for s in split_sections(text) if s.zone in keep)


def _reorder(text: str) -> str:
    """Methods and Results first, the argument in the middle, the tables last.

    Attention over a long prompt is U-shaped -- highest at the start and the end, lowest
    in the middle -- so the zones that hold facts go at the ends and the zones that hold
    argument go where attention is weakest. Nothing is dropped, which is what separates
    this arm from `sections`: if reordering wins and dropping does not, position is the
    mechanism and not content.
    """

    sections = split_sections(text)
    lead = ["front", "methods", "results"]
    middle = ["intro", "discussion", "other", "back"]
    tail = ["tables"]
    ordered = []
    for zone in lead + middle + tail:
        ordered += [s for s in sections if s.zone == zone]
    seen = {id(s) for s in ordered}
    ordered += [s for s in sections if id(s) not in seen]
    return REORDER_NOTE + "\n\n".join(s.text for s in ordered)


def _combined_digest(text: str, mode: str) -> str:
    """The digests that suit this pass, in one block.

    Routed by pass rather than concatenated: the analyses side needs the statistics and
    the comparison candidates, the entity side needs the Methods parameters, the cohort
    phrases and the ROI mentions, and both need the abbreviations. Sending all six to
    both would double the added context to serve one of them.
    """

    analysis_side = mode in ("analyses", "demands")
    parts = [abbreviation_block(text), region_block(text)]
    parts += ([statistic_block(text), contrast_block(text)] if analysis_side
              else [method_block(text), cohort_block(text)])
    return "".join(parts)


STRATEGIES: dict[str, Strategy] = {}


def _register(strategy: Strategy) -> Strategy:
    STRATEGIES[strategy.name] = strategy
    return strategy


_register(Strategy(
    "sections", "text",
    "drop the Introduction, the Discussion and the back matter",
    text_fn=lambda t: _zones(t, {"front", "methods", "results", "tables"}),
    aimed_at="hallucinated analyses copied out of the literature review"))

_register(Strategy(
    "reorder", "text",
    "same content, Methods and Results first and the tables last",
    text_fn=_reorder,
    aimed_at="facts that sit in the middle of a 120k-character prompt"))

_register(Strategy(
    "retrieval", "text",
    "BM25 over sentences against a schema-derived query, 45% of the prose kept",
    text_fn=lambda t: bm25_select(t, RETRIEVAL_QUERY, budget=0.45),
    aimed_at="prose that fills no slot"))

_register(Strategy(
    "abbrev", "digest",
    "Schwartz-Hearst abbreviation table ahead of the paper",
    digest_fn=abbreviation_block,
    aimed_at="two passes naming one entity two ways"))

_register(Strategy(
    "stats", "digest",
    "every sentence reporting a statistic, p-value, coordinate or cluster extent",
    modes=("analyses", "demands"), digest_fn=statistic_block,
    aimed_at="a result reported in prose and in no coordinate table"))

_register(Strategy(
    "contrasts", "digest",
    "cue-phrase sweep for tested comparisons, with null and hedge flags",
    modes=("analyses", "demands"), digest_fn=contrast_block,
    aimed_at="analysis recall: the text-side half of a dual anchor"))

_register(Strategy(
    "methods", "digest",
    "Methods parameters labelled with the extraction field each is a candidate for",
    modes=("entities", "satisfy"), digest_fn=method_block,
    aimed_at="Acquisition, Preprocessing and InferenceSettings field accuracy"))

_register(Strategy(
    "cohort", "digest",
    "sample size, sex, age, arms and timepoints as labelled candidates",
    modes=("entities", "satisfy"), digest_fn=cohort_block,
    aimed_at="Group, CategoryDistribution, Arm and Timepoint"))

_register(Strategy(
    "regions", "digest",
    "anatomy mentions split into ROI-context and result-table labels",
    digest_fn=region_block,
    aimed_at="Region recall, and Analysis.regions having anything to point at"))

_register(Strategy(
    "combo", "both",
    "section-scoped text plus the digests each pass is served by",
    text_fn=lambda t: _zones(t, {"front", "methods", "results", "tables"}),
    routed=True,
    aimed_at="whether the digests compose or crowd each other out"))


#: Every string this module can put in a prompt that does not vary with the paper.
#: `tests/test_prompt_leakage.py` reads it, so a new block's preamble is defended the
#: moment it is added to this tuple -- and `test_preprocess.py` asserts every `_block`
#: caller draws its preamble from here, so one cannot be added without it.
PROMPT_LITERALS = (
    _CAUTION, OMISSION, REORDER_NOTE, ORPHAN_NOTE,
    ABBREV_TITLE, ABBREV_NOTE, STATS_TITLE, STATS_NOTE,
    CONTRAST_TITLE, CONTRAST_NOTE, METHOD_TITLE, METHOD_NOTE,
    COHORT_TITLE, COHORT_NOTE, REGION_TITLE,
    REGION_DELIMITED_NOTE, REGION_REPORTED_NOTE,
)


def apply_strategy(name: str, text: str, mode: str) -> Prepared:
    """The one entry point `extract_record.py` calls."""

    if not name or name == "none":
        return Prepared(text)
    if name not in STRATEGIES:
        raise KeyError(f"unknown preprocessing strategy {name!r}; "
                       f"have {', '.join(sorted(STRATEGIES))}")
    return STRATEGIES[name].apply(text, mode)


# ------------------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--text", type=Path, help="the paper text")
    parser.add_argument("--strategy", default="none")
    parser.add_argument("--mode", default="demands")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--show", action="store_true", help="print the transformed prompt")
    parser.add_argument("--sections", action="store_true", help="print the zone map")
    args = parser.parse_args()

    if args.list:
        print(f"{'name':<12} {'kind':<8} {'passes':<28} description")
        for strategy in STRATEGIES.values():
            passes = ("routed by pass" if strategy.routed
                      else "all" if strategy.kind == "text"
                      else ",".join(strategy.modes))
            print(f"{strategy.name:<12} {strategy.kind:<8} {passes:<28} {strategy.describe}")
            print(f"{'':<12} {'':<8} {'':<28} aimed at: {strategy.aimed_at}")
        return 0

    if not args.text:
        parser.error("--text is required unless --list")
    text = args.text.read_text(encoding="utf-8")

    if args.sections:
        for section in split_sections(text):
            print(f"  {section.zone:<11} {len(section.text):>7,}  "
                  f"{'#' * section.level} {section.heading[:60]}")
        return 0

    prepared = apply_strategy(args.strategy, text, args.mode)
    if args.show:
        print(prepared.digest)
        print(prepared.text)
        return 0
    print(f"{args.strategy} / {args.mode}: paper {len(text):,} -> {len(prepared.text):,} chars "
          f"({len(prepared.text) / len(text):.0%}), digest {len(prepared.digest):,} chars, "
          f"net {(len(prepared.text) + len(prepared.digest)) / len(text):+.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
