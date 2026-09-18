# Evaluating stages 2–3 for accuracy and expressivity

**Date:** 2026-08-04
**Prerequisite:** [RESULTS.md](RESULTS.md) settled *shape* (`linked`, no evidence, low effort).
This plan addresses what that could not: **are the values right, and can the schema say what the
papers say?**

---

## 1. Three questions that need separate instruments

They get conflated and they fail differently.

| question | what it catches | instrument |
|---|---|---|
| **Precision** — is an emitted value correct? | wrong values | adjudicate model output against the source |
| **Recall** — was anything missed? | omissions | **blind** annotation, independent of model output |
| **Expressivity** — could the schema hold it at all? | the paper says something no field fits | targeted probe + mining fields the schema already has |

The trap worth naming up front: **adjudicating model output can only measure precision.** If no
configuration ever emitted a `Group.medication_status`, reviewing what was emitted will never
reveal that the paper reported one. That is why a small blind-annotated set is not optional.

---

## 2. What is already measurable at zero annotation cost

Run on the 240 records in hand.

**Only 1 of 128 schema fields was never filled** (`Analysis.mediation_path`, 0/856 opportunities).
The schema is almost fully exercised by real papers — a good sign for scope, and it means
expressivity problems will be about *distortion*, not dead fields.

**14.1% of filled values carry `value_source: generated`** — the model composed rather than
quoted. They concentrate in exactly the fields that are classifications, not quotations:

| field | generated |
|---|---:|
| `Term.type` | 1,134 |
| `Condition.condition_kind` | 997 |
| `Term.role` | 916 |
| `Assessment.assessment_type` | 678 |
| `Term.variation_level` | 567 |
| `Group.species` | 453 |

This is a schema-design finding, not a model failure. The schema instructs "verbatim" throughout,
but these fields cannot be verbatim — nothing in a paper says "this term's variation_level is
within_subject." **`generated` is the schema correctly flagging where it asks for inference.** Two
consequences: evidence spans cannot validate those fields, and they are where error will
concentrate. **They are the priority for annotation.**

**33% of analyses (286 of ~864) populated `model_representation_notes`** — the field that asks
what the schema could not represent. Only 5 used `not_structurable`, so the model rarely gives up
entirely; it records a gap and carries on. The recurring content is one theme:

> nuisance and no-interest regressors, and hierarchical model levels

- *"included image regressors for smoking, neutral, and animal images and motion"*
- *"Three levels of hierarchical analysis were completed within FEAT"*
- *"instruction and match conditions were included as predictors of no interest"*
- *"Covariates such as age, gender and educational level were regressed out"*

**Recommended before any annotation:** read those 286 notes (they are one line each) and cluster
them. That is the cheapest expressivity study available and it may already justify a schema
change — the schema has `Term.role: nuisance` but apparently no comfortable home for "the model
also contained these regressors of no interest," nor for multi-level model hierarchies.

---

## 3. Annotation design

### Adjudicate, don't transcribe

Writing a value from scratch takes minutes; judging a proposed one against a highlighted span
takes seconds. Same principle that made the alpha.11 review workable. Verdict vocabulary:

| verdict | meaning |
|---|---|
| `correct` | value is right |
| `wrong` | value is wrong — record the right one |
| `unsupported` | value may be true but the paper does not say it (hallucination) |
| `should_be_absent` | paper is silent; this should have been `not_reported` |
| `missed` | *(blind tier only)* paper reports this and nothing was emitted |
| `inexpressible` | paper says something the field cannot hold — expressivity, not accuracy |

`unsupported` and `should_be_absent` are separated deliberately: one is a fabrication, the other
over-eager filling. They imply different fixes.

### Workload, measured

Per paper, `linked_noev` emits **112 filled field-instances**; 61 are priority-0, 24 are
`generated`, 16 are both.

| scope | 10 papers | 25 papers | 40 papers |
|---|---:|---:|---:|
| every priority-0 field | 608 verdicts · **2.5 h** | 1,521 · 6.3 h | 2,433 · 10.1 h |
| priority-0 ∩ generated | 160 verdicts · **0.7 h** | 399 · 1.7 h | 639 · 2.7 h |

at ~15 s per verdict with the source span shown inline.

### Two tiers

