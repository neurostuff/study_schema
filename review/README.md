# Extraction review in Label Studio

Human review of every part of an extraction record: the values and the spans that
support them, the links between objects, the shape of each analysis, and the split of
each coordinate table into the analyses it reports.

## Five projects, one per task kind

A Label Studio project holds exactly one labeling config (`projects/models.py:198`)
and one `maximum_annotations` (`:259`). Splitting by task kind rather than by paper
is what lets each kind carry its own UI *and* its own reviewer overlap; grouping by
paper is a Data Manager view on `data.paper_id`, never a project. The reasoning and
the alternatives considered are in `task-organization.md`.

| project | config | tasks per paper | overlap | the judgement |
|---|---|---|---|---|
| `ns-review-value` | `value.xml` | 35–50 | 1 | one entity instance: every populated field, its excerpt, its spans |
| `ns-review-relationship` | `relationship.xml` | 6–7 | 1 | one association slot as a grid over the paper's candidate targets |
| `ns-review-structure` | `structure.xml` | 10–18 | 2 | the per-class instance inventory and a model's terms |
| `ns-review-contrast` | `contrast.xml` | 5–11 | 2 | one coordinate table's split into analyses, then each analysis's cells — both over the rendered table |
| `ns-adjudication` | `adjudication.xml` | as needed | 1 | two reviewers' canonical forms, side by side |

Measured on the three records in `examples/`: **~300 per-field tasks per paper
become 48–70**. Overlap differs on purpose — the value family is ~95% of the volume
and wants one reviewer plus a priority-0 second pass, while the structure family is
where a second opinion is informative.

`analysis-review-design.md` and `relationship-review-design.md` are the design
notes behind the structure and relationship UIs.

### Every config leads with the cheap judgement

The shared shape is: show the whole object, ask one question, and keep the editor
hidden until the answer is not "correct" (`visibleWhen="choice-unselected"`). At
35–50 entities and 5–9 analyses per paper, a reviewer who must fill a form for
every task will not finish.

- **value** — a read-only `<Table>` of all the entity's fields, then the
  `《》`-delimited excerpts, then one verdict. `all_correct` is one click; anything
  else reveals a paginated per-field form.
- **relationship** — the one exception with no gated editor, because the grid *is*
  the summary: a reviewer has to read it to answer at all. What is gated is naming
  a target that was never extracted.
- **structure** — the record rendered back into one English sentence beside the
  paper's own wording. `accept` is one click; otherwise a direction grid opens,
  with rows for every term the model declares and `absent` as a third option, so
  a term adjusted for rather than tested becomes an assertion.

### One config, several task kinds

`structure` serves the entity-inventory and model kinds from a single config, and
`contrast` serves the table and contrast kinds from another.
`Repeater` is expanded at config-parse time against the task's own data, and an
absent key yields zero copies (`core/Tree.tsx:70-73`), so a block wrapped in
`<Repeater on="$contrast">` renders only for a contrast task — and its
`required="true"` verdict is never instantiated for the other two, so it cannot
block their submission. Verified by expanding all six task variants:

```
value                  controls=  7 unique=  7 required=['verdict']
relationship           controls=  7 unique=  7 required=[]
adjudication           controls=  6 unique=  6 required=['resolution']
structure/entities     controls= 12 unique= 12 required=['inv_verdict_0']
structure/model        controls= 23 unique= 23 required=['model_verdict_0']
structure/contrast     controls= 12 unique= 12 required=['contrast_verdict_0']
```

At most one required question per task, and no name collisions. All six also pass
the running Label Studio's own `POST /api/projects/validate/` after expansion.

`relationship` requires nothing, listed in `config_gen.VERDICTLESS_KINDS`. Its grid
arrives pre-ticked from the extraction, so the ticks are the answer and a verdict
could only ask the reviewer to restate them. Everywhere else a task can be
submitted untouched, and the verdict is what separates "checked and correct" from
"never looked at".

### The span layer is dynamic

