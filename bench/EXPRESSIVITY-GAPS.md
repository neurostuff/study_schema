# Expressivity gaps, mined from the schema's own complaints

**Source:** `Analysis.model_representation_notes` across `bench/runs2` — **157 notes from 50 of 60 papers (83%)**, plus 22 `not_structurable` payloads.

The schema asks the extractor to record what it could not represent, so this is a free expressivity study. Frequency is counted in **distinct papers**, not notes, so one voluble paper cannot rank a gap.

`schema_gap` = no existing field can hold it. `guidance_problem` = a field exists and was not used, so fix the prompt, not the schema.

**Verified:** every `existing_field` named below was checked against
`neuroimaging-study-extraction.yaml` — all seven exist, so the `guidance_problem` verdicts stand
and are not the clustering model inventing a convenient field. The `schema_gap` rows were checked
the other way: no field on the named class covers the content.

| gap | papers | verdict | existing field | change |
|---|---:|---|---|---|
| **Structured factorial design and factor scope** | 9 | `schema_gap` | `none` | Add Analysis.factorial_design to encode each factor's within- or between-subject scope, levels, crossed interactions, and longitudinal structure. |
| **Explicit inference masking operations** | 4 | `schema_gap` | `none` | Add InferenceSettings.masking to record mask source, absolute or functional mask thresholds, and whether masking is inclusive or exclusive for each contrast. |
| **Connectivity time-series conditioning and lag settings** | 2 | `schema_gap` | `none` | Add ConnectivityDetails.time_series_model_settings to record conditioning variables, lag-order selection, and subject-level conjoint-significance requirements for Granger or related models. |
| **Bootstrap inference metadata** | 1 | `schema_gap` | `none` | Add InferenceSettings.resampling_method with fields for bootstrap type, sample count, confidence-interval construction, and bias correction. |
| **DCM model architecture and model reduction** | 1 | `schema_gap` | `none` | Add ConnectivityDetails.model_architecture to record neuronal and observation submodels, candidate connection sets, and Bayesian model-reduction or model-comparison procedures. |
| **Task, covariate, and nuisance regressors** | 28 | `guidance_problem` | `Analysis.terms` | Prompt the extractor to create a Term for every covariate, nuisance regressor, motion or physiological regressor, trialwise regressor, and no-interest task regressor, and link them through Analysis.terms with the appropriate role. |
| **HRF convolution details** | 12 | `guidance_problem` | `ModelEstimation.hrf_model` | Prompt the extractor to record the complete HRF basis, temporal derivative, convolution duration, and waveform in ModelEstimation.hrf_model, placing any remaining timing details in model settings. |
| **Filtering, autocorrelation, orthogonalization, and model-error settings** | 10 | `guidance_problem` | `ModelEstimation.model_settings` | Prompt the extractor to preserve high-pass filters, autocorrelation and nonsphericity corrections, orthogonalization, spline terms, model-selection rules, and related estimation settings in ModelEstimation.model_settings. |
| **Fixed, random, and hierarchical effects** | 6 | `guidance_problem` | `ModelEstimation.model_settings` | Prompt the extractor to record fixed-effects, random-effects, subject/session levels, and hierarchical variance-estimation details in ModelEstimation.model_settings, alongside ModelEstimation.level. |
| **Image and time-series denoising or normalization operations** | 4 | `guidance_problem` | `Preprocessing.steps` | Prompt the extractor to record global-signal regression, physiological time-series resampling, cerebellar normalization, and binding-potential image calculations as ordered Preprocessing.steps. |
| **Generalized PPI regressor architecture** | 3 | `guidance_problem` | `Analysis.terms` | Prompt the extractor to represent each psychological, physiological, and interaction/PPI regressor as a Term, using Term.interaction_with for the product terms and linking all of them through Analysis.terms. |
| **Hemisphere factor levels** | 1 | `guidance_problem` | `Term.levels` | Prompt the extractor to create a categorical Term for hemisphere and populate Term.levels with the reported hemisphere labels, linking it through Analysis.terms. |
| **Decoding classifier configuration** | 1 | `guidance_problem` | `Analysis.decoding` | Prompt the extractor to store classifier family, hyperparameters, feature-vector construction, label transformations, null-generation procedures, and run-wise training details in Analysis.decoding. |
| **Directed connectivity edges** | 1 | `guidance_problem` | `ConnectivityEdge.directionality` | Prompt the extractor to create separate directed ConnectivityEdge records and populate ConnectivityEdge.directionality for forward and backward connections. |
| **General methods summaries rather than representation gaps** | 9 | `out_of_scope` | `none` | Do not place result statements, post-hoc findings, seed-selection narratives, sample-splitting descriptions, or threshold inconsistencies in model_representation_notes unless a specific schema limitation is identified. |

