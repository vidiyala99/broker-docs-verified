# Slice 3b: rail completion (Personal lines, Tasks, Integrations)

Date: 2026-11-02. Status: design approved in chat, spec awaiting owner review.
Source plan: `docs/slice-map.md`. Evidence: `rea-out/shots/personal-lines.png`, `rea-out/shots/integrations.png`.

## Goal
Every rail item is either a working screen or an honest mock. No invented data presented as real.
After this slice no rail item is disabled.

## Owner decisions (brainstorm 2026-11-02, do not re-ask)
| # | Decision |
|---|---|
| R1 | "Personal lines" uses the industry meaning: household cover (homeowners, auto, renters, umbrella), not "lines personalised to the broker". |
| R2 | Personal lines = tagged runs (option A): a `line` field on runs, a filtered run list, two pre-tagged entry buttons. No new engine, no homeowners field set for Compare Quotes (that would be its own slice). |
| R3 | Tasks fields: title, optional due date, status open/done, optional link to a run or one discrepancy. No free-text note, no assignee, priority, comments or reminders. |
| R4 | Tasks need search and sorting (plus an Open/Done/All filter). |
| R5 | Task and discrepancy states are independent: neither changes the other; the task shows the discrepancy's live state. |
| R6 | Tasks are stored in the backend database. |
| R7 | Integrations: static mock, vendor names as text with a generic icon, no real logos, Connect disabled. |

## 1. Backend

### 1.1 `Run.line`
- New column `line: str`, values `"commercial" | "personal"`, default `"commercial"`. Existing rows read as commercial.
- `POST /api/runs` accepts optional `line`; an invalid value returns 400 `invalid_run` (same family as slot errors).
  `line` is not part of the duplicate-run check; a reused active run takes the requested line (SC3/D4).
- New `PATCH /api/runs/{run_id}` with body `{"line": ...}`; only `line` is patchable. 404 for an unknown run, 400 for a bad value.
- `GET /api/runs?line=personal|commercial` filters; no parameter returns all runs (current behaviour).
- `line` is included in every run payload.
- No migration framework: delete local `data/brokerdocs.db` after this change (same rule as earlier slices). Startup fails fast with
  that instruction when `run.line` or the `task` table is missing (O8/D10).

### 1.2 `Task` table
| Column | Type | Rule |
|---|---|---|
| id | int pk | |
| workspace_id | fk workspace | indexed, same scoping as runs |
| title | str(200) | required, trimmed, 1 to 200 chars |
| due_date | date, nullable | |
| status | str | `open` or `done`, default `open` |
| run_id | int, nullable | plain integer, no FK (SC1/D2); must name an existing run on write |
| discrepancy_item_id | str, nullable | only valid with `run_id`; must be in that run's current report (D3) |
| discrepancy_title | str(300), nullable | snapshot of the block title at link time, refreshed while in report (O2/D8) |
| created_at, updated_at | datetime | |
| completed_at | datetime, nullable | set when status becomes done, cleared when reopened |

### 1.3 Task endpoints
- `GET /api/tasks`: all tasks of the workspace. Each item adds read-only fields:
  `run_label` (the label the app already shows for a run), `run_missing` (true when `run_id` was set and the run is gone),
  `discrepancy` (`{item_id, title, status, in_report}` or null; `status` is the live discrepancy state from its state row;
  `in_report=false` when the item is not in the current report version, SC2/D3).
- `POST /api/tasks`: `{title, due_date?, run_id?, discrepancy_item_id?}`; 201 with the task.
- `PATCH /api/tasks/{id}`: any of `title, due_date, status, run_id, discrepancy_item_id` (null clears).
- `DELETE /api/tasks/{id}`: 204. Undo is client-side: the DELETE is sent only when the undo toast closes (C1/D6).
- Validation errors: 400 `invalid_task` with a field-level message (empty/too-long title, bad status, unknown run,
  discrepancy without run, discrepancy not in that run). Unknown task id: 404.
- `GET /api/runs/{run_id}/discrepancies` items gain `task: {id, status} | null` (first open task linked to that item, else latest).
- R5: no endpoint changes discrepancy state from a task or task state from a discrepancy.

### 1.4 Seed
- `dev_seed.py` tags Policy Check runs 7 and 8 (homeowners) as `personal`. No new fixtures.
- Seed two tasks: one free-standing with a past due date (shows overdue), one linked to a run 7 discrepancy.

## 2. Frontend
All three screens go through the full design stack (gstack, ui-ux-pro-max, taste-core + taste-redesign, impeccable,
emil-design-eng, frontend-design) with the Playwright MCP, max 3 review rounds. Tokens from `DESIGN.md` / `tokens.css`.
The layouts below are the functional contract; visual treatment is decided in the design pass.

