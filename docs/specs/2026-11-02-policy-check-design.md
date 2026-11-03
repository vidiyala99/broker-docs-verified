# BrokerDocs, Slice 3: Policy Check

Date: 2026-11-02. Status: draft for owner review. Builds on
`2026-11-01-brokerdocs-core-design.md` (slice 1), `2026-11-02-proposal-builder-design.md`
(slice 2) and the evidence in `docs/reference/brokerdocs-workflows-evidence.md` section 2.

## 1. Intent

Outcome: a broker uploads an expiring policy, a renewal, optionally a quote and an AMS export,
and gets a list of discrepancies. Every value shown in a comparison slot is a verified quote
with a citation, and every severity comes from a fixed rule table.

Audience: the owner, as a showcase of the verified-citation engine on a second real workflow.
Single-user local app. Not a pixel match of the original.

Aim chosen by the owner: citation-engine showcase (not visual fidelity, not workflow breadth).

Success criteria:
- Every cited value in every slot passes verification; N/A is shown when a role has no value, never a guess.
- Diff and severity are deterministic code. A model writes only title and explanation prose.
- Live eval gate: 0 hard fails (section 7).

Decisions already made (do not re-ask):
- Four roles: `expiring`, `quote`, `renewal`, `ams`. `expiring` and `renewal` required.
- AMS is an uploaded JSON or CSV file, cited by field name.
- Severity is a rule table in code, tiers Critical, High, Medium, Low.
- Approach A: a seeded `policy-check` workflow through the existing run pipeline, per-role facts, code-first diff.
  Not a standalone subsystem, not derived from Compare Quotes.

Out of scope (not observable in the evidence): Summary tab, Document Verification tab, Email Carrier,
Update AMS write-back, "Accept AI recommendations".

## 2. Roles and inputs

`POST /api/runs` with `workflow: "policy-check"` and `parameters.roles`, a map `document_id -> role`.
- `expiring` and `renewal` required; `quote` and `ams` optional; at most one document per role.
- Otherwise `400 invalid_run`. A missing optional role renders as N/A in every slot.
- AMS file (JSON flat key-value, or CSV with a header row) is parsed to pseudo-pages: one passage per
  field, text `field_name: value`. Existing size caps apply. Verify runs unchanged against these
  passages; a citation chip reads "AMS · field_name" instead of a page number.
- The AMS file is untrusted input: field names and values are capped in length and count, and are
  treated like document text (injection screening applies).

## 3. Facts and diff

Every document goes through the same `policy-check` fact schema: policy number and term, named insured
and address, coverage limits (A, B, C, D, ...), deductibles, premium, discounts, forms and endorsements
(form number, edition date), special provisions. Each fact carries a verified quote and page.
Merge runs inside a document only (windows logic unchanged). No merge across roles.

`pipeline/diff.py` (pure code):
1. Align facts across roles by canonical key (`coverage_a_limit`, `deductible_wind_hail`, ...). Forms align by
   form number, so an edition change is a change, not an add plus a remove.
2. Normalize values (money, percent, date, text). Raise a discrepancy when two present values differ, or a
   value is present in one role and absent in another.
3. Each discrepancy carries its slots (value or N/A, plus citations to verified quotes).
4. Severity table, keyed by fact type and change size. Starting values, to be tuned in review:
   - Critical: coverage limit down more than 10%, deductible increase.
   - High: coverage limit change under 10%, discount dropped, coverage removed.
   - Medium: form edition change, special-provision text change.
   - Low: formatting-only or non-material differences, or suppressed when equal after normalization.
5. Output ordered by severity, then key. Same input gives byte-identical output.

Compose writes title and explanation for each item from its slots only. The existing validator rejects any
number or passage id not in the item's facts. No-AI-prose mode uses template text and needs no model call.

## 4. Storage

- Run artifact: discrepancy list as JSON, items `{id, key, title, severity, lob, prose, slots, citations}`.
  Item id is derived from the key so it is stable across regeneration.
- New table `discrepancy_state(run_id, item_id, status, note, updated_at)`, status `open | resolved | accepted`.
  Separate from the artifact so a regenerate never wipes decisions. If an item's slot values change after a
  regenerate, it resets to `open` and is flagged "changed since resolved".
- No migration framework: the table is created at startup; delete old local DBs.

## 5. API (to be added to `backend/API.md`)

- `POST /api/runs` as in section 2.
- `GET /api/runs/{id}/discrepancies` with `?severity=` and `?hide_resolved=`; returns items and counts per severity.
- `PATCH /api/runs/{id}/discrepancies/{item_id}` sets status and note.
- `POST /api/runs/{id}/discrepancies/bulk` sets a status for a severity filter ("Apply to filtered", "Clear all").

## 6. UI scope (built through the design stack, not decided here)

- Wizard step to assign roles to uploaded documents, including the AMS file.
- Discrepancy list: severity chips with counts, Hide Resolved toggle, cards with the four-slot comparison row
  and citation chips, resolve control.
- One-at-a-time review modal (Previous/Next, "Discrepancy 3 of 14") opening the cited source side by side:
  PDF at the page, AMS citation highlighting the field.
- Design gate applies: DESIGN.md and tokens.css are the contract; max 3 review rounds.

## 7. Eval and tests

Fixtures: an expiring/quote/renewal policy set built with the existing fixture generator, plus an AMS JSON,
with a seeded discrepancy ground truth.

Hard fails (any one fails the gate):
- a cited value that fails verification;
- a discrepancy with a missing or wrong-page citation;
- a severity that violates the table;
- an injection canary verified.

Soft metrics: precision and recall of discrepancies against ground truth; accuracy of the four slots.
Offline tests: diff engine, severity table, AMS parser (including malformed and oversized input), state table,
role validation, regenerate keeps resolve state.

## 8. Risks and open questions

- Canonical key alignment is the hardest part: the same coverage can be worded differently across carriers and
  roles. Mitigation: extraction emits the canonical key from a closed list; unknown keys go to a "Other" bucket
  with Low severity and are never dropped silently.
- Severity thresholds are starting values with no external evidence; tune against the fixtures.
- The citation shape for non-PDF sources was never observed in the original; ours is defined here (field name).
- Fixture realism: synthetic policies may under-test real-world layout variation. Reuse the multi-column and
  scanned fixtures where possible.

---

# Engineering review (plan-eng-review, 2026-11-02)

Target: this spec (unchanged above this line). Mode: scope accepted as-is except the two cuts below.

