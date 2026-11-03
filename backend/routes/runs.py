"""Run routes: create (idempotent; optional client_name and, for proposals, source_run_id), list
(optionally by line), get, PATCH (line, client_name, queue_state: slice 3b and the work queue),
retry, cancel, the SSE progress stream, and the Policy Check discrepancy list and its per-item
status (slice 3 T6), each item with its linked task (slice 3b, O4)."""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from sqlalchemy import select

from backend import db, models, proposal
from backend.context import AppContext
from backend.pipeline.runner import SlotError, format_sse
from backend.routes.common import ApiError, get_ctx, not_found
from backend.routes.workspace import find_workflow
from backend.schemas import DiscrepancyBlock, ParameterErrors, Severity
from backend.views import discrepancy_sources, run_out

router = APIRouter(tags=["runs"])


SlotKey = Annotated[str, StringConstraints(min_length=1, max_length=50)]


class RunCreate(BaseModel):
    document_ids: list[int] = Field(..., max_length=64)
    workflow: str | int = db.COMPARE_QUOTES_SLUG  # slug or id
    parameters: dict[str, Any] = Field(default_factory=dict)
    # document_id -> input slot key (slice 3, D8); omitted = the workflow's only slot
    slots: dict[int, SlotKey] | None = Field(default=None, max_length=64)
    # Slice 3b: "commercial" | "personal". Typed Any on purpose: a bad value is a domain
    # 400 invalid_run checked in the route (O10), not the global 422.
    line: Any = None
    # Work queue, both typed Any for the same reason as `line` (400 invalid_run, not 422):
    # the broker's client name, and for proposal-builder the done Compare Quotes run it is built from.
    client_name: Any = None
    source_run_id: Any = None


def _check_line(value: Any) -> str:
    if not isinstance(value, str) or value not in models.RUN_LINES:
        raise ApiError(400, "invalid_run", "line must be commercial or personal.")
    return value


_NOTE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # also used for discrepancy notes below


def _clean_client_name(value: Any) -> str | None:
    """A client name: control characters dropped (the note pattern), whitespace runs made one
    space, trimmed. Empty means no name (None). Over 120 characters is 400 invalid_run."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, "invalid_run", "client_name must be text.")
    cleaned = " ".join(_NOTE_CONTROL.sub("", value).split())
    if len(cleaned) > models.MAX_CLIENT_NAME_CHARS:
        raise ApiError(400, "invalid_run",
                       f"client_name can be at most {models.MAX_CLIENT_NAME_CHARS} characters.")
    return cleaned or None


def _check_source_run(s, value: Any) -> models.Run:
    """`source_run_id` must name a Compare Quotes run that is done (M3)."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ApiError(400, "invalid_run", "source_run_id must be a run id.")
    source = s.get(models.Run, value)
    if source is None or source.workflow is None or source.workflow.slug != db.COMPARE_QUOTES_SLUG:
        raise ApiError(400, "invalid_run", "source_run_id must name a Compare Quotes run.")
    if str(source.status) != "done":
        raise ApiError(400, "invalid_run", "A proposal can be built only from a comparison that is done.")
    return source


def _run_json(ctx: AppContext, run_id: int) -> dict[str, Any]:
    with ctx.factory() as s:
        run = s.get(models.Run, run_id)
        if run is None:
            raise not_found("run", run_id)
        return run_out(run, active=ctx.manager.is_active(run_id))


