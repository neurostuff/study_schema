# Extraction review in Label Studio

Human review of every part of an extraction record: the values and the spans that
support them, the links between objects, the shape of each analysis, and the split of
each coordinate table into the analyses it reports.

Everything runs through one command:

```bash
python review/ls.py config     # write the labeling configs
python review/ls.py lint       # check them, offline and against a server
python review/ls.py export     # turn extraction records into tasks
python review/ls.py deploy     # create the projects and import
python review/ls.py verify     # prove the deployment actually works
python review/ls.py sync       # reconcile live tasks with a fresh export
python review/ls.py decode     # read reviewer answers back out
python review/ls.py chat       # the ML backend that answers questions
```

Every path defaults to where this repo keeps things, so the common case takes no
arguments. `--url` and `--token` fall back to `LABEL_STUDIO_URL` and
`LABEL_STUDIO_API_KEY`.

## Five projects, seven task kinds

A Label Studio project holds exactly one labeling config (`projects/models.py:198`)
and one `maximum_annotations` (`:259`). Splitting by task kind rather than by paper
is what lets each kind carry its own UI *and* its own reviewer overlap; grouping by
paper is a Data Manager view on `data.paper_id`, never a project. The reasoning and
the alternatives considered are in `task-organization.md`.

| project | kinds | overlap | the judgement |
|---|---|---|---|
| `ns-review-value` | `value` | 1 | one field of one entity: its value, and the passage behind it |
| `ns-review-relationship` | `relationship` | 1 | one association slot as a grid over the paper's candidate targets |
| `ns-review-structure` | `entities`, `model` | 2 | the per-class instance inventory, and one model's terms |
| `ns-review-contrast` | `table`, `contrast` | 2 | one table's split into analyses, then each analysis's cells |
| `ns-adjudication` | `adjudication` | 1 | two reviewers' canonical forms, side by side |

Measured on the three records in `examples/`: 609 value, 18 relationship, 21
structure and 24 contrast tasks. Overlap differs on purpose — the value family is
~95% of the volume and wants one reviewer plus a priority-0 second pass, while the
structural families are 13–20 tasks a paper and are where a second opinion is
informative.

`analysis-review-design.md` and `relationship-review-design.md` are the design notes
behind the structural and relationship UIs.

## One registry, read by everything

`spec.py` is the only place the layer's shape is written down. It holds the task
kinds and their stages, the projects and their overlap, the verdict vocabularies,
the Data Manager views, the two chat control names, and the control-name grammar.
Config generation, export, deployment, sync, verification and decoding all read it.

That is not tidiness for its own sake. The previous layer kept six copies of this —
one per script — and they drifted: a view was created for a task kind that no longer
existed, and the per-family overlap was written down twice under a comment claiming
it was derived once.

```
spec.py       the registry
lsapi.py      the one HTTP client: auth, paging, the routes that need a trailing slash
xmlbuild.py   element helpers -- the tags with a rule attached
style.py      the stylesheet
blocks.py     one judgement block per kind, and the sample task that exercises it
config.py     assemble a project's config; derive what its tasks must carry
lint.py       what the server checks, what it cannot, and what only expansion shows
record.py     one traversal of an extraction record
staging.py    put the text where Label Studio can serve it, and refuse if it is wrong
tasks.py      one emitter per kind
answers.py    read answers back into the record's vocabulary
tables.py     coordinate tables: parse, attribute rows, render
chat.py       the ML backend
ls.py         the CLI
```

### Every kind is the same five-part form

```
subject     what is being judged: a heading, a meta line, standing guidance
extras      whatever this kind shows beyond that -- a legend, a paraphrase, a grid
question    the one required verdict, whose values name failures
spans       where in the paper the answer is warranted
editor      the correction form, shut until the verdict says otherwise
```

The frame is written once. What differs per kind is two small functions — what goes
above the question, and what goes in the editor.

Every config leads with the cheap judgement: show the whole object, ask one question,
and keep the editor hidden until the answer is not "correct"
(`visibleWhen="choice-unselected"`). At 35–50 entities and 5–9 analyses per paper, a
reviewer who must fill a form for every task will not finish.

`relationship` is the one exception, and declares no verdict at all: its grid arrives
pre-ticked from the extraction, so the ticks *are* the answer and a verdict could
only ask the reviewer to restate them. Everywhere else a task can be submitted
untouched, and the verdict is what separates "checked and correct" from "never looked
at".

