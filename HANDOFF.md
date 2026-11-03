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
T8: first full 23-doc run (11:37) failed on 1 hard fail (`long_300.policy_form`). Fix lane merged (boilerplate-line merge filter for lines repeated on 3+ pages, `page_number` and `definition_body` plausibility rules, `policy_form` description, row-order tier in verify). Full 23-doc run after the fix (12:45): gate PASS, 0 hard fails, 267/286 (0.934), clean 1.000, multi_column 1.000, long 0.900, tokens 440k. Remaining misses: `territory` in long_120/long_300 (known) and `long_300.policy_term` conflicting with 'Policy No. LG-773595' (page 170, a header-style line; a new instance of the conflict-noise class). `password_protected` misses are expected. A DB seeded earlier keeps the old policy_form description until re-seeded.
Wizard owner calls taken by default (not explicitly answered): dev_seed.py proposal prose merged; reorder = Move up/down buttons only (no drag); phone top bar shows only current breadcrumb app-wide.
Open gaps: chat copy on proposal runs ("Run a comparison..." in Chat.tsx); duplicate uploads in the picker (collapse by sha256); Preview hides chips only; section edit-open state resets between steps; PRODUCT.md missing; left-panel design fix (chunky stack, chat cannot collapse, collapse Upload when Done); slice 2c extraction cache; Standard template has no live eval coverage; Playwright MCP wrote files to `BrokerDocs/.playwright-mcp/` outside the repo.

## Session end 2026-11-02 13:20: all merged to main (backend 892 pass/2 skip, eval 240 pass)
Merged since the wizard: T8 fix lane; wizard small gaps (proposal chat copy, picker dedupe by sha256, Preview hides trust counts, open sections kept in draft); per-field time check in `merge.py` (`policy_term`, `quote_expiration` must state a date/year/duration; fixes 'Policy No. LG-773595' false conflict). Last full 23-doc live eval (12:45, before the time check): PASS, 0 hard fails, 0.934.
Open:
1. `frontend/e2e/journey.spec.ts:135` fails: seeded Compare run 5 lost its 'Conflicting values' cell. `dev_seed.py` made it from a repeated heading in long_120 (`Section I: Coverages`, 3 pages) that merge now correctly drops as boilerplate. Seed needs a conflict the new rules treat as real (tried: a 'Coverage Form' line on <3 pages, no conflict appeared; cause not found), or the test should use another run.
2. New false conflict seen once in a live long run: `long_300.quote_expiration` '45 days of the end of the policy period (page 6)' vs 'December 15, 2026'. Options: require a date, drop durations measured from the policy period, or leave (noise only).
3. Not started, need owner decisions: save-outline-as-template / bigger template library; left-panel design fix; extraction cache (2c); Standard-template eval coverage; PRODUCT.md.
4. Dev backend was reaped for low memory at 13:16; restart with a fresh seed: `BROKERDOCS_DATA_DIR=<scratch> BROKERDOCS_UI_ORIGIN=http://localhost:5173`, `python -m backend.dev_seed seed` then `serve` (venv: `backend/.venv`).

## Session end 2026-11-02 14:30: T8 follow-up merged (fbec2b0), live eval PASS
- Merged: seed conflict fix (page-60 aggregate misread as each-occurrence limit, test_dev_seed.py), quote_expiration needs a date, policy-period durations dropped. Backend 913 pass/2 skip, e2e 3/3 axe 0.
- Full 23-doc live eval on merged main: PASS, 0 hard fails, 268/286 (0.937), clean 1.000, multi_column 1.000, long 0.933, tokens 440k. Misses: territory in long_120/long_300 (known), password_protected (expected). Run live eval with the key loaded: set -a; . ./.env; set +a before pytest -m live.
- Open (owner decisions): template library, left-panel design fix, extraction cache 2c, Standard-template eval, PRODUCT.md. Stale: qa-screenshots/1440-state-conflicting.png (regenerate with own node_modules). Possible merge false positive: heading 'Schedule of Forms and Endorsements' read as amendment marker (unseen today). Worktree .claude/worktrees/t8-followup has a node_modules junction (remove before deleting).

