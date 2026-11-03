# Queue UI + Activity brief (work queue task 2)

Written before building. Sources: `docs/specs/2026-11-02-work-queue-design.md` (section 2; ledger R1 to R11 and amendments
M1 to M8 override the text above them, here D5 polling, D6 draft Undo, D9 Undo toasts, D11 Activity cap, M1, M5),
`backend/API.md` (Work queue, `PATCH /api/runs/{id}`, `POST /api/runs`), `DESIGN.md`, `tokens.css`, `HANDOFF.md` and the
earlier briefs (`PERSONAL-LINES-BRIEF.md`, `TASKS-BRIEF.md`, `POLICY-CHECK-BRIEF.md`, `WIZARD-BRIEF.md`).

Skills loaded before the first line of UI code: gstack, ui-ux-pro-max, taste-core, taste-redesign, impeccable,
emil-design-eng, frontend-design. `impeccable context` reported `NO_PRODUCT_MD` again; this is a scoped change inside an
existing visual world, so it proceeds on DESIGN.md (PRODUCT.md still missing, flagged).

**Design read (taste-core 0.B):** Operate mode. A broker opens Personal lines or Workflows to answer one question: what
do I do next, and for whom. The owner rejected the engine view ("Run 8", file chips, slot letters, engine statuses).
So every row is named by the client and carries exactly one next action; raw runs move to Activity.
DESIGN.md pins Inter, `#6356F0`, 6px controls / 4px chips, Phosphor, hairlines without shadows, and the motion tokens.

**Dials (taste-core):** variance 2, motion 1 (the motion pass is a separate lane), density 5.

**Where the boldness goes (frontend-design):** the status detail of a Needs review row: how much work is left, by
severity ("9 open: 2 critical, 5 high, 1 medium, 1 low"), with the numerals in the severity tokens Policy Check already
uses. It is the one thing this list knows that a generic to-do list does not. Everything else is quiet.

## Layout

```
Personal lines                                     (h1, existing header and two start actions stay)
Homeowners, auto and other household policies.
[Compare homeowners quotes] [Check a renewal]

In progress  1                                      (slim strip, canvas tint, no primary button)
  (spin) Jordan Pellham (lake house)   Policy Check, renewal check   Reading documents   Open   ...

Needs review  1
  Avery and Jordan Pellham                          9 open: 2 critical, 5 high,      [Review]  ...
  Policy Check, renewal check                       1 medium, 1 low
Ready  1
  Copperfield Marine Services                       ! Proposal didn't finish         [Build proposal] ...
  Compare Quotes, 2 carriers
Needs attention  2
  Brightwater Logistics Inc.                        ! Stopped before finishing       [Retry]  ...
  ...
> Done recently  3                                  (disclosure, collapsed by default)

See every run in Activity
```

- One component (`queue/QueueView.tsx`) used by Personal lines (`line=personal`) and the Compare launch screen
  (`#/`, line from the entry preset, commercial by default). The Compare screen keeps its upload panel; its main area
  shows the queue under the "Compare Quotes" title instead of Recent runs.
- Sections in the spec order. Each is an `h2` with its count (the accordion count chip). Empty sections are not drawn.
  All five empty: one calm empty state carrying the start actions (Personal: the two links; Compare: the large drop
  zone plus "Check a renewal"). `truncated`: one small note above the list.
- Rows: a list with hairline dividers (no card per row, no shadow). Grid: identity | status | action | menu. Identity is
  the client name (16px semibold; "Unnamed client" in muted text when null) and a muted second line
  "<workflow> · <summary>". A name read from the documents (`client_source: extracted`) carries a small muted
  "from the documents" tag, so a broker knows it was not typed.
- One primary action per row: Review (Needs review: opens the run at its first open discrepancy, `?discrepancy=`),
  Build proposal (Ready), Retry (Needs attention, only when `retryable`). Rows with no next step (In progress, Done
  recently, a non-retryable Needs attention row) get a quiet "Open" link instead of a button.
- Overflow menu (`tasks/RowMenu`): Mark done, Dismiss, Open run, Move to Personal lines / Move to Commercial. Done
  recently rows: Open run, Move back to queue (only when the item was marked done or dismissed). In progress rows:
  Open run and Move only (M1).