@router.post("/runs")
async def create_run(body: RunCreate, ctx: AppContext = Depends(get_ctx)) -> JSONResponse:
    parameters = body.parameters
    line = None if body.line is None else _check_line(body.line)
    client_name = _clean_client_name(body.client_name)
    with ctx.factory() as s:
        wf = find_workflow(s, body.workflow)
        if wf is None:
            raise not_found("workflow", body.workflow)
        workflow_id = wf.id
        source = None
        if body.source_run_id is not None:
            if wf.slug != db.PROPOSAL_BUILDER_SLUG:
                raise ApiError(400, "invalid_run", "source_run_id is only for proposal-builder runs.")
            source = _check_source_run(s, body.source_run_id)
        if wf.slug == db.PROPOSAL_BUILDER_SLUG:
            # Untrusted outline (it reaches the prompt): validate, then snapshot the template (D6).
            try:
                parameters = proposal.prepare_parameters(s, wf, body.parameters)
            except ParameterErrors as exc:
                fields = [{"loc": ["body", "parameters", *(str(x) for x in loc)], "message": msg}
                          for loc, msg in exc.errors]
                raise ApiError(422, "invalid_request", "The proposal settings are not valid.",
                               fields=fields) from None
        if source is not None:
            # M3: inside parameters, so the same documents built from two comparisons are two runs.
            parameters = {**parameters, "source_run_id": source.id}
            if line is None:
                line = source.line or "commercial"  # M5: the proposal takes the comparison's line
    try:
        run_id, created = ctx.manager.submit(workflow_id, body.document_ids, parameters, slots=body.slots,
                                             line=line, client_name=client_name)
    except SlotError as exc:
        raise ApiError(400, "invalid_run", str(exc)) from None
    except ValueError as exc:
        limit = ctx.settings.limits.max_documents_per_run if "limit" in str(exc) else None
        raise ApiError(422, "invalid_run", str(exc), limit=limit) from None
    except LookupError as exc:
        raise ApiError(404, "not_found", str(exc).capitalize() + ".") from None
    return JSONResponse({"run": _run_json(ctx, run_id), "created": created}, status_code=201 if created else 200)


