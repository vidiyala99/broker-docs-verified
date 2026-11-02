"""Offline tests for the Proposal Builder eval (slice 2, T1): the multi-quote
fixtures, the fail-closed cell and prose scorer, the proposal gate and the
suite runner. No model calls."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import fitz
import pytest

import eval_gates as g
import proposal_generator as pg
import quote_generator as qg
from eval_adapter import run_proposal_suite, selected_proposals

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def fresh(tmp_path_factory):
    out = tmp_path_factory.mktemp("proposals")
    return out, pg.generate(out)


@pytest.fixture
def paths(proposal_dir, proposal_doc_truths):
    return {fid: str(proposal_dir / t["file"]) for fid, t in proposal_doc_truths.items()}


def _oracle(proposals, doc_truths, paths, pid, tokens=1000):
    return pg_out(proposals[pid], doc_truths, paths, tokens)


def pg_out(p, doc_truths, paths, tokens=1000):
    return g.proposal_oracle_output(p, doc_truths, {f: paths[f] for f in p["documents"]},
                                    input_tokens=tokens)


def _score(proposals, doc_truths, paths, pid, mutate=None):
    out = _oracle(proposals, doc_truths, paths, pid)
    if mutate:
        mutate(out)
    return g.score_proposal(proposals[pid], doc_truths, out, {f: paths[f] for f in proposals[pid]["documents"]})


def _cell(out, path, row):
    return next(c for c in out["proposal"]["cells"] if c["document"] == path and c["row"] == row)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def test_catalogue(proposals, proposal_doc_truths, proposal_manifest):
    assert sorted(proposals) == ["prop_amended", "prop_three_carrier", "prop_two_carrier_absent"]
    assert sorted(len(p["documents"]) for p in proposals.values()) == [2, 2, 3]
    docs = [f for p in proposals.values() for f in p["documents"]]
    assert sorted(docs) == sorted(proposal_doc_truths)
    carriers = [proposal_doc_truths[f]["carrier"] for f in docs]
    assert len(set(carriers)) == len(carriers)  # every quote is a different carrier
    for p in proposals.values():
        assert p["workflow"] == "proposal-builder" and p["gate_bucket"] == "proposal"
        assert p["fail_closed"] is True and p["template"] in ("standard", "premier")
        assert set(p["parameters"]) == {"broker_name", "brokerage_name", "additional_context",
                                        "no_ai_prose"}
        assert len(p["documents"]) <= 8
    assert any(p["parameters"]["no_ai_prose"] for p in proposals.values())
    assert proposal_manifest["seed"] == pg.DEFAULT_SEED


def test_same_seed_same_bytes(fresh, tmp_path):
    _, m1 = fresh
    m2 = pg.generate(tmp_path)
    assert [d["sha256"] for d in m1["documents"]] == [d["sha256"] for d in m2["documents"]]


def test_committed_fixtures_match_generator(fresh, proposal_manifest, proposal_dir):
    out, m = fresh
    assert proposal_manifest["generator_version"] == pg.GENERATOR_VERSION
    same_lib = proposal_manifest["pymupdf_version"] == fitz.VersionBind
    assert [d["fixture_id"] for d in proposal_manifest["documents"]] == [d["fixture_id"] for d in m["documents"]]
    for e, f in zip(proposal_manifest["documents"], m["documents"]):
        committed = (proposal_dir / e["file"]).read_bytes()
        assert hashlib.sha256(committed).hexdigest() == e["sha256"], e["fixture_id"]
        if same_lib:
            assert e["sha256"] == f["sha256"], f"{e['fixture_id']}: regenerate proposal fixtures"
        tc = json.loads((proposal_dir / e["truth"]).read_text(encoding="utf-8"))
        tf = json.loads((out / f["truth"]).read_text(encoding="utf-8"))
        tc.pop("size_bytes"), tf.pop("size_bytes")
        assert tc == tf, e["fixture_id"]
    for p in proposal_manifest["proposals"]:
        assert (json.loads((proposal_dir / p["truth"]).read_text(encoding="utf-8"))
                == json.loads((out / p["truth"]).read_text(encoding="utf-8")))


def test_truth_lists_every_proposal_field(proposal_doc_truths):
    for fid, t in proposal_doc_truths.items():
        assert set(t["facts"]) == set(pg.FIELDS), fid
        assert t["fail_closed"] is True and set(t["unscored_fields"]) == set(pg.UNSCORED_FIELDS)
        assert not set(t["facts"]) & set(t["unscored_fields"])
        for f in pg.POLICY_WIDE_NULL:
            assert t["facts"][f] is None, (fid, f)


def test_cyber_and_eo_sub_coverage_facts_are_present(proposal_doc_truths):
    always = [*pg.EO, "cyber_aggregate_limit", "cyber_retention", "cyber_business_interruption",
              "minimum_earned_premium", "aggregate_limit"]
    for fid, t in proposal_doc_truths.items():
        for f in always:
            assert t["facts"][f] is not None, (fid, f)
    for f in ("cyber_ransomware_sublimit", "cyber_social_engineering"):
        missing = [fid for fid, t in proposal_doc_truths.items() if t["facts"][f] is None]
        assert missing == ["prop_two_carrier_absent_e"], f


def test_every_printed_fact_uses_its_schema_label(proposal_doc_truths):
    """The schema is the contract: a tagged line is the field's schema label and its value
    (a table cell is the value alone, its label on the same baseline)."""
    for fid, t in proposal_doc_truths.items():
        for fname, fact in t["facts"].items():
            if fact is None:
                continue
            if t["variant"] == "proposal_table" and fname in pg.COVERAGE_PARTS:
                assert fact["quote"] == fact["value"], (fid, fname)
            else:
                assert fact["quote"] == f"{pg.SCHEMA_LABELS[fname]}: {fact['value']}", (fid, fname)


def test_fact_values_sit_on_their_truth_pages_only(proposal_doc_truths, proposal_dir):
    for fid, t in proposal_doc_truths.items():
        doc = fitz.open(proposal_dir / t["file"])
        assert doc.page_count == t["page_count"]
        texts = [p.get_text() for p in doc]
        for fname, fact in t["facts"].items():
            if fact is None:
                continue
            assert fact["value"] in fact["quote"], (fid, fname)
            assert fact["pages"] == [i for i, tx in enumerate(texts, 1) if fact["value"] in tx], (fid, fname)
            if fname != "carrier_name":  # the carrier name heads every page
                assert fact["pages"] == [fact["page"]], (fid, fname)
            page = doc[fact["page"] - 1]
            assert fact["quote"] in page.get_text(), (fid, fname)
            assert page.search_for(fact["value"]), (fid, fname)
            for other in set(range(1, doc.page_count + 1)) - set(fact["pages"]):
                assert fact["value"] not in texts[other - 1], (fid, fname, other)


def test_values_are_distinct_within_a_document(proposal_doc_truths):
    """A model that swaps two coverage rows must score wrong, never right by accident.
    (The tech E&O limit may equal the policy aggregate, as in real programs.)"""
    for fid, t in proposal_doc_truths.items():
        vals = [f["value"] for k, f in t["facts"].items() if f and k in pg.PROPOSAL_ONLY_FIELDS]
        assert len(vals) == len(set(vals)), fid


def test_absent_fields_have_no_labeled_line(proposal_doc_truths, proposal_dir):
    t = proposal_doc_truths["prop_two_carrier_absent_e"]
    text = " ".join(p.get_text() for p in fitz.open(proposal_dir / t["file"]))
    for f in ("am_best_rating", "cyber_ransomware_sublimit", "cyber_social_engineering"):
        assert t["facts"][f] is None
        assert pg.LABELS[f] not in text
    for fid, t in proposal_doc_truths.items():
        text = " ".join(p.get_text() for p in fitz.open(proposal_dir / t["file"]))
        for f in (*pg.POLICY_WIDE_NULL, "each_occurrence_limit", "retention"):
            assert pg.LABELS[f] + ":" not in text and qg.LABELS[f] + ":" not in text, (fid, f)


def test_amended_value_is_truth_and_declared_value_is_a_decoy(proposal_doc_truths, proposal_dir):
    t = proposal_doc_truths["prop_amended_f"]
    fact = t["facts"]["cyber_social_engineering"]
    (decoy,) = t["decoys"]
    assert decoy == {"field": "cyber_social_engineering", "value": "$100,000", "kind": "superseded",
                     "pages": [2]}
    assert fact["value"] == "$250,000" and fact["page"] == 4 == t["page_count"]
    assert fact["quote"] == "Social Engineering Sublimit: $250,000"
    doc = fitz.open(proposal_dir / t["file"])
    assert "Amendment of Social Engineering Sublimit" in doc[3].get_text()
    # The trap: the superseded figure is printed under the same schema label.
    assert "Social Engineering Sublimit: $100,000" in doc[1].get_text()


def test_table_layout_reads_values_before_labels(proposal_dir):
    text = fitz.open(proposal_dir / "prop_three_carrier_c.pdf")[1].get_text()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines.index("$1,500,000") < lines.index("E&O Each Claim Limit")
    assert lines.index("Outside the limits of liability") < lines.index("Cyber Aggregate Limit")


def test_no_em_dashes_and_no_real_carriers(proposal_doc_truths, proposal_dir, proposals):
    blob = json.dumps([proposal_doc_truths, proposals], ensure_ascii=False)
    for t in proposal_doc_truths.values():
        blob += " ".join(p.get_text() for p in fitz.open(proposal_dir / t["file"]))
    assert "—" not in blob
    for real in ("Chubb", "Travelers", "Liberty Mutual", "AIG", "Hartford", "Zurich", "Beazley",
                 "Coalition", "At-Bay", "Lloyd", "Markel", "Tokio"):
        assert real not in blob


def test_proposal_fields_extend_the_slice_1_fields():
    assert set(qg.FIELDS) <= set(pg.FIELDS) | set(pg.UNSCORED_FIELDS)
    assert not set(pg.PROPOSAL_ONLY_FIELDS) & set(qg.FIELDS)
    assert not set(pg.FIELDS) & set(pg.UNSCORED_FIELDS)
    assert set(pg.FIELDS) | set(pg.UNSCORED_FIELDS) == set(pg.SCHEMA_LABELS)


def _db_fact_field_literals() -> dict[str, str]:
    """key -> label of every FactField(...) literal in backend/db.py, read without importing it."""
    import ast

    out = {}
    tree = ast.parse((REPO_ROOT / "backend" / "db.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "FactField":
            kw = {k.arg: k.value.value for k in node.keywords if isinstance(k.value, ast.Constant)}
            out[kw["key"]] = kw["label"]
    return out


def test_schema_labels_match_backend_db_source():
    """Runs without backend deps: the mirrored labels are the FactField literals in db.py."""
    literals = _db_fact_field_literals()
    assert {k: literals.get(k) for k in pg.SCHEMA_LABELS} == pg.SCHEMA_LABELS


def test_proposal_fields_match_the_backend_proposal_schema():
    """The fail-closed contract with lane A: every scored proposal field is a
    FactField of the seeded proposal-builder workflow. Skipped until it exists."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from backend import db, models
        from sqlalchemy import select
    except Exception as exc:
        pytest.skip(f"backend not importable: {exc!r}")
    engine = db.make_engine("sqlite://")
    db.init_db(engine)
    with db.make_session_factory(engine)() as s:
        wf = s.scalar(select(models.Workflow).where(models.Workflow.slug == pg.WORKFLOW))
        schema = [dict(f) for f in wf.fact_schema] if wf is not None else None
    engine.dispose()
    if schema is None:
        pytest.skip("proposal-builder workflow not seeded yet (lane A)")
    keys = {f["key"] for f in schema}
    assert not set(pg.FIELDS) - keys, f"eval fields missing from the backend: {sorted(set(pg.FIELDS) - keys)}"
    extra = keys - set(pg.FIELDS) - set(pg.UNSCORED_FIELDS)
    assert not extra, f"backend fields the proposal truth does not list (fail-closed): {sorted(extra)}"
    # Labels too: every printed fact line uses its schema label.
    assert [(f["key"], f["label"]) for f in schema] == list(pg.SCHEMA_LABELS.items())


