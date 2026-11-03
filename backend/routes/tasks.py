"""Task routes (slice 3b, spec 1.3): a broker's to-dos, optionally linked to a run or to one
Policy Check discrepancy of a run.

    GET    /api/tasks          every task (newest first) with run_label, run_missing, discrepancy
    POST   /api/tasks          {title, due_date?, run_id?, discrepancy_item_id?}   -> 201 task
    PATCH  /api/tasks/{id}     any of title, due_date, status, run_id, discrepancy_item_id
    DELETE /api/tasks/{id}     204 (undo is client-side: the UI sends DELETE when its toast closes, D6)

Bodies are validated here, not by pydantic, so every problem is one `400 invalid_task` with the
`field` it concerns (O10). Rules:

- title: control characters removed (the discrepancy-note pattern, A2), trimmed, 1 to 200 chars.
- due_date: `YYYY-MM-DD` or null. status: `open | done`; done sets completed_at, open clears it.
- run_id: a plain id (no FK, D2) that must name a run when written; a run that goes away later
  shows `run_missing: true`.
- discrepancy_item_id: needs run_id and must be an item of that run's current report (D3). PATCH
  checks the merged result; changing or clearing run_id without sending an item clears it (O1).
- Tasks use the default workspace and are not filtered by workspace (as runs, SC4).
- Nothing here changes a discrepancy state, and no discrepancy route changes a task (R5).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend import db, models
from backend.context import AppContext
from backend.routes.common import ApiError, get_ctx, not_found
from backend.routes.runs import _NOTE_CONTROL
from backend.schemas import ArtifactContent

router = APIRouter(tags=["tasks"])

_CREATE_FIELDS = ("title", "due_date", "run_id", "discrepancy_item_id")
_PATCH_FIELDS = (*_CREATE_FIELDS, "status")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
MAX_ITEM_ID_CHARS = 200


def _invalid(field: str | None, message: str) -> ApiError:
    return ApiError(400, "invalid_task", message, field=field)


def _ts(value: datetime | None) -> str | None:
    """ISO 8601 UTC. SQLite hands timestamps back without a zone, so a fresh row and a reloaded
    one would print differently; both are UTC (models.utcnow)."""
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


# --------------------------------------------------------------------- field checks


def _object(body: Any, allowed: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise _invalid(None, "Send the task as a JSON object.")
    unknown = sorted(k for k in body if k not in allowed)
    if unknown:
        raise _invalid(unknown[0], f"{unknown[0]} is not a task field; use " + ", ".join(allowed) + ".")
    return body


def _title(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid("title", "The title must be text.")
    clean = _NOTE_CONTROL.sub("", value).strip()
    if not clean:
        raise _invalid("title", "Add a title.")
    if len(clean) > models.MAX_TASK_TITLE_CHARS:
        raise _invalid("title", f"The title can be at most {models.MAX_TASK_TITLE_CHARS} characters.")
    return clean


def _due_date(value: Any) -> date | None:
    if value is None:
        return None
    message = "The due date must be a date written as YYYY-MM-DD, or null."
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise _invalid("due_date", message)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _invalid("due_date", message) from None


def _status(value: Any) -> str:
    if not isinstance(value, str) or value not in models.TASK_STATUSES:
        raise _invalid("status", "The status must be open or done.")
    return value


def _run_id(s: Session, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid("run_id", "run_id must be a run id or null.")
    if s.get(models.Run, value) is None:
        raise _invalid("run_id", f"No run {value}.")
    return value


def _item_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_ITEM_ID_CHARS:
        raise _invalid("discrepancy_item_id", "discrepancy_item_id must be a discrepancy id or null.")
    return value


def _link_title(s: Session, run_id: int | None, item_id: str) -> str:
    """The block title of `item_id` in the run's current report (D3), else 400."""
    if run_id is None:
        raise _invalid("discrepancy_item_id", "A discrepancy link needs its run: send run_id too.")
    run = s.get(models.Run, run_id)
    if run is None:
        raise _invalid("run_id", f"No run {run_id}.")
    if run.artifact is None:
        raise _invalid("discrepancy_item_id", "This run has no report yet, so it has no discrepancy to link.")
    block = next((b for b in models.discrepancy_blocks(run.artifact.latest_content()) if b.id == item_id), None)
    if block is None:
        raise _invalid("discrepancy_item_id", "This discrepancy is not in the run's current report.")
    return block.title[: models.MAX_DISCREPANCY_TITLE_CHARS]


# --------------------------------------------------------------------- output


