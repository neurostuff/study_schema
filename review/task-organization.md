# How to organize the review: projects, papers, and task kinds

Measured on the three records in `review/examples/` against the current generated extraction
schema. Label Studio claims checked against the `label-studio/` checkout (OSS `1.24.0.dev0`).

> **What shipped, where it differs from this.** The value family is **per field**, not
> per entity: the 48-70 tasks a paper this argues for were not taken, and the live
> projects hold 609 value tasks across the three papers. The reason is in
> `tasks.Exporter.emit_value` -- an entity task bundles 13-25 judgements behind one
> verdict, so a reviewer either accepts all of them at once or opens a long form, and
> the answer needs an index path to address. The argument below for grouping is still
> the argument; it was outweighed, not refuted, and the hybrid it recommends
> (per-entity for many-field classes, per-field for the long tail) remains the open
> option. Five projects shipped, not four: adjudication is one of them. The scripts
> named throughout are now one CLI, `review/ls.py`, over the modules listed in
> `README.md`.

## The schema-wide census

Excluding the 43 `ExtractedValue` wrapper classes, the extraction schema has **45 entity
classes** holding **201 value slots, 35 compositions and 19 associations**. Those three
numbers are the three review families, and the ratio between them is the whole of the
organization problem.

## What one paper actually costs

| | `4cRnHYtfSwuK` | `5Rw4BhGBShSR` | `HU6mqxmtySg3` |
|---|---|---|---|
| analyses / models / terms / cells | 4 / 2 / 7 / 8 | 9 / 3 / 5 / 27 | 5 / 1 / 1 / 11 |
| **value** fields, one task each | 207 | 240 | 147 |
| **value** fields, one task per entity | **38** | **50** | **35** |
| **structure** fields (rolled into the structural tasks) | 86 | 151 | 54 |
| **structure** tasks (1 inventory + models + analyses) | 7 | 13 | 7 |
| **relationship** matrices (non-empty) | 7 | 6 | 6 |
| checkboxes across those matrices | 68 | 82 | 47 |

Two readings of the same paper:

- **per-field, as built today**: ~210–300 tasks, of which 95% are one-field judgments.
- **three families, value grouped per entity**: **48–70 tasks**. A 4–6× reduction, and the
  expensive judgments are concentrated in 13–20 of them.

The value family dominates by two orders of magnitude no matter how you slice it. So project
granularity is the *less* important question; how the value family is grouped is the more
important one.

## Should each article be its own project?

**No.** The decisive fact is that a project holds exactly one labeling config —
`label_config` is a single `TextField` on `Project` (`label_studio/projects/models.py:198`).
A per-paper project therefore does not give you different UIs; it gives you one UI applied to
one paper. It solves nothing about mixing task kinds.

It buys exactly one real advantage, and it is worth naming because it is genuinely
attractive: a per-paper config can be **statically expanded** against that paper's record —
exact term counts, exact grid rows, no `Repeater`, no padding, no dependence on a tag marked
for deprecation. `config_gen.py` already generates config from a record, so this is nearly
free to produce.

Against that:

- **Cross-paper triage dies.** The existing lever is a Data Manager view filtered on
  `data.priority` — "a priority-0-only first pass" across the whole corpus. With one project
  per paper there is no queue that spans papers, and a reviewer cannot do all the priority-0
  work first.
- **It multiplies by four.** Four families × N papers projects, each needing its own
  `LocalFilesImportStorage` row (the serving view 404s without one —
  `io_storages/localfiles/views.py:104-119`), its own import, its own views.
- **`setup_project.py` breaks at ~1000 projects.** Its own project lookup is
  `GET /api/projects?page_size=1000` (`review/setup_project.py:85`), so the tooling has a
  hard ceiling well below corpus scale.
- **Every config fix must be re-applied N times**, and a config bug found on paper 400 leaves
  399 projects wrong.
- Reviewer assignment, `maximum_annotations`, and progress are all per project, so none of
  them can be reasoned about corpus-wide.

## Can different UIs coexist in one project?

**Technically yes**, and this is the non-obvious part. `Repeater` is expanded at config-parse
time against the task's own data, and an absent or empty key yields zero copies:
`parseValue(props.on, taskData) || []` followed by a loop over its length
(`web/libs/editor/src/core/Tree.tsx:70-73`). So one config can carry four mutually exclusive
blocks:

```xml
<View>
  <Text name="paper" value="$paper_url" valueType="url" saveTextResult="yes" granularity="symbol"/>

  <Repeater on="$value_task">        <!-- 1 element for a value task, absent otherwise -->
    ... the value-family form ...
  </Repeater>
  <Repeater on="$relationship_task">
    ... the matrix ...
  </Repeater>
  <Repeater on="$contrast_task">
    ... the cell grid ...
  </Repeater>
</View>
```

