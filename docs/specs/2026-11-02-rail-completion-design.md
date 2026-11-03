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
- New `PATCH /api/runs/{run_id}` with body `{"line": ...}`; only `line` is patchable. 404 for an unknown run, 400 for a bad value.
- `GET /api/runs?line=personal|commercial` filters; no parameter returns all runs (current behaviour).
- `line` is included in every run payload.
- No migration framework: delete local `data/brokerdocs.db` after this change (same rule as earlier slices).

### 1.2 `Task` table
| Column | Type | Rule |
|---|---|---|
| id | int pk | |
| workspace_id | fk workspace | indexed, same scoping as runs |
| title | str(200) | required, trimmed, 1 to 200 chars |
| due_date | date, nullable | |
| status | str | `open` or `done`, default `open` |
| run_id | fk run, nullable | `ON DELETE SET NULL` |
| discrepancy_item_id | str, nullable | only valid with `run_id`; must exist in that run's discrepancy list |
| created_at, updated_at | datetime | |
| completed_at | datetime, nullable | set when status becomes done, cleared when reopened |

### 1.3 Task endpoints
- `GET /api/tasks`: all tasks of the workspace. Each item adds read-only fields:
  `run_label` (the label the app already shows for a run), `run_missing` (true when `run_id` was set and the run is gone),
  `discrepancy` (`{item_id, field_label, status}` or null; `status` is the live discrepancy state).
- `POST /api/tasks`: `{title, due_date?, run_id?, discrepancy_item_id?}`; 201 with the task.
- `PATCH /api/tasks/{id}`: any of `title, due_date, status, run_id, discrepancy_item_id` (null clears).
- `DELETE /api/tasks/{id}`: 204. Undo is a client re-create (POST with the same fields).
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
- Run list: the existing Workflows run-list component with `?line=personal`.
- Empty state: "No personal-lines runs yet." plus the two actions.
- Both wizards gain a line choice on step 1 (Commercial / Personal), defaulted from the entry point (rail Workflows = commercial).
- Run header gains a small Commercial / Personal control (PATCH). This is the only place to re-tag a run.

### 2.2 Tasks (`#/tasks`)
- Toolbar: search box, Open / Done / All segmented filter (default Open), "New task" button.
- Search: client-side, case-insensitive substring over title and `run_label`, live as you type.
- Sort: client-side by due date (default, ascending, no-date last), created date, title A to Z; sortable column headers
  with visible direction and `aria-sort`; click again reverses.
- Columns: done checkbox, title, due date (overdue = past due and open, shown with the warning token), linked item, created.
- Linked item: run label; for discrepancies also a chip "<field label> · <Open|Resolved>". Click opens the run with the
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
