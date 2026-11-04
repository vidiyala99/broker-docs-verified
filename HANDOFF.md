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
- Cleanup: merged worktrees `agent-a845a68c406d45127` (own real node_modules, not a link) and `agent-ae8dda905eb9fe1b9` can be removed with `git worktree remove --force` + `git branch -d workqueue-ui workqueue-backend worktree-agent-a845a68c406d45127 worktree-agent-ae8dda905eb9fe1b9`. (Done 07:40.)

## 2026-11-03 09:30: voice + motion merged ("Paper trail"), PRODUCT.md added
- Main: merge of `voice-motion` (73d7a86). Verified on main: backend 1435 pass/2 skip, tsc clean, full e2e 54/54 with `--workers=1` (8 new in `e2e/voice-motion.spec.ts`), axe 0 (98 checks at 1440/1024/390). `npm ci` was needed on main (new font package `@fontsource` Source Serif 4); it fails with EPERM while Vite 5173 runs, so stop Vite first.
- Voice C "Paper trail" is the app voice: Source Serif 4 600 (latin subsets, metric-matched Georgia fallback) for page titles, section headings and client names; Inter for everything the system says. Drawn mark set in `frontend/src/voice/marks.tsx` (rail, queue rows, run header, wizard titles, Activity rows, empty states); reading mark replaces spinners where documents are read; seal lands once when a row reaches Done recently. Rules in DESIGN.md "Voice" + "Motion"; decisions in `frontend/VOICE-BRIEF.md`; before/after `frontend/qa-screenshots/voice/current-*` vs `final-*`.
- Motion audit (34 items) built: shared pointer-vs-keyboard helper `ui/input.ts` (keyboard = instant), reduced motion keeps fades, sliding segmented indicator, tab/list/route cross-fades, exits for dialogs/menu/overlay/split pane, interruptible toast and row animations.
- Focus-return fixes (real bugs caught by e2e): task dialog now focuses the new "Task: Open" link after the native close event; same for Tasks page save, row menu items, narrow-panel Hide.
- Owner accepted lane defaults (seal on opening Done recently, Tasks sort fades, Chat closes instantly, "+N more" staggered, serif on report section and empty-state titles, secondary Retry in Needs attention, marks on wizard titles only).
- Watch: all 13 rail glyphs share the sheet family and read alike at small size. Pre-existing: run toolbar export icons clip at 390; on short viewports the last queue row's menu opens below the fold.
- PRODUCT.md (root) committed as a DRAFT: three [assumed] lines (portfolio demo for the BrokerDocs application; small/mid independent agency user; desktop-first) await owner confirmation.
- Servers: none running (Vite 5173 was stopped for `npm ci`). Restart: backend with a fresh seed (old DBs fail the schema check), then `npm run dev` in frontend.
- Next candidates (owner picks): rail glyph legibility pass; template library; extraction cache 2c; Standard-template eval; slice 4 (COI) per `docs/slice-map.md`. Fresh session.

## 2026-11-03 10:50: rail glyphs (A) + queue navigation merged (main acc0744)
- PRODUCT.md owner-confirmed (7e3cfb6), no [assumed] lines left.
- Rail: direction A "Silhouettes" (`frontend/src/voice/railMarks.tsx`, 8 redrawn marks, 5 shipped kept). Brief `frontend/RAIL-BRIEF.md`, shots `frontend/qa-screenshots/rail/`. DESIGN.md Voice: no two rail items share an outline (check blurred at 20px). Open (owner did not take): Proposal mark elsewhere to the bound cover; left panel strip icons in the voice. Assistant/Insights is the one close pair (0.920 blur score), accepted.
- Queue nav: client name links to `#/runs/<id>` (quiet "Open" link removed); menu "Open comparison"; meta line has start date, carrier names when a client repeats; seed run 3 = "Halvorsen Dental Partners"; report header/breadcrumb/tab title name the client ("<client> · Compare Quotes", small "Run N, version V"), "Change" when a name exists. Backend: single-run responses carry `client`/`client_source`; queue items carry `created_at`, `carriers` (API.md). Brief `frontend/QUEUE-NAV-BRIEF.md`.
- Verified on main: backend 1441 pass/2 skip, tsc clean, full e2e 58/58 (`--workers=1`, against 8000/5173), axe 0.
- Lane choices owner may veto: Cedar Hollow twice in seed; locale time format; carriers replace "Compare Quotes ·" on repeated rows; phone hides workflow word and time (at 390 the two Cedar Hollow rows differ only by the 2nd carrier: suggested fix = date first on phone); faint underline at rest; "run" wording left in "See every run in Activity", "All runs", "Recent runs".
- Correction: the report toolbar's second row at 1440 is pre-existing on main, not a regression.
- Servers: backend 8000 (fresh seed `scratchpad/dev-data-1103c` of session 482d9799), Vite 5173 from main.
- Cleanup: worktrees `agent-a4dda978897d04506` (rail) and `agent-a25dd99f37bfb7d26` (queue) merged; own node_modules (npm ci), not links.
- Next candidates: "run" wording pass; phone row order; template library; extraction cache 2c; Standard-template eval; slice 4 (COI). Fresh session.

