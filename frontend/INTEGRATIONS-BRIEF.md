# T5 design brief: Integrations mock page, slice 3b

Written before building. Sources: spec `docs/specs/2026-11-02-rail-completion-design.md` (2.4, 2.5, owner decision R7,
task T5), `DESIGN.md`, `tokens.css`, `HANDOFF.md`, the prior briefs (`POLICY-CHECK-BRIEF.md`, `LEFT-PANEL-BRIEF.md`) and
the evidence shot `rea-out/shots/integrations.png` (the reference product's marketing site showing their Integrations screen; used for
layout only, never for logos or copy claims).

Skills loaded before the first line of UI code: gstack, ui-ux-pro-max, taste-core, taste-redesign, impeccable,
emil-design-eng, frontend-design. `impeccable context` reported `NO_PRODUCT_MD`; this is a scoped addition to existing
code, so it proceeds on DESIGN.md as the visual authority (PRODUCT.md still missing, flagged again).

**Design read (taste-core 0.B):** Operate mode. A broker opens the rail item to see which outside systems this tool
might talk to one day. The honest answer is "none yet", so the page's one job is to say that clearly while showing the
planned shape. DESIGN.md pins Inter, `#6356F0`, 6px controls and 4px chips, Phosphor regular, hairlines with no
shadows, motion tokens only. Those are not open.

**Dials (taste-core):** variance 2, motion 1, density 5. Lower variance and motion than the Policy Check list (3/2/7):
this is a static directory of ten items with no live state, and nothing on it should move except the tooltip.

**Where the boldness goes (frontend-design):** the notice. It is the only true statement on the page, so it is the one
emphasised element: full width under the title, the existing warning notice silhouette (stale tint, amber hairline,
info glyph), 16px text instead of the 14px chrome. Everything else stays quiet: hairline vendor tiles, greyed Connect.

## Layout

```
Rail [Integrations active]   Top bar: BrokerDocs / Integrations
+-------------------------------------------------------------------------------+
| Integrations                                              (36px page title)   |
| [i] Integrations are not designed yet. This page is a mock; nothing connects. |
|                                                                               |
| Agency management systems                                 (20px h2)           |
| +-----------------------+ +-----------------------+ +-----------------------+ |
| | [db] Applied Epic     | | [db] AMS360           | | [db] Nexsure          | |
| | Agency management     | | Agency management     | | Agency management     | |
| | system                | | system                | | system                | |
| |             [Connect] | |             [Connect] | |             [Connect] | |
| +-----------------------+ +-----------------------+ +-----------------------+ |
| | [db] HawkSoft  ...    | | [db] ImageRight ...   |                           |
|                                                                               |
| Documents and email                                                           |
| [env] Gmail  [env] Outlook  [folder] Google Drive  [folder] OneDrive  [folder] SharePoint |
+-------------------------------------------------------------------------------+
1440: 3 columns (as the evidence shot). 1024: 2 or 3 by width (auto-fill, min 260px). 390: 1 column.
```

- Route `#/integrations`, rail item "Integrations" live (only that item), crumbs `Integrations` (single crumb, like a
  top-level section), document title "Integrations". The phone-width "Best viewed on a wider screen" note is about
  report tables, so it is not shown on this page.
- Tiles: `<ul>` per group, `<li>` per vendor with an `<h3>` name, so a screen reader can jump by heading and hear the
  list size. Icon: one generic Phosphor glyph per category (Database for agency systems, matching the AMS export glyph
  in Policy Check; EnvelopeSimple for email; FolderSimple for file storage). No logos, no brand colours.
- Descriptions are the generic category only: "Agency management system" (Applied Epic, AMS360, Nexsure, HawkSoft),
  "Document management system" (ImageRight), "Email" (Gmail, Outlook), "File storage" (Google Drive, OneDrive,
  SharePoint). No claims about what an integration would do.
- Left out from the evidence shot on purpose: the type filter and search box (a control that does nothing on a ten-item
  static page is a fake affordance), and the per-card "ORG" / "Personal" badges (they are connection scope states;
  R7 bans fake connection states).

## Accessibility pattern for the disabled Connect

A native `disabled` button cannot take focus, so its tooltip is unreachable by keyboard. Instead:

- `<button type="button" aria-disabled="true">` with visible text "Connect", accessible name "Connect Applied Epic"
  (`aria-label`; starts with the visible label, so WCAG 2.5.3 label-in-name holds) and `aria-describedby` pointing at a
  visually hidden "Mock. Nothing connects." The button stays in the tab order; activation does nothing (the click
  handler returns, there is no form to submit).
- The existing `Tip` component shows "Mock" on hover and on keyboard focus (`:focus-visible`), and Escape hides it.
  The bubble is `aria-hidden`; the description carries the same words for screen readers (DG10 rule from Tip).
- Look: DESIGN.md disabled rule (opacity 0.5, `cursor: not-allowed`, no hover change, no press scale). The focus ring
  stays at full strength so keyboard users see where they are.
- The notice is a `<p role="note">` directly under the `<h1>` inside `<main>`, so it is read right after the title and
  is reachable by browsing. Not a live region: it is present on load and never changes, so announcing it would only
  interrupt.

