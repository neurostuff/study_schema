# Reviewing relationships between objects

A brainstorm. The review layer needs three task families, not two, and the third — the links
between extracted objects — is almost entirely derivable from the schema. This works out
which slots belong to it, what the widget should be, and why analyses are the one family that
cannot be generated the same way.

Companion to `analysis-review-design.md`. Label Studio claims are checked against the
`label-studio/` checkout (OSS `1.24.0.dev0`).

## Three families, and the rule that assigns a slot

| Family | Unit of a task | What the reviewer judges |
|---|---|---|
| **Value** | one attribute of one object | Is this the right value, and do these spans support it? |
| **Relationship** | one association slot × one paper | Which objects does each source object link to? |
| **Structure** | one `ModelEstimation`, one `Analysis`, or one paper's analysis inventory | Is this model / contrast / inventory the right shape? |

The rule is mechanical, and it is the same rule `gen_extraction_schema.py` already applies
when it decides whether to wrap a slot:

- The slot's range is **not** a class → **value family**. It is wrapped in an
  `ExtractedValue` subtype, so it has a value and evidence, and the existing config serves it.
- The slot's range is a class and it is **inlined** → the slot is a *composition*: the parent
  owns the child, and the child's own slots are reviewed on their own. The slot itself is only
  an inventory question ("are these the right children?"), which belongs to the structure
  family when the children are analysis internals and to the value family's `not_reported`
  logic otherwise.
- The slot's range is a class and it is **not inlined** → an *association*, resolved by
  `local_id`. **Relationship family**, unless its candidate set is itself derived from another
  association — which is what makes the analysis internals special. See the last section.

## The census

Walking the generated modules: **19 associations, 35 compositions.** The associations split
8 / 11 between the generic family and the analysis structure.

**Generic (8).** Provisioning and assignment: which objects supplied data to which, and which
single owner a thing belongs to.

| slot | target | cardinality |
|---|---|---|
| `Analysis.acquisitions` | `Acquisition` | many |
| `Analysis.tasks` | `Task` | many |
| `Analysis.tables` | `Table` | many |
| `Analysis.assessments` | `Assessment` | many |
| `Analysis.model_estimation` | `ModelEstimation` | one, **required** |
| `ModelEstimation.inputs_from` | `ModelEstimation` | many, self-referential |
| `Analysis.preprocessing` | `Preprocessing` | one |
| `Task.acquisitions` | `Acquisition` | many |
| `Group.arm` | `Arm` | one |

**Analysis-structural (11).** `Cell.term`, `Mediation.mediator`,
`ModelTerm.interaction_with`, `ModelTerm.assessment`, `AnalysisGroup.group`,
`FactorLevel.{conditions,groups,timepoints,arms}`, plus `DecodingClass.condition` and
`PartialLeastSquaresDetails.behavioral_variables` in the details payloads.

Note what moved: `Task.conditions` was a reference task in the last export and is now a
*composition* (`Condition` is owned by its `Task`), so it is an inventory question rather
than a link. That is the kind of drift the mechanical rule handles for free and a hand-kept
list does not.

## The evidence gap this exposes

