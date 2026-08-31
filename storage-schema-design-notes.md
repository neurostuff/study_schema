# Storage schema design notes

Why the storage schema is shaped the way it is: the reasoning behind a decision, the
alternatives weighed against it, and the measurement that settled it. The field
definitions themselves are in [neuroimaging-study-storage/](neuroimaging-study-storage/)
and say what each field *is*; [representing-models.md](representing-models.md) says how to
use them; [analysis-entities.md](analysis-entities.md) says what owns what. This holds the
part none of those should carry.

> Two documents still link to sections of this file that are not written yet — "Conditions
> belong to a task" from [standards-crosswalk.md](standards-crosswalk.md), and the
> details-class merge from `analysis_details.yaml`. Those decisions are real and the links
> are the record that they need writing down.

---

## An arm held constant

**An `Arm` reaches an analysis by two routes, and there is deliberately no third.**

`FactorLevel.arms`, when the arm was *compared*: a cell names a level, the level names the
arm. And `Group.arm`, when the cohort was *assigned* to it: the analysis names the cohort,
the cohort names the arm. Both are the schema's usual device — direction lives in
`Effect.cells` and nothing else, and every entity slot outside the model is membership.

The two routes leave one case uncovered. In a **crossover analysis run within a single
arm**, the arm restricts which *data* entered, not which *people*: every participant is in
both arms, so `Group.arm` cannot carry it, and the fit has no arm column for a cell to
name, because a constant is not a term. Nothing in the record then states that the map is
the heroin map rather than the placebo one. `Analysis.definition` says so in prose, which
is not queryable.

### Why no slot was added

The obvious repair is one optional multivalued reference. It is cheap: no new class, no
enum, no entry in the identity map, one line in the priority inventory, and the review
layer derives a reference task from the range without being told. It was not taken, for
two reasons.