# ---------------------------------------------------------------------------
# Scorer: oracle and every hard-fail kind
# ---------------------------------------------------------------------------


def test_oracle_passes_the_proposal_gate(proposals, proposal_doc_truths, paths):
    scores = [_score(proposals, proposal_doc_truths, paths, pid) for pid in sorted(proposals)]
    m = g.aggregate_proposals(scores)
    rep = g.evaluate_proposal_gates(m)
    assert rep.passed, g.format_proposal_report(m, rep)
    assert m.cell_accuracy == 1.0 and m.hard_fails == [] and m.unlisted_fields == []
    assert m.prose_numbers_checked >= 7 and m.fact_accuracy == 1.0
    gated = {r.name for r in rep.results if r.gated}
    assert {"cell_accuracy[proposal]", "unlisted_fields[proposal] (fail-closed)"} <= gated


def test_cited_wrong_page_is_a_hard_fail(proposals, proposal_doc_truths, paths):
    """Deliberate failure: the right value cited to a page that does not hold it."""
    path = paths["prop_three_carrier_a"]

    def wrong_page(out):
        _cell(out, path, "eo_each_claim_limit")["citations"] = [{"document": path, "page": 3}]

    sc = _score(proposals, proposal_doc_truths, paths, "prop_three_carrier", wrong_page)
    assert [(h["field"], h["kind"]) for h in sc.hard_fails] == [("eo_each_claim_limit", "cited_wrong_page")]
    assert sc.cells_correct == sc.cells_present  # the value is right; only its source is wrong
    m = g.aggregate_proposals([sc])
    rep = g.evaluate_proposal_gates(m)
    assert not rep.passed
    assert [r.name for r in rep.failures] == [
        "proposal_hard_fails (uncited, wrong page, wrong value, prose number)"]
    assert "HARD FAIL prop_three_carrier_a.eo_each_claim_limit: cited_wrong_page" in g.format_proposal_report(m, rep)


