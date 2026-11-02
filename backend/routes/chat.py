"""Chat routes: thread, ask, apply a chat-proposed edit (to the latest version, D9), suggestions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend import chat, db, models
from backend.context import AppContext
from backend.edits import EditError, save_cell_edit
from backend.routes.artifacts import artifact_json
from backend.routes.common import ApiError, get_ctx, not_found
from backend.views import message_out

router = APIRouter(tags=["chat"])


class Ask(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


def _require_run(ctx: AppContext, run_id: int) -> None:
    with ctx.factory() as s:
        if s.get(models.Run, run_id) is None:
            raise not_found("run", run_id)


@router.get("/runs/{run_id}/messages")
async def list_messages(run_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    _require_run(ctx, run_id)
    with ctx.factory() as s:
        rows = s.scalars(select(models.Message).where(models.Message.run_id == run_id).order_by(models.Message.id))
        return {"messages": [message_out(m) for m in rows]}


@router.post("/runs/{run_id}/messages", status_code=201)
async def ask(run_id: int, body: Ask, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    if not body.content.strip():
        raise ApiError(422, "invalid_request", "Type a question first.")
    lock = ctx.lock(("chat", run_id))
    if lock.locked():
        raise ApiError(409, "chat_busy", "Wait for the current answer to finish.")
    async with lock:
        try:
            user, bot = await chat.answer(ctx, run_id, body.content)
        except chat.ChatFailed as exc:
            raise ApiError(exc.status, exc.code, exc.message, retryable=exc.status >= 500 or None) from None
    return {"user_message": message_out(user), "assistant_message": message_out(bot)}


@router.post("/runs/{run_id}/messages/{message_id}/edits/{edit_id}/apply")
async def apply_proposed_edit(run_id: int, message_id: int, edit_id: int,
                              ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        msg = s.get(models.Message, message_id)
        if msg is None or msg.run_id != run_id:
            raise not_found("message", message_id)
        proposal = next((e for e in (msg.proposed_edits or []) if e.get("id") == edit_id), None)
        if proposal is None:
            raise not_found("proposed edit", edit_id)
        if proposal.get("status") == "applied":
            raise ApiError(409, "already_applied", "This edit was already applied.",
                           version=proposal.get("applied_version"))
        run = s.get(models.Run, run_id)
        if run.artifact is None:
            raise ApiError(409, "no_report", "This run has no report to edit.")
        artifact_id = run.artifact.id
    try:
        saved = save_cell_edit(ctx, artifact_id, proposal["block_id"], proposal["row_key"],
                               proposal["column_key"], proposal["value"], note="chat edit")
    except EditError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from None
    with db.session_scope(ctx.factory) as s:
        msg = s.get(models.Message, message_id)
        msg.proposed_edits = [  # reassign: JSON columns do not track in-place mutation
            {**e, "status": "applied", "applied_version": saved.version} if e.get("id") == edit_id else e
            for e in msg.proposed_edits]
        s.flush()
        out = message_out(msg)
    return {"message": out, "artifact": artifact_json(ctx, artifact_id), "version": saved.version,
            "previous_version": saved.previous_version, "stale_sections": saved.stale_sections}


@router.get("/runs/{run_id}/suggestions")
async def get_suggestions(run_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        items = await chat.suggestions(ctx, run_id)
    except chat.ChatFailed as exc:
        raise ApiError(exc.status, exc.code, exc.message) from None
    return {"suggestions": items}
