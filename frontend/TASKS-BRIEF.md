# T7 design brief: Tasks, slice 3b

Written before building. Sources: spec `docs/specs/2026-11-02-rail-completion-design.md` (2.2, 2.3, 2.5, R3, R4, R5,
Decision ledger D2, D3, D6, D7, D8, O3, O4, O7, task T7), `backend/API.md` (Tasks, discrepancy item `task`),
`DESIGN.md`, `tokens.css`, `HANDOFF.md` Gotchas and the prior briefs (`PERSONAL-LINES-BRIEF.md`,
`INTEGRATIONS-BRIEF.md`, `POLICY-CHECK-BRIEF.md`). There is no BrokerDocs screen for Tasks (the slice map has only an
endpoint string), so this is designed from the system, not from a reference.

Skills loaded before the first line of UI code: gstack, ui-ux-pro-max, taste-core, taste-redesign, impeccable,
emil-design-eng, frontend-design. `impeccable context` reported `NO_PRODUCT_MD`; this is a scoped addition to existing
code, so it proceeds on DESIGN.md as the visual authority (PRODUCT.md still missing, flagged again).

**Design read (taste-core 0.B):** Operate mode. A broker keeps a short list of follow-ups that come out of the checks
they run ("Follow up: Coverage A decreased by 14.3%"). The job is: see what is due, tick it off, jump to the run or the
discrepancy it came from, and get back. Tasks are a list beside the work, not a project tool (R3: no notes, assignees,
priorities). DESIGN.md pins Inter, `#6356F0`, 6px controls / 4px chips, Phosphor regular, hairlines with no shadows and
motion tokens only. Those are not open.

**Dials (taste-core):** variance 2, motion 1, density 6. A working list inside an existing tool; a little denser than
Personal lines because each row carries four facts.

**Where the boldness goes (frontend-design):** the linked-item chip. It is the one thing a task list here has that a
generic to-do list does not: a live link back to the evidence, carrying the discrepancy's own state ("Coverage C
decreased by 15.0% · Open"). Everything around it stays quiet.

## Layout

```
Rail [Tasks active]   Top bar: BrokerDocs / Tasks
+------------------------------------------------------------------------------------+
| Tasks                                                              [+ New task]    |
| 2 open, 1 done                                    (14px muted, live count)         |
| [Q Search by title or run.........]   Show [v Open][Done][All]                     |
|                                                                                    |
| [ ] | Title ^v                      | Due date ^ | Linked to                 | Created v | ... |
| [ ] | Send the Cedar Hollow summary | Oct 30     |                           | Nov 2     | ... |
|     |                               | ! Overdue  |                           |           |     |
| [ ] | Follow up: Coverage C ...     |            | Policy check, run 7       | Nov 2     | ... |
|     |                               |            | [Coverage C ... · Open]   |           |     |
+------------------------------------------------------------------------------------+
Phone (<700px): same table element, rows reflow to a two-line block per task
  [ ] Title                                     ...
      Due Oct 30  ! Overdue
      Policy check, run 7  [Coverage C ... · Open]
  and the column headers give way to a "Sort by" select (the headers stay in the
  accessibility tree, visually hidden).
```

- Route `#/tasks`, deep link `#/tasks?task=<id>` (O7): after load the row is scrolled into view, tinted
  (`--color-primary-tint`, the "selected row" token) and its title button takes focus. A task hidden by the filter
  switches the filter to All. An id that no longer exists shows "That task no longer exists." above the table.
- Rail item Tasks live (`#/tasks`); crumbs `Tasks`; document title "Tasks". Full-width pane like Personal lines and
  Integrations, no left panel, no "Best viewed on a wider screen" note.
- Toolbar: search (client-side, case-insensitive substring over title and `run_label`, live), Show Open / Done / All
  (default Open), New task (primary, top right next to the title). With no tasks at all the search and filter are
  pointless, so the toolbar is not drawn and the empty state carries the one "New task" button (no duplicate CTA).
- Count line: "2 open, 1 done" over all tasks (does not move with the filter), `role=status`.
- Table columns: done checkbox, title, due date, linked to, created, row menu. Sortable headers (title, due date,
  created) are buttons inside `th` with `aria-sort` on the active one and a caret glyph showing direction; inactive
  sortable headers show a faint two-way caret. Default: due date ascending, tasks without a date last (in both
  directions). First click: title A to Z, created newest first, due soonest first; a second click reverses. Ties: newest
  created, then id.
- Done: native checkbox (`accent-color` primary), name "Done, <title>". Optimistic PATCH `{status}`; on failure the
  row rolls back and an error toast shows the server message. A task ticked while the Open filter is on stays in place
  (muted) until the filter or search changes, so focus never jumps (the Discrepancies "kept" pattern). Done rows: title
  in muted text, no strike-through (readability).
