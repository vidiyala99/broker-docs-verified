"""Compare Quotes terms and mark gate (slice 6, task T6; spec ledger R6, R7, R9/D10, R12/D13).

Pure functions: no file, network, model or backend access. The live eval and the offline tests feed hand-written
truth and the adapter's output into them.

What is scored
--------------
One case is one Compare Quotes run (``case.documents`` in column order). The adapter output carries the slice 1
``documents`` facts (scored by ``eval_gates.score_document``, the R6 facts gate) and a ``compare`` block: the cells
of the run's artifact response as the browser receives them (``backend.eval_hook._compare_block``)::

    {"run_status": str, "warnings": [str],
     "columns": [{"key", "label", "document": path, "document_kind"}],
     "cells": [{"row", "column", "document": path, "value", "state", "edited", "complete",
                "mark": "worse" | "best" | None, "mark_reason": str | None,
                "citations": [{"document": path, "page": int}]}],
     "key_difference": {"source", "text", "stale"} | None}

Terms (R6): the rows additional_insured, waiver_of_subrogation, primary_noncontributory and notable_exclusions of
every document whose truth expects it to parse, scored exact-match against ``truth["compare_terms"][row]["value"]``
(notable_exclusions as a set of watch-list labels). A cell *shows* a category when its state is verified or
scan_read and its value is not "Not stated". Truth "Not stated" is the silent case (like a null fact): it is out of
the accuracy denominator and nothing may be shown there.

Marks (R7, R12/D13): expected marks are hand-written per run in the truth (``expected_marks``: document, row, kind,
reason), never computed by compare_rules. Actual marks are the ``mark``/``mark_reason`` of the response cells.

Hard fails (gated at 0)
  * ``missing_mark`` / ``extra_mark`` / ``wrong_mark``: an expected mark the response lacks, a mark the truth does
    not expect, or a different kind (worse vs best) (R7: any missing or extra worse/best mark);
  * ``wrong_mark_reason``: right kind, different reason (the reason is the tooltip a broker reads);
  * ``verified_term_differs``: a verified cell shows a category other than the truth (a silent document shown as
    "Not included" is this kind, the R6 error);
  * ``cited_wrong_page`` / ``cited_other_document``: a verified, correct term cell cites a page the truth does not
    list, or another document (notable_exclusions only when it names watch-list exclusions: "None of these" has no
    single location);
  * ``term_row_missing``: the response has no cell for a scored row of a parsed document;
  * ``no_compare_response``: the output has no compare block or no cells.
scan_read cells are labeled unverified in the UI (D5): a wrong scan_read category is a miss, not a hard fail.

Reported (not gated): term accuracy, misses with reasons, ``complete`` disagreements on notable_exclusions, the Key
difference source per case.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

TERM_ROWS = ("additional_insured", "waiver_of_subrogation", "primary_noncontributory", "notable_exclusions")
EXCLUSIONS_ROW = "notable_exclusions"
MARKED_ROWS = (*TERM_ROWS, "total_premium")
MARK_KINDS = ("worse", "best")
NOT_STATED = "Not stated"
NONE_OF_THESE = "None of these"
SHOWN_STATES = ("verified", "scan_read")
HARD_FAIL_KINDS = ("missing_mark", "extra_mark", "wrong_mark", "wrong_mark_reason", "verified_term_differs",
                   "cited_wrong_page", "cited_other_document", "term_row_missing", "no_compare_response")
COMPARE_HARD_FAIL_MAX = 0


def _norm_path(p: str | None) -> str | None:
    return None if p is None else str(Path(p)).replace("\\", "/").lower()


def _clean(v: str | None) -> str:
    return " ".join((v or "").split())


def _labels(value: str | None) -> frozenset[str] | None:
    """The watch-list labels a notable_exclusions value names (empty for None of these), case-insensitive."""
    text = _clean(value)
    if not text or text.casefold() == NOT_STATED.casefold():
        return None
    if text.casefold() == NONE_OF_THESE.casefold():
        return frozenset()
    return frozenset(part.strip().casefold() for part in text.split(",") if part.strip())


def term_matches(row: str, shown: str | None, truth: Mapping) -> bool:
    accepted = [truth["value"], *(truth.get("accepted_values") or [])]
    if row == EXCLUSIONS_ROW:
        got = _labels(shown)
        return got is not None and any(got == _labels(a) for a in accepted)
    return any(_clean(shown).casefold() == _clean(a).casefold() for a in accepted)


@dataclass
class CompareScore:
    case_id: str
    term_present: int = 0
    term_correct: int = 0
    term_absent: int = 0
    marks_expected: int = 0
    marks_found: int = 0
    marks_right: int = 0
    hard_fails: list[dict] = field(default_factory=list)
    misses: list[dict] = field(default_factory=list)
    complete_mismatches: list[dict] = field(default_factory=list)
    key_difference: dict | None = None
    run_status: str | None = None

    def hard(self, kind: str, document: str | None, row: str | None, detail: str, **extra) -> None:
        assert kind in HARD_FAIL_KINDS, kind
        self.hard_fails.append({"case_id": self.case_id, "kind": kind, "document": document, "row": row,
                                "detail": detail, **extra})


def _shown(cell: Mapping) -> str | None:
    if cell.get("state") not in SHOWN_STATES and not cell.get("edited"):
        return None
    value = _clean(cell.get("value"))
    return None if not value or value.casefold() == NOT_STATED.casefold() else value


def _miss_reason(cell: Mapping | None) -> str:
    if cell is None:
        return "missing"
    state = cell.get("state")
    if state == "scan_read" and _shown(cell):
        return "scan_read"
    if state in ("not_found", "conflicting", "failed", "pending"):
        return {"not_found": "rejected"}.get(state, state)
    if _shown(cell):
        return "wrong_value"
    return "not_stated"


def score_compare_case(case: Mapping, doc_truths: Mapping[str, Mapping], output: Mapping,
                       paths: Mapping[str, str]) -> CompareScore:
    """Score one Compare run: the four term rows of every parsed document and the run's marks."""
    sc = CompareScore(case["case_id"])
    fid_of = {_norm_path(p): fid for fid, p in paths.items()}
    block = output.get("compare") if isinstance(output, Mapping) else None
    if not block or not block.get("cells"):
        sc.hard("no_compare_response", None, None,
                "the output has no compare block with cells (backend.eval_hook must return the artifact response)")
        sc.marks_expected = len(case.get("expected_marks") or [])
        return sc
    sc.run_status = block.get("run_status")
    sc.key_difference = block.get("key_difference")
    cells: dict[tuple[str, str], Mapping] = {}
    for c in block["cells"]:
        fid = fid_of.get(_norm_path(c.get("document")))
        if fid is not None:
            cells.setdefault((fid, c["row"]), c)

    # Terms (R6)
    for fid in case["documents"]:
        truth = doc_truths[fid]
        if (truth.get("expect") or {}).get("parse", "ok") != "ok":
            continue  # the column shows "Extraction failed": no term is read
        terms = truth.get("compare_terms") or {}
        for row in TERM_ROWS:
            t = terms.get(row)
            if t is None:
                continue
            cell = cells.get((fid, row))
            if cell is None:
                sc.hard("term_row_missing", fid, row, "no cell in the response for this row and document")
                continue
            shown = _shown(cell)
            silent = _clean(t["value"]).casefold() == NOT_STATED.casefold()
            if row == EXCLUSIONS_ROW and cell.get("state") == "verified" and \
                    cell.get("complete") is not None and bool(cell.get("complete")) != bool(t.get("complete")):
                sc.complete_mismatches.append({"case_id": sc.case_id, "document": fid,
                                               "predicted": cell.get("complete"), "truth": t.get("complete")})
            if silent:
                sc.term_absent += 1
                if shown is not None and cell.get("state") == "verified" and not cell.get("edited"):
                    sc.hard("verified_term_differs", fid, row,
                            f"shows {shown!r} for a document that states no category (truth Not stated)",
                            predicted=shown, truth=t["value"])
                elif shown is not None:
                    sc.misses.append({"case_id": sc.case_id, "document": fid, "row": row, "truth": t["value"],
                                      "predicted": shown, "state": cell.get("state"), "reason": "shown_for_silent"})
                continue
            sc.term_present += 1
            ok = shown is not None and term_matches(row, shown, t)
            if ok:
                sc.term_correct += 1
            else:
                sc.misses.append({"case_id": sc.case_id, "document": fid, "row": row, "truth": t["value"],
                                  "predicted": shown or cell.get("value"), "state": cell.get("state"),
                                  "reason": _miss_reason(cell)})
            if cell.get("state") != "verified" or cell.get("edited") or shown is None:
                continue
            if not ok:
                sc.hard("verified_term_differs", fid, row, f"shows {shown!r}, truth {t['value']!r}",
                        predicted=shown, truth=t["value"])
                continue
            pages = list(t.get("pages") or [])
            if row == EXCLUSIONS_ROW and not _labels(shown):
                pages = []  # None of these: the schedule and every title, no single location
            for cite in cell.get("citations") or []:
                doc = fid_of.get(_norm_path(cite.get("document")))
                if doc != fid:
                    sc.hard("cited_other_document", fid, row, f"cites {cite.get('document')!r}")
                elif pages and cite.get("page") not in pages:
                    sc.hard("cited_wrong_page", fid, row, f"cites page {cite.get('page')}, truth pages {pages}")

    # Marks (R7, R12)
    expected = {(m["document"], m["row"]): m for m in case.get("expected_marks") or []}
    actual: dict[tuple[str, str], Mapping] = {}
    for c in block["cells"]:
        if c.get("mark") is None:
            continue
        fid = fid_of.get(_norm_path(c.get("document")))
        actual[(fid or f"column {c.get('column')}", c["row"])] = c
    sc.marks_expected, sc.marks_found = len(expected), len(actual)
    for key, m in sorted(expected.items()):
        got = actual.get(key)
        if got is None:
            sc.hard("missing_mark", key[0], key[1], f"expected {m['kind']} ({m['reason']!r}); the response has none",
                    expected=m["kind"], expected_reason=m["reason"])
        elif got.get("mark") != m["kind"]:
            sc.hard("wrong_mark", key[0], key[1], f"expected {m['kind']}, the response marks {got.get('mark')}",
                    expected=m["kind"], predicted=got.get("mark"))
        elif _clean(got.get("mark_reason")) != _clean(m["reason"]):
            sc.hard("wrong_mark_reason", key[0], key[1],
                    f"reason {got.get('mark_reason')!r}, expected {m['reason']!r}",
                    expected_reason=m["reason"], predicted_reason=got.get("mark_reason"))
        else:
            sc.marks_right += 1
    for key, got in sorted(actual.items()):
        if key not in expected:
            sc.hard("extra_mark", key[0], key[1],
                    f"the response marks {got.get('mark')} ({got.get('mark_reason')!r}); the truth expects no mark "
                    f"(cell {got.get('value')!r}, state {got.get('state')})",
                    predicted=got.get("mark"), predicted_reason=got.get("mark_reason"))
    return sc


