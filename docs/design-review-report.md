# Design review report: BrokerDocs slice 1

Date: 2026-11-02. Rounds run: 2 of 3. Stack: dev backend (scripted model), Chromium 156 via Playwright MCP, 1440 and 1024 widths.
Ground truth: `../rea-out/frames/3kVG8k-01.jpg` (comparison report), `vLezj1-03.jpg` (proposal shell), `docs/reference/brokerdocs-ui-reference.md`.
Skills applied: gstack, ui-ux-pro-max, taste-core, taste-redesign, impeccable (Operate mode), emil-design-eng, frontend-design.

## Findings

| # | Screen | Difference | Severity | Verdict | Skill basis | Fix |
|---|---|---|---|---|---|---|
| F1 | Report table, 1440 | Column D clipped at the right edge, no sign more columns exist. The real product fits 4 carrier columns. | High | Drift | taste-redesign (no hardcoded widths), ui-ux-pro-max (no hidden horizontal scroll without an affordance) | Column minimums 160/150 to 128/128, row head 180 to 150, table minimum 640 to 600. Four columns fit at 1440. Added a scroll-driven right-edge fade (64px) that only shows while columns are hidden. |
| F2 | Report, 1024 | Outline open by default left about 2.5 of 4 columns visible. | High | Drift | impeccable Operate: the table is the anchor, chrome yields | Outline defaults open only at 1280 and up (`RunView.tsx`), matching how the shell already collapses the left panel. The handle and toolbar toggle still open it. |
| F3 | Left panel, 5 or more documents | Upload list scrolls inside a small box above the suggestions block. | Low | Drift | taste-redesign (density, misaligned rhythm) | Not fixed in round 1 (see Remaining). |
| F4 | Run 5, 5 quotes | A fifth column cannot fit at any width we support, so it scrolls. | Info | Accepted | | Covered by the F1 scroll cue. |
| M1 | Launch, report, split, progress copy and chips | Wordmark, chips with letter plus number, disabled rail, "All runs" tab, "Local workspace", input border `#848D99`. | None | Intentional (DG1, DG3, DG6, DG7, DG10, DG11) | | None. |
| M2 | Chip click to PDF split | Highlight on the source page, Esc exits, page counter, fit width all work. | None | Match | | None. |

## Accessibility

axe (`@axe-core/playwright`) on the launch page and runs 1 to 6, at 1440 and 1024, before the round 1 fixes: 0 violations on all 14 combinations. Re-run after the round 2 fixes: still 0 on all 14, and the e2e suite's own axe checks are 0 as well.

## Screenshots

`frontend/qa-screenshots/review/`: `r1-launch-1440`, `r1-report-1440` (before), `r1-report-1024` (before), `r1-run5-partial-1440`, `r1-split-1440`, `r1-after-1440`, `r1-after-1024` (after, table fits, outline collapsed), `r1-after-run5-1440` (after, scroll fade visible).

## Round 2

Scope: runs 1 to 4 at 1440, phone width (390), cell edit and undo, keyboard-only pass, reduced motion, axe after fixes, e2e suite.

| # | Screen | Difference | Severity | Verdict | Fix |
|---|---|---|---|---|---|
| G1 | Toolbar at 390 | Title overlapped by the action icons (thumbs-down sat on "Quotes,"). | Medium | Drift (bug) | Toolbar wraps; title takes its own row, actions wrap below. Wide layouts unchanged. |
| G2 | Cell edit | Single-line input in a narrow cell showed only the tail of long values (Territory, Policy Form). | Medium | Drift | Editor is now an auto-growing textarea (still role textbox, Enter saves, Esc cancels, newlines folded to spaces). |
| G3 | Undo toast, keyboard | The toast is outside the tab order and holds 6 s, so a keyboard user could not reach Undo. | Medium | Drift (a11y) | Ctrl/Cmd+Z runs the toast action while it is showing (not inside a text field). Button carries `aria-keyshortcuts`. Verified: edit then Ctrl+Z reverts. |
| G4 | Rail | 13 disabled rail items are 13 tab stops before any content (skip link exists). | Low | Intentional (DG1), recommendation only | Not changed: DG1 is an owner decision. Option: `tabIndex=-1` on disabled items. |
| G5 | Chat field | Focus shown by a violet wrapper border, not an outline ring. | Low | Accepted | None. |
| G6 | Run 1 (failed) | The same "could not be read" message appears in the panel header, the body and per document. | Low | Drift (copy density) | Not fixed. |
| G7 | 390 | Page still scrolls sideways; the banner says so ("Best viewed on a wider screen"). | Info | Accepted (desktop tool) | None. |

Checked, no change needed: runs 1 and 4 states, edit save (version bump, edited pencil, "Original source", counts update), reduced motion (toast, split pane and skeletons already degrade; the table fade follows scroll position, not time), focus order (skip link, rail, panel, toolbar, report, chips).

Verification after the fixes: axe 0 violations on 14 screen and width combinations; `npx playwright test` 2 passed (its own axe checks 0 violations); `tsc --noEmit` clean; `npm run build` ok.

Screenshots added: `r2-run1-1440`, `r2-run4-1440`, `r2-run6-390` (before), `r2-after-run6-390`, `r2-edit-1440` (before), `r2-after-edit-1440`, `r2-undo-toast-1440`.

## Remaining

- F3 left panel density with 5 or more documents (low).
- G4 rail tab stops (owner decision), G6 failed-run message repeated three times (low).
- Not reviewed: cancel and retry flows in the browser, chat panel states (error, proposed edit), stale tag and regenerate flow, the other reference frames (upload accordion, run progress), phone widths other than 390.
- Known gaps from the brief, unchanged: "sample quotes" link, version history UI, real too-large and too-many-pages upload rejections, Chromium only.
- Dev DB note: the dev database now holds manual edits and e2e runs; re-seed before the next review.
- Recommendation: one final round (3 of 3) on the "not reviewed" list, then stop.
