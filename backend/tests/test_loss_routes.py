"""Slice 5a T5: loss review routes end to end on a fake model: upload (PDF + spreadsheets), run, review, paged
claims on effective values (D2, D10), decisions with Undo (D4, O2), checks (accept, pick, reopen), chips (O7, R5)."""

from __future__ import annotations

import fitz
import pytest

from api_support import make_api
from backend.loss_runs import sheets
from loss_support import claims_sheet, loss_pdf, loss_responder, rows_reply_from_truth, xlsx_bytes

P1 = {"claim": "TR-100", "claimant": "Ann Lee", "dol": "05/01/2025", "status": "O", "paid": "90,000.00",
      "reserve": "30,000.00", "incurred": "120,000.00"}
P2 = {"claim": "TR-200", "claimant": "Bo Park", "dol": "01/05/2024", "status": "C", "paid": "1,000.00",
      "reserve": "0.00", "incurred": "1,000.00"}


def _sheet_2024():
    return xlsx_bytes({"2024": claims_sheet(
        [["TR-200", "Bo Park", "2024-01-05", "C", 900, 0, 900],         # older valuation of the PDF's TR-200
         ["TR-300", "Cy Diaz", "2024-02-01", "Open", 5000, 5000, 10000],
         ["TR-400", "Di Ek", "2024-03-01", "C", 100, 0, "=E5+F5"],      # formula with no stored value
         ["Total", None, None, None, 6000, 5000, 12000]],               # printed 12,000 vs rows 10,900 + 100 computed
        title=[["Carrier: Travelers"], ["Valued as of 12/31/2024"], ["Insured: Calloway Build Group LLC"]])})


def _csv_same_date():
    return (b"Carrier: Travelers\nValued as of 12/31/2024\n\nClaim #,Claimant,Status,Paid,Reserve,Total Incurred\n"
            b"TR-300,Cy Diaz,O,5000,6000,11000\n")  # same date as the xlsx, different amounts: a conflict


@pytest.fixture
def lapi(tmp_path, monkeypatch):
    responder = loss_responder(rows_reply_from_truth({1: [P1, P2]}, totals={1: [
        {"label": "Grand Total", "metric": "incurred", "amount": "121,000.00"}]}))
    a = make_api(tmp_path, monkeypatch, responder)
    yield a
    a.client.__exit__(None, None, None)


def _upload_sheet(api, name, data):
    r = api.client.post("/api/documents/spreadsheets", files=[("files", (name, data, "application/octet-stream"))])
    assert r.status_code == 200, r.text
    return r.json()


def _review(api):
    pdf = loss_pdf([[P1, P2]], valued="06/30/2025", totals={1: [("Grand Total", "121,000.00")]})
    pdf_id = api.upload(("travelers-2025.pdf", pdf))["documents"][0]["id"]
    xlsx = _upload_sheet(api, "travelers-2024.xlsx", _sheet_2024())["documents"][0]
    csv = _upload_sheet(api, "travelers-export.csv", _csv_same_date())["documents"][0]
    r = api.client.post("/api/runs", json={"document_ids": [pdf_id, xlsx["id"], csv["id"]], "workflow": "loss-run",
                                           "client_name": "Calloway Build Group LLC"})
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    out = api.client.get(f"/api/loss-runs/{run_id}").json()
    return run_id, out, (pdf_id, xlsx["id"], csv["id"])


def test_spreadsheet_upload_summarizes_sheets_and_refuses_over_caps(lapi):
    out = _upload_sheet(lapi, "t.xlsx", _sheet_2024())
    doc = out["documents"][0]
    assert doc["kind"] == "spreadsheet" and doc["page_count"] == 1
    sheet = doc["spreadsheet"]["sheets"][0]
    assert [m["header"] + " -> " + m["label"] for m in sheet["mapped"]][:2] == ["Claim # -> Claim number",
                                                                                 "Claimant -> Claimant"]
    many = xlsx_bytes({f"s{i}": [["x"]] for i in range(21)})
    bad = _upload_sheet(lapi, "many.xlsx", many)
    assert bad["documents"] == [] and bad["rejected"][0]["code"] == "too_many_sheets"
    assert lapi.client.get(f"/api/documents/{doc['id']}/sheets").json()["spreadsheet"]["data_rows"] == 4
    assert lapi.client.get(f"/api/documents/{doc['id']}/file").json()["error"]["code"] == "spreadsheet"


