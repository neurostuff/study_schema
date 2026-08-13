# Extraction rules and conventions

Rules that govern extraction but cannot be stated in the schema itself. Three kinds live here:
gates that decide whether a paper is extracted at all, conventions the schema's LinkML cannot
express, and known limits worth knowing before someone rediscovers them.

The schema files are the source of truth for *what* a field holds; this file covers *how* the
pipeline is expected to behave around them. Reasoning behind the schema's shape is in
[storage-schema-design-notes.md](storage-schema-design-notes.md); measured expressivity gaps
are in [storage-schema-expressivity-probe.md](storage-schema-expressivity-probe.md).

---

## 1. Gates — papers that are not extracted

### Meta-analyses are skipped

A meta-analysis pools published effects. Its analyses have no participant sample, no
acquisition, and no measure of the study's own — the three things an `Analysis` is built
around — so recording one means inventing a cohort. Skip the paper instead.

The gate runs on PubMed metadata **before any text is read**, in two parts:

1. **Publication type.** Skip when `Study.study_type` (the PubMed publication types, fetched
   from E-utilities by PMID) includes `Meta-Analysis`.
2. **Title.** Skip when the title contains "meta-analysis", case-insensitive.

Both are needed. Measured against 200 corpus papers whose own methods say they ran a
meta-analysis:

| | n | note |
|---|---:|---|
| PubMed labels `Meta-Analysis` | 62 | 53 of these (85%) also say so in the title |
| Unlabelled, **title says** meta-analysis | 6 | what the title gate catches |
| Unlabelled, title does not | 132 | primary studies *running* a meta-analysis as a step — correctly kept |

So the type label is precise, and its miss rate among genuine meta-analysis papers is roughly
6 in 68 (~9%); the title gate closes that. On a random sample of 250 corpus records, 2.8% carry
the `Meta-Analysis` type.

### An unindexed record means "unknown", not "pass"

13.6% of a random 250-record sample had **no MeSH headings**, meaning the record has not been
MEDLINE-indexed — typically a recent paper. Every record carries at least `Journal Article`, so
an unindexed record looks superficially complete while its type list is incomplete. Treat the
absence of `Meta-Analysis` on an unindexed record as unknown rather than negative: rely on the
title gate, and re-run the type gate later once indexing catches up.

### Types that are not gates

PubMed also returns funding types — `Research Support, Non-U.S. Gov't` was on 174 of 300
sampled records. They are stored verbatim and carry no design information. `Systematic Review`,
`Review`, `Editorial`, `Letter`, and `Comment` describe papers with no analyses of their own;
they are not currently gated, but a paper of those types will simply yield no `Analysis`
records.

---

## 2. Conventions the schema cannot state

### A multivalued extracted fact is one wrapper holding a list

This is the convention most easily got wrong. A multivalued *source-derived* field is a single
`ExtractedValue` whose `value` is a list — **not** a list of `ExtractedValue`s:

```json
"authors": {"extraction_status": "extracted",
            "value": ["Novakova, Lubomira", "Gajdos, Martin"],
            "evidence": {"status": "present", "sets": [...]}}
```

One evidence record covers the whole list, because the list usually comes from one sentence.
The schema says so rather than leaving it to convention: a multivalued scalar projects to an
`Extracted<T>List` wrapper whose `value` is the list, so `multivalued` sits inside the
wrapper and a list of wrappers is not expressible. `Group.inclusion_criteria`,
`Preprocessing.steps`, `Preprocessing.smoothing_fwhm_mm`, `MRI.echo_time_seconds`, and
`Group.exclusion_criteria` all work this way, as does an enum list such as
`Task.response_mode` (`ExtractedResponseModeList`).
`ModelTerm.levels` no longer does: its entries carry their own entity references, so it is a
nested `FactorLevel` list rather than one wrapper over a list of labels. Cross-reference lists
are the exception: `Analysis.acquisitions`, `Analysis.tasks`, and every other local-ID list
ranges on the target class without `inlined`, and serializes as a list of that class's
`local_id`s, because a local ID is not a source-derived value and carries no evidence.

### Extraction has the same vocabularies storage does

It used to have none: every normalized field was an `ExtractedString` and the map's synonym
tables were the only route to a permissible value. The projection now carries the enums
themselves, and each field keeps storage's own range.

**A closed field takes a permissible value and nothing else.** `Acquisition.modality`,
`Cell.direction`, `ModelTerm.type` and ten others are bare enums in storage, so they are
bare here. Storage would reject any other answer, so there is nothing to gain by writing
one down.

