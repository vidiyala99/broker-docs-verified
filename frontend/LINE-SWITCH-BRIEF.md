# Line switch brief: a clear transition when the line of business flips

Owner complaint (2026-11-03): "I still don't get why we don't have clear transitions when we switch between personal and
commercial" (Policy Check wizard step 1, `#/policy-check/new`, the "Line of business" segment). Direction approved by the
owner before this lane: one joined track with a visible slide, glyph and label cross-fade, and the choice shown where
the work is named (book-first crumb and the run header's book tab). Builds on `LINE-MOVE-BRIEF.md` (direction B).

## What was wrong (Playwright MCP, before)

1. The indicator did move, but on `--ease-in-out` (0.77, 0, 0.175, 1) at 200ms almost nothing happens in the first
   half and the rest lands in about four frames: it reads as a snap. Numbers in "Frame sampling" below.
2. Everything around it was instant: the check glyph toggled `visibility`, and the two options were separate bordered
   buttons 8px apart, so the indicator flew across an empty gap between two boxes. Two buttons swapping state, not one
   control with a moving selection.
3. Nothing else on the page changed: the title, the crumbs ("Policy check > New check") and the tab stayed the same.

First principles: the broker flips the line to say which book the new check is filed in. The flip must (a) be seen as
one thing moving from here to there, from the first frame, and (b) be confirmed by the page saying the book, in the same
words and the same tab the finished run will show.

## Decisions

| Decision | Why |
|---|---|
| One joined track: a single 1px `--color-border-input` outline (3.36:1, a UI boundary), no gap, no per-option borders; the indicator (tint fill, primary border) covers its half of the track edge | One control with one selection; the moving thing is the only thing with a primary edge |
| Indicator slides 220ms on `--ease-out` (new token `--dur-segment`) | Ease-out moves most of the way in the first frames, so the eye sees it leave; still under the 300ms ceiling |
| Check glyph: opacity + scale 0.85 to 1, 220ms in, 120ms out; label colour muted to text, same timing | Cross-fade with the slide instead of a visibility snap; exit faster than enter |
| Keyboard (arrow keys) and reduced motion: indicator jumps; under reduced motion the glyph and label still fade | DESIGN.md Motion: keyboard instant; reduced motion keeps fades |
| Phone compact (Tasks filter, <600px): options size to text, so the indicator becomes each option's own fill (background cross-fade) | A single sliding indicator assumes equal halves; text-sized options would leave it misaligned |
| Policy Check wizard: crumbs lead with the chosen book ("Personal lines > Policy check > New check", "Commercial > Policy check > New check") and the title carries the book tab (no Move button) | The page names where the check will be filed, in the run header's words and tab |
| On a pointer flip the tab drops in and the book crumb fades in (the run header's re-file motion); keyboard instant | Same motion as a run moved between books: one vocabulary for "filed in" |
| Compare start screen: crumbs lead with the queue's book ("Commercial > Compare Quotes"); the crumb does not follow the Upload panel's choice | The page is that book's work queue; see "Where the direction changed" |

## What each skill said (before building)

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design |
|---|---|---|---|---|---|---|---|
| Joined track | Plain words, no em dashes in copy | UI boundary 3:1; 44px hit area; visible focus ring | Shape lock: 6px control radius kept; one accent | Hover/active/focus states required; work with the stack, no library | Operate mode: brand lives in precise details, scanability first | Unseen details compound: a gap that the indicator flies across breaks the illusion of one thing | Visual structure is information: one outline says one choice |
| Ease-out slide 220ms | n/a | Motion conveys spatial continuity; avoid instant state changes | Motion must be motivated: state transition | Animate transform/opacity only | Exponential ease-out from a visible default | Strong custom ease-out gives instant feedback; under 300ms; transitions (retarget mid-flight), not keyframes | Motion that answers a person's action shows what changed |
| Glyph and label cross-fade | n/a | Reduced motion respected | n/a | No instant 0ms swaps | States carry the design | Never from scale(0); exit faster than enter; reduced motion keeps opacity | n/a |
| Keyboard instant | n/a | Keyboard nav preserved (native radios, arrow keys) | n/a | Focus ring | Keyboard focus is part of the floor | Never animate keyboard-initiated actions | n/a |
| Book crumb + book tab on the wizard | Plain words | Navigation shows the current location | No duplicate intent: the tab has no button | "No indication of where you are" is a defect | Wayfinding is the floor; reuse the incumbent tab, no new language | One motion vocabulary (re-file) for one meaning | An action keeps its name: the crumb and tab say "Personal lines" like the run will |
| Compare keeps the queue's book | n/a | Labels must match content | Copy self-audit: a crumb that names the wrong queue is wrong copy | n/a | Product truth over the brief's letter (refinement preserves function) | n/a | Name things by what the person sees |

## Frame sampling (Playwright MCP, 1440, `requestAnimationFrame` on `getComputedStyle(seg, "::after").transform`)

Pointer click Commercial to Personal on `#/policy-check/new`, headless Chromium (about 7ms frames). Travel: 131px
before, 123px after (final geometry, 12px option padding).

| Time after change | Before (200ms `--ease-in-out`) | After (220ms `--ease-out`, final build) |
|---|---|---|
| first moving frame (~10 to 13ms) | 0.1px (0%) | 16.6px (13%) |
| ~18ms | 0.4px | 32.4px (26%) |
| ~40ms | 2.9px (2%) | 71.1px (58%) |
| ~60ms | 9.1px (7%) | 95.8px (78%) |
| ~80ms | 24px (18%) | 109.4px (89%) |
| ~100ms | 70px (53%) | 116.4px (95%) |
| within 1px of rest | ~186ms | ~135ms (220ms total) |
| check glyph | `visibility` swap at frame 0 | old 1.00 to 0.17 by 42ms; new 0.13 at 11ms, 0.58 at 42ms, 0.93 at 94ms |
| crumb + tab | no change | "Personal lines" crumb and tab opacity 0.15 at 11ms, 0.62 at 42ms, 1.0 at 150ms |

Keyboard (focus the radio, ArrowLeft/Right): first frame already at the target, glyph opacity 1, tab and crumb opacity 1,
focus stays on the radio. Reduced motion: indicator at the target on the first frame, glyph and tab fade (0.14 at 12ms,
0.85 at 67ms), no transform on the glyph.

## Self-review rounds (Playwright MCP; 3 of 3 used)

1. Built as above. 1440: pointer flip sampled (table), mid-transition frame captured (`after-1440-mid-transition.png`,
   all transitions paused at 45ms), keyboard flip instant with the focus ring on the joined track, reduced motion fades
   only, 44px hit area (34px option + 5px above and below). No findings.
2. 1024 and 390: tab beside the title at 1024, under it at 390 (the phone top bar shows only the last crumb, so the tab
   is the only place the book shows there); no sideways overflow. Tasks at 390: each option draws its own fill and it
   follows the pick (the old single indicator sat half over "Open" and "Done"). Activity filter (3 options) at 1440.
   Compare: picked Personal, crumbs stay "Commercial > Compare Quotes" while the queue lists Cedar Hollow, Copperfield,
   Halvorsen and Brightwater (commercial work). No fixes needed.
3. The full e2e run caught what round 2 missed: in the 300px Compare Upload panel the halves were 130px and 115px
   (plain `1fr` gives way to the longer label's min-content once 16px padding pushed it past half the column), so the
   indicator stopped 2px short of "Personal" and was wider than it. Fixed with `minmax(0, 1fr)` and the original 12px
   option padding (122 / 122px, no clipped text). Rechecked at 1440, 1024 and 390, re-sampled (table above), shots
   retaken; the Compare e2e now asserts equal halves.

## Where the direction changed (owner may veto)

- **Compare start screen**: the approved direction had its crumb follow the choice. Seen live, the page is the book's
  work queue (`after-1440-compare-personal-picked.png`: Personal picked, commercial clients listed), so a "Personal
  lines" crumb would name the wrong queue. The crumb leads with the queue's book instead; the choice files the next
  comparison only. If the owner wants the choice confirmed there too, the cheapest honest option is the start button
  naming it ("Compare 2 quotes in Personal lines").
- **Rail on a preset wizard**: opened from Personal lines (`?line=personal`) the rail keeps highlighting Personal lines
  after a flip to Commercial (unchanged behaviour; "rail names the tool" was not reopened). The crumb and tab say
  Commercial.
- The joined track applies to every segmented control (Activity "Show" and the Tasks filter too), since they share the
  CSS; their check glyph is now always drawn (it cross-fades, and options keep one width).

Screenshots: `qa-screenshots/line-switch/` (before-1440/1024/390, before-390-tasks-filter; after-1440 (Personal picked), after-1440-commercial,
after-1024, after-390, after-1440-mid-transition, after-1440-keyboard-focus, after-390-tasks-filter,
after-1440-tasks-filter, after-1440-activity-filter, after-1440-compare-personal-picked).
