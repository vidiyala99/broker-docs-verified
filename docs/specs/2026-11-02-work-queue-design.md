# Broker work queue (Personal lines + Workflows home) and motion pass

Date: 2026-11-02. Status: owner-approved 2026-11-02 22:53; eng review next.
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

## Engineering review (plan-eng-review, 2026-11-02 23:05, target: this spec)
The plan above is unchanged; the decision ledger below overrides it once answered.

### Premise table
| Premise | Claim in spec | Receipt | Verdict |
|---|---|---|---|
| P1 | Compare Quotes does not extract insured names (Out of scope; W4) | `backend/db.py:154` Compare `_FIELDS` has `named_insured` | FALSE: already extracted per quote |
| P2 | Retry clears needs_attention | `runner.py:657` retry reruns the same run id; `runner.py:90` FAILED/PARTIAL/CANCELLED retryable | TRUE |
| P3 | Severity counts can reuse the state code | `routes/runs.py:202-210` counts built inline in `list_discrepancies` | TRUE, but inline, not a helper |
| P4 | Proposal wizard can take preselected documents | `wizard/draft.ts:20-21` draft holds `docs: DocumentInfo[]` and `client_name`, persisted | TRUE; it would overwrite a saved draft |
| P5 | "Live status" for In progress | `App.tsx:96` run list refetches on route change only; SSE is per run | No mechanism exists |
| P6 | Duplicate create | `runner.py:613-620` an identical active run is reused and takes `line` (D4) | TRUE; `client_name` must follow the same rule |
unverified=0

### Scope Challenge findings
- SC1 [P1] (9/10) `backend/db.py:154`: Compare already has `named_insured` facts; W4's fallback ignores them, so Compare rows read "Unnamed client" unless typed.
- SC2 [P1] (8/10) Done recently is keyed to `finished_at`: Mark done / Dismiss / last resolve on a run finished over 14 days ago makes the row vanish at once.
- SC3 [P1] (8/10) `proposal_run_id` is set at proposal start and proposal runs never enter the queue: a failed proposal moves the comparison to Done and the failure shows nowhere in the queue.
- SC4 [P2] (8/10) "newest 200 runs" drops old open work (Ready items stay open until acted on). TODOS.md:47 already tracks list paging.
- SC5 [P2] (9/10) `App.tsx:96`: no live refresh exists for the In progress strip.
- SC6 [P2] (8/10) `wizard/draft.ts:20`: Build proposal replaces a saved, unfinished proposal draft.
- SC7 [P3] (9/10) `routes/runs.py:202-210`: open-count code is inline; the queue would be a second caller.
- Mechanical (follows approved D4, no question): a reused active run takes `client_name` when given; Retry shows only when `run_out.retryable`; the Proposal wizard's existing `client_name` is prefilled from the comparison's client.
- Complexity: about 22 files, 3 new units (queue classifier + route, Queue page, Activity page). Gate tripped; no smaller arrangement keeps W1 to W6 (Activity already reuses RecentRuns).

## Decision ledger
Each row: grid columns hold every other choice pending/unchanged.

### R1: Client fallback for Compare runs (SC1)
Plan baseline: W4: broker field, else Policy Check named_insured, else "Unnamed client". Runtime: Compare stores a `named_insured` Fact per quote.
Grid: R1 current = Unnamed client | A = most common verified named_insured across the run's quotes (first verified on a tie) | B = Unnamed client. W4 broker-first unchanged in both.
Question D1: Use the insured name Compare Quotes already extracts as the fallback client name? Recommendation A.
Header: Compare name
Options:
A) Use extracted name (recommended): no extraction change, one query.
B) Keep as written: Compare rows read "Unnamed client" until the broker types a name.
State: approved
Actual answer: A (D1, owner, 2026-11-02 23:10)
Accepted scope: Compare fallback = most common verified named_insured across the run's quotes (first verified on a tie); client_source "extracted".

