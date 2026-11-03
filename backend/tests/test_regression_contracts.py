"""Slice 4 regression contracts (spec ledger T-R1 / D9 and T-R2 / D10), written before the features.

T-R1 (D9), the all-workflow extraction cache must not change what already works:
  - an eval batch never reuses an earlier read (the hook turns the cache off);
  - only DONE documents serve hits (a failed read is never copied);
  - a hit equals a live read (Compare Quotes and Policy Check, fake provider: facts, states, pages,
    chip targets on the new document);
  - a second run on the same bytes makes 0 extract calls and shows reused_from_run_id;
  - BROKERDOCS_EXTRACTION_CACHE=off reads live; the dev seed sets it for its scenario runs.

T-R2 (D10), Policy Check is pinned while its decision table also stores certificate claims:
  - status values stay open | resolved; "removed" answers 422; a note stays optional;
  - expected_fingerprint stays optional (a stale one is 409 item_changed); counts are unchanged;
  - backend/tests/test_discrepancy_state.py passes unchanged (it is not edited by this slice).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from api_support import DOC_A, DOC_B, make_api
from backend import dev_seed, eval_hook, models
from backend.pipeline import cache
from backend.providers import CallRole, FakeProvider
from conftest import make_pdf
from pipeline_fakes import any_responder, policy_responder
from test_policy_check_run import EXPIRING, RENEWAL

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "quotes"


def _extract_calls(api) -> int:
    return sum(1 for r in api.provider.requests if r.role is CallRole.EXTRACT)


def _run(api, doc_ids, **body):
    r = api.client.post("/api/runs", json={"document_ids": doc_ids, **body})
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    return api.client.get(f"/api/runs/{run_id}").json()


def _facts(api, run_id):
    with api.ctx.factory() as s:
        run = s.get(models.Run, run_id)
        letters = {rd.document_id: rd.letter for rd in run.documents}
        out = []
        for f in sorted(run.facts, key=lambda f: (letters[f.document_id], f.field)):
            p = s.get(models.Passage, f.passage_id) if f.passage_id else None
            out.append((letters[f.document_id], f.field, f.value, str(f.state), f.page_no, f.quote,
                        [list(r) for r in f.rects or []], p.page_no if p else None, p.verified if p else None,
                        [list(r) for r in (p.rects if p else [])], p.document_id if p else None))
        return out


@pytest.fixture
def api_any(tmp_path, monkeypatch):
    monkeypatch.delenv(cache.ENV_SWITCH, raising=False)
    a = make_api(tmp_path, monkeypatch, any_responder())
    yield a
    a.client.__exit__(None, None, None)


# --------------------------------------------------------------------- T-R1 (D9)


def test_second_run_on_same_bytes_makes_no_extract_call_and_equals_the_live_read(api_any):
    a = api_any
    pdf_a, pdf_b = make_pdf(DOC_A), make_pdf(DOC_B)
    first = _run(a, [a.upload(("a.pdf", pdf_a))["documents"][0]["id"],
                     a.upload(("b.pdf", pdf_b))["documents"][0]["id"]])
    calls = _extract_calls(a)
    assert calls == 2 and all(d["reused_from_run_id"] is None and d["read_at"] for d in first["documents"])
    # the broker uploads the same files again: new Document rows, same sha256
    again = [a.upload(("a again.pdf", pdf_a))["documents"][0]["id"],
             a.upload(("b again.pdf", pdf_b))["documents"][0]["id"]]
    second = _run(a, again)
    assert second["status"] == "done"
    assert _extract_calls(a) == calls  # 0 extract calls for the second run
    assert [d["reused_from_run_id"] for d in second["documents"]] == [first["id"], first["id"]]
    assert [d["read_at"] for d in second["documents"]] == [d["read_at"] for d in first["documents"]]
    live, reused = _facts(a, first["id"]), _facts(a, second["id"])
    assert [f[:10] for f in live] == [f[:10] for f in reused]  # facts, states, pages, quotes, rects
    assert {f[10] for f in reused if f[10]} == set(again)  # every citation points at the new upload
    # chip targets: same labels, pages and highlight boxes, on the broker's own documents
    def chip_view(run_id):
        art = a.artifact(run_id)
        return sorted((c["label"], c["page_no"], c["rects"], c["verified"]) for c in art["chips"].values()), \
            {c["document_id"] for c in art["chips"].values()}
    (live_chips, _), (reused_chips, docs) = chip_view(first["id"]), chip_view(second["id"])
    assert live_chips == reused_chips and docs <= set(again)


def test_picking_the_same_document_reuses_it_directly(api_any):
    a = api_any
    doc = a.upload_pages(DOC_A)
    first = _run(a, [doc])
    calls = _extract_calls(a)
    second = _run(a, [doc], client_name="Cedar Hollow LLC")  # another run (not the active-run reuse)
    assert second["id"] != first["id"] and _extract_calls(a) == calls
    assert second["documents"][0]["reused_from_run_id"] == first["id"]
    assert [f[:10] for f in _facts(a, first["id"])] == [f[:10] for f in _facts(a, second["id"])]


def _policy_items(api, run_id):
    items = api.client.get(f"/api/runs/{run_id}/discrepancies").json()["items"]
    return [(i["key"], i["severity"], i["item_state"],
             {r: (c.get("value"), c.get("state")) for r, c in i["slots"].items()}) for i in items]


def test_policy_check_hit_equals_live_read(api_any):
    a = api_any
    exp_pdf, ren_pdf = make_pdf(EXPIRING), make_pdf(RENEWAL)

    def check(exp_name, ren_name):
        exp = a.upload((exp_name, exp_pdf))["documents"][0]["id"]
        ren = a.upload((ren_name, ren_pdf))["documents"][0]["id"]
        return _run(a, [exp, ren], workflow="policy-check", slots={str(exp): "expiring", str(ren): "renewal"})

    first = check("exp.pdf", "ren.pdf")
    calls = _extract_calls(a)
    second = check("exp2.pdf", "ren2.pdf")
    assert _extract_calls(a) == calls
    assert all(d["reused_from_run_id"] == first["id"] for d in second["documents"])
    assert _policy_items(a, first["id"]) == _policy_items(a, second["id"])
    assert [f[:10] for f in _facts(a, first["id"])] == [f[:10] for f in _facts(a, second["id"])]


def test_only_done_documents_serve_hits(tmp_path, monkeypatch):
    monkeypatch.delenv(cache.ENV_SWITCH, raising=False)
    state = {"fail": True}
    smart = any_responder()

    def respond(req):
        if req.role is CallRole.EXTRACT and state["fail"]:
            return {"not": "a valid answer"}  # schema invalid twice: the document fails
        return smart(req)

    a = make_api(tmp_path, monkeypatch, respond)
    try:
        pdf = make_pdf(DOC_A)
        failed = _run(a, [a.upload(("a.pdf", pdf))["documents"][0]["id"]])
        assert failed["status"] == "failed" and failed["documents"][0]["status"] == "failed"
        state["fail"] = False
        before = _extract_calls(a)
        ok = _run(a, [a.upload(("a again.pdf", pdf))["documents"][0]["id"]])
        assert ok["status"] == "done" and _extract_calls(a) == before + 1  # read live, never copied
        assert ok["documents"][0]["reused_from_run_id"] is None
        with a.ctx.factory() as s:
            rds = s.scalars(select(models.RunDocument).order_by(models.RunDocument.id)).all()
            assert rds[0].extraction_key is None  # a failed read never gets a key, so it can never serve
    finally:
        a.client.__exit__(None, None, None)


def test_cache_off_reads_live(tmp_path, monkeypatch):
    monkeypatch.setenv(cache.ENV_SWITCH, "off")
    a = make_api(tmp_path, monkeypatch, any_responder())
    try:
        pdf = make_pdf(DOC_A)
        _run(a, [a.upload(("a.pdf", pdf))["documents"][0]["id"]])
        second = _run(a, [a.upload(("a2.pdf", pdf))["documents"][0]["id"]])
        assert _extract_calls(a) == 2 and second["documents"][0]["reused_from_run_id"] is None
    finally:
        a.client.__exit__(None, None, None)


def test_eval_batch_never_reuses(tmp_path, monkeypatch):
    monkeypatch.delenv(cache.ENV_SWITCH, raising=False)

    def boom(*_a, **_k):
        raise AssertionError("an eval batch looked up the extraction cache")

    monkeypatch.setattr(cache, "find_source", boom)
    src = FIXTURES / "carrier_a_clean.pdf"
    one, two = tmp_path / "one.pdf", tmp_path / "two.pdf"
    shutil.copyfile(src, one)
    shutil.copyfile(src, two)  # same bytes twice in one batch
    provider = FakeProvider(any_responder())
    out = eval_hook.run_eval_batch([str(one), str(two)], provider=provider)
    assert all(d["status"] == "ok" for d in out["documents"].values())
    assert sum(1 for r in provider.requests if r.role is CallRole.EXTRACT) == 2  # both read live


def test_dev_seed_runs_its_scenarios_with_the_cache_off(monkeypatch):
    monkeypatch.delenv(cache.ENV_SWITCH, raising=False)
    dev_seed.scenario_environment()
    import os
    assert os.environ[cache.ENV_SWITCH] == "off" and not cache.enabled_from_env()


# --------------------------------------------------------------------- T-R2 (D10)


@pytest.fixture
def pc(tmp_path, monkeypatch):
    a = make_api(tmp_path, monkeypatch, policy_responder())
    yield a
    a.client.__exit__(None, None, None)


def _policy_run(api) -> int:
    exp, ren = api.upload_pages(EXPIRING, "exp.pdf"), api.upload_pages(RENEWAL, "ren.pdf")
    run = _run(api, [exp, ren], workflow="policy-check", slots={str(exp): "expiring", str(ren): "renewal"})
    assert run["status"] == "done"
    return run["id"]


def test_policy_check_removed_is_422_and_changes_nothing(pc):
    run_id = _policy_run(pc)
    before = pc.client.get(f"/api/runs/{run_id}/discrepancies").json()
    item = before["items"][0]
    r = pc.client.patch(f"/api/runs/{run_id}/discrepancies/{item['id']}", json={"status": "removed"})
    assert r.status_code == 422, r.text
    after = pc.client.get(f"/api/runs/{run_id}/discrepancies").json()
    assert after["counts"] == before["counts"] and after["open_counts"] == before["open_counts"]
    assert after["version"] == before["version"]


def test_policy_check_resolve_without_note_or_fingerprint_still_works(pc):
    run_id = _policy_run(pc)
    listing = pc.client.get(f"/api/runs/{run_id}/discrepancies").json()
    item = listing["items"][0]
    assert "fingerprint" not in item  # the Policy Check item shape is pinned (test_tasks OLD_ITEM_KEYS)
    r = pc.client.patch(f"/api/runs/{run_id}/discrepancies/{item['id']}", json={"status": "resolved"})
    assert r.status_code == 200 and r.json()["status"] == "resolved" and r.json()["note"] is None
    out = pc.client.get(f"/api/runs/{run_id}/discrepancies").json()
    assert out["resolved"] == 1 and out["open_counts"][item["severity"]] == listing["open_counts"][item["severity"]] - 1


def test_policy_check_fingerprint_is_optional_but_checked_when_sent(pc):
    run_id = _policy_run(pc)
    item = pc.client.get(f"/api/runs/{run_id}/discrepancies").json()["items"][0]
    with pc.ctx.factory() as s:
        current = next(b for b in models.discrepancy_blocks(s.get(models.Run, run_id).artifact.latest_content())
                       if b.id == item["id"])
    ok = pc.client.patch(f"/api/runs/{run_id}/discrepancies/{item['id']}",
                         json={"note": "seen", "expected_fingerprint": models.discrepancy_fingerprint(current)})
    assert ok.status_code == 200
    stale = pc.client.patch(f"/api/runs/{run_id}/discrepancies/{item['id']}",
                            json={"status": "resolved", "expected_fingerprint": "0" * 64})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "item_changed"
    assert stale.json()["error"]["item"]["id"] == item["id"]


def test_policy_check_status_table_is_unchanged():
    assert models.DISCREPANCY_STATUSES == ("open", "resolved")
    assert "removed" in models.CLAIM_STATUSES


def test_discrepancy_state_test_file_is_unchanged_by_this_slice():
    """The D10 pin: the slice 3 test file keeps its exact content (checked against its git blob)."""
    import subprocess

    root = Path(__file__).resolve().parents[2]
    path = "backend/tests/test_discrepancy_state.py"
    diff = subprocess.run(["git", "diff", "--quiet", "cbc8ac1", "--", path], cwd=root, capture_output=True)
    if diff.returncode not in (0, 1):
        pytest.skip("git is not available here")
    assert diff.returncode == 0, "test_discrepancy_state.py changed; D10 says it stays as it is"


def test_seed_calls_the_scenario_environment_first():
    """journey.spec.ts:141 relies on seeded states; the seed's scenario runs read live (D9)."""
    src = Path(dev_seed.__file__).read_text(encoding="utf-8")
    assert "scenario_environment()" in src.split("def seed() -> None:", 1)[1].split("def ", 1)[0]
