"""Eval adapter: one Compare Quotes run over files on disk (contract in tests/eval/eval_adapter.py).

    run_eval_batch(paths) -> {"input_tokens": int, "documents": {path: {...}}}

Runs the real pipeline (parse, OCR, extract, verify, compose) through
RunManager against a throwaway in-memory database, so nothing is written to
data/. The provider defaults to OpenAIProvider.from_env(), which reads
OPENAI_API_KEY from the environment only (never from .env). Tests pass a
FakeProvider.

Mapping to the eval's vocabulary:
- status: "ok" when the document finished, else "failed".
- failure_reason: ParseErrorCode values, with encrypted -> password_protected
  and too_large / too_many_pages -> over_cap; provider failures keep their
  ProviderError code (budget_exhausted, refusal, ...); a path that cannot be
  read is missing_file.
- state: EvidenceState, with absent -> not_found and a null value (the eval
  treats a not_found fact with no value as absent). conflicting (T14: a long
  document states different values, none picked) -> not_found with value
  "Conflicting values: v1 (page p1); v2 (page p2)", so it counts as a
  rejected fact, never as a verified wrong value.
- page: the page the quote was found on (after a D6 re-point), else the page
  the model cited.
- input_tokens: prompt tokens of every model call in the run (M4), as logged
  on the run.

Proposal Builder (slice 2, task T1): ``run_eval_batch(paths,
workflow="proposal-builder", parameters={...}, template="standard")`` runs the
seeded ``proposal-builder`` workflow instead and adds a ``proposal`` block:
every table cell of the artifact (row = fact field, column = document path,
citations resolved to (document path, page) through the passages) and every
prose segment, so the eval can score cells and prose. Without ``workflow`` the
call is the Compare Quotes run above, unchanged. When the backend has no such
workflow (lane A not merged) it raises WorkflowUnavailable, a
NotImplementedError, before any model call. Run parameters follow spec section
2: the hook adds ``template_id`` and an ``outline`` copied from the template's
blocks when a ``proposal_template`` table exists; without one the run uses the
workflow's static section plan and a warning says so.

Policy Check (slice 3, task T7): ``run_eval_batch(paths, workflow="policy-check",
slots={path: role})`` runs the seeded ``policy-check`` workflow with each path in its
role slot (expiring, renewal, quote, ams; D8). An AMS path is the JSON or CSV export
itself: the hook parses and renders it to a PDF exactly as the upload route does (D4),
stores the document as kind "ams" with the original kept for ams.facts_from_original
(D5), and an AMS file the parser rejects is reported failed with its AmsError code. The
output adds a ``policy_check`` block: every DiscrepancyBlock of the artifact as an item
(id, key, title, severity, item_state, explanation) with one slot per role present in
the block (a role left out is N/A, no document). A slot carries the role's document
path, the raw evidence state (verified, scan_read, absent, failed, conflicting,
not_found), the value only for a cited state, its citations resolved to (document
path, page) and its conflict list.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend import ams, db, models
from backend.pipeline.parse import DEFAULT_LIMITS
from backend.pipeline.runner import DocumentRef, RunManager
from backend.providers import OpenAIProvider, Provider
from backend.schemas import (
    CITED_STATES,
    DiscrepancyBlock,
    EvidenceState,
    HeadingBlock,
    IssueBlock,
    ListBlock,
    ParagraphBlock,
    RunDocumentStatus,
    Segment,
    TableBlock,
)

_REASONS = {"encrypted": "password_protected", "too_large": "over_cap", "too_many_pages": "over_cap",
            "too_many_documents": "over_cap"}
_STATES = {EvidenceState.VERIFIED: "verified", EvidenceState.SCAN_READ: "scan_read",
           EvidenceState.NOT_FOUND: "not_found", EvidenceState.ABSENT: "not_found",
           EvidenceState.FAILED: "failed", EvidenceState.PENDING: "not_found",
           EvidenceState.CONFLICTING: "not_found"}

PROPOSAL_SLUG = "proposal-builder"
POLICY_SLUG = db.POLICY_CHECK_SLUG
CERTIFICATE_SLUG = db.CERTIFICATE_SLUG
POLICY_ROLES = ("expiring", "quote", "renewal", "ams")
_AMS_SUFFIXES = (".json", ".csv")
PROPOSAL_TEMPLATE_TABLE = "proposal_template"
# Wizard step 2 fields the caller may pass (spec section 2); template_id and
# outline are added here unless the caller gives them.
_PROPOSAL_PARAM_KEYS = ("broker_name", "brokerage_name", "additional_context", "no_ai_prose",
                        "template_id", "outline")
# Keys of a template block copied into a run outline item (spec section 2, D3 fact_sections).
_OUTLINE_KEYS = ("id", "kind", "title", "ai_instruction", "fact_sections")


class WorkflowUnavailable(NotImplementedError):
    """The requested workflow (or its template) is not seeded in this backend."""


def run_eval_batch(paths: Sequence[str], *, provider: Provider | None = None,
                   budget_limit: int | None = None, workflow: str | None = None,
                   parameters: dict[str, Any] | None = None,
                   template: str | int | None = None,
                   slots: Mapping[str, str] | None = None) -> dict[str, Any]:
    paths = [str(p) for p in paths]
    if len(paths) > DEFAULT_LIMITS.max_documents_per_run:
        raise ValueError(f"{len(paths)} documents; one run takes at most "
                         f"{DEFAULT_LIMITS.max_documents_per_run} (D3)")
    slug = workflow or db.COMPARE_QUOTES_SLUG
    known = (db.COMPARE_QUOTES_SLUG, PROPOSAL_SLUG, POLICY_SLUG, CERTIFICATE_SLUG)
    if slug not in known:
        raise ValueError(f"unknown eval workflow {slug!r}; known: {', '.join(known)}")
    role_of: dict[str, str] | None = None
    if slug == POLICY_SLUG:
        role_of = _policy_roles(paths, slots)
    elif slots is not None:
        raise ValueError("slots are only used by the policy-check workflow")
    if slug != db.COMPARE_QUOTES_SLUG:
        _require_workflow(slug)  # before a provider (and an API key) is needed
    owned = provider is None
    prov = provider or OpenAIProvider.from_env()

    async def _main() -> dict[str, Any]:
        try:
            return await _run(paths, prov, budget_limit, slug, parameters, template, role_of)
        finally:
            # Close the HTTP client we created inside this loop, else teardown logs
            # 'Event loop is closed' errors.
            close = getattr(getattr(prov, "_client", None), "close", None) if owned else None
            if close is not None:
                await close()

    return asyncio.run(_main())


def _policy_roles(paths: Sequence[str], slots: Mapping[str, str] | None) -> dict[str, str]:
    """path -> role for a policy-check run; every path has one known role, no role twice."""
    if not slots:
        raise ValueError("a policy-check eval run needs slots: {path: role}")
    given = {str(k): v for k, v in slots.items()}
    out: dict[str, str] = {}
    for p in paths:
        role = given.get(p)
        if role not in POLICY_ROLES:
            raise ValueError(f"path {p!r} has no policy-check role (one of {', '.join(POLICY_ROLES)})")
        if role in out.values():
            raise ValueError(f"role {role!r} is given to more than one document")
        out[p] = role
    extra = set(given) - set(paths)
    if extra:
        raise ValueError(f"slots name paths that are not in the run: {sorted(extra)}")
    return out


def _failed(reason: str) -> dict[str, Any]:
    return {"status": "failed", "failure_reason": reason, "option_count": None, "ocr_pages": None, "facts": []}


def _require_workflow(slug: str) -> None:
    """Raise WorkflowUnavailable unless a fresh database seeds workflow ``slug``."""
    engine = db.make_engine("sqlite://")
    try:
        db.init_db(engine)
        with db.make_session_factory(engine)() as s:
            found = s.scalar(select(models.Workflow.id).where(models.Workflow.slug == slug))
    finally:
        engine.dispose()
    if found is None:
        raise WorkflowUnavailable(
            f"workflow {slug!r} is not seeded in this backend (slice 2 lane A adds it: spec tasks "
            f"T2 and T6); the proposal eval cannot run until it is merged")


def _template_model() -> type | None:
    """The mapped class of the proposal_template table, if this backend has one."""
    for mapper in models.Base.registry.mappers:
        if getattr(mapper.local_table, "name", None) == PROPOSAL_TEMPLATE_TABLE:
            return mapper.class_
    return None


def outline_from_blocks(blocks: Sequence[Any]) -> list[dict[str, Any]]:
    """A run outline that is an unedited copy of a template's blocks (wizard step 3 with
    no edits): each block's id, kind, title, ai_instruction and fact_sections."""
    out = []
    for b in blocks or ():
        d = b if isinstance(b, dict) else (b.model_dump() if hasattr(b, "model_dump") else dict(b))
        out.append({k: d[k] for k in _OUTLINE_KEYS if k in d})
    return out


