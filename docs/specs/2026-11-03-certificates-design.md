# Slice 4: Checked certificates (COI)

Date: 2026-11-03. Status: owner-approved design (brainstorm sections 1 to 4), spec awaiting owner review.
Next: `/plan-eng-review`, then lanes. Rail item: Certificates (the last disabled item this slice makes live).
Evidence: `docs/reference/brokerdocs-workflows-evidence.md` section 3; `docs/slice-map.md`; PRODUCT.md.

## 1. Purpose and decisions

A broker issues a certificate of insurance when a client's counterparty (landlord, general contractor, lender)
asks for proof of coverage. The certificate is where brokers carry E&O exposure: it is easy to state coverage the
policies do not give (additional insured with no endorsement, a policy that expired). This slice proves the
engine's strength, verification, on that risk: every field cited, every claim checked before issue.

Owner decisions (2026-11-03, do not re-ask):
- C1 Purpose: a **checked** certificate (fill with citations, and check each claim against the policies before
  issue). Not fill-only; holder contract requirements (gap list) are out of scope.
- C2 Holder: typed on the certificate, remembered per client and offered as picks next time. No holder directory page.
- C3 Policies: upload, or pick documents already in the app for that client; already-read documents are reused.
- C4 Failed check: Issue is blocked until each failing claim is fixed (add document), removed from the certificate,
  or overridden with a typed reason saved on the issued record.
- C5 Approach: a new workflow on the existing run engine; code fills fields and runs checks (no model call after
  extraction); layout is a data field map so ACORD 25 can later be a second map.
- C6 Own one-page layout first (ACORD blanks are copyrighted; no licensed blank).
- C7 One declarations document = one line of coverage this slice; package policies are flagged unsupported.
- C8 Extraction cache applies to every workflow, but only for the same file (sha256) and the same schema version.

Out of scope: email sending, batch certificates for many holders, expiry tracking/reminders, ACORD 25 or other
ACORD forms, holder contract requirement checks, package policies, CRM/client entities.

## 2. Flow and states

1. **Start** from the Certificates rail item or a client's queue row menu ("Issue certificate", prefills client).
   Inputs: client (typed, or from the row); holder name and address (the client's past holders first, or new);
   requested wording checkboxes: additional insured, waiver of subrogation, primary and non-contributory;
   optional description of operations (free text); certificate date (default today).
2. **Policies**: upload declarations and endorsement PDFs, or pick the client's documents (proposal wizard picker,
   deduped by sha256). Cached documents skip the read; new ones are read with the reading mark.
3. **Draft**: certificate layout with a citation chip on every filled field; beside it the **check list**, one row
   per claim: Supported (cited page) or Not supported (reason). Rows: each requested wording item; automatic
   checks (each shown policy in force on the certificate date; named insured consistent across policies).
4. **Resolve** each Not supported row: Add document (read, then re-check), Remove from certificate, or Override
   with a required reason.
5. **Issue** is enabled only when every row is Supported, Removed or Overridden; the button shows the remaining
   count when disabled ("2 checks to resolve"). Issuing freezes values, override reasons, holder and PDF, and
   saves the holder to the client's list. Issued certificates are read-only; "New version" starts an edited copy.
6. **Queue**: draft with unresolved rows = Needs attention; draft with all rows handled = Ready ("Ready to issue");
   issued = Done recently. Certificate items appear on the Certificates page queue only; the main work queue
   (`#/`, Personal lines) does not list them, so certificate volume does not bury comparisons and checks
   [assumed in the spec, owner may veto]. Activity lists them like any run.

## 3. Data model and extraction

**Workflow** `certificate` (`Workflow.input_slots`): one slot `policies`, 1 to 8 PDFs. A document is a policy's
declarations or an endorsement. Endorsements join their policy in code by policy number.

**Extraction schema** (`cert:` keys, one combined pass like Policy Check's `form:<n>`; existing windows, verify,
merge pipeline):
- Per policy document: named insured, insured address, carrier, NAIC, policy number, line (general liability,
  auto, umbrella, workers comp, property, other), effective date, expiration date.
- Limits by line: GL each occurrence, general aggregate, products-completed operations aggregate, personal and
  advertising injury, damage to rented premises, medical expense; auto combined single limit; umbrella each
  occurrence and aggregate; WC statutory, employer's liability each accident, disease each employee, disease
  policy limit.
- Endorsements (per form): form number, type (additional insured blanket / scheduled with named party; waiver of
  subrogation; primary and non-contributory), policy number it modifies.
- A document stating several lines (package policy) gets a document-level notice and contributes no facts.
- A value without a verified citation is unverified, conflicting or missing, never guessed (PRODUCT.md).

**Extraction cache** (closes TODO 2c): before reading a document for a run, find the latest finished run whose
facts came from the same `document.sha256` with the same schema fingerprint (field set + prompt version). If
found, copy those facts (with state, quotes, rects, passages) into the new run and mark the run document
`reused_from_run_id`; otherwise read normally. Applies to every workflow; schema mismatch is always a miss.

**New tables** (fail-fast schema check extended per D10):
- `holder`: id, workspace_id, client_key (normalized client name), name, address, created_at, last_used_at.
- `certificate_claim`: run_id, claim_key, status (supported, not_supported, removed, overridden), reason,
  fingerprint, resolved_fingerprint, updated_at. A changed fingerprint reopens a removed/overridden claim
  (DiscrepancyState rule).
- `certificate_issue`: run_id, artifact_id, version, issued_at, holder snapshot (JSON), claims snapshot with
  override reasons (JSON), pdf path. Unique (artifact_id, version).

Draft certificate = an artifact version (`CertificateBlock` in the `Block` union), so edit/undo/versions reuse the
artifact machinery. Field map (data): `{field, source fact, format}` rows for the own layout.

**Checks** (`pipeline/coi_checks.py`, deterministic, no model call):
- Requested additional insured / waiver / primary and non-contributory: needs a matching endorsement type joined
  to a shown policy, with a verified citation. Scheduled AI must name the holder (normalized compare); blanket
  passes with its form cited.
