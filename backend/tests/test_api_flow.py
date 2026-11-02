"""T8: routes end to end with a FakeProvider. Workspace, documents, runs and SSE,
artifact with chips, cell edit (edited badge, D9 stale flags), undo, regenerate
and retry prose merging edits, retry run keeping edits, cancel, feedback,
suggestions, edit-aware chat, proposed edits, export."""

from __future__ import annotations

import io
import json
import time

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from backend import db, models
from backend.context import Settings
from backend.providers import CallRole, FakeProvider
from backend.providers.fake import FakeRefusal
from backend.schemas import RunDocumentStatus, RunStatus
from api_support import (  # noqa: F401
    DOC_A, DOC_B, api, api_factory, api_responder, context_payload, last_user_text, make_api, make_pdf, user_text,
)
from pipeline_fakes import GatedProvider, compose_reply

TABLE = "common_declarations-table"
COVERAGE = "coverage-table"


def cell(art, block_id, row_key, col="A"):
    for s in art["content"]["sections"]:
        for b in s["blocks"]:
            if b["type"] == "table" and b["id"] == block_id:
                for row in b["rows"]:
                    if row["key"] == row_key:
                        return row["cells"][col]
    raise KeyError(row_key)


def section(art, sid):
    return next(s for s in art["content"]["sections"] if s["id"] == sid)