`<Labels value="$span_labels">` builds its label set from task data
(`Labels.jsx:66`), so the structure under review *is* the label set: one label per
field, per row, or per object. Two consequences. A warrant becomes visible in the
text rather than described beside it — drawing a span and picking a label is one
gesture, deleting a highlight is how you deny one. And the old set-ceiling coupling
is gone: the exporter used to be able to emit a `set 4` label the config stopped at
`set 3`, which the server accepted with HTTP 201 and then failed to render with no
error at all. A `<Labels>` with no static `<Label>` children cannot have that
mismatch, and a test asserts none of the four configs has one.

Long label lists get a `<Filter>` (`shift+f`) — a `Group` has up to 25 fields and a
model up to 16 terms.

## Why Label Studio Community

Verified in the `label-studio/` checkout (OSS `1.24.0.dev0`) rather than from the
feature-comparison table, which understates the community edition:

- `maximum_annotations` is a real field on the OSS `Project` model
  (`projects/models.py:259`, default 1) with `need_annotators`,
  `_rearrange_overlap_cohort` and `_update_tasks_states` all present. Two-reviewer
  overlap works; it just is not exposed in the community settings UI, so
  `setup_project.py` sets it over the API.
- The Data Manager has an **"Annotated by"** column (`data_manager/functions.py:153`,
  `visibility_defaults.explore = True`), which is the cross-reviewer visibility
  requirement. OSS has no project-membership gate, so everyone sees every task.
- For a `<Text>` tag, regions serialize to plain integer offsets
  (`RichTextRegion.js:87`) — no xpath, no `globalOffsets`. That is what lets
  `EvidenceSpan.start_char`/`end_char` round-trip exactly.

Not in OSS, and supplied by scripts here instead: agreement metrics, a formal
accept/reject review stage, comments, and assignment.

## Review runs in stages, because corrections cascade

The task set is a function of the record, and review mutates the record. The
ordering that contains that is derived rather than chosen — sort the families by
what a correction can invalidate:

```
stage 0   entity inventory      changes nodes   -- invalidates everything below
            |
            +-- stage 1  relationships  changes edges  -- invalidates stage 2
            |        |
            |        +-- stage 2  analysis structure   reads nodes AND edges
            |
            +-- stage 1  values          changes leaves -- invalidates nothing
```

Values and relationships are independent and run concurrently, so the family with
all the volume never waits. The same ordering is the import-round order, the
invalidation order, and the order the decoder replays ops in to rebuild the record.

Stage 0 is the cheap round that gates the expensive ones: ~8-10 tasks per paper
against ~300 for the value family. It is also where "the extractor invented a Group"
is caught — a correction no value or relationship task can express, since both can
only judge an instance that exists.

**Only some corrections propagate.** `rename` and `merge` are a rewrite map, and
`build_record.apply_aliases` already applies exactly that to reference slots only
(`build_record.py:249-294`), so an alias can never corrupt a value that happens to
share a string with an id. `drop` and `split` have no target to rewrite to, so every
reference to the instance is also wrong and its downstream tasks are regenerated
instead. The disposition hints in the config say which behaviour each choice has.

**Regeneration is incremental** via two keys per task: `review_key` is the address
(`paper|Class|local_id|slot`), `content_hash` is a digest of the answer-bearing
payload. Same address and hash means the answer stands; a changed hash re-asks; a
vanished address orphans the answer. The hash deliberately excludes descriptors and
rendered prose, so correcting `Group.name` does not re-ask a dozen contrast
questions whose substance did not move.

Full reasoning, the reconstruction order, and the options not taken are in
`staged-validation.md`.

## The text-duplication problem

One paper is ~25-60 KB of text and hundreds of tasks. Inlining the text into each
task would produce ~18 MB of task JSON per paper. Instead every task in all four
projects carries only a URL:

```xml
<Text name="paper" value="$paper_url" valueType="url" saveTextResult="yes" granularity="symbol"/>
```

Measured on `2abntY3hQSyq`: **1.01 MB of tasks versus 17.91 MB inlined, 17.7x
smaller**, and the browser fetches the text once per paper then serves it from
cache across that paper's remaining tasks. Grouping the value family per entity
cuts the task count a further 4–6x on top of that, so the same property matters
less than it did and is still worth keeping — the cache hit is what makes walking
one paper's tasks consecutively cheap.

