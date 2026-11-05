"""Slice 5a T12: loss reviews in the work queue (Needs review while a check is open, Ready when none is, Done when
marked done) and a queue action label for every workflow slug."""

from __future__ import annotations

from datetime import UTC, datetime

from api_support import make_api
from backend import slugs, work_queue
from loss_support import claims_sheet, loss_responder, xlsx_bytes

NOW = datetime(2026, 11, 4, tzinfo=UTC)


def test_every_workflow_slug_has_a_queue_action_label():
    assert set(work_queue.QUEUE_ACTIONS) == set(slugs.ALL_SLUGS)
    assert work_queue.QUEUE_ACTIONS[slugs.LOSS_RUN_SLUG] == "Review losses"
    assert all(label for label in work_queue.QUEUE_ACTIONS.values())


def _qr(**kw):
    base = dict(run_id=1, workflow_slug=slugs.LOSS_RUN_SLUG, workflow_name="Loss Review", line="commercial",
                status="done", active=False, queue_state="open", created_at=NOW, finished_at=NOW,
                queue_changed_at=None, document_count=3, has_report=True, open_checks=2)
    base.update(kw)
    return work_queue.QueueRun(**base)


def test_classify_loss_reviews():
    group, item = work_queue.classify(_qr(), None, None, NOW)
    assert group == "needs_review" and item["action"] == "Review losses" and item["open_checks"] == 2
    assert item["summary"] == "2 checks to resolve"
    group, item = work_queue.classify(_qr(open_checks=0), None, None, NOW)
    assert group == "ready" and item["summary"] == "Loss review, 3 files"
    assert work_queue.classify(_qr(active=True), None, None, NOW)[0] == "in_progress"
    assert work_queue.classify(_qr(status="partial", failed_codes=("budget_exhausted",)), None, None, NOW)[0] \
        == "needs_attention"
    assert work_queue.classify(_qr(queue_state="done", open_checks=0), None, None, NOW)[0] == "done_recently"


def test_a_loss_review_is_a_client_row_in_the_queue(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch, loss_responder())
    try:
        data = xlsx_bytes({"2024": claims_sheet([["TR-1", "A", "2024-01-01", "O", 1, 0, 1],
                                                 ["Total", None, None, None, 5, 0, 5]],
                                                title=[["Insured: Calloway Build Group LLC"]])})
        r = api.client.post("/api/documents/spreadsheets", files=[("files", ("a.xlsx", data, "x"))])
        doc = r.json()["documents"][0]["id"]
        run_id = api.client.post("/api/runs", json={"document_ids": [doc], "workflow": "loss-run"}).json()["run"]["id"]
        api.events(run_id)
        queue = api.client.get("/api/queue", params={"line": "commercial"}).json()
        row = next(i for i in queue["needs_review"] if i["run_id"] == run_id)
        assert row["client"] == "Calloway Build Group LLC" and row["client_source"] == "extracted"
        # printed paid and incurred 5, rows add up to 1: two off totals on one printed line, so one open check
        # (one card per printed line, owner 2026-11-04 12:40)
        assert row["action"] == "Review losses" and row["open_checks"] == 1
        for check in [c for c in api.client.get(f"/api/loss-runs/{run_id}").json()["checks"] if c["verdict"] == "off"]:
            api.client.patch(f"/api/loss-runs/{run_id}/checks/{check['id']}", json={"action": "accept", "note": "ok"})
        queue = api.client.get("/api/queue", params={"line": "commercial"}).json()
        assert any(i["run_id"] == run_id for i in queue["ready"])
        api.client.patch(f"/api/runs/{run_id}", json={"queue_state": "done"})
        queue = api.client.get("/api/queue", params={"line": "commercial"}).json()
        assert any(i["run_id"] == run_id for i in queue["done_recently"])
    finally:
        api.client.__exit__(None, None, None)