**An open field is `any_of: [<Enum>, string]`, and the escape hatch is not a formality.**
Use the permissible value when one fits. When none does, write what the paper says rather
than forcing the nearest match — those free-text answers accumulating are the evidence for
whether the vocabulary is short a value. Twenty-three fields are open, including
`Analysis.spatial_scope`, `Measure.family`, and `Task.response_mode`.

Which a field is, is storage's decision and not one extraction may revisit;
`check_extraction_to_storage_map.py` fails if the projection opens or closes a vocabulary.

### Evidence invariants

Enforced by [review/validate_record.py](review/validate_record.py):

- `extraction_status: not_reported` ⇒ omit `value`, and `evidence.status` must be
  `not_applicable`.
- `evidence.status: present` ⇒ at least one `EvidenceSet`, each with at least one span.
- Every span satisfies `text == source[start_char:end_char]` against the normalized source
  text, half-open interval.
- Records are immutable. Do not hand-correct one; produce a new extraction version or a
  separate correction record.

### The method payload names itself

`Analysis.details` is one slot holding one payload, and `details_type` on that payload says
which kind it is — `DecodingDetails`, `ConnectivityDetails`, `ConjunctionDetails`,
`LatentDecompositionDetails`, `SimilarityDetails`,
`NotStructurableDetails`, `OtherAnalysisDetails`, or `MassUnivariateDetails`. `Acquisition`
works the same way: `acquisition_type` names `MRI`, `EEG`, `FNIRS`, `PET`, or `OtherModality`
and the modality-specific fields sit directly on the record.

**`MassUnivariateDetails` is the default**: a voxelwise, vertexwise, or ROI-wise GLM,
mixed-effects model, ANOVA, ANCOVA, or robust regression. It adds no fields of its own, so
naming it is the whole payload.

The extraction schema used to carry eight sibling slots here and five on `Acquisition`, with
"all eight empty" standing in for mass-univariate. Naming the type is one field instead of
eight and cannot be silently ambiguous, but it does ask the extractor to make a choice it
previously made by omission. If that turns out to extract worse, it is candidate 2 in
[extraction-deviations.yaml](extraction-deviations.yaml).

### An entity is declared once and referenced everywhere

A brain region the study delimited is one `Region` on `Study.regions`, however many analyses
use it, and each use names its `local_id`: `ConnectivityDetails.seed_regions` and
`target_regions`, `Analysis.regions` for a restricted search space,
`Analysis.defines_regions` for the analysis a functional ROI came *from*, `ModelTerm.region`
for a column carrying one region's signal, `FactorLevel.regions` for a factor comparing
places. The same move `Assessment` made, and `ModelTerm.assessment` with it.

**Deriving a column does not break the link to what supplied it.** `ModelTerm.region` says so
by example — an ROI mean and a PPI regressor are both computed from a region's signal and both
still name their region — and `ModelTerm.assessment` means it the same way: a change score or a
composite of subscales still names the instrument the numbers came from. What the derivation
*was* goes in `source_definition`, and there it is not optional: `FactorLevel.timepoints` names
the occasions a factor *compares*, a derived column has no levels to hang them on, so this is
the only place saying which occasions it spanned. A derived column with neither slot filled is
an unattributed number, and `validate_record.py` says so.

The region's own facts go on the `Region`: `name` in the source's wording,
`definition_method` for how it was delimited — `same_study_analysis` where it came from this
study's own earlier result — `atlas` for the parcellation, and `description` for the defining
sentence, which is where a sphere's centre and radius are kept.

**Occasions work the same way, and are the easiest to forget.** A `Timepoint` on
`StudyDesign.timepoints` is reached by exactly one slot, `FactorLevel.timepoints`, so a record
whose analyses report change over time and whose levels name no occasion has recorded the scans
and lost the comparison between them. The failing shape is a term like `pre > post change`,
continuous with no levels: the axis the design matrix distinguished, written down from its
contrast's side. [representing-models.md](representing-models.md) §5.6 is the encoding to use,
and the reason a study with no paradigm still has a factor.

Nothing constrains how an occasion is *named*. `Timepoint.name` is the source's wording and so
is the `FactorLevel.level` label pointing at it — `baseline`, `T0`, `week 12`, whatever the
paper writes. What is normalized is placement (`relation_to_intervention`) and sequence
(`order`), which carry every query an occasion vocabulary would have served.

### Cells carry direction; weights do not

