# T6 design brief: Personal lines, slice 3b

Written before building. Sources: spec `docs/specs/2026-11-02-rail-completion-design.md` (2.1, 2.5, R1, R2, Decision
ledger D4, D5, D7, D9, O5, task T6), `backend/API.md` (Run `line`, `POST /api/runs` `line`, `PATCH /api/runs/{id}`,
`GET /api/runs?line=`), `DESIGN.md`, `tokens.css`, `HANDOFF.md` Gotchas, the prior briefs (`INTEGRATIONS-BRIEF.md`,
`POLICY-CHECK-BRIEF.md`) and the evidence shot `rea-out/shots/personal-lines.png` (the reference product's marketing hero over a
homeowners comparison with citation chips; it shows the report, which already exists, and nothing of a Personal lines
screen, so it pins no layout here).

Skills loaded before the first line of UI code: gstack, ui-ux-pro-max, taste-core, taste-redesign, impeccable,
emil-design-eng, frontend-design. `impeccable context` reported `NO_PRODUCT_MD`; this is a scoped addition to existing
code, so it proceeds on DESIGN.md as the visual authority (PRODUCT.md still missing, flagged again).

**Design read (taste-core 0.B):** Operate mode. A broker who handles household cover wants the homeowners and auto work
apart from the commercial book: one place that lists those runs and two ways to start one. Personal lines is a tag on a
run (R2), not a new engine, so the page is a filtered view plus two entry points, and the tag must be easy to fix where
the run lives. DESIGN.md pins Inter, `#6356F0`, 6px controls, Phosphor regular, hairlines with no shadows, motion
tokens only. Those are not open.

**Dials (taste-core):** variance 2, motion 1, density 5. Same as Integrations: a list screen inside an existing tool.

**Where the boldness goes (frontend-design):** nowhere new. The one memorable thing on this surface is the run list,
which already has its look (Workflows). The new control (Commercial / Personal) is deliberately quiet: it reuses the
Policy Check filter-toggle silhouette, so the app gains no new visual idea.

## Layout

```
Rail [Personal lines active]   Top bar: BrokerDocs / Personal lines
+--------------------------------------------------------------------------+
| Personal lines                                        (36px page title)  |
| Homeowners, auto and other household policies.        (16px, muted)      |
| [Scales Compare homeowners quotes]  [ShieldCheck Check a renewal]        |
|                                                                          |
| Recent runs                                           (16px h2)          |
| Policy check, run 8                                     PARTIAL          |
|   A pc_homeowners_expiring.pdf  B ...                                     |
|   3 documents, Nov 2, 10:04                                               |
| ------------------------------------------------------------------------ |
| Policy check, run 7                                     DONE             |
+--------------------------------------------------------------------------+
Empty:  "No personal-lines runs yet." + one plain sentence + the two actions (they move out of the header).
100 runs returned: "Showing the newest 100 runs." under the list (D5 bound, said once, plainly).
```

- Route `#/personal-lines`; rail item Personal lines live (only that item); crumbs `Personal lines`; title "Personal
  lines". Full-width pane like Integrations (no left panel: nothing to upload here). No phone-width "Best viewed on a
  wider screen" note (it is about report tables).
- Run list: the existing `RecentRuns` component, fed by `GET /api/runs?limit=100&line=personal`. The Workflows launch
  list uses `?limit=20&line=commercial` (D9). `api.runs(limit, line)` is the one parameter point; the component is
  shared, not forked.
- Actions are links (they navigate): "Compare homeowners quotes" -> `#/?line=personal`, "Check a renewal" ->
  `#/policy-check/new?line=personal`. The preset lives in the URL so it survives reload; the rail keeps `#/` and
  `#/policy-check/new`, which mean Commercial.

## Line choice (Compare upload screen, Policy Check step 1, run header)

- One component, `LineChoice`: a `fieldset` with a legend and two native radios ("Commercial", "Personal") drawn as a
  two-button segment. Native radios give arrow-key movement, one tab stop and a checked state for free.
- Compare Quotes (O5, no wizard): inside the Upload section, above the Compare button, legend "Line of business". It is
  a property of the run about to start, so it sits next to the button that starts it.
- Policy Check: first group of step 1, above Policy PDFs, legend "Line of business".
- Both default from the URL (`?line=personal` -> Personal, else Commercial) and are sent as `line` on `POST /api/runs`.
  Changing the radio does not rewrite the URL or move the list: the entry point frames the screen, the radio tags the run.
- Run header: the same segment, compact (32px, inline label "Line" that is its accessible name; see round 1), in the toolbar right
  after the title. Change = optimistic: the segment, crumbs and side list switch at once, then `PATCH`. On failure the
  run rolls back and an error toast shows the server message. On success a toast says "Moved to Personal lines." or
  "Moved to Commercial." (the run left one list for the other, which is otherwise invisible from the run view).
  Changes while a PATCH is in flight are ignored.

## Wayfinding

- Personal run crumbs start with Personal lines: `Personal lines / Policy check / Run 7`, `Personal lines / Compare
  Quotes / Run N`, `Personal lines / Proposals / Run N`. The middle crumb keeps the personal preset (`#/?line=personal`,
  `#/policy-check/new?line=personal`).
- Rail: entry screens opened with `?line=personal` and the Personal lines page highlight Personal lines (the user came
  from there). The run view keeps highlighting by workflow (Policy check for a Policy Check run, whatever its line):
  the rail names the tool, the crumb names the book. This is also what the D7 regression contract needs
  (`policy-check.spec.ts` asserts Policy check is current on seeded runs 7 and 8, which are now personal).
