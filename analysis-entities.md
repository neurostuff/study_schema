# The entities around an analysis

What each thing surrounding an `Analysis` is called, what owns it, and what points at what.

This is the map, not the instructions. [representing-models.md](representing-models.md) says how to
encode a reported result and what to do when a paper resists;
[neuroimaging-study-storage/](neuroimaging-study-storage/) says what each field is and how to fill
it. Read this first if the class names do not yet mean anything, or if you know the statistics and
want to know which of these words the schema has borrowed for something narrower.

---

## 1. Three layers

Everything around an analysis sits in one of three layers, and each refers **downward** by
identifier. Nothing refers upward: no entity knows which model used it, no model knows which
analysis took a contrast from it.

| Layer | Question it answers | Classes |
|---|---|---|
| **Entities** | What existed in the study? | `Group`, `Task`, `Condition`, `Arm`, `Timepoint`, `Region`, `Assessment`, `Acquisition` |
| **Model** | What was fitted? | `ModelEstimation`, `ModelTerm`, `FactorLevel` |
| **Result** | What was tested, and what came of it? | `Analysis`, `Effect`, `Cell`, plus `Statistic`, `Measure`, `AnalysisGroup`, `AnalysisDetails`, `InferenceSettings` |

The middle layer is the one people skip, and it is the load-bearing one. A paper's cohorts and
conditions do not reach a result directly — they reach it by being the levels of a model term that a
contrast put a weight on. **`FactorLevel` is that junction**, and almost every "where does this go?"
question is really a question about it.

```
Study
├── groups[]              Group ◄─────────────────────────────┐
├── tasks[]               Task                                │
│                         └── conditions[]  Condition ◄───────┤
├── design                StudyDesign                         │
│                         ├── arms[]        Arm ◄─────────────┤
│                         └── timepoints[]  Timepoint ◄───────┤
├── regions[]             Region ◄──────────────────────────┐  │
├── assessments[]         Assessment ◄───────────────┐      │  │
├── acquisitions[]        Acquisition                │      │  │
│                                                    │      │  │
├── model_estimations[]                              │      │  │
│   └── ModelEstimation                              │      │  │
│       ├── inputs_from[] ──────► ModelEstimation (the stage below)
│       └── terms[]       ModelTerm                  │      │  │
│                         ├── assessment ────────────┘      │  │
│                         ├── region ───────────────────────┤  │
│                         ├── interaction_with[] ──► ModelTerm │
│                         └── levels[]      FactorLevel        │
│                                           └── conditions[], groups[],
│                                               timepoints[], arms[],
│                                               regions[] ─────┘
└── analyses[]
    └── Analysis
        ├── model_estimation ──► ModelEstimation   (the top stage only)
        ├── effect            Effect
        │                     ├── cells[]     Cell ──► ModelTerm
        │                     ├── mediation    Mediation ──► ModelTerm
        │                     └── statistic    Statistic
        ├── groups[]          AnalysisGroup ──► Group
        ├── measure           Measure
        ├── details           one AnalysisDetails subclass
        ├── inference_settings  InferenceSettings
        └── regions[], tasks[], acquisitions[], assessments[],
            preprocessing, tables[] ──► entities
```

Tree branches are ownership: the record lives there, inline. `──►` is a reference by identifier, to
a record declared once somewhere else. The distinction matters because it says where to *edit* a
fact: a cohort's demographics are on the `Group`, not on the `AnalysisGroup` that names it, and a
factor's level vocabulary is on the `ModelTerm`, not on the `Cell` that selects from it.

---

## 2. The words a paper uses, and what each is here

