#!/usr/bin/env python3
"""Generate the Label Studio labeling configs for extraction review.

Four projects, one config each, because a Label Studio project holds exactly one
labeling config (`projects/models.py:198`) and `maximum_annotations` is per
project (`:259`). Splitting by task kind rather than by paper is what lets each
kind carry its own UI and its own reviewer overlap:

  value         one task per entity instance. Every populated field of that
                entity, its value, its evidence excerpt, and a span layer whose
                labels are the entity's own field names.
  relationship  one task per association slot per paper. Rows are the source
                objects, columns the candidate targets, so the whole assignment
                is judged at once and an unused target shows up as an empty
                column.
  structure     one config, three task kinds -- the paper's analysis inventory,
                one ModelEstimation's terms, one Analysis's contrast. They share
                a project because they share an overlap policy and are answered
                in one sitting.
  adjudication  two reviewers' canonical forms side by side, plus a resolution.

Grouping by paper is a Data Manager view on `data.paper_id`, never a project.

## The paper text is never inlined

Every config carries the same `<Text valueType="url">`, so the browser fetches the
text once per paper and serves it from cache for that paper's remaining tasks.
Three attributes are load-bearing and the tests assert all three:

  valueType="url"        keeps the text out of the task JSON
  saveTextResult="yes"   RichTextRegion only emits `value.text` when it is set
                         (regions/RichTextRegion.js:116) and it defaults to
                         "none" (RichText/model.js:62); omit it and drawn spans
                         come back with no text
  granularity="symbol"   character-exact selection instead of word-snapped

## Every config carries a chat box, and nothing else is smart

Under the paper text sit `chat_q` and `chat_a`, the two ends of Label Studio's
interactive-preannotation round trip; `review/chat_backend.py` is what answers.
The answer is written into the annotation, so a question a curator had to ask
before deciding exports with the decision.

`smart` defaults to **true** on every control (`tags/control/Base.js:16`), and
with Auto-Annotation on -- which the chat requires -- any region whose results
include a smart control fires that round trip. That includes drawing a span on
the paper (`RichText/model.js:427`) and deleting one (`mixins/Regions.js:151`),
so `_mute_smart_controls` turns `smart` off on everything that has not asked for
it. `chat_q` is the one control that does.

## How one config serves several task kinds

`Repeater` is expanded at config-parse time against the task's own data, and an
absent or empty key yields zero copies (`core/Tree.tsx:70-73`,
`parseValue(...) || []`). So a block wrapped in `<Repeater on="$contrast">`
renders only for tasks carrying a `contrast` key, and a `required="true"` control
inside it is never instantiated for the other kinds and so never blocks
submission. The `structure` config uses this; `value` and `relationship` each
serve one kind and put their controls at the top level.

Gate arrays hold exactly one element, so `{{idx}}` inside a gate resolves to 0 --
which is how `whenTagName="contrast_verdict_{{idx}}"` reaches the verdict control
declared in the same block without hardcoding the index.

## Repeater caveats these configs stay inside

  * `{{idx}}` is substituted with String.replace and a string pattern
    (`core/Tree.tsx:48`), so only the FIRST occurrence in an attribute is
    replaced. No attribute here contains an index flag twice.
  * Substitution touches attributes only, never text content
    (`core/Tree.tsx:41`), so every label is an attribute value.
  * `Panel` admits `view` but not `pagedview` or `markdown`, so a paginated
    Repeater never goes inside a Panel and Markdown is always wrapped in a View.
  * `Repeater` is marked for deprecation in source (`tags/visual/Repeater.js:47`).
    It is load-bearing here because one project-wide config must adapt to a
    per-paper number of fields, rows and terms.

Usage:
    python review/config_gen.py --out-dir review/ls_config
    python review/config_gen.py --out-dir review/ls_config --record review/examples/x.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from xml.etree import ElementTree

KINDS = ("value", "relationship", "structure", "contrast", "adjudication")

#: Families whose form declares no required verdict, because the reviewer's edits
#: ARE the answer. Only `relationship` qualifies: its grid arrives pre-ticked from
#: the extraction, so submitting it unchanged is already an assertion. Everywhere
#: else a task can be submitted without the reviewer having touched anything, and
#: the verdict is what distinguishes "checked and correct" from "not looked at".
VERDICTLESS_KINDS = frozenset({"relationship"})

#: The two controls the chat backend reads and writes. `review/chat_backend.py`
#: imports these names rather than restating them, because a rename on one side
#: alone is silent: the backend would answer a question it never sees and write
#: into a control that does not exist.
CHAT_QUESTION = "chat_q"
CHAT_ANSWER = "chat_a"

#: Floor on the number of evidence-set labels the exporter builds per field. The
#: config no longer declares set labels statically -- `<Labels value="$span_labels">`
#: takes them from task data -- which removes the old coupling where the exporter
#: could emit a "set N" label the config had not declared and the span would then
#: silently fail to render. The constant survives as the exporter's own headroom
#: rule: in the first real extraction 340 of 341 evidenced fields used exactly one
#: set and a single field used two, so three leaves room for a set the extractor
#: missed.
DEFAULT_MIN_SETS = 3

#: Distinct hues so co-existing evidence sets are separable at a glance. The
#: exporter assigns one per (field, set) pair. Hardcoded because Label Studio
#: manages the text contrast of span highlights itself -- these are the one
#: intentional exception to the tokens-only rule below.
_SET_COLOURS = ["#ff9800", "#03a9f4", "#8bc34a", "#e91e63", "#9c27b0", "#795548"]

# -- verdict vocabularies --------------------------------------------------
#
# Each names the failure it is for. A vocabulary of `correct`/`wrong` pushes the
# diagnosis into free text, where nothing can count it.

_ENTITY_VERDICTS = [
    ("all_correct", "Every field above is right, with evidence that supports it"),
    ("some_wrong", "At least one field is wrong -- mark which below"),
    ("uncertain", "Cannot determine from the paper"),
]

_FIELD_VERDICTS = [
    ("correct", "Value and evidence are both right (or correctly marked not reported)"),
    ("wrong_value", "Evidence points at the right passage but the value is wrong"),
    ("wrong_evidence", "Value is right but the evidence does not support it"),
    ("wrong_both", "Neither the value nor the evidence is right"),
    ("should_be_not_reported", "The paper does not report this; the extractor invented a value"),
    ("missed_value", "Marked not reported, but the paper does state it"),
    ("uncertain", "Cannot determine from the paper"),
]

#: How a highlighted passage relates to the value, judged per span. Not the same
#: axis as `ValueSource` in extraction-evidence.yaml, which is a property of the
#: value (`reported` vs `generated`) rather than of the passage: a value the
#: extractor composed can still have a passage that states it outright, and a
#: value quoted from the paper can rest on a sentence that only implies it.
#:
#: Whether a passage supports the value at all is no longer asked -- a reviewer
#: deletes a passage that does not, and `evidence_diff.py` reads the deletion.
_SUPPORT_KINDS = [
    ("direct support", "The passage states the value"),
    ("inferred support", "The value follows from the passage but is not stated in it"),
]

# The relationship family has no verdict vocabulary. It had one -- links_correct,
# links_wrong, target_missing, target_spurious, uncertain -- and every entry was
# either a restatement of the grid the reviewer had just set, or a claim about
# which objects exist. Existence belongs to the stage-0 entity inventory, whose
# `instance_missing` / `instance_spurious` say the same thing at the point where
# the correction can actually be applied.

# The inventory vocabulary is generic over classes: "did the extractor find the
# right set of Groups / Acquisitions / Analyses?" is one question with one shape.
# It is stage 0 because every other task addresses objects by local_id, so a
# wrong object set invalidates work in every other family.
_INVENTORY_VERDICTS = [
    ("inventory_correct", "The right instances, and no others, were extracted"),
    ("instance_missing", "The paper describes one that is not here"),
    ("instance_spurious", "One here is not a real instance of this class"),
    ("duplicates_present", "Two rows are the same thing"),
    ("should_split", "One row covers two distinct things"),
    ("uncertain", "Cannot determine from the paper"),
]

#: Per-instance dispositions. These map onto the propagation rules exactly:
#: `merge` and `rename` are a rewrite map that `build_record.apply_aliases`
#: applies to reference slots only, mechanically and completely. `drop` and
#: `split` have no target to rewrite to, so every reference to that instance is
#: also wrong and its downstream tasks have to be regenerated rather than fixed.
_INSTANCE_DISPOSITIONS = [
    ("keep", "A real, distinct instance"),
    ("rename", "Right instance, wrong label -- propagates as a rewrite"),
    ("merge", "Same thing as another row -- name it below; propagates as a rewrite"),
    ("drop", "Not a real instance; every reference to it needs re-review"),
    ("split", "Two things recorded as one; every reference to it needs re-review"),
]

_MODEL_VERDICTS = [
    ("terms_correct", "The term list and its levels are right"),
    ("term_missing", "The paper states a term this model does not list"),
    ("term_spurious", "A term here is not real -- mark which below to drop it"),
    ("term_wrong", "A term's name, type or scope is wrong -- fix it below"),
    ("levels_wrong", "A factor's levels are wrong or incomplete"),
    ("uncertain", "Cannot determine from the paper"),
]

_CONTRAST_VERDICTS = [
    ("accept", "The record says what the paper says"),
    ("direction_wrong", "Right terms, wrong sides"),
    ("wrong_axis", "The comparison is on the wrong term"),
    ("cells_wrong", "A cell is missing, or one is here that should not be"),
    ("statistic_wrong", "The statistic or its degrees of freedom are wrong"),
    ("upstream_wrong", "This analysis should not exist in this shape -- see the inventory task"),
    ("uncertain", "Cannot determine from the paper"),
]

#: Stage 0 over one coordinate table: is this the right set of analyses, drawn from the
#: right rows? Every entry names a failure, because a vocabulary of correct/wrong pushes
#: the diagnosis into free text where nothing can count it -- and these six are the
#: findings the split actually produces.
_TABLE_VERDICTS = [
    ("segmentation_correct", "Each analysis drawn from this table is one real analysis, "
                             "and none is missing"),
    ("over_split", "One analysis was split into two or more"),
    ("merged", "Two analyses were recorded as one"),
    ("missed_analysis", "The table reports an analysis the parser did not find"),
    ("wrong_rows", "The split is right, but a row is attributed to the wrong analysis"),
    ("not_analyses", "This table reports no analysis -- demographics, an ROI definition, "
                     "a stimulus list"),
    ("uncertain", "Cannot determine from the table and the paper"),
]

#: Per parsed analysis, the same shape as _INSTANCE_DISPOSITIONS: without it a reviewer
#: can say "one of these is two analyses" and has nowhere to say which one.
_ANALYSIS_DISPOSITIONS = [
    ("keep", "A real, whole analysis"),
    ("split", "Two analyses recorded as one -- every task drawn from it needs re-review"),
    ("merge", "The same analysis as another row -- name it below"),
    ("drop", "Not an analysis -- every task drawn from it needs re-review"),
    ("rows_wrong", "Right analysis, wrong rows attributed to it"),
]

#: Offered per task rather than as a fixed set: the exporter drops any option whose
#: subject the record does not hold. No analysis across the three baseline papers
#: records degrees of freedom, so the old fixed `df_wrong` -- "wrong or missing" --
#: asked every reviewer about a value that was never there, and its "or missing" half
#: would have been true on all 18.
#:
#: `df_absent` replaces that half and is the option that *is* answerable when the
#: record has no df: it asks about the paper, not the record. Dropping it entirely
#: would lose the finding along with the noise.
_STATISTIC_VERDICTS = {
    "statistic_correct": "Right as recorded",
    "family_wrong": "The wrong statistic family",
    "df_wrong": "The degrees of freedom are wrong",
    "df_absent": "The paper reports degrees of freedom the record omits",
}

_TERM_VERDICTS = [
    ("correct", "This term is right as recorded"),
    ("wrong_name", "The name misdescribes the term"),
    ("wrong_type", "Categorical vs continuous is wrong"),
    ("wrong_variation_level", "Within- vs between-subject is wrong"),
    ("should_be_two_terms", "This is two terms recorded as one"),
    ("duplicate_of_another_term", "Another term in this model is the same term"),
    ("not_a_term_of_this_model", "The model does not contain this term -- drops it"),
    ("spurious_interaction", "interaction_with is set on a crossing the cells already state"),
]

_RESOLUTIONS = [
    ("take_left", "Reviewer A is right"),
    ("take_right", "Reviewer B is right"),
    ("synthesize", "Neither is wholly right -- the corrected form is below"),
    ("escalate", "Needs a domain decision beyond this task"),
]

# -- styling ---------------------------------------------------------------
#
# Colours come from Label Studio's design tokens, never hardcoded hex.
# `tokens.prefix.css` defines each of these twice -- once at the root and once
# under `[data-color-scheme="dark"]` (line 567) -- so they invert with the theme.
#
# Hardcoding a background is the specific bug this avoids: a fixed light panel
# keeps the theme's light-on-dark text colour in dark mode, leaving invisible
# text on a pale box. Background and foreground must always be set as a pair,
# which test_review_layer.py asserts.

# No child combinators, and no "<", ">" or "&" anywhere in this block. Style
# content is passed through `sanitizeHtml` (tags/visual/Style.jsx), which escapes
# them -- and one mangled selector invalidates its whole comma-separated rule, so
# a single `.ant-table-tbody > tr > td` silently voided the neighbouring
# `.ant-table` declarations and the panel kept rendering white with no error.
# Descendant selectors only.
_STYLE = """
.ns-row { display: flex; gap: 16px; align-items: flex-start; }
/* A flex column, not a scroller: the text scrolls inside .ns-paper-body and the
   chat box stays pinned under it, where a question about what is on screen is
   asked without scrolling back. */
