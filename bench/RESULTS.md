# Stage-2 extraction evaluation — results

**Date:** 2026-08-04
**Schema under test:** `neuroimaging-study-extraction.yaml` (38 classes, evidence-first)
**Samples:** 20 PubMed Central OA papers · 40 neurometabench papers with NiMADS gold
**Total spend:** $8.86 · **1,080+ calls**

## Recommendation

**`linked` mode, evidence excluded, low reasoning effort on both passes.**

| stage | model | effort | $/paper |
|---|---|---|---:|
| 1 · table → analyses | luna | low | ~$0.002 |
| 2 · study schema (entities) | luna | **low** | \} $0.0084 combined |
| 3 · analyses (annotate stage 1's list) | luna | **low** | \} |
| 4 · evidence spans | luna | low | ~$0.005 (untested) |
| **total** | | | **~$0.015/paper · $15 per 1,000** |

---

## 1. Architecture: what shape to extract in

Six shapes, luna-low, 60 papers each.

| | schema order | analyses_first | linked | **linked_noev** | two_pass_noev |
|---|---:|---:|---:|---:|---:|
| unparseable | 1 | 4 | 6 | **0** | 1 |
| analysis recall | 5% | 8% | 94% | **98%** | 10% |
| precision | 11% | 9% | 98% | **96%** | 6% |
| analyses/paper (gold 3.4) | 2.7 | 3.6 | 2.8 | **3.6** | 8.6 |
| unknown fields invented | 45 | 30 | 21 | **0** | 0 |
| dangling local_ids | 24 | 97 | 64 | 44 | 23 |
| priority-0 fill | 44% | 41% | 45% | **48%** | 45% |
| $/paper | $0.0077 | $0.0082 | $0.0110 | **$0.0084** | $0.0097 |

### Single-pass extraction silently drops the analyses

In schema order, **11 of 59 papers (19%) returned no `analyses` at all** — with
`finish_reason: stop`, so the model chose to stop rather than being truncated. `analyses` is
**12th of 14** fields in `Study`, behind 31-field `Group` records. The thing a meta-analysis
exists to consume is last in line.

Reordering the prompt so the analyses lead cuts that to 5%, but **introduces 97 dangling
`local_id` references** — emitting analyses first means pointing at entities not yet created. It
trades one failure for another.

### Stage 1 is load-bearing, not a convenience

`two_pass_noev` splits the passes but makes pass 2 *discover* the analyses from text. It emits
**8.6 analyses against a gold of 3.5** and matches 10% of them: unguided discovery invents and
over-splits rather than finding what was reported.

Handing it stage 1's parsed list instead takes recall to 98% and precision to 96%. **The
dependency on table parsing is hard** — version it, monitor it, and treat a stage-1 regression as
a stage-2 outage.

### Removing evidence from the extraction pass makes the extraction better

This was not the expected result — the case for a separate evidence pass was cost and model
tiering. Against `linked` with evidence inline:

| | linked | linked_noev |
|---|---:|---:|
| unparseable | 6 | **0** |
| recall | 94% | **98%** |
| unknown fields | 21 | **0** |
| priority-0 fill | 45% | **48%** |
| analyses/paper (gold 3.0 / 3.4) | 2.8 | **3.6** |
| $/paper | $0.0110 | **$0.0084** |

Better on every axis *and* cheaper. The mechanism looks like budget competition: with evidence
inline the model under-emits analyses and truncates 6 papers into unparseable JSON. Evidence is
**57% of output tokens** (measured: 10,893 → 4,682 with evidence stripped → 2,562 with wrappers
flattened; only **24% of emitted tokens are content**), and it was crowding out what it was meant
to support.

**Keep evidence as a separate pass regardless of what else you decide.**

---

## 2. Reasoning effort: 2×2 within the winning shape

240 calls, 60 papers per cell, zero failures.

| | p1 low / p2 low | p1 **high** / p2 low | p1 low / p2 **high** | p1 **high** / p2 **high** |
|---|---:|---:|---:|---:|
| analysis recall | 98% | 98% | 100% | 100% |
| precision | 96% | 97% | 98% | 98% |
| analyses/paper | 3.6 | 3.6 | 3.6 | 3.6 |
| **priority-0 fill** | **48%** | **48%** | **48%** | **48%** |
| dangling refs (papers) | 4/60 | **2/60** | 4/60 | 4/60 |
| reasoning tokens | 304 | 4,630 | 6,269 | 10,893 |
| **$/paper** | **$0.0084** | $0.0140 | $0.0122 | $0.0192 |

**Use low effort on both passes.** The priority-0 fill rate is **identical to the point — 48% in
all four cells**. Reasoning changes nothing about how much of the schema gets populated.

What effort does buy: +2 points of recall, and halved dangling references on the hot-schema cell
(4/60 → 2/60 papers). Both are worth less than the 1.7–2.3× cost, and the dangling-reference
problem is a prompt bug, not a reasoning shortfall — see below.

### High effort is not merely unnecessary, it is unstable with evidence inline

Before the evidence payload was removed, `luna-high` **failed 80% of calls** (110 of 137):
reasoning hit the `max_completion_tokens` cap exactly, `finish_reason: length`, `raw_chars: 0`.
Two-pass burned 64,000 tokens per paper for zero content. Removing evidence from the contract and
scoping the pass brought reasoning down to ~4,600 tokens and eliminated the failure.

This cost **$2.55 of the $8.86 total**. The runner now aborts a configuration after N consecutive
empty responses (`--abort-after`, default 4) and reports why.

---

## 3. Known defects and open items

**Dangling `local_id` references — 4 of 60 papers.** Concentrated entirely in
`model_estimation` (19) and `preprocessing` (19): papers where pass 1 emitted no
`preprocessings`/`model_estimations` block, so pass 2 invented ids to point at. **Fix in the
prompt** — instruct pass 2 to emit `null` when the entity digest offers nothing to reference.
Cheaper and more reliable than paying for reasoning.

**Constrained decoding is impossible for this schema.** OpenAI strict mode caps `$ref`-expanded
nesting at 5 levels; this schema reaches **13**, and `ExtractedValue → Evidence → EvidenceSet →
EvidenceSpan` is **6 on its own** — every leaf field already exceeds the cap. Output must be
`json_object` and validated after the fact, which is what `bench/score_study.py` does. If
constrained decoding is wanted later, the evidence wrapper is what has to go.

**Evidence spans are 95–97% verbatim, not 100%.** At 113–165 spans per paper, 3–5% invented means
**only ~25% of papers have every span verified** (14/55, 14/58). The wrapper is mostly honest but
a per-paper clean bill is the exception — another argument for a dedicated pass that can be
checked and retried per span.

**Prompt ordering leaves caching on the table.** `two_pass_noev` cached **13,663 input tokens per
paper (49%)** while `linked_noev` cached **0** — both send the paper twice. The difference is
prefix structure: the schema block comes first and differs per pass, so nothing shares a prefix.
**Put the paper text first, then the pass-specific schema**, and all four stages share one long
cacheable prefix. Caching on luna is opportunistic (3–49% depending on shape) rather than absent,
as an earlier isolated test had suggested.

**Untested.** Stage 4 as a real pass — the 95–97% figure is from evidence emitted *inline*, so
stage 4 is a design and a cost model, not a result. Stage 1 on luna — the 33/33 tables, 0
failures result was on `gpt-5-mini`. High effort on stage 3 is now tested and unnecessary.

**Repo state.** `README.md` documents ten files that do not exist — `extraction-readme.md`,
`gen_mvp_schema.py`, `check_field_provenance.py`, all four test files, the MVP tree. Since
`extraction-readme.md` is cited as the home of the rules the schema cannot state (skip gates,
extraction conventions, validator invariants), the extraction prompt here was built without them
and nothing they specify is being tested.

---

## 4. Measurement caveats

**Analysis recall in `linked` mode is partly true by construction** — stage 1's names are the
input. The valid comparisons are *between* linked arms (94% vs 98% differ only by the evidence
contract) and against the non-linked shapes. Do not quote 98% as absolute extraction accuracy.

**The neurometabench gold is a lower bound.** A meta-analysis keeps only the contrasts it needed,
so a paper reporting eight effects may appear with two. Recall is meaningful; precision is not,
and the scorer labels it.

**Name-based matching is unreliable against these golds** and is not the primary metric. Gold
`'BaselineBE > NBFood > NF'` (a mangled multi-level table header) against a model's
`'Baseline Food vs. NF, BE compared with NB'` scores near zero on token overlap while being the
same analysis. Count agreement and the linked-mode alignment carry the enumeration result.

---

## Reproducing

```bash
python3 bench/sources.py                        # sample inventory
python3 bench/schema_prompt.py --report         # prompt sizes per mode
python3 bench/bench_study.py --mock --limit 3   # plumbing, no API call

python3 bench/bench_study.py --key-file ~/.keys/portkey.key --out bench/runs2 \
  --samples pmc20,nmb --nmb-limit 40 --configs luna-low \
  --mode linked --no-evidence --max-out 48000 --abort-after 5 --workers 8

python3 bench/score_study.py --dir bench/runs2 --per-sample
```

Tools: [strict_schema.py](strict_schema.py) · [schema_prompt.py](schema_prompt.py) ·
[sources.py](sources.py) · [bench_study.py](bench_study.py) · [score_study.py](score_study.py)