def test_a_spreadsheet_only_goes_into_a_loss_review(lapi):
    doc = _upload_sheet(lapi, "t.xlsx", _sheet_2024())["documents"][0]
    r = lapi.client.post("/api/runs", json={"document_ids": [doc["id"]]})
    assert r.status_code == 400 and "loss review" in r.json()["error"]["message"]


def test_review_totals_roles_checks_and_notices(lapi):
    run_id, out, (pdf_id, xlsx_id, csv_id) = _review(lapi)
    assert out["run"]["status"] == "done", out["run"]
    assert out["client"] == "Calloway Build Group LLC"
    t = out["totals"]
    # counted: TR-100 (120,000) + TR-200 from the later PDF (1,000); TR-300 conflicts (xlsx vs csv, same date);
    # TR-400 unverified (formula not calculated) stays out until confirmed
    assert t["claims"] == 2 and t["incurred"]["cents"] == 121_000_00 and t["unreconciled"]
    kinds = sorted((c["kind"], c["verdict"]) for c in out["checks"])
    # PDF grand total incurred; the sheet's Total row prints paid, reserve and incurred (only incurred is off)
    assert kinds == [("conflict", "conflicting"), ("total", "matches"), ("total", "matches"), ("total", "matches"),
                     ("total", "off"), ("unverified", "unverified")]
    off = next(c for c in out["checks"] if c["verdict"] == "off")
    assert off["document_id"] == xlsx_id and off["printed"] == 12_000_00 and off["computed"] == 11_000_00
    assert off["chips"][0]["kind"] == "sheet" and off["chips"][0]["label"] == "Sheet 2024, row 8"
    assert any("travelers-export.csv prints no totals" in n["message"] for n in out["notices"])
    assert out["sheets"][str(xlsx_id)][0]["sheet"] == "2024"


def test_claims_page_sort_quick_filters_and_history(lapi):
    run_id, _, (pdf_id, xlsx_id, _) = _review(lapi)
    page = lapi.client.get(f"/api/loss-runs/{run_id}/claims", params={"sort": "claim_incurred"}).json()
    assert page["total"] == 5 and page["rows"][0]["claim_number"] == "TR-100"  # superseded TR-200 folded
    tr200 = next(r for r in page["rows"] if r["claim_number"] == "TR-200")
    assert tr200["role"] == "counted" and [h["document_id"] for h in tr200["history"]] == [xlsx_id]
    chip = page["rows"][0]["chip"]
    assert chip["kind"] == "page" and chip["label"] == "Travelers p.1" and chip["rects"]
    over = lapi.client.get(f"/api/loss-runs/{run_id}/claims", params={"over_100k": True}).json()
    assert [r["claim_number"] for r in over["rows"]] == ["TR-100"]
    open_ = lapi.client.get(f"/api/loss-runs/{run_id}/claims", params={"open_only": True}).json()
    assert {r["claim_number"] for r in open_["rows"]} == {"TR-100", "TR-300"}
    recent = lapi.client.get(f"/api/loss-runs/{run_id}/claims", params={"last_3_months": True}).json()
    assert [r["claim_number"] for r in recent["rows"]] == ["TR-100"]  # within 3 months of 2025-06-30
    reserve = lapi.client.get(f"/api/loss-runs/{run_id}/claims", params={"open_reserve": True}).json()
    assert {r["claim_number"] for r in reserve["rows"]} == {"TR-100", "TR-300"}
    p2 = lapi.client.get(f"/api/loss-runs/{run_id}/claims", params={"limit": 2, "offset": 2, "sort": "claim_number",
                                                                    "dir": "asc"}).json()
    assert [r["claim_number"] for r in p2["rows"]] == ["TR-300", "TR-300"]
    unv = lapi.client.get(f"/api/loss-runs/{run_id}/claims", params={"state": "unverified"}).json()
    assert [r["claim_number"] for r in unv["rows"]] == ["TR-400"]