- In force: each shown policy's effective <= certificate date <= expiration, both dates verified.
- Insured consistency: named insured matches across shown policies (case/space-insensitive, as the queue's rule).
- Limits print only when verified; otherwise the field prints "Not stated" (not a claim row, a visible gap).

## 4. Screens (look via the design stack; this section is content and behaviour only)

- **Certificates page** (`#/certificates`): certificate queue (Needs attention, Ready to issue, Done recently) with
  the work-queue row pattern (serif client, one next action), and "New certificate".
- **Wizard** (3 steps, like Proposal/Policy Check): Who (client, holder picks, wording, date, description);
  Policies (upload or pick; "already read" marker on cached ones); Read (progress, then the draft).
- **Draft view** (run page): certificate with chips (open the cited page side by side); check list with per-row
  actions (Add document, Remove from certificate, Override... dialog with required reason); header with client,
  holder, and Issue certificate (disabled with count).
- **Issued view**: read-only, "Issued <date>", override reasons on the record (not printed), Download PDF,
  New version.
- **Hooks**: queue row overflow "Issue certificate"; Activity lists certificate runs.
- **PDF**: own one-page layout in the Paper trail voice: producer, insured, insurers A to F with NAIC, coverage
  table, description of operations, holder, cancellation clause; missing values print "Not stated". Generated in
  code from the frozen values.

## 5. Errors and edge cases

- Document fails to read: "Not read" + Retry; dependent claims are Not supported.
- Package policy: document notice "This document covers several lines; not supported yet"; no facts used.
- Endorsement whose policy number matches no shown policy: notice naming the form and the number.
- Conflicting values (e.g. two expiration dates): dependent claim Not supported until resolved or overridden.
- Adding a document or editing a value re-runs checks; changed fingerprints reopen removed/overridden rows.
- Issue is atomic (PDF + record in one transaction, PDF written to a temp path then moved); a second Issue on the
  same version returns the existing issue (idempotent), never a duplicate.
- Cached document rows say "read on <date>".

## 6. Testing and eval

- Backend unit: each check type (pass, fail, conflicting, unverified); endorsement join (match, no match);
  scheduled vs blanket AI; in-force boundaries (effective day, expiration day); insured mismatch; cache hit,
  schema-mismatch miss, verified state copied; claim fingerprint reopen; issue freeze + idempotency; holder save
  and reuse per client_key.
- e2e: wizard to draft, resolve by add/remove/override, issue, download, new version; past-holder pick; cached
  document marker; queue row "Issue certificate"; axe 0 at 1440, 1024, 390.
- Fixtures (synthetic generator, `tests/fixtures/certificates/`): GL, auto, umbrella, WC declarations; AI scheduled
  and blanket, waiver, primary non-contributory endorsements; traps: expired policy, AI endorsement for another
  policy number, one scanned document, one package policy.
- Eval gate (live, `-k certificate`): every claim verdict vs truth; 0 hard fails (hard fail = Supported when the
  truth is not supported); recall on not-supported claims >= 0.90; field accuracy reported per field.

## 7. Build order (model policy: Opus for pipeline, migrations, checks, design)

1. Backend lane (Opus): workflow + slot, `cert:` schema, extraction cache, tables + schema check, checks module,
   claim state API, issue + PDF, holder API, queue classification, API.md.
2. Fixtures + eval lane (Opus): generator, truth sets, offline tests, live gate.
3. UI lane (Opus, full design stack, Playwright MCP, max 3 rounds).
4. Docs (Sonnet): DESIGN.md, slice map, HANDOFF.

## 8. Related, not in this slice

"Clicks to outcome" benchmark (owner question 2026-11-03): scripted shortest paths for core tasks with click,
keystroke and screen counts against a written manual baseline. Offered as its own item after this spec.

## Engineering review (2026-11-03, /plan-eng-review)

Target: this spec (`docs/specs/2026-11-03-certificates-design.md`), reviewed on main at 397b515. The ledger below
overrides the spec text above it.

### Scope record
feature answers: no feature cuts proposed (owner decisions C1 to C8 stand); structure: B (Smaller arrangement),
answer D1 2026-11-03; accepted scope: certificate claims are stored in `discrepancy_state` (status widened to
open/resolved/removed; a certificate resolve requires a note = the override reason; Remove sets `removed`);
existing PATCH `/api/runs/{id}/discrepancies/{item_id}`, `reconcile_discrepancy_states` and `discrepancy_counts`
are reused; new tables are only `holder` and `certificate_issue` (the spec's `certificate_claim` table is dropped);
pending remedies: S1, S2.

### Reality rows
Premise table (unverified=0):

| Premise | Claim | Receipt | Verdict |
|---|---|---|---|
| P1 | Existing read, verify, merge pipeline is reusable per document | backend/pipeline/runner.py:944 `windows.extract_document(...)`, :951 `windows.verify_document` | true |
| P2 | Policy Check uses one combined extraction pass with dynamic keys | commit d0f8608 "combined extraction pass with forms array, dynamic form keys in merge" | true |
| P3 | Draft as an artifact version gives edit/undo/versions | backend/models.py:357 `class ArtifactVersion`, routes/artifacts.py:92 PATCH cells, :107 undo | true |
| P4 | Claim reset rule exists (fingerprint) | backend/models.py:374 `class DiscrepancyState`, :694 `reconcile_discrepancy_states` | true |
| P5 | "Add document (read, then re-check)" on a draft | backend/models.py:485 `RunStatus.DONE: frozenset()`; no add-document route (routes/runs.py:94-301) | false (S1) |
| P6 | Cache by sha256 can copy facts with passages | routes/documents.py:87 new `Document` per upload; models.py:289 passage keyed by `document_id` | partial (S2) |
| P7 | PDF can be generated in code with an installed library | backend/export.py:320 `from reportlab.lib import colors` | true |
| P8 | Fail-fast schema check exists to extend | backend/db.py:89 | true |
| P9 | Queue classification is per workflow slug | backend/work_queue.py:177 `def classify`, :182-185 slug branches | true |
| P10 | A new Block type must join citation traversal | backend/schemas.py:603 `Block = Annotated[...]`, :646 `passage_ids` (prior learning brokerdocs-block-union-traversal) | true |

REALITY: premise-table finding 10 premises, 1 false (P5), 1 partial (P6), unverified=0 backend/models.py:485
REALITY: already-done pass no certificate code or cache on main (`git log --grep certificate|coi|extraction cache` = spec commits only) docs/specs/2026-11-03-certificates-design.md:1
REALITY: surface-check pass reused routes exist: PATCH /api/runs/{id}/discrepancies/{item_id} backend/routes/runs.py:301, POST /api/runs/{id}/retry :189; new routes are proposals backend/routes/runs.py:301
REALITY: binding-decisions pass C1-C8 owner decisions; slice 3b D9 "Workflows commercial-only" untouched (certificates have their own rail page) docs/specs/2026-11-03-certificates-design.md:16
REALITY: numbers pass 1-8 PDFs, recall 0.90, 0 hard fails are [estimated] new targets; per-document ceiling 500k [measured: backend/providers/config.py DOCUMENT_TOKEN_BUDGET] docs/specs/2026-11-03-certificates-design.md:132
REALITY: deferred-asks pass no linked issue docs/specs/2026-11-03-certificates-design.md:1
REALITY: pr-count pass no .gstack/ship-policy.json, default one PR per wave; the spec's 4 lanes merge sequentially to main (4 merges, each gated on tests) docs/specs/2026-11-03-certificates-design.md:139

## Decision ledger

### S1: How "Add document" works on a certificate draft
Finding: S1, CRITICAL, confidence 9/10, backend/models.py:485 `RunStatus.DONE: frozenset()` vs spec section 2 step 4 "Add document (read, then re-check)"; reviewer: main session.
Plan baseline: spec section 2 step 4 (owner-approved design section 1, 2026-11-03): Add document reads the new document, then re-checks.
Runtime evidence: a DONE run has no outgoing transition (models.py:485); `_begin` processes only QUEUED run documents (runner.py:880); no route adds a document to a run (routes/runs.py:94-301).
Comparison grid:

| Choice | Current | A (reopen draft) | B (successor run) |
|---|---|---|---|
| S1 mechanism | unspecified (premise false) | certificate drafts only: DONE/PARTIAL to QUEUED when a document is added; runner reads only the new QUEUED document, keeps existing facts, re-composes a new artifact version; reconcile keeps claim decisions | Add document starts a new certificate run with the same inputs plus the document; cache makes old documents free; old draft marked superseded |
| Issued certificates | never edited (spec) | unchanged: an issued version never reopens; New version is required | unchanged |
| S2 cross-upload cache | pending | pending | pending |

Question D2:
D2 — How should "Add document" work on a certificate draft?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: The spec lets a broker fix a failing claim by adding a missing endorsement and re-checking. But a finished run is final in this engine (models.py:485: DONE has no next state) and nothing adds a document to a run. Either we let an unissued certificate draft reopen to read one more document, or "Add document" starts a fresh run.
Stakes if we pick wrong: a successor run loses the broker's overrides and puts two drafts for one certificate in the queue; a careless reopen could let Compare or Policy Check runs change after they are done.
Recommendation: A because the runner already reads only QUEUED documents and keeps the rest, and reconcile already carries decisions to a new version.
Completeness: A=9/10, B=7/10
Pros / cons:
A) Reopen the draft (recommended)
  ✅ One run is one certificate draft; overrides and removals carry over through reconcile
  ✅ Reuses the retry path (PARTIAL/FAILED to QUEUED exists); only the new document is read
  ❌ Adds a guarded DONE to QUEUED transition (certificate drafts only) that tests must pin
B) Successor run
  ✅ Keeps DONE terminal for every workflow, no state-machine change
  ✅ The extraction cache makes the old documents free
  ❌ Decisions must be copied across runs, and the queue must hide the superseded draft
Net: A changes one transition under a guard; B keeps the state machine but moves complexity into copying and hiding.
Header: Add document
Options:
A) Reopen the draft (recommended)
Certificate drafts only (never issued, never other workflows): adding a document moves DONE/PARTIAL to QUEUED, reads just that document, re-composes a new version; claim decisions carry over via reconcile. Tests pin the guard. Human ~4h / CC ~20min.
B) Successor run
Add document starts a new certificate run with the same inputs plus the document (old documents come from the cache); decisions are copied; the old draft is marked superseded and hidden from the queue. Human ~6h / CC ~30min.

State: approved
Actual answer: A) Reopen the draft (owner, D2, 2026-11-03)
Accepted scope: certificate drafts only (never issued, never other workflows): adding a document moves DONE/PARTIAL to QUEUED, reads only that document, keeps existing facts, re-composes a new artifact version; claim decisions carry over via reconcile; tests pin the guard (other workflows' DONE stays terminal; an issued version never reopens).
History: none

### S2: Extraction cache for a re-uploaded identical file
Finding: S2, INFORMATIONAL, confidence 8/10, backend/routes/documents.py:87 `doc = models.Document(... sha256=hashlib.sha256(data).hexdigest() ...)` (every upload is a new Document) and backend/models.py:289 passage `UniqueConstraint("document_id", "page_no")`; reviewer: main session.
Plan baseline: C8 (owner, 2026-11-03): cache applies to the same file (sha256) and the same schema version; spec section 3 "copy those facts (with state, quotes, rects, passages)".
Runtime evidence: picking an existing document reuses its document_id (same Document row); re-uploading the same bytes creates a new Document with no pages, passages or facts, so copying facts by sha256 would point citations at the other document's id unless pages and passages are copied too.
Comparison grid:

| Choice | Current | A (sha256, copy pages + passages) | B (document_id only) |
|---|---|---|---|
| S2 cache match | spec: sha256 + schema | sha256 + schema; on a hit for a different Document, copy Page rows and passages to the new Document and remap fact passage ids; same Document reuses directly | document_id + schema only; a re-uploaded identical file is read again |
| S1 Add document | approved A (D2) | unchanged | unchanged |

Question D3:
D3 — Should the extraction cache also catch a re-uploaded identical file?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: You approved caching by "same file". Picking a document already in the app is easy: it is the same record, so its facts can be reused directly. But uploading the same PDF again makes a brand-new record (documents.py:87) with no pages or citation targets, so reusing facts needs a copy of the pages and citation targets onto the new record, otherwise citation chips would open the other copy.
Stakes if we pick wrong: B quietly pays for a re-read whenever a broker re-uploads; A done carelessly would open citations on the wrong document.
Recommendation: A because it is what C8 promised and the copy is a bounded, testable step.
Completeness: A=10/10, B=7/10
Pros / cons:
A) sha256 match with page and passage copy (recommended)
  ✅ Matches the owner's C8 promise: same bytes, no second read, wherever the file came from
  ✅ Citations open the broker's own upload, with highlights copied from the cached read
  ❌ One more copy step (pages, passages, fact remap) with its own tests for remapping
B) document_id only
  ✅ Simplest cache: no copying, citations already point at the right record
  ✅ Picking from the client's documents (the common path per C3) is still free
  ❌ Re-uploading an identical file is read and billed again, narrower than C8 promised
Net: A completes C8 for a bounded copy step; B ships less and re-reads re-uploads.
Header: Re-upload cache
Options:
A) sha256 + copy (recommended)
Match on sha256 + schema fingerprint; when the hit is a different Document, copy its Page rows and passages to the new Document and remap fact passage ids; tests cover remap and that chips open the new document. Human ~4h / CC ~20min.
B) document_id only
Cache only when the same Document is reused (picker path); an identical re-upload is read again. Spec C8 wording narrows to "same document". Human ~1h / CC ~5min.

State: approved
Actual answer: A) sha256 + copy (owner, D3, 2026-11-03)
Accepted scope: cache matches sha256 + schema fingerprint; a hit on a different Document copies its Page rows and passages to the new Document and remaps fact passage ids; same Document reuses directly; tests cover the remap and that chips open the new document.
History: none

### Section 1: Architecture

```
 wizard (Who, Policies, Read)
   │ POST /api/runs {workflow: certificate, slots.policies, parameters: holder, wording, date, description}
   ▼
 runner._begin ──► for each QUEUED run_document:
   │                 cache.lookup(sha256, schema_fp) ── hit same doc ──► copy facts
   │                                                 ── hit other doc ─► copy pages + passages, remap, copy facts
   │                                                 ── miss ──────────► windows.extract + verify (cert: fields)
   ▼
 _compose (no model call): certificate.build_content(facts, parameters)
   │   join endorsements to policies by normalized policy number
   │   coi_checks.run(...) ─► claim items (supported / not supported, reason, cited passages)
   ▼
 artifact version N (CertificateBlock + claim items) ──► reconcile_discrepancy_states (keeps decisions)
   │
   ├─ PATCH /discrepancies/{item}: resolve (note required = override) | removed
   ├─ Add document (D2): DONE/PARTIAL ─► QUEUED (certificate drafts only) ─► read new doc ─► version N+1
   └─ Issue (version N): no open items ─► render PDF (reportlab) ─► certificate_issue row + file, one transaction
                                           holder upsert by client_key
```

