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

## Verified live
Key works; `gpt-5.4-mini` (extract/vision), `gpt-5.5` (compose/chat), `gpt-5.4-nano` (suggest) all answered a tiny call.
The full live eval (`pytest -m live`, via `backend/eval_hook.py`) has NOT been run. Prices in `providers/config.py` are stand-ins.

## Decisions made this session (do not re-ask)
Input border `#848D99` (DG10 3:1) kept; collapsed-outline warning mark = triangle glyph on the handle;
lane C: amended endorsement value is truth, cited-wrong-page counts as hard fail, canary `verified` = hard fail;
failed extraction shows "Extraction failed" + Retry (M1), budget exhaustion not sticky, all-docs-failed = FAILED run;
long docs: 300 pages, windows + merge, conflicting values shown with a notice (never silently picked), page-scoring skip pass OFF.

## Open items
1. **Known security gap (2 strict xfails in `backend/tests/test_api_security.py`)**: verify can mark an injected
   value verified if the model's quote fuzzy-matches the injection sentence itself (e.g. AM Best A++). Fix in
   `pipeline/verify.py`: tighten fuzzy matching when the matched span does not start with the field label.
   Claim-level validation is TODO R10. Recommend fixing before any live eval.
2. `tests/fixtures/quotes/over_cap_pages.pdf` (81 pp) is now under the cap; the live parse group still lists it
   and will report a mismatch (ungated). `long_301` (generated with `--include-oversize`) is the real over-cap test.
3. No DB migrations: delete any local `data/brokerdocs.db` created before today (new columns on `fact`, `message`, `run_document`).
4. Pyright in the editor flags `backend.*` imports; harmless (code runs from `broker-docs-verified/` with pythonpath).
5. Rotate the OpenAI key if transcript exposure matters (it appeared in an earlier session transcript).

## Next, in order
1. Fix the verify injection gap (small, Opus).
2. Run the live eval once on the final pipeline (spends real tokens; owner approved live calls).
3. T10 frontend from `backend/API.md` + `DESIGN.md`: shell, report view, chips, PDF viewer, all states in the spec state
   table, axe in the Playwright flow; use Playwright MCP on the built app. Design stack must be loaded (see global rules).
4. Then `/design-review` on the live app, and the slice 2 spec (owner picks first workflow from the evidence ranking).

## Gotchas
- Test env: no shared venv in the repo. Create `backend/.venv` (Python 3.12) from `backend/pyproject.toml`;
  `pymupdf` must stay `<2` (2.0 segfaults here) and PyMuPDF 1.28.0 is needed for fixture byte-identity.
- Eval venv: `tests/.venv_c`. Run backend tests from `backend/`, eval tests from the repo root.
- Not for rewriting markdown with Python open(...,"w") on Windows without preserving CRLF.
- Tesseract not installed (only matters for the local-OCR TODO). `yt-dlp` PATH quirk: see earlier note (Python312 Scripts first).
- Agent worktrees live under `.claude/worktrees/` (gitignored); they can be deleted.
- Fresh session recommended: this one has a long context.