def test_sheet_chip_opens_the_rendered_page_with_the_row_box(lapi):
    run_id, _, (_, xlsx_id, _) = _review(lapi)
    rows = lapi.client.get(f"/api/loss-runs/{run_id}/claims", params={"document_id": xlsx_id,
                                                                      "include_superseded": True}).json()["rows"]
    tr300 = next(r for r in rows if r["claim_number"] == "TR-300")
    chip = tr300["chip"]
    assert chip["label"] == "Sheet 2024, row 6" and chip["url"].endswith("/sheet-pages/1")
    pdf = lapi.client.get(chip["url"])
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    page = fitz.open(stream=pdf.content, filetype="pdf")[0]
    assert "TR-300" in page.get_textbox(fitz.Rect(*chip["rects"][0]))
    assert lapi.client.get(f"/api/documents/{xlsx_id}/sheet-pages/9").status_code == 404


def test_edit_resolves_the_mismatch_and_undo_brings_it_back(lapi):
    run_id, out, (_, xlsx_id, _) = _review(lapi)
    rows = lapi.client.get(f"/api/loss-runs/{run_id}/claims", params={"document_id": xlsx_id,
                                                                      "include_superseded": True}).json()["rows"]
    tr400 = next(r for r in rows if r["claim_number"] == "TR-400")
    r = lapi.client.post(f"/api/loss-runs/{run_id}/rows/{tr400['id']}/decision",
                         json={"kind": "edit", "amounts": {"incurred": 110_000}})
    assert r.status_code == 201, r.text
    body = r.json()
    off = next(c for c in body["review"]["checks"] if c["kind"] == "total" and c["document_id"] == xlsx_id
               and c["metric"] == "incurred")
    assert off["verdict"] == "matches"  # 10,900 + 1,100 = 12,000
    row = next(x for x in lapi.client.get(f"/api/loss-runs/{run_id}/claims", params={
        "document_id": xlsx_id, "include_superseded": True}).json()["rows"] if x["id"] == tr400["id"])
    assert row["decision"]["kind"] == "edited" and row["extracted"]["incurred"] is None
    assert row["effective"]["incurred"] == 110_000 and row["counted"]
    undo = lapi.client.delete(f"/api/loss-runs/{run_id}/decisions/{body['decision']['id']}")
    assert undo.status_code == 200
    off = next(c for c in undo.json()["review"]["checks"] if c["kind"] == "total" and c["document_id"] == xlsx_id
               and c["metric"] == "incurred")
    assert off["verdict"] == "off"


