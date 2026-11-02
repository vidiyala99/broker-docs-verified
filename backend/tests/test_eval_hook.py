"""eval_hook.run_eval_batch: the contract in tests/eval/eval_adapter.py, offline (FakeProvider)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from backend import eval_hook
from backend.pipeline.parse import DEFAULT_LIMITS
from backend.providers import FakeProvider
from pipeline_fakes import smart_responder

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "quotes"
NAMES = ["carrier_a_clean", "password_protected", "not_a_pdf", "corrupt_garbage", "over_cap_pages",
         "carrier_a_scanned"]


def _page_count(path: Path) -> int:
    import fitz

    with fitz.open(path) as doc:
        return doc.page_count


@pytest.fixture(scope="module")
def output():
    paths = [str(FIXTURES / f"{n}.pdf") for n in NAMES]
    fake = FakeProvider(smart_responder({1: "Named Insured: Cedar Hollow Hospitality LLC"}))
    return paths, eval_hook.run_eval_batch(paths, provider=fake)


def test_every_path_is_reported_with_the_contract_keys(output):
    paths, out = output
    assert isinstance(out["input_tokens"], int) and out["input_tokens"] > 0
    assert set(out["documents"]) == set(paths)
    for doc in out["documents"].values():
        assert set(doc) == {"status", "failure_reason", "option_count", "ocr_pages", "facts"}
        assert doc["status"] in ("ok", "failed")
        for f in doc["facts"]:
            assert set(f) == {"field", "value", "page", "quote", "state"}
            assert f["state"] in ("verified", "scan_read", "not_found", "failed")


def test_parse_failures_map_to_eval_reasons(output):
    paths, out = output
    docs = {Path(p).stem: out["documents"][p] for p in paths}
    assert docs["password_protected"]["failure_reason"] == "password_protected"
    assert docs["not_a_pdf"]["failure_reason"] == "not_pdf"
    assert docs["corrupt_garbage"]["failure_reason"] == "corrupt"
    for name in ("password_protected", "not_a_pdf", "corrupt_garbage"):
        assert docs[name]["status"] == "failed" and docs[name]["facts"] == []
    # T14 raised the page cap to 300; the fixture is over the cap only once the eval
    # lane regenerates it past 300 pages (it had 81 when the cap was 80).
    if _page_count(FIXTURES / "over_cap_pages.pdf") > DEFAULT_LIMITS.max_pages:
        assert docs["over_cap_pages"]["failure_reason"] == "over_cap"
        assert docs["over_cap_pages"]["status"] == "failed" and docs["over_cap_pages"]["facts"] == []
    else:
        assert docs["over_cap_pages"]["failure_reason"] != "over_cap"


def test_ok_documents_carry_verified_facts_ocr_pages_and_option_count(output):
    paths, out = output
    docs = {Path(p).stem: out["documents"][p] for p in paths}
    clean = docs["carrier_a_clean"]
    assert clean["status"] == "ok" and clean["failure_reason"] is None
    assert clean["option_count"] == 1 and clean["ocr_pages"] == []
    by = {f["field"]: f for f in clean["facts"]}
    assert by["named_insured"] == {"field": "named_insured", "value": "Cedar Hollow Hospitality LLC",
                                   "page": 1, "quote": "Named Insured: Cedar Hollow Hospitality LLC",
                                   "state": "verified"}
    # absent fields: not_found with no value (eval_gates' "absent")
    absent = [f for f in clean["facts"] if f["value"] is None]
    assert absent and all(f["state"] == "not_found" for f in absent)
    scanned = docs["carrier_a_scanned"]
    assert scanned["status"] == "ok" and scanned["ocr_pages"] == [1, 2, 3]
    assert {f["state"] for f in scanned["facts"] if f["value"]} == {"scan_read"}


def test_output_parses_with_the_eval_scorer(output):
    paths, out = output
    sys.path.insert(0, str(ROOT / "tests" / "eval"))
    try:
        gates = importlib.import_module("eval_gates")
    finally:
        sys.path.remove(str(ROOT / "tests" / "eval"))
    for p, raw in out["documents"].items():
        result = gates.DocResult.from_dict({**raw, "fixture_id": Path(p).stem})
        assert result.status in ("ok", "failed")


def test_conflicting_fact_is_reported_not_found_with_every_candidate_value():
    from backend import models
    from backend.schemas import EvidenceState

    f = models.Fact(field="retention", value=None, state=EvidenceState.CONFLICTING, page_no=None, cited_page=None,
                    quote=None, conflicts=[{"value": "$5,000", "page_no": 20, "passage_id": 3},
                                           {"value": "$7,500", "page_no": 90, "passage_id": 8}])
    out = eval_hook._fact(f)
    assert out == {"field": "retention", "value": "Conflicting values: $5,000 (page 20); $7,500 (page 90)",
                   "page": None, "quote": None, "state": "not_found"}
    sys.path.insert(0, str(ROOT / "tests" / "eval"))
    try:
        gates = importlib.import_module("eval_gates")
    finally:
        sys.path.remove(str(ROOT / "tests" / "eval"))
    truth = {"fixture_id": "t", "facts": {"retention": {"value": "$5,000", "page": 20, "quote": "q"}}}
    score = gates.score_document(truth, gates.DocResult.from_dict({"fixture_id": "t", "facts": [out]}))
    assert score.hard_fails == 0 and score.displayed == 0 and score.rejected == 1


def test_more_than_eight_documents_is_rejected():
    with pytest.raises(ValueError):
        eval_hook.run_eval_batch([str(FIXTURES / "carrier_a_clean.pdf")] * 9, provider=FakeProvider([]))


def test_missing_file_is_reported_failed(tmp_path):
    missing = str(tmp_path / "nope.pdf")
    out = eval_hook.run_eval_batch([missing], provider=FakeProvider([]))
    assert out["documents"][missing]["status"] == "failed"
    assert out["documents"][missing]["failure_reason"] == "missing_file"
