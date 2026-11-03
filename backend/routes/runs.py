"""Run routes: create (idempotent), list, get, retry, cancel, and the SSE progress stream."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select

from backend import db, models, proposal
from backend.context import AppContext
from backend.pipeline.runner import SlotError, format_sse
from backend.routes.common import ApiError, get_ctx, not_found
from backend.routes.workspace import find_workflow
from backend.schemas import ParameterErrors
from backend.views import run_out

router = APIRouter(tags=["runs"])


SlotKey = Annotated[str, StringConstraints(min_length=1, max_length=50)]


class RunCreate(BaseModel):
    document_ids: list[int] = Field(..., max_length=64)
    workflow: str | int = db.COMPARE_QUOTES_SLUG  # slug or id
    parameters: dict[str, Any] = Field(default_factory=dict)
    # document_id -> input slot key (slice 3, D8); omitted = the workflow's only slot
    slots: dict[int, SlotKey] | None = Field(default=None, max_length=64)


def _run_json(ctx: AppContext, run_id: int) -> dict[str, Any]:
    with ctx.factory() as s:
        run = s.get(models.Run, run_id)
        if run is None:
            raise not_found("run", run_id)
        return run_out(run, active=ctx.manager.is_active(run_id))


@router.post("/runs")
async def create_run(body: RunCreate, ctx: AppContext = Depends(get_ctx)) -> JSONResponse:
    parameters = body.parameters
    with ctx.factory() as s:
        wf = find_workflow(s, body.workflow)
        if wf is None:
            raise not_found("workflow", body.workflow)
        workflow_id = wf.id
        if wf.slug == db.PROPOSAL_BUILDER_SLUG:
            # Untrusted outline (it reaches the prompt): validate, then snapshot the template (D6).
            try:
                parameters = proposal.prepare_parameters(s, wf, body.parameters)
            except ParameterErrors as exc:
                fields = [{"loc": ["body", "parameters", *(str(x) for x in loc)], "message": msg}
                          for loc, msg in exc.errors]
                raise ApiError(422, "invalid_request", "The proposal settings are not valid.",
                               fields=fields) from None
    try:
        run_id, created = ctx.manager.submit(workflow_id, body.document_ids, parameters, slots=body.slots)
    except SlotError as exc:
        raise ApiError(400, "invalid_run", str(exc)) from None
    except ValueError as exc:
        limit = ctx.settings.limits.max_documents_per_run if "limit" in str(exc) else None
        raise ApiError(422, "invalid_run", str(exc), limit=limit) from None
    except LookupError as exc:
        raise ApiError(404, "not_found", str(exc).capitalize() + ".") from None
    return JSONResponse({"run": _run_json(ctx, run_id), "created": created}, status_code=201 if created else 200)


@router.get("/runs")
async def list_runs(limit: int = Query(20, ge=1, le=100), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        runs = s.scalars(select(models.Run).order_by(models.Run.id.desc()).limit(limit))
        return {"runs": [run_out(r, active=ctx.manager.is_active(r.id)) for r in runs]}


@router.get("/runs/{run_id}")
async def get_run(run_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return _run_json(ctx, run_id)


@router.post("/runs/{run_id}/retry", status_code=202)
async def retry_run(run_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        started = ctx.manager.retry(run_id)
    except LookupError:
        raise not_found("run", run_id) from None
    if not started:
        run = _run_json(ctx, run_id)
        raise ApiError(409, "not_retryable", f"This run is {run['status']} and cannot be retried now.",
                       run_status=run["status"])
    return {"run": _run_json(ctx, run_id)}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    _run_json(ctx, run_id)  # 404 first
    cancelled = await ctx.manager.cancel(run_id)
    return {"run": _run_json(ctx, run_id), "cancelled": cancelled}


@router.get("/runs/{run_id}/events")
async def run_events(run_id: int, request: Request, last_event_id: int | None = Query(None),
                     ctx: AppContext = Depends(get_ctx)) -> StreamingResponse:
    """Server-sent events: replay after Last-Event-ID, then live until run.end (see API.md)."""
    _run_json(ctx, run_id)
    header = request.headers.get("last-event-id")
    if last_event_id is None and header and header.strip().isdigit():
        last_event_id = int(header.strip())
    keepalive = ctx.settings.sse_keepalive_s

    async def stream():
        events = ctx.manager.events(run_id, last_event_id)
        pending: asyncio.Task | None = None
        try:
            yield "retry: 3000\n\n"
            while True:
                if pending is None:
                    pending = asyncio.ensure_future(events.__anext__())
                done, _ = await asyncio.wait({pending}, timeout=keepalive)
                if not done:
                    yield ": keepalive\n\n"
                    continue
                task, pending = pending, None
                try:
                    event = task.result()
                except StopAsyncIteration:
                    break
                yield format_sse(event)
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                with contextlib.suppress(BaseException):
                    await pending
            with contextlib.suppress(BaseException):
                await events.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
