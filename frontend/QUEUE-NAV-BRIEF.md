# Queue navigation and client naming brief (2026-11-03)

Owner, on the Work queue: "I see duplicates" (Cedar Hollow Hospitality LLC several times) and "no way to navigate to
the comparison, it forces me to build proposal"; "This should be easy for the broker to navigate to, without
overload." Approved scope, five items, nothing more. Sources: `PRODUCT.md` (clients and next actions, one next action
per item, no em dashes), `DESIGN.md` (Work queue, Voice, Motion), `QUEUE-BRIEF.md`, the work queue spec ledger.

Skills loaded before the first edit: gstack, ui-ux-pro-max, taste-core, taste-redesign, impeccable, emil-design-eng,
frontend-design. Mode (impeccable): Operate, a refinement inside the incumbent "Paper trail" world, so it preserves
identity, copy voice and layout. Design read (taste-core 0.B): broker work queue for account managers, calm and
paper-literate, leaning on the existing tokens; dials variance 2, motion 1, density 5 (unchanged from QUEUE-BRIEF).
gstack routed this to its design-review discipline: drive the live page, bounded rounds (3 max), evidence by screenshot.

## 1. The client name is the link to the report

- Built: the serif client name on every row is an `<a href="#/runs/<id>">`. No new button, icon or line; the row keeps
  its one primary action. The quiet "Open" link on rows with no next step is removed: it went to the same place as
  the name, so it was the same stop twice in the tab order (In progress, Done recently, non-retryable Needs attention).
- Look: text colour, a 1px resting underline in `--color-border-input` that turns `currentColor` on hover (pointer
  only), the existing row tint, focus ring on `:focus-visible`. Whole-row click not added.
- Accessible name: visible name first (label in name), then screen-reader-only context: "Cedar Hollow Hospitality LLC,
  comparison started Nov 3, 10:06 AM, Harborline, Northbeam and 2 more". When two rows would still read the same,
  ", run N" settles it. Focus order per row: name, primary action, menu.
- impeccable: refinement preserves; the name already is the row's identity, so making it the way in adds no chrome.
- ui-ux-pro-max: accessible names unique and specific (ARIA labels); do not rely on hover only, hence the resting
  underline rather than hover-only; keyboard order follows reading order.
- taste-core: no new CTA, "no duplicate CTA intent" (one route to the report per row, the menu item aside); cards and
  extra buttons omitted in favour of the existing hairline list.
- taste-redesign: work with the existing stack and classes, small targeted change; visible focus ring kept.
- emil-design-eng: hover is a colour change, so `ease` on `--dur-fast`, gated behind `(hover: hover) and
  (pointer: fine)`; nothing animates for keyboard.
- frontend-design: structure is information; the link affordance is quiet so the one bold thing in the row stays the
  status detail (QUEUE-BRIEF's choice).

## 2. Menu copy

- "Open run" is now "Open comparison" (Compare Quotes), "Open policy check" (Policy Check), "Open report" otherwise.
  "Open failed proposal" stays. Checked the other queue strings: the footer "See every run in Activity" stays,
  because Activity is where raw runs live (PRODUCT.md principle 1) and "run" is the honest word there.
- frontend-design (writing): name things by what the user understands; a broker opens a comparison, not a run.
- impeccable: ask before replacing factual copy; this replacement is in the approved scope, the footer is not.

## 3. The meta line: when, and which carriers

- Every row: the date the work started, from the item's new `created_at`: "Nov 3, 10:06 AM" (locale; the year only
  when not this year). Desktop: "Compare Quotes · 2 carriers   Nov 3, 10:06 AM" (gap, not a second middle dot).
- When a client name shows on more than one row of the queue, a comparison's line becomes its carriers instead of the
  count: "Harborline, Northbeam and 2 more   Nov 3, 10:06 AM". Short names drop legal and line-of-business words
  ("Ridgecrest Mutual Casualty Company" is Ridgecrest), at most two names, "and N more" counts every other quote,
  named or not (`carriers` has one entry per document, `null` when no verified carrier).
- One line: the line no longer wraps; what and which carriers give way first (ellipsis), the date never truncates.
  The "name from the documents" tag moved beside the name it qualifies, so it no longer competes for this line.
  The identity column now takes 3fr to the status column's 2fr (status texts are short).
- Phone (390): the column is about 155px. The workflow word and the time are dropped there (the row's mark already
  names the workflow; the phone is for status, PRODUCT.md), so the line reads "2 carriers   Nov 3" or
  "Harborline, N...   Nov 3". Full detail stays in the link's accessible name.
- taste-core: middle dot rationed to one per line; no fake-precise data (every value is the run's own); one copy
  register. ui-ux-pro-max: no horizontal scroll at 390, tabular numerals already on the line. frontend-design:
  one or two type families only, unchanged; the date is Inter (the system), the name serif (the paper).
- emil-design-eng: no motion added; the line is static information.

## 4. Seed

- Run 3 (budget) has a typed name now (Halvorsen Dental Partners). Run 5 keeps the documents' name (Cedar Hollow, the
  fallback tag); run 6 keeps its typed Cedar Hollow (it is the Cedar Hollow file). So Cedar Hollow shows twice, and
  item 3 tells the two rows apart. Owner may veto: make run 6 a different client too.

## 5. Report header and crumbs

- A run with a client (typed, or the same fallback the queue uses, with the same tag) is titled "Cedar Hollow
  Hospitality LLC · Compare Quotes"; under it, in Inter at text-xs: "name from the documents" (when read) and
  "Run 5, version 1". Crumbs end with the client; the tab title is "<client>, Compare Quotes". The client button reads
  "Change" (screen reader: "Change client: <name>") when a name exists, "Add client" when none does. Runs with no name
  keep "Compare Quotes, run N". Proposals keep their own title.
- Backend: `GET`/`PATCH /api/runs/{id}` (and the other single-run responses) carry `client` and `client_source` by the
  queue's own rule (`work_queue.run_client`); documented in `backend/API.md`, tested.
- The title keeps the client whole before anything else: the report actions wrap to the toolbar's second row (as they
  did at 1440 before) and at 1024 the workflow word is what gives way; on the phone the name wraps to two lines.
- Checked on review (coordinator, from after-1440-report.png): the report actions on a second toolbar row at 1440 are
  not new. `qa-screenshots/queue-nav/before-1440-report.png`, captured from main's code before any edit, shows the
  same two rows (title, Line and the client button; then the icons). Fitting all on one row would leave the client
  about 240px at 1440 and almost nothing at 1024, so the wrap is kept; the header only grew by the 16px meta line.
- impeccable / taste-redesign: header only, no page redesign; existing toolbar, tokens and type sizes.
- frontend-design: the client is the subject in the broker's head (PRODUCT.md), so it leads; the run number is
  metadata, small and muted.

## Review rounds (Playwright MCP, 1440 / 1024 / 390)

- Round 1 found: the carriers truncated right where they disambiguate (identity column too narrow at 1440, the
  live grid rule lives in `voice/voice.css`); the tag forced a wrap; the header title squeezed to "Cedar Hollow
  Hospit..." beside Line, Change and the actions; the phone meta line was all date. Fixed: 3fr/2fr columns, carriers
  replace the workflow word on shared rows, tag beside the name, title flex-basis 24rem, phone drops workflow and time,
  phone title wraps.
- Round 2: hover (underline darkens, row tint), keyboard focus ring and Tab order (name, action, menu) checked; axe 0
  on the queue and the report page at all three widths; no horizontal scroll.
