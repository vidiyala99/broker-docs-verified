# BrokerDocs, Slice 2: Proposal Builder

Date: 2026-11-02. Status: draft for owner review. Builds on
`2026-11-01-brokerdocs-core-design.md` (slice 1) and the evidence in
`docs/reference/brokerdocs-workflows-evidence.md` section 1.

## 1. Intent

Outcome: a broker drops in one or more carrier quote PDFs, picks a template,
fills a few details, adjusts the outline, and gets a client-ready proposal in
which every figure is verified against the source page and cited. Export to
DOCX and PDF.

Audience: the owner, as a credible end-to-end demo of the verified-citation
engine producing a real deliverable (not a pixel match of the original editor).
Single-user local app; no roles, AMS, Vault, email or share.

Success criteria:
- Quote PDFs in, proposal out, with no unverified value shown as cited.
- The structure of the proposal (outline, cover page, TOC, comparison tables)
  is deterministic given the verified facts and the outline.
- Hard fails on the live eval gate: 0.
- DOCX opens in Word with cover page, TOC, tables, footnoted citations.

Decisions already made (do not re-ask):
- Scope option 1: wizard + generate + light edit. Not the TipTap editor.
- Approach A: the proposal is a seeded workflow (`proposal-builder`) run through
  the existing run pipeline. Not derived from another run, not a new subsystem.
- At least one quote PDF is required (`invalid_run` rule unchanged). A proposal
  with zero documents is out: it would need a "not sourced from documents" trust
  state.
- Two seeded templates ("Standard", "Premier") so the gallery is a real choice.
- Include a no-AI-prose mode and extraction caching by document hash.

## 2. Data model and engine changes

New table `proposal_template` (seeded as data):

| Column | Holds |
|---|---|
| `id`, `slug`, `name`, `description` | identity |
| `blocks` | ordered list of `{id, title, description, ai_instruction, kind, locked}` |
| `theme` | font, heading colors and sizes; applied at export only, not editable in this slice |
| `variables` | allowed names (`agency_name`, `broker_name`, `client_name`) |

Block `kind`: `client_details`, `line_comparison`, `narrative`,
`premium_comparison`, `subjectivities`. Final list fixed during planning; each
kind has one compose prompt and one deterministic table builder (if any).
Only Cover Page and Table of Contents are `locked`.

Per-run outline: step 3 of the wizard edits a copy of the template blocks
(reorder, delete, add from the kind library, edit title and instruction). It
is stored in `run.parameters`:

```json
{"template_id": 1, "broker_name": "...", "brokerage_name": "...",
 "additional_context": "...", "no_ai_prose": false,
 "outline": [{"id": "...", "kind": "...", "title": "...", "ai_instruction": "..."}]}
```

Engine changes (the minimum):
1. Seed the `proposal-builder` workflow: the Compare Quotes `fact_schema` plus
   the per-coverage facts that the cyber and E&O sub-tables need.
2. Compose reads `run.parameters.outline` when present, else the workflow's
   static `section_plan`. The outline is validated on the way in: known kinds,
   block-count cap, length cap on titles and instructions, locked blocks
   present, unique ids. It is untrusted input (it reaches the model prompt).
3. A block builder creates comparison tables in code from the verified fact
   grid. The model writes only framing prose.
4. Cover Page and TOC are generated in code from the title, the broker fields
   and the section titles. No model call.
5. Extraction cache keyed by document `sha256` + `fact_schema` version + model
   config. Any change to the schema version or model config is a miss. A hit
   reuses the stored facts, then verification still runs against the pages.
6. No-AI-prose mode: all prose uses the existing template fallback text
   (`compose_fallback` path). No compose model call; output is byte-identical
   for the same facts and outline.

Unchanged: parse, extract, verify, the validator (known passage ids, numbers in
facts), versions, edit/undo, regenerate, chat, SSE, stale flags.

Security: `additional_context`, block titles and instructions are user text sent
to the model. Handle them with slice 1's injection baseline: placed in a
clearly delimited user-instruction channel, never able to introduce passage ids
or facts; the validator still gates the output. Canary tests cover this.

## 3. API

New:
- `GET /api/templates` -> `{"templates": [TemplateSummary]}`
- `GET /api/templates/{id_or_slug}` -> template with `blocks`, `theme`, `variables`

Changed:
- `POST /api/runs` accepts the `parameters` above when `workflow` is
  `proposal-builder`; a bad outline returns `422 invalid_request` with `fields`
  paths (`["body","parameters","outline",2,"kind"]`).
