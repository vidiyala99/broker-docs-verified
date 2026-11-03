"""Work queue route: `GET /api/queue?line=commercial|personal` (rules in backend/work_queue.py)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from backend import work_queue
from backend.context import AppContext
from backend.routes.common import get_ctx

router = APIRouter(tags=["queue"])


@router.get("/queue")
async def get_queue(line: Literal["commercial", "personal"] = Query(...),
                    ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """The line's work grouped by what needs action. A missing or unknown `line` is 422
    invalid_request, like `?line=` on GET /api/runs."""
    with ctx.factory() as s:
        return work_queue.load_queue(s, line, is_active=ctx.manager.is_active)
