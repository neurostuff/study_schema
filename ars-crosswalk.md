# Crosswalk: this storage schema ↔ CDISC Analysis Results Standard

> **Note on names.** This crosswalk was written when the storage side carried `Effect.terms` as
> `TermUse` records with a `TermRole`. Those collapsed into `Effect.cells` and `Effect.adjusted_for`
> (see
> [storage-schema-design-notes.md](storage-schema-design-notes.md#direction-has-one-home)).
> The mapping to ARS is unaffected in substance — `Analysis.orderedGroupings` still corresponds to
> the per-analysis selection over terms held once on the model — and the ARS comparison is in fact
> closer now, since `OrderedGroupingFactor` also pairs a factor pointer with a level rather than
> pointing at study entities.

Written against ARS v1 `model/ars_ldm.yaml` from
[cdisc-org/analysis-results-standard](https://github.com/cdisc-org/analysis-results-standard),
read directly rather than from documentation prose.

ARS is the closest peer this schema has. Both are LinkML, both describe analyses recovered from
documents rather than executed from data, and both had to decide where a shared definition ends
and a per-analysis selection begins. That makes it a useful check on the model/contrast split,
and a useful place to notice what each one refuses to represent.

The two are not in a producer/consumer relationship, so this is a crosswalk for design
comparison, not an executable map. Nothing here is imported.

---

## 1. The crosswalk

### Containers

| ARS | Here | Fit |
|---|---|---|
| `ReportingEvent` | `Study` | partial. Both own the analyses and every entity those analyses reference. ARS's unit is a reporting requirement (a CSR, an interim analysis); ours is a publication. A `ReportingEvent` is planned, a `Study` is found. |
| `ReportingEvent.methods : AnalysisMethod[]` | `Study.model_estimations : ModelEstimation[]` | **parity.** Shared method definitions, referenced by id, one per distinct method, reused across analyses. |
| `ReportingEvent.analysisGroupings : GroupingFactor[]` | `ModelEstimation.terms : ModelTerm[]` (categorical) | partial — see finding 3. Same job, attached at different levels. |
| `ReportingEvent.analysisSets`, `.dataSubsets` | — | no equivalent. See finding 6. |

### The split

| ARS | Here | Fit |
|---|---|---|
| `Analysis.methodId → AnalysisMethod` | `Analysis.model_estimation → ModelEstimation` | **parity.** Required reference from the analysis to the shared method. |
| `Analysis.orderedGroupings : OrderedGroupingFactor[]`<br>`{order, groupingId, resultsByGroup}` | `Effect.terms : TermUse[]`<br>`{term, role, association_direction}` | **parity of shape.** Both are a per-analysis selection over shared factor definitions, referencing by id, holding only what varies analysis to analysis. This is the closest independent confirmation of the split. |
| `GroupingFactor.groups : Group[]` | `ModelTerm.levels : FactorLevel[]` | **parity.** The factor owns its levels in both. |
| `OrderedGroupingFactor.order` | — | gap. See finding 5. |
| `OrderedGroupingFactor.resultsByGroup` | — | gap; we have no per-cell result to switch on. |
| `AnalysisMethod.operations : Operation[]` | — | gap. See finding 4. |
| `GroupingFactor.dataDriven` | — | minor gap. ARS distinguishes prespecified level sets from ones read off the data; we take whatever the paper states. |
| `GroupingFactor.groupingDataset` / `.groupingVariable` | `ModelTerm.assessment` / `.source_definition` | weak. Same question — where do the values come from — answered against a dataset column there and against an `Assessment` record or prose here. |

### Entities

| ARS | Here | Fit |
|---|---|---|
| `Group` (a level of a grouping, `NamedObject` + `WhereClause`) | `FactorLevel` **plus** the `Group`/`Condition`/`Timepoint`/`Arm` it points at | **structural disagreement + name collision.** See finding 2. |
| `AnalysisSet` (the population, by `WhereClause`) | `Analysis.groups : GroupTerm[]` | partial. Both say who was in the analysis. `GroupTerm` adds `n` and a direction; `AnalysisSet` adds executable criteria and no direction. |
| `DataSubset` | — | no equivalent. |
| `NamedObject{name, description, label}` | `name`/`description` repeated per class; `DirectionalTerm{label, role}` | idiom parity. ARS factored the naming slots into an abstract superclass; we just did the same thing for the four directional terms. |

### Results

| ARS | Here | Fit |
|---|---|---|
| `Analysis.results : OperationResult[]`<br>`{operationId, resultGroups, rawValue, formattedValue}` | `Analysis.tables`, `Analysis.statistical_maps` | partial. ARS stores the numbers inline, one per grouping cell. We store coordinates in `Table` and images in `StatisticalMap`. |
| `OperationResult.resultGroups : ResultGroup{groupingId, groupId, groupValue}` | — | gap. Nothing here ties a result value to its cell of the design. |
| `Operation` (one statistic produced) | `Effect` (one tested contrast) | weak, and the cardinality differs: an ARS `Analysis` has many operations and many results; ours has exactly one `Effect`. |
| `ReferencedOperationRelationship{referencedOperationRole, operationId, analysisId}` | `ModelTerm.interaction_with`, `ConjunctionComponent`, `Effect.path` | partial. One generic "this result is computed from that one" mechanism there; three specific ones here. |
| `Analysis.programmingCode`, `AnalysisMethod.codeTemplate` | `ModelEstimation.software`, `.model_settings` | weak. Actual code there, software name and version here. |

### No counterpart in either direction

| ARS only | Here only |
|---|---|
| `WhereClause` / `WhereClauseCondition` — executable selection criteria | `Effect.kind`, `DirectionalRole` — direction and what kind of effect it makes |
| `Analysis.reason` (`SPECIFIED IN PROTOCOL` / `IN SAP` / `DATA DRIVEN` / `REQUESTED BY REGULATORY AGENCY`) | `Effect.baseline`, `Effect.path` |
| `Analysis.purpose` (`PRIMARY` / `SECONDARY` / `EXPLORATORY OUTCOME MEASURE`) | `Measure`, `InferenceSettings`, `spatial_scope`, `spatial_unit`, `coordinate_space` |
| `Output`, `OutputDisplay`, `DisplaySection` — the whole presentation layer | `AnalysisDetails` subclasses — the method payload |
| `TerminologyExtension` / `SponsorTerm` — a formal escape hatch for controlled terms | evidence and span provenance (extraction side) |

---

## 2. Findings

### 1. The split is confirmed by the closest peer, independently

`ReportingEvent.methods` ← `Analysis.methodId` is `Study.model_estimations` ←
`Analysis.model_estimation`, and `Analysis.orderedGroupings` is `Effect.terms`: a per-analysis
list of `{reference, plus only what varies}` over definitions held once elsewhere. ARS reached
that arrangement for clinical trial reporting with no knowledge of this schema. Two document-
derived standards converging on the same shape is better evidence for it than the drift
measurement was, because it is independent of our corpus and our extractor.

The convergence extends to the reference mechanism: both use identifiers, not names and not
positions. BIDS Stats Models matches contrasts to design columns by *name string* and
NIDM-Results by *vector position*; ARS and this schema both use ids.

### 2. ARS merges the cohort and the level; we separate them, and we have to

ARS `Group` is one level of a grouping factor, defined by a `WhereClause` — `TRT01P = "Placebo"`.
Our `FactorLevel` is the level and our `Group` is the cohort, and the level *points at* the
cohort.

That looks like gratuitous indirection until you notice what a cohort carries here: sample
sizes, a recruitment funnel, demographics, diagnostic criteria, an `Arm`. Those are facts about
the people, true regardless of which factor a given analysis groups them by, and the same cohort
appears in several analyses under different factorings. ARS keeps that in `AnalysisSet` and in
the `WhereClause` on each `Group`, which works because the subject-level data is present and can
be re-queried. We are reading a PDF, so the cohort has to be a record with its own attributes.

The name collision is real and worth remembering when talking to anyone from the CDISC side:
**their `Group` is our `FactorLevel`, and their `AnalysisSet` is closer to our `Group`.**

### 3. The factor hangs off different things, and this is a genuine domain difference

ARS attaches `GroupingFactor` to the `ReportingEvent` — study-level, defined once, referenced by
any analysis. We attach `ModelTerm` to the `ModelEstimation` — per-model.

The consequence is unflattering at first glance: a study running three models that each include
diagnosis produces three `ModelTerm` records named "diagnosis", each with levels pointing at the
same two `Group` records. That is a thinner version of the duplication the model/contrast split
just removed one level up.

It is nevertheless right, because the two things are not the same thing. An ARS grouping is a
*stratification for reporting* — sex, treatment arm, visit — and genuinely belongs to the study
rather than to any one method. A `ModelTerm` is a *column of a design matrix*. Two models that
both include diagnosis have two diagnosis columns; they can differ in coding, in which levels
were entered, in whether the term was crossed with something else. A term cannot float free of
the model whose matrix it is a column of.

The tell is that ARS has no design matrix at all: `AnalysisMethod` is a set of `Operation`s
producing values (n, mean, SD, p), not a specification of a linear model. The parity in
finding 1 is a parity of *shape* — shared definitions, per-analysis selection — over different
*content*. ARS splits population-stratification from method. We split model terms from contrast.

The duplication is real but cheap: name, type and level labels, with the entities themselves
already shared at study level. Worth revisiting only if the corpus shows many studies with
several models over the same factors.

### 4. ARS decomposes a method into operations; we do not, and probably should not

`AnalysisMethod.operations` is an ordered list, each `Operation` producing one value, with
`ReferencedOperationRelationship` recording that one operation's result feeds another
(`NUMERATOR`, `DENOMINATOR`). A method is "count subjects, compute percentage, run the test".

We have nothing like it. `Analysis` has exactly one `Effect`, and `Effect.statistic` names the
statistic family and its degrees of freedom without producing a value at all.

That asymmetry follows from purpose. ARS has to reconstruct a table cell by cell, so every
number needs a provenance chain. We have to decide whether two maps are poolable, and a map is
one tested contrast. Adopting operations would mean modelling the arithmetic of a results table
that we do not store.

### 5. Ordering: ARS has it structurally, we keep it in prose

`OrderedGroupingFactor.order` is a required integer. We say instead, in `DirectionalTerm.role`
and again in `Analysis.definition`, to mark the extremes `greater` and `lesser`, leave everything
between them `included`, and "keep the ordering in `Analysis.definition`". That covers three
cases — an ordered contrast, a series of follow-ups, and the levels of an ordered factor — and
in all three the sequence survives only as prose.

Two things make this worth acting on rather than noting. The claim it loses is one a synthesis
would actually filter on: an *n*-back manipulation where 1-back, 2-back and 3-back are ordered
levels and the finding is a monotonic increase with load. The schema has already solved the
identical problem once — `Timepoint.order` is an integer "counting from 1", added because
`relation_to_intervention` could not separate several `post_intervention` occasions. That is
exactly the shape of the fix and exactly the argument for it.

The home is an `order` on `FactorLevel`: the levels of a factor are where ordering lives, and
unlike `DirectionalRole` it need not be squeezed into three values. **This is the one thing in
the crosswalk I would act on.**

### 6. `Analysis.reason` has no equivalent here, and the gap is substantive

ARS records whether an analysis was `SPECIFIED IN PROTOCOL`, `SPECIFIED IN SAP`, `DATA DRIVEN`,
or `REQUESTED BY REGULATORY AGENCY`, and separately whether it is a `PRIMARY`, `SECONDARY` or
`EXPLORATORY OUTCOME MEASURE`.

We record nothing about prespecification. For a schema whose whole purpose is deciding what may
be pooled, that is a conspicuous absence: a preregistered primary contrast and an exploratory
whole-brain sweep from the same paper are not equivalent evidence, and selective-reporting bias
is a first-order threat to any meta-analysis.

The counterargument is the one that removed `certainty`: a field that papers rarely support
becomes an extractor's guess. But this is different in kind. Preregistration status is often
stated plainly — a trial registration number, "preregistered at OSF", "exploratory analyses" as a
Results subheading — and unlike `certainty` it is a fact about the study rather than about the
extractor's confidence. `Analysis.reason` also degrades honestly to `not_reported`, which most
older papers would take.

Worth a decision. It is out of scope for the model/contrast work and is recorded here rather
than acted on.

### 7. Neither standard represents direction, except this one

ARS has no contrast, no weights, no greater/lesser. It reports a value per cell and leaves the
comparison to the reader of the display. BIDS Stats Models and NIDM-Results both have contrasts
but express them as numeric weight vectors.

This schema is unique in representing direction: `DirectionalRole` and the four-axis `Effect.kind`
derivation have no counterpart anywhere in the comparison set. That is not a gap on our side —
it is the thing the schema exists to make queryable, and the reason a normalized
`greater`/`lesser` was chosen over weights is that weights cannot be recovered from prose.

It does mean the crosswalk is lossy in a specific direction: an ARS record could be expressed
here only by discarding its values and inventing directions it does not carry, and an
`Effect` could be expressed in ARS only by discarding `kind`, `baseline`, and every role.

---

## 3. What this changed

Findings 1 and 2 confirm decisions already made. Findings 3, 4 and 7 are differences that follow
from the domains and stay as they are.

Both gaps were closed:

- **`FactorLevel.order`** (finding 5), an integer counting from 1, on the model of
  `Timepoint.order`. Populated only where the source states or plainly implies a sequence — a
  dose series, a load or difficulty series, an ordinal severity grading — and left unset for a
  nominal factor, where numbering the levels in the order they happened to be written would
  assert an ordinal structure the design does not have. The mapper copies it and never
  synthesizes it.

- **`Analysis.prespecification`** (finding 6): `preregistered` / `exploratory`, required so that
  silence is recorded as `not_reported` rather than left ambiguous, and **closed** — one of the
  few enumerations here with no free-text binding, because the two values and the silence
  partition the question rather than catalogue what sources report.

  It is deliberately *not* ARS's four-value `AnalysisReasonEnum`. `SPECIFIED IN SAP` and
  `REQUESTED BY REGULATORY AGENCY` are artifacts of a regulatory pipeline with no analogue in
  published papers; ARS carries them because it records who required an analysis, where this
  schema only needs to know whether the contrast was fixed before the data were seen. A contrast
  prespecified in a protocol but never publicly registered is `preregistered` on that reading —
  what matters for pooling is that it was fixed in advance, not where the plan was deposited.

  Two asymmetries are enforced in the map rather than left to an extractor: an absent value
  becomes `not_reported` and never `exploratory`, since a paper that does not mention registration
  has not called its own analysis post hoc; and `preregistered` requires the source to point at
  something that fixes the plan, since a registration covering a study does not by itself cover
  every contrast reported from it.
