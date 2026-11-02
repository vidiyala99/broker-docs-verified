"""Artifact routes: view (with chips), versions, cell edit, undo, regenerate prose,
feedback and export."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from backend import db, export, models
from backend.context import AppContext
from backend.edits import EditError, regenerate_prose, restore, save_cell_edit
from backend.routes.common import ApiError, get_ctx, not_found
from backend.views import artifact_out, chips, version_out

log = logging.getLogger("brokerdocs.api")
router = APIRouter(tags=["artifacts"])
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _edit_error(exc: EditError) -> ApiError:
    return ApiError(exc.status, exc.code, exc.message)


def artifact_json(ctx: AppContext, artifact_id: int, version: int | None = None) -> dict[str, Any]:
    with ctx.factory() as s:
        art = s.get(models.Artifact, artifact_id)
        if art is None:
            raise not_found("artifact", artifact_id)
        try:
            return artifact_out(s, art, version)
        except LookupError:
            raise ApiError(404, "version_not_found", f"This report has no version {version}.") from None


@router.get("/runs/{run_id}/artifact")
async def run_artifact(run_id: int, version: int | None = Query(None, ge=1),
                       ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        run = s.get(models.Run, run_id)
        if run is None:
            raise not_found("run", run_id)
        if run.artifact is None:
            raise ApiError(404, "no_report", "This run has no report yet.")
        art_id = run.artifact.id
    return artifact_json(ctx, art_id, version)


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: int, version: int | None = Query(None, ge=1),
                       ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return artifact_json(ctx, artifact_id, version)


@router.get("/artifacts/{artifact_id}/versions")
async def list_versions(artifact_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        art = s.get(models.Artifact, artifact_id)
        if art is None:
            raise not_found("artifact", artifact_id)
        return {"artifact_id": art.id, "current_version": art.current_version,
                "versions": [version_out(v, art.current_version) for v in reversed(art.versions)]}


class CellEdit(BaseModel):
    block_id: str = Field(..., max_length=200)
    row_key: str = Field(..., max_length=200)
    column_key: str = Field(..., max_length=10)
    value: str | None = Field(..., max_length=2000)
    base_version: int | None = Field(None, ge=1)

    @field_validator("value")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = _CONTROL.sub("", v).strip()
        return v or None


def _saved(ctx: AppContext, saved) -> dict[str, Any]:
    return {"artifact": artifact_json(ctx, saved.artifact_id), "version": saved.version,
            "previous_version": saved.previous_version, "stale_sections": saved.stale_sections}


@router.patch("/artifacts/{artifact_id}/cells")
async def edit_cell(artifact_id: int, body: CellEdit, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        saved = save_cell_edit(ctx, artifact_id, body.block_id, body.row_key, body.column_key, body.value,
                               base_version=body.base_version)
    except EditError as exc:
        raise _edit_error(exc) from None
    return _saved(ctx, saved)


class Undo(BaseModel):
    version: int = Field(..., ge=1)  # the version to bring back (an edit response's previous_version)
    base_version: int | None = Field(None, ge=1)


@router.post("/artifacts/{artifact_id}/undo")
async def undo(artifact_id: int, body: Undo, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        saved = restore(ctx, artifact_id, body.version, base_version=body.base_version)
    except EditError as exc:
        raise _edit_error(exc) from None
    return _saved(ctx, saved)


class Regenerate(BaseModel):
    base_version: int | None = Field(None, ge=1)


@router.post("/artifacts/{artifact_id}/regenerate")
async def regenerate(artifact_id: int, body: Regenerate | None = None,
                     ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Regenerate prose (stale sections) and Retry prose (compose fallback) are this one call."""
    try:
        result = await regenerate_prose(ctx, artifact_id, base_version=body.base_version if body else None)
    except EditError as exc:
        raise _edit_error(exc) from None
    return {"artifact": artifact_json(ctx, artifact_id), "version": result.version,
            "previous_version": result.previous_version, "fallback": result.fallback, "reason": result.reason}


class FeedbackIn(BaseModel):
    rating: Literal[-1, 0, 1]  # 0 clears the thumbs
    comment: str | None = Field(None, max_length=2000)
    version: int | None = Field(None, ge=1)


@router.put("/artifacts/{artifact_id}/feedback")
async def put_feedback(artifact_id: int, body: FeedbackIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db.session_scope(ctx.factory) as s:
        art = s.get(models.Artifact, artifact_id)
        if art is None:
            raise not_found("artifact", artifact_id)
        version = body.version or art.current_version
        if not any(v.version == version for v in art.versions):
            raise ApiError(404, "version_not_found", f"This report has no version {version}.")
        fb = s.scalar(select(models.Feedback).where(models.Feedback.artifact_id == artifact_id,
                                                    models.Feedback.version == version))
        if body.rating == 0:
            if fb is not None:
                s.delete(fb)
            return {"artifact_id": artifact_id, "version": version, "feedback": None}
        if fb is None:
            fb = models.Feedback(artifact_id=artifact_id, version=version, rating=body.rating)
            s.add(fb)
        fb.rating = body.rating
        fb.comment = (body.comment or "").strip() or None
        s.flush()
        return {"artifact_id": artifact_id, "version": version,
                "feedback": {"rating": fb.rating, "comment": fb.comment}}


@router.get("/artifacts/{artifact_id}/export")
async def export_artifact(artifact_id: int, format: Literal["xlsx", "pdf"] = Query(...),
                          version: int | None = Query(None, ge=1), ctx: AppContext = Depends(get_ctx)) -> Response:
    with ctx.factory() as s:
        art = s.get(models.Artifact, artifact_id)
        if art is None:
            raise not_found("artifact", artifact_id)
        number = version or art.current_version
        try:
            row = art.version_row(number)
        except LookupError:
            raise ApiError(404, "version_not_found", f"This report has no version {number}.") from None
        content = models.ArtifactContent.model_validate(row.content)
        data = export.ExportData(title=art.title, run_id=art.run_id, version=number, content=content,
                                 chips=chips(s, art.run, content))
    try:
        if format == "xlsx":
            blob, mime = await asyncio.to_thread(export.to_xlsx, data), export.XLSX_MIME
        else:
            blob, mime = await asyncio.to_thread(export.to_pdf, data), export.PDF_MIME
    except Exception as exc:
        # Type only: renderer messages can quote report text, which never goes to the log.
        log.error("export failed artifact=%s format=%s error=%s", artifact_id, format, type(exc).__name__)
        raise ApiError(500, "export_failed", "Export failed. Retry.") from None
    name = export.filename(data, format)
    headers = {"Content-Disposition": f'attachment; filename="{name}"',
               "X-Export-Tables": str(export.table_count(content))}
    if format == "xlsx":
        headers["X-Export-Note"] = "XLSX holds the report tables only."
    return Response(blob, media_type=mime, headers=headers)