**The case does not occur.** Swept across the 16-record corpus on 2026-08-13: 8 records
declare arms, covering 45 analyses. Twelve analyses reach no arm. Six are correctly
arm-free — `84rGLhCbUJTh`'s four pre-medication diffusion contrasts, its drug-naive versus
previously-medicated comparison, and its baseline Y-BOCS correlation, none of which is
about the SSRI arm. The other six are a broken string join, not a missing slot: a
`Cell.level` that matches no `FactorLevel.level`, which severs the route one hop before
the arm. `xevP8UDRAVh9`'s cells say `heroin` where the term declares `heroin-associated
perfusion`; `aVGe9BmFTMDR`'s say `responder Rlocation (−42, 34, 38)` where the term
declares `active responder Rlocation`. Both are already reported by `check_cell_terms`.
Zero instances of the gap itself. A slot's description is rendered into the extraction
prompt, so adding one is adding a field the model can fill wrong, and there is nothing in
the corpus to calibrate the wording against.

**It would duplicate what cells already say.** For the common case — a crossover that
*compares* its arms — both arms are already named by `FactorLevel.arms`, and the new slot
would list them again. Two routes to one fact that can disagree is the defect
`xevP8UDRAVh9` already wears from the other direction: each of its `FactorLevel`s names
both a `Condition` and an `Arm` for the same thing, because the paper's word for the drug
was "condition". Avoiding a second instance of that shape is worth more than covering a
case no paper has yet presented.

### Where it would go if it is ever added

`ModelEstimation`, not `Analysis`. The arm restricts which data entered the fit, which is
the same reasoning that put `preprocessing` and `spatial_unit` on that class: *the data a
model was fitted to is part of its specification*. A heroin-only fit and a placebo-only
fit are two records under that rule whether or not a slot exists, so putting the arm there
makes it constant within a record by construction. `Analysis.arms` could vary across
analyses sharing one model, which reintroduces exactly the inconsistency that reading was
meant to close.

The parallel to `AnalysisGroup` is tempting and does not hold. That class sits on
`Analysis` because analysed N genuinely differs between contrasts of one fit — dropout for
motion or missing data, which `YwwKWoEFwY3G` shows five times over in one regression. No
such per-contrast variation exists for arms.

The slot would then need a rule that it is populated only when no term of the model
reaches a `FactorLevel` naming an arm — the arm was fixed, not varied. That cannot be a
LinkML `rules` block: `slot_conditions` are within-class, and this condition reaches
through `terms` → `levels` → `arms`. It would be another `check_` in
`review/validate_record.py`, which is where every cross-class invariant already lives.

### What carries it instead

`check_arm_reachability` in [review/validate_record.py](review/validate_record.py), built
on `check_occasion_factors`' pattern: the structure is checked, and prose is the trigger.
An analysis whose `name` or `definition` names an arm — matched against that record's own
`Arm.name` and `Arm.agent`, so the vocabulary is the paper's rather than a fixed word list
— while neither route reaches an `Arm` is warned about, and the message names both causes
because a reviewer has to tell them apart.

It fires on four analyses in the corpus, all of them `xevP8UDRAVh9`'s, all of them the
string join rather than the gap. **The trigger for revisiting this decision is that check
firing on a record where the levels are correct.** That is a paper that actually needs the
slot, and it would come with the wording to calibrate against.

### A caveat on reachability as evidence

`TgcHKMRfrVog` and `kzMj26hGWacQ` both give an observational healthy-control cohort an arm
named "No intervention", with `arm_kind: no_intervention`. It makes `Group.arm` total,
which is convenient, but an observational control group was not *assigned* to anything,
and `Arm` is "one thing participants were assigned to receive, or to receive in place of
it". The consequence for anything built on arm reachability: `kzMj26hGWacQ`'s three
baseline patient-versus-control analyses "reach an arm" only through that placeholder.
Whether the placeholder is intended is an open question; until it is settled, arm
reachability is weaker evidence than it reads.

---

## The input a model was fitted to

**A slot naming which data a fit ran over would be provenance rather than a new query,
because every input the corpus presents already reaches the analysis by an existing route.**

The rule that would motivate one is in [extraction-readme.md](extraction-readme.md) §3:
analyses whose design matrices differ must not share a `ModelEstimation`, because *the input
is part of the design*. Its worked example is a group model fitted over a left-seed
connectivity map and the same model fitted over a right-seed one. The rule prescribes two
records; nothing in it says which seed each one used.

### Why it reads as provenance

Every input that distinguishes two otherwise identical fits is already an entity with a route
from the `Analysis`:

| what differs between two fits | route that exists |
|---|---|
| condition or arm | `cells` → `level` → `FactorLevel.conditions` / `.arms` |
| seed or target region | `ConnectivityDetails.seed_regions` / `.target_regions` |
| a lower-stage contrast map | `ModelEstimation.inputs_from` |
| preprocessing variant | `ModelEstimation.preprocessing` |
| measure or modality | `Analysis.measure`, `Analysis.acquisitions` |

The rule's own example is the second row, so it is joinable today. A designator would be a
second route to a fact an entity already carries, and a free-text one would not even be that.
`xevP8UDRAVh9` carried `spatial_scope: whole_brain` beside `correction_scope: "explicit
frontal and temporal lobe mask"` on the same analysis for as long as nothing checked the two
against each other; that is what a second unguarded route to one fact does.

### The one query it protects

A contrast's adjustment set, which is derived rather than stored: the terms of its model minus
the terms its cells name. One record shared across two design matrices drops the other
matrix's terms into every contrast's adjustment set, so the record claims an adjustment that
never happened. That is a real query — *which analyses controlled for motion* — and the slot
would protect it rather than add to it, and only where the split is otherwise unfollowable.

### Where the split is unfollowable

