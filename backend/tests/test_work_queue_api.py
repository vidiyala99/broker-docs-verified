"""Work queue over the API and the runner (spec 2026-11-02-work-queue-design, ledger D1 to D10, M1 to M7).

GET   /api/queue?line=          groups, truncated, 422 on a bad line; selection and cap (D4, M2)
POST  /api/runs                 client_name (cleaned; a reused active run takes it), source_run_id (M3, M5)
PATCH /api/runs/{id}            client_name / queue_state / line subsets; 409 run_active (M1)
runner                          bookkeeping on every terminal transition (M4), Retry reopens (D8)
discrepancies                   last resolve stamps queue_changed_at (D2); counts over current blocks (M6)
"""

from __future__ import annotations

import time
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from api_support import DOC_A, DOC_B, api, api_factory, api_responder, make_api  # noqa: F401
from backend import db, models, work_queue
from backend.pipeline import runner
from backend.providers import CallRole
from backend.providers.fake import FakeRefusal
from pipeline_fakes import GatedProvider, policy_responder
from test_policy_check_run import EXPIRING, QUOTE, RENEWAL

GROUPS = {"in_progress", "needs_review", "ready", "needs_attention", "done_recently"}
PROPOSAL_PARAMS = {"template_id": "standard", "client_name": "Cedar Hollow LLC", "no_ai_prose": True}