## Scope record
feature answers: D1 cut bulk endpoint, D2 status is open|resolved only; structure: D3 B smaller arrangement
(severity table inside `pipeline/diff.py`, `ams.py` separate, `discrepancy_state` in `models.py`, endpoints join `routes/runs.py`);
accepted scope: spec sections 1 to 8 minus bulk endpoint and `accepted` status; pending remedies: R1 to R5.

## Scope Challenge findings
1. [P1] (9/10) uploads.py:60 non-PDF rejected; AMS JSON/CSV cannot be uploaded as spec section 2 says.
2. [P1] (8/10) schemas.py:84 flat single-valued FactField vs multi-valued forms and provisions (spec section 3, 8).
3. [P1] (9/10) schemas.py:470 ArtifactContent block union vs spec section 4 "artifact as JSON".
4. [P2] (8/10) models.py:193 RunDocument.slot and Workflow.input_slots already model roles; spec invents parameters.roles.
5. [P3] (7/10) spec 1 vs 5 unobserved behavior (resolved by D1, D2).
6. [P2] (7/10) TODOS.md cross-window reference gap is "needed before Policy Check".
7. Budget: 4 x 400k = 1.6M > 1.5M run budget [measured: HANDOFF.md T14]; spec should state the 3-PDF worst case 1.2M.

## Decision ledger

### R1: How the AMS file becomes citable source text
Finding: 1, P1, confidence 9/10, uploads.py:60, reviewer: Claude
Plan baseline: spec section 2: AMS parsed to pseudo-pages, one passage per field, cited by field name; none approved yet.
Runtime evidence: Page rows carry `words`, `width`, `height` and Passage carries `rects` (models.py:127-141, 258-262); verify and the PDF viewer read stored PDF bytes (`load_stored_bytes`, runner.py:268). Non-PDF unverified.
Comparison grid:
| Choice | Current | A render to PDF | B pseudo-pages |
|---|---|---|---|
| R1 ingestion | unspecified, pending | AMS file rendered to a PDF at upload, then the normal parse/verify/viewer path | Page rows built directly, new viewer path for text |
| R2 AMS fact derivation | pending | pending | pending |
| R3 multi-valued facts | pending | pending | pending |
| R4 artifact shape | pending | pending | pending |
| R5 roles via slots | pending | pending | pending |
Question D4:
D4 - How should the AMS file become citable source text? (Policy Check spec section 2, finding 1)
Recommendation: A because rendering the AMS export to a small PDF at upload reuses parse, verify and the viewer unchanged.
Header: AMS ingestion
Options:
A) Render to PDF (recommended)
Render the uploaded JSON/CSV to a simple table PDF at upload (PyMuPDF, already a dependency), keep the original file, then run the normal parse, verify, passage and viewer path. Citations highlight the field line.
B) Pseudo-pages
Build Page rows directly from fields (one passage per field, no rects) and add a text viewer for non-PDF sources, as in spec section 2.
State: approved
Actual answer: D4 A) Render to PDF (recommended)
Accepted scope: AMS JSON/CSV is rendered to a table PDF at upload (PyMuPDF), the original file is kept, and the PDF takes the normal parse, verify, passage and viewer path. Spec section 2 pseudo-pages and field-name chips are replaced by this. Other choices stay pending.
History: none

### R2: How AMS facts are derived
Finding: 1 (follow-on), P2, confidence 7/10, spec section 3 "same schema for every document", reviewer: Claude
Plan baseline: spec section 3: every document, including AMS, goes through the same model extraction; D4 A approved only the rendering to PDF.
Runtime evidence: extract.py calls a provider per document (ModelCall purpose "extract"); an AMS export is already structured key-value data. Token cost of model extraction on AMS: unknown, small (one short PDF).
Comparison grid:
| Choice | Current | A deterministic map | B model extraction |
|---|---|---|---|
| R1 ingestion | approved D4 A render to PDF | same | same |
| R2 AMS facts | model extraction (spec), pending | code maps AMS fields to canonical keys via a key table, then verify against the rendered PDF; 0 model tokens | same model extraction as the other roles |
| R3 multi-valued facts | pending | pending | pending |
| R4 artifact shape | pending | pending | pending |
| R5 roles via slots | pending | pending | pending |
Question D5:
D5 - Should AMS facts come from code or from the model? (Policy Check spec section 3)
Recommendation: A because AMS data is already structured, and code is exact and free.
Header: AMS facts
Options:
A) Code maps fields (recommended)
Map AMS fields to canonical fact keys with a key table in code, then verify each value against the rendered PDF like any other fact. No model call for AMS. Unmapped fields are ignored and listed in a notice.
B) Model extraction
Run the same extraction as the PDF roles over the rendered AMS PDF.
State: approved
Actual answer: D5 A) Code maps fields (recommended)
Accepted scope: AMS facts come from a code key table that maps AMS fields to canonical fact keys, each value verified against the rendered AMS PDF; no model call for the AMS role; unmapped fields are ignored and named in a notice. Spec section 3 "same schema through model extraction for every document" is narrowed to the PDF roles. Other choices stay pending.
History: none

### R3: How forms and special provisions are extracted (REOPENED by outside voice)
Finding: 2 and outside-voice findings 3 and 4, P1, confidence 9/10, config.py:80-93 and merge.py:587, reviewers: Claude, Codex
Plan baseline: D6 A approved a second extraction call per PDF (item pass); that answer is superseded below.
Runtime evidence: DOCUMENT_TOKEN_BUDGET is 500_000 and one pass over a 300-page document needs about 449k (config.py:86-93), so a second full pass cannot fit; merge_document emits only the supplied field list (merge.py:587), so dynamic keys need plumbing; special provisions have no stable identity across documents.
Comparison grid:
| Choice | Current | A combined pass | B scalar only |
|---|---|---|---|
| R1 ingestion | approved D4 A | same | same |
| R2 AMS facts | approved D5 A | same | same |
| R3 forms | D6 A second call (superseded), reopened | one pass per window returns scalars and a forms array (number, edition, title, page, quote); each form becomes a fact keyed form:<normalized number>; merge_document gets the dynamic form keys; no second read of the document; special provisions are not extracted as separate items | forms and provisions dropped from slice 3 |
| R4 shape | approved D7 A | same | same |
Question D13:
D13 - How should forms be extracted without a second read of the document? (reopens D6; config.py:93)
Recommendation: A because it adds output tokens only (no second input read) and keeps form changes, the main discrepancy type.
Header: Forms extraction
Options:
A) One combined pass (recommended)
Extend each window's extraction to return the scalar fields plus a forms array (number, edition, title, page, quote). Each form becomes a fact keyed form:<normalized number>, and merge_document is given the dynamic keys. No second read. Special provisions are not extracted as separate items.
B) Scalar fields only
Compare scalar fields only; drop forms and special provisions from slice 3.
State: approved
Actual answer: D13 A) One combined pass (recommended)
Accepted scope: each window's extraction returns the scalar fields plus a forms array (number, edition, title, page, quote); each form becomes a fact keyed form:<normalized number>; merge_document is given the dynamic keys; no second read of the document; special provisions are not extracted as separate items; the extended schema gets live eval coverage and a budget test on long_300 that stays inside the 500k document ceiling. Spec section 3 "special provisions" and the provision:<slug> key are dropped.
History: D6 A "Item pass" (second extraction call per PDF) approved earlier this session; reopened because DOCUMENT_TOKEN_BUDGET is 500k and one pass already needs about 449k for a 300-page document.

