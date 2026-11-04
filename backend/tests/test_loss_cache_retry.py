"""Slice 5a T6: a reused read carries claim rows and printed totals, never decisions (O6/D12); Retry after a budget
stop reads only the unread pages with a fresh document allowance (O5/D11); other workflows unchanged."""

from __future__ import annotations

import re

from sqlalchemy import func, select

from api_support import DOC_A, api_responder, make_api
from backend import models
from backend.pipeline import cache
from backend.providers import BudgetExhausted, ProviderConfig
from loss_support import claims_sheet, loss_pdf, loss_responder, rows_reply_from_truth, user_text, xlsx_bytes

R = [{"claim": f"TR-{i}", "claimant": f"C{i}", "dol": "01/05/2025", "status": "O", "paid": f"{i},000.00",
      "reserve": "0.00", "incurred": f"{i},000.00"} for i in range(1, 5)]
TRUTH = {1: [R[0]], 2: [R[1]], 3: [R[2]], 4: [R[3]]}


def _rows(api, run_id):
    with api.ctx.factory() as s:
        return list(s.scalars(select(models.ClaimRow).where(models.ClaimRow.run_id == run_id)
                              .order_by(models.ClaimRow.location)))


def _sig(rows):
    return [(r.location, r.claim_key, r.paid, r.reserve, r.incurred, r.verify_state, r.source) for r in rows]


def _upload_sheet(api, name, data):
    r = api.client.post("/api/documents/spreadsheets", files=[("files", (name, data, "application/octet-stream"))])
    return r.json()["documents"][0]["id"]


def _run(api, ids):
    r = api.client.post("/api/runs", json={"document_ids": ids, "workflow": "loss-run"})
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    return run_id


def test_reused_read_copies_rows_and_totals_never_decisions(tmp_path, monkeypatch):
    monkeypatch.setenv(cache.ENV_SWITCH, "on")
    api = make_api(tmp_path, monkeypatch, loss_responder(rows_reply_from_truth(TRUTH, totals={4: [
        {"label": "Grand Total", "metric": "incurred", "amount": "10,000.00"}]})))
    try:
        pdf = loss_pdf([[r] for r in R], totals={4: [("Grand Total", "10,000.00")]})
        sheet = xlsx_bytes({"2024": claims_sheet([["TR-9", "Z", "2024-01-01", "C", 5, 0, 5]],
                                                 title=[["Carrier: Travelers"], ["Valued as of 01/01/2024"]])})
        pdf_a = api.upload(("a.pdf", pdf))["documents"][0]["id"]
        sheet_a = _upload_sheet(api, "a.xlsx", sheet)
        first = _run(api, [pdf_a, sheet_a])
        row = _rows(api, first)[0]
        api.client.post(f"/api/loss-runs/{first}/rows/{row.id}/decision", json={"kind": "leave_out", "note": "x"})
        api.client.post(f"/api/loss-runs/{first}/claims", json={"claim_number": "ADD-1", "paid": 5})
        calls = len(api.provider.requests)
        # the broker uploads the same files again (new Document rows, same bytes)
        pdf_b = api.upload(("again.pdf", pdf))["documents"][0]["id"]
        sheet_b = _upload_sheet(api, "again.xlsx", sheet)
        second = _run(api, [pdf_b, sheet_b])
        assert len(api.provider.requests) == calls  # no model call: both reads reused
        run = api.client.get(f"/api/runs/{second}").json()
        assert [d["reused_from_run_id"] for d in run["documents"]] == [first, first]
        extracted_first = [r for r in _rows(api, first) if r.source == "extracted"]
        assert _sig(_rows(api, second)) == _sig(extracted_first)  # identical rows, no added row
        with api.ctx.factory() as s:
            assert s.scalar(select(func.count()).select_from(models.ClaimDecision)
                            .where(models.ClaimDecision.run_id == second)) == 0
            totals = s.scalar(select(func.count()).select_from(models.PrintedTotal)
                              .where(models.PrintedTotal.run_id == second))
            assert totals == 1
        review = api.client.get(f"/api/loss-runs/{second}").json()
        assert review["totals"]["claims"] == 5 and review["totals"]["counts"]["left_out"] == 0
        chip = api.client.get(f"/api/loss-runs/{second}/claims").json()["rows"][0]["chip"]
        assert chip["document_id"] in (pdf_b, sheet_b)  # citations open the broker's own upload
    finally:
        api.client.__exit__(None, None, None)


