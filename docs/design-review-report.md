# Design review report: BrokerDocs slice 1

Date: 2026-11-02. Rounds run: 1 of 3 (stopped early, see "Remaining"). Stack: dev backend (scripted model), Chromium 156 via Playwright MCP, 1440 and 1024 widths.
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

axe (`@axe-core/playwright`) on the launch page and runs 1 to 6, at 1440 and 1024, before the fixes: 0 violations on all 14 combinations. Not re-run after the fixes in this round (CSS min-widths and a default state only); re-run in round 2 or before release.

## Screenshots

`frontend/qa-screenshots/review/`: `r1-launch-1440`, `r1-report-1440` (before), `r1-report-1024` (before), `r1-run5-partial-1440`, `r1-split-1440`, `r1-after-1440`, `r1-after-1024` (after, table fits, outline collapsed), `r1-after-run5-1440` (after, scroll fade visible).

## Remaining

- F3 left panel density with 5 or more documents.
- Not yet reviewed in this round: cell edit and undo toast, stale tag and regenerate, cancel and retry flows, keyboard-only pass, reduced motion, phone widths (900 and below), runs 1 to 4 visually, chat panel states, comparison with the other reference frames (upload accordion, run progress).
- Known gaps from the brief, unchanged: "sample quotes" link, version history UI, real too-large and too-many-pages upload rejections, Chromium only.
- Recommendation: run round 2 on the unreviewed list above, then stop at round 3 per the review cap.
