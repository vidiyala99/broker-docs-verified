# Rail glyph legibility brief (Phase A: audit, three directions, one recommendation)

Owner-picked pass, 2026-11-03 (HANDOFF "Watch: all 13 rail glyphs share the sheet family and read alike at small
size"). Goal: every rail item is identifiable at a glance at its real size (20px glyph in a 40px item, 56px rail)
and in the 390 layout, inside the Paper trail voice and the reference product's rail structure. Phase A only: three directions
behind a dev-only flag; the default rail is unchanged. The owner picks before anything becomes the default.

Sources: `PRODUCT.md`, `DESIGN.md` (Voice, Motion, "Focus, disabled, rail"), `VOICE-BRIEF.md` (why C won, the
sheet rules, Phase B), `src/voice/marks.tsx`, `src/shell/Shell.tsx`, `HANDOFF.md` 09:30 entry.

Skills loaded before the first decision: gstack, ui-ux-pro-max, taste-core, taste-redesign, impeccable (context
ran: PRODUCT.md and DESIGN.md found; scoped refinement, Operate mode), emil-design-eng, frontend-design.

## How to see a direction

Dev server only (`import.meta.env.DEV`): add `?rail=a`, `?rail=b` or `?rail=c` before the hash, for example
`http://localhost:5210/?rail=a#/tasks`. Hash links keep the query string, so the choice holds while you click
around. Without the flag the rail renders the shipped `Glyph` set: the rail DOM and a rail screenshot on `#/tasks`
are byte-identical to main's `Shell.tsx` (checked by swapping main's file in and comparing).
Code: `src/voice/railMarks.tsx` (the three drawings and the flag) and `src/shell/Shell.tsx` (rail items name a
glyph instead of holding an icon element; `data-rail` on the nav when a preview is on).

## Audit of the shipped rail

Design read (taste-core 0.B): an Operate-mode navigation rail for insurance brokers in long desktop sessions; the
voice is the broker's paperwork; the rail must be scanned, not read. Dials: variance 2 (structure pinned), motion 1
(nothing new moves), density 6.

The rail is the same at every width: 56px wide, 40px items, 20px glyphs (there is no collapsed rail; at 1024 and
below the left panel overlays the page, and at 390 the rail stays as it is). So legibility is one problem at one
size, and the 390 view only adds a neighbour: the left strip's own icons.

| # | Item | State | Glyph today |
|---|---|---|---|
| 1 | Workflows | live | two sheets side by side, one line each |
| 2 | Proposals | live | portrait sheet, thin filled spine, three lines |
| 3 | Personal lines | live | house |
| 4 | Policy check | live | sheet over a dashed sheet, ticked |
| 5 | Certificates | disabled | landscape sheet, two lines, small seal |
| 6 | Tasks | live | portrait sheet (clipboard), two ticked lines |
| 7 | Integrations | live | two small sheets, joined |
| 8 | Assistant | disabled | rectangular speech balloon, two lines |
| 9 | Market search | disabled | half sheet with a loupe |
| 10 | Analytics | disabled | axes and a rising line |
| 11 | Insights | disabled | portrait sheet, three lines, faint band |
| 12 | Vault | disabled | box split in two, two handles |
| 13 | Documents | disabled | two sheets, offset |

Squint test: each glyph rasterised at its real 20px, blurred 1.2px, then compared pair by pair (cosine
similarity of ink; 1.0 = the same picture). It is a proxy for "can I tell them apart without reading", not a
perception study; the crops below are the real evidence.

Shipped: mean 0.812, max 0.952, **8 pairs at or above 0.90**, 27 at or above 0.85. Confusing pairs:

| Pair | Similarity | Why they collide |
|---|---|---|
| Tasks / Insights | 0.952 | same portrait sheet, interior lines are 1px at 20px |
| Proposals / Insights | 0.937 | same portrait sheet; the spine is 1.25px, the band is 30% ink |
| Policy check / Documents | 0.933 | both are two offset sheets; the dash does not survive 20px |
| Tasks / Documents | 0.932 | portrait mass in the same place |
| Insights / Documents | 0.926 | as above |
| Workflows / Vault | 0.919 | a box divided down or across the middle |
| Insights / Vault | 0.911 | rectangle with horizontal strokes |
| Proposals / Tasks | 0.910 | portrait sheet with lines |
| Certificates / Assistant | 0.882 | landscape box with a small feature at the bottom (seal, balloon tail) |

