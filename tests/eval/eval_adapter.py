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

Proposal Builder (slice 2, T1). The same callable runs one proposal when called
with keywords::

    run_eval_batch(paths, workflow="proposal-builder",
                   parameters={"broker_name", "brokerage_name",
                               "additional_context", "no_ai_prose"},
                   template="standard")  # template slug; the hook adds
                                         # template_id and outline (spec 2)
    -> the dict above plus
       "proposal": {
           "workflow": str, "run_status": str, "warnings": [str],
           "cells": [{"block_id", "row", "column", "document": path | None,
                      "value", "state", "conflicts": [str],
                      "citations": [{"document": path, "page": int}]}],
           "prose": [{"section_id", "block_id", "text",
                      "citations": [{"document": path, "page": int}]}],
       }

Called without ``workflow`` it is exactly the Compare Quotes run above. When
the backend has no ``proposal-builder`` workflow the call raises
NotImplementedError (the live test skips with its message).
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


GROUPS_ENV = "BROKERDOCS_EVAL_GROUPS"


def selected_groups(env: dict | None = None) -> list[str] | None:
    """Group names from ``BROKERDOCS_EVAL_GROUPS`` (comma-separated), or None
    when unset or blank. Unknown names raise ValueError."""
    raw = (os.environ if env is None else env).get(GROUPS_ENV, "")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        return None
    unknown = [n for n in names if n not in LIVE_RUNS]
    if unknown:
        raise ValueError(f"{GROUPS_ENV}: unknown group(s) {unknown}; known: {list(LIVE_RUNS)}")
    return list(dict.fromkeys(names))


def run_suite(adapter: Callable[[list[str]], dict], fixture_dir: Path, truths: dict,
              runs: dict[str, list[str]] | None = None, groups: list[str] | None = None):
    """Call ``adapter`` once per run, score every document, apply the gates.

    Returns ``(metrics, gate_report)``. A document missing from the adapter's
    output is scored as failed.
    """
    import eval_gates as g

    if groups is None and runs is None:
        groups = selected_groups()
    if groups is not None and runs is None:
        runs = {n: LIVE_RUNS[n] for n in groups}
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
    if groups is not None:
        # Subset run: only gated buckets that actually ran are evaluated.
        metrics.subset_groups = list(groups)
        required = tuple(b for b in g.GATED_BUCKETS if b in metrics.buckets)
    else:
        required = tuple(g.GATED_BUCKETS) if runs is None else ("clean", "multi_column")
    return metrics, g.evaluate_gates(metrics, require_buckets=required)


# ---------------------------------------------------------------------------
# Proposal Builder runs (slice 2, T1)
# ---------------------------------------------------------------------------

PROPOSALS_ENV = "BROKERDOCS_EVAL_PROPOSALS"


def selected_proposals(known: list[str], env: dict | None = None) -> list[str]:
    """Proposal ids from ``BROKERDOCS_EVAL_PROPOSALS`` (comma-separated), else all.
    Unknown names raise ValueError."""
    raw = (os.environ if env is None else env).get(PROPOSALS_ENV, "")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        return list(known)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(f"{PROPOSALS_ENV}: unknown proposal(s) {unknown}; known: {known}")
    return list(dict.fromkeys(names))


def run_proposal_suite(adapter: Callable[..., dict], proposal_dir: Path, proposals: dict,
                       doc_truths: dict, ids: list[str] | None = None):
    """Call ``adapter`` once per proposal fixture (workflow selected by the
    fixture), score cells and prose, apply the proposal gate.

    Returns ``(metrics, gate_report)``. NotImplementedError from the adapter
    (workflow not in the backend yet) propagates to the caller."""
    import eval_gates as g

    ids = ids if ids is not None else selected_proposals(sorted(proposals))
    scores = []
    for pid in ids:
        p = proposals[pid]
        paths = {fid: str(Path(proposal_dir) / doc_truths[fid]["file"]) for fid in p["documents"]}
        out = adapter([paths[f] for f in p["documents"]], workflow=p["workflow"],
                      parameters=dict(p["parameters"]), template=p.get("template"))
        scores.append(g.score_proposal(p, doc_truths, out, paths))
    metrics = g.aggregate_proposals(scores)
    return metrics, g.evaluate_proposal_gates(metrics)


# ---------------------------------------------------------------------------
# Policy Check runs (slice 3, T7)
# ---------------------------------------------------------------------------
#
# One case is one Policy Check run. The adapter is called with
#
#     run_eval_batch(paths, workflow="policy-check", slots={path: role})
#
# where role is expiring | renewal | quote | ams (an AMS path is the JSON or CSV export
# itself; the hook renders it to a PDF as the upload route does, D4). It returns the dict
# above plus "policy_check" (shape in eval_gates, Policy Check section).

POLICY_CASES_ENV = "BROKERDOCS_EVAL_POLICY_CASES"


def selected_policy_cases(known: list[str], env: dict | None = None) -> list[str]:
    """Case ids from ``BROKERDOCS_EVAL_POLICY_CASES`` (comma-separated), else all.
    Unknown names raise ValueError."""
    raw = (os.environ if env is None else env).get(POLICY_CASES_ENV, "")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        return list(known)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(f"{POLICY_CASES_ENV}: unknown case(s) {unknown}; known: {known}")
    return list(dict.fromkeys(names))


def run_policy_suite(adapter: Callable[..., dict], policy_dir: Path, cases: dict, doc_truths: dict,
                     ids: list[str] | None = None):
    """Call ``adapter`` once per policy case, score its discrepancy items, apply the D16 gate.

    Returns ``(metrics, gate_report)``. NotImplementedError from the adapter propagates."""
    import eval_gates as g

    ids = ids if ids is not None else selected_policy_cases(sorted(cases))
    scores = []
    for cid in ids:
        case = cases[cid]
        paths = {fid: str(Path(policy_dir) / doc_truths[fid]["file"]) for fid in case["roles"].values()}
        slots = {paths[fid]: role for role, fid in case["roles"].items()}
        out = adapter(list(slots), workflow=case["workflow"], slots=slots)
        scores.append(g.score_policy_case(case, doc_truths, out, paths))
    metrics = g.aggregate_policy(scores)
    return metrics, g.evaluate_policy_gates(metrics)
