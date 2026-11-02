# Handoff: BrokerDocs, slice 1

Date: 2026-11-01 (late). State: backend AND frontend (T10) built and merged to `main`. Next: design review against the real frames.
Repo: `broker-docs-verified` is now a git repo (`main`). `.env` (OpenAI key) is gitignored.

## What is built (all merged, 608 backend tests + 153 eval tests pass offline)
- T1 data model, T3 parse (OCR triggers, caps), T4 verify, T2 providers + budget, T5 extract/compose,
  T6 runner (SSE, reconcile, idempotent), T13 cancel, T7 security baseline, T8 routes/edit/undo/stale/chat/export,
  T9 synthetic PDFs + eval gates, T12 DESIGN.md + tokens.css, eval hook.
- T14 long documents (up to 300 pages / 50 MB): 15-page windows with 2 overlap, deterministic merge
  (`pipeline/windows.py`, `merge.py`), per-document budget (400k) inside a 1.5M run budget,
  new `conflicting_values` notice. Long fixtures `long_120`, `long_300` + `long` gate (0 hard fails, accuracy >= 0.85).
- **API contract for the frontend: `backend/API.md`.** Design contract: `DESIGN.md`, `frontend/src/styles/tokens.css`,
  spec section "Design review" (DG1 to DG12).
- Evidence for later slices: `docs/reference/brokerdocs-workflows-evidence.md` (ranking: Proposal Builder, data-only pack,
  Policy Check, COI, Loss Run, Assistant, Market Search, ACORD).

## Live eval status (2026-11-02)
Models `gpt-5.4-mini` (extract/vision), `gpt-5.5` (compose/chat), `gpt-5.4-nano` (suggest). Prices in `providers/config.py` are stand-ins.
Full run 5: 0 hard fails, 0 injection canaries verified, clean 1.000, multi-column 0.983, scanned 1.000, overall 264/286 (0.923).
Long group (subset run, after the containment merge rule): 28/30 (0.933), 0 hard fails; gate passes with margin
(3 earlier subset runs scored 26/30 each). Remaining misses: `territory` in long_120 and long_300 (a very long sentence across
pages 14 to 16 comes back truncated, verify rejects it): accepted, fixture-hard.
A full 23-document run has not been repeated since the containment rule and the backend fixes; do one before any release.
Run subsets with `BROKERDOCS_EVAL_GROUPS=long`; reports list per-field MISS lines. History: run1 31 hard fails, run2 4, run3 5, run4 1, run5 0.

## Decisions made this session (do not re-ask)
Input border `#848D99` (DG10 3:1) kept; collapsed-outline warning mark = triangle glyph on the handle;
lane C: amended endorsement value is truth, cited-wrong-page counts as hard fail, canary `verified` = hard fail;
failed extraction shows "Extraction failed" + Retry (M1), budget exhaustion not sticky, all-docs-failed = FAILED run;
long docs: 300 pages, windows + merge, conflicting values shown with a notice (never silently picked), page-scoring skip pass OFF.

## Open items
1. Residual injection bypasses: a note to the AI followed by a separate declarative sentence; imperatives outside the verb list.
   Claim-level checks remain TODO R10.
2. A database seeded before the `policy_form` description change keeps the old description (seed only inserts if absent);
   add re-seed/update at startup before anyone keeps a persistent DB.
3. Frontend gaps: "sample quotes" link (no endpoint), version history UI, real too-large/too-many-pages upload rejections in the
   browser, Chromium only.
4. Cross-reference gap (a value defined by reference across windows) is unmeasured: see TODOS.md.
5. `over_cap_pages.pdf` is obsolete (kept on disk); `long_301` (--include-oversize) is the over-cap test.
6. No DB migrations: delete any local `data/brokerdocs.db` created before 2026-11-02.
7. Rotate the OpenAI key if transcript exposure matters.
8. Port 5173: the BFL clone (JobHunt\BFLfl-clone) also uses it; one BFL server was stopped on 2026-11-02 at the owner's request.

## Next, in order
1. `/design-review` of the live app against the real frames: follow `docs/design-review-brief.md` (fresh session, design skills loaded,
   Playwright MCP, max 3 rounds). Dev stack: README in `frontend/`; set up `main` first (backend/.venv py3.12 pymupdf<2, npm install).
