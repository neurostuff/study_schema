# Schemas

This repository is the schema and nothing else: LinkML YAML, and the prose that says how to
read a paper into it. There is no Python here.

Everything that *reads* the schema lives in
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
| `id` (`identifier: true`) | `local_id`, a plain string; storage mints its own at ingestion |
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

Because the extraction schema is a projection, `extraction-to-storage.map.yaml` is an
identity map. It holds 23 derivations and 5 free-text tables; everything else is the field
of the same name on the same class, with `.value` unwrapped.

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

## Tests

There is no Python here to test. This repository is the schema: LinkML YAML and the prose
that goes with it. Everything that reads it — the generator, the checks above, the
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
