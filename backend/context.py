"""Process-wide settings and the per-app context the routes share (T7, T8).

Settings are local-only by construction (R3): the bind host is fixed to
127.0.0.1 and any other value is refused; CORS allows only the UI dev origin;
the Host header must name the loopback machine (DNS-rebinding guard).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Hashable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend import db, models
from backend.pipeline.parse import DEFAULT_LIMITS, ParseLimits
from backend.pipeline.runner import RunManager
from backend.providers import CallRecord, Provider, RunBudget

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_UI_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")  # Vite dev server, both spellings
APP_NAME = "BrokerDocs"  # DG7: one config constant


@dataclass(frozen=True)
class Settings:
    host: str = LOOPBACK_HOST
    port: int = 8000
    ui_origins: tuple[str, ...] = DEFAULT_UI_ORIGINS
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    database_url: str | None = None  # None: data/brokerdocs.db (db.database_url)
    limits: ParseLimits = DEFAULT_LIMITS
    budget_limit: int | None = None  # None: provider config (600k, D3)
    sse_keepalive_s: float = 15.0
    chat_page_chars: int = 120_000  # page text sent to chat, all documents together
    chat_history_messages: int = 6
    max_cell_chars: int = 2_000
    max_chat_chars: int = 4_000

    def __post_init__(self) -> None:
        if self.host != LOOPBACK_HOST:
            raise ValueError(f"the server binds {LOOPBACK_HOST} only (R3); got {self.host!r}")
        for origin in self.ui_origins:
            if origin.strip() in ("", "*") or not origin.startswith(("http://", "https://")):
                raise ValueError(f"CORS origin must be one explicit http(s) origin, got {origin!r}")
        if "*" in self.allowed_hosts:
            raise ValueError("allowed_hosts must be explicit names")

    @property
    def max_upload_request_bytes(self) -> int:
        """Content-Length cap for one upload request: every file at its cap plus multipart overhead."""
        return self.limits.max_bytes * self.limits.max_documents_per_run + 1024 * 1024

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """BROKERDOCS_PORT, BROKERDOCS_UI_ORIGIN (comma list), BROKERDOCS_DATABASE_URL,
        BROKERDOCS_RUN_TOKEN_BUDGET. The host is not configurable."""
        env = os.environ if env is None else env
        s = cls()
        changes: dict[str, Any] = {}
        if env.get("BROKERDOCS_PORT"):
            changes["port"] = int(env["BROKERDOCS_PORT"])
        if env.get("BROKERDOCS_UI_ORIGIN"):
            changes["ui_origins"] = tuple(o.strip().rstrip("/") for o in env["BROKERDOCS_UI_ORIGIN"].split(",")
                                          if o.strip())
        if env.get("BROKERDOCS_DATABASE_URL"):
            changes["database_url"] = env["BROKERDOCS_DATABASE_URL"]
        if env.get("BROKERDOCS_RUN_TOKEN_BUDGET"):
            changes["budget_limit"] = int(env["BROKERDOCS_RUN_TOKEN_BUDGET"])
        return replace(s, **changes) if changes else s


@dataclass
class AppContext:
    settings: Settings
    engine: Engine
    factory: sessionmaker[Session]
    manager: RunManager
    provider: Provider
    model_configured: bool
    suggestions: dict[int, list[str]] = field(default_factory=dict)  # artifact id -> prompts
    _locks: dict[Hashable, asyncio.Lock] = field(default_factory=dict)

    def lock(self, key: Hashable) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def run_budget(self, run_id: int) -> RunBudget:
        """What is left of the run's budget (D3), for calls made after the run (M4: chat,
        suggestions, regenerated prose all count). Every call is logged on the run."""
        with self.factory() as s:
            run = s.get(models.Run, run_id)
            spent = (run.tokens_in or 0) + (run.tokens_out or 0) if run else 0
        return RunBudget(max(1, self.manager.budget_limit - spent), on_record=self._recorder(run_id))

    def _recorder(self, run_id: int):
        def hook(rec: CallRecord) -> None:
            with db.session_scope(self.factory) as s:
                run = s.get(models.Run, run_id)
                if run is not None:
                    models.record_model_call(
                        s, run, purpose=rec.role.value, model=rec.model, tokens_in=rec.prompt_tokens,
                        tokens_out=rec.completion_tokens, cost_usd=rec.cost_usd, ok=rec.outcome == "ok")
        return hook