Two tests hold the line: one asserts each config declares exactly one `<Text>` with
these attributes, the other that no sample task exceeds 4 KB of JSON.

Three attributes there are load-bearing:

| attribute | why |
|---|---|
| `valueType="url"` | keeps the text out of the task |
| `saveTextResult="yes"` | `RichTextRegion.js:116` only emits `value.text` when this is set, and it defaults to `"none"` (`RichText/model.js:62`). Omit it and drawn spans come back with no text |
| `granularity="symbol"` | character-exact selection instead of word-snapped |

### Serving the text needs a storage row, not just env vars

`LOCAL_FILES_SERVING_ENABLED` and `LOCAL_FILES_DOCUMENT_ROOT` are **not
sufficient**. The serving view filters on `LocalFilesImportStorage` rows whose
`path` prefixes the requested file's directory and 404s when none match
(`io_storages/localfiles/views.py:104-119`). So each project also needs a local
files storage registered against the staged-text directory.

`setup_project.py` does this (`ensure_local_storage`) and deliberately **never
syncs** it — a sync would walk the directory and import every `.txt` as a task.
The row exists only so the endpoint will serve and project members inherit access.

This failure mode is invisible from the UI: tasks open, the form renders, and the
paper pane is just empty. `verify_deployment.py` exists to catch it.

## Grouping tasks by paper

Label Studio has no first-class task groups. Three mechanisms combine:

1. **Data Manager views** filtered on `data.paper_id`, created by `setup_project.py`
   via `POST /api/dm/views`.
2. **Import order** sets task id order, which sets labeling-stream order, so a
   reviewer walks one paper's attributes consecutively.
3. **Browser cache** on the shared text URL makes that walk cheap.

Views on `data.priority` are the triage lever.

## Pipeline

```
beast-proxy:/data/alejandro/projects/ns-pond/data
  └── <neurostore_id>/processed/{ace,pubget,elsevier}/text.txt
        │  rsync (hash-matched against source_text_hash)
        ▼
   review/texts/<id>/...                     paper text, gitignored
        │  build_record.py  (agent payloads + quote->offset resolution)
        ▼
   review/examples/<id>.extraction.json      the record under review
        │  to_labelstudio.py                 (stages text, emits tasks)
        ├──▶ review/ls_files/texts/<id>.txt   served by Label Studio
        └──▶ review/ls_tasks/<id>.tasks_{value,relationship,structure}.json
                │  setup_project.py
                ▼
        Label Studio: ns-review-{value,relationship,structure}, ns-adjudication
```

> **The exporter has not caught up.** `to_labelstudio.py` still emits the previous
> `tasks_{evidence,reference}.json` — one task per field — so the four configs
> cannot be fed end to end yet. What it has to produce instead is pinned down
> exactly: `config_gen.DATA_CONTRACT` lists every key each config reads, and
> `config_gen.sample_task(kind)` is a worked example of each shape that the tests
> assert covers the whole contract. `setup_project.py` looks for the new suffixes
> and will report zero tasks until the exporter emits them.

## Running it

Generate the four configs and validate them before Label Studio ever sees them:

```bash
python review/config_gen.py --out-dir review/ls_config
python review/check_label_config.py review/ls_config/*.xml
```

`check_label_config.py` reproduces Label Studio's own server-side validation, and
then adds the Repeater rules — none of which fail loudly. Expansion happens
client-side against task data, so the server never sees the expanded form: a name
that collides across two iterations passes validation and then drops a control in
the editor. The checker also catches an index flag used twice in one attribute
(only the first is replaced, `core/Tree.tsx:48`), one in element text (attributes
only, `:41`), a `mode="pagination"` Repeater or a `Markdown` inside a `Panel`
(neither is a legal child, so the block is simply absent), and nested Repeaters
sharing an index flag.

For the expanded form — what a reviewer actually gets — `expand_repeaters()`
mirrors the editor's own algorithm. It is what the tests use to assert that each of
the six task variants yields unique control names and exactly one required
question. If a Label Studio is running, the same expanded XML can be checked
against the real validator, which needs no project and mutates nothing:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
    -H "Authorization: Token $LABEL_STUDIO_API_KEY" -H 'Content-Type: application/json' \
    --data "$(python -c 'import json,pathlib;print(json.dumps({"label_config":pathlib.Path("review/ls_config/structure.xml").read_text()}))')" \
    "$LABEL_STUDIO_URL/api/projects/validate/"    # 204 means valid
