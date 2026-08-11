# Reviewing an analysis as a structure

A brainstorm, not a decision. The attribute-level review in `README.md` puts one field in
one task. That works for `Group.species` and it cannot work for `Effect.cells`. This
proposes the task family that reviews an analysis's model and contrast as single objects,
and argues for the specific widgets that make editing them possible in Label Studio
Community.

It is one of three families. `relationship-review-design.md` covers the other new one —
the links between objects — and works out the mechanical rule that assigns each schema slot
to a family. Read that first if you want the boundary; read this for the analysis case,
which is the one that rule cannot generate.

All Label Studio claims below are checked against the `label-studio/` checkout
(OSS `1.24.0.dev0`), cited as `file:line`.

## Why field-by-field breaks here

Five reasons, each independently sufficient:

**The unit of correctness is a relation, not a value.** `Cell.direction: positive` is
neither right nor wrong on its own. It is right relative to which `ModelTerm` the cell
names, which `FactorLevel` of that term, and what the paper reports. A reviewer shown
`analyses[9].effect.cells[3].direction` in isolation has to reconstruct the whole contrast
from memory before the field means anything.

**The characteristic errors are structural.** Direction inverted (both cells wrong
together). The contrast projected onto the wrong axis (cells naming the wrong term). One
term recorded that should be two. Levels attached to the wrong entity. An interaction
recorded as a plain regression, or a spurious product column on two crossed categorical
factors — which the schema itself says is "legal but decides nothing, the crossed cells
already saying what it says, and is flagged for review"
(`neuroimaging-study-storage/analysis.yaml:724`). None of those is a single-field error, and
a per-field verdict vocabulary cannot name any of them.

**The evidence granularity does not match the unit of correctness.** In the generated
extraction schema, `Cell.direction` and `Cell.level` are `ExtractedString`s and so carry
evidence, while `Cell.term` is a plain foreign key with none, and nothing carries evidence
for *"these cells, together, are the contrast"*
(`neuroimaging-study-extraction/analysis.yaml:286-315`). The one span a reviewer actually
needs — the sentence reporting the comparison — is the warrant for the whole `Effect`, and
the schema has no slot to hang it on. A structural task can hang it on the object.

**Cardinality is most of what is wrong.** The frequent corrections are "there should be a
fourth term", "this factor has a third level the paper names", "these two analyses are one",
"the negative cell is missing so a two-sided contrast reads as a one-sample test". The
existing config cannot express any of them — `README.md` already lists this as a known gap
("A reviewer cannot add or remove entries of an inlined list").

**Absence carries meaning.** `Effect.cells` is required, with non-emptiness stated in the
description (`neuroimaging-study-storage/analysis.yaml:266`), a lone `positive` cell *means* an
unmodelled implicit baseline, and an omitted middle level of an ordered factor *means* zero
weight. A review UI that can only judge the objects that exist can never confirm an
intended absence.

### Where to draw the boundary

A field belongs to the structural task when its correctness depends on another object's
identity — its range is a class, or it is a string constrained to match another object's
key. Everything with a scalar or enum range and no cross-object constraint stays in the
existing field-level task.

| Structural task | Stays field-level |
|---|---|
| `ModelEstimation.terms` and everything under `ModelTerm` | `ModelEstimation.model_family`, `model_type`, `hrf_model`, `estimator`, `level`, `software`, `model_settings` |
| `ModelTerm.levels` / all of `FactorLevel` | `Analysis.spatial_scope`, `spatial_unit`, `roi_definition`, `roi_label`, `coordinate_space` |
| `Effect.cells` (term, level, direction, label) | `Analysis.prespecification`, `interpretations`, `model_representation_notes` |
| `Effect.statistic` (family + dfs read together) | all of `InferenceSettings`, all of `Preprocessing` |
| `Effect.mediation` (path + mediator) | `Measure.family`, `type`, `source_label`, `specific_metric`, `unit` |
| `Analysis.definition` (the prose the cells must match) | `Analysis.statistical_maps`, `details` payload scalars |
| `AnalysisGroup.n` (per-cohort analysed size) | |

Note `Cell.label`, `FactorLevel.order` and `ModelTerm.unit` are plain scalars but ride
along in the structural task anyway, because addressing them at all requires the index path
the structural task already has.

`Analysis.model_estimation`, `acquisitions`, `tasks`, `tables`, `assessments` and
`preprocessing` are *associations* and go to the relationship family, not here. But
`model_estimation` is the hop that determines this task's candidate term set, so it is an
upstream dependency: its matrix has to be reviewed before the contrast tasks, alongside the
inventory.

