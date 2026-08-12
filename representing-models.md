# Representing an analysis

How to put a reported analysis into the storage schema, and how to tell which shape it is. This is
the practical companion to [storage-schema-design-notes.md](storage-schema-design-notes.md), which
holds the reasoning behind the shape rather than instructions for using it. The schema's field
definitions are in [neuroimaging-study-storage/](neuroimaging-study-storage/) and say what each
field *is*; this says how the classes fit together and what to do when a paper does not divide the
world the way the schema does.

Two worked records are checked against the derivation on every test run:
[3agtZxaWUcQV.storage.yaml](examples/3agtZxaWUcQV.storage.yaml), extracted by hand from a real
paper, and [factorial-2x2.storage.yaml](examples/factorial-2x2.storage.yaml), constructed to show
six contrasts off one design. Every YAML fragment below is verified the same way.

---

## 1. The one decision everything else follows from

**Which facts belong to the model, and which to the contrast?**

A paper reports one model and several results from it. The schema splits them:

| | `ModelEstimation` | `Effect` (one per `Analysis`) |
|---|---|---|
| holds | the design matrix: its terms, their levels, what those levels range over, the family, estimator, stage, software | which of those levels this comparison used, and which way |
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

**And one qualification.** "One per design matrix" counts *estimation stages*: a first-level GLM
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

Separate Analyses are required when the direction, the cohorts compared, the method, the cell
pattern, the seed, the decoded variable, the component identity, or the spatial scope differ. An
omnibus effect and its directional post-hoc contrast are two Analyses.

### `ModelEstimation` — one design matrix, at one stage

The model's terms and how it was fit. Referenced by every Analysis whose contrast came out of it.

**Where it ends.** It says nothing about any one contrast. It also carries the **full** term list —
including terms no reported contrast tests. "Controlling for age and sex" is a statement about the
design matrix, so age and sex are terms here even though no contrast will name them. A term omitted
from every stage reads as one the model did not adjust for.

`inputs_from` names the stages this one was fitted on, and `level` labels the stage in the source's
own words. Only the link orders them: two records both saying `group` say nothing about their
relation. An `Analysis` names the **top** stage — the one that produced the reported statistics —
and the stages below are reached from there, which is why a first-level model is referenced by no
Analysis and is not thereby orphaned.

### `ModelTerm` — one column

A column of the design matrix, in the general sense that covers a GLM regressor and the predictor of
any other family. Categorical terms declare their levels; continuous terms carry the facts that make
a slope interpretable (`unit`, `assessment`, `source_definition`).

`variation_level` is the one field to get right and easy to skip: whether the term moves *within* a
participant or only *across* the sample. It applies to every term, not only continuous ones, and for
a continuous term it decides whether the effect is a parametric modulation or a cross-subject
regression.

**Where it ends.** Nothing about a contrast. A term does not know whether it was tested.

### `FactorLevel` — one level, and what realizes it

A level of a categorical term, plus the study entities carrying it: `conditions`, `groups`,
`timepoints`, `arms`.

This is the join that makes a comparison legible. An `Effect` names levels; following those levels
here says whether the comparison was between cohorts, between conditions, between occasions, or
between arms. **Nothing on the Effect says which kind of comparison it is** — that is read off here.

A factor whose levels are not study entities at all — regions, hemispheres, tasks, frequency bands,
acquisition sessions — leaves all four slots empty and is carried by `level` alone. That factor is
complete, not deficient.

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
see §4 for the two unsigned values and what distinguishes them.

### `AnalysisGroup` — who was in it

Cohort membership and per-analysis `n`. **Not** who was compared: a cohort comparison is a pair of
directional cells on the term whose levels name the cohorts. Populate `n` whenever the paper gives
one; expect it below the acquired count when an analysis drops participants.

### `AnalysisDetails` subclasses — the method's own fields

Which subclass is filled *is* the method. No `analysis_type` field to keep in step with it. All eight
payload slots empty means `MassUnivariateDetails`, which carries no fields — that is how a
mass-univariate analysis is asserted rather than inferred from silence.

---

## 3. Working out which shape a result is

Read the result and answer these in order. Each step is decided by the cells, so there is nothing to
declare.

