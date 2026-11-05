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
Inter (`--font-sans`) for everything the system says, plus Source Serif 4 (`--font-serif`) for the paper layer:
see "Voice" below. Scale 12/14/16/20/36 with line heights
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
- Allowed moments (voice + motion lane, 2026-11-03; line move B adds the book tab and book crumb re-file): press feedback, tooltip, toast, side-by-side panel, the sliding
  segmented indicator (220ms `--ease-out`, `--dur-segment`; line switch, see "Line switch" below), folder-tab cross-fade and PDF tab enter/close, wizard step body,
  list fades on pointer filter/sort changes (opacity 0.35 to 1, 150ms, rows not remounted), disclosure height +
  opacity (`Collapse`), dialog/menu/overlay exits, queue row enter/exit, the reading line, the seal, and a 150ms
  cross-fade of `#main` on pointer route changes. Exits are faster than enters. No entrance staggers.
- Keyboard-triggered changes are instant everywhere: `ui/input.ts` keeps `<html data-input="pointer|keyboard">`;
  CSS drops transitions under `[data-input="keyboard"]`, components check `keyboardAction()`.
- `prefers-reduced-motion`: scale and movement off, opacity fades kept (durations are not zeroed; each component
  drops its transform), spinner slows to 1.5s, the reading line stands still, the seal does not land.

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

## Work queue (2026-11-03)
See `frontend/QUEUE-BRIEF.md`. No new tokens.
- Rows are named by client and carry one primary action; a hairline list, no cards. Each row starts with the workflow's
  drawn mark (see Voice; superseded the Phosphor duotone tabs on 2026-11-03).
- Section headers: serif 22px, a group mark and a plain tabular count (Inter); the In progress strip and Done recently
  stay 17px.