### R10: Who writes the title and explanation of each discrepancy
Finding: outside-voice finding 5, P1, confidence 9/10, compose.py:682-690, reviewers: Codex, Claude
Plan baseline: spec section 3: compose writes title and explanation from the item's slots, and "the existing validator rejects any number or passage id not in the item's facts"; none approved yet.
Runtime evidence: validate_draft scopes allowed passages and numbers to the whole run unless the run is a proposal (compose.py:682-690, `key = sid if inp.is_proposal else None`); the number check cannot catch reversed meaning ("increased" for a decrease) or numbers assigned to the wrong role (TODOS.md "Claim-level validation of report prose").
Comparison grid:
| Choice | Current | A deterministic text | B model prose |
|---|---|---|---|
| R1 to R3 | approved D4 A, D5 A, D13 A | same | same |
| R4 shape | approved D7 A | same | same |
| R10 prose | model prose with an item-scoped validator (does not exist), pending | title and explanation built in code from the slots and a template per fact type and direction; no model call in Policy Check | model prose plus a new per-item validator and a direction/role check |
Question D14:
D14 - Should discrepancy titles and explanations be written by code or by the model? (compose.py:682)
Recommendation: A because the item-scoped validator does not exist, the number check cannot catch a reversed direction, and the showcase is code-first.
Header: Discrepancy text
Options:
A) Deterministic text (recommended)
Build each title and explanation in code from the slots, the change direction and a per-fact-type template. No model call in Policy Check, so no prose risk and byte-identical reruns. A model-written summary can be added later.
B) Model prose with new validation
Keep model-written text and build a per-item validator plus a check that direction words and role assignments match the slots.
State: approved
Actual answer: D14 A) Deterministic text (recommended)
Accepted scope: each discrepancy title and explanation is built in code from the slots, the change direction and a per-fact-type template; no model call in Policy Check; byte-identical reruns; the no-AI-prose special case and any item-scoped prose validator are not needed; a model-written summary is a later option. Spec section 3 compose paragraph is replaced by this.
History: none

### R11: What a not-found or conflicting fact does to a comparison
Finding: outside-voice finding 2, P1, confidence 8/10, merge.py:581-585, reviewers: Codex, Claude
Plan baseline: D10 A says absent in AMS or quote never counts and absent in renewal while present in expiring counts as removed; D11 A handles failed documents only; none approved for unverified or conflicting facts.
Runtime evidence: merge returns facts whose state can be NOT_FOUND or CONFLICTING with value None (merge.py:581-585, MergedFact state CONFLICTING, resolution "ambiguous"); Cell already carries `state` and `conflicts` (schemas.py:Cell).
Comparison grid:
| Choice | Current | A needs-review state | B treat as absent |
|---|---|---|---|
| R1 to R10 | approved D4 A, D5 A, D13 A, D7 A, D8 A, D9 A, D10 A, D11 A, D12 A, D14 A | same | same |
| R11 uncertain facts | unspecified, pending | a key whose renewal or expiring fact is conflicting or failed verification becomes a "Needs review" item (no severity beyond the table minimum, never "removed", never "equal"), shown with the cell's conflict list | conflicting and unverified facts count as absent, so "removed" can be claimed from them |
Question D15:
D15 - What should a conflicting or unverified value do to a comparison? (merge.py:581)
Recommendation: A because claiming a coverage was removed from a value we could not read is exactly the unsupported conclusion this showcase must not make.
Header: Uncertain values
Options:
A) Needs-review state (recommended)
If the renewal or expiring value for a key is conflicting or did not verify, show a Needs review item with the cell's conflict list. Never report it as removed or as equal.
B) Treat as absent
Conflicting and unverified values count as absent, so a removal can be reported from them.
State: approved
Actual answer: D15 A) Needs-review state (recommended)
Accepted scope: if the renewal or expiring value for a key is conflicting or did not verify, the diff emits a "Needs review" item carrying the cell's conflict list; it never reports that key as removed or as equal; a quote or AMS value that is conflicting or unverified is shown as Needs review in its slot and never counts as a mismatch. Adds one item state to the DiscrepancyBlock, card UI and tests.
History: none

### R12: What the live eval gate requires
Finding: outside-voice finding 1, P1, confidence 8/10, spec section 7 and verify.py:489, reviewers: Codex, Claude
Plan baseline: spec section 7: four hard fails; recall, precision and slot accuracy are soft metrics; none approved yet.
Runtime evidence: an empty discrepancy list triggers none of the four hard fails (spec section 7); verify checks that a quote and value are present on a page (verify.py:489), not that the value belongs to the named canonical field (a Coverage B amount labeled Coverage A can be perfectly cited).
Comparison grid:
| Choice | Current | A blocking recall and field check | B soft metrics only |
|---|---|---|---|
| R1 to R11 | approved D4 A through D15 A | same | same |
| R12 eval gate | four hard fails, soft recall, pending | the gate also fails when seeded-discrepancy recall is below 0.80 (starting value, [estimated]) or when a cited value belongs to the wrong canonical field per the ground truth | as spec section 7: recall, precision and slot accuracy are reported only |
Question D16:
D16 - Should the eval gate block on missed discrepancies and wrong-field values? (spec section 7)
Recommendation: A because an empty or mislabeled result must not pass a gate for a checker.
Header: Eval gate
Options:
A) Blocking recall and field check (recommended)
The gate also fails when seeded-discrepancy recall is below 0.80 (starting value, to be tuned after the first run) or when a cited value is attached to the wrong canonical field according to the ground truth. Precision and slot accuracy stay reported.
B) Soft metrics only
Keep recall, precision and slot accuracy as reported numbers; only the four hard fails block.
State: approved
Actual answer: D16 A) Blocking recall + field (recommended)
Accepted scope: the live gate also fails when seeded-discrepancy recall is below 0.80 (starting value, [estimated], tuned after the first run) or a cited value is attached to the wrong canonical field per the ground truth; precision and slot accuracy stay reported. Spec section 7 soft-metric paragraph is amended by this.
History: none

