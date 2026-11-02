"""Offline tests for the long-document fixtures (120 and 300 pages) and the
'long' gate. No model calls."""

from __future__ import annotations

import re

import fitz
import pytest

import eval_gates as g
import quote_generator as qg
from eval_adapter import LIVE_RUNS

LONG_IDS = ["long_120", "long_300"]
MAX_BYTES = int(1.5 * 1024 * 1024)


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _page_texts(fixture_dir, truth) -> list[str]:
    doc = fitz.open(fixture_dir / truth["file"])
    return [_ws(p.get_text()) for p in doc]


@pytest.fixture(scope="module", params=LONG_IDS)
def long_fx(request, truths, fixture_dir):
    t = truths[request.param]
    return t, _page_texts(fixture_dir, t)


# ---------------------------------------------------------------------------
# The fixtures themselves
# ---------------------------------------------------------------------------

def test_page_counts_and_size(truths, fixture_dir):
    assert truths["long_120"]["page_count"] == 120
    assert truths["long_300"]["page_count"] == 300 == qg.LONG_CAP_PAGES
    for fid in LONG_IDS:
        pdf = fixture_dir / truths[fid]["file"]
        assert len(fitz.open(pdf)) == truths[fid]["page_count"]
        assert pdf.stat().st_size < MAX_BYTES, fid
        assert truths[fid]["gate_bucket"] == "long"


def test_fields_are_the_backend_schema_with_one_absent(long_fx):
    t, _ = long_fx
    assert set(t["facts"]) == set(qg.FIELDS)
    absent = [f for f, v in t["facts"].items() if v is None]
    assert len(absent) == 1


def test_absent_field_text_is_not_in_the_document(long_fx):
    t, texts = long_fx
    (absent,) = [f for f, v in t["facts"].items() if v is None]
    label = qg.LABELS[absent]
    assert not any(label in tx for tx in texts), absent


def test_every_fact_value_is_on_each_listed_page(long_fx):
    t, texts = long_fx
    for fname, f in t["facts"].items():
        if f is None or fname == "territory":
            continue
        assert f["pages"] and f["page"] == f["pages"][0], fname
        assert f["pages"] == [i for i, tx in enumerate(texts, 1) if _ws(f["value"]) in tx], fname


def test_facts_are_spread_across_the_document(long_fx):
    t, _ = long_fx
    n = t["page_count"]
    pages = {f["page"] for f in t["facts"].values() if f}
    assert min(pages) == 1 and max(pages) >= n - 5
    assert any(p > n * 0.25 for p in pages if p < n * 0.4)  # something mid-document
    assert len(pages) >= 7


def test_repeated_fact_appears_in_three_places_with_one_value(long_fx):
    t, texts = long_fx
    f = t["facts"][t["expect"]["repeated_field"]]
    assert len(f["pages"]) == 3
    assert f["pages"][0] <= 3 and f["pages"][-1] >= t["page_count"] - 6
    assert all(f["value"] in texts[p - 1] for p in f["pages"])


def test_conflicting_endorsement_deep_in_document(long_fx):
    t, texts = long_fx
    field = t["expect"]["conflict_field"]
    truth_fact = t["facts"][field]
    (decoy,) = [d for d in t["decoys"] if d["field"] == field]
    assert decoy["kind"] == "superseded" and decoy["value"] != truth_fact["value"]
    assert truth_fact["page"] == t["expect"]["conflict_amend_page"]
    assert truth_fact["page"] > t["page_count"] * 0.8
    assert max(decoy["pages"]) < truth_fact["page"]  # the original is declared early
    assert truth_fact["pages"] == [truth_fact["page"]]
    assert "Amendment of Deductible" in texts[truth_fact["page"] - 1]