.ns-paper { flex: 1 1 55%; max-height: 82vh; display: flex; flex-direction: column;
            padding-right: 12px;
            border-right: 1px solid var(--color-neutral-border); }
/* Monospace, because the paper text now carries its own formatting: headings over a
   rule, and coordinate tables as markdown with the columns padded to a common width.
   None of that lines up in a proportional face, and the pane cannot render markup --
   it is a Text tag, which is the only region-bearing tag whose offsets serialize as
   plain integers, and naming it in angle brackets here would trip the Style sanitizer. */
.ns-paper-body { flex: 1 1 auto; min-height: 0; overflow-y: auto;
                 font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
                 font-size: 12.5px; line-height: 1.5;
                 background: transparent; color: var(--color-neutral-content); }
.ns-chat { flex: 0 0 auto; margin-top: 8px; padding-top: 8px;
           border-top: 1px solid var(--color-neutral-border); }

/* The exchange reads question, answer, question, answer, with the box you type
   in at the bottom -- see _chat_pane and _chat_order_css.

   Each control renders its own entries as a block: every question, then every
   answer. Interleaving them means the individual entries have to become siblings
   in one column, which is what `display: contents` does here -- it drops the two
   wrappers, each control's block, and each list container out of the layout, so
   what is left as flex items of .ns-chat-body are the entries themselves
   (`div.lsf-row`, regions/TextAreaRegion.jsx) and the form.

   `div:has(...)` rather than a child combinator: sanitizeHtml escapes the
   child-combinator character and voids the whole rule it appears in, which here
   would leave the wrappers opaque and the order unchanged, silently. A row never
   contains a row or a form, so nothing inside an entry is flattened by these. */
.ns-chat-body { display: flex; flex-direction: column; }
.ns-chat-q, .ns-chat-a,
.ns-chat-q div:has(form),
.ns-chat-q div:has(.lsf-row),
.ns-chat-a div:has(.lsf-row) { display: contents; }
/* Past CHAT_TURNS an entry keeps this and falls in after the paired ones, still
   above the typing bar, rather than jumping to the top on order:0. */
.ns-chat-q .lsf-row, .ns-chat-a .lsf-row { order: 998; }
.ns-chat-q form { order: 999; margin-top: 4px; }

/* The question box reads as a composer. There is no submit button to place: the
   chat box sets showSubmitButton="false" and Shift+Enter submits, which any
   multi-row TextArea binds anyway (TextArea.jsx:376-386, independent of the
   button). An absolutely-positioned button was tried and it overlapped the
   submitted question beneath it -- the region list renders after the form and
   outside it, so there is nothing to anchor to that stays clear.
   `textarea` rather than a Label Studio class on purpose: 1.22 renders
   `lsf-text-area`, 1.24 renames the block, and both have a textarea. Inside
   .ns-chat there is exactly one, because chat_a carries maxSubmissions="0". */
.ns-chat form textarea { border-radius: 14px !important; resize: none !important;
                         padding: 11px 14px !important; }
/* 1.24 puts a character/submission counter under the box; it is noise here. */
.ns-chat form [data-testid="textarea-counts"],
.ns-chat form [data-testid="textarea-instruction"] { display: none !important; }
.ns-form { flex: 1 1 45%; max-height: 82vh; overflow-y: auto; }
.ns-llm { background: var(--color-warning-background);
          color: var(--color-neutral-content);
          border-left: 3px solid var(--color-warning-border);
          padding: 8px 12px; margin: 8px 0; border-radius: 3px; }
.ns-quote { background: var(--color-positive-background);
            color: var(--color-neutral-content);
            border-left: 3px solid var(--color-positive-border);
            padding: 8px 12px; margin: 8px 0; border-radius: 3px; }
.ns-para { background: var(--color-primary-background);
           color: var(--color-neutral-content);
           border-left: 3px solid var(--color-primary-border);
           padding: 10px 12px; margin: 8px 0; border-radius: 3px;
           font-size: 15px; line-height: 1.5; }
.ns-warn { background: var(--color-negative-background);
           color: var(--color-neutral-content);
           border-left: 3px solid var(--color-negative-border);
           padding: 8px 12px; margin: 8px 0; border-radius: 3px; }
.ns-card { background: var(--color-neutral-surface);
           color: var(--color-neutral-content);
           border: 1px solid var(--color-neutral-border);
           padding: 10px 12px; margin: 10px 0; border-radius: 4px; }
.ns-cell { background: var(--color-neutral-surface);
           color: var(--color-neutral-content);
           border-bottom: 1px solid var(--color-neutral-border-subtle);
           padding: 4px 8px; }
.ns-side { display: flex; gap: 12px; align-items: flex-start; }

/* Label Studio's Choices, Table and Collapse are antd components, and
   assets/styles/antd-no-reset.css hardcodes colours on 87 of their rules --
   `.ant-radio-wrapper { color: rgba(0,0,0,0.85) }` with no background of its
   own, `.ant-collapse-content` likewise. Those inherit onto the label text and
   go invisible against a dark page. Theme tokens invert with the scheme, so
   these overrides are what make the controls readable in both modes; each sets
   background and foreground as a pair. */
.ant-radio-wrapper, .ant-radio-group, .ant-radio, .ant-radio span,
.ant-checkbox-wrapper, .ant-checkbox, .ant-checkbox + span {
    background: transparent;
    color: var(--color-neutral-content); }
.ant-radio-inner, .ant-checkbox-inner {
    background: var(--color-neutral-surface);
    color: var(--color-neutral-content);
    border-color: var(--color-neutral-border-bold); }

/* The legacy Taxonomy, which the table task uses so a reviewer can name an analysis
   the parse missed. Not antd -- a CSS module -- but the same failure: its
   `.taxonomy__search` rule sets neither a background nor a colour, so the input takes
   the browser default white and the page's light text, and what you type is white on
   white. The add-a-label input hardcodes `color: #09f` on a 3%-black background, and
   the chips for what is already selected hardcode `hsl(0deg 0% 95%)`.

   Matched on the class prefix, never on `name=`. The class names are CSS-module output
   and carry a build hash (`taxonomy__search--qkTHD`), so only the readable prefix the
   hash is appended to can be relied on. The `name` attribute would be the more stable
   handle and is the obvious thing to reach for -- but Label Studio validates a config
   by regex-scanning it for `name="..."`, stylesheet included, and rejects the whole
   config with "contains non-unique names" when a CSS selector mentions one. The add
   input carries no class of its own, so it is reached through its container. */
[class*="taxonomy__search"], [class*="taxonomy__newitem"] input {
    background: var(--color-neutral-background);
    color: var(--color-neutral-content); }
[class*="taxonomy__search"]::placeholder, [class*="taxonomy__newitem"] input::placeholder {
    background: transparent;
    color: var(--color-neutral-content-subtler); }
[class*="taxonomy__selected"] div {
    background: var(--color-neutral-surface);
    color: var(--color-neutral-content); }
[class*="taxonomy__item"]:focus-within {
    background: var(--color-primary-background);
    color: var(--color-neutral-content); }
.ant-radio-checked .ant-radio-inner, .ant-checkbox-checked .ant-checkbox-inner {
    background: var(--color-primary-surface);
    color: var(--color-neutral-content);
    border-color: var(--color-primary-border); }
.ant-table, .ant-table-tbody td, .ant-table-placeholder {
    background: var(--color-neutral-surface) !important;
    color: var(--color-neutral-content) !important;
    border-color: var(--color-neutral-border) !important; }
.ant-table-thead th {
    background: var(--color-neutral-surface-hover) !important;
    color: var(--color-neutral-content) !important;
    border-color: var(--color-neutral-border) !important; }
.ant-collapse, .ant-collapse-item, .ant-collapse-header,
.ant-collapse-content, .ant-collapse-content-box {
    background: var(--color-neutral-surface) !important;
    color: var(--color-neutral-content) !important;
    border-color: var(--color-neutral-border) !important; }
.ns-half { flex: 1 1 50%;
           background: var(--color-neutral-surface);
           color: var(--color-neutral-content);
           border: 1px solid var(--color-neutral-border);
           padding: 8px 12px; border-radius: 4px; }