Root cause (first principles): at 20px a 1.5px stroke is 1.25 device pixels, so interior lines, dashes and small
seals carry almost nothing; what the eye uses is the outline and where the mass sits. Eight of 13 glyphs share the
same outline (a 14.5 x 18.5 sheet) and the same even mass, so they can only be told apart by detail that is not
there at this size. The disabled grey (`#9CA3AF`, by DESIGN.md) lowers contrast further on six of them. The voice
did not cause this; using the sheet as the base shape for the nav did.

Crops: `current-1440-rail.png` (real size), `current-{1440,1024,390}-rail@2x.png`, `current-*-rail-squint.png`,
`current-390-page.png`, `current-1024-run-overlay.png`; all in `qa-screenshots/rail/`.

## The three directions

All three keep the rail structure, sizes, colours, active tint, disabled grey, hover and focus from DESIGN.md;
only the drawings change. Comparison sheet with each glyph at 20px, 2x and blurred: `qa-screenshots/rail/compare-sheet.png`.

| | Name | One line | Mean | Max | Pairs >= 0.90 | Pairs >= 0.85 |
|---|---|---|---|---|---|---|
| | Shipped | sheet family | 0.812 | 0.952 | 8 | 27 |
| A | **Silhouettes** | each item its own outline and mass, still paper objects with the sheet fill | 0.781 | 0.920 | 1 | 10 |
| B | **Sheet + mark** | one shared quiet sheet, one bold mark per item laid on it | 0.874 | 0.957 | 28 | 56 |
| C | **Frameless** | the same marks as B, drawn alone at full size, no sheet | 0.725 | 0.880 | 0 | 5 |

### A. Silhouettes
Keeps the five shipped drawings whose outline was already unique (Workflows, Personal lines, Policy check,
Integrations, and Analytics with a dot on its latest figure) and redraws the eight that were "a sheet with lines"
so each has its own outer shape or mass:
- Proposals: a bound cover, heavy filled spine (left-weighted mass), a label window, no ruled lines.
- Certificates: the landscape certificate with a rosette that breaks its lower edge.
- Tasks: a clipboard whose clip is solid (top-weighted mass).
- Assistant: a round balloon with one written line (the only round outline besides the loupe).
- Market search: the loupe is the shape; the lines it reads sit inside the lens.
- Insights: a tinted sticky note with a folded corner (the only tinted fill).
- Vault: an archive box, lid overhanging (the only stepped outline).
- Documents: a file folder with its tab.
Remaining closest pair: Assistant / Insights 0.920 (round balloon vs folded square; distinct by eye at 20px, the
metric sees two compact blobs). Crops: `final-a-*`.

### B. Sheet + mark
Every item is the same sheet (stroke at 40%) with one mark at 68% scale in its middle: two quotes read across,
bound spine, house, tick over dashed box, seal, checklist, link, balloon, loupe, bars, sticky note, padlock,
paperclip. Most consistent as a family and closest to "the paper trail", but the shared outline is exactly what
the eye sees first, so similarity goes up, not down (28 pairs over 0.90). At 20px the marks are about 9px and the
boxed tiles read as app icons. Crops: `final-b-*`.

### C. Frameless
B's marks alone at full size, 1.75 stroke. The most separable by the numbers (no pair over 0.90). But without the
sheet most of the marks are the generic set (house, magnifier, padlock, chain link, paperclip, bars, balloon),
which is what PRODUCT.md rules out ("never feels like a generic SaaS dashboard") and what the Paper trail voice
replaced. Crops: `final-c-*`.

## Recommendation: A, Silhouettes

1. It fixes the cause, not the symptom: the confusion came from eight glyphs sharing one outline, and A gives each
   its own outline and mass while keeping the sheet fill, the 24 grid, round joins and the 1.5 stroke. Over-0.90
   pairs go from 8 to 1, and the one left is a metric artifact the crops do not show.
