# DESIGN.md: BrokerDocs, slice 1

Task T12 (DG8). Tokens live in `frontend/src/styles/tokens.css`; components use
variables only, never raw hex. Sources: `docs/reference/brokerdocs-ui-reference.md`
(tagged [exact]/[approx]) and a live Playwright read of `app.brokerdocs.ai/signIn`
on 2026-11-01 (tagged [live]). Mode: Operate. Light theme only.

Design read: a desktop tool for insurance brokers checking quotes. The report
table is the one visual anchor; everything else is quiet chrome with hairlines,
no shadows, no cards.

## Identity
- Wordmark: text "BrokerDocs" from one config constant (`APP_NAME`, DG7), Inter
  600 at 16px in `--color-text`, preceded by a plain CSS glyph (6px-radius
  square) in `--color-primary`. No orange mark, no BrokerDocs assets.
- One accent: `--color-primary` `#6356F0`. Document hues are data, not accent.

## Type
Inter only (`--font-sans`). Scale 12/14/16/20/36 with line heights
16/20/24/28/44. Weights 400 body, 500 labels and buttons, 600 headings.
- 36px: page title only, tracking -0.02em.
- 20px: report section headings. 16px: report prose, buttons. 14px: table
  cells, labels, panels. 12px: chip numerals, legal, captions; never body.
- Tables, counts, page numbers, money: `font-variant-numeric: tabular-nums`,
  numbers right-aligned, thousands separators.
- Sentence case everywhere. Status words in tables stay plain uppercase bold
  (observed), no pills.

## Spacing and shape
- 4px base: `--space-1` (4) to `--space-16` (64). Rows 8/12, panel padding 16,
  section gaps 24/32.
- Radius rule: 6px on every control (inputs, buttons, links, rail items, tabs);
  4px on chips and tags; full round only for avatar and spinner.
- Heights: buttons 44px, inputs 48px (including the DG12 cell edit), rail 56px,
  left panel 300px, top bar 56px [approx].
- Borders are 1px hairlines (`--color-hairline`). No drop shadows.

## Surfaces
| Token | Value | Use |
|---|---|---|
| `--color-surface` | `#FFFFFF` | report, panels, inputs |
| `--color-canvas` | `#F3F4F6` | app background |
| `--color-hairline` | `#E5E7EB` | dividers, secondary button border (decoration) |
| `--color-primary-tint` | `#EEECFD` | active rail item, selected row |

## Contrast rules
- Body text is `--color-text` `#35383B`; secondary text never lighter than
  `--color-text-muted` `#4B5563`.
- Placeholders only: `#6B7280` [live].
- Never as text: `#9CA3AF`, `#F97066`, `#3CCB7F` (and every `--doc-*-mark`).
- Text 4.5:1, UI boundaries and icons 3:1, all measured below.
- Inputs: the reference border `#CED5DE` is 1.48:1 and fails DG10. Shipped
  border is `--color-border-input` `#848D99` (3.36:1). Owner may revert for
  fidelity; that trades away DG10.

## Document hues (DG11)
Five pairs cycle by upload order; the chip also carries the document letter
A to H ("B2"), which prefixes the column header, so hue never works alone.
`-mark` is the reference stylesheet hue, used only for the column header rule
and PDF tab underline.

| # | Name | Chip bg | Chip text | Mark (decor) |
|---|---|---|---|---|
| 1 | violet | `#EEECFD` | `#4F42D8` | `#A99CF7` |
| 2 | coral | `#FEECEA` | `#B42318` | `#F97066` |
| 3 | green | `#E6F7EE` | `#067647` | `#3CCB7F` |
| 4 | blue | `#E6F2FE` | `#0459A8` | `#0188FB` |
| 5 | orange | `#FFEFE5` | `#9C3410` | `#FF9C66` |

## Contrast table (computed, WCAG 2.x relative luminance)
Script: WCAG formula over every pair below; 33 pairs, one intended fail (the
unshipped reference input border).

