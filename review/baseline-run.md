# The recommended extraction workflow, run on the three baseline papers

**Date:** 2026-08-06
**Schema:** `neuroimaging-study-extraction.yaml` v0.5.0 — 91 classes, 37 enums, 215
permissible values, generated from storage
**Papers:** `bench-baseline.pmids` — the three canonical clean cases, where "a failure here
is an extraction defect, not a schema gap"
**Spend:** 23 calls, ~370k input / ~67k output tokens

`bench/RESULTS.md` on the `pipeline_eval` branch settled the *shape* of extraction against
the old 38-class schema and left stage 4 unbuilt. This run implements that shape against the
current schema, executes it, and hand-corrects the output against the papers.

---

## Result

| | `5Rw4BhGBShSR` factorial | `4cRnHYtfSwuK` resting-state | `HU6mqxmtySg3` simple |
|---|---:|---:|---:|
| analyses found / stage 1 | **9 / 9** | **4 / 4** | **5 / 5** |
| names verbatim from stage 1 | 9 / 9 | **0 / 4** | 5 / 5 |
| fields emitted | 382 | 323 | 217 |
| filled (`extracted`) | 301 (79%) | 251 (78%) | 181 (83%) |
| evidence spans resolved | 81% | 76% | 77% |
| dangling local_ids | 0 | 0 | 0 |
| validator errors, first build | 5 | 9 | 1 |
| **hand-corrections applied** | **21** | **19** | **4** |

All three records validate: structure, required slots, ranges, vocabularies, evidence
invariants, and `text == source[start_char:end_char]` on every span.

Per-paper detail and reasoning: [5Rw4BhGBShSR](examples/5Rw4BhGBShSR.corrections.md) ·
[4cRnHYtfSwuK](examples/4cRnHYtfSwuK.corrections.md) ·
[HU6mqxmtySg3](examples/HU6mqxmtySg3.corrections.md).

---

## What the workflow is

Five stages. Only stages 2–4 are model calls on the paper.

| stage | what | model | calls/paper |
|---|---|---|---|
| 1 · tables → analyses | `review/parse_tables.py`, upstream autonima | `gpt-5-mini` | 1 per coordinate table |
| — · tables → `Table` records | copied from the pubget manifest, **no model** | — | 0 |
| 2 · entities | `extract_record.py --mode entities --no-evidence` | `gpt-5.6-luna`, low | 1 |
| 3 · analyses | `--mode analyses --no-evidence`, given stage 1's list + a digest of stage 2 | `gpt-5.6-luna`, low | 1 |
| 4 · evidence | `add_evidence.py`, quotes only, ~50 fields per call | `gpt-5.6-luna` | 4–7 |
| 5 · build + validate | `build_record.py`, `validate_record.py` | — | 0 |

`review/run_extraction.py --pmids bench-baseline.pmids` chains 2–5.

Two departures from the published recommendation, both worth keeping:

- **`Table` records are not extracted by a model.** `table_number`, `caption` and `footer`
  are literal strings in the pubget manifest; retyping them through an LLM can only add
  error. Their `local_id`s are handed to stage 3 so analyses can link to them.
- **Stage 3 gets more than the bare analysis names.** The paper text carries table captions
  but not table rows, so the model cannot see the coordinates. The injected block groups the
  analyses under the table that reported them and notes foci count, coordinate space and
  statistic type per analysis — offered as hints to confirm, not values to copy.

## Stage 1: the upstream re-parse beats the corpus copy

Re-parsed with `neurostuff/autonima` at `2a82a24` (prompt version `2026-07-30.annotations-v3`)
and diffed against the `analyses.jsonl` already in ns-pond. Same analysis count on all three
papers (9 / 4 / 5), but better on every difference:

| | fresh | pond |
|---|---|---|
| `5Rw4BhGBShSR` points | **95** — matches the 95 stated in `extraction-candidates.md` | 80 |
| `5Rw4BhGBShSR` names | `Direct > (Averted and Downward) — 2 Channels auditory clarity` | `2 Channels` |
| `4cRnHYtfSwuK` names | `HC > MDD` | `HC⇥2>⇥2MDD` (footnote markers glued in) |
| `HU6mqxmtySg3` points | **12** | 10 |

The name difference is the rewritten prompt's "preserve their explicit combinations in the
analysis names" rule doing exactly what it says.

---

## What went right

**Analysis enumeration is exact.** 18 of 18, on the nose, on all three papers. This is the
failure the two-pass shape exists to fix — single-pass extraction dropped the analyses
entirely on 19% of papers in the earlier benchmark — and handing stage 1's list to a
dedicated pass closes it.

**The self-naming payload works.** `details_type` correct on all 18 analyses:
`MassUnivariateDetails` on the mass-univariate contrasts, `ConjunctionDetails` — with
components, null hypothesis and implementation filled — on the conjunction, and
`ConnectivityDetails` with the right seed region on each of the four seed-based contrasts.
This was `extraction-deviations.yaml` candidate 2, the change from eight sibling slots to one
self-naming payload, and on this evidence it extracts fine.

**Enum conformance was total.** 37 vocabularies, 215 permissible values, and across 918
fields in three records **not one off-vocabulary value** — the only vocabulary warning the
validator has ever emitted was for a value I wrote by hand. Given how much of the schema is
now controlled, this was the most uncertain thing going in.