```

Export tasks and stage the text:

```bash
python review/to_labelstudio.py \
    --record review/examples/2abntY3hQSyq.extraction.json \
    --text review/texts/2abntY3hQSyq/processed/pubget/text.txt \
    --identifiers review/texts/2abntY3hQSyq/identifiers.json \
    --files-root review/ls_files \
    --out-dir review/ls_tasks
```

Staging refuses to write unless `sha256(text)` equals the record's
`source_text_hash`, and export re-verifies every span against that text. The bytes
reviewers see are the bytes the offsets were computed against.

Start Label Studio. This `docker run` is the fast path — it uses the published
image directly, so nothing is built, and it pre-seeds the login and API token so
no clicking is needed before `setup_project.py` can run:

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
```

The last two lines are for the chat backend below; drop them if you are not running
it. On Linux the container has no route to the host without `--add-host`, and
`ML_TIMEOUT_PREDICT` defaults to 100s (`ml/api_connector.py:27`), at which point
Label Studio abandons the question with no message to the reviewer.

First boot takes about 90 seconds (it runs migrations). Wait for it:

```bash
until curl -sf http://localhost:8080/version >/dev/null; do sleep 5; done; echo up
```

`LABEL_STUDIO_USER_TOKEN` only works alongside
`LABEL_STUDIO_ENABLE_LEGACY_API_TOKEN=true`. For anything beyond a local trial,
drop both and take the token from **Account & Settings → Access Token** instead.

`review/docker-compose.override.yml` is the alternative if you want the full
compose stack with postgres; note it builds the image from the checkout, which is
slow. It needs `export NS_REVIEW_FILES="$(pwd)/review/ls_files"` first.

Create the projects, register the text storage, and import:

```bash
export LABEL_STUDIO_URL=http://localhost:8080
export LABEL_STUDIO_API_KEY=nsreview0000000000000000000000000000000
python review/setup_project.py --tasks-dir review/ls_tasks --config-dir review/ls_config
```

Then confirm the deployment actually works, rather than trusting that it looks fine:

```bash
python review/verify_deployment.py --tasks-dir review/ls_tasks --files-root review/ls_files
```

Add the second reviewer under **Organization → People**. Overlap is only active if
`maximum_annotations` reads 2, which both scripts report.

### After the paper text changes

A `<Text>` region is stored as `{start, end, text}` against the staged paper, so
restaging that paper with anything inserted ahead of a span silently invalidates it:
the entry still looks well formed and Label Studio highlights whatever now sits at
those numbers. Inlining the coordinate tables moved every offset in all three papers.

Three tools cover three different populations, and only the middle one is new:

```bash
python review/sync_tasks.py --tasks-dir review/ls_tasks --apply     # predictions
python review/reanchor_spans.py --files-root review/ls_files        # drawn spans
python review/verify_deployment.py --tasks-dir review/ls_tasks --files-root review/ls_files
```

`sync_tasks` rewrites predictions from the export, so those repair themselves. A span
a *reviewer* drew exists nowhere but the database — no export contains it, and
`prune_orphan_answers` cannot see it because it prunes by `from_name` and the control
is still declared. `reanchor_spans.py` re-finds each quote with `spans.resolve(...,
near=<old offset>)`, is a dry run without `--apply`, snapshots to `review/backup/`
before the first write, and is idempotent — a span that already reads correctly is
skipped, so an interrupted run is finished by running it again. A quote it cannot
re-find is reported and left alone, never dropped.

`verify_deployment.offsets_hold` samples predictions, annotations and drafts. It
sampled predictions only until this bit: it reported the rot in the half that repairs
itself and stayed silent about the half that does not.

## Asking the model about the paper

Every task carries a chat box under the paper text. A reviewer asks a question, the
answer appears above the box, and **both are saved into the annotation** — which is
the reason it is built this way rather than as a side panel. What a curator had to
ask before deciding is part of the provenance of the decision, so it exports with
the verdict instead of evaporating in a browser tab.

