"""REST routes, mounted under /api by backend.main (contract: backend/API.md)."""

from fastapi import APIRouter

from backend.routes import (
    artifacts,
    certificates,
    chat,
    documents,
    loss_runs,
    queue,
    runs,
    tasks,
    templates,
    workspace,
)

api_router = APIRouter()
for _module in (workspace, templates, documents, runs, artifacts, chat, tasks, queue, certificates, loss_runs):
    api_router.include_router(_module.router)

__all__ = ["api_router"]
