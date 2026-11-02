"""Helpers for the API tests: an app on an in-memory database with a FakeProvider."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import db
from backend.context import Settings
from backend.main import create_app
from backend.providers import CallRole, FakeProvider, ProviderRequest
from backend.providers.types import TextPart
from conftest import make_pdf
from pipeline_fakes import smart_responder

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "quotes"
ORIGIN = "http://localhost:5173"

DOC_A = ["Carrier: Harborline Specialty\nNamed Insured: Cedar Hollow LLC\nTotal Premium: $26,391.14",
         "Each Occurrence Limit: $1,000,000"]
DOC_B = ["Carrier: Juniper Peak\nNamed Insured: Cedar Hollow LLC\nTotal Premium: $14,490.00"]


def user_text(req: ProviderRequest) -> str:
    return "\n".join(p.text for m in req.messages if m.role == "user" for p in m.parts() if isinstance(p, TextPart))


def last_user_text(req: ProviderRequest) -> str:
    users = [m for m in req.messages if m.role == "user"]
    return "\n".join(p.text for p in users[-1].parts() if isinstance(p, TextPart)) if users else ""


def context_payload(req: ProviderRequest) -> dict[str, Any]:
    """The JSON inside the chat request's CONTEXT data block."""
    text = next(t for t in (p.text for m in req.messages if m.role == "user" for p in m.parts()
                            if isinstance(p, TextPart)) if "<<<CONTEXT" in t)
    m = re.search(r"<<<CONTEXT (\w+)\n(.*)\nCONTEXT \1>>>", text, re.S)
    return json.loads(m.group(2))


def default_chat(req: ProviderRequest) -> dict[str, Any]:
    payload = context_payload(req)
    cell = next(c for c in payload["report"]["table_cells"] if c["passage_ids"])
    return {"answer": [{"text": f"{cell['column_key']} states {cell['field']} {cell['value']}.",
                        "passage_ids": cell["passage_ids"][:1]}], "proposed_edits": []}


def api_responder(*, chat: Callable | None = None, suggest: Callable | None = None,
                  ocr_pages: dict[int, str] | None = None, compose: Callable | None = None):
    smart = smart_responder(ocr_pages)

    def respond(req: ProviderRequest):
        if req.role is CallRole.CHAT:
            return (chat or default_chat)(req)
        if req.role is CallRole.SUGGEST:
            return suggest(req) if suggest else {"suggestions": ["Which quote is cheapest?", "Compare the limits."]}
        if req.role is CallRole.COMPOSE and compose is not None:
            return compose(req)
        return smart(req)

    return respond


@dataclass
class Api:
    client: TestClient
    provider: FakeProvider
    data_dir: Path
    settings: Settings
    _cm: Any = field(default=None, repr=False)

    @property
    def ctx(self):
        return self.client.app.state.ctx

    def upload(self, *docs: tuple[str, bytes]) -> dict[str, Any]:
        files = [("files", (name, data, "application/pdf")) for name, data in docs]
        r = self.client.post("/api/documents", files=files)
        assert r.status_code == 200, r.text
        return r.json()

    def upload_pages(self, pages, name="q.pdf") -> int:
        out = self.upload((name, make_pdf(pages)))
        assert out["rejected"] == [], out
        return out["documents"][0]["id"]

    def events(self, run_id: int, last_event_id: int | None = None) -> list[dict[str, Any]]:
        headers = {"Last-Event-ID": str(last_event_id)} if last_event_id is not None else {}
        out = []
        with self.client.stream("GET", f"/api/runs/{run_id}/events", headers=headers) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            for line in r.iter_lines():
                if line.startswith("data: "):
                    out.append(json.loads(line[6:]))
        return out

    def run(self, doc_ids: list[int]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        r = self.client.post("/api/runs", json={"document_ids": doc_ids})
        assert r.status_code == 201, r.text
        run_id = r.json()["run"]["id"]
        events = self.events(run_id)
        return self.client.get(f"/api/runs/{run_id}").json(), events

    def artifact(self, run_id: int) -> dict[str, Any]:
        r = self.client.get(f"/api/runs/{run_id}/artifact")
        assert r.status_code == 200, r.text
        return r.json()


def make_api(tmp_path, monkeypatch, responder=None, *, settings: Settings | None = None,
             provider: FakeProvider | None = None) -> Api:
    monkeypatch.setenv("BROKERDOCS_DATA_DIR", str(tmp_path))
    settings = settings or Settings(database_url="sqlite://")
    provider = provider or FakeProvider(responder or api_responder())
    app = create_app(settings, provider=provider, engine=db.make_engine("sqlite://"))
    client = TestClient(app, base_url="http://127.0.0.1")
    client.__enter__()
    return Api(client, provider, tmp_path, settings)


@pytest.fixture
def api(tmp_path, monkeypatch):
    a = make_api(tmp_path, monkeypatch)
    yield a
    a.client.__exit__(None, None, None)


@pytest.fixture
def api_factory(tmp_path, monkeypatch):
    made: list[Api] = []

    def build(responder=None, **kw) -> Api:
        a = make_api(tmp_path, monkeypatch, responder, **kw)
        made.append(a)
        return a

    yield build
    for a in made:
        a.client.__exit__(None, None, None)