Design-twice: shape 1 (spec) adds a certificate branch next to `_Plan.policy_check: bool` (runner.py:254) and the
slug branches in `work_queue.classify` (work_queue.py:182-185); shape 2 replaces the boolean with a `kind`
(compare / proposal / policy_check / certificate) on `_Plan` dispatched once per stage. Shape 2 hides more behind
one field, but with four workflows the branches stay readable and changing runner dispatch is unrelated scope;
pick shape 1, and fix the one red flag it exposes (A1, hand-synced list). Red flags screened: shallow module no;
information leakage A1; temporal decomposition no; pass-through no; split ownership no (claim state has one owner
after D1); two ways to do one task no; importable internals no; hand-synced list A1.

Findings:
- [INFORMATIONAL] (confidence: 9/10) backend/work_queue.py:52 — `COMPARE_QUOTES_SLUG = "compare-quotes"  # db.COMPARE_QUOTES_SLUG (db imports models; keep this module light)`; slugs are copied by hand from db.py:23/254/320, and the certificate slug would be a fourth hand-synced pair (A1).
- [CRITICAL] (confidence: 8/10) spec section 2 step 5 "Issuing freezes values ..." — Issue does not name the version it freezes; with D2 a re-compose can create version N+1 while the broker clicks Issue on N, issuing values they never saw (A2).
- [INFORMATIONAL] (confidence: 7/10) backend/export.py:304 `value = escape(cell.value ...)` — reportlab Paragraph parses markup, so holder name/address and description of operations (free text) must be escaped and length-bounded; the spec bounds neither (A3).

### A1: One source for workflow slugs
Finding: A1, INFORMATIONAL, 9/10, backend/work_queue.py:52-54 vs backend/db.py:23,254,320; reviewer: main session.
Plan baseline: spec section 7 adds a `certificate` workflow (slug not yet named); no slug-sharing plan.
Runtime evidence: three slug constants duplicated in work_queue.py with a comment pointing at db.py.
Comparison grid:

| Choice | Current | A (shared module) | B (test pins equality) |
|---|---|---|---|
| A1 slug source | duplicated by hand | new import-free `backend/slugs.py`; db.py and work_queue.py import from it (certificate slug added once) | keep copies, add a unit test asserting work_queue slugs == db slugs (incl. certificate) |
| D1, D2, D3 | approved | unchanged | unchanged |

Question D4:
D4 — Where should the certificate workflow's slug live?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: Each workflow has a name string like "policy-check". It is typed twice today, in db.py and again in work_queue.py (work_queue.py:52-54), to avoid an import loop. The certificate workflow would make a fourth copied pair. If one copy is ever mistyped, certificate runs silently fall out of the queue.
Stakes if we pick wrong: a typo makes certificate drafts vanish from the queue with no error.
Recommendation: A because a tiny import-free module removes the copy instead of policing it.
Completeness: A=10/10, B=8/10
Pros / cons:
A) Shared slugs module (recommended)
  ✅ One definition; a typo is impossible because both modules import the same name
  ✅ No import loop: the module imports nothing, so work_queue stays light
  ❌ Touches three existing imports in db.py and work_queue.py (mechanical)
B) Keep copies, pin with a test
  ✅ No change to existing modules beyond adding the new constant twice
  ✅ A failing test catches drift before merge
  ❌ Still two places to edit for every new workflow; the test is the only guard
Net: A deletes the duplication; B detects it.
Header: Slug source
Options:
A) Shared slugs module (recommended)
New import-free backend/slugs.py holds all four workflow slugs; db.py and work_queue.py import them; the copies are deleted. Human ~30min / CC ~5min.
B) Keep copies + test
Add CERTIFICATE_SLUG in both files; a unit test asserts work_queue's slugs equal db's. Human ~20min / CC ~5min.

State: approved
Actual answer: A) Shared slugs module (owner, D4, 2026-11-03)
Accepted scope: new import-free backend/slugs.py holds the four workflow slugs (incl. certificate); db.py and work_queue.py import them; the copies are deleted.
History: none

### A2: Issue names the version it freezes
Finding: A2, CRITICAL, 8/10, spec section 2 step 5 "Issuing freezes values, override reasons, holder and PDF"; reviewer: main session.
Plan baseline: spec section 5 "Issue is atomic ... a second Issue on the same version returns the existing issue (idempotent)".
Runtime evidence: with D2, Add document re-composes and creates a new artifact version while the page may still show the old one; artifact edits also create versions (routes/artifacts.py:92). Issue as specified does not carry the version the broker saw.
Comparison grid:

| Choice | Current | A (version-checked issue) | B (issue latest) |
|---|---|---|---|
| A2 issue target | unspecified | POST issue carries `version`; server rejects 409 `version_changed` if it is not the current version, 409 `run_active` while the run is queued/running/composing, 409 `claims_open` if any claim item is open; same version twice returns the existing issue | server issues whatever version is current at request time |
| D1-D4 | approved | unchanged | unchanged |

Question D5:
D5 — Which version does "Issue certificate" freeze?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: A certificate draft can change after the broker last looked at it: adding a document (D2) or a cell edit makes a new version. If Issue just takes "whatever is current", a broker can issue values they never saw. Sending the version they are looking at lets the server refuse when it changed underneath them.
Stakes if we pick wrong: a legal document goes out with coverage text the broker never reviewed, the exact E&O risk this slice exists to prevent.
Recommendation: A because issuing is the one irreversible step and the check is a few lines.
Completeness: A=10/10, B=5/10
Pros / cons:
A) Version-checked issue (recommended)
  ✅ The issued PDF is exactly the version on the broker's screen, or the request fails with a clear reason
  ✅ Also blocks issuing while a document is still being read, or with a claim still open
  ❌ The UI must handle a 409 by reloading the draft and showing what changed
B) Issue the latest version
  ✅ Simplest request: no version parameter, no conflict handling in the UI
  ✅ Fine when only one tab and no background work exist
  ❌ A re-compose or edit between view and click issues unseen values silently
Net: A spends a 409 path to make the irreversible step exact.
Header: Issue version
Options:
A) Version-checked issue (recommended)
POST issue sends the viewed version; 409 version_changed / run_active / claims_open; same version twice returns the existing issue; UI reloads and explains on 409. Tests for each 409 and idempotency. Human ~3h / CC ~15min.
B) Issue latest
Server issues the current version at request time; no version check. Human ~1h / CC ~5min.

State: approved
Actual answer: A) Version-checked issue (owner, D5, 2026-11-03)
Accepted scope: POST issue carries the viewed version; 409 version_changed (not current), run_active (queued/running/composing), claims_open (any open claim item); same version twice returns the existing issue; UI reloads and explains on 409; tests for each 409 and idempotency.
History: none

### A3: Free text on the certificate is escaped and bounded
Finding: A3, INFORMATIONAL, 7/10, backend/export.py:304 `value = escape(cell.value if cell.value is not None else "Not specified")` (precedent: reportlab Paragraph text is escaped); spec section 2 step 1 holder name/address and description of operations are free text with no bound; reviewer: main session.
Plan baseline: spec section 4 "PDF: ... generated in code from the frozen values"; no escaping or length rule stated.
Runtime evidence: reportlab Paragraph parses inline markup; an unescaped "<" or "&" in a holder name breaks rendering, which under D5 fails the Issue transaction.
Comparison grid:

| Choice | Current | A (escape + bounds) | B (escape only) |
|---|---|---|---|
| A3 free-text handling | unspecified | escape all free text into the PDF; bounds: holder name 120, address 300, description 1,500 chars; 422 with the field named; PDF fits one page at the bounds (test) | escape only; no length bounds |
| D1-D5 | approved | unchanged | unchanged |

Question D6:
D6 — How is the broker's free text handled on the certificate?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: Holder name, address and description of operations are typed by the broker and printed on the PDF. The PDF library reads "<" and "&" as formatting, so a holder like "Smith & <Sons>" can crash the PDF at Issue time. Very long text can also push the one-page certificate onto two pages. The existing exports already escape text (export.py:304).
Stakes if we pick wrong: Issue fails on an ordinary company name, or the certificate silently spills onto a second page.
Recommendation: A because escaping is required anyway and bounds keep the one-page promise testable.
Completeness: A=10/10, B=7/10
Pros / cons:
A) Escape and bound (recommended)
  ✅ Any typed name renders; the error names the field when text is too long
  ✅ A test proves the certificate stays one page at the maximum lengths
  ❌ Brokers with a very long description must shorten it (1,500 chars)
B) Escape only
  ✅ No limits for the broker to hit
  ✅ Fixes the crash on "&" and "<"
  ❌ Long text can overflow the one-page layout with no warning
Net: A keeps both promises (renders, one page) for a length rule.
Header: Free text
Options:
A) Escape and bound (recommended)
Escape all free text into the PDF; holder name 120, address 300, description 1,500 chars, 422 naming the field; a test renders the maximums on one page. Human ~2h / CC ~10min.
B) Escape only
Escape free text; no length bounds. Human ~30min / CC ~5min.

State: approved
Actual answer: A) Escape and bound (owner, D6, 2026-11-03)
Accepted scope: escape all free text into the PDF; bounds holder name 120, address 300, description 1,500 chars with a 422 naming the field; a test renders the maximums on one page.
History: none

### Section 2: Code quality

Findings:
- [INFORMATIONAL] (confidence: 8/10) spec section 3 "Endorsements join their policy in code by policy number" — no normalization rule; carriers print "GL-1234567", "GL 1234567" and "GL1234567" for one policy, so an exact join marks real endorsements unmatched and their claims Not supported (Q1).
- [INFORMATIONAL] (confidence: 8/10) spec section 3 "same schema fingerprint (field set + prompt version)" — no prompt version exists today; extraction output also depends on the model (backend/providers/config.py:31 `CallRole.EXTRACT: "gpt-5.4-mini"`, env-overridable) and on verify/merge code, so a key of fields + prompt alone would serve stale facts after a model or pipeline change (Q3).
- [INFORMATIONAL] (confidence: 6/10) spec section 3 "Scheduled AI must name the holder (normalized compare)" — punctuation and suffix variants ("L.L.C." vs "LLC") are unspecified; the failure direction is safe (Not supported, broker overrides with a reason), so no change is proposed beyond reusing one normalizer with Q1 (Q2, no question).