## 2026-11-02 15:05: left-panel fix merged to main
Upload folds when Done ("N documents, all read"), compact document rows with "+N more" past 6, Chat collapse handle. Brief: frontend/LEFT-PANEL-BRIEF.md; screenshots frontend/qa-screenshots/left-panel/. Branch e2e 7/7, axe 0; tsc clean on main (e2e not re-run on main). Owner calls: "Add or replace" copy dropped (needs a real action), failed run repeats its error in header and rows (G6), PRODUCT.md missing. OpenAI key rotation dropped by owner. Worktrees t8-followup and left-panel under .claude/worktrees can be deleted.

## 2026-11-02 15:20: main verified, ready for slice 3
- e2e on `main` (5a368bc): 7/7 pass, axe 0 at 1440/1024/390 (scratch backend on 8010, UI 5183). Left-panel merge is confirmed.
- Not done: worktree cleanup. About 28 stale worktrees/branches under `.claude/worktrees/` (all branches merged; only venvs, egg-info, screenshots uncommitted). Remove the `t8-followup/frontend/node_modules` junction with `cmd /c rmdir` first, unlock `agent-a8111b1d5360d15d0`, then `git worktree remove --force` and `git branch -d`. Also delete untracked `compare_eval*.log/json` in the repo root. The auto-mode classifier blocked this, so the owner runs it or adds a Bash permission rule.
- Next: slice 3 brainstorm. Read `docs/reference/brokerdocs-workflows-evidence.md`. Lean: data-only pack (reuses extract/verify, no new UI paradigm). Ask first: is the aim a verified-citation engine demo (favours Policy Check) or visual fidelity to BrokerDocs?
- Still open (owner decisions): template library, extraction cache 2c, Standard-template eval, PRODUCT.md.

## 2026-11-02 16:35: slice 3 Policy Check spec reviewed (eng review done, build not started)
- Spec + eng review ledger: `docs/specs/2026-11-02-policy-check-design.md` (spec committed at d2ad81d; the Engineering review section, the ledger D1 to D17 and `TODOS.md` entry are uncommitted). The ledger overrides the spec text above it.
- Key decisions: AMS file rendered to a PDF at upload and facts mapped by code (D4, D5); roles via `RunDocument.slot`/`input_slots` (D8); `DiscrepancyBlock` in the `Block` union (D7); forms from one combined extraction pass, no second read (D13, ceiling is 500k per document); deterministic discrepancy text (D14); renewal-centered compare (D10); Needs review for conflicting/unverified values (D15); Not read for failed optional documents (D11); eval gate also blocks on recall below 0.80 and wrong-field values (D16); bulk endpoint and `accepted` status cut (D1, D2).
- Next: build lanes T1 to T8 (backend, Opus per model policy), then T9 UI through the design stack. Task list in the spec; test plan in `~/.gstack/projects/aakas/aakas-main-eng-review-test-plan-20261102-163144.md`.
- Fresh session recommended: this one is long.

## 2026-11-02 19:50: slice 3 Policy Check backend built and merged (T1 to T8 + live fix)
- Main `e8eac21`. Backend offline 1224 pass/1 skip; eval offline 276 pass/1 skip (checked on 606bdbd, before the reading-order fix).
- Merged lanes: T1 AMS ingest (`backend/ams.py`, `POST /api/documents/ams`, rendered to PDF, `<uuid>.orig` sidecar); T2 run slots (`slots` on POST /api/runs, 400 invalid_run, required-slot failure, Not read); T3 combined extraction with `form:<n>` keys (`POLICY_CHECK_FIELDS` in schemas.py); T4 `pipeline/diff.py` (renewal-centered, severity table, deterministic text); T5 `DiscrepancyBlock`; integration `pipeline/policy_check.py` (no model call in compose, regenerate in code); T6 `discrepancy_state` + GET/PATCH `/api/runs/{id}/discrepancies`; T8 D12 regression tests; T7 fixtures `tests/fixtures/policy/` + D16 gate.
- Live Policy Check eval: run 1 FAIL (two-column renewal schedule read values-then-labels). Fix: `parse.reading_text` rejoins label/value columns row by row (`extract.page_text`, also chat). After fix 2 live runs PASS: recall 13/13, precision 13/13, 0 hard fails (~15k tokens per run).
  Command: `set -a; . ./.env; set +a; backend/.venv/Scripts/python -m pytest -m live tests/eval/test_live_eval.py -k policy_check -q -s`.