Direction is recorded on the cell, never as a numeric contrast weight. One `Cell` is one level
of one `ModelTerm` and the side it entered on, and `Cell.direction` is the `Direction`
vocabulary: `positive`, `negative`, `undirected`, `unstated`, `held`. It is a **closed** field, so
translate the paper's comparative wording — "greater in", "reduced relative to", "increased" —
into one of the five rather than writing it down. There is no sixth answer; see §3.

This reverses what this section said while extraction had no enums, when the wording was
recorded verbatim and a 23-entry synonym table in the map did the translating.

Extraction used to spread direction across five per-axis slots, one each for group, condition,
timepoint, arm and factor, because comparative wording is per axis and that is what an
extractor can point at. Storage always had the one slot, and now so does extraction. It is
candidate 3 in [extraction-deviations.yaml](extraction-deviations.yaml) — the biggest of the
six, and the one most worth measuring, since the collapse asks the extractor to resolve an
entity to the model term whose level names it *and* to pick a direction in the same step.

For an ordered contrast, mark the extremes and leave intermediate terms undirected, keeping the
ordering in `Analysis.definition` — the middle rank is not recoverable from the directions (see §4).

**A level with no side is three different facts, and "included" names all three.** The
vocabulary separates them, and choosing between them is now the extractor's call rather than
something the mapper resolves from wording:

| the source says | record | why |
|---|---|---|
| a three-level factor compared at two of its levels | no cell at all | absence *is* the zero weight |
| compared directionally, but no direction given | a cell with `unstated` | a comparison was made; its sign was not printed |
| tested undirectionally — an F or χ² over the factor | `undirected` on **every** level | the test yields no per-level sign to print |
| the contrast was taken *within* this level | a cell with `held` | it is on both sides at once |

A held-constant level is unsigned because it appears on **both sides** of the comparison: "patients
versus controls, in the task condition" puts task on the plus side and the minus side at once, so it
has no net sign on its own axis. Marking it `positive` would assert a condition comparison the
contrast never makes.

**The three unsigned values are told apart by two questions.** First: was the level on both sides
at once? A held-constant level was, and no report could sign it, which is `held`. Otherwise: does the
test yield a per-level sign at all? An F or χ² does not — it returns one statistic for the whole
factor — which is `undirected`; a t or z whose direction the paper simply did not print does, and
that is `unstated`. A corollary: a cell naming no level — on a slope or a product column — cannot sit
on both sides of anything, so an undirected test of such a column is `undirected`, never `held`.

None of these is directional, so the derived kind is the same either way. What differs is what
the record claims — and each wrong guess fails differently. Omitting the cell for an unreported
direction denies a weight the contrast gave. Marking a held-constant level `unstated` asserts a
comparison the contrast did not make. Marking an F-tested factor's levels `held` says the opposite —
that the factor was held on both sides of its own test, which is a claim about every level at once
and so about none. And marking an F's levels `unstated` says a direction was withheld when the test
never produced one, which turns an omnibus into a contrast. And treating a held-constant level as averaged-over puts a term in the derived adjustment
set that the contrast never averaged, which is the failure that went unnoticed longest.

---

## 3. Invariants the structural validator must enforce

LinkML rules cannot express a constraint that spans a multivalued nested slot or a sibling of
the entity carrying it. Seven such constraints are documented on the fields they constrain and
have to be checked in code:

1. **`Effect.cells` must be non-empty.** An effect that compared nothing tested nothing, and a
   map reported with no inferential test is not an Analysis — see §1. LinkML's `required` catches
   an absent slot; a present-but-empty list needs the code check. This replaced "recompute
   `effect.kind` and reject contradictions": there is no stored kind to contradict, since the
   kind is read off the cells. The derivation is stated below.
2. **`Cell.term` must reference a `ModelTerm` of the `ModelEstimation` its `Analysis` names, or
   of a stage that model reaches through `inputs_from`.**
   This is what the old cross-class match between `FactorLevel.factor_name` and a categorical
   `Term.name` became. That match was unenforceable in LinkML and violated by 26 of 150
   factor-level entries in the alpha.11 corpus; the factor is now declared once, on the model,
   and the entities carrying each level hang off it, so the failure mode it guarded against is
   mostly structural rather than checkable. What remains to check is that a cell names a term of
   the right chain — see *Stages* below for why that is a chain rather than one record.
3. **A cell's `level` must agree with its term's `type`.** Required for a categorical term, and
   matching one of that term's declared `FactorLevel.level`s — the join is on the string, so
   compare under the same normalization the mapper applies to `FactorLevel`, and a level naming
   nothing on the model is a structural error rather than a new level to create. Absent for a
   continuous term: a level of a slope is not a thing, and a continuous term carrying the effect
   makes it a `parametric_modulation` or a `cross_subject_regression` by step 1 of the derivation
   rather than an axis to count.
