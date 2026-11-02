"""Live eval against the real model (R6). Runs only with ``pytest -m live``
and an ``OPENAI_API_KEY`` in the environment. Never reads ``.env``.

Writes a JSON report to ``$BROKERDOCS_EVAL_REPORT`` when set, otherwise to
pytest's temporary directory, and prints the gate table.

Set ``BROKERDOCS_EVAL_GROUPS`` (comma-separated LIVE_RUNS names, e.g. ``long``) to
run only those groups; gates for buckets that did not run are then skipped and
the report is labeled a subset run. Unset runs the full suite.

The Proposal Builder gate is its own test; run only it with
``pytest -m live tests/eval/test_live_eval.py -k proposal``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import eval_gates as g
from eval_adapter import AdapterUnavailable, load_adapter, run_proposal_suite, run_suite

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def adapter():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    try:
        return load_adapter()
    except AdapterUnavailable as exc:
        pytest.skip(f"eval adapter unavailable: {exc}")


def test_live_eval_gates(adapter, fixture_dir: Path, truths: dict, tmp_path: Path, capsys):
    metrics, gates = run_suite(adapter, fixture_dir, truths)
    report = g.format_report(metrics, gates)
    target = Path(os.environ.get("BROKERDOCS_EVAL_REPORT") or tmp_path / "eval_report.json")
    target.write_text(json.dumps(g.report_dict(metrics, gates), indent=2, default=str),
                      encoding="utf-8")
    with capsys.disabled():
        print("\n" + report + f"\nreport: {target}")
    assert gates.passed, "R6 gates failed:\n" + report


def test_live_proposal_gates(adapter, proposal_dir: Path, proposals: dict, proposal_doc_truths: dict,
                             tmp_path: Path, capsys):
    """Proposal Builder gate (slice 2): one run per proposal fixture through the
    ``proposal-builder`` workflow. ``BROKERDOCS_EVAL_PROPOSALS`` selects a subset.
    The JSON report goes to ``$BROKERDOCS_EVAL_PROPOSAL_REPORT`` when set."""
    try:
        metrics, gates = run_proposal_suite(adapter, proposal_dir, proposals, proposal_doc_truths)
    except NotImplementedError as exc:
        pytest.skip(f"proposal eval not runnable yet: {exc}")
    report = g.format_proposal_report(metrics, gates)
    target = Path(os.environ.get("BROKERDOCS_EVAL_PROPOSAL_REPORT") or tmp_path / "proposal_report.json")
    target.write_text(json.dumps(g.proposal_report_dict(metrics, gates), indent=2, default=str),
                      encoding="utf-8")
    with capsys.disabled():
        print("\n" + report + f"\nreport: {target}")
    assert gates.passed, "proposal gate failed:\n" + report