### Every block is gated, including the ones that need not be

A `Repeater` is expanded client-side against the task's own data, and an absent or
empty key yields zero copies (`core/Tree.tsx:70-73`). So a block wrapped in
`<Repeater on="$gate_model">` renders only for a task carrying that key, and a
`required="true"` control inside it is never instantiated for any other kind.

Every kind uses that mechanism, even the projects holding a single kind. The
uniformity is the point: "exactly one required question per task" becomes a
structural property rather than a coincidence, and one expansion, one decoder and one
sample-task builder serve all seven variants.

```
value/value                controls= 6  required=['value_verdict_0']
relationship/relationship  controls= 6  required=[]
structure/entities         controls= 9  required=['entities_verdict_0']
structure/model            controls=21  required=['model_verdict_0']
contrast/table             controls= 9  required=['table_verdict_0']
contrast/contrast          controls=11  required=['contrast_verdict_0']
adjudication/adjudication  controls= 5  required=['adjudication_verdict_0']
```

All seven also pass the running Label Studio's own `POST /api/projects/validate/`
after expansion, which `ls.py lint --against-server` checks.

### One control-name grammar

`<kind>_<role>` plus one index per enclosing Repeater, with the flags fixed by depth:

```
value_verdict_{{i}}            ->  value_verdict_0
entities_row_{{i}}_{{j}}       ->  entities_row_0_3
model_level_{{i}}_{{j}}_{{k}}  ->  model_level_0_2_1
```

`spec.control` builds them and `spec.parse_control` reads them back. That is the
whole reason answers can be decoded at all: Label Studio stores `from_name` and
nothing else, so the name has to carry the address. Before it, one family had a
decoder and the rest had none.

### The span layer is dynamic, and it can be extended

`<Labels value="$labels">` builds its label set from task data, so the structure
under review *is* the label set: one label per field, per row, or per object. A
warrant becomes visible in the text rather than described beside it — drawing a span
and picking a label is one gesture, deleting a highlight is how you deny one.

For the three kinds whose subject is an inventory of objects — `entities`, `model`,
`table` — the layer is a `Taxonomy` in labeling mode instead, which draws exactly the
same regions and additionally lets the reviewer **type a name that is not in the
record**. That is the one way to add an object, everywhere it is possible: a missing
Group, a missing term and a missing analysis are all reported the same way, and each
is born attached to the passage that warrants it. A single `+ new ...` pseudo-label
could not do this — two missed things came back wearing the same label,
indistinguishable — and a prose box asks for the name a second time in a place that
can only disagree with itself.

## Review runs in stages, because corrections cascade

The task set is a function of the record, and review mutates the record. The ordering
that contains that is derived rather than chosen — sort the families by what a
correction can invalidate:

```
stage 0   entity inventory      changes nodes   -- invalidates everything below
          table segmentation    changes nodes   -- invalidates everything drawn from it
            |
            +-- stage 1  relationships  changes edges  -- invalidates stage 2
            |        |
            |        +-- stage 2  models and contrasts -- read nodes AND edges
            |
            +-- stage 1  values          changes leaves -- invalidates nothing
```

Values and relationships are independent and run concurrently, so the family with all
the volume never waits. The same ordering is the import order, the invalidation
order, and the order a decoder replays operations in to rebuild the record.

**Only some corrections propagate.** `rename` and `merge` are a rewrite map, and
`build_record.apply_aliases` already applies exactly that to reference slots only, so
an alias can never corrupt a value that happens to share a string with an id. `drop`
and `split` have no target to rewrite to, so every reference to the instance is also
wrong and its downstream tasks are regenerated instead. The disposition hints say
which behaviour each choice has.

**Regeneration is incremental** via two keys per task: `review_key` is the address
(`paper|kind|class|local_id|slot`), `content_hash` is a digest of the answer-bearing
payload. Same address and hash means the answer stands; a changed hash re-asks; a
vanished address orphans the answer. The hash deliberately excludes descriptors,
rendered prose and offsets, so correcting a `Group.name` does not re-ask a dozen
questions whose substance did not move.

Full reasoning, the reconstruction order, and the options not taken are in
`staged-validation.md`.

## The task envelope

Every task in every project carries the same keys, with exactly one gate non-empty:

