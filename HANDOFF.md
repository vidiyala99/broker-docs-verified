# Handoff: BrokerDocs, slice 1

Date: 2026-11-01 (late). State: backend COMPLETE and merged to `main`; frontend (T10) NOT started.
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

## Live eval (run 2, 2026-11-02, final pipeline)
Key works; models `gpt-5.4-mini` (extract/vision), `gpt-5.5` (compose/chat), `gpt-5.4-nano` (suggest).
Result: clean 1.000, multi-column 0.883, scanned 1.000, report-only 1.000 (all gates pass), injection canaries 0,
citation verification 0.971, 423k input tokens total. FAILS only the `long` gate: accuracy 0.833 (needs 0.85), 4 hard fails:
long_120.policy_form, long_300.policy_form ("Commercial General Liability Policy" instead of the form line),
long_300.territory ("Policy Territory Definitions", a heading), long_300.producer ("Producer of record", a label).
Cause: windows without the real value return a heading/label as the value; verify accepts it (it is on the page) and merge
keeps it. Prices in `providers/config.py` are still stand-ins. Report: scratchpad `live_eval_report2.json`.

## Decisions made this session (do not re-ask)
Input border `#848D99` (DG10 3:1) kept; collapsed-outline warning mark = triangle glyph on the handle;
lane C: amended endorsement value is truth, cited-wrong-page counts as hard fail, canary `verified` = hard fail;
failed extraction shows "Extraction failed" + Retry (M1), budget exhaustion not sticky, all-docs-failed = FAILED run;
long docs: 300 pages, windows + merge, conflicting values shown with a notice (never silently picked), page-scoring skip pass OFF.

## Open items
1. **Long-document hard fails (above).** Proposed: (a) extract prompt: return null unless the window states the actual
   value, never a heading or label; (b) merge: a non-amendment conflict should not verify an arbitrary earliest value
   (decision for owner: show "Conflicting values" with both and no pick, vs. keep earliest + notice as built).
   Then re-run live eval once. Review rounds cap at 3.
2. The verify injection gap is FIXED (directive_text guard, merged). Residual bypasses: a note to the AI followed by a
   separate declarative sentence; imperatives outside the verb list. Claim-level checks remain TODO R10.
3. `over_cap_pages.pdf` is obsolete (kept on disk); `long_301` (--include-oversize) is the over-cap test.
4. No DB migrations: delete any local `data/brokerdocs.db` created before 2026-11-01.
5. Editor Pyright flags `backend.*` imports; harmless.
6. Rotate the OpenAI key if transcript exposure matters.

## Next, in order
1. Long-document fix (item 1), re-run live eval once.
2. T10 frontend from `backend/API.md` + `DESIGN.md` (design stack must be loaded; Playwright MCP on the built app).
3. `/design-review` on the live app, then the slice 2 spec (owner picks the first workflow from the evidence ranking).

## Gotchas
- Test env: no shared venv in the repo. Create `backend/.venv` (Python 3.12) from `backend/pyproject.toml`;
  `pymupdf` must stay `<2` (2.0 segfaults here) and PyMuPDF 1.28.0 is needed for fixture byte-identity.
- Eval venv: `tests/.venv_c`. Run backend tests from `backend/`, eval tests from the repo root.
- Not for rewriting markdown with Python open(...,"w") on Windows without preserving CRLF.
- Tesseract not installed (only matters for the local-OCR TODO). `yt-dlp` PATH quirk: see earlier note (Python312 Scripts first).
- Agent worktrees live under `.claude/worktrees/` (gitignored); they can be deleted.
- Fresh session recommended: this one has a long context.