- Full 23-doc Compare Quotes live eval re-run on e8eac21 because the reading-order fix changes carrier_a..d multicol p2 text: result in `compare_eval_e8eac21.log` (see next entry).
- Choices taken by agents, owner may veto: policy_term not compared expiring vs renewal; agent-set severities (premium up Medium, deductible down Medium, limit added Medium, text change Medium, policy number Low, discount amount Low); AMS effective/expiration date and carrier go to "not compared" notice; slot `accepts` enforced (AMS cannot enter Compare Quotes); undo restoring values restores resolved; PDF/XLSX exports draw discrepancies; slot errors 400 while older invalid_run stays 422.
- Residual: reading_text skips word-only value columns, wrapped labels, 3+ column tables; one-call duplicate form editions keep first; long_300 plans 467k of 500k with forms schema.
- Next: T9 Policy Check UI through the design stack (all design skills, Playwright MCP, max 3 rounds; roles step incl. AMS, list with severity chips + Hide resolved, cards with four slots, review modal); T10 docs (spec amendments, TODOS.md R13 coverage-identity entry, API.md for slots + AMS endpoint). Fresh session.
- 19:53 full 23-doc Compare Quotes live eval on e8eac21: PASS, 0 hard fails, 267/286 (0.934), multi_column 1.000 (no regression from reading_text), clean 1.000, long 0.933, scanned 0.983, 440k tokens. Misses: territory long_120/long_300 (known), password_protected (expected), new one-off carrier_c_scanned.retention not_found (scanned bucket; watch). Slice 3 backend is done.

## 2026-11-02 20:45: slice 3 Policy Check complete (backend + UI merged)
- Main `71730ab`. T9 UI merged: Policy check rail item live (`#/policy-check/new`), 3-step wizard (Documents incl. AMS upload, Roles, Check), discrepancy list (severity toggles, Hide resolved, four-slot cards, resolve + note), review dialog with cited source side by side. Brief `frontend/POLICY-CHECK-BRIEF.md`; screenshots `frontend/qa-screenshots/policy-check/`. e2e 10/10 axe 0 at 1440/1024/390; backend 1233 pass/1 skip; tsc clean on main.
- Fix pass: PdfViewer drew a new document's highlight over the previous document's page while loading (also affected Compare Quotes tab switches); fixed + e2e regression. AMS conflict notice uses field labels (`ams.FIELD_LABELS`); failed-docs notice says "the Upload panel shows why".
- T10 docs merged (API.md slots, AMS endpoint, DiscrepancyBlock, slot states; spec "Amendments after build"; TODOS "Policy Check residuals").
- Owner calls open: AMS chip label "AMS · Cov C" (field name from the cited text) kept vs page chip; chat suggestions on Policy Check runs are quote-oriented (untested live); PRODUCT.md missing; API.md intro still says slice 1.
- Dev stack running: backend 8000 (seed `scratchpad/dev-data-main2`, runs 1 to 8, Policy Check = runs 7, 8), Vite 5173 from main.
- Next: slice 3b rail completion per `docs/slice-map.md` (Personal lines filtered entry, broker-defined Tasks, Integrations mock page with "not designed yet, this is a mock" note). Design pass first. Fresh session.