```
identity   paper_id review_key content_hash stage task_kind paper_text_hash
           paper_url paper_title paper_citation
triage     priority coordinate_status entity_class local_id field_path table_id
           row_count llm_status evidence_status rel_slot dispute_kind
subject    gate_<kind>: [{label, meta, body}]
payload    rows[] rows_single[] columns[] anomalies[] legend[] labels[]
           statistic[] options[] table_html left_md right_md
```

The contract is **derived from the generated XML**, not written down beside it:
`config.contract(project)` reports every key the config interpolates and whether it
must hold an array or a string, and `ls.py export` checks every task against it. That
also makes one platform rule enforceable rather than remembered — Label Studio
records a key's data type the first time a config using it is saved, so `table_html`
must be a string on *every* task in its project, including the ones with no grid and
including the PATCH the sync issues.

## The text is never inlined

One paper is ~25–60 KB of text and hundreds of tasks. Inlining would produce ~18 MB
of task JSON per paper. Instead every task carries only a URL:

```xml
<Text name="paper" value="$paper_url" valueType="url" saveTextResult="yes" granularity="symbol"/>
```

Three attributes there are load-bearing:

| attribute | why |
|---|---|
| `valueType="url"` | keeps the text out of the task, and the browser fetches it once per paper |
| `saveTextResult="yes"` | `RichTextRegion.js:116` only emits `value.text` when this is set, and it defaults to `"none"`. Omit it and drawn spans come back with no text |
| `granularity="symbol"` | character-exact selection instead of word-snapped |

The pane shows **the markdown as it is** — hashes on headings, pipes in the inlined
coordinate tables. It rendered them as a title over a rule for a while, which read
better and cost more than it was worth: the transform was lossy on the title's case,
it made the built text differ from the corpus text by something other than the tables
it existed to add, and every change to it moved every offset in every record. There
is no re-anchoring tool any more because there is nothing left to re-anchor.

### Serving the text needs a storage row, not just env vars

`LOCAL_FILES_SERVING_ENABLED` and `LOCAL_FILES_DOCUMENT_ROOT` are **not sufficient**.
The serving view filters on `LocalFilesImportStorage` rows whose `path` prefixes the
requested file's directory and 404s when none match
(`io_storages/localfiles/views.py:104-119`). So each project also needs a local files
storage registered against the staged-text directory.

`ls.py deploy` does this and deliberately **never syncs** it — a sync would walk the
directory and import every `.txt` as a task. The row exists only so the endpoint will
serve and project members inherit access.

This failure mode is invisible from the UI: tasks open, the form renders, and the
paper pane is just empty. `ls.py verify` exists to catch it.

## Pipeline

```
beast-proxy:/data/alejandro/projects/ns-pond/data
  └── <neurostore_id>/{source/pubget/article.xml, processed/pubget/text.txt}
        │  review/sync_texts.py
        ▼
   review/texts/<id>/...
        │  review/build_text.py     (rebuilds the text with its tables inline)
        ▼
   review/texts/<id>/processed/local/text.tables.txt
        │  review/build_record.py   (agent payloads + quote->offset resolution)
        ▼
   review/examples/<id>.extraction.json
        │  review/ls.py export      (stages the text, emits tasks)
        ├──▶ review/ls_files/texts/<id>.txt
        └──▶ review/ls_tasks/<id>.tasks_{value,relationship,structure,contrast}.json
                │  review/ls.py deploy
                ▼
        Label Studio: ns-review-{value,relationship,structure,contrast}, ns-adjudication
```

Staging refuses to write unless `sha256(text)` equals the record's
`source_text_hash`, and export re-verifies every span against that text. The bytes
reviewers see are the bytes the offsets were computed against.

## Running it

Start Label Studio. This `docker run` is the fast path — it uses the published image
directly, so nothing is built, and it pre-seeds the login and API token so no
clicking is needed before `deploy` can run:

