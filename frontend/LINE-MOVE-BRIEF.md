# Line move brief: moving a file between Commercial and Personal lines (direction B)

Owner complaint (2026-11-03): "moving from personal to commercial/vice versa feels static, hard for broker to
differentiate". Phase A (audit + directions A, B, C) is in this lane's report; the owner picked **B** and approved
the reversals listed below. Supersedes the run-header parts of `PERSONAL-LINES-BRIEF.md` ("Line choice", "Owner calls
taken by default": no Undo).

## What the audit found (Playwright MCP, run 7, 1440 / 1024 / 390)

1. The header segment jumped about 36px sideways on every flip: the check glyph was drawn only in the chosen option, and
   "Commercial" plus check is wider than "Personal" plus check. At 390 the sliding indicator did not match the option.
2. Commercial had no name anywhere: a commercial Policy Check run's crumbs were "Policy check > client", and the
   commercial book lives on a page called "Compare Quotes" under a rail item called "Workflows".
3. Nothing else changed after a move: rail, header and report identical; only the collapsed "Recent work" count moved.
4. The toast was far from the control and had no Undo, while the queue row's Move already offered Undo.

First principles: the broker needs to know, at a glance and at all times, which book a file is in, and, right after a
move, that the file is now in the other book and can go back. So the book must be a persistent label, the action must
name its consequence, and the confirmation must offer recovery.

## Decisions

| Decision | Why |
|---|---|
| Crumbs lead with the book for every run, Commercial included | The book is always named, at every width that shows crumbs |
| Run header: book tab (mark + name) and one "Move to <other book>" button, replacing the Line radios | A radio pair reads as a setting; a tab plus a named action reads as "filed here; move it there" |
| Commercial mark: an office block (voice set, `voice/marks.tsx` "commercial") | Each book needs its own silhouette; the house was already Personal lines |
| Toast "<client> moved to <book>." with Undo; Undo says "moved back" with no second Undo | Names what moved and where (frontend-design: action keeps its name); recoverable (PRODUCT.md 3) |
| Re-file motion on the tab and the book crumb only, pointer only | Motion explains the change at the two places the eye checks; keyboard instant |
| Rail unchanged (tool, not book) | Direction C not approved; D7 tests stay as written |
| Start screens keep the full-size Line choice, with the jump fix | They choose a book for a run that does not exist yet; a choice is right there |
| Toast region sizes to its message (capped at viewport less 32px) | With `left: 50%` alone it used half the viewport: seven lines at 390 |

Owner-approved reversals: Undo on the move toast; "Commercial >" crumb; Line radios in the run header replaced by the
book tab and Move button; office-block mark (DESIGN.md Voice). Not taken: direction C (rail follows the book).

## What each skill said

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design |
|---|---|---|---|---|---|---|---|
| Book in crumbs | Plain words, no em dashes | Nav: current location must be shown | n/a | "No indication of current page in navigation" is a defect | Wayfinding is part of the floor | n/a | Words make the page navigable |
| Tab + Move button | n/a | 44px target (hit area via `::before`); visible label; no false affordance (tab has no border) | No duplicate intent: one action, not a radio pair plus toast; one accent | Work with the stack (no new library); hover/active/focus states | Operate mode: brand in precise details (binder-divider edge) | Press scale 0.97 | Button says what happens; same words as the queue's Move menu |
| Office-block mark | SVG, no emoji | Decorative `aria-hidden`, text carries meaning | Its "never hand-roll icons" is overridden by DESIGN.md's drawn-marks rule | One stroke weight (1.5) | Marks drawn for the product | n/a | Distinct silhouette per book |
| Toast with Undo | Errors plain | Undo for reversible actions; announced via the live region | Toasts only for transient news | No exclamation marks | Honest, recoverable states (PRODUCT.md 3) | Toast timing unchanged (250/150ms) | "Moved to Commercial" echoes "Move to Commercial" |
| Re-file motion | n/a | 1 to 2 animated elements per change; reduced motion | Motion must be motivated (state transition) | transform/opacity only | Motion explains change | 200ms ease-out for entering; never on keyboard; reduced = opacity | Motion answers the person's action |
| Jump fix | n/a | Avoid layout shift | n/a | Optical stability | n/a | Unseen details compound | n/a |

## Self-review rounds (Playwright MCP; 2 of 3 used)

1. Built as above; at 1440 mouse move, mid-move frame, Undo (pointer), keyboard Enter (instant, focus kept) and Ctrl+Z,
   reduced motion (tab opacity only, crumb `crumb-fade`), forced PATCH 500 (rollback + server message), 44px hit area.
   Found: at 390 the longer toast wrapped to seven lines in a half-width box. Fixed (toast region width).
2. 390 and 1024 rechecked; start screen segment keeps one width on flip and the indicator matches the option. No new
   findings.

Verified (scratch stack 8070/5220, fresh seed): tsc clean; full e2e `--workers=1` 58/59, the one failure an axe
color-contrast hit on a toast caught mid-fade in `wizard.spec.ts` step 1 at 1024 (passed 2/2 on rerun); axe 0 at 1440,
1024 and 390 on the run header, Personal lines and entry screens. Edited e2e: `personal-lines.spec.ts` (each edit
commented "line move B"), plus the new "Move to Commercial then Undo" test. No backend change.

Known, not fixed: on rollback the old tab animates back in (it reads as "went back"); a pointer move costs a full page
re-render, so in headless Chromium the crumb fade started about 140ms after the tab's; the Tasks filter (the remaining
compact segment) still sizes options to text on phone, unchanged here.