Shared code: claim state reuses DiscrepancyState (D1); PDF reuses reportlab and the escape helper pattern (export.py:304); upload picker reuses the proposal wizard picker (sha256 dedupe in the UI). No other extraction is proposed.

### Q1: Policy-number normalization for the endorsement join
Finding: Q1, INFORMATIONAL, 8/10, spec section 3 endorsement join; reviewer: main session.
Plan baseline: spec section 3 "Endorsements join their policy in code by policy number" (exact match implied).
Runtime evidence: no join exists yet (proposed code); dev fixtures use "Policy No. LG-773595" style numbers (HANDOFF 2026-11-02 T8 entry).
Comparison grid:

| Choice | Current | A (normalized exact) | B (raw exact) |
|---|---|---|---|
| Q1 join key | raw string (implied) | uppercase, remove spaces, dashes, dots and slashes, then exact match; two policies with the same normalized number = notice, endorsement joins neither | raw string equality |
| D1-D6 | approved | unchanged | unchanged |

Question D7:
D7 — How are endorsements matched to their policy?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: An additional insured endorsement says which policy it changes, by number. The same number is often printed differently, "GL-1234567" on the declarations and "GL 1234567" on the endorsement. Matching the raw text would miss real endorsements and wrongly flag the claim.
Stakes if we pick wrong: brokers see false "No additional insured endorsement found" flags and learn to override, which defeats the check.
Recommendation: A because format noise is the common case and an ambiguous match still fails safe with a notice.
Completeness: A=9/10, B=6/10
Pros / cons:
A) Normalized exact match (recommended)
  ✅ "GL-1234567", "GL 1234567" and "gl1234567" join; table-driven tests pin each form
  ✅ Two policies colliding after normalization show a notice instead of guessing
  ❌ A real but different number that differs only by punctuation would wrongly join (very rare)
B) Raw exact match
  ✅ Never joins two different numbers
  ✅ Simplest code
  ❌ Common formatting differences produce false Not supported flags
Net: A removes the common false flag and keeps an explicit notice for collisions.
Header: Policy join
Options:
A) Normalized exact (recommended)
Uppercase and drop spaces, dashes, dots and slashes, then exact match; a normalized collision between two shown policies shows a notice and joins neither; table-driven unit tests. Human ~1h / CC ~5min.
B) Raw exact
Join only on identical text. Human ~15min / CC ~2min.

State: approved
Actual answer: A) Normalized exact (owner, D7, 2026-11-03)
Accepted scope: uppercase and drop spaces, dashes, dots and slashes, then exact match; a normalized collision between two shown policies shows a notice and joins neither; table-driven unit tests.
History: none

### Q3: What the extraction-cache key includes
Finding: Q3, INFORMATIONAL, 8/10, backend/providers/config.py:31 `CallRole.EXTRACT: "gpt-5.4-mini"` (env override BROKERDOCS_MODEL_<ROLE>, config.py:146) and spec section 3 "schema fingerprint (field set + prompt version)"; reviewer: main session.
Plan baseline: C8 + D3: cache on sha256 + schema version.
Runtime evidence: there is no prompt-version constant today; extraction results depend on field definitions, SYSTEM_PROMPT (backend/pipeline/extract.py:84), the extract model, and verify/merge code (changed repeatedly: T8 fixes, reading_text fix 810995f).
Comparison grid:

| Choice | Current | A (auto hash + manual version) | B (auto hash only) |
|---|---|---|---|
| Q3 cache key | "field set + prompt version" (undefined) | sha256 + hash(field definitions, extraction prompt text, extract model id) + `EXTRACTION_VERSION` constant; CLAUDE.md rule: bump it when verify, merge or windows behaviour changes; unit tests: each input change misses | sha256 + hash(field definitions, prompt text, model id) |
| D1-D7 | approved | unchanged | unchanged |

Question D8:
D8 — What must match before cached facts are reused?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: Reusing an earlier read is only safe if the earlier read would come out the same today. That depends on the fields asked for, the prompt wording, the model, and the code that checks and merges the answers. The first three can be hashed automatically; the checking code cannot, so a hand-bumped version number covers it.
Stakes if we pick wrong: after a pipeline fix (like the T8 merge fixes), old cached facts keep the bug that was fixed, and evals on cached documents look better or worse than the live code.
Recommendation: A because verify/merge fixes are frequent here and only a version number catches them.
Completeness: A=9/10, B=7/10
Pros / cons:
A) Auto hash plus a manual extraction version (recommended)
  ✅ Fields, prompt and model changes invalidate the cache automatically
  ✅ A verify/merge fix invalidates it by bumping one constant, noted in CLAUDE.md
  ❌ Relies on people remembering the bump for code changes
B) Auto hash only
  ✅ Nothing to remember; fully automatic
  ✅ Covers the most common change (fields and prompt)
  ❌ Verify/merge fixes never invalidate old cached facts
Net: A adds one remembered constant to cover the pipeline fixes B misses.
Header: Cache key
Options:
A) Auto hash + version (recommended)
Key = sha256 + hash(field definitions, extraction prompt text, extract model id) + EXTRACTION_VERSION constant; CLAUDE.md rule: bump it when verify, merge or windows behaviour changes; unit tests: each input change misses. Human ~1h / CC ~10min.
B) Auto hash only
Key = sha256 + hash(field definitions, prompt text, model id); pipeline code changes do not invalidate. Human ~45min / CC ~5min.

State: approved
Actual answer: A) Auto hash + version (owner, D8, 2026-11-03)
Accepted scope: cache key = sha256 + hash(field definitions, extraction prompt text, extract model id) + EXTRACTION_VERSION constant; CLAUDE.md rule: bump it when verify, merge or windows behaviour changes; unit tests: each input change misses.
History: none

### Section 3: Tests (regression contract first)

### T-R1: Regression contract for the all-workflow extraction cache
Finding: T-R1, CRITICAL, 8/10, backend/eval_hook.py:227 `engine = db.make_engine("sqlite://")` (evals are isolated per batch) vs backend/dev_seed.py seeding several runs from the same fixture files (runs 5 and 6 both Cedar Hollow; seeded failed/cancelled/budget/fallback/partial states asserted in frontend/e2e/journey.spec.ts:141); reviewer: main session.
Plan baseline: C8, D3, D8 approved (cache for every workflow, sha256 + key).
Runtime evidence: today every run reads every document; the seed's state scenarios rely on reads happening (budget exhaustion, fallback). With the cache, a later seed run on the same bytes could copy facts and skip the read that produces the seeded state.
Comparison grid:

| Choice | Current | A (contract + off switch) | B (contract + distinct seed bytes) |
|---|---|---|---|
| Preserve: evals always read live | true (fresh DB) | assert: eval batch has no reused documents | same |
| Preserve: only DONE documents are cache sources | n/a | assert: FAILED/CANCELLED/partial-budget documents never serve hits | same |
| Preserve: seeded states still appear | true | dev_seed sets `BROKERDOCS_EXTRACTION_CACHE=off` for its scenario runs; journey.spec.ts:141 unchanged and passing | dev_seed makes each scenario's fixture bytes unique (metadata marker), so hits never occur; journey unchanged |
| Preserve: hit equals live read | n/a | assert (fake provider): facts, states, page numbers and chip targets equal a live read of the same fixture, Compare and Policy Check | same |
| Intentional change | n/a | a second run on the same bytes + key makes 0 extract model calls for that document and shows `reused_from_run_id` | same |
| Off switch for debugging | none | env `BROKERDOCS_EXTRACTION_CACHE=off` exists | none |

Question D9:
D9 — How do we keep existing seed, e2e and eval behaviour intact once every workflow reuses earlier reads?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: Today every run reads every document. After this slice, a run on a file the app already read copies the earlier result. That is the point, but the dev seed deliberately builds failure, budget and partial states by running the same fixture files several times, and the e2e suite checks those states. Evals are safe because each eval batch starts from an empty database. We need a written contract of what must stay the same and how it is proven.
Stakes if we pick wrong: seeded failure states quietly disappear, e2e starts failing for reasons that look unrelated, or the demo stops showing honest failure states.
Recommendation: A because an off switch is also useful for debugging and keeps the seed scenarios explicit.
Completeness: A=10/10, B=9/10
Pros / cons:
A) Contract plus an off switch (recommended)
  ✅ Seed scenarios state plainly that they want a live read; journey states stay as they are
  ✅ The same switch lets anyone force a live read when debugging extraction
  ❌ One more environment setting to document in the README
B) Contract plus unique seed bytes
  ✅ No switch; the cache logic has no special case
  ✅ The seed behaves like any real user with different files
  ❌ Seed fixtures diverge from the eval fixtures byte for byte, and nobody can force a live read
Net: both keep the contract; A also gives a debugging control.
Header: Cache regressions
Options:
A) Contract + off switch (recommended)
Assertions: eval batch never reuses; only DONE documents serve hits; a hit equals a live read (Compare and Policy Check, fake provider); second run on same bytes makes 0 extract calls with reused_from_run_id. dev_seed runs its scenarios with BROKERDOCS_EXTRACTION_CACHE=off; journey.spec.ts:141 unchanged. Human ~3h / CC ~20min.
B) Contract + unique seed bytes
Same assertions; dev_seed stamps each scenario's fixture copy with a unique metadata marker so the cache misses; no off switch. Human ~3h / CC ~20min.

State: approved
Actual answer: A) Contract + off switch (owner, D9, 2026-11-03)
Accepted scope: assertions: eval batch never reuses; only DONE documents serve hits; a hit equals a live read (Compare and Policy Check, fake provider: facts, states, pages, chip targets); a second run on the same bytes + key makes 0 extract calls and shows reused_from_run_id. dev_seed runs its scenarios with BROKERDOCS_EXTRACTION_CACHE=off; journey.spec.ts:141 unchanged and passing; README documents the switch.
History: none

### T-R2: Regression contract for widening discrepancy_state (D1)
Finding: T-R2, CRITICAL, 8/10, backend/models.py:391 `CheckConstraint("status IN ('open', 'resolved')", name="ck_discrepancy_status")` and backend/routes/runs.py:313 `if "status" in body.model_fields_set: kwargs["status"] = body.status`; reviewer: main session.
Plan baseline: D1 approved: status widened to open/resolved/removed; certificate resolve requires a note.
Runtime evidence: Policy Check PATCH accepts any status the model allows; tests in backend/tests/test_discrepancy_state.py cover open/resolved and the fingerprint reset.
Comparison grid:

| Choice | Current | A (workflow-scoped rules, Policy Check pinned) | B (shared rules for both) |
|---|---|---|---|
| Policy Check PATCH status values | open, resolved | open, resolved; `removed` returns 422 for non-certificate runs | open, resolved, removed for both |
| Policy Check note | optional | optional (unchanged) | optional |
| Certificate resolve | n/a | note required (422 `note_required`), `removed` allowed | note required for certificate only |
| Counts and queue for Policy Check | open counts by severity | unchanged; removed items count as handled for certificates only | removed counts as handled everywhere |
| Proof | test_discrepancy_state.py | existing tests unchanged and passing; new rows: Policy Check `removed` 422, certificate resolve without note 422, certificate removed + fingerprint reset reopens | new rows for certificate only |

Question D10:
D10 — What must stay exactly the same for Policy Check when its decision table also stores certificate claims?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: You chose to store certificate claims in Policy Check's decision table (D1). That table now needs a third status, "removed", and certificates need a required reason. Policy Check should not suddenly allow "removed" or start demanding notes. We need to write down what stays the same and the tests that prove it.
Stakes if we pick wrong: Policy Check gains a hidden "removed" status that makes discrepancies vanish from counts, or brokers suddenly cannot resolve a discrepancy without typing a note.
Recommendation: A because it keeps Policy Check's behaviour byte-for-byte and puts the new rules behind the workflow.
Completeness: A=10/10, B=7/10
Pros / cons:
A) Rules scoped by workflow, Policy Check pinned (recommended)
  ✅ Policy Check accepts exactly what it accepts today; "removed" is rejected for it
  ✅ Existing test_discrepancy_state.py stays unchanged and must pass
  ❌ The PATCH handler gains a workflow check (a few lines, tested)
B) Shared rules for both workflows
  ✅ One rule set, no workflow check in the handler
  ✅ Certificates still require a note
  ❌ Policy Check can now mark items "removed", a behaviour nobody designed for it
Net: A spends one workflow check to keep Policy Check unchanged.
Header: Policy Check pin
Options:
A) Scoped, Policy Check pinned (recommended)
Policy Check: open/resolved only, note optional, removed = 422, counts unchanged; certificate: note required on resolve (422 note_required), removed allowed and counted as handled. Existing discrepancy tests unchanged and passing; new rows for each rule. Human ~2h / CC ~10min.
B) Shared rules
Both workflows accept open/resolved/removed; certificates require a note on resolve. Human ~1h / CC ~5min.

State: approved
Actual answer: A) Scoped, Policy Check pinned (owner, D10, 2026-11-03)
Accepted scope: Policy Check: open/resolved only, note optional, removed = 422, counts unchanged; certificate: note required on resolve (422 note_required), removed allowed and counted as handled. Existing discrepancy tests unchanged and passing; new rows for each rule.
History: none

#### Coverage diagram (all paths are proposed; GAP = test required by this plan)

```
CODE PATHS                                                   USER FLOWS
[+] backend/slugs.py (D4)                                    [+] New certificate (wizard)
  └── [GAP] db + work_queue import one slug set               ├── [GAP] [→E2E] Who → Policies → Read → draft
[+] cache (D3, D8, D9)                                         ├── [GAP] [→E2E] pick a past holder for the client
  ├── [GAP] hit same Document → facts copied, 0 extract calls  ├── [GAP] [→E2E] pick client documents; "read on <date>"
  ├── [GAP] hit other Document → pages+passages copied, remap  └── [GAP] 422 on over-long holder/description (D6)
  ├── [GAP] miss on field/prompt/model/EXTRACTION_VERSION    [+] Draft review
  ├── [GAP] FAILED/CANCELLED source never serves a hit         ├── [GAP] [→E2E] Remove claim → row Removed
  ├── [GAP] BROKERDOCS_EXTRACTION_CACHE=off reads live          ├── [GAP] [→E2E] Override → reason required → Overridden
  └── [GAP] hit equals live read (Compare, Policy Check)       ├── [GAP] [→E2E] Add document → reread → claim Supported (D2)
[+] runner: certificate add-document reopen (D2)               └── [GAP] chip opens the cited page (reused document too)
  ├── [GAP] DONE/PARTIAL → QUEUED for certificate drafts     [+] Issue
  ├── [GAP] other workflows DONE stays terminal (pinned)       ├── [GAP] [→E2E] Issue disabled with count until handled
  └── [GAP] issued version never reopens                       ├── [GAP] [→E2E] Issue → read-only → Download → New version
[+] certificate.build_content + coi_checks                     ├── [GAP] 409 version_changed reload + explain (D5)
  ├── [GAP] AI scheduled names holder / blanket form cited     └── [GAP] double click returns one issue
  ├── [GAP] waiver, primary non-contributory                 [+] Queue / Activity
  ├── [GAP] in force: effective day, expiration day, outside   ├── [GAP] [→E2E] queue row "Issue certificate" prefills
  ├── [GAP] insured mismatch; unverified/conflicting = Not supported └── [GAP] certificate drafts only on Certificates page
  ├── [GAP] policy join normalized; collision notice (D7)
  ├── [GAP] package policy notice, no facts
  └── [GAP] limits unverified print "Not stated"
[+] claim state via discrepancy_state (D1, D10)
  ├── [★★★ TESTED] Policy Check open/resolved/reset — backend/tests/test_discrepancy_state.py (must stay unchanged)
  ├── [GAP] Policy Check removed → 422
  ├── [GAP] certificate resolve without note → 422 note_required
  └── [GAP] certificate removed/overridden reopen on fingerprint change
[+] issue (D5, D6)
  ├── [GAP] 409 version_changed / run_active / claims_open
  ├── [GAP] idempotent same version; PDF + row one transaction (failure leaves neither)
  ├── [GAP] escape "&", "<" in every free-text field; one page at max lengths
  └── [GAP] holder upsert by client_key
[+] schemas: CertificateBlock in Block union
  └── [GAP] passage_ids includes certificate citations (prior learning brokerdocs-block-union-traversal)

LLM integration: [GAP] [→EVAL] cert: extraction fields — live gate `-k certificate`: 0 hard fails, recall on
not-supported claims >= 0.90, field accuracy per field; offline eval on the new fixtures.

COVERAGE: 1/45 paths tested (2%) | Code paths: 1/31 | User flows: 0/14
QUALITY: ★★★:1 | GAPS: 44 (13 E2E, 1 eval) — all are new-code proposals, required by the approved decisions.
```

Tests made obsolete by this plan: none. `journey.spec.ts:141` and `test_discrepancy_state.py` must stay unchanged (D9, D10).

### Section 4: Performance
- Cache lookup: one indexed query per document (`document.sha256` is indexed, backend/models.py:115) joined to a new
  `run_document.extraction_key` column (index on it; necessary implementation of D3/D8, no separate choice).
- Page copy on a cross-document hit: up to 300 Page rows with word boxes (T14 ceiling) in one transaction per
  document; scale ~300 rows, a few MB of JSON [estimated]; acceptable, runs inside the document task.
- Holder picker: query by `client_key`; index on (workspace_id, client_key) as part of the approved table.
- Queue: certificate counts reuse batched `discrepancy_counts` (backend/models.py:738), no N+1.
- PDF render: one page with reportlab inside the issue request; bounded by D6 lengths.
No findings needing a decision.

### Outside voice (Codex, gpt-6-astra, completed)
Seven findings (5 High, 2 Medium), quoted in full in the session; verified by the parent: edit_cell keeps evidence
state (backend/schemas.py:672-686, `cell.passage_ids = []`, no state change) and DiscrepancyPatch takes no version or
fingerprint (backend/routes/runs.py:279-300). Each goes through the decision procedure below (O1 to O7).

### O2: Free text in description of operations
Finding: O2, High (Codex), confidence 8/10, spec section 2 step 1 "optional description of operations (free text)" printed on the PDF (section 4) with no claim; reviewer: Codex outside voice, verified by parent.
Plan baseline: C1 checked certificate; D6 escape + bounds only.
Runtime evidence: proposed; the description has no claim row, so text such as "Holder is additional insured" would print unchecked.
Comparison grid:

| Choice | Current | A Apply | B Keep | C Investigate | D Defer |
|---|---|---|---|---|---|
| O2 free-text check | prints unchecked | description becomes a claim row "Description of operations reviewed": always open until the broker confirms it (resolve with a note); its text is shown beside the coverage claims; Issue waits on it like any claim | unchanged | bounded: list wording patterns brokers use; no change now | unresolved |

Question D11:
D11 — Should the broker's free-text "description of operations" be checked before issue?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: The certificate checks every box the broker ticks, but the free-text description prints as typed. A broker could leave "additional insured" unticked and write "Holder is named additional insured" in the description. It would print with no endorsement check, no citation and no reason recorded.
Stakes if we pick wrong: the exact E&O risk the slice exists to catch slips through the one field nobody checks.
Recommendation: A because a required sign-off row is cheap and keeps "nothing unchecked reaches issue" true.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Apply: description is a sign-off claim (recommended)
  ✅ Nothing typed reaches the PDF without a recorded broker sign-off and reason
  ✅ Reuses the claim row, note and Issue gate already approved (D1, D5)
  ❌ One more click on every certificate that has a description
B) Keep: print as typed
  ✅ No extra step for the broker
  ✅ Matches how ACORD 25's remarks box usually works
  ❌ Unchecked coverage language can be issued with no record
C) Investigate before choosing
  ✅ Could find a narrower rule (flag only coverage words)
  ✅ No build cost now
  ❌ Leaves the hole open until decided
D) Defer this change only
  ✅ Ships the rest unchanged
  ✅ Revisit after the first build
  ❌ Same hole as B in the meantime
Net: A turns the one unchecked field into a recorded sign-off.
Header: Free-text check
Options:
A) Apply this change (recommended)
Description of operations becomes a claim row that is open until the broker confirms it with a note; Issue waits on it (D5 claims_open). E2E covers confirm and the blocked Issue. Human ~2h / CC ~10min.
B) Keep current value
Description prints as typed with no claim row.
C) Investigate before choosing
Bounded: list common description wording and whether a keyword flag would suffice; no change now; O2 stays pending.
D) Defer this change only
O2 stays unresolved; recorded under Unresolved decisions.

State: approved
Actual answer: A) Apply this change (owner, D11, 2026-11-03)
Accepted scope: description of operations becomes a claim row open until the broker confirms it with a note; Issue waits on it (D5 claims_open); e2e covers confirm and the blocked Issue.
History: none

### O4: Edited values on a certificate
Finding: O4, High (Codex), confidence 9/10, backend/schemas.py:680-686 `cell.value = value` / `cell.passage_ids = []` / `cell.edited = True` (evidence state untouched); spec section 3 "Limits print only when verified"; reviewer: Codex, verified by parent.
Plan baseline: spec section 3: draft = artifact version, so edits reuse the artifact machinery; limits are not claim rows.
Runtime evidence: an edited cell keeps its previous evidence state and loses its citations; nothing in the plan re-checks an edited printed value.
Comparison grid:

| Choice | Current | A Apply | B Keep | C Investigate | D Defer |
|---|---|---|---|---|---|
| O4 edited printed values | edit keeps state, no check | on a certificate, every edited printed field adds a claim row "Edited by broker: <field>" open until confirmed with a note; edit_cell itself is unchanged for other workflows (Compare/Proposal behaviour pinned) | unchanged | bounded: decide whether certificate fields should be editable at all; no change now | unresolved |
| O2 description | approved D11 | unchanged | unchanged | unchanged | unchanged |

Question D12:
D12 — What happens when a broker edits a value printed on the certificate?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: The draft certificate is an editable report like Compare Quotes. Editing a cell today removes its citations but keeps whatever "verified" state it had (schemas.py:680-686). So a broker could raise a $1M limit to $2M and the certificate would still look verified, with no check row to stop Issue.
Stakes if we pick wrong: a hand-edited limit is issued looking like a verified, cited value.
Recommendation: A because it matches D11 (a typed value needs a recorded sign-off) and leaves other workflows untouched.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Apply: edits become sign-off claims (recommended)
  ✅ Every hand-typed printed value carries a recorded reason before issue
  ✅ Compare and Proposal edit behaviour stays exactly as today
  ❌ The check list grows by one row per edited field
B) Keep: edit as today
  ✅ No new rows
  ✅ Same edit behaviour across workflows
  ❌ Edited values can be issued looking verified
C) Investigate before choosing
  ✅ Might conclude fields should not be editable at all
  ✅ No build cost now
  ❌ Leaves the gap open until decided
D) Defer this change only
  ✅ Ships the rest unchanged
  ✅ Revisit after build
  ❌ Same gap as B meanwhile
Net: A extends D11's sign-off rule to edited values.
Header: Edited values
Options:
A) Apply this change (recommended)
On certificate runs, each edited printed field adds a claim row open until confirmed with a note; Issue waits on it; edit_cell unchanged for other workflows (pinned by existing tests). Human ~2h / CC ~10min.
B) Keep current value
Edits behave as today; no claim row.
C) Investigate before choosing
Bounded: decide whether certificate fields are editable at all; no change now; O4 stays pending.
D) Defer this change only
O4 stays unresolved; recorded under Unresolved decisions.

State: approved
Actual answer: A) Apply this change (owner, D12, 2026-11-03)
Accepted scope: on certificate runs, each edited printed field adds a claim row open until confirmed with a note; Issue waits on it; edit_cell unchanged for other workflows (pinned by existing tests).
History: none

### O3: What "Remove from certificate" removes
Finding: O3, High (Codex), confidence 8/10, spec section 2 step 4 "Remove from certificate" and D1 ("Remove sets `removed`") define a status, not a content change; reviewer: Codex, verified by parent (no content mutation specified anywhere in the spec).
Plan baseline: C4 (fix, remove, or override with reason); D1 status `removed`.
Runtime evidence: proposed; as written, `removed` silences a failing row while the asserted text (an expired policy's coverage row, the AI wording) can still print, and an all-removed or package-only certificate has an empty check list that permits Issue.
Comparison grid:

| Choice | Current | A Apply | B Keep | C Investigate | D Defer |
|---|---|---|---|---|---|
| O3 remove semantics | status only | Remove deletes what the claim asserts, as a new artifact version: requested wording claim → that wording leaves the certificate; in-force failure → that policy's coverage row leaves; insured mismatch → cannot be removed (override or remove the mismatching policy's row); checks recompute; Issue also requires at least one coverage row whose policy is in force (409 `no_coverage`) | unchanged | bounded: map each claim type to its content effect; no change now | unresolved |
| D11, D12 sign-off rows | approved | Remove on a description row clears the description; on an edited-value row restores the extracted value | unchanged | unchanged | unchanged |

Question D13:
D13 — What does "Remove from certificate" actually remove?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: "Remove" was meant to mean "take this claim off the certificate". As written it only marks the check row as removed, so an expired policy's coverage line or the "additional insured" wording could still print. And if everything is removed, an empty certificate could be issued.
Stakes if we pick wrong: "removed" becomes a way to silence a failed check while the false statement still prints.
Recommendation: A because a remove that changes the certificate is the only reading that keeps the check honest.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Apply: remove changes the certificate (recommended)
  ✅ What the check list says was removed is gone from the PDF, as a new version
  ✅ An empty certificate cannot be issued (at least one in-force coverage row)
  ❌ Each claim type needs its own content effect and tests
B) Keep: status only
  ✅ Simplest, no content changes
  ✅ Already modelled by D1
  ❌ Removed claims can still print
C) Investigate before choosing
  ✅ Maps each claim type before committing
  ✅ No build cost now
  ❌ Issue gate stays bypassable until decided
D) Defer this change only
  ✅ Ships the rest unchanged
  ✅ Revisit after build
  ❌ Same bypass as B meanwhile
Net: A makes "removed" mean removed.
Header: Remove effect
Options:
A) Apply this change (recommended)
Remove creates a new version without the asserted content (wording, coverage row; description cleared; edited value restored); insured mismatch can only be overridden or fixed by removing the mismatching policy's row; Issue requires one in-force coverage row (409 no_coverage); unit tests per claim type, e2e for remove. Human ~4h / CC ~20min.
B) Keep current value
Remove sets the status only.
C) Investigate before choosing
Bounded: map each claim type to its content effect; no change now; O3 stays pending.
D) Defer this change only
O3 stays unresolved; recorded under Unresolved decisions.

State: approved
Actual answer: A) Apply this change (owner, D13, 2026-11-03)
Accepted scope: Remove creates a new version without the asserted content (wording, coverage row; description cleared; edited value restored); insured mismatch can only be overridden or fixed by removing the mismatching policy's row; Issue requires one in-force coverage row (409 no_coverage); unit tests per claim type, e2e for remove.
History: none

### O1: Endorsement conditions and per-policy claims
Finding: O1, High (Codex), confidence 8/10, spec section 3 Checks "blanket passes with its form cited"; extraction schema (section 3) has no field for an endorsement's condition; reviewer: Codex, verified by parent against the spec text.
Plan baseline: spec section 3 checks; C1.
Runtime evidence: proposed; blanket AI endorsements commonly apply "where required by written contract", a condition the documents cannot prove; the spec also treats a requested wording as one claim across the certificate.
Comparison grid:

| Choice | Current | A Apply | B Keep | C Investigate | D Defer |
|---|---|---|---|---|---|
| O1 condition | form cited = Supported | extract an endorsement `condition` (quoted text, e.g. "where required by written contract"); a conditional endorsement makes the claim Not supported with reason "Applies only when <condition>; confirm and give the reason", so the broker must override with a reason (e.g. "contract on file") | unchanged | bounded: collect condition wording from sample endorsements; no change now | unresolved |
| O1 scope | one claim per wording | one claim per wording per shown policy line it is printed on (e.g. AI on GL and on auto are two rows) | unchanged | unchanged | unresolved |

Question D14:
D14 — When does an additional insured or waiver endorsement count as support?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: Many blanket endorsements say the holder is covered only "where required by a written contract". The documents can show the endorsement exists, but not that a contract exists, so the claim is only conditionally true. Also, additional insured on the general liability policy says nothing about the auto policy, yet the certificate may print it for both.
Stakes if we pick wrong: the certificate states unconditional coverage that only exists if a contract the broker never saw is in place.
Recommendation: A because the condition is quoted from the cited page and the broker records why it holds.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Apply: conditional endorsements need a reason, claims per policy (recommended)
  ✅ The condition is shown with its citation; the broker's reason is recorded on issue
  ✅ Each policy line printed with AI or waiver has its own check
  ❌ More check rows and one more extraction field (fits the existing combined pass)
B) Keep: form cited is enough
  ✅ Fewer rows; simplest checks
  ✅ Matches common broker practice of trusting blanket forms
  ❌ Conditional coverage is certified as unconditional
C) Investigate before choosing
  ✅ Grounds the condition field in real wording first
  ✅ No build cost now
  ❌ Checks stay too permissive until decided
D) Defer this change only
  ✅ Ships the rest unchanged
  ✅ Revisit after the first eval
  ❌ Same exposure as B meanwhile
Net: A turns "endorsement exists" into "endorsement applies, and here is why".
Header: Endorsement rule
Options:
A) Apply this change (recommended)
Extract an endorsement condition (quoted); a conditional endorsement makes the claim Not supported with a reason prompt so the broker overrides with a recorded reason; one claim per wording per printed policy line; fixtures add a "written contract" blanket form; unit + eval cases. Human ~4h / CC ~20min.
B) Keep current value
A cited blanket form is Supported; one claim per wording.
C) Investigate before choosing
Bounded: collect condition wording from sample endorsements; no change now; O1 stays pending.
D) Defer this change only
O1 stays unresolved; recorded under Unresolved decisions.

State: approved
Actual answer: A) Apply this change (owner, D14, 2026-11-03)
Accepted scope: extract an endorsement condition (quoted); a conditional endorsement makes the claim Not supported with a reason prompt so the broker overrides with a recorded reason; one claim per wording per printed policy line; fixtures add a "written contract" blanket form; unit + eval cases.
History: none

### O5: Stale Remove/Override
Finding: O5, High (Codex), confidence 9/10, backend/routes/runs.py:279-300 `class DiscrepancyPatch` has only `status` and `note` (no version or fingerprint); reviewer: Codex, verified by parent.
Plan baseline: D1 reuses this PATCH; D5 protects Issue only; D10 pins Policy Check behaviour.
Runtime evidence: a PATCH resolves the item's current fingerprint, whatever the broker last saw.
Comparison grid:

| Choice | Current | A Apply | B Keep | C Investigate | D Defer |
|---|---|---|---|---|---|
| O5 stale decision | PATCH applies to current values | PATCH accepts optional `expected_fingerprint`; required for certificate runs (422 if missing), 409 `item_changed` on mismatch with the current item returned; Policy Check may omit it (D10 behaviour unchanged) | unchanged | bounded: measure how often re-compose happens during review; no change now | unresolved |

Question D15:
D15 — Should Remove and Override refuse to apply if the claim changed since the broker looked?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: You protected Issue against a changed draft (D5). Overrides have the same hole: if a new document lands in another tab and the claim's evidence changes, clicking Override on the old screen would sign off evidence the broker never saw. Sending the claim's fingerprint lets the server refuse and show the new evidence.
Stakes if we pick wrong: the broker's recorded reason is attached to evidence they did not review, and the issued record says otherwise.
Recommendation: A because it is the same guard as D5, applied to the decisions that unlock Issue.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Apply: fingerprint-checked decisions on certificates (recommended)
  ✅ A decision always applies to the evidence on the broker's screen
  ✅ Policy Check keeps working unchanged because the field is optional there
  ❌ The UI handles one more 409 by refreshing the row
B) Keep: decisions apply to current values
  ✅ No new field or error path
  ✅ Fine for single-tab use
  ❌ A stale override can unlock Issue on unseen evidence
C) Investigate before choosing
  ✅ Measures whether the race happens in practice
  ✅ No build cost now
  ❌ The hole stays open until decided
D) Defer this change only
  ✅ Ships the rest unchanged
  ✅ Revisit after build
  ❌ Same hole as B meanwhile
Net: A extends D5's guard to the decisions that unlock Issue.
Header: Stale decisions
Options:
A) Apply this change (recommended)
PATCH discrepancies takes expected_fingerprint; required on certificate runs (422 if missing), 409 item_changed with the current item on mismatch; optional for Policy Check (unchanged); tests for mismatch, missing, and Policy Check without it. Human ~2h / CC ~10min.
B) Keep current value
PATCH applies to current values; no fingerprint.
C) Investigate before choosing
Bounded: measure how often a re-compose lands during review; no change now; O5 stays pending.
D) Defer this change only
O5 stays unresolved; recorded under Unresolved decisions.

State: approved
Actual answer: A) Apply this change (owner, D15, 2026-11-03)
Accepted scope: PATCH discrepancies takes expected_fingerprint; required on certificate runs (422 if missing), 409 item_changed with the current item on mismatch; optional for Policy Check (unchanged); tests for mismatch, missing, and Policy Check without it.
History: none

### O7: Where the issued PDF lives
Finding: O7, Medium (Codex), confidence 9/10, spec section 5 "PDF + record in one transaction, PDF written to a temp path then moved" and section 3 `certificate_issue.pdf path`; a filesystem rename is outside the SQLite transaction; reviewer: Codex, verified by parent.
Plan baseline: spec section 5 atomic issue; D5 idempotent issue.
Runtime evidence: proposed; commit-then-move can leave a record with no file; move-then-commit can leave an orphan file after rollback.
Comparison grid:

| Choice | Current | A Apply | B Keep | C Investigate | D Defer |
|---|---|---|---|---|---|
| O7 PDF storage | file path + rename | `certificate_issue.pdf_bytes` (BLOB) written in the same transaction as the record; Download streams the bytes; a one-page certificate is tens of KB [estimated] | unchanged | bounded: specify a file publication + recovery protocol; no change now | unresolved |

Question D16:
D16 — Where is the issued certificate PDF stored?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: The spec promises Issue writes the record and the PDF together or not at all. A file on disk cannot be part of a database transaction, so a crash can leave a record without its PDF or a PDF without its record. A one-page certificate is small, so it can live inside the database row itself.
Stakes if we pick wrong: an issued certificate whose PDF is missing, which is a legal record with no document.
Recommendation: A because it makes the atomic promise true with less code than a recovery protocol.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Apply: PDF bytes in the issue row (recommended)
  ✅ Record and PDF commit or roll back together, exactly as promised
  ✅ No orphan files, no recovery code, idempotency stays a unique key
  ❌ Database grows by tens of KB per issued certificate
B) Keep: file plus path
  ✅ Database stays small
  ✅ Same storage pattern as uploads
  ❌ Atomicity is not true; needs recovery rules nobody has specified
C) Investigate before choosing
  ✅ Could design a proper publication protocol
  ✅ No build cost now
  ❌ The promise stays false until decided
D) Defer this change only
  ✅ Ships the rest unchanged
  ✅ Revisit later
  ❌ Same risk as B meanwhile
Net: A makes "atomic issue" true by storing a small file in the row.
Header: PDF storage
Options:
A) Apply this change (recommended)
certificate_issue stores pdf_bytes in the same transaction as the record; Download streams them; test: a render failure leaves no record; test: idempotent re-issue returns the same bytes. Human ~1h / CC ~5min.
B) Keep current value
PDF file on disk with a path in the row; temp path then move.
C) Investigate before choosing
Bounded: specify a file publication and crash-recovery protocol; no change now; O7 stays pending.
D) Defer this change only
O7 stays unresolved; recorded under Unresolved decisions.

State: approved
Actual answer: A) Apply this change (owner, D16, 2026-11-03)
Accepted scope: certificate_issue stores pdf_bytes in the same transaction as the record; Download streams them; test: a render failure leaves no record; test: idempotent re-issue returns the same bytes.
History: none

### O6: OCR provenance in the cache
Finding: O6, Medium (Codex), confidence 7/10, backend/pipeline/runner.py:976-983 `_cached_pages` returns stored page text and `ocr_text` per Document with no record of which vision model or OCR prompt produced them; D8 key covers the extract model only; reviewer: Codex, verified by parent.
Plan baseline: D3 copies Page rows (incl. OCR text) across documents; D8 key = sha256 + hash(fields, extraction prompt, extract model) + EXTRACTION_VERSION.
Runtime evidence: page/OCR reuse per Document is pre-existing behaviour (not introduced by this plan); D3 extends it across documents.
Comparison grid:

| Choice | Current | A Apply | B Keep | C Investigate | D Defer |
|---|---|---|---|---|---|
| O6 key inputs | D8 key (extract side only) | D8 key also hashes the vision model id and OCR prompt text | unchanged | bounded: decide whether page caching needs its own key; no change now | unresolved |
| O6 page copy | D3 copies pages incl. OCR | D3 copies OCR text only when the source pages' OCR key matches (stored as `page.ocr_key`); otherwise copies text layer only and OCR runs again | unchanged | unchanged | unresolved |
| Pre-existing same-Document page cache | never invalidated | unchanged (out of scope; TODO candidate) | unchanged | unchanged | unchanged |

Question D17:
D17 — Should the cache also notice a change in how scanned pages are read (OCR)?
Project/branch/task: broker-docs-verified, main, eng review of the slice 4 certificates spec.
ELI10: Scanned pages are turned into text by a vision model before extraction. The approved cache key (D8) notices changes to the extraction model and prompt, but not to the OCR model or prompt. If OCR is improved, a cached scanned document would keep its old, worse text and old facts.
Stakes if we pick wrong: after an OCR fix, scanned certificates keep the old misreads; evals on scanned fixtures stop reflecting the fix.
Recommendation: A because adding two inputs to the key and one stamp on copied pages closes it cheaply.
Note: options differ in kind, not coverage — no completeness score.
Pros / cons:
A) Apply: OCR inputs in the key, stamped pages (recommended)
  ✅ An OCR model or prompt change re-reads scanned documents automatically
  ✅ Copied pages carry OCR only when it was produced the same way
  ❌ One more column on page rows and two more key inputs to test
B) Keep: extraction side only
  ✅ Smaller key, no page column
  ✅ Text-layer PDFs are unaffected either way
  ❌ OCR fixes do not reach cached scanned documents
C) Investigate before choosing
  ✅ Could fold page caching into one design
  ✅ No build cost now
  ❌ Stale OCR stays possible until decided
D) Defer this change only
  ✅ Ships the rest unchanged
  ✅ Revisit when OCR changes
  ❌ Same staleness as B meanwhile
Net: A extends the approved key to the OCR stage for one column and two inputs.
Header: OCR in cache
Options:
A) Apply this change (recommended)
D8 key adds the vision model id and OCR prompt hash; copied pages carry OCR text only when page.ocr_key matches, else OCR reruns; the pre-existing same-document page cache is unchanged (TODO candidate). Tests: OCR input change misses; mismatched ocr_key re-OCRs. Human ~2h / CC ~10min.
B) Keep current value
Key covers the extraction side only; copied pages keep their OCR text.
C) Investigate before choosing
Bounded: decide whether page caching needs its own key; no change now; O6 stays pending.
D) Defer this change only
O6 stays unresolved; recorded under Unresolved decisions.

State: approved
Actual answer: A) Apply this change (owner, D17, 2026-11-03)
Accepted scope: D8 key adds the vision model id and OCR prompt hash; copied pages carry OCR text only when page.ocr_key matches, else OCR reruns; the pre-existing same-document page cache is unchanged (TODO candidate). Tests: OCR input change misses; mismatched ocr_key re-OCRs.
History: none

### TODO decisions
- D18 (owner, 2026-11-03): A) add "Re-OCR a reused document when the OCR method changes" to TODOS.md. Done.

Approval readiness: PASS — S1 (D2), S2 (D3), A1 (D4), A2 (D5), A3 (D6), Q1 (D7), Q3 (D8), T-R1 (D9), T-R2 (D10), O2 (D11), O4 (D12), O3 (D13), O1 (D14), O5 (D15), O7 (D16), O6 (D17), structure (D1), TODO (D18); each cites its actual answer. Q2 needs no change (fails safe).

## Review outputs

### NOT in scope
- Email sending, batch certificates, expiry tracking/reminders: owner scope (section 1).
- ACORD 25 and other ACORD forms: no licensed blank (C6); the field map keeps this open.
- Holder contract requirement gap list: owner chose checked certificate, not requirements (C1).
- Package policies (several lines in one document): flagged unsupported (C7).
- Same-document OCR re-read: TODOS.md "Re-OCR a reused document when the OCR method changes" (D18).
- Runner dispatch refactor (`kind` on `_Plan`): not needed for four workflows (design-twice, Section 1).