def proposal_parameters(session: Any, parameters: dict[str, Any] | None,
                        template: str | int | None) -> tuple[dict[str, Any], list[str]]:
    """Run parameters for a proposal eval run, validated and snapshotted by the same
    code the POST /api/runs route uses (proposal.prepare_parameters), so the eval
    exercises the real outline validation and template snapshot (D3, D6)."""
    from backend import proposal as proposal_mod
    from backend.proposal import ParameterErrors

    params = {k: v for k, v in (parameters or {}).items() if k in _PROPOSAL_PARAM_KEYS}
    if template is not None:
        params["template_id"] = template
    elif "template_id" not in params:
        first = session.scalar(select(models.ProposalTemplate).order_by(models.ProposalTemplate.id))
        if first is None:
            raise WorkflowUnavailable("no proposal template is seeded in this backend")
        params["template_id"] = first.id
    wf = session.scalar(select(models.Workflow).where(models.Workflow.slug == PROPOSAL_SLUG))
    try:
        return proposal_mod.prepare_parameters(session, wf, params), []
    except proposal_mod.ParameterErrors as exc:
        raise ValueError(f"invalid proposal parameters: {exc}") from exc


async def _run(paths: list[str], provider: Provider, budget_limit: int | None,
               slug: str = db.COMPARE_QUOTES_SLUG, parameters: dict[str, Any] | None = None,
               template: str | int | None = None,
               role_of: Mapping[str, str] | None = None) -> dict[str, Any]:
    proposal = slug == PROPOSAL_SLUG
    policy = slug == POLICY_SLUG
    cert = slug == CERTIFICATE_SLUG
    client_name: str | None = None
    engine = db.make_engine("sqlite://")
    try:
        db.init_db(engine)
        factory = db.make_session_factory(engine)
        documents: dict[str, dict[str, Any]] = {}
        blobs: dict[int, bytes] = {}
        originals: dict[int, bytes] = {}
        order: list[tuple[str, int]] = []
        run_params: dict[str, Any] = {}
        warnings: list[str] = []
        with db.session_scope(factory) as s:
            workflow_id = s.scalar(select(models.Workflow.id).where(models.Workflow.slug == slug))
            if workflow_id is None:
                raise WorkflowUnavailable(f"workflow {slug!r} is not seeded in this backend")
            if proposal:
                run_params, warnings = proposal_parameters(s, parameters, template)
            if cert:
                run_params, client_name = certificate_parameters(parameters)
            for path in paths:
                try:
                    data = Path(path).read_bytes()
                except OSError:
                    documents[path] = _failed("missing_file")
                    continue
                kind, pdf, page_count = "quote", data, None
                if policy and (role_of[path] == "ams" or Path(path).suffix.lower() in _AMS_SUFFIXES):
                    # Same path as the AMS upload route (D4): parse, screen, render to a PDF.
                    try:
                        rendered = ams.render_pdf(ams.parse_ams(data))
                    except ams.AmsError as exc:
                        documents[path] = _failed(str(exc.code))
                        continue
                    kind, pdf, page_count = "ams", rendered.pdf, rendered.page_count
                doc = models.Document(workspace_id=db.DEFAULT_WORKSPACE_ID, filename=Path(path).name,
                                      stored_name=uuid.uuid4().hex, size_bytes=len(data),
                                      sha256=hashlib.sha256(data).hexdigest(), kind=kind,
                                      page_count=page_count)
                s.add(doc)
                s.flush()
                blobs[doc.id] = pdf
                if kind == "ams":
                    originals[doc.id] = data
                order.append((path, doc.id))
        if not order:
            out: dict[str, Any] = {"input_tokens": 0, "documents": documents}
            if proposal:
                out["proposal"] = {"workflow": slug, "run_status": None, "cells": [], "prose": [],
                                   "warnings": warnings + ["no readable document: no run"]}
            if policy:
                out["policy_check"] = _empty_policy_block(slug, role_of, ["no readable document: no run"])
            if cert:
                out["certificate"] = _empty_certificate_block(["no readable document: no run"])
            return out

        def load(ref: DocumentRef) -> bytes:
            return blobs[ref.id]

        def load_original(ref: DocumentRef) -> bytes:
            return originals[ref.id]

        # D9: an eval always reads live, never an earlier read of the same bytes (the cache is off).
        manager = RunManager(factory, provider, load_bytes=load, load_original=load_original,
                             budget_limit=budget_limit, extraction_cache=False)
        try:
            if proposal:
                run_id, _ = manager.submit(workflow_id, [doc_id for _, doc_id in order], run_params)
            elif policy:
                try:
                    run_id, _ = manager.submit(workflow_id, [doc_id for _, doc_id in order],
                                               slots={doc_id: role_of[path] for path, doc_id in order})
                except ValueError as exc:  # SlotError: a required role is missing (its file failed)
                    out = {"input_tokens": 0, "documents": documents}
                    for path, _ in order:
                        documents.setdefault(path, _failed("not_run"))
                    out["policy_check"] = _empty_policy_block(slug, role_of, [f"run not created: {exc}"])
                    return out
            elif cert:
                run_id, _ = manager.submit(workflow_id, [doc_id for _, doc_id in order], run_params,
                                           client_name=client_name)
            else:
                run_id, _ = manager.submit(workflow_id, [doc_id for _, doc_id in order])
            await manager.wait(run_id)
        finally:
            await manager.shutdown()

        with factory() as s:
            run = s.get(models.Run, run_id)
            by_doc = {rd.document_id: rd for rd in run.documents}
            facts: dict[int, list[models.Fact]] = {}
            for f in run.facts:
                facts.setdefault(f.document_id, []).append(f)
            for path, doc_id in order:
                rd = by_doc[doc_id]
                if RunDocumentStatus(rd.status) != RunDocumentStatus.DONE:
                    documents[path] = _failed(_REASONS.get(rd.error_code or "", rd.error_code or "unknown"))
                    continue
                doc = s.get(models.Document, doc_id)
                documents[path] = {
                    "status": "ok",
                    "failure_reason": None,
                    "option_count": rd.option_count or 1,
                    "ocr_pages": [p.page_no for p in doc.pages if p.ocr],
                    "facts": [_fact(f) for f in sorted(facts.get(doc_id, []), key=lambda f: f.id)],
                }
            tokens = int(run.tokens_in or 0)
            out = {"input_tokens": tokens, "documents": documents}
            if proposal:
                out["proposal"] = _proposal_block(s, run, slug, order, warnings)
            if policy:
                out["policy_check"] = _policy_block(s, run, slug, order, role_of)
            if cert:
                out["certificate"] = _certificate_block(s, run, order)
        return out
    finally:
        engine.dispose()