```bash
mkdir -p review/ls_data && chmod 777 review/ls_data

docker run -d --name ns-review -p 8080:8080 \
    -v "$(pwd)/review/ls_files:/label-studio/files:ro" \
    -v "$(pwd)/review/ls_data:/label-studio/data" \
    -e LOCAL_FILES_SERVING_ENABLED=true \
    -e LOCAL_FILES_DOCUMENT_ROOT=/label-studio/files \
    -e LABEL_STUDIO_USERNAME=curator@example.com \
    -e LABEL_STUDIO_PASSWORD=reviewreview \
    -e LABEL_STUDIO_USER_TOKEN=nsreview0000000000000000000000000000000 \
    -e LABEL_STUDIO_ENABLE_LEGACY_API_TOKEN=true \
    -e ML_TIMEOUT_PREDICT=180 \
    --add-host=host.docker.internal:host-gateway \
    heartexlabs/label-studio:latest

until curl -sf http://localhost:8080/version >/dev/null; do sleep 5; done; echo up
```

First boot takes about 90 seconds (it runs migrations). The last two arguments are
for the chat backend; drop them if you are not running it. On Linux the container has
no route to the host without `--add-host`, and `ML_TIMEOUT_PREDICT` defaults to 100s
(`ml/api_connector.py:27`), at which point Label Studio abandons the question with no
message to the reviewer.

`LABEL_STUDIO_USER_TOKEN` only works alongside
`LABEL_STUDIO_ENABLE_LEGACY_API_TOKEN=true`. For anything beyond a local trial, drop
both and take the token from **Account & Settings → Access Token** instead.

Then:

```bash
export LABEL_STUDIO_URL=http://localhost:8080
export LABEL_STUDIO_API_KEY=nsreview0000000000000000000000000000000

python review/ls.py config
python review/ls.py lint --against-server
python review/ls.py export
python review/ls.py deploy
python review/ls.py verify
```

Add the second reviewer under **Organization → People**. Overlap is only active if
`maximum_annotations` reads 2, which both `deploy` and `verify` report.

`review/docker-compose.override.yml` is the alternative if you want the full compose
stack with postgres; note it builds the image from the checkout, which is slow. It
needs `export NS_REVIEW_FILES="$(pwd)/review/ls_files"` first.

### After the record changes

```bash
python review/ls.py export
python review/ls.py sync            # dry run
python review/ls.py sync --apply
python review/ls.py verify
```

`sync` matches on `review_key` and `content_hash`: unchanged tasks are left alone,
display-only changes refresh the data and keep the answer, a changed hash is reported
as needing re-review, a new address is imported, and a vanished one is reported
rather than deleted unless `--prune` says so. Predictions are reconciled separately,
because adding a pre-selected radio changes what the reviewer sees without changing
what is asked.

`--prune-answers` additionally drops result entries whose control the config no longer
declares. Removing a control does not touch the answers already given to it: Label
Studio keeps the entry and stops rendering it, so an annotation goes on asserting a
verdict to a question that has been deleted, and nothing surfaces it.

## Asking the model about the paper

Every task carries a chat box under the paper text. A reviewer asks a question, the
answer appears above the box, and **both are saved into the annotation** — which is
the reason it is built this way rather than as a side panel. What a curator had to ask
before deciding is part of the provenance of the decision, so it exports with the
verdict instead of evaporating in a browser tab.

```bash
python review/ls.py chat
```

Then in each project: **Settings → Model → Connect Model**,
`http://host.docker.internal:9090`, with **Interactive preannotations** on. Each
reviewer also switches on **Auto-Annotation** in the labeling view — without it
`smartEnabled` is false and nothing is ever sent. A question typed with the toggle off
is recorded as a question and simply never answered, with no error, which is the first
thing to check when the chat looks dead.

**Auto-Annotation does not annotate anything by itself.** The name is Label Studio's
and it is a poor one here. The toggle sets one boolean whose only job is to make
`smartEnabled` true on smart controls (`tags/control/Base.js:62-66`). It starts no
batch, touches no other task, and creates nothing on its own: a request goes out only
when *you* submit a question, and the reply comes back as a suggestion on the
annotation you already have open. What it does change is that the answer is written
into that draft without a further click — `<Text>` has `supportSuggestions: false`,
and a suggestion an object tag cannot display is accepted immediately
(`Annotation.js:1183-1192`), which is exactly how the chat answer gets recorded.

Three pieces of upstream behaviour make this sharp rather than merely fiddly, and
`test_chat.py` pins all three:

- **`smart` defaults to *true* on every control** (`tags/control/Base.js:16`). With
  Auto-Annotation on, any region whose results include a smart control fires the round
  trip — and that is not just the comment box: drawing an evidence span notifies too,
  and so does deleting one. Left alone it would be an LLM call per highlight, each
  arriving with no question to answer. `xmlbuild.mute_smart` sweeps the finished tree
  and turns `smart` off on everything that has not explicitly asked for it.