```
1. Does a cell sit on a product column (a term with interaction_with)?
     signed   -> interaction        (a moderation; see 3.5)
     unsigned -> omnibus            (an interaction F-test)
2. Does a cell sit on a continuous term?
     variation_level within_subject  -> parametric_modulation
     variation_level between/mixed   -> cross_subject_regression
3. Count the terms carrying BOTH a positive and a negative cell -- the crossed terms.
     2 or more -> interaction
     exactly 1 -> contrast
4. No crossed term but some cell signed  -> simple_effect
5. Cells present, none signed            -> omnibus
```

"Crossed" rather than "signed" is the load-bearing word in step 3. A term signed once has not been
compared against itself. That is why a cohort comparison of an activation map is a `contrast` and not
an interaction: the cohort term is crossed, the condition term merely signed.

---

## 4. The four values of `direction`, and the three ways to be non-directional

`positive` and `negative` are the two sides of a comparison. Everything else is a way of taking part
without having a side, and they are different facts:

| the contrast did this | recorded as | in the adjustment set? |
|---|---|---|
| put the level on the plus side | `positive` | no |
| put it on the minus side | `negative` | no |
| gave the level no weight | **no cell at all** | the *term* is, if no level of it has a cell |
| compared it, sign not stated | `unstated` | no |
| tested it undirectionally — an F or χ² | `unstated`, on every level | no |
| held one level constant | `not_applicable` on that level | no — it took part, at one level |

Two of these are the ones most often conflated.

**Weighted out is absence.** A three-level factor compared at two of its levels gives the third no
cell. There is no `zero` value: a zero weight *is* a missing cell.

**Held constant is an unsigned cell.** "Patients versus controls, within the task condition" puts
`task` on the plus side and the minus side at once, so its net sign on the condition axis is nothing.
`positive` there would claim a condition comparison the contrast never makes. Because the level does
have a cell, its term stays out of the adjustment set — the contrast did not average over it.

### Which unsigned value: ask whether a sign exists

Both unsigned values say the record has no sign to give. They disagree about *why*, and the question
that settles it is: **could a fuller report have signed this cell?**

- **Yes → `unstated`.** An F-test over a three-level factor does compare its levels against each
  other; what it withholds is which way each went. The paper could have reported the means or the
  follow-up contrasts and signed every one of those cells. The sign exists in the data and is
  missing from the page, which is precisely what `unstated` records. The same holds for the cell
  carrying an interaction F on a product column: the interaction coefficient has a sign, the
  omnibus test just does not report it.
- **No → `not_applicable`.** A level the contrast was taken *within* sits on the plus side and the
  minus side simultaneously. No report, however complete, could give it one sign, because the sign
  is not missing — it is not defined. This is the only shape a `Cell` has for `not_applicable`.

A corollary worth stating, because it is checkable: a cell that names **no level** — on a slope or a
product column — can never sit on both sides of anything, so it can never be `not_applicable`. An
undirected test of such a column is `unstated`.

**Telling the two apart in a finished record** needs no extra field either: an F-tested factor has
*all* its levels celled and unsigned; a held-constant factor has *one* unsigned level and the rest
absent. That is a consequence of the rule above rather than a second rule — an undirected test spans
the whole factor, and holding a level constant is a statement about that one level.

---

## 5. Worked models

Each of these is verified against the derivation. Only the `ModelEstimation.terms` and
`Effect` fragments are shown; a complete record also needs the Analysis's sample, paradigm,
acquisition, measure, statistic and details.

### 5.1 A simple contrast — faces > houses

```yaml
terms:
  - id: t-cond
    name: stimulus_type
    type: categorical
    variation_level: within_subject
    levels:
      - {level: faces,  conditions: [cond-faces]}
      - {level: houses, conditions: [cond-houses]}

effect:
  cells:
    - {term: t-cond, level: faces,  direction: positive}
    - {term: t-cond, level: houses, direction: negative}
```
→ **`contrast`**, adjusted for nothing. One crossed term. That the comparison is between conditions
rather than cohorts is read off `FactorLevel.conditions`, not off the Effect.

Note the term name `stimulus_type`. Papers often state the levels and never name the axis; supplying
a name for a grouping the paper does make is allowed. The levels must be the source's own, and the
name must not assert a comparison or a construct the paper does not.

### 5.2 The same map against the implicit baseline

```yaml
effect:
  cells:
    - {term: t-cond, level: faces, direction: positive}
```
→ **`simple_effect`**. One signed cell and no second one is not an incomplete record: a lone `+1`
weight tests that coefficient against zero, and zero is what the unmodelled implicit baseline is. The
sign is what distinguishes activation from deactivation, which is why this is not encoded as "no
cells".