def _empty_policy_block(slug: str, role_of: Mapping[str, str] | None, warnings: list[str]) -> dict[str, Any]:
    return {"workflow": slug, "run_status": None,
            "roles": {r: p for p, r in (role_of or {}).items()}, "items": [], "notices": [],
            "warnings": list(warnings)}


def _policy_block(s: Any, run: models.Run, slug: str, order: list[tuple[str, int]],
                  role_of: Mapping[str, str]) -> dict[str, Any]:
    """Every DiscrepancyBlock of the run's current artifact version as an item, its slots
    with citations resolved to (document path, page) through the passages."""
    path_by_doc = {doc_id: path for path, doc_id in order}
    path_by_role = {r: p for p, r in role_of.items()}
    block = _empty_policy_block(slug, role_of, [])
    block["run_status"] = str(run.status)
    art = run.artifact
    if art is None or not art.versions:
        block["warnings"].append("the run produced no artifact")
        return block
    content = art.latest_content()
    block["notices"] = [{"kind": n.kind, "message": n.message} for n in content.notices]
    found = [b for _, b in content.iter_blocks() if isinstance(b, DiscrepancyBlock)]
    wanted: set[int] = set(content.passage_ids())
    for b in found:
        for cell in b.slots.values():
            wanted.update(c.passage_id for c in cell.conflicts if c.passage_id is not None)
    passages = {p.id: p for p in s.scalars(select(models.Passage).where(models.Passage.id.in_(wanted)))}

    def cites(ids: Sequence[int]) -> list[dict[str, Any]]:
        out = []
        for pid in ids:
            p = passages.get(pid)
            out.append({"document": path_by_doc.get(p.document_id) if p else None,
                        "page": p.page_no if p else None, "passage_id": pid})
        return out

    for b in found:
        slots: dict[str, dict[str, Any]] = {}
        for role, cell in b.slots.items():
            state = EvidenceState(cell.state) if cell.state is not None else None
            shown = state in CITED_STATES
            slots[str(role)] = {
                "document": path_by_role.get(str(role)),
                "state": str(state) if state is not None else None,
                "value": cell.value if shown else None,
                "citations": cites(cell.passage_ids),
                "conflicts": [{"value": c.value, "page": c.page_no} for c in cell.conflicts],
            }
        block["items"].append({
            "id": b.id, "key": b.key, "title": b.title, "severity": str(b.severity),
            "item_state": str(b.item_state), "explanation": " ".join(seg.text for seg in b.prose),
            "slots": slots,
        })
    return block


