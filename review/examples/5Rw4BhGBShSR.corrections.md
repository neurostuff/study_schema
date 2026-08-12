# Corrections — `5Rw4BhGBShSR` (pmid 28400328)

**McGettigan et al., "You talkin' to me? Communicative talker gaze activates left-lateralized
superior temporal cortex during perception of degraded speech", Neuropsychologia 2017.**
18 adults, one audiovisual task, a 3×2 within-subjects flexible factorial ANOVA in SPM8,
nine coordinate-bearing analyses across two tables.

Model output preserved at [5Rw4BhGBShSR.extraction.raw.json](5Rw4BhGBShSR.extraction.raw.json).
**21 corrections across 382 fields.** The hardest of the three papers, and the only one where
corrections reached the model structure rather than just field shapes.

## 1. The three checkpoints this paper was chosen for — all three passed

`extraction-candidates.md` states the intended reading in advance, so these are real checks
rather than my own judgement:

- **Gaze levels are nominal.** `FactorLevel.order` is unset on all three of Direct, Averted
  and Downward, and set to 1/2 on the two Auditory Clarity levels, which *are* ordered
  (2 vocoder channels then 3). Correct, unprompted.
- **Eyes Covered and rest are conditions that are levels of no factor.** Both are recorded as
  `Task.conditions` and neither appears in any `FactorLevel`. That is exactly the
  paradigm-versus-model distinction the paper states outright: *"we decided not to model the
  Eyes Covered conditions in the ANOVA because we did not want to conflate a manipulation of
  eyes present vs. eyes absent with one of gaze direction."* Mouth Covered is correctly
  **absent** — it was in the behavioural pilot only.
- **Subject is a nuisance term no cell names.** No `Cell` in any of the nine analyses
  references `term_subject`. Correct — though its `levels` needed fixing, below.

## 2. Corrections to the model

| what | model said | corrected to | why |
|---|---|---|---|
| `term_subject.levels` | one level, `"participants"` | `[]` | Subject's levels are the 18 participants. A single level called "participants" asserts a one-level factor, which is a claim the paper does not make and which no cell uses. The evidence span attached to it ("single subject") came from the first-level modelling sentence and does not support a level vocabulary at all. |
| `analysis_direct_2_channels.model_estimation`, `analysis_direct_3_channels.model_estimation` | `model_second_level_flexible_factorial` | new `model_one_way_gaze_2ch` / `model_one_way_gaze_3ch` | **The largest correction.** These two analyses did not come from the 3×2 model. Methods: *"additional one-way within-subjects ANOVAs with the single factor Gaze Direction (Direct, Downward, Averted) were run separately for the two levels of Auditory Clarity"*, and Table 3's caption repeats it. Two new `ModelEstimation` records added, each with its own Gaze Direction column, and the cells repointed — a `ModelTerm` is owned by its `ModelEstimation`, so a shared column would be the wrong claim. |
| the auditory-clarity cell on those two analyses | `not_applicable` | dropped | Under a one-way model there is no clarity factor to hold constant: these ANOVAs were run **on the 2-channel (resp. 3-channel) data only**, so clarity restricts the data rather than sitting on both sides of a modelled comparison. The restriction stays where it is already recorded, in the analysis name and definition. Under the 3×2 model the original `not_applicable` would have been right, which is why this correction follows from the model fix rather than standing on its own. |

## 3. Omnibus F cells: `not_applicable` → `unstated` (5 cells)

`analysis_main_gaze` (3 cells) and `analysis_main_clarity` (2 cells) marked every level
`not_applicable`. `extraction-readme.md` §2 separates the cases that all look like "a level
with no side", and this is the undirected one, not the held-constant one:

*(This reading is now the schema's own: `Direction` was re-cut so that `not_applicable` means
only a level on both sides of the comparison, and `check_unsigned_cells` flags the shape below
automatically. At the time of this pass the enum's text said the opposite, which is why the
correction had to argue for it.)*

| the source says | record | this case |
|---|---|---|
| compared at two of three levels | no cell | — |
| the contrast was taken *within* this level | `not_applicable` | ✗ what was recorded |
| compared, but no direction given | `unstated` | ✓ what an F main effect is |

