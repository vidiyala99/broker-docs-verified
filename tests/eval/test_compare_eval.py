"""Compare Quotes eval (slice 6, task T6): term truth on every fixture, the PDF / Word / Excel scenarios, the mark
gate and its replay path. Offline except ``test_live_compare_gates`` (``-m live``, paid, owner go-ahead).

The paid run (23 quote fixtures in their six LIVE_RUNS groups plus the two format scenarios, one run each), with
every model reply recorded for offline replay::

    BROKERDOCS_EVAL_RECORD=1 pytest -m live tests/eval/test_compare_eval.py -k live_compare

``BROKERDOCS_EVAL_COMPARE_CASES`` (comma-separated: clean, multi_column, scanned, adversarial, long, parse,
three_format, docx_variant) runs a subset; the JSON report goes to ``$BROKERDOCS_EVAL_COMPARE_REPORT``.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import re
import sys
from pathlib import Path

import fitz
import pytest

import compare_gate as cgate
import eval_gates as g
import reply_recorder as rr
from eval_adapter import LIVE_RUNS

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
COMPARE_DIR = REPO_ROOT / "tests" / "fixtures" / "compare"
RECORDED_DIR = EVAL_DIR / "recorded"
NEW_CASES = ("three_format", "docx_variant")
CATEGORIES = {"additional_insured": {"Blanket", "Scheduled only", "Not included", "Not stated"},
              "waiver_of_subrogation": {"Blanket", "Scheduled only", "Not included", "Not stated"},
              "primary_noncontributory": {"Included", "Not included", "Not stated"}}
WATCH_LABELS = {"Action over", "Residential / habitational work", "Subsidence / earth movement",
                "Designated ongoing operations", "Wrap-up / OCIP"}


def _backend(module: str):
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        return importlib.import_module(module)
    except Exception as exc:  # backend deps not installed in this venv
        pytest.skip(f"missing import {module}: {exc!r}")


@pytest.fixture(scope="module")
def suite():
    return cgate.load_cases(REPO_ROOT)


def _norm(s: str) -> str:
    return " ".join(s.split())


def _record_texts(truth: dict, path: str) -> list[str]:
    """Page texts of the PDF the pipeline reads: the file itself, or the rendered record of a Word/Excel file."""
    data = Path(path).read_bytes()
    if truth.get("format") in ("docx", "xlsx"):
        import compare_generator as cgen

        data = cgen.render_record(data, Path(path).name)
    with fitz.open(stream=data, filetype="pdf") as d:
        return [_norm(p.get_text()) for p in d]


# ---------------------------------------------------------------------------
# Truth (R6)
# ---------------------------------------------------------------------------


def test_every_document_has_truth_for_the_four_rows(suite):
    _, docs, _ = suite
    assert len([f for f, t in docs.items() if t.get("gate_bucket") != "compare_formats"]) == 24  # 23 + over_cap
    for fid, t in docs.items():
        terms = t["compare_terms"]
        assert set(terms) == set(cgate.TERM_ROWS), fid
        for row, cats in CATEGORIES.items():
            assert terms[row]["value"] in cats, (fid, row)
            if terms[row]["value"] != "Not stated":
                assert terms[row]["printed"] and terms[row]["pages"], (fid, row)
        ex = terms["notable_exclusions"]
        value = ex["value"]
        if value not in ("Not stated", "None of these"):
            assert set(value.split(", ")) <= WATCH_LABELS, fid
        if value == "None of these":  # D10: only against a printed schedule
            assert ex["complete"] and ex["schedule"] and ex["titles"], fid
        assert ex["complete"] == (ex["schedule"] is not None), fid


def test_the_23_eval_documents_are_scored_on_the_new_rows(suite):
    """R6: every document of the 23-document eval has term truth, and every LIVE_RUNS group is a Compare case with
    hand-written expected marks over the same documents in the same column order."""
    cases, docs, _ = suite
    ids = [f for run in LIVE_RUNS.values() for f in run]
    assert len(ids) == 23 and all("compare_terms" in docs[f] for f in ids)
    for name, run in LIVE_RUNS.items():
        assert cases[name]["documents"] == run, name


def test_printed_wording_is_on_the_pages_the_truth_lists(suite):
    """Each printed term, schedule heading and exclusion title the truth lists is in the text the pipeline reads, on
    every page it lists (for Word and Excel quotes: the rendered record's pages)."""
    _, docs, paths = suite
    checked = 0
    for fid, t in docs.items():
        terms = t["compare_terms"]
        items = [terms[r] for r in CATEGORIES if terms[r]["printed"]]
        ex = terms["notable_exclusions"]
        items += ([ex["schedule"]] if ex["schedule"] else []) + list(ex["titles"])
        if not items:
            continue
        texts = _record_texts(t, paths[fid])
        for item in items:
            text = item.get("printed") or item.get("title")
            for p in item["pages"]:
                assert _norm(text) in texts[p - 1], (fid, text, p)
            assert _norm(item["quote"]) in texts[item["page"] - 1], (fid, item["quote"])
            assert _norm(text) in _norm(item["quote"]), (fid, text)
            checked += 1
    assert checked >= 20


_TERM_WORDS = re.compile(r"additional\s+insured|subrogation|non-?\s*contributory|schedule\s+of\s+forms|"
                         r"exclusion:|action\s+over|wrap-up|subsidence", re.I)


def test_quotes_with_no_term_truth_print_no_term(suite):
    """Not stated on the three-page quotes is what they print: none of the term phrases appears in any page."""
    _, docs, paths = suite
    for fid, t in docs.items():
        if t.get("gate_bucket") == "compare_formats" or t["expect"].get("parse") != "ok":
            continue
        terms = t["compare_terms"]
        if any(terms[r]["printed"] for r in CATEGORIES) or terms["notable_exclusions"]["titles"]:
            continue
        with fitz.open(paths[fid]) as d:
            for i, page in enumerate(d, 1):
                assert not _TERM_WORDS.search(page.get_text()), (fid, i)


def test_expected_marks_are_hand_written_and_well_formed(suite):
    cases, docs, _ = suite
    for cid, case in cases.items():
        for m in case["expected_marks"]:
            assert m["document"] in case["documents"], cid
            assert m["row"] in cgate.MARKED_ROWS and m["kind"] in cgate.MARK_KINDS, (cid, m)
            assert (m["kind"] == "best") == (m["row"] == "total_premium"), (cid, m)
            assert m["reason"], (cid, m)
        best = [m for m in case["expected_marks"] if m["kind"] == "best"]
        assert len(best) <= 1, cid
    # O6: neither the gate nor the fixture generator computes marks with backend code
    for name in ("compare_gate.py", "compare_generator.py"):
        src = (EVAL_DIR / name).read_text(encoding="utf-8")
        assert "compare_rules" not in src.replace("never computed by compare_rules", ""), name


def test_hand_written_marks_agree_with_compare_rules_over_the_truth(suite):
    """Diagnostic only (R12: the recomputation is not the gate): compare_rules over the truth values gives the
    hand-written marks. A failure means a truth key or a ranking rule is wrong: look at both."""
    cr = _backend("backend.pipeline.compare_rules")
    schemas = _backend("backend.schemas")
    cases, docs, _ = suite
    for cid, case in cases.items():
        cols = [schemas.Column(key=chr(65 + i), label=docs[f].get("carrier") or f, document_id=i + 1)
                for i, f in enumerate(case["documents"])]
        rows = []
        for row in (*cgate.TERM_ROWS, "total_premium"):
            cells = {}
            for col, fid in zip(cols, case["documents"]):
                t = docs[fid]
                failed = t["expect"].get("parse") != "ok"
                if row == "total_premium":
                    prem = t["facts"].get("total_premium")
                    state = ("absent" if not prem else "scan_read" if prem["source"] == "image" else "verified")
                    value = prem["value"] if prem else "Not specified"
                else:
                    term = t["compare_terms"][row]
                    value = term["value"]
                    state = "absent" if value == "Not stated" else "verified"
                if failed:
                    value, state = "Extraction failed", "failed"
                extra = {"complete": t["compare_terms"]["notable_exclusions"]["complete"]} \
                    if row == "notable_exclusions" else {}
                cells[col.key] = schemas.Cell(value=value, state=state, **extra)
            rows.append(schemas.Row(key=row, label=row, cells=cells))
        table = schemas.TableBlock(id="t", title="t", columns=cols, rows=rows)
        got = {(case["documents"][ord(c) - 65], r): (m.kind, m.reason) for (r, c), m in cr.mark_cells(table).items()}
        want = {(m["document"], m["row"]): (m["kind"], m["reason"]) for m in case["expected_marks"]}
        assert got == want, cid


# ---------------------------------------------------------------------------
# New fixtures: generator, determinism, labels
# ---------------------------------------------------------------------------


def test_compare_fixtures_are_what_the_generator_writes(tmp_path):
    import compare_generator as cgen

    _backend("backend.uploads")
    committed = json.loads((COMPARE_DIR / "manifest.json").read_text(encoding="utf-8"))
    fresh = cgen.generate(tmp_path)
    assert committed["generator_version"] == cgen.GENERATOR_VERSION and committed["seed"] == cgen.DEFAULT_SEED
    same_libs = committed["libraries"] == cgen.library_versions()
    for e, f in zip(committed["documents"], fresh["documents"], strict=True):
        data = (COMPARE_DIR / e["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == e["sha256"], e["fixture_id"]
        if same_libs:
            assert (e["sha256"], e["record_sha256"]) == (f["sha256"], f["record_sha256"]), \
                f"{e['fixture_id']}: regenerate with python tests/eval/compare_generator.py"
        t_committed = json.loads((COMPARE_DIR / e["truth"]).read_text(encoding="utf-8"))
        t_fresh = json.loads((tmp_path / f["truth"]).read_text(encoding="utf-8"))
        if not same_libs:
            for k in ("size_bytes", "record_sha256"):
                t_committed.pop(k), t_fresh.pop(k)
        assert t_committed == t_fresh, e["fixture_id"]
    for e in committed["cases"]:
        assert (COMPARE_DIR / e["truth"]).read_text(encoding="utf-8") == \
            (tmp_path / e["truth"]).read_text(encoding="utf-8")


def test_three_formats_one_account_and_a_docx_variant(suite):
    cases, docs, paths = suite
    tri = [docs[f] for f in cases["three_format"]["documents"]]
    assert [t["format"] for t in tri] == ["pdf", "docx", "xlsx"]
    assert len({t["facts"]["named_insured"]["value"] for t in tri}) == 1
    variant = [docs[f] for f in cases["docx_variant"]["documents"]]
    assert [t["format"] for t in variant] == ["pdf", "docx", "pdf"]
    assert [t["document_kind"] for t in variant] == ["proposal", "quote", "quote"]
    carriers = {t["carrier"] for c in NEW_CASES for t in (docs[f] for f in cases[c]["documents"])}
    old = {t.get("carrier") for f, t in docs.items() if t.get("gate_bucket") != "compare_formats"}
    assert len(carriers) == 6 and not carriers & old  # new carrier names (spec section 6)


def test_new_fixtures_are_labeled_synthetic_sample_data(suite):
    cases, docs, paths = suite
    for cid in NEW_CASES:
        assert cases[cid]["synthetic"] and "Synthetic sample data" in cases[cid]["description"]
        for fid in cases[cid]["documents"]:
            t = docs[fid]
            assert t["synthetic"] and t["synthetic_label"] == "Synthetic sample data", fid
            texts = _record_texts(t, paths[fid])
            assert "Synthetic sample data" in texts[0], fid


def test_partial_lists_and_none_of_these_follow_d10(suite):
    """R9/D10 in the truth: "None of these" only with a printed schedule; a list without a schedule is partial
    (never complete), and a partial list with no watch-list title is Not stated."""
    _, docs, _ = suite
    ex = {f: docs[f]["compare_terms"]["notable_exclusions"] for f in docs if f.startswith("cmp_")}
    assert ex["cmp_tri_a_quote"]["value"] == "None of these" and ex["cmp_tri_a_quote"]["complete"]
    assert ex["cmp_tri_c_summary"] == {**ex["cmp_tri_c_summary"], "value": "Subsidence / earth movement",
                                       "complete": False}
    assert ex["cmp_docx_a_proposal"]["complete"] is False and ex["cmp_docx_b_quote"]["complete"] is True
    assert ex["cmp_docx_c_quote"]["value"] == "Not stated" and ex["cmp_docx_c_quote"]["titles"]


# ---------------------------------------------------------------------------
# The gate (R7, R12) on hand-built responses
# ---------------------------------------------------------------------------


def _oracle_output(case, docs, paths):
    return {"input_tokens": 1000, "documents": {}, "compare": cgate.oracle_compare_block(case, docs, paths)}


def _score(case, docs, paths, out):
    sc = cgate.score_compare_case(case, docs, out, {f: paths[f] for f in case["documents"]})
    m = cgate.aggregate_compare([sc])
    return sc, m, cgate.evaluate_compare_gates(m)


def test_a_perfect_response_passes_every_case(suite):
    cases, docs, paths = suite
    for cid, case in cases.items():
        sc, m, rep = _score(case, docs, paths, _oracle_output(case, docs, paths))
        assert rep.passed and sc.hard_fails == [], cgate.format_compare_report(m, rep)
        assert sc.marks_right == sc.marks_expected == len(case["expected_marks"]), cid
        assert m.term_accuracy in (None, 1.0), cid


def _cell(out, document, row):
    return next(c for c in out["compare"]["cells"] if c["document"] == document and c["row"] == row)


def _plant(kind: str, out: dict, paths: dict) -> None:
    b, a, c = (paths[f] for f in ("cmp_tri_b_quote", "cmp_tri_a_quote", "cmp_tri_c_summary"))
    if kind == "missing_mark":  # the lowest premium loses its best mark
        _cell(out, b, "total_premium").update(mark=None, mark_reason=None)
    elif kind == "extra_mark":  # A's blanket additional insured marked worse
        _cell(out, a, "additional_insured").update(mark="worse", mark_reason="Others: Blanket")
    elif kind == "extra_best":  # a second, wrong best on C's premium
        _cell(out, c, "total_premium").update(mark="best", mark_reason="Lowest premium")
    elif kind == "wrong_mark":
        _cell(out, b, "total_premium").update(mark="worse", mark_reason="Lowest premium")
    elif kind == "wrong_mark_reason":
        _cell(out, b, "additional_insured").update(mark_reason="Others: Scheduled only")
    elif kind == "moved_best":  # a ranking bug: the best mark lands on the wrong carrier
        _cell(out, b, "total_premium").update(mark=None, mark_reason=None)
        _cell(out, a, "total_premium").update(mark="best", mark_reason="Lowest premium")
    else:
        raise AssertionError(kind)


@pytest.mark.parametrize("plant,kinds", [
    ("missing_mark", ["missing_mark"]), ("extra_mark", ["extra_mark"]), ("extra_best", ["extra_mark"]),
    ("wrong_mark", ["wrong_mark"]), ("wrong_mark_reason", ["wrong_mark_reason"]),
    ("moved_best", ["missing_mark", "extra_mark"])])
def test_a_planted_wrong_mark_in_the_response_is_a_hard_fail(suite, plant, kinds):
    cases, docs, paths = suite
    case = cases["three_format"]
    out = _oracle_output(case, docs, paths)
    _plant(plant, out, paths)
    sc, m, rep = _score(case, docs, paths, out)
    assert sorted(h["kind"] for h in sc.hard_fails) == sorted(kinds), sc.hard_fails
    assert not rep.passed and [r.name for r in rep.failures] == ["compare_hard_fails (marks and verified terms)"]
    assert "HARD FAIL three_format" in cgate.format_compare_report(m, rep)


def test_an_invented_verified_term_is_a_hard_fail_and_a_scan_read_one_is_a_miss(suite):
    cases, docs, paths = suite
    case = cases["clean"]
    a = paths["carrier_a_clean"]
    out = _oracle_output(case, docs, paths)
    _cell(out, a, "additional_insured").update(value="Not included", state="verified",
                                               citations=[{"document": a, "page": 1}])
    sc, _, rep = _score(case, docs, paths, out)
    assert [h["kind"] for h in sc.hard_fails] == ["verified_term_differs"] and not rep.passed
    out = _oracle_output(case, docs, paths)
    _cell(out, a, "additional_insured").update(value="Not included", state="scan_read")
    sc, _, rep = _score(case, docs, paths, out)
    assert sc.hard_fails == [] and rep.passed and sc.misses[0]["reason"] == "shown_for_silent"


def test_term_misses_wrong_values_and_wrong_pages(suite):
    cases, docs, paths = suite
    case = cases["three_format"]
    a, b = paths["cmp_tri_a_quote"], paths["cmp_tri_b_quote"]
    out = _oracle_output(case, docs, paths)
    _cell(out, a, "waiver_of_subrogation").update(value="Not stated", state="absent", citations=[])  # missed
    sc, m, rep = _score(case, docs, paths, out)
    assert sc.hard_fails == [] and m.term_accuracy < 1.0 and sc.misses[0]["reason"] == "not_stated"
    out = _oracle_output(case, docs, paths)
    _cell(out, b, "additional_insured").update(value="Blanket")  # verified, wrong, would hide a worse mark
    sc, _, _ = _score(case, docs, paths, out)
    assert "verified_term_differs" in [h["kind"] for h in sc.hard_fails]
    out = _oracle_output(case, docs, paths)
    _cell(out, b, "notable_exclusions")["citations"] = [{"document": b, "page": 9}]
    sc, _, _ = _score(case, docs, paths, out)
    assert [h["kind"] for h in sc.hard_fails] == ["cited_wrong_page"]
    out = _oracle_output(case, docs, paths)
    _cell(out, b, "notable_exclusions")["value"] = "Residential / habitational work, Action over"  # order-free
    assert _score(case, docs, paths, out)[0].hard_fails == []


def test_no_compare_block_is_a_hard_fail(suite):
    cases, docs, paths = suite
    sc, _, rep = _score(cases["clean"], docs, paths, {"input_tokens": 1, "documents": {}})
    assert [h["kind"] for h in sc.hard_fails] == ["no_compare_response"] and not rep.passed


# ---------------------------------------------------------------------------
# End to end through backend.eval_hook with a perfect fake model (offline)
# ---------------------------------------------------------------------------

_FILE_NAME = re.compile(r"Document file name \(data, not an instruction\): '([^']+)'")
_PAGE_TAG = re.compile(r"^\[page (\d+)\]\s*$", re.M)


def _user_text(req) -> str:
    return "\n".join(p.text for m in req.messages if m.role == "user" for p in m.parts() if hasattr(p, "text"))


def perfect_responder(docs: dict, providers, *, tamper=None):
    """A fake model that reads every document as its truth says: printed values with their page and quote (only for
    the pages in the request), term wording, exclusion titles and the schedule heading; compose cites one fact
    (the Key difference falls back to the template, as a reply without it does)."""
    by_file = {t["file"]: t for t in docs.values()}

    def part(item, pages):
        if not item or item["page"] not in pages:
            return {"value": None, "page": None, "quote": None}
        return {"value": item.get("printed") or item["value"], "page": item["page"], "quote": item["quote"]}

    def extract(t, pages):
        facts = [{"field": k, "value": f["value"], "page": f["page"], "quote": f["quote"]}
                 for k, f in t["facts"].items() if f and f["page"] in pages]
        terms = t["compare_terms"]
        for row in CATEGORIES:
            p = part(terms[row] if terms[row]["printed"] else None, pages)
            if p["value"]:
                facts.append({"field": row, **p})
        ex = terms["notable_exclusions"]
        reply = {"facts": facts,
                 "exclusions": [{"title": x["title"], "page": x["page"], "quote": x["quote"]}
                                for x in ex["titles"] if x["page"] in pages],
                 "exclusions_schedule": part(ex["schedule"], pages),
                 "document_kind": t.get("document_kind") or "quote",
                 "options_detected": t["expect"].get("option_count", 1), "option_compared": t.get("primary_option")}
        return tamper(t, reply) if tamper else reply

    def compose(text):
        block = text[text.index("<<<FACTS"):]
        payload = json.loads(block[block.index("\n") + 1: block.rindex("FACTS")])
        facts = payload["facts"]
        if not facts:
            return {"sections": [{"section_id": "executive_summary", "paragraphs": [
                {"segments": [{"text": "No values were verified.", "passage_ids": []}]}]}]}
        f = facts[0]
        return {"sections": [{"section_id": "executive_summary", "paragraphs": [{"segments": [
            {"text": f"{f['document']} states {f['label']} {f['value']}.", "passage_ids": [f["passage_id"]]}]}]}]}

    def respond(req):
        text = _user_text(req)
        if req.role is providers.CallRole.EXTRACT:
            t = by_file[_FILE_NAME.search(text).group(1)]
            return extract(t, {int(n) for n in _PAGE_TAG.findall(text)})
        if req.role is providers.CallRole.COMPOSE:
            return compose(text)
        raise AssertionError(f"unexpected {req.role} call")

    return respond


E2E_CASES = ["clean", *NEW_CASES]


def _hook_suite(suite, ids, provider_factory):
    eval_hook = _backend("backend.eval_hook")
    _backend("backend.office_docx")
    cases, docs, paths = suite

    def adapter(p):
        return eval_hook.run_eval_batch(p, provider=provider_factory())

    return cgate.run_compare_suite(adapter, cases, docs, paths, ids=ids)


def test_backend_end_to_end_with_a_perfect_fake_model(suite):
    """The real pipeline (parse, Word/Excel rendering through prepare_office, extract, verify, compose, marks on
    read) through backend.eval_hook with a fake model that reads every document perfectly: both gates pass, every
    term is right and every mark in the artifact response is exactly the hand-written one (0 hard fails)."""
    providers = _backend("backend.providers")
    _, docs, _ = suite
    fm, fg, cm, cg_ = _hook_suite(suite, E2E_CASES,
                                  lambda: providers.FakeProvider(perfect_responder(docs, providers)))
    report = g.format_report(fm, fg) + "\n" + cgate.format_compare_report(cm, cg_)
    assert fg.passed and cg_.passed, report
    assert cm.hard_fails == [] and fm.hard_fails == 0, report
    assert cm.marks_right == cm.marks_expected == 12, report  # 1 + 6 + 5 hand-written marks
    assert cm.term_accuracy == 1.0 and cm.misses == [], report
    assert fm.buckets["compare_formats"].field_accuracy == 1.0, report
    for cid in NEW_CASES:
        assert cm.key_differences[cid] and cm.key_differences[cid]["source"] == "template", report


def test_a_planted_ranking_bug_in_the_backend_response_hard_fails(suite, monkeypatch):
    """R12: the gate reads the marks the response carries. A ranking bug in compare_rules (the best premium mark
    moved to the most expensive quote) shows up in the response and fails the gate; computing the marks again
    from the cells inside the eval would have hidden it."""
    providers = _backend("backend.providers")
    cr = _backend("backend.pipeline.compare_rules")
    _, docs, _ = suite
    real = cr.mark_cells

    def buggy(tables):
        marks = dict(real(tables))
        best = [k for k, m in marks.items() if m.kind == "best"]
        for k in best:
            del marks[k]
            marks[(k[0], "C")] = cr.Mark("best", "Lowest premium")  # C is the most expensive quote
        return marks

    monkeypatch.setattr(cr, "mark_cells", buggy)
    _, _, cm, rep = _hook_suite(suite, ["three_format"],
                                lambda: providers.FakeProvider(perfect_responder(docs, providers)))
    kinds = sorted((h["kind"], h["document"], h["row"]) for h in cm.hard_fails)
    assert kinds == [("extra_mark", "cmp_tri_c_summary", "total_premium"),
                     ("missing_mark", "cmp_tri_b_quote", "total_premium")], cgate.format_compare_report(cm, rep)
    assert not rep.passed


def test_an_invented_endorsement_the_page_does_not_print_is_never_shown(suite):
    """R6 through the backend: the fake model invents "Additional insured: Not included" for a quote that prints no
    such term. Verify rejects the quote, so the cell is never shown as Not included and draws no mark: the gate
    sees no wrong term and no extra mark (the hard-fail path for a shown one is tested on built responses)."""
    providers = _backend("backend.providers")
    eval_hook = _backend("backend.eval_hook")
    cases, docs, paths = suite

    def invent(t, reply):
        if t["fixture_id"] == "carrier_a_clean":
            reply["facts"].append({"field": "additional_insured", "value": "Not included", "page": 1,
                                   "quote": "Additional Insured: Not included"})
        return reply

    case = cases["clean"]
    p = {f: paths[f] for f in case["documents"]}
    out = eval_hook.run_eval_batch([p[f] for f in case["documents"]], provider=providers.FakeProvider(
        perfect_responder(docs, providers, tamper=invent)))
    cell = _cell(out, p["carrier_a_clean"], "additional_insured")
    assert cell["state"] != "verified" and cell["mark"] is None, cell
    sc = cgate.score_compare_case(case, docs, out, p)
    assert sc.hard_fails == [], sc.hard_fails


def test_recorder_round_trip_replays_a_compare_run_exactly(suite):
    """Record a fake-model run keyed by request fingerprint, replay it: every request finds its reply and the
    compare block (cells, marks) is the same. This is what makes a recorded live run replayable offline."""
    providers = _backend("backend.providers")
    eval_hook = _backend("backend.eval_hook")
    cases, docs, paths = suite
    case = cases["three_format"]
    p = [paths[f] for f in case["documents"]]
    store: dict[str, list[dict]] = {}
    fake = providers.FakeProvider(perfect_responder(docs, providers))
    first = eval_hook.run_eval_batch(p, provider=rr.record_into(fake, store))
    assert store
    second = eval_hook.run_eval_batch(p, provider=rr.replay_provider(json.loads(json.dumps(store))))

    def cells(out):
        return sorted(json.dumps({k: v for k, v in c.items() if k != "citations"}, sort_keys=True)
                      for c in out["compare"]["cells"])

    assert cells(second) == cells(first)
    # the compose request numbers passages in the order documents finish: replay matches it on the canonical key
    # and renumbers the reply, so the prose replays too (no compose fallback)
    assert any(r.get("canonical_key") for v in store.values() for r in v)
    assert second["compare"]["notices"] == first["compare"]["notices"]
    assert all(n["kind"] != "compose_fallback" for n in second["compare"]["notices"])
    assert second["compare"]["key_difference"]["text"] == first["compare"]["key_difference"]["text"]
    sc = cgate.score_compare_case(case, docs, second, {f: paths[f] for f in case["documents"]})
    assert sc.hard_fails == [] and sc.marks_right == 6


def test_recorded_live_compare_replay(suite):
    """Replays the latest ``recorded/compare_live_*.json`` (every model reply of an approved live run) offline. Skips
    until the first paid run is recorded (owner go-ahead)."""
    recorded = sorted(RECORDED_DIR.glob("compare_live_*.json"))
    if not recorded:
        pytest.skip("no recorded live compare run yet (tests/eval/recorded/compare_live_<date>.json)")
    data = json.loads(recorded[-1].read_text(encoding="utf-8"))
    assert data.get("format") == rr.FORMAT, recorded[-1]
    replies = data["cases"]
    eval_hook = _backend("backend.eval_hook")
    cases, docs, paths = suite

    def adapter_for(cid):
        return lambda p: eval_hook.run_eval_batch(p, provider=rr.replay_provider(replies[cid]["replies"]))

    scores, doc_scores = [], []
    for cid in sorted(replies):
        case = cases[cid]
        p = {f: paths[f] for f in case["documents"]}
        out = adapter_for(cid)([p[f] for f in case["documents"]])
        scores.append(cgate.score_compare_case(case, docs, out, p))
        for fid in case["documents"]:
            raw = out["documents"].get(p[fid]) or {"status": "failed"}
            doc_scores.append(g.score_document(docs[fid], g.DocResult.from_dict({**raw, "fixture_id": fid})))
    cm = cgate.aggregate_compare(scores)
    rep = cgate.evaluate_compare_gates(cm)
    if data.get("passed"):
        assert rep.passed, f"{recorded[-1].name}\n" + cgate.format_compare_report(cm, rep)
        assert g.aggregate(doc_scores).hard_fails == 0


def test_recorded_long_120_retention_label_is_dropped_on_replay(suite):
    """The 2026-11-05 live run failed the facts gate once: long_120.retention came back as "Amended Deductible:
    $10,000 per occurrence" (page 104) and verified, since the value is inside its quote. Replaying that run's
    recorded replies offline, the amount rule (backend.pipeline.merge.labelled_amount) drops the label: the cell is
    the amount, still on page 104, and the long group has no facts hard fail. Only the facts are checked: the
    compose prompt holds the corrected value, so the recorded compose reply no longer matches it."""
    path = RECORDED_DIR / "compare_live_2026-11-05.json"
    if not path.exists():
        pytest.skip(f"{path.name} is not in this checkout")
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = next(r["content"] for rs in data["cases"]["long"]["replies"].values() for r in rs
               if '"Amended Deductible: $10,000 per occurrence","page":104' in r["content"])
    assert '"field":"retention","value":"Amended Deductible: $10,000 per occurrence"' in raw  # the recorded reply
    eval_hook = _backend("backend.eval_hook")
    cases, docs, paths = suite
    case = cases["long"]
    p = [paths[f] for f in case["documents"]]
    out = eval_hook.run_eval_batch(p, provider=rr.replay_provider(data["cases"]["long"]["replies"]))
    for fid, path_ in zip(case["documents"], p):
        sc = g.score_document(docs[fid], g.DocResult.from_dict({**out["documents"][path_], "fixture_id": fid}))
        assert sc.hard_fails == 0, (fid, [o for o in sc.outcomes if o.hard_fail])
    s120 = g.score_document(docs["long_120"],
                            g.DocResult.from_dict({**out["documents"][paths["long_120"]], "fixture_id": "long_120"}))
    ret = next(o for o in s120.outcomes if o.field == "retention")
    assert (ret.predicted, ret.state, ret.page, ret.correct) == ("$10,000 per occurrence", "verified", 104, True)


# ---------------------------------------------------------------------------
# Live gate (paid; owner approves each run)
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_compare_gates(suite, tmp_path, capsys):
    """One Compare run per case: the 23 quote fixtures in their LIVE_RUNS groups plus the two format scenarios.
    Gates: the R6 facts gate (0 hard fails, clean >= 0.90, multi-column >= 0.80, long >= 0.85) and the compare gate
    (0 hard fails: every hand-written mark exactly, no extra mark, no wrong verified term). ``BROKERDOCS_EVAL_RECORD=1``
    writes every model reply to ``recorded/compare_live_<date>.json`` for offline replay."""
    from eval_adapter import AdapterUnavailable, load_adapter

    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    try:
        adapter = load_adapter()
    except AdapterUnavailable as exc:
        pytest.skip(f"eval adapter unavailable: {exc}")
    cases, docs, paths = suite
    record = os.environ.get("BROKERDOCS_EVAL_RECORD") == "1"
    recorded: dict[str, dict[str, list[dict]]] = {}
    case_of = {tuple(paths[f] for f in c["documents"]): cid for cid, c in cases.items()}
    live_adapter = adapter

    def run(p):
        if not record:
            return live_adapter(p)
        store = recorded.setdefault(case_of[tuple(p)], {})
        prov = rr.recording_provider(store)
        try:
            return live_adapter(p, provider=prov)
        finally:
            rr.close_provider(prov)

    fm, fg, cm, cg_ = cgate.run_compare_suite(run, cases, docs, paths)
    report = g.format_report(fm, fg) + "\n\n" + cgate.format_compare_report(cm, cg_)
    target = Path(os.environ.get("BROKERDOCS_EVAL_COMPARE_REPORT") or tmp_path / "compare_report.json")
    target.write_text(json.dumps({"facts": g.report_dict(fm, fg), "compare": cgate.compare_report_dict(cm, cg_)},
                                 indent=2, default=str), encoding="utf-8")
    with capsys.disabled():
        print("\n" + report + f"\nreport: {target}")
        if record:
            import datetime

            from backend.pipeline.cache import EXTRACTION_VERSION

            day = datetime.date.today().isoformat()
            n = len(list(RECORDED_DIR.glob(f"compare_live_{day}*.json")))
            out = RECORDED_DIR / f"compare_live_{day}{'' if n == 0 else chr(ord('a') + n)}.json"
            rr.write_recording(out, recorded, {"extraction_version": EXTRACTION_VERSION,
                                               "passed": fg.passed and cg_.passed,
                                               "synthetic": "Synthetic sample data (tests/fixtures/quotes, "
                                                            "tests/fixtures/compare)"})
            print(f"recorded: {out} ({sum(len(v) for r in recorded.values() for v in r.values())} replies)")
    assert fg.passed and cg_.passed, report