### R13: TODO, coverage identity for multi-location and multi-schedule policies
Finding: outside-voice finding 3 (part), P2, confidence 8/10, spec section 3 and 8, reviewers: Codex, Claude
Plan baseline: no TODO recorded; canonical keys such as coverage_a_limit have no location or schedule scope, so policies with several insured locations or coverage schedules cannot align.
Runtime evidence: the scalar key list is a closed list per workflow (schemas.py:84, extract.py:14); slice 3 fixtures are single-location.
Comparison grid:
| Choice | Current | A add TODO | B skip |
|---|---|---|---|
| R1 to R12 | approved through D16 A | same | same |
| R13 TODO | none, pending | TODOS.md gets an entry "Coverage identity for multi-location and multi-schedule policies"; spec section 8 states slice 3 supports single-location policies | no entry; the limit is not recorded outside this review |
Question D17:
D17 - Record multi-location policies as a TODO? (TODOS.md)
Recommendation: A because the limit is real, cheap to write down, and silent loss of it would hurt a later slice.
Header: TODO entry
Options:
A) Add to TODOS.md (recommended)
Add an entry with What, Why, Pros, Cons, Context and Depends on, and state in spec section 8 that slice 3 supports single-location policies.
B) Skip
Do not record it; the limit stays only in this review.
State: approved
Actual answer: D17 A) Add to TODOS.md (recommended)
Accepted scope: TODOS.md gets the entry "Coverage identity for multi-location and multi-schedule policies"; spec section 8 states that slice 3 supports single-location policies.
History: none

### R4: Where the discrepancy list lives
Finding: 3, P1, confidence 9/10, schemas.py:470 and models.py latest_content, reviewer: Claude
Plan baseline: spec section 4: the run artifact holds the discrepancy list as JSON with items {id, key, title, severity, lob, prose, slots, citations}; none approved yet.
Runtime evidence: `ArtifactContent` is `sections` of `Block = Heading | Paragraph | List | Table | Issue` (schemas.py:419-456, 470-474) and `latest_content()` validates every stored version against it; `Cell` already carries value, passage_ids, state, conflicts; `IssueBlock` has code, title, severity, category but no slots.
Comparison grid:
| Choice | Current | A new block type | B no new block |
|---|---|---|---|
| R1 ingestion | approved D4 A | same | same |
| R2 AMS facts | approved D5 A | same | same |
| R3 forms | approved D6 A | same | same |
| R4 shape | custom JSON, pending | `DiscrepancyBlock` added to the Block union: id, key, title, severity, lob, prose segments, slots dict role -> Cell | each discrepancy = IssueBlock plus a one-row TableBlock with role columns; no schema change |
| R5 roles via slots | pending | pending | pending |
Question D7:
D7 - Where should the discrepancy list be stored? (Policy Check spec section 4, finding 3)
Recommendation: A because one explicit block keeps every discrepancy a single unit that versions, chat citations and export already understand.
Header: Discrepancy storage
Options:
A) New block type (recommended)
Add DiscrepancyBlock to the Block union (id, key, title, severity, lob, prose segments, slots mapping role to Cell). The artifact stays an ArtifactContent, so versions, stale flags, chat citations and export keep working. Export gets a handler for the new block. Resolve state stays in the separate discrepancy_state table.
B) Reuse existing blocks
Express each discrepancy as an IssueBlock followed by a one-row TableBlock with the role columns. No schema change; the UI groups the pairs by id.
State: approved
Actual answer: D7 A) New block type (recommended)
Accepted scope: `DiscrepancyBlock` (id, key, title, severity, lob, prose segments, slots mapping role to Cell) is added to the Block union; the artifact stays an ArtifactContent; export_docx and the frontend renderer get a handler for it (unknown-block fallback tested); resolve state stays in `discrepancy_state`. Spec section 4 "artifact as JSON" is replaced by this. Other choices stay pending.
History: none

### R5: How roles are passed to the run
Finding: 4, P2, confidence 8/10, models.py:193, db.py:178, API.md:92, reviewer: Claude
Plan baseline: spec section 2: `parameters.roles`, a map document_id -> role, with required roles and one document per role; none approved yet.
Runtime evidence: `RunDocument.slot` (default "quotes") and `Workflow.input_slots` with `key`, `label`, `accepts`, `min`, `max` already exist (models.py:193, API.md:92); `RunManager.submit` takes only `document_ids` and `parameters` and dedupes active runs on (ids, parameters) (runner.py submit); no caller sets a slot other than the default.
Comparison grid:
| Choice | Current | A use slots | B parameters.roles |
|---|---|---|---|
| R1 ingestion | approved D4 A | same | same |
| R2 AMS facts | approved D5 A | same | same |
| R3 forms | approved D6 A | same | same |
| R4 shape | approved D7 A | same | same |
| R5 roles | parameters.roles (spec), pending | seeded `input_slots` expiring (1..1), renewal (1..1), quote (0..1), ams (0..1); POST /api/runs takes `slots` mapping document_id -> slot key; RunDocument.slot stores it; min and max validated from the workflow | roles map stored in run.parameters; min/max and uniqueness validated by new code |
Question D8:
D8 - How should the four document roles be passed to a run? (Policy Check spec section 2, finding 4)
Recommendation: A because the slot columns and the per-slot min and max rules already exist and are shown by the workflow API.
Header: Document roles
Options:
A) Use existing slots (recommended)
Seed policy-check input_slots (expiring 1..1, renewal 1..1, quote 0..1, ams 0..1); POST /api/runs accepts a slots mapping document_id to slot key, stored in RunDocument.slot, with min and max validated from the workflow. Active-run dedupe includes the slot.
B) parameters.roles
Keep the spec's roles map in run.parameters and validate required roles and uniqueness in new code.
State: approved
Actual answer: D8 A) Use existing slots (recommended)
Accepted scope: seed policy-check `input_slots` expiring (1..1), renewal (1..1), quote (0..1), ams (0..1); POST /api/runs accepts a `slots` mapping document_id -> slot key stored in RunDocument.slot; min and max validated from the workflow; active-run dedupe includes the slot. Spec section 2 `parameters.roles` is replaced by this.
History: none