def _tasks_out(s: Session, tasks: list[models.Task]) -> list[dict[str, Any]]:
    """Tasks with their read-only link fields. Loads per distinct run, never per task (A1, P1):
    the runs and workflow names, the current report version of each run a discrepancy link
    points at, and those runs' discrepancy states. A linked run with no report has no blocks,
    so its items read in_report=false. Refreshes a saved discrepancy title that differs from
    the live one (D8)."""
    run_ids = {t.run_id for t in tasks if t.run_id is not None}
    runs: dict[int, models.Run] = {}
    if run_ids:
        runs = {r.id: r for r in s.scalars(select(models.Run).where(models.Run.id.in_(run_ids))
                                          .options(selectinload(models.Run.workflow)))}
    linked = {t.run_id for t in tasks if t.discrepancy_item_id and t.run_id in runs}
    blocks: dict[int, dict[str, Any]] = {rid: {} for rid in linked}
    states: dict[tuple[int, str], models.DiscrepancyState] = {}
    if linked:
        current = s.execute(
            select(models.Artifact.run_id, models.ArtifactVersion.content)
            .join(models.ArtifactVersion, (models.ArtifactVersion.artifact_id == models.Artifact.id)
                  & (models.ArtifactVersion.version == models.Artifact.current_version))
            .where(models.Artifact.run_id.in_(linked)))
        for rid, content in current:
            blocks[rid] = {b.id: b for b in models.discrepancy_blocks(ArtifactContent.model_validate(content))}
        states = {(r.run_id, r.item_id): r for r in s.scalars(
            select(models.DiscrepancyState).where(models.DiscrepancyState.run_id.in_(linked)))}

    out = []
    for t in tasks:
        run = runs.get(t.run_id) if t.run_id is not None else None
        discrepancy = None
        if run is not None and t.discrepancy_item_id:
            block = blocks.get(run.id, {}).get(t.discrepancy_item_id)
            if block is not None:
                live = block.title[: models.MAX_DISCREPANCY_TITLE_CHARS]
                if t.discrepancy_title != live:
                    t.discrepancy_title = live
            row = states.get((run.id, t.discrepancy_item_id))
            discrepancy = {"item_id": t.discrepancy_item_id, "title": t.discrepancy_title,
                           "status": row.status if row else "open", "in_report": block is not None}
        label = None
        if run is not None:
            label = f"{run.workflow.name} · Run {run.id}" if run.workflow else f"Run {run.id}"
        out.append({
            "id": t.id,
            "title": t.title,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "status": t.status,
            "run_id": t.run_id,
            "discrepancy_item_id": t.discrepancy_item_id,
            "created_at": _ts(t.created_at),
            "updated_at": _ts(t.updated_at),
            "completed_at": _ts(t.completed_at),
            "run_label": label,
            "run_missing": t.run_id is not None and run is None,
            "discrepancy": discrepancy,
        })
    return out


def _get_task(s: Session, task_id: int) -> models.Task:
    task = s.get(models.Task, task_id)
    if task is None:
        raise not_found("task", task_id)
    return task


# --------------------------------------------------------------------- routes


@router.get("/tasks")
async def list_tasks(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db.session_scope(ctx.factory) as s:  # writes only refreshed discrepancy titles (D8)
        tasks = list(s.scalars(select(models.Task).order_by(models.Task.created_at.desc(), models.Task.id.desc())))
        return {"tasks": _tasks_out(s, tasks)}


@router.post("/tasks", status_code=201)
async def create_task(body: Any = Body(...), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    body = _object(body, _CREATE_FIELDS)
    if "title" not in body:
        raise _invalid("title", "Add a title.")
    with db.session_scope(ctx.factory) as s:
        title = _title(body["title"])
        due = _due_date(body.get("due_date"))
        run_id = _run_id(s, body.get("run_id"))
        item = _item_id(body.get("discrepancy_item_id"))
        item_title = _link_title(s, run_id, item) if item is not None else None
        now = models.utcnow()
        task = models.Task(workspace_id=db.DEFAULT_WORKSPACE_ID, title=title, due_date=due, status="open",
                           run_id=run_id, discrepancy_item_id=item, discrepancy_title=item_title,
                           created_at=now, updated_at=now, completed_at=None)
        s.add(task)
        s.flush()
        return _tasks_out(s, [task])[0]


@router.patch("/tasks/{task_id}")
async def patch_task(task_id: int, body: Any = Body(...), ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db.session_scope(ctx.factory) as s:
        task = _get_task(s, task_id)
        body = _object(body, _PATCH_FIELDS)
        if not body:
            raise _invalid(None, "Send at least one field to change.")
        # Validate everything against the merged post-patch state, then apply (O1).
        title = _title(body["title"]) if "title" in body else task.title
        due = _due_date(body["due_date"]) if "due_date" in body else task.due_date
        status = _status(body["status"]) if "status" in body else task.status
        run_id = _run_id(s, body["run_id"]) if "run_id" in body else task.run_id
        if "discrepancy_item_id" in body:
            item = _item_id(body["discrepancy_item_id"])
            item_title = _link_title(s, run_id, item) if item is not None else None
        elif "run_id" in body and run_id != task.run_id:
            item, item_title = None, None  # the run changed or was cleared: drop the stale item (O1)
        else:
            item, item_title = task.discrepancy_item_id, task.discrepancy_title
        now = models.utcnow()
        if status != task.status:
            task.completed_at = now if status == "done" else None
        task.title, task.due_date, task.status = title, due, status
        task.run_id, task.discrepancy_item_id, task.discrepancy_title = run_id, item, item_title
        task.updated_at = now
        s.flush()
        return _tasks_out(s, [task])[0]


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: int, ctx: AppContext = Depends(get_ctx)) -> Response:
    with db.session_scope(ctx.factory) as s:
        s.delete(_get_task(s, task_id))
    return Response(status_code=204)