@dataclass
class CompareMetrics:
    cases: int
    term_present: int
    term_correct: int
    term_accuracy: float | None
    term_absent: int
    marks_expected: int
    marks_found: int
    marks_right: int
    hard_fails: list[dict]
    misses: list[dict]
    complete_mismatches: list[dict]
    key_differences: dict[str, dict | None]
    subset_cases: list[str] | None = None


def aggregate_compare(scores: Iterable[CompareScore]) -> CompareMetrics:
    scores = list(scores)
    s = lambda attr: sum(getattr(x, attr) for x in scores)  # noqa: E731
    present = s("term_present")
    return CompareMetrics(
        cases=len(scores), term_present=present, term_correct=s("term_correct"),
        term_accuracy=None if present == 0 else s("term_correct") / present, term_absent=s("term_absent"),
        marks_expected=s("marks_expected"), marks_found=s("marks_found"), marks_right=s("marks_right"),
        hard_fails=[h for x in scores for h in x.hard_fails], misses=[m for x in scores for m in x.misses],
        complete_mismatches=[c for x in scores for c in x.complete_mismatches],
        key_differences={x.case_id: x.key_difference for x in scores})


def evaluate_compare_gates(m: CompareMetrics):
    import eval_gates as g

    n = len(m.hard_fails)
    res = [
        g.GateResult("compare_hard_fails (marks and verified terms)", n, f"== {COMPARE_HARD_FAIL_MAX}", True,
                     n <= COMPARE_HARD_FAIL_MAX, "any missing, extra or different worse/best mark is one (R7)"),
        g.GateResult("compare_cases_scored", m.cases, ">= 1", True, m.cases >= 1, "no case is not a pass"),
        g.GateResult("term_accuracy", m.term_accuracy, "reported", False, True,
                     "exact-match over terms the documents state (R6)"),
        g.GateResult("marks_right", m.marks_right, f"of {m.marks_expected} expected", False, True,
                     f"{m.marks_found} marks in the responses"),
        g.GateResult("complete_mismatches", len(m.complete_mismatches), "reported", False, True,
                     "notable_exclusions complete flag differs from the truth (D10)"),
    ]
    return g.GateReport(res)


