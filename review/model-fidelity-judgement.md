# How well do the extracted models represent what the papers state?

An adjudication of the 6 `ModelEstimation`s and 18 `Analysis` contrasts in
`ns-review-structure`, judged against the Methods and Results of the three papers
in `review/examples/`. Read the paper first, then the record; every finding below
cites the sentence it turns on.

Verdict per paper, then the findings.

| paper | terms | cells | coverage | overall |
|---|---|---|---|---|
| `5Rw4BhGBShSR` gaze × clarity | exact | exact | 2 stated contrasts missing | **good** |
| `HU6mqxmtySg3` proverbs | faithful | exact | complete | **good** |
| `4cRnHYtfSwuK` amygdala RSFC | one model conflated from two | correct but undifferentiated | 4+ analyses missing | **weak** |

The models are better than the coverage. Where the extractor built a term list it
got the design right in all three papers; what it missed is *analyses* — contrasts
and maps the papers state and the record does not contain.

## 5Rw4BhGBShSR — Communicative talker gaze

**Terms: exact.** The paper says "a second-level, random effects 3×2
within-subjects flexible factorial ANOVA model in SPM8, with factors Subject, Gaze
Direction (Direct, Downward, Averted) and Auditory Clarity (2,3)". The record has
exactly those three terms with exactly those levels.

**Three models, correctly separated.** "additional one-way within-subjects ANOVAs
with the single factor Gaze Direction … were run separately for the two levels of
Auditory Clarity" — the record has `model_one_way_gaze_2ch` and
`model_one_way_gaze_3ch`, each with the single factor, and the two per-clarity
contrasts reference the right one. This is the case the schema most wants right —
analyses whose design matrices differ taking their own records — and it is right.

**Cells: all 9 correctly signed**, including the two-against-one contrasts
(`Direct=+, Averted=−, Downward=−` for `[2−1−1 2−1−1]`) and the combined
`Direct=+, Averted=+, Downward=−` for `[1 1−2 1 1−2]`.

**Two stated contrasts are missing.** The Methods list them in the same sentence as
the ones that were extracted:

- **Interaction of Gaze Direction and Auditory Clarity**
  (`[kron([1−1], orth(diff(eye(3))')']`) — an F contrast, absent from the record.
- **Downward Gaze > Averted and Direct** (`[−1−1 2−1−1 2]`) — a T contrast, absent,
  while its two siblings (Direct>, Averted>) were both extracted.

Missing the third of three parallel contrasts is the more troubling of the two: it
suggests truncation of a list rather than a judgement about relevance.

Also unextracted: "three within-subjects ANOVAs with factors Gaze Direction and
Auditory … for (i) Direct vs. Downward, (ii) Averted vs. Downward and (iii) Direct
vs. Averted". Their results are in the Supplemental Material, so omitting them is
defensible — but they are three stated `ModelEstimation`s and the record does not
say they were skipped.

**One definition is inverted.** `analysis_conjunction` reads

> Conjunction null: (Direct **<** Averted & Downward) ∩ (3 channels > 2 channels)

The paper says "activations showing **preferential responses to direct gaze** and
increased auditory clarity … A conjunction null of these two contrasts". Direct is
the *greater* side. The **cells are right** (`Direct=+, Averted=−, Downward=−`);
only the prose is wrong. That combination is the dangerous one: a reader trusting
`Analysis.definition` gets the opposite of what the cells encode, and nothing in
the record is internally inconsistent enough for a validator to catch it.

## HU6mqxmtySg3 — Proverb comprehension

**Complete and correctly signed.** The Results and the Figure 2 caption name five
contrasts and the record has five, each with the right sign:

| paper | record |
|---|---|
| "(a) Proverb > Literal sentence" | `opaque=+, transparent=+, literal=−` |
| "transparent proverbs elicited activation … compared with literal sentences" | `transparent=+, literal=−` |
| "opaque proverbs elicited activation in the left IFG and right SMG" (vs literal) | `opaque=+, literal=−` |
| "(c) Opaque proverb > Transparent proverb" | `opaque=+, transparent=−` |
| "(b) Transparent proverb > opaque proverb" | `transparent=+, opaque=−` |

The three-level `sentence condition` factor with `opaque proverb / transparent
proverb / literal sentence` is the right shape, and modelling "Proverbs > Literal"
as two positive cells against one negative — rather than inventing a two-level
proverb/literal factor — is exactly what the schema asks for.