- Overdue (due date before today, status open): the date plus a Warning glyph and the word "Overdue" in
  `--color-stale-fg` (the warning token, 7.52:1 on white). Silhouette first (glyph + word), colour second.
- Linked to:
  - no link: empty cell (screen readers hear "Not linked").
  - run: link with the app's run wording "Policy check, run 7" to `#/runs/7`.
  - discrepancy: the run link plus a chip link "<title> · Open" or "<title> · Resolved" (resolved carries the check
    glyph) to `#/runs/7?discrepancy=<item_id>`. Long titles truncate with an ellipsis; the full text is the accessible
    name and the tooltip.
  - item left the report (`in_report=false`, D3/D8): chip "<saved title> · No longer in the report" linking to
    `#/runs/7` with no scroll.
  - run gone (`run_missing`, D2): "Linked run removed" with the LinkBreak glyph, not a link.
- Row menu: a 36px DotsThree icon button (name "Actions for <title>"), menu with Edit and Delete. Arrow keys move,
  Esc closes and returns focus, a click outside closes. The title itself is also a button that opens Edit (the fastest
  path; the menu is the discoverable one).
- Delete (D6, O3): no confirm. The row hides at once; toast "Task deleted." with Undo (6 s, the cell-edit pattern).
  DELETE is sent only when that toast closes for any reason other than Undo (timeout, dismiss, a newer toast replacing
  it, the toast region unmounting) or when the Tasks page unmounts (navigation); the toast is dismissed then so a stale
  Undo cannot linger. Undo cancels and the row returns with the same id. A failed DELETE puts the row back and shows an
  error toast "Couldn't delete the task. It is back in the list." `Toast.tsx` gains `onClose(reason)` and a dismiss
  handle for this.
- Empty: "No tasks yet. Create one, or add one from a Policy Check discrepancy." + New task. No match: "No tasks match
  this search and filter." + "Show all tasks" (clears the search, filter to All).
- Loading: three skeleton rows; error: the error notice with Retry.

## New / edit dialog

- One component, native `<dialog>` with `showModal()` (focus trap, Esc, inert page), 520px wide, full width minus 16px
  on a phone. Same open motion as the review dialog (opacity + scale 0.98 at `--dur-base`, pointer opens only).
- Fields, labels above: Title (required, 200 max, counter near the limit), Due date (optional, native date input),
  Run (optional select, "None" first; options from `GET /api/runs?limit=100`, both lines, "Policy check, run 7
  (Personal)"; D5 bound), Discrepancy (only when the chosen run is a Policy Check run; options from the run's
  discrepancy list, "None, link the run only" first). Changing the run clears the discrepancy.
- Edit sends only what changed; a changed discrepancy is sent with its run so the backend checks the pair. A linked
  item that left the report stays selectable as "<saved title> (no longer in the report)" so editing the title keeps it.
- Errors: `400 invalid_task` shows its message under the named field (which takes focus); no field = message above the
  buttons. Empty title is caught before sending ("Enter a title.").
- Buttons: Cancel (secondary) and "Create task" / "Save changes" (primary, "Saving..." while in flight). On success a
  toast "Task created." / "Task saved."

## Discrepancy card (Policy Check)