## 2026-11-02 20:55: slice 3b spec approved (no code yet)
- Spec: `docs/specs/2026-11-02-rail-completion-design.md` (commit 586c79f), owner-approved. Decisions R1 to R7 inside; do not re-ask.
- Summary: `Run.line` (commercial/personal) + PATCH /api/runs/{id} + `?line=` filter; Personal lines = filtered run list + two pre-tagged wizard entries (runs 7, 8 seeded personal);
  Task table + CRUD (title, due date, open/done, optional run/discrepancy link), client-side search/sort/filter, "Create task" on discrepancy cards, task and discrepancy states independent;
  Integrations = static mock with a visible "not designed yet" notice, no logos.
- Defaults taken in the spec (owner may veto): delete uses undo toast, no confirm; discrepancy card shows first open linked task, else latest.
- Next: `/plan-eng-review` on the spec (fresh session), then backend lane (Opus), then UI lanes through the design stack (Integrations, Personal lines, Tasks).
- Dev stack may still be running (backend 8000, Vite 5173).

## 2026-11-02 22:50: slice 3b T1 to T6 merged; work queue spec written; Tasks lane still running
- Main `cb647cf`. Merged: backend lane T1 to T4 (`33b20cb`: Run.line, PATCH /api/runs, `?line=`, Tasks API, fail-fast on old DB, seed),
  Integrations mock T5 (`a26def6`), Personal lines T6 (`16a05d7`). Eng review of slice 3b: D1 to D11 in
  `docs/specs/2026-11-02-rail-completion-design.md` (ledger overrides spec text). Backend 1315 pass/2 skip (checked on the branch);
  tsc clean on main after each merge. Lane e2e (agent-reported, on their branches): Integrations 16/16, Personal lines 24/24, axe 0.
  Full e2e has NOT been run on main after the merges: do that first.
- Old-test edits so far (D7 exceptions, commented): disabled-rail count in journey.spec.ts and policy-check.spec.ts (10 -> 8),
  journey's "Personal lines coming later" check now checks the live link.
- **Tasks lane T7 still running** as a background agent in worktree `.claude/worktrees/agent-a0592be45707cfcc1`, branch `slice3b-tasks`
  (it hit a transient API SSL error once and was resumed). If the session ends before it reports: inspect that worktree
  (`git -C <worktree> log/status`), finish or rerun T7 from the brief in the slice 3b spec (sections 2.2, 2.3, D2/D3/D6/D8/O3/O4/O7),
  ports 8030/5195. Told not to touch `frontend/src/launch/RecentRuns.tsx`.
- **Owner rejected the run-list design** (22:44): Personal lines / Workflows showing raw runs is an engine view with no next action.
  New spec `docs/specs/2026-11-02-work-queue-design.md` (cb647cf), decisions W1 to W6: work queue grouped Needs review / Ready /
  Needs attention / Done recently (+ In progress strip), rows named by client (broker-typed Client field, fallback extracted
  named_insured), Activity page `#/activity` for raw runs, items leave the queue when work is done, no extraction change.
  Status: written, NOT yet owner-approved. Next: owner review, then `/plan-eng-review`, then backend lane, queue UI lane, motion lane.
  The table redesign of RecentRuns proposed at 22:43 is withdrawn.
- **Motion pass (W5)**: owner flagged that segmented controls (All/Open etc.), tabs and list filters have no transitions app-wide.
  Separate lane after Tasks merges: `improve-animations` audit first, shared segmented control with sliding indicator, reduced-motion.
- Owner calls taken by lane agents (owner may veto): Integrations omits the evidence shot's search/filter/badges (R7); ImageRight stays in
  agency group; rail on a run page highlights the workflow, not Personal lines; re-tag shows a success toast without Undo; Policy Check line
  choice at top of step 1, Compare's in Upload above the button. Backend: bad `?line=` is 422 like `severity`; POST /api/tasks rejects
  unknown keys; PATCH without `discrepancy_item_id` does not re-validate an existing link.
- Pre-existing issues seen, not fixed: run toolbar actions clipped ~60px at 390; run breadcrumb flashes "Workflows / Compare Quotes" while loading;
  PRODUCT.md missing (every design lane flags it).