Priorities agree that this is where the expensive review sits: `Effect.cells` 0, every
`Cell` field 0 except `label`, `ModelEstimation.terms` 0, `ModelTerm.name`/`type`/
`variation_level`/`assessment` 0 (`storage-parameter-priorities.yaml`).

### It is a split, not an addition

Measured on `2abntY3hQSyq` (11 analyses, 6 `ModelEstimation`s, 16 `Term`s), the current
export is 604 evidence tasks and 129 reference tasks. The structural ones are:

| | evidence | reference |
|---|---|---|
| `Term` (name, source_definition, type) | 48 | 3 |
| `TermUse` | 28 | 28 |
| `ConditionTerm` | 21 | 21 |
| `GroupTerm` | 36 | 18 |
| `Statistic` | 11 | — |
| `Analysis.definition` | 11 | — |
| **total** | **155** | **70** |

**225 of 733 tasks**, or 450 judgments at two reviewers, become **18 structural tasks** —
1 inventory + 6 model + 11 contrast — or 36 judgments. A further 58 reference tasks go to
the relationship family and collapse into 6 matrices there, so between the two families the
733 tasks become 24.

Caveat on those counts: that record and that export predate the regeneration of the
extraction schema from storage. `TermUse`, `ConditionTerm` and `GroupTerm` were the
ancestors of `Effect.cells` and `FactorLevel`'s entity slots, and the review layer's
`build_record.py` / `to_labelstudio.py` have not yet been re-run against the generated
shape. The magnitude is right; the per-class rows will move.

### One definition, both schemas

Now that `neuroimaging-study-extraction.yaml` is generated from the storage schema plus
`extraction-deviations.yaml`, the extraction and storage sides carry the *same* analysis
representation — `Effect`, `Cell`, `ModelTerm`, `FactorLevel`, `AnalysisGroup` — differing
only in that extraction wraps scalar leaves in `ExtractedString` and storage does not
(`neuroimaging-study-extraction/analysis.yaml:181-542` against
`neuroimaging-study-storage/analysis.yaml:245-808`). So the structural review does not need
two definitions of "what an analysis is", and the config generator can walk either schema to
build the label sets and grid rows. That was not true before the regeneration, and it is
what makes this task family worth building now rather than after the next reshape.

## Three review units, not one

"One task per analysis" is the wrong granularity in both directions.

**Model unit** — one task per `ModelEstimation`, reviewing its `terms` and their
`FactorLevel`s. `ModelEstimation` is shared: on this paper `mw_u` serves 4 analyses,
`spearman` 3, `glm_filter` 2. Reviewing the term list per analysis would review `mw_u`'s
terms four times. The evidence is usually one or two Methods sentences — the whole
`psych_*` family of this paper comes from a single span at 14545–14688.

**Contrast unit** — one task per `Analysis`, reviewing `Effect` (cells, statistic,
mediation), `definition`, and `groups[].n`. Depends on the model unit: the cells' term
references only mean something against a settled term list.

**Inventory unit** — one task per paper, listing the extracted `Analysis` and
`ModelEstimation` records. This is the only place "you missed an analysis", "these two are
one analysis" and "this ROI definition is not an analysis at all" can be said. Field-level
review has no home for it whatsoever, which is a gap worth more than any single field.

### Ordering