4. **`Effect.mediation.mediator` must reference a `ModelTerm` of this analysis's model** — invariant 2 again, for the one other term pointer, and over the same chain.
5. **`Table.coordinate_space` and `Analysis.coordinate_space` are mutually exclusive** — see §4. Both populated is an error.
6. **`ModelEstimation.inputs_from` must be acyclic.** A model fitted on its own output is not a
   stage order. Every consumer of the term list walks this chain, so a cycle is a hang as well
   as a falsehood.
7. **A `ModelTerm.name` must be unique across a whole stage chain**, not merely within one
   record. `unique_keys` scopes per model, so without this a first-level `motion` and a
   group-level `motion` are two columns with one name in one term list, and a reader cannot tell
   a column refitted at the stage above from one restated there by mistake.

Two constraints that used to be here are now unrepresentable rather than checked. A term cannot be
both tested and controlled for — see *The adjustment set is derived* below. And a mediation path
cannot appear without its mediator: they were two slots on `Effect` that required each other, and
they are now two required slots inside an optional `Mediation`, so neither half can exist alone.

### The derivation reads the signs on each term

The kind of an effect is not a field. It is what reading the effect's `cells` produces, computed
where it is needed rather than stored anywhere. Cells each name a `ModelTerm` and, for a
categorical term, one of its levels — the **axis of a comparison is the term** — so:

> A term with **both a positive and a negative cell** is *crossed*: compared against itself.
> Count the crossed terms.
>
> - a **signed** cell on a term with non-empty **`interaction_with`** → `interaction`, before
>   anything below. An *unsigned* cell there is a multi-degree-of-freedom interaction F-test, which
>   falls through to `omnibus`
> - a cell on a **continuous** term → `parametric_modulation` or `cross_subject_regression` by
>   that term's `variation_level`, whatever else is signed
> - **2 or more** crossed terms → `interaction`
> - **1** crossed term → `contrast`
> - **0** crossed, but some cell signed → `simple_effect`
> - cells present, **none** signed → `omnibus`
> - **no cells** → unconstructable; `cells` is required, because an effect that compared nothing
>   tested nothing

`undirected`, `unstated` and `held` are not signs — a test with no sign to give, a sign the source
withheld, and a level with no sign to have. None of the three can cross a term.

Crossing, rather than mere signedness, is what an axis of a comparison is. Two levels of `region`
are one axis because they name one term. A cohort comparison, a condition contrast, a pre-post
change and a crossover comparison of arms are all one crossed term each, by the same rule and the
same code — what distinguishes them is what the crossed term's levels range over, which
`FactorLevel` states once on the model. A cell cannot carry a sign without naming the term it is a
sign of, so there is no per-slot case to get wrong and no grouping pass to forget.

**A lone signed cell is a test against the implicit baseline.** A single `+1` weight tests that
coefficient against zero, and zero is what the implicit baseline is. The sign still says activation
or deactivation, which the alternative — recording no cells at all — would throw away, while also
dropping the tested condition into the derived adjustment set.

This is why there is **no `Effect.baseline`**. Extraction still records the paper's baseline
wording, and the mapper routes each kind of reference to where storage already keeps it:

| the paper says | where it goes |
|---|---|
| rest, fixation, a control task | a `negative` cell on that condition's level |
| the implicit baseline, or zero | no second cell — the lone signed cell *is* the test against zero |
| chance, chance level | `PerformanceMetric.reference_value`, per metric |
| "versus baseline", unspecified | whatever cells the contrast yielded; the vagueness stays in the evidence |

The chance row is why a single baseline field could not have worked anyway: accuracy against chance
and AUC against 0.5 are two references on one analysis, and `reference_value` is per metric.

The modelled-condition row is the one that can fail. If the baseline condition is named in prose
but no model term has a level carrying it, there is no cell to make — report it as the structural
error it is, the same as any entity with no term. Do not invent the term.

Reading signs rather than counting terms settles three things the count rule could not:

| case | counting terms | reading signs |
|---|---|---|
| activation against an implicit baseline | `contrast` ✗ | `simple_effect` ✓ |
| cohort comparison of an activation contrast | `interaction` ✗ | `contrast` ✓ |
| no crossed term, nothing signed | `{omnibus, simple_effect}` — unresolvable | `omnibus` ✓ |

The third row is why there is no compatible-set to check membership in any more: every determinate
pattern yields exactly one label.