def test_confirm_needs_a_reason_leave_out_needs_a_note(lapi):
    run_id, _, (_, xlsx_id, _) = _review(lapi)
    rows = lapi.client.get(f"/api/loss-runs/{run_id}/claims", params={"state": "unverified"}).json()["rows"]
    rid = rows[0]["id"]
    r = lapi.client.post(f"/api/loss-runs/{run_id}/rows/{rid}/decision", json={"kind": "confirm"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "note_required"
    r = lapi.client.post(f"/api/loss-runs/{run_id}/rows/{rid}/decision",
                         json={"kind": "confirm", "note": "Formula checked in Excel"})
    review = r.json()["review"]
    assert review["totals"]["incurred"]["cents"] == 121_100_00  # TR-400 counts, incurred computed (100 + 0)
    assert next(c for c in review["checks"] if c["kind"] == "unverified")["status"] == "resolved"
    r = lapi.client.post(f"/api/loss-runs/{run_id}/rows/{rid}/decision", json={"kind": "leave_out"})
    assert r.status_code == 422
    r = lapi.client.post(f"/api/loss-runs/{run_id}/rows/{rid}/decision", json={"kind": "leave_out", "note": "dup"})
    assert r.json()["decision"]["replaced_id"] is not None
    assert lapi.client.delete(f"/api/loss-runs/{run_id}/decisions/{r.json()['decision']['replaced_id']}").status_code \
        == 409  # only the current decision can be undone


def test_pick_a_conflicting_value_then_undo(lapi):
    run_id, out, (_, xlsx_id, csv_id) = _review(lapi)
    conflict = next(c for c in out["checks"] if c["kind"] == "conflict")
    r = lapi.client.patch(f"/api/loss-runs/{run_id}/checks/{conflict['id']}",
                          json={"action": "pick", "document_id": csv_id, "expected_fingerprint": conflict["fingerprint"]})
    assert r.status_code == 200, r.text
    review = r.json()
    assert review["totals"]["incurred"]["cents"] == 132_000_00
    rows = lapi.client.get(f"/api/loss-runs/{run_id}/claims", params={"claim_key": "TR-300"}).json()["rows"]
    loser = next(x for x in rows if x["document_id"] == xlsx_id)
    assert loser["role"] == "superseded" and loser["role_reason"] == "Not used (you picked travelers-export.csv)"
    r = lapi.client.patch(f"/api/loss-runs/{run_id}/checks/{conflict['id']}", json={"action": "reopen"})
    assert r.json()["totals"]["incurred"]["cents"] == 121_000_00
    stale = lapi.client.patch(f"/api/loss-runs/{run_id}/checks/{conflict['id']}",
                              json={"action": "pick", "document_id": csv_id, "expected_fingerprint": "0" * 64})
    assert stale.status_code == 409


def test_accept_a_gap_needs_a_note(lapi):
    run_id, out, _ = _review(lapi)
    off = next(c for c in out["checks"] if c["verdict"] == "off")
    r = lapi.client.patch(f"/api/loss-runs/{run_id}/checks/{off['id']}", json={"action": "accept"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "note_required"
    r = lapi.client.patch(f"/api/loss-runs/{run_id}/checks/{off['id']}",
                          json={"action": "accept", "note": "Carrier total includes a closed claim"})
    check = next(c for c in r.json()["checks"] if c["id"] == off["id"])
    assert check["status"] == "resolved" and check["note"] == "Carrier total includes a closed claim"
    unv = next(c for c in r.json()["checks"] if c["kind"] == "unverified")
    r = lapi.client.patch(f"/api/loss-runs/{run_id}/checks/{unv['id']}", json={"action": "accept", "note": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "decided_by_rows"


def test_add_a_missed_claim_and_undo(lapi):
    run_id, _, (_, xlsx_id, _) = _review(lapi)
    r = lapi.client.post(f"/api/loss-runs/{run_id}/claims",
                         json={"claim_number": "TR-999", "document_id": xlsx_id, "status": "Closed", "paid": "1,000.00"})
    assert r.status_code == 201, r.text
    row = r.json()["row"]
    assert row["source"] == "added" and row["chip"] is None and row["carrier"] == "Travelers"
    off = next(c for c in r.json()["review"]["checks"]
               if c["kind"] == "total" and c["document_id"] == xlsx_id and c["metric"] == "incurred")
    assert off["verdict"] == "matches", off  # the missed claim closes the 1,000 gap
    assert lapi.client.delete(f"/api/loss-runs/{run_id}/claims/{row['id']}").status_code == 200
    extracted = lapi.client.get(f"/api/loss-runs/{run_id}/claims").json()["rows"][0]
    r = lapi.client.delete(f"/api/loss-runs/{run_id}/claims/{extracted['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "extracted_row"


def test_analytics_list_and_review_cap(lapi, monkeypatch):
    run_id, _, ids = _review(lapi)
    reviews = lapi.client.get("/api/loss-runs").json()["reviews"]
    assert reviews[0]["run_id"] == run_id and reviews[0]["client"] == "Calloway Build Group LLC"
    assert reviews[0]["files"] == 3 and reviews[0]["latest_valuation_date"] == "2025-06-30"
    assert reviews[0]["open_checks"] == 3
    monkeypatch.setattr(sheets, "DEFAULT_SHEET_LIMITS", sheets.SheetLimits(max_rows_per_review=3))
    r = lapi.client.post("/api/runs", json={"document_ids": [ids[1], ids[2]], "workflow": "loss-run"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_many_rows"


def test_decisions_wait_for_a_running_review(lapi):
    run_id, out, _ = _review(lapi)
    r = lapi.client.patch(f"/api/loss-runs/{run_id}/checks/nope", json={"action": "reopen"})
    assert r.status_code == 404
    assert lapi.client.get("/api/loss-runs/999").status_code == 404
