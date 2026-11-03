"""FastAPI app factory, lifespan and the local-only security baseline (T7, R3).

Run it (from broker-docs-verified/):

    backend/.venv/Scripts/python -m backend.main          # binds 127.0.0.1:8000 only

Baseline, outermost first:

    TrustedHost   Host header must be 127.0.0.1 or localhost (DNS-rebinding guard)
    CORS          only the UI dev origin may read responses (no credentials)
    LocalGuard    state-changing requests from any other Origin get 403; an upload
                  request larger than the caps allow gets 413; nosniff and no-store
                  headers on every response
    routes        /api/... (backend/routes, contract in backend/API.md)

Lifespan: init_db (schema check D10, tables + seed), reconcile_interrupted_runs (M6), then one
RunManager for the process; on shutdown every run task is cancelled and awaited.

Secrets (R3): the provider reads OPENAI_API_KEY from the environment only
(never from .env); without it the app still starts and every model call fails
with missing_key. A logging filter scrubs the key, and anything shaped like an
OpenAI key, from every log record. Routes never log document text.
"""

from __future__ import annotations

import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend import db
from backend.context import AppContext, Settings
from backend.edits import merge_edits_after_retry
from backend.pipeline.runner import RunManager, load_stored_bytes, reconcile_interrupted_runs
from backend.providers import MissingApiKey, OpenAIProvider, Provider, ProviderConfig
from backend.routes import api_router
from backend.routes.common import ApiError, error_body

log = logging.getLogger("brokerdocs.api")

# --------------------------------------------------------------------- secrets in logs

_KEY_SHAPE = re.compile(r"sk-[A-Za-z0-9_\-]{12,}")


def redact(text: str) -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if key and len(key) >= 8:
        text = text.replace(key, "[redacted]")
    return _KEY_SHAPE.sub("[redacted]", text)


def _redact_arg(value: Any) -> Any:
    """A str arg is redacted in place; any other arg whose text leaks becomes the redacted text."""
    if isinstance(value, str):
        return redact(value)
    try:
        text = str(value)
    except Exception:  # pragma: no cover - an arg that cannot print cannot leak through %s either
        return value
    clean = redact(text)
    return value if clean == text else clean


class RedactSecrets(logging.Filter):
    """Scrubs the API key from the message, its args and any traceback text.

    The record keeps its shape: `msg` stays the format string and `args` stays
    a tuple (or mapping) of the same length, because formatters read them.
    uvicorn's AccessFormatter unpacks `record.args` into five names, so
    replacing args with None (or ()) crashes every access-log line. Only when
    per-arg redaction cannot clean the message (a key split across args, or a
    %d arg that had to become text) does the record collapse to the redacted
    message with empty args.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - a broken format string is not ours to fix
            return True
        if redact(message) != message:
            self._scrub(record, message)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True

    @staticmethod
    def _scrub(record: logging.LogRecord, message: str) -> None:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_arg(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        try:
            out = record.getMessage()
        except Exception:  # e.g. a %d arg that is now redacted text
            out = None
        if out is None or redact(out) != out:
            record.msg, record.args = redact(message), ()


_FILTER = RedactSecrets()


def install_log_redaction() -> None:
    """Attach the filter to every handler that can see our records (idempotent)."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for name in (None, "uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            if _FILTER not in handler.filters:
                handler.addFilter(_FILTER)


# --------------------------------------------------------------------- provider


class UnconfiguredProvider(Provider):
    """Stands in when OPENAI_API_KEY is unset: the app runs, model calls fail as missing_key."""

    async def _send(self, request):
        raise MissingApiKey("No API key is configured for the model service.", label=request.label)


def default_provider() -> tuple[Provider, bool]:
    try:
        return OpenAIProvider.from_env(), True
    except MissingApiKey:
        log.warning("OPENAI_API_KEY is not set; model calls will fail until it is")
        return UnconfiguredProvider(ProviderConfig.from_env()), False


# --------------------------------------------------------------------- local guard (pure ASGI)


class LocalGuard:
    UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(self, app, *, settings: Settings):
        self.app = app
        self.origins = set(settings.ui_origins)
        self.max_upload = settings.max_upload_request_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        method = scope.get("method", "GET")
        origin = headers.get("origin")
        if method in self.UNSAFE and origin is not None and origin.rstrip("/") not in self.origins:
            return await self._deny(send, 403, "origin_not_allowed", "Requests from this origin are not allowed.")
        length = headers.get("content-length", "")
        if method == "POST" and length.isdigit() and int(length) > self.max_upload:
            return await self._deny(send, 413, "request_too_large",
                                    "This upload is larger than the per-run limits allow.")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                extra = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer")]
                if not any(k.lower() == b"cache-control" for k, _ in message.get("headers", [])):
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    async def _deny(send, status: int, code: str, message: str) -> None:
        body = json.dumps(error_body(code, message)).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                (b"x-content-type-options", b"nosniff")]})
        await send({"type": "http.response.body", "body": body})


# --------------------------------------------------------------------- error handlers


def _install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = [{"loc": [str(x) for x in e.get("loc", ())], "message": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(error_body("invalid_request", "The request is not valid.", fields=fields),
                            status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        codes = {404: "not_found", 405: "method_not_allowed", 413: "request_too_large"}
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(error_body(codes.get(exc.status_code, "http_error"), message),
                            status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _crash(request: Request, exc: Exception):
        log.error("unhandled error on %s %s: %s", request.method, request.url.path, type(exc).__name__)
        return JSONResponse(error_body("internal_error", "Something went wrong. Retry."), status_code=500)


# --------------------------------------------------------------------- factory


def create_app(settings: Settings | None = None, *, provider: Provider | None = None,
               engine=None, load_bytes=None) -> FastAPI:
    """Build the app. Tests pass a FakeProvider and an in-memory engine; nothing runs until lifespan."""
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        install_log_redaction()
        own_engine = engine is None
        eng = engine if engine is not None else db.make_engine(settings.database_url)
        db.init_db(eng)
        factory = db.make_session_factory(eng)
        reconcile_interrupted_runs(factory)
        prov, configured = (provider, True) if provider is not None else default_provider()
        manager = RunManager(factory, prov, load_bytes=load_bytes or load_stored_bytes, limits=settings.limits,
                             budget_limit=settings.budget_limit, merge_edits=merge_edits_after_retry)
        app.state.ctx = AppContext(settings=settings, engine=eng, factory=factory, manager=manager,
                                   provider=prov, model_configured=configured)
        try:
            yield
        finally:
            await manager.shutdown()
            if own_engine:
                eng.dispose()

    app = FastAPI(title="BrokerDocs local API", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    _install_handlers(app)
    app.include_router(api_router, prefix="/api")
    # add_middleware wraps: the last one added runs first.
    app.add_middleware(LocalGuard, settings=settings)
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.ui_origins), allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", "Last-Event-ID"],
                       expose_headers=["Content-Disposition", "X-Export-Tables", "X-Export-Note"], max_age=600)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    return app


def uvicorn_options(settings: Settings) -> dict[str, Any]:
    """Always the loopback host (Settings refuses any other)."""
    return {"host": settings.host, "port": settings.port, "log_level": "info", "proxy_headers": False,
            "server_header": False}


def main() -> None:  # pragma: no cover - starts a server
    import uvicorn

    settings = Settings.from_env()
    db.exit_if_outdated(settings.database_url)  # D10: an old database stops here with the instruction
    uvicorn.run(create_app(settings), **uvicorn_options(settings))


if __name__ == "__main__":  # pragma: no cover
    main()