If the paper contrasted against a *modelled* rest condition instead, `rest` takes a `negative` cell
and the result is 5.1 — a `contrast`.

### 5.3 Brain–behaviour correlation with a continuous measure

```yaml
terms:
  - id: t-symptom
    name: depression severity
    type: continuous
    variation_level: between_subject
    assessment: asmt-bdi          # the measurement's provenance
    unit: BDI-II total
  - id: t-age
    name: age
    type: continuous
    variation_level: between_subject
    unit: years

effect:
  cells:
    - {term: t-symptom, direction: negative}
```
→ **`cross_subject_regression`**, adjusted for `t-age`. The cell has no `level` because a slope has
none, and its `direction` is the sign of the fitted coefficient.

`variation_level: between_subject` is what makes this a cross-subject regression. Change it to
`within_subject` — a value regressor varying trial to trial — and the same cell pattern derives
**`parametric_modulation`**. That one field is the whole difference, and it is on the model because
it is a property of the measurement rather than of the contrast.

Age appears in no cell, which is how the record says the correlation was adjusted for it. There is
no covariate list.

### 5.4 Moderation — a continuous measure crossed with a cohort factor

"Regions showing a symptom-severity by diagnosis interaction."

```yaml
terms:
  - id: t-symptom
    type: continuous
    variation_level: between_subject
  - id: t-dx
    type: categorical
    variation_level: between_subject
    levels:
      - {level: patients, groups: [grp-pt]}
      - {level: controls, groups: [grp-hc]}
  - id: t-symptom-x-dx
    type: continuous
    interaction_with: [t-symptom, t-dx]

effect:
  cells:
    - {term: t-symptom-x-dx, direction: negative}   # the interaction coefficient's sign
    - {term: t-dx, level: patients, direction: positive}
    - {term: t-dx, level: controls, direction: negative}
```
→ **`interaction`**, adjusted for `t-symptom`.

This is the one crossing the cells alone cannot express, and the reason `interaction_with` exists. A
continuous term has no levels, so it cannot be crossed; without the product column this would read as
a plain regression on symptom severity. The product column also holds the only thing that can carry
the moderation's *direction* — "the symptom–activation relationship was steeper in patients" is a
fact about the crossing, not about either term's own slope.

**Do not add a product column for a crossing of two categorical factors.** There the crossed levels
already say it (5.5), the column decides nothing, and records that add one are flagged for review.

### 5.5 A factorial with one within- and one between-subject factor

Diagnosis (between) × memory load (within), one model, four results.

```yaml
terms:
  - id: t-dx
    type: categorical
    variation_level: between_subject
    levels:
      - {level: patients, groups: [grp-pt]}
      - {level: controls, groups: [grp-hc]}
  - id: t-load
    type: categorical
    variation_level: within_subject
    levels:
      - {level: high, order: 2, conditions: [cond-3back]}
      - {level: low,  order: 1, conditions: [cond-1back]}
  - id: t-dx-x-load
    type: categorical
    interaction_with: [t-dx, t-load]
  - id: t-motion
    type: continuous
    variation_level: within_subject
```

| result | cells | derives | adjusted for |
|---|---|---|---|
| main effect of diagnosis | `t-dx` crossed | `contrast` | `t-load`, `t-dx-x-load`, `t-motion` |
| main effect of load | `t-load` crossed | `contrast` | `t-dx`, `t-dx-x-load`, `t-motion` |
| the interaction, directional | both crossed | `interaction` | `t-motion` |
| the interaction, F-test | `unstated` cell on `t-dx-x-load` | `omnibus` | `t-dx`, `t-load`, `t-motion` |
| diagnosis within high load | `t-dx` crossed + `not_applicable` on `high` | `contrast` | `t-motion` |

Two things to read off that table. **Averaging over a factor is the absence of its cells** — the main
effect of diagnosis simply has no load cells, which is also how it comes to be adjusted for the
interaction column. The last two rows are §4's pair in one model: the F-test's cell is `unstated`
because the interaction has a sign the paper withheld, and the simple effect's `high` cell is
`not_applicable` because the comparison was taken within that level, which puts it on both sides.
And **the two factors differ only in `variation_level`**; the cells are the same
shape for a between-subject and a within-subject factor, because what differs is a property of the
design, recorded once on the model.

The full six-analysis version is [factorial-2x2.storage.yaml](examples/factorial-2x2.storage.yaml).

