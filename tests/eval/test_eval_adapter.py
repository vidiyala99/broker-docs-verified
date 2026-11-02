"""Offline tests for the live-eval plumbing, using fake adapters (no model)."""

from __future__ import annotations

import sys
import types

import pytest

import eval_gates as g
from eval_adapter import LIVE_RUNS, AdapterUnavailable, load_adapter, run_suite


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


def test_live_runs_cover_every_fixture_within_doc_cap(truths):
    ids = [i for run in LIVE_RUNS.values() for i in run]
    assert sorted(ids) == sorted(truths)
    assert all(len(run) <= 8 for run in LIVE_RUNS.values())  # D3: 8 documents per run


def test_run_suite_with_oracle_passes(truths, fixture_dir, by_file):
    m, rep = run_suite(_oracle_adapter(truths, by_file), fixture_dir, truths)
    assert rep.passed, g.format_report(m, rep)
    assert m.max_run_input_tokens == 50_000


def test_run_suite_over_budget_fails(truths, fixture_dir, by_file):
    m, rep = run_suite(_oracle_adapter(truths, by_file, tokens=700_000), fixture_dir, truths)
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