- **A TextArea's result holds all of its submissions in one list**, and accepting a
  suggestion *replaces* the control's whole area. So the reply resends the entire
  answer log; returning just the new answer would erase the rest.
- **The context is every textarea region on `paper`**, grouped by region type and
  `to_name` only. The reviewer's `comment` and any correction boxes arrive here too,
  and the backend ignores everything that is not the chat.

The system message is the instructions and the paper and **nothing else** —
byte-identical for every task on that paper — and which task is open rides on the live
question instead, ~40 tokens below the paper where it changes nothing upstream of it.
Measured on `4cRnHYtfSwuK` (8.5k tokens): the first question of a paper's first task
sends 8511 prompt tokens and reuses 0; the first question of every later task reuses
8313 of 8475 (98%); later turns within a task reuse 99.6%. With the task block inside
the system message it was `cached_tokens: 0` three times out of three, because the
prefix diverged before the paper ended.

Which task data reaches the model is `spec.CHAT_SKIP_KEYS` — everything except the
addressing keys, the span chips, the grid columns and the rendered table, whose 20 KB
of markup would otherwise be pasted into every turn. Allow-by-default, so a key the
exporter grows reaches the model unless it is excluded on purpose.

## Reviewer workflow

Every task shows the full paper on the left and the object under review on the right.
Spans are pre-highlighted from the extraction and their boundaries are draggable;
spans sharing a label are jointly required while separate labels are each
independently sufficient.

**value.** One field of one entity: its value, the schema's description of the slot,
how much evidence stands behind it, and one verdict naming the failure — `correct`,
`wrong_value`, `wrong_evidence`, `wrong_both`, `should_be_not_reported`,
`missed_value`, `uncertain`. The span layer's two labels are `direct support` and
`inferred support`, pre-filled from the record's own `value_source`; whether a passage
supports the value at all is answered by deleting it.

**relationship.** One row per source object, one column per candidate target, so the
whole assignment is judged at once and an unused target shows up as an empty column. A
single-valued slot gets an explicit `no link` column, so "this links to nothing" is an
assertion rather than an unanswered row. Hard anomalies — a link to an id that was
never extracted, a required row with nothing in it — are listed above the grid.

**entities.** One class per task: is this the right set of instances? Each row carries
its descriptor and how many things reference it, so "is this a real cohort?" and "what
breaks if I drop it?" are both answerable here. Per-row dispositions are keep, rename,
merge, drop, split.

**model.** One `ModelEstimation`'s terms, as an accordion of per-term cards with
nested level editing. The list is this model's terms *and* the terms of any stage it
was fitted on, because a contrast taken from the group stage can cell a first-level
column.

**table.** One coordinate table, rendered as a grid with each row attributed to the
analysis that claims it, and a numbered list of the analyses stage 1 parsed out of it.
The judgement is the **split**, not the encoding.

**contrast.** One `Analysis`, as the record rendered back into one sentence beside the
grid its rows were read off. Accepting is one click; otherwise a direction grid opens
with a row per term-and-level and five options — `positive`, `negative`, `absent`,
`unstated`, `not_applicable`. `absent` makes a term adjusted for rather than tested an
assertion; `unstated` is what an omnibus F reports.

**adjudication.** Two canonical forms side by side with the diff above them, and a
resolution of `take_left`, `take_right`, `synthesize` or `escalate`. Canonical, not
raw: agreement on a structural task is computed on the sorted cell set, or control
ordering reads as disagreement.

References are never shown as bare ids. `record.descriptor` renders
`grp_1 -- Parkinson's patients . n=20 . age 64.5` from the target class's priority-0
fields, derived at export and never stored — so it tracks
`storage-parameter-priorities.yaml` instead of drifting as a second copy of the entity.

### Finding the evidence without scrolling

The paper pane holds the full text, so a highlight near the end of a 25–60 KB document
starts off-screen. **Press `Alt+.`** to jump the pane to the first highlight, centred.
This is the built-in `region:cycle` hotkey, and with nothing yet selected
`selectNext()` picks `regions[0]`, which routes through `scrollIntoView({block:
"center"})`. Pressing it again cycles to the next span.