def test_territory_straddles_the_page_14_15_16_window_edge(long_fx):
    t, texts = long_fx
    f = t["facts"][t["expect"]["straddle_field"]]
    assert f["pages"] == [14, 15, 16] == t["expect"]["straddle_pages"]
    assert t["expect"]["window_pages"] == 15
    assert f["page"] == 14
    # No single page holds the whole value; the pieces sit on 14, 15 and 16 in order.
    assert not any(_ws(f["value"]) in tx for tx in texts)
    words = _ws(f["value"]).split(" ")
    on_pages = []
    pos = 0
    for pno in (14, 15, 16):
        tx = texts[pno - 1]
        # Longest prefix of the remaining words that ends the page (14), fills it (15)
        # or starts it (16).
        remaining = words[pos:]
        k = 0
        while k < len(remaining) and " ".join(remaining[: k + 1]) in tx:
            k += 1
        assert k > 0, pno
        on_pages.append(k)
        pos += k
    assert pos == len(words), on_pages


def test_endorsements_and_schedules_come_late(long_fx):
    t, texts = long_fx
    n = t["page_count"]
    first_endorsement = next(i for i, tx in enumerate(texts, 1) if "Endorsement No. 1:" in tx)
    first_schedule = next(i for i, tx in enumerate(texts, 1) if "Schedule of Locations" in tx)
    assert first_endorsement > n * 0.6 and first_schedule > first_endorsement
    assert "Declarations" in texts[0]


def test_no_real_company_names_and_no_em_dashes(long_fx):
    _, texts = long_fx
    blob = " ".join(texts)
    assert "—" not in blob
    for real in ("Chubb", "Travelers", "Liberty Mutual", "AIG", "Hartford", "Zurich", "Nationwide",
                 "State Farm", "Allstate", "Berkshire", "Lloyd"):
        assert real not in blob


def test_default_seed_is_deterministic():
    a = qg.long_policy_fixture(qg.DEFAULT_SEED, qg.LONG_SPECS[0])
    b = qg.long_policy_fixture(qg.DEFAULT_SEED, qg.LONG_SPECS[0])
    assert qg.page_texts(a) == qg.page_texts(b)
    c = qg.long_policy_fixture(qg.DEFAULT_SEED + 1, qg.LONG_SPECS[0])
    assert qg.page_texts(a) != qg.page_texts(c)


def test_long_301_is_only_built_with_include_oversize():
    base = {f.fixture_id for f in qg.build_fixtures(qg.DEFAULT_SEED)}
    assert "long_301" not in base and {"long_120", "long_300"} <= base
    fx = next(f for f in qg.build_fixtures(qg.DEFAULT_SEED, include_oversize=True)
              if f.fixture_id == "long_301")
    assert len(fx.pages) == 301
    assert fx.expect == {"parse": "failed", "failure_reason": "over_cap", "cap": "pages",
                         "cap_value": 300}
    pdf = qg.render_pdf(fx, __import__("random").Random(1))
    assert len(fitz.open(stream=pdf, filetype="pdf")) == 301
    assert len(pdf) < MAX_BYTES


# ---------------------------------------------------------------------------
# Scorer and gate on the long fixtures
# ---------------------------------------------------------------------------

def test_live_run_group_for_long_documents():
    assert LIVE_RUNS["long"] == LONG_IDS
    assert len(LIVE_RUNS["long"]) <= 8


def test_oracle_passes_the_long_gate(truths):
    scores = [g.score_document(truths[i], g.oracle_result(truths[i])) for i in LONG_IDS]
    m = g.aggregate(scores, [1000])
    rep = g.evaluate_gates(m, require_buckets=["long"])
    assert m.buckets["long"].field_accuracy == 1.0 and m.buckets["long"].hard_fails == 0
    names = {r.name for r in rep.results if r.gated}
    assert {"field_accuracy[long]", "hard_fails[long]"} <= names
    long_results = [r for r in rep.results if r.name.endswith("[long]")]
    assert all(r.passed for r in long_results)


def _corrupt(truth, fields, state="verified"):
    res = g.oracle_result(truth)
    for f in res.facts:
        if f.field in fields and f.value is not None:
            f.value, f.state = "$99,999,999", state
    return res