### R6: The cross-window reference gap
Finding: 6, P2, confidence 7/10, TODOS.md "Cross-reference long fixture", reviewer: Claude
Plan baseline: spec section 8 does not mention it; TODOS.md says it is "needed before Policy Check and Coverage Gap workflows"; none approved yet.
Runtime evidence: windows are read independently (windows.py, merge.py); a value defined by reference across windows (page 12 "see Schedule C", Schedule C on page 250) is unmeasured.
Comparison grid:
| Choice | Current | A document and defer | B measure first |
|---|---|---|---|
| R1 to R5 | approved D4 A, D5 A, D6 A, D7 A, D8 A | same | same |
| R6 cross-reference | unmeasured, pending | add the risk to spec section 8; a missed referenced value shows as N/A in a slot, never a guess; no new fixture in slice 3 | add the long cross-reference fixture and run its live eval before the slice 3 build starts; a bounded second pass only if it fails |
Question D9:
D9 - What to do about values defined by reference across long-document windows? (Policy Check spec section 8, finding 6)
Recommendation: A because the failure mode is a missing value shown as N/A, which the spec already handles, and the fixture can follow.
Header: Cross-references
Options:
A) Document and defer (recommended)
Add the risk to spec section 8 and the README of known gaps; a missed referenced value renders as N/A, never a guess. Keep the TODOS.md fixture as a later item.
B) Measure first
Build the long cross-reference fixture and run its live eval before slice 3 build starts; add the bounded second pass only if it fails.
State: approved
Actual answer: D9 A) Document and defer (recommended); logged as decision da138d57
Accepted scope: add the cross-window reference risk to spec section 8 and keep the TODOS.md fixture as a later item; a missed referenced value renders as N/A, never a guess; no new fixture in slice 3. Gstack shortcut: ceiling = false N/A-vs-value discrepancies on long policies; upgrade when the cross-reference fixture is measured.
History: none

### R7: Which roles are compared and what an absent value means
Finding: 8 (Section 1), P1, confidence 8/10, spec section 3 steps 2 and 4, reviewer: Claude
Plan baseline: spec section 3: raise a discrepancy "when two present values differ, or a value is present in one role and absent in another"; severity "limit down more than 10%" keyed by change size; no baseline pair is defined; none approved yet.
Runtime evidence: the AMS role is a partial export whose fields are a subset of a policy (D5 A key table); quote is optional; severity by change size needs a direction (from which role to which).
Comparison grid:
| Choice | Current | A renewal-centered | B all pairs |
|---|---|---|---|
| R1 to R6 | approved D4 A, D5 A, D6 A, D7 A, D8 A, D9 A | same | same |
| R7 comparison rule | any present-vs-absent or differing pair, pending | every other present role is compared to renewal; a key is a discrepancy when renewal differs from any of them; absent in AMS or quote never counts; absent in renewal while present in expiring counts as removed; severity direction is expiring to renewal, and a quote or AMS-only mismatch is flat High | every pair of present roles is compared; absent-vs-present counts for every role |
Question D10:
D10 - Which roles are compared, and does a missing value count as a difference? (Policy Check spec section 3)
Recommendation: A because the renewal is the document being checked and AMS is a partial record, so absence in AMS is not evidence.
Header: Comparison rule
Options:
A) Renewal-centered (recommended)
Compare every other present role to the renewal. A key is a discrepancy when renewal differs from any present role; absent in AMS or quote never counts; present in expiring and absent in renewal counts as removed. Severity direction is expiring to renewal; a mismatch seen only in quote or AMS is flat High.
B) All pairs
Compare every pair of present roles and count present-versus-absent for every role, as spec section 3 reads.
State: approved
Actual answer: D10 A) Renewal-centered (recommended)
Accepted scope: every other present role is compared to the renewal; a key is a discrepancy when renewal differs from any present role; absent in AMS or quote never counts; present in expiring and absent in renewal counts as removed; severity direction is expiring to renewal; a mismatch seen only in quote or AMS is flat High. Spec section 3 steps 2 and 4 are replaced by this.
History: none

### R8: What a failed document does to the run
Finding: 9 (Section 1), P2, confidence 8/10, spec section 3 (silent), HANDOFF decision "all-docs-failed = FAILED run", reviewer: Claude
Plan baseline: spec is silent; slice 1 rule: a failed document shows "Extraction failed" + Retry, all documents failed = FAILED run; none approved yet for Policy Check.
Runtime evidence: runner.py `_fail_open_documents` and `Column.warning` carry per-document failure (runner.py:296, schemas.py Column); a discrepancy slot with no value looks identical to "document does not state it" unless marked.
Comparison grid:
| Choice | Current | A required fails run | B any failure fails run |
|---|---|---|---|
| R1 to R7 | approved D4 A, D5 A, D6 A, D7 A, D8 A, D9 A, D10 A | same | same |
| R8 failure rule | unspecified, pending | expiring or renewal failed: run FAILED with Retry; quote or AMS failed: run completes, that role's slots show "Not read" (distinct from N/A), column notice with Retry | any role failed: run FAILED with Retry |
Question D11:
D11 - What happens when one of the documents fails to read? (Policy Check spec section 3, finding 9)
Recommendation: A because a failed optional document should not hide a good comparison, and "Not read" must never look like "not stated".
Header: Failed documents
Options:
A) Required fails the run (recommended)
If the expiring or renewal document fails, the run fails with Retry. If the quote or AMS document fails, the run completes, that role's slots show Not read (never N/A), and the column carries a notice with Retry.
B) Any failure fails the run
If any role's document fails, the whole run fails with Retry; no partial results.
State: approved
Actual answer: D11 A) Required fails the run (recommended)
Accepted scope: expiring or renewal failing fails the run with Retry; quote or AMS failing lets the run complete, that role's slots show "Not read" (never N/A), and the column carries a notice with Retry. Reuses the per-document failure state; adds one slot state.
History: none

