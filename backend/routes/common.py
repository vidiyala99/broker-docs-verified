"""Shared route plumbing: the error shape and the context dependency.

Every error response has one shape (documented in backend/API.md):

    {"error": {"code": "<stable_code>", "message": "<safe for the user>", ...extra}}

`message` never contains document text, file paths or secrets.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from backend.context import AppContext


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> dict[str, Any]:
        return error_body(self.code, self.message, **self.extra)


def error_body(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, **{k: v for k, v in extra.items() if v is not None}}}


def not_found(what: str, ident: Any) -> ApiError:
    return ApiError(404, "not_found", f"No {what} {ident}.")


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx
