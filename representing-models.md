# Representing an analysis

How to put a reported analysis into the storage schema, and how to tell which shape it is. This is
the practical companion to [storage-schema-design-notes.md](storage-schema-design-notes.md), which
holds the reasoning behind the shape rather than instructions for using it. The schema's field
definitions are in [neuroimaging-study-storage/](neuroimaging-study-storage/) and say what each
field *is*; this says how the classes fit together and what to do when a paper does not divide the
world the way the schema does. If the class names are not yet familiar,
[analysis-entities.md](analysis-entities.md) is the map: what each entity is called, what owns it,
and which of these words the schema has borrowed for something narrower than its usual sense. For
the whole record rather than its analyses — the cohorts, the paradigm, the acquisitions, and the
rules for filling any of it — [schema-tutorial.md](schema-tutorial.md) teaches it chapter by
chapter, and its chapters 8 and 9 are this document taught rather than stated.

---

## 1. The one decision everything else follows from

**Which facts belong to the model, and which to the contrast?**

A paper reports one model and several results from it. The schema splits them:

| | `ModelEstimation` | `Effect` (one per `Analysis`) |
|---|---|---|
| holds | the design matrix: its terms, their levels, what those levels range over, the family, estimator, stage, software, the element it was fitted over, and the pipelines and lower stages that produced its data | which of those levels this comparison used, and which way |
| how many | one per design matrix | one per reported result |

The test is whether the fact would change if the paper reported a different contrast off the same
model. The levels of a factor would not — a three-level load factor has three levels whichever pair
a contrast compares — so levels are on the model. Which pair was compared would, so that is on the
Effect.

Everything difficult about using this schema is an instance of that question. When you are unsure
where something goes, ask whether a second contrast from the same model would repeat it.

**One consequence worth stating early.** Analyses whose design matrices differ take their own
`ModelEstimation` records. Sharing one is not a convenience: a contrast's adjustment set is derived
as the model's term list minus what the contrast used, so two design matrices behind one term list
makes that derivation wrong for both.

The test for "differ" is mechanical — list the columns each analysis was fitted with, and the data
they were fitted to. A different dependent variable, a different covariate set, a different
participant subset, a different element (`spatial_unit`), or a different input (`inputs_from`) is a
different design matrix, and takes its own record even where family, estimator and software are
identical. Two regressions of one measure on one regressor, one over the patients and one over
patients and controls pooled, are two records. This is the defect that stays *valid*: nothing in the
record contradicts a shared term list, so a bare two-sample t-test ends up claiming it controlled
for covariates that were in no model it was fitted with.

**One qualification.** "One per design matrix" counts *estimation stages*: a first-level GLM
and the group model fitted on its output are two records, linked by `inputs_from` on the group one.
The link is what keeps them one model — a stage's terms are its own plus those of the stages it
consumed — so nothing above has to restate a column fitted below. §5.12 works this through.

---

## 2. The classes, and where each one's job ends

### `Analysis` — one reported result

One `Analysis` per distinct tested effect: per statistical map, or per effect tested without a map.
It links the sample, the paradigm, the acquisition, the model, the effect, the method payload, and
the source tables.

**Where it ends.** Not everything in a paper's results section is an Analysis. ROI definitions,
acquisition summaries, descriptive sample tables, masks, and maps reported with no inferential test
are not. `Effect.cells` is required and non-empty, so a map that compared nothing has nowhere to go
— which is the intended answer, since extraction follows the existence of a result.

A coordinate table of one of those is not thereby lost: it is a `Table` whose
`non_analysis_content` says what its rows are — region definitions, atlas parcels, connectivity
seeds, component peaks, coordinates cited from elsewhere. Filling that field is what separates a
table deliberately not encoded as an analysis from one the extraction missed, since otherwise both
are the same silence.

Separate Analyses are required when the direction, the cohorts compared, the method, the cell
pattern, the seed, the decoded variable, the component identity, or the spatial scope differ. An
omnibus effect and its directional post-hoc contrast are two Analyses.

**The search space is not the seed.** `regions` is what `spatial_scope: roi` restricted inference
to, and the schema states both halves as rules: an `roi` analysis must name regions, and a
`whole_brain` or `searchlight` one must name none. A whole-brain seed-based analysis therefore has a
seed and no `regions` entry — putting the seed there says inference was restricted to it, which is
the opposite of what happened. Seeds are `ConnectivityDetails.seed_regions`, and regions this
analysis *produced* are `defines_regions`.

**Four of an Analysis's slots point at records the Study declares once**, and each is shared by
every analysis it holds for: `model_estimation`, `measure`, `inference_settings`, and the entities.
The sharing test is the same shape in each case — two analyses share a `Measure` when `family`,
`type`, `specific_metric` and `unit` all match, and an `InferenceSettings` when the threshold, its
type, the correction method, the scope corrected over and the alpha do. A study measures one or two
quantities and tests them many ways, so eight analyses of one connectivity measure are one
`Measure`; and because inference attaches to the test rather than to the fit, one model thresholded
two ways is two `InferenceSettings` and one `ModelEstimation`.

**`definition` and `interpretations` are the two ends of the same result.** `definition` is the
Methods-side statement of what was tested, in the source's words, and is where an ordering or a set
of contrast magnitudes lives that the cells cannot carry. `interpretations` is the
Results-and-Discussion-side statement of what came of it. Neither is a paraphrase, and a prediction
belongs to neither: `Study.hypothesis` is what the paper said it expected.

### `ModelEstimation` — one design matrix, at one stage

The model's terms and how it was fit. Referenced by every Analysis whose contrast came out of it.

**Where it ends.** It says nothing about any one contrast. It also carries the **full** term list —
including terms no reported contrast tests. "Controlling for age and sex" is a statement about the
design matrix, so age and sex are terms here even though no contrast will name them. A term omitted
from every stage reads as one the model did not adjust for.