### R9: Regression contract for existing behavior
Finding: 10 (Section 3, IRON RULE), P1, confidence 9/10, runner.py submit, schemas.py Block union, documents upload route, reviewer: Claude
Plan baseline: no regression contract is approved; D8 A changes run creation, D7 A changes the Block union, D4 A adds an upload path beside the PDF-only one.
Runtime evidence: existing suites cover Compare Quotes and Proposal runs (backend/tests/test_runner.py, test_api_flow.py, test_export_docx.py, test_models.py, test_api_security.py); none exercises a non-default slot, a DiscrepancyBlock, or a non-PDF upload. Whether current stored artifact versions still validate after the union change is unverified.
Comparison grid:
| Choice | Current | A explicit contract | B rely on existing suite |
|---|---|---|---|
| R1 to R8 | approved D4 A to D11 A | same | same |
| R9 regression coverage | none approved, pending | tests assert: runs without slots still land in slot "quotes" and dedupe as before; every stored artifact version still validates after the union change; export and chat handle an unknown block without crashing; the PDF-only document upload still rejects non-PDF (not_pdf) and the AMS path accepts only JSON/CSV | existing tests only; new-feature tests cover new paths |
Question D12:
D12 - How should we protect existing Compare Quotes and Proposal behavior? (Policy Check plan, IRON RULE)
Recommendation: A because the plan changes run creation, the artifact block set and the upload rules that every existing workflow uses.
Header: Regression tests
Options:
A) Explicit regression contract (recommended)
Add tests that assert: runs without slots still land in slot quotes and dedupe as before; stored artifact versions still validate after the union change; export and chat tolerate a DiscrepancyBlock; PDF upload still rejects non-PDF and the AMS path accepts only JSON or CSV.
B) Rely on the existing suite
Existing tests plus new-feature tests only; no tests aimed at the changed existing interfaces.
State: approved
Actual answer: D12 A) Explicit regression contract (recommended)
Accepted scope: tests assert that runs without slots still land in slot "quotes" and dedupe as before; stored artifact versions still validate after the Block union grows; export_docx and chat tolerate a DiscrepancyBlock; the PDF upload still rejects non-PDF (not_pdf) and the AMS path accepts only JSON or CSV.
History: none

Approval readiness: PASS. Checked R1 (D4 A), R2 (D5 A), R3 (D13 A, reopened from D6 A), R4 (D7 A), R5 (D8 A), R6 (D9 A, logged da138d57), R7 (D10 A), R8 (D11 A), R9 (D12 A), R10 (D14 A), R11 (D15 A), R12 (D16 A), R13 (D17 A); scope cuts D1 and D2; structure D3 B. Each cites its own actual answer.

## Carried forward (required proof of approved behavior, no new choice)
- Outside finding 6 (schemas.py:506-519): `ArtifactContent.passage_ids()` must traverse `DiscrepancyBlock` slots (cells) so stale flags and chat citations see its evidence, in current and historical versions. Part of D7 A.
- Outside finding 7 (edits.py:221, runner.py:505): `discrepancy_state` stores a fingerprint of the compared slot values; open/resolved is reconciled whenever the active comparison changes (regenerate, retry, version restore); a changed fingerprint resets to open and flags "changed since resolved". Part of D2 and spec section 4.
- Budget correction: the per-document ceiling is 500k (config.py:93), not the 400k in HANDOFF; a budget test on `long_300` with the D13 combined schema must stay inside it. Part of D13 A.
- Budget exhaustion (existing `budget_exhausted` notice): affected role slots must read "Not read", never N/A. Part of D11 A.

## Outside voice
Codex (plan-review tier) completed with 7 findings: 5 High, 2 Medium. Findings 3, 4, 5 and 6 were confirmed by file and line (merge.py:587, config.py:93, compose.py:682-690, schemas.py:506-519). Dispositions: 1 -> D16 A; 2 -> D15 A; 3 -> D13 A and D17 A (provision keys dropped, multi-location deferred); 4 -> D13 A (D6 reopened); 5 -> D14 A; 6 and 7 -> carried forward above. Agreement between reviewers approved nothing; every change has its own answer.

## NOT in scope
- Bulk status endpoint and "Apply to filtered" / "Clear all" (D1): unobserved behavior.
- `accepted` status (D2): only Resolved is observed.
- Special provisions as separate items and `provision:<slug>` keys (D13): no stable identity across documents.
- Model-written discrepancy prose (D14): deterministic text; a model summary is a later option.
- Cross-window reference measurement (D9): documented risk, the TODOS.md fixture stays open.
- Multi-location and multi-schedule policies (D17): TODOS.md entry.
- Summary tab, Document Verification tab, Email Carrier, Update AMS, Accept AI recommendations (spec section 1).

## What already exists
- Reused as is: parse, windows, merge, verify, passages and chips, `Cell` (value, passage_ids, state, conflicts), versions and chat, the per-document failure state and Retry.
- Reused with a small change: `RunDocument.slot` and `Workflow.input_slots` (D8), `merge_document` dynamic form keys (D13), the `Block` union and `passage_ids()` (D7).
- New: `pipeline/diff.py` (with the severity dict), `ams.py` (parse, render to PDF, key table), `discrepancy_state`, `DiscrepancyBlock`, two endpoints in `routes/runs.py`, the combined extraction schema.
- Shared code (rubric): the diff's value comparison should reuse `verify._number` and `_eq_value` (verify.py:145-165). Deferred to implementation as T11; behavior-neutral.

## Diagram (data flow after the decisions)
```
PDF roles ─► parse ─► windows ─► extract (scalars + forms[], one pass, D13) ─► merge (+form:<n> keys) ─► verify
AMS file ──► ams.py parse ─► render to PDF (D4) ─► parse/verify; facts from key table, no model (D5)
                                     ▼
 diff.py: align by key ─► renewal-centered compare (D10) ─► Needs review for conflicting/unverified (D15)
          ─► severity dict ─► deterministic title+text (D14) ─► DiscrepancyBlock[] in ArtifactContent (D7)
                                     ▼
 discrepancy_state(fingerprint, open|resolved) ─► GET list / PATCH item (runs.py) ─► list + modal (design stack)
Run creation: POST /api/runs {slots} (D8); required role failed -> FAILED + Retry; optional failed -> "Not read" (D11)
```
Files needing inline diagrams: `pipeline/diff.py` (compare rule and states) and `ams.py` (parse, render, map).

## Failure modes
| Path | Realistic failure | Test / handling | User sees | Critical gap |
|---|---|---|---|---|
| AMS render | malformed JSON/CSV, BOM, huge file | parser tests, reject at upload | message naming the limit | no |
| Required doc extract | corrupt or encrypted renewal | run FAILED + Retry (D11) | "Extraction failed" + Retry | no |
| Optional doc extract | quote fails | slots "Not read" (D11) | notice + Retry | no |
| Budget exhausted | long policy runs out of tokens | slots "Not read", existing notice | notice | no |
| Forms array | model returns truncated or duplicate forms | merge dedupes by key, eval covers, recall gate (D16) | shown or missed | no |
| Wrong field filing | value cited under the wrong key | ground-truth field check (D16) | blocked at the gate | no |
| Conflicting value | two values, no rule picks one | Needs review (D15) | Needs review + conflicts | no |
| Resolve state | regenerate or retry changes values | fingerprint reset | "changed since resolved" | no |

Critical gaps: 0.