- Run view side links follow the line: "All runs" goes to Personal lines, "New comparison" / "New check" carry
  `?line=personal`, and the left panel's Recent runs lists runs of the same line.

## Decisions and what each skill said

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design |
|---|---|---|---|---|---|---|---|
| Page = title, subline, two actions, shared run list; full-width pane | Voice: plain words, no em dashes; spec copy verbatim | Navigation: deep link per screen, predictable back (crumb + rail) | Grid/flow over invention; one focused message per section; density 5 suits an app list | Work with the existing stack; do not rewrite; current page shown in nav | Operate: scanability and consistency outrank expression; reuse the Workflows list | n/a (static) | Spend boldness in one place; here the list already is that place, so add nothing |
| Two actions as equal secondary buttons with Phosphor glyphs (Scales, ShieldCheck) | n/a | Icons from one SVG family, decorative `aria-hidden`, 44px targets | No duplicate CTA intent: each label appears once per state; icons Phosphor only | "Always one filled + one ghost" is a tell; two peers get the same weight | Controls name the action ("Check a renewal") | Press scale 0.97 already on `.btn` | CTA says what happens; the same verbs as the destination screens |
| Empty state carries the two actions (header drops them) | n/a | Empty state explains and offers the next step | Empty states must say how to populate; no duplicate CTAs | "No empty states" is a defect; compose a getting-started view | onboard: an empty screen is an invitation to act | n/a | Emptiness is direction, not mood: one sentence, two actions |
| Line choice as native radios in a fieldset, segment look borrowed from Policy Check filters | n/a | Visible label (legend), keyboard nav, 44px hit area via `::before` | Full state cycle (hover, checked, focus, disabled); label above control | Focus ring required; no new radius | Native affordance beats a custom widget; reuse the filter-toggle silhouette | Press scale 0.97 at `--dur-press`; no animation on keyboard change | Structure is information: legend says what is being chosen |
| Run header re-tag: optimistic, rollback + error toast, success toast | Errors stay plain and say what to do | Errors announced (`role=alert` via the toast's live region); rapid changes must not corrupt state (ignore while in flight) | Error states contextual; toasts only for transient news | n/a | harden: failure path designed, not left to the console | Toast enter/exit is the existing 250/150ms; no list animation for the move | Action keeps its name: control says Personal, toast says "Moved to Personal lines." |
| Crumbs follow the line, rail follows the workflow on runs | n/a | Breadcrumbs reflect hierarchy; back is predictable | n/a | Every page needs a way back (crumb to Personal lines) | Wayfinding is part of the floor | n/a | Words make the page easier to navigate |
| Motion: none added | n/a | Respect reduced motion | Motion must be motivated; nothing here needs it | Transitions on interactive elements only | One authored moment or none: none | Re-tag is occasional; the toast already says what changed | Motion answers an action; the toast is that answer |

## Deliberate choices against a skill

- taste-core and taste-redesign push away from Inter and violet; DESIGN.md pins both (override path, as in T5/T7/T9).
- taste-core 4.9 calls a long `<ul>` with dividers lazy; the run list is the shared Workflows component (spec 2.1
  requires reuse), so it stays.
- impeccable reports `NO_PRODUCT_MD`; not written (out of scope), flagged again.

## Owner calls taken by default

- The line choice on the entry screens does not rewrite the URL or swap the screen's list; reload returns to the entry
  preset.
- The run view rail highlights the workflow, not Personal lines (D7 forces it for Policy Check runs; applied to every
  workflow so the rule is one rule).
- Success toast on re-tag (no Undo: the same control flips it back).
- The launch screen opened with `?line=personal` lists personal runs under Recent runs (it is the personal entry).

## Self-review rounds (3 of 3, Playwright MCP, screenshots in `qa-screenshots/personal-lines/`)

1. `r1-*`: built as briefed; opened Personal lines from the rail, clicked into run 7, re-tagged it Commercial (crumbs
   and side list switched, toast shown), checked 390 for run 7 and the personal Policy Check entry. Fixed: the header
   segment had no visible label and read as two stray buttons, so it now carries an inline "Line" label (its accessible
   name); the checked option relied on tint and border, so it gains the check glyph (DESIGN.md filter-toggle rule);
   at 390 the compact segment stretched across the row.
2. `r2-*`: at 390 the labelled segment overflowed the 250px run pane, so below 600px the label is visually hidden (still
   the accessible name) and the options size to their text. Keyboard re-tag at 1024 (focus ring, ArrowRight, toast)
   and a forced PATCH failure (rollback to Personal, server message in the toast) both behave.
3. `r3-*`: the Compare upload screen opened from "Compare homeowners quotes" at 1440 (Personal checked, crumbs
   "Personal lines / Compare Quotes", personal runs listed). No new findings. The e2e spec then showed the option text
   sat under the label's `::before` hit area (Playwright saw the label intercept a click on the words); the text and
   glyph now paint above it.

Known, not fixed (existing behaviour, not from this lane): at 390 the run toolbar's report actions (`.toolbar__actions`,
`flex: none`) are about 60px wider than the run pane and clip at the right edge; the page itself does not scroll
sideways. While a run loads, its crumbs briefly show the Compare Quotes fallback before the run's workflow is known.
