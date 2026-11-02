# Handoff: BrokerDocs, slice 1

Date: 2026-11-01. State: spec approved, engineering review DONE (Codex outside voice completed).
Design review STARTED, NOT finished. No code written yet.

## Where everything is
- Spec + decision ledger + tasks + eng review report: `docs/specs/2026-11-01-brokerdocs-core-design.md`
  (Decision ledger R1 to R11 = D2 to D12; Carried mechanics M1 to M8; Implementation Tasks T1 to T11).
- **UI ground truth: `docs/reference/brokerdocs-ui-reference.md`** (exact tokens from the live app's sign-in
  DOM, plus shell, chip, state and per-workflow notes from the demo videos). Screenshot: `docs/reference/app-signin-1440.png`.
- TODOS: `TODOS.md`. QA test plan: `C:\Users\aakas\.gstack\projects\aakas\aakas-nogit-eng-review-test-plan-20261101-210500.md`.
- Raw evidence: `../BrokerDocs-REA-Teardown.md`, `../rea-out/` (six demo videos + frames) and
  `../rea-out/yt-reference/watch-eg5_xfp8/` (official "BrokerDocs Overview" YouTube video, 38 frames).

## Design review status (/plan-design-review)
- Done: design stack loaded; design completeness rated 4/10; Playwright read of the live sign-in page;
  frames mined; UI reference written; D1 (review mode) and D2 (fidelity) answered.
- D2 answer: match the reference product's real tokens (Inter, #6356F0, #35383B, 6px radius, 44px buttons, 48px inputs),
  own logo, name and copy, no copied images or code. Spec section 5 updated.
- Image mockups were CANCELLED (owner: clone from the real UI, not invented mockups). Do not generate them.
- NOT done: the 7 design passes (information architecture, states, journey, AI-slop, design system,
  responsive and a11y, unresolved decisions), the design review report, review log.
- The new states we must design ourselves (not in any frame): evidence-state chips (verified, scan-read,
  couldn't verify, failed), stale-section banner, failed-column state, run progress, multi-option warning,
  budget-exhausted partial report. Shape and label as well as color.

## UPDATE 2026-11-01 (later): design review DONE
Design review finished: 12 decisions DG1 to DG12, scores 2 to 8, recorded in the spec section "Design review"
(state table, journey, tasks T12 DESIGN.md and T13 run Cancel). One open item: collapsed-outline warning mark.
Name stays "BrokerDocs" (config constant). Next: write DESIGN.md (T12), start backend lanes A/B/C, then T10.
The "NOT done" list above is superseded.

## Decisions (do not re-ask)
D1 to D12 eng review (see spec). Design D2: real tokens, own logo.

## Next, in order
1. Finish `/plan-design-review` against the UI reference (text-only passes, no image generation).
2. Backend lanes can start in parallel (spec "Worktree parallelization strategy"): A: T1,T3,T4,T5,T6; B: T2; C: T9.
3. Frontend (T10) after the design review. Use the Playwright MCP on the built app, not on BrokerDocs (no login).

## Gotchas
- Not a git repo (offer `git init`).
- No login to app.brokerdocs.ai: only the sign-in page and the videos are available.
- The key is in `broker-docs-verified/.env` (gitignored once a repo exists); it also appeared in the session
  transcript, so rotate it if that matters. It was NOT used for mockups.
- This machine's `yt-dlp` on PATH is a stale Python 3.10 launcher. I installed yt-dlp for Python 3.12
  (`pip install --user`); to use it put `C:\Users\aakas\AppData\Roaming\Python\Python312\Scripts` first on PATH.
- Do not rewrite markdown with Python open(...,"w") on Windows (CRLF breaks gstack-plan-reality).
- Tesseract is not installed (only matters for the local-OCR TODO).