2. It stays in the voice. Every new drawing is paperwork (cover, certificate, clipboard, note, archive box,
   folder); nothing reads as a stock set, unlike C.
3. It is the smallest change: 5 of 13 drawings stay as shipped, so the rail still matches the queue and run marks
   the owner already approved.

If A is chosen, Phase B decisions to make: whether the Proposal workflow mark elsewhere (queue rows, wizard title,
start buttons) adopts the new bound cover so the same thing keeps the same mark; and whether the left strip's own
icons (panel, upload, chat; visible beside the rail at 1024 and 390) join the voice. Both are outside this pass.

## Decisions and what each skill said

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design |
|---|---|---|---|---|---|---|---|
| Diagnose by outline and mass at real size, with a measured squint test | Name the file and the user-visible impact | Icon legibility at size; consistent per context | Audit before touching (redesign protocol 11.B) | Diagnose before fixing; work with the existing stack | Operate: scanability outranks expression; verify in bounded passes | Unseen details compound; review at real size | Visual structure is information |
| Previews behind a dev-only `?rail=` flag, default byte-identical | Plain words; one flag | n/a | Preserve IA and nav labels; nothing changes silently (11.F) | Do not break existing function; test after | Refinement preserves the incumbent until the owner picks | n/a | n/a |
| A: one outline and mass per item, sheet material kept | n/a | One family, one stroke, SVG, aria-hidden | Hand-rolled marks: overridden by the owner's brief, as in VOICE-BRIEF; one stroke width | Replace cliche metaphors; one stroke weight | The brief wins (drawn paperwork marks) | Beauty is leverage; shape before detail | Ground in the subject's materials (cover, certificate, clipboard, archive box, folder) |
| B: shared sheet + mark, kept as an option, not recommended | n/a | Consistency across a set | Avoid the boxed-icon default | Generic icon tiles are a tell | Expression must not cost scanability | n/a | Spend boldness in one place, not on 13 frames |
| C: frameless marks, kept as an option, not recommended | n/a | Most legible by numbers | Stock-looking set is the default to avoid | Lucide-like default set is a tell | Brand lives in precise details (lost here) | n/a | Distinct over templated |
| Active, hover, focus, disabled unchanged | n/a | Active state visible; focus ring kept; 44px hit area unchanged | Shape lock | Honour accessibility wins | Native expectations in Operate | Hover gated to fine pointers (already) | Quality floor without announcing it |
| No new motion | n/a | Motion must convey meaning | Motion must be motivated | n/a | n/a | Rail is used tens of times a day: no animation | Motion only to answer an action |
| Copy | No em dashes | n/a | Zero em dashes | Plain language | n/a | n/a | Name things by what users understand |

## Review rounds (3 of 3, Playwright MCP)

1. Built all three, drove `#/` at 1440 (2x crops, squint metric). Found: A's pencil for Assistant read as "edit";
   B's marks at 60% were too small and the tiles looked like app icons; C's Insights (three lines) read as a menu
   button; C's Certificates and paperclip were both narrow verticals (0.919). Fixed: A Assistant is a round balloon;
   Insights in B and C is the sticky note; the paperclip is turned 40 degrees; B's mark scale went to 68%.
2. Final captures for shipped, A, B, C at 1440, 1024 and 390 (2x rail crops, full 390 page, squint at 1440) on the
   shared seed; metric re-run (table above).
3. Interaction, driven live: active item on `#/tasks` with hover tooltip on Integrations (A); keyboard Tab into the
   rail with the focus ring and "Proposals: new proposal" tooltip (A, B, C); 1024 run page with the left panel
   overlay open beside the rail (shipped, A, B, C). Axe (wcag2a/aa, 21aa): 0 violations with each flag on `#/` and
   `#/tasks` at 1440 and 390 (12 checks). No open findings beyond the Phase B questions above.

## Not verified
- The squint metric is a proxy (translation-sensitive cosine on a blurred raster); the owner's eye decides.
- Windows ClearType and non-Chromium rendering of the 20px glyphs were not checked.
- Dark theme is out of scope (PRODUCT.md).