`inputs_from` names the stages this one was fitted on, and `stage` labels the stage in the source's
own words. Only the link orders them: two records both saying `group` say nothing about their
relation. An `Analysis` names the **top** stage — the one that produced the reported statistics —
and the stages below are reached from there, which is why a first-level model is referenced by no
Analysis and is not thereby orphaned.

Two of its slots are about the data rather than the design, and they are part of the specification
all the same. `spatial_unit` is the element the fit ran over — voxel, vertex, region mean, parcel —
where the Analysis's `spatial_scope` gives the extent; a voxelwise GLM and an ROI-mean ANOVA of one
design are two records, because no contrast of one is readable off the other's fit. `preprocessing`
names the pipelines that produced the images, more than one where the fit consumed more than one
kind, so a classifier fitted to unsmoothed data does not share a record with the mass-univariate
fit on smoothed data.

### `ModelTerm` — one column

A column of the design matrix, in the general sense that covers a GLM regressor and the predictor of
any other family. Categorical terms declare their levels; continuous terms carry the facts that make
a slope interpretable (`unit`, `assessment`, `source_definition`).

`variation_level` is the one field to get right and easy to skip: whether the term moves *within* a
participant or only *across* the sample. It applies to every term, not only continuous ones, and for
a continuous term it decides whether the effect is a parametric modulation or a cross-subject
regression.

**Where it ends.** Nothing about a contrast: a term does not know whether it was tested. Nothing
about the quantity being modelled — what the model *models* is `Analysis.measure`, never a term of
it. A table laid out with one row-block per measured parameter (four diffusion metrics, three
frequency bands) is one analysis per parameter over one `Measure` each, not one analysis over a
factor whose levels are the parameters. Entered as a term, the dependent variable lands in its own
analysis's derived adjustment set, and the record says the analysis controlled for the thing it
measured.

### `FactorLevel` — one level, and what realizes it

A level of a categorical term, plus the study entities carrying it: `conditions`, `groups`,
`timepoints`, `arms`, `regions`.

This is the join that makes a comparison legible. An `Effect` names levels; following those levels
here says whether the comparison was between cohorts, between conditions, between occasions, between
arms, or between places in the brain. **Nothing on the Effect says which kind of comparison it is** —
that is read off here. A level realized by a crossing, such as a drug given at follow-up, fills more
than one slot.

A factor whose levels are not study entities at all — hemispheres, tasks, frequency bands,
acquisition sessions — leaves all five slots empty and is carried by `level` alone. That factor is
complete, not deficient. A factor over **regions** is not one of those: a `Region` is an entity the
study declares, so a factor comparing places names them in `regions`, exactly as a cohort factor
names `groups` (§5.10).

`order` carries a sequence the cells cannot: a contrast signs only its extremes, so without `order`,
1-back / 2-back / 3-back arrive as an unordered set.

### `Effect` — one comparison

Three slots: `cells`, `mediation`, `statistic`. Three things a reader might expect and will not find,
because they are derived:

| looked-for | actually |
|---|---|
| what kind of effect it is | from which terms carry cells of both signs |
| what it was adjusted for | the model's terms, plus those of the stages it was fitted on, minus the terms the cells name |
| what it was tested against | a `negative` cell on the reference level, or the absence of a second cell, or `PerformanceMetric.reference_value` |

### `Cell` — one level on one side

`term`, `level`, `direction`, and an optional verbatim `label`.

The **term is the axis** of the comparison. Cells sharing a term compare levels within that factor;
cells on different terms compare across them. That is the whole of the encoding, and it is why a
condition contrast, a cohort comparison, a pre-post change, a crossover comparison and a
region-by-condition dissociation are all the same shape.

`direction` is a sign, never a weight. `positive` and `negative` are the two sides;
see §4 for the three unsigned values and what distinguishes them.

### `AnalysisGroup` — who was in it

Cohort membership and per-analysis `n`. **Not** who was compared: a cohort comparison is a pair of
directional cells on the term whose levels name the cohorts. Populate `n` whenever the paper gives
one.

### `AnalysisDetails` subclasses — the method's own fields

Which subclass is filled *is* the method. No `analysis_type` field to keep in step with it: `details`
is one slot holding one payload, and that payload's `details_type` names which of the eight it is.
`MassUnivariateDetails` carries no fields at all, so naming it is the whole payload — which is how a
mass-univariate analysis comes to be asserted rather than inferred from silence.

---

## 3. Working out which shape a result is

Read the result and answer these in order. Each step is decided by the cells, so there is nothing to
declare.

```
1. Does a cell sit on a product column (a term with interaction_with)?
     signed      -> interaction     (a moderation; see 3.5)
     undirected  -> omnibus         (an interaction F-test)
2. Does a cell sit on a continuous term?
     variation_level within_subject  -> parametric_modulation
     variation_level between/mixed   -> cross_subject_regression
3. Count the terms carrying BOTH a positive and a negative cell -- the crossed terms.
     2 or more -> interaction
     exactly 1 -> contrast
4. No crossed term but some cell signed  -> simple_effect
5. Every cell `undirected`             -> omnibus
6. Cells unsigned but a term COMPARED -- cells on two or more of its levels --
   -> the kind that term's cells would give if signed: 2+ compared terms is an
      interaction, one is a contrast. The direction is lost; the shape is not.
```

"Crossed" rather than "signed" is the load-bearing word in step 3. A term signed once has not been
compared against itself. That is why a cohort comparison of an activation map is a `contrast` and not
an interaction: the cohort term is crossed, the condition term merely signed.