An F test of a main effect *does* compare the levels against each other; what it does not
give is a sign. `not_applicable` means the level sat on both sides at once — it asserts the
main effect held its own factor constant, which is self-contradictory.

The extraction got the directional contrasts right throughout, so this is specifically the
undirected case being collapsed into the wrong one of the two unsigned options.

## 4. The reported statistic is T, not Z (4 fields)

`analysis_direct_averted_vs_downward`, `analysis_conjunction`, and both Table 3 analyses had
`statistic.family: z`. Both result tables carry an **`F/T` column and a separate `Z` column**,
and the model read the Z. Methods names these as T contrasts — *"as well as T contrasts
describing … the response to Direct Gaze … and the combined response to Direct Gaze and
Averted Gaze"* — and an SPM conjunction null yields T.

Corrected to `t`. Note the lower case: I first wrote `T`, and the validator's new
vocabulary check caught it — `StatisticFamily` has `t`, not `T`.

## 5. The conjunction's direction is inverted in the paper's own table (4 fields)

Table 1 labels the row **`Conjunction null: (Direct < Averted & Downward) ∩ (3 channels > 2
channels)`** — verified as `&lt;` in the source XML, so not a parsing artefact — and the
extraction faithfully followed it, marking Direct negative.

The Results section describes the opposite: *"Fig. 5 overlays the significant activations
showing preferential responses to **direct gaze** and increased auditory clarity … A
conjunction null of these two contrasts resulted in four significant clusters, with peaks in
left inferior frontal gyrus, right insula, left lingual gyrus and right calcarine gyrus."*
Those four clusters are exactly the four rows under that table label, in the same regions.

So the `<` in the table header is a typo in the published paper. Cells corrected to Direct
positive, Averted and Downward negative, and the component definition to
"Direct > Averted and Downward.", both evidenced by the Results sentence. `name` is left
verbatim as the table prints it — the name is a quotation, the cells are the claim.

This is the one correction that could not be made from the tables alone, and the one an
adjudicator working from highlighted spans would most likely miss.

## 6. Shape errors (5 fields)

- `groups[0].arm` and three `ModelTerm.assessment` slots carried `not_reported` wrappers.
  A cross-reference is a bare `local_id`; when there is nothing to point at the key is
  absent. Dropped. (Single-cohort within-subject study: no arms. None of the three factors
  draws its values from a participant-level assessment.)
- `groups[0].medical_condition` held a bare string in an `ExtractedStringList` slot, and the
  group is healthy: no condition characterises it. The absence of neurological, speech or
  language problems is an exclusion criterion and is already recorded as one. Set to
  `not_reported`.

## Checked and left alone

- **Acquisition.** Siemens Avanto 1.5 T, dual-echo EPI, TR 9 s, TE `[0.024, 0.058]`, 3 mm
  isotropic. `number_of_volumes: 285` is correct and inferred — the paper says "three runs of
  95 whole-brain volumes", and 285 appears nowhere in the text.
- **Preprocessing.** Realign and unwarp, coregister, unified-segmentation normalise, resample
  to 2 mm, smooth 8 mm FWHM. All present and in order.
- **Inference settings.** *p* < 0.005 voxelwise uncorrected with a 68-voxel cluster extent for
  a whole-brain α of 0.001 by Monte Carlo simulation. Correct on all nine.
- **The six experimental conditions** as the 3×2 crossing, each `FactorLevel` naming the two
  conditions that carry it. This is the structure the paper is in the benchmark to test, and
  it came out right.
- **`analysis_direct_averted_vs_downward` cells** — Direct +, Averted +, Downward −, matching
  the contrast weights `[1 1 −2 1 1 −2]` given in Methods.

## Noted, not corrected

The **interaction of Gaze Direction and Auditory Clarity** was computed (Methods lists the F
contrast; Results says "There were no significant clusters showing an interaction") but has
no `Analysis` record, because it produced no coordinates and so does not appear in any table.
That is correct under the scope of this run — stage 1 enumerates coordinate-bearing analyses —
but it means the record cannot distinguish *an effect that was tested and came out null* from
*an effect that was never tested*. A null result is a reportable finding, and nothing in the
record carries it.