### 2.1 Personal lines (`#/personal-lines`)
- Header "Personal lines", subline "Homeowners, auto and other household policies."
- Actions: "Compare homeowners quotes" (Compare Quotes wizard) and "Check a renewal" (Policy Check wizard), both open with `line=personal`.
- Run list: the existing Workflows run-list component with `?line=personal&limit=100` (newest 100 runs, SC6/D5).
- Empty state: "No personal-lines runs yet." plus the two actions.
- Both wizards gain a line choice on step 1 (Commercial / Personal), defaulted from the entry point (rail Workflows = commercial).
- Run header gains a small Commercial / Personal control (PATCH). This is the only place to re-tag a run.
- Workflows' recent-runs list shows commercial runs only (`?line=commercial`, O6/D9); a re-tagged run moves between the lists.

### 2.2 Tasks (`#/tasks`)
- Toolbar: search box, Open / Done / All segmented filter (default Open), "New task" button.
- Search: client-side, case-insensitive substring over title and `run_label`, live as you type.
- Sort: client-side by due date (default, ascending, no-date last), created date, title A to Z; sortable column headers
  with visible direction and `aria-sort`; click again reverses.
- Columns: done checkbox, title, due date (overdue = past due and open, shown with the warning token), linked item, created.
- Linked item: run label; for discrepancies also a chip "<discrepancy title> · <Open|Resolved>". Click opens the run with the
  discrepancy scrolled into view and highlighted. `run_missing` shows "Linked run removed" (not a link).
- New / edit: one dialog with title, due date, link picker (run, then optional discrepancy of that run).
- Delete: row overflow menu, no confirm, undo toast (existing cell-edit pattern).
- Empty states: no tasks at all ("No tasks yet. Create one, or add one from a Policy Check discrepancy."); no matches for
  the search or filter (with a clear action).

### 2.3 Create task from a discrepancy
- Policy Check discrepancy card gains "Create task". It opens the task dialog prefilled:
  title "Follow up: <discrepancy text, truncated to 200>", link = this run + item.
- When a task exists the card shows "Task: Open" / "Task: Done" linking to `#/tasks` with that task highlighted.

### 2.4 Integrations (`#/integrations`)
- Persistent notice at the top: "Integrations are not designed yet. This page is a mock; nothing connects."
- Groups: Agency management systems (Applied Epic, AMS360, Nexsure, HawkSoft, ImageRight);
  Documents and email (Gmail, Outlook, Google Drive, OneDrive, SharePoint).
- Card: vendor name (text), one-line neutral description, generic icon, disabled "Connect" with a "Mock" tooltip.
  No logos, no fake connection states.

### 2.5 Rail
Personal lines, Tasks and Integrations become live items. Disabled-item handling stays for future items.

## 3. Testing
- Backend: `line` default, POST/PATCH validation, `?line=` filter; task CRUD, every `invalid_task` case, workspace scoping,
  `completed_at` set/clear, `run_missing` when a run row is removed (direct DB delete, since no API deletes runs),
  discrepancy live state in the task list, `task` field on discrepancy items, R5 independence both ways; seed test.
- e2e (Playwright, scratch backend on 8010): personal filter shows runs 7/8 and not Compare runs; re-tag a run;
  create a task from a discrepancy and see it on Tasks; search + each sort + filter; delete + undo; Integrations notice
  and disabled Connect. axe 0 at 1440, 1024, 390.

## 4. Build order
1. Backend lane (Opus): 1.1 to 1.4 with tests, `backend/API.md` updated.
2. UI lanes through the design stack: Integrations, then Personal lines, then Tasks + discrepancy action.

## Out of scope
Homeowners field set for Compare Quotes; real integrations or OAuth; task notes, assignees, priorities, reminders;
server-side search; run deletion.

## Engineering review (plan-eng-review, 2026-11-02)
Target: this spec (fixed). Report file: this spec. Decisions R1 to R7 are binding.

### Scope Challenge A: findings
Evidence kind: static read of current code on main (83e376b).
- SC1 [P2] (confidence: 9/10) spec 1.2 `run_id ... ON DELETE SET NULL` vs 1.3 `run_missing (true when run_id was set and the run is gone)`:
  `backend/db.py:49` runs `PRAGMA foreign_keys=ON`, so SET NULL erases the link and `run_missing` can never be computed. Contradiction.
- SC2 [P2] (confidence: 8/10) spec 1.2 "must exist in that run's discrepancy list": `backend/models.py:358` "an item missing from the
  current version: row untouched (it may come back)". A linked item can drop out of the current version after a regenerate; the spec
  has no display or validation rule for that.
- SC3 [P3] (confidence: 8/10) `backend/pipeline/runner.py:610-613` dedupes an active run on documents, slots and `parameters` only;
  if `line` is a column it is not part of identity, so a quick resubmit with a different line returns the active run with the old line. Spec is silent.
- SC4 factual (no question): runs take `workspace_id` from the workflow (`runner.py:618`) and `list_runs` does not filter by workspace
  (`routes/runs.py:76-79`). "Same scoping as runs" therefore means: tasks get `DEFAULT_WORKSPACE_ID`, no workspace filter.
- SC5 factual (no question): there is no backend run label; the UI shows `Run ${id}` (`frontend/src/App.tsx:86-90`) and `run_out` has
  `workflow_name` (`backend/views.py:59`). `run_label` = `"<workflow_name> · Run <id>"`.
- SC6 [P3] (confidence: 8/10) `routes/runs.py:76` caps `GET /api/runs` at 100 (default 20). The Personal lines list and the task
  link picker see at most the newest 100 runs. Acceptable for a single-user clone; state it in the spec.
- Prior learning checked: brokerdocs-block-union-traversal (confidence 9/10, 2026-11-03): n/a, no new Block type.
- TODOS.md: no task-related entries; nothing blocks this plan.

Complexity: about 9 backend files (models, routes/runs, new routes/tasks, app wiring, views, dev_seed, API.md, 3 to 4 test files)
and about 15 frontend files; new classes: Task model, task router + request models, PersonalLines, Tasks, TaskDialog, Integrations
[estimated from the spec sections]. Gate trips (8+ files, 2+ new classes).

REALITY: premise-table pass 6 claims checked, unverified=0 (receipts above)
REALITY: already-done pass no Task model or line field on main (backend/models.py:149-167)
REALITY: surface-check finding PATCH /api/runs/{id} does not exist yet (routes/runs.py:46-221), new route as planned
REALITY: binding-decisions pass R1 to R7 respected
REALITY: numbers pass file counts tagged [estimated]
REALITY: deferred-asks pass search + sort (R4) present in 2.2
REALITY: pr-count pass one backend lane + 3 UI lanes, merged to main per lane (project practice, no PR queue)
REALITY: transaction-semantics n/a no driver or pool change

### Scope Challenge B: scope record
feature answers: none proposed; structure: A Original arrangement (D1, owner 2026-11-02); accepted scope: the spec as written, one backend
lane + three UI lanes; pending remedies: SC1, SC2, SC3, SC6. Scope Challenge result: scope accepted as-is.

## Decision ledger

### SC1: how a task keeps a link to a run that no longer exists
Finding: SC1, P2, 9/10, spec 1.2 + backend/db.py:49, reviewer Claude
Plan baseline: spec 1.2 `run_id fk ON DELETE SET NULL` and 1.3 `run_missing` (approved in chat Section 1; contradictory)
Runtime evidence: foreign keys are enforced (db.py:49); no API deletes runs today (routes/runs.py), so the case is reachable only by direct DB edits
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| SC1 link column | FK SET NULL (link lost) | plain int, no FK, checked on write; run_missing = id set and no run | FK ON DELETE CASCADE removes the task |
| "Linked run removed" display | promised | kept | dropped |
| SC2, SC3, SC6 | pending | pending | pending |
Question D2: Plain integer link (recommended) vs cascade delete. Recommendation: A because it keeps the approved "Linked run removed" behaviour.
Header: Run link
Options:
A) Plain id, checked on write (recommended)
B) Cascade delete tasks
State: approved
Actual answer: A, D2 (owner 2026-11-02)
Accepted scope: `Task.run_id` plain integer, no FK; POST/PATCH 400 invalid_task for an unknown run; run_missing = run_id set and no run row; tests for both.
History: none

### SC2: a linked discrepancy that drops out of the current report version
Finding: SC2, P2, 8/10, spec 1.2/1.3 + backend/models.py:358, reviewer Claude
Plan baseline: spec 1.2 "must exist in that run's discrepancy list"; 1.3 `discrepancy {item_id, field_label, status}` (approved Section 1)
Runtime evidence: DiscrepancyState rows survive when an item leaves the current version (models.py:358); item ids are stable across regenerate (models.py:351)
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| Validate link on write | against current blocks (unstated) | against current blocks | against DiscrepancyState rows |
| Item absent from current version | unspecified | `discrepancy.in_report=false`, status from the state row, chip "No longer in the report" | same as A |
| SC3, SC6 | pending | pending | pending |
Question D3: validate against the current report and show "No longer in the report" when an item drops out (recommended), or also allow linking items that are absent.
Header: Missing item
Options:
A) Current report, show dropped (recommended)
B) Allow any known item
State: approved
Actual answer: A, D3 (owner 2026-11-02)
Accepted scope: link accepted only for an item in the run's current report (else 400 invalid_task); task list `discrepancy` gains `in_report`; when false the chip reads "No longer in the report", status from the DiscrepancyState row, click opens the run without scrolling; backend test for regenerate-drops-item and restore.
History: none

