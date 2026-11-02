"""Offline tests for the live-eval plumbing, using fake adapters (no model)."""

from __future__ import annotations

import sys
import types

import pytest

import eval_gates as g
from eval_adapter import LIVE_RUNS, AdapterUnavailable, load_adapter, run_suite, selected_groups


def _oracle_adapter(truths, by_file, tokens=50_000, mutate=None):
    def adapter(paths):
        docs = {}
        for p in paths:
            t = truths[by_file[p.replace("\\", "/").rsplit("/", 1)[-1]]]
            r = g.oracle_result(t)
            if mutate:
                mutate(t, r)
            docs[p] = {"status": r.status, "failure_reason": r.failure_reason,
                       "option_count": r.option_count, "ocr_pages": r.ocr_pages,
                       "facts": [vars(f) for f in r.facts]}
        return {"input_tokens": tokens, "documents": docs}
    return adapter


@pytest.fixture
def by_file(truths):
    return {t["file"]: fid for fid, t in truths.items()}


# Fixtures deliberately not in LIVE_RUNS. over_cap_pages is 81 pages, under the
# 300-page cap, so it no longer exercises the cap; long_301 (generated with
# --include-oversize, not committed) is the over-cap case now.
OBSOLETE_FIXTURES = {"over_cap_pages": "obsolete: over_cap_pages"}


def test_live_runs_cover_every_fixture_within_doc_cap(truths):
    ids = [i for run in LIVE_RUNS.values() for i in run]
    assert "over_cap_pages" not in ids
    assert sorted(ids + list(OBSOLETE_FIXTURES)) == sorted(truths)
    assert all(len(run) <= 8 for run in LIVE_RUNS.values())  # D3: 8 documents per run


def test_run_suite_with_oracle_passes(truths, fixture_dir, by_file):
    m, rep = run_suite(_oracle_adapter(truths, by_file), fixture_dir, truths)
    assert rep.passed, g.format_report(m, rep)
    assert m.max_run_input_tokens == 50_000


def test_run_suite_over_budget_fails(truths, fixture_dir, by_file):
    m, rep = run_suite(_oracle_adapter(truths, by_file, tokens=1_600_000), fixture_dir, truths)
    assert not rep.passed
    assert [r.name for r in rep.failures] == ["max_run_input_tokens"]


def test_run_suite_injection_success_fails(truths, fixture_dir, by_file):
    def obey_injection(t, r):
        if t["fixture_id"] == "injection":
            for f in r.facts:
                if f.field == "total_premium":
                    f.value = "$1.00"

    m, rep = run_suite(_oracle_adapter(truths, by_file, mutate=obey_injection), fixture_dir, truths)
    assert not rep.passed and m.canary_hits == 1 and m.hard_fails == 1


def test_missing_document_is_scored_failed(truths, fixture_dir):
    m, rep = run_suite(lambda paths: {"input_tokens": 1, "documents": {}}, fixture_dir, truths,
                       runs={"clean": LIVE_RUNS["clean"], "multi_column": LIVE_RUNS["multi_column"]})
    assert m.buckets["clean"].field_accuracy == 0.0
    assert not rep.passed


def test_load_adapter_errors_and_success(monkeypatch):
    with pytest.raises(AdapterUnavailable):
        load_adapter("no_colon_here")
    with pytest.raises(AdapterUnavailable):
        load_adapter("definitely_missing_module_xyz:run")
    mod = types.ModuleType("fake_eval_hook")
    mod.run = lambda paths: {"input_tokens": 0, "documents": {}}
    mod.not_callable = 3
    monkeypatch.setitem(sys.modules, "fake_eval_hook", mod)
    assert load_adapter("fake_eval_hook:run") is mod.run
    with pytest.raises(AdapterUnavailable):
        load_adapter("fake_eval_hook:not_callable")
    monkeypatch.setenv("BROKERDOCS_EVAL_ADAPTER", "fake_eval_hook:run")
    assert load_adapter() is mod.run


# --- BROKERDOCS_EVAL_GROUPS subset runs -------------------------------------


def _recording_adapter(inner):
    seen = []

    def adapter(paths):
        seen.append(list(paths))
        return inner(paths)
    adapter.seen = seen
    return adapter


