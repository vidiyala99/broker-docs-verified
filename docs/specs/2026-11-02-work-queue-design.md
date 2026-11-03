# Broker work queue (Personal lines + Workflows home) and motion pass

Date: 2026-11-02. Status: design approved in chat, spec awaiting owner review.
Supersedes: the run-list parts of `docs/specs/2026-11-02-rail-completion-design.md` section 2.1 (Personal lines page body) and
the D9 Workflows recent-runs list. The `line` field, re-tagging, line choice on start screens and the Tasks lane stay as built.

## Why
Owner review of the merged Personal lines page (2026-11-02 22:44): the run list ("Run 8", file chips, slot letters, engine
statuses) is an engine view. It gives a broker no next action and adds cognitive load. A broker thinks in clients and work to do.
Raw runs belong in their own section.

## Owner decisions (brainstorm 2026-11-02, do not re-ask)
| # | Decision |
|---|---|
| W1 | Personal lines and the Workflows home open on a work queue grouped by what needs action, rows named by client. |
| W2 | The full run list moves to its own Activity page (`#/activity`), linked from the queue. |
| W3 | Items leave the queue when their work is done: check = all discrepancies resolved; comparison = proposal built from it or "Mark done"; failed/partial = successful Retry or "Dismiss". Finished items go to a collapsed "Done recently" group (14 days), then only Activity. |
| W4 | Client name: optional "Client" field on both start screens, editable on the run; empty falls back to Policy Check's extracted `named_insured`, else "Unnamed client". No extraction change. |
| W5 | Transitions: a separate app-wide motion pass after the Tasks lane merges (segmented controls, tabs, list filtering, group expand), reduced-motion respected. |
| W6 | Same queue on Workflows home (`line=commercial`) and Personal lines (`line=personal`). |

## 1. Backend

### 1.1 Run fields
- `client_name: str | None` (1 to 120 chars after cleaning with the `_NOTE_CONTROL` pattern and trim; empty string clears).
- `queue_state: "open" | "done" | "dismissed"`, default `open`.
- `proposal_run_id: int | None`: set when a Proposal Builder run is started from this comparison (plain integer, same rule as `Task.run_id`).
- All three appear in `run_out`. `POST /api/runs` accepts optional `client_name` and optional `source_run_id` (proposal runs only:
  sets `proposal_run_id` on the source comparison). `PATCH /api/runs/{id}` accepts `client_name` and `queue_state` in addition
  to `line` (any subset, at least one; same 400 `invalid_run` rules).
- Old databases: extend the D10 startup check to the new columns.

### 1.2 `GET /api/queue?line=commercial|personal`
Returns groups computed on the server from the newest 200 runs of that line (bound stated; Activity shows the rest):
```
{ "in_progress": [Item], "needs_review": [Item], "ready": [Item], "needs_attention": [Item], "done_recently": [Item] }
Item = { run_id, workflow_slug, workflow_name, client (resolved name), client_source ("broker"|"extracted"|null),
         summary (e.g. "Renewal check", "3 carriers"), open_counts {high, medium, low} | null,
         reasons [str] | null (plain-language failure reasons), finished_at, queue_state }
```
Classification (in order, first match wins):
| Group | Rule |
|---|---|
| in_progress | run active (queued/running) |
| needs_attention | failed or partial, `queue_state=open` |
| needs_review | Policy Check, done or partial with report, any open discrepancy, `queue_state=open` |
| ready | Compare Quotes, done, no `proposal_run_id`, `queue_state=open` |
| done_recently | everything else finished within 14 days (resolved checks, proposal built, done, dismissed) |
| (omitted) | older than 14 days and not in a group above: Activity only |
Proposal Builder runs themselves do not appear in the queue (they live under Proposals and Activity).
A partial Policy Check with open discrepancies appears once, under needs_attention, with its open counts shown too.
`reasons`: from document read failures and run error, mapped to plain copy ("2 documents not read", "Extraction stopped: budget reached").
Severity counts reuse the discrepancy state code from the Tasks lane.

### 1.3 Build proposal from a comparison
"Build proposal" opens the Proposal wizard with the comparison's documents preselected and `source_run_id` carried to
`POST /api/runs`. Feasibility (does the wizard accept a preselected document set) is an eng-review check.

## 2. Frontend (design stack, Playwright MCP, max 3 rounds)
- **Queue page component** used by `#/` (Workflows home, commercial) and `#/personal-lines` (personal). Header + the two start
  actions stay. Sections: "In progress" slim strip (live status), Needs review, Ready, Needs attention, Done recently (collapsed).
  Each section shows a count; empty sections are hidden; all-empty shows one calm empty state with the start actions.
- **Row:** client name (primary), workflow + summary (secondary), status detail (severity counts / reasons), one primary action
  (Review / Build proposal / Retry), overflow menu (Mark done / Dismiss / Open run / Move to Personal or Commercial).
  "Review" opens the run at its first open discrepancy (`?discrepancy=` deep link from the Tasks lane).
- **Client field** on the Compare upload screen and Policy Check step 1, and editable in the run header next to the Line control.
- **Activity page** `#/activity`: the existing run list (RecentRuns) with a line filter (All / Commercial / Personal), reached
  from a link at the bottom of the queue and the run view's "All runs" tab.
- The Compare launch screen keeps its upload panel; its main area shows the queue instead of Recent runs.

## 3. Motion pass (W5, separate lane after Tasks merges)
Start from the `improve-animations` audit across `frontend/src`, then: one shared segmented control with a sliding indicator
(replacing the Line choice, Open/Done/All, severity toggles where they are segmented), tab transitions, short fades on list
filter changes, height animation for collapsible groups, toast enter/exit if missing. `prefers-reduced-motion` turns motion off.
Skills: `emil-design-eng`, `improve-animations`, `animate`, plus the full design stack.

## 4. Testing
- Backend: each classification rule and its ordering; 14-day window; `client` resolution (broker > extracted > null);
  `queue_state` transitions via PATCH; `source_run_id` sets `proposal_run_id`; reasons copy; schema check; regression pins
  (D7 rule applies: old suites unchanged).
- e2e: queue on both pages with seeded runs in the right groups; Review deep link; Retry and Dismiss leave the group;
  Mark done; Build proposal preselects documents; client field set and edited; Activity filter; axe 0 at 1440/1024/390.
- Seed: give runs 7/8 and a couple of comparisons client names, leave one unnamed.

## 5. Order
1. Tasks lane (running). 2. Eng review of this spec. 3. Backend lane. 4. Queue UI + Activity lane. 5. Motion lane.

## Out of scope
Client entity/CRM pages, renewal dates from AMS, extraction of insured names in Compare Quotes, notifications.
