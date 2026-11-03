# T9 design brief: Policy Check UI, slice 3

Written before building. Sources: spec `docs/specs/2026-11-02-policy-check-design.md` (section 6; the decision
ledger D1 to D17 overrides the text above it), `backend/API.md` (slots, `POST /api/documents/ams`,
`DiscrepancyBlock`, slot states, GET/PATCH discrepancies), `backend/schemas.py`, `DESIGN.md`, `tokens.css`, the
prior briefs (`DESIGN-BRIEF.md`, `WIZARD-BRIEF.md`, `LEFT-PANEL-BRIEF.md`) and the live payloads of the seeded
Policy Check runs 7 and 8 (`python -m backend.dev_seed seed`: 10 items in run 7, one resolved, one changed since
resolved, one needs review, a scan-read quote, an AMS export; run 8 has a quote that was not read and no AMS).

Skills loaded before the first line of UI code: gstack, ui-ux-pro-max, taste-core, taste-redesign, impeccable,
emil-design-eng, frontend-design. `impeccable context` reported `NO_PRODUCT_MD`; this is a scoped addition to
existing code, so it proceeds on DESIGN.md as the visual authority (PRODUCT.md still missing, flagged again).

**Design read (taste-core 0.B):** Operate mode. A broker checks a renewal against the expiring policy, an
optional quote and an optional AMS export, and works a short list of differences down to zero. DESIGN.md pins
Inter, `#6356F0`, 6px controls and 4px chips, Phosphor regular, hairlines with no shadows, motion tokens only.
Those are not open.

**Dials (taste-core):** variance 3, motion 2, density 7. Same family as T10 (3/2/7); this is a review list.

**Where the boldness goes (frontend-design):** the four-slot comparison row. Every card carries the same four
columns in the same order (Expiring, Quote, Renewal, AMS), each value with its citation chip, so the eye reads
down a column across cards. The renewal column is the document being checked (D10, renewal-centred), so it
gets the one emphasis: the selected-row tint (`--color-primary-tint`) behind its slot. Everything else stays
quiet.

## Layout

```
Rail [Policy check active]   Top bar: BrokerDocs / Policy check > New check
New policy check                                         (36px page title)
[1 Documents]--[2 Roles]--[3 Check]                       (stepper, shared with proposals)
Step 1 Documents                          Step 2 Roles (one row per role)
 Policy PDFs   [drop zone]                 Expiring  Required  [select: pc_expiring.pdf    v]
   A pc_homeowners_expiring.pdf  3 pages   Renewal   Required  [select: pc_renewal.pdf     v]
   B pc_homeowners_renewal.pdf   3 pages   Quote     Optional  [select: Not used           v]
 AMS export    [drop zone: JSON or CSV]    AMS       Optional  [select: ams.json, 12 fields v]
   ams.json  12 fields, 2 not compared     "Run check" posts the run and opens the run view (step 3)
[Cancel]                         summary                            [Continue] / [Run check]

Run view (existing shell: left panel with Upload rows + role, chat; toolbar; report area)
 notices (AMS fields not compared ...)
 10 discrepancies, 1 resolved                                    [Review one at a time]
 [All 10] [Critical 2] [High 5] [Medium 2] [Low 1]   [x] Hide resolved
 +---------------------------------------------------------------------------------+
 | |||| CRITICAL   Coverage C (Personal Property) decreased by 15.0%        Open    |
 | Expiring states $200,000; renewal states $170,000, a decrease of $30,000 (15.0%).|
 | Expiring A      | Quote B           | Renewal C (tinted) | AMS D              |
 | $200,000  [A1]  | $170,000 [:B1]    | $170,000  [C2]     | $170,000 [AMS · Cov C] |
 |                 | Read from scan,   |                    |                    |
 |                 | unverified        |                    |                    |
 | [Mark resolved] [Add note] [Review]                                             |
 +---------------------------------------------------------------------------------+

Review dialog (native <dialog>, near full viewport)
 Discrepancy 3 of 10     [< Previous] [Next >]                                 [x]
 +-- detail (420px) ----------------+-- source --------------------------------+
 | severity, title, explanation     | Source: C. pc_homeowners_renewal.pdf p.2 |
 | slot list, one row per role:     | [PdfViewer at the page, dashed box on    |
 |   role, value, chips (a chip     |  the cited fact; AMS: box on the field   |
 |   shows that source on the right)|  line of the rendered AMS page]          |
 | resolve control + note           |                                          |
 +----------------------------------+------------------------------------------+
Phone 390: slots 2 x 2 on cards; dialog full screen, source stacked under the detail.
```