- Dev stack: backend 8000 restarted on main code with a fresh seed at
  `C:\Users\aakas\AppData\Local\Temp\claude\C--Users-aakas-Documents-JobHunt-BrokerDocs\23f47f7d-be6b-4b5f-b308-230d1734b5b5\scratchpad\dev-data-3b`
  (runs 1 to 8, 7/8 personal, two tasks); Vite 5173 from main. Old DBs now refuse to start (D10): reseed with `python -m backend.dev_seed seed`.
- Cleanup pending: merged lane worktrees under `.claude/worktrees/` (agent-a76ce5b93a99de08c, agent-a4e0b986261be2a61, agent-ab9097927fb1621d0)
  and branches slice3b-backend/-integrations/-personal-lines can be removed; untracked compare_eval*.log/json in repo root.
- Fresh session recommended.

## 2026-11-02 23:12: slice 3b complete (T7 merged); work queue spec eng-cleared
- Main `582d11b`. T7 Tasks UI merged (`8aa474c`): `#/tasks` page, dialog, Create task on discrepancy cards, delete with Undo, deep links. Full e2e on merged main: 32/32, axe 0; tsc clean. Every slice 3b rail item is live.
- Work queue spec `docs/specs/2026-11-02-work-queue-design.md` (`164c12a`): owner-approved, eng review + Codex outside voice done. Ledger R1 to R11 and amendments M1 to M8 override the text above them. NO UNRESOLVED DECISIONS.
  Key calls: Compare client fallback = majority verified named_insured (D1); `queue_changed_at` drives Done recently (D2); comparison leaves Ready only when its proposal is done (D3); select by state, cap 500 (D4); poll queue every 3 s while something runs (D5); Build proposal replaces draft with Undo (D6); shared `discrepancy_counts` over current blocks (D7, M6); Retry reopens (D8); Undo on Mark done/Dismiss (D9); cancelled = Needs attention (D10); Activity paging deferred to TODOS (D11).
  Test plan: `~/.gstack/projects/aakas/aakas-main-eng-review-test-plan-20261102-2315.md`.
- Next: backend lane (Opus) per the spec's task list, then Queue UI + Activity lane (design stack), then motion lane (W5). Fresh session.
- T7 open items: backend reuses deleted task ids (SQLite without AUTOINCREMENT; weakens Undo same-id promise); Tasks list does not refetch on hash-only change; chip spacing "...  · Open" at 1440; pre-existing run toolbar clipped at 390; PRODUCT.md missing.
- **Worktree pitfall:** lane worktrees link `frontend/node_modules` to main's folder; `git worktree remove` deleted main's packages through the link (repaired with `npm install`, lockfile restored). Before removing a worktree, delete its `node_modules` link first (`cmd /c rmdir <wt>\frontend\node_modules` removes only the link). Remaining worktrees: T7 (`agent-a0592be45707cfcc1`, merged) and 8 older merged ones; stray dir `agent-ab9097927fb1621d0` left after a failed removal.
- Dev stack: backend 8000 (seed dev-data-3b), Vite 5173 from main; restart Vite if a page fails to load an icon/font package.

