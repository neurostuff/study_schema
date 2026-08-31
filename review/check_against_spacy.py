"""Score `preprocess.py`'s two linguistic components against scispaCy.

`preprocess.py` is standard library only, which is a constraint worth defending rather
than assuming: this repo's dependency list is three packages, and a preprocessing step
that needs a 100 MB biomedical model is a different proposition from one that needs a
regex. The two components where a real NLP pipeline could plausibly do better are the
sentence splitter and the abbreviation finder, so those are the ones measured here.

scispaCy's `AbbreviationDetector` implements the same Schwartz & Hearst (2003) algorithm,
so the abbreviation comparison is against a reference implementation and not against a
different idea. The sentence comparison is against `en_core_sci_sm`'s dependency-parse
boundaries, which is a genuinely different mechanism.

Not part of the pipeline and not imported by it. Run it when the patterns change.

    /path/to/nlpenv/bin/python review/check_against_spacy.py --texts data/texts \
        --papers xevP8UDRAVh9 6oTrCJA43Jcd
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preprocess  # noqa: E402


def normalise(sentence: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", sentence.lower()).strip()


def load_pipeline():
    import spacy
    from scispacy.abbreviation import AbbreviationDetector  # noqa: F401  (registers it)

    nlp = spacy.load("en_core_sci_sm")
    nlp.add_pipe("abbreviation_detector")
    return nlp


def prose(text: str) -> str:
    """The same input both sides see: prose lines, no headings and no table rows."""

    return "\n".join(line.strip() for line in text.split("\n")
                     if line.strip() and not line.strip().startswith(("#", "|")))


def compare(paper: str, text: str, nlp) -> dict:
    document = nlp(prose(text))

    reference = {normalise(s.text) for s in document.sents if len(s.text.strip()) > 2}
    ours = {normalise(s) for s in preprocess.sentences(text) if len(s) > 2}
    shared = reference & ours

    theirs_abbrev = {}
    for abbreviation in document._.abbreviations:
        theirs_abbrev.setdefault(str(abbreviation), str(abbreviation._.long_form).lower())
    ours_abbrev = {short: long_form.lower()
                   for short, long_form, _ in preprocess.abbreviations(text)}

    both = set(theirs_abbrev) & set(ours_abbrev)
    agree = {s for s in both if normalise(theirs_abbrev[s]) == normalise(ours_abbrev[s])}
    return {
        "paper": paper,
        "sentences": (len(ours), len(reference), len(shared)),
        "abbrev": (len(ours_abbrev), len(theirs_abbrev), len(both), len(agree)),
        "only_ours": sorted(set(ours_abbrev) - set(theirs_abbrev)),
        "only_theirs": sorted(set(theirs_abbrev) - set(ours_abbrev)),
        "disagree": {s: (ours_abbrev[s], theirs_abbrev[s]) for s in both - agree},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--texts", type=Path, required=True)
    parser.add_argument("--papers", nargs="+", required=True)
    parser.add_argument("--variant", default="processed/local/text.tables.txt")
    args = parser.parse_args()

    nlp = load_pipeline()
    results = []
    for paper in args.papers:
        path = args.texts / paper / args.variant
        if not path.is_file():
            print(f"{paper}: no {args.variant}", file=sys.stderr)
            continue
        results.append(compare(paper, path.read_text(encoding="utf-8"), nlp))

    print("\n-- sentence boundaries: exact-string agreement with en_core_sci_sm ------")
    print("  paper           ours  spacy  shared   agreement")
    for result in results:
        ours, theirs, shared = result["sentences"]
        union = ours + theirs - shared
        print(f"  {result['paper']:<14} {ours:>5} {theirs:>6} {shared:>7}   "
              f"{shared / union:>8.1%}" if union else "")

    print("\n-- abbreviations: vs scispaCy's AbbreviationDetector -------------------")
    print("  paper           ours  spacy  both  same expansion")
    for result in results:
        ours, theirs, both, agree = result["abbrev"]
        print(f"  {result['paper']:<14} {ours:>5} {theirs:>6} {both:>5} {agree:>15}")
    for result in results:
        if result["only_theirs"]:
            print(f"  {result['paper']}: scispaCy only -> {', '.join(result['only_theirs'])}")
        if result["only_ours"]:
            print(f"  {result['paper']}: ours only     -> {', '.join(result['only_ours'])}")
        for short, (mine, theirs) in result["disagree"].items():
            print(f"  {result['paper']}: {short} -> ours {mine!r} / spacy {theirs!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