def test_one_wrong_page_among_several_citations_is_a_hard_fail(proposals, proposal_doc_truths, paths):
    path = paths["prop_amended_f"]

    def extra(out):
        _cell(out, path, "cyber_social_engineering")["citations"].append({"document": path, "page": 2})

    sc = _score(proposals, proposal_doc_truths, paths, "prop_amended", extra)
    assert [h["kind"] for h in sc.hard_fails] == ["cited_wrong_page"]


def test_citation_into_another_document_is_a_hard_fail(proposals, proposal_doc_truths, paths):
    a, b = paths["prop_three_carrier_a"], paths["prop_three_carrier_b"]

    def cross(out):
        _cell(out, a, "named_insured")["citations"] = [{"document": b, "page": 1}]

    sc = _score(proposals, proposal_doc_truths, paths, "prop_three_carrier", cross)
    assert [(h["kind"], h["detail"]) for h in sc.hard_fails] == [
        ("cited_wrong_page", "citation points into another document")]


def test_uncited_value_is_a_hard_fail_even_in_an_unscored_row(proposals, proposal_doc_truths, paths):
    a = paths["prop_three_carrier_a"]

    def uncited(out):
        _cell(out, a, "total_premium")["citations"] = []
        out["proposal"]["cells"].append({"row": "subjectivities", "document": a, "column": "A",
                                         "value": "Signed and dated application", "state": "verified",
                                         "citations": []})

    sc = _score(proposals, proposal_doc_truths, paths, "prop_three_carrier", uncited)
    assert sorted((h["field"], h["kind"]) for h in sc.hard_fails) == [
        ("subjectivities", "uncited_value"), ("total_premium", "uncited_value")]
    assert sc.unlisted == []  # subjectivities is a listed unscored field