## Decisions and what each skill said

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design |
|---|---|---|---|---|---|---|---|
| Notice as the one emphasised element: warning notice silhouette, 16px, under the title, `role="note"`, no live region | Router only; voice: plain words, no em dashes (the spec copy uses a semicolon, kept verbatim) | Contextual status: announce meaningful change without competing live regions; a static notice is not a change, so no `aria-live` | One focused message per section; copy self-audit: the sentence is plain and true | Strategic omission check: the page must not imply anything works | Operate: the usage scene is "can I connect my AMS?"; the answer must be the first thing read | n/a (static) | Spend boldness in one place; words make the page easier to understand, not decoration |
| Vendor tiles in an auto-fill grid (3 / 2 / 1 columns), hairline border, Connect pinned bottom-right | n/a | No horizontal scroll at 390; 44px targets (Connect uses the 32px `btn--sm` with the existing 44px `::before` hit area) | Grid over flex math; explicit mobile collapse; "three equal cards" is a landing-page tell, but the evidence shot pins a tile grid for this screen and the brief asks for layout fidelity, so the brief wins | Pin buttons to the card bottom so CTAs line up across a row | Brief wins over saturated-pattern warnings; same-size cards are the lazy default unless the content is a set of peers, which ten vendors are | n/a | Structure is information: groups are h2 + list, no numbering, no eyebrows |
| Generic icon per category, no logos, no brand colours, no badges | Voice rule only | Icons from one SVG library, no emoji; decorative icons `aria-hidden` | Phosphor is the allowed default; one family; never hand-rolled marks | Consistent stroke weight (Phosphor regular, 20px) | Refuse unicode or emoji as icons | n/a | Visual structure encodes information: the glyph says the category, which the description repeats in words |
| Connect: `aria-disabled` + description + Tip "Mock", focusable, inert | n/a | Disabled states: reduced opacity and not-allowed cursor; never confuse disabled with enabled; accessible names on every button | Full state cycle: disabled must look disabled and still explain itself | Dead buttons must look disabled; focus ring required | States: disabled and keyboard focus are part of the floor | Tooltip: delay first, instant after (Tip already does this); no press scale on an inert button, since press feedback would claim the press did something | Controls name their action; "Connect Applied Epic" is the action that is not available |
| Motion: none added. Tooltip keeps its 150ms fade; Connect has no press scale, no hover tint | n/a | Respect reduced motion | Motion must be motivated; nothing here changes state | Transitions on interactive elements only | One authored moment or none; here none | "Should this animate at all?" No: nothing happens | Motion answers an action; there is no action |
| Leave out the evidence shot's search, type filter and scope badges | Voice rule only | n/a | No fake affordances; no decorative status labels | Dead controls are a defect | Coverage: every brief requirement present; nothing beyond it | n/a | Remove one accessory |

## Deliberate choices against a skill

- taste-core and taste-redesign push away from Inter and violet; DESIGN.md pins both (override path, as in T10/T7/T9).
- taste-core's scope note puts product UI outside it; only its state, shape, copy and fake-affordance locks were applied.
- taste-core and impeccable both flag a grid of same-size cards; the evidence shot and the spec's "Card" wording pin it,
  and the tiles are true peers (one vendor each), so the grid stays, kept quiet (hairlines, no shadow, no hover lift).
- impeccable reports `NO_PRODUCT_MD`; not written (out of scope), flagged again.

## Self-review rounds (3 of 3, Playwright MCP, screenshots in `qa-screenshots/integrations/`)

1. `r1-*`: built as briefed; hover tooltip, Tab to the first Connect, Escape, click (Playwright itself refuses the click
   as "not enabled", so `aria-disabled` is honoured) at 1440, 1024, 390. Fixed: the 0.5 opacity also dimmed the focus
   ring, so the ring now draws on the tooltip anchor at full strength; at 390 each tile was about 160px tall (ten tiles,
   five screens), so phone tiles became one row (glyph, name, Connect on the right, category under the name); the
   notice glyph centred on three wrapped lines, now it sits on the first line.
2. `r2-*`: with Connect in the right column the category wrapped to three lines at 390, so it now runs under the
   button's column too (one line, ImageRight two). The top-aligned notice text sat 2px high on one line; padding now
   centres one line in the 44px notice and longer text grows down.
3. `r3-*`: final pass at 1440 (opened from the rail), 1024, 390 scrolled to the second group (the hover tip at 1024
   was captured before its 350ms first-show delay; hover and focus tips are covered by `r1-*` and the e2e spec).
   No new findings. e2e 16/16 on a fresh seed, axe 0 on every checked state including this page at 1440/1024/390.

Known, not fixed (existing `Tip` behaviour app-wide): a tooltip left open while the window is resized keeps its old
position until it is hidden. Touch: there is no hover, so on phones the page notice is the explanation; tapping Connect
does nothing.

## Owner calls taken by default

- Category descriptions as above; ImageRight is described as "Document management system" (its generic category)
  while staying in the Agency management systems group the spec puts it in.
- No search, type filter or scope badges from the evidence shot (fake affordances / fake states under R7).
- The page renders without the backend (it needs nothing from it); every other route still shows "Can't reach the
  local server" when the backend is down.
- Ten Connect buttons stay in the tab order so the Mock explanation is reachable; the rail's "coming later" items were
  taken out of the tab order in the design review because they had no content behind them, which is a different case.