The projection deliberately does not wrap reference slots
(`gen_extraction_schema.py:307-317`: "a bare reference resolves through the target's
identifier, which is its local_id"). So **all 19 association slots carry no
`extraction_status`, no `value_source`, and no evidence**, and the generator counts them
separately as `references` rather than as wrapped values.

That is a problem for "review every part of the schema with its supporting sentences". For
these 19 slots there is nowhere in the record to put the sentence. "The PPI analysis used
the resting-state scan" *is* a sentence in the paper; the schema simply has no slot for it.
Two ways out:

**Wrap reference slots.** An `ExtractedReference` (value = the target's `local_id`, plus the
standard `evidence`) added to `extraction-deviations.yaml`. This is exactly what the
`deviations` section exists for, and the required `why` writes itself: the link is otherwise
unreviewable and unauditable, and the extractor cannot be asked to justify a link it has no
field to justify. Costs: 19 slots get more verbose, the mapper has to unwrap them, and the
extractor spends tokens producing spans for links it currently asserts for free — which is a
real cost given `extraction-cost-estimate.md`.

**Or accept the gap.** The reviewer's link evidence lives only in the review layer's output
and is lost the next time the record is regenerated. Cheaper, and defensible if link
evidence is only wanted for adjudication rather than for the record.

I would wrap them, but wrap them *lazily*: `ExtractedReference` on the slots where the link
is a claim about the paper (`Analysis.acquisitions`, `Task.acquisitions`, `Group.arm`), and
leave the ones that are bookkeeping about the record's own structure
(`Analysis.tables`, `Cell.term`) unwrapped. That distinction is a judgement call and it is
the thing worth deciding before building the exporter, because it sets whether the
relationship task has a span layer at all.

## The generic UX: one matrix per relationship per paper

The current reference family asks one task per source object: "here are the candidate
`local_id`s, is this analysis's acquisition set right?" — eleven times, with the same
candidate list, and no way to see whether the assignment is coherent overall.

Invert it. **Rows are the source objects, columns are the candidate targets, one task per
relationship slot per paper.**

```
Analysis.acquisitions                          acq_mri_task   acq_mri_rest
  beh_total_pd_vs_hc                               [ ]            [ ]        ⚠ no acquisition, no task
  beh_canonical_pd_vs_hc                           [ ]            [ ]        ⚠
  ppi_right_striatum_noncanon_pd_vs_hc             [x]            [ ]
  corr_ppi_sma_noncanon_accuracy                   [x]            [ ]
  …
                                                                  ⚠ column unused by any analysis
```

Widget by cardinality, all from tags already verified for the analysis design:

| schema shape | widget |
|---|---|
| `multivalued`, few candidates | `<Choices choice="multiple" value="$columns" layout="inline">`, one per row via `<Repeater on="$rows">` |
| `multivalued`, many candidates | same, `layout="vertical"`, Repeater in `mode="pagination"` — one row at a time |
| single, `required` | `<Choices choice="single" required="true">` |
| single, optional | `<Choices choice="single">` plus an explicit `none` column, so "no link" is an assertion rather than an empty row |

`Choices` takes its options from task data (`Choices.jsx:93`), supports
`choice="multiple"`, and has a `layout` of `select | inline | vertical` (`Choices.jsx:95`),
which is the whole widget vocabulary this needs. The `select` layout is the escape hatch for a
row with 15 candidate tables.

The switch between grid and paginated rows is a generation-time decision from the measured
candidate count, not a reviewer setting — task-data-driven visibility does not exist
(`visibleWhen` reads only choices and regions).

### What the matrix gives you that per-object tasks cannot

- **Orphan targets are visible as empty columns.** An `Acquisition` no analysis and no task
  uses is almost always an extraction error, and nothing in the current family can surface it
  because no task ever shows the whole column set.
- **Coherence across rows.** Two analyses that obviously share a scan but disagree about it
  is a one-glance catch in a grid and invisible across two tasks.
- **Cross-slot rules become visible.** The schema says an analysis with no paradigm is linked
  through acquisitions, so `tasks` empty implies `acquisitions` non-empty. On a grid that
  is a flagged row; across 22 separate tasks it is nobody's question.
- **Cost.** On `2abntY3hQSyq` (11 analyses, 6 models, 2 acquisitions, 1 task, 11 assessments,
  1 preprocessing, 2 groups) the last export produced **58 reference tasks**. The same
  content is **6 non-empty matrices** — and two of the eight slots (`Analysis.tables`,
  `Group.arm`) have no candidates at all on this paper, so the generator should emit nothing
  rather than 11 tasks asserting emptiness.

### The candidates have to be legible

A column headed `acq_mri_rest` is unreviewable. Put a read-only legend above the grid:
`<Table name="cands" value="$candidates"/>` renders an array of objects as a table, one row
per object (`tags/object/Table.jsx`, `isJsonArrayOfObjects`). The generator picks the
identifying fields per target class — `local_id`, `name`, and the class's priority-0 scalars
from `storage-parameter-priorities.yaml`, which is already the "what matters about this
object" list. For `Acquisition` that is modality and the sequence basics; for `Assessment`
its name and what it measures.

### Where the spans go, if the slots get wrapped

One dynamic label per **row**, not per cell. A 11×11 assessment matrix is 121 cells and would
need 121 labels; it needs 11. That granularity is also the right one on principle: a wrapped
reference slot carries one evidence block for the slot, and the slot *is* the row. So the
label set is `analysis: ppi_right_striatum_noncanon_pd_vs_hc` and friends, the reviewer
highlights the sentence licensing that row's links, and per-region `<Choices perRegion="true"
value="$columns">` records which of the row's targets that particular span licenses when it
matters.

If the slots stay unwrapped, the relationship family has no span layer and looks like the
current reference project — just arranged as a grid instead of a queue.

## Validation that comes free

All of this is readable off the schema, so the generator can pre-compute it and ship the
anomaly list in the task rather than making the reviewer find it:

- `required` + single → exactly one selection per row; a row with none is flagged before the
  reviewer opens the task.
- single → at most one; the widget enforces it.
- `unique_keys` on the association class — `AnalysisGroup.group_per_analysis`,
  `Cell.(term, level)` — become duplicate-row checks.
- Referential integrity: every asserted `local_id` resolves to an extracted object.
- Orphan targets: any candidate no row selects.
- Empty candidate set: emit no task, and record that the slot was skipped for want of
  candidates rather than reviewed and found empty. (The existing README's warning about
  silent caps applies: a skipped slot must be reported, not merely absent.)

## Why analyses cannot be generated this way

Two reasons, both structural rather than cosmetic.

**The candidate set is derived from another relationship.** For every generic slot, the
columns are "all instances of the target class in this paper". For `Cell.term` they are not:
the schema requires the term to be one of the terms of the ModelEstimation the containing
Analysis references, or of a stage that model reaches through `inputs_from`. So the column set
for one analysis's cell grid is a *three-hop* derivation — follow `Analysis.model_estimation`,
then `ModelEstimation.terms`, then the same again over `inputs_from` — and it differs per row's
parent. On this paper that is the
difference between 16 candidate terms and the 12 that `ttest2_group` actually declares.
`Mediation.mediator` has the same constraint, and `FactorLevel.groups` is coherent only
against the groups of the analyses that use the term. A generic matrix over "all
`ModelTerm`s in the paper" would offer columns that are invalid by construction.

**Validity is conditional on the target's contents.** `Cell.level` is "required exactly when
the referenced term declares levels, and unset when it does not"
(`neuroimaging-study-storage/analysis.yaml:426`) — so whether a cell needs a level depends on
whether the *other* object has `levels`, and the row inventory has to expand a categorical
term into one row per level while leaving a continuous term as a single row. No cardinality
rule in the schema expresses that; the structural validator does.

Add to those the derivations the schema explicitly hands to that validator — chiefly
`Effect.kind` from which terms carry direction (`neuroimaging-study-storage.yaml:19`) — and
the analysis family needs its own generator, which is what `analysis-review-design.md`
proposes. The `FactorLevel` case is the neat illustration of the boundary: four parallel
association slots (`conditions`, `groups`, `timepoints`, `arms`) that are really one question
— which entity carries this level — with four typed target sets, and a schema note that a
factor normally ranges over exactly one of the four. Generically that is four independent
matrices; correctly it is one nested pick (`Choices allowNested`, `Choices.jsx:94`) of type
then instance, with a rule that the four are near-exclusive.

## Corrections land as a new extraction version

The extraction header already licenses this: "Do not manually correct an extraction record:
create a new extraction version or a separate correction record." So the review layer's job
is to emit the next version, not to patch the current one, and there is no conflict with the
schema's immutability language.

What that requires of the exporter and importer, independent of the format:

- **`local_id`s must be carried into task data and back out.** Row and column identity has to
  survive the round trip by `local_id`, never by grid position, because a reviewer who deletes
  an object shifts every index after it.
- **A new object needs a `local_id` minted by the review layer**, in a namespace that cannot
  collide with the extractor's — `rev_` prefixed, say — so a later diff can tell which objects
  a human added.
- **The version needs provenance.** `ExtractionMetadata` is a `required_addition` and is where
  "which model ran, what text it read" lives. A reviewed version needs the reviewer, the
  review project, and the annotation ids alongside it, or the record cannot say how it came to
  differ from the extractor's output.

## Open questions

- Which reference slots deserve `ExtractedReference` and which stay bare? This is the
  decision that gates everything else, because it decides whether the relationship family
  has a span layer.
- Does the relationship family need two reviewers? A grid is one task carrying up to 121
  judgments, so overlap is cheap per judgment but the disagreement unit is awkward — two
  reviewers disagreeing on 3 of 121 cells is not "a disagreement on the task". Agreement
  should be computed per cell, which means the adjudication view is a cell-level diff of two
  grids, not two task results side by side.
- `Analysis.tables` is `deterministic` (the table parser fills it, not the model). Should
  deterministic associations be reviewed at all, or only spot-checked? Reviewing a parser's
  output with the same effort as a model's output is probably the wrong allocation.
- One matrix per slot per paper, or one matrix per (slot, target-class) pair across papers?
  Per paper matches the text-caching argument that the whole pipeline rests on; across papers
  would let a reviewer see one relationship's failure mode repeatedly and get faster at it.