```bash
python review/chat_backend.py --key-file .env
```

Then in each of the four projects: **Settings → Model → Connect Model**,
`http://host.docker.internal:9090`, with **Interactive preannotations** on. Each
reviewer also switches on **Auto-Annotation** in the labeling view — without it
`smartEnabled` is false and nothing is ever sent. A question typed with the toggle
off is recorded as a question and simply never answered, with no error, which is
the first thing to check when the chat looks dead.

**Auto-Annotation does not annotate anything by itself.** The name is Label
Studio's and it is a poor one here. The toggle sets one boolean
(`AppStore.setAutoAnnotation`, persisted in `localStorage`) whose only job is to
make `smartEnabled` true on smart controls (`tags/control/Base.js:62-66`). It
starts no batch, touches no other task, and creates nothing on its own: a request
goes out only when *you* submit a question, and the reply comes back as a
suggestion on the annotation you already have open. Nothing is saved to the server
until you press Submit, as before. What it does change is that the answer is
written into that draft without a further click — `<Text>` has
`supportSuggestions: false`, and a suggestion an object tag cannot display is
accepted immediately (`Annotation.js:1183-1192`), which is exactly how the chat
answer gets recorded. The neighbouring **Auto-Accept** toggle governs suggestions
on tags that *can* display them, so it has nothing to do with the chat.

9090 is the ML-backend convention and is also Prometheus's, so `--port 9091` may be
needed. If Label Studio was started without `--add-host` (as the `docker run` block
above was until recently), `http://172.17.0.1:9091` reaches the host over the default
bridge without recreating the container — check it with
`docker exec ns-review curl -s http://172.17.0.1:9091/health`. In that case also pass
`--timeout 85` so the backend gives up *before* Label Studio's 100s does and can
still record what happened.

It answers with the extractor's own model at the extractor's own effort
(`gpt-5.6-luna`, low), reading `OPENAI_API_KEY` and `OPENAI_API_GATEWAY` from `.env`
like every other script here, and it is told to answer only from the paper, to quote
what it asserts, and not to propose a value — the judgement stays the reviewer's.

### Why this shape

Open-source Label Studio cannot run JavaScript in the labeling interface; that is an
Enterprise feature. What it does have is the interactive-preannotation round trip,
and two TextAreas are its two ends:

| | |
|---|---|
| `chat_q` | the only `smart` control in any of the four configs |
| `chat_a` | written only by the backend's reply; `maxSubmissions="0"` hides its own input |

Submitting a question fires `regionFinishedDrawing`, the Data Manager turns that into
`POST /api/ml/<pk>/interactive-annotating` carrying every textarea region on `paper`
as context (`DataManager.jsx:157-191`), Label Studio forwards it to `/predict`, and
the reply comes back as suggestions. `<Text>` has `supportSuggestions: false`, and a
suggestion an object tag cannot display is accepted without a click
(`Annotation.js:1183-1192`) — which is what puts the answer in the annotation.

Three pieces of upstream behaviour make this sharp rather than merely fiddly, and
`test_chat_backend.py` pins all three:

- **`smart` defaults to *true* on every control** (`tags/control/Base.js:16`). With
  Auto-Annotation on, any region whose results include a smart control fires the
  round trip — and that is not just the comment box: drawing an evidence span
  notifies too (`RichText/model.js:427`), and so does deleting one. Left alone it
  would be an LLM call per highlight, each arriving with no question to answer. So
  `config_gen._mute_smart_controls` sweeps the finished tree and turns `smart` off on
  everything that has not explicitly asked for it.
- **A TextArea's result holds all of its submissions in one list**, and accepting a
  suggestion *replaces* the control's whole area. So the reply resends the entire
  answer log; returning just the new answer would erase the rest, and the reviewer
  would watch the history vanish as the new line appeared.
- **The context is every textarea region on `paper`**, grouped by region type and
  `to_name` only (`mixins/Regions.js:77-91`). The reviewer's `comment` and any
  correction boxes arrive here too, and the backend ignores everything that is not
  the chat.

What the annotation stores is the exchange, never the source: one question and one
answer per turn, measured at 254 bytes against a 39,812-byte paper. The paper goes
to the model and comes back as a few sentences and a quote, which is what the
instructions ask for.