### 5.6 A pre–post change with no paradigm

```yaml
terms:
  - id: t-time
    type: categorical
    variation_level: within_subject
    levels:
      - {level: post, order: 2, timepoints: [tp-followup]}
      - {level: pre,  order: 1, timepoints: [tp-baseline]}

effect:
  cells:
    - {term: t-time, level: post, direction: positive}
    - {term: t-time, level: pre,  direction: negative}
```
→ **`contrast`**. A longitudinal structural analysis has no `Condition` and no `Task`; it links to
data through `Analysis.acquisitions`. Nothing about the encoding differs from 5.1 — the levels name
`timepoints` instead of `conditions`, and that is what makes it a change over time.

A crossover comparison of arms is the same with `arms:` on the levels. When the arms are separate
cohorts rather than a within-person crossing, the levels name `groups` and the allocation is on
`Group.arm`; `StudyDesign.assignment_structure` says which a study is.

### 5.7 An ordered factor contrasted at its extremes

```yaml
levels:
  - {level: 3-back, order: 3}
  - {level: 2-back, order: 2}
  - {level: 1-back, order: 1}

effect:
  cells:
    - {term: t-load, level: 3-back, direction: positive}
    - {term: t-load, level: 1-back, direction: negative}
```
→ **`contrast`**. `2-back` has no cell, which records that the contrast weighted it out. The
monotonic structure is not in the cells at all — it is `FactorLevel.order`, plus whatever
`Analysis.definition` says in the source's words.

### 5.8 An omnibus F-test over a three-level factor

```yaml
effect:
  cells:
    - {term: t-load, level: 3-back, direction: unstated}
    - {term: t-load, level: 2-back, direction: unstated}
    - {term: t-load, level: 1-back, direction: unstated}
```
→ **`omnibus`**. All three levels take part and none is signed. Giving them cells rather than
omitting them is what keeps the factor out of the adjustment set: it was tested, not controlled for.

`unstated` and not `not_applicable`: the F *did* compare the three levels, and each of them had a
side in the data that the paper did not report — §4's question answers "yes, a fuller report could
have signed this". `not_applicable` on all three would say each level sat on both sides of the
comparison at once, which would make the factor its own control.

### 5.9 Multivariate — decoding above chance

```yaml
terms:
  - id: t-class
    name: decoded class
    type: categorical
    variation_level: within_subject
    levels:
      - {level: faces,  conditions: [cond-faces]}
      - {level: houses, conditions: [cond-houses]}

effect:
  cells:
    - {term: t-class, level: faces, direction: positive}

details:
  details_type: DecodingDetails
  decoded_variable: stimulus category
  performance_metrics:
    - {name: accuracy, value: 0.72, reference_value: 0.5, relation: above_reference}
    - {name: AUC,      value: 0.79, reference_value: 0.5, relation: above_reference}
  validation_scheme: leave-one-run-out
```
→ **`simple_effect`**. What the accuracy was compared *to* is `reference_value`, per metric, because
accuracy against chance and AUC against 0.5 are two references and one field on the Effect could not
hold both.

