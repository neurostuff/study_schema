# Corrections — `HU6mqxmtySg3` (pmid 29075575)

**Yi et al., "Neural correlates of Korean proverb processing", Brain and Behavior 2017.**
15 healthy adults, one visual sentence-reading task, three sentence types, five
coordinate-bearing contrasts across two tables.

The model output is preserved at
[HU6mqxmtySg3.extraction.raw.json](HU6mqxmtySg3.extraction.raw.json); the corrections below
are applied in place to `HU6mqxmtySg3.extraction.json`.

This was the designated easy case, and it behaved like one: **4 corrections across 217
fields**, none of them in the analyses. All five analyses, their cells, directions, table
links and inference settings were correct as extracted.

## Corrections

| field | model said | corrected to | why |
|---|---|---|---|
| `groups[0].medical_condition` | `"healthy; no previous neurological or psychiatric disease"` (a bare string) | `not_reported` | Two faults at once. The slot is an `ExtractedStringList`, so a bare string is the wrong shape — the validator caught this. And the group is *healthy*: no condition defines or characterises it. The absence of neurological or psychiatric history is an **inclusion criterion**, and it is already recorded as one in `inclusion_criteria`. |
| `groups[0].clinical_characteristics` | `"Healthy adults; seven men and eight women."` | `not_reported` | Neither clause belongs. The slot holds disease duration, severity, treatment status or comorbidities; the paper reports none. The sex split is already in `sex_distribution`, correctly, and restating it here duplicates a fact the schema has a home for. |
| `groups[0].enrolled_count` | absent | `15` | "We prospectively recruited 15 healthy adults" states enrolment, and all 15 were scanned. The model recorded only `acquired_count`; both are stated by that one sentence. |
| `model_estimations[0].terms` | included `term_familiarity` (continuous) | dropped | Familiarity was **equalised across proverb types by stimulus selection**, not entered in the model — opaque proverbs scored 4.04 and transparent 4.13 on a pre-rating, deliberately matched. A `ModelTerm` is a column of the design matrix; this is a design control. Tellingly, the model's own `source_definition` for the term said familiarity "was set to the same condition", and no `Cell` in any of the five analyses referenced it. |

## Checked and left alone

- **All five analyses.** Names verbatim from stage 1; cells reference `term_sentence_condition`
  with the right levels and signs; `tbl3`/`tbl4` links match the table each contrast was
  reported in; `details_type: MassUnivariateDetails` on all five; `spatial_scope: whole_brain`.
- **The three-level rule.** `Proverbs > Literal sentences` carries three cells (opaque +,
  transparent +, literal −) because "proverbs" is both proverb levels. `Transparent proverbs >
  Literal sentences` carries **two**, omitting opaque — which is `extraction-readme.md` §2's
  "a three-level factor compared at two of its levels → no cell at all" applied correctly.
- **Acquisition.** Philips Achieva 3 T, TR 3 s, TE 35 ms, 4 mm slices, 128×128 — all correct,
  and the T1 recorded as a separate acquisition.
- **Age.** mean 30.2, range 27–33. Correct, and worth stating because the abstract's
  "cohort of 15 healthy participants" gives no ages; the model used the Participants section.

## Noted, not corrected: the paper contradicts itself on threshold

Methods says the significance threshold was "uncorrected *p* < .001, uncorrected for multiple
comparisons, which corresponded to a *T*-value threshold of **4.5**". But the Figure 2 caption
says *p* < .001 uncorrected **[T ≥ 3.30]** with extent ≥ 10 voxels, and every *T* value in
Tables 3 and 4 falls between **3.31 and 3.56** — consistent with 3.30, impossible under 4.5.

The extraction handles this the right way round without being told to: `inference_settings`
records *p* < .001 uncorrected with a 10-voxel extent, which is what the reported results
actually used, while `model_estimations[0].model_settings` quotes the Methods sentence
including the 4.5. Both are faithful to their sources. No schema field asserts which is
correct, and none should — this is the paper's inconsistency, not the record's.