| Pair | Fg | Bg | Ratio | Min | Result |
|---|---|---|---|---|---|
| doc 1 text on tint | `#4F42D8` | `#EEECFD` | 5.82 | 4.5 | pass |
| doc 2 text on tint | `#B42318` | `#FEECEA` | 5.76 | 4.5 | pass |
| doc 3 text on tint | `#067647` | `#E6F7EE` | 5.12 | 4.5 | pass |
| doc 4 text on tint | `#0459A8` | `#E6F2FE` | 6.17 | 4.5 | pass |
| doc 5 text on tint | `#9C3410` | `#FFEFE5` | 6.43 | 4.5 | pass |
| doc 1 to 5 text on white | | `#FFFFFF` | 5.69 to 7.21 | 4.5 | pass |
| text on white / canvas | `#35383B` | white / `#F3F4F6` | 11.80 / 10.72 | 4.5 | pass |
| muted on white / canvas | `#4B5563` | white / `#F3F4F6` | 7.56 / 6.87 | 4.5 | pass |
| placeholder on white | `#6B7280` | `#FFFFFF` | 4.83 | 4.5 | pass |
| primary link on white / canvas | `#6356F0` | white / `#F3F4F6` | 5.10 / 4.63 | 4.5 | pass |
| white on primary / hover | `#FFFFFF` | `#6356F0` / `#5245E0` | 5.10 / 6.39 | 4.5 | pass |
| focus ring on white / canvas | `#6356F0` | white / `#F3F4F6` | 5.10 / 4.63 | 3.0 | pass |
| active rail icon on tint | `#6356F0` | `#EEECFD` | 4.38 | 3.0 | pass |
| danger text on white | `#B42318` | `#FFFFFF` | 6.57 | 4.5 | pass |
| stale tag text on tint | `#93370D` | `#FFFAEB` | 7.21 | 4.5 | pass |
| tooltip text | `#FFFFFF` | `#35383B` | 11.80 | 4.5 | pass |
| shipped input border on white / canvas | `#848D99` | white / `#F3F4F6` | 3.36 / 3.05 | 3.0 | pass |
| reference input border (not shipped) | `#CED5DE` | `#FFFFFF` | 1.48 | 3.0 | fail |
| hairline (decor) | `#E5E7EB` | `#FFFFFF` | 1.24 | n/a | decor only |
| `#9CA3AF` / `#F97066` / `#3CCB7F` | | `#FFFFFF` | 2.54 / 2.79 / 2.09 | n/a | never text |

## Evidence states (DG3)
Each state has its own silhouette; color is the second signal.

| State | Silhouette | Tokens |
|---|---|---|
| Verified | solid tint chip, letter+number, no border | `--doc-N-bg`, `--doc-N-fg` |
| Scan-read | same chip, 1px dashed border in `--doc-N-fg`, scan glyph; tooltip "Read from scan, unverified" | `--evidence-scan-border-*` |
| Couldn't verify | no chip; text "Couldn't verify" with triangle glyph | `--evidence-unverified-fg` `#4B5563` |
| Failed | "Extraction failed", refresh glyph, Retry (cell and column header) | `--evidence-failed-fg` `#35383B` |
| Edited | value plus pencil glyph; chips fold into "original source" | `--evidence-edited-fg` |

Failed uses neutral text, not red, so it never reads as document 2 (coral).
Chips are 20px visual, 44px hit area via `::before`. PDF passage highlight:
`--pdf-highlight-border` 2px dashed primary (observed dashed violet box).
Stale section tag "May be out of date": `--color-stale-bg/fg`, glyph plus text.

## Focus, disabled, rail
- Focus: `outline: var(--focus-ring); outline-offset: var(--focus-ring-offset)`
  on `:focus-visible` (2px `#6356F0`, 2px offset, DG10). The reference uses a
  2.4px text-colored outline [live]; DG10 overrides.
- Disabled controls: `opacity: 0.5` [live], `cursor: not-allowed`, no hover or
  press change.
- Disabled rail item (DG1): icon in `--rail-disabled-icon` `#9CA3AF`, no label
  text, no hover tint, `aria-disabled="true"`, still focusable so the tooltip
  "Coming in a later slice" shows on hover and focus. Not a link.
- Active rail item: `--color-primary-tint` 6px square, icon `--color-primary`.

## Motion
- Animate only `transform` and `opacity`, never `transition: all`.
- Curves: `--ease-out` for enter and feedback; `--ease-in-out` for on-screen
  moves; spinner linear.
- Durations: press 120ms (scale 0.97), hover and tooltip 150ms, tabs and
  side-by-side 200ms, toast enter 250ms and exit 150ms, ceiling 300ms. The
  reference animates at 300ms [live]; we run shorter on purpose.
- Allowed moments: press feedback, tooltip, toast, side-by-side panel,
  progress spinner. No entrance staggers, no keyboard-triggered animation
  (Esc, Enter, chip open via keyboard are instant).
- `prefers-reduced-motion`: scale and movement off, opacity fades kept,
  spinner slows to 1.5s.

## Left panel (patterns, 2026-11-02)
See `frontend/LEFT-PANEL-BRIEF.md`. No new tokens.
- Shares: the sections area shrinks to its content (`flex: 0 1 auto`); chat with a report
  takes the rest (`flex: 1 1 0`, floor 240px); chat without a report, or folded, docks at the
  bottom as a 44px handle. No fixed-height inner scroll box.