**Directions are right where they are signed.** Every directional contrast across all three
papers carried the correct `Cell.direction`, including the three-level rule (a factor
compared at two of its levels gets no cell for the third) applied unprompted on the proverb
paper.

**Low reasoning effort held up.** 220–410 reasoning tokens per call. Nothing in the error
profile looks like a reasoning shortfall; the corrections are conventions and source
conflicts, which more thinking about the same prompt would not fix.

**A separate evidence pass is viable.** 76–81% of filled values got a span that resolves
exactly against the normalized text, at ~15k output tokens per paper. This is the first time
stage 4 has been run at all.

## What went wrong

**Three prompt defects, found by running and fixed in the prompt** — all caught on the first
real call, before any of it reached a record:

1. `terms` and `levels` emitted as `{"extraction_status": ..., "value": [...]}`. A nested
   record list is not a multivalued scalar. Now every schema line states which of the three
   shapes a slot takes.
2. Entity lists emitted under `study` instead of at the top level — one paper returned no
   entities at all through that route. Rule 2 now forbids the second shape, and `normalize()`
   hoists and reports rather than silently losing them.
3. Cross-references filled with `not_reported` wrappers or `null`. **15 of the 15 validator
   errors surviving the first build were this one defect.** A reference has no `not_reported`
   form; the rule now says so.

**Four bugs in the reused pipeline, none of which had ever run against this schema:**

| where | bug | effect |
|---|---|---|
| `extract_record.render_schema` | read `classes` off the root YAML, which has **zero own classes** since the schema went modular | the model would have seen only the evidence wrappers |
| `extract_record.ENTITY_LISTS` | still named `conditions` and `terms`, gone since `Condition` moved under `Task` and `Term` became `ModelTerm` | both dropped as "unexpected payload key" |
| `build_record.check_local_ids` | collected declared ids from the top-level lists only | **41 false dangling references** on one paper — every `Cell.term` and `FactorLevel.conditions`, because `ModelTerm` lives under `model_estimations[].terms` |
| `validate_record` | three gaps: no subclass resolution through `details_type`/`acquisition_type`; `Extracted<T>List.value` rejected for being a list; enum values never checked at all | 13 false errors on one paper, and no vocabulary checking anywhere |

`test_extraction_prompt.py` (24 tests) now asserts each of these cannot regress: the entity
lists match `build_record`'s, every slot named in the prompt exists, the two modes partition
the schema, every referenced enum is rendered, and every class pass 2 points at is one pass 1
emits.

**One instruction was disobeyed, on one paper.** `4cRnHYtfSwuK` named all four analyses after
the table caption rather than stage 1's verbatim label, producing two pairs of
indistinguishable names. It is the paper whose stage-1 labels are terse and repeat across
tables — the case the injected table grouping was added for. The grouping was used correctly
for `Analysis.tables`; only the name went astray.

---

## Findings that are about the schema, not the extraction

**Two scanners, one `Instrument` slot.** `4cRnHYtfSwuK` scanned 63 participants on a GE Signa
VHi and 11 on a Discovery MR 750. The extraction wrote "Signa VHi and Discovery MR 750" into
one `model` field — a scanner that does not exist. Corrected by splitting into two
`Acquisition` records, but **which participants used which scanner remains unrepresentable**:
the 11 are a subset of a group, not a group, and no slot links a participant subset to an
acquisition. The paper's own handling (scanner as a covariate of no interest) *is* captured.

**A tested null result has nowhere to go.** `5Rw4BhGBShSR` computed the Gaze × Clarity
interaction and reports no significant clusters. Because it yields no coordinates it appears
in no table, so stage 1 never surfaces it and no `Analysis` records it. The record cannot
distinguish an effect tested and null from an effect never tested. This is inherent to
scoping extraction to coordinate-bearing analyses, and worth deciding deliberately.

**Papers contradict themselves, and the schema has no place to say so.** Two instances here:
`HU6mqxmtySg3`'s Methods claims a T threshold of 4.5 while its figure caption and every table
value say 3.30; `5Rw4BhGBShSR`'s Table 1 labels the conjunction `Direct < Averted &
Downward` while Results describes — and the clusters confirm — the opposite. Both were
resolved correctly in the corrected records, but only because a human read both sources. An
adjudicator shown a value and its highlighted span would confirm each one as correct.

---

## What this does and does not establish

**Does:** the linked two-pass shape transfers intact to the 91-class schema; enumeration is
exact; enum conformance is total; low effort suffices; a separate evidence pass works. The
three corrected records are a reference set with every correction reasoned in writing.

**Does not:** n = 3, all chosen as clean cases. The correction counts (21 / 19 / 4) are not a
precision estimate — they are one reader's judgement on three papers, and I both extracted
and adjudicated. The `bench/EVAL-PLAN.md` warning stands: adjudicating model output measures
precision only, and nothing here measures what the extraction *missed*. The stress-test set
in `bench-stress-test.pmids` is where the shape gets tested against papers designed to break
it.

## Reproducing

```bash
python review/sync_texts.py   --pmids bench-baseline.pmids
python review/parse_tables.py --pmids bench-baseline.pmids --key-file .env   # stage 1, costs money
python review/run_extraction.py --pmids bench-baseline.pmids --key-file .env # stages 2-5
python -m pytest test_extraction_prompt.py                                   # prompt contract
```

Stage 1 is deliberately not chained into the driver: it is the load-bearing input, versioned
on disk with its prompt version recorded, and a re-run is an explicit act.