The units have a dependency order and the stream should follow it. `README.md` already
establishes that import order sets task-id order which sets labeling-stream order, so per
paper: the inventory task, then the `Analysis.model_estimation` matrix from the relationship
family (which fixes each contrast's candidate term set), then the model tasks, then the
contrast tasks. One reviewer walks a paper's structure top-down in a single sitting, and the
browser cache on the shared text URL keeps it cheap.

That ordering is advisory, not enforced, so every downstream task needs an escape verdict —
`upstream_wrong`, meaning "the model/analysis this task is about should not exist in this
shape; I have said why on the inventory task". Parking a task is much better than a reviewer
inventing a repair that the inventory decision will invalidate.

## The primary judgment: a paraphrase, not a form

The fast path matters more than the editor. At 11 analyses per paper, a reviewer who must
fill a form for every analysis will not finish. So render the structured record back into
one mechanically-built English sentence, put the paper's own `definition` and the
extractor's evidence excerpt beside it, and ask one question.

```
The record says
  Between-subject contrast on striatal PPI connectivity, whole-brain voxelwise:
  group  PwPD (+)  vs  HC (−),  under psychological term  non-canonical,
  adjusted for 24 motion parameters, WM signal, CSF signal, FD spikes,
  t(33),  n = 20 PwPD + 15 HC.

The paper says
  "…《we compared the PPI connectivity of the right striatum during non-canonical
  sentences between PwPD and HC》…"                                    [Methods, 14712]

Does the record say what the paper says?
  ( ) yes — accept                       ← one click, done
  ( ) direction or sides are wrong
  ( ) wrong term / wrong axis
  ( ) missing or spurious cell
  ( ) statistic wrong
  ( ) upstream_wrong — this analysis should not exist in this shape
  ( ) uncertain
```

The paraphrase is what makes "validate the whole thing at once" concrete: it is the only
artifact that renders term identity, level, direction, adjustment set and sample into
something a reader can check against a sentence in the paper. It is also cheap — the
generator already has the record.

The editor stays collapsed until the verdict is not `yes`. `<View visibleWhen="choice-unselected"
whenTagName="verdict" whenChoiceValue="yes">` does exactly this (`tags/visual/View.jsx:62-65`),
and the existing field-level config already uses the same device for its corrected-value box.

Render the paraphrase block with `<Markdown value="$paraphrase"/>` rather than `<Header>`.
`Markdown` takes a task-data value, a real `className`, and its own `visibleWhen`
(`tags/visual/Markdown.jsx:36-44`) — which sidesteps the constraint the existing config works
around, that `Header` supports only inline `style` and silently drops `className`. A
paraphrase wants a bulleted adjustment set and a bolded direction; `Header` cannot give it
either.

## Relating structure to text: the structure *is* the label set

The best available mechanism is dynamic labels. `<Labels value="$field">` builds its label
set from task data (`tags/control/Labels/Labels.jsx:66`, resolved by
`mixins/DynamicChildrenMixin.js:76`), and each item may carry `value`, `background`, `alias`
and `showalias` (`Labels.jsx:35-52`).

So emit one label per structural object that can have a text warrant, generated from the
record under review:

```xml
<Filter name="flt" toName="obj" hotkey="shift+f" minlength="2" placeholder="filter terms"/>
<Labels name="obj" toName="paper" value="$object_labels" showInline="false"/>
```

```json
"object_labels": [
  {"value": "term: load",              "alias": "T1", "background": "#8bc34a"},
  {"value": "level: 1-back",           "alias": "L1", "background": "#c5e1a5"},
  {"value": "level: 2-back",           "alias": "L2", "background": "#c5e1a5"},
  {"value": "term: group",             "alias": "T2", "background": "#03a9f4"},
  {"value": "+ new term",              "alias": "NEW", "background": "#e91e63"}
]
```

What this buys:

- **The warrant is visible in the text, not described next to it.** The reviewer opens the
  task and the Methods sentence already carries `term: load` and `level: 2-back` in
  distinct colours, pre-annotated from the extraction exactly as the field-level tasks
  already pre-annotate spans.
- **Denying a warrant is deleting a highlight.** Asserting one is drawing a span and
  picking a label. Both are one gesture, both round-trip as integer offsets
  (`regions/RichTextRegion.js:89`, with the span text emitted at `:116` only under
  `saveTextResult="yes"`), which is the property the existing pipeline already depends on.
- **One hue per term, shades for its levels** groups a factor visually, so "these three
  levels came from one sentence" reads off the page.
- **`Alt+.` becomes a read-through of the contrast.** The built-in `region:cycle` hotkey
  walks the regions in order; with cells as labels that is a guided tour of the comparison.

Cost: label count. Sixteen terms plus levels is ~30 labels. `<Filter>` mitigates it
(`tags/visual/Filter.jsx`, works with `Labels` or `Choices`, `minlength` default 3 — set it
to 2), as does `showInline="false"` and short `alias`es.

Per-span judgments hang off `perRegion` controls (`mixins/PerRegion.js`, documented at
`TextArea.jsx:76`): keep `supports / irrelevant / boundary_off` from the field-level config,
and add a per-region note.

**Do not** reach for `Taxonomy labeling="true"` for this, tempting as the hierarchy is
(term → level → direction). It is gated behind `FF_TAXONOMY_LABELING`
(`Taxonomy.jsx:231`), frontend flags fall back to `window.APP_SETTINGS.feature_flags_default_value`
(`utils/feature-flags.ts:164-179`), that defaults to `False`
(`label_studio/core/settings/base.py:727`), and there is no shipped flags file to turn it
on. Plain `Taxonomy` as a classifier is fine; labeling mode is not available in the OSS
image.

## Editing the structure

### Cells: a grid, not a list of objects

This is the single highest-value widget. A cell is (term, level, direction). Render the
model's full term × level inventory as **rows**, with direction as a three-way choice:

```
                                   positive   negative   absent
  group : PwPD                        (•)        ( )      ( )
  group : HC                          ( )        (•)      ( )
  load : 1-back                       ( )        ( )      (•)
  load : 2-back                       ( )        ( )      (•)
  age (continuous)                    ( )        ( )      (•)
  24 motion parameters                ( )        ( )      (•)
```

Why a grid beats an object list:

- **Absence becomes an assertion.** `absent` is a reviewer statement that the term was
  adjusted for and not tested, which is precisely what the schema means by a term with no
  cell. In an object list absence is indistinguishable from oversight.
- **The foreign key cannot be violated.** Rows come from the reviewed model, so a cell can
  only name a term that exists. The widget enforces what would otherwise be a
  post-hoc validation failure.
- **Wrong-axis errors become visible.** The reviewer sees every candidate term as a row, so
  "this should have been on `load`, not on `group`" is a glance rather than a deduction.
- **Direction inversion is one click**, which is the most common single fix.
- `Cell.label` (the source's wording for this level in this comparison) is one optional
  text box per non-absent row.

Mechanically this is a `<Repeater on="$cell_rows">` with a single-choice `<Choices>` per
row. The row inventory is derived, so it costs nothing to also include the derived
`Effect.kind` — computed by the structural validator the storage schema already
posits — as a read-only line above the grid, giving the reviewer a second reading to
check against the paper.

### Terms and levels: repeated forms

```xml
<Repeater on="$terms" indexFlag="{{idx}}" mode="pagination">
  <Collapse>
    <Panel value="$terms[{{idx}}].heading">
      <Choices name="tv_{{idx}}" toName="paper" choice="single" value="$term_verdicts"/>
      <TextArea name="tname_{{idx}}" toName="paper" rows="1" editable="true"
                placeholder="corrected term name"/>
      <Choices name="ttype_{{idx}}" toName="paper" choice="single">
        <Choice value="categorical"/><Choice value="continuous"/>
      </Choices>
      <Choices name="tvar_{{idx}}" toName="paper" choice="single" value="$variation_levels"/>
      <Repeater on="$terms[{{idx}}].levels" indexFlag="{{jdx}}">
        <TextArea name="lv_{{idx}}_{{jdx}}" toName="paper" rows="1" editable="true"/>
        <Number   name="lo_{{idx}}_{{jdx}}" toName="paper" min="1"/>
        <Choices  name="lent_{{idx}}_{{jdx}}" toName="paper" value="$entity_choices"/>
      </Repeater>
    </Panel>
  </Collapse>
</Repeater>
```

`mode="pagination"` renders one term at a time, which keeps a 16-term model from building
16 forms at once. `<Collapse><Panel>` accepts `choices`, `taxonomy`, `textarea`, `labels`,
`number` as children (`tags/visual/Collapse.jsx`, `PanelModel.children` union).

Per-term verdicts want to name the structural failures directly, not just `wrong`:
`correct`, `wrong_name`, `wrong_type`, `wrong_variation_level`, `should_be_two_terms`,
`duplicate_of_another_term`, `not_a_term_of_this_model`, `levels_wrong`,
`spurious_interaction` (the over-applied `interaction_with`), `missing_from_model`.

### Adding objects: three mechanisms, use different ones for different objects

**Draw-then-declare, for terms and levels.** The reviewer highlights the sentence that
names the missing term with the reserved `+ new term` label, and per-region controls on that
region carry its name, type and variation level. The new object is *born attached to its
evidence*, which is the invariant the extraction schema wants anyway, and it needs no guess
about how many might be missing. A new level uses `+ new level` and a `<Relations>` link
(`tags/control/Relations.js`) to the term region it belongs to — or, more simply, a
per-region choice naming the parent term from the existing inventory.

**Blank slots, for cells.** A cell has no independent evidence — its warrant is the same
Results sentence as the rest of the contrast — so the grid should just include every
term × level row whether or not the extractor gave it a cell. There is nothing to "add".
This is the grid's second advantage: for cells the add problem disappears entirely.

**JSON escape hatch, for the residue.** One `editable` `<TextArea>` holding the object's
JSON, revealed only by a `structure_cannot_be_expressed` verdict, and routed to a curator
rather than applied automatically. Unpleasant and unvalidatable in the browser, which is
exactly why it should be the parked path and not the primary one. `Analysis.model_representation_notes`
is the schema's own version of this admission, and the review layer should have one too.

### Deleting

A per-object verdict value (`spurious`, `duplicate_of_another_term`, `not_an_analysis`), not
a separate control. Deletion of an object that other objects reference is where this gets
sharp: deleting a term with live cells must either cascade or be refused. Refuse, and say
so in the task — the reviewer marks the term `spurious` and the cell rows that named it
`absent`, which is two statements that agree rather than one that implies the other.

## Layout

Two columns, as now — a third pane does not fit beside a 60% text pane.

```
┌───────────────────────────────┬────────────────────────────────────────┐
│  paper (full text, url-loaded)│  ① paraphrase  vs  paper's definition  │
│                               │     + 《》-delimited evidence excerpt   │
│  pre-highlighted:             │                                        │
│    ▓ group: PwPD  (+)         │  ② one verdict                         │
│    ▓ group: HC    (−)         │  ─────────── if not "yes" ───────────  │
│    ▓ term: non-canonical      │  ③ ▸ cell grid            (collapsed)  │
│    ▓ statistic: t(33)         │     ▸ statistic / measure (collapsed)  │
│                               │     ▸ terms               (collapsed)  │
│  Alt+.  cycles the highlights │     ▸ sample n per cohort (collapsed)  │
│  Shift+F filters the labels   │                                        │
│                               │  ④ note to adjudicator                 │
└───────────────────────────────┴────────────────────────────────────────┘
```

Section 1 answers the common case with no scrolling, which is the same reasoning the
field-level excerpt block rests on. Everything in 3 is `<Collapse>`d and revealed by
verdict, so the default rendering of an accepted analysis is short.

## Label Studio mechanics: what is verified, what is risky

**Verified working.** Dynamic labels and dynamic choices from task data
(`DynamicChildrenMixin`, used by `Labels`, `Choices`, `Taxonomy`). `Repeater`, expanded at
config-parse time against task data (`core/Tree.tsx:69-99`). `Collapse`/`Panel`.
`visibleWhen` on `View`. `perRegion` controls. `Filter`. `Number`. `Relations`. Server-side
config validation accepts all of these: `validate_label_config`
(`label_studio/core/label_config.py:107`) checks the config against
`label_config_schema.json`, whose root `View` sets `additionalProperties: true`, then checks
only that `name="..."` values are unique and every `toName` resolves. `{{idx}}` literals are
unique as written, so an unexpanded Repeater passes.

**Risky, and worth knowing before building.**

- **`Repeater` is marked for deprecation in source**: `@todo Tag will be deprecated,
  currently it's removed from docs` (`tags/visual/Repeater.js:47`). It works in
  `1.24.0.dev0` and is undocumented. The fallback is cheap because `config_gen.py` already
  generates config from a record: emit per-index blocks literally instead. The catch is that
  Label Studio has one config per *project*, so a literal expansion has to be padded to a
  fixed maximum N and the unused slots will render as empty forms — `visibleWhen` reads
  choices and regions, never task data, so they cannot be hidden. Repeater is load-bearing
  for variable cardinality; the fallback is cosmetically worse, not impossible.
- **`{{idx}}` is replaced once per attribute.** `deepReplaceAttributes` uses
  `String.replace` with a string pattern (`core/Tree.tsx:48`), which replaces only the first
  occurrence. `value="$x[{{idx}}].y[{{idx}}]"` silently breaks. Assert this in
  `check_label_config.py`.
- **Attributes only, never text content.** `recursiveClone` returns early on nodes with no
  attributes (`core/Tree.tsx:41`), so `<Header>Term {{idx}}</Header>` never substitutes.
  Headers must use `value="..."`.
- **Nested Repeaters need distinct index flags.** The outer pass rewrites the inner
  `on="$terms[{{idx}}].levels"` before the inner tag is parsed, so it should work — but this
  is inference from `Tree.tsx:78-84`, not an observed run. Test it first; the terms/levels
  nesting depends on it.
- **Repeater-generated `from_name`s are invisible to the server.** `parse_config` runs on
  the unexpanded config, so `tname_0` is not in its control-tag map. JSON export carries the
  results regardless, but any tooling keyed on control names must handle index suffixes, and
  Data Manager columns for those controls will not exist.
- **`mode="pagination"` cannot go inside a `Panel`.** A Repeater becomes a `view`, or a
  `pagedview` under pagination (`core/Tree.tsx:91-97`). `Panel`'s children union admits
  `view` but not `pagedview` (`tags/visual/Collapse.jsx`, `PanelModel.children`), so a
  paginated Repeater inside a collapsed panel will not instantiate. Paginate the *outer*
  Repeater, which sits above the `Collapse`, and leave nested ones on the default mode.
- **`Markdown` is not a legal `Panel` child either** — the same union omits it, though `View`
  admits both `markdown` and `pagedview` (`tags/visual/View.jsx:124`). Wrap it in a `View`
  inside the panel. These two are the kind of failure `check_label_config.py` should learn to
  catch, since the symptom is a silently missing block rather than an error.

## The output is a new extraction version

Reviewer corrections are edits: the review layer reads a record and writes the corrected
next version of it. The extraction header already sanctions exactly this — "Do not manually
correct an extraction record: create a new extraction version or a separate correction
record" — so producing a version rather than mutating one is the whole of the constraint.

What that requires of the round trip, independent of format:

- **Identity must survive by `local_id`, never by index.** Every Repeater result comes back
  keyed `tname_3`, and index 3 means nothing once a reviewer deletes term 1. Task data has to
  carry the `local_id` for every row it renders, and the importer has to map back through it.
  This is the single thing most likely to be got wrong and hardest to notice, because an
  off-by-one reassignment produces a valid record with the wrong content.
- **New objects need review-minted `local_id`s** in a namespace the extractor cannot collide
  with — `rev_` prefixed — so a diff of two versions can say which objects a human added.
- **The version needs provenance.** `ExtractionMetadata` records which model ran and what
  text it read; a reviewed version needs the reviewer, the review project and the annotation
  ids beside it, or the record cannot say how it came to differ.
- **Structural edits have no scalar path**, so the decoder is a rebuilder rather than a
  patcher: "split this term in two", "merge these two analyses", "this cell belongs on a
  different term" are reconstructions of the subtree. That is an argument about the decoder's
  shape, not about the output format — it reads the whole model or contrast out of the
  results and re-emits it.

An accepted analysis re-emits its subtree unchanged, so the accept path costs nothing and a
diff of the two versions is exactly the set of reviewer corrections.

## Agreement and adjudication

Structured results make agreement harder than a scalar verdict, and comparing raw result
JSON would report disagreement on control ordering. Compute agreement on canonical forms:

- **cells**: the sorted set of `(term, level, direction)` triples. Two reviewers agree when
  they produced the same contrast, which is the question worth measuring.
- **terms**: the sorted set of `(name, type, variation_level)`, plus the set of levels per
  term.
- **inventory**: the partition of the paper's analyses (so a split/merge disagreement shows
  up as a different partition, not as a diff of ids).

That feeds the existing separate `ns-adjudication` project, and the canonical forms are what
the adjudicator should see side by side — two paraphrases, not two result blobs.

## What I would build first

1. **The paraphrase renderer**, standalone and testable, over the existing record. It is
   the cheapest artifact, it is the primary judgment, and writing it will surface every
   place the record cannot be read back into a sentence — which is itself a finding about
   the schema.
2. **The inventory task**, config plus exporter. Smallest task family (one per paper), and
   it covers the failure mode nothing currently covers.
3. **The cell grid** as a contrast task with a *read-only* term list, before any term
   editing exists. Most of the priority-0 structural fields, none of the Repeater nesting
   risk.
4. Term/level editing last, behind a tested nested Repeater.

## Open questions

- Should the inventory task be per paper or per `ModelEstimation` group? Per paper is one
  task but a long one; on this record it lists 11 analyses and 6 models.
- Does the contrast task need the model's *full* term inventory as grid rows, or only terms
  plausibly relevant? Full is correct and makes wrong-axis errors visible; on
  `ppi_right_striatum_noncanon_pd_vs_hc` that is 12 terms, and with levels perhaps 18 rows.
  A "show adjustment-set rows" toggle is not expressible (no task-data-driven visibility),
  so this is a generation-time choice, not a reviewer one.
- Where do the `AnalysisDetails` subclasses go? `ConnectivityDetails.seed`,
  `ConjunctionComponent`, `DecodingClass` are structural by the criterion above but each is
  a different shape, which is a fourth task family rather than a section of the contrast
  task.
- Two reviewers on a structural task is 2× the most expensive judgment in the pipeline. Is
  overlap better spent on the inventory task alone, with contrasts single-reviewed and
  adjudicated only where the validator flags an invariant violation?
