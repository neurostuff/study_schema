#!/usr/bin/env python3
"""The stylesheet every config carries.

Four rules govern this file, and each one exists because breaking it fails
silently rather than loudly.

**Tokens, never hex.** `libs/ui/src/tokens/tokens.prefix.css:567` redefines every
`--color-*` token under `[data-color-scheme="dark"]`, so a token-built panel
inverts with the theme.

**Background and foreground are set as a pair.** This is the specific trap: a
hardcoded light background keeps the theme's light *text* colour in dark mode,
leaving invisible light-on-light. Setting only one of the pair does the same. A
test asserts every rule that sets one sets the other.

**No `<`, `>` or `&` anywhere in the block.** Style content is passed through
`sanitizeHtml` (`tags/visual/Style.jsx`), which escapes them -- and one mangled
selector invalidates its whole comma-separated rule. A single
`.ant-table-tbody > tr > td` silently voided the neighbouring `.ant-table`
declarations and the panel went on rendering white with no error. Descendant
selectors and pseudo-classes only. That constraint reaches into the comments too,
which is why none of them uses those characters either.

**Vendored components must be overridden explicitly.** Label Studio's Choices,
Table and Collapse are antd, and `assets/styles/antd-no-reset.css` hardcodes
colours on 87 of their rules -- `.ant-radio-wrapper { color: rgba(0,0,0,0.85) }`
with no background of its own. Those inherit onto the label text and go invisible
against a dark page. The legacy Taxonomy has the same problem from a CSS module.

Two blocks are generated rather than written out, so their counts cannot drift
from the code that depends on them: one rule per analysis tint, and one `order`
pair per chat turn.
"""

from __future__ import annotations

import spec

#: Class names the coordinate-table renderer emits. Listed here because the
#: renderer and the stylesheet are one artifact split across two files: a class
#: emitted and not styled renders as unstyled markup with nothing to say why.
#: A test asserts this list covers what `tables.py` actually writes.
TABLE_CLASSES = (
    "ns-table", "ns-tbl", "ns-tbl-cap", "ns-tbl-note", "ns-tbl-key",
    "ns-key", "ns-key-mark", "ns-key-hit", "ns-key-maybe", "ns-key-own",
    "ns-sec", "ns-hit", "ns-maybe", "ns-gut", "ns-coord", "ns-num", "ns-warn",
)

_LAYOUT = """
.ns-row { display: flex; gap: 16px; align-items: flex-start; }

/* A flex column, not a scroller: the text scrolls inside .ns-paper-body and the
   chat box stays pinned under it, where a question about what is on screen can be
   asked without scrolling back to find the box. */
.ns-paper { flex: 1 1 55%; max-height: 82vh; display: flex; flex-direction: column;
            padding-right: 12px;
            border-right: 1px solid var(--color-neutral-border); }

/* Monospace, because the paper text carries its own formatting: markdown headings
   and coordinate tables as pipe-delimited grids. Neither lines up in a
   proportional face, and the pane cannot render markup -- it is a Text tag, the
   only region-bearing tag whose offsets serialize as plain integers. */
.ns-paper-body { flex: 1 1 auto; min-height: 0; overflow-y: auto;
                 font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
                 font-size: 12.5px; line-height: 1.5;
                 background: transparent; color: var(--color-neutral-content); }

.ns-form { flex: 1 1 45%; max-height: 82vh; overflow-y: auto; }
.ns-side { display: flex; gap: 12px; align-items: flex-start; }
.ns-half { flex: 1 1 50%;
           background: var(--color-neutral-surface);
           color: var(--color-neutral-content);
           border: 1px solid var(--color-neutral-border);
           padding: 8px 12px; border-radius: 4px; }
"""

_BLOCKS = """
/* The extractor's own answer, the paraphrase, a warning, and the two neutral
   containers. Each pairs a background with a foreground meant for it. */
.ns-llm { background: var(--color-warning-background);
          color: var(--color-neutral-content);
          border-left: 3px solid var(--color-warning-border);
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
"""

_CHAT = """
/* The exchange reads question, answer, question, answer, with the box you type in
   at the bottom.

   Each control renders its own entries as one block: its input, then every
   submission made to it. Interleaving the two controls means the individual
   entries have to become siblings in one column, which is what `display: contents`
   does here -- it drops the two wrappers, each control's block and each list
   container out of the layout, leaving the entries themselves (`div.lsf-row`,
   `regions/TextAreaRegion.jsx`) and the form as flex items of .ns-chat-body.

   `div:has(...)` rather than a child combinator, because the sanitizer voids any
   rule containing one. A row never contains a row or a form, so nothing inside an
   entry is flattened by these. */
.ns-chat { flex: 0 0 auto; margin-top: 8px; padding-top: 8px;
           border-top: 1px solid var(--color-neutral-border); }
.ns-chat-body { display: flex; flex-direction: column; }
.ns-chat-q, .ns-chat-a,
.ns-chat-q div:has(form),
.ns-chat-q div:has(.lsf-row),
.ns-chat-a div:has(.lsf-row) { display: contents; }

/* Past the paired turns an entry keeps this and falls in after them, still above
   the typing bar, rather than jumping to the top on order 0. */
.ns-chat-q .lsf-row, .ns-chat-a .lsf-row { order: 998; }
.ns-chat-q form { order: 999; margin-top: 4px; }

/* The question box reads as a composer. There is no submit button to place: the
   box sets showSubmitButton="false" and Shift+Enter submits, which any multi-row
   TextArea binds anyway (`TextArea.jsx:376-386`, independent of the button). An
   absolutely-positioned button overlapped the submitted question beneath it --
   the region list renders after the form and outside it, so there is nothing to
   anchor to that stays clear.

   `textarea` rather than a Label Studio class on purpose: 1.22 renders
   `lsf-text-area`, 1.24 renames the block, and both have a textarea. Inside
   .ns-chat there is exactly one, because the answer log carries maxSubmissions 0. */
.ns-chat form textarea { border-radius: 14px !important; resize: none !important;
                         padding: 11px 14px !important; }
/* 1.24 puts a character and submission counter under the box; it is noise here. */
.ns-chat form [data-testid="textarea-counts"],
.ns-chat form [data-testid="textarea-instruction"] { display: none !important; }
"""