/* The rendered coordinate table. Every colour is a token pair because the
   reference implementation this is ported from was a fixed dark palette, and a
   hardcoded background here keeps the theme's own text colour and goes invisible
   in the other mode -- which the theming tests enforce.

   `td:first-child` below is a pseudo-class, not a child combinator, so it
   survives the Style sanitizer -- which escapes the greater-than character, and
   which is why that character cannot appear even in this comment.

   The box-shadow reads a --color-* token for a border colour and is exempt from
   the background/foreground pairing check only because that check cannot see
   "color" inside a custom property name. It is decoration on a row whose pair is
   already set two rules above; do not "fix" it by adding a bare color here. */
.ns-tbl-host { margin: 8px 0;
               background: transparent; color: var(--color-neutral-content); }
.ns-table  { max-height: 42vh; overflow: auto; border-radius: 4px;
             background: var(--color-neutral-surface);
             color: var(--color-neutral-content);
             border: 1px solid var(--color-neutral-border); }
.ns-tbl    { border-collapse: separate; border-spacing: 0; width: 100%;
             font-size: 12px;
             background: transparent; color: var(--color-neutral-content); }
.ns-tbl th { position: sticky; top: 0; z-index: 1; text-align: left;
             font-weight: 600; padding: 5px 8px; white-space: nowrap;
             background: var(--color-neutral-surface-hover);
             color: var(--color-neutral-content);
             border-bottom: 1px solid var(--color-neutral-border); }
.ns-tbl td { padding: 4px 8px; vertical-align: top;
             background: transparent;
             color: var(--color-neutral-content);
             border-bottom: 1px solid var(--color-neutral-border-subtle); }
.ns-tbl td.ns-num { text-align: right; font-variant-numeric: tabular-nums;
             background: transparent; color: var(--color-neutral-content); }
.ns-tbl td.ns-coord { font-weight: 600;
             background: transparent; color: var(--color-primary-content); }
.ns-tbl td.ns-gut { width: 2.2em; text-align: right; font-size: 10.5px;
             background: transparent;
             color: var(--color-neutral-content-subtler); }
/* A section row is structure, not a state: it says where the table divides, which
   is a different kind of fact from "this row is contested". Giving it the warning
   colour put it in the same vocabulary as the row states and left amber meaning
   two things at once. Weight and a rule carry it instead. */
.ns-tbl tr.ns-sec td { font-weight: 700; letter-spacing: 0.02em;
             background: var(--color-neutral-surface-hover);
             color: var(--color-neutral-content);
             border-top: 2px solid var(--color-neutral-border); }
.ns-tbl tr.ns-hit td { background: var(--color-positive-background);
             color: var(--color-neutral-content); }
.ns-tbl tr.ns-hit td:first-child {
             box-shadow: inset 3px 0 0 var(--color-positive-border);
             background: var(--color-positive-background);
             color: var(--color-neutral-content); }
/* Amber, not red. A contested row is an ambiguity the reviewer is being shown, not
   an error they made: two analyses report the same peak and the parser cannot say
   whose it is. Red read as "this row is wrong".
   Quieter than a marked row, too, and that ordering is the point. On this corpus a
   contrast can have 3 rows of its own against 22 contested, and filling all 22 with
   a saturated warning tint buried the answer under the caveat. The fill is neutral;
   the amber is carried by the rule and the mark. */
.ns-tbl tr.ns-maybe td { background: var(--color-neutral-surface-hover);
             color: var(--color-neutral-content); }
.ns-tbl tr.ns-maybe td:first-child {
             box-shadow: inset 3px 0 0 var(--color-warning-border);
             background: var(--color-neutral-surface-hover);
             color: var(--color-neutral-content); }
/* The gutter mark ties to its legend chip, so the two are read as one thing. */
.ns-tbl tr.ns-maybe td.ns-gut { font-weight: 700;
             background: var(--color-neutral-surface-hover);
             color: var(--color-warning-content); }
.ns-tbl tr.ns-hit td.ns-gut { font-weight: 700;
             background: var(--color-positive-background);
             color: var(--color-positive-content); }
.ns-tbl-cap { font-size: 12.5px; margin: 2px 0 6px;
             background: transparent;
             color: var(--color-neutral-content-subtler); }
.ns-tbl-note { font-size: 11.5px; margin-top: 5px;
             background: transparent;
             color: var(--color-neutral-content-subtler); }
.ns-tbl-key { font-size: 11.5px; margin: 0 0 5px;
             background: transparent;
             color: var(--color-neutral-content-subtler); }
.ns-key    { margin-right: 12px; white-space: nowrap;
             background: transparent;
             color: var(--color-neutral-content-subtler); }
.ns-key-mark { display: inline-block; min-width: 1.5em; text-align: center;
             margin-right: 4px; padding: 0 3px; border-radius: 3px;
             font-weight: 700;
             background: var(--color-neutral-surface-hover);
             color: var(--color-neutral-content); }
.ns-key-hit { background: var(--color-positive-background);
             color: var(--color-positive-content); }
.ns-key-maybe { background: var(--color-warning-background);
             color: var(--color-warning-content); }
.ns-key-own { background: var(--color-primary-background);
             color: var(--color-primary-content); }
.ns-wide { flex: 1 1 58%;
           background: transparent; color: var(--color-neutral-content); }
