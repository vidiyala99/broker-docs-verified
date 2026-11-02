"""Health, workspace and workflow routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select

from backend import db, models
from backend.context import APP_NAME, AppContext
from backend.routes.common import get_ctx, not_found

router = APIRouter(tags=["workspace"])


def _limits(ctx: AppContext) -> dict[str, Any]:
    lim = ctx.settings.limits
    return {"max_pages": lim.max_pages, "max_bytes": lim.max_bytes,
            "max_megabytes": round(lim.max_bytes / (1024 * 1024), 2),
            "max_documents_per_run": lim.max_documents_per_run, "accepts": "application/pdf",
            "run_token_budget": ctx.manager.budget_limit}


def _workflow_summary(wf: models.Workflow) -> dict[str, Any]:
    return {"id": wf.id, "slug": wf.slug, "name": wf.name, "category": wf.category}


def _workflow_full(wf: models.Workflow) -> dict[str, Any]:
    return {**_workflow_summary(wf), "input_slots": wf.input_slots, "instructions": wf.instructions,
            "section_plan": wf.section_plan, "fact_schema": wf.fact_schema}


@router.get("/health")
async def health(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    # Reports only whether a model key is configured, never the key itself (R3).
    return {"status": "ok", "app": APP_NAME, "model_configured": ctx.model_configured}


@router.get("/workspace")
async def workspace(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        ws = s.get(models.Workspace, db.DEFAULT_WORKSPACE_ID)
        workflows = s.scalars(select(models.Workflow).order_by(models.Workflow.id)).all()
        return {"id": ws.id, "name": ws.name, "app_name": APP_NAME, "model_configured": ctx.model_configured,
                "limits": _limits(ctx), "workflows": [_workflow_summary(w) for w in workflows],
                "default_workflow": db.COMPARE_QUOTES_SLUG}


@router.get("/workflows")
async def list_workflows(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        return {"workflows": [_workflow_summary(w) for w in
                              s.scalars(select(models.Workflow).order_by(models.Workflow.id))]}


def find_workflow(s, key: str | int) -> models.Workflow | None:
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        return s.get(models.Workflow, int(key))
    return s.scalar(select(models.Workflow).where(models.Workflow.slug == key))


@router.get("/workflows/{key}")
async def get_workflow(key: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        wf = find_workflow(s, key)
        if wf is None:
            raise not_found("workflow", key)
        return _workflow_full(wf)
