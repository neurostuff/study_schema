# Schemas

This repository is the schema: LinkML YAML, and the prose that says how to read a paper into
it. The only Python here is generated from that YAML -- the `study-schema` package, which
hands the contracts to every repository that produces or consumes them (see
[The contracts as a package](#the-contracts-as-a-package)).

Everything else that *reads* the schema lives in
[pondie](https://github.com/neurostuff/pondie) — the generator, the checks this README
runs, the extraction pipeline, and the three modules that define what a record means
(`schema_utils`, `text_index`, `table_parse`). pondie carries this repository as a
submodule, so a schema change and the code that consumes it are versioned together without
either being a copy of the other.

`neuroimaging-study-storage.yaml` and the modules under `neuroimaging-study-storage/` are
the full storage schema: everything we might ever want to represent. It is the source of
truth and is never narrowed in place.

## Per-field metadata

Three axes annotate the storage fields, and they deliberately do not collapse into one. A
field can be filled deterministically and still be outside the MVP, or be low priority and
inside it.

| Axis | Where it lives | Question it answers |
|---|---|---|
| Does a release represent this field? | `in_subset: [mvp]` | Is this in the MVP? |
| How does the field get filled? | `in_subset: [deterministic]` or `in_subset: [model_extracted]` | Does code fill it — API lookup, local store, derived value, generated identifier — or does a language model read it out of the source? |
| How urgently does a human review it? | `storage-parameter-priorities.yaml`, keyed `Class.field` → `0`–`3` or `n/a` | Reviewer triage order |

The subsets are declared in `neuroimaging-study-storage/subsets.yaml` and marked on the
attribute itself:

```yaml
      publication_year:
        in_subset: [mvp, deterministic]
        range: integer
      design:
        in_subset: [mvp, model_extracted]
```

Identifiers are minted at ingestion, so they count as `deterministic` whether or not they
carry the mark. Type designators are structural rather than either: the extraction record
states which variant it is — nothing downstream could guess — and storage keeps the same
slot, so they are in both schemas and in neither subset.

```bash
python3 -m pondie.schema.checks.storage_parameter_priorities  # every field has exactly one priority entry
python3 -m pondie.schema.checks.field_provenance              # every field says how it gets filled
python3 -m pondie.schema.checks.field_provenance --strict     # ...and none are left unclassified
```

`field_provenance` fails on a field marked both `deterministic` and
`model_extracted`, or on an identifier marked `model_extracted`. Fields not yet classified
are reported as remaining work and only fail under `--strict`, so the check is usable while
the pass is in progress. It also prints where the marks disagree with `n/a` in the priority
file, as a second opinion rather than an authority — `n/a` has carried the same meaning as
`deterministic`, so a disagreement usually means a field changed hands and the priority
entry has not caught up.

## Generating the MVP schema

**Not implemented.** No `gen_mvp_schema.py` appears anywhere in this repository's history
and no `neuroimaging-study-storage-mvp` tree is committed, so what follows is the design a
generator would be written to. It is kept because the `mvp` marks it reads are on the
fields today, and because the constraints below are what makes them meaningful.

Attributes are the only thing marked. A class has no mark of its own — it survives when a
marked attribute's range points at it — so leaving every attribute of a class unmarked is
how a whole entity gets dropped. Identifiers and type designators are kept without a mark,
but do not by themselves keep a class alive.

The generated tree would be committed and must not be hand-edited. The generator must
refuse to write a structurally broken schema, reporting instead when a marked attribute
points at a
class with nothing marked in it, or when a surviving class drops a `required` field.
`required` is only enforced inside classes that survive, so a field required within an
entity we do not extract is not a problem.

Only `mvp` generates a schema. `deterministic` and `model_extracted` label fields across
the whole schema rather than slicing it — the two are interleaved down every path from the
tree root, so pruning to either would strand the other's fields. Each subset declaration
says which it is via a `generates_schema` annotation, and the generator refuses the ones
that do not.

## The extraction schema

The extraction schema is the storage schema with the values wrapped. Same classes, same
slots, same nesting; a scalar becomes an `ExtractedValue` carrying the evidence a model
found for it. It is generated, so that stays true:

```bash
python3 -m pondie.schema.generate           # writes neuroimaging-study-extraction{.yaml,/}
python3 -m pondie.schema.generate --check   # fails when the committed tree is out of date
```

The projection is mechanical, and the whole of it is:

| Storage | Extraction |
|---|---|
| `in_subset: [model_extracted]` | kept |
| `in_subset: [deterministic]` | dropped — code fills it, so there is nothing to read off the page |
| `id` (`identifier: true`) | `local_id`, a plain string and still the class's `identifier`; storage mints its own id at ingestion |
| `range: string` | `ExtractedString` |
| `range: integer` / `float` / `boolean` | `ExtractedInteger` / `ExtractedNumber` / `ExtractedBoolean` |
| `range: <Enum>` | `Extracted<Enum>`, a generated wrapper whose `value` is that same closed vocabulary |
| `any_of: [<Enum>, string]` | `Extracted<Enum>`, whose `value` keeps the same `any_of` — the escape hatch moves inside the wrapper |
| `multivalued: true` on any of the above | the cardinality moves inside: `Extracted<T>List`, one wrapper over a list, under one evidence record |
| `range: <Class>`, inlined | unchanged; the child is projected too |
| `range: <Class>`, not inlined | unchanged; LinkML resolves it through the target's `local_id` |
| `minimum_value`, `rules`, `unique_keys` | dropped — they constrain a scalar, and the wrapper is in the way. The run report lists each one |

The vocabularies come across whole: 34 enums, 186 permissible values, 180 of them with
descriptions, and each field's range copied exactly — closed where storage is closed, open
where storage left an escape hatch. A closed one compiles to a real `enum` in the generated
JSON Schema, so structured output can be constrained to it. Storage has 37; the three that do
not project are the ones no extracted field reaches — `EffectKind`, which no slot has as a
range at all, `StudyType`, which the API supplies, and `EdgeDirectionality`, which the mapper
derives.

Anything that is not mechanical lives in `extraction-deviations.yaml`, in two parts.
`required_additions` is what extraction has because it is an extraction — which model ran,
what text it read, where the sections of that text begin and end. `deviations` is where the
two schemas are allowed to disagree about the domain, and it starts empty: the baseline is
that they are identical, and each entry is a claim that a paper-reading model does better
with a different shape. The generator refuses an entry that does not say what it rests on.

## Keeping extraction and storage in step

The extraction schema is a projection, so `extraction-to-storage.map.yaml` is an
identity map. It holds 35 derivations, 5 free-text tables and 1 conditional field;
everything else is the field of the same name on the same class, with `.value` unwrapped.

```bash
python3 -m pondie.schema.checks.extraction_to_storage_map
```

Three things must hold, and each fails in its own way:

| Check | The drift it catches |
|---|---|
| Every extracted storage field has an extraction field of the same name, and every extraction field has a storage field to land in | A rename on either side, or a shape change applied through `extraction-deviations.yaml` without a matching map entry |
| `derivations` names exactly the storage fields marked `deterministic` | A field that changes hands — from an API lookup to something a model reads, or back |
| Each enum-ranged field's extraction wrapper carries the same vocabulary with the same range and the same cardinality | A projection that opens a closed vocabulary, letting through a value storage rejects — or closes an open one, making the extractor coerce |

**There is no normalize step, and this is recent.** Extraction used to flatten every
vocabulary to `ExtractedString`, and 16 tables holding 316 synonyms were the only route from
a paper's wording back to a permissible value. Now the extractor emits a value storage
already accepts, so the tables had nothing left to do and were deleted; `git log` has them.

The five fields storage keeps as `range: string` — `Group.age_unit`, `ModelEstimation.stage`
and friends — still have a table under `free_text_normalizations`. There is no vocabulary to
project and nothing makes their wording consistent, so it earns its place. It normalizes for
queryability rather than for validity: an unmatched value is already storable, which is also
why nothing checks it. `ModelEstimation.stage` is the clearest case — it labels an estimation
stage in the source's words, and which stage fed which is `inputs_from`, so a value the table
misses costs a facet and nothing structural.

The direction that check three catches is worth naming, because only one half of it is
loud. Opening a closed vocabulary fails at ingestion. Closing an open one works fine and
quietly costs you something: an open vocabulary is where the paper's own wording is worth
keeping, since an answer the vocabulary has no slot for is the evidence it is short a value.

## The paper-parse schema

`neuroimaging-paper-parse.yaml` and `neuroimaging-paper-parse/` hold the two artifacts the
ingestion workflow hands downstream, as contracts both sides validate against:

| Contract | Class | Producer | Consumers |
|---|---|---|---|
| C2 | `ParsedPaper`: the text every offset addresses, tables row by row, bibliography, `is_meta_analysis` | ns-pond-ingestion-workflow (extract, metadata, triage) | the workflow's coordinate stages, pondie, neurostore |
| C3 | `CoordinateParse`: points grouped into keyed analyses, with roles and sign splits | ns-pond-ingestion-workflow (analyses, resolve, space); pondie for a revision | pondie, neurostore |

They live here rather than as a NIMADS extension because the extraction and storage
records point into them: evidence spans address `ParsedPaper.text_sha256`, and
`Analysis.source_table_analysis` names a C3 key. NIMADS stays the meta-analysis
interchange. A `ParsedPoint`'s `coordinates`, `space` and `values` carry NIMADS's names and
meaning, so projecting a parse onto a studyset is a field copy.

What each side may rely on:

- **One shape for every source.** PMC, Europe PMC, pubget, Elsevier, ACE and PDF all emit
  a `ParsedPaper`, so a consumer never branches on where a paper came from.
- **Bibliography is copied, never extracted.** pondie fills the record's `Study.title`,
  `authors`, `doi`, `journal`, `publication_year`, `language` and `study_type` from
  `ParsedPaper.bibliography` and the header's identifiers (`transform: copy` in
  `extraction-to-storage.map.yaml`), and skips a paper whose `is_meta_analysis` is true.
- **Keys come from where an analysis was read, not its position.** A table analysis is
  `<table_id>#<h>`, where `h` hashes the sorted `row:column_group` references of its
  `cells`; a text or figure analysis is `text#<h>` or `figure#<h>` over its spans
  and its normalized name, since one sentence can name several analyses
  (`ParsedAnalysis.key` gives the recipe). Cells include rows that name a contrast without
  coordinates, so a contrast a table lists as "n.s." has a key of its own. A re-run that
  reads the same cells reaches the same key, and claims stored against it carry over.
- **A sign split is declared.** Both halves carry `split{half, rule}`, `half` being
  `original` (the analysis as named) or `inverse` (the reversed contrast); the inverse half's
  `original_analysis` is the original's key. Points with a negative value, of any kind, form
  the inverse half. Points with only p, F or chi-square values, or none, are unsigned and join
  the original half. pondie extracts the original and derives the inverse, which carries
  `mirror_of`.
- **Grouping and role are proposals; pondie's verdict overrides them.** When pondie
  disagrees, it writes a revision: a complete `CoordinateParse` with `revision_of` set and
  one `AnalysisVerdict` per original analysis (accept, relabel, split, merge, omit, each
  with a reason and evidence). Accepted and relabelled analyses keep their keys. A split
  or merge copies the claims and votes stored against the old keys to their replacements,
  each copy noting where it came from; the revised version is kept unchanged as history
  and left out of projections (`AnalysisVerdict` gives the rules). Region,
  seed and target sets are relabelled `anchor` and become CoordinateSets in the record,
  not Analyses with statistics.
- **Uploads are asymmetric.** neurostore accepts a parse alone, a parse with a record, a
  record alone against a parse already stored (every verdict accept), or a revision with a
  record. A revision is stored as a new version of the paper's analyses
  and supersedes the parse it revises; the record always names the `parse_id` it was
  extracted against.
- **Nothing is edited in place.** A parse is never rewritten by its reader; disagreement
  is a revision.
- **Absence is stated, never inferred.** A paper the parse ran on always has a
  `CoordinateParse`, even with no analyses, and `tables` says what reading each table
  found: coordinates, contrasts listed without coordinates, no coordinates at all, or not
  read (triage, a person). `text_sweep` says whether the search of the text was complete.
  An analysis with no points is a contrast the paper names but prints no coordinates for.
  pondie extracts a paper when its parse has coordinates or contrasts listed without them;
  a paper whose nulls are stated only in prose is not sent, for cost. Within an extracted
  paper, pondie adds what the parse could not see -- a null stated only in prose, a
  figure-only result -- with an `add` verdict in a revision, so every analysis the record
  describes has a parse key.
- **A study with no analyses, or only null ones, is described, not dropped.** The record
  says which with `Study.result_reporting` (`analyses_reported`, `no_analyses_reported`,
  `analyses_reported_elsewhere`) and each `Analysis.outcome`; `Study.outcome_summary` is
  derived from them (`some_significant_effects`, `only_null_effects`, `no_analyses`,
  `undetermined`).
- **Null analyses reach the meta-analysis.** An analysis with no coordinates is carried
  into the studyset with its outcome, not dropped for lacking points, so the estimators
  whose result depends on how many studies found nothing (MKDAChi2, CBMR) can count them.
- **One analysis is one test, with every value that describes it.** A point carries the
  test statistic and its companions -- p values with their correction, the cluster's p --
  in `values`, each with a `level` (peak or cluster). A different test is a different
  analysis, even on the same rows: an omnibus F and its directional post-hoc t are two.
  `thresholds` holds every level the paper states (a voxel height and a cluster
  correction together), so pondie copies them into InferenceSettings rather than
  re-reading them. Where storage's single `multiple_comparison_method` cannot hold two
  levels, the parse keeps both.
- **Roles are classified, not proposed.** The `roles` stage sets `role`, `anchor_kind`,
  `from_prior_study` (a flag beside the role, not a role), `role_confidence` and
  `role_source`; below its confidence threshold the proposal stands.
- **Retracted papers are marked and excluded by default.** `Bibliography.corrections`
  carries the retraction notice; a studyset leaves the paper out unless a filter asks for
  it.

Not done yet:
- Neither the workflow nor pondie writes or reads these artifacts.
- `Analysis.source_table_analysis` and `CoordinateSet.id` in storage and extraction now
  describe the cell-derived key, but pondie still mints the positional
  `<table id>#<ordinal>` key and spells text keys `prose#` rather than `text#`.

## The contracts as a package

Four artifacts cross a repository boundary, and each has one definition here. The
`study-schema` Python package carries them as pydantic models and JSON Schemas generated from
the YAML, so a producer and a consumer cannot hold different copies of a contract:

| Contract | Written by | Read by | Model | JSON Schema |
|---|---|---|---|---|
| Parsed paper | ingestion | pondie, the ingester | `study_schema.models.paper_parse.ParsedPaper` | `parsed-paper` |
| Coordinate parse, original or revision | ingestion; pondie for a revision | pondie, the ingester | `study_schema.models.paper_parse.CoordinateParse` | `coordinate-parse` |
| Extraction record | pondie | the ingester | `study_schema.models.extraction.Study` | `extraction-record` |
| Storage study | the ingester | neurostore | `study_schema.models.storage.Study` | `storage-study` |

The studyset neurostore hands compose-runner and NiMARE is NIMADS, which neurostore's OpenAPI
already specifies; it is not repeated here.

```bash
pip install "study-schema @ git+https://github.com/neurostuff/study_schema"            # models + JSON Schema
pip install "study-schema[layouts] @ git+https://github.com/neurostuff/study_schema"   # + file layouts
```

```python
from study_schema.models.paper_parse import CoordinateParse
parse = CoordinateParse.model_validate_json(path.read_bytes())
```

Every model forbids fields its schema does not declare, so a file written against another
version fails where it is read, naming the field, instead of losing what the reader does not
recognise. Each model module records the schema version it was generated from as `version`,
and every paper-parse artifact states the version it was written against in its header.

**Where the files sit.** `study_schema.layouts` declares the file layout of the two trees the
contracts travel in, as [pyarty](https://github.com/jdkent/pyarty) bundles that read straight
into the models and write back byte for byte:

    <corpus>/<study_id>/parse/parsed_paper.json        ParsedPaper           ingestion writes
    <corpus>/<study_id>/parse/coordinate_parse.json    CoordinateParse       ingestion writes
    <run>/records/<study_id>.extraction.json           extraction Study      pondie writes
    <run>/revisions/<study_id>.coordinate_parse.json   CoordinateParse       pondie writes

Only those files are the contract; a read ignores everything else in either tree, so
ingestion's own `stage1/` and `processed/` files and pondie's payloads stay theirs to change.
A revision lives with the run that made it, not in the corpus: a reader never writes its
inputs. The layouts need a pyarty that reads pydantic payloads (`File[Model]`).

**What one file cannot say.** A model checks a file; `check_paper`, `check_revision` and
`check_run` check how files agree -- a parse addresses the same text as its paper, every key is
the one its cells or spans derive, and a revision gives a verdict for every analysis it
revises and accounts for every analysis it introduces. `study_schema.keys` is the key rule
itself, so ingestion and pondie mint keys with the same code rather than two copies of a
sentence.
`study_schema.spaces.normalize_space` is the same for coordinate spaces: it folds any stated
space to `ReportedSpace`'s `MNI`, `TAL` or `OTHER`, or to none when nothing (or both) is stated,
so ingestion, pondie and neurostore read "Talairach & Tournoux 1988" the same way.

**Examples.** `examples/` holds one paper and one run: a table whose ingestion parse is a
single mixed-sign analysis, and pondie's revision that splits it by sign and adds the null
result the text states. The tests read them through every model, JSON Schema and layout, and
the checks must find nothing wrong with them.

**Storing coordinates.** The models say what a parse means, not how a corpus of them is
kept. `study_schema.parquet` stores coordinate parses as three Parquet tables -- `parses`,
`analyses`, `points` -- whose columns are derived from the generated models, so a schema change
reaches them on regeneration and `read(write(parses)) == parses` holds (the tests check it).
The points table uses NIMADS's names where they mean the same thing, and neurostore's `Point`
columns for what NIMADS leaves out:

| NIMADS / neurostore `Point` | `points.parquet` | `ParsedPoint` |
|---|---|---|
| `coordinates` [x, y, z] | `x`, `y`, `z` | `coordinates` |
| `space` | `space` | `space` |
| `values` [{kind, value}] | `values` [{kind, value, level, correction, kind_as_printed}] | `values` |
| `subpeak` | `subpeak` | `is_subpeak` |
| `cluster_size` | `cluster_size` | `cluster_size` |
| `cluster_measurement_unit` | `cluster_measurement_unit` | `cluster_measure` |
| `order` | `order` | position in `points` |
| `analysis` | `analysis_key` | `ParsedAnalysis.key` |

An analysis keeps NIMADS's `name` and `description`, and `analyses.point_count` is 0 for a
null result, so null analyses can be counted without reading a point. Over 5,000 synthetic
parses whose points are the real coordinates of NiMARE's Laird studyset (338,276 points,
with generated rows, t values and labels), indented JSON took 285 MB, minified JSON with
zstd 8.7 MB, and the Parquet tables 3.1 MB -- 9 bytes a point -- and reading every point's
study, analysis and x, y, z takes 0.02 s. JSON stays the format files are handed over in;
Parquet is for keeping and querying many of them.

**Regenerating.** The generated files are committed, so installing the package needs no
LinkML. After changing the YAML:

```bash
pip install -e ".[generate,layouts,parquet,test]" jsonschema
python tools/generate_models.py           # rewrite src/study_schema/{models,jsonschema}
python tools/generate_models.py --check   # what CI runs: fails if they are out of date
pytest
```

## Tests

The generated package has its own suite here (`pytest`, above). Everything else that reads
the schema—the extraction generator, the checks above, the
extraction pipeline, and the modules that define what a record means (`schema_utils`,
`text_index`, `table_parse`) — lives in
[pondie](https://github.com/neurostuff/pondie), which carries this repository as a
submodule and runs its own suite against it:

```bash
cd pondie && python3 -m pytest
```

The commands in this README are therefore run from a pondie checkout, and resolve the
schema through `PONDIE_SCHEMA_DIR` or through the submodule. Point them at this checkout
if it is not the submodule one:

```bash
PONDIE_SCHEMA_DIR=/path/to/study_schema python3 -m pondie.schema.checks.linkml
```

The Label Studio review layer lives in the
[ns-validate](https://github.com/neurostuff/ns-validate) repo, which reads the schema and
the priority inventory from here.


## Documents

| File | What it holds |
|---|---|
| [schema-tutorial.md](schema-tutorial.md) | The course: twelve chapters teaching the whole schema — what every entity is for, what each field holds and what it must not be confused with, the rules for filling a record, and the reasoning steps to take when a paper is ambiguous. Start here if you are new to the schema; the documents below are the references it teaches from |
| [extraction-readme.md](extraction-readme.md) | Rules the schema cannot state: the gates that skip a paper, extraction conventions, validator invariants, mapper responsibilities, and known limits |
| [extraction-deviations.yaml](extraction-deviations.yaml) | Every way the extraction schema is not a projection of storage. Currently: pipeline provenance, and nothing else. Its trailing comment lists the six shapes the hand-written schema used to have, as candidates to re-test |
| [storage-schema-design-notes.md](storage-schema-design-notes.md) | Why the storage schema is shaped the way it is |
| [analysis-entities.md](analysis-entities.md) | What each entity around an analysis is called, what owns it, and what points at what: the three layers, a crosswalk from the words papers use, and the joins a synthesis reads a record back out through. Start here before representing-models.md |
| [representing-models.md](representing-models.md) | How to put a reported analysis into the schema: which facts belong to the model and which to the contrast, where each class's job ends, and what to do when a paper does not divide things the way the schema does. Its YAML fragments and the worked records under `pondie/tests/fixtures/examples/` are checked on every test run |
| [ars-crosswalk.md](ars-crosswalk.md) | Field-level comparison against CDISC's Analysis Results Standard — the closest peer this schema has, and so the main check on the model/contrast split. For design comparison, not an executable map |
| [standards-crosswalk.md](standards-crosswalk.md) | The same reading against BIDS Stats Models and NIDM-Results — what each represents, where they are more expressive, and where this schema keeps something they cannot say. Companion to the ARS crosswalk and does not repeat it |
| [storage-schema-expressivity-probe.md](storage-schema-expressivity-probe.md) | Measured expressivity gaps against 25 corpus papers, with options |
| [multivariate-probe.md](multivariate-probe.md) | The same reading narrowed to decoding, RSA, PLS and searchlight work: what a meta-analyst cannot filter on today, ranked, with options. Companion to the probe above and numbered `M1`–`M8` so the two do not collide |

# Ideation about LLM extraction workflow

| LLM entity identification | Emit |
|---|---|
| `Group` | `local_id`, `name` |
| `Task` | `local_id`, `name` |
| `Acquisition` | `local_id`, `name` |
| `Preprocessing` | `local_id`, `name` |
| `StatisticalModel` | `local_id`, `name` |
| `Assessment` | `local_id`, `name` |
| `Region` | `local_id`, `name` |
| `Predictor` | `local_id`, `name` |
| `Condition` | `local_id`, `name` |


independent parsing of tables.

| LLM table parsing | Emit |
|---|---|
| `Analysis` | the coordinates and name of the analysis |


## Notes

Tasks can have multiple acquisitions, from either
simultaneous recordings (EEG+fMRI) for a particular task,
or from multiple sites/or the scanner changing during data collection. I am not representing the difference on purpose.
