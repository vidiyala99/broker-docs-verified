# CERTIFICATES-BRIEF.md: slice 4 UI (C11), phase A

Status: phase A (audit + directions), 2026-11-03. **Owner picks a direction before phase B builds anything.**
Spec: `docs/specs/2026-11-03-certificates-design.md` (ledger D1 to D18 overrides the text above it).
Backend contract: `backend/API.md` "Certificates (slice 4)". Branch `slice4-ui`.

## 1. Demo data (done)

`backend/dev_seed.py` now seeds two certificate runs for **Calloway Build Group LLC** from
`tests/fixtures/certificates/` (case `cert_contractor`, certificate date 2026-11-03), read with the cache off (D9:
`scenario_environment()` already runs first):

- **Run 10, issued**: GL declarations only, no wording, holder *Larkspur Commons Owners Association*. Gives the client
  a past holder (C2) and the Certificates page a Done recently row.
- **Run 11, draft**: GL, auto and WC declarations plus three endorsements; all three wordings requested; a description.
  12 claims: 7 Supported (three in force, insured, AI and P&NC on GL, waiver on WC), 4 open (AI, waiver, P&NC on auto,
  plus the description sign-off), 1 Overridden (waiver on GL, with a reason). Issue is blocked by `claims_open`.

The scripted model answers the certificate pass from each fixture's `truth.json` (`dev_seed.cert_extract`, the same
reply shape as the eval's perfect fake). Test: `test_dev_seed.py::test_seeded_certificates_show_every_claim_state_and_a_past_holder`
(verdicts, override note, counts, holder list, queue groups, no reused reads). Backend suite: 1573 passed, 2 skipped.

## 2. Audit: what the app already gives us

| Need | Reuse | Notes |
|---|---|---|
| Certificates page queue | `queue/QueueView.tsx` row pattern, `GroupMark`, `ui/motion.tsx` list enter/exit, the seal | `GET /api/certificates/queue` has the same group shape (needs attention, ready, done recently). Row action: "Resolve 4 checks" / "Issue" / "Open". |
| Wizard (Who, Policies, Read) | `policy/PolicyWizard.tsx` + `wizard/Stepper.tsx`, `wizard/StepDetails.tsx` fields, proposal picker (sha256 dedupe) | New: holder picks (`GET /holders?client=`), wording checkboxes, the "already read on <date>" marker (`already_read`, `read_at`). Free-text bounds from D6 as inline field errors (422 `fields[].loc`). |
| Reading progress | `RunProgress`, `ReadingMark`, document rows of the left panel | Add document (D2) puts the run back to `queued`: the same reading state, one new row. |
| Claim decisions | `policy/Discrepancies.tsx` `useResolve`, `ResolveControls` (note editor), `ItemHead` (stale tag, "changed since resolved") | Certificate rules differ: Override needs a note (422 `note_required`), Remove exists (except `insured`), every PATCH sends `expected_fingerprint` (D15, 409 `item_changed` returns the fresh claim: replace the row, keep the typed note). |
| Citations | `report/Chip.tsx`, `pdf/PdfViewer.tsx`, `source-pane` in `RunView` | Works as is on `view.chips` (checked live: B1 opens the auto declarations with the dashed passage box). |
| Status words, severity meter | `status-word`, the four-tick meter | Certificates need claim states, not severity. |
| Toasts, Undo | `ui/Toast.tsx` | Remove and Override are reversible (status `open` again), so the toast can offer Undo. |
| Types | none | `api/types.ts` has no `CertificateBlock` / `ClaimBlock`; the prototype carries local types (section "the API shape" in `Prototypes.tsx`) to lift into `types.ts` in phase B. |

**Genuinely new:** the certificate drawn as paper (own layout, C6), the link between a claim and the line of the
certificate it vouches for, claim states (Supported, Not supported, Needs sign-off, Overridden, Confirmed, Removed), the
Issue gate with its count and its 409 reasons, and the issued (read-only) view.

Claim state glyphs used by all three directions (silhouette first, colour second, word always present):
Supported = ticked circle (green `--doc-3-fg`), Not supported = crossed circle (`--severity-critical-fg`), Needs sign-off
= signature, bold (`--severity-high-fg`), Confirmed = signature, regular, Overridden = the broker's stamp (primary),
Removed = eraser (muted). Phosphor glyphs, no new tokens; every pair is from the DESIGN.md contrast table.

## 3. Directions for the draft view