def _error(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code, r.text
    return r.json()["error"]


def _queue(api, line="commercial") -> dict:
    r = api.client.get("/api/queue", params={"line": line})
    assert r.status_code == 200, r.text
    return r.json()


def _where(queue: dict, run_id: int) -> tuple[str, dict] | None:
    for g in GROUPS:
        for it in queue[g]:
            if it["run_id"] == run_id:
                return g, it
    return None


def _compare(api, pages=DOC_A, name="a.pdf", **extra) -> dict:
    r = api.client.post("/api/runs", json={"document_ids": [api.upload_pages(pages, name)], **extra})
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    return api.client.get(f"/api/runs/{run_id}").json()


def _wait_waiting(gated, n=1):
    end = time.monotonic() + 5
    while gated.waiting < n and time.monotonic() < end:
        time.sleep(0.01)
    assert gated.waiting >= n


def _set(api, run_id, **values):
    with db.session_scope(api.ctx.factory) as s:
        run = s.get(models.Run, run_id)
        for k, v in values.items():
            setattr(run, k, v)


# --------------------------------------------------------------------- GET /api/queue


def test_queue_shape_and_a_done_comparison_is_ready_with_its_extracted_client(api):
    run = _compare(api)
    q = _queue(api)
    assert set(q) == GROUPS | {"line", "truncated"} and q["line"] == "commercial" and q["truncated"] is False
    group, it = _where(q, run["id"])
    assert group == "ready"
    assert it["client"] == "Cedar Hollow LLC" and it["client_source"] == "extracted"  # D1
    assert it["summary"] == "1 carrier" and it["workflow_slug"] == "compare-quotes"
    assert it["workflow_name"] == "Compare Quotes" and it["queue_state"] == "open" and it["open_counts"] is None
    assert it["finished_at"] == run["finished_at"]


@pytest.mark.parametrize("params", [{}, {"line": "household"}, {"line": ""}, {"line": "Personal"}])
def test_queue_line_is_required_and_checked_like_runs_line(api, params):
    r = api.client.get("/api/queue", params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"


def test_queue_is_filtered_by_line_and_follows_a_re_tag(api):
    a = _compare(api)
    b = _compare(api, DOC_B, "b.pdf", line="personal")
    assert _where(_queue(api), a["id"]) and not _where(_queue(api), b["id"])
    assert _where(_queue(api, "personal"), b["id"])[0] == "ready"
    api.client.patch(f"/api/runs/{a['id']}", json={"line": "personal"})
    assert not _where(_queue(api), a["id"]) and _where(_queue(api, "personal"), a["id"])


def test_an_unnamed_comparison_has_no_client(api):
    run = _compare(api, ["Carrier: Harborline Specialty\nTotal Premium: $26,391.14"])
    _, it = _where(_queue(api), run["id"])
    assert it["client"] is None and it["client_source"] is None


def test_compare_client_is_the_majority_named_insured_across_quotes(api):
    one = api.upload_pages(["Carrier: A\nNamed Insured: Birch Lane Inc"], "1.pdf")
    two = api.upload_pages(["Carrier: B\nNamed Insured: Cedar Hollow LLC"], "2.pdf")
    three = api.upload_pages(["Carrier: C\nNamed Insured: Cedar Hollow LLC"], "3.pdf")
    r = api.client.post("/api/runs", json={"document_ids": [one, two, three]})
    api.events(r.json()["run"]["id"])
    _, it = _where(_queue(api), r.json()["run"]["id"])
    assert it["client"] == "Cedar Hollow LLC" and it["summary"] == "3 carriers"
    # a tie goes to the leftmost quote (M7)
    r = api.client.post("/api/runs", json={"document_ids": [two, one]})
    api.events(r.json()["run"]["id"])
    assert _where(_queue(api), r.json()["run"]["id"])[1]["client"] == "Cedar Hollow LLC"
    r = api.client.post("/api/runs", json={"document_ids": [one, two]})
    api.events(r.json()["run"]["id"])
    assert _where(_queue(api), r.json()["run"]["id"])[1]["client"] == "Birch Lane Inc"


def test_unverified_named_insured_is_not_used(api_factory):
    def tamper(req):
        reply = api_responder()(req)
        if req.role is CallRole.EXTRACT:
            for f in reply["facts"]:
                if f["field"] == "named_insured":
                    f["value"], f["quote"] = "Not On The Page LLC", "Named Insured: Not On The Page LLC"
        return reply
    a = api_factory(tamper)
    run = _compare(a)
    assert _where(_queue(a), run["id"])[1]["client"] is None


def test_proposal_runs_are_not_in_the_queue(api):
    source = _compare(api)
    doc = source["documents"][0]["document_id"]
    r = api.client.post("/api/runs", json={"document_ids": [doc], "workflow": "proposal-builder",
                                           "parameters": PROPOSAL_PARAMS})
    assert r.status_code == 201, r.text
    api.events(r.json()["run"]["id"])
    q = _queue(api)
    assert not _where(q, r.json()["run"]["id"])
    assert sum(len(q[g]) for g in GROUPS) == 1


def test_queue_loads_are_batched(api):
    """Section 4: the number of SQL statements does not grow with the number of runs."""
    def count_statements():
        seen = []
        engine = api.ctx.factory.kw["bind"]
        listener = lambda *a, **k: seen.append(1)  # noqa: E731
        event.listen(engine, "before_cursor_execute", listener)
        try:
            _queue(api)
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        return len(seen)

    for i in range(3):
        _compare(api, DOC_A, f"{i}.pdf")
    few = count_statements()
    for i in range(3, 9):
        _compare(api, DOC_A, f"{i}.pdf")
    assert count_statements() == few


# --------------------------------------------------------------------- selection and cap (D4, M2)


def _bulk(api, n, *, status="done", queue_state="dismissed", finished_days_ago=1.0, line="commercial"):
    doc = api.upload_pages(DOC_A, f"bulk-{status}-{queue_state}.pdf")
    now = models.utcnow()
    ids = []
    with db.session_scope(api.ctx.factory) as s:
        wf = s.scalar(select(models.Workflow).where(models.Workflow.slug == db.COMPARE_QUOTES_SLUG))
        for _ in range(n):
            run = models.Run(workspace_id=1, workflow_id=wf.id, parameters={}, status=status, line=line,
                             queue_state=queue_state, finished_at=now - timedelta(days=finished_days_ago),
                             queue_changed_at=now - timedelta(days=finished_days_ago) if queue_state != "open" else None)
            run.documents.append(models.RunDocument(document_id=doc, position=0, slot="quotes", status="done"))
            s.add(run)
            s.flush()
            ids.append(run.id)
    return ids


def test_old_actionable_rows_survive_the_cap(api):
    """M2: 500+ recent rows that left the queue plus one older actionable row: the old row stays."""
    old_failed = _bulk(api, 1, status="failed", queue_state="open", finished_days_ago=60)[0]
    old_ready = _bulk(api, 1, status="done", queue_state="open", finished_days_ago=60)[0]
    _bulk(api, 505, queue_state="dismissed", finished_days_ago=1)
    q = _queue(api)
    assert q["truncated"] is True
    assert _where(q, old_failed)[0] == "needs_attention"
    assert _where(q, old_ready)[0] == "ready"
    assert len(q["done_recently"]) == 498  # 500 selected: the 2 actionable rows first, then the newest 498
    assert sum(len(q[g]) for g in GROUPS) == 500


def test_old_finished_rows_that_left_are_not_selected(api):
    _bulk(api, 3, queue_state="done", finished_days_ago=20)
    recent = _bulk(api, 2, queue_state="done", finished_days_ago=2)
    q = _queue(api)
    assert q["truncated"] is False and [i["run_id"] for i in q["done_recently"]] == sorted(recent, reverse=True)


def test_exactly_the_cap_is_not_truncated(api):
    _bulk(api, 3, queue_state="done")
    with api.ctx.factory() as s:
        assert work_queue.load_queue(s, "commercial", cap=3)["truncated"] is False
        assert work_queue.load_queue(s, "commercial", cap=2)["truncated"] is True


# --------------------------------------------------------------------- POST client_name


def test_post_client_name_is_cleaned_and_beats_the_extracted_name(api):
    run = _compare(api, client_name="  Acme\x00 \t Holdings\n LLC  ")
    assert run["client_name"] == "Acme Holdings LLC"
    _, it = _where(_queue(api), run["id"])
    assert it["client"] == "Acme Holdings LLC" and it["client_source"] == "broker"


@pytest.mark.parametrize("value", ["", "   ", "\x00\x07", None])
def test_post_an_empty_client_name_means_none(api, value):
    assert _compare(api, client_name=value)["client_name"] is None


@pytest.mark.parametrize("value", [5, True, ["Acme"], {"name": "Acme"}, "x" * 121])
def test_post_a_bad_client_name_is_400_and_creates_nothing(api, value):
    doc = api.upload_pages(DOC_A)
    _error(api.client.post("/api/runs", json={"document_ids": [doc], "client_name": value}), 400, "invalid_run")
    with api.ctx.factory() as s:
        assert s.scalars(select(models.Run)).all() == []


def test_post_client_name_of_exactly_120_characters(api):
    assert _compare(api, client_name="y" * 120)["client_name"] == "y" * 120


def test_resubmit_takes_the_client_name_like_line(api_factory):
    """Mirrors D4: client_name is outside the duplicate-run identity; a reused active run takes it."""
    gated = GatedProvider(api_responder())
    a = api_factory(provider=gated)
    doc = a.upload_pages(DOC_A)
    r1 = a.client.post("/api/runs", json={"document_ids": [doc], "client_name": "First Name"})
    run_id = r1.json()["run"]["id"]
    r2 = a.client.post("/api/runs", json={"document_ids": [doc], "client_name": "Second Name"})
    assert r2.status_code == 200 and r2.json()["created"] is False and r2.json()["run"]["id"] == run_id
    assert r2.json()["run"]["client_name"] == "Second Name"
    r3 = a.client.post("/api/runs", json={"document_ids": [doc]})
    assert r3.json()["run"]["client_name"] == "Second Name"  # without a name the name stays
    a.client.post(f"/api/runs/{run_id}/cancel")
    gated.gate.set()


# --------------------------------------------------------------------- PATCH


def test_patch_client_name_queue_state_and_line_in_any_subset(api):
    run = _compare(api)
    rid = run["id"]
    out = api.client.patch(f"/api/runs/{rid}", json={"client_name": " Birch  Lane "}).json()
    assert out["client_name"] == "Birch Lane" and out["queue_state"] == "open" and out["queue_changed_at"] is None
    assert _where(_queue(api), rid)[1]["client_source"] == "broker"
    out = api.client.patch(f"/api/runs/{rid}", json={"queue_state": "done"}).json()
    assert out["queue_state"] == "done" and out["queue_changed_at"] and out["client_name"] == "Birch Lane"
    assert _where(_queue(api), rid)[0] == "done_recently"
    out = api.client.patch(f"/api/runs/{rid}", json={"line": "personal", "client_name": "", "queue_state": "open"}).json()
    assert out["line"] == "personal" and out["client_name"] is None and out["queue_state"] == "open"
    group, it = _where(_queue(api, "personal"), rid)
    assert group == "ready" and it["client_source"] == "extracted"  # cleared: back to the extracted name
    assert api.client.patch(f"/api/runs/{rid}", json={"client_name": "Acme"}).json()["client_name"] == "Acme"
    assert api.client.patch(f"/api/runs/{rid}", json={"client_name": None}).json()["client_name"] is None
    for key in ("status", "documents", "artifact", "parameters", "finished_at"):
        assert api.client.get(f"/api/runs/{rid}").json()[key] == run[key]


@pytest.mark.parametrize("state", ["done", "dismissed"])
def test_mark_done_or_dismiss_then_undo(api, state):
    run = _compare(api)
    api.client.patch(f"/api/runs/{run['id']}", json={"queue_state": state})
    group, it = _where(_queue(api), run["id"])
    assert group == "done_recently" and it["queue_state"] == state
    api.client.patch(f"/api/runs/{run['id']}", json={"queue_state": "open"})  # D9 Undo
    assert _where(_queue(api), run["id"])[0] == "ready"


def test_mark_done_on_a_run_finished_long_ago_lands_in_done_recently(api):
    """D2: the window counts from queue_changed_at, so the item does not vanish at once."""
    run = _compare(api)
    _set(api, run["id"], finished_at=models.utcnow() - timedelta(days=40))
    assert _where(_queue(api), run["id"])[0] == "ready"
    api.client.patch(f"/api/runs/{run['id']}", json={"queue_state": "done"})
    assert _where(_queue(api), run["id"])[0] == "done_recently"
    _set(api, run["id"], queue_changed_at=models.utcnow() - timedelta(days=15))
    assert _where(_queue(api), run["id"]) is None  # Activity only


def test_patch_queue_state_on_an_active_run_is_409_run_active(api_factory):
    """M1: an In progress item cannot be marked done or dismissed; other changes still work."""
    gated = GatedProvider(api_responder())
    a = api_factory(provider=gated)
    run_id = a.client.post("/api/runs", json={"document_ids": [a.upload_pages(DOC_A)]}).json()["run"]["id"]
    _wait_waiting(gated)
    for state in ("done", "dismissed"):
        err = _error(a.client.patch(f"/api/runs/{run_id}", json={"queue_state": state}), 409, "run_active")
        assert err["run_status"] in ("queued", "running")
    # the whole body is refused: a line sent with it does not change either
    _error(a.client.patch(f"/api/runs/{run_id}", json={"queue_state": "done", "line": "personal"}), 409, "run_active")
    current = a.client.get(f"/api/runs/{run_id}").json()
    assert current["queue_state"] == "open" and current["line"] == "commercial"
    ok = a.client.patch(f"/api/runs/{run_id}", json={"queue_state": "open", "client_name": "Acme"})
    assert ok.status_code == 200 and ok.json()["client_name"] == "Acme"
    assert _where(_queue(a), run_id)[0] == "in_progress"
    gated.gate.set()
    a.events(run_id)
    assert a.client.patch(f"/api/runs/{run_id}", json={"queue_state": "done"}).status_code == 200


def test_patch_unknown_run_is_404_before_the_body_is_checked(api):
    _error(api.client.patch("/api/runs/999", json={"queue_state": "bogus"}), 404, "not_found")


# --------------------------------------------------------------------- source_run_id (M3, M5)


def _proposal(api, docs, **extra):
    return api.client.post("/api/runs", json={"document_ids": docs, "workflow": "proposal-builder",
                                              "parameters": PROPOSAL_PARAMS, **extra})


def test_source_run_id_links_the_proposal_and_is_part_of_its_parameters(api):
    source = _compare(api, line="personal")
    doc = source["documents"][0]["document_id"]
    r = _proposal(api, [doc], source_run_id=source["id"])
    assert r.status_code == 201, r.text
    prop = r.json()["run"]
    assert prop["parameters"]["source_run_id"] == source["id"]
    assert prop["line"] == "personal"  # M5: inherits the comparison's line
    assert api.client.get(f"/api/runs/{source['id']}").json()["proposal_run_id"] == prop["id"]
    api.events(prop["id"])
    # an explicit line wins over the inherited one
    r = _proposal(api, [doc], source_run_id=source["id"], line="commercial")
    assert r.json()["run"]["line"] == "commercial"
    api.events(r.json()["run"]["id"])
    assert api.client.get(f"/api/runs/{source['id']}").json()["proposal_run_id"] == r.json()["run"]["id"]


def test_source_run_id_rules_are_400_invalid_run(api):
    good = _compare(api)
    doc = good["documents"][0]["document_id"]
    failed = _compare(api, ["nothing to read here"], "empty.pdf")
    _set(api, failed["id"], status="failed")
    prop = _proposal(api, [doc]).json()["run"]
    api.events(prop["id"])
    cases = [
        (_proposal(api, [doc], source_run_id=9999), "Compare Quotes"),
        (_proposal(api, [doc], source_run_id=prop["id"]), "Compare Quotes"),  # a proposal is not a comparison
        (_proposal(api, [doc], source_run_id=failed["id"]), "done"),
        (_proposal(api, [doc], source_run_id=str(good["id"])), "run id"),
        (_proposal(api, [doc], source_run_id=True), "run id"),
        (api.client.post("/api/runs", json={"document_ids": [doc], "source_run_id": good["id"]}), "proposal-builder"),
    ]
    for r, words in cases:
        assert words in _error(r, 400, "invalid_run")["message"]
    with api.ctx.factory() as s:
        assert len(s.scalars(select(models.Run)).all()) == 3  # nothing new was created


def test_source_run_id_from_a_policy_check_is_400(tmp_path, monkeypatch):
    a = make_api(tmp_path, monkeypatch, policy_responder())
    try:
        check = _policy_run(a)
        doc = a.upload_pages(DOC_A)
        _error(_proposal(a, [doc], source_run_id=check), 400, "invalid_run")
    finally:
        a.client.__exit__(None, None, None)


def test_build_proposal_from_two_comparisons_with_the_same_documents_makes_two_runs(api_factory):
    """M3: source_run_id is inside parameters, so it is part of the duplicate-run identity."""
    gated = GatedProvider(api_responder())
    a = api_factory(provider=gated)
    gated.gate.set()
    doc = a.upload_pages(DOC_A)
    first, second = (_compare_with(a, doc) for _ in range(2))
    gated.gate.clear()
    r1 = _proposal(a, [doc], source_run_id=first)
    r2 = _proposal(a, [doc], source_run_id=second)
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["run"]["id"] != r2.json()["run"]["id"]
    again = _proposal(a, [doc], source_run_id=first)  # a double click on the same comparison
    assert again.status_code == 200 and again.json()["created"] is False
    assert again.json()["run"]["id"] == r1.json()["run"]["id"]
    for r in (r1, r2):
        a.client.post(f"/api/runs/{r.json()['run']['id']}/cancel")
    gated.gate.set()


def _compare_with(a, doc) -> int:
    r = a.client.post("/api/runs", json={"document_ids": [doc]})
    run_id = r.json()["run"]["id"]
    a.events(run_id)
    assert a.client.get(f"/api/runs/{run_id}").json()["status"] == "done"
    return run_id


# --------------------------------------------------------------------- D3 + M4: proposal outcome paths


def test_proposal_done_moves_the_comparison_to_done_recently(api):
    source = _compare(api)
    r = _proposal(api, [source["documents"][0]["document_id"]], source_run_id=source["id"])
    api.events(r.json()["run"]["id"])
    assert api.client.get(f"/api/runs/{r.json()['run']['id']}").json()["status"] == "done"
    after = api.client.get(f"/api/runs/{source['id']}").json()
    assert after["queue_changed_at"] is not None and after["queue_state"] == "open"
    group, it = _where(_queue(api), source["id"])
    assert group == "done_recently" and it["proposal_run_id"] == r.json()["run"]["id"]


def test_proposal_failed_puts_the_comparison_in_needs_attention(api_factory):
    mode = {"refuse": False}
    base = api_responder()

    def respond(req):
        if mode["refuse"] and req.role is CallRole.EXTRACT:
            return FakeRefusal()
        return base(req)

    a = api_factory(respond)
    source = _compare(a)
    mode["refuse"] = True
    r = _proposal(a, [source["documents"][0]["document_id"]], source_run_id=source["id"])
    a.events(r.json()["run"]["id"])
    assert a.client.get(f"/api/runs/{r.json()['run']['id']}").json()["status"] == "failed"
    group, it = _where(_queue(a), source["id"])
    assert group == "needs_attention" and it["reasons"] == ["Proposal didn't finish"]
    assert it["proposal_retryable"] is True  # the queue's "Retry proposal" (owner feedback 2026-11-03)
    assert a.client.get(f"/api/runs/{source['id']}").json()["queue_changed_at"] is None

    # Retry proposal: the failed proposal run reruns and, done, moves the comparison to Done recently (D3).
    mode["refuse"] = False
    assert a.client.post(f"/api/runs/{r.json()['run']['id']}/retry").status_code == 202
    a.events(r.json()["run"]["id"])
    assert a.client.get(f"/api/runs/{r.json()['run']['id']}").json()["status"] == "done"
    group, it = _where(_queue(a), source["id"])
    assert group == "done_recently" and it["proposal_retryable"] is False


def test_proposal_retryable_is_null_without_a_proposal(api):
    fresh = _compare(api)
    assert _where(_queue(api), fresh["id"])[1]["proposal_retryable"] is None


def test_proposal_running_then_cancelled(api_factory):
    gated = GatedProvider(api_responder())
    a = api_factory(provider=gated)
    gated.gate.set()
    source = _compare(a)
    gated.gate.clear()
    prop_id = _proposal(a, [source["documents"][0]["document_id"]], source_run_id=source["id"]).json()["run"]["id"]
    _wait_waiting(gated)
    group, it = _where(_queue(a), source["id"])
    assert group == "in_progress" and it["summary"] == "Building proposal"
    a.client.post(f"/api/runs/{prop_id}/cancel")
    assert a.client.get(f"/api/runs/{prop_id}").json()["status"] == "cancelled"
    group, it = _where(_queue(a), source["id"])
    assert group == "needs_attention" and it["reasons"] == ["Proposal didn't finish"]
    gated.gate.set()


def test_proposal_interrupted_by_a_restart_puts_the_comparison_in_needs_attention(api):
    source = _compare(api)
    prop = _proposal(api, [source["documents"][0]["document_id"]], source_run_id=source["id"]).json()["run"]
    api.events(prop["id"])
    # as if the process died mid-run: the proposal row is left running
    _set(api, prop["id"], status="running", finished_at=None)
    _set(api, source["id"], queue_changed_at=None)
    assert runner.reconcile_interrupted_runs(api.ctx.factory) == [prop["id"]]
    group, it = _where(_queue(api), source["id"])
    assert group == "needs_attention" and it["reasons"] == ["Proposal didn't finish"]


def test_only_the_latest_proposal_moves_the_comparison(api):
    """An older proposal of the same comparison finishing later does not count (proposal_run_id rule)."""
    source = _compare(api)
    doc = source["documents"][0]["document_id"]
    old = _proposal(api, [doc], source_run_id=source["id"]).json()["run"]
    api.events(old["id"])
    _set(api, source["id"], queue_changed_at=None, proposal_run_id=old["id"] + 100)
    with db.session_scope(api.ctx.factory) as s:
        work_queue.on_run_terminal(s, s.get(models.Run, old["id"]))
    assert api.client.get(f"/api/runs/{source['id']}").json()["queue_changed_at"] is None


@pytest.fixture
def spy(monkeypatch):
    calls = []
    real = work_queue.on_run_terminal

    def record(session, run, now=None):
        calls.append((run.id, str(run.status), run.finished_at is not None, session.is_active))
        return real(session, run, now)

    monkeypatch.setattr(work_queue, "on_run_terminal", record)
    return calls


def test_bookkeeping_runs_on_done_and_partial(api, spy):
    done = _compare(api)
    assert (done["id"], "done", True, True) in spy
    bad = api.upload(("bad.pdf", b"%PDF-1.4 truncated"))["documents"][0]["id"]
    r = api.client.post("/api/runs", json={"document_ids": [api.upload_pages(DOC_A, "ok.pdf"), bad]})
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    assert api.client.get(f"/api/runs/{run_id}").json()["status"] == "partial"
    assert (run_id, "partial", True, True) in spy


def test_bookkeeping_runs_when_no_document_can_be_read(api_factory, spy):
    a = api_factory(lambda req: FakeRefusal() if req.role is CallRole.EXTRACT else api_responder()(req))
    run = _compare(a)
    assert run["status"] == "failed" and (run["id"], "failed", True, True) in spy


def test_bookkeeping_runs_on_cancel(api_factory, spy):
    gated = GatedProvider(api_responder())
    a = api_factory(provider=gated)
    run_id = a.client.post("/api/runs", json={"document_ids": [a.upload_pages(DOC_A)]}).json()["run"]["id"]
    _wait_waiting(gated)
    a.client.post(f"/api/runs/{run_id}/cancel")
    assert (run_id, "cancelled", True, True) in spy
    gated.gate.set()


def test_bookkeeping_runs_on_a_crash(api_factory, spy, monkeypatch):
    async def boom(self, run_id, plan, budget):
        raise RuntimeError("compose crashed")
    monkeypatch.setattr(runner.RunManager, "_compose", boom)
    a = api_factory()
    run = _compare(a)
    assert run["status"] == "failed" and (run["id"], "failed", True, True) in spy


def test_bookkeeping_runs_on_startup_reconcile(api, spy):
    run = _compare(api)
    _set(api, run["id"], status="composing", finished_at=None)
    spy.clear()
    runner.reconcile_interrupted_runs(api.ctx.factory)
    assert spy == [(run["id"], "failed", True, True)]


# --------------------------------------------------------------------- Retry reopens (D8)


def test_retry_reopens_a_dismissed_item(api_factory):
    mode = {"refuse": True}
    base = api_responder()
    a = api_factory(lambda req: FakeRefusal() if mode["refuse"] and req.role is CallRole.EXTRACT else base(req))
    run = _compare(a)
    assert _where(_queue(a), run["id"])[1]["reasons"] == ["1 document not read"]
    a.client.patch(f"/api/runs/{run['id']}", json={"queue_state": "dismissed"})
    assert _where(_queue(a), run["id"])[0] == "done_recently"
    mode["refuse"] = False
    r = a.client.post(f"/api/runs/{run['id']}/retry")
    assert r.status_code == 202, r.text
    assert r.json()["run"]["queue_state"] == "open" and r.json()["run"]["queue_changed_at"] is None
    a.events(run["id"])
    assert a.client.get(f"/api/runs/{run['id']}").json()["status"] == "done"
    assert _where(_queue(a), run["id"])[0] == "ready"


# --------------------------------------------------------------------- Policy Check (D2, M6, M7)


@pytest.fixture
def pc(tmp_path, monkeypatch):
    a = make_api(tmp_path, monkeypatch, policy_responder())
    yield a
    a.client.__exit__(None, None, None)


def _policy_run(api, *, expiring=EXPIRING, renewal=RENEWAL, quote=None, line="commercial", **extra) -> int:
    exp, ren = api.upload_pages(expiring, "exp.pdf"), api.upload_pages(renewal, "ren.pdf")
    slots = {str(exp): "expiring", str(ren): "renewal"}
    ids = [exp, ren]
    if quote is not None:
        q = api.upload_pages(quote, "quote.pdf")
        ids.append(q)
        slots[str(q)] = "quote"
    r = api.client.post("/api/runs", json={"document_ids": ids, "workflow": "policy-check", "slots": slots,
                                           "line": line, **extra})
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    return run_id


def test_a_check_with_open_items_needs_review_with_counts_and_its_client(pc):
    run_id = _policy_run(pc, line="personal")
    q = _queue(pc, "personal")
    group, it = _where(q, run_id)
    listing = pc.client.get(f"/api/runs/{run_id}/discrepancies").json()
    assert group == "needs_review" and it["open_counts"] == listing["open_counts"]
    assert it["summary"] == "Renewal check" and it["client"] == "Cedar Hollow LLC" and it["client_source"] == "extracted"


def test_policy_check_client_prefers_renewal_then_expiring_then_quote(pc):
    def named(pages, name):
        return [pages[0].replace("Named Insured: Cedar Hollow LLC", f"Named Insured: {name}")]
    both = _policy_run(pc, expiring=named(EXPIRING, "Expiring Name"), renewal=named(RENEWAL, "Renewal Name"),
                       quote=named(QUOTE, "Quote Name"))
    no_renewal = _policy_run(pc, expiring=named(EXPIRING, "Expiring Name"),
                             renewal=[RENEWAL[0].replace("Named Insured: Cedar Hollow LLC\n", "")],
                             quote=named(QUOTE, "Quote Name"))
    quote_only = _policy_run(pc, expiring=[EXPIRING[0].replace("Named Insured: Cedar Hollow LLC\n", "")],
                             renewal=[RENEWAL[0].replace("Named Insured: Cedar Hollow LLC\n", "")],
                             quote=named(QUOTE, "Quote Name"))
    q = _queue(pc)
    assert _where(q, both)[1]["client"] == "Renewal Name"
    assert _where(q, no_renewal)[1]["client"] == "Expiring Name"
    assert _where(q, quote_only)[1]["client"] == "Quote Name"
    broker = _policy_run(pc, client_name="Broker Typed")
    assert _where(_queue(pc), broker)[1]["client"] == "Broker Typed"


def _items(api, run_id):
    return api.client.get(f"/api/runs/{run_id}/discrepancies").json()["items"]


def test_resolving_the_last_open_item_stamps_queue_changed_at(pc):
    """D2: the check leaves Needs review when its last open item is resolved."""
    run_id = _policy_run(pc)
    items = _items(pc, run_id)
    assert len(items) > 1
    for it in items[:-1]:
        pc.client.patch(f"/api/runs/{run_id}/discrepancies/{it['id']}", json={"status": "resolved"})
        assert pc.client.get(f"/api/runs/{run_id}").json()["queue_changed_at"] is None
    assert _where(_queue(pc), run_id)[0] == "needs_review"
    _set(pc, run_id, finished_at=models.utcnow() - timedelta(days=30))  # an old check
    pc.client.patch(f"/api/runs/{run_id}/discrepancies/{items[-1]['id']}", json={"note": "a note only"})
    assert pc.client.get(f"/api/runs/{run_id}").json()["queue_changed_at"] is None
    pc.client.patch(f"/api/runs/{run_id}/discrepancies/{items[-1]['id']}", json={"status": "resolved"})
    stamped = pc.client.get(f"/api/runs/{run_id}").json()["queue_changed_at"]
    assert stamped is not None
    group, it = _where(_queue(pc), run_id)
    assert group == "done_recently" and sum(it["open_counts"].values()) == 0
    # re-resolving an already resolved item does not move the clock
    pc.client.patch(f"/api/runs/{run_id}/discrepancies/{items[-1]['id']}", json={"status": "resolved"})
    assert pc.client.get(f"/api/runs/{run_id}").json()["queue_changed_at"] == stamped
    # reopening puts it back in Needs review
    pc.client.patch(f"/api/runs/{run_id}/discrepancies/{items[0]['id']}", json={"status": "open"})
    assert _where(_queue(pc), run_id)[0] == "needs_review"


def _set_fact(api, run_id, field, value, *, slot="renewal"):
    with db.session_scope(api.ctx.factory) as s:
        run = s.get(models.Run, run_id)
        doc_id = next(rd.document_id for rd in run.documents if rd.slot == slot)
        fact = next(f for f in run.facts if f.document_id == doc_id and f.field == field)
        fact.value = value


def test_counts_cover_current_blocks_only(pc):
    """M6: an item gone from the latest version is not counted, and counts again if it returns."""
    run_id = _policy_run(pc)
    with pc.ctx.factory() as s:
        art = s.get(models.Run, run_id).artifact.id
        before = models.discrepancy_counts(s, s.get(models.Run, run_id))
    assert before.open_counts["critical"] == 2
    _set_fact(pc, run_id, "coverage_a_limit", "$350,000")  # renewal equals expiring: the item leaves
    pc.client.post(f"/api/artifacts/{art}/regenerate", json={})
    with pc.ctx.factory() as s:
        gone = models.discrepancy_counts(s, s.get(models.Run, run_id))
        rows = s.scalars(select(models.DiscrepancyState).where(models.DiscrepancyState.run_id == run_id)).all()
    assert gone.open_counts["critical"] == 1 and gone.total == before.total - 1
    assert len(rows) == before.total  # its state row is still there, just not counted
    assert _where(_queue(pc), run_id)[1]["open_counts"] == gone.open_counts
    assert pc.client.get(f"/api/runs/{run_id}/discrepancies").json()["open_counts"] == gone.open_counts
    _set_fact(pc, run_id, "coverage_a_limit", "$300,000")
    pc.client.post(f"/api/artifacts/{art}/regenerate", json={})
    with pc.ctx.factory() as s:
        assert models.discrepancy_counts(s, s.get(models.Run, run_id)) == before


def test_counts_helper_is_none_without_a_report(api):
    run = _compare(api)
    with api.ctx.factory() as s:
        r = s.get(models.Run, run["id"])
        assert models.discrepancy_counts(s, r).total == 0  # Compare report: no discrepancy blocks
        r2 = models.Run(workspace_id=1, workflow_id=r.workflow_id, parameters={}, status="failed")
        s.add(r2)
        s.flush()
        assert models.discrepancy_counts(s, r2) is None


def test_a_partial_check_appears_once_under_needs_attention(pc):
    exp, ren = pc.upload_pages(EXPIRING, "exp.pdf"), pc.upload_pages(RENEWAL, "ren.pdf")
    bad = pc.upload(("quote.pdf", b"%PDF-1.4 truncated"))["documents"][0]["id"]
    r = pc.client.post("/api/runs", json={"document_ids": [exp, ren, bad], "workflow": "policy-check",
                                          "slots": {str(exp): "expiring", str(ren): "renewal", str(bad): "quote"}})
    run_id = r.json()["run"]["id"]
    pc.events(run_id)
    assert pc.client.get(f"/api/runs/{run_id}").json()["status"] == "partial"
    q = _queue(pc)
    hits = [(g, it) for g in GROUPS for it in q[g] if it["run_id"] == run_id]
    assert len(hits) == 1 and hits[0][0] == "needs_attention"
    assert hits[0][1]["reasons"] == ["1 document not read"] and sum(hits[0][1]["open_counts"].values()) > 0


def test_a_check_whose_documents_name_no_insured_has_no_client(pc):
    run_id = _policy_run(pc, renewal=[RENEWAL[0].replace("Named Insured: Cedar Hollow LLC\n", "")],
                         expiring=[EXPIRING[0].replace("Named Insured: Cedar Hollow LLC\n", "")])
    assert _where(_queue(pc), run_id)[1]["client"] is None


# --------------------------------------------------------------------- queue navigation (2026-11-03)
# Owner: the same client showed four times with nothing to tell the rows apart, and the report page said "run 10"
# even when the queue named the client from the documents. Items carry `created_at` and `carriers`; single-run
# responses carry the resolved `client` / `client_source`, by the same rule as the queue.


def test_queue_items_carry_created_at_and_the_carriers_in_column_order(api):
    a = api.upload_pages(DOC_A, "a.pdf")  # Harborline Specialty
    b = api.upload_pages(DOC_B, "b.pdf")  # Juniper Peak
    none = api.upload_pages(["Named Insured: Cedar Hollow LLC\nTotal Premium: $1.00"], "n.pdf")
    r = api.client.post("/api/runs", json={"document_ids": [b, none, a]})
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    run = api.client.get(f"/api/runs/{run_id}").json()
    _, it = _where(_queue(api), run_id)
    assert it["created_at"] == run["created_at"]
    # one entry per document in column order; a quote without a verified carrier is null (the count stays honest)
    assert it["carriers"] == ["Juniper Peak", None, "Harborline Specialty"]


def test_queue_carriers_are_verified_values_only(api):
    run = _compare(api, DOC_A, "a.pdf")
    assert _where(_queue(api), run["id"])[1]["carriers"] == ["Harborline Specialty"]
    with db.session_scope(api.ctx.factory) as s:
        for f in s.scalars(select(models.Fact).where(models.Fact.run_id == run["id"],
                                                     models.Fact.field == "carrier_name")):
            f.state = "scan_read"
    assert _where(_queue(api), run["id"])[1]["carriers"] == [None]


def test_run_response_carries_the_queue_client_and_follows_a_rename(api):
    run = _compare(api)
    assert run["client_name"] is None
    assert run["client"] == "Cedar Hollow LLC" and run["client_source"] == "extracted"
    rid = run["id"]
    out = api.client.patch(f"/api/runs/{rid}", json={"client_name": "Birch Lane"}).json()
    assert out["client"] == "Birch Lane" and out["client_source"] == "broker"
    out = api.client.patch(f"/api/runs/{rid}", json={"client_name": ""}).json()
    assert out["client"] == "Cedar Hollow LLC" and out["client_source"] == "extracted"
    _, it = _where(_queue(api), rid)
    assert (it["client"], it["client_source"]) == (out["client"], out["client_source"])


def test_run_response_client_is_null_without_any_name(api):
    run = _compare(api, ["Carrier: Harborline Specialty\nTotal Premium: $26,391.14"])
    assert run["client"] is None and run["client_source"] is None


def test_run_response_client_for_a_policy_check_matches_the_queue(pc):
    run_id = _policy_run(pc)
    got = pc.client.get(f"/api/runs/{run_id}").json()
    _, it = _where(_queue(pc), run_id)
    assert got["client"] == it["client"] == "Cedar Hollow LLC"
    assert got["client_source"] == it["client_source"] == "extracted"
