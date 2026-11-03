"""Slice 3b T2: broker tasks (spec 1.2 / 1.3 with D2, D3, D8, O1, O4, A1, A2, P1, R5).

GET    /api/tasks            every task of the workspace, with run_label, run_missing, discrepancy
POST   /api/tasks            {title, due_date?, run_id?, discrepancy_item_id?} -> 201 task
PATCH  /api/tasks/{id}       any of title, due_date, status, run_id, discrepancy_item_id (null clears)
DELETE /api/tasks/{id}       204

`run_id` is a plain integer (no FK, D2): a run removed later shows run_missing. A discrepancy link
is accepted only for an item in the run's current report (D3); the block title is saved on the task
(D8) and shown when the item has left the report (in_report=false). Tasks and discrepancy states
never change each other (R5). Validation errors are 400 invalid_task with the field (O10).
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, select, text

from api_support import DOC_A, make_api
from backend import db, models
from pipeline_fakes import policy_responder
from test_discrepancy_state import COV_A, PREMIUM, _art, _policy_run, _set_fact

TASK_KEYS = {"id", "title", "due_date", "status", "run_id", "discrepancy_item_id", "created_at", "updated_at",
             "completed_at", "run_label", "run_missing", "discrepancy"}
# Discrepancy item keys before slice 3b (API.md); D7 pins them: only `task` is new.
OLD_ITEM_KEYS = {"type", "id", "key", "title", "severity", "item_state", "lob", "prose", "slots", "sources",
                 "status", "note", "changed_since_resolved", "updated_at"}


@pytest.fixture
def pc(tmp_path, monkeypatch):
    a = make_api(tmp_path, monkeypatch, policy_responder())
    yield a
    a.client.__exit__(None, None, None)


def _create(api, status=201, **body):
    r = api.client.post("/api/tasks", json=body)
    assert r.status_code == status, r.text
    return r.json()


def _patch(api, task_id, body, status=200):
    r = api.client.patch(f"/api/tasks/{task_id}", json=body)
    assert r.status_code == status, r.text
    return r.json()


def _list(api) -> list[dict]:
    r = api.client.get("/api/tasks")
    assert r.status_code == 200, r.text
    return r.json()["tasks"]


def _one(api, task_id) -> dict:
    return next(t for t in _list(api) if t["id"] == task_id)


def _invalid(r, field):
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "invalid_task" and err["field"] == field and err["message"], err
    return err


def _discrepancies(api, run_id) -> dict:
    r = api.client.get(f"/api/runs/{run_id}/discrepancies")
    assert r.status_code == 200, r.text
    return {i["id"]: i for i in r.json()["items"]}


def _compare_run(api) -> int:
    r = api.client.post("/api/runs", json={"document_ids": [api.upload_pages(DOC_A, "cq.pdf")]})
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    return run_id


def _delete_run_row(api, run_id):
    """No API deletes runs (out of scope), so remove the row directly; FK cascades take its children."""
    with db.session_scope(api.ctx.factory) as s:
        s.execute(text("DELETE FROM run WHERE id = :id"), {"id": run_id})


# --------------------------------------------------------------------- CRUD


def test_create_a_free_standing_task_and_list_it(pc):
    t = _create(pc, title="Send the renewal summary", due_date="2026-10-30")
    assert set(t) == TASK_KEYS
    assert t["title"] == "Send the renewal summary" and t["due_date"] == "2026-10-30" and t["status"] == "open"
    assert t["run_id"] is None and t["discrepancy_item_id"] is None and t["completed_at"] is None
    assert t["run_label"] is None and t["run_missing"] is False and t["discrepancy"] is None
    assert t["created_at"] and t["updated_at"]
    assert _list(pc) == [t]
    with pc.ctx.factory() as s:
        assert s.get(models.Task, t["id"]).workspace_id == db.DEFAULT_WORKSPACE_ID


def test_list_is_newest_first_and_empty_at_first(pc):
    assert _list(pc) == []
    a, b = _create(pc, title="first"), _create(pc, title="second")
    assert [t["id"] for t in _list(pc)] == [b["id"], a["id"]]


def test_title_is_cleaned_and_trimmed(pc):
    assert _create(pc, title="  Call\x00 the\x07 carrier \x1f ")["title"] == "Call the carrier"
    assert _create(pc, title="x" * 200)["title"] == "x" * 200
    assert _create(pc, title="  " + "y" * 200 + "  ")["title"] == "y" * 200  # trimmed before the length check


def test_patch_fields_and_null_clears_due_date(pc):
    t = _create(pc, title="Draft", due_date="2026-11-05")
    out = _patch(pc, t["id"], {"title": "Final", "due_date": "2026-11-09"})
    assert out["title"] == "Final" and out["due_date"] == "2026-11-09"
    assert _patch(pc, t["id"], {"due_date": None})["due_date"] is None
    assert _one(pc, t["id"])["title"] == "Final"


def test_done_sets_completed_at_and_reopen_clears_it(pc):
    t = _create(pc, title="Chase signature")
    done = _patch(pc, t["id"], {"status": "done"})
    assert done["status"] == "done" and done["completed_at"]
    again = _patch(pc, t["id"], {"status": "done"})  # done -> done keeps the first completion time
    assert again["completed_at"] == done["completed_at"]
    assert _patch(pc, t["id"], {"title": "Chase the signature"})["completed_at"] == done["completed_at"]
    reopened = _patch(pc, t["id"], {"status": "open"})
    assert reopened["status"] == "open" and reopened["completed_at"] is None


def test_delete_and_unknown_ids(pc):
    t = _create(pc, title="Gone soon")
    r = pc.client.delete(f"/api/tasks/{t['id']}")
    assert r.status_code == 204 and r.content == b""
    assert _list(pc) == []
    for r in (pc.client.delete(f"/api/tasks/{t['id']}"), pc.client.patch(f"/api/tasks/{t['id']}", json={"title": "x"}),
              pc.client.delete("/api/tasks/999")):
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------- invalid_task (route-level, O10)


@pytest.mark.parametrize(("body", "field"), [
    ({}, "title"),
    ({"title": ""}, "title"),
    ({"title": "   "}, "title"),
    ({"title": "\x00\x07"}, "title"),
    ({"title": "x" * 201}, "title"),
    ({"title": 12}, "title"),
    ({"title": None}, "title"),
    ({"title": "ok", "due_date": "2026-13-01"}, "due_date"),
    ({"title": "ok", "due_date": "tomorrow"}, "due_date"),
    ({"title": "ok", "due_date": "20261102"}, "due_date"),
    ({"title": "ok", "due_date": 20261102}, "due_date"),
    ({"title": "ok", "due_date": "2026-11-02T10:00:00"}, "due_date"),
    ({"title": "ok", "run_id": 999}, "run_id"),
    ({"title": "ok", "run_id": "1"}, "run_id"),
    ({"title": "ok", "run_id": True}, "run_id"),
    ({"title": "ok", "run_id": 1.5}, "run_id"),
    ({"title": "ok", "discrepancy_item_id": COV_A}, "discrepancy_item_id"),
    ({"title": "ok", "discrepancy_item_id": 5}, "discrepancy_item_id"),
    ({"title": "ok", "status": "done"}, "status"),
    ({"title": "ok", "note": "free text"}, "note"),
])
def test_create_rejects_bad_fields(pc, body, field):
    _invalid(pc.client.post("/api/tasks", json=body), field)
    assert _list(pc) == []


def test_create_rejects_an_item_that_is_not_in_the_runs_report(pc):
    run_id = _policy_run(pc, with_ams=False)
    err = _invalid(pc.client.post("/api/tasks", json={"title": "x", "run_id": run_id,
                                                     "discrepancy_item_id": "disc-nope"}), "discrepancy_item_id")
    assert "report" in err["message"]


def test_create_rejects_an_item_on_a_run_with_no_report(pc):
    with db.session_scope(pc.ctx.factory) as s:
        wf = s.scalar(select(models.Workflow).where(models.Workflow.slug == db.POLICY_CHECK_SLUG))
        run = models.Run(workspace_id=1, workflow_id=wf.id, parameters={}, status="failed")
        s.add(run)
        s.flush()
        run_id = run.id
    _invalid(pc.client.post("/api/tasks", json={"title": "x", "run_id": run_id, "discrepancy_item_id": COV_A}),
             "discrepancy_item_id")
    # a plain run link to that run is fine
    assert _create(pc, title="x", run_id=run_id)["run_id"] == run_id


@pytest.mark.parametrize(("body", "field"), [
    ({}, None),
    ({"title": ""}, "title"),
    ({"title": "x" * 201}, "title"),
    ({"status": "closed"}, "status"),
    ({"status": None}, "status"),
    ({"status": 1}, "status"),
    ({"title": None}, "title"),
    ({"due_date": "11/02/2026"}, "due_date"),
    ({"run_id": 999}, "run_id"),
    ({"discrepancy_item_id": COV_A}, "discrepancy_item_id"),
    ({"completed_at": "2026-11-02T00:00:00"}, "completed_at"),
])
def test_patch_rejects_bad_fields(pc, body, field):
    t = _create(pc, title="Keep me", due_date="2026-11-01")
    r = pc.client.patch(f"/api/tasks/{t['id']}", json=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_task", r.text
    if field:
        assert r.json()["error"]["field"] == field
    after = _one(pc, t["id"])
    assert after["title"] == "Keep me" and after["status"] == "open" and after["due_date"] == "2026-11-01"


def test_a_non_object_body_is_rejected(pc):
    r = pc.client.post("/api/tasks", json=["title"])
    assert r.status_code in (400, 422)


# --------------------------------------------------------------------- run and discrepancy links


def test_link_to_a_run_shows_its_label(pc):
    run_id = _compare_run(pc)
    t = _create(pc, title="Ask about the deductible", run_id=run_id)
    assert t["run_id"] == run_id and t["run_label"] == f"Compare Quotes · Run {run_id}"
    assert t["run_missing"] is False and t["discrepancy"] is None


def test_link_to_a_discrepancy_snapshots_its_title_and_shows_the_live_state(pc):
    run_id = _policy_run(pc)
    item = _discrepancies(pc, run_id)[COV_A]
    t = _create(pc, title="Follow up: limit", run_id=run_id, discrepancy_item_id=COV_A)
    assert t["discrepancy_item_id"] == COV_A and t["run_label"] == f"Policy Check · Run {run_id}"
    assert t["discrepancy"] == {"item_id": COV_A, "title": item["title"], "status": "open", "in_report": True}
    with pc.ctx.factory() as s:
        assert s.get(models.Task, t["id"]).discrepancy_title == item["title"]
    # R5: resolving the discrepancy shows on the task but does not touch the task's own status
    pc.client.patch(f"/api/runs/{run_id}/discrepancies/{COV_A}", json={"status": "resolved"})
    now = _one(pc, t["id"])
    assert now["discrepancy"]["status"] == "resolved" and now["status"] == "open" and now["completed_at"] is None
    # R5 the other way: completing the task leaves the discrepancy decision alone
    _patch(pc, t["id"], {"status": "done"})
    pc.client.patch(f"/api/runs/{run_id}/discrepancies/{COV_A}", json={"status": "open"})
    assert _discrepancies(pc, run_id)[COV_A]["status"] == "open"
    assert _one(pc, t["id"])["status"] == "done"
    _patch(pc, t["id"], {"status": "open"})
    assert _discrepancies(pc, run_id)[COV_A]["status"] == "open"
    with pc.ctx.factory() as s:
        row = s.scalar(select(models.DiscrepancyState).where(models.DiscrepancyState.run_id == run_id,
                                                             models.DiscrepancyState.item_id == COV_A))
        assert row.status == "open" and row.note is None


def test_changing_or_clearing_the_run_clears_the_item(pc):
    """O1: the merged post-patch state is validated; a run change without an item drops the item."""
    run_id = _policy_run(pc)
    other = _compare_run(pc)
    t = _create(pc, title="Follow up", run_id=run_id, discrepancy_item_id=COV_A)
    same = _patch(pc, t["id"], {"run_id": run_id})  # the same run is not a change
    assert same["discrepancy_item_id"] == COV_A and same["discrepancy"]["in_report"] is True
    moved = _patch(pc, t["id"], {"run_id": other})
    assert moved["run_id"] == other and moved["discrepancy_item_id"] is None and moved["discrepancy"] is None
    with pc.ctx.factory() as s:
        assert s.get(models.Task, t["id"]).discrepancy_title is None
    # re-link run + item in one patch, then clear the run
    relinked = _patch(pc, t["id"], {"run_id": run_id, "discrepancy_item_id": PREMIUM})
    assert relinked["discrepancy"]["item_id"] == PREMIUM
    cleared = _patch(pc, t["id"], {"run_id": None})
    assert cleared["run_id"] is None and cleared["discrepancy_item_id"] is None and cleared["run_label"] is None
    # an item without a run is refused, in the merged state too
    _invalid(pc.client.patch(f"/api/tasks/{t['id']}", json={"discrepancy_item_id": COV_A}), "discrepancy_item_id")
    _invalid(pc.client.patch(f"/api/tasks/{t['id']}", json={"run_id": None, "discrepancy_item_id": COV_A}),
             "discrepancy_item_id")
    # clearing only the item keeps the run
    _patch(pc, t["id"], {"run_id": run_id, "discrepancy_item_id": COV_A})
    only_run = _patch(pc, t["id"], {"discrepancy_item_id": None})
    assert only_run["run_id"] == run_id and only_run["discrepancy"] is None
    # an item of another run is refused
    _invalid(pc.client.patch(f"/api/tasks/{t['id']}", json={"run_id": other, "discrepancy_item_id": COV_A}),
             "discrepancy_item_id")
    assert _one(pc, t["id"])["run_id"] == run_id


def test_a_removed_run_shows_run_missing(pc):
    """D2: the link is a plain id; the run row going away leaves run_missing, not a lost link."""
    run_id = _policy_run(pc, with_ams=False)
    linked = _create(pc, title="Follow up", run_id=run_id, discrepancy_item_id=COV_A)
    plain = _create(pc, title="Call", run_id=run_id)
    _delete_run_row(pc, run_id)
    for tid in (linked["id"], plain["id"]):
        t = _one(pc, tid)
        assert t["run_id"] == run_id and t["run_missing"] is True
        assert t["run_label"] is None and t["discrepancy"] is None
    assert _one(pc, linked["id"])["discrepancy_item_id"] == COV_A  # the stored link is kept
    # other edits still work; re-sending the missing run is refused (must exist on write)
    assert _patch(pc, plain["id"], {"title": "Call back"})["run_missing"] is True
    _invalid(pc.client.patch(f"/api/tasks/{plain['id']}", json={"run_id": run_id}), "run_id")
    assert _patch(pc, plain["id"], {"run_id": None})["run_missing"] is False


def test_item_that_leaves_the_report_shows_in_report_false_and_comes_back_on_restore(pc):
    """D3 + D8: a regenerate that drops the item keeps the link, with the saved title and live state."""
    run_id = _policy_run(pc)
    title = _discrepancies(pc, run_id)[COV_A]["title"]
    t = _create(pc, title="Follow up", run_id=run_id, discrepancy_item_id=COV_A)
    pc.client.patch(f"/api/runs/{run_id}/discrepancies/{COV_A}", json={"status": "resolved"})
    _set_fact(pc, run_id, "coverage_a_limit", "$350,000")  # renewal now equals expiring: the item goes
    art = _art(pc, run_id)
    assert pc.client.post(f"/api/artifacts/{art}/regenerate", json={}).status_code == 200
    assert COV_A not in _discrepancies(pc, run_id)
    gone = _one(pc, t["id"])["discrepancy"]
    assert gone == {"item_id": COV_A, "title": title, "status": "resolved", "in_report": False}
    # a new link to the dropped item is refused (current report only), the old one stays
    _invalid(pc.client.post("/api/tasks", json={"title": "x", "run_id": run_id, "discrepancy_item_id": COV_A}),
             "discrepancy_item_id")
    assert _patch(pc, t["id"], {"title": "Follow up again"})["discrepancy"]["in_report"] is False
    r = pc.client.post(f"/api/artifacts/{art}/undo", json={"version": 1})
    assert r.status_code == 200, r.text
    back = _one(pc, t["id"])["discrepancy"]
    assert back == {"item_id": COV_A, "title": title, "status": "resolved", "in_report": True}


def test_saved_title_is_refreshed_while_the_item_is_in_the_report(pc):
    run_id = _policy_run(pc, with_ams=False)
    live = _discrepancies(pc, run_id)[COV_A]["title"]
    t = _create(pc, title="Follow up", run_id=run_id, discrepancy_item_id=COV_A)
    with db.session_scope(pc.ctx.factory) as s:
        s.get(models.Task, t["id"]).discrepancy_title = "An older title"
    assert _one(pc, t["id"])["discrepancy"]["title"] == live
    with pc.ctx.factory() as s:
        assert s.get(models.Task, t["id"]).discrepancy_title == live


def test_list_survives_a_linked_run_without_a_report(pc):
    """A1: a linked run with no artifact gives in_report=false, never a failed list."""
    run_id = _policy_run(pc, with_ams=False)
    t = _create(pc, title="Follow up", run_id=run_id, discrepancy_item_id=COV_A)
    plain = _create(pc, title="Free")
    with db.session_scope(pc.ctx.factory) as s:
        s.execute(text("DELETE FROM artifact WHERE run_id = :id"), {"id": run_id})
    tasks = {x["id"]: x for x in _list(pc)}
    assert tasks[t["id"]]["discrepancy"]["in_report"] is False
    assert tasks[t["id"]]["discrepancy"]["title"]  # the saved title
    assert tasks[t["id"]]["run_missing"] is False and tasks[plain["id"]]["discrepancy"] is None


def test_list_loads_each_linked_run_once(pc):
    """P1: the statement count depends on the distinct runs, not on the number of tasks."""
    run_id = _policy_run(pc)
    _create(pc, title="a", run_id=run_id, discrepancy_item_id=COV_A)
    _create(pc, title="b", run_id=run_id, discrepancy_item_id=PREMIUM)
    _list(pc)  # settle the title snapshots

    def count() -> int:
        n = 0

        def on(*_a):
            nonlocal n
            n += 1
        event.listen(pc.ctx.engine, "before_cursor_execute", on)
        try:
            _list(pc)
        finally:
            event.remove(pc.ctx.engine, "before_cursor_execute", on)
        return n

    few = count()
    for i in range(6):
        _create(pc, title=f"more {i}", run_id=run_id, discrepancy_item_id=COV_A if i % 2 else PREMIUM)
    assert count() == few


# --------------------------------------------------------------------- discrepancy items gain `task` (O4)


def test_discrepancy_items_carry_their_task(pc):
    run_id = _policy_run(pc)
    items = _discrepancies(pc, run_id)
    assert all(set(i) == OLD_ITEM_KEYS | {"task"} for i in items.values())  # D7
    assert all(i["task"] is None for i in items.values())
    older = _create(pc, title="older open", run_id=run_id, discrepancy_item_id=COV_A)
    newer = _create(pc, title="newer", run_id=run_id, discrepancy_item_id=COV_A)
    assert _discrepancies(pc, run_id)[COV_A]["task"] == {"id": newer["id"], "status": "open"}
    _patch(pc, newer["id"], {"status": "done"})
    assert _discrepancies(pc, run_id)[COV_A]["task"] == {"id": older["id"], "status": "open"}  # first open
    _patch(pc, older["id"], {"status": "done"})
    assert _discrepancies(pc, run_id)[COV_A]["task"] == {"id": newer["id"], "status": "done"}  # else latest
    assert _discrepancies(pc, run_id)[PREMIUM]["task"] is None
    # a plain run link is not an item's task; PATCH discrepancy returns the same shape
    _create(pc, title="run only", run_id=run_id)
    patched = pc.client.patch(f"/api/runs/{run_id}/discrepancies/{PREMIUM}", json={"note": "n"}).json()
    assert set(patched) == OLD_ITEM_KEYS | {"task"} and patched["task"] is None
    pc.client.delete(f"/api/tasks/{newer['id']}")
    pc.client.delete(f"/api/tasks/{older['id']}")
    assert _discrepancies(pc, run_id)[COV_A]["task"] is None


def test_task_status_reflects_on_the_item_without_changing_its_state(pc):
    run_id = _policy_run(pc, with_ams=False)
    t = _create(pc, title="Follow up", run_id=run_id, discrepancy_item_id=COV_A)
    _patch(pc, t["id"], {"status": "done"})
    item = _discrepancies(pc, run_id)[COV_A]
    assert item["task"] == {"id": t["id"], "status": "done"} and item["status"] == "open"