Question and answer are paired positionally — nothing in the payload timestamps them
— so a call that never returned would shift every later pair by one. The gap is
padded with `(unanswered)` instead, and a failed call is written down as the answer
rather than raised as a 500, which keeps the pairing true and tells the reviewer what
happened where they are already looking.

### What the model is told

The whole paper, every prior turn of that task's chat, and the task's own data
rendered to text — everything the exporter put in `data` except `paper_url`, the
two address keys, and the span layer's label chips. Whatever shape it has: a ROWS
array becomes one line per row, and a record holding an array (`terms[].levels`)
nests.

That last part was wrong at first and worth stating, because it failed silently.
The filter admitted only short scalars, which is fine for a `value` task — the
extracted value is a string — but for the other two kinds *the object under review
is an array*. A contrast task reached the model as `task_kind: contrast` and four
other scalars: it knew the paper and nothing about the contrast, and answered from
the paper alone, confidently. Now the same task sends the paraphrase, the cells,
the covariates and the evidence excerpt, and "is this contrast in the right
direction?" is a question it can actually answer.

Measured per kind on `4cRnHYtfSwuK`: entities 695 chars, relationship 879, model
881, contrast 1693 — 0.2–2 KB against a ~40 KB paper. It sits below the paper in
the prompt, so the paper's prefix still caches: the contrast task above sent 8811
prompt tokens and reused 8313 of them.

What it is **not** told is the reviewer's own annotation. Label Studio sends it —
a captured `/predict` payload carried 1 annotation (2881 bytes), 1 prediction and
any drafts — and the backend reads only `task["data"]` and the two chat controls.
So it cannot see which verdict is selected, which spans have been drawn or deleted,
or what is in the comment box.

### Cost

A paper is ~8.5k tokens and a question is ~20, and a paper carries 35–50 value tasks,
so nearly all of the spend is re-sending the paper. The system message is therefore
the instructions and the paper and **nothing else** — byte-identical for every task
on that paper — and which task is open rides on the live question instead, ~40 tokens
below the paper where it changes nothing upstream of it.

Measured on `4cRnHYtfSwuK` (8.5k tokens) through the project gateway:

| | prompt | cached |
|---|---|---|
| first question, first task of a paper | 8511 | 0 |
| first question, every later task | 8475 | 8313 (98%) |
| second and later turns within a task | 8551 | 8516 (99.6%) |

The split was measured, not assumed, and the first arrangement was wrong: with the
task block inside the system message, the first question of every new task reported
`cached_tokens: 0` — three for three. This gateway only rewards a prompt that
*extends* a previous one, so a prefix that diverges before the paper ends buys
nothing. Moving the block below the paper is the whole difference between 0 and 98%.

`prompt_cache_key` is sent so the gateway routes one paper's traffic consistently; a
gateway that rejects the parameter is detected once and it is dropped for the rest of
the run. The staged text itself is read from disk once per paper, keyed on mtime and
size so re-running the exporter invalidates it.

## Reviewer workflow

Every task shows the full paper on the left and the object under review on the
right. Spans are pre-highlighted from the extraction and their boundaries are
draggable (`canResizeSpans` is true for text); spans sharing a label are jointly
required while separate sets are each independently sufficient — the `EvidenceSet`
semantics from `extraction-evidence.yaml`.

**value.** One entity instance. The read-only table is every populated field with
its value, status and priority; below it the excerpts, then one question. Answer
`all_correct` and you are done. Otherwise a paginated form opens with one card per
field carrying the field-level vocabulary — `correct`, `wrong_value`,
`wrong_evidence`, `wrong_both`, `should_be_not_reported`, `missed_value` (marked
not reported but the paper does state it), `uncertain` — and a corrected-value box.

**relationship.** A legend table saying what each candidate `local_id` *is*, a
pre-computed anomaly list, then one row per source object with checkboxes for the
targets (a dropdown for a single-valued slot, which also carries an explicit
`none`). Unused targets show up as empty columns.