def _proposal_block(s: Any, run: models.Run, slug: str, order: list[tuple[str, int]],
                    warnings: list[str]) -> dict[str, Any]:
    """Every table cell and prose segment of the run's current artifact version,
    with citations resolved to (document path, page)."""
    path_by_doc = {doc_id: path for path, doc_id in order}
    doc_by_letter = {rd.letter: rd.document_id for rd in run.documents}
    block: dict[str, Any] = {"workflow": slug, "run_status": str(run.status), "cells": [], "prose": [],
                             "warnings": list(warnings)}
    art = run.artifact
    if art is None or not art.versions:
        block["warnings"].append("the run produced no artifact")
        return block
    content = art.latest_content()
    wanted: set[int] = set(content.passage_ids())
    for _, b in content.iter_blocks():
        if isinstance(b, TableBlock):
            for row in b.rows:
                for cell in row.cells.values():
                    wanted.update(c.passage_id for c in cell.conflicts if c.passage_id is not None)
    passages = {p.id: p for p in s.scalars(select(models.Passage).where(models.Passage.id.in_(wanted)))}

    def cites(ids: Sequence[int]) -> list[dict[str, Any]]:
        out = []
        for pid in ids:
            p = passages.get(pid)
            out.append({"document": path_by_doc.get(p.document_id) if p else None,
                        "page": p.page_no if p else None, "passage_id": pid})
        return out

    def prose(section_id: str, block_id: str | None, segments: Sequence[Segment]) -> None:
        for seg in segments:
            block["prose"].append({"section_id": section_id, "block_id": block_id, "text": seg.text,
                                   "citations": cites(seg.passage_ids)})

    for section in content.sections:
        prose(section.id, None, [Segment(text=section.title)])
        for b in section.blocks:
            if isinstance(b, TableBlock):
                doc_by_col = {c.key: c.document_id for c in b.columns}
                for row in b.rows:
                    for key, cell in row.cells.items():
                        doc_id = doc_by_col.get(key) or doc_by_letter.get(key)
                        state = _STATES[EvidenceState(cell.state)] if cell.state is not None else None
                        # Only cited states show a value; others show fixed text ("Not specified").
                        shown = state in ("verified", "scan_read") or state is None
                        block["cells"].append({
                            "block_id": b.id, "row": row.key, "column": key,
                            "document": path_by_doc.get(doc_id) if doc_id is not None else None,
                            "value": cell.value if shown else None, "state": state,
                            "citations": cites(cell.passage_ids),
                            "conflicts": [c.value for c in cell.conflicts],
                        })
            elif isinstance(b, ParagraphBlock):
                prose(section.id, b.id, b.segments)
            elif isinstance(b, ListBlock):
                if b.title:
                    prose(section.id, b.id, [Segment(text=b.title)])
                for item in b.items:
                    prose(section.id, b.id, item)
            elif isinstance(b, HeadingBlock):
                prose(section.id, b.id, [Segment(text=b.text)])
            elif isinstance(b, IssueBlock):
                prose(section.id, b.id, [Segment(text=b.title), *b.impact])
    return block