---

## Detail

### Structured factorial design and factor scope — 9 papers · `schema_gap`

ANCOVA, ANOVA, flexible-factorial, and longitudinal models included multiple factors and interactions, often distinguishing within-subject from between-subject factors; the full dependency structure was not captured.

- **Could an existing field hold it?** `none`
- **Change:** Add Analysis.factorial_design to encode each factor's within- or between-subject scope, levels, crossed interactions, and longitudinal structure.
- **Papers:** nmb_17923164, nmb_19136216, nmb_21949675, nmb_31972520, nmb_32125616, pmc_5671608, pmc_8209034, pmc_9245544, pmc_9954217

Examples:

> The second-level ANCOVA included a between-subjects factor group (MA vs HC), a within-subjects factor task (CC vs. IC), and age as a covariate.

> An ANCOVA design with a between-subjects factor group (MA vs HC), a within-subjects factor task (CC vs. IC), and age as a covariate.

> The ANCOVA included a between-subjects factor group, a within-subjects factor task, and age as a covariate.

### Explicit inference masking operations — 4 papers · `schema_gap`

Analyses used absolute-threshold masks, group-averaged functional masks, and exclusive masking of contrasts, details not covered by the existing correction-scope or search-volume fields.

- **Could an existing field hold it?** `none`
- **Change:** Add InferenceSettings.masking to record mask source, absolute or functional mask thresholds, and whether masking is inclusive or exclusive for each contrast.
- **Papers:** nmb_18801475, nmb_20731627, pmc_12132253, pmc_12316975

Examples:

> total gray matter volume was set as a covariate; an absolute threshold mask of 0.1 was used.

> The ANCOVA adjusted for known confounds of age and global GM; an absolute threshold mask of .1 and nonisotropic smoothness correction were also applied.

> The ANCOVA covaried for total GM volume and age; an absolute threshold mask of .1 was used.

### Connectivity time-series conditioning and lag settings — 2 papers · `schema_gap`

Bivariate models selected lag order using AIC, and conditional Granger causality conditioned on other regions with stringent across-subject significance requirements.

- **Could an existing field hold it?** `none`
- **Change:** Add ConnectivityDetails.time_series_model_settings to record conditioning variables, lag-order selection, and subject-level conjoint-significance requirements for Granger or related models.
- **Papers:** nmb_21106817, pmc_12316975

Examples:

> For each bivariate model, the optimal lag order was determined using the Akaike Information Criterion; group-level time series were used.

> Conditional Granger causality was computed by conditioning on IFS or mIPS; significant connections were required in four of five subjects and to have a conjoint probability <3.125 × 10-7 across subjects

### Bootstrap inference metadata — 1 papers · `schema_gap`

Inference used nonparametric bootstrap samples, including bias-corrected and accelerated confidence intervals, and sometimes omitted degrees of freedom because of bootstrap p-values.

- **Could an existing field hold it?** `none`
- **Change:** Add InferenceSettings.resampling_method with fields for bootstrap type, sample count, confidence-interval construction, and bias correction.
- **Papers:** pmc_10490010

