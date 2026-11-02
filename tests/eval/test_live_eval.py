"""Live eval against the real model (R6). Runs only with ``pytest -m live``
and an ``OPENAI_API_KEY`` in the environment. Never reads ``.env``.

Writes a JSON report to ``$BROKERDOCS_EVAL_REPORT`` when set, otherwise to
pytest's temporary directory, and prints the gate table.

Set ``BROKERDOCS_EVAL_GROUPS`` (comma-separated LIVE_RUNS names, e.g. ``long``) to
run only those groups; gates for buckets that did not run are then skipped and
the report is labeled a subset run. Unset runs the full suite.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import eval_gates as g
from eval_adapter import AdapterUnavailable, load_adapter, run_suite

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