# --------------------------------------------------------------------- certificates (slice 4, C10 adapter)

_CERT_PARAM_KEYS = ("holder_name", "holder_address", "wording", "certificate_date", "description")


def certificate_parameters(parameters: Mapping[str, Any] | None) -> tuple[dict[str, Any], str | None]:
    """(run parameters, client name) for a certificate eval run, validated by the same model the
    POST /api/runs route uses (pipeline.certificate.CertificateParameters)."""
    from backend.pipeline.certificate import CertificateParameters

    raw = dict(parameters or {})
    params = {k: v for k, v in raw.items() if k in _CERT_PARAM_KEYS}
    try:
        stored = CertificateParameters.model_validate(params).stored()
    except Exception as exc:  # pydantic ValidationError: a bad case file
        raise ValueError(f"invalid certificate parameters: {exc}") from exc
    client = raw.get("client_name")
    return stored, (" ".join(str(client).split()) or None) if client else None


def _empty_certificate_block(warnings: list[str]) -> dict[str, Any]:
    return {"workflow": CERTIFICATE_SLUG, "run_status": None, "claims": [], "notices": [], "warnings": list(warnings)}


def gate_claim_key(claim: Any, policy_number: str | None, line: str | None) -> str | None:
    """The eval gate's claim key (tests/eval/certificate_gate.py): wording:<wording>:<line>,
    in_force:<normalized policy number>, insured_consistent, description_reviewed. The backend keys
    claims by document letter (stable when a document is added); edited-value claims are not scored."""
    from backend.pipeline import coi_checks

    kind = str(claim.kind)
    if kind == "wording":
        return f"wording:{claim.wording}:{line or 'other'}"
    if kind == "in_force":
        number = coi_checks.normalize_policy_number(policy_number)
        return f"in_force:{number or claim.policy_letter}"
    return {"insured": "insured_consistent", "description": "description_reviewed"}.get(kind)


