"""Slice 3 T6: discrepancy_state (open | resolved, note, value fingerprint) and its endpoints.

GET  /api/runs/{id}/discrepancies   ?severity= ?hide_resolved=   items + counts per severity
PATCH /api/runs/{id}/discrepancies/{item_id}                     status and note

Resolve state lives outside the artifact, so a regenerate never wipes it. Whenever the
active comparison changes (regenerate, retry, version restore), each item's fingerprint
of its compared slot values is reconciled: a resolved item whose values changed resets to
open and is flagged "changed since resolved". No bulk endpoint and no accepted status (D1, D2).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from api_support import make_api
from backend import db, models
from backend.providers import FakeProvider
from backend.schemas import Cell, DiscrepancyBlock, EvidenceState, PolicyRole, RunStatus, Severity
from conftest import make_pdf
from pipeline_fakes import policy_responder
from test_policy_check_run import AMS, EXPIRING, QUOTE, RENEWAL, PCHarness, _run
from test_runner import go

COV_A = "disc-coverage-a-limit"
PREMIUM = "disc-total-premium"


@pytest.fixture
def pc(tmp_path, monkeypatch):
    a = make_api(tmp_path, monkeypatch, policy_responder())
    yield a
    a.client.__exit__(None, None, None)


def _upload_ams(api, obj) -> int:
    r = api.client.post("/api/documents/ams",
                        files=[("files", ("ams.json", json.dumps(obj).encode(), "application/json"))])
    assert r.status_code == 200 and not r.json()["rejected"], r.text
    return r.json()["documents"][0]["id"]


def _policy_run(api, with_ams=True) -> int:
    exp, ren = api.upload_pages(EXPIRING, "exp.pdf"), api.upload_pages(RENEWAL, "ren.pdf")
    slots = {str(exp): "expiring", str(ren): "renewal"}
    ids = [exp, ren]
    if with_ams:
        amsd = _upload_ams(api, AMS)
        ids.append(amsd)
        slots[str(amsd)] = "ams"
    r = api.client.post("/api/runs", json={"document_ids": ids, "workflow": "policy-check", "slots": slots})
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    assert api.client.get(f"/api/runs/{run_id}").json()["status"] == "done"
    return run_id


def _get(api, run_id, **params):
    r = api.client.get(f"/api/runs/{run_id}/discrepancies", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _patch(api, run_id, item_id, body, status=200):
    r = api.client.patch(f"/api/runs/{run_id}/discrepancies/{item_id}", json=body)
    assert r.status_code == status, r.text
    return r.json()


def _item(listing, item_id):
    return next(i for i in listing["items"] if i["id"] == item_id)


# --------------------------------------------------------------------- GET


def test_list_returns_items_in_order_with_open_status_and_counts(pc):
    run_id = _policy_run(pc)
    out = _get(pc, run_id)
    assert [i["key"] for i in out["items"]] == ["coverage_a_limit", "deductible_all_peril",
                                                "discount_multi_policy", "total_premium", "form:HO0003"]
    assert out["counts"] == {"critical": 2, "high": 2, "medium": 1, "low": 0}
    assert out["open_counts"] == out["counts"]
    assert out["total"] == 5 and out["resolved"] == 0
    first = out["items"][0]
    assert first["status"] == "open" and first["note"] is None and first["changed_since_resolved"] is False
    assert first["severity"] == "critical" and first["title"] and first["prose"]
    assert first["slots"]["renewal"]["value"] == "$300,000"
    assert out["artifact_id"] and out["version"] == 1


def test_severity_filter_and_validation(pc):
    run_id = _policy_run(pc)
    crit = _get(pc, run_id, severity="critical")
    assert [i["key"] for i in crit["items"]] == ["coverage_a_limit", "deductible_all_peril"]
    assert crit["counts"] == {"critical": 2, "high": 2, "medium": 1, "low": 0}  # counts stay unfiltered
    both = _get(pc, run_id, severity="critical,medium")
    assert [i["severity"] for i in both["items"]] == ["critical", "critical", "medium"]
    r = pc.client.get(f"/api/runs/{run_id}/discrepancies", params={"severity": "urgent"})
    assert r.status_code == 422


def test_list_404s(pc):
    assert pc.client.get("/api/runs/999/discrepancies").status_code == 404
    assert pc.client.patch("/api/runs/999/discrepancies/disc-x", json={"status": "resolved"}).status_code == 404


# --------------------------------------------------------------------- PATCH


def test_resolve_with_note_and_hide_resolved(pc):
    run_id = _policy_run(pc)
    item = _patch(pc, run_id, COV_A, {"status": "resolved", "note": "Insured asked for the lower limit."})
    assert item["status"] == "resolved" and item["note"] == "Insured asked for the lower limit."
    assert item["updated_at"]
    out = _get(pc, run_id)
    assert _item(out, COV_A)["status"] == "resolved"
    assert out["resolved"] == 1 and out["open_counts"]["critical"] == 1 and out["counts"]["critical"] == 2
    hidden = _get(pc, run_id, hide_resolved="true")
    assert COV_A not in [i["id"] for i in hidden["items"]] and len(hidden["items"]) == 4
    # a note alone keeps the status; reopening clears nothing else
    assert _patch(pc, run_id, COV_A, {"note": "Confirmed by phone."})["status"] == "resolved"
    reopened = _patch(pc, run_id, COV_A, {"status": "open"})
    assert reopened["status"] == "open" and reopened["note"] == "Confirmed by phone."
    assert _patch(pc, run_id, COV_A, {"note": None})["note"] is None


@pytest.mark.parametrize("body", [{"status": "accepted"}, {"status": "dismissed"}, {}, {"note": "x" * 2001},
                                  {"status": "resolved", "extra": 1}])
def test_patch_rejects_unknown_status_empty_and_oversized_bodies(pc, body):
    run_id = _policy_run(pc, with_ams=False)
    r = pc.client.patch(f"/api/runs/{run_id}/discrepancies/{COV_A}", json=body)
    assert r.status_code == 422, r.text


def test_patch_unknown_item_is_404(pc):
    run_id = _policy_run(pc, with_ams=False)
    r = pc.client.patch(f"/api/runs/{run_id}/discrepancies/disc-nope", json={"status": "resolved"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "discrepancy_not_found"


def test_note_control_characters_are_stripped(pc):
    run_id = _policy_run(pc, with_ams=False)
    item = _patch(pc, run_id, COV_A, {"note": "  ok\x00\x07 then  "})
    assert item["note"] == "ok then"


def test_no_bulk_endpoint(pc):
    run_id = _policy_run(pc, with_ams=False)
    r = pc.client.post(f"/api/runs/{run_id}/discrepancies/bulk", json={"status": "resolved"})
    assert r.status_code in (404, 405)


def test_compare_quotes_run_has_an_empty_discrepancy_list(tmp_path, monkeypatch):
    from api_support import DOC_A
    a = make_api(tmp_path, monkeypatch)
    try:
        run, _ = a.run([a.upload_pages(DOC_A)])
        out = _get(a, run["id"])
        assert out["items"] == [] and out["counts"] == {"critical": 0, "high": 0, "medium": 0, "low": 0}
    finally:
        a.client.__exit__(None, None, None)


# --------------------------------------------------------------------- regenerate, restore


def _art(api, run_id) -> int:
    with api.ctx.factory() as s:
        return s.get(models.Run, run_id).artifact.id


def test_resolve_survives_regenerate_without_a_model_call(pc):
    run_id = _policy_run(pc)
    _patch(pc, run_id, COV_A, {"status": "resolved", "note": "ok"})
    calls = len(pc.provider.requests)
    r = pc.client.post(f"/api/artifacts/{_art(pc, run_id)}/regenerate", json={})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2 and r.json()["fallback"] is False
    assert len(pc.provider.requests) == calls  # deterministic rebuild (D14)
    out = _get(pc, run_id)
    assert out["version"] == 2
    item = _item(out, COV_A)
    assert item["status"] == "resolved" and item["note"] == "ok" and item["changed_since_resolved"] is False


def _set_fact(api, run_id, field, value, *, slot="renewal"):
    with db.session_scope(api.ctx.factory) as s:
        run = s.get(models.Run, run_id)
        doc_id = next(rd.document_id for rd in run.documents if rd.slot == slot)
        fact = next(f for f in run.facts if f.document_id == doc_id and f.field == field)
        fact.value = value


def test_changed_values_reset_to_open_and_flag_changed_since_resolved(pc):
    run_id = _policy_run(pc)
    _patch(pc, run_id, COV_A, {"status": "resolved"})
    _patch(pc, run_id, PREMIUM, {"status": "resolved"})
    _set_fact(pc, run_id, "coverage_a_limit", "$310,000")  # the renewal's value changes
    assert pc.client.post(f"/api/artifacts/{_art(pc, run_id)}/regenerate", json={}).status_code == 200
    out = _get(pc, run_id)
    changed = _item(out, COV_A)
    assert changed["status"] == "open" and changed["changed_since_resolved"] is True
    assert changed["slots"]["renewal"]["value"] == "$310,000"
    assert _item(out, PREMIUM)["status"] == "resolved"  # untouched values keep the decision
    # resolving again clears the flag
    again = _patch(pc, run_id, COV_A, {"status": "resolved"})
    assert again["changed_since_resolved"] is False


def test_restoring_the_resolved_version_brings_the_decision_back(pc):
    run_id = _policy_run(pc)
    _patch(pc, run_id, COV_A, {"status": "resolved"})
    _set_fact(pc, run_id, "coverage_a_limit", "$310,000")
    art = _art(pc, run_id)
    assert pc.client.post(f"/api/artifacts/{art}/regenerate", json={}).status_code == 200
    assert _item(_get(pc, run_id), COV_A)["status"] == "open"
    r = pc.client.post(f"/api/artifacts/{art}/undo", json={"version": 1})
    assert r.status_code == 200, r.text
    item = _item(_get(pc, run_id), COV_A)
    assert item["status"] == "resolved" and item["changed_since_resolved"] is False
    assert item["slots"]["renewal"]["value"] == "$300,000"


def test_items_that_leave_and_return_keep_their_state(pc):
    run_id = _policy_run(pc)
    _patch(pc, run_id, COV_A, {"status": "resolved"})
    _set_fact(pc, run_id, "coverage_a_limit", "$350,000")  # renewal now equals expiring; AMS agrees
    art = _art(pc, run_id)
    pc.client.post(f"/api/artifacts/{art}/regenerate", json={})
    assert COV_A not in [i["id"] for i in _get(pc, run_id)["items"]]
    _set_fact(pc, run_id, "coverage_a_limit", "$300,000")
    pc.client.post(f"/api/artifacts/{art}/regenerate", json={})
    assert _item(_get(pc, run_id), COV_A)["status"] == "resolved"


# --------------------------------------------------------------------- retry


def test_retry_reconciles_resolve_state(pdf_factory):
    h = PCHarness(pdf_factory)
    try:
        exp, ren = h.add_doc(EXPIRING), h.add_doc(RENEWAL)
        quo = h.add_doc(data=b"not a pdf at all", name="quote.pdf")
        fake = FakeProvider(policy_responder())
        rid = _run(h, fake, {"expiring": exp, "renewal": ren, "quote": quo})
        with db.session_scope(h.factory) as s:
            assert s.get(models.Run, rid).status == RunStatus.PARTIAL
            models.update_discrepancy_state(s, rid, COV_A, status="resolved")
            models.update_discrepancy_state(s, rid, "disc-deductible-all-peril", status="resolved")
        h.blobs[quo] = make_pdf(QUOTE)  # the quote can be read now

        async def main():
            m = h.manager(fake)
            assert m.retry(rid)
            await m.wait(rid)
            await m.shutdown()
        go(main())

        with h.factory() as s:
            run = s.get(models.Run, rid)
            assert run.status == RunStatus.DONE and run.artifact.current_version == 2
            states = {r.item_id: r for r in s.scalars(select(models.DiscrepancyState)
                                                       .where(models.DiscrepancyState.run_id == rid))}
        # the quote slot went from Not read to a value: the comparison changed, so it reopens
        assert states[COV_A].status == "open" and states[COV_A].changed_since_resolved
        # the quote does not state the deductible: Not read became N/A, also a changed comparison
        assert states["disc-deductible-all-peril"].status == "open"
    finally:
        h.engine.dispose()


# --------------------------------------------------------------------- fingerprint (pure)


def _block(**slots) -> DiscrepancyBlock:
    return DiscrepancyBlock(id="disc-k", key="k", title="t", severity=Severity.HIGH,
                            slots={PolicyRole(k): v for k, v in slots.items()})


def test_fingerprint_covers_compared_values_not_citations():
    a = _block(renewal=Cell(value="$1", passage_ids=[1], state=EvidenceState.VERIFIED))
    moved = _block(renewal=Cell(value="$1", passage_ids=[7], state=EvidenceState.VERIFIED))
    changed = _block(renewal=Cell(value="$2", passage_ids=[1], state=EvidenceState.VERIFIED))
    unread = _block(renewal=Cell(state=EvidenceState.FAILED))
    fp = models.discrepancy_fingerprint
    assert fp(a) == fp(moved)
    assert len({fp(a), fp(changed), fp(unread), fp(_block())}) == 4


def test_state_table_is_created_with_its_constraints():
    engine = db.make_engine("sqlite://")
    db.init_db(engine)
    factory = db.make_session_factory(engine)
    with factory() as s:
        cols = {c.name for c in models.DiscrepancyState.__table__.columns}
        assert {"run_id", "item_id", "status", "note", "updated_at", "fingerprint"} <= cols
        with pytest.raises(ValueError):
            models.update_discrepancy_state(s, 1, "disc-x", status="accepted")
    engine.dispose()