## Worktree parallelization strategy
| Step | Modules touched | Depends on |
|------|----------------|------------|
| T1 AMS ingest | backend/ams, routes/documents, uploads | none |
| T2 slots + failure rules | backend/pipeline/runner, routes/runs, db seed | none |
| T3 combined extraction + dynamic keys | backend/pipeline (extract, merge), schemas | none |
| T4 diff engine | backend/pipeline/diff | T3 key names (stub allowed) |
| T5 DiscrepancyBlock + traversal + export | backend/schemas, export_docx, chat | none |
| T6 state table + endpoints | backend/models, routes/runs | T5 |
| T7 fixtures + eval gate | tests/eval, tests/fixtures | T1, T3, T4 |
| T8 regression tests | backend/tests | T2, T5 |
| T9 UI (design stack) | frontend | T5, T6 |

Lane A: T1. Lane B: T2 then T6 (routes/runs.py shared). Lane C: T3 then T4. Lane D: T5. Lane E: T7 after A and C, T8 after B and D, T9 last.
Execution order: launch A, B, C, D together; merge; then T6, T7, T8; then T9.
Conflict flags: routes/runs.py (T2, T6) and schemas.py (T3, T5) are shared, so sequence or coordinate. PR count: 2 (backend, then UI); no ship-policy.json exists.

## Implementation Tasks
Synthesized from this review's findings. Each task derives from a specific finding above. Effort ratios assumed: tests ~50x, features ~30x, architecture ~5x.

- [ ] **T1 (P1, human: ~3d / CC: ~1h)** - ams - parse, render to PDF, key table, upload path
  - Surfaced by: Scope Challenge 1 (uploads.py:60), D4, D5
  - Files: backend/ams.py, backend/routes/documents.py, backend/uploads.py
  - Verify: AMS parser and render tests; the PDF path still rejects non-PDF
- [ ] **T2 (P1, human: ~1.5d / CC: ~45m)** - runs - slots on run creation, failure rules, Not read
  - Surfaced by: Scope Challenge 4 (models.py:193), D8, D11
  - Files: backend/pipeline/runner.py, backend/routes/runs.py, backend/db.py
  - Verify: slot min/max tests, no-slot runs still in "quotes", failed optional role is Not read
- [ ] **T3 (P1, human: ~3d / CC: ~1.5h)** - extract/merge - combined schema, dynamic form keys
  - Surfaced by: Scope Challenge 2, outside findings 3 and 4 (config.py:93, merge.py:587), D13
  - Files: backend/pipeline/extract.py, backend/pipeline/merge.py, backend/schemas.py
  - Verify: merge tests with form keys; live eval; long_300 stays under 500k
- [ ] **T4 (P1, human: ~3d / CC: ~1.5h)** - diff - align, renewal-centered rule, Needs review, severity dict, deterministic text
  - Surfaced by: Architecture 8, outside findings 2 and 5, D10, D14, D15
  - Files: backend/pipeline/diff.py
  - Verify: table-driven tests incl. the 10% edge, absent-in-AMS, removed, conflicting; byte-identical rerun
- [ ] **T5 (P1, human: ~1.5d / CC: ~45m)** - schemas - DiscrepancyBlock, passage_ids traversal, export and chat handling
  - Surfaced by: Scope Challenge 3 (schemas.py:470), outside finding 6, D7
  - Files: backend/schemas.py, backend/export_docx.py, backend/chat.py
  - Verify: old versions validate; citations found in slots; export tolerates the block
- [ ] **T6 (P1, human: ~1.5d / CC: ~45m)** - state - discrepancy_state with fingerprint, GET and PATCH
  - Surfaced by: spec section 4, outside finding 7, D2
  - Files: backend/models.py, backend/routes/runs.py
  - Verify: resolve survives regenerate; changed values reset to open; retry and restore reconcile
- [ ] **T7 (P1, human: ~3d / CC: ~1.5h)** - eval - fixtures, ground truth, recall 0.80 and wrong-field gate
  - Surfaced by: Test review, outside finding 1, D16
  - Files: tests/eval, tests/fixtures
  - Verify: the gate fails on an empty result and on a mislabeled field; one live run
- [ ] **T8 (P1, human: ~1d / CC: ~30m)** - regression - existing-interface contract
  - Surfaced by: Test review IRON RULE, D12
  - Files: backend/tests
  - Verify: the four D12 assertions
- [ ] **T9 (P2, human: ~5d / CC: ~2h)** - frontend - roles step, list, cards, review modal (design stack, 3 rounds)
  - Surfaced by: spec section 6; slots from D8, GET and PATCH from T6
  - Files: frontend/src
  - Verify: e2e with axe 0 at 1440/1024/390
- [ ] **T10 (P2, human: ~0.5d / CC: ~10m)** - docs - API.md, spec amendments for sections 2, 3, 4, 7, 8
  - Surfaced by: all decisions
  - Files: backend/API.md, docs/specs/2026-11-02-policy-check-design.md
  - Verify: read-through against the ledger
- [ ] **T11 (P3, human: ~1h / CC: ~10m)** - diff - reuse the verify value helpers (deferred)
  - Surfaced by: Code Quality, verify.py:145-165
  - Files: backend/pipeline/verify.py, backend/pipeline/diff.py
  - Verify: existing verify tests still pass

## Unresolved decisions
None in this review.

## Completion summary
- Step 0: Scope Challenge: scope reduced per recommendation (D1, D2 cuts; D3 smaller arrangement)
- Reality rows: 7/7 applicable rows emitted, 0 unverified premises
- Architecture Review: 2 issues found
- Code Quality Review: 2 issues found
- Test Review: diagram produced, 27 gaps identified
- Performance Review: 1 issue found
- NOT in scope: written
- What already exists: written
- TODOS.md updates: 1 item proposed (added)
- Failure modes: 0 critical gaps flagged
- Unresolved decisions: 0 in this review
- Outside voice: Codex completed, 7 findings, all dispositioned
- Parallelization: 5 lanes, 4 parallel / 1 sequential
- Lake Score: 11/12 scored answers picked the highest-completeness option (no option scored 10/10; D9 chose speed over the 9/10 option)

## Suppressed findings (confidence below 5)
None.