def test_corrupted_result_fails_the_long_gate(truths):
    t = truths["long_120"]
    res = _corrupt(t, {"total_premium"})
    sc = g.score_document(t, res)
    assert sc.hard_fails == 1
    m = g.aggregate([sc, g.score_document(truths["long_300"], g.oracle_result(truths["long_300"]))])
    rep = g.evaluate_gates(m, require_buckets=["long"])
    assert "hard_fails[long]" in [r.name for r in rep.failures]


def test_picking_the_superseded_deductible_is_a_hard_fail(truths):
    for fid in LONG_IDS:
        t = truths[fid]
        decoy = t["decoys"][0]
        res = g.oracle_result(t)
        for f in res.facts:
            if f.field == "retention":
                f.value, f.page = decoy["value"], decoy["pages"][0]
        sc = g.score_document(t, res)
        assert sc.hard_fails == 1 and sc.decoy_hits == {"superseded": 1}, fid


def test_wrong_page_citation_on_straddled_fact_is_a_hard_fail(truths):
    t = truths["long_300"]
    res = g.oracle_result(t)
    for f in res.facts:
        if f.field == "territory":
            f.page = 40
    assert g.score_document(t, res).hard_fails == 1
    for ok in (14, 15, 16):  # any page of the straddle is a valid citation
        for f in res.facts:
            if f.field == "territory":
                f.page = ok
        assert g.score_document(t, res).hard_fails == 0


def test_accuracy_boundary_at_085():
    def doc(correct):
        return g.DocScore("d", "long", present=20, correct=correct)
    ok = g.evaluate_gates(g.aggregate([doc(17)]), require_buckets=["long"])
    bad = g.evaluate_gates(g.aggregate([doc(16)]), require_buckets=["long"])
    pick = lambda rep: [r for r in rep.results if r.name == "field_accuracy[long]"][0]  # noqa: E731
    assert pick(ok).passed and pick(ok).gated and pick(ok).threshold == ">= 0.85"
    assert not pick(bad).passed


def test_dropping_fields_in_long_docs(truths):
    """15 present fields per document: two misses stay at 0.867 (pass), three fall to 0.80."""
    def acc(drop):
        scores = []
        for fid in LONG_IDS:
            t = truths[fid]
            res = g.oracle_result(t)
            dropped = 0
            for f in res.facts:
                if dropped < drop and f.value is not None:
                    f.value, f.state = None, "not_found"
                    dropped += 1
            scores.append(g.score_document(t, res))
        return g.aggregate(scores).buckets["long"].field_accuracy
    assert acc(2) >= g.FIELD_ACCURACY_LONG_MIN > acc(3)


def test_gated_long_group_with_no_data_fails():
    m = g.aggregate([g.DocScore("a", "clean", present=10, correct=10),
                     g.DocScore("b", "multi_column", present=10, correct=10)])
    assert g.evaluate_gates(m).passed  # not required by default (existing behavior)
    rep = g.evaluate_gates(m, require_buckets=["clean", "multi_column", "long"])
    assert not rep.passed
    assert {r.name for r in rep.failures} == {"field_accuracy[long]", "hard_fails[long]"}
    assert all(r.note == "no data" for r in rep.failures)


def test_full_suite_requires_long_data(truths, fixture_dir):
    from eval_adapter import run_suite
    by_file = {t["file"]: fid for fid, t in truths.items()}

    def adapter(paths):
        docs = {}
        for p in paths:
            r = g.oracle_result(truths[by_file[p.replace("\\", "/").rsplit("/", 1)[-1]]])
            docs[p] = {"status": r.status, "failure_reason": r.failure_reason,
                       "option_count": r.option_count, "ocr_pages": r.ocr_pages,
                       "facts": [vars(f) for f in r.facts]}
        return {"input_tokens": 1, "documents": docs}

    runs = {k: v for k, v in LIVE_RUNS.items() if k != "long"}
    m, rep = run_suite(adapter, fixture_dir, truths, runs=runs)
    assert "long" not in m.buckets
    m2, rep2 = run_suite(adapter, fixture_dir, truths)
    assert rep2.passed and m2.buckets["long"].documents == 2