def _certificate_block(s: Any, run: models.Run, order: list[tuple[str, int]]) -> dict[str, Any]:
    """Every claim of the run's current draft with its check verdict (before any broker decision),
    citations as (document path, page), and the notices with their kind and document."""
    from backend.pipeline import certificate

    path_by_doc = {doc_id: path for path, doc_id in order}
    path_by_letter = {rd.letter: path_by_doc.get(rd.document_id) for rd in run.documents}
    block = _empty_certificate_block([])
    block["run_status"] = str(run.status)
    if run.artifact is None or not run.artifact.versions:
        block["warnings"].append("the run produced no artifact")
        return block
    analysis = certificate.analyse(s, run)
    policies = {p.letter: p for p in analysis.joined.policies}
    wanted = {pid for c in analysis.claims for pid in c.passage_ids()}
    passages = {p.id: p for p in s.scalars(select(models.Passage).where(models.Passage.id.in_(wanted)))}
    for c in analysis.claims:
        p = policies.get(c.policy_letter) if c.policy_letter else None
        pn = p.fact("cert:policy_number") if p is not None else None
        key = gate_claim_key(c, pn.value if pn is not None and pn.cited else None, p.line if p else None)
        if key is None:
            continue
        cites = []
        for pid in sorted(c.passage_ids()):
            psg = passages.get(pid)
            if psg is not None:
                cites.append({"document": path_by_doc.get(psg.document_id), "page": psg.page_no})
        block["claims"].append({"key": key, "backend_key": c.key, "kind": str(c.kind),
                                "status": "supported" if str(c.verdict) == "supported" else "not_supported",
                                "reason": c.reason, "condition": c.condition, "citations": cites})
    block["notices"] = [{"kind": n.kind, "message": n.message, "document": path_by_letter.get(n.document_letter)}
                        for n in analysis.joined.notices]
    for rd in run.documents:
        if str(rd.status) == "failed":
            block["notices"].append({"kind": "document_not_read", "message": rd.error or "Not read",
                                     "document": path_by_doc.get(rd.document_id)})
    return block


def _fact(f: models.Fact) -> dict[str, Any]:
    state = EvidenceState(f.state)
    value = None if state == EvidenceState.ABSENT else f.value
    if state == EvidenceState.CONFLICTING:
        listed = "; ".join(f"{c.get('value')} (page {c['page_no']})" if c.get("page_no") is not None
                           else f"{c.get('value')}" for c in f.conflicts or ())
        value = f"Conflicting values: {listed}" if listed else None
    return {"field": f.field, "value": value, "page": f.page_no if f.page_no is not None else f.cited_page,
            "quote": f.quote, "state": _STATES[state]}