**The term list is thin but not wrong.** One term, no nuisance regressors. The
paper describes motion correction as preprocessing and never states regressors in
the design matrix, so there is nothing omitted.

## 4cRnHYtfSwuK — Amygdala resting-state connectivity

The weakest of the three, and the errors are structural rather than sign errors.

**One first-level model conflated from two.** The paper: "a regression model was
created to include **the left amygdala time series** as a predictor and eight
nuisance covariates … **This process was repeated again for the right amygdala.**"
That is two models with different design matrices. The record has a single
`model_subject_filM` carrying *both* seeds as terms, which asserts a model that
was never estimated — one regression containing both amygdalae simultaneously.
The schema is explicit that "analyses whose design matrices differ take their own
records rather than sharing one".

**Eight covariates bundled into one term.** `white matter, CSF, and motion
nuisance covariates` is a single `ModelTerm` where the paper states "eight nuisance
covariates (time series predictors for WM, CSF, and the six motion parameters)".
Since `ModelEstimation.terms` is the adjustment set of every contrast taken from
the model, collapsing eight columns into one loses the granularity that slot
exists to carry. Less serious than the conflation, but the same kind of error.

**The group model is right.** "Given the documented effect of age and sex … these
were used as covariates of no-interest … we used the scanner as the covariate of
no-interest" → `diagnostic group (MDD, HC)`, `age`, `sex`, `scanner`. Exact.

**Four analyses missing, from one sentence.** "statistical parameter maps were
generated for the right and left amygdala separately, **to determine the mean of
each of the patient and control groups**, as well as the significant difference
between groups." The record has the four between-group contrasts and none of the
four within-group mean maps (MDD mean and HC mean, per seed). Those are one-sample
tests — a single positive cell each — and the schema explicitly provides for them.

**Left and right are indistinguishable in the record.** All four contrasts carry
identical cells (`MDD` vs `HC`), reference the same model, and differ in no
structured field: `roi_label` is unset, `spatial_scope` is `whole_brain`, and no
`ConnectivityDetails` payload was filled, so the seed exists nowhere queryable.
The only thing separating `analysis_left_hc_gt_mdd` from `analysis_right_hc_gt_mdd`
is the table each cites and the extractor's own choice of `local_id`. A storage
record built from this extraction cannot answer "which amygdala was the seed?".

**Two further tested effects unextracted**, both judgement calls rather than clear
errors: the L–R amygdala synchrony comparison ("compared between MDD patients and
HCs … by Student's t-test") and the clinical correlations (cluster mean Z against
HRSD, HAM-A, duration of illness). Both are tested effects; neither is a map, so
whether they are `Analysis` records depends on how "reported inferential map or
tested effect" is read for non-image statistics. Worth deciding explicitly, since
the same shape recurs constantly.

## The pattern across the three

**Signs are reliable.** 17 of 18 contrasts carry cells that match the paper. The
single discrepancy is prose, not cells.

**Term lists are reliable when the paper states a design matrix plainly.** Both
ANOVA papers are exact. The one that went wrong is the paper describing a model
*procedurally* ("repeated again for the right amygdala") rather than declaratively.

**Coverage is the weak axis.** At least six stated analyses are absent across the
three papers — an interaction F, a third directional T, and four within-group mean
maps — and in every case they sit in the same sentence or list as analyses that
*were* extracted. This is what the stage-0 inventory and the `analysis_missing`
verdict exist to catch, and it is the strongest argument for running that stage
before the contrast tasks rather than after.

**One thing the review UI gets wrong, not the extractor.** *(Fixed: the grid now offers
all five values, and the exporter maps a recorded `unstated` to `unstated`. Kept because
it is the record of how the defect was found. The last sentence below has since been
settled, and then re-cut again: an omnibus F is now `undirected`, `unstated` is a
directional test whose sign the paper withheld, and `held` is a level on both sides of
the comparison. See `representing-models.md` §4.)* Both F-test main effects
in the gaze paper carry cells with `direction: unstated` — which the Direction enum
defines as "a sign exists and the source does not report it … distinct from
omitting the cell, which says the contrast weighted this level out". The contrast
grid has only `positive / negative / absent`, so the exporter maps `unstated` onto
`absent` and the task renders an omnibus F as "every term adjusted for, none
tested" — the opposite of the truth. The grid needs the enum's full vocabulary,
including `not_applicable`, which is arguably the correct value for an omnibus F
in the first place.
