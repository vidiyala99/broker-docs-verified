# T10 design brief: BrokerDocs frontend, slice 1

Written before building. Sources: `DESIGN.md` (tokens, silhouettes, motion, contrast),
`backend/API.md` (contract), spec "Design review" (DG1 to DG12, state table, journey),
`docs/reference/brokerdocs-ui-reference.md`, the sign-in screenshot and the demo frames in
`../rea-out/frames`.

**Design read:** Operate-mode desktop tool for insurance brokers checking quotes against
their source pages. The report table and its chips are the one visual anchor; every other
surface is quiet chrome on hairlines. The brief pins Inter, `#6356F0`, 6px/4px radii and the
reference shell (D2), so those are not open choices.

**Dials (taste-core):** variance 3, motion 2, density 7. Trust-first data tool, not a landing page.

## Layout

```
+----+---------------------------------------------------------------------------+
|rail| top bar: [glyph] BrokerDocs / Workflows > Compare Quotes > Run 6   [avatar] |
| 56 +-------------------+-------------------------------------------------------+
|    | left panel 300    | tabs: All runs | Report (spinner while running) | B.pdf x|
|    |  Upload (4)  v    | toolbar: title, version ... thumbs, copy, export, |o|  |
|    |   rows / drop zone+--------------------------------------+----------------+
|    |  Input parameters | notices (max 2 rows)                 | outline 240    |
|    |  Recent runs      | 1 Executive summary  [stale tag]     | trust counts   |
|    |  ---- chat ----   |   prose with chips                   | 1 Exec summary |
|    |  suggestions      | 2 Common declarations                | 2 Common decl. |
|    |  messages         |   table, sticky first column         | 3 ...          |
|    |  [ask a question] |                                      |                |
+----+-------------------+--------------------------------------+----------------+
side-by-side (chip click, >=1280): report column | PDF viewer column (Exit), outline hidden
```

Launch (no run): right pane shows Recent runs (DG1); when there are none it shows the
drop zone (DG6). Left panel Upload accordion holds the drop zone, per-file rows and Run.

## Decisions and what each skill said

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design |
|---|---|---|---|---|---|---|---|
| Shell: rail + left panel + right pane, no cards, hairlines only | Voice: plain words, no em dashes in copy | Nav must show current location: active rail tint, breadcrumb | Out of scope for its landing rules (Sec. 13: dashboards); keep only state, contrast and shape locks | "Dashboard always has a left sidebar" is flagged, but the reference shell is pinned (D2), so kept | Operate mode: earned familiarity, standard top bar + side nav + tabs are permitted | n/a | Spend boldness in one place: the table and chips; everything else quiet |
| Evidence states (DG3): silhouette per state | n/a | Never color alone (priority 10 rule); icon buttons need labels | Full state cycle required (loading, empty, error) | Disabled must look disabled | Every component has default, hover, focus, active, disabled, loading, error | n/a | Structure is information: dashed border and glyphs carry meaning, not decoration |
| Chips as 20px buttons with 44px hit area, hue + letter | n/a | 44x44 touch target, 4.5:1 text (doc fg on tint 5.1 to 6.4) | Shape lock: 4px chips, 6px controls, round only avatar/spinner | Tabular numerals on data | Theme browser surfaces (selection, focus ring, scrollbar) from the palette | No animation on keyboard open; hover tooltip 150ms | Hue is data, not accent |
| Run progress rows (DG4) with role=status, failures role=alert | n/a | Progress indicators for multi-step work; one atomic status message, not a live region per badge | Loading should match layout shape: skeleton lines in prose, page skeleton in viewer | No loading states is a top omission | Skeletons, not spinners in content; spinner only in tab and status rows | Spinner linear, faster spin feels faster | n/a |
| Cell edit (DG12) inline, 48px input, Undo toast 6s | n/a | Error next to the field with aria-describedby; toast for transient info | Inline errors, toasts only for transient | Do not use alert() | Modal is not the first thought: inline editing | Toast enter 250ms, exit 150ms (exit faster); press scale 0.97 | Action keeps its name: "Edited. Undo", "Saving...", "Couldn't save. Retry" |
| Scoped warnings (DG5): top rows max 2, column header row, section tag | n/a | Error placement near the cause | No decorative dots; glyph + text | n/a | No colored side-stripe borders on alerts; full hairline row with glyph | n/a | Errors name problem and recovery, never apologize |
| Outline with trust counts as filters (DG2), triangle mark on collapsed handle | n/a | Live badge counts need context in the label ("3 unverified, 1 failed") | No decorative status dots: the mark is the DG3 triangle glyph | n/a | Brand lives in precise details | Collapse is instant from keyboard | Counts are the structure, not labels above headings |
| PDF side-by-side (pdf.js) with dashed highlight, Esc exits | n/a | Keyboard nav for all controls | n/a | n/a | Overlays escape containers (tooltips use fixed positioning near edges) | Side panel 200ms ease-out on pointer open, instant on keyboard | Motion answers an action and shows what changed |
| Icons: Phosphor regular, one family | n/a | SVG icons, no emoji | Phosphor first; never hand-roll SVG icons | Phosphor over Lucide | Unicode glyphs are not an icon system | n/a | n/a |
| Type: Inter (self-hosted via @fontsource) 12/14/16/20 | n/a | Base 16 for prose, 14 for tables | Inter discouraged as default, but the brief pins it (override path) | Medium 500 and SemiBold 600 for hierarchy; tabular-nums | One family is right for product UI | n/a | Sentence case, no all-caps labels except observed status words |
| Motion: press, tooltip, toast, side panel, spinner only | n/a | Reduced motion honored | Reduced motion mandatory | No `transition: all` | One authored moment at most; motion conveys state | Custom curves from tokens, under 300ms | Non-user-triggered motion sparingly |
| Viewports (DG9): 1280+ full, 1024 to 1279 collapsed strip + PDF as tab, under 1024 notice | n/a | Table: horizontal scroll wrapper, sticky first column | Mobile collapse explicit per layout | No horizontal page scroll; only the table scrolls | Responsive behavior is structural | n/a | n/a |
| Copy | No AI vocabulary | Visible labels, helper text | Zero em dashes; no filler verbs | No "Oops", no exclamation marks | Controls name their action | n/a | Plain verbs, empty states invite action ("Drop quote PDFs to compare") |

## Deliberate choices against a skill

- taste-core and taste-redesign push away from Inter and violet; DESIGN.md and D2 pin both, so
  the override path applies.
- taste-redesign flags "left sidebar dashboard"; the reference shell is the fidelity target.
- impeccable asked for PRODUCT.md (none exists). Not written: out of scope for T10, flagged.
</content>
</invoke>