## 2026-11-02 23:45: work queue backend lane merged
- Merged `workqueue-backend` (96dd10c) into main. Verified on main: backend 1434 pass/2 skip, tsc clean, full e2e 32/32 axe 0 (scratch stack 8040/5200, fresh seed). Eval suite not re-run (no pipeline change).
- Built: Run.client_name/queue_state/proposal_run_id/queue_changed_at + index + schema check; POST client_name/source_run_id; PATCH any subset of line/client_name/queue_state (409 run_active per M1); `models.discrepancy_counts`; `backend/work_queue.py` (classify + batched load_queue) and `GET /api/queue?line=`; runner terminal bookkeeping `work_queue.on_run_terminal`; retry reopens; seed run 9 = failed proposal from run 4 (Ready, "Proposal didn't finish"); API.md; `frontend/src/api/types.ts` Queue/QueueItem.
- Lane choices, owner may veto: module named `work_queue.py` (a `backend/queue.py` shadows stdlib `queue` under pytest); partial proposal counts as built; PATCH client_name null clears, POST ""/null = no name; explicit line beats inherited line; Compare majority compares names case/space-insensitively, verified facts only; extra Item fields (line, status, retryable, queue_changed_at, proposal_run_id); extra reasons "Interrupted by a restart", "Stopped with an error"; latest proposal overwrites proposal_run_id; `open_counts` includes `critical` (spec listed high/medium/low).
- Seed now has 9 runs (run 9 is a commercial proposal, visible in today's Recent runs until the queue UI replaces it). The dev DB on 8000 predates the new columns: reseed before reuse.
- Next: Queue UI + Activity lane (Opus, full design stack, Playwright MCP, max 3 rounds), then motion lane (W5). Fresh session.
- Cleanup: stray dirs `.claude/worktrees/t8-followup`, `agent-a9326185a76821a66`, `agent-ab9097927fb1621d0` (unregistered, no junctions) can be deleted by hand; backend lane worktree `agent-ae8dda905eb9fe1b9` (merged, no node_modules link) can be removed.

## 2026-11-03 07:45: work queue UI + Activity merged (main 2231f30)
- Verified on main: backend 1435 pass/2 skip, tsc clean, full e2e 46/46 (14 new in `frontend/e2e/queue.spec.ts`), axe 0 (lane: 77 checks at 1440/1024/390).
- Built: queue on `#/` (Compare main area, tab "Work queue") and `#/personal-lines`; rows by client with one primary action (Review / Build proposal / Retry / Retry proposal); overflow Mark done / Dismiss / Open run / Move (+ Undo toasts); Build proposal preselects docs, client, source_run_id, line, replaces draft with Undo; Retry proposal reruns the failed proposal (backend `proposal_retryable` on queue items); Client field on both start screens + run header (`run/RunClient.tsx`); Activity `#/activity` with line filter + 100 cap note; poll 3 s while in progress. Motion primitives `frontend/src/ui/motion.tsx`/`motion.css` (row enter/leave, group height, count pop, live line, reduced-motion = fades only). Brief `frontend/QUEUE-BRIEF.md`; screenshots `frontend/qa-screenshots/queue/`; DESIGN.md "Work queue" section.
- Lane choices, owner may veto: quiet "Open" link on rows with no next step; "name from the documents" tag on extracted names; Activity highlights Personal lines or Workflows in the rail; run-header "Client" word only at 1600px+; Compare empty state keeps the drop zone + "Check a renewal instead".
- **Owner verdict on look (07:25):** functional, but still reads as typical SaaS. Icons are stock Phosphor duotone; font proposal (Inter opsz + cv11/ss alternates, `font-proposal-before/after.png`) is near-invisible, not worth applying. Structure stays (from BrokerDocs evidence).
- Next: ONE combined lane, product voice + app-wide motion (W5): bolder icon language tied to insurance work (drawn marks, not a stock set), distinctive header/client-name type treatment within the BrokerDocs structure, colour/texture with meaning; then the motion audit (`improve-animations`), shared segmented control with sliding indicator, tabs, list filter fades, reusing `ui/motion.tsx`. Full design stack, Playwright MCP, max 3 rounds, owner sees before/after. Fresh session.
- Known: 390 severity counts wrap mid-phrase ("1 / medium"); run toolbar clips at 390 (pre-existing); PRODUCT.md missing; Playwright MCP logs in `BrokerDocs/.playwright-mcp/`.
- Servers: only Vite 5173 (main). Backend 8000 is off (reaped for memory); its DB predates the queue columns, so reseed before starting.
- Cleanup: merged worktrees `agent-a845a68c406d45127` (own real node_modules, not a link) and `agent-ae8dda905eb9fe1b9` can be removed with `git worktree remove --force` + `git branch -d workqueue-ui workqueue-backend worktree-agent-a845a68c406d45127 worktree-agent-ae8dda905eb9fe1b9`.
