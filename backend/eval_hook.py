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
  treats a not_found fact with no value as absent).
- page: the page the quote was found on (after a D6 re-point), else the page
  the model cited.
- input_tokens: prompt tokens of every model call in the run (M4), as logged
  on the run.
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
from backend.schemas import EvidenceState, RunDocumentStatus

_REASONS = {"encrypted": "password_protected", "too_large": "over_cap", "too_many_pages": "over_cap",
            "too_many_documents": "over_cap"}
_STATES = {EvidenceState.VERIFIED: "verified", EvidenceState.SCAN_READ: "scan_read",
           EvidenceState.NOT_FOUND: "not_found", EvidenceState.ABSENT: "not_found",
           EvidenceState.FAILED: "failed", EvidenceState.PENDING: "not_found"}


def run_eval_batch(paths: Sequence[str], *, provider: Provider | None = None,
                   budget_limit: int | None = None) -> dict[str, Any]:
    paths = [str(p) for p in paths]
    if len(paths) > DEFAULT_LIMITS.max_documents_per_run:
        raise ValueError(f"{len(paths)} documents; one run takes at most "
                         f"{DEFAULT_LIMITS.max_documents_per_run} (D3)")
    owned = provider is None
    prov = provider or OpenAIProvider.from_env()

    async def _main() -> dict[str, Any]:
        try:
            return await _run(paths, prov, budget_limit)
        finally:
            # Close the HTTP client we created inside this loop, else teardown logs
            # 'Event loop is closed' errors.
            close = getattr(getattr(prov, "_client", None), "close", None) if owned else None
            if close is not None:
                await close()

    return asyncio.run(_main())


def _failed(reason: str) -> dict[str, Any]:
    return {"status": "failed", "failure_reason": reason, "option_count": None, "ocr_pages": None, "facts": []}


async def _run(paths: list[str], provider: Provider, budget_limit: int | None) -> dict[str, Any]:
    engine = db.make_engine("sqlite://")
    try:
        db.init_db(engine)
        factory = db.make_session_factory(engine)
        documents: dict[str, dict[str, Any]] = {}
        blobs: dict[int, bytes] = {}
        order: list[tuple[str, int]] = []
        with db.session_scope(factory) as s:
            workflow_id = s.scalar(select(models.Workflow.id).where(models.Workflow.slug == db.COMPARE_QUOTES_SLUG))
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
            return {"input_tokens": 0, "documents": documents}

        def load(ref: DocumentRef) -> bytes:
            return blobs[ref.id]

        manager = RunManager(factory, provider, load_bytes=load, budget_limit=budget_limit)
        try:
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
        return {"input_tokens": tokens, "documents": documents}
    finally:
        engine.dispose()


def _fact(f: models.Fact) -> dict[str, Any]:
    state = EvidenceState(f.state)
    value = None if state == EvidenceState.ABSENT else f.value
    return {"field": f.field, "value": value, "page": f.page_no if f.page_no is not None else f.cited_page,
            "quote": f.quote, "state": _STATES[state]}