- Upload folds to one summary line ("4 documents, all read") only when the run is Done;
  the user's toggle wins until the run status changes. A folded section's summary replaces
  its count badge.
- Document rows: one line (letter, filename, page count, 16px status glyph with its own
  silhouette, never a colour-only dot); a second line only for news (progress, failure,
  option note). Past 6 rows, quiet rows after the 5th fold into "+N more" at their position;
  rows with news never fold.
- Disclosure motion: caret rotation (`--dur-base`), body fades in 150ms on pointer opens only;
  keyboard opens are instant.

## Policy Check (slice 3, 2026-11-02)
See `frontend/POLICY-CHECK-BRIEF.md`. New tokens: `--severity-critical-fg` `#B42318` (6.57:1 on
white), `--severity-high-fg` `#93370D` (7.52:1), `--severity-medium-fg` `#35383B`, `--severity-low-fg`
`#4B5563`, `--severity-tick-off` `#D1D5DB` (decoration only).
- Severity: plain uppercase bold word (the status-word rule) plus a four-tick meter (4 Critical to 1 Low),
  so the tier reads without colour. No pills.
- Severity filters: toggle buttons with the 6px control radius; pressed = primary tint, primary border
  and a check glyph.
- Card: one hairline-bordered unit per discrepancy (no shadow); four slots always in role order
  (Expiring, Quote, Renewal, AMS); the renewal slot takes `--color-primary-tint` (the document being
  checked). Slots reflow by the list's width: 4 columns, 2 x 2, one per row.
- Slot states reuse the evidence silhouettes: value + chip, dashed chip + "Read from scan, unverified",
  N/A with "No document" or "Not stated", Not read with the refresh glyph and the document's reason,
  Needs review with the triangle glyph. "Changed since resolved" reuses the stale tag.
- AMS chip label "AMS · <field>"; every Policy Check chip boxes the cited fact, not the whole page.
- Review dialog: native `<dialog>`, opacity + scale 0.98 at `--dur-base` on pointer open only; full
  screen below 900px with the source stacked under the item.

## Open item: collapsed-outline warning mark (recommendation, not decided)
Recommend: when the outline is collapsed and any value is scan-read,
couldn't verify, or failed, the collapse handle shows the triangle glyph from
DG3 in `--evidence-unverified-fg`, 16px, no color fill and no dot. The handle's
`aria-label` and tooltip carry the counts ("Outline. 3 unverified, 1 failed").
Activating the handle expands the outline and moves focus to the first trust
count. Why: it reuses an existing silhouette, so it adds no new visual
language, and it keeps the counts one action away without a top strip (DG2).
Owner decides.

## Skill notes
- gstack: router only; no sub-skill fit a tokens doc. Took its voice rule (no
  em dashes, plain words) for all copy here.
- ui-ux-pro-max: contrast 4.5:1 and 44px touch targets are priority 1 and 2;
  that drove the 44px chip hit area and the input border fix. Table search
  result (horizontal scroll, number formatting) became the tabular numerals and
  separators rule.
- taste-core: Inter and violet are its named defaults to avoid, but its
  override path covers a brief that pins them (D2 did). Its shape-lock rule
  gave the documented 6px/4px/round radius rule; its "no decorative dots" rule
  shaped the warning mark as a glyph, not a dot.
- taste-redesign: tabular figures for data, one gray family (Tailwind cool
  grays only), hover/active/focus states required, disabled things must look
  disabled. All present as tokens.
- impeccable: Operate mode, so scanability over expression; brand lives in
  precise details. Its context check found no PRODUCT.md; I did not write one
  (out of scope for T12) and flag it.
- emil-design-eng: custom ease-out curves, UI motion under 300ms, press scale
  0.97, exit faster than enter, no animation on keyboard actions, reduced
  motion keeps opacity. Directly set the motion tokens and the 300ms to 200ms
  change.
- frontend-design: spend boldness in one place (the report table and its
  chips), keep the rest quiet; avoid uppercase labels and template chrome.
  That kept status words as the only uppercase and removed shadows.

## Not verified against the reference
- `--color-primary-hover` `#5245E0`, `--color-primary-tint` `#EEECFD`, all
  document `bg`/`fg` pairs, stale and danger colors: derived, not measured.
- Top bar 56px, rail 56px, left panel 300px: [approx] from frames.
- Chip radius 4px: [approx]. Canvas `#F3F4F6`: [approx] grey, picked from
  the reference's Tailwind greys.
- Focus ring and motion durations deliberately differ from the live page.
