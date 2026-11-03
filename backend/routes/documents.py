"""Document routes: upload (T7 checks), AMS upload (slice 3 T1), list, get, serve the PDF, delete."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy import select

from backend import db, models
from backend.context import AppContext
from backend.pipeline.parse import ParseError, ParseErrorCode
from backend.pipeline.runner import DocumentRef, load_stored_bytes
from backend.routes.common import ApiError, get_ctx, not_found
from backend.ams import DEFAULT_AMS_LIMITS, AmsError
from backend.uploads import (
    PreparedAms,
    inspect_pdf,
    new_stored_name,
    prepare_ams,
    remove_stored,
    safe_display_name,
    store_bytes,
    store_original,
)
from backend.views import document_out

log = logging.getLogger("brokerdocs.api")
router = APIRouter(tags=["documents"])
_CHUNK = 1024 * 1024


async def _read_capped(upload: UploadFile, cap: int) -> bytes | None:
    """At most `cap` bytes; None when the file is larger (never read it whole)."""
    buf = bytearray()
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            return bytes(buf)
        buf.extend(chunk)
        if len(buf) > cap:
            return None


def _reject(filename: str, exc: ParseError | AmsError) -> dict[str, Any]:
    return {"filename": filename, "code": str(exc.code), "message": exc.message, "limit": exc.limit}


def _too_many_documents(limits) -> ParseError:
    return ParseError(ParseErrorCode.TOO_MANY_DOCUMENTS,
                      f"Only {limits.max_documents_per_run} files can be added at once; the limit is "
                      f"{limits.max_documents_per_run} documents per run.",
                      limit=limits.max_documents_per_run)


@router.post("/documents")
async def upload_documents(files: list[UploadFile] = File(...), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    limits = ctx.settings.limits
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for upload in files:
        name = safe_display_name(upload.filename)
        try:
            if len(accepted) >= limits.max_documents_per_run:
                raise _too_many_documents(limits)
            data = await _read_capped(upload, limits.max_bytes)
            if data is None:
                mb = f"{limits.max_bytes / (1024 * 1024):g} MB"
                raise ParseError(ParseErrorCode.TOO_LARGE,
                                 f"This file is larger than {mb}; the limit is {mb} per document.",
                                 limit=limits.max_bytes)
            info = await asyncio.to_thread(inspect_pdf, data, limits)
        except ParseError as exc:
            rejected.append(_reject(name, exc))
            continue
        finally:
            await upload.close()
        stored = new_stored_name()
        await asyncio.to_thread(store_bytes, data, stored)
        try:
            with db.session_scope(ctx.factory) as s:
                doc = models.Document(workspace_id=db.DEFAULT_WORKSPACE_ID, filename=name, stored_name=stored,
                                      size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                                      page_count=info.page_count, parse_error=info.parse_error,
                                      parse_message=info.parse_message)
                s.add(doc)
                s.flush()
                accepted.append(document_out(doc))
        except Exception:
            remove_stored(stored)
            raise
    log.info("upload accepted=%d rejected=%d", len(accepted), len(rejected))
    return {"documents": accepted, "rejected": rejected}


AMS_KIND = "ams"


def _store_ams(prepared: PreparedAms, stored: str) -> None:
    store_bytes(prepared.pdf, stored)
    store_original(prepared.original, stored)


@router.post("/documents/ams")
async def upload_ams(files: list[UploadFile] = File(...), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """AMS exports (JSON or CSV), rendered to a PDF at upload (Policy Check, R1 D4 A).

    The stored PDF takes the normal file, parse and verify path; the original is kept
    beside it. Document kind is "ams"; each accepted item also carries an "ams" summary.
    """
    limits = ctx.settings.limits
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for upload in files:
        name = safe_display_name(upload.filename)
        try:
            if len(accepted) >= limits.max_documents_per_run:
                raise _too_many_documents(limits)
            cap = min(DEFAULT_AMS_LIMITS.max_bytes, limits.max_bytes)
            data = await _read_capped(upload, cap)
            if data is None:
                kb = f"{cap / 1024:g} KB"
                raise AmsError("too_large", f"This file is larger than {kb}; the limit is {kb} per AMS export.",
                               limit=cap)
            prepared = await asyncio.to_thread(prepare_ams, data, name, limits)
        except (ParseError, AmsError) as exc:
            rejected.append(_reject(name, exc))
            continue
        finally:
            await upload.close()
        stored = new_stored_name()
        try:
            await asyncio.to_thread(_store_ams, prepared, stored)
            with db.session_scope(ctx.factory) as s:
                doc = models.Document(workspace_id=db.DEFAULT_WORKSPACE_ID, filename=name, stored_name=stored,
                                      size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                                      page_count=prepared.page_count, kind=AMS_KIND)
                s.add(doc)
                s.flush()
                out = document_out(doc)
        except Exception:
            remove_stored(stored)
            raise
        out["ams"] = {"format": prepared.rendered.record.format,
                      "field_count": len(prepared.rendered.record.fields),
                      "unmapped": list(prepared.facts.unmapped),
                      "notices": list(prepared.rendered.record.notices)}
        accepted.append(out)
    log.info("ams upload accepted=%d rejected=%d", len(accepted), len(rejected))
    return {"documents": accepted, "rejected": rejected}


@router.get("/documents")
async def list_documents(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        docs = s.scalars(select(models.Document).order_by(models.Document.id.desc()))
        return {"documents": [document_out(d) for d in docs]}


@router.get("/documents/{document_id}")
async def get_document(document_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        doc = s.get(models.Document, document_id)
        if doc is None:
            raise not_found("document", document_id)
        return document_out(doc)


@router.get("/documents/{document_id}/file")
async def get_document_file(document_id: int, ctx: AppContext = Depends(get_ctx)) -> Response:
    with ctx.factory() as s:
        doc = s.get(models.Document, document_id)
        if doc is None:
            raise not_found("document", document_id)
        ref = DocumentRef(doc.id, doc.filename, doc.stored_name)
    try:
        data = await asyncio.to_thread(load_stored_bytes, ref)
    except (OSError, ValueError):
        raise ApiError(404, "file_missing", "Couldn't open this file. It is no longer on disk.") from None
    ascii_name = ref.filename.encode("ascii", "replace").decode().replace('"', "").replace("?", "_")
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(ref.filename)}",
        "Cache-Control": "private, max-age=3600",
    })


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: int, ctx: AppContext = Depends(get_ctx)) -> Response:
    with db.session_scope(ctx.factory) as s:
        doc = s.get(models.Document, document_id)
        if doc is None:
            raise not_found("document", document_id)
        used = s.scalars(select(models.RunDocument.run_id).where(models.RunDocument.document_id == document_id)
                         .limit(1)).first()
        if used is not None:
            raise ApiError(409, "document_in_use", f"This document is part of run {used}, so it is kept.",
                           run_id=used)
        stored = doc.stored_name
        for p in s.scalars(select(models.Passage).where(models.Passage.document_id == document_id)):
            s.delete(p)
        s.delete(doc)
    remove_stored(stored)
    return Response(status_code=204)