def format_compare_report(m: CompareMetrics, rep) -> str:
    import eval_gates as g

    lines = ["Compare Quotes terms and marks (R6, R7, R12)"
             + (f"  [subset run: {', '.join(m.subset_cases)}]" if m.subset_cases else ""), "-" * 72]
    for r in rep.results:
        flag = ("PASS" if r.passed else "FAIL") if r.gated else "info"
        note = f"  ({r.note})" if r.note else ""
        lines.append(f"{flag:5} {r.name:52} {g._fmt(r.value):>8}  {r.threshold}{note}")
    lines.append("-" * 72)
    for h in m.hard_fails:
        lines.append(f"HARD FAIL {h['case_id']} {h['kind']} {h['document']}.{h['row']}: {h['detail']}")
    for d in m.misses:
        lines.append(f"MISS {d['case_id']} {d['document']}.{d['row']}: {d['reason']} "
                     f"(predicted {d['predicted']!r}, truth {d['truth']!r}, state {d['state']})")
    for c in m.complete_mismatches:
        lines.append(f"COMPLETE {c['case_id']} {c['document']}: predicted {c['predicted']}, truth {c['truth']}")
    for cid, kd in sorted(m.key_differences.items()):
        if kd:
            lines.append(f"KEY DIFFERENCE {cid} [{kd.get('source')}]: {kd.get('text')}")
    lines.append(f"overall: {'PASS' if rep.passed else 'FAIL'}")
    return "\n".join(lines)