- Mark done / Dismiss: no confirm; the row moves to Done recently at once (optimistic) and a toast "Marked done." /
  "Dismissed." offers Undo (PATCH `queue_state: open`). Move: the row leaves this page; toast "Moved to Personal lines."
  with Undo (owner may veto: the run header's re-tag has no Undo, but there the run stays on screen).
- Live (D5): the queue polls every 3 s while In progress is non-empty and the document is visible; it stops otherwise
  and resumes when the tab becomes visible. An error shows the existing error notice with Retry and stops polling until
  the next navigation or Retry. A row that leaves In progress is announced once ("Avery and Jordan Pellham moved to
  Needs review.") in one polite status region.
- Phone (<700px): the row stacks: identity, status, then the action and the menu on one line.
- Footer: "See every run in Activity" (`#/activity?line=<line>`).

## Build proposal (D6, M5)
Reads the comparison (`GET /api/runs/{id}`) and the document list, writes a new proposal draft with the comparison's
documents (in column order), the client name and `source_run_id` + `line`, then opens `#/proposals/new`. A saved
unfinished draft is replaced and a toast "Replaced your unfinished proposal draft." offers Undo, which restores it
(the open wizard reloads it). The wizard shows "From the comparison for <client> (run N)." under its title, and Generate
sends `source_run_id` and `line` to `POST /api/runs`.

## Client field (W4)
- Compare upload panel and Policy Check step 1: "Client" text input, labelled above, "optional" tag, 120 characters,
  sent as `client_name` (empty = not sent). Policy Check keeps it in its saved draft.
- Run header: next to the Line control, a quiet button "Client: <name>" (or "Add client") that becomes an input in
  place. Enter or leaving the field saves (PATCH `client_name`, `""` clears), Esc cancels. Optimistic with rollback and
  an error toast, like the Line control.

## Activity `#/activity`
Full-width page like Personal lines: title "Activity", one line "Every run, newest first.", a segment filter All /
Commercial / Personal (native radios in the Line-choice look; `?line=` preset from the link that opened it, kept in the
URL with `replaceState`), then `RecentRuns` unchanged. At 100 runs: "Showing the newest 100 runs." (D11). Linked from
the queue footer and the run view's "All runs" tab. Rail: Personal lines when the preset is personal, else Workflows.

## Decisions and what each skill said

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design |
|---|---|---|---|---|---|---|---|
| Rows named by client, one primary action | Voice: plain words, name the action | Visible primary action, 44px targets | No duplicate CTA intent per row | Every control does one job | Operate: next step obvious, scanability first | n/a | Name things by what users understand |
| Hairline list, not cards | n/a | Consistency with the existing run list | Cards only when elevation means hierarchy | Remove generic card look | Brand lives in precise details | n/a | Restraint; structure is information |
| Severity counts as the one expressive element | n/a | Never colour alone: words + numbers | Colour lock: severity tokens only | Tabular figures for numbers | Reuse the Policy Check silhouette | n/a | Spend boldness in one place |
| Section h2 + count, empty sections hidden, one calm empty state | n/a | Empty state explains and offers the next step | No duplicate CTAs (start actions once per state) | Compose an empty state | onboard: emptiness is an invitation | n/a | Empty screen = invitation to act |
| Done recently collapsed disclosure | n/a | Progressive disclosure, `aria-expanded` | n/a | Hover/focus states | Keep the working set in view | Caret only now; height motion is the motion lane | n/a |
| Poll while active, stop on error, one announcement | n/a | Contextual live update, one atomic status, no focus move | n/a | Loading = skeleton, not spinner | harden: failure stops and says how to recover | No motion on data refresh | n/a |
| Mark done / Dismiss / Move: Undo toast, no confirm | Error copy says what to do | Destructive actions need a guard: Undo is it (D9) | Toasts for transient news | No `window.confirm` | Reversible beats confirmed | Toast enter 250 / exit 150 (existing) | Same verb in action and toast ("Mark done" / "Marked done.") |
| Overflow menu reuses RowMenu | n/a | Menu keyboard support | One component per intent | Reuse | Native affordances | Scale from trigger 150ms, pointer only (existing) | n/a |
| Client field labelled above, optional tag | n/a | Visible label, helper, error near field | No placeholder-as-label | Inline validation | Forms: labels, not hints | n/a | "Client" is the broker's word |
| Header Client as inline edit | n/a | 44px hit area | n/a | Focus ring | Fast path where the run is | Keyboard actions instant | Action names what it does |
| Activity: filter segment + RecentRuns as is | n/a | Visible filter label, one tab stop | Reuse the segment silhouette | No new radius | Raw runs belong in their own place | n/a | Adds no new visual idea |

## Deliberate choices against a skill
- taste-core / taste-redesign push away from Inter and violet; DESIGN.md pins both (override path, as in every lane).
- ui-ux-pro-max "confirm before destructive action": D9 chose Undo over confirm.
- impeccable `NO_PRODUCT_MD`: not written (out of scope), flagged.

## Owner calls taken by default (owner may veto)
- Rows without a next step show a quiet "Open" link (spec lists only Review / Build proposal / Retry).
- Mark done and Dismiss both appear on every open row (Needs review, Ready, Needs attention).
- "Move back to queue" only on Done recently rows that were marked done or dismissed (a resolved check or a built
  proposal would land in Done again).
- Move from the queue has an Undo toast.
- The client-name source tag "from the documents" on extracted names.
- The launch tab reads "Work queue" (it was "All runs" over the run list); the run view's "All runs" tab opens Activity.
- Activity crumbs "Activity"; filter preset from the opening link.