- Needs review status reuses the Policy Check four-tick severity meter (worst open item) before the open counts.
- Queue navigation (2026-11-03, `frontend/QUEUE-NAV-BRIEF.md`): the client name is the link to the row's report (text
  colour, 1px resting underline in `--color-border-input`, `currentColor` on hover); no separate "Open" link. The meta
  line is one line: what (or, when a client shows on two rows, the carriers' short names), then the start date; the
  "name from the documents" tag sits beside the name. Phone drops the workflow word and the time, and lets the line wrap so the carriers are never cut. Identity and status
  columns split 3fr / 2fr. The report header names the client ("<client> · Compare Quotes"), run number and version
  as text-xs metadata under it; the client button reads "Change" when a name exists.
- Motion primitives in `ui/motion.tsx` / `ui/motion.css` (for reuse by the motion lane): list enter (opacity +
  translateY 6px, 200ms ease-out, `backwards` fill so no stacking context is left), list exit (opacity + grid-row fold,
  190ms), disclosure collapse (grid rows 0fr to 1fr, pointer only), count bump (scale 0.85 + opacity, 200ms), live sweep
  (2px line, 1.6s linear). First load never animates. Reduced motion: opacity only.

## Line move: book tab (owner-approved 2026-11-03, direction B)
See `frontend/LINE-MOVE-BRIEF.md`. No new tokens.
- A run is filed in a book, Commercial or Personal lines, and the page always says which:
  - **Crumbs** lead with the book for every run, Commercial included: "Commercial > Policy check > client",
    "Personal lines > Compare Quotes > client", "Commercial > Proposals > Run N". The Compare and Policy Check start
    screens lead with a book too (line switch, below).
  - **Run header**: a book tab (the book's mark at 18px plus its name; 32px tall, `--color-canvas` fill, 3px
    `--color-text-muted` left edge like a binder divider, 4px chip radius, no border so it never reads as a button),
    then one text button that names the consequence: "Move to Commercial" / "Move to Personal lines" (link colour,
    resting underline in `--color-border-input`, `currentColor` on hover, 32px drawn, 44px hit area). This replaced the
    Commercial / Personal radios in the run header; the start screens keep their full-size "Line of business" choice.
  - Phone (<600px): the button takes its own row under the tab.
- The move is optimistic. The toast names the file and the book ("Avery and Jordan Pellham moved to Commercial.") and
  offers Undo (6s; Ctrl/Cmd+Z while it shows). Undo says "... moved back to Personal lines." and offers nothing more.
  A failed PATCH puts the old book back and shows the server's message.
- Motion: on a pointer move the new tab drops in (opacity + translateY 6px to 0, `--dur-base`, `--ease-out`) and the
  book crumb fades in (opacity + translateY 4px, same timing). Keyboard moves are instant and focus stays on the button
  (its label flips). Reduced motion: fades only. The rail keeps highlighting the tool (D7).
- The line choice segment (start screens) always draws its check glyph and hides it when unchecked, so both halves keep
  one width and a flip never shifts the control.

## Line switch: the segmented control and the chosen book (owner-approved 2026-11-03)
See `frontend/LINE-SWITCH-BRIEF.md`. New token: `--dur-segment` 220ms.
- **Segmented control** (Line of business, Activity "Show", Tasks filter; `personal/personal.css`): one joined track, a
  single 1px `--color-border-input` outline with the 6px control radius, no gap and no per-option borders; equal halves.
  The selection is one indicator (tint fill, 1px primary border) laid over its half of the track edge. Labels are
  `--color-text-muted`, the chosen one `--color-text`. Native radios in a fieldset (one tab stop, arrow keys), focus ring
  on the option, 36px drawn (32px compact) with a 44px hit area.
- **Motion**: on a pointer change the indicator slides `--dur-segment` on `--ease-out` (a third of the way in about
  40ms, so it is seen leaving); the check glyph fades and scales in from 0.85 (220ms) and out in 120ms; label colours
  cross-fade on the same timing. This replaces the 200ms `--ease-in-out` slide, which spent its first 80ms moving 18%
  and read as a snap. Keyboard changes: everything instant. Reduced motion: the indicator jumps, glyph and labels fade.
- **Phone compact** (<600px, Tasks): options size to text, so each option draws its own selection (same tint and edge)
  and cross-fades its background; there is no sliding indicator.
- **The chosen book on the page**: the Policy Check wizard's crumbs lead with the chosen book ("Personal lines > Policy
  check > New check", "Commercial > Policy check > New check") and its title carries the run header's book tab without
  the Move button. A pointer flip re-files them like a run move (tab drops in, crumb fades in); keyboard flips are
  instant. The Compare start screen leads with its queue's book ("Commercial > Compare Quotes"); its Upload panel's
  choice files the next comparison and does not rename the page, because the page lists that book's queue.

## Voice: "Paper trail" (owner-approved 2026-11-03, direction C)
See `frontend/VOICE-BRIEF.md`. The product's promise is that every value traces to a page in a source document, so
the voice is the paperwork itself. Structure and layout stay as the BrokerDocs evidence has them.

**Type rule: serif is the paper, sans is the system.**
- `--font-serif` (Source Serif 4 SemiBold 600, latin + latin-ext subsets, 21.5 KB, font-display swap, metric-matched
  Georgia Bold fallback `Source Serif 4 Fallback` so the swap does not shift layout): page titles (h1), section
  headings (h2: queue groups, wizard steps, report sections, dialogs' titles, empty-state titles), client names
  (queue rows, run header client). Nothing else; no italic, no other weight.
- Inter: every action, status, label, count, number, table cell, form, chip and caption. "Add client" (no name yet)
  is the system asking, so it stays Inter.
- Sizes: page title 36 (-0.015em), queue section 22 (-0.01em), strip/archive 17, client in a row 19, run-header client
  17, empty-state titles 18; phone: page title 32, section 19, row client 17.

**Marks** (`frontend/src/voice/marks.tsx`, drawn on purpose, no stock set): one 24 grid, 1.5px strokes, round joins,
a paper sheet (`--color-surface` fill, 1.5px radius) as the base shape. Decorative (`aria-hidden`); text carries
the meaning. Each needs its own silhouette at 20 to 28px:
- Workflows: Compare Quotes = two sheets side by side; Policy Check = the renewal laid over the dashed expiring sheet,
  ticked; Proposal = a bound sheet (filled spine). Used on queue rows (28px), the run header and wizard titles
  (30 to 34px), start buttons (18px, inside the button).
- Queue states: In progress = sheet with the reading line; Needs review = sheet with the dashed citation box (the
  viewer's passage highlight); Ready = sheet with a ribbon bookmark; Needs attention = sheet with its corner missing;
  Done = sheet under a seal.
- Rail ("Silhouettes", owner-picked 2026-11-03, see `frontend/RAIL-BRIEF.md`; drawings in
  `frontend/src/voice/railMarks.tsx`): at 20px interior lines carry nothing, so **each rail item has its own outline
  and mass**, never a plain sheet with lines. Same material (sheet fill, 1.5 stroke, round joins, monochrome).
  Workflows = two sheets side by side; Proposals = bound cover with a solid spine and a label window; Personal lines =
  house; Policy check = renewal over the dashed expiring sheet, ticked; Certificates = landscape certificate with a
  rosette breaking its lower edge; Tasks = clipboard with a solid clip; Integrations = two small sheets joined;
  Assistant = round balloon with one line; Market search = loupe with the lines inside the lens; Analytics = axes,
  rising line, a dot on the latest figure; Insights = tinted sticky note with a folded corner; Vault = archive box with
  an overhanging lid; Documents = file folder with its tab. A new rail item must not share an outline with an existing
  one (check it blurred at 20px). Active/disabled colours, hover and focus as in "Focus, disabled, rail". The rail
  marks are rail-only: the Proposal workflow mark on queue rows, wizard titles and start buttons stays the bound sheet.
- Books (line move B, owner-approved 2026-11-03): Personal lines = the house (shared with the rail item); Commercial = an
  office block on its ground line (sheet-filled block, two rows of windows, a door). Used only in the run header's book
  tab (18px); not a rail item.
- Colour inside marks: `currentColor` for the drawing; document hues (`--doc-N-mark`) only as small sheet tabs;
  `--color-primary` only for the citation box, the reading line and the ribbon.

**States as paper:** wherever the system is reading documents (document rows, the Report tab, the run's reading and
writing states, app load), the reading mark replaces the spinner: a sheet whose line travels down it (1.6s linear,
constant motion; reduced motion: static line). Button progress ("Starting...", exports) keeps the spinner. Empty
states show a sheet (blank, tasks, filed) above a serif title.

**One loud button per region:** a group whose rows all offer the same action (Needs attention: Retry) shows it in the
secondary look; the primary violet stays for the one next step per region.

**The seal:** a row that reaches Done recently carries a small seal on its mark. It lands once (scale 1.25 to 1 with a
rotation settle, 220ms `--ease-out`) only when a pointer action filed it; keyboard actions, polling and first load
show it still; reduced motion: no motion.

## Certificates (slice 4, owner-approved 2026-11-03, direction A "Proof and ledger")
See `frontend/CERTIFICATES-BRIEF.md`. No new tokens.
- **Rail and pages**: Certificates is live (`#/certificates`): the certificate queue (In progress, Needs attention, Ready to
  issue, Done recently) with the work-queue row pattern (certificate mark, serif client link, one secondary action per
  row: "Resolve checks", "Review and issue", "Retry"), and New certificate. Certificates are not in the main queue (C8);
  Activity lists them ("Certificate, run N"). A queue row's menu offers "Issue certificate", which opens the wizard with
  the client filled in.
- **Wizard** (Who, Policies, Read): past holders for the client as radio cards (serif name, address, last used), New
  holder; wording as checkboxes; certificate date; description with its count. Policies: the client's documents as
  checkbox rows with "Read on <date>, no new read" in `--doc-3-fg` when the bytes were read before, and an upload zone.
- **Draft**: the certificate is drawn as one sheet (`--color-surface`, 1px hairline, 2px radius, no shadow, max 760px)
  on the canvas; serif for its title, the insured's and holder's names; Inter for every value; "Not stated" in muted
  text. The legal header and cancellation text carry "Sample wording: ..." in `--color-stale-fg` (owner, 2026-11-03),
  on screen and on the PDF. Each checked line carries its claim glyph in the left margin (16px).
- **Ledger** (right column, 400px; 340px under 1280; first on phone): To resolve, Handled by you, Supported by the
  documents (folded). Claim states have their own silhouettes (Phosphor, decorative) plus the plain uppercase status
  word: Supported = ticked circle `--doc-3-fg`; Not supported = crossed circle, bold, `--severity-critical-fg`; Needs
  sign-off = signature, bold, `--severity-high-fg`; Confirmed = signature `--doc-3-fg`; Overridden = stamp
  `--color-primary`; Removed = eraser `--color-text-muted`. Open rows have a 3px inset rule in their state colour.
  Actions: Add document (secondary), then Override... / Confirm... and the Remove verb named by its effect ("Remove from
  certificate", "Remove this policy", "Remove the description", "Restore the extracted value") as ghost buttons.
  Override and Confirm open an inline reason editor ("Saved on the issued record. Not printed."), never a modal.
- **Pointing**: hovering or focusing a check boxes its line on the paper with the viewer's dashed passage box and
  scrolls only the paper's column when the line is out of view (smooth on pointer, instant on keyboard and reduced
  motion). A chip swaps the paper for the cited page in the same column ("Back to certificate", Esc).
- **Issue gate**: "Issue certificate" is the one primary button, `aria-disabled` with the reason under it ("4 checks to
  resolve", "Every check is handled" in `--doc-3-fg`). A refused Issue (version changed, still reading, checks open,
  no policy in force) reloads the draft and explains in a stale-tinted banner. Issued: "ISSUED <date>" with the done
  mark, Download PDF (primary), New version (ghost); the ledger becomes the Record (no actions).
- **Left panel**: folded to the strip at every width (Policies with Add document, Input parameters, Issued versions);
  no chat; hidden on phone.
- **Motion**: a row that changes group after a pointer decision enters its new place with the list enter (opacity +
  translateY 6px, `--dur-base`); keyboard decisions and reloads are still. The Supported fold and the reason editor use
  `Collapse` (pointer only).

## Loss runs (slice 5a, owner-approved 2026-11-04 12:40, direction A "Register and ledger" with B's footing)
See `frontend/LOSS-RUNS-BRIEF.md`. No new tokens.
- **Analytics** (`#/analytics`, rail item live, "Analytics: loss reviews"): loss reviews by client in the work-queue row
  pattern (analytics mark, serif client link, "8 files, valued as of 09/30/2025", "3 checks to resolve", claims and
  incurred, "Review losses"); groups In progress, Needs review, Needs attention, Ready, Done recently (folded). New loss
  review opens the wizard (Who, Files, Read); a queue row's menu offers "Review losses" with the client filled in, and a
  loss review's own queue row carries "Review losses" as its primary. Activity titles it "Loss review, run N".
- **Review page**: header names the client ("<client> · Loss review"), files, carriers, valuation date, run and version;
  "Synthetic sample data" as a stale-tinted tag; an "All claims / By file" switch (one joined track, the segmented
  look); "Mark review done" is the one primary, `aria-disabled` with "N checks to resolve" under it.
- **Register** (main column): the counted totals are the table's own footing row, pinned under the column names, each
  figure under its column, "56 claims, 33 open, valued as of ..." and an UNRECONCILED tag (stale tag colours); while a
  slow rebuild runs the figures dim and the tag reads "Recounting..." (primary tint). Recoveries and deductibles are one
  sentence above. Quick filters are toggle buttons with the server's counts (`filter_counts`); an empty filter says
  what it checked. Rows: one fixed 56px height (two lines: claimant and source chip; status word and row state), a
  window over server pages of 100, sort on every column head, older valuations folded under their claim (struck
  amounts on canvas), a row menu with the broker's decisions. Under 1280 the checks come first in two columns and the
  register takes the full width with Loss date hidden: no sideways scroll at 1024.
- **Chips** (D13): the letter and its hue, then a short label ("Driftwood p.2", "Sheet 2024, row 17", "Row 6" for a
  CSV); the full file and page are the tooltip and part of the accessible name. A chip swaps the check ledger for the
  cited page (a sheet chip opens its rendered sheet page) with "Back to checks" (Esc).
- **Check ledger** (right column, 380px): To resolve (need order: Off, Couldn't check, Conflicting, Not verified),
  Handled by you, Matches (folded, "14 printed lines, 46 totals"), notices. One card per printed line: its metrics as a
  small Printed / Rows add up to / Off by table and one Accept with a note. States, silhouette first:
  Off = not-equals glyph, "OFF BY $12,400.00", `--severity-critical-fg`, 3px solid rule; **Couldn't check**
  (`needs_review`) = dashed-circle glyph, "COULDN'T CHECK", `--severity-high-fg`, 3px *dashed* rule, "Not checked" where
  the sum would be and no difference; Conflicting = scales, text colour, the two files' figures side by side with
  "Use <file>"; Not verified = triangle (scan glyph on scanned rows); handled = stamp ("Accepted by you", "Picked by
  you") or signature ("Handled row by row"). Files are named for people: carrier word and valuation year ("Driftwood
  2025"), the file type only when two would read the same ("Ashgrove 2025 workbook" / "Ashgrove 2025 CSV").
- **A check's rows** (B's footing in A): "Show the rows" scopes the register and foots the printed line under the rows,
  pinned to the table's bottom: Rows above add up to / Printed with its chip / Printed minus rows.
- **By file**: one schedule per file (serif carrier name, need first, folded when it ties), rows in printed order and
  every printed total footed after the last row it covers ("Printed: Total, all policies" with MATCHES, or its sum,
  difference and actions).
- **Row states** under the status word: Not verified, Conflicting (and a 3px rule on the claim cell), Confirmed by you,
  Edited by you (the value read from the file struck beside the new one), Added by you ("No source"), Left out of
  totals, Incurred differs, Printed twice, Changed since you decided; more than one reads "+N" with all in the tooltip.
- **Actions**: Add a missed claim, Fix amounts, Confirm, Leave out (notes required for confirm and leave out) open one
  docked editor under the register, never a modal; Accept opens an inline note under its card. Every action applies at
  once (the row reads "Confirming..."), the toast names the result and offers Undo.
- **Phone (<720px)**: status only: header, the totals as a two-column list with "from N claims", and the open checks
  by word and title; no table, no actions, no Mark done button (its count stays).
- **Motion**: Collapse for the Matches fold and the note editor (pointer only); totals dim during a recount (opacity,
  150ms); keyboard changes are instant; reduced motion keeps the fades.

## Compare match (slice 6, lane B, 2026-11-05)
See `frontend/COMPARE-MATCH-BRIEF.md`. No new tokens.
- **Book homes**: `#/` is titled "Commercial" (it lists all commercial work) and shares one component with Personal lines:
  serif title, one muted line, primary "Compare quotes" (to `#/compare/new`) and secondary "Check a renewal"; no left
  panel. The empty state keeps a large drop zone that hands its files to the upload page.
- **New comparison** (`#/compare/new`): wizard shell, title "New comparison" with the book tab, crumbs follow the Line
  choice. "Compare N quotes" top right, `aria-disabled` with its reason under it below two ready quotes (R4). Quote rows:
  column letter, format glyph (Phosphor file family), name, "Word, 2 pages", Remove; a refused file keeps its row with
  the server's message.
- **Marks**: worse = value in a 1px `--severity-high-fg` box (4px radius) plus a WarningDiamond glyph whose tooltip is
  the reason (the DG3 triangle stays "Couldn't verify"); best = value in `--doc-3-fg`, semibold, plus the status word
  LOWEST. Both are in the cell's accessible name. Column heads carry a muted kind line ("Word quote", "Spreadsheet").
- **Key difference**: the report's first section, one `--color-canvas` panel (6px radius, no border, no side rule),
  serif title, 16px prose with a chip after each cited segment (kept on its last word's line), a 12px source line
  (rule or model). An edited cited cell shows an "edited by you" chip (surface fill, input border, pencil) that goes to
  the cell. Stale: the usual tag plus Rewrite.

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