Examples:

> Test statistics were reported for the original unsampled data, but degrees of freedom were not reported because p-values were estimated using nonparametric bootstrapping with 1,000 bootstrap samples.

> Two-way interactions between experimental and intervention groups were tested; bootstrap inference used 1,000 bootstrap samples with bias-corrected and accelerated confidence intervals.

### DCM model architecture and model reduction — 1 papers · `schema_gap`

The DCM comprised separate neuronal and observation models, and Bayesian Model Reduction switched candidate connections on and off.

- **Could an existing field hold it?** `none`
- **Change:** Add ConnectivityDetails.model_architecture to record neuronal and observation submodels, candidate connection sets, and Bayesian model-reduction or model-comparison procedures.
- **Papers:** pmc_9797690

Examples:

> DCM is comprised of two models: the neuronal model and the observation model. Bayesian Model Reduction was used to control the switching on and off of each connection.

### Task, covariate, and nuisance regressors — 28 papers · `guidance_problem`

The models included numerous age, sex, education, clinical, scanner, motion, global-signal, CSF, physiological, trialwise, and other nuisance or no-interest regressors.

- **Could an existing field hold it?** `Analysis.terms`
- **Change:** Prompt the extractor to create a Term for every covariate, nuisance regressor, motion or physiological regressor, trialwise regressor, and no-interest task regressor, and link them through Analysis.terms with the appropriate role.
- **Papers:** nmb_15585344, nmb_15668960, nmb_17923164, nmb_18095280, nmb_18801475, nmb_20385663, nmb_20731627, nmb_22332246, nmb_23021615, nmb_23982589 …

Examples:

> The “instruction” and “match” conditions were included in the model as predictors of no interest.

> Realignment parameters in all six dimensions were entered in the model.

> Age was included as a covariate.

### HRF convolution details — 12 papers · `guidance_problem`

Regressors were convolved with canonical, empirical, synthetic, gamma-variate, double-gamma, or other HRFs, sometimes with temporal derivatives or specified durations.

- **Could an existing field hold it?** `ModelEstimation.hrf_model`
- **Change:** Prompt the extractor to record the complete HRF basis, temporal derivative, convolution duration, and waveform in ModelEstimation.hrf_model, placing any remaining timing details in model settings.
- **Papers:** nmb_18095280, nmb_20112243, nmb_20172508, nmb_20385663, nmb_20840335, nmb_22956675, nmb_23825408, nmb_24517388, nmb_29890323, pmc_11426113 …

Examples:

> The instruction and match conditions were included in the model as predictors of no interest; temporal autocorrelation was corrected; the boxcar waveform was convolved with an empirically founded hemodynamic response function.

> Image onset was convolved with a canonical hemodynamic response function and its temporal derivative; six realignment parameters were entered; the data were high-pass filtered at 1/128 Hz and serial autocorrelation was corrected by an AR(1) model.

> Baseline parameters removed mean, linear, and quadratic trends and motion-related variance; React and Reappraise regressors were convolved with the Cohen’s gamma variate hemodynamic response function.

### Filtering, autocorrelation, orthogonalization, and model-error settings — 10 papers · `guidance_problem`

Analyses used high-pass filters, autoregressive or local-autocorrelation corrections, nonsphericity corrections, orthogonalized or nonlinear terms, stepwise/AIC selection, and other model-estimation settings.

- **Could an existing field hold it?** `ModelEstimation.model_settings`
- **Change:** Prompt the extractor to preserve high-pass filters, autocorrelation and nonsphericity corrections, orthogonalization, spline terms, model-selection rules, and related estimation settings in ModelEstimation.model_settings.
- **Papers:** nmb_20112243, nmb_21106817, nmb_21949675, nmb_23825408, nmb_24517388, nmb_25112281, nmb_29890323, pmc_12316975, pmc_6543522, pmc_9797690

Examples:

> Baseline parameters to remove mean, linear, and quadratic trends, and motion-related variance in the BOLD signal

> The parametric regressor was orthogonalized to the main effect and a quadratic regressor was included.

> Response time was entered as the first and confidence as the second parametric regressor; confidence was orthogonalized with respect to response time.

### Fixed, random, and hierarchical effects — 6 papers · `guidance_problem`

Participants or sessions were modeled as random effects, analyses used fixed-effects and group-level random-effects stages, and some models specified subject and condition fixed effects or multiple hierarchical levels.

- **Could an existing field hold it?** `ModelEstimation.model_settings`
- **Change:** Prompt the extractor to record fixed-effects, random-effects, subject/session levels, and hierarchical variance-estimation details in ModelEstimation.model_settings, alongside ModelEstimation.level.
- **Papers:** nmb_20172508, nmb_21790899, nmb_28131862, pmc_10490010, pmc_12316975, pmc_4879128

Examples:

> Participants were modeled as random effects; six standard motion-correction regressors and third-order polynomial baseline detrending were included.

> Participants were modeled as random effects.

> participants (modeled as random effects)

### Image and time-series denoising or normalization operations — 4 papers · `guidance_problem`

The papers reported global-signal regression, resampling physiological measures to the fMRI TR, cerebellar normalization, and derivation of PET binding-potential images.

- **Could an existing field hold it?** `Preprocessing.steps`
- **Change:** Prompt the extractor to record global-signal regression, physiological time-series resampling, cerebellar normalization, and binding-potential image calculations as ordered Preprocessing.steps.
- **Papers:** nmb_15668960, nmb_15695781, pmc_12316975, pmc_8455857

Examples:

> The results in this analysis were obtained from data analyzed with global signal regression (GSR).

> Binding potential images were calculated by dividing radioactivity concentration images collected between 60 and 90 minutes after injection by mean cerebellar radioactivity concentration.

> IBI and RMSSD time-series were resampled at the fMRI TR using a 20 s sliding window.

### Generalized PPI regressor architecture — 3 papers · `guidance_problem`

Generalized PPI models contained multiple psychological regressors, a physiological seed regressor, separate interaction terms, original GLM regressors, and additional covariates.

- **Could an existing field hold it?** `Analysis.terms`
- **Change:** Prompt the extractor to represent each psychological, physiological, and interaction/PPI regressor as a Term, using Term.interaction_with for the product terms and linking all of them through Analysis.terms.
- **Papers:** nmb_20385663, nmb_27998996, pmc_9245544

Examples:

> Generalized PPI included HAP, HAN, LAP, LAN, and FIX psychological regressors, a physiological regressor, and separate interaction terms; this full regressor structure is not otherwise represented.

> The generalized PPI model included psychological regressors, the physiological variable of the region of interest, and PPI variables for HAP, HAN, LAP, LAN, and FIX.

> Age was included as a covariate; generalized PPI included psychological regressors, a physiological variable, and PPI interaction terms.

### Hemisphere factor levels — 1 papers · `guidance_problem`

Hemisphere was a factor in a repeated-measures ANOVA but was not extracted as a factor level.

- **Could an existing field hold it?** `Term.levels`
- **Change:** Prompt the extractor to create a categorical Term for hemisphere and populate Term.levels with the reported hemisphere labels, linking it through Analysis.terms.
- **Papers:** nmb_18095280

Examples:

> The repeated-measures ANOVA included ROI, hemisphere, condition, and group factors; hemisphere is not otherwise represented as a factor level in the extracted entities.

### Decoding classifier configuration — 1 papers · `guidance_problem`

Decoding analyses used support-vector regression or classification with specified hyperparameters, run-wise patterns, confidence or decision-variable labels, cross-validation, and permutation-based null distributions.