**The one crossing signs cannot reach.** A continuous term has no levels, so it cannot be crossed:
an `age × group` moderation is one cell on age and two on group, which reads as a regression. The
interaction *column* is the only record that it was a moderation, and that is what the
`interaction_with` step is for. It runs first because otherwise `variation_level` would settle the
kind as a regression before the crossing was seen. The column carries the cell there, and that
cell's sign is the interaction coefficient's — a fact about the crossing, not about either
component's own slope.

The converse is a reporting habit worth naming. A crossing of two **categorical** factors needs no
product term: give each factor's levels their sides and `interaction` derives from the crossing. A
product column on top of that is legal, since it may really be in the design matrix, but it decides
nothing and is **flagged for review**. It is the most over-applied field in the schema — of 19 bench
disagreements on `has_interaction_with`, all 19 were a model setting it where the reference left it
empty, and most were categorical crossings. When in doubt, record levels and sides and leave it
empty.

The continuous step running first fixes the **F4 defect**, which an ordered row table over groups
and conditions used to hit: its row 2 (directional groups *and* directional conditions →
`interaction`) fired before its continuous-term rows, so a brain–behaviour regression on an
interaction contrast derived `interaction` instead of `cross_subject_regression`. A regression *on*
a contrast is a regression, and the contrast it was computed from is recorded by the categorical
cells either way.

Two consequences worth stating. A signed pair on an occasion factor alone is a `contrast`, which
is what a longitudinal pre-post comparison is; what the crossed term's levels range over, not the
label, is what marks it as a within-person change. A design crossing task with occasion or arm
derives `interaction` because both terms are crossed.

And a crossing *within* what used to be one entity axis — four cohorts that are genotype ×
diagnosis cells, or four arms that are drug A × drug B — needs nothing special. Those cells are
levels of two categorical `ModelTerm`s, so signing both levels of each crosses both and the
interaction derives as one. Under the five-slot encoding this was the case that had no home,
because a cohort could be named only once per effect and the crossing collapsed onto a single
axis.

### The adjustment set is derived

There is no slot for what a contrast controlled for. Being a column of the design matrix **is**
what adjusting for something is — a motion regressor adjusts the condition betas whether or not
any contrast weights it — so:

> A contrast's adjustment set is the terms of its `ModelEstimation` **and of every stage that
> model reaches through `inputs_from`**, minus the terms its `cells` name, minus any product
> column whose components are all **crossed**.

The second subtraction is not a refinement but a correction. A product column has no levels, so it
can never carry a categorical cell; subtracting only the celled terms therefore put *every*
interaction column in the adjustment set, and had the record claim the analysis controlled for the
column it tested.

**Celled, not crossed.** The criterion is whether the estimand depends on the term being in the
model, which is coding-free: drop the interaction column and refit, and the simple effect moves while
the marginal one does not. Without the interaction the model forces parallel lines, so "within b₁"
collapses onto the average — that dependence is what makes a simple effect a different quantity, and
why the interaction is not one of its covariates.

Deliberately *not* "the contrast weight on that column is non-zero", which is coding-dependent. Over
a 2×2 the averaged contrast puts weight 0 on the product column under ±1 effects coding and 0.5 under
dummy coding, so that test would give different answers for the same analysis. A held-constant level
is celled without being crossed, so keying the subtraction on crossing called the interaction a
covariate of the contrast that depends on it.

The two contrasts over cell means, which is the coding-free statement of both:

```
                [pt/task, pt/rest, hc/task, hc/rest]
within task     [   +1        0       -1        0   ]
averaged        [  +0.5     +0.5     -0.5     -0.5  ]
```

They differ only in the rest cells, and that is exactly what the two records differ in: the simple
effect has an unsigned `task` cell (restricting to it, so `rest` drops out) and the main effect has no
condition cell at all (so both cells carry the diagnosis sign). The vector is recoverable from the
cells; [test_effect_kind_derivation.py](test_effect_kind_derivation.py) reconstructs all four of the
factorial example's.

So a factorial yields, from one model:

| contrast | encoding | kind | adjusted for |
|---|---|---|---|
| main effect of A | A crossed; B not celled | `contrast` | B, A×B |
| A × B interaction | A and B both crossed | `interaction` | — |
| simple effect of A within b₁ | A crossed; b₁ cell, unsigned | `contrast` | — |
| omnibus F of the interaction | A×B celled, unsigned | `omnibus` | A, B |
| moderation, A continuous | A×B celled, **signed** | `interaction` | — |

Compute it; do not store it. Three consequences for a mapper.