def compare_report_dict(m: CompareMetrics, rep) -> dict:
    return {"passed": rep.passed, "gates": [asdict(r) for r in rep.results], "metrics": asdict(m)}


# ---------------------------------------------------------------------------
# Cases: the existing runs (tests/fixtures/quotes) and the format scenarios (tests/fixtures/compare)
# ---------------------------------------------------------------------------

CASES_ENV = "BROKERDOCS_EVAL_COMPARE_CASES"


def load_cases(repo_root: Path) -> tuple[dict[str, dict], dict[str, dict], dict[str, str]]:
    """(cases, doc truths, paths) for every Compare eval run: the LIVE_RUNS groups of the 23 quote fixtures with the
    hand-written marks of ``tests/fixtures/quotes/compare_marks.json``, then the cases of tests/fixtures/compare."""
    import json

    quotes = repo_root / "tests" / "fixtures" / "quotes"
    compare = repo_root / "tests" / "fixtures" / "compare"
    docs: dict[str, dict] = {}
    paths: dict[str, str] = {}
    for e in json.loads((quotes / "manifest.json").read_text(encoding="utf-8"))["fixtures"]:
        docs[e["fixture_id"]] = json.loads((quotes / e["truth"]).read_text(encoding="utf-8"))
        paths[e["fixture_id"]] = str(quotes / e["file"])
    cases: dict[str, dict] = {}
    marks = json.loads((quotes / "compare_marks.json").read_text(encoding="utf-8"))
    for cid, run in marks["runs"].items():
        cases[cid] = {"case_id": cid, "documents": list(run["documents"]),
                      "expected_marks": list(run["expected_marks"]), "source": "quotes"}
    manifest = json.loads((compare / "manifest.json").read_text(encoding="utf-8"))
    for e in manifest["documents"]:
        docs[e["fixture_id"]] = json.loads((compare / e["truth"]).read_text(encoding="utf-8"))
        paths[e["fixture_id"]] = str(compare / e["file"])
    for e in manifest["cases"]:
        case = json.loads((compare / e["truth"]).read_text(encoding="utf-8"))
        cases[case["case_id"]] = {**case, "source": "compare"}
    return cases, docs, paths