## Reality rows
premise-table: 9 premises checked (upload PDF-only, slot column, flat fact schema, block union, 500k ceiling, run-scoped validator, passage_ids enumeration, merge field list, verify value checks), unverified=0.
REALITY: premise-table pass 9 claims checked, unverified=0 backend/uploads.py:60
REALITY: already-done pass no policy-check code or branch on main (git log --grep policy, grep backend and frontend) backend/models.py:66
REALITY: surface-check finding POST /api/runs exists, the discrepancy routes are proposals; submit() takes no slots today backend/API.md:276
REALITY: binding-decisions pass no relevant prior decision for this project (gstack-decision-search --scope branch) docs/specs/2026-11-02-policy-check-design.md:1
REALITY: numbers finding HANDOFF's 400k per-document ceiling is stale, config.py says 500k [measured: config.py:93]; severity thresholds are [estimated] backend/providers/config.py:93
REALITY: deferred-asks pass no linked issue, the evidence file lists what the slice leaves out (see NOT in scope) docs/reference/brokerdocs-workflows-evidence.md:222
REALITY: pr-count pass no ship-policy.json, default one PR per wave, review recommends 2 (backend, then UI) docs/specs/2026-11-02-policy-check-design.md:506

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | not run | n/a |
| Outside Review | codex (plan-review, via `/plan-eng-review`) | Independent 2nd opinion | 4 (3 prior) | completed (issues found) | 7 findings, 7 dispositioned |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 5 (4 prior) | ISSUES OPEN (not persisted until logged) | 32 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 fresh (1 older than 7 days) | not run for this plan | n/a |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | not run | n/a |

**OUTSIDE COVERAGE:** codex, plan-review phase, completed; 5 High and 2 Medium findings; 4 confirmed against file and line, all dispositioned (D13 to D17 and carried-forward items).
**CROSS-MODEL:** native Claude review and completed Codex review both exist. Overlap: both found the multi-valued forms and provisions gap (D6, reopened as D13). Codex alone found the 500k token ceiling, the missing item-scoped prose validator, the eval-gate hole, the uncertain-value state and the resolution fingerprint. The native review alone found the PDF-only upload, the existing slots, the artifact block union and the AMS partial-record noise. Model identity is recorded as reported: Codex plan-review tier; native Claude Sonnet 5.5.
**VERDICT:** no review is CLEAR. Eng Review is ISSUES OPEN (mapped work, 32 issues all dispositioned, 0 unresolved decisions); eng review required before it can read CLEAR.

NO UNRESOLVED DECISIONS

---

## Amendments after build (2026-11-02)

The backend was built and merged on 2026-11-02 (HANDOFF.md, "slice 3 Policy Check backend built and merged"). The sections above are left as written; this section lists where the build differs from sections 2, 3, 4, 5, 7 and 8. `backend/API.md` documents the shipped contract.

**Section 2, roles and inputs**
- `parameters.roles` is replaced by `slots` on `POST /api/runs` (ledger D8): a map `document_id -> slot key`, where the keys `expiring`, `renewal`, `quote`, `ams` are the workflow's `input_slots` with `min`/`max` and `accepts`. Slot errors answer `400 invalid_run`; the older `422 invalid_run` cases (no documents, a duplicate, more than 8) stay 422.
- `accepts` is enforced: an AMS export fits only the `ams` slot, a PDF only the PDF slots, so an AMS export cannot enter Compare Quotes.
- A required slot (`expiring`, `renewal`) with no readable document fails the run (retryable). A failed optional slot (`quote`, `ams`) shows "Not read" and is never N/A (D11).
- The AMS file has its own endpoint, `POST /api/documents/ams`, not the PDF upload. It takes `.json` (flat object) and `.csv` (one record row, or a `field,value` table), 512 KB, 400 fields, 100-character names, 1000-character values. It is rendered to a PDF at upload (one `field_name: value` line per field) and verified like any PDF, so a citation is a normal passage on the rendered page, not a separate pseudo-page type. The original is kept beside it. The response adds an `ams` summary (`format`, `field_count`, `unmapped`, `notices`).

**Section 3, facts and diff**
- Forms are extracted in the same pass as the scalar fields (one combined schema, ledger D13) and align by `form:<normalized number>` keys, so an edition change is one change.
- The comparison is renewal-centered (D10): expiring vs renewal is graded by fact type; quote and AMS are compared with the renewal only, and only a slot that has a value counts. A quote or AMS mismatch is a flat `high`.
- `policy_term` is not compared expiring vs renewal (the term always moves); it is compared with quote and AMS only.
- The AMS effective date, expiration date and carrier are not compared: they map to no policy field and are named in the `ams.unmapped` list and in a run notice.
- A conflicting or unverified expiring or renewal value becomes a `needs_review` item (severity `low`), never "removed" and never equal (D15).
- Severity values were set during the build and are starting values to tune: limit down more than 10% `critical` (exactly 10% is `high`), other limit change or removal `high`, limit added `medium`; deductible increase or added `critical`, decrease `medium`; premium up `medium`, down `low`; discount dropped `high`, discount amount change `low`; form edition change `medium`; text or address change `medium`; policy number change `low`; keys outside the closed list `low`.
- Title and explanation are deterministic text built in code (D14). There is no model prose and no compose call for Policy Check, so Regenerate is free and byte-identical; "No-AI-prose mode" does not apply.
- Reading fix found by the live eval: a two-column renewal schedule was read values-then-labels. `parse.reading_text` now rejoins label and value columns row by row (used by `extract.page_text`, so chat sees it too).

**Section 4, storage**
- `discrepancy_state` statuses are `open | resolved` only; `accepted` was cut (D2). The table also keeps a fingerprint of the compared slot values (citations excluded) and the resolved fingerprint. A resolved item whose values change reopens with `changed_since_resolved`; restoring the resolved values (for example by undo) restores the resolved status.
- The stored discrepancy item is a `DiscrepancyBlock` in the artifact `Block` union (D7): `{type, id, key, title, severity, item_state, lob, prose, slots}`. Citations live in the slot cells and prose segments, not in a separate `citations` field.

**Section 5, API**
- `POST /api/runs/{id}/discrepancies/bulk` is cut (D1), and so is the `accepted` status.
- `GET /api/runs/{id}/discrepancies` also returns `open_counts` and totals; `PATCH` takes `status` and/or `note` (a note up to 2000 characters, `null` clears it).
- Added: `POST /api/documents/ams`, `slots` on `POST /api/runs`, and a `slot` field on every run document in REST and SSE.

**Section 7, eval and tests**
- Fixtures live in `tests/fixtures/policy/`; the live gate also blocks on recall below 0.80 and on wrong-field values (D16). After the reading fix two live Policy Check runs passed: recall 13/13, precision 13/13, 0 hard fails, about 15k tokens per run. The 23-document Compare Quotes live eval on `e8eac21` also passed with 0 hard fails (267/286, 0.934).

**Section 8, risks and open questions**
- Slice 3 supports single-location policies (D17): canonical keys carry no location or schedule, so a multi-location policy cannot align. Recorded in TODOS.md.
- The non-PDF citation question is settled by rendering the AMS export to a PDF; no new citation type was added.
- Open residuals are listed in TODOS.md: reading_text limits, duplicate form editions in one call, the long_300 token ceiling and a one-off scanned retention miss.
