# Understanding the study schema

A course in reading and filling the neuroimaging study schema: what every entity is for, how they
connect, what each field holds and what it must not be confused with, and how to decide when a paper
is ambiguous.

This is the teaching document. The schema files under
[neuroimaging-study-storage/](neuroimaging-study-storage/) are the source of truth for what a field
holds, and they win any disagreement with what follows.

---

## How to use this

**Who it is for.** Anyone who has to fill a record, review one, or query one, and who knows the
statistics but not this schema's names for them. No LinkML knowledge is assumed; §2.1 supplies the
little that is needed.

**What you should be able to do at the end.**

1. Name every entity in the schema, say what question it answers, and say what owns it.
2. Take an arbitrary reported fact and put it in exactly one field, or say why the schema has no
   field for it.
3. Tell apart the field pairs that are routinely confused — there are dozens, and each chapter ends
   with the ones in its own area.
4. Fill a record under the four standing rules: one encoding of silence, the source's own words,
   declare once and reference everywhere, and never invent a value to satisfy a slot.
5. Read a record back out: what was compared, what it was adjusted for, who was in it, and what kind
   of effect it was — none of which is stored.

**Why twelve chapters.** One per boundary that has decision rules of its own. Two chapters orient
you, five cover the entities (one per kind of thing a study *has*), three cover the model and the
result (where nearly all the difficulty lives), and two cover the rules that span everything. Merging
any two of them would put two different decision procedures under one heading, which is the thing
this schema is otherwise careful not to do.

| Part | Chapters | You come out able to |
|---|---|---|
| I — Orientation | 1–2 | Place any fact in a layer, and read the wrapper around every value |
| II — The entities | 3–7 | Fill everything a study *has*: its frame, its people, its paradigm, its data, its places |
| III — The model and the result | 8–10 | Encode what was fitted and what was tested, and choose a method payload |
| IV — The rules | 11–12 | Say "the paper does not report this" correctly, and pass the checks |

**Each chapter has the same shape**: learning goals, a zoom-out that places its subject in the whole
record, a field-by-field pass with the near-miss each field guards against, and one or more **drills**
with answers. Chapters 1–10 close with a **failure modes** table saying what a wrong fill *asserts* —
which is the only question that matters, since a record is a claim about a publication.

**If you read only three:** chapter 1 for the layers, chapter 8 for the model, chapter 9 for the
result. Chapters 8 and 9 are where records go wrong.

**Companion documents.** This one teaches; those state.

| For | Go to |
|---|---|
| What each entity is called, what owns it, what points at what | [analysis-entities.md](analysis-entities.md) |
| How to encode a reported analysis, with twelve worked records | [representing-models.md](representing-models.md) |
| Why the schema is shaped this way — alternatives weighed, measurements | [storage-schema-design-notes.md](storage-schema-design-notes.md) |
| Rules the schema cannot state: gates, conventions, invariants, known limits | [extraction-readme.md](extraction-readme.md) |
| How this compares to BIDS Stats Models, NIDM-Results, CDISC ARS | [standards-crosswalk.md](standards-crosswalk.md), [ars-crosswalk.md](ars-crosswalk.md) |

---

# Part I — Orientation

## Chapter 1. What one record is, and the four layers

**Learning goals.** By the end you can

- say in one sentence what a record is a claim about, and what it is not;
- place any reported fact in one of four layers, and say which direction the references run;
- name the junction class that almost every "where does this go?" question turns on.

### 1.1 Zoom out: one publication, one record

A record is **one publication's answer to four questions**: what existed in the study, what was
fitted, what was tested, and where in the text each of those was found. It is not a summary, not a
reproduction of the pipeline, and not a data set. Everything in it is either a claim about the paper
or an identifier holding the claims together.

Two consequences follow immediately, and they explain most of the schema's oddities.

**A record follows the reporting, not the science.** A study certainly averaged its first-level
contrast images at a group stage; if the paper does not describe that stage, the record has no
`ModelEstimation` for it. Silence in the source is silence in the record, and the schema has exactly
one way to say so (chapter 11).

**A record is a claim, so a wrong fill is a false claim rather than untidiness.** "The analysis
controlled for age" is not something anyone writes down here — it is *derived* from age being a
column of the model with no cell on this contrast. So forgetting age does not leave a gap; it asserts
that the model did not adjust for age. Almost every failure mode in this tutorial has that form, and
the failure-mode tables always give the false claim rather than the missing field.

### 1.2 The four layers

| Layer | Question | Classes |
|---|---|---|
| **Entities** | What existed in the study? | `Group`, `Task`, `Condition`, `Arm`, `Timepoint`, `Region`, `Assessment`, `Acquisition`, `Device`, `Preprocessing` |
| **Model** | What was fitted? | `ModelEstimation`, `ModelTerm`, `FactorLevel` |
| **Result** | What was tested, and what came of it? | `Analysis`, `Effect`, `Cell`, `Statistic`, `Measure`, `AnalysisGroup`, `Mediation`, the eight `AnalysisDetails` subclasses, `InferenceSettings`, `Table`, `StatisticalMap` |
| **Evidence** | How do we know? | `ExtractedValue` and its typed subtypes, `Evidence`, `EvidenceSet`, `EvidenceSpan` — chapter 2 |

The first three layers refer **downward** and never upward. No entity knows which model used it; no
model knows which analysis took a contrast from it. That is what lets one cohort serve twelve
analyses without being restated, and it is why you read a record by starting at an `Analysis` and
walking down.

```
Study
├── entities: groups[]  tasks[] ── conditions[]  design ── arms[], timepoints[]  regions[]
│             assessments[] acquisitions[] devices[] preprocessings[]
├── model:    model_estimations[] ── ModelEstimation ── terms[] ── ModelTerm ── levels[] ── FactorLevel
│                                        │                                                   └─► entities
│                                        └── inputs_from[] ─► ModelEstimation (the stage below)
└── result:   analyses[] ── Analysis ── model_estimation ─► the top stage
                              ├── effect ── cells[] ── Cell ─► ModelTerm
                              ├── groups[] ── AnalysisGroup ─► Group
                              ├── measure ─► Measure     inference_settings ─► InferenceSettings
                              └── details ── one AnalysisDetails subclass
```

[analysis-entities.md §1](analysis-entities.md) has the full tree with every reference slot; consult
it whenever you need to know where a record *lives* as opposed to where it is *used*.

### 1.3 The middle layer is the one people skip

A paper's cohorts and conditions do not reach a result directly. They reach it by being the levels of
a model term that a contrast put a weight on:

```
Group  ◄──  FactorLevel.groups  ◄──  ModelTerm.levels  ◄──  Cell.term  ◄──  Effect.cells
```

**`FactorLevel` is the junction**, and most "where does this go?" questions are questions about it. A
cohort comparison is not recorded on the cohorts; it is two directional `Cell`s on the term whose
levels name them. Nothing on an `Effect` says whether a comparison was between cohorts, conditions,
occasions, arms or regions — you read that off the `FactorLevel`s the cells reach.

Skipping the middle layer produces the commonest structural defect there is: the entities are all
present, the analyses are all present, and nothing connects them, so no query can tell what any map
compared.

### 1.4 Drill

> A paper reports "reduced amygdala reactivity in patients versus controls during fearful faces". You
> have a `Group` for patients, a `Group` for controls, a `Task` with a `fearful faces` condition, and
> an `Analysis`. What is still missing?

**Answer.** The model layer. There must be a `ModelEstimation` with a categorical `ModelTerm` for
diagnosis whose two `FactorLevel`s name the two `Group`s, and the `Effect` must carry two `Cell`s on
that term — `positive` on controls, `negative` on patients (reduced *in patients*). The condition
enters as a level of a second term, or as a `held` cell if the comparison was taken within it. Without
the terms, the two cohorts are recorded as having been *analysed* (`Analysis.groups`) and nowhere as
having been *compared*.

### 1.5 Failure modes

| Wrong fill | What it asserts |
|---|---|
| Entities present, no `Cell`s | The analyses tested nothing about anyone |
| A comparison written into `Analysis.name` only | Prose says patients were compared; every query says the sample happened to contain two cohorts |
| A cohort in `Analysis.groups` but in no `FactorLevel` | Its participants were included; nothing was compared between cohorts |

---

## Chapter 2. Two schemas, one shape

**Learning goals.** By the end you can

- say what the storage schema and the extraction schema each are for, and which one you fill;
- read an `ExtractedValue` and its evidence, and say when each of the three evidence statuses applies;
- explain why `required` constrains the slot and not the answer;
- say which schema-level constraints survive the projection and which move into the validator.

### 2.1 The minimum LinkML

A **class** has **attributes** (fields). An attribute's **range** is what it holds: a scalar type, an
**enum** (a controlled vocabulary), or another class. An attribute holding another class is either

- **inlined** — the child record lives inside the parent, which owns it; or
- **not inlined** — the slot holds the target's identifier, and the target lives somewhere else.

That distinction is where a fact gets *edited*. `AnalysisGroup.group` is a reference, so a cohort's
demographics are edited on the `Group`, not on the analysis that names it.

**`required: true`** means the slot must be present. It does *not* mean the paper must have reported
something — see §2.4. **`unique_keys`** says a combination may not repeat within its container.
**`rules`** are conditional constraints (if `spatial_scope` is `roi`, `regions` must be present).

### 2.2 Storage, extraction, and the projection

| | `neuroimaging-study-storage` | `neuroimaging-study-extraction` |
|---|---|---|
| What it is | The normalized, query-optimized target: typed values bound to vocabularies, no provenance | The immutable record of one extraction: the same shape with every value wrapped in its evidence |
| Who writes it | The mapper, from an extraction record | A language model reading the paper, plus deterministic passes |
| Hand-edited? | Never — re-running the mapper regenerates it | Never — produce a new version or a separate correction record |
| Size | 45 classes, 37 vocabularies, 301 fields, across 11 modules | Generated: the same classes plus the evidence types |

The extraction schema is **generated** from storage by `gen_extraction_schema.py`, so the two cannot
drift. The whole of the projection:

- fields marked `deterministic` are dropped (code fills them, so there is nothing to read off a page);
- `id` becomes `local_id`, a plain document-local string; storage mints the real identifier at ingestion;
- a scalar becomes an `ExtractedString` / `ExtractedInteger` / `ExtractedNumber` / `ExtractedBoolean`;
- an enum becomes `Extracted<Enum>`, keeping storage's own range — closed where storage is closed,
  open where storage left an escape hatch;
- `multivalued` moves *inside* the wrapper: one `Extracted<T>List` over a list, under one evidence
  record, rather than a list of wrappers;
- an inlined child is projected too; a reference stays a reference and resolves through `local_id`;
- `minimum_value`, `rules` and `unique_keys` are **dropped**, because they constrain a scalar and the
  wrapper is in the way. Those move into `review/validate_record.py` (chapter 12).

You fill the extraction schema. You read either. Everything this tutorial says about a field is true
of both, because they are the same field.

### 2.3 The evidence wrapper

```json
"acquired_count": {
  "extraction_status": "extracted",
  "value": 28,
  "value_source": "reported",
  "evidence": {"status": "present",
               "sets": [{"spans": [{"text": "28 patients were scanned",
                                    "start_char": 5411, "end_char": 5435}]}]}
}
```

| Part | Values | Means |
|---|---|---|
| `extraction_status` | `extracted` / `not_reported` | Whether a usable value was found. There is no third option |
| `value` | the value | Present exactly when the status is `extracted`; omitted otherwise |
| `value_source` | `reported` / `generated` | Whether the source states it, or the system built it from what the source states |
| `evidence.status` | `present` / `not_found` / `not_applicable` | A span was found / a value was extracted with no span behind it / nothing was extracted |

Three invariants, and the validator enforces all three:

1. `not_reported` ⇒ no `value`, and `evidence.status: not_applicable`. Such a field therefore carries
   **no span at all**: there is no way to cite the sentence proving the answer was looked for and is
   not on the page.
2. `evidence.status: present` ⇒ at least one `EvidenceSet`, each with at least one span.
3. Every span satisfies `text == source[start_char:end_char]` against the normalized source text,
   half-open. An offset that does not match is an error, not a rounding problem.

An `EvidenceSet` is **one independently sufficient** set of spans. Two sets mean either alone would
do; two spans in one set mean both are needed together.