## 2026-11-03 11:00: "run" wording pass + phone queue rows merged (main 69ebf9b)
- Descriptive text no longer says "run": queue footer "See everything in Activity", report tab "Activity", left panel "Recent work", Activity subtitle "Every comparison, check and proposal, newest first.", upload limits "per comparison" (proposal drop zone "per proposal"), Personal lines hint, no-key hint, load errors "Couldn't load recent work.", cancelled "This was cancelled before a report was written.". Run numbers ("Run 5, version 1", "Compare Quotes, run 5", Tasks picker) stay as reference IDs (owner call).
- Phone (<700px): queue meta line wraps so carriers are never clipped (the two Cedar Hollow rows were "Harborline, N..." vs "Harborline, J..."; "date first" would not help, both are Nov 3). DESIGN.md updated; queue-nav e2e asserts no clipped summary at 390.
- Verified: tsc clean; full e2e 57/58 on the branch before the test update, the one failure was the old one-line-at-390 assertion; queue-nav suite 4/4 after; axe 0.
- e2e regenerates tracked qa-screenshots from a mutated seed: restore them (`git checkout -- frontend/qa-screenshots`) unless a shot is evidence for the change.
- Servers: backend 8000 + Vite 5173 still up; the 8000 DB was mutated by e2e, reseed before demoing.
- Next candidates: template library; extraction cache 2c; Standard-template eval; slice 4 (COI). Proposal-mark / left-panel icon calls still open. Fresh session.

## 2026-11-03 12:50: line move merged; slice 4 spec + eng review done; build next
- Main `f4b7f90`. Line move (B) merged (`397b515`): book tab + "Move to ..." button in the run header, book-first crumbs ("Commercial > ..."), toast with Undo; verified on main: full e2e 59/59, tsc clean. Brief `frontend/LINE-MOVE-BRIEF.md`.
- Owner decisions today: slice map updated (slice 4 = checked certificates next; slices 5/6 use labelled synthetic data); stay on Vite (no Next.js switch: no SSR/SEO need, backend is Python, ~1-2 days CC + full re-verify for no visible gain); gstack upgraded to 1.94.34.0; project CLAUDE.md with skill routing added.
- **Slice 4 spec:** `docs/specs/2026-11-03-certificates-design.md` (owner-approved), eng review + Codex outside voice done: ledger D1 to D18 at the end of the spec overrides the text above it. NO UNRESOLVED DECISIONS. Tasks C1 to C12 in the spec ("Implementation Tasks"); test plan `~/.gstack/projects/aakas/aakas-main-eng-review-test-plan-20261103-121919.md`.
- Key calls: claims stored in discrepancy_state with workflow-scoped rules (D1, D10, Policy Check pinned); Add document reopens certificate drafts only (D2); cache on sha256 + fields + prompts + extract/vision models + EXTRACTION_VERSION, cross-upload copy of pages/passages (D3, D8, D17); dev_seed uses BROKERDOCS_EXTRACTION_CACHE=off (D9); version-checked Issue and fingerprint-checked decisions (D5, D15); PDF bytes in the issue row (D16); description + edited values need sign-off rows, Remove removes content, conditional endorsements need a reason (D11 to D14); normalized policy-number join (D7); free text escaped and bounded (D6).
- **Next (fresh session):** launch lane A (backend core C1 to C9, Opus, worktree, own node_modules via npm ci, scratch ports) and lane B (fixtures + truth + offline gate C10, Opus) in parallel; merge A, run B's gates on main incl. live `-k certificate`; then UI lane C11 through the full design stack (Playwright MCP, max 3 rounds); docs C12 last.
- Open small items: Proposal mark / left-panel strip icons (owner calls); Tasks filter segmented control sizes to text on phone; failed line-move PATCH animates the old tab back in; "clicks to outcome" benchmark offered (spec section 8).
- Servers: backend 8000 (seed `dev-data-1103d` in session bb97e76c scratchpad) runs from this session and stops with it; Vite 5173 from main. Reseed before demoing.

