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
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import select

from backend import db, models
from backend.pipeline.parse import DEFAULT_LIMITS
from backend.pipeline.runner import DocumentRef, RunManager
from backend.providers import OpenAIProvider, Provider
from backend.schemas import (
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
                   template: str | int | None = None) -> dict[str, Any]:
    paths = [str(p) for p in paths]
    if len(paths) > DEFAULT_LIMITS.max_documents_per_run:
        raise ValueError(f"{len(paths)} documents; one run takes at most "
                         f"{DEFAULT_LIMITS.max_documents_per_run} (D3)")
    slug = workflow or db.COMPARE_QUOTES_SLUG
    if slug not in (db.COMPARE_QUOTES_SLUG, PROPOSAL_SLUG):
        raise ValueError(f"unknown eval workflow {slug!r}; known: {db.COMPARE_QUOTES_SLUG}, {PROPOSAL_SLUG}")
    if slug != db.COMPARE_QUOTES_SLUG:
        _require_workflow(slug)  # before a provider (and an API key) is needed
    owned = provider is None
    prov = provider or OpenAIProvider.from_env()

    async def _main() -> dict[str, Any]:
        try:
            return await _run(paths, prov, budget_limit, slug, parameters, template)
        finally:
            # Close the HTTP client we created inside this loop, else teardown logs
            # 'Event loop is closed' errors.
            close = getattr(getattr(prov, "_client", None), "close", None) if owned else None
            if close is not None:
                await close()

    return asyncio.run(_main())


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
    """Run parameters for a proposal eval run, as the wizard would send them
    (spec section 2), and warnings for the report.

    The integration point with lane A: template lookup by slug or id, and the
    outline copied from the template's blocks."""
    params = {k: v for k, v in (parameters or {}).items() if k in _PROPOSAL_PARAM_KEYS}
    warnings: list[str] = []
    cls = _template_model()
    if cls is None:
        if "outline" not in params:
            warnings.append("this backend has no proposal_template table: the run used the "
                            "workflow's static section plan (no template_id, no outline)")
        return params, warnings
    row = None
    if isinstance(template, int) or (isinstance(template, str) and template.isdigit()):
        row = session.get(cls, int(template))
    elif template is not None and hasattr(cls, "slug"):
        row = session.scalar(select(cls).where(cls.slug == template))
    elif template is None:
        row = session.scalar(select(cls).order_by(cls.id))
    if row is None:
        raise WorkflowUnavailable(f"proposal template {template!r} is not seeded in this backend")
    params.setdefault("template_id", row.id)
    if "outline" not in params:
        params["outline"] = outline_from_blocks(getattr(row, "blocks", None) or [])
    return params, warnings


async def _run(paths: list[str], provider: Provider, budget_limit: int | None,
               slug: str = db.COMPARE_QUOTES_SLUG, parameters: dict[str, Any] | None = None,
               template: str | int | None = None) -> dict[str, Any]:
    proposal = slug == PROPOSAL_SLUG
    engine = db.make_engine("sqlite://")
    try:
        db.init_db(engine)
        factory = db.make_session_factory(engine)
        documents: dict[str, dict[str, Any]] = {}
        blobs: dict[int, bytes] = {}
        order: list[tuple[str, int]] = []
        run_params: dict[str, Any] = {}
        warnings: list[str] = []
        with db.session_scope(factory) as s:
            workflow_id = s.scalar(select(models.Workflow.id).where(models.Workflow.slug == slug))
            if workflow_id is None:
                raise WorkflowUnavailable(f"workflow {slug!r} is not seeded in this backend")
            if proposal:
                run_params, warnings = proposal_parameters(s, parameters, template)
            for path in paths:
                try:
                    data = Path(path).read_bytes()
                except OSError:
                    documents[path] = _failed("missing_file")
                    continue
                doc = models.Document(workspace_id=db.DEFAULT_WORKSPACE_ID, filename=Path(path).name,
                                      stored_name=uuid.uuid4().hex, size_bytes=len(data),
                                      sha256=hashlib.sha256(data).hexdigest())
                s.add(doc)
                s.flush()
                blobs[doc.id] = data
                order.append((path, doc.id))
        if not order:
            out: dict[str, Any] = {"input_tokens": 0, "documents": documents}
            if proposal:
                out["proposal"] = {"workflow": slug, "run_status": None, "cells": [], "prose": [],
                                   "warnings": warnings + ["no readable document: no run"]}
            return out

        def load(ref: DocumentRef) -> bytes:
            return blobs[ref.id]

        manager = RunManager(factory, provider, load_bytes=load, budget_limit=budget_limit)
        try:
            if proposal:
                run_id, _ = manager.submit(workflow_id, [doc_id for _, doc_id in order], run_params)
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
        return out
    finally:
        engine.dispose()


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


def _fact(f: models.Fact) -> dict[str, Any]:
    state = EvidenceState(f.state)
    value = None if state == EvidenceState.ABSENT else f.value
    if state == EvidenceState.CONFLICTING:
        listed = "; ".join(f"{c.get('value')} (page {c['page_no']})" if c.get("page_no") is not None
                           else f"{c.get('value')}" for c in f.conflicts or ())
        value = f"Conflicting values: {listed}" if listed else None
    return {"field": f.field, "value": value, "page": f.page_no if f.page_no is not None else f.cited_page,
            "quote": f.quote, "state": _STATES[state]}