### SC3: `line` and the duplicate-run check
Finding: SC3, P3, 8/10, backend/pipeline/runner.py:610-613, reviewer Claude
Plan baseline: spec 1.1 `POST /api/runs accepts optional line` (approved Section 1); dedupe behaviour unspecified
Runtime evidence: an active run is reused when workflow, documents, slots and parameters match (runner.py:607-613)
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| `line` storage | column (spec) | column, outside dedupe identity; a reused active run takes the requested line | column, part of dedupe identity (two runs) |
| SC6 | pending | pending | pending |
Question D4: same documents resubmitted with a different line while the first run is still running.
Header: Duplicate run
Options:
A) Reuse run, apply new line (recommended)
B) Start a second run
State: approved
Actual answer: A, D4 (owner 2026-11-02)
Accepted scope: `line` stays out of the duplicate-run identity; when POST /api/runs returns an active run (created=false) and `line` was sent, that run's line is set to it; one backend test.
History: none

### SC6: the 100-run cap on GET /api/runs
Finding: SC6, P3, 8/10, backend/routes/runs.py:76 `limit: int = Query(20, ge=1, le=100)`, reviewer Claude
Plan baseline: spec 2.1 run list and 2.2 link picker use `GET /api/runs` (approved Section 2); bound unspecified
Runtime evidence: newest-first, default 20, max 100, no offset
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| Personal list + picker bound | unspecified | `limit=100`, newest 100, bound stated in the spec | add `offset` paging + "Load more" |
Question D5: state the 100-run bound (recommended) or add paging.
Header: Run list cap
Options:
A) Newest 100, stated (recommended)
B) Add paging
State: approved
Actual answer: A, D5 (owner 2026-11-02)
Accepted scope: Personal lines list and task run picker call `GET /api/runs?limit=100` (list adds `line=personal`); spec states the newest-100 bound; no API change. Shortcut: upgrade to offset paging when a workspace passes 100 runs.
History: none

### Section 1: Architecture
Task list composition (GET /api/tasks):
```
tasks (1 query) ──► distinct run_ids ──► runs + workflow names (1 query, IN)
                                      └► per linked run with a discrepancy: current blocks (latest artifact content)
                                         + DiscrepancyState rows (1 query, IN) ──► {field_label, status, in_report}
run_id set, no run row ──► run_missing=true, run_label=null, discrepancy=null
```
- A1 [P3] (confidence: 8/10) spec 1.3: the list needs each linked run's current blocks. Load them once per distinct run, not per task
  (required mechanics of the approved list; see Section 4). No question.
- A2 [P3] (confidence: 8/10) spec 1.2 "title ... trimmed": discrepancy notes already strip control characters
  (`routes/runs.py:113` `_NOTE_CONTROL`). Titles get the same cleaning by importing that pattern (one caller pair, no new helper).
  Required mechanics of "trimmed". No question.
- Security: single user, no auth (unchanged); titles render as React text (escaped). No new boundary. No issues.
- Failure per new path: task list when one linked run has no artifact yet (`_current_blocks` raises no_report, routes/runs.py:143-144):
  the list must treat that as `discrepancy.in_report=false`, never fail the whole list. Required mechanics. No question.

### Section 2: Code quality
- C1 [P2] (confidence: 8/10) spec 1.3 "Undo is a client re-create (POST with the same fields)": a re-create gets a new id and
  created_at, so the restored task jumps in created-date sort, and a discrepancy card's "Task: Open" link (spec 2.3, `#/tasks` with that
  task highlighted) would point at a dead id. Toast default is 6 s (`frontend/src/ui/Toast.tsx:7`).