def test_unscored_row_with_a_proper_citation_passes(proposals, proposal_doc_truths, paths):
    a = paths["prop_three_carrier_a"]

    def add(out):
        out["proposal"]["cells"].append({"row": "subjectivities", "document": a, "column": "A",
                                         "value": "Signed and dated application", "state": "verified",
                                         "citations": [{"document": a, "page": 3}]})

    sc = _score(proposals, proposal_doc_truths, paths, "prop_three_carrier", add)
    assert sc.hard_fails == [] and sc.unlisted == []


def test_cell_with_no_state_but_a_value_counts_as_shown(proposals, proposal_doc_truths, paths):
    a = paths["prop_three_carrier_a"]

    def stateless(out):
        c = _cell(out, a, "territory")
        c["state"], c["citations"] = None, []

    sc = _score(proposals, proposal_doc_truths, paths, "prop_three_carrier", stateless)
    assert [h["kind"] for h in sc.hard_fails] == ["uncited_value"]


def test_value_in_a_null_row_is_a_hard_fail(proposals, proposal_doc_truths, paths):
    """A coverage the quote does not include, filled with another sublimit; and a rate
    the quote does not state."""
    e = paths["prop_two_carrier_absent_e"]

    def fill(out):
        c = _cell(out, e, "cyber_social_engineering")
        c.update(value="$250,000", state="verified", citations=[{"document": e, "page": 2}])
        c = _cell(out, e, "rate")
        c.update(value="$7,500", state="verified", citations=[{"document": e, "page": 2}])

    sc = _score(proposals, proposal_doc_truths, paths, "prop_two_carrier_absent", fill)
    assert sorted((h["field"], h["kind"]) for h in sc.hard_fails) == [
        ("cyber_social_engineering", "value_for_absent_field"), ("rate", "value_for_absent_field")]