**Steps 5 and 6 are the two ways to have no signs, and they are not the same result.** An F over a
factor never had a per-level direction, so the effect is an `omnibus`. A two-level comparison whose
direction the paper simply did not print did have one, and it is still a `contrast` — the comparison
happened whichever way it went. The cells carry the difference: `undirected` for the first, and for
the second a cell whose `direction` is `extraction_status: not_reported`. Before they were split,
both read as "cells present, none signed" and a contrast with an unreported direction was labelled
an omnibus test.

---

## 4. The four values of `direction`, and the three ways to be non-directional

`positive` and `negative` are the two sides of a comparison. Everything else is a way of taking part
without having a side, and they are different facts:

| the contrast did this | recorded as | in the adjustment set? |
|---|---|---|
| put the level on the plus side | `positive` | no |
| put it on the minus side | `negative` | no |
| gave the level no weight | **no cell at all** | the *term* is, if no level of it has a cell |
| tested it with an F or χ², which yields no sign | `undirected`, on every level | no |
| compared it directionally, and the paper does not print which way | a cell whose `direction` is `not_reported` | no |
| held one level constant | `held` on that level | no — it took part, at one level |

Two of these are the ones most often conflated.

**Weighted out is absence.** A three-level factor compared at two of its levels gives the third no
cell. There is no `zero` value: a zero weight *is* a missing cell.

**Held constant is an unsigned cell.** "Patients versus controls, within the task condition" puts
`task` on the plus side and the minus side at once, so its net sign on the condition axis is nothing.
`positive` there would claim a condition comparison the contrast never makes. The level has a cell,
so its term stays out of the adjustment set—the contrast did not average over it.

### Which unsigned value: two questions

All three unsigned cells say the record has no sign to give. They disagree about *why*, and two
questions settle it.

**First: was the level on both sides at once?** If the contrast was taken *within* that level, yes —
and that is `held`. No report, however complete, could sign it, because the sign is not missing but
undefined. This is the only shape a `Cell` has for `held`.

**Otherwise: does this analysis yield a per-level sign at all?**

- **No → `undirected`.** An F or χ² over a factor returns one statistic for the whole set. There is
  no direction in the result to report. The post-hoc contrast that would supply one is a *different
  Analysis* (§2), so a fuller report of *this* analysis still could not sign these cells. Cells that
  are all `undirected` are what make an effect an `omnibus`.
- **Yes, and the paper withheld it → `extraction_status: not_reported` on `direction`.**
  "Activation differed between patients and controls", a t or a z, and no direction. The sign exists
  in the data and is missing from the page, which is missingness like any other. The comparison
  still happened, so the derived kind is the one the same cells would give if signed; only the
  direction is lost.

A corollary worth stating, because it is checkable: a cell that names **no level** — on a slope or a
product column — can never sit on both sides of anything, so it can never be `held`. An undirected
test of such a column is `undirected`.

This vocabulary has no home outside a cell. `Cell.direction` is the only slot in the schema that
binds `Direction`, which is what makes "the cells are the only place direction lives" a property of
the schema rather than only of this guidance. A connectivity analysis whose parameter is negative
says so with a `negative` cell on the term the coupling was estimated for, and one whose coupling
rose in a condition says so by crossing that condition's term.

**Telling `held` from `undirected` in a finished record** needs no extra field either: an F-tested
factor has *all* its levels celled and unsigned; a held-constant factor has *one* unsigned level and
the rest absent. That is a consequence of the rule above rather than a second rule — an undirected
test spans the whole factor, and holding a level constant is a statement about that one level.

**Why a `not_reported` direction is not just a flavour of `undirected`.** They differ in what a
better source would fix. A `not_reported` direction is *completable*: a corrigendum, a supplementary
table or the authors could supply the sign. An `undirected` cell is not, because nothing was
withheld. A curation pass that
wants to know which gaps are worth chasing reads exactly this distinction, and §3 reads it too — see
steps 5 and 6.

---

## 5. Worked models

Only the `ModelEstimation` fragment — its
`stage` and `terms` — and the `Effect` are shown; a complete record also needs the Analysis's
sample, paradigm, acquisition, measure, statistic and details.

### 5.1 A simple contrast — emotion labeling > emotion matching

```yaml
stage: subject
terms:
  - id: term-condition
    name: task condition
    type: categorical
    variation_level: within_subject
    levels:
      - {level: emotion labeling, conditions: [cond-emotion-labeling]}
      - {level: emotion matching, conditions: [cond-emotion-matching]}
      - {level: gender labeling, conditions: [cond-gender-labeling]}
      - {level: gender matching, conditions: [cond-gender-matching]}
      - {level: shape matching, conditions: [cond-shape-matching]}
  - id: term-motion
    name: motion parameters
    type: continuous
    variation_level: within_subject

effect:
  cells:
    - {term: term-condition, level: emotion labeling, direction: positive}
    - {term: term-condition, level: emotion matching, direction: negative}
```
→ **`contrast`**, adjusted for `term-motion`. One crossed term, and three of its five levels
weighted out. That the comparison is between conditions rather than cohorts is read off
`FactorLevel.conditions`, not off the Effect.

It is the only record, though the map was fitted per subject and carried to a group test. That
group stage takes no record here because the paper does not describe it; §5.12 has the rule and
the case where a second stage does earn one.

Note the term name `task condition`. The paper names its five conditions and never names the axis
they vary on; supplying a name for a grouping the paper does make is allowed. The levels must be
the source's own, and the name must not assert a comparison or a construct the paper does not.

**In the paper.**

> mean accuracy (percentage correct) and mean reaction time (RT) were calculated for each individual condition (i.e., emotion labeling, emotion matching, gender labeling, gender matching, shape matching)
>
> In addition, emotion labeling and emotion matching were directly contrasted.

**Referent** `4CA3Ca2bzfPW` (pmid 25821147) — Gee et al. 2015, "Emotion Labeling > Emotion
Matching": two conditions of one five-condition emotion paradigm, contrasted directly.