def test_retry_after_a_budget_stop_reads_only_the_unread_pages(tmp_path, monkeypatch):
    state = {"stop": True}
    rows_reply = rows_reply_from_truth(TRUTH)
    base = loss_responder(rows_reply)

    def respond(req):
        if req.schema is not None and req.schema.name == "loss_rows":
            pages = [int(p) for p in re.findall(r"\[page (\d+)\]", user_text(req))]
            if state["stop"] and 3 in pages:
                return BudgetExhausted(requested=10, remaining=0, label="test")
        return base(req)

    api = make_api(tmp_path, monkeypatch, respond)
    try:
        pdf_id = api.upload(("long.pdf", loss_pdf([[r] for r in R])))["documents"][0]["id"]
        run_id = _run(api, [pdf_id])
        run = api.client.get(f"/api/runs/{run_id}").json()
        doc = run["documents"][0]
        assert run["status"] == "partial" and doc["error_code"] == "budget_exhausted"
        assert doc["error"] == "Stopped at page 2: budget reached. Retry reads pages 3 to 4."
        review = api.client.get(f"/api/loss-runs/{run_id}").json()
        assert review["totals"]["documents"][0]["unread_pages"] == [3, 4]
        assert {r.claim_key for r in _rows(api, run_id)} == {"TR-1", "TR-2"}
        assert any("not fully read (pages 3 to 4)" in n["message"] for n in review["notices"])
        # spent more than the whole loss-run budget: Retry still gets a fresh document allowance (O5)
        with api.ctx.factory() as s:
            s.get(models.Run, run_id).tokens_in = ProviderConfig().loss_run_token_budget + 1
            s.commit()
        state["stop"] = False
        before = len(api.provider.requests)
        assert api.client.post(f"/api/runs/{run_id}/retry").status_code == 202
        api.events(run_id)
        row_windows = [sorted({int(p) for p in re.findall(r"\[page (\d+)\]", user_text(q))})
                       for q in api.provider.requests[before:] if q.schema and q.schema.name == "loss_rows"]
        assert row_windows == [[2, 3], [3, 4]]  # pages 1 and 2 were read: only windows with an unread page
        run = api.client.get(f"/api/runs/{run_id}").json()
        assert run["status"] == "done" and run["documents"][0]["status"] == "done"
        assert sorted(r.claim_key for r in _rows(api, run_id)) == ["TR-1", "TR-2", "TR-3", "TR-4"]  # no duplicate
    finally:
        api.client.__exit__(None, None, None)


def test_decision_survives_a_re_read_and_a_changed_value_reopens(tmp_path, monkeypatch):
    sheet = xlsx_bytes({"2024": claims_sheet([["TR-1", "A", "2024-01-01", "O", 100, 0, "=E2+F2"]])})
    api = make_api(tmp_path, monkeypatch, loss_responder())
    try:
        sid = _upload_sheet(api, "a.xlsx", sheet)
        run_id = _run(api, [sid])
        row = _rows(api, run_id)[0]
        api.client.post(f"/api/loss-runs/{run_id}/rows/{row.id}/decision", json={"kind": "confirm", "note": "ok"})
        # a re-read replaces the extracted rows (new ids); the decision is keyed by location + claim key
        from backend.loss_runs import report  # noqa: PLC0415
        with api.ctx.factory() as s:
            old = s.get(models.ClaimRow, row.id)
            values = {c.key: getattr(old, c.key) for c in models.ClaimRow.__table__.columns if c.key != "id"}
            s.delete(old)
            s.flush()
            s.add(models.ClaimRow(**values))
            run = s.get(models.Run, run_id)
            report.rebuild(s, run, note="re-read")
            s.commit()
        new = _rows(api, run_id)[0]
        assert new.decision == "confirmed" and new.counted
        with api.ctx.factory() as s:
            s.get(models.ClaimRow, new.id).paid = 90
            report.rebuild(s, s.get(models.Run, run_id), note="re-read with a new amount")
            s.commit()
        changed = api.client.get(f"/api/loss-runs/{run_id}/claims").json()["rows"][0]
        assert changed["changed_since_resolved"] and changed["decision"] is None and not changed["counted"]
    finally:
        api.client.__exit__(None, None, None)


# --------------------------------------------------------------------- regression contract: other workflows


def test_compare_quotes_reuse_and_key_are_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv(cache.ENV_SWITCH, "on")
    cfg = ProviderConfig()
    assert cache.extraction_key([], "x", cfg) == cache.extraction_key([], "x", cfg, extra=None)
    assert cache.extraction_key([], "x", cfg) != cache.extraction_key([], "x", cfg, extra={"rows": 1})
    api = make_api(tmp_path, monkeypatch, api_responder())
    try:
        from conftest import make_pdf  # noqa: PLC0415
        pdf = make_pdf(DOC_A)
        a = api.upload(("a.pdf", pdf))["documents"][0]["id"]
        first, _ = api.run([a])
        b = api.upload(("b.pdf", pdf))["documents"][0]["id"]
        second, _ = api.run([b])
        assert second["documents"][0]["reused_from_run_id"] == first["id"]
        with api.ctx.factory() as s:
            assert s.scalar(select(func.count()).select_from(models.ClaimRow)) == 0
            assert s.scalar(select(func.count()).select_from(models.LossWindow)) == 0
    finally:
        api.client.__exit__(None, None, None)


def test_compare_quotes_retry_budget_is_unchanged(tmp_path, monkeypatch):
    """A non-loss retry keeps the run budget minus what was spent (no loss-run allowance)."""
    api = make_api(tmp_path, monkeypatch, api_responder())
    try:
        from conftest import make_pdf  # noqa: PLC0415
        a = api.upload(("a.pdf", make_pdf(DOC_A)))["documents"][0]["id"]
        run, _ = api.run([a])
        manager = api.ctx.manager
        with api.ctx.factory() as s:
            r = s.get(models.Run, run["id"])
            r.tokens_in, r.tokens_out = manager.budget_limit - 10, 0
            r.status = "partial"
            s.commit()
        with api.ctx.factory() as s:
            r = s.get(models.Run, run["id"])
            r.status = "queued"
            s.commit()
        plan = manager._begin(run["id"])
        assert plan.budget_limit == 10 and not plan.loss_run
    finally:
        api.client.__exit__(None, None, None)
