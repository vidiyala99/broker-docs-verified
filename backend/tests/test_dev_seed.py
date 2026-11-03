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