## 2026-11-03 14:55: slice 4 backend (C1-C9) + fixtures/gate (C10) merged; live gate PASS with false alarms; fix lane running
- Main `e865167`. Merged lane A `slice4-backend` (4b915c4) and lane B `slice4-eval` (37357c0); reconciliation 7c8ab2c (gate fake reply uses the real `endorsements` array; `cert:producer` is backend-only, never graded); cache fix e865167 (a read made with the cache off stores no extraction_key, so dev-seed reads never serve e2e uploads; regression test in test_extraction_cache.py).
- Verified on main: backend 1557 pass/2 skip; offline eval 334 pass/4 skip (before e865167); tsc clean; e2e 57/59 on a fresh seed with the cache on. The 2 failures are personal-lines keyboard tests (`:139` ArrowRight on the line radios, `:282` Ctrl+Z undo) that pass alone and in a 4-spec run: flaky, frontend unchanged in slice 4; watch them.
- e2e note: with the cache on, journey.spec.ts passes only on a fresh seed (a rerun on a used DB reuses its own earlier read, so the reading mark never shows). Always reseed before e2e.
- Live certificate gate (`-k certificate`, ~54k tokens): PASS, 0 hard fails, recall 17/17, scalar fields 1.000. But supported_accuracy 0.533 (7 false alarms): endorsement-only PDFs become policy rows (model fills cert:policy_number from "forms part of Policy No."), breaking insured_consistent and the join; endorsement extraction weak (form_number/type/policy_number 3/9, condition 0/2); unmatched-endorsement notice missing. Report: session 305898a1 scratchpad `cert_live_report.json`.
- **Fix lane running** (Opus, worktree, branch `slice4-falsealarms`, max 2 live runs). If the session ends first, inspect that worktree and finish from its brief (above bullet).
- Lane A choices, owner may veto: endorsement type classified in code from the printed title; Remove allowed on any claim except insured (removing in_force drops that policy's wording rows); Override needs a note; certificate PDF shrinks type to stay one page; holder not saved without a client name; claim keys use document letters (eval hook maps to gate keys); name match ignores dots/commas.
- Slice map: Vault joins slice 6 (owner, today); then every rail item is live after slice 6.
- Next: merge fix lane + rerun live gate; then C11 UI (full design stack, Playwright MCP, max 3 rounds; `ClaimBlock`/`CertificateBlock` not yet in the frontend Block union, add with rendering); dev seed has no certificate demo run yet; C12 docs (CLAUDE.md EXTRACTION_VERSION rule, README cache switch). Fresh session.
- Memory: ~1-2 GB free; `wsl --shutdown` frees ~1.5 GB when vmmemWSL is up. No servers running.

## 2026-11-03 15:15: slice 4 false alarms fixed and merged (main bad0bab); backend + gate done
- Merged `slice4-falsealarms` (76d2a8f). Root causes: endorsement-only PDFs became policy rows (model filled cert:line and cert:policy_number from the endorsement's own text); edition doubled in form numbers ("OM 20 02 04 24 04 24", never verifies, wrong key); conditions over-read (grant/scope taken as a condition). Fixes: `coi_checks.join` needs a declarations-only fact (named insured, address, dates or a limit, any state) before a document that prints endorsements becomes a policy row; `extract.split_edition`; ENDORSEMENTS_NOTE defines a condition; EXTRACTION_VERSION "2026-11-03.2".
- Live certificate gate run 2 on the fix: 0 hard fails, recall 17/17, supported_accuracy 15/15, claim_accuracy 1.000, notices 2/2, fact_accuracy 0.988. Recorded replies replayed offline in `tests/eval/test_certificate_replay.py` (`tests/eval/recorded/`).
- Verified on main: backend 1572 pass/2 skip; offline eval 337 pass/4 skip.
- Open: `field_accuracy[...:condition]` 0/2 is an exact-match artifact (model quotes the whole sentence, truth stores the clause; verdicts right; loosening the matcher is an owner call); package document facts still listed in eval output (10, unused by claims); Policy Check `_form_facts` likely has the same doubled-edition bug (not changed, needs a live Policy Check eval); personal-lines keyboard e2e flakes.
- Fix-lane choice, owner may veto: carrier, NAIC, producer, policy number and line don't count as declarations-only (endorsements print them too).
- Next (fresh session): C11 certificates UI through the full design stack (all design skills, Playwright MCP, max 3 rounds; add ClaimBlock/CertificateBlock to the frontend Block union with rendering; add a certificate demo run to dev_seed); then C12 docs. Cleanup: lane worktrees agent-a3fb9836f6e2bb05d, agent-a098f4ab34af49f4c, agent-a5b8453ec7bf3f691 (merged; check for node_modules links before removing). (Done 15:21.)

## 2026-11-03 17:15: slice 4 complete (C11 UI + C12 docs merged, main 6941a65 + docs commit)
- C11 merged from `slice4-ui` (3d07064 phase A, d8a2fff phase B). Owner picked direction A "Proof and ledger": certificate sheet beside a check list (To resolve, Handled by you, Supported folded), hover/focus boxes the checked line, chips swap in the cited page, Issue disabled with the reason ("4 checks to resolve"). Certificates page + rail item live; 3-step wizard (Who with past-holder picks, Policies with "Read on <date>" marker, Read); issued view (read-only, Download PDF, New version); queue row "Issue certificate"; Activity lists certificate runs. D5/D15 409s reload/refresh and explain; D11/D12 sign-off rows; D13/D14 handled. Brief `frontend/CERTIFICATES-BRIEF.md` (3 design rounds), DESIGN.md "Certificates", shots `frontend/qa-screenshots/certificates/`.
- Owner calls (15:47, do not re-ask): direction A; certificate runs have no Chat, left panel (Policies, Input parameters) folded; Supported folded; removed lines leave the paper ("Put back on the certificate"); legal header + cancellation clause are lane-written sample wording marked "Sample wording" on screen and on the PDF (replace before any real use).
- Lane choices owner may veto: no left panel on phone for certificate runs; client optional in the wizard; Override/Remove/Confirm toasts offer Undo; "Read on <date>" marker also on Compare/Policy Check rows reused from cache; edited limit rows get no margin glyph.
- Seed: run 10 issued + run 11 draft for Calloway Build Group LLC (12 claims: 7 supported, 4 open, 1 overridden; past holder Larkspur Commons Owners Association).
- Verified on main: backend 1574 pass/2 skip; tsc clean; full e2e 61/63 on a fresh seed (`--workers=1`). The 2 failures (`certificates.spec.ts:93` wizard read-on marker, `tasks.spec.ts:104`) pass on a fresh seed when run alone (12/12) and passed in the lane's full run (63/63): order-dependent on shared seed state, cause not traced (full-run error text not captured). Same watch class as the personal-lines keyboard flakes.
- C12 docs: CLAUDE.md EXTRACTION_VERSION rule; frontend/README.md seed contents + cache switch + reseed-before-e2e; spec tasks ticked + "Amendments after build"; slice map: slice 4 Built. API.md already covered the cache.
- Open: issued record shows draft prose on sign-off rows ("confirm it with a reason, or remove it"); every decision makes a new draft version (seed run reaches v8 to v9); after Issue the sheet keeps the last pointed scroll; seeded docs show "not read for a certificate yet" (cache off in seed); condition exact-match scoring; Policy Check `_form_facts` doubled-edition bug likely (needs live Policy Check eval); Proposal mark / left-panel strip icons.
- Cleanup: lane worktree `.claude/worktrees/agent-aa39d6d2a3522bea4` (branches `slice4-ui`, `worktree-agent-aa39d6d2a3522bea4`) merged; check for a node_modules link before removing.
- Servers: backend 8000 (fresh seed `dev-data-c11` in session 9017f0bb scratchpad) stops with this session. Reseed before demoing.
- Next candidates: trace the two order-dependent e2e failures; slice 5 (Loss Run, synthetic data) brainstorm; template library. Fresh session.

## 2026-11-03 21:20: the two "order-dependent" e2e failures fixed (main e810446)
- Neither was order-dependent. `certificates.spec.ts:93`: uploads dedupe by sha256, so the fixed fixture only counted as a new upload on a never-used DB (on a used DB it lands in the client's documents as "Read on ..."). Now uploads its own copy (`cert_written_contract_gl--e2e<ms>.pdf`, PDF comment after %%EOF); `dev_seed._cert_truth` ignores a `--<tag>` stem suffix (unit test in `backend/tests/test_dev_seed.py`). `tasks.spec.ts:104`: `check()` on a toggle whose PATCH is forced to 500 raced the optimistic rollback; now `click()`, rollback still asserted.
- Verified: both tests x10 on a used DB: 7/20 failed before, 20/20 after; tsc clean; test_dev_seed 7/7. Full e2e 63/63 on a fresh seed before the fix; not re-run after (change is test-only).
- Pattern for future specs: any e2e upload that must be "new" should use unique name + bytes, not a fixed fixture. Playwright `check()` is wrong for toggles expected to roll back.
- Personal-lines keyboard flakes (`:139`, `:282`) passed in both full runs today; not reproduced, unchanged.
- Owner verdict on conference flyers (OLake, Datastrato/Gravitino, PuppyGraph): not relevant to the clone (lakehouse infra, no fit for SQLite + per-document extraction). Do not re-raise.
- Open (unchanged): issued record shows draft prose on sign-off rows; each decision makes a new draft version; issued sheet keeps old scroll; Policy Check `_form_facts` doubled-edition bug likely (needs live Policy Check eval, paid); owner calls: real legal wording, condition matcher loosening, Proposal mark / left-panel icons.
- Servers: backend 8000 (seed `dev-data-e2e2` in session 54b9b315 scratchpad, mutated by e2e) stops with this session. Reseed before demoing.
- Next candidates: slice 5 (Loss Run, synthetic data) brainstorm; template library. Fresh session.
