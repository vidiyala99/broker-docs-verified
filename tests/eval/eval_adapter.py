"""Bridge between the live eval and the backend pipeline.

The eval does not import backend internals directly. It loads one callable,
named by ``BROKERDOCS_EVAL_ADAPTER`` (``"module:function"``), default
``backend.eval_hook:run_eval_batch``. The backend lane provides it.

Contract::

    run_eval_batch(paths: list[str]) -> {
        "input_tokens": int,            # every model call in this run (M4)
        "documents": {
            "<path as given>": {
                "status": "ok" | "failed",
                "failure_reason": None | "corrupt" | "not_pdf"
                                  | "password_protected" | "over_cap" | ...,
                "option_count": int | None,      # D8
                "ocr_pages": list[int] | None,   # 1-based, M3
                "facts": [
                    {"field": str, "value": str | None, "page": int | None,
                     "quote": str | None,
                     "state": "verified" | "scan_read" | "not_found" | "failed"},
                ],
            },
        },
    }

One call is one Compare Quotes run (at most 8 documents, D3). Field names
are the keys of ``quote_generator.FIELDS``, a subset of the backend's canonical
FactField keys (carrier_name, address, policy_term, total_premium, retention,
quote_expiration, ...), so no key mapping is needed between backend and eval.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Callable

DEFAULT_ADAPTER = "backend.eval_hook:run_eval_batch"
REPO_ROOT = Path(__file__).resolve().parents[2]


class AdapterUnavailable(RuntimeError):
    pass


def load_adapter(spec: str | None = None) -> Callable[[list[str]], dict]:
    spec = spec or os.environ.get("BROKERDOCS_EVAL_ADAPTER") or DEFAULT_ADAPTER
    module_name, _, func_name = spec.partition(":")
    if not func_name:
        raise AdapterUnavailable(f"adapter spec {spec!r} must look like 'module:function'")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise AdapterUnavailable(f"cannot import {module_name!r}: {exc}") from exc
    fn = getattr(module, func_name, None)
    if not callable(fn):
        raise AdapterUnavailable(f"{spec!r} is not a callable")
    return fn


# Runs used by the live eval: each is one Compare Quotes run (<= 8 documents).
# over_cap_pages (81 pages) is obsolete: it is under the 300-page cap. The
# over-cap case is long_301 (generate with --include-oversize).
LIVE_RUNS: dict[str, list[str]] = {
    "clean": ["carrier_a_clean", "carrier_b_clean", "carrier_c_clean", "carrier_d_clean"],
    "multi_column": ["carrier_a_multicol", "carrier_b_multicol", "carrier_c_multicol",
                     "carrier_d_multicol"],
    "scanned": ["carrier_a_scanned", "carrier_b_scanned", "carrier_c_scanned",
                "carrier_d_scanned"],
    "adversarial": ["multi_option", "conflicting_endorsement", "injection"],
    "long": ["long_120", "long_300"],
    "parse": ["parse_sparse", "parse_mixed", "password_protected", "corrupt_garbage",
              "corrupt_truncated", "not_a_pdf"],
}


def run_suite(adapter: Callable[[list[str]], dict], fixture_dir: Path, truths: dict,
              runs: dict[str, list[str]] | None = None):
    """Call ``adapter`` once per run, score every document, apply the gates.

    Returns ``(metrics, gate_report)``. A document missing from the adapter's
    output is scored as failed.
    """
    import eval_gates as g

    scores = []
    run_tokens = []
    for ids in (runs or LIVE_RUNS).values():
        paths = [str(Path(fixture_dir) / truths[i]["file"]) for i in ids]
        out = adapter(paths)
        run_tokens.append(int(out.get("input_tokens", 0)))
        docs = out.get("documents", {})
        for fid, path in zip(ids, paths):
            raw = docs.get(path) or {"status": "failed",
                                     "failure_reason": "missing_from_adapter_output"}
            result = g.DocResult.from_dict({**raw, "fixture_id": fid})
            scores.append(g.score_document(truths[fid], result))
    metrics = g.aggregate(scores, run_tokens)
    # The full suite must have data in every gated bucket (clean, multi-column, long).
    required = tuple(g.GATED_BUCKETS) if runs is None else ("clean", "multi_column")
    return metrics, g.evaluate_gates(metrics, require_buckets=required)