### 5.2 The same map against the implicit baseline

```yaml
effect:
  cells:
    - {term: term-condition, level: emotion labeling, direction: positive}
```
→ **`simple_effect`**. One signed cell and no second one is not an incomplete record: a lone `+1`
weight tests that coefficient against zero, and zero is what the unmodelled implicit baseline is. The
sign is what distinguishes activation from deactivation, which is why this is not encoded as "no
cells".

If the paper contrasted against a *modelled* rest condition instead, `rest` takes a `negative` cell
and the result is 5.1 — a `contrast`.

**In the paper.**

> Emotion labeling and emotion matching were also compared with implicit baseline (consisting of unmodeled fixation events during the intertrial intervals).

**Referent** `4CA3Ca2bzfPW` (pmid 25821147), 5.1's paper and 5.1's model — "Emotion Labeling >
Baseline", where the baseline is stated to be "unmodeled fixation events during the intertrial
intervals". Both maps come off the one design matrix, which is why they are one
`ModelEstimation` and two `Analysis` records.

### 5.3 Brain–behaviour correlation with a continuous measure

```yaml
stage: group
terms:
  - id: term-perceived-stress
    name: perceived stress
    type: continuous
    variation_level: between_subject
    unit: PSS score
    assessment: asmt-pss          # the measurement's provenance
  - id: term-age
    name: age
    type: continuous
    variation_level: between_subject
    unit: years
  - id: term-sex
    name: sex
    type: categorical
    variation_level: between_subject
    levels:
      - {level: female}
      - {level: male}
  - id: term-framewise-displacement
    name: framewise displacement
    type: continuous
    variation_level: between_subject
  - id: term-anxiety
    name: anxiety
    type: continuous
    variation_level: between_subject
    assessment: asmt-sai

effect:
  cells:
    - {term: term-perceived-stress, direction: positive}
```
→ **`cross_subject_regression`**, adjusted for every other term in the model. The cell has no
`level` because a slope has none, and its `direction` is the sign of the fitted coefficient.

`term-sex` is categorical and declares its levels even though no contrast names it. A covariate is
a column like any other: what makes it a covariate is the absence of a cell, not a different kind
of term.

`variation_level: between_subject` is what makes this a cross-subject regression. Change it to
`within_subject` — a value regressor varying trial to trial — and the same cell pattern derives
**`parametric_modulation`**. That one field is the whole difference, and it is on the model because
it is a property of the measurement rather than of the contrast.

The covariates appear in no cell, which is how the record says the correlation was adjusted for
them. There is no covariate list.

**In the paper.**

> First, a whole-brain correlation analysis was conducted to uncover the brain areas related to perceived stress.
>
> A whole-brain correlation analysis showed that higher levels of perceived stress were associated with greater fALFF in the left superior frontal gyrus (SFG)
>
> the framewise displacement (FD; Van Dijk, Sabuncu, & Buckner, 2012) was calculated as a measure of head motion and was treated as a covariate in the subsequent data analyses
>
> Controlling for age, sex, and head motion

**Referent** `3KGhvY7MhanA` (pmid 31397949) — perceived stress against resting-state fALFF,
controlling for age, sex and head motion. Transcribed, not corrected.

`3qC7anyYszL4` (pmid 22076840) is the same shape in structural data — grey-matter volume on a trait
impulsiveness score — and differs in one instructive way: there the extractor typed `gender` as
`continuous`. Two levels and no order is a categorical term, and a covariate being uninteresting
does not make it exempt.

### 5.4 Moderation — a continuous measure crossed with a cohort factor

"Regions showing a symptom-severity by diagnosis interaction."

```yaml
stage: group
terms:
  - id: term-ctq
    name: CTQ total score
    type: continuous
    variation_level: between_subject
    assessment: asmt-ctq
  - id: term-ptsd
    name: PTSD diagnosis
    type: categorical
    variation_level: between_subject
    levels:
      - {level: PTSD group, groups: [grp-ptsd]}
      - {level: TC group, groups: [grp-tc]}
  - id: term-ctq-x-ptsd
    name: CTQ total score × PTSD diagnosis
    type: continuous
    variation_level: between_subject
    interaction_with: [term-ctq, term-ptsd]
  - id: term-age
    name: age
    type: continuous
    variation_level: between_subject
    unit: years

effect:
  cells:
    - {term: term-ctq-x-ptsd, direction: negative}   # the interaction coefficient's sign
```
→ **`interaction`**, adjusted for `term-ctq`, `term-ptsd` and `term-age`.

This is the one crossing the cells alone cannot express, and the reason `interaction_with` exists. A
continuous term has no levels, so it cannot be crossed; without the product column this would read as
a plain regression on maltreatment severity. The product column also holds the only thing that can
carry the moderation's *direction* — "maltreatment was negatively associated with rACC activation in
the PTSD group but not in controls" is a fact about the crossing, not about either term's own slope.

**Do not add a product column for a crossing of two categorical factors.** There the crossed levels
already say it (5.5), the column decides nothing, and records that add one are flagged for review.

**In the paper.**

> To test for regions showing different effects of child maltreatment in the PTSD versus control groups, group-level models included an interaction term for CTQ total score × PTSD, main effects of CTQ score and PTSD status, and an age covariate.

**Referent** `5P7tnuyp5NTP` (pmid 27062552) — Stevens et al. 2016, "Interaction of CTQ and PTSD
diagnosis". The paper also reports each group's slope separately, and the extractor cell-ed those
the way §5.5's last row does: `{term: term-ctq, direction: positive}` alongside
`{term: term-ptsd, level: PTSD, direction: held}`, the held level marking the group the
slope was taken within.

### 5.5 A factorial with one within- and one between-subject factor

Subject group (between) × task (within), one model, five results.

