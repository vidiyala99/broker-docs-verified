# Voice + motion brief (Phase A: directions and motion audit)

Written before building. Phase A only: three voice directions behind a dev-only switch, a motion audit, one
recommendation. Nothing changes for the default app. The owner picks a direction before Phase B.

Sources: owner notes 2026-11-03 ("all the icons have no flavour/product vision ... looks like a typical SaaS";
after the queue lane: functional, still typical SaaS; the Inter opsz font proposal was too timid), `DESIGN.md`,
`QUEUE-BRIEF.md`, `HANDOFF.md`, `docs/reference/`. Structure stays (rail, panels, queue sections, run view).

Skills loaded before the first line of code: gstack, ui-ux-pro-max, taste-core, taste-redesign, impeccable,
emil-design-eng, frontend-design, improve-animations, animate, apple-design, deliberate. `impeccable context`
reported `NO_PRODUCT_MD` (scoped change on existing code, so it proceeds on DESIGN.md; PRODUCT.md still missing).

## How to see a direction

Dev server only (`import.meta.env.DEV`): add `?voice=a`, `?voice=b` or `?voice=c` before the hash, for example
`http://localhost:5230/?voice=c#/personal-lines`. The choice is kept for the tab (sessionStorage) so links keep it;
`?voice=off` clears it. The switch sets `data-voice` on `<html>`, loads that direction's font and stylesheet
lazily, and swaps the queue and run-header marks. Without it, no new CSS, font or markup reaches the page (the e2e
suite runs this way). Code: `src/voice/` (`voice.ts`, `marks.tsx`, `voice.css`) plus small guarded branches in
`queue/QueueView.tsx` and `run/RunView.tsx`.

Scope of each direction: the queue (`#/` and `#/personal-lines`: page title, group headers, rows, marks, status)
and the run view header (title line and client). Plus, shared by all three, the prototype sliding segmented
control on `.line-choice` (Line of business, Tasks filter, Activity filter, run header Line).

## Diagnosis of the current queue (taste-redesign audit, deliberate "tells")

- Every row speaks at one pitch: tinted 36px tab + stock duotone glyph, 16px semibold name, muted line, violet
  button. Six identical violet buttons stack down the right edge of Needs attention (current-1440-queue.png).
- The glyphs are a stock family restyled (scales, shield-check, handshake, seal): their frequency is invariant to
  the brief, which is deliberate's definition of a tell. Nothing on screen says "pages", "carriers", "renewal".
- Type is Inter at four sizes and two weights everywhere; the client (the person the broker works for) and the
  system's words (workflow, status) are set the same way.
- The product's one promise (every value traced to a page in the source document) is invisible on the queue.

## Design read (taste-core 0.B)

Reading this as: an Operate-mode work queue for insurance brokers, whose world is declarations pages, carrier
quote packets, renewal schedules, ACORD forms, rubber stamps and cited pages; the voice must come from that
paper, inside the BrokerDocs structure. Dials: variance 3 (structure is pinned), motion 4, density 5.

## The three directions (decision sheets, deliberate format)

### A. "Schedule": the queue as an ACORD form
Rationale: brokers live in forms; ACORD and declarations pages have a strict visual grammar (ink section bars,
boxed fields with small captions, check boxes). Using that grammar makes the queue read as the broker's own
paperwork rather than an app list. It is the most structured and the quietest in colour.

```
SUBJECT    the broker's to-do list as a filled-in form
GROUND     ACORD forms: inverted section bars, ruled field boxes, field captions, check boxes, form codes
PALETTE    ink #35383B (bars, rules), paper #FFFFFF, field rule #848D99, accent #6356F0 (action only),
           attention #93370D; neutral bias cool grey (existing)
TYPE       IBM Plex Sans Condensed 600 for section bars, client names and form captions (condensed is the
           form-column face; Plex's engineering-document heritage); Inter for body and buttons
SPACE      intra-field 4/8; between form sections 32; rows are boxed cells, not padded strips
SHAPE      form grid square (radius 0, it is paper); controls keep 6px (documented exception: paper vs controls)
MOTION     the reading line in the In progress box (shared); no signature motion of its own (a tick-in on
           Mark done was planned and cut: a form should feel still)
SIGNATURE  ink section bars with a white condensed title and the count in a boxed field
ICONS      line-drawn form marks, 1.5px stroke, square caps, on a 24 grid: a quote schedule sheet with three
           ruled columns (Compare), expiring/renewal pair with a tick box (Policy Check); group marks are
           form states (box with reading line, box with loupe, box with signature line, torn-corner sheet,
           ticked box)
STATUS     the status column is a boxed field with a caption ("Status") and a check box state
```