2. Slice 2 spec: owner picks the first workflow from `docs/reference/brokerdocs-workflows-evidence.md` (ranking: Proposal Builder,
   data-only pack, Policy Check, COI, Loss Run, Assistant, Market Search, ACORD). A slice map was offered, not drafted.

## Next, in order
1. T10 frontend (running as an agent in a worktree as of 2026-11-02 09:00; merge, build, check its screenshots and axe results).
2. `/design-review` on the live app, then the slice 2 spec (owner picks the first workflow from the evidence ranking;
   a slice map was offered but not drafted).

## Gotchas
- Test env: no shared venv in the repo. Create `backend/.venv` (Python 3.12) from `backend/pyproject.toml`;
  `pymupdf` must stay `<2` (2.0 segfaults here) and PyMuPDF 1.28.0 is needed for fixture byte-identity.
- Eval venv: `tests/.venv_c`. Run backend tests from `backend/`, eval tests from the repo root.
- Not for rewriting markdown with Python open(...,"w") on Windows without preserving CRLF.
- Tesseract not installed (only matters for the local-OCR TODO). `yt-dlp` PATH quirk: see earlier note (Python312 Scripts first).
- Agent worktrees live under `.claude/worktrees/` (gitignored); they can be deleted.
- Fresh session recommended: this one has a long context.

## Design review (2026-11-02): done, 3 of 3 rounds
Report: `docs/design-review-report.md`; screenshots `frontend/qa-screenshots/review/`. Fixed: table column fit and scroll cue, outline collapsed below 1280,
toolbar wrap, auto-growing cell editor, Ctrl/Cmd+Z for the Undo toast, disabled rail items out of the tab order. axe 0 on 14 combinations, e2e 2 passed.
Owner calls left: repeated "Extraction failed + Retry" per cell (M1), collapse Upload when a run is Done, double-colon failure copy.
Dev stack was left running (backend 8000, vite 5173); stop them when done. Re-seed the dev DB before reuse.

## Slice 2 Proposal Builder (2026-11-02): backend, DOCX, eval and wizard UI (T7) merged to main
Spec + eng review: `docs/specs/2026-11-02-proposal-builder-design.md`. Contract: `backend/API.md`. Wizard brief: `frontend/WIZARD-BRIEF.md`.
Main: backend 838 pass/2 skip; wizard e2e 3/3 with axe 0 (run with a scratch backend: `BROKERDOCS_DATA_DIR=<scratch> BROKERDOCS_PORT=8010 BROKERDOCS_UI_ORIGIN=http://localhost:5183`, `python -m backend.dev_seed seed` then `serve`; then `E2E_UI_PORT=5183 VITE_API_BASE=http://127.0.0.1:8010 npx playwright test` in frontend/).
Live proposal eval passed (0 hard fails, cell accuracy 0.939). Run under `backend/.venv`, see `tests/eval/test_live_eval.py` (`-k proposal`).
T8 full 23-doc live eval run 2026-11-02 11:37: overall FAIL, 261/286 (0.913), 1 hard fail: `long_300.policy_form` verified 'Commercial General Liability Coverage Part' vs truth 'CG 00 02 04 13 ... Coverage Form (Claims-Made)'. Other gates pass (clean 1.000, multi-col 0.917, long 0.867, tokens 438k). Also: `carrier_a_multicol` 5 fields rejected not_found despite correct values (was 0.983 before; check for variance vs verify regression); `long_300.policy_term` conflicting-values merge picked up 'Page 27 of 300' as a value; territory misses in long_120/300 known. `password_protected` misses are expected (parse-only). Not yet diagnosed or fixed.
Wizard owner calls taken by default (not explicitly answered): dev_seed.py proposal prose merged; reorder = Move up/down buttons only (no drag); phone top bar shows only current breadcrumb app-wide.
Open gaps: chat copy on proposal runs ("Run a comparison..." in Chat.tsx); duplicate uploads in the picker (collapse by sha256); Preview hides chips only; section edit-open state resets between steps; PRODUCT.md missing; left-panel design fix (chunky stack, chat cannot collapse, collapse Upload when Done); slice 2c extraction cache; Standard template has no live eval coverage; Playwright MCP wrote files to `BrokerDocs/.playwright-mcp/` outside the repo.
