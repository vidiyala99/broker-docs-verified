# Compare match brief (slice 6, lane B, task T7)

Spec: `docs/specs/2026-11-05-compare-match-design.md` sections 4 and 5, overridden by its review ledger (R3, R4, R5, R10).
Owner decisions there are final and not reopened here. Evidence of the target behaviour is the BrokerDocs demo the spec
quotes ("Three carriers. Three formats.", "Drop them all in. No prompt needed.", "The lowest premium isn't the same
coverage."): a dedicated upload screen, weaker terms marked, the lowest premium highlighted, one Key difference. We match
the function and the hierarchy in our own system (DESIGN.md, Paper trail voice), never their look or words.

Design read: Operate mode. A broker at a desk with three carriers' files for one client wants them compared; the
comparison table is the product and the Key difference is the one sentence they read first. Everything else stays quiet
chrome. Dials: variance 3, motion 3, density 6 (a tool, not a landing page).

## Screens

1. **Commercial home `#/`** (was "Compare Quotes" with an Upload and an Input parameters accordion): it lists all
   commercial work (comparisons, loss reviews, policy checks), so it is titled by its book, like Personal lines.
2. **New comparison `#/compare/new`** (`?line=personal` from Personal lines): drop quotes in any of the three formats,
   see each file's format and any refusal, name the client, pick the book, optional Additional context, Compare.
3. **Comparison report**: column format labels, the four new term rows (backend), worse and best marks, the Key
   difference callout, Word and Excel sources.
4. **Pickers outside Compare and Proposal Builder** hide Word and Excel (R5).

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | Home title "Commercial", one muted line under it, then the actions: primary **Compare quotes** (to `#/compare/new`), secondary **Check a renewal**. Personal lines gets the same header component: its "Compare homeowners quotes" becomes the primary and goes to `#/compare/new?line=personal` | The page lists every kind of commercial work, so a workflow name as title was wrong. One component for both books keeps them consistent (spec 4.2) |
| 2 | Home has no left panel (the Upload and Input parameters accordions are gone; the empty chat "Run a comparison to ask questions" went with them) | Same as Personal lines; a chat with nothing to ask about is a dead control |
| 3 | Empty home keeps a large drop zone; dropped files go into a shared upload store and the page moves to `#/compare/new`, where they keep uploading | Spec 4.2; the store is a module singleton, so the rows and their progress survive the route change |
| 4 | `#/?line=personal` (old entry) redirects to `#/personal-lines`; run crumbs "Compare Quotes" and "New comparison" go to `#/compare/new` (with the book preset) | One home per book; the workflow crumb opens its start screen, as "Policy check" does |
| 5 | Upload page uses the wizard shell (title "New comparison" with the Compare mark and the book tab, hairline head, scrolling body), named like "New policy check". Crumbs lead with the chosen book: "Commercial > Compare Quotes > New comparison", following the Line choice like the Policy Check wizard | Reuses the line-switch pattern (crumb and book tab follow the choice) instead of inventing one |
| 6 | **Compare N quotes** is the one primary, top right of the head (spec 4.1). Below 2 ready files it stays focusable with `aria-disabled` and the reason under it: "Add two quotes to compare.", "Add one more quote to compare.", "Wait for the uploads to finish."; pressing it then turns the reason into an alert. Once ready the same line reads "Columns A to C, in the order added." so the head never changes height. Ghost **Cancel** beside it | The certificate Issue gate pattern: a disabled button that says why. R4: the minimum lives on this page only |
| 7 | Body: files first (drop zone "Upload quotes", "PDF, Word and Excel quotes, proposals and summaries", limits line; large while empty, compact once a file is listed), then the file list; details (Client, Line of business, Additional context) in a 320/340px column from 1000px, under the files (capped at 560px) below that | The files are the task; the details are optional. Two columns while the file list still gets 500px or more (1024 included) |
| 8 | File rows: document letter (ready rows, in order; the letter is the column letter), a format glyph (Phosphor FilePdf / FileDoc / FileXls), file name, then "Word, 2 pages" / "Excel, 1 page" / "PDF, 4 pages", Remove. Uploading: progress bar. Refused: danger glyph and the server's `rejected[]` message in full on its own line, no letter | Spec 4.1 "format icon, status, remove"; letters show list order = column order; refusals are honest and per file |
| 9 | Additional context: labelled textarea, optional, spec placeholder, live count "0 of 1,000 characters", `maxLength` 1000 | Spec 4.1; the count is text, not a bar |
| 10 | Column head: a muted 12px line under the carrier: "PDF quote", "PDF proposal", "PDF summary", "Spreadsheet", "Word quote", "Word proposal", "PDF" / "Word document" when the kind is other or missing | Spec 5 labels verbatim |
| 11 | **Worse** cell: the value sits in a 1px `--severity-high-fg` outline (4px chip radius) with a 14px WarningDiamond glyph; the glyph carries the tooltip (`mark_reason`); the cell's accessible name adds "worse than other quotes: <reason>" | Spec 5. The diamond is not the triangle: DG3 keeps the triangle for "Couldn't verify", so the two never share a silhouette. Amber-brown, not coral (document 2's hue) or danger red (an error) |
| 12 | **Best** premium: value in `--doc-3-fg` (the success green used for "Every check is handled"), semibold, plus the plain uppercase status word LOWEST after it; accessible name adds "lowest premium" | Spec 5 says success colour; DESIGN.md's status-word rule (plain uppercase bold, no pill) gives it a second, non-colour signal |
| 13 | Marks never fade with the evidence filter: a filter only tints matching cells, so marks stay as drawn; phone layout keeps the same markup | Spec 5 "marks show under every evidence filter and in the phone layout" |
| 14 | **Key difference** callout: the first section, drawn as one `--color-canvas` panel (6px radius, no border, no shadow, no coloured side rule), serif "Key difference" heading, the sentence in 16px report prose with chips after each cited segment, and one 12px muted source line: "Written by rule from the marked cells." (template) or "Drafted by the model and checked against the marked cells." (model) | The slice's headline gets the page's one spend of emphasis, in the existing paper/canvas language. impeccable refuses coloured side rules on callouts |
| 15 | Segments join with a space unless either side already has one or the next starts with closing punctuation; a segment's chips stay on the line of its last word (no chip alone at the start of a line) | Segments may carry no trailing whitespace (eval report read "$14,490.00.B ("); template segments that do carry it are not doubled |
| 16 | A cited cell renders its current passage chips; an edited cited cell renders an "edited by you" chip (hairline, pencil glyph) that scrolls to and focuses that cell; a cell with neither renders nothing | R10 |
| 17 | Stale: the existing stale tag "May be out of date" plus a **Rewrite** link (runs the existing regenerate) in the callout's head | R3 and spec 3.3; same tag as every stale section |
| 18 | Word and Excel sources: chips open the rendered page as for PDFs; the chip tooltip adds "Rendered from the Word file" / "Rendered from the Excel file"; the source pane head and the document tab show the format glyph and a **Download original** link (`GET /api/documents/{id}/original`) | The record is a PDF we drew; saying so, and giving the original, keeps the evidence honest |
| 19 | R5: Policy Check's "Choose from uploaded files" lists PDF records only; the Proposal picker keeps Word and Excel (Build proposal needs them). The certificate picker already hides them (lane A1) | R5 |
| 20 | Motion: none new. Existing press scale, route cross-fade, list enter on rows and progress bars apply | Operate mode; a mark must not draw attention to itself by moving, and no new moment is needed |

## What each skill said (before building)

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design |
|---|---|---|---|---|---|---|---|
| 1-2 Home retitle, no left panel | Router only; voice rule: plain words, no em dashes | Navigation shows where you are; labels match content | Copy self-audit: a title that names one workflow on a page of all work is wrong copy; no duplicate CTA intent | Indicate the current page; remove dead controls | Refinement preserves the incumbent world; Operate: scanability first | Unseen details compound: the dead chat was one | An action keeps its name: "Compare quotes" on home, "Compare 3 quotes" on the page that starts it |
| 3 Shared upload store | n/a | Loading feedback must persist (progress rows) | Interactive states: loading, empty, error | No dead ends in flows | States are part of the floor | Handle edge cases invisibly (uploads keep going across a route change) | Empty screen is an invitation to act: the drop zone |
| 5-6 Wizard shell, gated primary | n/a | 44px targets; disabled state explains itself; error near the control | Button contrast; CTA never wraps; one primary | Disabled things look disabled; no `#` links | Wayfinding reuses the incumbent tab and crumb | Never animate keyboard actions | Name the consequence ("Compare 3 quotes") |
| 7-9 Files first, rows, context | n/a | Visible labels, helper text, errors below the field; mobile-first collapse | Label above input; no placeholder-as-label; collapse declared per breakpoint | Tabular numerals for page counts; grid, not flex math | Run the real copy at every width | Progress bar animates transform only | Structure is information: the letter says the column |
| 10-13 Labels and marks | n/a | Never rely on colour alone; icon buttons/glyphs need names | One accent: the marks use existing status tokens, no new hue | Consistent glyph family (Phosphor) | Status colour tinted from the palette, not grey | No motion on marks | Spend boldness in one place (the callout), keep marks quiet |
| 14-17 Key difference | Plain sentence copy | Callout readable at 16px, 4.5:1 | No coloured glows, no gradient; cards only where hierarchy is real | Avoid generic card (border + shadow): fill only | Refuses a coloured side rule on callouts; one authored emphasis | Cohesion: same stale tag and chips as the rest of the report | The memorable thing is the sentence itself, typeset in the report's prose |
| 18-19 Sources and pickers | n/a | Downloads labelled by what they fetch | n/a | No dead links | Product truth: say the page was drawn from Word/Excel | n/a | Name things by what the user sees ("Download original") |
| 20 No new motion | n/a | Motion conveys meaning only | Motion must be motivated | Transform/opacity only | One authored moment or none | Frequency: a broker sees marks all day: no animation | Motion answers an action; none is needed here |

## Design review rounds (Playwright MCP, 3 of 3 used)

1. **1440, 1024, 390, live seed.** Found and fixed: a refused `.doc` row drew the PDF glyph (unknown formats now draw
   Phosphor's plain file); the head shrank 20px when the gate line disappeared (the line now always holds text); the
   large drop zone pushed the list below the fold once files existed (compact after the first file); a Key difference
   chip could wrap alone to the next line (chips now stay with the segment's last word); a multi-line worse value drew
   one outline per line (now one box); at 1024 the details column dropped under the files with an 870px-wide Client
   field (two columns from 1000px, details capped at 560px when stacked). Shots `r1-*`.
2. **States.** Edited A's premium through the cell editor: the best mark moved to A at once, the Key difference showed
   "May be out of date" with Rewrite; Rewrite rewrote it from the marks and cited A as "edited by you"; that chip
   scrolled to and focused the cell. A Word chip opened the rendered page with "Download original" in the pane head.
   No new findings. Shots `r2-*`.
3. **e2e captures at 1440/1024/390** (`e2e-*`): one outlined box per multi-line worse value, chips and glyph under it;
   the upload page at 390 keeps every row's refusal readable. No new findings. impeccable `detect` on the changed UI:
   0 findings.

Known and left as they were (not this lane): at 390 the report's sticky first column covers most of a 3-column table
(marks are there on sideways scroll); the outline handle floats over the first section's top-right corner on phone;
the run view briefly shows "No report was written" before the artifact loads.

Screenshots: `frontend/qa-screenshots/compare-match/` (`before-*`, `r1-*`, `r2-*`, `e2e-*`).

## Lane choices the owner may veto

- Crumbs on the upload page use the book names DESIGN.md mandates ("Commercial > Compare Quotes > New comparison"),
  not the spec's "<Line> Insurance > Compare Quotes" wording; the page title is "New comparison".
- The empty home (both books) starts a comparison from its drop zone; its "Compare quotes" link is not repeated there.
- Proposal Builder's "Choose from uploaded files" names Word and Excel records ("Word, 2 pages").
- Inputs parameters on a Compare run show its Additional context when one was given.
- Home title "Commercial" with the line "Business policies: liability, property, commercial auto and workers' comp.";
  Personal lines keeps its title and line; its compare button became primary.
- Home has no left panel; the empty chat panel is gone.
- `#/?line=personal` redirects to `#/personal-lines`.
- Compare button reads "Compare N quotes" (spec: "Compare").
- Best premium carries a LOWEST status word in addition to the success colour.
- Worse glyph is WarningDiamond (not the DG3 triangle).
- No reorder control on the file list: order is the order added (remove and re-add to change it).
- Additional context counter shows "N of 1,000 characters".
- Word file of kind summary reads "Word summary" (spec lists only quote/proposal/other).
