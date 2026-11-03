"""Slice 3b T1 + T4: `Run.line` (commercial | personal) and the pinned run contract (D7).

POST /api/runs        optional `line`; a bad value is 400 invalid_run (route-level, O10);
                      `line` is outside the duplicate-run identity, and a reused active run
                      takes the line that was sent (D4)
PATCH /api/runs/{id}  only `line`; 404 unknown run, 400 bad value or another field
GET /api/runs?line=   personal | commercial filter; no parameter is the old behaviour exactly
SSE run.snapshot      unchanged (O9)
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from api_support import DOC_A, DOC_B, api, api_factory, api_responder  # noqa: F401
from backend import db, models
from pipeline_fakes import GatedProvider

# The Run keys before slice 3b (API.md "Run"); D7 pins them: only `line` is new.
OLD_RUN_KEYS = {"id", "workflow_id", "workflow_name", "status", "active", "terminal", "error", "retryable",
                "parameters", "tokens_in", "tokens_out", "cost_usd", "created_at", "started_at", "finished_at",
                "documents", "artifact"}
# D7 exception (work queue spec 1.1, 2026-11-02): run_out gains these four keys, so the Run-shape
# assertions below compare against OLD_RUN_KEYS | {"line"} | QUEUE_RUN_KEYS. The old keys are still
# pinned exactly; nothing else in this file changed.
QUEUE_RUN_KEYS = {"client_name", "queue_state", "proposal_run_id", "queue_changed_at"}


def _run(api, doc_ids, **extra) -> dict:
    r = api.client.post("/api/runs", json={"document_ids": doc_ids, **extra})
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    return api.client.get(f"/api/runs/{run_id}").json()


def _error(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code, r.text
    return r.json()["error"]


# --------------------------------------------------------------------- POST /api/runs line


def test_line_defaults_to_commercial_and_is_in_every_run_payload(api):
    run = _run(api, [api.upload_pages(DOC_A)])
    assert run["line"] == "commercial"
    # D7: the old keys, plus line (+ queue, see top). Queue navigation (2026-11-03, deliberate update): single-run
    # responses also carry the resolved `client` / `client_source`; the list does not (API.md "Run").
    assert set(run) == OLD_RUN_KEYS | {"line"} | QUEUE_RUN_KEYS | {"client", "client_source"}
    listed = api.client.get("/api/runs").json()["runs"][0]
    assert listed["line"] == "commercial" and set(listed) == OLD_RUN_KEYS | {"line"} | QUEUE_RUN_KEYS
    with api.ctx.factory() as s:
        assert s.get(models.Run, run["id"]).line == "commercial"


def test_post_with_personal_line(api):
    r = api.client.post("/api/runs", json={"document_ids": [api.upload_pages(DOC_A)], "line": "personal"})
    assert r.status_code == 201, r.text
    assert r.json()["run"]["line"] == "personal" and r.json()["created"] is True
    api.events(r.json()["run"]["id"])


@pytest.mark.parametrize("line", ["Personal", "household", "", 5, True, ["personal"], {"x": 1}])
def test_post_with_a_bad_line_is_400_invalid_run(api, line):
    doc = api.upload_pages(DOC_A)
    err = _error(api.client.post("/api/runs", json={"document_ids": [doc], "line": line}), 400, "invalid_run")
    assert "commercial" in err["message"] and "personal" in err["message"]
    with api.ctx.factory() as s:
        assert s.scalars(select(models.Run)).all() == []  # nothing was created


def test_null_line_is_the_same_as_no_line(api):
    r = api.client.post("/api/runs", json={"document_ids": [api.upload_pages(DOC_A)], "line": None})
    assert r.status_code == 201 and r.json()["run"]["line"] == "commercial"
    api.events(r.json()["run"]["id"])


def test_resubmit_with_another_line_reuses_the_active_run_and_takes_the_line(api_factory):
    """D4: `line` is not part of the duplicate-run identity; the reused run takes the sent line."""
    gated = GatedProvider(api_responder())
    a = api_factory(provider=gated)
    doc = a.upload_pages(DOC_A)
    r1 = a.client.post("/api/runs", json={"document_ids": [doc]})
    assert r1.status_code == 201 and r1.json()["run"]["line"] == "commercial"
    run_id = r1.json()["run"]["id"]
    r2 = a.client.post("/api/runs", json={"document_ids": [doc], "line": "personal"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["created"] is False and r2.json()["run"]["id"] == run_id
    assert r2.json()["run"]["line"] == "personal"
    # a resubmit without a line leaves the line alone
    r3 = a.client.post("/api/runs", json={"document_ids": [doc]})
    assert r3.json()["created"] is False and r3.json()["run"]["line"] == "personal"
    # a bad line on a resubmit is still a 400 and changes nothing
    _error(a.client.post("/api/runs", json={"document_ids": [doc], "line": "nope"}), 400, "invalid_run")
    assert a.client.get(f"/api/runs/{run_id}").json()["line"] == "personal"
    with a.ctx.factory() as s:
        assert len(s.scalars(select(models.Run)).all()) == 1
    a.client.post(f"/api/runs/{run_id}/cancel")
    gated.gate.set()


# --------------------------------------------------------------------- PATCH /api/runs/{id}


def test_patch_line_moves_a_run_between_lines(api):
    run = _run(api, [api.upload_pages(DOC_A)])
    r = api.client.patch(f"/api/runs/{run['id']}", json={"line": "personal"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["line"] == "personal" and out["id"] == run["id"]
    # the Run shape, like GET /api/runs/{id} (queue navigation, deliberate update: + client, client_source)
    assert set(out) == OLD_RUN_KEYS | {"line"} | QUEUE_RUN_KEYS | {"client", "client_source"}
    assert api.client.get(f"/api/runs/{run['id']}").json()["line"] == "personal"
    back = api.client.patch(f"/api/runs/{run['id']}", json={"line": "commercial"}).json()
    assert back["line"] == "commercial"
    # nothing else about the run changed
    for key in ("status", "documents", "artifact", "parameters", "created_at", "finished_at"):
        assert back[key] == run[key]


def test_patch_unknown_run_is_404(api):
    _error(api.client.patch("/api/runs/999", json={"line": "personal"}), 404, "not_found")


@pytest.mark.parametrize("body", [{"line": "household"}, {"line": None}, {"line": 1}, {},
                                  {"line": "personal", "status": "done"}, {"status": "done"},
                                  {"parameters": {}},
                                  # work queue rows (appended; the rows above are unchanged): bad
                                  # client_name / queue_state values, and a bad key next to a good line,
                                  # which must change nothing (the assertion below checks line)
                                  {"queue_state": "closed"}, {"queue_state": None}, {"queue_state": 1},
                                  {"client_name": 5}, {"client_name": ["Acme"]}, {"client_name": "x" * 121},
                                  {"line": "personal", "client_name": 5},
                                  {"line": "personal", "queue_state": "archived"},
                                  {"line": "personal", "queue_state": "done", "parameters": {}}])
def test_patch_bad_body_is_400_invalid_run(api, body):
    run = _run(api, [api.upload_pages(DOC_A)])
    _error(api.client.patch(f"/api/runs/{run['id']}", json=body), 400, "invalid_run")
    assert api.client.get(f"/api/runs/{run['id']}").json()["line"] == "commercial"


def test_patch_a_non_object_body_is_rejected(api):
    run = _run(api, [api.upload_pages(DOC_A)])
    r = api.client.patch(f"/api/runs/{run['id']}", json=["personal"])
    assert r.status_code in (400, 422)


# --------------------------------------------------------------------- GET /api/runs?line=


def test_list_filters_by_line_and_no_parameter_lists_every_run(api):
    a = _run(api, [api.upload_pages(DOC_A, "a.pdf")])
    b = _run(api, [api.upload_pages(DOC_B, "b.pdf")], line="personal")
    c = _run(api, [api.upload_pages(DOC_A, "c.pdf")])

    def ids(**params):
        r = api.client.get("/api/runs", params=params)
        assert r.status_code == 200, r.text
        return [x["id"] for x in r.json()["runs"]]

    assert ids() == [c["id"], b["id"], a["id"]]  # D7: unchanged, newest first, both lines
    assert ids(line="personal") == [b["id"]]
    assert ids(line="commercial") == [c["id"], a["id"]]
    assert ids(line="commercial", limit=1) == [c["id"]]
    api.client.patch(f"/api/runs/{a['id']}", json={"line": "personal"})
    assert ids(line="personal") == [b["id"], a["id"]]  # a re-tagged run moves lists
    assert ids(line="commercial") == [c["id"]]
    assert ids() == [c["id"], b["id"], a["id"]]


def test_list_without_parameters_keeps_its_default_limit_and_cap(api):
    doc = api.upload_pages(DOC_A)
    with db.session_scope(api.ctx.factory) as s:  # 25 finished runs, written directly (fast)
        wf = s.scalar(select(models.Workflow).where(models.Workflow.slug == db.COMPARE_QUOTES_SLUG))
        for i in range(25):
            run = models.Run(workspace_id=1, workflow_id=wf.id, parameters={}, status="done",
                             line="personal" if i % 2 else "commercial")
            run.documents.append(models.RunDocument(document_id=doc, position=0, slot="quotes", status="done"))
            s.add(run)
    runs = api.client.get("/api/runs").json()["runs"]
    assert len(runs) == 20 and [r["id"] for r in runs] == list(range(25, 5, -1))
    assert {r["line"] for r in runs} == {"personal", "commercial"}
    assert api.client.get("/api/runs", params={"limit": 101}).status_code == 422
    assert len(api.client.get("/api/runs", params={"line": "personal", "limit": 100}).json()["runs"]) == 12


def test_list_with_a_bad_line_filter_is_422(api):
    r = api.client.get("/api/runs", params={"line": "household"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


# --------------------------------------------------------------------- SSE unchanged (O9)


def test_sse_snapshot_does_not_carry_line(api):
    run = _run(api, [api.upload_pages(DOC_A)], line="personal")
    events = api.events(run["id"], last_event_id=10**6)  # after the last event: a snapshot
    snap = [e for e in events if e["type"] == "run.snapshot"]
    assert snap, events
    assert set(snap[0]["data"]["run"]) == {"id", "status", "error", "retryable", "tokens_in", "tokens_out",
                                           "cost_usd"}


def test_patch_line_on_an_active_run(api_factory):
    """Re-tagging works while the run is still running (it is metadata only)."""
    gated = GatedProvider(api_responder())
    a = api_factory(provider=gated)
    run_id = a.client.post("/api/runs", json={"document_ids": [a.upload_pages(DOC_A)]}).json()["run"]["id"]
    end = time.monotonic() + 5
    while gated.waiting < 1 and time.monotonic() < end:
        time.sleep(0.01)
    r = a.client.patch(f"/api/runs/{run_id}", json={"line": "personal"})
    assert r.status_code == 200 and r.json()["line"] == "personal" and r.json()["active"] is True
    gated.gate.set()
    a.events(run_id)
    final = a.client.get(f"/api/runs/{run_id}").json()
    assert final["status"] == "done" and final["line"] == "personal"