Every control lives inside its block, so names never collide across kinds, and a
`required="true"` control in a block that renders zero copies is never instantiated and so
never blocks submission. The paper `<Text>` is shared, which is what all four families want
anyway.

**But it should not be the plan.** `maximum_annotations` is per project
(`label_studio/projects/models.py:259`), so one project forces one overlap policy across all
four families. That is the wrong trade in an obvious direction: the value family is 95% of
the volume and mostly wants one reviewer, while the structural family is 13–20 tasks a paper
where a second opinion is where the information is. A single project makes you pay double on
the 300 cheap tasks to get double on the 15 expensive ones.

The secondary costs: one config becomes the union of four, so a change to the analysis block
can break the value block; `check_label_config.py` has to hold four families' invariants
simultaneously; and the Data Manager's per-control columns are already absent for
Repeater-generated controls, since the server parses only the unexpanded config.

Keep the trick in reserve for a genuine case — merging the inventory task with the
`Analysis.model_estimation` matrix, say, since they are answered together and want the same
overlap.

## Grouping by paper is a view, never a project

This is worth stating plainly because it is where the question conflates two things. Paper
grouping is already solved and costs nothing: Data Manager views filtered on `data.paper_id`
created via `POST /api/dm/views`, plus import order setting the labeling-stream order, plus
browser caching of the shared text URL. All three are per project and all three already work
(`README.md`, "Grouping tasks by paper"). Nothing about wanting per-paper grouping implies
wanting per-paper projects.

## The recommendation

**Four projects, split by task kind, papers grouped by view inside each.**

| project | tasks/paper | overlap | UI |
|---|---|---|---|
| `ns-review-value` | 35–50 | 1, second pass on priority 0 | value + spans, per entity |
| `ns-review-relationship` | 6–7 | 1 | candidate legend + matrix |
| `ns-review-structure` | 2–8 | 2 | inventory → term forms |
| `ns-review-contrast` | 5–11 | 2 | rendered table → split verdict; paraphrase → cell grid |
| `ns-adjudication` | as needed | — | diff of two canonical forms |

That is also the shape the code already has — the existing evidence/reference split is two of
these four — so it is an extension rather than a rewrite. Each project's config is generated
once and serves every paper, which is what makes `Repeater` load-bearing: a project-wide
config must adapt to a per-paper number of terms and rows.

## The grouping question that actually matters

Within the value family, the choice is one task per field or one task per entity instance.
Measured: **207–240 per-field tasks become 38–50 per-entity tasks.**

Per-entity is the same move as the analysis design, applied to plain entities: a `Group` with
25 populated fields becomes one task showing all 25 with their spans, a `Repeater` over the
fields, and a per-field verdict inside it. The reviewer reads the paper's participants
paragraph once instead of being handed it 25 times.

Trade-offs, honestly:

- **For it**: 4–6× less navigation; the reviewer holds one entity's context instead of
  re-establishing it per field; internal inconsistency becomes visible (an
  `enrolled_count` that contradicts `acquired_count` is one glance in a form and invisible
  across two tasks); and the pre-highlighted spans for one entity mostly cluster in one
  passage, so the text pane stops jumping.
- **Against it**: agreement is no longer naturally per field — it has to be computed per
  field *inside* the result, which is more decoder work; priority triage gets coarser, since
  an entity mixes priority 0 and 2 fields and the task can only carry one priority (mitigation:
  sort fields within the task by priority and let the reviewer stop, but that loses the
  guarantee that priority-0 work is complete); and a 25-field form is long enough that
  attention drops down the page.

The distribution argues for a **hybrid**: per-entity for the classes with many fields
(`Group` at 13–25, `Task` at 8, `InferenceSettings` at 8–15, `Acquisition`, `Assessment`,
`ModelEstimation`), per-field for the long tail — the median instance has only 2–3 populated
fields, so grouping those saves nothing and costs the priority signal.

## Open questions

- Does the value family want a per-entity *or* a per-section grouping? The record already
  computes a section breadcrumb for each field, so "everything this paper's Methods paragraph
  supports" is an available axis and might beat the entity axis for reading flow. It cuts
  across entities, which makes the decoder harder.
- If per-entity wins, is `Analysis` a value task at all? Its remaining value fields after the
  structural split are ~7, and they are the ones the contrast task's paraphrase already shows.
  Folding them into the contrast task would remove a whole task kind per analysis.
- Should the adjudication project be one project or one per family? Its config has to render
  whatever the disputed family renders, which is the one place the omni-config trick is
  clearly the right answer.