All three render the seeded run 11 behind a dev-only route, `#/dev/certificates/<a|b|c>/<runId>` (App mounts it only
when `import.meta.env.DEV`; `frontend/src/certificates/phaseA/`, deleted in phase B). Decisions are local to the page
(Override with a reason, Confirm, Remove, Put back, Undo all work; Add document and Issue are stubs that say what
they would do). Chips open the real PDF. The same certificate paper is shared, so the directions differ in how the
paper, the checks and the gate relate, not in the drawing.

### A. Proof and ledger (side by side)

The certificate as an intact sheet on the canvas, the **check ledger** as a fixed right column (400px) ordered by
need: *To resolve* (4), *Handled by you* (overrides, confirmations, removals, each with its reason and Undo),
*Supported by the documents* (folded, count shown). Each claimed line on the paper carries the claim's glyph in the
left margin, so the paper reads as checked at a glance; hovering or focusing a check points at its line with the
viewer's dashed passage box and scrolls it into view. A chip swaps the paper for the source PDF in the same column
("Back to certificate", Esc), so evidence and the check you are deciding sit side by side. Issue is the one primary
button, top right, with "4 checks to resolve" under it. Phone: the ledger comes first, the paper below.
Screens: `qa-screenshots/certificates/phaseA/a-1440.png`, `a-1440-override.png` (pointing + reason editor),
`a-1440-source.png` (chip opened beside the ledger), `a-1024.png`, `a-390.png`.

Carries into: **Certificates page** rows read "Calloway Build Group LLC, for Marlowe Property Group LLC, 4 checks to
resolve" with the same claim glyphs as the status; **wizard** step 3 shows the ledger filling as documents are read;
**issued view** = the same two columns, paper stamped "Issued 11/03/2026" with the seal, the ledger becomes the record
(reasons kept, actions gone), Download PDF and New version in the header.

### B. Marked proof (notes in the paper's margin)

One wide sheet with a margin column, like a proof marked up by a checker: every row of the certificate that carries a
claim (insured, each policy, description) has its notes beside it; Supported claims are one-line ticks with their chip,
open ones are full notes with their actions. The gate lives in a sticky bar at the foot: "4 marks on the proof need
you", Next mark (moves focus to the next open note), Issue. Phone: notes fall under their row.
Screens: `b-1440.png`, `b-1440-next-mark.png`, `b-1024.png`, `b-390.png`.

Carries into: **Certificates page** shows a small proof thumbnail per row (heavier than the queue pattern);
**wizard** read step lands straight on the proof; **issued view** = the clean sheet, margin collapsed to a "Record"
toggle showing the reasons.

Weak points seen in the prototype: open notes are tall, so the paper stretches apart (the auto policy row is 3x its
own height in `b-1440-next-mark.png`) and stops reading as one document; the work is in paper order, not need order
(the first open item is below the fold at 1024); the margin halves the sheet at 1024.

### C. Checks first (the certificate as a preview)

The check list is the main column, as a work queue of claims: one row per claim, need first, the selected row opens
in place with its evidence and actions. The right column is a live preview of the certificate that scrolls to and boxes
the line the selected claim is about ("Where this sits on the certificate"). A tick strip shows "8 of 12 handled".
Phone: the list only, the preview below.
Screens: `c-1440.png`, `c-1440-after-remove.png` (Remove takes the wording off the preview; count drops to 3),
`c-1024.png`, `c-390.png`.

Carries into: **Certificates page** and draft share one list idiom (easy to learn); **wizard** read step fills the
list; **issued view** has to switch the weight back to the paper, which this direction treats as secondary.

Weak points: the certificate, the thing the broker signs and sends, becomes a side preview; at 1024 the preview wraps
labels and numbers; resolved rows re-sort (the row you just removed jumps down), which needs motion work to stay legible.

## 4. Recommendation: A, Proof and ledger

The broker's job on this screen is to put their name to a document. First principles: they must see the certificate
whole, as it will print, and know for every line whether the documents back it. A is the only direction where both
stay true at 1440 and 1024: the paper is never broken up (B) or demoted (C), and the ledger keeps the work in need
order with a count that matches the Issue gate. It also reuses the most: the Policy Check list idiom (resolve with a
note), the source pane for chips, the queue's glyph-plus-word status. Two things to take from the others: C's
"selected claim boxes its line" is already A's pointing behaviour; B's per-line ticks are A's margin glyphs.