### C1: how task delete is undone
Finding: C1, P2, 8/10, spec 1.3, reviewer Claude
Plan baseline: spec 1.3 / 2.2 delete with undo toast, undo = re-create (approved Section 2 + spec review)
Runtime evidence: toast 6 s (Toast.tsx:7); no server-side undo for tasks exists
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| Undo mechanism | client re-create (new id) | hide row now, send DELETE when the toast closes; Undo cancels it | server soft delete (`deleted_at`) + restore endpoint |
| Same id/created_at after undo | no | yes | yes |
Question D6: delay the DELETE until the toast closes (recommended) or add soft delete.
Header: Delete undo
Options:
A) Delete when toast closes (recommended)
B) Server soft delete
State: approved
Actual answer: A, D6 (owner 2026-11-02)
Accepted scope: row hides at once; DELETE is sent when the undo toast closes (6 s) or on navigation; Undo cancels it; a failed DELETE restores the row with an error toast; e2e covers delete+undo and delete committed.
History: none

### Section 3: Tests
Framework: pytest (backend/tests, run from backend/ with backend/.venv), Playwright e2e (frontend/e2e, scratch backend on 8010).
```
CODE PATHS                                              USER FLOWS
[+] POST /api/runs line                                 [+] Personal lines
  ├── [GAP] default commercial, explicit personal          ├── [GAP] [→E2E] list shows runs 7/8 only
  ├── [GAP] bad value -> 400 invalid_run                   ├── [GAP] [→E2E] entry button opens wizard tagged personal
  └── [GAP] reused active run takes new line (D4)          ├── [GAP] [→E2E] re-tag from run header moves the run
[+] PATCH /api/runs/{id}                                   └── [GAP]        empty state
  ├── [GAP] line only; extra field -> 400/422           [+] Tasks
  └── [GAP] unknown run -> 404                             ├── [GAP] [→E2E] create, edit, mark done, reopen
[+] GET /api/runs?line=                                    ├── [GAP] [→E2E] search title + run label; each sort both ways
  ├── [GAP] personal / commercial / none (all)             ├── [GAP] [→E2E] filter Open/Done/All; no-match empty state
[+] Task CRUD                                              ├── [GAP] [→E2E] delete + Undo keeps id; delete commits (D6)
  ├── [GAP] each invalid_task case (empty/201-char title,  └── [GAP]        overdue marking (past due, open only)
  │         bad status, unknown run, item w/o run,      [+] Discrepancy -> task
  │         item not in current report)                    ├── [GAP] [→E2E] Create task prefilled, card shows Task: Open
  ├── [GAP] completed_at set on done, cleared on reopen    └── [GAP] [→E2E] chip click scrolls to the card
  ├── [GAP] control chars stripped from title (A2)      [+] Integrations
  ├── [GAP] run_missing after direct row delete (D2)       └── [GAP] [→E2E] notice visible, Connect disabled
  ├── [GAP] in_report=false after regenerate, back on restore (D3)
  ├── [GAP] run with no report: list still returns (A1)
  └── [GAP] R5: PATCH task never changes discrepancy state and back
[+] GET /discrepancies item.task
  └── [GAP] null / open task / only done task -> latest
[+] dev_seed
  └── [GAP] runs 7, 8 personal; two tasks (extend test_dev_seed.py)
axe 0 at 1440/1024/390 on the three new screens (extend existing axe helper)
COVERAGE: 0/27 paths tested (all new)  |  GAPS: 27 (11 E2E, 0 eval)
```
No LLM or prompt change: no eval scope.

Regression risk: existing behaviour that this plan touches: POST /api/runs dedupe and response (test_run_slots.py, test_api_flow.py),
GET /api/runs with no parameters, discrepancy item shape (test_discrepancy_state.py), Policy Check cards and wizard step 1
(policy-check.spec.ts, wizard.spec.ts, journey.spec.ts).

### T1: regression contract
Finding: T1, CRITICAL (regression rule), 9/10, files above, reviewer Claude
Plan baseline: spec 3 lists new tests only; no regression contract stated
Runtime evidence: suites above pass on main (HANDOFF 20:45: backend 1233 pass/1 skip, e2e 10/10)
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| Existing suites | unstated | must pass unchanged; only intended additions (new `line`, `task` fields) may be asserted anew | must pass, edits allowed where needed |
| New pinned assertions | none | `GET /api/runs` with no params unchanged; old run payload keys unchanged; discrepancy item keys unchanged plus `task` | none |
Question D7: pin the existing contract (recommended) or allow test edits.
Header: Regression
Options:
A) Pin existing contract (recommended)
B) Existing suites, edits allowed
State: approved
Actual answer: A, D7 (owner 2026-11-02)
Accepted scope: test_run_slots, test_api_flow, test_discrepancy_state and the policy-check/wizard/journey e2e pass unchanged; new assertions pin GET /api/runs with no params, existing run payload keys, existing discrepancy item keys (+ `task`); any needed edit to an old test is flagged in the lane report, not made silently.
History: none