- `.disc__actions` gains, before Review: "Create task" (ghost small button, ListChecks glyph) when the item has no task
  and the report is editable; once a task exists, a link "Task: Open" / "Task: Done" to `#/tasks?task=<id>` (O4 pick
  from the API). The dialog opens prefilled: title "Follow up: <discrepancy title>" cut to 200, run = this run, item =
  this item. After creating, the card shows "Task: Open" at once (local update of the item's `task`) and focus moves to it.
- `#/runs/<id>?discrepancy=<item_id>`: after the list loads (and on a later hash change) the card is scrolled to the
  middle of the list, outlined in primary (2px, the focus-ring colour, not a coloured side border) and focused. Filters
  that hide it are cleared first. The existing card scroll (`openFromCard`) is the same mechanism.
- No new classes that `policy-check.spec.ts` selects by (`.disc`, `.chip`, `.slot`, `.disc__resolved` and so on); the
  task link uses `.disc__task`.

## Decisions and what each skill said

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design |
|---|---|---|---|---|---|---|---|
| Real `<table>` with sortable header buttons and `aria-sort` | Router only; voice: plain words, no em dashes | Tables on mobile: card layout or scroll (ux "Table Handling"); keyboard nav, labels | Section 13: dense data UI is out of its scope, so use the platform table; density 6 | Semantic HTML, tabular figures for dates | Operate: scanability and consistency; native affordances | n/a (static) | Structure is information: headers name the columns, carets say the order |
| Phone: same table reflows to two-line rows + a Sort select | n/a | Mobile-first, no horizontal scroll | Mobile collapse explicit per layout | Fix layout at every width | adapt: compose for the small screen, do not shrink | n/a | Restraint: one list, not two components |
| Filter = native radios drawn as the existing segment (Show Open/Done/All) | n/a | Visible label, one tab stop, arrow keys | Full state cycle; check glyph when on | Reuse; no new radius | Reuse the filter-toggle silhouette | Press 0.97; no animation on keyboard change | Adds no new visual idea |
| Overdue = Warning glyph + word in the warning token | Plain word "Overdue" | Never colour alone; 4.5:1 | No decorative dots | Status words, one gray family | Status by silhouette first | n/a | Colour is the second signal |
| Linked-item chip with live state is the one expressive element | n/a | Link text names the destination | Middle dot once per line only (spec copy) | No pill badges: 4px tag radius | Brand in precise details | Hover colour 150ms only | Spend boldness in one place |
| Delete: no confirm, Undo toast, DELETE on toast close | Error copy says what to do | Destructive actions need a guard: here Undo is the guard (spec D6) | Errors contextual, toasts for transient news | No `window.confirm` | harden: failure restores the row | Exit faster than enter (toast 250/150ms) | "Task deleted." names the action |
| Row menu (Edit, Delete) + title button opens Edit | n/a | 44px targets, menu keyboard support | Each intent once per row | Hover/active/focus states | Discoverable path plus fast path | Menu scales from its trigger (top right), 150ms, pointer only | Controls name the action |
| Native `<dialog>` for new/edit | n/a | Labels above, errors under the field, focus the field in error | No placeholder-as-label | Inline validation | A modal is right here: one short form that needs protected focus | Open: opacity + scale 0.98, centred origin, 200ms, pointer only | "Create task" / "Save changes" say what happens |
| Deep links scroll + tint/outline + focus | n/a | Deep linking, predictable back | n/a | Current item indicated | Wayfinding is part of the floor | No scroll animation beyond the browser's | The highlight answers the click that brought you here |
| Empty states carry the one New task button | n/a | Empty state explains and offers the next step | No duplicate CTAs | Compose a getting-started view | onboard: emptiness is an invitation | n/a | Direction, not mood |

## Deliberate choices against a skill

- taste-core and taste-redesign push away from Inter and violet; DESIGN.md pins both (override path, as in T5/T6/T9).
- ui-ux-pro-max's "confirm before delete" row: spec D6 chose Undo over confirm; Undo is the guard.
- impeccable reports `NO_PRODUCT_MD`; not written (out of scope), flagged again.

## Owner calls taken by default

- Created sort starts newest first (the useful first click); title starts A to Z, due date soonest first.
- No-date tasks stay last in both due-date directions.
- The discrepancy card shows only the task link once a task exists (no second "Create task"); more tasks for the same
  item can be made from the Tasks page. The review dialog does not get the task action.
- "Create task" shows only while the report is editable (same rule as Mark resolved).
- The title is a button that opens Edit, besides the menu's Edit.
- A deep-linked row stays tinted while the URL names it.
- Field errors in the dialog are each their own `role=alert` under the field (no separate summary).
- The Tasks list loads once per visit to the page; a hash-only change (`#/tasks?task=3` to `#/tasks?task=4`) re-targets
  without refetching.

## Self-review rounds (3 of 3, Playwright MCP, screenshots in `qa-screenshots/tasks/`)

1. `r1-*`: built as briefed; at 1440 opened Tasks from the rail, created a task linked to run 7's Wind/Hail item,
   searched "run 7", sorted by Created, ticked one done (stayed in the Open view, muted), deleted from the menu and let
   the toast time out (DELETE sent, row gone), deleted again and pressed Undo (same id back, focus on its title),
   followed the chip to the run (card outlined, focused), made a task from the Form HO 04 81 card (card switched to
   "Task: Open", focused), followed it back (row tinted, title focused); then 390 and 1024. Fixed: column widths jumped
   whenever the rows changed and the title column was squeezed at 1024 (now `table-layout: fixed` with set widths); the
   tinted target row showed faint seams between cells (tint moved to the row); on a phone the chip cut the discrepancy
   title short (it wraps there).
2. `r2-*`: 1024 page and the 390 edit dialog (title button opens it, Esc returns focus, no sideways scroll). No new
   findings.
3. The e2e screenshots (`1440-*`, `1024-*`, `390-*`): dropped item, removed run, empty state, dialog with a field error,
   the card deep link at each width. Fixed: at 390 the deep-linked card is taller than the screen, so centring it hid its
   heading; a card taller than 60% of the window now aligns to the top.

Known, not fixed: with a long discrepancy title the 1440 chip shows the ellipsis, then " · Open" (the separator's own
space reads as a small gap). At 390 the run toolbar's report actions still clip at the right edge (existing, noted in
PERSONAL-LINES-BRIEF.md). The review dialog has no task action (owner call above).