A `term_use` recorded as a covariate produces no cell, and that is all it does to the contrast. But
the term must appear somewhere in the chain, because a covariate missing from every stage's term
list becomes a derived claim that the model did not adjust for it. If the extraction names a
covariate no term list holds, add it to the list of the stage that fitted it.

Analyses whose design matrices differ must not share a `ModelEstimation` record. One term list
behind two design matrices makes the derived adjustment set wrong for both. **The input is part
of the design**: a group model fitted over a left-seed connectivity map and the same group model
fitted over a right-seed one are two records differing in `inputs_from`, not one record used
twice.

### Stages

Neuroimaging splits one model across estimation stages — run, session, subject, group — because
fitting it in one step is not tractable, not because the stages are separate inferences. The
level of inference is normally the top stage, and the columns fitted beneath it are still columns
that inference conditions on.

So each stage the source describes takes its own `ModelEstimation`, keeping its own family,
estimator, software and HRF basis, and the higher stage names the lower one in `inputs_from`.
**A model's terms are then its own plus, transitively, those of the stages it names.** Three
things follow, and they are the whole reason the link exists:

- A first-level nuisance regressor is in the adjustment set of every group contrast taken above
  it. Motion regressed out at the first level adjusts the group betas.
- A cell may name a first-level column. A group contrast of a task condition, or of a seed's
  time series, is a cell on that stage's term — not a copy of the term hoisted upward.
- A product column may cross a column of its own stage with one of the stage below, which is how
  `diagnosis × condition` is expressed when condition was fitted per subject.

`Analysis.model_estimation` names the **top** stage, always: the one that produced the statistics
being reported. A first-level model that no reported contrast comes from is referenced by no
Analysis and reached only through the chain, which is correct — it was estimated, and nothing was
reported from it directly.

Leave `inputs_from` empty when the source describes one design matrix, and when it describes the
lower stage too thinly to constitute a model. Empty is the absence of a statement, not a claim
that no lower stage existed. A group stage that fits only an intercept — the ordinary one-sample
activation map — is not worth a record of its own unless the source describes it; and where such a
record does exist, its `terms` is legitimately empty. Never invent an intercept term to fill it.

`level` is a free-text label and carries no ordering. Two records both saying `group` say nothing
about their relation; `inputs_from` is the only thing that says which stage fed which.

**The same subtraction runs one level down.** For a term the cells *do* name, the levels they do
not name are the levels this contrast weighted out. So there is no `Direction` value for a zero
weight: absence is the zero weight, read against the term's `FactorLevel`s. A factor the contrast
averages over is the other subtraction — no cell at all, so it falls into the adjustment set.

The one case that needs care: an undirected test of a term, such as an F-test over a factor, must
give its levels cells with `unstated` rather than omitting them. Omitting them would put the
term in the adjustment set and say the analysis controlled for the factor it tested.

Because a term either has a cell or is in the difference, "tested and controlled for at once" is
unconstructable rather than merely invalid. The same holds a level down for "signed and weighted
out".

**What replaced the duplicate-axis check.** The five-slot encoding let one comparison be asserted
twice — a directional `ConditionTerm` and a directional `FactorTerm` on the term whose level names
that condition — and a validator had to detect it by walking `FactorLevel`. It is now
unrepresentable: there is one slot, and the entity is reached by joining through the model rather
than by being pointed at a second time.

### Off-vocabulary values leave the kind unrecomputable

One derivation input is an open vocabulary: `ModelTerm.variation_level`. A free-text level leaves
the continuous step unable to choose between `parametric_modulation` and
`cross_subject_regression`, so the kind is **undetermined** for that record — the derivation
returns nothing rather than guessing. Do not reject the record, and do not coerce the level into
`within_subject` or `between_subject` to make the step apply: those free-text answers accumulating
are the evidence for whether a further value earns a place in the vocabulary.

There is no longer a compatible-set to check membership in. The old derivation could not separate
`omnibus` from `simple_effect`, so it returned both and let the record choose; reading the signs on
each term settles it, and every determinate pattern yields exactly one label.

"Across sessions" used to be the case that recurred here. It now has a structural home — a signed
pair of cells on an occasion factor — so a longitudinal within-person term should carry a normal
`within_subject` level and let that factor say what changed.

---

## 4. Mapper responsibilities

The mapper mostly copies. The extraction schema is generated from the storage schema, so a
storage field is filled from the extraction field of the same name on the same class, with
`.value` unwrapped and `.evidence` kept in the audit log. What it does beyond that is two
uniform moves — unwrap, and resolve `local_id` to a minted `id` — and the short list below.
There is no normalize step: extraction carries storage's own vocabularies, so the value that
arrives is already one storage accepts.