There is no config-only way to auto-select a region on load: nothing in the editor does
it, and `stores/SettingsStore.js` has no flag for it. Making it automatic means
patching `Annotation`/`RegionStore`, rebuilding the `web/` bundle, and shipping a
custom image.

## Theming

All colours in the generated config come from Label Studio's design tokens
(`var(--color-warning-background)`, …), never hardcoded hex.
`libs/ui/src/tokens/tokens.prefix.css:567` redefines those tokens under
`[data-color-scheme="dark"]`, so panels invert with the theme.

Hardcoding a panel background is the specific trap: the fixed light background stays
light while the *text* colour still comes from the theme, so dark mode renders
near-invisible light-on-light text. **Background and foreground must be set as a pair**,
and a test asserts every rule that sets one sets the other.

The `<Style>` block also contains no `<`, `>` or `&` anywhere, comments included: style
content is passed through `sanitizeHtml`, and one mangled selector invalidates its whole
comma-separated rule. A single `.ant-table-tbody > tr > td` silently voided the
neighbouring `.ant-table` declarations and the panel kept rendering white with no error.

Label Studio's Choices, Table and Collapse are antd components whose stylesheet
hardcodes colours on 87 of their rules, and the legacy Taxonomy has the same problem
from a CSS module. Those are overridden explicitly, in pairs; a test asserts that using
one of those tags obliges the override.

The `<Label>` chip colours are the one intentional exception — those are span highlight
colours, and Label Studio handles their text contrast itself.

## Known gaps

- **A Repeater project cannot be reconfigured once anyone has answered it.** This is
  the sharpest limit in the whole layer. `PATCH /api/projects/<id>/` re-validates the
  entire `label_config` against every existing annotation *and draft*, whatever the
  change was — a CSS-only edit trips it identically. `Project.validate_config` skips
  the check only when `num_annotations == 0 and num_drafts == 0`
  (`projects/models.py:676`); there is no force flag. It resolves control names through
  `check_control_in_config_by_regex`, which builds its entries for `$variable` names
  only, so every Repeater-generated control is permanently unknown to the validator and
  one draft touching one of them freezes that project's config for good. TextArea
  results are exempt (`:698`), which is why the chat controls never block anything.

  `ls.py deploy --force` is the way through: it exports the annotations and drafts,
  deletes them, pushes the config, and re-creates them over the API, snapshotting to
  `review/backup/` before the first delete. Do it when nobody has the project open, or
  the editor will auto-save the draft back mid-flight.
- **`Repeater` is marked for deprecation** in Label Studio's source
  (`tags/visual/Repeater.js:47`). It works in `1.24.0.dev0` and `1.22.0` accepts every
  config here, but it is undocumented. It is load-bearing because one project-wide
  config must adapt to a per-paper number of fields, rows and terms. The fallback is to
  pre-expand against a fixed maximum N and live with empty slots, since `visibleWhen`
  reads only choices and regions, never task data.
- **A new object's fields have to be typed into a note.** The select-or-create control
  names the object and attaches it to its evidence, which is the hard half; but a form
  for an object that does not exist yet cannot be repeated over task data, so its fields
  go in prose. Cells have no such problem: the grid already carries a row per declared
  term.
- **Data Manager columns do not exist for repeated controls.** `parse_config` runs on
  the unexpanded config, so `model_type_0_1` is not in its control map. JSON export
  carries the results regardless, but filtering on annotation content is limited to the
  `data.*` keys.
- **Multivalued list cardinality**, outside the analysis structure. A reviewer still
  cannot add or remove entries of an inlined list (`sex_distribution[2]`) from within a
  value task, only judge existing indexed entries.
- **The chat is one exchange at a time, not a thread.** The answers accumulate in a
  read-only box above the question box; there is no chat bubble UI, because that needs
  JavaScript in the labeling interface and open source does not allow it. Multi-turn
  context is real — earlier turns are sent back to the model — but it reads as a log.
- `LOCAL_FILES_SERVING_ENABLED` will serve anything under the document root. The mount
  is a dedicated `texts/` directory, read-only. `chat.py` reads the same tree and
  confines the `d` parameter to it, because that value arrives from task data.
- **`Analysis.terms` has no priority entry**, so it exports as `unranked`. It is an
  extraction-only slot: storage carries terms under `Effect.terms`, so there is no
  `Analysis.terms` storage field for the priority inventory to rank.
