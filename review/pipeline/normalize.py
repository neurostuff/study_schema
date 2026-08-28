"""Map a record's own wording onto shared vocabularies, without changing the record.

The storage schema deliberately binds no subject-matter vocabulary: values are the
source's own words, and `neuroimaging-study-storage.yaml` says mapping them onto ONVOC or
the Cognitive Atlas "is a later stage that reads the free text and its evidence
sentences". This is that stage.

It never edits a record. A mapping is an assertion *about* a record -- "this paper's
`escitalopram` is ONVOC's Escitalopram" -- and writing it into the field would destroy the
thing that makes the mapping checkable, which is the paper's own wording next to it. So
mappings are emitted as their own rows, carrying the method that produced them and the
text they were produced from.

Two vocabularies, chosen because they cover different halves of the record:

  ONVOC             drugs, disorders, brain regions, tests, population groups -- the
                    nouns a clinical trial's arms, groups and assessments are made of
  Cognitive Atlas   tasks, cognitive concepts and disorders -- what a paradigm *is*,
                    which ONVOC does not attempt

Matching is layered from certain to merely plausible, and every mapping records which
layer produced it. A token-overlap match and an exact label match are not the same claim,
and collapsing them into one confidence number would hide that.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

VOCAB_DIR = Path(__file__).resolve().parents[3] / "data" / "vocab"

#: Words that carry no identity. Dropped only when comparing token sets, never when
#: deciding whether a phrase exists -- "usual care" is made entirely of weak words.
_WEAK = frozenset({
    "the", "a", "an", "of", "in", "and", "or", "for", "with", "group", "groups",
    "patients", "subjects", "participants", "condition", "conditions", "task", "tasks",
    "test", "tests", "scale", "inventory", "questionnaire", "disorder", "arm",
})


#: True function words. Distinct from `_WEAK`, which also drops domain nouns for the
#: purpose of comparing token sets; those nouns still carry a letter in an acronym.
_FUNCTION = frozenset({"the", "a", "an", "of", "in", "and", "or", "for", "with", "on"})


def fold(text: str) -> str:
    """Case, accents, punctuation and spacing removed; what two labels are compared on."""
    stripped = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", stripped.lower())).strip()


def tokens(text: str) -> frozenset[str]:
    return frozenset(fold(text).split())


def content(text: str) -> frozenset[str]:
    """Content tokens, or every token when the phrase is nothing but weak ones."""
    every = tokens(text)
    return (every - _WEAK) or every


@dataclass(frozen=True)
class Concept:
    """One vocabulary entry, with every string it can be recognised by."""

    id: str
    label: str
    vocabulary: str
    synonyms: tuple[str, ...] = ()
    branch: str = ""
    definition: str = ""

    def surfaces(self) -> tuple[str, ...]:
        return (self.label,) + self.synonyms


#: Suffixes stripped to relate `depression` to `Depressive Disorder`. ONVOC carries the
#: clinical noun phrase and papers write the everyday noun, and no amount of containment
#: bridges the two: neither string contains the other. Ordered longest first so
#: `-ational` is tried before `-al`.
_SUFFIXES = ("ational", "iveness", "ically", "ation", "ities", "ive", "ity", "ies",
             "ing", "ed", "al", "s")


def stem(word: str) -> str:
    """A crude suffix strip. Crude on purpose: it only has to make two surface forms of
    the same clinical noun collide, and a real stemmer would be a dependency for that."""
    for suffix in _SUFFIXES:
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def stems(text: str) -> frozenset[str]:
    return frozenset(stem(word) for word in content(text))


def acronym(text: str) -> str:
    """The initials of a multi-word label, or "" when it is not that kind of label.

    Two words is too few -- `Drug Use` would claim `DU`. Single-letter tokens are dropped
    before counting, because folding `Alzheimer's Disease` leaves a stray `s` that turns
    a two-word name into the three-letter `ASD`, which is a different disorder entirely.
    """
    # Function words only, never the domain nouns `_WEAK` drops. `disorder`, `scale` and
    # `test` are precisely the words a clinical acronym is built from -- dropping them
    # turns `Autism Spectrum Disorder` into two words and no acronym at all.
    words = [w for w in fold(text).split() if w not in _FUNCTION and len(w) > 1]
    if len(words) < 3 or len(words) > 6:
        return ""
    return "".join(word[0] for word in words)


#: `Autism Diagnostic Observation Schedule (ADOS)` is two surface forms, and the one a
#: cell level or a table header uses is usually the one in the brackets.
_PARENTHETICAL = re.compile(r"\s*[\(\[]([^)\]]{2,60})[\)\]]")

#: Qualifiers that modify a concept without changing which concept it is. Stripped only
#: when the remainder still has a content word -- `left` alone is not a region.
_QUALIFIERS = re.compile(
    r"\b(left|right|bilateral|ipsilateral|contralateral|anterior|posterior|dorsal|"
    r"ventral|superior|inferior|medial|lateral|rostral|caudal|parcel|roi|seed|mask|"
    r"region|cluster|network|active|sham|total|mean|score|sub|scale)\b", re.I)


def variants(text: str, abbreviations: Any = None) -> list[str]:
    """The surface forms worth looking a phrase up under, most specific first.

    A field's value is rarely a bare vocabulary label. It carries an acronym in
    brackets, a laterality, a version number, a dose. Each of those is stripped into its
    own candidate rather than all at once, so the most specific form still wins.

    When an abbreviation store is given, every short form in the phrase is also offered
    expanded. This is the layer that lets ONVOC do its job: the vocabulary spells
    everything out and papers do not, and `dlPFC` reaches `dorsolateral prefrontal
    cortex` only because some paper defined it in brackets.
    """

    text = str(text or "").strip()
    if not text:
        return []
    seen, out = set(), []

    def offer(candidate: str) -> None:
        candidate = candidate.strip(" .,;:-")
        key = fold(candidate)
        if key and key not in seen:
            seen.add(key)
            out.append(candidate)

    offer(text)
    for inner in _PARENTHETICAL.findall(text):
        offer(inner)                                   # the acronym
    offer(_PARENTHETICAL.sub("", text))                # the phrase without it
    stripped = _QUALIFIERS.sub(" ", _PARENTHETICAL.sub("", text))
    if content(stripped):
        offer(stripped)

    if abbreviations is not None:
        from .abbreviations import expansions_in  # noqa: PLC0415

        for candidate in list(out):
            replaced = candidate
            for short, expansion in expansions_in(candidate, abbreviations):
                replaced = re.sub(rf"(?<![A-Za-z0-9]){re.escape(short)}(?![A-Za-z0-9])",
                                  expansion, replaced)
            if replaced != candidate:
                offer(replaced)
                offer(_QUALIFIERS.sub(" ", replaced))
    return out


#: How a mapping was made, most trustworthy first. Kept as an ordered tuple because the
#: layer *is* the confidence -- an exact label match and a token-overlap match are
#: different claims and a single score would flatten them.
METHODS = ("exact", "synonym", "variant", "acronym", "contains", "stem", "overlap")


@dataclass(frozen=True)
class Candidate:
    """A value no vocabulary could place, proposed as a term the vocabulary lacks.

    The useful output of a normalization layer is not only what it mapped. ONVOC's Tests
    branch has 53 entries and the corpus asks it about ADOS, MADRS, HAMD and BDI; that is
    a gap in the vocabulary, and the evidence for it is exactly this list. Counted across
    papers because a term used once is a paper's idiosyncrasy and a term used in ten is a
    term.
    """

    text: str
    path: str
    branch_group: str
    papers: tuple[str, ...]
    expansions: tuple[str, ...] = ()

    @property
    def support(self) -> int:
        return len(self.papers)

    def render(self) -> str:
        expanded = f"  (= {self.expansions[0]})" if self.expansions else ""
        return (f"{self.support:3d} paper(s)  {self.branch_group:11s} "
                f"{self.text[:58]!r}{expanded}")


@dataclass(frozen=True)
class Mapping:
    """One assertion about one field of one record."""

    study_id: str
    path: str
    text: str
    concept: Concept | None
    method: str = ""
    alternatives: tuple[str, ...] = ()
    #: What the abbreviation layer thought this phrase's short forms stood for. Carried
    #: even when nothing matched, because an unmapped value with a known expansion is a
    #: far better proposal for the vocabulary than the acronym alone.
    expansions: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return self.concept is not None

    def render(self) -> str:
        if not self.concept:
            return f"{self.path}: {self.text!r} -> (no match)"
        extra = f"  ~{len(self.alternatives)} other" if self.alternatives else ""
        return (f"{self.path}: {self.text!r} -> {self.concept.label!r} "
                f"[{self.concept.vocabulary}/{self.method}]{extra}")


class Vocabulary:
    """A term list, indexed every way the matcher looks things up."""

    def __init__(self, name: str, concepts: list[Concept]):
        self.name = name
        self.concepts = concepts
        self.by_surface: dict[str, list[Concept]] = {}
        for concept in concepts:
            for surface in concept.surfaces():
                key = fold(surface)
                if key:
                    self.by_surface.setdefault(key, []).append(concept)
        # Longest first: `Selective Serotonin Reuptake Inhibitor` must be tried before
        # the shorter labels nested inside it.
        self._ordered = sorted(self.by_surface, key=len, reverse=True)
        self._scopes: dict[tuple[str, ...], "Vocabulary"] = {}
        # A second index on stems, so `depression` reaches `Depressive Disorder`. Only
        # unambiguous stem sets are kept: if two concepts stem alike, the stem cannot
        # decide between them and the match would be a coin flip.
        by_stem: dict[frozenset[str], list[Concept]] = {}
        for concept in concepts:
            for surface in concept.surfaces():
                key = stems(surface)
                if key:
                    by_stem.setdefault(key, []).append(concept)
        self.by_stem = {k: v[0] for k, v in by_stem.items()
                        if len({c.id for c in v}) == 1}
        # Acronyms built from the labels themselves. ONVOC spells `Autism Spectrum
        # Disorder` out and every paper writes `ASD`; the vocabulary carries no synonym
        # for it and nothing else can bridge three letters to three words. Ambiguous
        # acronyms are dropped rather than guessed -- two concepts sharing initials is
        # exactly when an acronym stops identifying anything.
        by_acronym: dict[str, list[Concept]] = {}
        for concept in concepts:
            for surface in concept.surfaces():
                letters = acronym(surface)
                if letters:
                    by_acronym.setdefault(letters, []).append(concept)
        self.by_acronym = {k: v[0] for k, v in by_acronym.items()
                           if len({c.id for c in v}) == 1 and k not in self.by_surface}

    def __len__(self) -> int:
        return len(self.concepts)

    def scoped(self, groups: tuple[str, ...]) -> "Vocabulary":
        """The sub-vocabulary a field is allowed to draw from. Cached per group set."""
        key = tuple(sorted(groups))
        if key not in self._scopes:
            allowed = {branch for group in groups for branch in BRANCHES.get(group, ())}
            self._scopes[key] = Vocabulary(
                f"{self.name}/{'+'.join(key)}",
                [c for c in self.concepts if c.branch in allowed])
        return self._scopes[key]

    def match(self, text: str, abbreviations: Any = None
              ) -> tuple[Concept | None, str, list[Concept]]:
        """The best concept for this phrase, the method used, and the runners-up.

        Each surface form of the phrase is tried in full before the next is considered,
        so an exact hit on the acronym beats a containment hit on the whole phrase.
        """
        for index, candidate in enumerate(variants(text, abbreviations)):
            concept, method, others = self._match_one(candidate)
            if concept:
                return concept, (method if index == 0 else "variant"), others
        return None, "", []

    def _match_one(self, text: str) -> tuple[Concept | None, str, list[Concept]]:
        key = fold(text)
        if not key:
            return None, "", []

        hits = self.by_surface.get(key)
        if hits:
            method = "exact" if fold(hits[0].label) == key else "synonym"
            return hits[0], method, hits[1:]

        # A label appearing whole inside the phrase: "paroxetine 20 mg daily" contains
        # "paroxetine". Guarded on a word boundary so "sham" does not match "shampoo".
        contained = [surface for surface in self._ordered
                     if len(surface) >= 4
                     and re.search(rf"(?<![a-z0-9]){re.escape(surface)}(?![a-z0-9])", key)]
        if contained:
            best = self.by_surface[contained[0]][0]
            return best, "contains", [self.by_surface[s][0] for s in contained[1:4]]

        # An acronym the vocabulary spells out. Only for short all-caps-ish input: a
        # lowercase word that happens to have the right letters is not an acronym.
        if 2 <= len(key.replace(" ", "")) <= 6 and " " not in key:
            expanded = self.by_acronym.get(key.replace(" ", ""))
            if expanded is not None:
                return expanded, "acronym", []

        # Morphology: `depression` and `Depressive Disorder` share no substring but do
        # share a stem set once the weak words are gone.
        stemmed = self.by_stem.get(stems(text))
        if stemmed is not None:
            return stemmed, "stem", []

        # Last resort: the phrase's content words are exactly a concept's, in some order.
        wanted = content(text)
        if len(wanted) >= 2:
            same = [c for c in self.concepts if content(c.label) == wanted]
            if len(same) == 1:
                return same[0], "overlap", []
        return None, "", []


def crosswalk_synonyms(directory: Path | None = None) -> dict[str, set[str]]:
    """Extra surface forms for an ONVOC term, from its own crosswalks.

    ONVOC publishes maps to MeSH, MONDO, DOID and SNOMED. Each row pairs an ONVOC term
    with the other vocabulary's term for the same thing, and the other vocabulary's
    wording is a surface form a paper might well use. This is the cheapest widening
    available: no model, no network, and the pairings are the ontology author's own.
    """

    directory = (directory or VOCAB_DIR) / "onvoc-mappings"
    extra: dict[str, set[str]] = {}
    for name in ("mesh", "mondo", "doid", "snomed"):
        path = directory / f"{name}.tsv"
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            continue
        header = lines[0].split("\t")
        try:
            onvoc_at = header.index("vocabulary_id")
            term_at = next(i for i, column in enumerate(header)
                           if column.endswith("_term") and column != "vocabulary_term")
        except (ValueError, StopIteration):
            continue
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) > max(onvoc_at, term_at) and parts[term_at].strip():
                extra.setdefault(parts[onvoc_at], set()).add(parts[term_at].strip())
    return extra


def load_onvoc(path: Path | None = None) -> Vocabulary:
    """ONVOC as a Vocabulary, with each concept's parent kept as its branch.

    The branch is what makes a mapping auditable at a glance: `Escitalopram` under
    `Antidepressants` is a different kind of claim from `Escitalopram` under `Tests`,
    and one of them would be a bug worth seeing.
    """
    raw = json.loads((path or VOCAB_DIR / "onvoc.json").read_text(encoding="utf-8"))
    crosswalked = crosswalk_synonyms()
    concepts = []
    for entry in raw:
        label = entry.get("prefLabel")
        if not label:
            continue
        parents = [p.get("prefLabel") or "" for p in entry.get("parents") or []]
        short = entry.get("@id", "").rsplit("/", 1)[-1].replace("_", ":")
        synonyms = set(entry.get("synonym") or ()) | crosswalked.get(short, set())
        concepts.append(Concept(
            id=entry.get("@id", ""), label=label, vocabulary="ONVOC",
            synonyms=tuple(sorted(synonyms)),
            branch=next((p for p in parents if p), ""),
            definition=" ".join(entry.get("definition") or ())[:300]))
    return Vocabulary("ONVOC", concepts)


def load_cognitive_atlas(kinds: tuple[str, ...] = ("task", "concept", "disorder"),
                         directory: Path | None = None) -> Vocabulary:
    directory = directory or VOCAB_DIR
    concepts = []
    for kind in kinds:
        path = directory / f"cognitiveatlas-{kind}.json"
        if not path.is_file():
            continue
        for entry in json.loads(path.read_text(encoding="utf-8")):
            label = entry.get("name")
            if not label:
                continue
            alias = entry.get("alias") or ""
            concepts.append(Concept(
                id=entry.get("id", ""), label=label, vocabulary="CognitiveAtlas",
                synonyms=tuple(a.strip() for a in alias.split(",") if a.strip()),
                branch=kind,
                definition=(entry.get("definition_text") or "")[:300]))
    return Vocabulary("CognitiveAtlas", concepts)


#: ONVOC branches grouped by what they are about. The vocabulary is one namespace and a
#: field is not: `Wechsler Abbreviated Scale of Intelligence` contains the word
#: `Intelligence`, and matching an assessment against the psychological-concept branch
#: returns exactly that, confidently and wrongly. ONVOC's own README makes the point --
#: "Study Focus: Schizophrenia" and "Exclusion Criteria: Schizophrenia" are different
#: claims about the same term -- so which branch a field may draw from is part of the
#: mapping, not a filter applied to it afterwards.
BRANCHES: dict[str, tuple[str, ...]] = {
    "drugs": ("Drugs and Medications", "Antidepressants", "Anti Psychotics",
              "Anxiolytics", "Mood Stabilizers", "Psychostimulants", "Opioids",
              "Psychedelics", "Cannabinoids", "Anesthetics", "Anti Inflammatory",
              "Parkinsons Disease Medication", "Migraine Medication", "Contraception",
              "Dementia Medication", "ADHD Medications Nonstimulants"),
    "disorders": ("Psychiatric Disorders", "Neurological Disorders", "Medical Disorders",
                  "Psychiatric Symptoms", "Neurological Symptoms", "Medical Symptoms",
                  "Health"),
    "population": ("Population Groups", "Population Characteristics", "Age", "Species",
                   "Family Relations"),
    "tests": ("Tests",),
    "regions": ("Cortical Regions", "Subcortical Regions", "Brain Networks"),
    "concepts": ("Psychological Concepts", "Decision Making", "Executive Function",
                 "Attention", "Learning", "Memory", "Perception", "Social Cognition"),
}

#: (field, vocabulary, branch groups). A field may draw from more than one group -- a
#: group's name is a diagnosis or a population, and either is a defensible mapping.
ROUTES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("design.arms.agent", "ONVOC", ("drugs",)),
    ("design.arms.name", "ONVOC", ("drugs",)),
    ("groups.diagnosis", "ONVOC", ("disorders",)),
    ("groups.name", "ONVOC", ("disorders", "population")),
    ("assessments.name", "ONVOC", ("tests",)),
    ("regions.name", "ONVOC", ("regions",)),
    ("tasks.name", "CognitiveAtlas", ()),
    ("tasks.conditions.name", "CognitiveAtlas", ()),
    ("measures.source_label", "CognitiveAtlas", ()),
)


def _value(node: Any) -> Any:
    return node.get("value") if isinstance(node, dict) and "value" in node else node


def iter_targets(record: dict) -> Iterator[tuple[str, str]]:
    """(routed path, text) for every field a vocabulary is asked about."""
    for route, _vocab, _branches in ROUTES:
        head, _, leaf = route.rpartition(".")
        for owner in _walk_to(record, head.split(".") if head else []):
            text = _value(owner.get(leaf)) if isinstance(owner, dict) else None
            if isinstance(text, list):
                for item in text:
                    if isinstance(item, str) and item.strip():
                        yield route, item
            elif isinstance(text, str) and text.strip():
                yield route, text


def _walk_to(node: Any, steps: list[str]) -> Iterator[dict]:
    if not steps:
        if isinstance(node, dict):
            yield node
        return
    head, rest = steps[0], steps[1:]
    if isinstance(node, dict):
        child = node.get(head)
        if isinstance(child, list):
            for item in child:
                yield from _walk_to(item, rest)
        elif child is not None:
            yield from _walk_to(child, rest)


def candidates(mappings: Iterable["Mapping"], minimum: int = 1) -> list[Candidate]:
    """The unmatched values, grouped and counted, as proposals for the vocabulary.

    Grouped on the phrase with its parenthetical removed, so `Beck Depression Inventory`
    and `beck depression inventory (BDI)` count as one proposal rather than two. Grouping
    on the whole folded string looks equivalent and is not -- the bracketed acronym is
    part of it, and the two forms never meet.
    """

    routes = {path: groups for path, _vocab, groups in ROUTES}
    grouped: dict[tuple[str, str], dict] = {}
    for mapping in mappings:
        if mapping.matched:
            continue
        key = (fold(_PARENTHETICAL.sub("", mapping.text)) or fold(mapping.text),
               mapping.path)
        slot = grouped.setdefault(key, {"text": mapping.text, "path": mapping.path,
                                        "papers": set(), "expansions": set()})
        slot["papers"].add(mapping.study_id)
        slot["expansions"].update(mapping.expansions)
        # Keep the longest surface form seen; it is the most informative proposal.
        if len(mapping.text) > len(slot["text"]):
            slot["text"] = mapping.text

    out = [Candidate(text=slot["text"], path=slot["path"],
                     branch_group="+".join(routes.get(slot["path"], ())) or "-",
                     papers=tuple(sorted(slot["papers"])),
                     expansions=tuple(sorted(slot["expansions"])))
           for slot in grouped.values()]
    return sorted([c for c in out if c.support >= minimum],
                  key=lambda c: (-c.support, c.path, c.text.lower()))


def _all_text(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        for value in node.values():
            _all_text(value, out)
    elif isinstance(node, list):
        for value in node:
            _all_text(value, out)
    elif isinstance(node, str) and len(node) > 2:
        out.append(node)


def corroborated(concept: Concept, record_text: str) -> bool:
    """Does the record itself spell out what this acronym was expanded to?

    An acronym unambiguous inside a vocabulary can still be the wrong referent outside
    it: ONVOC contains exactly one label whose initials are MDD, and it is Mood
    Dysregulation Disorder, while every paper writing MDD means Major Depressive
    Disorder. The vocabulary cannot tell those apart and the record can -- a paper that
    means the expansion almost always writes it somewhere.
    """

    wanted = content(concept.label)
    return bool(wanted) and wanted <= content(record_text)


def normalize(record: dict, vocabularies: dict[str, Vocabulary],
              abbreviations: Any = None) -> list[Mapping]:
    """Every mapping this record supports, matched and unmatched alike.

    Unmatched rows are kept on purpose. The useful question about a normalization layer
    is what it *cannot* place, and a list of only its successes cannot answer it.
    """
    study_id = _value(record.get("local_id")) or ""
    routes = {path: (vocab, groups) for path, vocab, groups in ROUTES}
    strings: list[str] = []
    _all_text(record, strings)
    record_text = " ".join(strings)

    found: list[Mapping] = []
    for path, text in iter_targets(record):
        vocabulary_name, groups = routes[path]
        vocabulary = vocabularies.get(vocabulary_name)
        if vocabulary is None:
            continue
        scoped = vocabulary.scoped(groups) if groups else vocabulary
        concept, method, others = scoped.match(text, abbreviations)
        expanded: tuple[str, ...] = ()
        if abbreviations is not None:
            from .abbreviations import expansions_in  # noqa: PLC0415

            expanded = tuple(e for _short, e in expansions_in(text, abbreviations))
        # An expansion the record never spells out is a guess, and a wrong mapping is
        # worse than a missing one -- it is the kind that gets queried across a corpus
        # and believed.
        if concept and method == "acronym" and not corroborated(concept, record_text):
            concept, method, others = None, "", []
        found.append(Mapping(str(study_id), path, text, concept, method,
                             tuple(c.label for c in others), expanded))
    return found