Facts the storage schema needs that no extraction field supplies. These are exactly the
storage fields marked `in_subset: [deterministic]`, and `check_extraction_to_storage_map.py`
asserts that correspondence in both directions, so this table cannot quietly fall behind:

| Storage field | How the mapper fills it |
|---|---|
| `Study.title`, `.authors`, `.journal`, `.doi`, `.publication_year` | `api_lookup` — bibliographic facts the E-utilities API states, which is not something a model should be asked to read off the page |
| `Study.study_type` | `api_lookup` — PubMed publication types by PMID, stored verbatim and multivalued |
| `Analysis.id`, and every other `id` | `generate` — must be a deterministic function of the source location, so re-extraction reproduces it and externally stored evidence still joins. Recipe: study, table or figure identifier, analysis label slug or index. The extraction record's `local_id` goes in the audit log and every reference to it resolves through the same table |
| `Analysis.statistical_maps` | `api_lookup` — the maps NeuroVault holds for the study. A paper that shares maps says so in a data-availability sentence, and the sentence is not the map |
| `Table.source_path` | `generate` — the repository path of the raw table file |
| `Table.coordinate_space` | `derive` from the table parse; leaves `Analysis.coordinate_space` blank when it succeeds |
| `Table.coordinate_count` | `derive` — count the coordinate rows in the normalized table; zero when there are none |

Three things worth stating that are *not* mapper work any more:

- `Effect.kind` — *there is no such field*. The kind is read off the cells at query time (see §3).
- **Normalizing a vocabulary.** Extraction emits the permissible value itself, or the
  paper's wording on a field storage leaves open. The 16 tables that used to do this held
  316 synonyms and are gone; the five under `free_text_normalizations` remain, for fields
  storage keeps as `range: string` and so has no vocabulary to project.
- `Effect.cells`, `Analysis.details`, `Acquisition.acquisition_type`, `StudyDesign.arms` — the
  extractor supplies all of these directly now. They used to be assembled by the mapper from a
  differently-shaped extraction record, and that assembly was the only place the two schemas
  disagreed about the domain.

### Every extraction field has a storage field

There used to be a section here about the ones that did not. The two schemas were
deliberately different shapes: extraction kept the paper's language -- five entity axes,
comparative roles, a baseline field -- and storage kept the collapsed form, so a set of
extraction fields had nowhere to land and a section of the map called `absorbed_sources`
recorded where each one went instead.

That is gone. The extraction schema is generated from the storage schema by
`gen_extraction_schema.py`, so an extraction field with no storage counterpart is now
impossible by construction rather than merely documented, and
`check_extraction_to_storage_map.py` asserts the correspondence in both directions.
[test_map_coverage.py](test_map_coverage.py) exercises each failure mode against mutated
copies of the real schemas, because a completeness check that cannot fail is worth nothing.

The shapes that used to need absorbing are listed as candidates at the end of
[extraction-deviations.yaml](extraction-deviations.yaml). They were not obviously wrong --
they were chosen because they read more naturally to an extractor -- and the point of
starting from the projection is to find out which of them actually earn their cost. Adding
one back is an entry in that file plus a matching entry in the map.

Three routing rules survive the change, because an extractor's wording still decides them:

- **A stated baseline has no field of its own.** A named baseline condition is a `negative` cell
  on that condition's level; an implicit baseline or zero is *nothing*, since a lone signed cell
  already tests its coefficient against zero; chance is `PerformanceMetric.reference_value`. If
  the condition is named in prose but no model term carries it, there is no cell to make —
  report it, do not invent the term.
- **A weighted-out level and an undirected level are different facts.** A level the contrast
  weighted out gets no cell, because storage records a zero weight by having none; a level
  compared with no direction reported gets a cell with `unstated`, whether the direction went
  unreported for a signed contrast or because the test was an F.
- **"Moderator" is not a direction.** It has no destination. A moderated effect is the moderator
  and the moderated effect both as terms of interest, plus a product `ModelTerm` naming them in
  `interaction_with`. A cell whose direction says "moderator" is flagged for review, since it
  usually means the product term was not extracted.

### Try the parser first, fall back to the model, fill exactly one field

Some facts have two possible sources: an ingestion artifact that usually carries them and the
paper text that always does. The coordinate space is the case that recurs — the table parser
recovers it much of the time, but not always, and an analysis reported without a coordinate
table has no table to recover it from.

The rule is a precedence with an exclusivity, and it cannot be a schema constraint because it
spans two classes:

1. Fill `Table.coordinate_space` from the parse. It is a `derive`, and the extractor is not
   asked for it.