```yaml
stage: group
inputs_from: [me-first-level]        # task and motion are fitted below
terms:
  - id: term-group
    name: subject group
    type: categorical
    variation_level: between_subject
    levels:
      - {level: RA patients}
      - {level: healthy controls}
  - id: term-disease-activity
    name: disease activity state
    type: categorical
    variation_level: between_subject
    levels:
      - {level: active}
      - {level: remission}
  - id: term-das28-crp
    name: DAS28-CRP
    type: continuous
    variation_level: between_subject
```

| result | cells | derives | adjusted for |
|---|---|---|---|
| main effect of group | both group levels `undirected` | `omnibus` | task, disease activity, DAS28 |
| main effect of task | both task levels `undirected` | `omnibus` | group, disease activity, DAS28 |
| group × task, F-test | all four levels `undirected` | `omnibus` | disease activity, DAS28 |
| rotation > comparison, within RA | task crossed + `held` on `RA patients` | `contrast` | disease activity, DAS28 |
| RA > HC, within rotation | group crossed + `held` on `rotation` | `contrast` | disease activity, DAS28 |

All five are this paper's, off the one model, and the record holds two more: the same simple
effect of task within the control group, and a comparison of active against remission patients.

Three things to read off that table. **Averaging over a factor is the absence of its cells** — the
main effect of group simply has no task cells, which is also how it comes to be adjusted for them.
Rows three and four are §4's pair in one model: the F-test cells every level `undirected` because
the test yields no per-level sign, and the simple effect's `RA patients` cell is `held` because the
comparison was taken within that level, which puts it on both sides.
**The two factors differ only in `variation_level`**; the cells have the same shape for a
between-subject and a within-subject factor, because what differs is a property of the design,
recorded once on the model.

The last two rows are the same comparison read along its two axes, and they are what makes the
held cell load-bearing: without it, "rotation vs comparison within RA" and "rotation vs comparison
within HC" are the same two cells and the record cannot tell them apart.

**In the paper.**

> A 2 × 2 factorial design analysis of variance (ANOVA) was designed for fMRI analyses, with the group (RA and HC group) and task (rotation and comparison) as factors.
>
> The differences of activation were analyzed for the main effect and simple effect of the group, task and the interaction effects of group by task.
>
> Compared to the control group, RA patients showed enhanced activation in the left precuneus, left superior frontal gyrus and right cingulate gyrus during the rotation task, with left hemisphere dominance.

**Referent** `Qa5HqrHq97Pm` (pmid 37559139) — a mental-rotation task in rheumatoid arthritis. This
one is **audited by hand**, and the audit is
[corrections/Qa5HqrHq97Pm.corrections.json](../corrections/Qa5HqrHq97Pm.corrections.json): 34
operations that take the record from five validator errors to none.

Four of the five defects are worth knowing because they are not this paper's:

- the three omnibus cells carried `extraction_status: not_reported` **in place of a level**, so the
  factor being tested named none of its own levels;
- `term-group` and the two DAS28 scores sat on `me-first-level`, a per-participant GLM, which cannot
  carry a between-subject column at all;
- task and group were each declared twice, once per stage, which §5.12 rejects;
- the two simple effects of task came out with **identical cells**, because the held group level was
  missing — the defect the fourth and fifth rows above exist to prevent.

The fifth is the ordinary one: `healthy control subjects` cell-ed against a declaration reading
`healthy controls`. Naming the model was wrong too — both simple effects named the first-level
stage, and an `Analysis` names the top stage and reaches downward through `inputs_from`, never up.

`6qSfdQCVbYhH` (pmid 11050021) is the same design in autism and shows the level-string rewrite on
its own: `explicit processing of emotional facial expressions` declared, `explicit` cell-ed, four of
its fifteen errors from that one abbreviation.

### 5.6 A pre–post change with no paradigm

```yaml
stage: group
terms:
  - id: term-vbm-time
    name: scan occasion
    type: categorical
    variation_level: within_subject
    levels:
      - {level: after practice,  order: 2, timepoints: [tp-followup]}
      - {level: before practice, order: 1, timepoints: [tp-baseline]}
  - id: term-vbm-group
    name: group
    type: categorical
    variation_level: between_subject
    levels:
      - {level: practice group, groups: [grp-practice]}
      - {level: control group, groups: [grp-control]}

effect:
  cells:
    - {term: term-vbm-time, level: after practice,  direction: positive}
    - {term: term-vbm-time, level: before practice, direction: negative}
```
→ **`contrast`**, adjusted for `term-vbm-group`. A longitudinal structural analysis has no
`Condition` and no `Task`; it links to data through `Analysis.acquisitions`. Nothing about the
encoding differs from 5.1 — the levels name `timepoints` instead of `conditions`, and that is what
makes it a change over time.

A crossover comparison of arms is the same with `arms:` on the levels. When the arms are separate
cohorts rather than a within-person crossing, the levels name `groups` and the allocation is on
`Group.arm`; `StudyDesign.assignment_structure` says which a study is.

**In the paper.**

> The comparison of GM before and after practice shows a significant increase in GM in a subset of the regions (gray) activated in the right occipital cortex during mirror reading (green).
>
> The longitudinal voxel-based morphometry analysis yielded an increase of gray matter in the right dorsolateral occipital cortex that corresponded to the peak of mirror-reading-specific activation.

**Referent** `39HoutR6iLMj` (pmid 18417700) — Ilg et al. 2008, a longitudinal VBM comparison of
grey matter after two weeks of mirror-reading practice against before it. The paper also runs an
fMRI task, and this analysis is not of it: the structural contrast names two scans and no
condition.

### 5.7 An ordered factor contrasted at its extremes