def test_ambiguous_policy_wide_rows_are_unscored_but_checked(proposals, proposal_doc_truths, paths):
    """each_occurrence_limit and retention are unscored on cyber and E&O quotes (their
    descriptions fit a per-claim figure): a cited value there is not a hard fail, an
    uncited one is."""
    a = paths["prop_three_carrier_a"]

    def fill(cite):
        def mutate(out):
            out["proposal"]["cells"] += [
                {"row": "each_occurrence_limit", "document": a, "column": "A", "value": "$1,000,000",
                 "state": "verified", "citations": [{"document": a, "page": 2}] if cite else []},
                {"row": "retention", "document": a, "column": "A", "value": "$25,000",
                 "state": "verified", "citations": [{"document": a, "page": 2}]},
            ]
        return mutate

    sc = _score(proposals, proposal_doc_truths, paths, "prop_three_carrier", fill(True))
    assert sc.hard_fails == [] and sc.unlisted == []
    sc = _score(proposals, proposal_doc_truths, paths, "prop_three_carrier", fill(False))
    assert [(h["field"], h["kind"]) for h in sc.hard_fails] == [("each_occurrence_limit", "uncited_value")]


def test_superseded_value_is_a_hard_fail_with_a_decoy_hit(proposals, proposal_doc_truths, paths):
    f = paths["prop_amended_f"]

    def superseded(out):
        c = _cell(out, f, "cyber_social_engineering")
        c.update(value="$100,000", citations=[{"document": f, "page": 2}])

    sc = _score(proposals, proposal_doc_truths, paths, "prop_amended", superseded)
    assert [h["kind"] for h in sc.hard_fails] == ["wrong_value"]
    assert sc.decoy_hits == {"superseded": 1}
    assert sc.misses[0]["field"] == "cyber_social_engineering" and sc.misses[0]["reason"] == "wrong_value"


def test_prose_number_not_in_the_facts_is_a_hard_fail(proposals, proposal_doc_truths, paths):
    def invent(out):
        out["proposal"]["prose"].append({"section_id": "premium", "block_id": "premium-prose",
                                         "text": "Bundling would save about $4,200 a year.",
                                         "citations": []})

    sc = _score(proposals, proposal_doc_truths, paths, "prop_two_carrier_absent", invent)
    assert [(h["kind"], h["predicted"]) for h in sc.hard_fails] == [("prose_number_not_in_facts", "@n4200")]


def test_prose_may_restate_fact_numbers_in_other_forms(proposals, proposal_doc_truths, paths):
    def restate(out):
        out["proposal"]["prose"] += [
            {"section_id": "s", "block_id": "b", "citations": [],
             "text": "Section 3.1 compares 2 quotes for 2027: a $3 million aggregate, 25 percent minimum "
                     "earned premium, and cover from 01/01/2027."},
        ]

    sc = _score(proposals, proposal_doc_truths, paths, "prop_three_carrier", restate)
    assert sc.hard_fails == [], sc.hard_fails


def test_conflicting_values_may_be_stated_in_prose(proposals, proposal_doc_truths, paths):
    f = paths["prop_amended_f"]

    def conflict(out):
        c = _cell(out, f, "cyber_social_engineering")
        c.update(value=None, state="not_found", citations=[{"document": f, "page": 2},
                                                          {"document": f, "page": 4}],
                 conflicts=["$100,000", "$250,000"])
        out["proposal"]["prose"].append({"section_id": "s", "block_id": "b", "citations": [],
                                         "text": "The quote states both $100,000 and $250,000."})

    sc = _score(proposals, proposal_doc_truths, paths, "prop_amended", conflict)
    assert sc.hard_fails == []
    assert [m["reason"] for m in sc.misses] == ["not_shown"]


def test_checked_numbers():
    assert g.checked_numbers("Limits of $1,000,000 and 25% on 01/01/2027") == {
        "@n1000000", "@n25%", "@d20270101"}
    assert g.checked_numbers("Section 3.1, 2 quotes, in 2027, page 4") == set()
    assert g.checked_numbers("$2.5 million") == {"@n2500000"} == g.checked_numbers("2,500,000")
    assert g.checked_numbers("12500 dollars") == {"@n12500"}
    assert g.all_numbers("Page 4 of 5") == {"@n4", "@n5"}