2. If no table of an analysis yields a space, ask the model for `Analysis.coordinate_space`.
3. **Populate exactly one of the two, never both.** A value on the Analysis means the tables
   were silent; a value on a Table means the Analysis is blank.

Both being populated is an error rather than a disagreement to reconcile, and a structural
validator should reject it. The pay-off is that each field then has exactly one filler, which
is what lets `Table.coordinate_space` be honestly `deterministic` and
`Analysis.coordinate_space` honestly `model_extracted` — a field filled sometimes by code and
sometimes by a model fits neither mark, and the provenance subsets are exclusive by design.

Expect the same shape wherever an artifact is a best-effort source rather than a guaranteed
one. `StatisticalMap` is the open case: its fields are marked `deterministic` because they come
from a repository, but "no repository link exists" is its own kind of unavailable, and the
extraction schema still asks for them.

### Review is routed deterministically

There is no record-level confidence field. An extraction record is flagged for human review
from the record itself:

- `unstated` values, counted per record;
- a required field left empty;
- a structural check failing (§3);
- a value outside a vocabulary — a signal for review, not a validation failure.

That last one is also the vocabulary-growth loop: off-vocabulary values are the raw material
for deciding which categories the enums are missing. Nothing in the repo aggregates them yet.

---

## 5. Known limits

Facts a paper may report that no field currently holds. Full evidence and options are in
[storage-schema-expressivity-probe.md](storage-schema-expressivity-probe.md); this is the short
list so an extractor is not left hunting for a slot.

- **Non-imaging outcomes.** `MeasureFamily` covers imaging signals only, so an intervention
  study's behavioural and clinical endpoints (BDI-II, STAI) and biospecimen assays (salivary
  cortisol) have no analysis-level home. `Study.design` carries the trial; nothing carries its
  result.
- **A meta-analysis embedded in a primary paper.** The gates in §1 skip meta-analysis *papers*,
  not a meta-analysis run as a step inside a primary study — that analysis still has no sample.
- **Test tail** (one- vs two-tailed), reported by ~17.5% of the corpus.
- **Exploratory vs confirmatory intent**, ~8.4%.
- **Nested corrections** — FDR across clusters then Bonferroni across an ROI set;
  `multiple_comparison_method` is one field.
- **Cluster extent in physical units** — `cluster_extent_threshold` is an integer count, so a
  threshold reported as 640 μl does not fit.
- **Bayesian inference** — model-space size, winning model, posterior probability, log evidence.
  `InferenceSettings` is frequentist throughout.
- **Frequency band** for EEG/MEG power and coherence; use `Measure.specific_metric`.
- **Intervention delivery detail** — dose, unit, route, schedule, washout, counterbalancing.
  What *was* extracted is the design: `StudyDesign` (allocation, assignment structure,
  blinding), an `Arm` per thing received including comparators (kind and agent name), and a
  `Timepoint` per occasion (relation to the intervention, order, elapsed time). Conditions and
  groups point at those by local_id, so record the manipulation as an arm and leave the dose out.
- **Derived-parameter measures** — a DCM self-connection, a growth-curve asymptote, an
  intersubject correlation, a graph metric, a pre-to-post change score. Put the quantity in
  `Measure.source_label` and `specific_metric`; that it is *derived* is not recorded.
- **Random effects, latent variables, growth-curve parameters** — name them in
  `Analysis.model_representation_notes`.
- **Ordered contrasts** lose the middle rank of `A > B > C`.
- **Regions as a comparison** — `Analysis.regions` lists the regions an analysis ran over but
  carries no direction. A dissociation *between* regions still needs them as levels of a
  categorical term, compared by `Cell`s — the levels now naming `Region`s through
  `FactorLevel.regions`, the way a cohort factor's levels name `Group`s.
- **Longitudinal within-subject level** — `VariationLevel` has no session or timepoint value
  between trial-wise and between-subject, and no `EffectKind` names a within-person change over
  time. The occasion itself does have a home: a `Timepoint` record, referenced from the
  condition.
- **Condition-level stimulus, response and instruction** — a condition records a name, a
  description, and whether it carried a demand or was a rest baseline. CogPO's three parts are
  described once for the paradigm, on `Task.stimuli`, `Task.instructions` and
  `Task.response_mode`, not per condition, so a paradigm where the response mode differs *between*
  conditions records both modes on the task and the difference only in each condition's
  `description`. Stimulus and response **laterality**, BrainMap's other response dimension, are
  not recorded at all.
- **Species other than the listed six** pass through as free text on `Group.species`.