### What already exists (reused, not rebuilt)
- Read, verify, merge per document: backend/pipeline/runner.py:944-951, windows.py.
- Claim state, reset rule and counts: DiscrepancyState + reconcile + discrepancy_counts (models.py:374, 694, 738), per D1/D10.
- Artifact versions, cell edits and undo: models.py:357, routes/artifacts.py:92-107 (edits on certificates add sign-off rows per D12).
- PDF rendering and escaping: reportlab and escape in export.py:304-321.
- Document picker deduped by sha256 in the proposal wizard (UI).
- Fail-fast schema check: db.py:89. Queue grouping and batched counts: work_queue.py, models.py:738.

### Failure modes
| Path | Realistic failure | Test / handling | User sees |
|---|---|---|---|
| Cache cross-document copy | passages remapped to the wrong document | D3 tests: chips open the new document | correct page, or a test failure before merge |
| Cache key | model or OCR change serves stale facts | D8/D17 tests: each input change misses | fresh read |
| Seed scenarios | cache hit hides a seeded failure state | D9: dev_seed off switch, journey.spec.ts:141 | seeded states unchanged |
| Add document reopen | another workflow's DONE run reopened | D2 guard tests | 409/422, nothing changes |
| Issue | draft changed after viewing | D5 409 version_changed, UI reloads | clear reason, no issue |
| Issue | PDF render fails | D16 one transaction; test leaves no record | error toast, Issue still available |
| Override | stale evidence | D15 409 item_changed, row refreshes | updated row, decision not applied |
| Remove | assertion still printed | D13 content effect tests | removed text gone from the draft |
| Free text / edits | unchecked wording issued | D11/D12 sign-off rows block Issue | open row until confirmed with a reason |
| Conditional endorsement | certified as unconditional | D14 Not supported with reason prompt | open row quoting the condition |

Critical gaps (no test, no handling, silent): 0.

### Worktree parallelization strategy
| Step | Modules touched | Depends on |
|---|---|---|
| Backend core (C1 to C9) | backend/ (models, db, pipeline, routes, work_queue, export) | none |
| Fixtures + eval (C10) | tests/fixtures/certificates, tests/eval | none for generator and truth; backend core for the live gate |
| UI (C11) | frontend/ | Backend core (API) |
| Docs (C12) | backend/API.md, docs/, CLAUDE.md, README | Backend core |

Lane A: backend core (sequential, shared models/runner). Lane B: fixtures + truth sets (independent).
Execution order: launch A + B. Merge A, then run B's offline and live gates on main. Then launch UI (C11). Docs last.
Conflict flags: B owns tests/fixtures/certificates and the gate file; A must not edit them.

## Implementation Tasks
Synthesized from this review's findings. Each task derives from a specific finding above.

- [x] **C1 (P1, human: ~1h / CC: ~10min)** slugs: shared slugs module and certificate workflow seed
  - Surfaced by: Architecture A1 (D4)
  - Files: backend/slugs.py, backend/db.py, backend/work_queue.py
  - Verify: backend tests; work_queue and db import one slug set
- [x] **C2 (P1, human: ~1.5d / CC: ~1h)** cache: key, cross-document copy, OCR stamp, off switch
  - Surfaced by: S2 (D3), Q3 (D8), O6 (D17), T-R1 (D9)
  - Files: backend/models.py (run_document.extraction_key, reused_from_run_id, page.ocr_key), backend/pipeline/runner.py, backend/pipeline/cache.py
  - Verify: hit/miss per key input, remap chips, DONE-only sources, BROKERDOCS_EXTRACTION_CACHE=off, hit equals live read
- [x] **C3 (P1, human: ~1d / CC: ~40min)** extract: cert: schema with endorsement condition, package notice
  - Surfaced by: spec section 3, O1 (D14)
  - Files: backend/schemas.py, backend/pipeline/extract.py (fields)
  - Verify: offline extraction on fixtures; package policy contributes no facts
- [x] **C4 (P1, human: ~2d / CC: ~1h)** checks: certificate.build_content + coi_checks, CertificateBlock in the Block union
  - Surfaced by: Q1 (D7), O1 (D14), O2 (D11), O4 (D12), prior learning brokerdocs-block-union-traversal
  - Files: backend/pipeline/certificate.py, backend/pipeline/coi_checks.py, backend/schemas.py
  - Verify: table-driven tests per claim type; passage_ids includes certificate citations
- [x] **C5 (P1, human: ~1d / CC: ~40min)** state: widen discrepancy_state, workflow-scoped PATCH, expected_fingerprint, Remove effects
  - Surfaced by: D1, T-R2 (D10), O5 (D15), O3 (D13)
  - Files: backend/models.py, backend/routes/runs.py
  - Verify: test_discrepancy_state.py unchanged and passing; new rows per rule
- [x] **C6 (P1, human: ~4h / CC: ~20min)** runner: Add document reopens certificate drafts only
  - Surfaced by: S1 (D2)
  - Files: backend/models.py (_RUN_TRANSITIONS guard), backend/routes/runs.py, backend/pipeline/runner.py
  - Verify: guard tests (other workflows terminal; issued never reopens); decisions carried
- [x] **C7 (P1, human: ~1d / CC: ~40min)** issue: holder table/API, version-checked issue, PDF bytes, bounds
  - Surfaced by: A2 (D5), A3 (D6), O7 (D16), O3 (D13 no_coverage)
  - Files: backend/models.py, backend/routes/certificates.py, backend/export_certificate.py
  - Verify: each 409, idempotency, escape and one page at max, render failure leaves no record
- [x] **C8 (P1, human: ~3h / CC: ~15min)** queue: certificate classification on the Certificates page; Issue certificate row action
  - Surfaced by: spec section 2 step 6
  - Files: backend/work_queue.py, backend/routes
  - Verify: certificate drafts absent from the main queue; present on the Certificates queue
- [x] **C9 (P1, human: ~3h / CC: ~20min)** regression: D9/D10 contracts and dev_seed off switch
  - Surfaced by: T-R1 (D9), T-R2 (D10)
  - Files: backend/dev_seed.py, backend/tests
  - Verify: journey.spec.ts:141 unchanged and passing; eval batch never reuses
- [x] **C10 (P1, human: ~2d / CC: ~1h)** eval: synthetic fixtures (incl. written-contract blanket form) and gate
  - Surfaced by: spec section 6, O1 (D14)
  - Files: tests/fixtures/certificates, tests/eval
  - Verify: offline gate; live `-k certificate`: 0 hard fails, recall >= 0.90
- [x] **C11 (P2, human: ~3d / CC: ~2h)** frontend: Certificates page, wizard, draft, issue (design stack)
  - Surfaced by: spec section 4; D5, D11, D12, D13, D15 UI handling
  - Files: frontend/src/certificates/*
  - Verify: e2e per the Test Plan; axe 0 at 1440/1024/390
- [x] **C12 (P2, human: ~3h / CC: ~15min)** docs: API.md, spec amendments, CLAUDE.md EXTRACTION_VERSION rule, README switch
  - Surfaced by: D8, D9
  - Files: backend/API.md, CLAUDE.md, README
  - Verify: doc review
- [ ] **T11 (P3, carried from the Policy Check review)** diff: reuse verify value helpers (deferred)

### Unresolved decisions
None.

## Amendments after build (2026-11-03)

These record what shipped where it differs from or sharpens the text above.

- Join (fix lane, `76d2a8f`): a document that prints endorsements becomes a policy row only if it also carries a
  declarations-only fact (named insured, address, dates or a limit). Carrier, NAIC, producer, policy number and
  line do not count, because endorsements print them too. Form numbers keep one edition (`extract.split_edition`);
  the endorsements prompt defines a condition. `EXTRACTION_VERSION` is "2026-11-03.2".
- Live gate run 2: 0 hard fails, recall 17/17, supported accuracy 15/15. Replies replayed offline in
  `tests/eval/test_certificate_replay.py`. Field accuracy on `condition` is 0/2 by exact match only (verdicts right).
- UI direction A "Proof and ledger" (owner pick): certificate sheet beside a check list (To resolve, Handled by you,
  Supported folded); hovering a check boxes its line; chips swap the sheet for the cited page. Brief:
  `frontend/CERTIFICATES-BRIEF.md`; DESIGN.md "Certificates".
- Owner calls on the UI: certificate runs have no Chat and the left panel (Policies, Input parameters) is folded by
  default; removed lines leave the paper and stay under Handled by you with "Put back on the certificate"; the legal
  header and cancellation clause are plain sample wording, marked "Sample wording" on screen and on the PDF.
- Lane choices the owner may veto: no left panel on phone for certificate runs; client optional in the wizard;
  Override, Remove and Confirm toasts offer Undo; the "Read on <date>" marker also shows on Compare Quotes and Policy
  Check documents reused from the cache; edited limit rows have no margin glyph.
- Seed: run 10 issued and run 11 draft for Calloway Build Group LLC (past holder Larkspur Commons Owners Association).

### Completion summary
- Step 0: Scope Challenge: scope accepted as-is (smaller structure, D1)
- Reality rows: 7/7 applicable rows emitted (PLAN_REALITY complete), 0 unverified premises
- Architecture Review: 3 issues found
- Code Quality Review: 3 issues found
- Test Review: diagram produced, 44 gaps identified (all new-code proposals); 2 regression contracts
- Performance Review: 0 issues found
- NOT in scope: written
- What already exists: written
- TODOS.md updates: 1 item proposed to user (added)
- Failure modes: 0 critical gaps flagged
- Unresolved decisions: 0 in this review
- Outside voice: codex (gpt-6-astra), completed, 7 findings, all accepted
- Parallelization: 4 lanes, 2 parallel / 2 sequential
- Lake Score: 16/16 = answers picking a 10/10 option / answers scored for Completeness (where scored)

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Outside Review | codex (gpt-6-astra), default-on after eng review | Independent 2nd opinion | 1 | completed | 7 findings (5 High, 2 Medium), all accepted (D11 to D17) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open | 50 issues (3 architecture, 3 code quality, 44 test gaps, 0 performance), 0 critical gaps; all mapped to tasks C1 to C12 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **OUTSIDE COVERAGE:** codex, plan-review phase, completed, 7 findings; all resolved through the decision ledger.
- **VERDICT:** no review CLEAR for this spec yet; Eng Review is issues_open with every finding mapped to approved work (eng review required gate: findings are decided, not open questions). Design work for the UI lane goes through the design stack.

NO UNRESOLVED DECISIONS