### R2: Done recently clock (SC2)
Plan baseline: 14-day window on finished_at. Runtime: Run has finished_at and updated_at (any change) only.
Grid: current = finished_at | A = new `queue_changed_at`, set when an item leaves (PATCH queue_state, proposal done, last discrepancy resolved); window = later of the two | B = finished_at.
Question D2: Count Done recently's 14 days from the moment an item leaves the queue? Recommendation A.
Header: Done clock
Options:
A) Add queue_changed_at (recommended): finished work always lands in Done recently first.
B) Keep finished_at: old runs jump straight to Activity when marked done.
State: approved
Actual answer: A (D2, owner, 2026-11-02 23:10)
Accepted scope: Add Run.queue_changed_at; set on PATCH queue_state, proposal done, last discrepancy resolved; Done window = later of finished_at and queue_changed_at.

### R3: Failed proposal (SC3)
Plan baseline: comparison leaves Ready when a proposal run is started.
Grid: current = on start | A = leaves when the proposal run is done; while it runs the row shows In progress; failed/cancelled returns it to Ready with reason "Proposal didn't finish" | B = on start.
Question D3: Keep a comparison in the queue until its proposal actually finishes? Recommendation A.
Header: Failed proposal
Options:
A) Leave on proposal done (recommended): a failed proposal is never lost.
B) Leave on start: simpler; failures only visible under Proposals and Activity.
State: approved
Actual answer: A (D3, owner, 2026-11-02 23:10)
Accepted scope: Ready exit on linked proposal run done; proposal running = row in In progress; proposal failed/cancelled = back in Ready with reason "Proposal didn't finish".

### R4: Queue bound (SC4)
Plan baseline: newest 200 runs of the line.
Grid: current = newest 200 | A = SQL filter first (active, or queue_state open, or changed within 14 days), cap 500, `truncated` flag shown as a note | B = newest 200.
Question D4: Pick queue rows by state instead of "newest 200"? Recommendation A.
Header: Queue bound
Options:
A) Filter, then cap (recommended): old open work stays visible.
B) Newest 200: open items older than 200 runs silently drop to Activity.
State: approved
Actual answer: A (D4, owner, 2026-11-02 23:10)
Accepted scope: Select by SQL (active OR queue_state open OR changed within 14 days), cap 500, response flag `truncated`, UI note when set.

### R5: Live In progress (SC5)
Plan baseline: "live status", mechanism unspecified.
Grid: current = none | A = poll GET /api/queue every 3 s while In progress is non-empty and the tab is visible | B = refresh on navigation only.
Question D5: How should In progress stay live? Recommendation A.
Header: Live refresh
Options:
A) Poll while active (recommended): stops when nothing runs.
B) Navigation only: strip goes stale until the broker navigates.
State: approved
Actual answer: A (D5, owner, 2026-11-02 23:10)
Accepted scope: Poll GET /api/queue every 3 s while in_progress non-empty and document visible; stop otherwise.

### R6: Saved proposal draft (SC6)
Plan baseline: wizard opens with the comparison's documents preselected.
Grid: A = replace the draft, toast with Undo restoring the old draft | B = replace silently.
Question D6: What happens to an unfinished proposal draft on Build proposal? Recommendation A.
Header: Proposal draft
Options:
A) Replace with Undo (recommended): nothing lost.
B) Replace silently: unfinished draft is lost.
State: approved
Actual answer: A (D6, owner, 2026-11-02 23:10)
Accepted scope: Build proposal replaces the saved draft; toast with Undo restores the old draft.

### R7: Shared open-count helper (SC7)
Plan baseline: "reuse the discrepancy state code".
Grid: A = extract `models.discrepancy_counts(s, run)` used by list_discrepancies and the queue (~15 lines moved, existing tests pin it) | B = copy the loop.
Question D7: Extract the open-count code into one helper? Recommendation A.
Header: Count helper
Options:
A) Extract helper (recommended): queue and list cannot disagree on "open".
B) Duplicate loop: two copies to keep in step.
State: approved
Actual answer: A (D7, owner, 2026-11-02 23:10)
Accepted scope: Extract models.discrepancy_counts(s, run); list_discrepancies and the queue both call it; existing discrepancy tests must pass unchanged.