- `GET /api/artifacts/{id}/export?format=docx|xlsx|pdf`. `docx` is new.
  `xlsx` for a proposal holds its tables only, as in slice 1.

Artifact content reuses slice 1 blocks. New block types are not expected; the
cover page and TOC are `heading`/`paragraph`/`list` blocks flagged by id
(`cover`, `toc`). If planning shows a dedicated type is cleaner, add it then.

## 4. UI

Structure only. Visual design goes through the design stack (impeccable, taste,
`DESIGN.md` and `tokens.css`, Playwright MCP) at build time, with a design gate.

Wizard, four steps with a stepper:
1. Template: gallery of the two cards, search box, preview.
2. Details: upload or pick quote PDFs (at least one), broker name, brokerage
   name, additional context, no-AI-prose toggle.
3. Outline: reorder, delete, add, edit title and instruction; Cover Page and TOC
   locked; Generate button.
4. Create: the existing run progress (SSE), then the report view.

The draft lives in the browser until Generate. After generation the proposal is
a normal artifact: cell edit, undo, regenerate prose, chat, versions, stale
flags. A Preview toggle hides citation chips.

Failure states carried over: failed document column, budget exhausted notice,
compose fallback with Retry prose, invalid outline (inline 422 in step 3).

## 5. Export

DOCX (new): cover page, TOC, sections, prose, tables, theme fonts and heading
styles. Citation chips become numbered footnotes; a Sources list ends the
document. `Couldn't verify`, `Conflicting values`, `Read from scan` states are
written as text with the source note, never as plain values. Export of the same
version twice is identical.

PDF reuses the slice 1 exporter, extended for the cover page and TOC.

Risk: python-docx has no footnote API, so footnotes need low-level XML. Spike
this first (see section 7).

## 6. Testing

Offline unit tests (bulk of coverage):
- Outline validation: unknown kind, too many blocks, oversize instruction,
  missing locked block, duplicate ids -> 422 with field path.
- Table builder: fixed grid gives a fixed table; missing, conflicting, edited
  cell; 1 to 8 columns.
- Cover page and TOC after reorder, delete, add.
- Compose with outline overrides the static plan; absent outline falls back;
  validator still rejects an unknown passage id or an unsupported number.
- No-AI-prose mode makes no model call and is byte-identical across runs.
- Extraction cache: same hash hits; schema version or model change misses.
- DOCX (python-docx): cover, TOC entries, cell text, footnote count, Sources
  list, unverified states never plain; two exports identical.
- Injection: instructions in `additional_context` or a block instruction cannot
  change facts or citations (slice 1 canary approach).
- API: template endpoints; zero documents rejected (`invalid_run`); bad outline 422.

Live eval gates (OpenAI calls approved by the owner for evals and spikes):
- Two or three synthetic multi-quote fixtures with ground truth for table cells.
- 0 hard fails (uncited value, cited-wrong-page value, prose number not in the
  facts). Table-cell accuracy >= 0.85, the slice 1 gate. Prose is gated by the
  validator, not scored.

Frontend: typecheck and build; Playwright e2e for the happy path (template,
details, outline edit, generate with the fake provider, open, export); axe on
each wizard step at the two design widths; phone width for step 3.

## 7. Build order (input to /plan-eng-review)

1. Spike: DOCX footnotes with python-docx low-level XML (throwaway; answer is
   feasibility and the export design).
2. Template table, seed, endpoints, outline validation.
3. Compose reads the outline; table builder; cover and TOC; no-AI mode.
4. Extraction cache.
5. DOCX export; PDF cover and TOC.
6. Wizard UI (design gate applies).
7. Live eval fixtures and gate.

## 8. Not in scope

- Slice 2b (planned follow-up): drag-and-drop block editing after generation
  (TipTap), theme panel, variable chips.
- Deferred, no slice: DOCX template import. The original has the endpoint string
  but nothing shows its behavior.
- Never, for a single-user local clone: email, share.
- Zero-document proposals.
- The data-only workflow pack (Contract Requirements, Coverage Gap, Tower) is a
  separate thin lane, not part of this spec.
- Editable theme, AMS data, cover-page configuration beyond the broker fields.

## 9. Open questions

- Final block kind list and the per-kind compose prompts (planning).
- Cache invalidation also on parse changes? Currently keyed on hash, schema
  version and model config only.
- Whether cover page and TOC need a dedicated block type (decide in planning).
- Slice numbering: slice 1's spec says "slice 3 (custom workflows)", the evidence
  doc orders Policy Check next. A slice map is not drafted.

## Engineering review (plan-eng-review, 2026-11-02)

Scope record: feature answers: D1 (defer extraction cache to slice 2c); structure: A, original arrangement (D2); accepted scope: spec as written minus the extraction cache (spec sections 2.5 and the cache tests move to slice 2c); pending remedies: Scope Challenge findings S1 to S6 below.

Owner feedback logged during review (not slice 2): the left panel (Upload, Input parameters, Recent runs, New comparison, chat) is chunky and the chat cannot collapse. Handle with the open design call "collapse Upload when a run is Done" as one bounded design fix through the design stack.

### Scope Challenge findings (evidence: existing code)
- S1 [P1] (confidence 9/10) backend/pipeline/compose.py:313: `sid = fdef.section if fdef.section in known_sections else fallback_section`. With a per-run outline, a deleted block's facts would be placed into the first section instead of being dropped.
- S2 [P1] (9/10) backend/pipeline/runner.py:319 and :646 both read `wf.section_plan`; retry, regenerate and chat go through `compose_input_for_run` (:646). The outline override must be one shared helper used at both sites.
- S3 [P2] (8/10) `build_tables` (compose.py:302) already builds tables in code from the fact grid; the spec's "new block builder" should extend it, not add a second builder.
- S4 [P2] (8/10) backend/pyproject.toml lists openpyxl and reportlab but not python-docx; DOCX needs a new dependency, and footnotes need raw XML (spike first, fallback: superscript numbers plus a Sources list).
- S5 [P2] (7/10) backend/edits.py:125 `merge_edits` re-applies edits by block id; outline-derived table ids must be unique and stable under reorder, and cover/TOC blocks must survive a merge.
- S6 [P3] (8/10) db.py seeds only if absent (HANDOFF open item 2); new templates and the `proposal-builder` workflow need an upsert.

### Review sections: findings
- A1 [P1] (9/10) db.py FactField.section is one static section per field; outline blocks need explicit fact bindings (D3).
- A2 [P1] (8/10) Export resolves the theme at export time and ArtifactVersion stores content only (models.py:306); snapshot the template per run (D6).
- A3 [P1] (9/10) eval_hook.py:89 selects Compare Quotes only and eval_gates.py:372-377 ignores unlisted fields; the proposal gate needs a proposal-aware adapter first (required proof of the approved gate, task T1).
- A4 [P2] (8/10) validator uses report-wide allowlists (compose.py:495); keep TODO "Claim-level validation of report prose"; mitigation: each block's prose sees only its own facts.
- C1 [P2] (9/10) template_report (compose.py:552) models failure; the no-AI mode needs its own successful renderer (D5).
- C2 [P3] (8/10) DOCX footnotes reuse cell_note and _source_note (export.py:63-100); no new abstraction.
- O4 [P1] (9/10, Codex) compose_edits (edits.py:155) drops block identity; two blocks showing one fact can contradict (D4).
- Performance: no findings (at most 8 documents, outline capped near 20 blocks, in-memory DOCX).

### Decision ledger
- D1: extraction cache deferred to slice 2c. Answer: "Defer to slice 2c".
- D2: original file arrangement. Answer: "Original arrangement".
- S1 to S6: accepted as plan items on the owner's "sounds good" after the recommendation was stated.
- D3: explicit `fact_sections` per block; facts owned by no block are dropped, not relocated. Compare Quotes with no outline unchanged. Answer: "Explicit fact_sections (Recommended)".
- D4: at most one table per fact section; narrative blocks may repeat; violation is 422 at outline validation. Answer: "One table per fact section (Recommended)".
- D5: no-AI mode is a separate deterministic renderer (plain per-kind text from verified facts, no compose_fallback notice, preserved across retry and regenerate). Answer: "Deterministic renderer (Recommended)".
- D6: resolved template (blocks, theme, variables) snapshotted into run.parameters at creation; export and regenerate read the snapshot. Answer: "Snapshot into run (Recommended)".
Process note: D3 to D6 were asked in one batched call, and the ledger was recorded after the answers, not before.
Approval readiness: PASS for D1 to D6 and S1 to S6.

### NOT in scope
Extraction cache (slice 2c); TipTap editor, theme panel, variable chips (slice 2b); DOCX template import; email and share; zero-document proposals; claim-level prose validation (existing TODO); left-panel redesign (separate design fix).

### What already exists
build_tables (compose.py:302) is extended, not duplicated; template_report stays the failure fallback; cell_note and _source_note (export.py) feed footnotes; SectionPlanItem and the workflow seed in db.py carry the outline shape; slice 1 edit, undo, chat, SSE and versions are reused unchanged.