def selected_cases(known: list[str], env: Mapping | None = None) -> list[str] | None:
    import os

    raw = (os.environ if env is None else env).get(CASES_ENV, "")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        return None
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(f"{CASES_ENV}: unknown case(s) {unknown}; known: {known}")
    return list(dict.fromkeys(names))


def run_compare_suite(adapter, cases: Mapping[str, dict], doc_truths: Mapping[str, dict],
                      paths: Mapping[str, str], ids: list[str] | None = None):
    """One adapter call per case (one Compare run). Returns (facts metrics, facts gates, compare metrics, compare
    gates): the R6 facts gate over every document that ran, and the terms and mark gate."""
    import eval_gates as g

    subset = ids if ids is not None else selected_cases(list(cases))
    run_ids = subset or list(cases)
    doc_scores, compare_scores, tokens = [], [], []
    for cid in run_ids:
        case = cases[cid]
        p = {fid: paths[fid] for fid in case["documents"]}
        out = adapter([p[f] for f in case["documents"]])
        tokens.append(int(out.get("input_tokens", 0)))
        docs = out.get("documents", {})
        for fid in case["documents"]:
            raw = docs.get(p[fid]) or {"status": "failed", "failure_reason": "missing_from_adapter_output"}
            doc_scores.append(g.score_document(doc_truths[fid], g.DocResult.from_dict({**raw, "fixture_id": fid})))
        compare_scores.append(score_compare_case(case, doc_truths, out, p))
    fm = g.aggregate(doc_scores, tokens)
    if subset:
        fm.subset_groups = list(subset)
    required = tuple(b for b in g.GATED_BUCKETS if b in fm.buckets) if subset else tuple(g.GATED_BUCKETS)
    cm = aggregate_compare(compare_scores)
    cm.subset_cases = list(subset) if subset else None
    return fm, g.evaluate_gates(fm, require_buckets=required), cm, evaluate_compare_gates(cm)


# ---------------------------------------------------------------------------
# A perfect response (self-test of the scorer, and the reference for the adapter)
# ---------------------------------------------------------------------------


def oracle_compare_block(case: Mapping, doc_truths: Mapping[str, Mapping], paths: Mapping[str, str]) -> dict:
    """What a perfect run's compare block holds: every parsed document's term cells as the truth states them
    (verified at the truth's first page), its total premium, and exactly the expected marks."""
    marks = {(m["document"], m["row"]): m for m in case.get("expected_marks") or []}
    cells, columns = [], []
    for i, fid in enumerate(case["documents"]):
        col = chr(ord("A") + i)
        t = doc_truths[fid]
        columns.append({"key": col, "label": t.get("carrier"), "document": paths[fid],
                        "document_kind": t.get("document_kind")})
        failed = (t.get("expect") or {}).get("parse", "ok") != "ok"
        rows: dict[str, tuple[str, str, int | None]] = {}
        for row in TERM_ROWS:
            term = (t.get("compare_terms") or {}).get(row) or {"value": NOT_STATED, "pages": []}
            silent = term["value"] == NOT_STATED
            pages = term.get("pages") or []
            rows[row] = (term["value"], "absent" if silent else "verified", pages[0] if pages else None)
        prem = (t.get("facts") or {}).get("total_premium")
        if prem:
            rows["total_premium"] = (prem["value"], "scan_read" if prem.get("source") == "image" else "verified",
                                     prem["page"])
        for row, (value, state, page) in rows.items():
            if failed:
                value, state, page = "Extraction failed", "failed", None
            m = marks.get((fid, row))
            cells.append({"row": row, "column": col, "document": paths[fid], "value": value, "state": state,
                          "edited": False, "complete": (t.get("compare_terms") or {}).get(row, {}).get("complete")
                          if row == EXCLUSIONS_ROW else None,
                          "mark": m["kind"] if m else None, "mark_reason": m["reason"] if m else None,
                          "citations": [] if page is None else [{"document": paths[fid], "page": page}]})
    return {"run_status": "done", "columns": columns, "cells": cells, "key_difference": None, "warnings": []}
