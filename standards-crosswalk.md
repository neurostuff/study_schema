# Crosswalk: this storage schema ↔ BIDS Stats Models and NIDM-Results

Written against the [BIDS Stats Models specification](https://bids-standard.github.io/stats-models/)
and [NIDM-Results 1.3.0](http://nidm.nidash.org/specs/nidm-results_130.html), read at field level.
Companion to [ars-crosswalk.md](ars-crosswalk.md), which covers CDISC's Analysis Results Standard;
that document is referred to here as *the ARS crosswalk* and its findings are not repeated.

Neither standard is in a producer/consumer relationship with this schema, and nothing here is
imported. This is a crosswalk for design comparison, not an executable map. The two were named in
`ModelEstimation.see_also` from the beginning and never read against the schema; this is that
reading.

---

## 1. Three different things are being represented

Almost every difference below follows from one asymmetry, so it is worth stating once rather than
per row.

| | unit of representation | when it is written | can it be re-executed |
|---|---|---|---|
| **BIDS Stats Models** | a *recipe*: how to fit a model over a BIDS dataset | before fitting | yes — that is the point |
| **NIDM-Results** | a *record of an execution*: the PROV graph SPM/FSL/AFNI emitted | after fitting, by the tool | no, but every input is hashed |
| **CDISC ARS** | a planned analysis and its reported table cells | before and after | partly (`WhereClause`, `programmingCode`) |
| **here** | a *reported tested effect*, recovered from a published document | after publication, by an extractor | no |

BIDS Stats Models says so in its own words: it is "prescriptive (as in a recipe), rather than
descriptive (as in summary of already fit models)."

Three consequences run through everything that follows.

**A prescriptive standard has no results.** BSM has no threshold, no correction, no statistic
value, no direction of outcome — inference is the fitting tool's business. So BSM is *less*
expressive than both this schema and NIDM-Results across the whole inference surface, and the
comparison there is one-sided.

**A tool-exported standard has no silence.** Neither BSM nor NIDM has any counterpart to
`not_reported`, because a pipeline that ran knows what it did. Every field in NIDM-Results is either
present or the exporter chose not to emit it, and there is no way to say "the source did not
report this." That single fact accounts for a whole class of fields this schema has and they
cannot.

**A standard bound to data does not need semantics in the document.** BSM `Model.X` entries are
column names in `events.tsv` and `participants.tsv`; NIDM is explicit that design-matrix regressor
names are *abstract identifiers*. What a regressor means is recoverable by opening the data. This
schema has no data, so the meaning has to be in the record — which is why `FactorLevel` points at
typed study entities and `ModelTerm` carries `variation_level`, `unit`, and `assessment`.

**A subject-matter vocabulary is not a fourth row.** ONVOC, Cognitive Atlas, CogPO, BrainMap's
taxonomy, MeSH and UBERON are a different kind of artifact: they supply *values*, where the three
standards above supply *structure*. None of this table's columns means anything for a SKOS concept
scheme, so they are deliberately not crosswalked here, and no field in this schema binds one —
[storage-schema-design-notes.md §"Conditions belong to a task"](storage-schema-design-notes.md)
gives the reason. What a vocabulary can be checked against instead is whether a field exists whose
free-text value a later normalization step could map onto it, which is a per-field audit rather
than a crosswalk; [onvoc-mapping-audit.md](onvoc-mapping-audit.md) is that audit for ONVOC.

---

## 2. The crosswalk

### 2.1 BIDS Stats Models

#### Containers and the stage graph

| BSM | Here | Fit |
|---|---|---|
| `BIDSStatsModel{Name, BIDSModelVersion, Description, Input, Nodes, Edges}` | `Study` + its `ModelEstimation` records | weak. A BSM document is one model spanning every stage; a `Study` is a publication containing many models. |
| `Input` (entity filter selecting the data) | `Analysis.acquisitions`, `.tasks` | partial. Both say what the model ran on — an entity query there, a reference to a described protocol here. |
| `Node{Name, Level, GroupBy, Transformations, Model, Contrasts, DummyContrasts}` | `ModelEstimation` | **the central row, and now largely parity.** A Node is one stage and so is a `ModelEstimation`; what a Node holds and this does not is `GroupBy` and `Transformations`. See finding 5. |
| `Node.Level` ∈ `Run`, `Session`, `Subject`, `Dataset` | `ModelEstimation.stage` (free text) | weak, and deliberately. Same concept, closed vocabulary there and open string here — the stage *order* is `inputs_from`, so nothing reads the label. |
| `Node.GroupBy` | — | no equivalent. How inputs partition into estimation units is not represented. |
| `Edges[{Source, Destination, Filter}]` | `ModelEstimation.inputs_from` | partial. Carries "this stage consumed that stage," pointing from consumer to consumed; `Filter`, which of the source's contrasts feed on, has no counterpart. See finding 5. |

#### The model

| BSM | Here | Fit |
|---|---|---|
| `Model.X` (design matrix variables) | `ModelEstimation.terms : ModelTerm[]` | **parity of role, and this schema is richer.** Both are the columns. `X` is a list of name strings; `ModelTerm` carries type, levels, unit, provenance and variation level. |
| `Model.Formula` (Wilkinson notation) | `ModelTerm.interaction_with`, `.functional_form` | partial. A composable algebra there — nesting, expansion, crossing — against two slots here. See finding 8. |
| `Model.Type` ∈ `glm`, `meta` | `ModelEstimation.model_family` (open) + which `AnalysisDetails` subclass is filled | partial, in this schema's favour. Two values there; seven families and eight method payloads here. |
| `Model.HRF{Variables, Model, Parameters}` | `ModelEstimation.hrf_model` (free text) | weak. Structured basis and parameters there, one string here. |
| `Model.Options` (e.g. high-pass cutoff, mask, serial-correlation handling) | `ModelEstimation.model_settings` (free text) | weak. See finding 7. |
| `Model.Software` | `ModelEstimation.software`, `.estimator` | **parity.** |
| `Transformations{Transformer, Instructions}` | — | no equivalent. See finding 8. |

#### The contrast

| BSM | Here | Fit |
|---|---|---|
| `Contrast{Name, ConditionList, Weights, Test}` | `Effect{cells, statistic}` + `Analysis.name` | **parity of shape, divergence of content.** Both select over the model's columns. `ConditionList` + `Weights` is a name-keyed numeric vector; `cells` is a list of `{term, level, direction}`. |
| `Contrast.ConditionList` (matched to `X` by **name string**) | `Cell.term` (matched by **identifier**) | this schema is more robust. Carried over from the ARS crosswalk's finding 1. |
| `Contrast.Weights` | `Cell.direction` (`positive`/`negative`/`undirected`/`held`) | **deliberate divergence.** Signs, not magnitudes. See finding 6. |
| `Contrast.Test` ∈ `t`, `F` (plus a pass-through that runs no test) | `Statistic.family` (open: `t`, `z`, `f`, `chi_square`, `likelihood_ratio`, `beta`, `correlation`) | this schema is broader, as it must be: papers report statistics BSM has no reason to prescribe. |
| `DummyContrasts{Contrasts, Test}` | — | not applicable. A convenience for generation; there is nothing to generate from a document. |
| — | `Effect.mediation`, `Analysis.prespecification`, `Measure`, `InferenceSettings`, `Region`, `spatial_scope`, `spatial_unit` | no counterpart in BSM. |

### 2.2 NIDM-Results

#### The provenance chain

| NIDM-Results | Here | Fit |
|---|---|---|
| `ModelParameterEstimation` (activity) *used* `Data`, `DesignMatrix`, `ErrorModel`, `MaskMap` | `ModelEstimation` | **parity of role.** Both are "how the model was fit," shared by every contrast taken from it. |
| `ContrastEstimation` (activity) *used* `DesignMatrix`, `ParameterEstimateMap`, `ContrastWeightMatrix` | `Effect` | **parity of role.** One per reported contrast in both. |
| `Inference` (activity) *used* `StatisticMap`, thresholds, criteria | `Analysis.inference_settings` | partial, and the cardinality differs. A separate act there, an inlined single-valued object here. See finding 4. |
| the PROV edges themselves (`used`, `wasGeneratedBy`) | implicit in the reference structure | weak. Traversable there; here the chain is recovered from which record points at which. |
| `Data{grandMeanScaling, targetIntensity, hasMRIProtocol}` | `Acquisition`, `Preprocessing` | partial. Global scaling and target intensity have no structured home here. |
| `StudyGroupPopulation{groupName, numberOfSubjects}` | `AnalysisGroup{group, n}` + `Group` | this schema is far richer — `Group` carries a recruitment funnel, demographics, clinical characterization. |

#### The design matrix

| NIDM-Results | Here | Fit |
|---|---|---|
| `DesignMatrix{regressorNames, hasHRFBasis, hasDriftModel, format, fileName}` | `ModelEstimation.terms`, `.hrf_model` | partial. NIDM says outright that `regressorNames` are *abstract identifiers*; the matrix itself is attached as a file. This schema has no file and so has to carry the semantics. |
| `hasHRFBasis` (a closed ontology: canonical, gamma-difference, gamma, Gaussian, FIR, Fourier, spline, sine, derivatives, custom) | `hrf_model` (free text) | weak. See finding 7. |
| `hasDriftModel` (`DCTDriftModel{cutoffPeriod}`, `GaussianRunningLineDriftModel{cutoffPeriod}`) | — | gap, folded into `model_settings`. See finding 7. |
| `ErrorModel{errorDistribution, errorVarianceHomogeneous, errorDependence, varianceMapWiseDependence, dependenceMapWiseDependence}` | `ModelEstimation.model_settings` (free text) | **gap.** See finding 7. |
| `withEstimationMethod` (OLS, GLS, WLS, IRWLS) | `ModelEstimation.estimator` | **parity.** |
| `ContrastWeightMatrix{contrastName, statisticType, value}` | `Effect.cells` + `Statistic.family` | deliberate divergence, as with BSM. See finding 6. |

#### Inference and results

| NIDM-Results | Here | Fit |
|---|---|---|
| `StatisticMap{statisticType, contrastName, effectDegreesOfFreedom, errorDegreesOfFreedom, ...}` | `Statistic{family, degrees_of_freedom_numerator, degrees_of_freedom_denominator}` | **parity**, and closer than it looks: NIDM's effect/error split is exactly the numerator/denominator split, for the same reason. |
| `hasAlternativeHypothesis` ∈ one-tailed, two-tailed | — | **gap.** See finding 3. |
| `HeightThreshold{value, correctionMethod, equivalentZ, pValue}` | `InferenceSettings.height_threshold_value`, `.height_threshold_type`, `.multiple_comparison_method` | partial, and the attachment differs. NIDM binds the correction to the threshold; here it floats beside several thresholds. See finding 2. |
| `ExtentThreshold{value, equivalentZ, pValue}` | `.cluster_extent_threshold`, `.clusterwise_threshold_value` | partial, same attachment problem. |
| `ClusterDefinitionCriteria{clusterConnectivityCriterion}` | `.neighborhood_definition` | **parity.** |
| `PeakDefinitionCriteria{peakStrategy, minDistanceBetweenPeaks, maxNumberOfPeaks}` | — | no equivalent, and appropriately: a table's peaks are what the authors chose to print. |
| `SearchSpaceMaskMap{searchVolumeInVoxels/InUnits/InResels, reselSizeInVoxels, noiseFWHM, heightCriticalThresholdFWE05/FDR05, expectedNumberOfClusters}` | `.search_volume` (free text), `.number_of_tests` | **gap.** RFT quantification has no structured home. |
| `ExcursionSetMap` | `StatisticalMap{map_type, is_thresholded, url}` | partial. `is_thresholded: true` is roughly an excursion set. |
| `SupraThresholdCluster{clusterSizeInVoxels, clusterSizeInResels, pValueUncorrected, pValueFWER, qValueFDR}` | — (delegated to `Table`) | **gap within the schema.** See finding 1. |
| `Peak{coordinate, value, pValueUncorrected, pValueFWER, equivalentZStatistic}` | — (delegated to `Table`) | **gap within the schema.** See finding 1. |
| `Coordinate{coordinateVector, coordinateUnit}`, `CoordinateSpace{dimensionsInVoxels, voxelSize, voxelUnits, voxelToWorldMapping, numberOfDimensions, inWorldCoordinateSystem}` | `Table.coordinate_space` / `Analysis.coordinate_space` (string) | partial → gap. Only the world coordinate system survives. See finding 3. |
| `ParameterEstimateMap`, `ContrastMap`, `ContrastStandardErrorMap`, `ResidualMeanSquaresMap`, `GrandMeanMap`, `ReselsPerVoxelMap`, `MaskMap`, `DisplayMaskMap` | `StatisticalMap` (one class, `map_type` free text) | partial. Eight typed map classes there, one class with a type string here — right for a schema that records whichever maps a study happens to share. |
| per-map `sha512`, `format`, `location`, `MapHeader` | `StatisticalMap.url` | weak. No checksum. |
| `ConjunctionInference`, `PartialConjunctionInference{partialConjunctionDegree}` | `ConjunctionDetails{null_hypothesis, implementation, components}` | **mostly this schema's favour, with one exception.** `global_null`/`conjunction_null` and the implementation are richer than NIDM's split; NIDM's *u*-of-*n* degree has no home here. |

### 2.3 No counterpart in any of the three

| BSM / NIDM only | Here only |
|---|---|
| `Node.GroupBy`, `Edges` — the stage graph | `Cell.direction` as the sign of a *fitted* coefficient |
| `Transformations` — a variable-manipulation DSL | `FactorLevel.{conditions, groups, timepoints, arms}` — typed level referents |
| `Model.Formula` — a composable design algebra | `ModelTerm.variation_level`, and the `EffectKind` derivation |
| `ErrorModel`, `hasDriftModel` | `Measure{family, type, source_label, specific_metric, unit}` |
| `Peak`, `SupraThresholdCluster`, `Coordinate` values | `Region.definition_method` — independence as a claim |
| `sha512` and file-level integrity | `Analysis.prespecification` |
| `hasAlternativeHypothesis` | `Effect.mediation{path, mediator}` |
| `SearchSpaceMaskMap` RFT quantities | `DecodingDetails`, `SimilarityDetails`, `ConnectivityDetails`, `LatentDecompositionDetails` |
| `CoordinateSpace` geometry | `NotStructurableDetails`, `model_representation_notes`, `undirected` vs `held` vs a `not_reported` direction |

---

## 3. Findings

### 1. On results, NIDM-Results is far more expressive, and this is a boundary rather than a gap

`ExcursionSetMap` → `SupraThresholdCluster` → `Peak` → `Coordinate` is a complete, typed,
queryable results payload: cluster size in voxels *and* resels, corrected and uncorrected
*p*-values at both cluster and peak level, equivalent *z*, coordinate vectors with units. The
analysis module here has none of it. `Table` carries a caption, a footer, column headings and a
`coordinate_count`; the points themselves live in the repository's coordinate representation,
reached through `source_path`.

Worth stating plainly, because it is easy to miss while reading the schema: **there is no effect
size, *p*-value, or confidence interval anywhere on an `Analysis`.**
`PerformanceMetric.value` — a decoding accuracy — is the only reported number in the entire
analysis module. ARS stores its numbers inline in `OperationResult`; NIDM stores them per peak and
per cluster; this schema stores none.

That is a boundary, not an oversight: the module's job is to decide whether two maps are poolable,
and the coordinates are handled by a layer built for them. It is worth recording because the
boundary is invisible from inside `analysis.yaml`, and because it means a query that filters on
statistic values cannot be answered from this module alone.

### 2. The correction method floats free of the threshold it corrected

NIDM attaches `correctionMethod` to a `HeightThreshold` or `ExtentThreshold`, so each threshold
carries its own. `InferenceSettings` has three threshold slots — `height_threshold_value`,
`clusterwise_threshold_value`, `cluster_extent_threshold` — and **one**
`multiple_comparison_method` beside them.

The commonest sentence in the fMRI literature has two corrections in it: *voxelwise p < 0.001
uncorrected, clusterwise FWE-corrected p < 0.05*. That is `none` at the voxel level and `FWE` at
the cluster level, and there is one field for both. An extractor must pick one, and whichever it
picks, the record asserts something false about the other threshold.

This was found while writing the crosswalk rather than being in the plan, and it is the cheapest
of the gaps to close: a small `Threshold` class with `{level, value, type, correction_method}`,
multivalued on `InferenceSettings`, subsumes all three current slots and the correction. It is also
the most invasive to `extraction-to-storage.map.yaml` for the same reason.

Half of it has since been done, for a different reason. `voxelwise_threshold_value` and
`cluster_forming_threshold_value` were one fact under two names — the height threshold, called after
whichever of its two jobs a paper was describing — and no record in the corpus ever filled both, so
they are now one `height_threshold_value`. That leaves the level count at three and the correction
still floating; the `Threshold` class remains the fix for the attachment.

### 3. Two facts a meta-analysis needs are not recorded: tailedness and voxel size

**Tailedness.** NIDM has `hasAlternativeHypothesis` — one-tailed or two-tailed. Nothing here does.
This is not a reporting nicety: converting a reported *p* to a signed *z* requires knowing the
tail, and that conversion is a routine step in coordinate-based meta-analysis. `Cell.direction`
implies a one-tailed test in the ordinary case and an `omnibus` implies two, but neither is a
statement, and papers do report two-tailed directional tests. A closed
`one_tailed`/`two_tailed` enum on `Effect` would cost one slot.

**Voxel size.** NIDM's `CoordinateSpace` carries `dimensionsInVoxels`, `voxelSize`, `voxelUnits`,
`voxelToWorldMapping` and `numberOfDimensions` alongside the world coordinate system. Here,
`coordinate_space` is a string naming the template — the world system and nothing else. The
consequence is direct: `InferenceSettings.cluster_extent_threshold` is documented as a count of
"voxels, vertices, or the unit stated in the source," and a 20-voxel cluster at 2 mm isotropic and
a 20-voxel cluster at 4 mm differ eightfold in volume. The threshold as recorded is not comparable
across studies, which is what the field exists for.

`Acquisition.acquisition_voxel_size_mm` is not the answer: it is the *acquired* resolution, before
normalization, and analyses are thresholded in the resampled space.

### 4. NIDM separates estimating a contrast from drawing inference on it; here they are one record

`ContrastEstimation` and `Inference` are distinct PROV activities, so *n* inferences hang off one
contrast estimate. `Analysis.inference_settings` is inlined and single-valued.

A paper that reports one contrast at whole-brain FWE **and** at an uncorrected exploratory
threshold — routine, and often the whole point of a supplementary table — has two inferences over
one effect. The schema's options are to mint two `Analysis` records that duplicate the effect, the
sample, the model reference and the measure, or to drop one threshold. Neither is right, and the
separate-Analysis rule in `Analysis.description` does not cover this case: it lists direction,
group, method, cell pattern, seed, decoded variable, component identity and spatial scope, and
thresholding is not among them, correctly — two thresholdings of one map are not two tested
effects.

Making `inference_settings` multivalued is the small fix and breaks nothing. Note that it composes
with finding 2 rather than substituting for it: multivalued `InferenceSettings` fixes *two
inferences on one contrast*, and a `Threshold` class fixes *two corrections within one inference*.

### 5. The stage graph is represented after all — the edges, not the recipe

**Reversed.** This finding used to read "the flattening is deliberate" and sat on the
not-to-be-relitigated list. `ModelEstimation.inputs_from` was added because the price it recorded
came due; what follows is what changed, and what did not.

BSM models the hierarchy explicitly: `Node.Level` ∈ `Run`/`Session`/`Subject`/`Dataset`,
`Node.GroupBy` saying how inputs partition into estimation units, and
`Edges[{Source, Destination, Filter}]` saying which node's contrasts feed which. A three-stage
analysis is three nodes and two edges.

Here, each stage the source describes takes a `ModelEstimation` and the higher one names the lower
in `inputs_from` — Edges without `Filter`. A model's terms are its own plus, transitively, those of
the stages it names, so the mixed term list is unmixed and every derivation reads the whole chain.

**What forced it.** The old defence was that a paper states its group-level design matrix and
rarely its first-level one in enough detail to constitute a separate model, so two linked records
would come out one populated and one full of `not_reported`. Extraction found the opposite often
enough to matter. In pmid 24600410 the model produced exactly two records — a FEAT first level
holding the amygdala seed regressors and the WM/CSF/motion nuisance covariates, and a FLAME group
model holding diagnosis, age, sex and scanner — and with no link between them the first was
referenced by nothing, no `Cell` could name the seed the maps were *of*, and all four contrasts
derived as unadjusted for motion. The mixing this finding called "not inert" is the same problem
seen from the other side: whether the stages are flattened into one record or split into two
unlinked ones, the adjustment set comes out wrong, and the schema had no third option.

The cheapness of the fix is the other half of the argument. `inputs_from` is optional and adds one
slot: a paper that describes one design matrix still takes one record, and `level` is still free
text with no ordering read from it.

Still not represented, and still deliberately:

- `GroupBy` has no counterpart, so "one model per run, then averaged within subject" and "one model
  over concatenated runs" remain the same record. That is a statement about how estimation units
  partition, which is recipe rather than result.
- BSM's `Edges[].Filter` — *which* of a node's contrasts feeds the next — has no counterpart
  either. The link says the stage was consumed, not which output of it was.
- There is still no general "this result was computed from that one" mechanism. ARS has one
  (`ReferencedOperationRelationship`); this schema now has four specific ones — `interaction_with`,
  `ConjunctionComponent`, `Mediation.mediator`, and `inputs_from`. `inputs_from` relates *models*;
  the analysis→analysis case, where a second model is fitted to a first analysis's map, is F3 in
  the expressivity probe and remains open. The two are complementary: a first-level model no paper
  reports a contrast from has no Analysis for an analysis→analysis link to point at.

### 6. Signs instead of weights: the trade, and its price

BSM `Contrast.Weights` and NIDM `ContrastWeightMatrix.value` are numeric vectors.
`Cell.direction` is a sign. `Direction`'s own description states the trade — `[1, -1/2, -1/2]` and
`[1, -1, 0]` over three levels are both "one positive level against two others" — and the
justification is that weights are not recoverable from prose. That is right, and both peers
confirm it is a real fork rather than an accident: nobody else records signs, and nobody else is
reading a PDF.

The price is worth writing down next to it, in one place:

- **A multi-row contrast matrix collapses.** An F-contrast in BSM or NIDM is a matrix, and its rank
  says how many comparisons it spans. Here an omnibus F is a set of cells with
  `direction: undirected`, which records *that* the factor was tested undirectionally but not
  which comparisons the test spanned. A 3-level factor's omnibus F and a specific 2-row subset of
  it are the same record.
- **Unequal weights collapse.** A linear trend across four dose levels and a comparison of the
  extremes are the same cells. `FactorLevel.order` recovers the sequence, which is what the ARS
  crosswalk added it for, but not the coefficients.

Both losses are bounded by what a paper reports, which is the argument for accepting them. Neither
is recorded anywhere in the schema at present.

### 7. On how the model was fit, NIDM is structured where this schema has prose

Three things NIDM makes queryable and `ModelEstimation.model_settings` holds as free text:

- `ErrorModel.errorDependence` ∈ independent, exchangeable, Toeplitz, compound symmetry,
  unstructured — with `errorVarianceHomogeneous` as a separate boolean, and
  `varianceMapWiseDependence` / `dependenceMapWiseDependence` saying whether each was estimated
  per element or pooled.
- `hasDriftModel` — a DCT basis with a cutoff period, or a Gaussian running line with one. BSM
  reaches the same fact through `Model.Options` (a high-pass cutoff in Hz).
- `hasHRFBasis` — a closed ontology of eleven basis sets, against `hrf_model` as a string.

Of the three, error dependence has the strongest claim on a slot: whether a repeated-measures
analysis assumed sphericity is a fact that decides comparability, papers do state it (a
Greenhouse-Geisser correction is already accommodated by `degrees_of_freedom_denominator` being a
float, which is a partial admission that this matters), and `model_settings` cannot be filtered on.

Drift and high-pass filtering are COBIDAS items and are reported often. `hrf_model` as free text
is defensible: the wording is what papers give, and the vocabulary is long-tailed.

### 8. On specifying a design, BSM is more expressive, and most of it does not transfer

`Model.Formula` is Wilkinson notation — crossing, nesting, polynomial expansion, all composable.
`Transformations` is a variable-manipulation DSL (`Scale`, `Threshold`, `Orthogonalize`,
`Convolve`, `Factor`, `Product`, `Sum`, `Lag`, `Split`). Against that, this schema has
`ModelTerm.functional_form` with four values plus an open string, and `interaction_with` for
products.

Most of the difference is inert here, because a paper does not report its transformation pipeline
and a schema cannot extract what is not written. Two exceptions:

- **Orthogonalization of parametric modulators** *is* reported, does change what a coefficient
  means, and has nowhere to go — not `functional_form` (which is the term's shape, not its
  relation to other columns) and not `interaction_with` (which is what makes a column a product).
  It would currently land in `source_definition` as prose.
- **Random effects structure** is conceded rather than represented, which the schema says out
  loud: `model_representation_notes` names "random slopes, latent variables, dynamic connectivity,
  and dependencies between terms" as the things it exists to record the absence of. That is the
  honest version of the gap, and it is a better answer than a half-built `Formula` would be.

Also absent, and following from the same root: **event timing**. BSM works from `events.tsv`
onsets, durations and amplitudes. `Condition` here is a name and a description; `Task.design_type`
is a free-text "event-related, block, mixed, or naturalistic." A block design and an event-related
design of the same contrast are distinguishable; the timing is not.

### 9. Where this schema is more expressive than both, and why each one had to be

Ranked by how much of the schema's purpose depends on it rather than by size.

1. **Direction of a fitted coefficient.** All three peers record the contrast *specification*. On
   a continuous term, `Cell.direction` is the sign the coefficient came out with — an outcome, not
   a specification. With `PerformanceMetric.relation` beside it, this schema is the only one of the
   four that answers "which way did it go." It has to be, because that is the finding a paper
   reports and a synthesis pools. `Cell.direction` is the whole of it: the connectivity payload used
   to carry a whole-map sign of its own, and two routes to one direction is one too many.
2. **Typed level referents.** `FactorLevel.{conditions, groups, timepoints, arms, regions}` says what a
   level *is*. No peer can distinguish a cohort comparison from a pre-post change from a crossover
   comparison structurally: BSM has column-name strings, NIDM has declared-abstract identifiers,
   ARS has an untyped `WhereClause`.
3. **`ModelTerm.variation_level`**, and therefore the parametric-modulation vs
   cross-subject-regression distinction. Absent from all three, and it separates two effects that
   are otherwise the same record.
4. **The `EffectKind` derivation.** No peer answers "what kind of effect is this" at all. With
   weights and a design matrix you could work out a crossing; without `variation_level` you could
   not reach the modulation/regression split at any effort.
5. **Method families beyond the mass-univariate GLM.** NIDM-Results is mass-univariate only. BSM
   `Type` is `glm` or `meta`. The seven `AnalysisDetails` subclasses — decoding with per-metric
   `reference_value`, RSA, connectivity with `EdgeDirectionality` and `InferenceTarget`, latent
   decomposition with `second_block` and `PolaritySemantics` — have no counterpart anywhere in
   the comparison set.
6. **`Measure`.** The scientific quantity of the map. For BSM and NIDM it is implicit in the input
   files; ARS has no imaging measure. A schema deciding poolability cannot leave it implicit —
   grey-matter volume and grey-matter density are deliberately not collapsed here.
7. **`Region.definition_method` as an independence claim.** `same_study_analysis` marks a circular
   ROI, and `Analysis.defines_regions` names the analysis it was drawn from, so the circularity is
   checkable rather than asserted. No peer represents non-independence in any form, and it is a
   first-order threat to the validity of a pooled estimate.
8. **The epistemic layer.** `prespecification`; `interpretations`, marked as inferred by which slot
   it sits in; `undirected`, `held` and a `not_reported` direction as three different facts; `NotStructurableDetails`;
   `model_representation_notes`; `reason_first_class_type_not_used`. None of this can exist in a
   prescriptive or tool-exported standard, because neither has an author who might not have said.
   ARS's `reason` and `purpose` are the only partial peers, and the ARS crosswalk already settled
   why `Prespecification` is not `AnalysisReasonEnum`.
9. **`Effect.mediation`** — direct, indirect, total. Without it an indirect-effect map is
   structurally identical to a regression on the same predictor.

One thing carried forward from the ARS crosswalk, now confirmed across the full set: on reference
mechanism, **BSM binds contrasts to design columns by name string, NIDM by vector position, ARS
and this schema by identifier.** Of the four, the two document-derived LinkML schemas are the two
that use ids.

---

## 4. What this leaves open

Nothing here was acted on. Five candidates, in the order their claim on the schema's stated purpose
runs strongest to weakest:

| gap | finding | candidate shape | cost |
|---|---|---|---|
| correction method per threshold | 2 | a `Threshold{level, value, type, correction_method}` class, multivalued | one class; subsumes four slots; the largest map change |
| tailedness | 3 | `Effect.alternative_hypothesis`: `one_tailed`/`two_tailed`, closed | one slot, one enum |
| *n* inferences per contrast | 4 | make `inference_settings` multivalued | loosens a cardinality; breaks nothing |
| voxel size of the analysed space | 3 | `voxel_size_mm` on `Table`, or a `CoordinateSpace` class | one slot, or one class done properly |
| error dependence | 7 | `error_dependence` + `error_variance_homogeneous` on `ModelEstimation` | two slots, one enum |

Settled and recorded, not to be relitigated: signs rather
than weights and the two losses that follow (finding 6), `Formula` and `Transformations`
(finding 8), event timing (finding 8), and the delegation of all result values to the coordinate
layer (finding 1).

Noted and not worth a slot: NIDM's `partialConjunctionDegree`, grand-mean scaling and target
intensity, per-map checksums, RFT search-space quantities, and `PeakDefinitionCriteria`.