def wait_terminal(api, run_id, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        run = api.client.get(f"/api/runs/{run_id}").json()
        if run["terminal"]:
            return run
        time.sleep(0.02)
    raise AssertionError("run did not finish")


@pytest.fixture
def done(api):
    a, b = api.upload_pages(DOC_A, "a.pdf"), api.upload_pages(DOC_B, "b.pdf")
    run, events = api.run([a, b])
    return api, run, events, api.artifact(run["id"])


# --------------------------------------------------------------------- workspace and errors


def test_workspace_and_workflows(api):
    ws = api.client.get("/api/workspace").json()
    assert ws["app_name"] == "BrokerDocs" and ws["default_workflow"] == "compare-quotes"
    assert ws["limits"] == {"max_pages": 300, "max_bytes": 50 * 1024 * 1024, "max_megabytes": 50.0,
                            "max_documents_per_run": 8, "accepts": "application/pdf", "run_token_budget": 1_500_000}
    wf = api.client.get("/api/workflows/compare-quotes").json()
    assert wf["name"] == "Compare Quotes" and len(wf["fact_schema"]) == 20 and wf["section_plan"][0]["number"] == "1"
    assert api.client.get(f"/api/workflows/{wf['id']}").json()["slug"] == "compare-quotes"
    assert [w["slug"] for w in api.client.get("/api/workflows").json()["workflows"]] == ["compare-quotes", "proposal-builder"]


def test_error_shape(api):
    r = api.client.get("/api/runs/999")
    assert r.status_code == 404 and r.json() == {"error": {"code": "not_found", "message": "No run 999."}}
    r = api.client.post("/api/runs", json={"document_ids": "nope"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request" and r.json()["error"]["fields"]
    r = api.client.get("/api/nothing-here")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = api.client.post("/api/runs", json={"document_ids": []})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_run"


# --------------------------------------------------------------------- documents


def test_document_list_get_delete(api):
    a = api.upload_pages(DOC_A, "a.pdf")
    b = api.upload_pages(DOC_B, "b.pdf")
    listed = api.client.get("/api/documents").json()["documents"]
    assert [d["id"] for d in listed] == [b, a]
    one = api.client.get(f"/api/documents/{a}").json()
    assert one["filename"] == "a.pdf" and one["page_count"] == 2 and one["warning"] is None
    run, _ = api.run([a])
    r = api.client.delete(f"/api/documents/{a}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "document_in_use"
    assert r.json()["error"]["run_id"] == run["id"]
    assert len(list((api.data_dir / "files").iterdir())) == 2
    assert api.client.delete(f"/api/documents/{b}").status_code == 204
    assert api.client.get(f"/api/documents/{b}").status_code == 404
    assert len(list((api.data_dir / "files").iterdir())) == 1


# --------------------------------------------------------------------- runs and SSE


def test_run_progress_stream_and_shapes(done):
    api, run, events, art = done
    assert run["status"] == "done" and run["terminal"] and not run["active"]
    assert run["artifact"] == {"id": art["id"], "version": 1}
    types = [e["type"] for e in events]
    assert types[-1] == "run.end" and "artifact.ready" in types and "document.status" in types
    doc_event = next(e for e in events if e["type"] == "document.status")
    assert set(doc_event["data"]) == set(run["documents"][0])  # REST and SSE share one document shape
    assert [d["letter"] for d in run["documents"]] == ["A", "B"]
    # replay after a Last-Event-ID resumes after that event
    mid = events[3]["id"]
    replay = api.events(run["id"], last_event_id=mid)
    assert [e["id"] for e in replay] == [e["id"] for e in events if e["id"] > mid]
    listed = api.client.get("/api/runs").json()["runs"]
    assert listed[0]["id"] == run["id"]


def test_double_submit_returns_the_active_run_and_cancel_ends_it(api_factory):
    gated = GatedProvider(api_responder())
    a = api_factory(provider=gated)
    doc = a.upload_pages(DOC_A)
    r1 = a.client.post("/api/runs", json={"document_ids": [doc]})
    r2 = a.client.post("/api/runs", json={"document_ids": [doc]})
    assert r1.status_code == 201 and r2.status_code == 200
    assert r2.json()["created"] is False and r2.json()["run"]["id"] == r1.json()["run"]["id"]
    run_id = r1.json()["run"]["id"]
    end = time.monotonic() + 5
    while gated.waiting < 1 and time.monotonic() < end:
        time.sleep(0.01)
    assert a.client.get(f"/api/runs/{run_id}").json()["active"] is True
    assert a.client.post(f"/api/runs/{run_id}/retry").status_code == 409
    out = a.client.post(f"/api/runs/{run_id}/cancel").json()
    assert out["cancelled"] is True and out["run"]["status"] == "cancelled"
    assert out["run"]["retryable"] is True  # POST /retry accepts a cancelled run
    assert out["run"]["documents"][0]["error_code"] == "cancelled"
    events = a.events(run_id)
    assert events[-1]["type"] == "run.end" and events[-1]["data"]["status"] == "cancelled"


def test_all_documents_failed_run_is_failed_with_no_artifact(api):
    locked = api.upload(("x.pdf", make_pdf(DOC_A, password="pw")))["documents"][0]["id"]
    run, _ = api.run([locked])
    assert run["status"] == "failed" and run["artifact"] is None and run["retryable"]
    r = api.client.get(f"/api/runs/{run['id']}/artifact")
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_report"
    r = api.client.post(f"/api/runs/{run['id']}/messages", json={"content": "hi"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_report"
    assert api.client.get(f"/api/runs/{run['id']}/suggestions").json() == {"suggestions": []}


def test_missing_key_app_starts_and_documents_fail_with_missing_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("BROKERDOCS_DATA_DIR", str(tmp_path))
    from fastapi.testclient import TestClient

    from backend.main import create_app
    app = create_app(Settings(database_url="sqlite://"), engine=db.make_engine("sqlite://"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/health").json()["model_configured"] is False
        files = [("files", ("a.pdf", make_pdf(DOC_A), "application/pdf"))]
        doc = client.post("/api/documents", files=files).json()["documents"][0]["id"]
        run_id = client.post("/api/runs", json={"document_ids": [doc]}).json()["run"]["id"]
        with client.stream("GET", f"/api/runs/{run_id}/events") as r:
            list(r.iter_lines())
        run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert run["documents"][0]["error_code"] == "missing_key"
    assert "API key" in run["documents"][0]["error"]


def test_startup_reconciles_interrupted_runs(tmp_path, monkeypatch):
    engine = db.make_engine("sqlite://")
    db.init_db(engine)
    factory = db.make_session_factory(engine)
    with db.session_scope(factory) as s:
        doc = models.Document(workspace_id=1, filename="a.pdf", stored_name="0" * 32, size_bytes=1, sha256="x")
        s.add(doc)
        s.flush()
        run = models.Run(workspace_id=1, workflow_id=1, status=RunStatus.RUNNING)
        run.documents.append(models.RunDocument(document_id=doc.id, position=0, status=RunDocumentStatus.EXTRACTING))
        s.add(run)
        s.flush()
        rid = run.id
    monkeypatch.setenv("BROKERDOCS_DATA_DIR", str(tmp_path))
    from fastapi.testclient import TestClient

    from backend.main import create_app
    app = create_app(Settings(database_url="sqlite://"), provider=FakeProvider(api_responder()), engine=engine)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        out = client.get(f"/api/runs/{rid}").json()
    assert out["status"] == "failed" and out["retryable"] and "restart" in out["error"].lower()
    assert out["documents"][0]["error_code"] == "interrupted"


# --------------------------------------------------------------------- artifact and chips


def test_artifact_has_chips_with_labels_pages_and_trust_counts(done):
    api, run, _, art = done
    prem = cell(art, "premium-table", "total_premium")
    assert prem["state"] == "verified" and prem["value"] == "$26,391.14" and len(prem["passage_ids"]) == 1
    chip = art["chips"][str(prem["passage_ids"][0])]
    assert chip["label"] == "A1" and chip["filename"] == "a.pdf" and chip["page_no"] == 1 and chip["verified"]
    assert chip["rects"] and chip["page_width"] == 612 and chip["page_height"] == 792
    occ = cell(art, COVERAGE, "each_occurrence_limit")
    assert art["chips"][str(occ["passage_ids"][0])]["label"] == "A2"
    b_prem = cell(art, "premium-table", "total_premium", "B")
    assert art["chips"][str(b_prem["passage_ids"][0])]["label"] == "B1"
    absent = cell(art, COVERAGE, "each_occurrence_limit", "B")
    assert absent["value"] == "Not specified" and absent["passage_ids"] == []
    assert art["counts"]["verified"] == 7 and art["counts"]["edited"] == 0
    assert art["counts"]["absent"] == 40 - 7
    assert art["is_current"] and art["version"] == 1 and art["stale_sections"] == []
    by_id = api.client.get(f"/api/artifacts/{art['id']}").json()
    assert by_id["content"] == art["content"]


# --------------------------------------------------------------------- edit, stale, undo


def test_cell_edit_marks_edited_keeps_original_and_flags_sections_citing_it(done):
    api, run, _, art = done
    # the fake compose cites A's page-1 passage in the executive summary
    summary_pids = {p for b in section(art, "executive_summary")["blocks"] if b["type"] == "paragraph"
                    for seg in b["segments"] for p in seg["passage_ids"]}
    prem = cell(art, "premium-table", "total_premium")
    assert set(prem["passage_ids"]) <= summary_pids
    r = api.client.patch(f"/api/artifacts/{art['id']}/cells", json={
        "block_id": "premium-table", "row_key": "total_premium", "column_key": "A",
        "value": "  $30,000.00 ", "base_version": 1})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["version"] == 2 and out["previous_version"] == 1 and out["stale_sections"] == ["executive_summary"]
    new = out["artifact"]
    edited = cell(new, "premium-table", "total_premium")
    assert edited == {**edited, "value": "$30,000.00", "edited": True, "passage_ids": [],
                      "original_value": "$26,391.14", "original_passage_ids": prem["passage_ids"]}
    assert str(prem["passage_ids"][0]) in new["chips"]  # original source stays available
    assert section(new, "executive_summary")["stale"] is True and new["stale_sections"] == ["executive_summary"]
    assert new["counts"]["edited"] == 1
    # an edit to a cell no prose cites marks nothing stale
    r = api.client.patch(f"/api/artifacts/{art['id']}/cells", json={
        "block_id": COVERAGE, "row_key": "each_occurrence_limit", "column_key": "A", "value": "$2,000,000"})
    assert r.json()["stale_sections"] == [] and r.json()["version"] == 3
    # stale base version and unknown cells are refused
    r = api.client.patch(f"/api/artifacts/{art['id']}/cells", json={
        "block_id": COVERAGE, "row_key": "retention", "column_key": "A", "value": "1", "base_version": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_conflict"
    r = api.client.patch(f"/api/artifacts/{art['id']}/cells", json={
        "block_id": COVERAGE, "row_key": "nope", "column_key": "A", "value": "1"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "cell_not_found"
    versions = api.client.get(f"/api/artifacts/{art['id']}/versions").json()
    assert [v["version"] for v in versions["versions"]] == [3, 2, 1] and versions["versions"][0]["current"]
    old = api.client.get(f"/api/artifacts/{art['id']}", params={"version": 1}).json()
    assert old["is_current"] is False and cell(old, "premium-table", "total_premium")["edited"] is False


def test_undo_restores_the_previous_version_as_a_new_one(done):
    api, run, _, art = done
    r = api.client.patch(f"/api/artifacts/{art['id']}/cells", json={
        "block_id": "premium-table", "row_key": "total_premium", "column_key": "A", "value": "$1"}).json()
    u = api.client.post(f"/api/artifacts/{art['id']}/undo", json={"version": r["previous_version"]})
    assert u.status_code == 200
    out = u.json()
    assert out["version"] == 3 and out["stale_sections"] == []
    restored = out["artifact"]
    assert cell(restored, "premium-table", "total_premium")["value"] == "$26,391.14"
    assert cell(restored, "premium-table", "total_premium")["edited"] is False
    assert api.client.post(f"/api/artifacts/{art['id']}/undo", json={"version": 99}).status_code == 404


# --------------------------------------------------------------------- regenerate and retry prose


def test_regenerate_prose_sees_edits_clears_stale_and_keeps_the_edit(api_factory):
    def compose(req):
        text = user_text(req)
        if "user_edits" in text:
            payload = json.loads(text[text.index("<<<FACTS"):].split("\n", 1)[1].rsplit("\nFACTS", 1)[0])
            edit = payload["user_edits"][0]
            fact = payload["facts"][0]
            return {"sections": [{"section_id": "executive_summary", "paragraphs": [{"segments": [
                {"text": f"{edit['document']} total premium is {edit['value']}, edited by the user.",
                 "passage_ids": []},
                {"text": f"The document states {edit['original_value']}.", "passage_ids": [fact["passage_id"]]}]}]}]}
        return compose_reply(text)

    api = api_factory(api_responder(compose=compose))
    a, b = api.upload_pages(DOC_A, "a.pdf"), api.upload_pages(DOC_B, "b.pdf")
    run, _ = api.run([a, b])
    art = api.artifact(run["id"])
    api.client.patch(f"/api/artifacts/{art['id']}/cells", json={
        "block_id": "premium-table", "row_key": "total_premium", "column_key": "A", "value": "$30,000.00"})
    r = api.client.post(f"/api/artifacts/{art['id']}/regenerate", json={"base_version": 2})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["fallback"] is False and out["reason"] is None and out["version"] == 3
    new = out["artifact"]
    assert new["stale_sections"] == [] and new["note"] == "regenerate prose"
    prose = " ".join(seg["text"] for b in section(new, "executive_summary")["blocks"] if b["type"] == "paragraph"
                     for seg in b["segments"])
    assert "$30,000.00" in prose
    kept = cell(new, "premium-table", "total_premium")
    assert kept["edited"] and kept["value"] == "$30,000.00" and kept["original_value"] == "$26,391.14"
    req = [r for r in api.provider.requests if r.role is CallRole.COMPOSE][-1]
    assert "user_edits" in user_text(req) and "overrides the extracted value" in user_text(req)
    with api.ctx.factory() as s:
        purposes = [c.purpose for c in s.scalars(select(models.ModelCall).where(models.ModelCall.run_id == run["id"]))]
    assert purposes.count("compose") == 2  # the regenerate call is logged on the run (M4)


def test_retry_prose_after_compose_fallback_removes_the_notice(api_factory):
    state = {"bad": True}

    def compose(req):
        if state["bad"]:
            return {"sections": [{"section_id": "executive_summary", "paragraphs": [{"segments": [
                {"text": "Premium is $999,999.", "passage_ids": [424242]}]}]}]}
        return compose_reply(user_text(req))

    api = api_factory(api_responder(compose=compose))
    run, _ = api.run([api.upload_pages(DOC_A)])
    art = api.artifact(run["id"])
    assert [n["kind"] for n in art["content"]["notices"]] == ["compose_fallback"]
    assert art["content"]["notices"][0]["retryable"] is True
    state["bad"] = False
    out = api.client.post(f"/api/artifacts/{art['id']}/regenerate").json()
    assert out["fallback"] is False and out["artifact"]["content"]["notices"] == []


def test_retry_run_keeps_edits_and_marks_their_sections_stale(api_factory):
    refuse = {"on": True}
    base = api_responder()

    def respond(req):
        if refuse["on"] and req.role is CallRole.EXTRACT and "Juniper" in last_user_text(req):
            return FakeRefusal("no")
        return base(req)

    api = api_factory(respond)
    a, b = api.upload_pages(DOC_A, "a.pdf"), api.upload_pages(DOC_B, "b.pdf")
    run, first_events = api.run([a, b])
    assert run["status"] == "partial" and run["retryable"] is True
    assert [e for e in first_events if e["type"] == "run.status"][-1]["data"]["retryable"] is True
    art = api.artifact(run["id"])
    assert cell(art, TABLE, "named_insured", "B")["state"] == "failed"
    api.client.patch(f"/api/artifacts/{art['id']}/cells", json={
        "block_id": "premium-table", "row_key": "total_premium", "column_key": "A", "value": "$30,000.00"})
    refuse["on"] = False
    r = api.client.post(f"/api/runs/{run['id']}/retry")
    assert r.status_code == 202 and r.json()["run"]["retryable"] is False
    events = api.events(run["id"])
    # the stream belongs to the retry: it ends on the retry's run.end, not the first execution's
    assert [e["type"] for e in events].count("run.end") == 1
    assert events[-1]["type"] == "run.end" and events[-1]["data"]["status"] == "done"
    resumed = api.events(run["id"], last_event_id=first_events[-1]["id"])
    assert resumed and resumed[-1]["data"]["status"] == "done"
    run = wait_terminal(api, run["id"])
    assert run["status"] == "done" and run["retryable"] is False
    new = api.artifact(run["id"])
    assert new["version"] == 3 and new["note"] == "retry, generated"
    kept = cell(new, "premium-table", "total_premium")
    assert kept["edited"] and kept["value"] == "$30,000.00"
    assert cell(new, TABLE, "named_insured", "B")["state"] == "verified"
    assert new["stale_sections"] == ["executive_summary"]


def test_edits_and_regenerate_are_refused_while_a_retry_rebuilds_the_report(api_factory):
    refuse = {"on": True}
    base = api_responder()

    def respond(req):
        if refuse["on"] and req.role is CallRole.EXTRACT and "Juniper" in last_user_text(req):
            return FakeRefusal("no")
        return base(req)

    gated = GatedProvider(respond, gate_role=CallRole.COMPOSE)
    gated.gate.set()
    api = api_factory(provider=gated)
    run, _ = api.run([api.upload_pages(DOC_A, "a.pdf"), api.upload_pages(DOC_B, "b.pdf")])
    art = api.artifact(run["id"])
    refuse["on"] = False
    gated.gate.clear()
    before = gated.waiting
    assert api.client.post(f"/api/runs/{run['id']}/retry").status_code == 202
    end = time.monotonic() + 5
    while gated.waiting == before and time.monotonic() < end:
        time.sleep(0.01)
    edit = {"block_id": "premium-table", "row_key": "total_premium", "column_key": "A", "value": "$1"}
    r = api.client.patch(f"/api/artifacts/{art['id']}/cells", json=edit)
    assert r.status_code == 409 and r.json()["error"]["code"] == "run_active"
    r = api.client.post(f"/api/artifacts/{art['id']}/regenerate")
    assert r.status_code == 409 and r.json()["error"]["code"] == "run_active"
    gated.gate.set()
    assert wait_terminal(api, run["id"])["status"] == "done"
    assert api.client.patch(f"/api/artifacts/{art['id']}/cells", json=edit).status_code == 200


# --------------------------------------------------------------------- feedback


def test_feedback_set_change_clear(done):
    api, run, _, art = done
    url = f"/api/artifacts/{art['id']}/feedback"
    assert api.client.put(url, json={"rating": 1}).json()["feedback"] == {"rating": 1, "comment": None}
    assert api.client.put(url, json={"rating": -1, "comment": " wrong carrier "}).json()["feedback"] == {
        "rating": -1, "comment": "wrong carrier"}
    assert api.artifact(run["id"])["feedback"] == {"rating": -1, "comment": "wrong carrier"}
    assert api.client.put(url, json={"rating": 0}).json()["feedback"] is None
    assert api.artifact(run["id"])["feedback"] is None
    assert api.client.put(url, json={"rating": 5}).status_code == 422
    assert api.client.put(url, json={"rating": 1, "version": 9}).status_code == 404


# --------------------------------------------------------------------- suggestions


def test_suggestions_come_from_one_cached_call_with_a_fallback(done, api_factory):
    api, run, _, _ = done
    first = api.client.get(f"/api/runs/{run['id']}/suggestions").json()
    second = api.client.get(f"/api/runs/{run['id']}/suggestions").json()
    assert first == second == {"suggestions": ["Which quote is cheapest?", "Compare the limits."]}
    assert sum(1 for r in api.provider.requests if r.role is CallRole.SUGGEST) == 1
    req = next(r for r in api.provider.requests if r.role is CallRole.SUGGEST)
    assert "<<<OUTLINE" in user_text(req) and "Harborline Specialty" in user_text(req)

    broken = api_factory(api_responder(suggest=lambda req: FakeRefusal("no")))
    run2, _ = broken.run([broken.upload_pages(DOC_A)])
    out = broken.client.get(f"/api/runs/{run2['id']}/suggestions").json()["suggestions"]
    assert out and out[0] == "Which quote has the lowest total premium?"


# --------------------------------------------------------------------- chat


def test_chat_answers_with_validated_chips_and_stores_the_thread(done):
    api, run, _, art = done
    r = api.client.post(f"/api/runs/{run['id']}/messages", json={"content": "Who is carrier A?"})
    assert r.status_code == 201, r.text
    out = r.json()
    bot = out["assistant_message"]
    assert out["user_message"]["content"] == "Who is carrier A?"
    assert bot["role"] == "assistant" and bot["segments"][0]["passage_ids"]
    assert all(str(p) in art["chips"] for p in bot["passage_ids"])
    thread = api.client.get(f"/api/runs/{run['id']}/messages").json()["messages"]
    assert [m["role"] for m in thread] == ["user", "assistant"]
    # history goes into the next call
    api.client.post(f"/api/runs/{run['id']}/messages", json={"content": "And B?"})
    req = [r for r in api.provider.requests if r.role is CallRole.CHAT][-1]
    assert [m.role for m in req.messages] == ["system", "user", "assistant", "user"]


def test_chat_sees_the_latest_edited_values_with_the_original(api_factory):
    seen = {}

    def chat(req):
        payload = context_payload(req)
        seen["payload"] = payload
        c = next(x for x in payload["report"]["table_cells"] if x.get("edited_by_user"))
        return {"answer": [{"text": f"Premium for A is {c['value']}, edited by the user; the quote said "
                                    f"{c['original_value']}.", "passage_ids": c["original_passage_ids"]}],
                "proposed_edits": []}

    api = api_factory(api_responder(chat=chat))
    run, _ = api.run([api.upload_pages(DOC_A)])
    art = api.artifact(run["id"])
    api.client.patch(f"/api/artifacts/{art['id']}/cells", json={
        "block_id": "premium-table", "row_key": "total_premium", "column_key": "A", "value": "$30,000.00"})
    r = api.client.post(f"/api/runs/{run['id']}/messages", json={"content": "What is A's premium?"})
    assert r.status_code == 201, r.text
    payload = seen["payload"]
    assert payload["report"]["version"] == 2
    edited = next(x for x in payload["report"]["table_cells"] if x.get("edited_by_user"))
    assert edited["value"] == "$30,000.00" and edited["original_value"] == "$26,391.14"
    assert edited["passage_ids"] == [] and edited["original_passage_ids"]
    assert any(s.get("may_be_out_of_date") for s in payload["report"]["sections"])
    assert payload["pages"][0]["text"].startswith("Carrier: Harborline")
    assert "$30,000.00" in r.json()["assistant_message"]["content"]


def test_chat_keeps_the_spaces_between_answer_segments(api_factory):
    def chat(req):
        payload = context_payload(req)
        pid = payload["passages"][0]["passage_id"]
        return {"answer": [{"text": "A is Harborline Specialty,", "passage_ids": [pid]},
                           {"text": " per the quote.", "passage_ids": []}], "proposed_edits": []}

    api = api_factory(api_responder(chat=chat))
    run, _ = api.run([api.upload_pages(DOC_A)])
    r = api.client.post(f"/api/runs/{run['id']}/messages", json={"content": "Who is A?"})
    assert r.status_code == 201, r.text
    bot = r.json()["assistant_message"]
    assert [s["text"] for s in bot["segments"]] == ["A is Harborline Specialty,", " per the quote."]
    assert bot["content"] == "A is Harborline Specialty, per the quote."


def test_chat_rejects_bad_citations_and_invented_numbers_then_retries_once(api_factory):
    replies = []

    def chat(req):
        replies.append(req)
        payload = context_payload(req)
        pid = payload["passages"][0]["passage_id"]
        if len(replies) == 1:
            return {"answer": [{"text": "The premium is $5,555.55.", "passage_ids": [987654]}], "proposed_edits": []}
        # page text of the cited passage may supply a number that is not a fact value
        return {"answer": [{"text": "Each occurrence limit is $1,000,000.", "passage_ids": [
            next(p["passage_id"] for p in payload["passages"] if p["page"] == 2)]},
            {"text": "See the quote.", "passage_ids": [pid]}], "proposed_edits": []}

    api = api_factory(api_responder(chat=chat))
    run, _ = api.run([api.upload_pages(DOC_A)])
    r = api.client.post(f"/api/runs/{run['id']}/messages", json={"content": "Limits?"})
    assert r.status_code == 201, r.text
    assert len(replies) == 2
    retry_text = last_user_text(replies[1])
    assert "failed the citation checker" in retry_text and "987654" in retry_text and "5,555.55" in retry_text


def test_chat_that_fails_twice_says_couldnt_answer_and_stores_nothing(api_factory):
    api = api_factory(api_responder(chat=lambda req: {"answer": [{"text": "It is $123,456.", "passage_ids": []}],
                                                      "proposed_edits": []}))
    run, _ = api.run([api.upload_pages(DOC_A)])
    r = api.client.post(f"/api/runs/{run['id']}/messages", json={"content": "Premium?"})
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["code"] == "answer_rejected" and err["message"].startswith("Couldn't answer") and err["retryable"]
    assert api.client.get(f"/api/runs/{run['id']}/messages").json()["messages"] == []


def test_chat_number_from_the_question_is_allowed(api_factory):
    api = api_factory(api_responder(chat=lambda req: {
        "answer": [{"text": "A premium of $20,000 is not stated in any quote.", "passage_ids": []}],
        "proposed_edits": []}))
    run, _ = api.run([api.upload_pages(DOC_A)])
    r = api.client.post(f"/api/runs/{run['id']}/messages", json={"content": "Is any premium $20,000?"})
    assert r.status_code == 201


def test_chat_proposed_edit_applies_to_the_latest_version(api_factory):
    def chat(req):
        return {"answer": [{"text": "I can set A's retention.", "passage_ids": []}], "proposed_edits": [
            {"block_id": COVERAGE, "row_key": "retention", "column_key": "A", "value": "$5,000",
             "reason": "user asked"},
            {"block_id": "made-up", "row_key": "x", "column_key": "Z", "value": "1", "reason": "bogus"}]}

    api = api_factory(api_responder(chat=chat))
    run, _ = api.run([api.upload_pages(DOC_A)])
    art = api.artifact(run["id"])
    bot = api.client.post(f"/api/runs/{run['id']}/messages",
                          json={"content": "Set A's deductible to $5,000"}).json()["assistant_message"]
    assert [e["row_key"] for e in bot["proposed_edits"]] == ["retention"]  # the bogus cell is dropped
    proposal = bot["proposed_edits"][0]
    assert proposal["status"] == "proposed" and proposal["proposed_on_version"] == 1
    # the user edits another cell first; applying the proposal must build on that version
    api.client.patch(f"/api/artifacts/{art['id']}/cells", json={
        "block_id": "premium-table", "row_key": "total_premium", "column_key": "A", "value": "$30,000.00"})
    r = api.client.post(f"/api/runs/{run['id']}/messages/{bot['id']}/edits/{proposal['id']}/apply")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["version"] == 3 and out["previous_version"] == 2
    latest = out["artifact"]
    assert cell(latest, COVERAGE, "retention")["value"] == "$5,000"
    assert cell(latest, "premium-table", "total_premium")["value"] == "$30,000.00"
    assert out["message"]["proposed_edits"][0]["status"] == "applied"
    again = api.client.post(f"/api/runs/{run['id']}/messages/{bot['id']}/edits/{proposal['id']}/apply")
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_applied"


def test_chat_budget_exhausted_is_a_clear_409(api_factory):
    api = api_factory(settings=Settings(database_url="sqlite://", budget_limit=60_000))
    run, _ = api.run([api.upload_pages(DOC_A)])
    with db.session_scope(api.ctx.factory) as s:
        s.get(models.Run, run["id"]).tokens_in = 59_999
    r = api.client.post(f"/api/runs/{run['id']}/messages", json={"content": "Premium?"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "budget_exhausted"


# --------------------------------------------------------------------- export


def test_export_xlsx_tables_only_with_evidence_notes_and_no_formulas(api_factory):
    api = api_factory()
    doc = api.upload_pages(["Carrier: =HYPERLINK(\"http://evil\")\nTotal Premium: $26,391.14"])
    run, _ = api.run([doc])
    art = api.artifact(run["id"])
    api.client.patch(f"/api/artifacts/{art['id']}/cells", json={
        "block_id": "premium-table", "row_key": "total_premium", "column_key": "A", "value": "$30,000.00"})
    r = api.client.get(f"/api/artifacts/{art['id']}/export", params={"format": "xlsx"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert r.headers["content-disposition"] == f'attachment; filename="compare-quotes-run-{run["id"]}-v2.xlsx"'
    assert r.headers["x-export-note"] == "XLSX holds the report tables only."
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames[-1] == "Sources" and "2 Common Declarations" in wb.sheetnames
    ws = wb["2 Common Declarations"]
    assert ws["B2"].value.startswith("A: =HYPERLINK")
    carrier = ws["B3"]
    assert carrier.value == '=HYPERLINK("http://evil")' and carrier.data_type == "s"
    prem = next(wb[n] for n in wb.sheetnames if n.startswith("3.2"))
    assert prem["B3"].value == "$30,000.00 (edited)"
    note = prem["B3"].comment.text
    assert "Edited by user. Original value: $26,391.14" in note and "Original source A1: q.pdf page 1" in note
    assert not any(n.startswith("1 ") for n in wb.sheetnames)  # prose-only sections are not exported
    src = wb["Sources"]
    assert [c.value for c in src[1]] == ["Chip", "Document", "File", "Page", "Evidence"]
    assert src["A2"].value == "A1" and src["D2"].value == 1
    old = api.client.get(f"/api/artifacts/{art['id']}/export", params={"format": "xlsx", "version": 1})
    assert "v1.xlsx" in old.headers["content-disposition"]


def test_export_pdf_is_a_full_report(done):
    api, run, _, art = done
    api.client.patch(f"/api/artifacts/{art['id']}/cells", json={
        "block_id": "premium-table", "row_key": "total_premium", "column_key": "A", "value": "$30,000.00"})
    r = api.client.get(f"/api/artifacts/{art['id']}/export", params={"format": "pdf"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    import fitz
    doc = fitz.open(stream=r.content, filetype="pdf")
    text = "\n".join(p.get_text() for p in doc)
    assert "Executive Summary" in text and "Harborline Specialty" in text and "may be out of date" in text
    assert "$30,000.00" in text and "edited; original: $26,391.14" in text and "Sources" in text
    assert api.client.get(f"/api/artifacts/{art['id']}/export", params={"format": "doc"}).status_code == 422
    assert api.client.get("/api/artifacts/999/export", params={"format": "pdf"}).status_code == 404
