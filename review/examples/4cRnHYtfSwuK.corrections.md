# Corrections — `4cRnHYtfSwuK` (pmid 24600410)

**Ramasubbu et al., "Reduced intrinsic connectivity of amygdala in adults with major
depressive disorder", Frontiers in Psychiatry 2014.**
55 unmedicated MDD patients and 19 healthy controls, one resting-state scan each,
amygdala seed-based whole-brain connectivity, four coordinate-bearing contrasts across
two tables.

Model output preserved at [4cRnHYtfSwuK.extraction.raw.json](4cRnHYtfSwuK.extraction.raw.json).
**19 corrections across 323 fields**, in four groups. The analyses' substance —
seeds, directions, thresholds — was right; what needed fixing was identity and shape.

## 1. Every analysis was named after the table caption, not the contrast (4 fields)

| local_id | model said | corrected to |
|---|---|---|
| `analysis_left_hc_gt_mdd` | "Brain regions exhibiting a significant difference between patients with MDD and HC in the resting-state functional connectivity with the left amygdala" | `HC > MDD` |
| `analysis_left_mdd_gt_hc` | *(identical caption)* | `MDD > HC` |
| `analysis_right_hc_gt_mdd` | *(caption, right amygdala)* | `HC > MDD` |
| `analysis_right_mdd_gt_hc` | *(identical caption)* | `MDD > HC` |

This is the one instruction the analyses pass is given most explicitly — *"keeping the given
name verbatim in `name.value`"* — and it is the only paper of the three where it was
disobeyed: 18 of 18 names were verbatim on the other two, 0 of 4 here.

The reason is visible in the stage-1 list. This paper's contrast labels are terse and each
appears **twice**, once per table, so `HC > MDD` occurs at positions 1 and 3. The caption is
the only thing that looks like it distinguishes them — but it does not, because both
directions in a table share one caption. The result was two pairs of identical names, which
is strictly worse than the terse labels, and the captions were already recorded verbatim on
`tables[1].caption` and `tables[2].caption`.

The disambiguator the record actually has is `Analysis.tables`: `tbl2` for the left-amygdala
pair, `tbl3` for the right. That was correct throughout.

## 2. `coordinate_space` was omitted on all four analyses (4 fields)

Set to "standard Montreal Neurological Institute (MNI) template", evidenced by the
preprocessing sentence in Methods. The paper states it twice — in Methods, and in the footer
of both result tables ("MNI, Montreal Neurological Institute"), which is itself recorded on
the `Table` records. Stage 1 also reported `MNI` for all 21 coordinates.

Worth noting how this got through: the slot is optional, so it was **omitted entirely**
rather than marked `not_reported`, and nothing in the pipeline flags an absent optional
field. Only reading the paper finds these.

## 3. Null cross-references (9 fields)

`groups[*].arm` and seven `ModelTerm.assessment` slots carried JSON `null`. A cross-reference
is a bare `local_id` string; it has no `not_reported` form and no null form — when there is
nothing to point at, the key is simply absent. Dropped.

Both are correct as absences. The study is observational — two diagnostic cohorts, no
allocation — so there are no arms. And none of the seven terms draws its values from a
participant-level assessment: group, sex and scanner are categorical, age is demographic,
and the amygdala time series and WM/CSF/motion regressors come from the images.

This was the single largest error class across all three papers (15 of the 15 validator
errors that survived the first build), so the prompt now states the exception:
*"a reference is not an ExtractedValue, so it has no `not_reported` form … Rule 5 does not
apply to these."*

## 4. Two scanners in one field (2 fields, 2 new records)

`instrument.model` read **"Signa VHi and Discovery MR 750"** on both acquisitions — one
string asserting a scanner model that does not exist. The paper used two: 63 of 74
participants on a GE Signa VHi, and 11 MDD patients on a GE Discovery MR 750 "using the same
parameters and protocol".

An `Acquisition` holds one `Instrument`, so the honest record is two acquisitions per
modality. Split into `acq_fmri_rest` / `acq_fmri_rest_scanner2` and `acq_mri_structural` /
`acq_mri_structural_scanner2`, each with its own model and its own evidence span. All four
analyses now reference both fMRI acquisitions, because the sample was pooled — scanner enters
the group model as a covariate of no interest rather than splitting the analysis.

**Residual expressivity gap:** *which* participants used which scanner is not representable.
The 11 rescanned patients are a subset of `group_mdd`, not a group of their own, and no slot
links a participant subset to an acquisition. The paper's own handling — `term_scanner` as a
covariate of no interest in the FLAME model — *is* captured, so the modelling is recorded
even though the assignment is not.

## Checked and left alone

- **`ConnectivityDetails` on all four analyses**: `connectivity_method: seed_based`,
  `seed_regions` left/right amygdala matching the table each came from, and
  `parameter_change` `decreased`/`increased` agreeing with the contrast direction.
- **Inference settings**: Z = 2.3, cluster extent 77 voxels, corrected *p* = 0.05 by Monte
  Carlo simulation in AlphaSim. Correct on all four.
- **Cells**: `term_group` with `HC`/`MDD` at the right signs on each contrast.
- **`acquisition_duration_seconds: 460` with `number_of_volumes: 230`.** Reads oddly against
  the paper's "230 s ... (115 volumes)", but those are per-scan and there were two scans, so
  the record's totals — 460 s, 230 volumes — are right and mutually consistent at TR = 2 s.
- **`age_minimum: 20`, `age_maximum: 55`.** Borderline: the sentence "Patients were of both
  genders, right-handed and within the age range of 20–55 years" reads as eligibility, but it
  is phrased descriptively about the patients who took part, so it does bound the group's
  ages. Kept. The observed mean and SD are only in Table 1, which is not in the source text.
- **`medication_status`.** The paper is genuinely awkward here — the abstract says
  "unmedicated", Results says all but three had prior antidepressant exposure, and Discussion
  says "our patient sample was exposed to medication". The extracted value ("unmedicated at
  imaging; free of psychotropic medication for at least 3 weeks at recruitment, although most
  had prior antidepressant exposure") reconciles all three correctly.