### Section 4: Performance
- P1 [P3] (confidence: 8/10) GET /api/tasks: one query for tasks, one for linked runs (IN), one for state rows (IN), plus one
  artifact content load per distinct linked run with a discrepancy. Scale [estimated]: tens of tasks, under 10 linked runs in the demo.
  Required mechanics (A1). Index `task.run_id`. No question.
- P2 [P3] (confidence: 8/10) GET /discrepancies `task` field: one extra query on `task.run_id` per request. No question.
- No caches, no unbounded loops beyond the task table itself (single user). No issues beyond the above.

### Outside Voice (Codex, read-only, 2026-11-02)
Ten findings, each checked against code by Claude. Dispositions:
- O1 [P1] PATCH changing/clearing `run_id` can leave a stale `discrepancy_item_id`. Verified (spec 1.3). Required mechanics of the
  approved rule "item only valid with run": validate the merged post-patch state; changing or clearing `run_id` without sending an
  item clears the item. No question.
- O2 [P1] `field_label` does not exist: `DiscrepancyBlock` has `title` (`backend/schemas.py:585`) and DiscrepancyState stores no label,
  so a dropped item (D3) has no label to show. Verified. Question D8.
- O3 [P1] Toast has no close callback (`frontend/src/ui/Toast.tsx:28` `show(t)` only). Verified. Required mechanics of D6: add an
  `onClose(reason)` fired on timeout, dismiss, replacement and unmount; Undo cancels the pending DELETE. No question.
- O4 [P2] several tasks per discrepancy: make the pick deterministic: first open task by `created_at DESC, id DESC`, else latest. No question.
- O5 [P2] Compare Quotes has no wizard (`frontend/src/launch/LaunchView.tsx`). Correction: the Commercial/Personal choice sits on the
  Compare Quotes upload screen and on Policy Check step 1; placement decided in the design pass. No question.
- O6 [P2] Workflows' run list stays unfiltered (`frontend/src/api/client.ts:70`), contradicting "rail Workflows = commercial". Verified. Question D9.
- O7 [P2] no URL contract for highlight/scroll. Required mechanics: `#/tasks?task=<id>` and `#/runs/<id>?discrepancy=<item_id>`;
  scroll and highlight after load; a filtered-out task switches the filter to All; a dropped item opens the run without scrolling (D3). No question.
- O8 [P2] `init_db` uses `create_all` (`backend/db.py:76-78`), which never adds `Run.line` to an old database, so an old DB starts
  and then fails on first request. Verified. Question D10.
- O9 [P3] SSE snapshots use their own serializer (`backend/pipeline/runner.py:419`). Correction: "every run payload" means `run_out`
  (REST); SSE is unchanged. No question.
- O10 [P3] Pydantic `Literal` failures go to the global 422 `invalid_request` (`backend/main.py:199-203`), not the spec's 400. Required
  mechanics of the approved 400 contract: validate `line` and task fields in the route (like slots) and test status and code. No question.

### O2: label for a linked discrepancy that left the report
Finding: O2, P1, 9/10, backend/schemas.py:585 `title: str`, reviewer Codex (verified by Claude)
Plan baseline: D3 approved "No longer in the report" chip; label source unspecified
Runtime evidence: DiscrepancyState has no title column (models.py:361-370)
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| Label source | none | `Task.discrepancy_title` snapshot at link time; live title used while in report | search older artifact versions for the item |
Question D8. Header: Dropped label. Options: A) Save title on the task (recommended) B) Look up old versions
State: approved
Actual answer: A, D8 (owner 2026-11-02)
Accepted scope: `Task.discrepancy_title` (str, nullable) set from the block title when the link is made, refreshed during GET /api/tasks while the item is in the report, shown when in_report=false.
History: none

### O6: what the Workflows run list shows
Finding: O6, P2, 8/10, frontend/src/api/client.ts:70 `runs: (limit = 20) => request(... /api/runs?limit=${limit})`, reviewer Codex (verified)
Plan baseline: spec 2.1 "rail Workflows = commercial" (wizard default only); Workflows list behaviour unspecified
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| Workflows recent runs | all runs | commercial only (`?line=commercial`) | all runs, personal ones marked with a small "Personal" tag |
Question D9. Header: Workflows list. Options: A) Commercial only (recommended) B) All runs, tagged
State: approved
Actual answer: A, D9 (owner 2026-11-02)
Accepted scope: Workflows recent-runs list calls GET /api/runs?line=commercial; personal runs appear only under Personal lines; e2e checks a re-tag moves a run between the lists.
History: none