### B. "Bound": ink and stamps on the broker's desk
Rationale: the physical vocabulary of an agency office (received, reviewed, returned, filed) is stamps and
inked marks. Status becomes a stamp the eye finds instantly; client names get a wide letterhead grotesque.
The loudest direction, with one signature motion: the stamp lands when work is filed.

```
SUBJECT    each row is a file on the desk, stamped with where it stands
GROUND     rubber stamps, ink pads, binder clips, letterhead
PALETTE    paper #FFFFFF, ink violet #4F42D8 (stamps, existing doc-1 fg), red ink #B42318 (returned/critical),
           brown ink #93370D (attention), carbon #35383B (names); neutral bias cool grey
TYPE       Archivo variable on its width axis: client names at wdth 112 / 700 (letterhead), stamps at
           wdth 62 / 800 uppercase (stamp dies), section headers wdth 112 / 800 16px; Inter for body
SPACE      intra-row 4/8; sections 32
SHAPE      stamps: 2px double rule (border + inset outline), radius 2px, rotated -2deg; controls keep 6px
MOTION     stamp lands (scale 1.06 to 1, opacity, rotate settles, 180ms ease-out) when a row arrives in
           Done or Needs review after the queue was shown; reduced motion: fade only
SIGNATURE  the stamp in the status column ("In review", "Ready to propose", "Returned", "Filed")
ICONS      filled ink silhouettes (stamp-die style, no strokes): binder-clipped quote stack (Compare),
           sealed renewal (Policy Check); groups: dater stamp, red pencil, binder clip, bent flag, filed tray
STATUS     stamp word + the existing counts; the four-tick severity meter stays
```

### C. "Paper trail": the source documents are the picture
Rationale: the core promise is that every value traces to a page. So each row's mark is its own paperwork, a
fanned stack of the actual source documents with their letter tabs in the document hues the report already
uses, and review states reuse the dashed citation box from the PDF viewer. The client, the person the work is
for, is set like the named insured on a declarations page.

```
SUBJECT    each row is a client's file of source documents
GROUND     declarations pages, quote packets, page citations ("p. 14"), the dashed passage box in the viewer
PALETTE    paper #FFFFFF, document hues (DG11) #A99CF7 #F97066 #3CCB7F #0188FB #FF9C66 as tab marks only,
           citation violet #6356F0 (dashed box), ink #35383B, attention #93370D; neutral bias cool grey
TYPE       Source Serif 4 (optical sizes) 600 for client names and group headers: the named-insured line of a
           dec page, legal document type; Inter for everything the system says (workflow, status, actions)
SPACE      intra-row 4/8; sections 32; the mark column widens to 44px to hold a fanned stack
SHAPE      sheets 2px radius (paper); controls 6px; chips 4px (existing rule)
MOTION     In progress: a reading line travels down the page mark (replaces spinner + sweep), 1.6s linear;
           reduced motion: static line. Row enter/exit reuse ui/motion.
SIGNATURE  the fanned document stack with coloured letter tabs, one sheet per source document
ICONS      drawn sheets: Compare = N fanned quote sheets (N from "N carriers", capped at 4) with doc-hue tabs;
           Policy Check = expiring sheet (dashed, the old) behind the renewal sheet (solid) with a small
           arrow; groups: sheet being read (line), sheet with the dashed citation box (Needs review), sheet
           with a ribbon bookmark (Ready to hand over), sheet with a missing corner (Needs attention), sheet
           with an embossed seal (Done)
STATUS     counts set as "9 open" with the worst tier word; Needs review rows get the dashed citation frame on
           the mark; In progress shows "Reading page by page" via the moving line
```