def test_groups_env_runs_only_selected_group(truths, fixture_dir, by_file, monkeypatch):
    monkeypatch.setenv("BROKERDOCS_EVAL_GROUPS", "long")
    ad = _recording_adapter(_oracle_adapter(truths, by_file))
    m, rep = run_suite(ad, fixture_dir, truths)
    assert len(ad.seen) == 1 and len(ad.seen[0]) == len(LIVE_RUNS["long"])
    assert set(m.buckets) == {"long"}
    assert m.subset_groups == ["long"]
    assert rep.passed, g.format_report(m, rep)  # clean / multi_column not required
    assert "subset run" in g.format_report(m, rep)
    assert g.report_dict(m, rep)["metrics"]["subset_groups"] == ["long"]


def test_groups_subset_still_gates_buckets_that_ran(truths, fixture_dir, by_file, monkeypatch):
    monkeypatch.setenv("BROKERDOCS_EVAL_GROUPS", "clean, scanned")

    def drop_values(t, r):
        for f in r.facts:
            f.value = None
    m, rep = run_suite(_oracle_adapter(truths, by_file, mutate=drop_values), fixture_dir, truths)
    assert not rep.passed
    assert "field_accuracy[clean]" in [r.name for r in rep.failures]
    assert "field_accuracy[long]" not in [r.name for r in rep.failures]


def test_groups_unset_is_unchanged(truths, fixture_dir, by_file, monkeypatch):
    monkeypatch.delenv("BROKERDOCS_EVAL_GROUPS", raising=False)
    ad = _recording_adapter(_oracle_adapter(truths, by_file))
    m, rep = run_suite(ad, fixture_dir, truths)
    assert len(ad.seen) == len(LIVE_RUNS)
    assert m.subset_groups is None and "subset run" not in g.format_report(m, rep)
    monkeypatch.setenv("BROKERDOCS_EVAL_GROUPS", " , ")
    assert selected_groups() is None


def test_groups_unknown_name_raises(monkeypatch):
    monkeypatch.setenv("BROKERDOCS_EVAL_GROUPS", "nope")
    with pytest.raises(ValueError):
        selected_groups()


# --- per-field misses -----------------------------------------------------


def test_misses_recorded_with_reason_buckets():
    fx = {"value": "$10,000", "page": 1, "quote": "q"}
    truth = {"fixture_id": "x", "gate_bucket": "clean",
             "facts": {"a": dict(fx), "b": dict(fx), "c": dict(fx), "d": dict(fx),
                       "e": dict(fx), "f": dict(fx), "g": None}}
    facts = [
        g.PredictedFact("a", "$10,000", 1, "q", "verified"),          # correct
        g.PredictedFact("b", "$99", 1, "q", "verified"),              # wrong_value (also hard fail)
        g.PredictedFact("c", "$10,000", 1, "q", "not_found"),         # rejected
        g.PredictedFact("d", "$77", 1, "q", "scan_read"),             # scan_read
        g.PredictedFact("e", None, None, None, "not_found"),          # missing
        # f: no fact at all -> missing
        g.PredictedFact("g", "$5", 1, "q", "verified"),               # absent violation, not a miss
    ]
    sc = g.score_document(truth, g.DocResult("x", facts))
    m = g.aggregate([sc])
    got = {d["field"]: d for d in m.misses}
    assert {k: v["reason"] for k, v in got.items()} == {
        "b": "wrong_value", "c": "rejected", "d": "scan_read", "e": "missing", "f": "missing"}
    assert got["b"]["fixture_id"] == "x" and got["b"]["truth"] == "$10,000"
    assert got["b"]["predicted"] == "$99" and got["b"]["state"] == "verified"
    assert got["f"]["predicted"] is None and got["f"]["state"] is None
    rep = g.evaluate_gates(m)
    text = g.format_report(m, rep)
    assert text.count("MISS x.") == 5 and "MISS x.b: wrong_value" in text
    assert [d["field"] for d in g.report_dict(m, rep)["metrics"]["misses"]] == list(got)


def test_oracle_has_no_misses(truths):
    # Fixtures expected to fail parsing have no facts to extract, so they are excluded.
    sc = [g.score_document(t, g.oracle_result(t)) for t in truths.values()
          if t.get("expect", {}).get("parse", "ok") != "failed"]
    assert g.aggregate(sc).misses == []
