"""dev_seed's scripted model must keep producing the states the e2e journey checks.

Seeded run 5 (partial) needs one cell in state CONFLICTING: the e2e test (frontend/e2e/journey.spec.ts,
"seeded states") clicks the "Conflicting values" filter and expects two chips. The scripted model gets
it from long_120, so the conflict must be one the merge rules keep (not boilerplate, not contained).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend import dev_seed
from backend.db import COMPARE_QUOTES
from backend.pipeline import extract, merge, parse, verify, windows
from backend.schemas import EvidenceState, FactField

FIELDS = [FactField.model_validate(f) for f in COMPARE_QUOTES["fact_schema"]]
LONG_120 = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "quotes" / "long_120.pdf"


@pytest.fixture(scope="module")
def long_120_merged() -> dict[str, merge.MergedFact]:
    pages = parse.parse_pdf(LONG_120.read_bytes()).pages
    cands = []
    for w in windows.plan_windows(len(pages)):
        msgs = extract.build_extraction_messages(FIELDS, [p for p in pages if p.page_no in w],
                                                 document_name="long_120.pdf")
        user = "\n".join(p.text for p in msgs[1].parts())
        facts, _, _ = extract.normalize_output(dev_seed.extract(user), FIELDS)  # one fact per field per window
        cands += [verify.CandidateFact(f.field, f.value, f.page if f.page in w else w.first, f.quote)
                  for f in facts if f.value is not None]
    checked = verify.verify_facts(cands, pages)
    text = {p.page_no: p.text for p in pages}
    return {m.field: m for m in merge.merge_document(FIELDS, checked, text)}


def test_seeded_long_120_has_a_conflict_the_merge_rules_keep(long_120_merged):
    conflicting = [m for m in long_120_merged.values() if m.state == EvidenceState.CONFLICTING]
    assert [m.field for m in conflicting] == ["each_occurrence_limit"]
    m = conflicting[0]
    assert all(c.state == EvidenceState.VERIFIED for c in m.conflicts)
    assert [(c.value, c.page_no) for c in m.conflicts] == [("$1,000,000", 3), ("$2,000,000", 60)]


def test_seeded_long_120_other_values_are_unaffected(long_120_merged):
    assert long_120_merged["retention"].value == "$10,000 per occurrence"
    assert long_120_merged["policy_form"].state == EvidenceState.VERIFIED
    assert long_120_merged["aggregate_limit"].value == "$2,000,000"


def test_seeded_policy_checks_are_personal_and_two_tasks_exist(tmp_path, monkeypatch):
    """Slice 3b spec 1.4: the Policy Check runs (7 and 8 in a full seed) are tagged personal, and the
    seed adds one free-standing overdue task and one task linked to a discrepancy of the first run."""
    import datetime as dt

    monkeypatch.setenv("BROKERDOCS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s = dev_seed.Seeder()
    try:
        compare = s.run(s.upload("carrier_a_clean.pdf"))
        first, second = dev_seed.seed_policy_checks(s)
        dev_seed.seed_tasks(s, first)
        runs = {r["id"]: r for r in s.client.get("/api/runs").json()["runs"]}
        assert runs[first]["line"] == runs[second]["line"] == "personal"
        assert runs[compare["id"]]["line"] == "commercial"
        personal = s.client.get("/api/runs", params={"line": "personal"}).json()["runs"]
        assert sorted(r["id"] for r in personal) == sorted([first, second])
        tasks = s.client.get("/api/tasks").json()["tasks"]
        assert len(tasks) == 2
        free = next(t for t in tasks if t["run_id"] is None)
        assert free["status"] == "open" and dt.date.fromisoformat(free["due_date"]) < dt.date.today()
        linked = next(t for t in tasks if t["run_id"] is not None)
        assert linked["run_id"] == first and linked["discrepancy"]["in_report"] is True
        assert linked["title"].startswith("Follow up: ") and len(linked["title"]) <= 200
        items = s.client.get(f"/api/runs/{first}/discrepancies").json()["items"]
        assert [i["task"]["id"] for i in items if i["task"]] == [linked["id"]]
    finally:
        s.close()


def test_seeded_comparisons_name_distinct_clients_and_keep_one_documents_fallback(tmp_path, monkeypatch):
    """Queue navigation (2026-11-03): the owner saw Cedar Hollow (every quote fixture's named insured) several
    times with nothing to tell the rows apart. Every typed name is a different client, only run 6 (the Cedar
    Hollow file) types that name, and a run without a typed name (run 5) still shows the documents' name with
    the "extracted" source, its carriers and its date, so the queue can tell it from run 6."""
    names = list(dev_seed.CLIENTS.values())
    assert len(set(names)) == len(names)
    assert [k for k, v in dev_seed.CLIENTS.items() if "Cedar Hollow" in v] == ["main"]
    assert "budget" in dev_seed.CLIENTS  # run 3 no longer falls back to Cedar Hollow

    monkeypatch.setenv("BROKERDOCS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s = dev_seed.Seeder()
    try:
        typed = s.run(s.upload("carrier_a_clean.pdf"), client=dev_seed.CLIENTS["budget"])
        fallback = s.run(s.upload("carrier_c_clean.pdf"))
        items = {i["run_id"]: i for g in ("ready", "needs_attention")
                 for i in s.client.get("/api/queue", params={"line": "commercial"}).json()[g]}
        assert items[typed["id"]]["client"] == dev_seed.CLIENTS["budget"]
        assert items[typed["id"]]["client_source"] == "broker"
        fb = items[fallback["id"]]
        assert fb["client"] == "Cedar Hollow Hospitality LLC" and fb["client_source"] == "extracted"
        assert fb["carriers"] and fb["created_at"]
        run = s.client.get(f"/api/runs/{fallback['id']}").json()
        assert (run["client"], run["client_source"]) == ("Cedar Hollow Hospitality LLC", "extracted")
    finally:
        s.close()


def test_seeded_queue_names_clients_and_links_a_failed_proposal(tmp_path, monkeypatch):
    """Work queue seed: runs 7/8 and two comparisons carry broker client names, one comparison is
    left unnamed, and a failed proposal built from a comparison puts it in Ready with
    "Proposal didn't finish" (D3). Proposal runs never show in the queue."""
    monkeypatch.setenv("BROKERDOCS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s = dev_seed.Seeder()
    try:
        unnamed = s.run(s.upload("corrupt_truncated.pdf", "corrupt_garbage.pdf"))  # like run 1
        named = s.run(s.upload("carrier_a_clean.pdf", "carrier_d_clean.pdf"), client=dev_seed.CLIENTS["fallback"])
        first, second = dev_seed.seed_policy_checks(s)
        proposal = dev_seed.seed_failed_proposal(s, named["id"])
        runs = {r["id"]: r for r in s.client.get("/api/runs").json()["runs"]}
        assert runs[first]["client_name"] == dev_seed.CLIENTS["policy_check"]
        assert runs[second]["client_name"] == dev_seed.CLIENTS["policy_check_quote_unread"]
        assert runs[proposal]["status"] == "failed" and runs[proposal]["parameters"]["source_run_id"] == named["id"]
        assert runs[named["id"]]["proposal_run_id"] == proposal

        commercial = s.client.get("/api/queue", params={"line": "commercial"}).json()
        ready = {i["run_id"]: i for i in commercial["ready"]}
        assert ready[named["id"]]["client"] == dev_seed.CLIENTS["fallback"]
        assert ready[named["id"]]["reasons"] == ["Proposal didn't finish"]
        attention = {i["run_id"]: i for i in commercial["needs_attention"]}
        assert attention[unnamed["id"]]["client"] is None and attention[unnamed["id"]]["client_source"] is None
        every = [i["run_id"] for g in ("in_progress", "needs_review", "ready", "needs_attention", "done_recently")
                 for i in commercial[g]]
        assert proposal not in every

        personal = s.client.get("/api/queue", params={"line": "personal"}).json()
        assert [i["run_id"] for i in personal["needs_review"]] == [first]
        assert [i["run_id"] for i in personal["needs_attention"]] == [second]
        assert personal["needs_review"][0]["client_source"] == "broker"
    finally:
        s.close()


def test_seeded_certificates_show_every_claim_state_and_a_past_holder(tmp_path, monkeypatch):
    """Slice 4 (C11 demo data): run 10 is issued for a past holder, run 11 is a draft for the same client with
    Supported, Not supported, one Overridden claim and the description sign-off, read with the cache off (D9)."""
    from backend.pipeline import cache

    monkeypatch.setenv("BROKERDOCS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv(cache.ENV_SWITCH, raising=False)
    dev_seed.scenario_environment()
    s = dev_seed.Seeder()
    try:
        issued, draft = dev_seed.seed_certificates(s)
        done = s.client.get(f"/api/certificates/{issued}").json()
        assert done["issue"] and done["issue"]["version"] == done["version"]
        view = s.client.get(f"/api/certificates/{draft}").json()
        assert view["run"]["status"] == "done" and view["client"] == dev_seed.CERT_CLIENT
        assert all(d["reused_from_run_id"] is None for d in view["run"]["documents"])  # D9: read live
        verdicts = {c["key"]: c["verdict"] for c in view["claims"]}
        assert verdicts == {
            "in_force:A": "supported", "in_force:B": "supported", "in_force:C": "supported", "insured": "supported",
            "additional_insured:A": "supported", "primary_noncontributory:A": "supported",
            "waiver_of_subrogation:C": "supported",
            "additional_insured:B": "not_supported", "waiver_of_subrogation:B": "not_supported",
            "primary_noncontributory:B": "not_supported", "waiver_of_subrogation:A": "not_supported",
            "description": "needs_signoff"}
        override = next(c for c in view["claims"] if c["key"] == "waiver_of_subrogation:A")
        assert override["status"] == "resolved" and override["note"] == dev_seed.CERT_OVERRIDE_NOTE
        assert view["counts"]["overridden"] == 1 and view["counts"]["open"] == 4
        assert not view["can_issue"] and "claims_open" in view["blocked"]
        holders = s.client.get("/api/certificates/holders", params={"client": dev_seed.CERT_CLIENT}).json()["holders"]
        assert [h["name"] for h in holders] == [dev_seed.CERT_PAST_HOLDER[0]]
        q = s.client.get("/api/certificates/queue").json()
        assert [i["run_id"] for i in q["needs_attention"]] == [draft]
        assert [i["run_id"] for i in q["done_recently"]] == [issued]
    finally:
        s.close()


def test_cert_truth_ignores_a_tag_suffix_so_e2e_can_upload_fresh_copies():
    """certificates.spec.ts uploads cert_written_contract_gl--e2e<ms>.pdf (own name and bytes, so every run makes the
    first read itself); the scripted reply must still be that fixture's truth."""
    plain = dev_seed._cert_truth("cert_written_contract_gl.pdf")
    assert plain is not None
    assert dev_seed._cert_truth("cert_written_contract_gl--e2e1793760357.pdf") == plain
    assert dev_seed._cert_truth("cert_no_such_fixture--e2e1.pdf") is None
    assert dev_seed._cert_truth("quote--e2e1.pdf") is None