For phase B, A would add: `CertificateBlock`/`ClaimBlock` in `api/types.ts` (with rendering in the Block switch);
real PATCH with `expected_fingerprint` and 409 `item_changed` (refresh the row, keep the typed reason, say "This check
changed while you were deciding. Its new evidence is below."); 409 on Issue (`version_changed`: reload the draft and
name what changed; `run_active`: "Still reading 1 document"; `claims_open`, `no_coverage`); the stale tag on reopened
rows (`changed_since_resolved`); edited-value sign-off rows (D12, not in the seed: no cell edits yet); Undo toasts; the
issued view; the Certificates page and wizard; e2e and axe at 1440, 1024, 390.

## 5. Owner calls

1. **Direction**: A (recommended), B or C.
2. **Left panel on certificate runs.** Other runs keep the 300px panel (Upload, parameters, Recent work, Chat). A
   certificate makes no model call after extraction (C5), so Chat has nothing to answer from the checks. Recommend: the
   panel keeps Policies (documents, Add document, Retry) and Input parameters (holder, wording, date), no Chat, folded
   by default on the draft so paper + ledger get the width (the prototypes were shot without it).
3. **Supported claims folded by default** in the ledger ("Supported by the documents 7"). Recommend yes: the margin
   ticks on the paper already show them.
4. **Removed lines on the draft**: they disappear from the paper (D13) and stay in *Handled by you* with "Put back on
   the certificate". Recommend no ghost line on the paper, so the draft is exactly what will print.
5. **Paper copy**: the legal header and cancellation text in the prototype are my plain wording for the own layout
   (C6); they need the owner's (or a licensed) wording before the PDF ships.

## 6. Per-skill notes (what each said, where it landed)

| Decision | gstack | ui-ux-pro-max | taste-core | taste-redesign | impeccable | emil-design-eng | frontend-design |
|---|---|---|---|---|---|---|---|
| Paper drawn as a sheet, serif title, Inter data | Voice: plain words, no em dashes (copy self-checked) | Typography: base 14 to 16, tabular numbers for limits | Serif only with a reason: here the certificate *is* paper (Paper trail rule pins Source Serif 4 to titles and names) | Tabular figures for data, one grey family | Operate mode: familiarity wins, brand in precise details; the brief (Paper trail) wins over taste | n/a | Spend boldness in one place: the sheet; everything around it quiet |
| Claim states: glyph + word + colour | | Priority 1: never colour alone; 4.5:1 text | No decorative dots; glyphs only for real state | Disabled must look disabled (Issue at 0.5 with a reason) | Every state named and recoverable (Undo, Put back) | | Status words carry information, not decoration |
| Need-ordered ledger, Supported folded | | Progressive disclosure | Long lists need grouping, not one divided list | Strategic omission: empty/all-clear state ("Every check handled") | Cognitive load: one next action per item | | Visual structure is information: group headers count real things |
| Override/Confirm as an inline reason editor, not a modal | | Forms: label above, hint under, error near the field | Label above input, no placeholder-as-label | Modals for everything is a tell | "Modal as first thought is laziness" (operate.md) | Inline expansion animates height+opacity on pointer only | Name the action: "Override", the record keeps "Reason" |
| Pointing a check at its line (dashed box) | | Motion conveys meaning, spatial continuity | Motion motivated: it explains which line a claim is about | | Reuses an existing silhouette (the viewer's passage box), no new visual language | Hover/focus 150ms, keyboard instant (`data-input="keyboard"`), no animation on frequent actions | One reused device, not a new one |
| Issue gate with a count | | Disabled state explains why | One loud button per region | | The gate is the screen's single primary action | Press scale 0.97 already in `.btn` | CTA says exactly what happens: "Issue certificate" |
| Phone: ledger first | | Mobile: no horizontal scroll, 44px targets | Explicit collapse per layout | | Phone is for status, not review work (PRODUCT.md) | | |

Impeccable's interview step: this lane runs as a subagent, so the owner's direction pick in this report is the
decision probe; nothing was built past prototypes. gstack is a router only; no sub-skill was needed for phase A.

## 7. Verification

- Live, Playwright MCP (Chrome): every direction driven at 1440, 1024 and 390 against the seeded run 11; Override
  editor opened, a chip opened the PDF beside the ledger, Remove took the wording off the paper and dropped the count,
  Next mark moved focus to the first open note. `tsc --noEmit` clean.
- Not run in phase A: axe and e2e (prototype route, deleted in phase B).