### R8: Retry reopens a left item (Section 1)
Finding: A1, P2, 8/10, `runner.py:657-676` retry resets status/error/finished_at but nothing queue-related (field is new). Plan baseline: unspecified.
Question D8: Should Retry reset the item to open? Recommendation A. Header: Retry reopen
Options: A) Retry reopens (recommended): POST /retry sets queue_state=open. B) Keep dismissed.
State: approved
Actual answer: A (D8, owner, 2026-11-02 23:14)
Accepted scope: `retry` sets queue_state="open" (and clears queue_changed_at) in the same transaction.

### R9: Undo on Mark done / Dismiss (Section 1)
Finding: A2, P3, 8/10, spec section 2 Row: one-click exits with no recovery path stated. Plan baseline: unspecified.
Question D9: Undo toast on Mark done / Dismiss? Recommendation A. Header: Undo leave
Options: A) Undo toast (recommended). B) No undo.
State: approved
Actual answer: A (D9, owner, 2026-11-02 23:14)
Accepted scope: toast "Marked done" / "Dismissed" with Undo = PATCH queue_state "open"; no confirm dialog; overflow menu on Done recently rows offers "Move back to queue".

## Review sections

### Section 1: Architecture
Classification (approved D2, D3, D4 applied), first match wins:
```
run (line=L, selected by: active OR queue_state=open OR max(finished_at, queue_changed_at) >= now-14d; cap 500)
 |
 +- workflow == Proposal Builder ----------------------------> skip (Proposals / Activity)
 +- active (queued/running) --------------------------------> in_progress
 +- Compare with proposal_run_id and that proposal active ---> in_progress ("Building proposal")
 +- queue_state == open:
 |    +- status failed|partial|cancelled ------------------> needs_attention (reasons, D10; open_counts if a check)
 |    +- Policy Check, has report, open discrepancies > 0 --> needs_review
 |    +- Compare done, no proposal, or proposal failed|cancelled -> ready (reason "Proposal didn't finish" when so)
 |    +- otherwise (check fully resolved, proposal done) -> done_recently if in window
 +- queue_state done|dismissed -----------------------------> done_recently if in window
 +- outside window -----------------------------------------> omitted (Activity only)
```
- Placement: pure `backend/queue.py: classify(run, counts, proposal, now) -> (group, item)` plus thin `routes/queue.py`. Pure function keeps the rules unit-testable without the run manager.
- `source_run_id` on POST /api/runs: must name an existing Compare Quotes run, else 400 invalid_run; only accepted with workflow proposal-builder.
- `queue_changed_at` is set on PATCH queue_state, when a linked proposal finishes (runner terminal transition for proposal runs with a source), and when the last open discrepancy is resolved (`update_discrepancy_state`).
- Security: client_name is text rendered by React (escaped) and not sent to the model; cleaned like notes. Local loopback app; no new auth surface.
- Failure path: GET /api/queue error shows the existing inline error with Retry; polling (D5) backs off to stop after an error until the next navigation.
Findings: A1 (D8), A2 (D9). Dispositions: both accepted.

### Section 2: Code quality
- Q1 [P3] (8/10) shared helper: approved as R7. Q2 [P3] (8/10) Activity reuses `launch/RecentRuns.tsx` as is; the line filter lives in the page, not in RecentRuns. Q3: the classification diagram above goes in `backend/queue.py`'s docstring.
- Tasks lane T7 edits `App.tsx`, `Shell.tsx`, `Toast.tsx`, `api/types.ts`, `api/client.ts`, `Discrepancies.tsx`: queue UI lane starts after T7 merges (order already in section 5). Dispositions: no new choices.