"""

#: One tint per analysis on a table task, as (background, foreground) token pairs.
#: Four, not six like _SET_COLOURS: Label Studio ships four chromatic semantic families
#: and a token-only palette cannot invent a fifth. Past four the tint cycles, and the
#: gutter column's sibling number -- not the colour -- is what tells the fifth analysis
#: from the first. The colour is a scanning aid; the number is the identity.
#: The `-background` family, not `-surface`: a `-surface` fill is saturated enough that
#: a coordinate printed on it is harder to read than the coordinate beside it, and a
#: table task tints every row. It also put the table task and the contrast task on two
#: different weights of the same idea, so the two screens did not look related.
#:
#: Three, and none of them red. Four chromatic families exist, but the fourth is
#: `negative`, and this stylesheet reserves red for nothing at all -- an analysis is not
#: an error for being the fourth in its table. Cycling at three is the same mechanism the
#: fourth would have used one analysis later, and the gutter number is the identity in
#: any case: the tint is only a scanning aid.
#: (fill, text, rule down the gutter edge). The rule is what makes a block of rows read
#: as one analysis: at this fill weight the tints are deliberately faint, and a run of
#: twelve faintly-tinted rows does not group by itself. It is the same device the
#: contrast view's marked rows already use.
_ANALYSIS_TINTS = [
    ("--color-primary-background", "--color-neutral-content", "--color-primary-border"),
    ("--color-positive-background", "--color-neutral-content", "--color-positive-border"),
    ("--color-warning-background", "--color-neutral-content", "--color-warning-border"),
]


def _analysis_tint_css(tints: list[tuple[str, str]] | None = None) -> str:
    """One rule per tint, generated rather than written out.

    Same device as `_chat_order_css`: the count has to agree with what
    `table_render.render_table_html` cycles through, so both read `_ANALYSIS_TINTS`
    instead of restating it.
    """

    rules = []
    for index, (background, foreground, border) in enumerate(tints or _ANALYSIS_TINTS):
        rules.append(
            f".ns-tbl tr.ns-a{index} td {{ background: var({background}); "
            f"color: var({foreground}); }}"
        )
        rules.append(
            f".ns-tbl tr.ns-a{index} td:first-child {{ "
            f"box-shadow: inset 3px 0 0 var({border}); "
            f"background: var({background}); color: var({foreground}); }}"
        )
    return "\n" + "\n".join(rules) + "\n"

# The Header tag supports `style` but NOT `className` (visual/Header.jsx documents
# value/size/style/underline only), so secondary text is styled inline. A
# className there is silently dropped and the text renders full-weight.
#
# Inline styles are parsed by Tree.cssConverter, which splits on ";" then on the
# first ":" -- a var() reference survives that intact, but a value containing a
# semicolon would not.
_META_STYLE = "color: var(--color-neutral-content-subtler); font-weight:400; margin:4px 0"

#: Pre-wrap keeps the one-line-per-evidence-set layout the excerpt builds.
_QUOTE_STYLE = "white-space:pre-wrap; font-weight:400; line-height:1.5; margin:0"

# -- the task-data contract ------------------------------------------------
#
# Every `$key` a config interpolates, per kind. This is the exporter's target:
# `to_labelstudio.py` must populate exactly these, and the tests assert that the
# generated XML interpolates nothing outside them. Keys marked GATE hold exactly
# one element and select which block of a shared config renders; keys marked ROWS
# hold one element per repeated form.

#: Present on every task. `paper_id` and `review_key` are not interpolated by any
#: config -- they exist for the Data Manager views that group by paper and for
#: duplicate detection -- so the contract is a superset of what the XML reads.
SHARED_KEYS = {
    "paper_id": "grouping key for the Data Manager view; not interpolated",
    "review_key": "stable ADDRESS of this judgement (paper|class|local_id|slot)",
    "content_hash": "digest of the answer-bearing payload; see staged-validation.md",
    "stage": "0 entities | 1 relationships | 1 values | 2 structure -- import round",
    "paper_url": "/data/local-files/?d=texts/<id>.txt -- the only place the text lives",
    "paper_title": "heading above the text pane",
    "paper_citation": "id, pmid, doi",
    "paper_text_hash": "sha256 of the text the offsets were computed against; not\n"
    "            interpolated. Without it a re-staged text is invisible to sync_tasks.py:\n"
    "            offsets are not in content_hash and the section breadcrumb moves with its\n"
    "            span, so `data` stays byte-identical, the sync takes its unchanged branch\n"
    "            and every stored prediction keeps addressing the old text",
}

#: Extra per-family keys the Data Manager filters on. Not interpolated either;
#: `setup_project.py` builds its views from these, so adding a triage axis means
#: adding it here and having the exporter populate it.
FILTER_KEYS: dict[str, dict[str, str]] = {
    "value": {
        "coordinate_status": "yes | unrelated -- whether a reported result rests on this",
        "entity_class": "e.g. Group -- the triage axis alongside priority",
        "local_id": "which instance of that class",
        "field_path": "the slot, e.g. age_mean or sex_distribution[1].count",
        "priority": "0-3, or n/a",
        "llm_status": "extracted | not_reported",
        "evidence_status": "present | not_found | not_applicable",
    },
    "relationship": {
        "coordinate_status": "yes | unrelated -- whether any row supports a reported result",
        "rel_slot": "e.g. Analysis.acquisitions",
        "target_class": "e.g. Acquisition",
        "anomaly_count": "how many record-integrity flags this slot raised, backing "
        "`anomaly_gate`. Not a triage axis: non-zero means the record is malformed, "
        "which is a bug for validate_record.py rather than a judgement for a reviewer",
    },
    "structure": {
        "task_kind": "entities | model -- which gate this task fills",
        "coordinate_status": "yes | no_table | no_contrast | no_coordinates | not_applicable"
        "  -- whether the object is tied to a reported result. An analysis with no table"
        "  reports nothing; a model with no analysis is taken from no result. The triage"
        "  axis for skipping both",
        "local_id": "the ModelEstimation or Analysis under review; empty for inventory",
        "cell_count": "rows in the contrast grid, or terms in the model",
    },
    "contrast": {
        "task_kind": "table | contrast -- which gate this task fills",
        "coordinate_status": "yes | no_table | no_contrast | no_coordinates | "
        "not_applicable -- whether the object is tied to a reported result",
        "local_id": "the Table under review, or the Analysis",
        "cell_count": "analyses parsed from the table, or rows in the contrast grid",
        "table_id": "the pubget table this task renders. The triage axis this project "
        "exists for: 'show me one table and everything drawn from it'",
    },
    "adjudication": {
        "dispute_field_count": "how many fields or cells the two reviewers differ on",
    },
}

DATA_CONTRACT: dict[str, dict[str, str]] = {
    "value": {
        **SHARED_KEYS,
        **FILTER_KEYS["value"],
        "field_label": "e.g. 'Group grp_mdd  ·  age_mean'",
        "field_description": "the slot's schema description, first sentence",
        "llm_value": "the extracted value, or 'not reported'",
        "llm_meta": "span count and section, so the highlight is findable",
        "span_labels": "dynamic Labels, one per evidence set; at least one even when "
        "the extractor found none, so a missed value can still be highlighted",
    },
    "relationship": {
        **SHARED_KEYS,
        **FILTER_KEYS["relationship"],
        "rel_label": "e.g. 'Analysis.acquisitions -> Acquisition (many)'",
        "rel_description": "the slot's schema description",
        "anomaly_gate": "GATE [{text}] -- present only when a hard anomaly exists "
        "(dangling reference, required row with no link)",
        "columns": "dynamic Choices: [{value: descriptor, alias: local_id}] -- matrix\n"
        "            columns. The alias is what the annotation stores, so it is the id;\n"
        "            the value is only ever read on screen",
        "rows_multi": "ROWS array of {label, meta} for multivalued slots",
        "rows_single": "ROWS array of {label, meta} for single-valued slots",
        "link_labels": "dynamic Labels, one per candidate target -- the same options the\n"
        "            checkboxes offer, and the same {value: descriptor, alias: local_id}\n"
        "            split, so a highlight records the id it supports",
    },
    "structure": {
        **SHARED_KEYS,
        **FILTER_KEYS["structure"],
        "structure_labels": "dynamic Labels: the objects of this task plus '+ new ...' slots",
        "entities": "GATE [{label, meta, guidance}] -- present only on a stage-0 task",
        "entity_table": "ROWS array of {local_id, descriptor, referenced_by} for the legend",
        "entity_rows": "ROWS array of {label, descriptor, referenced_by} -- one per instance",
        "model": "GATE [{label, meta, summary}] -- present only on a model task",
        "terms": "ROWS array of {heading, summary, levels: [{label}]}; evidence is not\n            quoted here, only highlighted in the paper pane",
    },
    "contrast": {
        **SHARED_KEYS,
        **FILTER_KEYS["contrast"],
        "table_html": "the rendered coordinate table, as an HTML string. ALWAYS a\n"
        "            string, never null and never a list: saving this config records the\n"
        "            key as a HyperText data type, after which Label Studio admits only\n"
        "            str for it -- on import AND on the PATCH sync_tasks.py issues",
        "structure_labels": "dynamic Labels: the analyses of this task plus '+ new analysis'",
        "table": "GATE [{label, meta, guidance}] -- present only on a table task",
        "sibling_rows": "ROWS array of {label, meta} -- one per parsed analysis of this\n"
        "            table, numbered to match the rendered table's gutter column",
        "contrast": "GATE [{label, parsed, paraphrase}] -- present only on a\n"
        "            contrast task; `parsed` names the sibling it was read off",
        "cell_rows": "ROWS array of {label} -- one per term, or per level of a factor",
        "statistic": "GATE [{summary}] -- present only when the record holds a\n"
        "            statistic to judge; empty when it holds none, so the block goes",
        "statistic_options": "dynamic Choices: the statistic verdicts whose subject\n"
        "            this record actually carries",
    },
    "adjudication": {
        **SHARED_KEYS,
        **FILTER_KEYS["adjudication"],
        "dispute_label": "what is disputed, e.g. 'Group - pd - age_mean'",
        "dispute_kind": "value | relationship | structure -- for the decoder",
        "left_md": "reviewer A's canonical form",
        "right_md": "reviewer B's canonical form",
        "diff_md": "markdown; only the fields/cells that differ",
    },
}


def _sub(parent: ElementTree.Element, tag: str, **attrs: str) -> ElementTree.Element:
    return ElementTree.SubElement(parent, tag, {k: v for k, v in attrs.items()})


def _ask(parent: ElementTree.Element, prompt: str) -> None:
    """The one required question of a task.

    Not marked "Required" in the text: the prompt reads better without it, and the
    requiredMessage quotes this exact wording, so a reviewer who does miss it is
    pointed straight back here.
    """

    _sub(parent, "Header", value=prompt, size="5")


def _meta(parent: ElementTree.Element, value: str) -> None:
    _sub(parent, "Header", value=value, size="5", style=_META_STYLE)


def _panel(parent: ElementTree.Element, css_class: str, value: str) -> None:
    """A markdown block inside a styled card.

    Markdown carries no `name` (tags/visual/Markdown.jsx has only an auto id), so
    repeated copies of this need no index flag. It is not a legal Panel child
    either, hence the wrapping View.
    """

    box = _sub(parent, "View", className=css_class)
    _sub(box, "Markdown", value=value)


def _quote(parent: ElementTree.Element, value: str) -> None:
    """The 《》-delimited excerpt block, rendered as pre-wrapped text."""

    box = _sub(parent, "View", className="ns-quote")
    _sub(box, "Header", value=value, size="5", style=_QUOTE_STYLE)


def _choices(
    parent: ElementTree.Element,
    name: str,
    vocabulary: list[tuple[str, str]],
    *,
    required: bool = False,
    inline: bool = False,
    required_message: str | None = None,
) -> ElementTree.Element:
    """A single-select control, optionally required.

    `required_message` must name the question it belongs to. On failure Label
    Studio calls `requiredModal()` and shows this string, and that is all it does
    -- there is no scroll-to and no highlight for a whole-object control
    (`mixins/Required.js:93-97`; only the perRegion branch calls `selectArea`).
    So a generic "Answer this before submitting" leaves the reviewer hunting a
    long form for whatever is blank.
    """

    attrs = {
        "name": name,
        "toName": "paper",
        "choice": "single-radio",
        "showInline": "true" if inline else "false",
    }
    if required:
        if not required_message:
            raise ValueError(f"required control {name!r} needs a requiredMessage naming it")
        attrs["required"] = "true"
        attrs["requiredMessage"] = required_message
    node = _sub(parent, "Choices", **attrs)
    for value, hint in vocabulary:
        _sub(node, "Choice", value=value, hint=hint)
    return node


def _choices_from(
    parent: ElementTree.Element, name: str, value: str, *, inline: bool = False
) -> ElementTree.Element:
    """A single-select whose options come from the task, like a dynamic label set.

    For a question whose *answers* depend on what the record holds rather than only
    its subject. The statistic block is the case: no analysis in the corpus records
    degrees of freedom, so a fixed `df_wrong` option asked every reviewer about a
    value that was never there. Offering it only when there is a df to be wrong about
    keeps every option answerable.

    `Choices` carries DynamicChildrenMixin and a `value` attribute for exactly this,
    so the option list is task data and the config declares no `<Choice>` of its own
    -- the same arrangement `_span_layer` uses for labels, and for the same reason:
    the exporter can then emit an option the config never had to anticipate.
    """

    return _sub(
        parent,
        "Choices",
        name=name,
        toName="paper",
        choice="single-radio",
        showInline="true" if inline else "false",
        value=value,
    )


def _textarea(
    parent: ElementTree.Element, name: str, placeholder: str, rows: int = 2
) -> None:
    _sub(
        parent,
        "TextArea",
        name=name,
        toName="paper",
        rows=str(rows),
        editable="true",
        maxSubmissions="1",
        placeholder=placeholder,
    )


def _gated(parent: ElementTree.Element, gate: str, index_flag: str) -> ElementTree.Element:
    """A block that renders only for tasks carrying `gate`.

    `on` resolves through parseValue, and a missing key yields "" which
    `|| []` turns into zero iterations (core/Tree.tsx:70). The gate array holds
    one element by contract, so `index_flag` always resolves to 0 -- which is what
    lets a `whenTagName` inside the block name a control declared in it.
    """

    return _sub(parent, "Repeater", on=f"${gate}", indexFlag=index_flag)


def _chat_pane(pane: ElementTree.Element) -> None:
    """The question box, and the log of answers it has produced.

    Two TextAreas rather than a chat widget, because a chat widget would need
    JavaScript in the labeling interface and that is an Enterprise feature
    (`docs.humansignal.com/guide/plugins`). What open source gives instead is the
    interactive-preannotation round trip, and these two controls are its ends:

      chat_q  the only `smart` control in any of these configs. Submitting one
              fires `regionFinishedDrawing`, which the Data Manager turns into
              POST /api/ml/<pk>/interactive-annotating carrying every textarea
              region on `paper` as context (`DataManager.jsx:157-191`).
      chat_a  written only by the backend's reply. `<Text>` has
              `supportSuggestions: false` (`tags/object/Base.js:23`), and a
              suggestion an object tag cannot display is accepted immediately
              rather than waiting for a click (`Annotation.js:1186`), so the
              answer lands in the annotation with no reviewer action -- which is
              the point: the exchange is recorded as part of the review, not
              alongside it.

    `maxSubmissions="0"` on the answer log hides its own input box
    (`TextArea.jsx:133-140`: `submissionsNum < 0` is never true) without blocking
    deserialization, which does not go through the submit path. The reviewer can
    still delete an entry; they cannot type one.

    ## Reading order

    A TextArea renders as one block -- its input, then the list of what has been
    submitted to it (`TextArea.jsx:398-503`) -- so declaration order alone gives
    answers, then the box, then the questions. The exchange has to read
    question, answer, question, answer, with the box where you type at the
    bottom, which means splitting `chat_q`'s input from `chat_q`'s history. The
    two wrappers below exist to be flattened with `display: contents`, which
    makes the form and the submitted-question list separate flex items of the
    same column so `order` can interleave them with the answers. Nothing else
    can reorder them: they are siblings inside a block this config does not emit.

    ## Collapsing

    `Collapse`/`Panel` is a visual tag -- it holds no `name`, produces no result,
    and so keeps the open/closed state out of the annotation, which a `Choices`
    gate with `visibleWhen` would not. `Panel` accepts `textarea` and `view` as
    children (`Collapse.jsx:38-75`), so the pane goes in whole.
    """

    box = _sub(pane, "View", className="ns-chat")
    # accordion because every Collapse in these configs is one; with a single
    # panel it changes nothing, and the rule is worth more than the exception.
    collapse = _sub(box, "Collapse", accordion="true")
    panel = _sub(collapse, "Panel", value="Ask about this paper", open="true")
    body = _sub(panel, "View", className="ns-chat-body")

    # Declared question-first so the DOM order matches the reading order for
    # anything that ignores `order` -- a screen reader, or a browser without
    # `display: contents`. The CSS then only has to move the typing bar down.
    asked = _sub(body, "View", className="ns-chat-q")
    _sub(
        asked,
        "TextArea",
        name=CHAT_QUESTION,
        toName="paper",
        rows="2",
        editable="true",
        smart="true",
        showSubmitButton="false",
        placeholder="Ask a question, then Shift+Enter. Saved with your review.",
    )
    answered = _sub(body, "View", className="ns-chat-a")
    _sub(
        answered,
        "TextArea",
        name=CHAT_ANSWER,
        toName="paper",
        rows="6",
        maxSubmissions="0",
        placeholder="Answers appear here, oldest first.",
    )


def _paper_pane(row: ElementTree.Element) -> None:
    pane = _sub(row, "View", className="ns-paper")
    _sub(pane, "Header", value="$paper_title", size="5")
    _meta(pane, "$paper_citation")
    body = _sub(pane, "View", className="ns-paper-body")
    _sub(
        body,
        "Text",
        name="paper",
        value="$paper_url",
        valueType="url",
        saveTextResult="yes",
        granularity="symbol",
    )
    _chat_pane(pane)


def _span_layer(
    form: ElementTree.Element,
    labels_name: str,
    labels_value: str,
    prompt: str,
) -> None:
    """Dynamic labels over the paper text.

    The label set comes from task data, so the structure under review *is* the
    label set: one label per field, row or object. That is what makes a warrant
    visible in the text instead of described beside it, and it removes the old
    ceiling where the exporter could emit a label the config had not declared.

    Alt+. cycles the highlights (region:cycle -> selectNext -> scrollIntoView),
    which is the way to reach a span near the end of a 25-60 KB document.
    """

    _sub(form, "Header", value=prompt, size="5")
    _sub(form, "Labels", name=labels_name, toName="paper", value=labels_value, showInline="false")
    # Direct vs inferred is carried by the LABEL, not a per-region control. A
    # perRegion control reads `annotation.highlightedNode` (mixins/PerRegion.js:29)
    # and `perRegionVisible()` returns false when nothing is selected, so it stays
    # hidden until a span is clicked -- drawing one is not enough. The judgement is
    # made while drawing, so it belongs on the label drawn with: one click, while
    # the passage is already in view.
    return


def _naming_span_layer(
    form: ElementTree.Element,
    name: str,
    value: str,
    prompt: str,
) -> None:
    """A span layer whose label set the reviewer can extend while annotating.

    `<Labels>` can only offer what the exporter put in the task, which is fine when the
    label set is the structure under review and fatally limiting when the point is to
    report something the structure *lacks*. The table task carried a single
    `+ new analysis` pseudo-label for that, and it could not represent two missed
    analyses at all: both spans came back wearing the same label, indistinguishable.

    A Taxonomy in labeling mode draws regions exactly as Labels does -- `isLabeling`
    results attach to an area, so the region still serialises with `start`, `end` and
    `text`, and `reanchor_spans`, `prune_orphan_answers` and `offsets_hold` go on
    working unchanged. What it adds is `userLabels`: the reviewer types the name of the
    analysis they found, as many times as the paper needs, and the name they typed is
    what lands in the result.

    `legacy="true"` is what exposes the add control (the tag's own docs say so), and it
    costs only `apiUrl`, which nothing here uses. `value=` still supplies the parsed
    analyses through DynamicChildrenMixin, so the known names remain one click.
    """

    _sub(form, "Header", value=prompt, size="5")
    _sub(
        form,
        "Taxonomy",
        name=name,
        toName="paper",
        value=value,
        labeling="true",
        legacy="true",
        leafsOnly="true",
        showFullPath="false",
        placeholder="Pick an analysis, or type a name to add one",
    )


def _comment(form: ElementTree.Element) -> None:
    _sub(form, "Header", value="Notes", size="5")
    _textarea(form, "comment", "")


# -- value -----------------------------------------------------------------


def _value_form(form: ElementTree.Element) -> None:
    """One field of one entity.

    Per field rather than per entity: an entity task bundled 13-25 judgements
    behind a single verdict, so a reviewer either accepted all of them at once or
    opened a long form, and the answer needed an index path to address. One field
    is one decision.

    Evidence is never quoted. If the extractor found any it is already highlighted
    in the paper pane under a `set N` label, and a field it called not_reported
    still gets a label so the reviewer can highlight the passage that proves
    otherwise.
    """

    _sub(form, "Header", value="$field_label", size="4")
    _meta(form, "$field_description")

    box = _sub(form, "View", className="ns-llm")
    _sub(box, "Header", value="$llm_value", size="5")
    _meta(box, "$llm_meta")

    _ask(form, "Is this right?")
    _choices(
        form,
        "verdict",
        _FIELD_VERDICTS,
        required=True,
        required_message="Answer 'Is this right?'",
    )

    editor = _sub(
        form,
        "View",
        visibleWhen="choice-unselected",
        whenTagName="verdict",
        whenChoiceValue="correct",
    )
    _textarea(editor, "corrected_value", "corrected value")

    _span_layer(form, "ev", "$span_labels", "Where this value appears")


# -- relationship ----------------------------------------------------------


def _relationship_form(form: ElementTree.Element) -> None:
    """One association slot, as a grid over the paper's candidate targets.

    Order matters here and got it wrong once: the slot name came first, then the
    anomalies, then the candidate legend, and the source object being judged
    appeared fourth -- so a reviewer read three blocks before learning what the
    question was about. The subject and its checkboxes now come first, and
    everything else is reference material below them.

    Rows are the source objects and columns the candidates, so the assignment is
    judged as a whole: an unused target is an empty column, and two rows that
    disagree about a shared target are one glance apart.

    Two row Repeaters, gated on which one the task supplies, because `choice` is
    fixed in the config and a multivalued slot needs `multiple` while a
    single-valued one needs `single`.
    """

    _sub(form, "Header", value="$rel_label", size="4")

    multi = _sub(form, "Repeater", on="$rows_multi", indexFlag="{{mdx}}")
    row = _sub(multi, "View", className="ns-card")
    # The row's own name reads as the heading; its local_id sits underneath in
    # small text. `local_id -- name . fact` as a heading led with the token a
    # reviewer cares least about, and the id is still needed to match the
    # highlight labels, so it stays -- just demoted.
    _sub(row, "Header", value="$rows_multi[{{mdx}}].label", size="4")
    _meta(row, "$rows_multi[{{mdx}}].meta")
    _sub(
        row,
        "Choices",
        name="lm_{{mdx}}",
        toName="paper",
        choice="multiple",
        layout="inline",
        value="$columns",
    )

    single = _sub(form, "Repeater", on="$rows_single", indexFlag="{{sdx}}")
    row = _sub(single, "View", className="ns-card")
    _sub(row, "Header", value="$rows_single[{{sdx}}].label", size="4")
    _meta(row, "$rows_single[{{sdx}}].meta")
    # `none` is a column the exporter appends, so "no link here" is an assertion
    # rather than an unanswered row.
    _sub(
        row,
        "Choices",
        name="ls_{{sdx}}",
        toName="paper",
        choice="single-radio",
        layout="select",
        value="$columns",
    )

    # Only hard anomalies, and only when there are any. This used to be an
    # unconditional panel whose commonest line was "`x` is used by no Task" for
    # every unticked column -- a restatement of the grid directly above it, and
    # one whose "used by" meant something different from the legend's `used_by`
    # (this slot vs. the whole record). What is left is the class of finding the
    # grid genuinely cannot show: a link to an id that was never extracted, or a
    # required row with nothing in it.
    warn = _gated(form, "anomaly_gate", "{{adx}}")
    _panel(warn, "ns-warn", "$anomaly_gate[{{adx}}].text")

    # No verdict control, unlike every other family. The grid IS the answer: a
    # submitted task asserts the ticks as they stand, so "are these links right?"
    # only asked the reviewer to restate what they had just set. The two verdicts
    # that said something the grid could not -- a target missing, a target that
    # should not exist -- were claims about which objects exist, and that is the
    # entity inventory's question in stage 0, not this one's.
    #
    # This leaves the family with no required control. That is deliberate: the
    # ticks arrive pre-filled from the extraction, so submitting unchanged is a
    # meaningful act of confirmation rather than an empty answer.

    # Labelled by target, not by row: the checkbox options are what the reviewer
    # is being asked to place in the text, so those are the chips. The legend
    # table that used to sit here carried the same descriptors a second time.
    _span_layer(form, "rel", "$link_labels", "Where these links are stated")


# -- structure -------------------------------------------------------------


def _inventory_block(form: ElementTree.Element) -> None:
    """Stage 0: does this class's extracted instance set match the paper?

    Generic over classes -- Groups, Acquisitions, Conditions, ModelEstimations,
    Analyses -- because "did the extractor find the right set of these?" is one
    question with one shape. It used to be analysis-specific, which left the
    commonest cascade with no home at all: an invented Group is referenced by
    `AnalysisGroup.group` and `FactorLevel.groups`, and no value or relationship
    task can say the Group should not exist, only judge fields and links of one
    that does.

    This is stage 0 because every other family addresses objects by `local_id`.
    A wrong instance set is the one correction that can invalidate work
    everywhere else, so it is asked first and cheaply: one task per class per
    paper, ~8-10 per paper against ~300 for the value family.

    Each row carries a descriptor, not just an id, so "is this a real cohort?"
    is answerable without opening another task.
    """

    block = _gated(form, "entities", "{{vdx}}")
    _sub(block, "Header", value="$entities[{{vdx}}].label", size="4")
    _meta(block, "$entities[{{vdx}}].meta")
    _panel(block, "ns-card", "$entities[{{vdx}}].guidance")
    _sub(block, "Table", name="entity_inventory_{{vdx}}", value="$entity_table")

    _ask(block, "Is this the right set of instances?")
    _choices(
        block,
        "inv_verdict_{{vdx}}",
        _INVENTORY_VERDICTS,
        required=True,
        required_message="Answer 'Is this the right set of instances?'",
    )

    # Sits directly under the verdict, so the question and the tool for answering
    # it are together. One layer at the foot of the form put them an editor apart.
    _span_layer(
        block,
        "st_e_{{vdx}}",
        "$structure_labels",
        "Where each instance appears",
    )
    editor = _sub(
        block,
        "View",
        visibleWhen="choice-unselected",
        whenTagName="inv_verdict_{{vdx}}",
        whenChoiceValue="inventory_correct",
    )
    _sub(
        editor,
        "Header",
        value="Disposition",
        size="5",
    )
    rows = _sub(editor, "Repeater", on="$entity_rows", indexFlag="{{rdx}}")
    row = _sub(rows, "View", className="ns-cell")
    _sub(row, "Header", value="$entity_rows[{{rdx}}].label", size="5")
    _meta(row, "$entity_rows[{{rdx}}].descriptor")
    _meta(row, "$entity_rows[{{rdx}}].referenced_by")
    _choices(row, "inv_row_{{vdx}}_{{rdx}}", _INSTANCE_DISPOSITIONS, inline=True)
    _textarea(
        row,
        "inv_note_{{vdx}}_{{rdx}}",
        "note",
        rows=1,
    )
    _sub(editor, "Header", value="Missing instances", size="5")
    _textarea(editor, "new_instances_{{vdx}}", "One per line.", rows=3)


def _model_block(form: ElementTree.Element) -> None:
    """One ModelEstimation's terms, reviewed once however many contrasts use them.

    On the measured records a model serves up to four analyses, so reviewing the
    term list per analysis would review it four times.
    """

    block = _gated(form, "model", "{{gdx}}")
    _sub(block, "Header", value="$model[{{gdx}}].label", size="4")
    _meta(block, "$model[{{gdx}}].meta")
    _panel(block, "ns-card", "$model[{{gdx}}].summary")

    _ask(block, "Is this the right term list?")
    _choices(
        block,
        "model_verdict_{{gdx}}",
        _MODEL_VERDICTS,
        required=True,
        required_message="Answer 'Is this the right term list?'",
    )

    # Sits directly under the verdict, so the question and the tool for answering
    # it are together. One layer at the foot of the form put them an editor apart.
    _span_layer(
        block,
        "st_m_{{gdx}}",
        "$structure_labels",
        "Where each term appears",
    )
    # The term list is NOT gated on the verdict. Answering `terms_correct` used to
    # hide the very thing being called correct, and unlike a contrast -- whose cells
    # are restated in its paraphrase -- a model's terms appear nowhere else on the
    # task. They stay visible; the accordion keeps each to one line until opened.
    editor = block
    # Paginated so a 16-term model builds one form at a time rather than sixteen.
    # It sits in a View (Repeater wraps each iteration in one), never in a Panel,
    # because Panel's children union admits `view` but not `pagedview`.
    # Neither paginated nor collapsed, and both for the same kind of reason: the
    # two wrapping mechanisms Label Studio offers assume things this layout does
    # not provide. See the note in _value_form for PagedView's NaN page, and
    # antd's `.ant-collapse-content` pairs a hardcoded dark foreground with a
    # transparent background, so a Collapse's contents vanish on a dark page.
    # Both blocks are already gated behind a verdict, so the full list only
    # renders once a reviewer says something is wrong.
    # One accordion panel per term, collapsed, so a 16-term model reads as a list
    # of headings and only the one being worked on is open. This is not
    # `mode="pagination"`: PagedView derives its page from the object tag's name
    # when a region is selected and a shared `<Text name="paper">` makes that NaN
    # (see _value_form). Collapse needs no page state at all -- what it needed was
    # the `.ant-collapse*` colour overrides in the Style block, since antd pairs a
    # hardcoded dark foreground with a transparent background there.
    _sub(editor, "Header", value="Missing terms", size="5")
    _textarea(
        editor,
        "new_terms_{{gdx}}",
        "One per line.",
        rows=3,
    )
    terms = _sub(editor, "Repeater", on="$terms", indexFlag="{{tdx}}")
    collapse = _sub(terms, "Collapse", accordion="true")
    panel = _sub(collapse, "Panel", value="$terms[{{tdx}}].heading")
    # The heading is the term's name and nothing else. It used to repeat the name
    # and type in a second panel below, while the type and scope radios beneath
    # that carry the same two values a third time -- so the card said "left
    # amygdala time series, continuous" three ways. `summary` now holds only the
    # facts no control on this card represents: the source's own definition, the
    # unit, and how many levels the factor declares.
    _meta(panel, "$terms[{{tdx}}].summary")

    # No excerpt block. The term's evidence is in the paper pane, labelled
    # `term: <name>` so it is clear which highlight belongs to which card, and
    # Alt+. cycles through them. Quoting it here as well duplicated the same
    # sentence in two places and invited the reading that the box and the
    # highlights disagreed -- the box showed one term's `name` evidence while the
    # pane shows every term's.

    _choices(panel, "tv_{{gdx}}_{{tdx}}", _TERM_VERDICTS)
    _textarea(panel, "tname_{{gdx}}_{{tdx}}", "corrected name", rows=1)
    _sub(panel, "Header", value="Type", size="5")
    kind = _sub(
        panel,
        "Choices",
        name="ttype_{{gdx}}_{{tdx}}",
        toName="paper",
        choice="single-radio",
        layout="inline",
    )
    for value, hint in (
        ("categorical", "Declares levels"),
        ("continuous", "A slope or product column; declares no levels"),
    ):
        _sub(kind, "Choice", value=value, hint=hint)

    _sub(panel, "Header", value="Scope", size="5")
    scope = _sub(
        panel,
        "Choices",
        name="tvar_{{gdx}}_{{tdx}}",
        toName="paper",
        choice="single-radio",
        layout="inline",
    )
    for value, hint in (
        ("within_subject", "Moves within a participant, like a repeated-measures factor"),
        ("between_subject", "Moves only across the sample, like a diagnosis or an age covariate"),
        ("both", "Moves both within and across participants"),
        ("unstated", "The paper does not say"),
    ):
        _sub(scope, "Choice", value=value, hint=hint)

    levels = _sub(panel, "Repeater", on="$terms[{{tdx}}].levels", indexFlag="{{ldx}}")
    level = _sub(levels, "View", className="ns-cell")
    _sub(level, "Header", value="$terms[{{tdx}}].levels[{{ldx}}].label", size="5")
    _textarea(level, "lv_{{gdx}}_{{tdx}}_{{ldx}}", "corrected level", rows=1)
    # Number takes no `hint` (tags/control/Number.jsx documents min/max/step/
    # hotkey/required/perRegion/slider only), so an unlabelled input would give
    # the reviewer no idea what it is for. The Header is the label.
    _sub(level, "Header", value="Order", size="5")
    _sub(
        level,
        "Number",
        name="lo_{{gdx}}_{{tdx}}_{{ldx}}",
        toName="paper",
        min="1",
    )


def _table_block(form: ElementTree.Element) -> None:
    """Stage 0 over one coordinate table: is this the right set of analyses?

    The judgement nothing else in the pipeline can make. Stage 1 splits each table into
    analyses with one model call, and that split is where over-splitting, merging, missed
    analyses and misattributed rows happen -- but every downstream task addresses an
    analysis that already exists, so none of them can say the set is wrong. The entity
    inventory says it at the paper level; only this says it against the grid the split
    was read off.

    Stage 0 for the same reason the entity inventory is: `over_split` invalidates the
    contrast, model, value and relationship tasks of every analysis drawn from the table,
    so it is asked first and cheaply.

    The rendered table sits above, outside both gates -- see `_contrast_form`. Each
    parsed analysis is numbered, and the number is the gutter column's, so the list below
    and the grid above are one artifact rather than two that have to be matched by eye.
    """

    block = _gated(form, "table", "{{tdx}}")
    _sub(block, "Header", value="$table[{{tdx}}].label", size="4")
    _meta(block, "$table[{{tdx}}].meta")
    _panel(block, "ns-card", "$table[{{tdx}}].guidance")

    _ask(block, "Is this the right set of analyses?")
    _choices(
        block,
        "table_verdict_{{tdx}}",
        _TABLE_VERDICTS,
        required=True,
        required_message="Answer 'Is this the right set of analyses?'",
    )
    _naming_span_layer(
        block,
        "st_t_{{tdx}}",
        "$structure_labels",
        "Where each analysis is described",
    )
    editor = _sub(
        block,
        "View",
        visibleWhen="choice-unselected",
        whenTagName="table_verdict_{{tdx}}",
        whenChoiceValue="segmentation_correct",
    )
    _sub(editor, "Header", value="Disposition", size="5")
    rows = _sub(editor, "Repeater", on="$sibling_rows", indexFlag="{{sdx}}")
    row = _sub(rows, "View", className="ns-cell")
    _sub(row, "Header", value="$sibling_rows[{{sdx}}].label", size="5")
    _meta(row, "$sibling_rows[{{sdx}}].meta")
    _choices(row, "tbl_row_{{tdx}}_{{sdx}}", _ANALYSIS_DISPOSITIONS, inline=True)
    _textarea(row, "tbl_note_{{tdx}}_{{sdx}}", "note", rows=1)

    # No per-row question here. Restating a 77-row grid as 77 radio groups underneath it
    # asks the reviewer to hold two views in their head and match them up by coordinate,
    # which is more work than the correction is worth -- and the answer it collects is
    # one the grid above already displays. A row-level correction is worth having only
    # when it can be made *on* the grid, which needs a control Label Studio does not
    # offer over a HyperText. Until then `rows_wrong` on the analysis, plus the note
    # beside it, is where a wrong attribution is reported.

    # No "missing analyses" textarea either. A missed analysis is reported by drawing
    # its span above and typing the name, which is the better record because it carries
    # the warrant with it; asking for the name a second time in prose is double entry
    # that can only disagree with itself.
    #
    # The case that argued for keeping it -- a table section the prose never mentions,
    # and so nothing to point at -- no longer exists: the paper text is rebuilt with the
    # coordinate tables inlined, so a table's caption, section headings and rows are all
    # in the pane and can be spanned like any other sentence.


def _contrast_block(form: ElementTree.Element) -> None:
    """One Analysis's Effect, as a paraphrase and a direction grid.

    The paraphrase is the primary judgement: the record rendered back into one
    sentence, beside the paper's own definition. Accepting is one click, which is
    what makes 5-9 analyses per paper affordable.

    The grid's rows are the model's term-and-level inventory, and every row
    carries `absent` as a third option. That makes absence an assertion -- a term
    adjusted for rather than tested -- where an object list leaves it
    indistinguishable from oversight, and it means a cell can only ever name a
    term the model declares.
    """

    block = _gated(form, "contrast", "{{cdx}}")
    _sub(block, "Header", value="$contrast[{{cdx}}].label", size="4")
    # Which parsed analysis this record was read off, and how many of the table's rows
    # report it. An unmatched record says so here rather than rendering an uncoloured
    # table that looks like an analysis with no results.
    _meta(block, "$contrast[{{cdx}}].parsed")
    _sub(block, "Header", value="The record says", size="5", style=_META_STYLE)
    _panel(block, "ns-para", "$contrast[{{cdx}}].paraphrase")

    # No "The paper says" panel. The evidence is already pre-highlighted in the paper
    # pane under the `definition` label, and Alt+. cycles to it -- so quoting it here
    # printed the same passage twice, once out of context. Read in place: the sentences
    # either side are what settle whether it supports the record, and an excerpt hides
    # exactly those. When the record's definition has no evidence at all, the meta line
    # above says so rather than leaving the reviewer hunting for a highlight that was
    # never drawn.

    _ask(block, "Does the record say what the paper says?")
    _choices(
        block,
        "contrast_verdict_{{cdx}}",
        _CONTRAST_VERDICTS,
        required=True,
        required_message="Answer 'Does the record say what the paper says?'",
    )

    # Sits directly under the verdict, so the question and the tool for answering
    # it are together. One layer at the foot of the form put them an editor apart.
    _span_layer(
        block,
        "st_c_{{cdx}}",
        "$structure_labels",
        "Where this contrast is reported",
    )
    editor = _sub(
        block,
        "View",
        visibleWhen="choice-unselected",
        whenTagName="contrast_verdict_{{cdx}}",
        whenChoiceValue="accept",
    )
    _sub(
        editor,
        "Header",
        value="Direction per term",
        size="5",
    )
    rows = _sub(editor, "Repeater", on="$cell_rows", indexFlag="{{rdx}}")
    row = _sub(rows, "View", className="ns-cell")
    _sub(row, "Header", value="$cell_rows[{{rdx}}].label", size="5")
    grid = _sub(
        row, "Choices", name="cell_{{cdx}}_{{rdx}}", toName="paper", choice="single-radio", layout="inline"
    )
    for value, hint in (
        ("positive", "Weighted positively, or a positive fitted coefficient"),
        ("negative", "Weighted negatively, or the lesser side of the comparison"),
        ("absent", "Not in this contrast -- adjusted for, or a zero weight"),
    ):
        _sub(grid, "Choice", value=value, hint=hint)
    _textarea(
        row,
        "cell_label_{{cdx}}_{{rdx}}",
        "source wording",
        rows=1,
    )

    # Gated, because an analysis whose record holds no statistic at all has nothing
    # here to judge, and a radio group over an empty summary reads as a question the
    # reviewer failed to answer rather than one that never applied.
    stat = _sub(editor, "Repeater", on="$statistic", indexFlag="{{qdx}}")
    _panel(stat, "ns-card", "$statistic[{{qdx}}].summary")
    _choices_from(stat, "stat_verdict_{{cdx}}_{{qdx}}", "$statistic_options", inline=True)
    # Only once something has been called wrong, and it says what to write rather than
    # naming the field again. "corrected statistic" followed the house pattern of
    # "corrected value" and "corrected name", but those name one thing each -- a
    # statistic is a family and two degrees of freedom, so which of them the box wanted
    # depended on a verdict the box did not mention.
    # `choice-selected` over the wrong-verdicts rather than `choice-unselected` over
    # the right one: the latter is true before anything is answered, so the box would
    # still greet the reviewer empty and unexplained, which is the complaint.
    # `whenChoiceValue` is split on commas (`mixins/Visibility.js:57`).
    fix = _sub(
        stat,
        "View",
        visibleWhen="choice-selected",
        whenTagName="stat_verdict_{{cdx}}_{{qdx}}",
        whenChoiceValue=",".join(v for v in _STATISTIC_VERDICTS if v != "statistic_correct"),
    )
    _textarea(
        fix,
        "stat_note_{{cdx}}_{{qdx}}",
        "what the paper reports, e.g. F(1,57) or t(33)",
        rows=1,
    )


def _structure_form(form: ElementTree.Element) -> None:
    """Three task kinds in one config, selected by which gate key the task carries.

    They share a project because they share an overlap policy (two reviewers,
    since this is where disagreement is informative) and because import order
    walks a reviewer through one paper's inventory, then its models, then its
    contrasts in a single sitting.
    """

    _inventory_block(form)
    _model_block(form)


# -- contrast --------------------------------------------------------------


def _contrast_form(form: ElementTree.Element) -> None:
    """Two task kinds over one rendered coordinate table.

    The table is declared once, outside both gates. Two reasons, and the second is not
    optional. Both kinds want it, so gating it would mean rendering it twice; and Label
    Studio records `table_html` as a HyperText data type the moment this config is saved,
    after which `tasks/validation.py` admits only a string for that key -- on import and
    on the PATCH `sync_tasks.py` issues. A key that exists on only one gate's tasks would
    fail the other's, so it has to be on every task in the project.

    `inline="true"` is load-bearing rather than cosmetic: without it the value renders
    into an iframe with its own document, which the Style block above cannot reach, and
    the table would come back unstyled with nothing to say why.

    Nothing points at it with a `toName`, so it draws no regions: it is evidence to read
    beside the paper, and the paper pane stays the only place a span is drawn.
    """

    host = _sub(form, "View", className="ns-tbl-host")
    _sub(
        host,
        "HyperText",
        name="table_html",
        value="$table_html",
        inline="true",
        selectionEnabled="false",
        clickableLinks="false",
    )
    _table_block(form)
    _contrast_block(form)


# -- adjudication ----------------------------------------------------------


def _adjudication_form(form: ElementTree.Element) -> None:
    """Two canonical forms and a resolution.

    Canonical, not raw: agreement on a structural task has to be computed on the
    sorted cell set and the term set, or control ordering reads as disagreement.
    The adjudicator sees the two readings, not two result blobs.
    """

    _sub(form, "Header", value="$dispute_label", size="4")
    _meta(form, "$dispute_kind")

    _panel(form, "ns-warn", "$diff_md")

    side = _sub(form, "View", className="ns-side")
    left = _sub(side, "View", className="ns-half")
    _sub(left, "Header", value="Reviewer A", size="5", style=_META_STYLE)
    _sub(left, "Markdown", value="$left_md")
    right = _sub(side, "View", className="ns-half")
    _sub(right, "Header", value="Reviewer B", size="5", style=_META_STYLE)
    _sub(right, "Markdown", value="$right_md")

    _ask(form, "Which reading is right?")
    _choices(
        form,
        "resolution",
        _RESOLUTIONS,
        required=True,
        required_message="Answer 'Which reading is right?'",
    )
    editor = _sub(
        form,
        "View",
        visibleWhen="choice-selected",
        whenTagName="resolution",
        whenChoiceValue="synthesize,escalate",
    )
    _textarea(editor, "resolved_value", "Corrected form", rows=4)


_FORMS = {
    "value": _value_form,
    "relationship": _relationship_form,
    "structure": _structure_form,
    "contrast": _contrast_form,
    "adjudication": _adjudication_form,
}


#: How many question/answer pairs the chat pane interleaves by position. CSS has
#: no arithmetic on `order`, so a pair costs two written-out rules and the count
#: has to be fixed at generation time. Twenty-five is well past what a reviewer
#: asks about one field -- the busiest task in the first real run had four -- and
#: anything beyond it still renders, in question-block then answer-block order,
#: below the paired entries and above the typing bar.
CHAT_TURNS = 25


def _chat_order_css(turns: int = CHAT_TURNS) -> str:
    """`order` for each entry, so question N sits directly above answer N.

    The two lists are separate controls and their entries only become orderable
    siblings once the containers are flattened (see the .ns-chat-body rules in
    _STYLE). Position is the only thing tying a question to its answer -- the
    payload carries no ids linking them, which is the same positional pairing
    `chat_backend.align` depends on, and it holds for the same reason: one answer
    per question, appended in order.
    """

    lines = ["", "/* generated: question N above answer N, for CHAT_TURNS turns */"]
    for turn in range(1, turns + 1):
        lines.append(f".ns-chat-q .lsf-row:nth-of-type({turn}) {{ order: {2 * turn - 1}; }}")
        lines.append(f".ns-chat-a .lsf-row:nth-of-type({turn}) {{ order: {2 * turn}; }}")
    return "\n".join(lines) + "\n"


def _mute_smart_controls(root: ElementTree.Element) -> None:
    """Turn off `smart` on every control that has not asked for it.

    `smart` defaults to **true** on every control tag (`tags/control/Base.js:16`)
    and `smartEnabled` is `smart && store.autoAnnotation` (`:62-66`). With
    Auto-Annotation on -- which the chat requires -- any region whose results
    include a smart control fires `regionFinishedDrawing` (`mixins/Regions.js:266`)
    and spends an interactive call. That is not only the `comment` box: a span
    drawn on the paper notifies too (`RichText/model.js:427`), and so does
    deleting one (`Regions.js:151`). Highlighting evidence is the most frequent
    thing a reviewer does here, so leaving the span layer smart would mean an LLM
    call per highlight, each arriving with no question to answer.

    Done as a sweep over the finished tree rather than an argument on each helper
    because the failure is silent and the cost is per click: a control added later
    without the attribute is muted by default, and turning it on has to be
    deliberate. `toName` is what identifies a control -- an object tag carries
    `value`, a visual tag carries neither -- so this needs no list of tag names to
    keep in step.
    """

    for node in root.iter():
        if node.get("toName") and "smart" not in node.attrib:
            node.set("smart", "false")


def build_config(kind: str, max_sets: int = DEFAULT_MIN_SETS) -> str:
    """Render one project's labeling config.

    `max_sets` is accepted for signature compatibility with the exporter, which
    imports DEFAULT_MIN_SETS from here. The configs no longer declare evidence-set
    labels statically -- they come from task data -- so it does not affect the XML.
    """

    if kind not in _FORMS:
        raise ValueError(f"unknown config kind {kind!r}; expected one of {KINDS}")

    root = ElementTree.Element("View")
    style = _sub(root, "Style")
    style.text = _STYLE + _analysis_tint_css() + _chat_order_css()

    row = _sub(root, "View", className="ns-row")
    _paper_pane(row)
    form = _sub(row, "View", className="ns-form")

    _FORMS[kind](form)
    _comment(form)
    _mute_smart_controls(root)

    ElementTree.indent(root, space="  ")
    return ElementTree.tostring(root, encoding="unicode") + "\n"


#: How many identifying fields a descriptor carries beyond the name. Four keeps a
#: `Group` descriptor to about one line while still distinguishing two cohorts.
DESCRIPTOR_FIELDS = 4


def descriptor(local_id: str, name: str | None, facts: list[str]) -> str:
    """Render a reference as something a reviewer can recognise.

    A bare `local_id` is unreviewable: "does this analysis use the right group?"
    cannot be answered from `grp_1`. Every place a reference appears -- an
    inventory row, a matrix column, a candidate legend, a contrast paraphrase --
    shows `local_id -- name . fact . fact` instead.

    Two rules matter. It is **derived at export time and never stored**, or it
    becomes a second copy of the entity that drifts from the first. And it is
    built from the target class's priority-0 scalars in
    `storage-parameter-priorities.yaml`, which is already the project's answer to
    "what matters about this object" -- so the descriptor tracks the priority file
    rather than being a hand-kept list that goes stale.
    """

    head = f"{local_id} -- {name}" if name else local_id
    kept = [fact for fact in facts if fact][:DESCRIPTOR_FIELDS]
    return f"{head} . {' . '.join(kept)}" if kept else head


#: Per gated config, the task kinds it gates on mapped to the required verdict control
#: each declares. Expansion suffixes the gate index, so a task of kind `model`
#: instantiates exactly `model_verdict_0`. The decoder reads the same map to know which
#: answer it is looking at, and `setup_project.py` reads it to build one Data Manager
#: view per kind.
#:
#: Keyed by config kind rather than flat, because more than one config is gated. A
#: second flat constant alongside this one would be a second list to keep in step, which
#: is the failure mode this file is written against.
GATES: dict[str, dict[str, str]] = {
    "structure": {
        "entities": "inv_verdict",
        "model": "model_verdict",
    },
    "contrast": {
        "table": "table_verdict",
        "contrast": "contrast_verdict",
    },
}


def sample_task(kind: str, size: int = 2, gate: str | None = None) -> dict[str, object]:
    """A minimal task of this kind, populating every contracted key.

    Two jobs. It is the exporter's worked example -- the shape
    `to_labelstudio.py` has to produce -- and it is what lets the tests expand
    the Repeaters and check the form a reviewer actually gets, which Label
    Studio's own validation cannot do because it only sees the unexpanded config.

    `size` is how many repeated rows to build: 2 is enough for two iterations to
    collide on a name if the config gets that wrong.

    `gate` picks the task kind for a gated config, defaulting to that config's first
    gate. It replaces a `structure_kind` argument that named one config in its own name.
    """

    if kind not in DATA_CONTRACT:
        raise ValueError(f"unknown config kind {kind!r}; expected one of {KINDS}")
    if kind in GATES:
        gate = gate or next(iter(GATES[kind]))
        if gate not in GATES[kind]:
            raise ValueError(f"unknown {kind} gate {gate!r}; expected one of {list(GATES[kind])}")

    task: dict[str, object] = {
        "paper_id": "HU6mqxmtySg3",
        "review_key": f"HU6mqxmtySg3|{kind}|0",
        "content_hash": "0" * 16,
        "stage": {
            "value": 1, "relationship": 1, "structure": 2, "contrast": 2, "adjudication": 3
        }[kind],
        "paper_url": "/data/local-files/?d=texts/HU6mqxmtySg3.txt",
        "paper_title": "HU6mqxmtySg3",
        "paper_citation": "HU6mqxmtySg3 . pmid 0 . 10.0/0",
        "paper_text_hash": "0" * 64,
    }
    rows = range(size)

    if kind == "value":
        task.update(
            coordinate_status="yes",
            entity_class="Group",
            local_id="pd",
            field_path="age_mean",
            priority=0,
            llm_status="extracted",
            evidence_status="present",
            field_label="Group pd  ·  age_mean",
            field_description="Mean age of the cohort",
            llm_value="64.5",
            llm_meta="1 span(s)  ·  Methods > Participants",
            span_labels=[
                {"value": kind, "background": _SET_COLOURS[i % len(_SET_COLOURS)]}
                for i, (kind, _hint) in enumerate(_SUPPORT_KINDS)
            ],
        )
    elif kind == "relationship":
        task.update(
            coordinate_status="yes",
            rel_slot="Analysis.acquisitions",
            target_class="Acquisition",
            anomaly_count=1,
            rel_label="Analysis.acquisitions -> Acquisition (many)",
            rel_description="Acquisition protocols supplying data to this analysis.",
            anomaly_gate=[{"text": "- **analysis_1** links to `acq_9`, which was never extracted"}],
            columns=[
                {"value": descriptor(f"acq_{i}", f"run {i}", ["fMRI"]),
                 "alias": f"acq_{i}"}
                for i in rows
            ] + [{"value": "no link", "alias": "none"}],
            rows_multi=[
                {"label": f"analysis_{i}", "meta": f"ana_{i}", "local_id": f"ana_{i}"}
                for i in rows
            ],
            rows_single=[],
            link_labels=[
                {"value": descriptor(f"acq_{i}", f"run {i}", ["fMRI"]), "alias": f"acq_{i}"}
                for i in rows
            ],
        )
    elif kind == "contrast":
        # `table_html` is deliberately tiny here. A sample task is capped at 4000 bytes
        # by test_paper_text_is_never_inlined_in_a_sample_task, and that cap is what
        # would catch someone pasting a real 20 KB rendered table in.
        task.update(
            task_kind=gate,
            coordinate_status="yes",
            local_id={"table": "tbl3", "contrast": "ana_0"}[gate],
            table_id="t0005",
            cell_count=size,
            table_html='<div class="ns-table"><table class="ns-tbl">'
            "<thead><tr><th></th><th>x</th></tr></thead>"
            '<tbody><tr class="ns-a0"><td class="ns-gut">1</td>'
            '<td class="ns-coord ns-num">-58</td></tr></tbody></table></div>',
            structure_labels=[{"value": "analysis: PwPD . HC"}],
            table=[],
            sibling_rows=[],
            contrast=[],
            cell_rows=[],
            statistic=[{"summary": "**t**  ·  df 33  ·  model_0"}],
            statistic_options=[
                {"value": value, "hint": hint}
                for value, hint in _STATISTIC_VERDICTS.items()
            ],
        )
        if gate == "table":
            task.update(
                table=[
                    {
                        "label": "Table 3  .  2 analyses parsed",
                        "meta": "Regions showing a group difference.",
                        "guidance": "Judge the SPLIT, not the encoding: is each numbered "
                        "block one real analysis, and does the table report one this "
                        "list misses?",
                    }
                ],
                sibling_rows=[
                    {
                        "label": f"#{i + 1}  .  PwPD . HC",
                        "meta": "3 point(s)  .  3 row(s) attributed  .  encoded as ana_0",
                    }
                    for i in rows
                ],
            )
        else:
            task.update(
                contrast=[
                    {
                        "label": "ana_0  .  PwPD vs HC",
                        "parsed": "Parsed as analysis 2 of 4 from Table 3, with 9 points.",
                        "paraphrase": "Between-subject contrast of **group**: "
                        "PwPD (+) vs HC (-).",
                    }
                ],
                cell_rows=[{"label": f"group : cohort {i}"} for i in rows],
            )
    elif kind == "structure":
        # Exactly one gate is non-empty. The other blocks then render zero copies, so
        # their `required="true"` verdicts are never instantiated and never block
        # submission -- which is what lets several kinds share a project.
        task.update(
            task_kind=gate,
            coordinate_status="yes" if gate != "entities" else "not_applicable",
            local_id={"entities": "", "model": "glm_0", "contrast": "ana_0"}[gate],
            cell_count=size,
            structure_labels=[{"value": "cell: group . PwPD"}, {"value": "+ new term"}],
            entities=[],
            entity_table=[],
            entity_rows=[],
            model=[],
            terms=[],
        )
        if gate == "entities":
            task.update(
                entities=[
                    {
                        "label": "Groups in HU6mqxmtySg3 -- 2 extracted",
                        "meta": "One participant cohort each.",
                        "guidance": "A cohort the paper describes but never analysed "
                        "is still a Group.",
                    }
                ],
                entity_table=[
                    {
                        "local_id": f"grp_{i}",
                        "descriptor": descriptor(f"grp_{i}", f"cohort {i}", [f"n={20 - i}"]),
                        "referenced_by": "2 links",
                    }
                    for i in rows
                ],
                entity_rows=[
                    {
                        "label": f"grp_{i}",
                        "descriptor": descriptor(f"grp_{i}", f"cohort {i}", [f"n={20 - i}"]),
                        "referenced_by": "referenced by AnalysisGroup.group x2",
                    }
                    for i in rows
                ],
            )
        elif gate == "model":
            task.update(
                model=[{"label": "ModelEstimation . glm_0", "meta": "GLM", "summary": "2 terms"}],
                terms=[
                    {
                        "heading": f"term_{i}",
                        "summary": f"definition of term_{i}  ·  2 level(s) declared",
                        "levels": [{"label": f"level_{j}"} for j in rows],
                    }
                    for i in rows
                ],
            )
        else:
            task.update(
                contrast=[
                    {
                        "label": "Analysis . ana_0",
                        "paraphrase": "Between-subject contrast of **group**: PwPD (+) vs HC (-).",
                    }
                ],
                cell_rows=[{"label": f"group : level_{i}"} for i in rows],
            )
    else:
        task.update(
            dispute_kind="structure",
            dispute_field_count=2,
            dispute_label="Analysis . ana_0 . effect.cells",
            left_md="group:PwPD **+**, group:HC **-**",
            right_md="group:PwPD **-**, group:HC **+**",
            diff_md="- direction inverted on both cells",
        )

    missing = set(DATA_CONTRACT[kind]) - set(task)
    if missing:
        raise AssertionError(f"sample_task({kind!r}) omits contracted keys: {sorted(missing)}")
    return task


def interpolated_keys(config: str) -> set[str]:
    """Every task-data key the config reads.

    Mirrors parseValue's own regex (`utils/data.js:13`) and then strips the index
    path, so `$fields[{{idx}}].label` reports as `fields`. That is the unit the
    exporter populates.
    """

    keys = set()
    for match in re.findall(r"\$[\w\[\].{}]+", config):
        keys.add(re.split(r"[\[.]", match[1:], maxsplit=1)[0])
    return keys


def max_sets_in_record(record_path: Path) -> int:
    """Largest number of evidence sets on any single field, for label headroom."""

    record = json.loads(record_path.read_text(encoding="utf-8"))
    largest = 0

    def walk(node: object) -> None:
        nonlocal largest
        if isinstance(node, dict):
            if "extraction_status" in node:
                evidence = node.get("evidence")
                if isinstance(evidence, dict) and isinstance(evidence.get("sets"), list):
                    largest = max(largest, len(evidence["sets"]))
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(record)
    return largest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--record",
        type=Path,
        help="report the record's largest evidence-set count, as exporter headroom",
    )
    parser.add_argument("--min-sets", type=int, default=DEFAULT_MIN_SETS)
    args = parser.parse_args()

    if args.record:
        observed = max_sets_in_record(args.record)
        print(f"largest evidence-set count in {args.record.name}: {observed}")
        print(f"exporter should build at least {max(args.min_sets, observed + 1)} set labels")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for kind in KINDS:
        config = build_config(kind)
        path = args.out_dir / f"{kind}.xml"
        path.write_text(config, encoding="utf-8")
        contract = set(DATA_CONTRACT[kind])
        missing = interpolated_keys(config) - contract
        print(f"wrote {path} ({len(interpolated_keys(config))} task-data keys)")
        if missing:
            print(f"  WARNING: interpolates keys absent from DATA_CONTRACT: {sorted(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