### O8: an old local database after `Run.line` is added
Finding: O8, P2, 9/10, backend/db.py:76-78 `models.Base.metadata.create_all(engine)`, reviewer Codex (verified)
Plan baseline: spec 1.1 "delete local data/brokerdocs.db after this change" (manual step); HANDOFF open item 6 "No DB migrations"
Comparison grid:
| Choice | Current | A | B |
|---|---|---|---|
| Old DB at startup | starts, then 500 on first runs query | startup check: missing `run.line` or `task` table -> exit with "delete data/brokerdocs.db (schema changed 2026-11-02)" | add the column in place (`ALTER TABLE run ADD COLUMN line ... DEFAULT 'commercial'`) + create `task` |
Question D10. Header: Old database. Options: A) Fail fast with message (recommended) B) Upgrade in place
State: approved
Actual answer: A, D10 (owner 2026-11-02)
Accepted scope: init_db checks for `run.line` and the `task` table; if missing it exits with a message naming the DB file to delete and the reseed command; one backend test with an old-schema DB.
History: none

### TODO: run-list paging
D11: A) Add to TODOS.md (owner 2026-11-02). Added as "Run-list paging past 100 runs".

Approval readiness: PASS. Checked: D1 (scope), D2 SC1, D3 SC2, D4 SC3, D5 SC6, D6 C1, D7 T1, D8 O2, D9 O6, D10 O8, D11 TODO.
Required mechanics carried under approved contracts (no question): A1, A2, no-report failure, P1, P2, O1, O3, O4, O5, O7, O9, O10.

## Review output

### NOT in scope
- Run-list paging: deferred (D5), TODO added (D11).
- Real migration framework: kept out; startup fails fast instead (D10).
- Homeowners field set for Compare Quotes, real integrations, task notes/assignees/priorities/reminders, run deletion: owner decisions R2, R3, R7.
- SSE run snapshots carrying `line`: not needed by any screen (O9).

### What already exists (reuse)
- Run list component (`frontend/src/launch/RecentRuns.tsx`) and `api.runs` (`frontend/src/api/client.ts:70`): reused with `line` and `limit` params.
- Discrepancy state rows and stable item ids (`backend/models.py:347-370`): source of a linked item's live status.
- Note cleaning regex `_NOTE_CONTROL` (`backend/routes/runs.py:113`): reused for task titles.
- Toast with Undo (`frontend/src/ui/Toast.tsx`): extended with `onClose(reason)` (O3).
- Card scroll-into-view (`frontend/src/policy/Discrepancies.tsx:122`): reused for `?discrepancy=` deep links.
- Domain 400 pattern for slot errors (`routes/runs.py:65-66`): same pattern for `invalid_run` line errors and `invalid_task`.

### Failure modes
| Path | Realistic failure | Handling | Test | User sees |
|---|---|---|---|---|
| GET /api/tasks | linked run has no report yet | in_report=false, list still returns | yes (A1) | chip, no error |
| GET /api/tasks | linked item dropped by regenerate | in_report=false + saved title (D3, D8) | yes | "No longer in the report" |
| GET /api/tasks | run row gone | run_missing (D2) | yes | "Linked run removed" |
| Delete + Undo | DELETE fails after toast closes | row restored + error toast (D6) | e2e | clear error |
| Startup | old DB without run.line | fail fast with instruction (D10) | yes | clear message |
| POST /api/runs | resubmit with other line | reused run takes new line (D4) | yes | correct tag |
| PATCH task | run changed, stale item | item cleared (O1) | yes | no stale chip |

Critical gaps: 0.

### Worktree parallelization strategy
| Step | Modules touched | Depends on |
|---|---|---|
| Backend lane | backend/ (models, routes, views, db, dev_seed, tests), backend/API.md | — |
| Integrations UI | frontend/src/integrations (new), shell | — (static) |
| Personal lines UI | frontend/src/launch, policy wizard, run header, api client | Backend lane |
| Tasks UI + discrepancy action | frontend/src/tasks (new), policy/Discrepancies, ui/Toast, App routing, api client | Backend lane |

Lane A: backend. Lane B: Integrations UI (independent). Then lane C: Personal lines UI, then lane D: Tasks UI.
Execution order: launch A + B in parallel, merge both, then C, merge, then D (C and D both edit App.tsx routing and the api client).
Conflict flags: App.tsx, shell/Shell.tsx and api/client.ts are shared by B, C and D.

