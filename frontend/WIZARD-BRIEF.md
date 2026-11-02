# T7 design brief: Proposal Builder wizard, slice 2

Written before building. Sources: spec `docs/specs/2026-11-02-proposal-builder-design.md` (sections 2, 4, 6;
D1 to D6), `backend/API.md` ("Proposal templates", "Proposal runs", Documents), `DESIGN.md`, `tokens.css`,
the T10 brief (`DESIGN-BRIEF.md`) and the live template payloads from the dev backend (Standard 8 blocks,
Premier 11 blocks, 5 addable kinds, 8 fact sections, `limits`).

**Design read:** Operate mode. A broker turns carrier quotes into a client proposal in four steps; the wizard
is a short form flow inside the existing shell, not a landing page. DESIGN.md pins Inter, `#6356F0`, 6px
controls and 4px chips, Phosphor regular, hairlines with no shadows, motion tokens only. Those are not open.

**Dials (taste-core):** variance 3, motion 2, density 6. Same family as T10 (3/2/7), one notch airier because
steps 2 and 3 are forms people type into.

**Where the boldness goes (frontend-design):** the outline in step 3. It is the proposal's own table of
contents, numbered exactly as the document will be (Cover Page and Table of Contents unnumbered, then 1, 2,
3...), so the numbers carry information. Everything else (stepper, template options, fields) stays quiet.

## Layout

```
+----+---------------------------------------------------------------------------+
|rail| top bar: [glyph] BrokerDocs / Proposals > New proposal                     |
| 56 +---------------------------------------------------------------------------+
|[P] | pane                                                                      |
|    |  New proposal                                         (36px page title)   |
|    |  [1 Template]--[2 Details]--[3 Outline]--[4 Create]   (stepper, ol)       |
|    |  -----------------------------------------------------------------------  |
|    |  step body (max 960, left aligned, scrolls)                               |
|    |                                                                           |
|    |  -----------------------------------------------------------------------  |
|    |  [Back]                      summary text   [Continue] / [Generate proposal]
+----+---------------------------------------------------------------------------+

Step 1 Template                         Step 3 Outline (one row per block)
[search templates            ]          [lock] Cover Page          Locked, built from your details
( ) Standard    8 sections | preview    [lock] Table of Contents   Locked, lists the sections below
(o) Premier    11 sections | Premier    1  Executive Summary   Prose        [^][v][Edit][x]
                           | contents   2  Coverage Comparison Table: General Liability Coverage
                           | 1 ...      ...
                           | Word: Georgia, heading colour  [+ Add section]  (inline library, no modal)
```

Step 4 is the existing run view: Generate posts the run and opens `#/runs/{id}`, where the T10 progress rows
(SSE) and then the report appear. The report view gains Preview (hides chips) and DOCX export.

Phone (390): stepper shows numerals plus the current step name; template options and preview stack; outline
rows wrap to two lines (title row, then actions row with 44px targets); footer bar stays pinned.