### Section 3: Tests
Framework: pytest (backend/tests), Playwright (frontend/e2e). Regression contract carried forward: spec line 80 (D7 rule), `backend/tests/test_run_line.py:123-136` cases stay unchanged and passing; new keys extend that table, they do not edit rows.
```
CODE PATHS (all proposed)                          USER FLOWS
[+] queue.classify                                 [+] Queue page (both lines)
  |- [GAP] each group rule + first-match order       |- [GAP][->E2E] seeded runs land in right groups
  |- [GAP] partial check -> needs_attention once     |- [GAP][->E2E] Review deep link opens first open item
  |- [GAP] proposal active / failed / done (D3)      |- [GAP][->E2E] Retry leaves group (D8 reopen)
  |- [GAP] window on queue_changed_at (D2)           |- [GAP][->E2E] Mark done + Undo, Dismiss + Undo (D9)
  |- [GAP] Proposal Builder runs skipped             |- [GAP][->E2E] Build proposal preselects docs+client, Undo restores draft (D6)
  '- [GAP] cancelled run -> needs_attention (D10) |- [GAP][->E2E] In progress row moves on finish (D5 poll)
[+] client resolution                                |- [GAP] all-empty calm state
  |- [GAP] broker > extracted (check) > extracted (compare, majority, D1) > null
[+] GET /api/queue                                 [+] Client field: set on start screens, edit in header
  |- [GAP] line filter, 422 bad line, cap + truncated (D4)
[+] PATCH /api/runs client_name/queue_state        [+] Activity: line filter All/Commercial/Personal
  |- [GAP] subsets, clear with "", 400 on bad values, test_run_line rows unchanged
[+] POST /api/runs source_run_id, client_name on reused active run
[+] retry sets queue_state open (D8)
[+] discrepancy_counts helper: existing list_discrepancies tests unchanged (R7)
[+] schema check rejects DB missing new columns
COVERAGE: 0/22 (all proposed)   axe 0 at 1440/1024/390 on Queue and Activity
```
Test plan artifact: `~/.gstack/projects/aakas/aakas-main-eng-review-test-plan-20261102-2315.md`.

### Section 4: Performance
- P1 [P3] (7/10) per-run work in GET /api/queue: up to 500 runs, each needing workflow, documents, artifact JSON (checks only) and states. Use `selectinload` for workflow/documents/artifact, one `discrepancy_state` query for all selected check runs, one facts query for named_insured over all selected runs. Scale: local SQLite, demo has 8 runs; bounded at 500 by D4.
- Polling (D5) costs one queue request per 3 s only while something runs. No new index needed at this scale (stated, not measured).
Dispositions: accepted as implementation guidance, no new choice.

## Task list (build order)
1. Backend lane (Opus): columns client_name, queue_state, proposal_run_id, queue_changed_at + schema check; PATCH/POST changes (client_name rule mirrors D4); `discrepancy_counts` extraction; `backend/queue.py` + GET /api/queue; retry reopen; seed (named runs, one unnamed, one failed proposal); API.md; tests above.
2. Queue UI + Activity lane (Opus, design stack, Playwright MCP, max 3 rounds): after T7 merges.
3. Motion lane (W5).

## Outside voice (Codex, read-only, 2026-11-02 23:20)
Codex `exec -s read-only` on this spec + ledger R1-R9; 10 findings (raw output in session scratchpad `codex-out.txt`). Claude verified each against code.
| # | Codex finding | Verdict | Disposition |
|---|---|---|---|
| O1 | P1 cancelled runs uncovered (schemas.py:61, runner.py:90) | confirmed | R10 / D10 |
| O2 | P1 Mark done/Dismiss on an active run loses to in_progress | confirmed | Amendment M1 |
| O3 | P1 cap can still evict old actionable rows (open-but-finished rows match forever) | confirmed | Amendment M2 (implements D4 intent) |
| O4 | P1 `source_run_id` outside proposal idempotency (runner.py:613) | confirmed | Amendment M3 |
| O5 | P1 D2/D3 bookkeeping must live in runner terminal transitions | confirmed | Amendment M4 |
| O6 | P2 proposals from personal comparisons tagged commercial (client.ts:118) | confirmed | Amendment M5 |
| O7 | P2 counts helper must iterate current blocks, not all state rows (models.py:361) | confirmed | Amendment M6 (binds R7) |
| O8 | P2 Activity is capped at 100 | confirmed | R11 / D11 |
| O9 | P2 Policy Check name precedence undefined | confirmed | Amendment M7 (refines R1) |
| O10 | P2 schema check must cover all four columns + indexes | confirmed | Amendment M8 |
Cross-model: Codex and Claude agreed on the queue shape; Codex found 9 gaps Claude missed (O1-O7, O9, O10); no disagreement on approved decisions.