_VENDOR = """
/* antd, via assets/styles/antd-no-reset.css. Each of these hardcodes a colour with
   no background of its own, so the label text goes invisible on a dark page. */
.ant-radio-wrapper, .ant-radio-group, .ant-radio, .ant-radio span,
.ant-checkbox-wrapper, .ant-checkbox, .ant-checkbox + span {
    background: transparent;
    color: var(--color-neutral-content); }
.ant-radio-inner, .ant-checkbox-inner {
    background: var(--color-neutral-surface);
    color: var(--color-neutral-content);
    border-color: var(--color-neutral-border-bold); }
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

/* The legacy Taxonomy, which every naming span layer uses. Not antd -- a CSS
   module -- but the same failure: its search rule sets neither a background nor a
   colour, so the input takes the browser default white and the page's light text,
   and what you type is white on white. The add-a-label input hardcodes a blue on a
   3% black background, and the chips for what is already selected hardcode a near
   white.

   Matched on the class prefix, never on `name=`. The class names are CSS-module
   output carrying a build hash (`taxonomy__search--qkTHD`), so only the readable
   prefix can be relied on. The `name` attribute would be the more stable handle
   and is the obvious thing to reach for -- but Label Studio validates a config by
   regex-scanning it for `name="..."`, stylesheet included, and rejects the whole
   config as carrying non-unique names when a CSS selector mentions one. */
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
"""

_TABLE = """
/* The rendered coordinate table. Every colour is a token pair because the
   reference implementation this was ported from used a fixed dark palette, and a
   hardcoded background here keeps the theme's own text colour and goes invisible
   in the other mode.

   `td:first-child` is a pseudo-class, not a child combinator, so it survives the
   sanitizer. The box-shadow reads a --color token for a rule down the gutter edge;
   it is decoration on a row whose pair is already set above it. */
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
   whose it is. Quieter than a marked row, too, and that ordering is the point --
   a contrast can have 3 rows of its own against 22 contested, and filling all 22
   with a saturated tint buried the answer under the caveat. The fill is neutral;
   the amber is carried by the rule and the mark. */
.ns-tbl tr.ns-maybe td { background: var(--color-neutral-surface-hover);
             color: var(--color-neutral-content); }
.ns-tbl tr.ns-maybe td:first-child {
             box-shadow: inset 3px 0 0 var(--color-warning-border);
             background: var(--color-neutral-surface-hover);
             color: var(--color-neutral-content); }
.ns-tbl tr.ns-maybe td.ns-gut { font-weight: 700;
             background: var(--color-neutral-surface-hover);
             color: var(--color-warning-content); }
.ns-tbl tr.ns-hit td.ns-gut { font-weight: 700;
             background: var(--color-positive-background);
             color: var(--color-positive-content); }

/* The caption above, the note below, and the legend that names the marks in use. */
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
"""


def tint_rules(tints: tuple[tuple[str, str, str], ...] = spec.TINTS) -> str:
    """One rule per analysis tint, and a rule down the gutter edge of each.

    Generated because the count has to agree with what the table renderer cycles
    through. Both read `spec.TINTS`; neither restates it.

    The gutter rule is what makes a block of rows read as one analysis: at this
    fill weight the tints are deliberately faint, and a run of twelve faintly
    tinted rows does not group by itself.
    """

    lines = ["", "/* generated: one rule per analysis tint */"]
    for index, (fill, text, rule) in enumerate(tints):
        lines.append(
            f".ns-tbl tr.ns-a{index} td {{ background: var({fill}); color: var({text}); }}"
        )
        lines.append(
            f".ns-tbl tr.ns-a{index} td:first-child {{ "
            f"box-shadow: inset 3px 0 0 var({rule}); "
            f"background: var({fill}); color: var({text}); }}"
        )
    return "\n".join(lines) + "\n"


def chat_order_rules(turns: int = spec.CHAT_TURNS) -> str:
    """`order` for each entry, so question N sits directly above answer N.

    The two lists are separate controls, and their entries only become orderable
    siblings once the containers are flattened by the `display: contents` rules
    above. Position is the only thing tying a question to its answer -- the payload
    carries no ids linking them, which is the same positional pairing the chat
    backend depends on, and it holds for the same reason: one answer per question,
    appended in order.
    """

    lines = ["", "/* generated: question N above answer N */"]
    for turn in range(1, turns + 1):
        lines.append(f".ns-chat-q .lsf-row:nth-of-type({turn}) {{ order: {2 * turn - 1}; }}")
        lines.append(f".ns-chat-a .lsf-row:nth-of-type({turn}) {{ order: {2 * turn}; }}")
    return "\n".join(lines) + "\n"


def stylesheet() -> str:
    return "".join(
        [_LAYOUT, _BLOCKS, _CHAT, _VENDOR, _TABLE, tint_rules(), chat_order_rules()]
    )
