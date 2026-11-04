"""Slice 5a T13 (measurement only, no bound approved): a generated review at the L8 cap, time per broker action.

The normal suite runs a small review (5,000 rows) as a smoke test. The full measurement:

    BROKERDOCS_LOSS_PERF_ROWS=200000 pytest tests/test_loss_perf.py -k loss_perf -s

prints the time of each action (build, page, edit, confirm, leave out, pick, accept, undo) and writes them to
$BROKERDOCS_LOSS_PERF_REPORT when set. Two spreadsheet files of half the rows each; the later file re-values
10% of the earlier file's claims (superseded), a third file re-values 1% on the same date with other amounts, 2% are unverified, 3 printed totals.
"""

from __future__ import annotations

import json
import os
import time

from sqlalchemy import insert, select

from api_support import make_api
from backend import db, models
from backend.loss_runs import report
from backend.schemas import RunDocumentStatus

ROWS = int(os.environ.get("BROKERDOCS_LOSS_PERF_ROWS", "5000"))
GENEROUS_SECONDS = 120.0  # only catches a pathology; the measured times are the deliverable


def _seed(s, n: int) -> int:
    wf = s.scalar(select(models.Workflow).where(models.Workflow.slug == db.LOSS_RUN_SLUG))
    docs = []
    for i, (name, valued) in enumerate((("a.xlsx", "2024-12-31"), ("b.xlsx", "2025-06-30"), ("c.xlsx", "2025-06-30"))):
        d = models.Document(workspace_id=1, filename=name, stored_name=f"{i:032x}", size_bytes=1, sha256=f"{i}" * 64,
                            page_count=1, kind="spreadsheet")
        s.add(d)
        docs.append((d, valued))
    s.flush()
    run = models.Run(workspace_id=1, workflow_id=wf.id, parameters={}, status="done", line="commercial",
                     queue_state="open", client_name="Perf Client")
    for pos, (d, _) in enumerate(docs):
        run.documents.append(models.RunDocument(document_id=d.id, position=pos, slot="loss_runs",
                                                status=RunDocumentStatus.DONE, option_count=1))
    s.add(run)
    s.flush()
    for d, valued in docs:
        for field, value in (("lr:carrier", "Travelers"), ("lr:valuation_date", valued)):
            s.add(models.Fact(run_id=run.id, document_id=d.id, field=field, value=value, state="verified", rects=[]))
    half = n // 2
    payload = []
    sizes = (half, n - half - n // 100, n // 100)  # C re-values 1% of B on B's date with other amounts: conflicts
    for k, (d, _) in enumerate(docs):
        for i in range(sizes[k]):
            claim = i if k == 0 else (i if i < half // 10 else half + i) if k == 1 else half + half // 10 + i
            same_date = k == 2
            payload.append({
                "run_id": run.id, "document_id": d.id, "source": "extracted", "location": f"s0:r{i + 2}",
                "sheet_name": "2024", "sheet_row": i + 2, "page_no": 1 + i // 40, "claim_number": f"TR-{claim:07d}",
                "claim_key": f"TR-{claim:07d}", "carrier": "Travelers", "carrier_key": "travelers",
                "policy_year": 2024, "status": "open" if i % 3 else "closed", "date_of_loss": "2025-03-01",
                "paid": (i % 997) * 1000, "reserve": (i % 13) * 5000 if not same_date else 1, "incurred": None,
                "verify_state": "unverified" if i % 50 == 0 else "verified", "cells": {}, "rects": [],
                "warnings": [], "flags": [], "role": "counted", "counted": False, "group_key": "",
                "claim_open": False, "decision_stale": False})
    for start in range(0, len(payload), 5000):
        s.execute(insert(models.ClaimRow), payload[start:start + 5000])
    for d, _ in docs:
        for metric in ("paid", "reserve", "incurred"):
            s.add(models.PrintedTotal(run_id=run.id, document_id=d.id, metric=metric, scope_kind="all",
                                      amount=123_45, location=f"s0:r{half + 5}:{metric}", sheet_name="2024",
                                      sheet_row=half + 5, verify_state="verified", label="Total"))
    s.flush()
    if s.get(models.Run, run.id).artifact is None:
        models.create_artifact(s, run, title="Loss Review", content=report.content_for_run(s, run))
    return run.id


def test_loss_perf_time_per_broker_action(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch)
    timings: dict[str, float] = {}

    def timed(name, fn):
        t0 = time.perf_counter()
        out = fn()
        timings[name] = round(time.perf_counter() - t0, 3)
        return out

    try:
        with api.ctx.factory() as s:
            run_id = timed("seed_and_first_build", lambda: _seed(s, ROWS))
            s.commit()
        c = api.client
        review = timed("open_review", lambda: c.get(f"/api/loss-runs/{run_id}").json())
        assert review["totals"]["counts"]["rows"] == ROWS
        page = timed("claims_page_sorted", lambda: c.get(f"/api/loss-runs/{run_id}/claims",
                                                         params={"sort": "claim_incurred", "limit": 100}).json())
        timed("claims_page_quick_filters", lambda: c.get(f"/api/loss-runs/{run_id}/claims", params={
            "over_100k": True, "open_only": True, "limit": 100}).json())
        row = page["rows"][0]
        edit = timed("edit_amount", lambda: c.post(f"/api/loss-runs/{run_id}/rows/{row['id']}/decision",
                                                   json={"kind": "edit", "amounts": {"paid": 1}}).json())
        timed("undo_edit", lambda: c.delete(f"/api/loss-runs/{run_id}/decisions/{edit['decision']['id']}").json())
        unv = c.get(f"/api/loss-runs/{run_id}/claims", params={"state": "unverified", "limit": 1}).json()["rows"][0]
        timed("confirm_unverified", lambda: c.post(f"/api/loss-runs/{run_id}/rows/{unv['id']}/decision",
                                                   json={"kind": "confirm", "note": "seen"}).json())
        timed("leave_out", lambda: c.post(f"/api/loss-runs/{run_id}/rows/{row['id']}/decision",
                                          json={"kind": "leave_out", "note": "dup"}).json())
        checks = c.get(f"/api/loss-runs/{run_id}").json()["checks"]
        conflict = next(x for x in checks if x["kind"] == "conflict")
        timed("pick_conflict", lambda: c.patch(f"/api/loss-runs/{run_id}/checks/{conflict['id']}", json={
            "action": "pick", "document_id": conflict["options"][1]["document_id"]}).json())
        off = next(x for x in checks if x["kind"] == "total" and x["verdict"] == "off")
        timed("accept_gap", lambda: c.patch(f"/api/loss-runs/{run_id}/checks/{off['id']}",
                                            json={"action": "accept", "note": "ok"}).json())
        timed("reopen_check", lambda: c.patch(f"/api/loss-runs/{run_id}/checks/{off['id']}",
                                              json={"action": "reopen"}).json())
    finally:
        api.client.__exit__(None, None, None)
    print(f"\nloss_perf rows={ROWS} " + json.dumps(timings))
    target = os.environ.get("BROKERDOCS_LOSS_PERF_REPORT")
    if target:
        with open(target, "w", encoding="utf-8") as fh:
            json.dump({"rows": ROWS, "seconds": timings}, fh, indent=1)
    assert all(v < GENEROUS_SECONDS for v in timings.values()), timings