## Decisions and what each skill said

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design |
|---|---|---|---|---|---|---|---|
| Stepper: `ol` of four named steps, numeral square + name, done steps show a check and are clickable, `aria-current="step"`; Back and Continue in a pinned footer | Voice: plain words, no em dashes | "Show progress for multi-step processes: step indicator" (Feedback, Progress Indicators) | Bans generic "Step 1 / Step 2" labels: steps are named by content (Template, Details, Outline, Create); the numeral stays because the order matters | Every flow needs a way back: Back button plus clickable done steps | Operate: earned familiarity, a standard stepper beats an invented one; nav shows where you are | Step change is instant (frequent, often keyboard): no slide or fade between steps | Numbering only when the content is a sequence: a 4-step process is |
| Template gallery: two radio options (real `input type=radio`), hairline border, selected = primary border + tint, with the radio dot as the non-colour signal; preview pane beside it shows the outline as numbered contents, table marks, Word font and heading colour; search filters name, description and section titles | n/a | Visible labels; 44px targets; never colour alone (the checked radio is the second signal; a check icon on top was cut as a duplicate) | Cards only when elevation means hierarchy: these are choice controls on hairlines, no shadow; no 3-equal-card row (there are 2, side by side with the preview) | Align shared elements across side-by-side options (name, count, description start at the same y) | Every state: default, hover, focus, selected, loading (skeleton rows), empty search ("No template matches...", Clear search), error (Retry) | Hover tint only behind `(hover: hover)`; press scale 0.97 | The preview uses the template's real section titles, not a fake document thumbnail |
| Details: Quote PDFs first (reuse `DropZone` + `uploadFile`, plus "Choose from uploaded files" disclosure listing `GET /api/documents`), then client, broker, brokerage, additional context (counter `n / 4000` from `limits.max_context_chars`), "Plain text only" checkbox for `no_ai_prose` | No AI vocabulary in helper text | Label above input, helper text, error below with `aria-describedby`; at least one PDF checked on Continue with the error next to the drop zone | Labels above inputs, never placeholder-as-label; form contrast (input border `#848D99` 3.36:1) | No `alert()`; inline errors | Controls name their action: "Choose files", "Add to proposal" | Upload progress bar is the only motion (existing scaleX transition) | Helper text says what the toggle does in the user's terms ("Sections get plain sentences from the verified values; no AI writing") |
| Outline editor: one row per block, numbered like the document; Edit opens an inline editor under the row (title with counter, instruction with counter, fact sections as a checkbox group); Delete with "Removed X. Undo" toast; "Add section" opens an inline kind library; "Reset to template" | n/a | Dragging must not be the only way (WCAG 2.2): Move up and Move down buttons; focus stays on the moved row's button; a polite live region announces "Moved to position 3 of 8" | Full state cycle; no decorative dots; tags 4px for fact sections | Inline editing, not modals, for simple edits | Modal is not the first thought: inline editor and inline library; overlays none | Reorder is keyboard-triggered and frequent: no move animation; the moved row gets a short opacity fade of the tint so the eye finds it (kept under reduced motion, opacity only) | The boldness lives here: the outline is the proposal's contents, numbers match the export |
| Reorder: buttons only, no drag | n/a | Single-pointer alternative required; buttons are that alternative and work on touch | n/a | n/a | Earned familiarity over invented drag affordances | Drag would need springs and pointer capture to feel right; buttons are instant and honest | One way that works at every width (phone included) |
| Locked Cover Page and TOC: first two rows, lock glyph instead of a number, canvas-tinted row, text "Locked" plus what it is built from; no move, edit or delete controls (not rendered, so nothing disabled to tab through); block 3 cannot move above them | n/a | Disabled things must explain why; never colour alone (glyph + word) | Glyph + text, no pill | Disabled must look disabled: here the controls are absent and the row says why | State vocabulary: locked is a state with its own silhouette | n/a | Structure is information: the lock glyph replaces the number because these blocks are not numbered in the document |
| Inline 422 errors: client checks first (mirror of the server rules: title 1 to 120 chars on one line, instruction up to 1000, table block needs a fact section, one table per fact section D4, at most 20 blocks), then server `fields[].loc` mapped to the same keys (`outline.N.title`, `outline.N.fact_sections.M`, `client_name`...); an error summary at the top of step 3 (focused, `role="alert"`, each item a button that opens the block and focuses the field); each field shows its error below with `aria-describedby`; rows with errors show a warning glyph and "Needs a fix" | Errors in plain words | Focusable error summary + inline errors near the field (both, High severity) | Inline errors; toasts only for transient | Errors name problem and fix; no "Oops" | Errors name the problem and the recovery | Summary appears instantly (keyboard submit) | Errors never apologise; they say what to change ("Pick at least one fact section for this table.") |
| Phone 390, step 3: rows stack (number + title + kind on line 1, fact tags line 2, actions line 3 with 44px targets); editor fields full width; footer Back / Generate stays pinned; no horizontal scroll | n/a | 44px touch targets, 8px spacing; no horizontal scroll | Mobile collapse declared per layout | No horizontal page scroll | Responsive behaviour is structural | n/a | n/a |
| Draft lifetime: kept in `sessionStorage` until Generate, so step changes and in-app navigation never lose it; `beforeunload` warns when closing the tab with a draft; leaving the wizard in-app shows a toast "Proposal draft kept." with "Back to draft"; cleared after a successful Generate; "Discard draft" link in the footer | Plain copy | Predictable back; no data loss | n/a | No dead ends | Handle the edge case invisibly: the draft survives rather than nagging with a blocking dialog | Toast enter 250ms / exit 150ms (existing) | The action keeps its name: "Generate proposal" -> "Generating..." |
| Report: Preview toggle (eye icon, `aria-pressed`) hides citation chips for a clean read; DOCX joins PDF and XLSX in the toolbar; cover and TOC headings render without a stray "." number | n/a | Icon buttons have labels and tooltips | Same icon family (Phosphor `Eye`, `FileDoc`) | n/a | Consistent vocabulary: Preview sits with the other toggles (side by side, outline) | Toggle is instant | n/a |
| Copy | No AI vocabulary, no em dashes | Visible labels, helper text | Zero em dashes; no filler verbs | No exclamation marks; sentence case | Controls name their action | n/a | Plain verbs: "Continue", "Generate proposal", "Add section", "Move up", "Reset to template", "Choose files" |

## Deliberate choices against a skill

- taste-core and taste-redesign push away from Inter and violet; DESIGN.md pins both (override path, as in T10).
- taste-core's section 13 puts wizards out of its scope; only its state, shape, copy and em dash locks were applied.
- impeccable asks for PRODUCT.md (none exists, `impeccable context` reported `NO_PRODUCT_MD`). Not written: out
  of scope for T7, flagged again.
- ui-ux-pro-max suggests "Step 2 of 4" text; taste-core bans generic step labels. Resolved: named steps with
  numerals; the ordinal is in the accessible name ("Step 2 of 4, Details").
- No drag and drop for reorder (spec says "reorder"; buttons satisfy it and WCAG 2.2). Drag is slice 2b
  (TipTap editor) territory.