@router.get("/runs")
async def list_runs(limit: int = Query(20, ge=1, le=100),
                    line: Literal["commercial", "personal"] | None = Query(None),
                    ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Newest first. `line` (slice 3b) filters; without it every run is listed, as before."""
    query = select(models.Run)
    if line is not None:
        query = query.where(models.Run.line == line)
    with ctx.factory() as s:
        runs = s.scalars(query.order_by(models.Run.id.desc()).limit(limit))
        return {"runs": [run_out(r, active=ctx.manager.is_active(r.id)) for r in runs]}


@router.get("/runs/{run_id}")
async def get_run(run_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return _run_json(ctx, run_id)


@router.patch("/runs/{run_id}")
async def patch_run(run_id: int, body: Any = Body(...), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Change a run's tags: any non-empty subset of `line` (slice 3b), `client_name` and
    `queue_state` (work queue), and no other key. Everything is checked before anything changes."""
    with db.session_scope(ctx.factory) as s:
        run = s.get(models.Run, run_id)
        if run is None:
            raise not_found("run", run_id)
        if not isinstance(body, dict) or not body or not set(body) <= _PATCHABLE:
            raise ApiError(400, "invalid_run", "Send one or more of line, client_name and queue_state, "
                                               'for example {"line": "personal"}.')
        changes: dict[str, Any] = {}
        if "line" in body:
            changes["line"] = _check_line(body["line"])
        if "client_name" in body:
            changes["client_name"] = _clean_client_name(body["client_name"])  # "" or null clears
        if "queue_state" in body:
            state = body["queue_state"]
            if not isinstance(state, str) or state not in models.QUEUE_STATES:
                raise ApiError(400, "invalid_run", "queue_state must be open, done or dismissed.")
            active = ctx.manager.is_active(run_id) or str(run.status) in _ACTIVE
            if state != "open" and active:  # M1: a running item cannot leave the queue
                raise ApiError(409, "run_active", "This run is still running; it can be marked done or "
                                                  "dismissed once it finishes.", run_status=str(run.status))
            changes["queue_state"] = state
            changes["queue_changed_at"] = models.utcnow()
        for key, value in changes.items():
            setattr(run, key, value)
    return _run_json(ctx, run_id)


_PATCHABLE = {"line", "client_name", "queue_state"}
_ACTIVE = {"queued", "running", "composing"}


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


# --------------------------------------------------------------------- discrepancies (slice 3 T6)
#
# The list is the current artifact version's DiscrepancyBlocks, in report order, each with
# its decision from discrepancy_state. No bulk endpoint and no "accepted" status (D1, D2).

_SEVERITIES = tuple(s.value for s in Severity)
MAX_NOTE_CHARS = 2000


def _item_out(block: DiscrepancyBlock, row: models.DiscrepancyState | None,
              sources: dict[str, dict[str, list]] | None = None,
              task: models.Task | None = None) -> dict[str, Any]:
    out = block.model_dump(mode="json")
    out.update({
        "sources": (sources or {}).get(block.id, {}),
        "status": row.status if row else "open",
        "note": row.note if row else None,
        "changed_since_resolved": bool(row.changed_since_resolved) if row else False,
        "updated_at": _ts(row.updated_at) if row else None,
        # slice 3b (O4): the item's task, read-only; a task never changes this item's state (R5)
        "task": {"id": task.id, "status": task.status} if task else None,
    })
    return out


def _ts(value) -> str | None:
    return value.isoformat() if value is not None else None


def _discrepancy_rows(s, run_id: int) -> dict[str, models.DiscrepancyState]:
    return {r.item_id: r for r in s.scalars(select(models.DiscrepancyState)
                                           .where(models.DiscrepancyState.run_id == run_id))}


def _current_blocks(s, run_id: int) -> tuple[models.Run, list[DiscrepancyBlock]]:
    run = s.get(models.Run, run_id)
    if run is None:
        raise not_found("run", run_id)
    if run.artifact is None:
        raise ApiError(404, "no_report", "This run has no report yet.")
    return run, models.discrepancy_blocks(run.artifact.latest_content())


@router.get("/runs/{run_id}/discrepancies")
async def list_discrepancies(run_id: int, severity: str | None = Query(None, max_length=100),
                             hide_resolved: bool = Query(False),
                             ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Items of the current version with their status; `severity` is one tier or a comma list.
    `counts` and `open_counts` are per severity over every item (filters do not change them)."""
    wanted: set[str] | None = None
    if severity:
        wanted = {x.strip().lower() for x in severity.split(",") if x.strip()}
        unknown = sorted(wanted - set(_SEVERITIES))
        if unknown:
            raise ApiError(422, "invalid_request", f"Unknown severity {unknown[0]!r}; use one of "
                           + ", ".join(_SEVERITIES) + ".")
    with ctx.factory() as s:
        run, blocks = _current_blocks(s, run_id)
        rows = _discrepancy_rows(s, run_id)
        sources = discrepancy_sources(s, run, blocks)
        tasks = models.item_tasks(s, run_id)
        all_items = [_item_out(b, rows.get(b.id), sources, tasks.get(b.id)) for b in blocks]
        tally = models.discrepancy_counts(s, run, blocks=blocks, rows=rows)  # shared with the queue (R7)
        items = [it for it in all_items
                 if (wanted is None or it["severity"] in wanted) and not (hide_resolved and it["status"] == "resolved")]
        return {"run_id": run_id, "artifact_id": run.artifact.id, "version": run.artifact.current_version,
                "items": items, "counts": tally.counts, "open_counts": tally.open_counts, "total": tally.total,
                "resolved": tally.resolved}


class DiscrepancyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["open", "resolved"] | None = None
    note: str | None = Field(None, max_length=MAX_NOTE_CHARS)

    @field_validator("note")
    @classmethod
    def _clean(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return re.sub(r" {2,}", " ", _NOTE_CONTROL.sub("", v)).strip() or None

    @model_validator(mode="after")
    def _something(self) -> DiscrepancyPatch:
        if not self.model_fields_set:
            raise ValueError("send a status, a note, or both")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status must be open or resolved")
        return self


@router.patch("/runs/{run_id}/discrepancies/{item_id}")
async def patch_discrepancy(run_id: int, item_id: str, body: DiscrepancyPatch,
                            ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Set one item's status and/or note (`note: null` clears it)."""
    with db.session_scope(ctx.factory) as s:
        run, blocks = _current_blocks(s, run_id)
        block = next((b for b in blocks if b.id == item_id), None)
        if block is None:
            raise ApiError(404, "discrepancy_not_found", "This report has no such discrepancy.")
        if not _discrepancy_rows(s, run_id).get(item_id):  # a version stored before T6: reconcile first
            models.reconcile_discrepancy_states(s, run_id, s.get(models.Run, run_id).artifact.latest_content())
        kwargs: dict[str, Any] = {}
        if "status" in body.model_fields_set:
            kwargs["status"] = body.status
        if "note" in body.model_fields_set:
            kwargs["note"] = body.note
        row = models.update_discrepancy_state(s, run_id, item_id, **kwargs)
        return _item_out(block, row, discrepancy_sources(s, run, [block]), models.item_tasks(s, run_id).get(item_id))


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