- **Could an existing field hold it?** `Analysis.decoding`
- **Change:** Prompt the extractor to store classifier family, hyperparameters, feature-vector construction, label transformations, null-generation procedures, and run-wise training details in Analysis.decoding.
- **Papers:** nmb_25112281

Examples:

> Predicted decision-variable labels were transformed into predicted confidence values by removing run means and multiplying by the choice-sign vector; all 120 run permutations per subject were used to generate the null distribution.

> A linear support vector regression model with nu = 0.5 and cost c = 1 was trained using 30 pattern vectors and leave-one-run-out cross-validation; subject-specific maps were transformed to MNI space and smoothed.

> The analysis used a linear support vector regression model with nu = 0.5 and c = 1; six decision-variable regressors were modeled per run, corresponding to three confidence levels for each of two choices.

### Directed connectivity edges — 1 papers · `guidance_problem`

Both forward and backward connections between two prefrontal regions were evaluated.

- **Could an existing field hold it?** `ConnectivityEdge.directionality`
- **Change:** Prompt the extractor to create separate directed ConnectivityEdge records and populate ConnectivityEdge.directionality for forward and backward connections.
- **Papers:** pmc_9797690

Examples:

> Both forward and backward connections between L-dlPFC and mPFC were evaluated; the text reports both forward and backward linear-regression effects.

### General methods summaries rather than representation gaps — 9 papers · `out_of_scope`

These notes mainly summarized nonsignificant results, follow-up tests, seed selection, median splits, ROI extraction, map averaging, outlier handling, condition aggregation, or inconsistent reported thresholds rather than identifying an unrepresentable model component.

- **Could an existing field hold it?** `none`
- **Change:** Do not place result statements, post-hoc findings, seed-selection narratives, sample-splitting descriptions, or threshold inconsistencies in model_representation_notes unless a specific schema limitation is identified.
- **Papers:** nmb_20385663, nmb_20840335, nmb_25112281, nmb_27177981, pmc_12132253, pmc_5702877, pmc_6414400, pmc_7306625, pmc_8455857

Examples:

> No significant regions were found in the group × haplotypes interactions of the right hippocampal network below a p < 0.05 threshold corrected by Monte Carlo simulation.

> The interaction was followed by post-hoc independent-samples t-tests and correlation with memory scores.

> The left inferior temporal gyrus was selected as a seed from clusters with significant between-group differences in ALFF, fALFF and ReHo.

---

## `not_structurable` payloads

Effects the extractor declined to structure at all — rare, and worth reading individually rather than clustering.

- **nmb_32125616**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **nmb_32125616**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **nmb_32125616**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **nmb_32125616**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **nmb_32125616**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **nmb_32125616**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **nmb_32125616**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **pmc_9954217**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **nmb_20385663**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **nmb_20385663**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **nmb_20385663**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **nmb_20385663**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **nmb_20385663**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **pmc_5671608**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **pmc_5671608**: `{"reason": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}, "explanation": {"extraction_status": "not_reported", "evidence": {"status": "not_applicable"}}}`
- **nmb_29428771**: `{"reason": {"extraction_status": "not_reported"}, "explanation": {"extraction_status": "not_reported"}}`
- **nmb_21106817**: `{"reason": {"extraction_status": "not_reported"}, "explanation": {"extraction_status": "not_reported"}}`
- **nmb_21106817**: `{"reason": {"extraction_status": "not_reported"}, "explanation": {"extraction_status": "not_reported"}}`
- **nmb_15701234**: `{"reason": {"extraction_status": "not_reported"}, "explanation": {"extraction_status": "not_reported"}}`
- **nmb_15701234**: `{"reason": {"extraction_status": "not_reported"}, "explanation": {"extraction_status": "not_reported"}}`
- **nmb_27567867**: `{"reason": {"extraction_status": "not_reported"}, "explanation": {"extraction_status": "not_reported"}}`
- **nmb_27567867**: `{"reason": {"extraction_status": "not_reported"}, "explanation": {"extraction_status": "not_reported"}}`
