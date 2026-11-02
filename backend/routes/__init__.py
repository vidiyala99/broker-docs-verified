"""REST routes, mounted under /api by backend.main (contract: backend/API.md)."""

from fastapi import APIRouter

from backend.routes import artifacts, chat, documents, runs, templates, workspace

api_router = APIRouter()
for _module in (workspace, templates, documents, runs, artifacts, chat):
    api_router.include_router(_module.router)

__all__ = ["api_router"]