### R10: Cancelled runs (O1)
Question D10: Where should cancelled runs go? Header: Cancelled. Options: A) Needs attention (recommended). B) Done recently.
State: approved
Actual answer: A (D10, owner, 2026-11-02 23:22)
Accepted scope: cancelled + queue_state open -> needs_attention, reason "Stopped before finishing", actions Retry / Dismiss.

### R11: Activity paging (O8)
Question D11: Add paging now or keep TODO? Header: Activity paging. Options: A) Defer to TODO (recommended). B) Add paging now.
State: approved
Actual answer: A, defer (D11, owner, 2026-11-02 23:22)
Accepted scope: Activity shows the newest 100 with a visible note when the cap is hit; paging stays TODOS.md:47 ("full run list" wording in section 2 reads "newest 100").

### Amendments (inside approved decisions, no new behavior choice)
- M1: in_progress rows have no Mark done / Dismiss; PATCH queue_state done|dismissed on an active run returns 409 `run_active`.
- M2 (D4): selection orders actionable rows first (open AND (failed|partial|cancelled, or Policy Check, or Compare without a done proposal)), then recent left rows; cap 500 applies after that order. Test: 500+ recent rows plus one older actionable row stays.
- M3: proposal runs carry `source_run_id` inside `parameters`, so it is part of the duplicate-run identity; source must be a done Compare Quotes run, else 400 invalid_run. Test repeated Build proposal from two comparisons with the same documents.
- M4 (D2, D3, D8): queue bookkeeping (source comparison's queue_changed_at on proposal done; retry reopen) lives in one runner helper called from every terminal transition (done, failed, cancelled, startup reconcile) in the same transaction. Tests per path.
- M5: a proposal started from a comparison inherits its `line`; the Proposal client passes `line`.
- M6 (R7): `discrepancy_counts` iterates the current artifact's DiscrepancyBlocks joined to state rows (as list_discrepancies does), never all state rows. Test: an item gone from the latest version is not counted, and counts again if it returns.
- M7 (R1): extracted name precedence: Policy Check renewal -> expiring -> quote (verified facts only); Compare majority with ties broken by RunDocument.position. Test conflicts.
- M8: schema check (D10 of slice 3b, db.py:83) requires all four new columns and the queue_state constraint; index on run(line, queue_state). One test per missing element.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|---|---|---|---|---|---|
| Eng Review | `/plan-eng-review` | lock the work-queue plan before the backend lane | 1 | CLEAR | 7 scope + 2 arch findings, all resolved (D1-D9) |
| Outside Voice | automatic | independent challenge | 1 (Codex) | ISSUES_RESOLVED | 10 findings: 2 decisions (D10, D11), 8 amendments (M1-M8) |

- **SCOPE:** scope accepted as-is (complexity gate: no smaller arrangement keeps W1-W6).
- **ARCHITECTURE:** pure `backend/queue.py` classifier + thin route; bookkeeping in runner terminal transitions (M4).
- **TESTS:** 22 proposed paths, regression contract `test_run_line.py:123-136` unchanged; plan `~/.gstack/projects/aakas/aakas-main-eng-review-test-plan-20261102-2315.md`.
- **PERFORMANCE:** batched loads, cap 500, polling only while active.
- **REALITY:** premise-table finding (P1 false: Compare extracts named_insured) db.py:154; already-done pass (no queue code on main) routes/runs.py:87; surface-check pass (routes/fields verified) routes/runs.py:105; binding-decisions pass (W1-W6 kept) spec:12; numbers pass (200/500/14d/3s stated as bounds, not measurements) spec ledger; deferred-asks n/a (no linked issue); pr-count pass (one backend lane, one UI lane, one motion lane) spec section 5.
- **OUTSIDE COVERAGE / CROSS-MODEL:** Codex read-only, completed; 10/10 confirmed by Claude.
- **VERDICT:** ENG CLEARED, ready for the backend lane (Opus) once the Tasks lane T7 merges for the UI lane.

NO UNRESOLVED DECISIONS