## Decisions and what each skill said

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design |
|---|---|---|---|---|---|---|---|
| Entry: "Policy check" rail item goes live (`#/policy-check/new`, section `policy-check`, `is-active` + `aria-current="page"` on the wizard and on Policy Check runs); crumbs "Policy check > New check" / "Policy check > Run 7"; other placeholders stay disabled | Plain words, no em dashes | "Predictable back, deep linking": the hash route deep-links like proposals | One nav pattern, no new chrome | "Indicate current page in navigation" | Operate: earned familiarity, same as Proposals | No motion on navigation | n/a |
| Wizard: reuse the proposal `Stepper` (generalised to take its steps), three named steps Documents, Roles, Check; Check is the run view, reached only through "Run check"; draft kept in `sessionStorage` until Run check | n/a | "Show progress for multi-step processes" | Named steps, no "Step 1" labels; numerals stay because it is a sequence | Every flow needs a way back: Back plus clickable done steps | Consistent vocabulary with the proposal wizard | Step change instant (often keyboard) | Numbering only for a real sequence: it is |
| Documents step: two drop zones, Policy PDFs (`POST /api/documents`) and AMS export (`POST /api/documents/ams`, `.json,.csv`), each with its own limits line; rows show letter, filename, pages; AMS rows show "12 fields, 2 not compared" from the `ams` summary; inline rejections name the limit; "Choose from uploaded files" picker reused | No AI vocabulary | Error near the field, never colour alone, 44px targets | Labels above inputs, inline errors, full state cycle (uploading bar, rejected, empty) | No `alert()` | Controls name their action ("Choose files", "Choose AMS file") | Upload progress bar is the only motion (existing scaleX) | Helper text in the broker's terms: "An export of the policy record from your agency system, as JSON or CSV." |
| Roles step: one row per role (Expiring, Renewal required; Quote, AMS optional), each a native `select` of the eligible documents (PDFs for the three PDF roles, AMS exports only for AMS; a document already used by another role is shown as "(used as Renewal)" and disabled); a first guess from file names (`expiring`, `renewal`, `quote`, AMS kind) that the user confirms; errors inline under the row, plus 400 `invalid_run` from the server shown in place | Errors in plain words | Native select is the accessible choice; label tied to control; error below with `aria-describedby` | No custom dropdown when a native one does the job; no decorative dots | Form contrast: input border `#848D99` | Inline, not a modal; required vs optional said in words, not only an asterisk | No animation | "Required" and "Optional" are words; the role's one-line purpose sits under its name |
| List header: "10 discrepancies, 1 resolved" (status line, `role="status"` on change) and "Review one at a time" (secondary button; primary stays with the per-card action) | n/a | "Contextual live updates: one atomic status message", not a bare number | One CTA per intent | Sentence case | Scanability first | n/a | Words do one job each |
| Severity filter: toggle buttons (`aria-pressed`) "All 10", "Critical 2", "High 5", "Medium 2", "Low 1", multi-select, wrap on small screens; zero-count tiers stay visible but disabled; "Hide resolved (1)" is a real checkbox | n/a | "Chip collection reflow: wrap, never clip"; chips that act are buttons, badges that state are spans | No pills: chips use the 6px control radius and hairline, pressed = primary tint + primary border + check glyph | Hover/active/focus states required | Filters are local and instant (client side over the GET list; counts come from the server and never change with filters, per API.md) | No animation on toggle | n/a |
| Severity mark: the word in plain uppercase bold (DESIGN.md "status words stay plain uppercase bold, no pills") plus a four-tick meter glyph (4 ticks Critical, 3 High, 2 Medium, 1 Low) so tier reads without colour; new tokens `--severity-*-fg` (Critical uses the danger red, High the stale amber-brown, Medium and Low neutral text) | n/a | Never colour alone; 4.5:1 text | Max one accent: severity colours are status, kept to two tinted words; no coloured dots | One gray family | Brand lives in precise details | n/a | Structure is information: the tick count is an ordinal scale, so a sequence mark is honest |
| Card: `article` on a hairline, no shadow, no card stack effects; title is an `h3`; explanation as prose; four-slot grid always in role order, a missing role still takes its column ("N/A"), so columns line up down the list | n/a | Consistent alignment across repeated items | Cards only when they group one unit: each discrepancy is one decision, so one bordered unit is earned; no 3-equal-card rows | Align shared elements across side-by-side items | Operate: same place, same thing, every time | n/a | The four-slot row is the anchor |
| Slot states: value + chip(s) (verified solid chip; scan read dashed chip + "Read from scan, unverified" under the value); "N/A" muted with sr text "no document" or "not stated"; "Not read" with the refresh glyph (D11, never N/A); "Needs review" with the triangle glyph and the conflict list ("$300,000 (page 1) or $500,000 (page 1)") or "Couldn't verify" | n/a | Each state has a text label, glyph second | Silhouette first (DG3 rules reused) | n/a | States are vocabulary: reuse the report's evidence silhouettes exactly | n/a | n/a |
| AMS chip: reads "AMS · Cov A". The field name is the text before ": " in the verified fact's quote (the rendered AMS PDF prints one line "field_name: value"); the backend adds `sources` per slot to the GET/PATCH item (passage, page, the fact's own rects, `field` for AMS documents). Chosen over a page-style chip because the field is what a broker recognises in the AMS export, and it still opens the rendered AMS page at that field. Fallback when no field is known: the normal page chip | n/a | Accessible name carries the full source ("Source AMS field Cov A, pc_homeowners_ams.json, page 1") | One middle dot per line at most (taste-core 9.F): exactly one | n/a | Same chip silhouette, label changes | n/a | n/a |
| Highlight: every chip in the Policy Check view opens the source with the fact's own rects (not the page's union of all facts), so on the AMS page only the cited field line is boxed, and on a two-column schedule only the cited value | n/a | n/a | n/a | n/a | Precision is the showcase (spec aim: citation engine) | Existing 200ms side-by-side transition | n/a |
| Resolve control: "Mark resolved" / "Reopen" (PATCH status), "Add note" / "Edit note" opens an inline textarea (2000 char counter, Save / Cancel, PATCH note), saving state on the button, inline "Couldn't save. Retry" on error; resolved items show a check glyph + "Resolved" and a muted title; no bulk actions, no "accepted" (D1, D2) | n/a | Loading feedback on the control; error near the control | Full state cycle; inline errors, toasts only for transient | No dead ends | Inline editing, not a modal | Press scale 0.97 only | The action keeps its name: "Mark resolved" -> toast-free inline "Resolved" |
| "Changed since resolved": stale tag (`--color-stale-*`, clock glyph) "Changed since resolved" next to the status, with the hint "The values changed after you resolved this. Check them again." | n/a | Glyph + text, not colour alone | Glyph + text, no pill | n/a | Reuses the "May be out of date" silhouette: same meaning, same look | n/a | n/a |
| Review dialog: native `<dialog>` with `showModal()` (focus trap, Esc, inert page); "Discrepancy 3 of 10" heading over the filtered list; Previous / Next buttons (and Left / Right arrow keys when focus is not in a text field); left: the item with slots as rows; right: the cited source in the existing `PdfViewer` (first cited slot, renewal first); a chip in the dialog switches the right pane instead of opening a tab; focus returns to the opener on close | n/a | Focus visible inside the modal, focus not obscured, Esc closes | Modal only where it earns it: one-at-a-time review is a focused mode | Modals not for everything: cards stay inline-editable; the dialog is an optional mode | Operate: a review mode should be fast to step through | Modal: centred, opacity + scale 0.98 at 200ms on pointer open, none on keyboard open or Prev/Next (keyboard, frequent) | One orchestrated moment: the dialog open, nothing else moves |
| Phone 390: slot grid 2 x 2 (Expiring, Quote / Renewal, AMS), filters wrap, card actions wrap with 44px targets; dialog full screen with the source stacked under the detail and a "Show source" jump link | n/a | 44px targets, no horizontal scroll | Mobile collapse declared per layout | No horizontal page scroll | Responsive is structural | n/a | n/a |
| States: loading skeleton cards; list error with Retry; no discrepancies ("No discrepancies found..." from the report paragraph, with the documents compared); filters match nothing ("No discrepancies match these filters." + Show all); run still running uses the existing placeholder | n/a | Skeleton over spinner | Empty states say how to proceed | Loading, empty, error required | Every state designed | n/a | Empty screens point to the next action |
| Copy | No AI vocabulary, no em dashes | Visible labels | Zero em dashes | Sentence case, no exclamation marks | Name actions plainly | n/a | "Run check", "Mark resolved", "Reopen", "Add note", "Review one at a time", "Previous", "Next" |

