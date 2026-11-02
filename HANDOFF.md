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

## Live eval (run 5, 2026-11-02, final pipeline)
Models `gpt-5.4-mini` (extract/vision), `gpt-5.5` (compose/chat), `gpt-5.4-nano` (suggest). Prices in `providers/config.py` are stand-ins.
Run 5: **0 hard fails, 0 injection canaries verified**, clean 1.000, multi-column 0.983, scanned 1.000, report-only 1.000,
overall 264/286 (0.923), citation verification 0.971, 437k input tokens for the whole eval.
Only gate red: `field_accuracy[long]` 0.833 (25/30; needs 0.85 = 26/30). Run 4 scored 0.867 (26/30): it flickers by one field,
so the long gate sits on a noise boundary with 30 fields. Per-field misses are not stored in the report json (add that before
tuning further). History: run1 31 hard fails (harness bugs + 300-page budget), run2 4, run3 5, run4 1, run5 0.
Reports: scratchpad `live_eval_report{1..5}.json`.

## Decisions made this session (do not re-ask)
Input border `#848D99` (DG10 3:1) kept; collapsed-outline warning mark = triangle glyph on the handle;
lane C: amended endorsement value is truth, cited-wrong-page counts as hard fail, canary `verified` = hard fail;
failed extraction shows "Extraction failed" + Retry (M1), budget exhaustion not sticky, all-docs-failed = FAILED run;
long docs: 300 pages, windows + merge, conflicting values shown with a notice (never silently picked), page-scoring skip pass OFF.

## Open items
1. Long-document accuracy gate flickers at 0.833 to 0.867 (see above). Options for the owner: measure with 3 runs, add
   per-field miss detail to the eval report, or set the long gate by sample size (30 fields makes 0.85 = one field).
2. Residual injection bypasses: a note to the AI followed by a separate declarative sentence; imperatives outside the verb
   list. Claim-level checks remain TODO R10.
3. A database seeded before the `policy_form` description change keeps the old description (seed only inserts if absent);
   add re-seed/update at startup before anyone keeps a persistent DB.
4. `over_cap_pages.pdf` is obsolete (kept on disk); `long_301` (--include-oversize) is the over-cap test.
5. No DB migrations: delete any local `data/brokerdocs.db` created before 2026-11-02.
6. Rotate the OpenAI key if transcript exposure matters.

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
