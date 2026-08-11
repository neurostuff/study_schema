# Staging, propagation, and reconstructing the corrected record

The task set is a *function of the record*, and review mutates the record. So a
correction can invalidate tasks that were generated from the old version — the
cascade. This works out the ordering that contains it, what propagates
mechanically and what cannot, and how the corrected extraction JSON gets rebuilt.

Companions: `task-organization.md` (why four projects),
`analysis-review-design.md` and `relationship-review-design.md` (the UIs).

## Yes, stages — and the order is derived, not chosen

The ordering falls out of one question: **what can a correction invalidate?**

| a correction to… | changes | invalidates |
|---|---|---|
| the instance set (a Group that isn't real, two Analyses that are one) | nodes | every task that addresses an object by `local_id` — i.e. all of them |
| an association (`Analysis.acquisitions`, `Analysis.model_estimation`) | edges | anything that *reads* the edge: a contrast's candidate term set comes from `Analysis.model_estimation` → `ModelEstimation.terms`, and then over `inputs_from` into the stages beneath |
| a field value (`Group.age_mean`) | leaves | nothing |
| an analysis subtree (`Effect.cells`, `ModelTerm.levels`) | leaves of the deepest object | nothing |

Topologically sorted, that is three rounds, not four:

```
stage 0   entity inventory        changes nodes    ── invalidates everything below
            │
            ├── stage 1  relationships   changes edges  ── invalidates stage 2
            │        │
            │        └── stage 2  analysis structure    reads nodes AND edges
            │
            └── stage 1  values           changes leaves ── invalidates nothing
```

Values and relationships are **independent** and run concurrently: a corrected
`age_mean` moves no link, and a corrected acquisition link moves no field. That is
worth knowing because it is where most of the volume is — 35–50 value tasks against
6–7 relationship tasks per paper — and it means the expensive family never waits.

The same ordering has three uses, which is the strongest sign the decomposition is
right: it is the **import round order**, the **invalidation order**, and the
**replay order** the decoder rebuilds the record in.

## Stage 0 was missing, and it is where your example lives

"The LLM annotated a group that didn't exist" had no home. The value family can
judge a Group's fields; the relationship family can judge which Analysis links to
it; neither can say *the Group should not exist*. The only inventory task was
analysis-specific.

So the inventory is now generic over classes — `$entities` in `structure.xml`. One
task per class per paper: Groups, Tasks, Conditions, Acquisitions, Assessments,
ModelEstimations, Analyses, Tables. That is **~8–10 tasks per paper** against ~300
for the value family, which is what makes staging affordable: the round that can
invalidate everything else is also the cheapest one to run first.

Each row carries its descriptor and **how many things reference it**, so "is this a
real cohort?" and "what breaks if I drop it?" are both answerable in the task.

## Two kinds of correction, and only one of them propagates

This is the distinction that makes the cascade tractable.

**`rename` and `merge` propagate mechanically.** Both are a rewrite map
`{old_id: new_id}`, and `build_record.apply_aliases` already applies exactly that —
walking the record and rewriting only slots that `schema_utils.classify_slot`
calls `reference`, so an alias can never corrupt an extracted value that happens to
share a string with an id (`build_record.py:249-294`, and
`test_aliases_only_rewrite_reference_slots` holds it). This is the common case:
duplicates and mislabels. It needs no downstream re-review at all, because the
edges are unchanged — only what they are called.

**`drop` and `split` cannot propagate.** There is no target to rewrite to. Every
reference to the dropped instance is *also* wrong and needs a human: if a Group was
never real, the `AnalysisGroup.group` entries pointing at it are wrong too, and no
rewrite can guess what they should have been. `split` is worse — a reference now has
two candidates and only the paper says which.

So the propagation rule is two lines:

```
rename | merge  →  apply_aliases({old: new});  no task is invalidated
drop   | split  →  every task whose payload names the instance is invalidated
add             →  no existing reference points at it; it can only create new tasks
```

The disposition vocabulary in the inventory config is exactly these five, and the
hints say which behaviour each has, so a reviewer knows that choosing `drop` costs
downstream work and choosing `merge` does not.

## Propagation needs content-addressed tasks

"Those changes may need to be propagated throughout the other tasks" is, mechanically,
the question *which already-generated tasks are still asking the same question?*
Answering it needs two identifiers per task, and today there is only one:

- **`review_key`** — the *address*: `paper|Class|local_id|slot`. Already built
  (`to_labelstudio.py:341`). Says **where** the judgement belongs.
- **`content_hash`** — a digest of the *answer-bearing payload*. Says **what** was
  asked. New, and now in `DATA_CONTRACT`.

Regeneration then has four rules and needs no bookkeeping beyond the two keys:

| address | content_hash | action |
|---|---|---|
| same | same | keep the annotation; do not re-ask |
| same | changed | re-ask; mark the old answer superseded |
| gone | — | archive the answer as orphaned |
| new | — | a new task |

**The hash must cover the answer-bearing payload only.** Not the rendered strings —
otherwise correcting `Group.name` in stage 1 changes the descriptor shown in every
contrast task and re-asks a dozen structural questions whose substance did not
move. Concretely, for a contrast task the hash covers the term ids, levels,
directions and statistic; the paraphrase prose and the descriptors are display and
are excluded. Getting this boundary wrong is the difference between staging that
converges and staging that thrashes.

## Descriptors: never a bare local_id

`grp_1` is unreviewable — "does this analysis use the right group?" cannot be
answered from it. Every place a reference surfaces now renders
`local_id -- name . fact . fact`:

```
grp_1 -- Parkinson's patients . n=20 . age 64.5 . human
acq_0 -- run 0 . fMRI . TR 2.0s
```

Two rules, both in `config_gen.descriptor`:

- **Derived at export, never stored.** A stored descriptor is a second copy of the
  entity that drifts from the first.
- **Built from the target class's priority-0 scalars** in
  `storage-parameter-priorities.yaml`, which is already this project's answer to
  "what matters about this object". So descriptors track the priority file instead
  of being a hand-kept list that goes stale.

It appears in the inventory table and rows, the relationship legend `<Table>`, the
matrix column `alias`es, and the contrast paraphrase.

## The escape hatch, for discoveries out of order

A reviewer doing a stage-1 value task may notice the entity should not exist. Stages
are advisory, so each downstream family needs a way to kick back rather than invent
a repair that stage 0 will invalidate. `structure.xml` has `upstream_wrong`.
Kicking back parks the task, reopens stage 0 for that paper, and the next
regeneration re-asks whatever the resulting `drop`/`split` invalidated.

`relationship.xml` used to carry its own pair, `target_missing` / `target_spurious`.
They are gone: both were claims about which objects exist, which is the stage-0
inventory's `instance_missing` / `instance_spurious` — the same statement made where
the correction can actually be applied. A second route to one correction means two
places to reconcile and a rule for which wins.

## Reconstructing the corrected record

Replay in stage order over the immutable original:

```
R0   extractor output, never mutated
 │   stage 0: apply_aliases(renames+merges), then drops, then adds
 ▼
R1   the corrected instance set
 │   stage 1: set association slots from the matrices
 ▼
R2   the corrected graph
 │   stage 1: set values and evidence spans from the value tasks
 ▼
R3   corrected leaves
 │   stage 2: rebuild Effect / ModelTerm / FactorLevel subtrees
 ▼
R4   the corrected extraction version
```

Then **validate R4 and refuse to write it if it fails**. `validate_record.py`
already checks span offsets against the source text, the recorded hash, and dangling
cross-references; the structural validator checks the invariants LinkML cannot
state, chiefly `Effect.kind` from which terms carry direction. A review that
produces an inconsistent record is a bug in the decoder or in the ops, and it should
surface as a failure rather than being written and discovered downstream.

Four requirements on the decoder, independent of format:

- **Identity round-trips by `local_id`, never by index.** Repeater results come back
  as `fv_3`, and index 3 means nothing after a reviewer drops instance 1. Task data
  carries the `local_id` for every row it renders; the decoder maps back through it.
  This is the likeliest thing to get wrong and the hardest to notice, because an
  off-by-one reassignment produces a *valid* record with the wrong content.
- **New objects get review-minted ids** in a namespace the extractor cannot collide
  with — `rev_` prefixed — so a diff of two versions says which a human added.
- **Ops are order-dependent within stage 0**: renames and merges first (so later ops
  address post-rewrite ids), then drops, then adds.
- **Provenance travels with the version.** `ExtractionMetadata` records which model
  ran and what text it read; a reviewed version needs the reviewer, project and
  annotation ids beside it, or the record cannot say how it came to differ.

The extraction header already licenses this: "create a new extraction version or a
separate correction record." An accepted object re-emits unchanged, so a diff of R0
and R4 *is* the set of reviewer corrections.

## Better ways worth considering

Things the staged design does not do, with an honest read on each.

**A paper-level triage pass before stage 0.** Render the whole extraction as prose
and ask one question: is this close enough to review at all? A paper whose instance
set is badly wrong costs ~300 tasks to review and produces a record nobody trusts.
One task to reject it outright is the cheapest thing in the pipeline. **Recommended** —
this is the highest-value addition on the list, and it is one more gate in
`structure.xml`.

**Send bad extractions back rather than reviewing around them.** An invented Group
is an *extraction* bug. If stage 0's error rate on a paper is high, re-prompting is
cheaper than reviewing, and staging is what makes that decision possible: stage 0 is
a cheap early measurement of exactly the failure that predicts downstream cost.
Report the stage-0 disposition mix per paper and per extractor version, and set a
threshold above which a paper goes back for re-extraction. **Recommended**, and it
turns the review layer into a feedback loop on the extractor rather than only a
correction pipeline.

**Flag rather than delete.** Deletion is what causes the cascade. If stage 0 only
flags and a curator batches the deletions, the invalidation happens once per batch
instead of once per reviewer action. Worth doing if regeneration turns out to be
expensive; unnecessary if it is cheap. **Defer** until the regeneration cost is
measured.

**One mega-task per paper.** Eliminates the cascade entirely — one reviewer, one
atomic edit, no cross-task staleness possible. Rejected: 200–400 fields in one form
is unreviewable, there is no parallelism, and no per-field agreement. Named here
because it is the only design that makes the problem *disappear* rather than
managing it, and it is worth knowing why that trade is bad.

## Open questions

- Where exactly does the `content_hash` boundary fall for a value task? The field
  values clearly count and the section breadcrumb clearly does not; the evidence
  span offsets are the awkward middle — a span that moved by two characters is
  arguably the same question.
- Should stage 0 be two reviewers? It is 8–10 tasks per paper and it gates
  everything, which argues yes; it is also the most mechanical judgement, which
  argues no. Its disagreement rate on the first pilot paper should decide it.
- Does a `split` in stage 0 need the reviewer to specify the split *there*, or is
  it enough to flag it and let a curator do the surgery? Specifying it in the task
  means a form for objects that do not exist yet, which is the same limitation the
  analysis design hit for adding terms.
- Regeneration is per paper. Is there a cross-paper case — a schema change, an
  extractor upgrade — where every paper's tasks need rebuilding at once, and does
  `content_hash` make that safe to run over papers already half-reviewed?