**Tier A — broad precision, adjudicated. 25 papers, ~6 h.**
All priority-0 fields. Gives per-field precision with usable confidence intervals: at n=25 papers
a field appearing once per paper lands ±19 points, one appearing 5×/paper lands ±9. Good enough to
rank fields and find the broken ones, not to certify any single field.

**Tier B — deep recall and expressivity, blind. 8 papers, ~6–8 h.**
Annotate from the paper alone, without seeing any model output, then diff against extraction. This
is the only way to measure **omission** and the only unbiased read on expressivity. Eight papers
is small but it is measuring a *rate per paper* (how many facts were missed, how many things did
not fit), not a per-field rate, so it goes further than it looks.

Pick the 8 to span the hard cases: one multi-group clinical, one multi-session/longitudinal, one
connectivity, one decoding or RSA, one with a hierarchical model, one 10+ analysis paper, and two
ordinary ones as a baseline.

### Sampling: spend the budget where it is informative

1. **Disagreement-weighted.** Four effort cells ran the same 60 papers. Where all four agree on a
   field, sample 1 in 5. Where they disagree, review all. In the alpha.11 work disagreement
   roughly halved the clean rate — a real signal, though agreement did not certify correctness
   (44% of agreed records still had an error), so **do not skip agreed fields entirely.**
2. **`generated` first.** Evidence spans cannot check them; they are pure inference.
3. **Stratify across both samples.** pmc20 papers average 5.8 analyses, neurometabench 2.5 —
   don't let the easy short papers dominate.

### Guard against the two ways this goes wrong

- **Don't let me be the second annotator** on any field where a model generated the candidate.
  For inter-annotator agreement, re-annotate 3 of the 25 Tier-A papers yourself after a gap, or
  have a colleague do a subset. Report the agreement figure alongside the accuracy figure.
- **Annotate before seeing scores.** If the accuracy numbers are known first, verdicts drift
  toward them.

---

## 4. Expressivity instrument

Three parts, cheapest first:

1. **Mine the 286 `model_representation_notes`** (free, above). Cluster into named gaps.
2. **Per-paper probe** during Tier B: after annotating, answer one question — *"what does this
   paper report that you could not put anywhere?"* Free text. This is where genuinely novel gaps
   surface, and it is what produced the useful findings in the alpha.7→alpha.11 iterations.
3. **Round-trip through the mapper.** For each Tier-A paper, push the extraction through
   `extraction-to-storage.map.yaml` and check the `normalize_enum` steps. A verbatim value that no
   `value_mappings` entry covers is an expressivity failure at the boundary rather than in the
   extraction — and it is checkable mechanically, no annotation needed. **This may be the highest
   value-per-hour item in the whole plan** and I have not tested it at all yet.

---

## 5. What I would build

| tool | purpose |
|---|---|
| `bench/build_field_review.py` | adjudication app: one row per field-instance, the value, its evidence span highlighted in the source, four verdict keys, disagreement-sorted. Same pattern as `review/model-review.html`, which worked |
| `bench/blind_annotate.py` | Tier B: renders the paper with an empty schema form, exports annotations in extraction-record shape so the diff is mechanical |
| `bench/score_fields.py` | precision/recall per field, per class, per priority tier; CIs; agreement stats |
| `bench/check_mapper.py` | round-trip extraction → storage, report unmapped enum values |
| `bench/mine_notes.py` | cluster `model_representation_notes` and `generated` fields into a gap report |

Suggested order: `mine_notes` and `check_mapper` first — both are zero-annotation and might change
what is worth annotating. Then the Tier-A app, then Tier B.

---

## 6. What this will and will not tell you

**Will:** per-field precision for priority-0 fields, ranked so you know which fields to fix or
drop; an omission rate; a named list of expressivity gaps with frequencies; whether verbatim
extraction survives the mapper's normalisation.

**Will not:** certify any individual field to a tight bound (n is too small), or establish
accuracy for priority 2–3 fields, or tell you anything about stage 4 — evidence remains untested
as a separate pass.

**The honest risk:** Tier A measures agreement between a human and a model on fields where the
"verbatim" instruction is not actually followable — 14% of values are inferences. Expect
irreducible disagreement there that is neither party being wrong, and expect to spend some of the
review resolving what the schema *should* say rather than what the paper does. That is a useful
outcome, but it is schema work discovered through annotation, so budget for it rather than being
surprised by it.