```yaml
stage: group
terms:
  - id: term-condition
    name: condition
    type: categorical
    variation_level: within_subject
    levels:
      - {level: 2-back, order: 3, conditions: [cond-2back]}
      - {level: 1-back, order: 2, conditions: [cond-1back]}
      - {level: 0-back, order: 1, conditions: [cond-0back]}

effect:
  cells:
    - {term: term-condition, level: 2-back, direction: positive}
    - {term: term-condition, level: 0-back, direction: negative}
```
→ **`contrast`**. `1-back` has no cell, which records that the contrast weighted it out. The
monotonic structure is not in the cells at all — it is `FactorLevel.order`, plus whatever
`Analysis.definition` says in the source's words.

**In the paper.**

> In the 0-back condition, the target corresponded to the first pseudo-word presented; in the 1-back condition, the target occurred each time a pseudo-word matched the immediately preceding pseudo-word; and in the 2-back condition the target occurred when a pseudo-word matched that presented two positions prior.
>
> we examined activation of the 2-back (most difficult) minus 0-back (easiest) conditions for each group

**Referent** `7EEyXsyEDf2Q` (pmid 26624517) — Pierce et al. 2015, a phonological n-back at
0-back, 1-back and 2-back reporting every pairwise contrast; "2-back > 0-back" is this one. §5.8
is the same model's omnibus.

### 5.8 An omnibus F-test over a three-level factor

```yaml
effect:
  cells:
    - {term: term-condition, level: 0-back, direction: undirected}
    - {term: term-condition, level: 1-back, direction: undirected}
    - {term: term-condition, level: 2-back, direction: undirected}
```
→ **`omnibus`**. All three levels take part and none is signed. Giving them cells rather than
omitting them is what keeps the factor out of the adjustment set: it was tested, not controlled for.

`undirected` and not the other two unsigned values, and §4's two questions say why. The levels were
not held: the F compares them against each other rather than taking a contrast within any of them,
so `held` on all three would claim the factor was held at three levels at once — the shape
`check_unsigned_cells` flags as a miscoded F. Nothing was withheld: an F over three levels
returns one statistic for the set and has no per-level direction to print, so this is not a withheld
sign either. Reporting the follow-up contrasts would supply signs, but those are separate Analyses
(§2) and would not sign *these* cells.

That distinction is what makes this an `omnibus` at all. Had the paper run a two-level comparison
and merely omitted which way it went, the cells' `direction` would be `not_reported` and step 6
would derive a `contrast`.

**In the paper.**

> Accuracy and reaction time were compared using 3 × 3 analysis of variances (ANOVAs) with group (monolingual, bilingual, IA) and task (0-back, 1-back, 2-back) as factors.
>
> There was a significant main effect of condition in the left superior frontal gyrus, inferior parietal lobule and posterior cingulate, and in the right anterior insula, anterior cingulate and middle frontal gyrus

**Referent** `7EEyXsyEDf2Q` (pmid 26624517), §5.7's paper and §5.7's model — the "main effect of
condition" coordinate table off the 3 × 3 ANOVA. The same three load levels §5.7 contrasted at
its extremes, here all cell-ed and none signed.

### 5.9 Multivariate — decoding above chance

```yaml
stage: subject and group
terms:
  - id: term-task-mvpa
    name: classification task
    type: categorical
    variation_level: within_subject
    levels:
      - {level: tone perception,  conditions: [cond-tone]}
      - {level: vowel listening,  conditions: [cond-vowel-listening]}
      - {level: vowel imagery,    conditions: [cond-vowel-imagery]}
      - {level: vowel production, conditions: [cond-vowel-production]}

effect:
  cells:
    - {term: term-task-mvpa, level: vowel imagery, direction: positive}

details:
  details_type: DecodingDetails
  decoded_variable: the seven vowels of the Italian language
  validation_scheme: leave-one-stimulus-out
  performance_metrics:
    - {name: classification accuracy, relation: above_reference}
```
→ **`simple_effect`**. One signed cell, on the task the classifier was run within — not one cell per
decoded class. The classes are `decoded_variable`'s business, and cell-ing them would say the
comparison was between them.

What the accuracy was compared *to* is `reference_value`, per metric, because a study reporting
accuracy against chance and AUC against 0.5 has two references and one field on the Effect could
not hold both. It is unset here, correctly: this paper tests against chance by permutation and
never states a chance figure, so `relation` carries the claim alone.