| The paper says | Here it is | Watch for |
|---|---|---|
| model, design matrix, GLM, first level, second level | one `ModelEstimation` **per estimation stage** | Two stages are two records joined by `inputs_from`, not one record |
| regressor, column, predictor, EV, covariate, term | `ModelTerm` | Covers a GLM regressor *and* the predictor of a family with no design matrix |
| factor | a `ModelTerm` with `type: categorical` | There is no `Factor` class |
| level, condition of a factor | `FactorLevel` | Declared on the term, not on the contrast |
| "controlling for age and sex" | two `ModelTerm`s with no `Cell` on this contrast | Nothing marks a term as nuisance; having no cell *is* the mark |
| contrast, comparison, weight vector | `Effect`, via its `cells` | One per `Analysis`, always |
| the +1 and −1 of a contrast | `Cell.direction` | Signs, never weights — see [§4](representing-models.md#4-the-five-values-of-direction-and-the-three-ways-to-be-non-directional) |
| condition, cohort, occasion, arm, region | entities, reached from `FactorLevel` | An `Effect` never names one directly |
| main effect, interaction, simple effect, omnibus | derived, from the cell pattern | No slot holds it; the vocabulary is `EffectKind` and its ranges are none |
| baseline | a `negative` cell if modelled; no second cell if implicit | `Condition` does not record what things were tested against |
| moderator | a component of a term whose `interaction_with` is non-empty | |
| mediator | `Effect.mediation.mediator` | Present only for a mediation analysis |
| n, sample size for this test | `AnalysisGroup.n` | `Group` carries recruitment down to acquisition and no further |
| ROI | `Region`, in one of five roles | See [§5.4](#54-one-region-five-roles) |
| threshold, correction, permutations | `InferenceSettings` | The statistic itself is `Effect.statistic` |
| the method — decoding, RSA, connectivity, ICA | which `AnalysisDetails` subclass is filled | No `analysis_type` field to keep in step with it |
| t, F, z, and their df | `Effect.statistic` | Peak magnitudes belong to the coordinates, not here |
| what the map measures — BOLD, thickness, FA | `Analysis.measure` | Independent of the statistic that tested it |

---

## 3. Three words that mean something narrower here

### `Cell` is not a cell of the design

In ANOVA usage a cell is a combination of levels: a 2×2 design has four cells. A `Cell` here is
**one level of one term on one side of one comparison** — the unit a contrast is built from, not a
compartment of the design. The 2×2 interaction contrast takes four `Cell`s, but they are two levels
of the condition term and two of the cohort term, and the design's four compartments are nowhere in
the record. Nothing enumerates them, because nothing needs to: the levels are on the terms and the
crossing is in the cell pattern.

### A "term" is not always a column

`ModelTerm` is a column of the design matrix *in the general sense that covers a family with no
design matrix*: a decoder's feature set, a similarity model's predictor. If the method has
predictors, they are terms.

### "Level" is a string in one place and a class in another

`FactorLevel.level` is the label — `"2-back"`, `"patients"`. `Cell.level` is a copy of that string,
and how a cell says which level it is. **A cell reaches a level by matching that string, not by
identifier**: `FactorLevel` has no `id`, being uniquely keyed by `level` within its term. So the
join is `cell.term` → that `ModelTerm` → the `FactorLevel` whose `level` equals `cell.level`.

---

## 4. What owns what

| Class | Declared in | Referenced from | How many |
|---|---|---|---|
| `Group` | `Study.groups` | `AnalysisGroup.group`, `FactorLevel.groups` | one per cohort in the study |
| `Task` | `Study.tasks` | `Analysis.tasks` | one per paradigm, resting state included |
| `Condition` | `Task.conditions` | `FactorLevel.conditions` | one per modelled state of that task |
| `Arm` | `StudyDesign.arms` | `Group.arm`, `FactorLevel.arms` | one per thing assigned, comparators included |
| `Timepoint` | `StudyDesign.timepoints` | `FactorLevel.timepoints` | one per occasion data were collected |
| `Region` | `Study.regions` | five slots — [§5.4](#54-one-region-five-roles) | one per region the study delimited |
| `Assessment` | `Study.assessments` | `ModelTerm.assessment`, `Analysis.assessments` | one per instrument, however many columns it supplies |
| `Acquisition` | `Study.acquisitions` | `Task.acquisitions`, `Analysis.acquisitions` | one per protocol |
| `Preprocessing` | `Study.preprocessings` | `Analysis.preprocessing` | one per pipeline |
| `ModelEstimation` | `Study.model_estimations` | `Analysis.model_estimation`, `inputs_from` | one per design matrix per stage |
| `ModelTerm` | `ModelEstimation.terms` | `Cell.term`, `interaction_with`, `mediation.mediator` | one per column of that stage |
| `FactorLevel` | `ModelTerm.levels` | by matching string from `Cell.level` | one per level of a categorical term |
| `Analysis` | `Study.analyses` | nothing — the top of the result layer | one per distinct tested effect |
| `Effect` | `Analysis.effect` | — | exactly one per analysis |
| `Cell` | `Effect.cells` | — | one per level that entered the contrast |
| `AnalysisGroup` | `Analysis.groups` | — | one per cohort the analysis ran on |
| `Table` | `Study.tables` | `Analysis.tables` | one per publication table |

Three uniqueness rules follow the same pattern, and each says "recorded twice is one thing":
`ModelTerm.name` is unique within its model, `FactorLevel.level` within its term, and
`(term, level)` within one `Effect`.

---

## 5. The relationships that carry information

### 5.1 `Cell.term` — the axis of the comparison

Cells sharing a term compare levels **within** that factor; cells on different terms compare
**across** them. That is the whole encoding, and it is why a condition contrast, a cohort
comparison, a pre–post change, a crossover comparison and a region-by-condition dissociation are
the same shape. Which shape a given pattern is, is derived rather than declared —
[§3 of representing-models.md](representing-models.md#3-working-out-which-shape-a-result-is) has the
decision procedure.

### 5.2 `FactorLevel`'s five entity slots — what kind of comparison it was

**Nothing on an `Effect` says whether a comparison was between cohorts, conditions, occasions, arms
or regions.** It is read off here, by following the cells' levels to the term and looking at which
of `conditions`, `groups`, `timepoints`, `arms`, `regions` is populated.

```yaml
# The chain, at its shortest. Full records are in representing-models.md §5.
cells:
  - term: term-condition          # ── the axis
    level: emotion labeling       # ── matches a FactorLevel.level of that term
    direction: positive
  - term: term-condition
    level: emotion matching
    direction: negative
```
```yaml
terms:
  - id: term-condition
    type: categorical
    levels:
      # ── conditions populated, so this was a comparison of conditions
      - {level: emotion labeling, conditions: [cond-emotion-labeling]}
      - {level: emotion matching, conditions: [cond-emotion-matching]}
```

A factor whose levels are not study entities at all — hemispheres, tasks, frequency bands,
acquisition sessions — leaves all five empty and is carried by `level` alone. That factor is
complete, not deficient.

### 5.3 `inputs_from` — the stage chain

A neuroimaging model is split across stages for tractability, and this is what puts it back
together. **A stage's terms are its own plus, transitively, those of the stages it consumed.** An
`Analysis` names only the top stage — the one that produced the reported statistics — so a
first-level model is referenced by no analysis and is not thereby orphaned.

`stage` is a label in the source's own words and orders nothing: two records both saying `group` say
nothing about their relation. Only `inputs_from` does, and it must be acyclic.

### 5.4 One `Region`, five roles

`Region` is declared once and used from five places, and which slot names it is the entire statement
of what it did:

| Slot | What the region was |
|---|---|
| `Analysis.regions` | the search space inference was restricted to — empty for whole-brain and searchlight, where the emptiness is the claim |
| `Analysis.defines_regions` | produced *by* this analysis — a localizer's clusters, peaks later used as seeds |
| `ConnectivityDetails.seed_regions` | the seed a connectivity analysis ran from |
| `ModelTerm.region` | the place a column's signal came from — a seed timecourse, an ROI-mean covariate |
| `FactorLevel.regions` | one level of a factor comparing places |

The near-miss worth naming: a whole-brain seed-based analysis has a seed and **no** search-space
region. Putting the seed in `Analysis.regions` says inference was restricted to it, which is the
opposite of what happened. And `ModelTerm.region` is singular, so it cannot serve a factor that
compares regions — that is one term with several levels, with the regions on `FactorLevel.regions`.

### 5.5 The two ways an analysis touches a `Group`

| Slot | Says |
|---|---|
| `Analysis.groups[].group` | this cohort's participants were **in** the analysis, and how many |
| `FactorLevel.groups` on a celled term | this cohort was **compared** |

Membership and comparison are separate facts, and a cohort comparison has both: the cohorts are in
`groups` because their data were analysed, and they are levels of a crossed term because they were
contrasted. Direction lives in one place — the cells — rather than being duplicated per entity type.

---

## 6. Reading a record back out

Three questions a synthesis asks, and the walk that answers each. None of the three has a slot;
all three are derived, which is why the joins above have to be right.

**What was compared?** For each `Cell`, follow `term` to its `ModelTerm` — in the analysis's model
or, through `inputs_from`, in a stage below it — then match `level` against that term's
`FactorLevel`s, and read the entity slots. Cells sharing a term are a within-factor comparison;
cells on different terms cross.

**What was it adjusted for?** Take the model's `terms`, add those of every stage reachable through
`inputs_from`, and subtract the terms the cells name. What remains is the adjustment set — which is
why a term the source states must be recorded even when no contrast tests it, and why two design
matrices must not share one `ModelEstimation`.

**Who was in it?** `Analysis.groups`, each entry's `group` and `n`. Expect `n` below the cohort's
acquired count when the analysis dropped participants.

---

## 7. Where the rest of it is

| For | Go to |
|---|---|
| Which facts belong to the model and which to the contrast | [representing-models.md §1](representing-models.md#1-the-one-decision-everything-else-follows-from) |
| Which shape a reported result is | [§3](representing-models.md#3-working-out-which-shape-a-result-is) |
| The five values of `direction`, and the three ways to be non-directional | [§4](representing-models.md#4-the-five-values-of-direction-and-the-three-ways-to-be-non-directional) |
| Twelve worked records, from a simple contrast to a two-stage model | [§5](representing-models.md#5-worked-models) |
| A paper that does not divide the world the way the schema does | [§6](representing-models.md#6-when-the-paper-does-not-fit) |
| What each field is, and how to fill it | [neuroimaging-study-storage/](neuroimaging-study-storage/) |
| Why the schema is shaped this way | [storage-schema-design-notes.md](storage-schema-design-notes.md) |
