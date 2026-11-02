# Left-panel design fix: brief

Written before building. Sources: `docs/design-review-report.md` (F3, H8), `HANDOFF.md` (owner call "collapse
Upload when Done"), `DESIGN.md`, `tokens.css`, `src/run/RunView.tsx`, `RunProgress.tsx`, `Chat.tsx`,
`launch/Upload.tsx`, `ui/Accordion.tsx`, `shell/layout.tsx`.

**Design read:** Operate mode. The 300px run panel is chrome around the report table. Today it is one scroll box
(`.panel-scroll`, flex 1, min 120px) stacked on a chat with `flex: 1 1 45%; min-height: 240px`, so a finished run
gives chat about 250px and five or more documents scroll inside a small box (F3, H8). Out of scope: panel width,
rail, report table, wizard. Tokens, Inter, `#6356F0`, 6px/4px radii, hairlines and motion tokens are pinned.

**Dials (taste-core):** variance 2, motion 2, density 7. Same family as T10 (3/2/7); tighter variance because this
is a fix to an existing panel, not a new surface.

**Where the boldness goes (frontend-design):** nowhere new. The report table stays the one anchor. The panel gets
quieter: fewer lines, one summary line when there is nothing to do.

## What changes

```
Running / idle / failed / partial          Done (default)                    Done, chat hidden
+------------------------------+           +------------------------------+  +------------------------------+
| Upload  3                 ^  |           | Upload  3 documents,       v |  | Upload  3 documents,       v |
|  Run 7  RUNNING     [Cancel] |           |             all read         |  | Input parameters           v |
|  A carrier_a.pdf  4 pages  o |           | Input parameters           v |  | Recent runs  6             v |
|    Reading pages 2/4         |           | Recent runs  6             v |  | + New comparison             |
|  B carrier_b.pdf  2 pages  o |           | + New comparison             |  |                              |
|  C carrier_c.pdf  3 pages  o |           +------------------------------+  |                              |
|  (news rows never fold)      |           | Chat  2                    v |  +------------------------------+
| Input parameters           v |           |  (transcript takes the rest) |  | Chat  2 messages           ^ |
| Recent runs                v |           |  [Ask about these quotes  ^] |  +------------------------------+
+------------------------------+           +------------------------------+
| Chat (idle line + input)     |
+------------------------------+
```

1. **Upload collapses on Done.** `Accordion` gains a `summary` shown only while closed, inside the toggle (so it is
   part of the button's name). Run view: open = user override, else `status !== "done"`. The override resets when
   the status changes, so a Retry that starts running again reopens it. Failed, partial, cancelled, running and
   idle stay open. Summary copy: "3 documents, all read" (or "1 document, all read").
2. **Compact document rows.** One line per document: letter badge, filename (ellipsis, full name in `title` and
   in the accessible text), page count ("12 pages", tabular), 16px status glyph. A second line appears only when
   it carries news: live progress ("Reading pages 2/4"), the failure reason, or "Compared option 1 of 3". Page
   counts come from `GET /api/documents` (the run payload has none); missing counts are simply omitted.
   No inner scroll box: the list grows with the panel. Past 6 rows, quiet rows (done, queued) after the fifth
   fold into a "+N more" button row (`aria-expanded`); rows that carry news (failed, active) never fold. The
   launch file list gets the same row shape and fold (rejected and uploading rows never fold) and keeps Remove.
3. **Chat collapses.** Chat gets a header that works like the panel's accordion heads: "Chat", message count,
   caret, `aria-expanded`/`aria-controls`. Collapsed, it docks as a single 44px handle at the bottom of the panel
   with a summary in its name ("Chat. 2 messages"), the same idea as the outline handle (summary in the label, one
   action to reopen). Shares: the upload area is `flex: 0 1 auto` (shrinks to content, scrolls only if it must);
   chat with a report is `flex: 1 1 0` with `min-height: 240px`, so on Done it takes the remaining height; chat
   without a report (running, idle) or collapsed is `flex: none`, so Upload owns the panel. Under 1280px the strip
   buttons "Show uploads" and "Show chat" open the overlay with that section expanded.

## Decisions and what each skill said

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design |
|---|---|---|---|---|---|---|---|
| Collapse behaviour: Upload auto-collapses only on Done, user override wins until the status changes; summary line inside the toggle | Router only; voice: plain words, no em dashes | Progressive disclosure; content truncation needs an expand option (Content/Truncation); keyboard nav + aria (priority 1) | One focused message per section; full state cycle (idle, running, failed all keep it open) | Strategic omission check: no dead ends, every collapsed thing says what is inside | Operate: scanability and the usage scene win; a finished run's next task is reading and asking, so chat gets the room | Handle edge cases invisibly: a Retry reopens it, the user's own toggle is never fought | Words name what is there: "3 documents, all read". "Add or replace" from the owner's example is not used: a run's documents cannot be changed in the run view (owner call below) |
| Row density: one line per document, second line only for news, "+N more" past 6, news rows never fold | n/a | 44px targets for the "+N more" and Remove buttons; never colour alone (status glyph silhouettes differ: check, ring, spinner, warning) | Bans decorative dots; a status mark is allowed only for real state, so it is a glyph with its own silhouette, not a coloured dot. Long lists need another component: fold beats a scroll box | Density for data UIs; tabular figures for page counts; misaligned rhythm fixed by one row height | Operate: density when users need it; consistency: the launch list and the run list share one row shape | n/a | Structure is information: the letter badge already numbers the documents, no extra counters |
| Chat handle: accordion-style header with count and caret; collapsed = 44px docked handle with summary in its name | n/a | aria-expanded/controls on disclosures; visible label, not icon-only | Shape lock: same 6px controls and hairlines as the accordions above it | Every state: empty chat, idle (no report), collapsed, answering, error | Earned familiarity: reuse the panel's own accordion vocabulary instead of a new control | Toggled tens of times a day at most: no height animation | Keep the quiet chrome quiet; no new colour |
| Motion: caret rotates (transform, `--dur-base`, existing); opened body fades in 150ms opacity via `@starting-style` on pointer opens only; keyboard opens are instant; no height animation | n/a | Respect reduced motion (High); animate transform/opacity only | Motion must be motivated: the fade shows what appeared, nothing else moves | Transitions under 300ms on transform/opacity only | Motion floor: exponential ease-out from a visible default | Never animate keyboard-initiated actions; ease-out for enter; under 300ms; reduced motion keeps opacity | Motion answers a person's action and shows what changed |
| Focus and keyboard: tab order Upload toggle, document rows' actions, "+N more", other accordions, New comparison, chat toggle, chat log, input | n/a | Focus rings visible (DG10 ring token); logical order | n/a | Missing focus ring is a defect | States include keyboard focus | n/a | Quality floor: visible keyboard focus |

## Self-review rounds (3 of 3, screenshots in `qa-screenshots/left-panel/`)

1. `r1-*`: built as briefed. Fixed: Upload flashed open then shut while a Done run loaded (now folded
   while loading); suggestions pushed the chat input out of the 240px floor in the 1024/390 overlay (they
   now scroll); "+N more" sat after rejected rows instead of where the folded rows are; chat with a report
   but no suggestions showed an empty box (now "Answers cite the page they come from."); dropped the chat
   icon so the head matches the accordion heads.
2. `r2-*`: 1, 6 and 8 documents, live running to Done, chat with answers, keyboard order, reduced motion.
   Fixed: launch filenames truncated to about 110px; the ready check glyph was redundant with the letter
   badge, so launch rows drop the glyph column (rejected rows show their warning glyph in the page-count
   cell, warnings carry theirs on the news line).
3. `r3-*`: final pass at 390, 1024, 1440 for launch (10 files), running, Done, Done with Upload open and
   chat folded, failed. No new findings.

## Deliberate choices against a skill

- taste-core and taste-redesign push away from Inter and violet; DESIGN.md pins both (override path, as in T10/T7).
- taste-core's scope note puts dense product UI outside it; only its state, shape, dot and copy locks were applied.
- taste-redesign suggests 200 to 300ms transitions on everything; DESIGN.md and emil-design-eng keep keyboard
  actions instant and the disclosure fade at 150ms.
- impeccable reports `NO_PRODUCT_MD`; not written (out of scope), flagged again.
- Owner's status "dot" is shipped as the existing 16px status glyphs (check, ring, spinner, warning): a dot that
  differs only by colour fails DG3 (silhouette first) and taste-core's dot rule.