## Implementation Tasks
- [ ] **T1 (P1, human: ~1 day / CC: ~45 min)** — backend — `Run.line`, PATCH /api/runs/{id}, `?line=` filter, D4 resubmit rule, O10 400 validation
  - Surfaced by: SC3/D4, O9, O10, spec 1.1
  - Files: backend/models.py, backend/routes/runs.py, backend/pipeline/runner.py, backend/views.py, backend/tests/
  - Verify: `cd backend && .venv/Scripts/python -m pytest -q`
- [ ] **T2 (P1, human: ~1 day / CC: ~45 min)** — backend — Task table and CRUD with D2, D3, D8, O1, O4, A1, A2
  - Surfaced by: SC1/D2, SC2/D3, O2/D8, O1, O4, A1, A2, P1
  - Files: backend/models.py, backend/routes/tasks.py (new), backend/main.py, backend/tests/test_tasks.py (new)
  - Verify: pytest, including regenerate-drops-item and removed-run-row cases
- [ ] **T3 (P2, human: ~2 hr / CC: ~10 min)** — backend — startup schema check (D10), seed runs 7/8 personal + two tasks, API.md
  - Surfaced by: O8/D10, spec 1.4
  - Files: backend/db.py, backend/dev_seed.py, backend/API.md, backend/tests/test_dev_seed.py
  - Verify: pytest; starting on an old DB prints the message
- [ ] **T4 (P1, human: ~1 hr / CC: ~10 min)** — backend + e2e — pinned regression contract (D7)
  - Surfaced by: T1/D7
  - Files: backend/tests/test_api_flow.py (new assertions only); existing e2e suites unchanged
  - Verify: old suites pass unchanged
- [ ] **T5 (P2, human: ~1 day / CC: ~1 hr)** — frontend — Integrations mock page (design stack)
  - Surfaced by: spec 2.4
  - Files: frontend/src/integrations/, frontend/src/shell/Shell.tsx, App.tsx
  - Verify: e2e notice + disabled Connect, axe 0 at 3 widths
- [ ] **T6 (P2, human: ~1 day / CC: ~1 hr)** — frontend — Personal lines page, line choice on Compare upload + Policy step 1, run-header control, Workflows commercial-only (D9)
  - Surfaced by: O5, O6/D9, SC6/D5
  - Files: frontend/src/launch/, frontend/src/policy/PolicyWizard.tsx, frontend/src/run/RunView.tsx, api/client.ts
  - Verify: e2e filter + re-tag moves a run, axe 0
- [ ] **T7 (P2, human: ~2 days / CC: ~1.5 hr)** — frontend — Tasks page, dialog, deep links (O7), Toast onClose (O3), delayed delete (D6), Create task on cards
  - Surfaced by: C1/D6, O3, O7, spec 2.2/2.3
  - Files: frontend/src/tasks/ (new), frontend/src/ui/Toast.tsx, frontend/src/policy/Discrepancies.tsx, App.tsx
  - Verify: e2e per test plan, axe 0

### Completion summary
- Step 0: Scope Challenge: scope accepted as-is
- Reality rows: 8/8 applicable rows emitted, 0 unverified premises
- Architecture Review: 3 issues found
- Code Quality Review: 1 issue found
- Test Review: diagram produced, 27 gaps identified (plus regression contract T1)
- Performance Review: 2 issues found
- NOT in scope: written
- What already exists: written
- TODOS.md updates: 1 item proposed to user (added)
- Failure modes: 0 critical gaps flagged
- Unresolved decisions: 0 in this review
- Outside voice: codex, completed (10 findings, all verified; 3 became questions D8 to D10)
- Parallelization: 4 lanes, 2 parallel / 2 sequential
- Lake Score: 5/8 (10/10 picked on D2, D3, D4, D7, D8; D5 8/10, D6 9/10, D10 9/10)

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | not run | none |
| Outside Review | codex via `/plan-eng-review` | Independent 2nd opinion | 3 | issues_found | 10 findings, all dispositioned (D8 to D10 + mechanics) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 5 | ISSUES OPEN | 33 issues, 0 critical gaps (all mapped to tasks T1 to T7) |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | not run | none |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | not run | none |

- **OUTSIDE COVERAGE:** codex, plan-review, completed, 10 findings (3 P1, 5 P2, 2 P3), all verified against code.
- **CROSS-MODEL:** native Claude pass found 6 scope findings plus section findings; Codex found 10 others with no overlap. Codex's three P1s (stale link on PATCH, no `field_label`, no Toast close callback) were missed by the native pass.
- **VERDICT:** ENG reviewed, issues mapped to tasks, every decision answered. Ready for the backend lane; UI lanes go through the design stack.

NO UNRESOLVED DECISIONS