# ---------------------------------------------------------------------------
# Fail-closed: unlisted fields and columns
# ---------------------------------------------------------------------------


def test_unlisted_cell_row_fails_closed(proposals, proposal_doc_truths, paths):
    a = paths["prop_three_carrier_a"]

    def add(out):
        out["proposal"]["cells"].append({"row": "policy_fee", "document": a, "column": "A",
                                         "value": None, "state": "not_found", "citations": []})

    sc = _score(proposals, proposal_doc_truths, paths, "prop_three_carrier", add)
    assert [(u["field"], u["where"]) for u in sc.unlisted] == [("policy_fee", "cell")]
    rep = g.evaluate_proposal_gates(g.aggregate_proposals([sc]))
    assert [r.name for r in rep.failures] == ["unlisted_fields[proposal] (fail-closed)"]


def test_unlisted_fact_field_fails_closed(proposals, proposal_doc_truths, paths):
    a = paths["prop_three_carrier_a"]

    def add(out):
        out["documents"][a]["facts"].append({"field": "breach_response_limit", "value": "$100,000",
                                             "page": 2, "quote": "q", "state": "verified"})

    sc = _score(proposals, proposal_doc_truths, paths, "prop_three_carrier", add)
    assert [(u["field"], u["where"]) for u in sc.unlisted] == [("breach_response_limit", "fact")]
    assert not g.evaluate_proposal_gates(g.aggregate_proposals([sc])).passed


def test_cell_in_an_unmapped_column_fails_closed(proposals, proposal_doc_truths, paths):
    def add(out):
        out["proposal"]["cells"].append({"row": "carrier_name", "document": None, "column": "Z",
                                         "value": "Someone", "state": "verified", "citations": []})

    sc = _score(proposals, proposal_doc_truths, paths, "prop_three_carrier", add)
    assert sc.unlisted and sc.unlisted[0]["where"] == "cell"
    assert [h["kind"] for h in sc.hard_fails] == ["uncited_value"]


def test_score_document_fail_closed_only_when_the_truth_says_so(proposal_doc_truths):
    t = proposal_doc_truths["prop_amended_g"]
    r = g.oracle_result(t)
    r.facts += [g.PredictedFact("policy_fee", None, None, None, "not_found"),
                g.PredictedFact("key_exclusions", "War", 3, "q", "verified")]
    sc = g.score_document(t, r)
    assert [u["field"] for u in sc.unlisted] == ["policy_fee"] and sc.hard_fails == 0
    m = g.aggregate([sc])
    rep = g.evaluate_gates(m, require_buckets=())
    assert "unlisted_fields (fail-closed fixtures)" in [x.name for x in rep.failures]
    assert "UNLISTED prop_amended_g.policy_fee" in g.format_report(m, rep)
    open_truth = {**t, "fail_closed": False}
    assert g.score_document(open_truth, r).unlisted == []


# ---------------------------------------------------------------------------
# Accuracy gate and no-data
# ---------------------------------------------------------------------------


def test_cell_accuracy_boundary(proposals, proposal_doc_truths, paths):
    p = proposals["prop_three_carrier"]
    present = sum(1 for f in p["documents"] for v in proposal_doc_truths[f]["facts"].values() if v)

    def acc_after_dropping(n):
        def drop(out):
            shown = [c for c in out["proposal"]["cells"] if c["value"] is not None][:n]
            for c in shown:
                c.update(value=None, state="not_found", citations=[])
        sc = _score(proposals, proposal_doc_truths, paths, "prop_three_carrier", drop)
        return g.evaluate_proposal_gates(g.aggregate_proposals([sc]))

    max_drop = int(present * (1 - g.PROPOSAL_CELL_ACCURACY_MIN))
    assert acc_after_dropping(max_drop).passed
    assert not acc_after_dropping(max_drop + 1).passed


def test_failed_document_scores_its_cells_missing(proposals, proposal_doc_truths, paths):
    e = paths["prop_two_carrier_absent_e"]

    def fail(out):
        out["documents"][e] = {"status": "failed", "failure_reason": "corrupt", "facts": []}
        out["proposal"]["cells"] = [c for c in out["proposal"]["cells"] if c["document"] != e]

    sc = _score(proposals, proposal_doc_truths, paths, "prop_two_carrier_absent", fail)
    assert sc.documents_failed == ["prop_two_carrier_absent_e"]
    assert {m["reason"] for m in sc.misses} == {"missing_row"}