**A multivalued fact is one wrapper holding a list**, not a list of wrappers — the list usually comes
from one sentence, and one evidence record covers it. `Group.inclusion_criteria`,
`Preprocessing.steps`, `MRI.echo_time_seconds` all work this way. The exceptions are lists of
*references* (`Analysis.tasks`, `FactorLevel.groups`): a `local_id` is not a source-derived value and
carries no evidence.

### 2.4 `required` is about the slot, not the answer

This is the single most misunderstood thing in the schema, and it is worth stating flatly.

> A required field whose fact the paper never reports is filled with
> `extraction_status: not_reported`. The slot is present; the answer is that there is no answer.

`Analysis.measure` is required. A paper vague about what it measured still takes a `Measure` record,
with `source_label` as given and `not_reported` on the rest. What `required` rules out is *dropping
the slot* — which would make "no measure" and "measure not stated" the same JSON.

References are the exception that shapes several rules. A reference slot holds an identifier, so it
has no `not_reported` form: you can point at a record or not point at anything, and there is no third
state. That is why the schema states both directions of the search-space rule (§9.2) and why the
"declare a record and mark its fields `not_reported`" move recurs — for `Measure`,
`InferenceSettings`, `ModelEstimation`, `Device`.

### 2.5 The three axes of per-field metadata

They deliberately do not collapse into one. A field can be code-filled and still outside the MVP, or
low-priority and inside it.

| Axis | Where | Question |
|---|---|---|
| Is this field in the first release? | `in_subset: [mvp]` | Which slice does a consumer see? |
| How does it get filled? | `in_subset: [deterministic]` or `[model_extracted]` | Code, or a model reading the page? |
| How urgently does a human check it? | `storage-parameter-priorities.yaml` | Reviewer triage order |

`deterministic` is what an API, a parser, or a derivation supplies: bibliographic metadata, PubMed
publication types, minted identifiers, `Table.coordinate_space`, NeuroVault maps. `model_extracted`
is everything read off the page. Every field is in exactly one of the two, and
`check_field_provenance.py` fails if not; identifiers and type designators are in neither by
definition.

A derivation need not come from outside the record. `ConnectivityEdge.directionality` is derived from
a *sibling field* — `ConnectivityDetails.connectivity_method` — through a value map in
`extraction-to-storage.map.yaml`, because the method decides whether an edge is directed and no
wording of the paper can override it. That is the test for moving a field to `deterministic`: not
"could a model answer it" but "does anything the record already holds settle it".

### 2.6 Drill

> A paper says nothing about its scanner's field strength. Two candidate fills: (a) omit
> `magnetic_field_strength_tesla`; (b) `{"extraction_status": "not_reported", "evidence":
> {"status": "not_applicable"}}`. Which, and what does the other one cost?

**Answer.** (b). The field is optional, so (a) validates — but a downstream reader cannot tell a field
nobody looked at from one that was looked for and is absent, and any count of "how much did this paper
report?" silently reads (a) as unexamined. The same applies to every optional field: `not_reported` is
an assertion, and an omitted slot is not.

### 2.7 Failure modes

| Wrong fill | What it asserts |
|---|---|
| A value plus `evidence.status: not_applicable` | A value was extracted from nothing — a contradiction the validator rejects |
| `not_reported` with a span | A citation for the absence of a citation; rejected |
| A list of wrappers where one wrapper over a list belongs | Each item was independently evidenced, and the schema cannot express it anyway |
| An invented value to satisfy a `required` slot | The paper reported something it did not. Worse than `not_reported` in every case |

---

# Part II — The entities

Five chapters, one per kind of thing a study *has*. Everything here is declared once on the `Study`
(or on the one entity that owns it) and referenced by identifier wherever it is used. That is the
second standing rule: **declare once, reference everywhere.** A seed region written as free text in
three places is three spellings that nothing can join.

## Chapter 3. The study frame: `Study`, `StudyDesign`, `Arm`, `Timepoint`

**Learning goals.** By the end you can

- say what belongs to the study as a whole rather than to any analysis;
- separate a prediction from an aim from a finding;
- decide whether a manipulation is an `Arm` and where an occasion goes;
- say why an `Arm` and a `Timepoint` are useless until a model term names them.

### 3.1 Zoom out

`Study` is the tree root and mostly a set of containers: the entity lists, the model list, the
analysis list, the table list. Its own fields are the publication-level facts. `StudyDesign` is the
one inlined child, and holds what happened to participants **outside the task** — the manipulation
and the occasions.

Task-internal structure is *not* here. What participants did inside the scanner is a `Task` and its
`Condition`s (chapter 5); what was done *to* them across sessions is an `Arm` and a `Timepoint`.

### 3.2 `Study`

| Field | Holds | Not to be confused with |
|---|---|---|
| `title`, `authors`, `journal`, `doi`, `publication_year` | Bibliographic facts | Nothing — all `deterministic`, from the E-utilities API rather than the page |
| `study_type` | PubMed publication types, verbatim and multivalued | A design label of your own. `Meta-Analysis` here is also a *gate*: the paper is skipped |
| `description` | Brief summary of aims and scope | `StudyDesign.description`, which is the design narrative |
| `hypothesis` | The study's own prediction, quoted, one entry per prediction | An aim. See below |
| `design` | The inlined `StudyDesign` | — |
| the thirteen list slots | `groups`, `tasks`, `assessments`, `regions`, `devices`, `acquisitions`, `preprocessings`, `model_estimations`, `inference_settings`, `measures`, `analyses`, `tables`, `external_datasets` | Each holds the records themselves; every use of one elsewhere is a reference by identifier |