A between-cohort comparison of accuracies is a crossed cohort term (5.1's shape). Accuracy regressed
on a behavioural score is 5.3's shape. The `Effect` does not change form because the method did — the
method lives in `details`.

**In the paper.**

> Specifically, patches of cortex in inferior frontal and superior temporal regions retained information to significantly discriminate the seven vowels of the Italian language in each condition.
>
> A cross-validation leave-one-stimulus-out procedure was adopted to measure classification accuracy.
>
> To assess significance, group accuracies were tested against chance by a permutation test

**Referent** `3jDCyBsgwY5d` (pmid 29208951) — Vowel decoding across listening, imagery and
production. Transcribed, not corrected: the extractor produced three decoding analyses, each a
single signed cell, which is the schema's own `a-classifier-signs-one-cell-not-two` rule obeyed
without prompting.

Five decoding papers were extracted before this one. Two produced no `DecodingDetails` at all,
one produced no analyses, and one cell-ed **both** classes `undirected` — which derives `omnibus`
and asserts the classes were compared with each other. That last failure is the one this example
is written against, and it is why the rule is worth stating.

### 5.10 A double dissociation between regions

```yaml
stage: group
inputs_from: [me-subject-fc]
terms:
  - id: term-seed
    name: seed region
    type: categorical
    variation_level: within_subject
    levels:
      - {level: anterior right dlPFC,  regions: [reg-anterior-right-dlpfc]}
      - {level: posterior right dlPFC, regions: [reg-posterior-right-dlpfc]}
  - id: term-group
    name: subject group
    type: categorical
    variation_level: between_subject
    levels:
      - {level: Parkinson's disease patients, groups: [grp-pd]}
      - {level: healthy controls, groups: [grp-hc]}

effect:
  cells:
    - {term: term-seed, level: posterior right dlPFC, direction: positive}
    - {term: term-seed, level: anterior right dlPFC,  direction: negative}
    - {term: term-group, level: healthy controls, direction: positive}
    - {term: term-group, level: Parkinson's disease patients, direction: negative}
```
→ **`interaction`**. The point of signing the region axis is that it makes the group direction
*scoped*: "controls > patients" is asserted at the posterior seed, not of the analysis as a whole.
Without it the record reads as a plain group contrast and asserts a difference true in one region
and false in the other.

**In the paper.**

> a region at the inferior frontal sulcus involved in cognitive action control was partitioned into an anterior and posterior subdivision based on their whole-brain co-activation profiles
>
> To analyze seed-specific FC differences between patients and healthy controls, that is, FC group differences that are significantly higher for one seed compared to the other, we tested for the “seed × subject group” interaction effects in conjunction with the positively correlated network of the respective seed in the respective subject group.
>
> When testing for specific connectivity differences, i.e., the “seed × subject group” interaction (in the direction of a PD-related posterior right dlPFC connectivity decrease) for the medical OFF condition

**Referent** `6Ts55HvrSTEJ` (pmid 28611616) — connectivity of an anterior and a posterior right
dlPFC seed in Parkinson's disease. The extractor signed **both** axes unprompted, and the region
levels join their declarations; the correction above is on the group levels, which it cell-ed `HC`
and `PD` against declarations reading `healthy controls` and `Parkinson's disease patients` — §5.5's
24%, not a failure of this shape.

A factor comparing places is scarce in coordinate tables — across 39,192 pubget table captions, a
region axis crossed with a condition appears essentially only where the regions are seeds, as here,
because everywhere else such a factor is reported as an ROI analysis and ROI analyses do not
produce the tables stage 1 reads.

`3agtZxaWUcQV` (pmid 16154453) — Simons et al. 2005, medial against lateral anterior PFC over task
and position memory — is the same shape and shows the failure. The extractor built its region factor
correctly, filled `FactorLevel.regions`, and then **cell-ed only the context axis**, producing
exactly the plain condition contrast this example warns against. That record also settles something
this example used to assert: it carried the comment "no entity slots: a region is not a study
entity", and that is wrong.

### 5.11 A mediated path

```yaml
stage: group
terms:
  - id: term-age-med
    name: age
    type: continuous
    variation_level: between_subject
    unit: years
  - id: term-gmd
    name: grey matter density
    type: continuous
    variation_level: between_subject
  - id: term-ica-loading
    name: bilateral PFC ICA network loading
    type: continuous
    variation_level: between_subject

effect:
  cells:
    - {term: term-age-med, direction: positive}
  mediation:
    path: indirect
    mediator: term-gmd
```
→ **`cross_subject_regression`**, adjusted for whatever else is in the model but *not* for
`term-gmd`. `Mediation` is present only for a mediation analysis, and both its fields are
required, so a path always names its mediator.

Which path was tested decides the mediator's status. A `direct` path is by definition the effect
holding the mediator constant, so there it *is* adjusted for. An `indirect` path is undefined without
it and a `total` path is estimated without conditioning on it, so in neither is it a covariate.

**In the paper.**

> of GM density (GMD) and used this information to reassess the age-related relationships between age (the predictor variable) and ICA activity in the bilateral PFC network (the outcome variable)
>
> mediation analysis can be conceptualised as a series of three separate regression equations testing different components of the mediation hypothesis in each voxel within: 1) the age-related decline in GMD (the a effect), 2) the relationship between GMD and ICA loading on the bilateral PFC network, controlling for age (the b effect)

**Referent** `5PXzmhEsxc2e` (pmid 25172389) — age acting on a prefrontal network's loading through
grey matter density. Transcribed, not corrected.

Finding it took eleven papers, and the search that worked is worth recording. Papers *about*
mediation, found by title and abstract, produced no `Effect.mediation` at all — four of them,
including one with the word in its title. Mediation reported in prose and figures never reaches
stage 1, and an `Effect` the pipeline never created cannot carry a `mediation` block. Searching
**table captions** instead — "MNI coordinates for significant mediation clusters", "Path a-, b- and
a×b-related brain activations" — found papers that table their mediation, and three of the first
four produced the block. If a construct is missing across a corpus, check whether the papers put it
in a table before concluding the extractor cannot see it.

`b9EfXh32hvPV` (pmid 36221050) is the same shape reported the other way: an indirect effect of body
mass index on negative symptoms via insular grey matter, with total and direct effects
non-significant. That last part is why `path` is a field and not an inference — the same three
variables carry three different answers, and only the tested one is the record's.

### 5.12 A model estimated in two stages

"Seed-based connectivity of the left amygdala, computed per participant with white-matter, CSF and
motion regressors, then compared between patients and controls in a group model with age, sex and
scanner as covariates of no interest."

Two design matrices, so two records — and one model, so a link:

```yaml
model_estimations:
  - id: me-subject-rsfmri
    model_family: glm
    stage: subject
    estimator: Pearson correlation
    terms:
      - {id: term-vim-timecourse, name: VIM seed time series, region: reg-vim-left,
         type: continuous, variation_level: within_subject}
      - {id: term-motion, name: head motion and nuisance signals,
         type: continuous, variation_level: within_subject}

  - id: me-group-diagnosis
    model_family: anova
    stage: group
    estimator: random-effects two-sample t-test
    inputs_from: [me-subject-rsfmri]
    terms:
      - id: term-diagnosis
        name: group
        type: categorical
        variation_level: between_subject
        levels:
          - {level: ET patients, groups: [grp-et]}
          - {level: HCs, groups: [grp-hc]}
      - {id: term-trs, name: tremor severity, assessment: asmt-trs,
         type: continuous, variation_level: between_subject}
```