def test_no_cells_is_not_a_pass(proposals, proposal_doc_truths, paths):
    sc = g.score_proposal(proposals["prop_amended"], proposal_doc_truths, {"input_tokens": 0},
                          {f: paths[f] for f in proposals["prop_amended"]["documents"]})
    rep = g.evaluate_proposal_gates(g.aggregate_proposals([sc]))
    assert not rep.passed and "cell_accuracy[proposal]" in [r.name for r in rep.failures]
    empty = g.evaluate_proposal_gates(g.aggregate_proposals([]))
    assert [r.note for r in empty.failures] == ["no data"]


# ---------------------------------------------------------------------------
# Suite runner: workflow selected by the fixture
# ---------------------------------------------------------------------------


def test_suite_calls_the_proposal_workflow_per_fixture(proposals, proposal_doc_truths, proposal_dir,
                                                       monkeypatch):
    monkeypatch.delenv("BROKERDOCS_EVAL_PROPOSALS", raising=False)
    by_path = {}
    calls = []

    def adapter(paths, *, workflow=None, parameters=None, template=None):
        calls.append({"n": len(paths), "workflow": workflow, "parameters": parameters, "template": template})
        pid = next(pid for pid, p in proposals.items()
                   if [str(proposal_dir / proposal_doc_truths[f]["file"]) for f in p["documents"]] == paths)
        by_path.update({f: str(proposal_dir / proposal_doc_truths[f]["file"]) for f in proposals[pid]["documents"]})
        return pg_out(proposals[pid], proposal_doc_truths, by_path, tokens=2000)

    m, rep = run_proposal_suite(adapter, proposal_dir, proposals, proposal_doc_truths)
    assert rep.passed, g.format_proposal_report(m, rep)
    assert [c["workflow"] for c in calls] == ["proposal-builder"] * 3
    assert sorted(c["n"] for c in calls) == [2, 2, 3]
    assert {c["template"] for c in calls} == {"standard", "premier"}
    assert all(set(c["parameters"]) == {"broker_name", "brokerage_name", "additional_context",
                                        "no_ai_prose"} for c in calls)
    assert m.max_run_input_tokens == 2000
    d = g.proposal_report_dict(m, rep)
    assert d["passed"] and len(d["metrics"]["proposals"]) == 3


def test_suite_subset_and_unknown_names(proposals, proposal_doc_truths, proposal_dir, monkeypatch):
    monkeypatch.setenv("BROKERDOCS_EVAL_PROPOSALS", "prop_amended")
    assert selected_proposals(sorted(proposals)) == ["prop_amended"]
    seen = []

    def adapter(paths, **kw):
        seen.append(paths)
        p = proposals["prop_amended"]
        return pg_out(p, proposal_doc_truths,
                      {f: str(proposal_dir / proposal_doc_truths[f]["file"]) for f in p["documents"]})

    run_proposal_suite(adapter, proposal_dir, proposals, proposal_doc_truths)
    assert len(seen) == 1
    monkeypatch.setenv("BROKERDOCS_EVAL_PROPOSALS", "nope")
    with pytest.raises(ValueError):
        selected_proposals(sorted(proposals))


def test_adapter_without_the_workflow_propagates_not_implemented(proposals, proposal_doc_truths, proposal_dir):
    def adapter(paths, **kw):
        raise NotImplementedError("workflow 'proposal-builder' is not seeded in this backend")

    with pytest.raises(NotImplementedError, match="not seeded"):
        run_proposal_suite(adapter, proposal_dir, proposals, proposal_doc_truths, ids=["prop_amended"])


def test_cli_writes_proposal_fixtures(tmp_path):
    assert pg.main(["--out", str(tmp_path)]) == 0
    m = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert len(m["proposals"]) == 3 and len(m["documents"]) == 7
    assert all(re.fullmatch(r"[0-9a-f]{64}", d["sha256"]) for d in m["documents"])


def test_oracle_output_is_deep_copied_per_call(proposals, proposal_doc_truths, paths):
    a = pg_out(proposals["prop_amended"], proposal_doc_truths, paths)
    b = copy.deepcopy(a)
    a["proposal"]["cells"][0]["value"] = "changed"
    assert pg_out(proposals["prop_amended"], proposal_doc_truths, paths) == b
