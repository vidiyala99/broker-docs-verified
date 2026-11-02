"""Proposal template routes (slice 2): list and show the seeded templates."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select

from backend import db, models, proposal
from backend.context import AppContext
from backend.routes.common import get_ctx, not_found
from backend.routes.workspace import find_workflow

router = APIRouter(tags=["templates"])


@router.get("/templates")
async def list_templates(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        rows = s.scalars(select(models.ProposalTemplate).order_by(models.ProposalTemplate.id))
        return {"templates": [proposal.template_summary(t) for t in rows]}


@router.get("/templates/{key}")
async def get_template(key: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        tpl = proposal.find_template(s, key)
        if tpl is None:
            raise not_found("template", key)
        return proposal.template_full(tpl, find_workflow(s, db.PROPOSAL_BUILDER_SLUG))