```yaml
analysis:
  model_estimation: me-group-diagnosis   # the top stage, always
  effect:
    cells:
      - {term: term-diagnosis, level: HCs, direction: positive}
      - {term: term-diagnosis, level: ET patients, direction: negative}
```
→ **`contrast`**, adjusted for `term-trs`, `term-motion` **and** `term-vim-timecourse`.

Two of those three covariates are columns of a record this analysis does not name. That is the
point: motion regressed out at the first level adjusts the group betas, and without the link the
record asserted the opposite by omission.

**Do not cell the seed.** The link makes an unsigned `{term: term-vim-timecourse}` cell constructible,
and it is the trap: a cell says the contrast *tested* that column, and a tested continuous
within-subject term derives `parametric_modulation` by step 2 of §3 — so the diagnosis contrast
stops reading as a contrast. What the map is *of* is `Measure` and `ConnectivityDetails`; what the
contrast *compared* is the cells. The seed belongs in the adjustment set, which is exactly what the
connectivity beta is conditional on.

**Name the seed once, as a `Region`.** The place and the column carrying its signal are two
things: `term-vim-timecourse` is the column, and the region is a `Region` that `term-vim-timecourse` points at through
`ModelTerm.region` and every analysis built on that map names in `seed_regions`. As bare
strings one seed becomes three spellings — `left VIM`, `VIM seed time series`,
`Left VIM connectivity` — that nothing joins. Its provenance is
`Region.definition_method`, so a seed taken from this study's own earlier contrast is
`same_study_analysis`, and that contrast names it in `defines_regions`.

The rule is unchanged by stages: cell what the comparison compared, and nothing else. Where a
first-level column genuinely *is* what was compared — a group contrast of a task condition fitted
per subject — cell it, and the derivation reads it as it reads any other factor.

**A crossing spanning the stages** is a product column on the stage that fitted it, naming the
lower stage's column directly: a group-level `term-dx-x-vim` with
`interaction_with: [term-diagnosis, term-vim-timecourse]` is "the seed's connectivity related to diagnosis", and derives
`interaction` by §5.4's rule. The lower column is never copied upward.

**Two seeds are two chains.** The left-VIM and right-VIM group models have identical term lists
and different inputs, and the input is part of the specification, so they are two records rather
than one shared by four analyses.

**When not to split.** `inputs_from` records a stage the source describes. A one-sample activation
map has a group stage too — an intercept over the first-level contrast images — and papers say
nothing about it, so it takes no record of its own and the first-level record stands alone. §5.1 is
that case, and most of §5 with it: a two-stage fit reported as one map is one record. Where such a
stage *is* described, its `terms` is legitimately empty; do not invent an intercept term.

**In the paper.**

> a VIM seed-based functional connectivity (FC) analysis of resting-state functional magnetic resonance imaging (RS-fMRI) data was performed to characterize the VIM FC network in ET patients.
>
> Fisher's z-transformation was applied to improve the normality of these correlation coefficients, and individual VIM-related RS-FC maps were constructed.
>
> We combined the group-level significant brain regions into a mask, within which we further identified the group differences using the random-effects two-sample t-test.
>
> Compared with HCs, ET patients displayed VIM-related FC changes, primarily within the VIM-motor cortex (MC)-cerebellum (CBLM) circuit, which included decreased FC in the CBLM and increased FC in the MC.

**Referent** `6WJs2gBAhcQL` (pmid 26467643) — Fang et al. 2016, a VIM seed connectivity map
computed per participant and then compared between essential tremor patients and controls. The
seed is named as a region and cell-ed in nothing; what the contrast compared is diagnosis.

---

## 6. When the paper does not fit

**A crossing whose parameterization the paper never states.** Whether "faces vs houses" is two levels
of one factor or two independent regressors is a design-matrix choice papers rarely report. Record it
as one factor with two levels; the cells describe the comparison either way, and nothing in the
derivation depends on the choice.

**A factor the paper does not name.** Supply a name for a grouping the paper does make. The levels
must be the source's own.

**Magnitudes that matter.** `direction` is a sign, so `[1, -½, -½]` and `[1, -1, 0]` over three levels
are not distinguished. If the weighting is the point, it goes in `Analysis.definition` in the
source's words.

**A method with no structured decomposition.** `OtherAnalysisDetails` names itself in
`method_label`; `NotStructurableDetails` is for a method that has no stable decomposition at all.
`Analysis.model_representation_notes` is for a first-class method whose model has a component the
schema represents only approximately — random slopes, latent variables, dynamic connectivity.

**A value the vocabulary cannot name.** Most enums bind `any_of: [<Enum>, string]` — 26 fields do —
so the source's own wording passes through, and accumulating free-text values is the evidence for
whether a further value earns a place. Ten fields bind a closed vocabulary instead, one per enum:
`Direction`, `TermType`, `Prespecification`, `EffectPath`, `RegionDefinition`, `Modality`,
`NullHypothesis`, `PolaritySemantics`, `PerformanceRelation` and `EdgeDirectionality`. Each carves a
space rather than cataloguing observations, so there is no wording a source could use that these do
not already partition — and a closed vocabulary cannot report that it is short a value, which is why
opening one is a schema decision rather than an extraction judgement.

The last of the ten is closed and also **derived**: `ConnectivityEdge.directionality` is a lookup on
`connectivity_method`, since only a directed model supports a claim about which region influences
which and no wording can override that. It is the one vocabulary nothing reads off the page.

**Nothing was tested.** Then it is not an Analysis. See §2.