## Deliberate choices against a skill

- taste-core and taste-redesign push away from Inter and violet; DESIGN.md pins both (override path, as in T10 and T7).
- taste-core section 13 puts dashboards and multi-step product UI out of its scope; only its state, shape, copy,
  dot and em dash locks were applied.
- taste-core bans pills; severity filters are toggle buttons with the 6px control radius (not full round), and
  severity on cards is a plain word plus glyph.
- The brief asks for "AMS · field_name" while ledger D4 A replaced field-name chips with the rendered PDF path.
  Both hold here: the chip opens the rendered PDF (D4 A) and its label carries the field name read from the
  verified quote. Owner call.
- ui-ux-pro-max would add swipe between items on phone; not built (Prev/Next buttons work at every width).

## Review rounds (Playwright MCP on the live page, 1440 / 1024 / 390)

1. Build and drive: list, filters, resolve, note, review dialog (pointer and keyboard), AMS field highlight,
   wizard with AMS upload and roles, Not read run. Fixed: focus fell to the page after an arrow-key step in
   the dialog (now the "Discrepancy n of N" heading takes it); at 390 values broke mid-number in a 2 x 2 slot
   grid (slots now reflow by the list's own width: 4 columns, 2 x 2, then one per row); the narrow-screen
   notice talked about tables on a view without tables (hidden on Policy Check runs); "Not read" now shows
   the failed document's reason (the run notice now reads "the Upload panel shows why", true for both views).

Fix pass after round 3 (not a design round): the source viewer drew the new chip's box over the previous
document's page while the new file loaded (seen in `1440-review-ams.png`: AMS name and box over the renewal
page). `PdfViewer` now pairs the loaded document with its id and shows the skeleton until this document's
page is drawn; e2e holds the AMS file back to prove the gap, then checks the drawn document is the
rendered AMS export and the box matches the cited line. The AMS conflict notice names the field label.
2. Fresh-eyes pass: opening a source from a card reflowed the list into the split and scrolled the card
   away (the card is brought back into view once the split settles). Contrast figures re-measured
   (High tier 7.52:1).
3. Gates only: tsc clean, build ok, e2e 10/10 with axe 0 at 1440/1024/390, backend offline suite green.