### Critique of the sheets before building (frontend-design, deliberate)
- A first leaned on "hairline broadsheet" (frontend-design trait 3). Changed: the grammar is ACORD's inverted ink
  section bars and boxed captioned fields, which are specific to insurance paperwork, not a newspaper.
- B first used rubber stamps on every element (kitsch). Changed: one stamp per row, in the status column only;
  everything else stays quiet; width axis (wide for who, narrow for state) is the type idea, one family.
- C first used a serif because "documents feel serif" (taste-core's flagged reflex). Kept, with a reason that
  is specific: it separates the person (client, serif) from the system's words (sans), and policy wording is
  set in serif text faces. Not Fraunces or Instrument Serif.
- All three: the owner explicitly asked for drawn marks tied to insurance work, so taste-core's "never hand-roll
  SVG icons" is overridden by the brief (impeccable and deliberate: the brief wins). Each set is drawn on one
  grid with one stroke or fill rule.

## Decisions and what each skill said

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design | improve-animations | animate | apple-design | deliberate |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Dev-only `?voice=` switch, lazy CSS/fonts, default untouched | Name the file and the user-visible impact | n/a | Redesign mode: preserve IA, audit first | Do not break existing function; test after | Refinement preserves the incumbent until the owner chooses | n/a | n/a | n/a | n/a | Prototype interactively: a demo beats static designs | Decide before generating; the switch makes the decision visible |
| Three distinct worlds (form / stamp / document) | No AI vocabulary in names | Match style to product type | Brief inference first; avoid the defaults | Fix priority: font swap, colour, states | Operate: brand lives in precise details; be bold, not timid | Taste is trained; unseen details compound | Ground in the subject's materials and vernacular | n/a | n/a | Familiarity: metaphors neither too literal nor too abstract | A tell is a choice invariant to the brief; each sheet derives from this brief only |
| Drawn marks instead of Phosphor | n/a | SVG, one family, consistent stroke, aria-hidden | Never hand-roll (overridden by the owner's brief) | Replace cliche metaphors (scales, shield) | The brief wins over saturated-pattern warnings | n/a | Visual structure is information | n/a | n/a | Craft: nothing random, one grid | Asset honesty: icons drawn for this subject |
| Display face for client names (A Plex Condensed, B Archivo wide, C Source Serif 4) | n/a | Hierarchy by size and weight; body stays 16/14 | Inter is a default to avoid; serif only with a specific reason | Font swap is the biggest lift | typeset: pair for contrast of roles | n/a | Type carries the personality; one or two families, clearly distinct | n/a | n/a | Tracking is size-specific; weight + size + leading as a set | Type roles: display / text / utility |
| Colour with meaning (ink bars / ink stamps / document hues) | n/a | Never colour alone; contrast 4.5:1 | One accent; colour lock | One grey family | colorize: colour must carry meaning | n/a | Spend boldness in one place | n/a | n/a | n/a | Palette: named roles, stated neutral bias |
| Status treatment (field box / stamp / citation frame) | n/a | Status word + glyph, not colour | No decorative dots | Words, not pills | Reuse existing silhouettes where possible | n/a | Name things by what users understand | n/a | n/a | Feedback: status, completion, warning, error | Weight states above appearance |
| Signature motion (A tick / B stamp lands / C reading line) | n/a | Motion conveys meaning | Motion must be motivated | transform/opacity only | animate: purposeful | ease-out, <300ms, never scale(0), no keyboard animation | One orchestrated moment beats scattered effects | Frequency map drives severity | Gate: rare/occasional; purpose named; CSS keyframe/transition | Response first; reduced motion = gentler equivalent | Motion: count of moments + duration band |
| Sliding segmented control (prototype) | n/a | Motion shows spatial continuity | n/a | Instant state changes feel broken | n/a | Transitions, not keyframes, for rapid UI; no keyboard animation | Motion answers a person's action | Missed opportunity: active state teleports | Tab indicator recipe; ease-in-out for on-screen move, 200ms | Interruptible: retarget from the current value | States are where tells survive |

## The directions as built (screenshots in `qa-screenshots/voice/`)

Each voice has `<v>-1440-queue.png` (`#/`), `<v>-1440-personal.png`, `<v>-1440-run.png` (run 7 header),
`<v>-390-queue.png`, `<v>-390-personal.png`; the default app is `current-*` with the same names. All captured
on one fresh seed. `r1-*` are round-1 working shots.

| | Name | Rationale (3 lines) |
|---|---|---|
| A | **Schedule** | The queue drawn as an ACORD form: ink section bars, captioned columns (Named insured / Status / Next step), printed rules, square cells. Reads as the broker's own paperwork; the calmest in colour. Weakest on the promise: it shows structure, not sources. |
| B | **Bound** | The broker's desk: a stamp in every status cell (In review, Ready to propose, Returned, Filed), wide letterhead Archivo for names, filled ink marks. The loudest; its signature moment is the stamp landing when a row arrives. Risk: novelty wears and stamps compete with the severity words. |
| C | **Paper trail** | Each row's mark is its own paperwork: a fanned hand of source sheets (one per carrier, tabs in the document hues the report uses); review states reuse the viewer's dashed citation box; the client is set in Source Serif 4 like the named insured. The only direction whose picture is the product's promise. |

What changes per direction (all three): page title, section headers, client names, workflow and group marks,
status treatment, Personal lines start buttons, run-header title mark and client name. C also steps Needs
attention's repeated Retry down to the secondary look, so one violet button leads each region.

## Recommendation: C, "Paper trail"

1. It is the only direction derived from what BrokerDocs promises rather than from the office around it: the mark
   is the documents, the hues are the DG11 document hues, the review motif is the viewer's citation box. Every
   piece already exists in the report, so C extends the app's language instead of adding a second one (A's ink
   bars and B's stamps would be new vocabularies that the report never uses).
2. It scales to the rest of the app in Phase B without inventing more: rail marks become sheet states, the Upload
   rows can show their fanned tab, the In progress reading line replaces generic spinners on the run page.
3. Serif for the person, sans for the system is a rule a broker feels at a glance (who vs what) and costs one
   font file (Source Serif 4, lazy).
Borrow from B for Phase B if the owner wants one more moment: the 180ms "lands" on a row arriving in Done (as a
seal on the sheet, not a stamp word). Weak spots of C to fix in Phase B: the run-header mark is small next to the
toolbar; the 2-sheet fan reads close to a plain document glyph; the Policy Check fan needs the dashed box drawn
heavier at 22px.

## Self-review rounds (3 of 3, Playwright MCP at 1440 and 390)

1. Built as briefed, drove `#/`, `#/personal-lines`, `#/runs/7` in each voice. Fixed: C's sheets overlapped into
   one glyph and the tabs vanished (redrawn as a fanned hand on a 48x36 frame, one tab per sheet on a visible
   corner); B stamped a failed proposal "Ready to propose" above "Proposal didn't finish" (Ready + reason now
   stamps "Returned" in red); the client name truncated in all three run headers (cap 220 to 300/320px); A's 22px
   button marks pushed Personal lines 4px past the pane at 390 (marks keep the 18px glyph footprint, scaled);
   B's 36px expanded title and C's 52px mark column crowded phone rows (phone sizes added).
2. Motion sampled in the page: the sliding segment measures the option width exactly (123.65px), slides 132 to 0px
   in 200ms on `--ease-in-out`, and a reversal mid-flight continues from the live value (77, 121, 105, 0: no snap);
   with keyboard modality (`:focus-visible`) it moves instantly. B's stamp lands (scale 1.12 to 1, rotate settles,
   about 180ms) when a retried row enters In progress; fixed: that row inherited the red "Returned" tilt from its
   stale reasons. C's reading line moves 0 to 9px linearly while a run reads. Reduced motion (emulated): segment
   `transition: none`, reading line static.
3. Final capture on a fresh seed; axe (wcag2a/aa, 21aa) 0 violations for A, B, C on `#/`, `#/personal-lines`,
   `#/runs/7` at 1440 and on the two queue pages at 390. No open findings from the rounds beyond C's weak spots
   above. The final capture ran as one Playwright MCP code block (navigate, wait, screenshot, axe) for consistency
   across 20 shots; the design passes before it were driven step by step.

Pre-existing, not caused here: at 390 the Compare queue (`#/`) scrolls sideways and clips the row menu (present
in `current-390-queue.png`); severity counts wrap mid-phrase at 390 ("1 / medium").

## Motion audit (improve-animations, standard effort; read-only)

Recon: plain CSS + a few React timers, no motion library. Tokens `--ease-out` (0.23,1,0.32,1), `--ease-in-out`
(0.77,0,0.175,1), `--dur-press` 120, `--dur-fast` 150, `--dur-base` 200, `--dur-toast` 250. Primitives from the
queue lane in `ui/motion.tsx` / `motion.css`: `usePresence` (list enter/exit), `Collapse` (grid-rows disclosure,
pointer-gated), `Count` (bump), `.motion-live` (sweep). Grep: no `transition: all`, no `ease-in`, no `scale(0)`, no
width/height/top/left animation; only 4 of 9 CSS files have a reduced-motion block.

### Inventory (34 items) and proposals

Counts: 11 segmented controls / toggle groups, 3 tab sets, 5 list filters, 9 collapsibles, 4 overlay surfaces
(toast, 2 dialogs, menu; plus tooltip), 1 route change. Today: segment, tab, stepper and filtered-list state
changes are instant (no motion at all); disclosures fade bodies on pointer opens only; dialogs and menu scale in on
pointer opens and close instantly; routes swap with no transition.

| Kind | Where | Current | Proposed (property, duration, curve, interrupt, reduced motion) | Reuse |
|---|---|---|---|---|
| Segmented (radio) | `personal/LineChoice.tsx` full + compact, Tasks filter `TasksPage.tsx:395`, Activity filter `ActivityPage.tsx:59` (all `.line-choice*`) | colour swap 150ms ease, press scale | Shared sliding indicator: `transform` translateX, 200ms `--ease-in-out`, transition (retargets mid-flight), instant on keyboard (`:has(input:focus-visible)`), reduced = instant. **Prototyped** in `voice/voice.css`, all three voices | new (CSS only) |
| Toggle group | Policy severity filters `policy/Discrepancies.tsx:239` | colour 150ms, press | Multi-select, so no single indicator: keep colour; check glyph fades in 120ms opacity | none |
| Toggle list | Outline trust filter `report/Outline.tsx:114` | instant (no transition) | bg 150ms ease like the others (consistency) | none |
| Toggle | Run toolbar icon buttons, PDF Fit | press + bg | keep | n/a |
| Cards | Template picker `wizard/StepTemplate.tsx:72` | press uses literal `scale(0.99)` (wizard.css:378) | use `--press-scale` so reduced motion drops it | token |
| Sort | Tasks sort buttons | rows re-sort instantly | rows: no motion (data being read; frequent) | n/a |
| Tabs | Run Report/PDF tabs `RunView.tsx:497` (folder tabs, `aria-current`) | instant | Active folder tab: background/border cross-fade 150ms; PDF tab enter (opened from a chip): `motion-enter` 200ms; close: `motion-exit` 190ms; no sliding underline (folder-tab look); keyboard instant | `usePresence` |
| Tab | Launch "Work queue" (static) | none | none (not a control) | n/a |
| Stepper | Wizard `Stepper.tsx` | marks swap; step body swaps | step body: opacity 150ms + 8px translateX in the direction of travel on pointer Next/Back; keyboard instant; reduced = opacity | new keyframe |
| List filter | Tasks search/filter, Discrepancy list, Activity list, Template search | instant swap | Rows that leave on a filter change: `motion-exit` (opacity + fold, 190ms); rows that arrive: `motion-enter` (200ms); only on pointer filter clicks; search typing stays instant (keyboard, frequent) | `usePresence` |
| List | Work queue rows | enter/exit keyframes; also play on keyboard actions | Gate on input method like `Collapse` (keyboard row actions instant) | `usePresence` + `animate` flag |
| Collapsible | Accordion, Chat handle, Upload fold, pickers, add-section | body fades on pointer; caret rotates 200ms also on keyboard | Bodies: `Collapse` (height + opacity, pointer only); carets: instant on keyboard | `Collapse` |
| Collapsible | Done recently | `Collapse`, pointer-gated | keep (reference implementation) | `Collapse` |
| Collapsible | "+N more" Fold, Outline show/hide | instant | Fold rows: `Collapse` pointer-only; Outline: opacity 150ms + 12px translateX from the edge it lives on, pointer only | `Collapse` |
| Panel | Narrow left panel overlay | scrim fades, panel none, no exit | panel `transform` translateX(-100%) to 0, 250ms `--ease-out`, exit 200ms; reduced = opacity | new |
| Panel | Split source pane | `pane-in` keyframe, no exit | exit mirrors enter (same edge), 150ms | existing keyframe |
| Toast | `ui/Toast.tsx` | enter keyframe 250ms, exit transition 150ms bare `ease` | Enter as a transition (`@starting-style`) so a replacing toast retargets; exit on `--ease-out`; reduced = 150ms opacity (today 0ms, see B1) | none |
| Dialog | Task dialog, Review dialog (native `<dialog>`) | scale 0.98 + opacity 200ms keyframe on pointer open; no exit; task dialog has no reduced block | add exit (opacity + scale 0.98, 150ms) and backdrop fade; task dialog gets the review dialog's reduced fade | review dialog pattern |
| Menu | `tasks/RowMenu.tsx` | scale 0.97 + opacity 150ms from top right, pointer only; no exit; still scales under reduced motion | add 120ms exit; reduced = opacity only | none |
| Tooltip | `ui/Tip.tsx` | opacity 150ms, instant within 400ms | skip the fade on keyboard focus too | none |
| Route | hash routes in `App.tsx` | instant swap, breadcrumb flashes | View Transitions API cross-fade of the main pane only, 150ms opacity, skipped for back/forward keyboard and under reduced motion; rail and panels do not move | new (one wrapper) |

### Top findings (vetted at file:line)
1. **B1 MEDIUM, reduced motion removes fades instead of keeping them.** `tokens.css:149-156` zeroes `--dur-base`
   and `--dur-toast`, so toast, split pane, `Collapse`, scrim and caret go to 0ms although DESIGN.md says "opacity
   fades kept". Fix: stop zeroing durations; drop transforms per component and add a `--dur-fade` (150ms).
2. **B4 MEDIUM, queue row motion plays on keyboard actions.** `QueueView.tsx:96-98` sets `animate` from first-show
   only; Enter/Space on Mark done, Retry, Undo still run exit/enter/bump. Carets rotate on keyboard toggles in five
   places (`app.css:762`, `app.css:1326`, `queue.css:94`, `wizard.css:603`, `wizard.css:874`).
3. **B5 MEDIUM, RowMenu scales under reduced motion** (`tasks.css:406-414`, literal 150ms not zeroed).
4. **B2 MEDIUM, Task dialog has no reduced fallback** (`tasks.css:472-480`), unlike the review dialog.
5. **B3 MEDIUM, keyframes on rapidly triggered UI**: toast enter, queue rows and count bump restart rather than
   retarget.
6. LOW: literal `scale(0.99)` (`wizard.css:378`), toast exit bare `ease` (`app.css:677`), ungated
   `.crumbs a:hover` (`app.css:230`, no transition so harmless).
Missed opportunities (additive): segment/tab/stepper state teleports (segment prototyped); filtered lists swap
instantly; panels and dialogs have no exit.

Primitives reusable as they are: `usePresence` (tabs added/removed, filtered lists, task rows), `Collapse` (every
disclosure body), `Count` (tab and filter counts), `.motion-live` / the C reading line (spinners). Needed new: one
`input-method` helper (pointer vs keyboard) shared by `usePresence` callers, and the segment indicator (done).

## Deliberate choices against a skill
- taste-core / animate "never hand-roll SVG icons": overridden by the owner's explicit ask for drawn marks.
- taste-core "AVOID Inter": body stays Inter in all three ([exact] from the live BrokerDocs app); only display roles
  change.
- DESIGN.md "Inter only": a Phase A proposal; DESIGN.md changes only after the owner picks.