`xevP8UDRAVh9` is the case. "In BPM, each perfusion condition (heroin and placebo) was
correlated separately with the VBM data" is two fits over two inputs. Splitting them deletes
`term_perfusion_condition`, and with it the only route from each analysis to its arm — the gap
[An arm held constant](#an-arm-held-constant) describes from the other side. The record keeps
one model and gives the condition a categorical term whose level each analysis holds unsigned.

Measured 2026-08-13: that choice costs nothing here. All four BPM analyses cell both terms, so
each derived adjustment set is empty, and two single-term models would give empty ones too.
The shared record is wrong in principle and free in practice, which is why it is a note on the
record rather than a schema change.

### What carries it instead

`Analysis.model_representation_notes` on each of the four BPM analyses, which §6 of
[representing-models.md](representing-models.md) reserves for a first-class method whose model
the schema represents only approximately.

And `check_arm_reachability`, which is what tells the two failures apart. The repaired record
in `data/gold/` validates with no warnings: every BPM analysis reaches `arm_heroin` or
`arm_placebo` through `FactorLevel.arms`, so the arm is queryable without a designator. The
extraction in `data/records/` fires the check on all four, alongside four `check_cell_terms`
errors — its cells say `heroin` where the term declares `heroin-associated perfusion`. Same
paper, same shape, and the difference between them is a string join rather than a missing
slot. This is the trigger the section above names, resolved: the check stopped firing once the
levels were correct.

### The trigger for revisiting

A paper whose two fits differ by an input no other entity records — no arm, no condition, no
seed, no preprocessing variant — **and** whose contrasts do not cell every term, so the derived
adjustment set actually diverges. Both halves are needed: the first makes the split lossy, the
second makes it matter. Nothing in the 16-record corpus is either.

---

## An instrument administered is not an instrument that classified

**`Group.diagnostic_instrument` is a reference into `Study.assessments`, not a name.**

`xevP8UDRAVh9` filled it with the SCID-II. The sentence the record cites as evidence says
the SCID-II was conducted "to assess the diagnosis of comorbid personality disorders" — a
comorbidity the exclusion criteria then screened out, and not what made anyone
heroin-dependent. That cohort is defined by enrollment: heroin-maintained treatment for at
least six months. The paper names no instrument for the diagnosis at all.

The extraction was not wrong against its instruction, which read "instrument used to
establish diagnosis". Diagnosis was unqualified, so any diagnosis satisfied it. Two
defects sat underneath, and only one of them is about wording:

1. **Nothing bound the instrument to `medical_condition`**, so an instrument the cohort
   merely underwent read as qualifying.
2. **The true fact had no home.** The SCID-II *was* administered and *did* produce
   participant-level data. With `diagnostic_instrument` the only instrument-shaped slot in
   reach, a model with a true fact and one place to put it put it there.

The second is what structure fixes, and `Study.assessments` already existed to hold it —
it already held the SCID-II in that very record. Swept across the 16-record corpus on
2026-08-13: 35 groups, 12 carrying an instrument, and the field was silently inconsistent
with the catalogue. `xevP8UDRAVh9` and `6oTrCJA43Jcd` named instruments that were also
assessments; `QQCjAAT6SwwQ`, `7HPLh5nJzmP5` and `JzsUUQbDr2bm` named instruments that
appeared nowhere else in the record.

Stating the instrument once, in `assessments`, records "was administered" truthfully. The
group slot then stops accepting any instrument-shaped string and becomes a claim that
*this* assessment classified *this* cohort — which a model can decline to make. It follows
the idiom the schema already uses for `Study.assessments` (inlined catalogue) against
`Analysis.assessments` (`inlined: false` pointer).

### Why the slot was not deleted outright

Folding the instrument into `Assessment` and removing `diagnostic_instrument` altogether
is the tidier-looking move, and it destroys the signal. Nothing would then record which
group an instrument classified, and "the SCID-II was used in this study" is
*unconditionally true* for `xevP8UDRAVh9`. The defect above stops being expressible, let
alone falsifiable. Deduplication was never the goal; the group-level binding is the whole
content of the field.

### Why `medical_condition` did not become an object

Hanging the instrument off each condition gives per-condition precision. The corpus does
not have the shape that would pay for it — the relation is many-to-many, and the dominant
pattern is one instrument spanning *all* of a group's conditions:

| record | conditions | instruments |
|---|---|---|
| `7HPLh5nJzmP5` | 7 (depression, bipolar, anxiety, ADHD, ASD, PD…) | 1 MINI covering all |
| `kzMj26hGWacQ` | 2 (schizophrenia, schizoaffective) | 1 DIGS covering both |
| `6oTrCJA43Jcd` | 1 (ASD) | 2 (ADI-R *and* ADOS) |
| `QQCjAAT6SwwQ` controls | 0 | 2 (screened for absence) |

Nesting makes `7HPLh5nJzmP5` either repeat one id seven times or fill one condition and
leave six blanks that read as missing data. Worse, it makes the healthy-control row
**unrepresentable**: two instruments, no condition to hang them on, and confirming the
absence of illness is exactly the case a healthy group needs to state. Four of the twelve
instrument-bearing groups are wrong-shaped under it. The binding belongs at group level.

### The cost that was accepted

`gen_extraction_schema.py` carries plain references through unwrapped, so the link has no
`ExtractedValue`: no evidence span, no `extraction_status`. The justifying quote moves to
`Assessment.description`, whose wording now asks for the purpose the source gave for
administering the instrument.

This is a real loss and it was taken deliberately, because evidence on the field is what
made the original error look supported. `xevP8UDRAVh9`'s span was truncated to the
instrument's title — chars 6612–6680, "Structured Clinical Interview for DSM-IV Axis II
Disorders (SCID-II)" — cutting the clause that disqualified it. The same truncation
poisoned `diagnostic_system`: DSM-IV occurs exactly once in that paper, inside that title,
and was read as the system heroin dependence was diagnosed under. On the `Assessment` the
purpose has somewhere to live, and it contradicts a bad link where a reviewer can see it.

### What carries it

`check_group_instruments` in [review/validate_record.py](review/validate_record.py): every
entry must resolve to an `assessments[].local_id`. Referential integrity is mechanical in
a way clinical relevance is not — no validator can know that the SCID-II is beside the
point for heroin dependence, but any validator can know that an id resolves to an entry
whose description says what the instrument was for. It would have fired on three records
before the migration.

### One instrument per name the source gives

`QQCjAAT6SwwQ` names both "structured clinical interview" and "Mini-International
Neuropsychiatric Interview (MINI) version 5.0", and they are plausibly the same interview
described twice. They are kept as two `Assessment` entries. Merging them means deciding
that the paper's two phrasings denote one instrument, which is an inference about the
source rather than a reading of it, and the record's job is to preserve what the source
distinguished. A curator who knows better can merge; a merge performed at ingestion cannot
be undone from the record.

---

## Inference is a property of the test, not the fit

**`InferenceSettings` is a referenced entity on `Study`, not a block inlined on `Analysis`
and not a slot on `ModelEstimation`.**

Inlined, it duplicated. Across 16 adjudicated records, 102 analyses carried 30 distinct
settings by value — and 61 once the supporting evidence spans are counted too, meaning the
same scheme was not merely restated but re-grounded in a different sentence each time.
`JzsUUQbDr2bm` inlines one identical block seventeen times; seven analyses in
`QQCjAAT6SwwQ` each restate `p<0.001, k=247` from the one Methods sentence that states it.
A paper states its correction scheme once and applies it throughout, so an extractor asked
for it per analysis is being asked to copy, and copying is where records disagree with
themselves.

### Why not a slot on ModelEstimation

Because a fit and a threshold are not the same decision, and 5 of 29 model estimations in
the corpus prove it. `SULKxviGFurw/model-mvpa-lda` is one LDA fit tested two ways: an ROI
test Bonferroni-corrected over four regions at α=0.0125, and a searchlight over the same
fit corrected by FDR at α=0.05 with 5000 permutations. `ngDTY5BgJUuX/model-mass-univariate`
is one mass-univariate model reported FWE-corrected for two analyses and uncorrected for
four others.

Hoisting to `ModelEstimation` would force those into separate model records. That asserts a
difference in how the data were fitted that did not happen, and because a contrast's
adjustment set is derived from its model's terms, a spurious model split corrupts more than
the field that caused it — the same failure `ModelEstimation` already warns about from the
other direction, where one record is shared across two design matrices.

### Why a reference rather than either parent

The relation is many-to-many in both directions. Five model estimations carry two or more
schemes; four schemes are shared across two different model estimations. Hoisting to either
parent expresses one direction and loses the other. A reference from `Analysis` is the only
shape that holds both, and it is the shape `preprocessing` and `model_estimation` already
use.

### What carries it

`check_analysis_inference_settings` in [review/validate_record.py](review/validate_record.py):
the id must resolve to an `inference_settings[].local_id`. A dangling id leaves an analysis
with no threshold, no correction and no alpha, which reads as a result reported without
inference — a different claim from one whose thresholding the paper never stated. That case
is a declared scheme whose fields are `not_reported`, and it is the one the validator must not
confuse with the other.

---

## One measure, tested many ways

**`Measure` is a referenced entity on `Study`, a sibling of `InferenceSettings` rather than
anything nested under it.**

Inlined on `Analysis`, it duplicated at nearly the rate `InferenceSettings` did: 102 analyses
across the 16 adjudicated records carried **39 distinct measures**, or **71** counting the
evidence spans, so the same quantity was re-grounded in a different sentence rather than
merely restated. `eaEGQiVtDp9e` inlines one measure for all 8 of its analyses;
`JzsUUQbDr2bm` has 17 analyses over 4. The blocks carry a median of 4 filled fields, which
is what separates this from the duplication that is not worth removing — `Effect.statistic`
matches across analyses only because 69 of 102 of its blocks hold nothing but `family`, and
two analyses both being a *t* is not them sharing an entity.

### Why not under InferenceSettings

Because the two crosscut, and the corpus says so directly: **7 of 30 inference-settings
blocks span more than one measure**, and **5 of 38 measures span more than one settings
block**. `YwwKWoEFwY3G` thresholds five different measures under a single scheme.

A study states one correction scheme in Methods and applies it to everything it measured;
it also measures one quantity and tests it under whatever scheme each test warrants. Nesting
either under the other picks one of those two facts and discards the other, which is the
same error as hoisting `InferenceSettings` onto `ModelEstimation`. Both are references from
`Analysis`, and the `Analysis` is where the two meet.

### What carries it

`check_analysis_measures` in [review/validate_record.py](review/validate_record.py), through
the shared `check_references_resolve`. A dangling id leaves a result whose measured quantity
nothing states, which is not the same claim as a paper vague about what it measured — that
is a declared `Measure` holding the source's wording in `source_label` and `not_reported`
elsewhere, and it stays queryable.

---

## Direction has one home

**`ConnectivityDetails.parameter_sign` and `parameter_change` are gone. `Cell.direction` is now
the only slot in the schema that binds `Direction`.**

Both carried direction for the map as a whole: the sign of the connectivity parameter, and whether
the experimental effect raised or lowered it. Both are what the analysis *tested*, which is what
`Effect.cells` is for, so a record could state a direction twice — and cross-tabbing the two
against the cells they sat beside, over 54 extraction records and 46 connectivity analyses on
2026-08-14, shows all three of the ways that goes wrong:

| | `parameter_sign` (19 filled) | `parameter_change` (25 filled) |
|---|---:|---:|
| duplicates the effect's single signed cell | 8 | 4 |
| vacuous — `undirected`/`not_applicable` where the cells already say so | 6 | 14 |
| asserts one whole-map direction beside **crossed** cells | 5 | 7 |

The third row is the one that decided it. A crossed term means the map has a direction *per level*;
a whole-map `increased` beside it says the map has one direction and two at once, and nothing in the
record contradicts either. That is the same defect as two routes to an arm, which
[An arm held constant](#an-arm-held-constant) refused a slot to avoid.

Nothing is lost. A negative PPI is a `negative` cell on the term the coupling was estimated for;
coupling higher in one condition than another is that condition's term crossed, which is what the
`increased` rows were trying to say. `parameter_change`'s `not_applicable` even explained itself as
"direction is carried by condition or group roles instead" — naming the five-slot encoding that was
deleted two versions earlier, so the field had already outlived its own description.

`ParameterChange` went with it, having no other binding. `EffectPath` and `PerformanceRelation` are
untouched: a mediation path is which quantity was estimated rather than which way it went, and a
decoding metric's relation is to its own `reference_value` rather than to a level of a term.

## An edge's directionality is a property of the method

**`ConnectivityEdge.directionality` is `deterministic`, derived from
`ConnectivityDetails.connectivity_method`.**

Only a generative or precedence-based model supports a claim about which region influences which.
DCM and Granger causality do; a PPI, a seed correlation, a coherence estimate and structural
covariance do not, whatever the paper's wording — and the field's own description already said
"never infer a direction the method cannot support". If the method settles it, asking a reader for
it is asking for a chance to disagree with the method, and the corpus took the chance: of 23 edges
on 2026-08-14, 16 sat on `dcm` and **five of those were marked `undirected`**, which DCM cannot be.

So it became a lookup, in `derivations.ConnectivityEdge.directionality.value_map` in
[extraction-to-storage.map.yaml](extraction-to-storage.map.yaml) — two directed values, six
undirected, one line each.

### What the derivation cannot do

A `connectivity_method` written as free text has no entry, so the derivation leaves the field unset.
That costs the seven remaining corpus edges, all from one analysis whose method is "time-varying
sliding-window correlation between ICA components", where the model's `undirected` was correct and
is now inferred by nobody. It was accepted because the alternative is a field that is right by
lookup in eight cases out of nine and wrong by extraction in five out of sixteen. An off-vocabulary
method is already a review signal, and an analysis that has edges *and* an off-vocabulary method is
the shape to look at if the gap starts to matter.

This is the first derivation that reads the record rather than the world — every other one is an API
lookup, a minted identifier, or a value off the table parse. The test it establishes for moving a
field to `deterministic` is not "could a model answer this" but "does something the record already
holds settle it".

## The height threshold had two names and two slots

**`voxelwise_threshold_value` and `cluster_forming_threshold_value` are one field,
`height_threshold_value`.**

They were one fact under two names. The threshold applied to each element's statistic is called the
*voxelwise* threshold when elements are tested individually and the *cluster-forming* threshold when
the survivors are grouped into clusters, and it is the same number applied once to the same map. An
extractor facing "p < 0.001" therefore had to guess which of two slots the paper meant, and a query
had to read both.

Measured over 68 inference-settings records on 2026-08-14: 45 fill the voxelwise slot, 9 the
cluster-forming one, and **0 fill both**. No record in the corpus treats them as two facts.

`clusterwise_threshold_value` and `cluster_extent_threshold` are untouched, and they are the reason
the merged field is named for the *height* rather than for either of the words papers use: a cluster
threshold is a different quantity — the p a whole cluster had to beat, and the size it had to reach —
and "p < 0.001 uncorrected, cluster-level p < 0.05 FWE" fills the height field once and the
clusterwise field once.

This closes half of finding 2 in [standards-crosswalk.md](standards-crosswalk.md), which proposed a
`Threshold{level, value, type, correction_method}` class to subsume all of them. The other half —
one `multiple_comparison_method` beside thresholds corrected differently — is unaffected, and that
class is still the fix for it.

## Both places the coordinate space lives may be filled

**`Analysis.coordinate_space` is authoritative; `Table.coordinate_space` is per table; the
exclusivity between them is dropped.**

The rule used to be "populate exactly one, never both", and a structural validator was supposed to
reject both. Neither half held. Nothing ever checked it — `validate_record.py` has no such check —
and `build_record.py`'s `derive_coordinate_spaces` fills `Analysis.coordinate_space` from the table
parse whenever every table behind an analysis agrees, which is 214 of 308 analyses on 2026-08-14.
The exclusivity was being violated by the pipeline that was meant to hold it, in the ordinary case.

The reason to keep the two fields is the reason they disagree: a space is parsed **per table**, and
one analysis's tables can be parsed inconsistently. That is a fact about the tables worth keeping
rather than one to resolve by blanking a slot. So both are filled, and the analysis's value is the
analysis's space — a table can be parsed as one space while the analysis reporting it states another.

The cost is that `Analysis.coordinate_space` has two fillers while the provenance subsets are
exclusive by design. It stays `model_extracted`, because the model is the only filler that can read
a page; a value the builder derived from the parse carries `evidence.status: not_found`, there being
no quote for it. That is a real blemish on the `deterministic`/`model_extracted` split, and it is
smaller than an invariant nobody enforces.

## A scanner is a thing the study has

**`Instrument` became `Device`, referenced by `Acquisition` rather than inlined into it.**

A study that collected functional, structural and diffusion scans in one session has three
acquisitions and one scanner, and inlining made it three copies of the scanner. Across the
corpus 28 instrument blocks resolve to 20 machines; every multi-acquisition study except two
reuses one. The volume is small next to `Measure` — this is the right model rather than a
large saving.

### Why the rename

`instrument` was already taken. `Group.diagnostic_instrument` ranges over `Assessment`, so
the word meant a clinical questionnaire in one part of the schema and a scanner in another,
and a reader had to know which file they were in to tell. `Device` is unambiguous, and the
class had exactly one referring slot, so the rename cost almost nothing.

The distinction the reference now carries: two `Device` records assert the study used two
machines — a second site, or a rescan on different hardware. That is a claim about the data
and worth stating, and it is not the same as two sequences differing, which the
`Acquisition` records already say on their own.