**structure.** One of three, depending on the task. An **instance inventory** for
one class — every extracted `Group`, `Acquisition`, `Analysis` … with its descriptor
and reference count, and a per-row disposition of keep / rename / merge / drop /
split. One `ModelEstimation`'s **terms** as collapsible per-term forms with nested
level editing. Or one `Analysis`'s **contrast** as a paraphrase plus a
positive/negative/absent grid over every term the model declares.

References are never shown as bare ids. `config_gen.descriptor` renders
`grp_1 -- Parkinson's patients . n=20 . age 64.5` from the target class's priority-0
fields, derived at export and never stored — so it tracks
`storage-parameter-priorities.yaml` instead of drifting as a second copy of the
entity.

**adjudication.** Two canonical forms side by side with the diff above them, and a
resolution of `take_left`, `take_right`, `synthesize` or `escalate`. Canonical, not
raw: agreement on a structural task is computed on the sorted cell set, or control
ordering reads as disagreement.

Each verdict value names the failure it is for rather than being a bare
`correct`/`wrong`, so the diagnosis is countable instead of buried in free text.

## Finding the evidence without scrolling

The paper pane holds the full text, so a highlight near the end of a 25-60 KB
document starts off-screen. Two mitigations, neither requiring a code change:

**Press `Alt+.`** to jump the pane to the first highlight, centred. This is the
built-in `region:cycle` hotkey, and with nothing yet selected `selectNext()` picks
`regions[0]` (`stores/RegionStore.js:602`), which routes through
`annotation.selectArea` → `regionStore.highlight` → `region.selectRegion()`
(`stores/RegionStore.js:68`) → `scrollIntoView({block: "center"})`
(`mixins/HighlightMixin.js:216`). Pressing it again cycles to the next span, which
is useful when a set has several jointly-required spans.

**Read the excerpt block instead.** Each task renders its evidence with ~140
characters of context either side and the exact span delimited by `《 》`, so the
common judgement — does this passage support this value? — needs no scrolling at
all. The delimiters matter: they show where the extractor drew the boundary, which
is what separates `wrong_evidence` from a boundary that is merely off. Multi-span
sets are labelled "N spans, all required"; independently sufficient sets are
labelled `set 1`, `set 2`. Scroll the paper pane only when you actually need to
re-span or read wider context.

There is no config-only way to auto-select a region on load: nothing in the editor
does it, and `stores/SettingsStore.js` has no flag for it. Making it automatic
means patching `Annotation`/`RegionStore` to select the first region after
`initializeAnnotation`, rebuilding the `web/` bundle, and shipping a custom image.

## Measured on the first real extraction

`2abntY3hQSyq` (pmid 36967818, CC-BY 4.0, 24,731 chars):

| | |
|---|---|
| evidence tasks | 592 |
| cross-reference tasks | 132 |
| pre-highlighted spans | 398, all resolved exactly |
| task JSON | 1.01 MB (vs 17.91 MB inlined) |

By priority: 296 at priority 0, 135 at 1, 144 at 2, 4 at 3, 13 `n/a`. A
priority-0-only first pass is still 296 tasks per paper, or 592 judgments at two
reviewers — scope a pilot by entity class as well as priority.

## Theming

All colours in the generated config come from Label Studio's design tokens
(`var(--color-warning-background)`, `var(--color-neutral-content)`, …), never
hardcoded hex. `libs/ui/src/tokens/tokens.prefix.css:567` redefines those tokens
under `[data-color-scheme="dark"]`, so panels invert with the theme.

Hardcoding a panel background is the specific trap: the fixed light background
stays light while the *text* colour still comes from the theme, so dark mode
renders near-invisible light-on-light text. **Background and foreground must be
set as a pair** — `test_review_layer.py` asserts both (no hex in the `<Style>`
block, and no `.ns-*` rule that sets a background without a `color`).

Inline `style` on `<Header>` is parsed by `Tree.cssConverter`, which splits on
`;` then the first `:`. A `var()` reference survives that; a value containing a
semicolon would be silently truncated.

The `<Label>` chip colours are the one intentional exception — those are span
highlight colours, and Label Studio handles their text contrast itself.

## Known gaps

