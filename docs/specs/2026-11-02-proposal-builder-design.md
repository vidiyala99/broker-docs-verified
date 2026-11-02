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