**A prediction is not a purpose.** "The aim of this study was to examine whether connectivity differs
between patients and controls" says what was tested: it takes no `hypothesis` value. "We expected
connectivity to be lower in patients" says what was expected: it does. Recording an aim as a
hypothesis asserts a direction the study never committed to — which is precisely what a reader
consults the field to check. Most papers state no prediction, and the field is then absent. Where the
only statement is in the Discussion ("consistent with our hypothesis that connectivity would be
lower"), quote that clause.

Three fields form a natural progression across the record, and confusing them loses the distinction
that makes the record worth having:

| | Says | Lives on |
|---|---|---|
| what was expected | `hypothesis` | `Study` |
| what was tested | `definition` | each `Analysis` |
| what came of it | `interpretations` | each `Analysis` |

### 3.3 `StudyDesign`

Six fields. Three of them — `allocation`, `assignment_structure`, `blinding` — are a matched set, each
binding an open vocabulary, and together they are what a synthesis filters interventional studies on.

| Field | Holds | The boundary |
|---|---|---|
| `description` | The design in narrative form | Free text; the three normalized fields are what queries use |
| `allocation` | How participants came to be in the arm they were in: `randomized`, `non_randomized`, `single_arm`, `not_applicable` | `single_arm` — everyone got the same thing, so there was nothing to allocate. `not_applicable` — nothing was administered at all |
| `assignment_structure` | Whether the arms are separate cohorts or the same people passing through: `parallel`, `crossover`, `within_subject`, `single_group` | This is what decides where an arm reference goes: see §3.5 |
| `blinding` | Who was kept unaware | `not_applicable` when nothing was administered |
| `arms` | The `Arm` records themselves | Comparator arms included — they are arms |
| `timepoints` | The `Timepoint` records themselves | — |

`within_subject` against `crossover` is the pair to get right: a single-arm pre–post design is
`within_subject`, because there is no randomized or counterbalanced order of arms to cross over.

### 3.4 `Arm`

One thing participants were assigned to receive, **or to receive in place of it**. A placebo, a sham,
usual care and a waitlist are all arms; a study comparing a drug against an active comparator and a
study comparing it against a placebo are not making the same comparison, which is why `arm_kind`
spans both sides.

| Field | Holds | The boundary |
|---|---|---|
| `name` | The source's short label — `methylphenidate`, `sham tDCS`, `waitlist` | — |
| `description` | What this arm received, completely | — |
| `arm_kind` | Required: `pharmacological`, `stimulation`, `behavioural_intervention`, `placebo`, `sham`, `active_comparator`, `usual_care`, `no_intervention` | `no_intervention` is a waitlist or untreated *arm*; a rest or fixation period in the scanner is a `Condition`, not this |
| `agent` | The drug, agent or stimulation target, as named | Names only. Dose, unit, route and schedule are not recorded anywhere — see §12.6 |

### 3.5 `Timepoint`, and how an occasion reaches a model

| Field | Holds | The boundary |
|---|---|---|
| `name` | The source's own label — `baseline`, `T0`, `week 12` | Nothing normalizes the name |
| `relation_to_intervention` | Required: `pre_intervention`, `during_intervention`, `post_intervention`, `single_occasion`, `not_applicable` | `not_applicable` = several occasions but nothing administered (a longitudinal observational study). `single_occasion` = data collected once |
| `order` | Position in the sequence, from 1 | Carries what `relation_to_intervention` cannot: several `post_intervention` occasions, of which the earliest is the endpoint and the rest are follow-ups |
| `time_from_intervention` | Elapsed time, in the source's words and units | Free text — "24 h", "six weeks" |

**Now the rule that makes this chapter matter.** A `Timepoint` is reached by exactly one slot in the
whole schema: `FactorLevel.timepoints`. An `Arm` is reached by exactly two: `FactorLevel.arms` when
the arm was *compared*, and `Group.arm` when a cohort was *assigned* to it.

So a record whose analyses report change over time and whose model terms name no occasion has recorded
the scans and lost the comparison between them. The failing shape is a term called `pre > post
change`, continuous, with no levels — the axis the design matrix distinguished, written down from its
contrast's side. The encoding to use is a categorical term with a level per occasion; see
[representing-models.md §5.6](representing-models.md#56-a-prepost-change-with-no-paradigm) and
chapter 8.

The crossover case has a known blind spot worth knowing about. An analysis run within *one* arm of a
crossover has no cohort to hang the arm on (everyone is in both arms) and no column naming it (it was
held constant, not compared), so nothing queryable says which arm the map is. The schema has no slot
for it, deliberately — [storage-schema-design-notes.md](storage-schema-design-notes.md), "An arm held
constant" — and `check_arm_reachability` warns when an analysis's prose names an arm that neither
route reaches.

### 3.6 Drill

> An open-label study scans 20 patients before and after eight weeks of CBT, with no control group.
> Fill `allocation`, `assignment_structure`, the arms and the timepoints.

**Answer.** `allocation: single_arm` (everyone got the same thing, so nothing was allocated — not
`not_applicable`, which is for studies that administered nothing). `assignment_structure:
within_subject` (repeated occasions around one intervention, no order of arms to cross).
One `Arm`: name from the paper, `arm_kind: behavioural_intervention`. Two `Timepoint`s:
`pre_intervention` with `order: 1`, `post_intervention` with `order: 2`. And — the part that is
usually missed — the pre–post analysis needs a categorical `ModelTerm` whose two levels name those two
timepoints, or the occasions stay unreachable.

### 3.7 Failure modes

| Wrong fill | What it asserts |
|---|---|
| An aim quoted as `hypothesis` | The study predicted a direction it never committed to |
| `allocation: not_applicable` for a single-arm drug study | Nothing was administered |
| Timepoints declared, no `FactorLevel.timepoints` | The scans happened; nothing was compared across them |
| Dose or schedule squeezed into `Arm.name` | A normalized arm label that is really a dose string, and no field says so |

---

## Chapter 4. The people: `Group`, `CategoryDistribution`, `Assessment`

**Learning goals.** By the end you can

- say what one `Group` is, and where a cohort's *analysed* size lives;
- walk the recruitment funnel and say which count belongs where;
- separate a group's defining condition from what it was screened for;
- say what an `Assessment` is, and the two quite different jobs it does.

### 4.1 Zoom out

A `Group` is an **aggregate** cohort: no participant-level rows anywhere in this schema. It is the
biggest class in the schema (34 fields) because cohort description is what a meta-analysis filters on,
and it divides into six blocks.

### 4.2 The six blocks of `Group`

**Identity.** `name` (the study-local label), `description`, `species` (required; open, default
`human`), `arm` (§3.5).

**The recruitment funnel.** `approached_count` → `consented_count` → `enrolled_count` →
`acquired_count` → `excluded_count`. The funnel stops at acquisition on purpose: **how many of this
cohort a given analysis used is `AnalysisGroup.n`**, not a `Group` field, because different analyses
drop different participants. Expect `n` ≤ `acquired_count`.

**Age.** `age_mean`, `age_standard_deviation`, `age_minimum`, `age_maximum`, `age_median`, `age_unit`.
Record what the paper gives, in the unit it gives; `age_unit` is normally `years` but may be months or
gestational weeks.

**Distributions.** `sex_distribution`, `gender_distribution`, `handedness_distribution`,
`race_distribution`, `ethnicity_distribution` — each a list of `CategoryDistribution`:

| `CategoryDistribution` field | Holds |
|---|---|
| `category` | The bucket, labelled as the study writes it — `female`, `left-handed`, `Hispanic or Latino`. Never normalized to an external scheme |
| `count`, `percentage`, `denominator` | Whichever the paper gives; the denominator is what a percentage was of |
| `reporting_framework` | The classification system the study used, in its own words — OMB/NIH categories, a named handedness inventory, a self-report answer set |

Phenotypic sex and self-described gender are separate slots and are not interchangeable. Most papers
report one of them, and which one they reported is itself information.

**Clinical.** `is_healthy`, `medical_condition` (multivalued: primary diagnosis and all
comorbidities), `diagnostic_system`, `diagnostic_instrument`, `clinical_characteristics`,
`medications`, `medication_status`.

Four boundaries here, all of them routinely crossed:

| | | |
|---|---|---|
| `is_healthy` | Whether the source characterizes the cohort as healthy | `true` together with a non-empty `medical_condition` is a contradiction. No LinkML slot condition can emit a boolean constant, so the schema states the invariant in its root header and a record audit outside the schema enforces it (`audit_records.py` in the ns-validate review layer) |
| `diagnostic_system` | The system the group's condition was diagnosed under | An edition inside an *instrument's title* does not establish it: "SCID for DSM-IV Axis II Disorders", used to screen comorbidities, says nothing about how the defining diagnosis was made |
| `diagnostic_instrument` | Which of the study's `Assessment`s **classified this cohort** | Not everything administered to it. An interview screening a comorbidity the group is not defined by, or grading severity of a diagnosis made elsewhere, is an `Assessment` that nothing points at |
| `medications` vs `medication_status` | The agents, one per entry / whether the cohort was medicated, drug-naive, withdrawn, on or off during acquisition | The state is what a synthesis filters on; the names are what it reports |

Groups defined by enrollment or history rather than by an instrument — a treatment programme's
patients, self-reported users — name **no** `diagnostic_instrument`, and that emptiness is correct.

**Context.** `recruitment_method`, `recruitment_dates`, `inclusion_criteria`, `exclusion_criteria`,
`education_summary`, `socioeconomic_status_summary`.

### 4.3 `Assessment`

Three fields besides its identifier, and it is the smallest important class in the schema.

| Field | Holds |
|---|---|
| `name` | The instrument — `Digit Span`, `MMSE`, `BDI` |
| `description` | What it produced **and the purpose the source gives for administering it** |
| `assessment_type` | Broad type, free text: cognitive test, clinical scale, diagnostic interview, questionnaire, behavioral task, physiological measure, biospecimen assay |

An `Assessment` is anything measured about participants **independently of the acquisition and of the
experimental conditions**. Task accuracy inside the scanner is not one; it is
`Task.performance_measures`.

The purpose sentence in `description` is load-bearing and easy to skip: it is what decides whether a
`Group` may name this assessment as its `diagnostic_instrument`, and it is often the only sentence in
the paper distinguishing the two.

An `Assessment` does two jobs, and the second is what makes it part of the model layer's business:

1. it describes the study's instruments, and can be listed on `Analysis.assessments` for an analysis
   whose sample it selected or whose values it supplied;
2. it is what a continuous `ModelTerm` points at through `ModelTerm.assessment` — **including when
   the column is derived from it** (a change score, a composite of subscales). One instrument is one
   record however many columns it supplies; which subscale a column carries is the term's `name`.

### 4.4 Drill

> A study enrolls 30 patients, scans 28, and excludes 3 for motion. Its VBM analysis uses 25 patients;
> its resting-state analysis uses 24 because one scan was missing. Where do 30, 28, 3, 25 and 24 go?

**Answer.** `enrolled_count: 30`, `acquired_count: 28`, `excluded_count: 3` on the `Group`; `n: 25`
on the VBM analysis's `AnalysisGroup`, and `n: 24` on the resting-state one's. Putting 25 on the
`Group` would make the second analysis's 24 unexplainable and would misstate how many were scanned.

### 4.5 Failure modes

| Wrong fill | What it asserts |
|---|---|
| The analysed n as `acquired_count` | Fewer participants were scanned than were |
| A screening interview as `diagnostic_instrument` | That instrument classified the cohort |
| A DSM edition read out of an instrument's title | The defining diagnosis was made under that system |
| `is_healthy: true` with a `medical_condition` | Both that the cohort was healthy and that it had a condition; rejected |
| Task accuracy as an `Assessment` | A measurement independent of the paradigm, when it came out of the paradigm |

---

## Chapter 5. The paradigm: `Task`, `Condition`

**Learning goals.** By the end you can

- decide when a study needs a `Task` record at all;
- put a rest period on the right side of the `Task`/`Condition` line;
- say what a `Condition` deliberately does *not* record.

### 5.1 Zoom out

A `Task` is a **meaningful participant paradigm** — a task, a resting-state protocol, stimulation,
passive viewing. Its `Condition`s are the states it was made of. Conditions belong to the task, not to
the study.

**When a Task record is required:** wherever the study models conditions, resting state included. A
standalone resting-state run is a paradigm and takes a `Task` with one condition or none.

**When it is not:** an acquisition with no paradigm — morphometry, diffusion, a static PET scan. Those
reach an analysis through `Analysis.acquisitions` instead, and this stays true in a longitudinal or
interventional study: a structural comparison across occasions is carried by the model's timepoint
factor, not by inventing a task.

### 5.2 `Task`

| Field | Holds | The boundary |
|---|---|---|
| `name` | Short unique label; conceptually BIDS `TaskName` | — |
| `description` | What participants experienced and did, completely | — |
| `conditions` | The `Condition` records — the different states or modes of the task | — |
| `design_type` | Event-related, block, mixed, naturalistic, or continuous with no modelled events (resting state) | — |
| `acquisitions` | The protocols that collected data for this task | Exclude structural scans unless they are part of the task itself |
| `presentation_software`, `instructions`, `stimuli` | As reported | — |
| `response_mode` | Open vocabulary, **multivalued**: `button_press`, `hand_movement`, `speech`, `covert_response`, `eye_movement`, `foot_or_leg_movement`, `oral_nonspeech`, `none` | `none` is a positive assertion — passive viewing, film watching, resting state — and is not the same as leaving the field empty. `covert_response` is a real, instructed, unobservable response, so no accuracy comes with it |
| `performance_measures` | Behavioural variables recorded, and the summaries used to verify performance | Not an `Assessment`: this came out of the paradigm |

A paradigm whose response mode differs *between* conditions records both modes here and the
difference in each condition's `description` — the schema describes stimulus, instruction and response
once for the paradigm rather than per condition.

### 5.3 `Condition`

Two fields besides its identifier — `name` and `description` — and it is thin on purpose.

A `Condition` is only **what happened inside the paradigm**. The occasion the task was run on and the
arm it was run under are a `Timepoint` and an `Arm`, and they enter an analysis as levels of their own
model terms.

Two things a `Condition` does not record:

- **What an effect was measured against.** A contrast against this condition gives it a `negative`
  cell; a contrast against the unmodelled implicit baseline has no second cell. There is no
  `baseline` field anywhere in the schema, and chapter 9 is why.
- **Whether it was compared.** That is a `Cell` on the term whose level names it.

**The rest/fixation rule.** A rest or fixation period *inside* another paradigm is a `Condition` of
that paradigm's `Task`. A standalone resting-state run is its own `Task`. The line is which paradigm
the participant was in, not what they were doing: a fixation baseline between working-memory blocks
belongs to the working-memory task.

### 5.4 Drill

> A study runs a face-matching task, a separate 8-minute resting-state scan, and a T1 for VBM. How
> many `Task` records, and what does the VBM analysis link to?

**Answer.** Two `Task`s — the face-matching paradigm, and the resting-state protocol (a paradigm,
with one condition or none). The T1 gets no `Task`: the VBM analysis links to data through
`Analysis.acquisitions`, and `Analysis.tasks` stays empty. The emptiness is the claim that this
analysis had no paradigm.

### 5.5 Failure modes

| Wrong fill | What it asserts |
|---|---|
| Resting state with no `Task` | The record models conditions that belong to no paradigm, or loses that a paradigm existed |
| A within-paradigm fixation as its own `Task` | The participant was in a separate paradigm |
| A `Task` invented for a structural scan | Participants performed a paradigm they did not |
| `response_mode` left empty for passive viewing | Nobody looked, rather than "no response was required" |

---

## Chapter 6. The data: `Acquisition`, the modality classes, `Device`, `Preprocessing`

**Learning goals.** By the end you can

- count the `Acquisition` records a study needs, and say which class each instantiates;
- fill a modality the schema does not name;
- say what one `Device` record means, and what two of them claim;
- say where a preprocessing pipeline attaches, and why it is not on the analysis.

### 6.1 Zoom out and the type designator

`Acquisition` holds only what every modality has in common. Everything modality-specific lives in the
subclass that the `modality` value **instantiates**:

| `modality` (closed) | Class holding its parameters |
|---|---|
| `fMRI`, `sMRI`, `dMRI`, `MRI` | `MRI` |
| `EEG` | `EEG` |
| `fNIRS` | `FNIRS` |
| `PET` | `PET` |
| `MEG`, `SPECT`, `other` | `OtherModality` |

`acquisition_type` names the class the record instantiates. It is a **type designator**: derived from
the modality value, never read off the page, and in neither provenance subset. `Modality` is the one
closed vocabulary that keeps an `other` member, because that value selects a payload class.

**One `Acquisition` carries one modality.** Simultaneous EEG+fMRI is two `Acquisition` records that a
`Task` lists together, and the schema does not represent their simultaneity.

Shared fields: `acquisition_duration_seconds` (total usable duration — the field a synthesis filters
resting-state studies on) and `device`.

### 6.2 The modality classes

Each carries the parameters that materially change comparability, and nothing more.

- **`MRI`** — `magnetic_field_strength_tesla`, `pulse_sequence_type`, `mr_acquisition_type` (2D/3D),
  `repetition_time_seconds`, `echo_time_seconds` (a list, one per echo — length one for single-echo),
  `acquisition_voxel_size_mm` ([x, y, z] as acquired, before any resampling),
  `number_of_volumes`.
- **`EEG`** — `sampling_frequency_hz`, `eeg_reference`, `eeg_placement_scheme`, `recording_type`,
  `eeg_channel_count`.
- **`FNIRS`** — `sampling_frequency_hz`, `nirs_system_type`, `nirs_channel_count`, `wavelengths_nm`,
  `short_channel_count` (zero means none), `source_detector_distances_mm`, `nirs_placement_scheme`,
  `recorded_signal_types`.
- **`PET`** — `image_units`, `tracer_name`, `tracer_radionuclide`, `mode_of_administration`,
  `scan_type` (static or dynamic), `uptake_time_seconds`, `reconstructed_voxel_size_mm`,
  `spatial_resolution_mm`.
- **`OtherModality`** — `modality_label` (required: the source's own name, verbatim, since `Modality`
  is coarser than what papers report) and `reason_no_modality_class_used`. Nothing here is normalized.
  A parameter that `MRI`, `EEG`, `FNIRS` or `PET` *has a field for* belongs on that class instead.

### 6.3 `Device`

`manufacturer` and `model`, either of which may be missing — papers name a model without a
manufacturer as often as the reverse.

**One record is one physical machine.** A study that collected functional, structural and diffusion
scans in one session has three `Acquisition`s and **one** `Device`. Two `Device` records assert that
the study used two machines — a second site, a rescan on different hardware — which is a claim about
the data, not a way of recording that two sequences differed.

### 6.4 `Preprocessing`

| Field | Holds | The boundary |
|---|---|---|
| `description` | The pipeline, its inputs, outputs and purpose | — |
| `software` | Packages and versions | — |
| `steps` | Operation names **in execution order**, free text | The smoothing kernel is not a phrase in this list |
| `smoothing_fwhm_mm` | FWHM in mm: one value isotropic, three for [x, y, z]. **Record 0 when the source states no smoothing** | The one preprocessing parameter a synthesis filters on, which is why it is structured |

And the connection people miss: **a pipeline attaches to the model, not to the analysis.**
`ModelEstimation.preprocessing` names the pipelines that produced the data the model was fitted to,
because the data a model was fitted to is part of its specification (chapter 8). More than one entry
where the fit consumed more than one kind of image — a multimodal regression of perfusion on
grey-matter volume came through both pipelines, and naming either alone says the other's smoothing
and normalisation did not touch the map.

### 6.5 Drill

> A study scans 30 participants on a Siemens Prisma: a 6-minute EPI run, an MPRAGE, and a
> diffusion scan. It also records simultaneous EEG during the EPI run. Count the records.

**Answer.** Four `Acquisition`s — EPI (`fMRI` → `MRI`), MPRAGE (`sMRI` → `MRI`), diffusion (`dMRI` →
`MRI`), EEG (`EEG` → `EEG`) — and **one** `Device` if the EEG amplifier is not separately named, two
if it is (an amplifier is a recording system, and one record is one machine). The task lists the EPI
and the EEG acquisitions together; nothing records that they were simultaneous.

### 6.6 Failure modes

| Wrong fill | What it asserts |
|---|---|
| One `Acquisition` with two modalities' parameters | A modality the schema cannot name, and unfilterable parameters |
| A `Device` per acquisition | The study used three scanners |
| Resampled voxel size in `acquisition_voxel_size_mm` | Data were acquired at a resolution they were not |
| `smoothing_fwhm_mm` empty for an explicitly unsmoothed analysis | The paper did not say, when it did say zero |
| Pipeline on the analysis "because it is the analysis's data" | There is no such slot; the model is what conditions on the pipeline |

---

## Chapter 7. Places: `Region`

**Learning goals.** By the end you can

- say what makes a brain region worth a `Region` record;
- name the five roles a region plays and pick the right slot;
- explain why `definition_method` is a statement about independence rather than about geometry.

### 7.1 Zoom out

A `Region` is a brain region **the study delimited and then used**. It is declared once on
`Study.regions` and referenced from five places, because one seed written out separately at each use
becomes a new spelling every time — `left VIM`, `VIM seed time series`, `Left VIM connectivity` —
and nothing joins those.

Regions the paper merely *reports finding* are not `Region` records: result coordinates reach the
record through the table parser.

### 7.2 The five roles

Which slot names a region is the entire statement of what it did.

| Slot | The region was |
|---|---|
| `Analysis.regions` | the search space inference was restricted to — empty for whole-brain and searchlight, where the emptiness is the claim |
| `Analysis.defines_regions` | produced *by* this analysis — a localizer's clusters, peaks later used as seeds |
| `ConnectivityDetails.seed_regions` (and `target_regions`) | the seed a connectivity analysis ran from |
| `ModelTerm.region` | the place a column's signal came from — a seed timecourse, an ROI-mean covariate, a PPI physiological regressor |
| `FactorLevel.regions` | one level of a factor **comparing** places |

Two near-misses, and both are worth memorising.

**A whole-brain seed-based analysis has a seed and no search-space region.** Putting the seed in
`Analysis.regions` says inference was restricted to it, which is the opposite of what happened.

**`ModelTerm.region` is singular**, so it cannot serve a factor that compares regions. A three-region
factor is one `ModelTerm` with three levels and the regions on `FactorLevel.regions`.

### 7.3 The fields

| Field | Holds | The boundary |
|---|---|---|
| `name` | The region as the source names it — `aSCC`, `left dlPFC parcel`, `salience network` | Its own words. Mapping onto an anatomical vocabulary happens downstream and needs the original to map from |
| `definition_method` | Required, **closed**: `atlas`, `prior_literature`, `anatomical_a_priori`, `functional_localizer`, `same_study_analysis` | See below — this is the field the schema cares most about |
| `region_type` | Open: `anatomical`, `atlas_parcel`, `network`, `coordinate_sphere`, `functional_cluster` | What `definition_method` does not settle: an atlas parcel and an anatomical structure can both come from an atlas, and a network is neither |
| `atlas` | The atlas, parcellation or template as named, with version or parcel index | `definition_method: atlas` records that one was used, not which — which is not enough to pool two studies |
| `description` | How the source defines it, **including geometry**: "8 mm spheres centered at [0 36 −6]" | Definitional coordinates have no structured slot and belong here, where the evidence span makes them checkable |

**`definition_method` is about independence, not about how functional the region looks.** It is among
the most-corrected fields in the corpus, and the confusion is always the same pair:

- `functional_localizer` means a **separate** acquisition or run whose only purpose was to locate the
  region, independent of the effect later tested inside it.
- A region that is a peak, a cluster, or a component of one of **this paper's own** analyses is
  `same_study_analysis`, however functionally it was defined. That value is what makes the
  circularity visible — and `Analysis.defines_regions` on the analysis it came from is what makes it
  checkable rather than merely asserted.

### 7.4 Drill

> A paper runs a group activation contrast, takes the peak of the resulting cluster as a 6 mm sphere,
> and uses it as a seed for a whole-brain connectivity analysis compared between groups. What region
> records exist, and which slots name them?

**Answer.** One `Region`: name as the paper gives it, `definition_method: same_study_analysis`,
`region_type: coordinate_sphere`, geometry and centre coordinate in `description`. The activation
analysis names it in `defines_regions`. The connectivity analysis names it in
`ConnectivityDetails.seed_regions`, and — because it is whole-brain — leaves `Analysis.regions`
empty. Its `ModelTerm` for the seed timecourse names it in `ModelTerm.region`. Three slots name one
record, a fourth is deliberately left empty, and the circularity is now readable off the record
instead of hidden.

### 7.5 Failure modes

| Wrong fill | What it asserts |
|---|---|
| A seed in `Analysis.regions` | Inference was restricted to the seed |
| `functional_localizer` for a cluster of this study's own contrast | The region was defined independently of the effect tested in it |
| `definition_method: atlas` with no `atlas` value | An atlas was used, unidentifiably — two studies cannot be pooled on it |
| A separate `Region` per use of one region | The study delimited three regions |
| A comparison of regions as `Analysis.regions` | Inference ran over both, and nothing says either was compared |

---

# Part III — The model and the result

Chapters 8–10 are where records go wrong. Everything in them follows from one question:

> **Which facts belong to the model, and which to the contrast?**
>
> Test: would this fact change if the paper reported a *different* contrast off the *same* model? If
> not, it is on the model.

The levels of a factor would not change — a three-level load factor has three levels whichever pair a
contrast compares — so levels are on the model. Which pair was compared would, so it is on the
`Effect`. [representing-models.md §1](representing-models.md) states this; the two chapters below
teach it.

## Chapter 8. The model: `ModelEstimation`, `ModelTerm`, `FactorLevel`

**Learning goals.** By the end you can

- decide how many `ModelEstimation` records a paper needs, by a mechanical test;
- fill a term's `type`, `levels` and `variation_level`, and say what each decides downstream;
- link a two-stage fit, and say what the link buys;
- express a moderation, and say when a product column is wrong.

### 8.1 `ModelEstimation` — one design matrix, at one stage

The model's terms and how it was fit, referenced by every analysis whose contrast came out of it.

| Field | Holds | The boundary |
|---|---|---|
| `model_family` | Required, open: `glm`, `mixed_effects`, `anova`, `ancova`, `robust_regression` | The queryable form. A method the vocabulary cannot name — ICA, RSA, a decoder — is free text here, and the `details` subclass identifies the method anyway |
| `model_type` | Required: the source's own wording, minimally cleaned | "mass-univariate GLM", "representational similarity analysis" |
| `stage` | Required: the source's word for the stage — run, session, subject, group, or anything else it uses | **A label, not an ordering.** Two records both saying `group` say nothing about their relation |
| `estimator` | OLS, WLS, ML, REML, permutation, Bayesian… | — |
| `software` | Name, version, implementation details | — |
| `hrf_model` | The haemodynamic basis the design matrix was built with | Unset for a measure that models no timeseries — morphometry, diffusion, static PET |
| `model_settings` | Variance, autocorrelation, covariance, regularization, software-specific settings | — |
| `spatial_unit` | Open: `voxel`, `vertex`, `roi`, `parcel` — the element the fit ran over | `Analysis.spatial_scope` gives the *extent*. A voxelwise GLM and an ROI-mean ANOVA of one design are two records |
| `preprocessing` | The pipelines that produced the data fitted (§6.4) | — |
| `inputs_from` | The stages whose fitted output this model was estimated on | Must be acyclic |
| `terms` | The columns of **this stage's** design matrix | May legitimately be empty (§8.5) |

**How many records?** One per design matrix per stage. The test is mechanical: list the columns each
analysis was fitted with, and the data they were fitted to. A different dependent variable, a
different covariate set, a different participant subset, a different `spatial_unit`, or a different
`inputs_from` is a different design matrix — even where family, estimator and software are identical.

This is the defect that stays *valid*, which is what makes it dangerous. A contrast's adjustment set
is derived by subtracting celled terms from the term list, so one record standing behind two matrices
makes both claim adjustments that were never in the model that produced them, and nothing in the
record contradicts it.

### 8.2 `ModelTerm` — one column

A column of the design matrix, in the general sense that covers a GLM regressor and the predictor of
any family that has no design matrix.

| Field | Holds | The boundary |
|---|---|---|
| `name` | Required, unique within the model. For a categorical term, the factor's identity | Often a label the source never writes: a paper reporting 1-back/2-back/3-back names the levels and no axis, and `load` is a permitted name for a grouping the source does make. Two limits — the **levels** must be source-stated, and the name must not assert a comparison or a construct the paper does not |
| `type` | Required, **closed**: `categorical` or `continuous` | Settles four other things (§8.3) |
| `levels` | The `FactorLevel`s of a categorical term | Empty for a continuous term. Record a level the source names even where no contrast uses it — a three-level factor contrasted at two levels is still three-level |
| `variation_level` | Open: `within_subject`, `between_subject`, `mixed` | Populate for **any** term, not only continuous ones (§8.4) |
| `assessment` | The instrument supplying a continuous column, **including when the column is derived from it** | One instrument, one record, however many columns it supplies |
| `region` | The place a column's signal came from | Singular (§7.2) |
| `unit` | The unit a continuous term is measured in | What makes a reported slope interpretable |
| `functional_form` | Open: `linear`, `quadratic`, `absolute`, `log` | A squared term with a negative coefficient is an inverted U, not a negative association. A model fitting both a linear and a quadratic form has **two** terms. Not where a product is recorded |
| `interaction_with` | The terms this column is a product of | §8.6 |
| `source_definition` | The source's own definition, and the origin of a continuous column no `Assessment` supplies — age, dose, a motion parameter, cardiac phase, scanner drift | **Required for a derived column**, whatever `assessment` says (§8.4) |

**What a term is not.** It is not the thing being measured. What the model *models* is
`Analysis.measure`, never a term of it. A table laid out with one row-block per measured parameter —
four diffusion metrics, three frequency bands — is one analysis per parameter over one `Measure` each,
not one analysis over a factor whose levels are the parameters. Entered as a term, the dependent
variable falls into its own analysis's derived adjustment set, and the record says the analysis
controlled for the thing it measured.

**And a term does not know whether it was tested.** Nothing marks a column as a nuisance regressor:
having no `Cell` on a given contrast *is* the mark. Which is why the rule is to record **every** term
the source states, including ones no reported contrast tests, and including ones mentioned only in
passing — "controlling for age and sex" is a statement about the design matrix.

### 8.3 `type` decides four things

Small field, wide reach. It settles whether `levels` exists, whether a `Cell` on this term takes a
`level`, whether the term can be crossed, and which branch of the derived effect kind fires.

> **A term whose name states a comparison — `pre > post`, `A versus B` — is almost always a
> categorical factor written down from its contrast's side.**

The design matrix distinguished two occasions, cohorts or conditions; the paper labelled the
difference and left the axis unnamed. Record the factor with a level per side and let the cells carry
the direction. Collapsed into one continuous column, nothing says what was compared, and the effect
reads as a slope. `check_occasion_factors` warns on exactly this shape.

The exception is a column genuinely holding one number per participant — a difference score, a percent
change entered as a covariate. That is continuous, and then:

- `assessment` still names the instrument the numbers came from. Deriving a column does not break the
  link to what supplied it — `region` says the same by example, an ROI mean and a PPI regressor both
  naming their region.
- `source_definition` records the derivation in the source's words **including the occasions it
  spans**: "percent reduction in BDI, (post−pre)/pre, from baseline to post-treatment". Nothing else
  can carry those occasions, because a column with no levels has no `FactorLevel.timepoints`. In a
  study with several post-intervention occasions, this is the only thing separating a change to the
  endpoint from a change to a later follow-up.
- `functional_form` is still the shape of the *fit*. A percent change entered linearly is a linear
  term computed from a difference; recording the construction as the form would lose whether the fit
  was linear or quadratic.

### 8.4 `variation_level`

Whether the term moves *within* a participant or only *across* the sample. Easy to skip, and it does
two different jobs:

- **For a continuous term it decides the effect kind.** A tested continuous term that is
  `within_subject` is a `parametric_modulation`; `between_subject` or `mixed` makes it a
  `cross_subject_regression`. That one field is the whole difference between a trial-wise value
  regressor and a brain–behaviour correlation, and it is on the model because it is a property of the
  measurement rather than of the contrast.
- **For a categorical factor it feeds no derivation** — but two studies that crossed a factor within
  participants and between them did not estimate the same thing, so it is still the factor's scope
  and still worth having.

A factor's levels usually settle it, and the two should agree: levels naming `groups` are
between-subject (a participant belongs to one cohort); levels naming `timepoints` are within-subject
(an occasion is when the same people were measured again); levels naming `conditions` are
within-subject in the ordinary repeated-measures case; levels naming `arms` follow
`assignment_structure` — crossover within, parallel between.

The vocabulary is open, and a free-text value has a specific consequence: the continuous step of the
derivation cannot choose between a modulation and a regression, so the kind is **undetermined** for
that record rather than wrong. Do not coerce a value into `within_subject` to make the step apply.

### 8.5 The stage chain

Neuroimaging splits one model across stages — run, session, subject, group — because fitting it in one
step is not tractable, not because the stages are separate inferences. `inputs_from` is what puts it
back together:

> **A model's terms are its own plus, transitively, those of the stages it names.**

Three things follow, and they are the whole reason the link exists.

1. A first-level nuisance regressor is in the adjustment set of every group contrast taken above it.
   Motion regressed out at the first level adjusts the group betas.
2. A `Cell` may name a first-level column. A group contrast of a task condition, or of a seed's time
   series, is a cell on **that stage's term** — never on a copy hoisted upward.
3. A product column may cross a column of its own stage with one of the stage below, which is how
   `diagnosis × condition` is expressed when condition was fitted per subject.

`Analysis.model_estimation` names the **top** stage, always. A first-level model that no reported
contrast comes from is referenced by no analysis and reached only through the chain — which is
correct: it was estimated, and nothing was reported from it directly.

**When not to split.** `inputs_from` records a stage the *source describes*. A one-sample activation
map has a group stage too — an intercept over the first-level contrast images — and papers say nothing
about it, so it takes no record and the first-level record stands alone. Empty `inputs_from` is the
absence of a statement, not a claim that no lower stage existed. Where such a stage *is* described,
its `terms` is legitimately empty; never invent an intercept term to fill it.

Two constraints live here that LinkML cannot state: `inputs_from` must be **acyclic**, and a
`ModelTerm.name` must be unique **across a whole chain** rather than within one record — otherwise a
first-level `motion` and a group-level `motion` are two columns with one name in one term list, and a
reader cannot tell a column refitted above from one restated there by mistake.

### 8.6 `FactorLevel` and `interaction_with`

`FactorLevel` is one level of a categorical term plus the entities carrying it. Its fields:

| Field | Holds |
|---|---|
| `level` | Required. The label as the source words it; unique within its term |
| `order` | Position from 1, for a factor whose levels are ordered — a dose series, a load series, an ordinal severity grading. Leave unset for a nominal factor, where an order would assert structure the design lacks |
| `conditions`, `groups`, `timepoints`, `arms`, `regions` | The entities carrying this level |

`order` carries what the cells cannot: a contrast signs only its extremes and omits what lies between,
so without it 1-back, 2-back and 3-back arrive as an unordered set.

A factor normally ranges over one of the five entity kinds, so the others stay empty; a level realized
by a crossing — a drug given at follow-up — fills more than one. A factor whose levels are not
entities at all (hemispheres, tasks, frequency bands, sessions) leaves all five empty and is carried
by `level` alone; such a factor is complete, not deficient.

**`interaction_with` is the one crossing that cells cannot express.** Two categorical factors crossed
are readable from the cells: each factor carries a positive and a negative cell, and the crossing
derives. A **continuous** term has no levels, so it cannot be crossed — an `age × group` moderation is
one cell on age and two on group, which reads as a plain regression. The product column is the only
record that it was a moderation, and it is also the only thing that can carry the moderation's own
direction, which is a fact about the crossing rather than about either term's slope.

The converse is the most over-applied field in the schema: **do not add a product column for a
crossing of two categorical factors.** It is legal, since it may really be in the design matrix, but
it decides nothing and is flagged for review. When in doubt, record levels and sides and leave it
empty.

### 8.7 Drill

> "Seed-based connectivity of the left amygdala was computed per participant with white-matter, CSF
> and motion regressors, then compared between patients and controls in a group model with age, sex
> and scanner as covariates." How many `ModelEstimation` records, and what does the group analysis
> name?

**Answer.** Two records. The subject-level one has terms for the amygdala timecourse (continuous,
`within_subject`, `region` → the amygdala `Region`) and the nuisance signals. The group-level one has
a categorical diagnosis term whose two levels name the two `Group`s, plus age, sex and scanner, and
`inputs_from: [the subject-level record]`. The `Analysis` names **only the group record**, and its
adjustment set derives as age, sex, scanner **plus** the first-level nuisance and seed columns — which
is the point of the link. Do not cell the seed: a cell says the contrast tested that column, and a
tested continuous within-subject term derives a parametric modulation, so the diagnosis contrast would
stop reading as a contrast. [representing-models.md §5.12](representing-models.md#512-a-model-estimated-in-two-stages)
is this worked out in full.

### 8.8 Failure modes

| Wrong fill | What it asserts |
|---|---|
| One record behind two design matrices | Each contrast adjusted for covariates that were not in its model |
| A stated covariate in no term list | The model did not adjust for it |
| `pre > post change` as a continuous term | A slope, where a contrast between occasions belongs |
| A derived column with no `source_definition` | An unattributed number, with no way to say which occasions it spanned |
| The dependent variable as a term | The analysis controlled for the thing it measured |
| A product column over two crossed categorical factors | Nothing extra; flagged for review as a likely misreading |
| A first-level term copied onto the group stage | Two columns where the design had one |
| An invented intercept term | A column the paper never described |

---

## Chapter 9. The result: `Analysis`, `Effect`, `Cell`

**Learning goals.** By the end you can

- decide what counts as one `Analysis`, and when a reported map is not one;
- build an `Effect` from cells, and say what the term of a cell means;
- choose among the four `direction` values and the three ways of having no sign;
- name the four things the schema never stores and derive each of them yourself.

### 9.1 What one `Analysis` is

One per **distinct tested effect**: per statistical map, or per effect tested without a map. Separate
analyses are required when the direction, the cohorts compared, the method, the pattern of cells, the
seed, the decoded variable, the component identity, or the spatial scope differ. An omnibus effect and
its directional post-hoc contrast are two analyses.

**What is not an Analysis.** ROI definitions, acquisition summaries, descriptive sample tables, masks,
and any map reported with **no inferential test** — an ICA component map presented descriptively, a
connectivity matrix given without a test. There is nothing for such a map to be the effect *of*, and
`Effect.cells` being required and non-empty is what enforces it.

A coordinate table of one of those is not lost: it is a `Table` whose `non_analysis_content` says what
its rows are. Filling that field is what separates a table deliberately not encoded from one the
extraction missed.

### 9.2 The ten required slots, and what they say

`Analysis` has 21 fields; nine of them plus the identifier are required, and the list is a good
summary of what the schema thinks a result *is*.

| Required | Holds |
|---|---|
| `name` | The study-local label, in the source's words |
| `definition` | Precise statement of the tested effect, in the source's words. Where an ordering or a set of contrast magnitudes lives that the cells cannot carry |
| `prespecification` | **Closed**: `preregistered` or `exploratory`. The source's own claim, never an inference from an absent registration — and a paper that makes neither claim leaves the slot `not_reported`, which is what a required closed field looks like when the page is silent (§2.4) |
| `spatial_scope` | Open: `whole_brain`, `roi`, `searchlight` |
| `measure` | → a `Measure` the study declares |
| `groups` | The `AnalysisGroup` entries: which cohorts ran, and how many of each |
| `effect` | The inlined `Effect` |
| `details` | The inlined method payload (chapter 10) |
| `model_estimation` | → the **top** stage of the model |

The rest: `interpretations` (what the source says came of it), `regions` and `defines_regions` (§7.2),
`acquisitions`, `tasks`, `assessments`, `tables`, `statistical_maps`, `inference_settings`,
`coordinate_space`, `model_representation_notes`.

Two rules on the class are stated in the schema itself, because a reference slot has no
`not_reported` form and without both directions "whole-brain, no ROI" and "the extractor missed the
ROIs" are the same JSON:

- `spatial_scope: whole_brain` or `searchlight` ⇒ `regions` **absent**;
- `spatial_scope: roi` ⇒ `regions` **present**.

`coordinate_space` has a precedence of its own, and it is a precedence rather than an exclusivity.
Both this and `Table.coordinate_space` may be filled, and usually are: the parser fills each table,
and where every table behind an analysis agrees the same parse supplies this field too, leaving the
model to answer only where they do not. **Where the two disagree, the analysis's value wins** — a
table can be parsed as one space while the analysis reporting it states another, and the space of a
result is a property of the analysis. It is never inferred from the model's `spatial_unit`, because an
analysis on a surface reports a surface template.

`AnalysisGroup` is two fields, `group` and `n`, and says **membership only**. It never says what was
compared. A cohort comparison has both: the cohorts are in `groups` because their data were analysed,
*and* they are levels of a crossed term because they were contrasted.

### 9.3 `Effect` — three slots

| Slot | Holds |
|---|---|
| `cells` | Required and non-empty: one entry per level of a model term that entered the comparison, each with the side it sat on. **The only place direction lives** |
| `mediation` | Present only for a mediation analysis; both its fields (`path`, `mediator`) are required, so a path always names its mediator |
| `statistic` | Required: the `Statistic` — `family` (t, z, f, chi_square, likelihood_ratio, beta, correlation) and its degrees of freedom. Not the statistic's *value*: peak magnitudes belong to the reported coordinates |

Degrees of freedom follow the family: the numerator is normally populated only for `f` and
`likelihood_ratio`; a single-degree-of-freedom statistic leaves it unset, since t(151) is F(1,151);
`z` has neither. They are floats so a Greenhouse–Geisser correction can be recorded as reported.

`Mediation.path` is closed — `direct`, `indirect`, `total` — and which path was tested decides the
mediator's status in the adjustment set: a `direct` path is by definition the effect holding the
mediator constant, so there it *is* adjusted for; an `indirect` path is undefined without it, and a
`total` path is estimated without conditioning on it.

### 9.4 `Cell` — the unit a contrast is built from

`term`, `level`, `direction`, and an optional verbatim `label`.

> **The term is the axis of the comparison.** Cells sharing a term compare levels within that factor;
> cells on different terms compare across them.

That is the whole of the encoding, and it is why a condition contrast, a cohort comparison, a pre–post
change, a crossover comparison and a region-by-condition dissociation are all the same shape. What
distinguishes them is what the named term's levels range over — which `FactorLevel` states on the
model, not here.

- `term` must name a `ModelTerm` of the analysis's own model or of a stage it reaches through
  `inputs_from`.
- `level` is required exactly when the term declares levels, and must match one of them. **The join is
  on the string**, so a level cell-ed as `explicit` against a declaration reading `explicit processing
  of emotional facial expressions` is a broken join, not a shorthand. It recurs in audited records and
  it fails quietly: the record still looks like it recorded which level was compared.
- `direction` is a **sign, never a weight**. `[1, −½, −½]` and `[1, −1, 0]` over three levels are not
  distinguished; a contrast whose magnitudes matter keeps them in `Analysis.definition`.
- `(term, level)` is unique within one `Effect`.

**There is no value for a zero weight.** A level the contrast weighted out has **no cell at all**, and
is read back by subtraction against the term's declared levels. A whole factor the contrast averaged
over has no cells at all, which is how it comes to be in the adjustment set.

### 9.5 The four values of `direction`, and the three ways to have no sign

`Direction` is closed: `positive`, `negative`, `undirected`, `held`. Only the first two are
directional. Everything else is a way of taking part without having a side, and they are different
facts:

| The contrast did this | Recorded as | In the adjustment set? |
|---|---|---|
| put the level on the plus side | `positive` | no |
| put it on the minus side | `negative` | no |
| gave the level no weight | **no cell at all** | the *term* is, if no level of it has a cell |
| tested it with an F or χ², which yields no sign | `undirected`, on **every** level | no |
| compared it directionally and did not print which way | a cell whose `direction` is `extraction_status: not_reported` | no |
| held one level constant | `held` on that level, the rest **absent** | no — it took part, at one level |

**Two questions settle which unsigned value.**

1. *Was the level on both sides at once?* If the contrast was taken **within** that level — "patients
   versus controls, in the task condition" puts task on the plus and minus side at once — that is
   `held`. No report, however complete, could sign it: the sign is not missing but undefined.
2. Otherwise, *does this analysis yield a per-level sign at all?*
   - **No → `undirected`.** An F or χ² returns one statistic for the whole set. The post-hoc contrast
     that would supply a sign is a *different Analysis*, so a fuller report of *this* one still could
     not sign these cells.
   - **Yes, and the paper withheld it → `not_reported` on `direction`.** The sign exists in the data
     and is missing from the page, which is missingness like any other.

Two consequences worth holding on to. A `not_reported` direction is **completable** — a corrigendum or
the authors could supply it — and an `undirected` cell is not, because nothing was withheld. And they
derive different kinds: only `undirected` cells make an `omnibus`; a withheld sign leaves the kind the
same as the cells would give if signed.

Cell counts tell `held` from `undirected` in a finished record, with no extra field: an F-tested factor
has **all** its levels celled and unsigned, and a held-constant factor has **one** unsigned level and
the rest absent. A cell that names **no level** — on a slope or a product column — can never sit on
both sides of anything, so it can never be `held`.

### 9.6 The four things that are never stored

This is what the chapter is for. Each of the four is derived from the cells, and each has a reader
who expects a field and will not find one.

**1. What kind of effect it is.** `EffectKind` is a vocabulary that **no slot has as a range**. The
derivation, in order:

```
1. a SIGNED cell on a term with interaction_with     -> interaction     (a moderation)
   an UNSIGNED cell there                            -> falls through to omnibus
2. a cell on a CONTINUOUS term                        -> parametric_modulation   (within_subject)
                                                      -> cross_subject_regression (between/mixed)
3. count the CROSSED terms -- those with both a positive and a negative cell
     2 or more                                        -> interaction
     exactly 1                                        -> contrast
4. none crossed, some cell signed                     -> simple_effect
5. cells present and every one `undirected`            -> omnibus
6. none crossed, a term COMPARED with `not_reported`   -> the kind those cells would give if signed
```

"Crossed" rather than "signed" is the load-bearing word in step 3: a term signed once has not been
compared against itself, which is why a cohort comparison of an activation contrast is a `contrast`
and not an interaction — the cohort term is crossed, the condition term merely signed.

**2. What it was adjusted for.** The terms of its `ModelEstimation`, plus those of every stage reached
through `inputs_from`, minus the terms its cells name, minus any product column whose components are
all crossed. Being a column of the design matrix *is* what adjusting for something is, so there is no
covariate list and nothing marks a term as nuisance.

**3. What it was tested against.** A modelled reference is that condition's level with a `negative`
cell; the unmodelled implicit baseline, or zero, is the **absence of a second cell**; a decoding
metric's reference is `PerformanceMetric.reference_value`. There is no `Effect.baseline`, and the
chance case is why one field could not have worked: accuracy against chance and AUC against 0.5 are
two references on one analysis.

**4. Which kind of comparison it was** — between cohorts, conditions, occasions, arms or places. Read
off the `FactorLevel`s the cells' levels reach (§1.3).

**A lone signed cell is a test against the implicit baseline.** A single `+1` weight tests that
coefficient against zero, and zero is what the unmodelled baseline is. The sign still says activation
or deactivation, which recording "no cells" would throw away — while also dropping the tested
condition into the derived adjustment set.

### 9.7 Drill

> A 2 × 2 ANOVA crosses group (patients, controls) with task (rotation, comparison). The paper reports
> the main effect of group as an F, the group × task interaction as an F, and "rotation > comparison in
> patients". Three analyses, one model. Give the cells and the derived kind for each.

**Answer.**

| Result | Cells | Kind | Adjusted for |
|---|---|---|---|
| main effect of group | both group levels `undirected`; **no task cells** | `omnibus` | task |
| group × task F | all four levels `undirected` | `omnibus` | — |
| rotation > comparison in patients | task crossed (`positive`/`negative`), plus `held` on `patients` | `contrast` | — |

The third row is where records fail: without the `held` cell, "rotation > comparison in patients" and
the same simple effect in controls are the *same two cells*, and the record cannot tell them apart.
And note the first row — averaging over a factor is the **absence** of its cells, which is also how it
comes to be adjusted for them.

### 9.8 Failure modes

| Wrong fill | What it asserts |
|---|---|
| `Cell.level` abbreviated relative to the declaration | The comparison is unrecoverable from the entity side; the string join fails |
| Omitting the cells of an F-tested factor | The analysis controlled for the factor it tested |
| `held` on every level of a factor | The factor was held on both sides of its own test — a claim about every level at once, so about none |
| `not_reported` on an F's cells | A direction was withheld when the test never produced one; turns an omnibus into a contrast |
| No cell for an unreported direction | Denies a weight the contrast gave |
| Both classes of a decoder signed | The paper reported which way the classifier erred |
| Two analyses, identical cells, one model, contradictory prose | The same estimand is both an interaction and a main effect; at most one is right |
| A seed cell-ed on a connectivity contrast | The contrast tested the seed timecourse — deriving a parametric modulation instead of a contrast |

---

## Chapter 10. The method payload and the shared satellites

**Learning goals.** By the end you can

- choose among eight `AnalysisDetails` subclasses, and say why choosing is stating the method;
- fill the payload for a decoder, an RSA, a connectivity analysis, a conjunction and a decomposition;
- share `Measure` and `InferenceSettings` records correctly;
- record a table's role, including when it reports no effect.

### 10.1 Which subclass is filled *is* the method

`Analysis.details` is one required slot holding one payload, whose `details_type` names which of the
eight it is. There is no `analysis_type` field on `Analysis` to keep in step with it.

| Subclass | Use it for | Required in it |
|---|---|---|
| `MassUnivariateDetails` | The default family: voxelwise, vertexwise or ROI-wise GLM, mixed-effects model, ANOVA, ANCOVA, robust regression | Nothing — it adds no fields, so **naming it is the whole payload** |
| `DecodingDetails` | Classification or pattern discriminability | `decoded_variable`, `performance_metrics` |
| `SimilarityDetails` | Representational similarity, at the RDM level | — |
| `ConnectivityDetails` | A relationship between regions, from a signal or across subjects | `connectivity_method` |
| `ConjunctionDetails` | A logical combination of independently defined effects | — |
| `LatentDecompositionDetails` | One component or latent variable of a decomposition — ICA, PCA, NMF, PLS, CCA, multimodal fusion | `method`, `second_block`, `component_id` |
| `OtherAnalysisDetails` | A first-class-shaped method outside the families, e.g. mediation | `method_label`, `reason_first_class_type_not_used` |
| `NotStructurableDetails` | An effect grounded in the source with no stable structured decomposition | `reason`, `explanation` |

Naming `MassUnivariateDetails` is how mass-univariate gets **asserted** rather than inferred from
silence. And whatever the payload, **what was tested always lives in `Effect`**: a connectivity,
decoding, RSA or component analysis states its tested effect in exactly the same fields a voxelwise
GLM does.

### 10.2 The five substantive payloads

**`DecodingDetails`.** `decoded_variable` (what was predicted), `classes` (the classes discriminated —
non-directional; direction lives in `Effect`), `performance_metrics`, `validation_scheme`,
`generalization` (the train/test split for cross-decoding). A `PerformanceMetric` is `name`, `value`
(in the source's own scale — a percentage and a proportion are recorded as written, since converting
would invent precision), `reference_value` and `relation` (`above_reference`, `below_reference`,
`not_applicable`). Record **every** metric the paper reports: accuracy with sensitivity and
specificity is three entries, each with its own reference.

The encoding rule that goes with it: above-chance decoding is **one** signed cell, on the class the
accuracy is *for* — not one per class. Signing both would assert a directional contrast between the
classes that an accuracy against chance never makes.

**`SimilarityDetails`.** `model` (the model RDM tested; unset for a genuinely model-free test),
`neural_representation`, `similarity_metric`, `competing_models`. Check before choosing this family:
the association must be at the **RDM level**. A similarity regressor entered into a univariate GLM is
a parametric modulation, not RSA.

**`ConnectivityDetails`.** `connectivity_method` (required, open: `ppi`, `gppi`, `seed_based`, `dcm`,
`granger`, `coherence`, `structural_covariance`, `multivariate`), `seed_regions`, `target_regions`
(empty for seed-to-whole-brain), `edges`, `modulatory_input` (for DCM), `inference_target`
(`connectivity_parameter`, `model_comparison`, `network` — a model comparison and a parameter test are
not poolable).

**Nothing in this payload carries direction.** A negative PPI is a `negative` cell on the term the
coupling was estimated for, and coupling higher in one condition than another is that condition's
term crossed — the same encoding a mass-univariate contrast uses. A whole-map sign recorded in the
payload as well would be a second route to one fact, and the two can disagree.

A `ConnectivityEdge` carries `source_region`, `target_region` and `directionality` — and that last one
is **derived from `connectivity_method`**, not read off the page: only DCM and Granger causality
support a claim about which region influences which, whatever the paper's wording, so the mapper looks
it up (§2.5) and leaves it unset for a method written as free text.

Structural covariance shares this payload deliberately: correlating thickness between a seed and every
other region *across participants* gives the same shape of answer, with no time series.

**`ConjunctionDetails`.** `components` (the independently defined effects, each a
`ConjunctionComponent` with a required source-grounded `definition` including its direction, plus an
optional `analysis_id` pointing at the sibling analysis and its own `analysis_type`),
`null_hypothesis` (**closed**: `global_null` = at least one component present; `conjunction_null` =
every component present — only this licenses the strong reading), `implementation`
(`minimum_statistic`, `masking`, `logical_or`). The hypothesis and the procedure are recorded
separately because they are different statements.

**`LatentDecompositionDetails`.** Fourteen fields; the three required ones are the ones that make two
decompositions comparable. `method` (the algorithm, which decides what the values and signs mean),
`second_block` (**multivalued**, what the brain block was decomposed against — `[none]` for a
single-block ICA, which is a positive assertion, and one value per additional block, so a
three-modality fusion carries `other_imaging_modality` twice), and `component_id` (without which two
analyses from one decomposition are indistinguishable).

Then: `second_block_assessments` (when a behavioural block is made of this study's `Assessment`s),
`second_block_detail` (what a non-assessment block held, in the source's words),
`component_label`, `total_components`, `dimensionality_selection_method`, `selection_criterion`,
`variance_explained_percent` (within the brain block) and `crossblock_covariance_percent` (between
blocks — **not interchangeable**, and meaningless when `second_block` is `[none]`), `map_quantity`,
`salience_threshold` (a bootstrap-ratio cutoff is a *stability* measure, not a test statistic, so it
is not `InferenceSettings`), and `polarity_semantics` (`meaningful` / `arbitrary` / `unsigned` — a
latent variable's sign is arbitrary up to a flip, so a paper must say what positive means before a
direction can be pooled).

One analysis reports **one** component; a second component from the same decomposition is a second
`Analysis`. And a component map presented descriptively with no test is not an Analysis at all.

### 10.3 The two escape hatches, and how to choose

- **`OtherAnalysisDetails`** — the method is first-class in shape but outside the families. It names
  itself in `method_label` (the paper's own words, since this is the only record of what the method
  was) and says in `reason_first_class_type_not_used` why nothing fits. `structured_details` holds a
  JSON payload where the method has structure worth keeping.
- **`NotStructurableDetails`** — the effect has no stable structured decomposition at all. `reason` is
  a short open vocabulary (`method_specific_derived_metric`, `insufficient_standard_structure`,
  `source_too_vague`) and `explanation` says what specifically was lost. `Analysis.definition` stays
  required, so the meaning survives even where the structure does not.
- **`Analysis.model_representation_notes`** — neither of the above. This is for a first-class method
  whose *model* has a component the schema represents only approximately: random slopes, latent
  variables, dynamic connectivity, dependencies between terms.

### 10.4 `Measure` — what the map is of

Independent of the statistic that tested it. `family` (required, open: `functional_bold`,
`structural_morphometry`, `diffusion`, `perfusion`, `molecular_imaging`, `electrophysiology`), `type`
(required, open, 20 values), `source_label` (the paper's own term), `specific_metric` (a named metric
where `type` is intentionally broad — fractional anisotropy, ALFF, a tracer's binding potential),
`unit`.

Three boundaries:

- **Derived measures take the family of the underlying signal.** BOLD connectivity and decoding
  accuracy are `functional_bold`.
- **Activation and deactivation are directions, not measure types.** Both are `bold_response`; the
  cells carry the direction. Grey-matter density is *not* collapsed into volume.
- **Where a paper alternates terms** — density in Methods, volume in Results — take the Methods term
  for `type` and keep both wordings in `source_label`.

Sharing is the common case: two analyses share a record when `family`, `type`, `specific_metric` and
`unit` all match, so eight analyses of one connectivity measure are **one** `Measure`.

### 10.5 `InferenceSettings` — how it was thresholded

Fourteen fields besides its identifier, all optional, and the canonical home for thresholding:
`inference_level`, `height_threshold_value` and `_type`, `cluster_extent_threshold`,
`clusterwise_threshold_value`, `multiple_comparison_method`, `correction_scope`, `search_volume`,
`neighborhood_definition`, `tfce_used`, `tfce_parameters`, `permutation_count`, `alpha_level`,
`number_of_tests`.

**Three thresholds, and they are three different facts.** `height_threshold_value` is the threshold
applied to each element's statistic — papers call it the *voxelwise* threshold when elements are
tested individually and the *cluster-forming* threshold when survivors are grouped, but it is one
number applied once to the map, so it is one field. `clusterwise_threshold_value` is the p or q a
whole cluster had to beat, and `cluster_extent_threshold` the size it had to reach. "p < 0.001
uncorrected, cluster-level p < 0.05 FWE" fills the first and the second, not the first twice.

**Inference attaches to the test, not to the fit.** One model estimated once is routinely thresholded
several ways — an ROI test Bonferroni-corrected over four regions, a searchlight over the same fit
corrected by FDR — so this is a reference on the `Analysis` and never a property of the
`ModelEstimation`. Two analyses share a record when the threshold, its type, the correction method,
the scope corrected over and the alpha are all the same; a paper that states one scheme in Methods and
applies it throughout has one record.

The test statistic itself is not here: it is `Effect.statistic`.

### 10.6 `Table` and `StatisticalMap`

A `Table` is a publication table supplying coordinates, statistics, or context: `table_number`,
`title`, `caption`, `footer`, `column_headings`, `description`, plus the deterministic
`source_path`, `coordinate_space` and `coordinate_count` from the ingestion pipeline and the parser.

`non_analysis_content` is the field that earns the class its place in this chapter. Open vocabulary,
filled **only** when the rows are not the foci of a reported effect: `region_definitions`,
`atlas_or_parcellation`, `connectivity_seeds`, `component_peaks`,
`prior_literature_coordinates`, `stimulus_or_task_list`, `demographics`,
`descriptive_statistics`.

> A coordinate table is not an analysis merely by having coordinates in it, and this is the only field
> that can say so.

Leave it absent when the table reports results, which is the common case. Filling it is what
distinguishes a table deliberately not encoded from one the extraction missed — without it the two
are the same silence, and the coordinates belong on the entity they locate (a `Region`'s
`description`) rather than on a contrast that never produced them.

`StatisticalMap` is fully deterministic — `map_type`, `url`, `coordinate_space`, `is_thresholded`,
`statistic_type`, `description` — and comes from a repository lookup, because a paper that shares maps
says so in a data-availability sentence and the sentence is not the map.

### 10.7 Drill

> A paper decodes faces vs houses with a searchlight, reporting 71% accuracy against a 50% chance
> level, and separately compares that accuracy between patients and controls. What payloads, what
> measures, what cells?

**Answer.** Two analyses. Both take `DecodingDetails` with `decoded_variable` naming the
face/house distinction and `classes` listing both; both have `spatial_scope: searchlight` on the
`Analysis` (spatial scope is never in the payload) and both share one `Measure`
(`functional_bold` / `decoding_performance`). The first has one signed cell on the term the classifier
ran within, and a `PerformanceMetric` with `value: 71`, `reference_value: 50`,
`relation: above_reference`. The second is a crossed cohort term — two directional cells on the
diagnosis factor — and its own performance metrics if the paper gives them per group. The payload does
not change shape because the question changed; the cells do.

### 10.8 Failure modes

| Wrong fill | What it asserts |
|---|---|
| No payload chosen for a plain GLM | Nothing states the method; `details` is required, and silence is not mass-univariate |
| `OtherAnalysisDetails` for a method that has a first-class family | The method is outside the families, so nothing filters it |
| A `Measure` per analysis | The study measured eight quantities |
| `InferenceSettings` shared across two different corrections | Both analyses were corrected the same way |
| A bootstrap ratio in `height_threshold_value` | A stability measure is a test statistic |
| A cluster-level p in `height_threshold_value` | Every element had to beat a threshold that only whole clusters did |
| `crossblock_covariance_percent` with `second_block: [none]` | Covariance between blocks that do not exist |
| A results table left unreferenced and unmarked | Nothing says whether it was deliberately skipped or missed |
| A directed edge from a correlation | The method supports a causal claim it cannot |

---

# Part IV — The rules

## Chapter 11. Vocabularies, missingness, and how the schema says "no"

**Learning goals.** By the end you can

- record silence in the one way the schema accepts, and distinguish it from three things that look
  like silence;
- tell an open vocabulary from a closed one and know what to do at each;
- say which fields keep the source's words and which are normalized;
- say what the schema deliberately does not encode about meaning.

### 11.1 Missingness has one encoding

> **A value the article does not report takes `extraction_status: not_reported`.**

No vocabulary offers an `unstated` member, and `check_schema.py` fails the build if one appears —
silence recorded two ways is silence a query finds half of.

Nothing is lost by that rule, because a `not_reported` wrapper is still **present**: everything the
slot's presence asserts survives. That a test was run comes from the `Analysis` existing at all; that
a table holds no tested effect comes from `non_analysis_content` being filled.

Three things look like silence and are not. Each names something the source *did* report:

| Value | Means |
|---|---|
| `not_applicable` | The concept does not apply. An observational study allocated nobody |
| `undirected` | The test yields no per-level sign. An F reports that, and it is a different claim from a sign the page withheld |
| `none`, `other` | An asserted absence and an asserted unlisted value. `response_mode: none` says no response was required; `second_block: [none]` says the brain data were decomposed alone |

### 11.2 Open and closed vocabularies

| | Binding | What to do when nothing fits |
|---|---|---|
| **Open** (26 fields) | `any_of: [<Enum>, string]` | Write what the paper says, rather than forcing the nearest match. The escape hatch is not a formality: accumulating free-text answers is the evidence for whether the vocabulary is short a value |
| **Closed** (10 fields) | a bare enum range | There is no alternative. These carve a space rather than cataloguing observations, so translate the paper's wording into one of the members |

The closed ones, one field each: `Direction`, `TermType`, `Prespecification`, `EffectPath`,
`RegionDefinition`, `Modality`, `NullHypothesis`, `PolaritySemantics`, `PerformanceRelation`, and
`EdgeDirectionality` — which is closed *and* derived, so nine of the ten reach the extraction schema
and an extractor is asked for nine. `EffectKind` is bound by no field at all (§9.6), which is why the
37 vocabularies come to 26 + 10 + 1.

Whether a field is open or closed is storage's decision, and the projection may not revisit it:
`check_extraction_to_storage_map.py` fails if extraction opens a closed vocabulary or closes an open
one. The second direction is the quiet one — closing an open vocabulary works fine and costs you the
signal that a value is missing.

An off-vocabulary value is a signal **for review**, not a validation failure. There is one place it
has a further consequence: a free-text `variation_level` leaves the effect kind undetermined for that
record (§8.4).

### 11.3 Which fields keep the source's words

A recurring pattern, and knowing which side a field is on saves a lot of second-guessing.

| The source's own words | Normalized |
|---|---|
| `Analysis.name`, `.definition`, `.interpretations` | `Analysis.spatial_scope`, `.prespecification` |
| `Measure.source_label`, `.specific_metric` | `Measure.family`, `.type` |
| `ModelEstimation.model_type`, `.stage`, `.estimator`, `.software` | `.model_family`, `.spatial_unit` |
| `ModelTerm.name`, `.source_definition`, `.unit` | `.type`, `.variation_level`, `.functional_form` |
| `FactorLevel.level`, `Cell.label` | `Cell.direction` |
| `Timepoint.name`, `Arm.name`, `Arm.agent` | `Timepoint.relation_to_intervention`, `Arm.arm_kind` |
| `Region.name`, `.atlas`, `.description` | `Region.definition_method`, `.region_type` |
| `CategoryDistribution.category`, `.reporting_framework` | — |
| `Group.medical_condition`, `.medication_status` | `Group.species` |

Two fields that look as though they should be vocabularies are deliberately free text.
`ModelEstimation.stage` is, because no vocabulary fits every design and nothing derives anything from
it — the stage graph is `inputs_from`. `Assessment.assessment_type` is, because the range of
instruments is open: create a category when none of the usual ones fit.

Where the levels have an order the source states or plainly implies, that goes in a dedicated field
rather than into the naming: `FactorLevel.order` for a factor, `Timepoint.order` for occasions. Leave
both unset for a nominal set, where an order would assert structure the design does not have.

### 11.4 What the schema deliberately does not encode

No field in this schema binds a subject-matter vocabulary, and none stores a cognitive concept,
construct or domain. Values are the source's own wording; mapping them onto ONVOC, Cognitive Atlas,
MeSH or an anatomical vocabulary is a **later stage** that reads the free text and its evidence
sentences. That is why `Region.name` is "the source's own words" and not a normalized anatomical
label — the downstream mapper needs the original to map from.

### 11.5 Drill

> Four cases: (a) a paper never states its smoothing kernel; (b) it states that no smoothing was
> applied; (c) an observational study with no intervention, filling `allocation`; (d) a paper compares
> patients and controls with a t-test and never says which direction. Fill each.

**Answer.** (a) `not_reported`. (b) `smoothing_fwhm_mm: [0]` — a reported zero is a value.
(c) `allocation: not_applicable` — the concept does not apply, and the source did report a design in
which nothing was administered. (d) two cells on the diagnosis term, each with
`extraction_status: not_reported` on `direction` — the comparison happened and its sign is missing
from the page. Not `undirected`, which would say the test produced no sign; not omitted, which would
deny that the cohorts were compared.

---

## Chapter 12. Filling a record end to end, and the checks that will catch you

**Learning goals.** By the end you can

- work in an order that makes the hard decisions once;
- name the checks that run over a finished record, and read their output;
- decide what to do when the paper does not fit the schema.

### 12.1 The gates come first

Two checks run on PubMed metadata **before any text is read**, and both exist because a
meta-analysis has no participant sample, no acquisition and no measure of its own — the three things
an `Analysis` is built around, so recording one means inventing a cohort.

1. Skip when `Study.study_type` includes `Meta-Analysis`.
2. Skip when the title contains "meta-analysis", case-insensitively.

Both are needed: the type label is precise but missing from unindexed records, and the title gate
closes that gap. A primary study that *runs* a meta-analysis as one step is correctly kept — and that
embedded analysis still has no sample, which is a known limit rather than something to encode.

### 12.2 An order of work

The dependencies run one way, so the work does too. In the pipeline this is two model passes — one
over everything the analyses point at, one over the analyses themselves — and doing it by hand in the
same order is what stops you inventing entities to satisfy a cell.

1. **Frame.** `Study` metadata, `StudyDesign`, arms, timepoints (chapter 3).
2. **Entities.** Groups, assessments, tasks and conditions, acquisitions, devices, preprocessing
   pipelines, regions (chapters 4–7). Everything a model or an analysis will point at.
3. **Measures and inference schemes.** The quantities the study measured and the thresholding schemes
   it applied, each declared once (§10.4–10.5).
4. **Models.** One `ModelEstimation` per design matrix per stage; terms; levels; the entity references
   on each level; `inputs_from` where the source describes a lower stage (chapter 8).
5. **Analyses.** One per distinct tested effect. For each: cells, statistic, payload, measure,
   cohorts and n, tables, scope and regions (chapters 9–10).
6. **Tables.** Every table either named by an analysis or marked with `non_analysis_content` (§10.6).

Two habits worth forming. When a step needs an entity that does not exist, **go back and declare it**
rather than writing the fact in prose — an entity named only in an `Analysis.definition` is invisible
to every query. And when a paper's wording resists, ask the one question of chapter 8: would this fact
change if a different contrast came off the same model?

### 12.3 What the validator checks

`review/validate_record.py` runs three tiers over a finished extraction record. Errors mean the record
is wrong; warnings mean a reviewer has to look.

**Tier 1 — conformance (errors).** Types, required slots, closed vocabularies, the evidence
invariants of §2.3, span offsets against the normalized text, and the source-text hash. Plus the
class `rules` the projection dropped — the two on `Analysis` and the non-empty `cells` rule on
`Effect`.

**Tier 2 — structure (errors).** The invariants LinkML cannot express because they span a multivalued
nested slot or a sibling class:

| Check | Catches |
|---|---|
| `Cell.term` in scope | A cell naming no term, or a term of a model this analysis does not reach through `inputs_from` |
| `Cell.level` agrees with its term | A level the term does not declare — the string join the mapper depends on |
| `Effect.mediation.mediator` in scope | The same, for the one other term pointer |
| `inputs_from` acyclic | A model fitted on its own output; a hang as well as a falsehood |
| term names unique across a chain | A first-level `motion` indistinguishable from a group-level one |
| references resolve | A dangling `Analysis.measure`, `Analysis.inference_settings`, `Acquisition.device` or `Group.diagnostic_instrument` |
| table purpose | A table marked non-analysis that an analysis nonetheless names |

One invariant is stated on the field it constrains and is **not** checked here: the `is_healthy`
contradiction of §4.2, which a record audit in the review layer catches instead.

**Tier 3 — prose-triggered review (warnings).** These read a name or a definition, so they route a
record to review rather than rejecting it. Each catches a defect that is invisible to tiers 1 and 2 —
every cell resolves, every level agrees, and the record is structurally perfect while the comparison
the paper reported has gone missing.

| Check | The defect |
|---|---|
| crossings | Prose names an interaction that the cells do not record: no product column and fewer than two crossed terms. Or two analyses with identical cells on one model whose prose disagrees about what was tested |
| product columns | A product column whose components are outside its chain, or that no cell anywhere names |
| unsigned cells | `held` on a cell with no level, or on *every* declared level of a factor |
| occasion factors | A continuous term named after a comparison; or several timepoints declared, analyses reporting change over time, and no `FactorLevel.timepoints` naming any |
| arm reachability | Prose names an arm that neither `FactorLevel.arms` nor `Group.arm` reaches (§3.5) |
| derived columns | A derived column with no `source_definition`, or none naming its `assessment` |
| table purpose | A table no analysis names and that carries no `non_analysis_content` |

A record is also routed to human review by count: `not_reported` fields per record, required fields
left empty, a failing structural check, and any value outside a vocabulary.

### 12.4 Reading a record back out

The three questions a synthesis asks, and the walk that answers each. None has a slot.

- **What was compared?** For each `Cell`, follow `term` to its `ModelTerm` — in the analysis's model
  or, through `inputs_from`, a stage below — match `level` against that term's `FactorLevel`s, and
  read the entity slots. Cells sharing a term compare within a factor; cells on different terms cross.
- **What was it adjusted for?** The model's terms plus every stage reachable through `inputs_from`,
  minus the terms the cells name, minus a product column whose components are all crossed.
- **Who was in it?** `Analysis.groups`, each entry's `group` and `n`.

If you can do these three walks on a record you have filled, it is probably right. If any of them
comes back empty on an analysis that plainly compared something, the middle layer is missing.

### 12.5 When the paper does not fit

In order of preference:

1. **A crossing whose parameterization the paper never states.** Record "faces vs houses" as one
   factor with two levels. The cells describe the comparison either way and nothing in the derivation
   depends on the choice.
2. **A factor the paper does not name.** Supply a name for a grouping the paper *does* make. The
   levels must be the source's own.
3. **Magnitudes that matter.** `direction` is a sign, so put the weighting in `Analysis.definition`.
4. **A model component represented only approximately** — random slopes, latent variables, dynamic
   connectivity. `Analysis.model_representation_notes`.
5. **A method outside the families.** `OtherAnalysisDetails`, naming itself.
6. **A method with no stable decomposition at all.** `NotStructurableDetails`.
7. **A value no vocabulary can name.** Write the source's wording on an open field (§11.2).
8. **Nothing was tested.** Then it is not an Analysis. Mark the table instead.

[representing-models.md §6](representing-models.md#6-when-the-paper-does-not-fit) is the same list
stated as rules.

### 12.6 What no field holds

Worth knowing before you go hunting for a slot. The full list with evidence is in
[extraction-readme.md §5](extraction-readme.md); the ones that come up most:

- **Non-imaging outcomes.** `MeasureFamily` covers imaging signals only, so a trial's behavioural and
  clinical endpoints and its biospecimen assays have no analysis-level home. The design is recorded;
  its result is not.
- **Intervention delivery detail** — dose, unit, route, schedule, washout, counterbalancing. Record
  the manipulation as an `Arm` and leave the dose out.
- **Test tail** (one- vs two-tailed), **nested corrections**, **Bayesian inference**, and a **cluster
  extent in physical units** — `InferenceSettings` is frequentist and its extent is an integer count.
- **Frequency band** for EEG/MEG power — use `Measure.specific_metric`.
- **The middle rank of an ordered contrast** `A > B > C`, and **numeric contrast weights**.
- **That a measure is derived** — a change score, a graph metric, an intersubject correlation. The
  quantity goes in `Measure.source_label` and `specific_metric`; its derivedness is not recorded.
- **Stimulus and response laterality**, and per-condition stimulus/instruction/response.

### 12.7 Final drill

> You have finished a record. Name five things you can check in under a minute that catch most defects.

**Answer.**
1. Every analysis's cells reach a `FactorLevel` with an entity slot filled — or you can say why not
   (a factor over non-entities, a slope).
2. Every covariate the paper mentions appears in some stage's `terms`.
3. No two analyses with different design matrices share a `ModelEstimation`.
4. Every declared `Timepoint` and `Arm` is reached by a `FactorLevel` or a `Group.arm`.
5. Every table is either named by an analysis or marked with `non_analysis_content`.

---

## Appendix A. Class inventory

45 classes. "Declared in" is where the record lives; everything else references it by identifier. The
required column leaves out `id`, which every class that has one requires; "Fields" counts it.

| Class | Declared in | Required fields | Fields |
|---|---|---|---|
| `Study` | the record root | `title`, `study_type` | 23 |
| `StudyDesign` | `Study.design` (inlined) | — | 6 |
| `Arm` | `StudyDesign.arms` | `name`, `arm_kind` | 5 |
| `Timepoint` | `StudyDesign.timepoints` | `name`, `relation_to_intervention` | 5 |
| `Group` | `Study.groups` | `name`, `species` | 34 |
| `CategoryDistribution` | five `Group` slots | `category` | 5 |
| `Assessment` | `Study.assessments` | `name` | 4 |
| `Task` | `Study.tasks` | — | 11 |
| `Condition` | `Task.conditions` | `name` | 3 |
| `Acquisition` | `Study.acquisitions` | `acquisition_type`, `modality` | 5 |
| `MRI` / `EEG` / `FNIRS` / `PET` | subclasses of `Acquisition` | — | 7 / 5 / 8 / 8 |
| `OtherModality` | subclass of `Acquisition` | `modality_label` | 2 |
| `Device` | `Study.devices` | — | 3 |
| `Preprocessing` | `Study.preprocessings` | — | 5 |
| `Region` | `Study.regions` | `name`, `definition_method` | 6 |
| `Measure` | `Study.measures` | `family`, `type` | 6 |
| `InferenceSettings` | `Study.inference_settings` | — | 15 |
| `ModelEstimation` | `Study.model_estimations` | `model_family`, `model_type`, `stage` | 12 |
| `ModelTerm` | `ModelEstimation.terms` | `name`, `type` | 11 |
| `FactorLevel` | `ModelTerm.levels` | `level` | 7 |
| `Analysis` | `Study.analyses` | 9 — see §9.2 | 21 |
| `Effect` | `Analysis.effect` (inlined) | `cells`, `statistic` | 3 |
| `Cell` | `Effect.cells` | `term`, `direction` | 4 |
| `Statistic` | `Effect.statistic` (inlined) | `family` | 3 |
| `Mediation` | `Effect.mediation` (inlined) | `path`, `mediator` | 2 |
| `AnalysisGroup` | `Analysis.groups` | `group` | 2 |
| `AnalysisDetails` | abstract | `details_type` | 1 |
| `MassUnivariateDetails` | `Analysis.details` | — | 0 |
| `DecodingDetails` | `Analysis.details` | `decoded_variable`, `performance_metrics` | 5 |
| `PerformanceMetric` / `DecodingClass` | inside `DecodingDetails` | `name` / `label` | 4 / 2 |
| `SimilarityDetails` | `Analysis.details` | — | 4 |
| `ConnectivityDetails` | `Analysis.details` | `connectivity_method` | 6 |
| `ConnectivityEdge` | `ConnectivityDetails.edges` | — | 3 |
| `ConjunctionDetails` | `Analysis.details` | — | 3 |
| `ConjunctionComponent` | `ConjunctionDetails.components` | `definition` | 3 |
| `LatentDecompositionDetails` | `Analysis.details` | `method`, `second_block`, `component_id` | 14 |
| `OtherAnalysisDetails` | `Analysis.details` | `method_label`, `reason_first_class_type_not_used` | 3 |
| `NotStructurableDetails` | `Analysis.details` | `reason`, `explanation` | 2 |
| `Table` | `Study.tables` | — | 11 |
| `StatisticalMap` | `Analysis.statistical_maps` | `map_type` | 7 |
| `ExternalDataset` | `Study.external_datasets` | — | 2 |

## Appendix B. Where everything is

**The schema.** `neuroimaging-study-storage.yaml` plus eleven modules under
[neuroimaging-study-storage/](neuroimaging-study-storage/): `subsets`, `design`, `study`, `group`,
`task`, `assessment`, `region`, `acquisition`, `analysis_enums`, `analysis_details`, `analysis`.
`analysis_enums` holds 27 of the 38 vocabularies, separately so that the analysis and
analysis-details modules can both bind to them without either importing the other.
[neuroimaging-study-extraction/](neuroimaging-study-extraction/) is the generated projection and is
never hand-edited.

**The uniqueness rules**, each saying "recorded twice is one thing": `ModelTerm.name` within its
model, `FactorLevel.level` within its term, `(term, level)` within one `Effect`,
`AnalysisGroup.group` within one analysis.

**The checks.**

```bash
python3 check_schema.py                        # both schemas load; no rule names a missing slot
python3 check_field_provenance.py --strict     # every field says how it gets filled
python3 check_extraction_to_storage_map.py     # the projection has not drifted
python3 gen_extraction_schema.py --check       # the committed extraction tree is current
python3 -m pytest                              # everything the review layer stands on
python3 review/validate_record.py --record <id>.extraction.json --text <text>
```

**Worked records.** [representing-models.md §5](representing-models.md#5-worked-models) has twelve,
each with the sentences from the paper it came from and a referent record. Three complete extraction
records live under `review/examples/`, each with a corrections note saying what the extractor got
wrong and why.