### Failure modes
- Deleted block's facts shown elsewhere: fixed by D3, test required. No critical gap.
- Eval gate passes without touching a proposal: fixed by T1 before the wizard.
- Old version re-exports differently: fixed by D6.
- Intentional no-AI mode looks broken: fixed by D5.
- DOCX footnote XML invalid in Word: spike (T5) with python-docx reopen test; fallback superscripts plus a Sources list. Not silent: export_failed is shown to the user.
Critical gaps: 0.

### Implementation Tasks
- [ ] **T1 (P1, human: ~4h / CC: ~25min)**: eval: proposal-aware adapter, 2 to 3 multi-quote fixtures with cell ground truth, scorer coverage for new fields, 0 hard fails and accuracy gate 0.85. Files: backend/eval_hook.py, tests/eval/. Verify: offline eval tests, then a live run.
- [x] **T2 (P1, ~4h / ~25min)**: outline in schemas.py (kinds, caps, locked, unique ids, one table per fact section), shared plan helper at runner.py:319 and :646, template snapshot into run.parameters. Verify: backend tests plus Compare Quotes regression (test_compose.py, test_runner.py, test_api_flow.py unchanged).
- [x] **T3 (P1, ~4h / ~20min)**: compose: block-to-fact_sections binding, drop unowned facts, per-block fact scoping for prose, cover and TOC, merge_edits survives them. Files: backend/pipeline/compose.py, backend/edits.py.
- [x] **T4 (P1, ~3h / ~15min)**: deterministic renderer for no-AI mode, preserved across retry and regenerate. File: backend/pipeline/compose.py.
- [ ] **T5 (P2, ~6h / ~30min)**: DOCX spike (python-docx footnote XML, reopen test), then export and PDF cover and TOC; add python-docx to pyproject. Files: backend/export.py, backend/routes/artifacts.py.
- [x] **T6 (P2, ~3h / ~15min)**: proposal_template table, seeded Standard and Premier with fact_sections, workflow seed and template upsert, template endpoints. Files: backend/models.py, backend/db.py, backend/routes/templates.py.
- Lane A build notes (2026-11-02, T2 T3 T4 T6 done): contract in backend/API.md ("Proposal templates", "Proposal runs"). Table ids are `<fact section>-table`; cover and TOC sections have ids `cover`, `toc` (blocks `cover-title`, `cover-details`, `toc-entries`) and number `""`. Field paths in `fields.loc` are strings (`"2"`), as for every slice 1 `invalid_request`. The seed now upserts every seeded workflow and template by slug (also closes HANDOFF open item 2). New files: backend/proposal.py (template resolve and snapshot, kind library).
- [ ] **T7 (P2, ~2d / ~1h)**: wizard UI under the design gate (design stack, Playwright MCP, axe at both widths, phone width for step 3). Files: frontend/src/wizard/.
- [ ] **T8 (P3, ~1h / ~10min)**: full live eval before release.

### Worktree parallelization
| Step | Modules touched | Depends on |
|---|---|---|
| T1 eval | tests/eval, backend/eval_hook | none |
| T2, T3, T4 core | backend/schemas, backend/pipeline, backend/edits | T2 first |
| T5 export | backend/export, routes/artifacts | none (spike first) |
| T6 templates | backend/models, db, routes | T2 |
| T7 wizard | frontend/src/wizard | T6 API contract |

Lane A: T2, T3, T4 then T6 (shared backend/pipeline and db). Lane B: T5. Lane C: T1. Lane D: T7 after T6. Launch A, B, C together, merge, then D, then T8. Conflict flag: T3 and T4 both edit compose.py, so keep them in one lane.

### Unresolved decisions
- TODO proposals not yet asked: extraction cache as slice 2c; left-panel design fix (chunky panel, collapsible chat, collapse Upload when Done).

### Suppressed findings
None.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | none | none |
| Outside Review | codex, plan-review | Independent 2nd opinion | 1 | completed | 6 findings, all dispositioned |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open | 7 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | none | wizard UI goes through the design gate at build time |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | none | none |

**OUTSIDE COVERAGE:** codex, plan-review, completed, 6 findings (A1 to A4, C1 and O4 overlap; all resolved by D3 to D6 or kept as an existing TODO).
**VERDICT:** ENG REVIEW: issues_open (all mapped to tasks T1 to T8); CEO and design reviews not run.

**UNRESOLVED DECISIONS:**
- TODO proposals not yet asked: extraction cache as slice 2c; left-panel design fix.