A between-cohort comparison of accuracies is a crossed cohort term (5.1's shape). Accuracy regressed
on a behavioural score is 5.3's shape. The `Effect` does not change form because the method did — the
method lives in `details`.

### 5.10 A double dissociation between regions

```yaml
terms:
  - id: t-region
    type: categorical
    variation_level: within_subject
    levels:                      # no entity slots: a region is not a study entity
      - {level: medial anterior PFC}
      - {level: lateral anterior PFC}
  - id: t-condition
    type: categorical
    variation_level: within_subject
    levels:
      - {level: task,     conditions: [cond-task-mem]}
      - {level: position, conditions: [cond-position-mem]}

effect:
  cells:
    - {term: t-region, level: medial anterior PFC,  direction: positive}
    - {term: t-region, level: lateral anterior PFC, direction: negative}
    - {term: t-condition, level: task,     direction: positive}
    - {term: t-condition, level: position, direction: negative}
```
→ **`interaction`**. The point of signing the region axis is that it makes the condition direction
*scoped*: "task > position" is asserted at the medial level, not of the analysis as a whole. Without
it the record reads as a plain condition contrast and asserts an ordering true in one region and false
in the other.

The real instance is `3agtZxaWUcQV-a3` in the worked example.

### 5.11 A mediated path

```yaml
effect:
  cells:
    - {term: t-stress, direction: positive}
  mediation:
    path: indirect
    mediator: t-cortisol
```
→ **`cross_subject_regression`**, adjusted for whatever else is in the model but *not* for
`t-cortisol`. `Mediation` is present only for a mediation analysis, and both its fields are required,
so a path always names its mediator.

Which path was tested decides the mediator's status. A `direct` path is by definition the effect
holding the mediator constant, so there it *is* adjusted for. An `indirect` path is undefined without
it and a `total` path is estimated without conditioning on it, so in neither is it a covariate.

### 5.12 A model estimated in two stages

"Seed-based connectivity of the left amygdala, computed per participant with white-matter, CSF and
motion regressors, then compared between patients and controls in a group model with age, sex and
scanner as covariates of no interest."

Two design matrices, so two records — and one model, so a link:

```yaml
model_estimations:
  - id: me-l1-left
    model_family: glm
    level: subject
    estimator: OLS (AR1)
    terms:
      - {id: t-lamyg, name: left amygdala time series,
         type: continuous, variation_level: within_subject}
      - {id: t-nuisance, name: WM, CSF and motion,
         type: continuous, variation_level: within_subject}

  - id: me-group-left
    model_family: mixed_effects
    level: group
    estimator: FLAME
    inputs_from: [me-l1-left]
    terms:
      - id: t-dx
        type: categorical
        variation_level: between_subject
        levels:
          - {level: patients, groups: [grp-pt]}
          - {level: controls, groups: [grp-hc]}
      - {id: t-age, type: continuous, variation_level: between_subject}
```

```yaml
analysis:
  model_estimation: me-group-left      # the top stage, always
  effect:
    cells:
      - {term: t-dx, level: controls, direction: positive}
      - {term: t-dx, level: patients, direction: negative}
```
→ **`contrast`**, adjusted for `t-age`, `t-nuisance` **and** `t-lamyg`.

Two of those three covariates are columns of a record this analysis does not name. That is the
point: motion regressed out at the first level adjusts the group betas, and without the link the
record asserted the opposite by omission.

**Do not cell the seed.** The link makes an unsigned `{term: t-lamyg}` cell constructible,
and it is the trap: a cell says the contrast *tested* that column, and a tested continuous
within-subject term derives `parametric_modulation` by step 2 of §3 — so the diagnosis contrast
stops reading as a contrast. What the map is *of* is `Measure` and `ConnectivityDetails`; what the
contrast *compared* is the cells. The seed belongs in the adjustment set, which is exactly what the
connectivity beta is conditional on.

**Name the seed once, as a `Region`.** The place and the column carrying its signal are two
things: `t-lamyg` is the column, and the region is a `Region` that `t-lamyg` points at through
`ModelTerm.region` and every analysis built on that map names in `seed_regions`. As bare
strings one seed becomes three spellings — `left amygdala`, `left amygdala time series`,
`Left amygdala connectivity` — that nothing joins. Its provenance is
`Region.definition_method`, so a seed taken from this study's own earlier contrast is
`same_study_analysis`, and that contrast names it in `defines_regions`.

The rule is unchanged by stages: cell what the comparison compared, and nothing else. Where a
first-level column genuinely *is* what was compared — a group contrast of a task condition fitted
per subject — cell it, and the derivation reads it as it reads any other factor.

**A crossing spanning the stages** is a product column on the stage that fitted it, naming the
lower stage's column directly: a group-level `t-dx-x-lamyg` with
`interaction_with: [t-dx, t-lamyg]` is "the seed's connectivity related to diagnosis", and derives
`interaction` by §5.4's rule. The lower column is never copied upward.

**Two seeds are two chains.** The left-seed and right-seed group models have identical term lists
and different inputs, and the input is part of the specification, so they are two records rather
than one shared by four analyses.

**When not to split.** `inputs_from` records a stage the source describes. A one-sample activation
map has a group stage too — an intercept over the first-level contrast images — and papers say
nothing about it, so it takes no record of its own and the first-level record stands alone. Where
such a stage *is* described, its `terms` is legitimately empty; do not invent an intercept term.

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

**A value the vocabulary cannot name.** Most enums bind `any_of: [<Enum>, string]`, so the source's
own wording passes through. Accumulating free-text values is the evidence for whether a further value
earns a place. `Direction`, `TermType`, `Prespecification` and `EffectPath` are closed, because they
carve a space rather than catalogue observations.

**Nothing was tested.** Then it is not an Analysis. See §2.