- **The exporter still emits the old task shapes.** `to_labelstudio.py` produces
  `tasks_{evidence,reference}.json`, one task per field. The four configs are
  written, validated and tested, but nothing feeds them yet.
  `config_gen.DATA_CONTRACT` and `config_gen.sample_task()` specify exactly what
  it must emit. This is the next piece of work and the only thing between here
  and a runnable pipeline.
- **`Repeater` is marked for deprecation** in Label Studio's source
  (`tags/visual/Repeater.js:47`). It works in `1.24.0.dev0`, and `1.22.0` accepts
  every config here, but it is undocumented. It is load-bearing because one
  project-wide config must adapt to a per-paper number of fields, rows and terms.
  The fallback is to pre-expand in `config_gen.py` against a fixed maximum N and
  live with empty slots, since `visibleWhen` reads only choices and regions,
  never task data.
- **Adding an object is only partly expressible.** A new term or level is created
  by drawing its span and labelling it `+ new ...`, so it is born attached to its
  evidence — but its fields then have to be typed into the note box rather than a
  structured form, because a form for an object that does not exist yet cannot be
  repeated over task data. Cells have no such problem: the grid already carries a
  row per declared term.
- **No auto-scroll to the span on task load.** See "Finding the evidence" above
  for the two mitigations in place. True auto-jump needs a frontend change:
  nothing auto-selects a region on load, and `SettingsStore.js` has no flag for
  it.
- **Data Manager columns do not exist for repeated controls.** `parse_config` runs
  on the unexpanded config, so `fv_0` is not in its control map. JSON export
  carries the results regardless, but filtering on annotation content is limited
  to the `data.*` keys in `config_gen.FILTER_KEYS`.
- **Multivalued list cardinality**, outside the analysis structure. A reviewer
  still cannot add or remove entries of an inlined list (`sex_distribution[2]`)
  from within a value task, only judge existing indexed entries.
- `LOCAL_FILES_SERVING_ENABLED` will serve anything under the document root. The
  mount is a dedicated `texts/` directory, read-only. `chat_backend.py` reads the
  same tree and confines the `d` parameter to it, because that value arrives from
  task data.
- **The chat is one exchange at a time, not a thread.** The answers accumulate in
  a read-only box above the question box; there is no chat bubble UI, because that
  needs JavaScript in the labeling interface and open source does not allow it.
  Multi-turn context is real — earlier turns are sent back to the model — but it
  reads as a log, not a conversation.
- **A Repeater project cannot be reconfigured once anyone has answered it.** This is
  the sharpest limit in the whole layer and it is worth stating exactly.
  `PATCH /api/projects/<id>/` re-validates the entire `label_config` against every
  existing annotation *and draft*, whatever the change was — a CSS-only edit trips
  it identically. `Project.validate_config` skips the check only when
  `num_annotations == 0 and num_drafts == 0` (`projects/models.py:676`); there is no
  force flag.

  It resolves control names through `check_control_in_config_by_regex`, which sounds
  like it would cover a generated name and does not: `parse_config` builds its
  `regex` entries for `$variable` names only, and on `structure.xml` it returns
  **zero** controls carrying one. Measured in the running container, `chat_q` matches
  the config and `tvar_0_0`, `st_m_0` and `lm_0` do not. So every
  Repeater-generated control is permanently unknown to the validator, and one draft
  touching one of them freezes that project's config for good.

  TextArea results are exempt (`:698`, DEV-1598), which is why the chat controls
  never block anything — only `choices` and `labels` do.

  The way through is a round-trip: export the annotations and drafts, delete them,
  push the config, re-create them over the API. The names they carry are still
  generated by the new config, so restored work renders correctly; it is only the
  validator that cannot see it. Do it when nobody has the project open, or the
  editor will auto-save the draft back mid-flight.
- **Chat text rides along in the annotation `result` array.** Nothing downstream
  breaks on it: `evidence_diff.py` selects `type == "labels"`, and the other
  scripts only count annotations. A decoder that walks results without filtering
  will need to skip `chat_q` and `chat_a`.
- **`Analysis.terms` has no priority entry**, so it exports as `unranked`. It is an
  extraction-only slot: storage carries terms under `Effect.terms`, so there is no
  `Analysis.terms` storage field for the priority inventory to rank.
