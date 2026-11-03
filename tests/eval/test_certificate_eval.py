"""Certificate eval (slice 4, C10): synthetic fixtures, claim ground truth, the gate and the
suite runner. Offline tests make no model call. The live gate is ``test_live_certificate_gates``:

    pytest -m live tests/eval/test_certificate_eval.py -k certificate

(0 hard fails, recall on Not supported claims >= 0.90; needs OPENAI_API_KEY and a certificate
branch in backend/eval_hook.py, otherwise it skips with the reason).

Tests that need lane A's backend (the ``cert:`` schema, ``backend.pipeline.coi_checks``) skip
with the missing import named, so this file is green before lane A merges and checks the
backend once it lands.
"""

from __future__ import annotations

import ast
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

import certificate_gate as cg
import certificate_generator as cgen
import eval_gates as g

REPO_ROOT = Path(__file__).resolve().parents[2]
CERT_DIR = cgen.FIXED_DIR


# ---------------------------------------------------------------------------
# Fixtures (module-local: tests/eval/conftest.py is shared and not changed by this lane)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cert_manifest() -> dict:
    return json.loads((CERT_DIR / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cert_cases(cert_manifest) -> dict[str, dict]:
    return {e["case_id"]: json.loads((CERT_DIR / e["truth"]).read_text(encoding="utf-8"))
            for e in cert_manifest["cases"]}


@pytest.fixture(scope="module")
def cert_docs(cert_manifest) -> dict[str, dict]:
    return {e["fixture_id"]: json.loads((CERT_DIR / e["truth"]).read_text(encoding="utf-8"))
            for e in cert_manifest["documents"]}


@pytest.fixture(scope="module")
def paths(cert_docs) -> dict[str, str]:
    return {fid: str(CERT_DIR / t["file"]) for fid, t in cert_docs.items()}


@pytest.fixture(scope="module")
def fresh(tmp_path_factory):
    out = tmp_path_factory.mktemp("certificates")
    return out, cgen.generate(out)


def _oracle(cases, docs, paths, cid, tokens=1000):
    case = cases[cid]
    return cg.certificate_oracle_output(case, docs, {f: paths[f] for f in case["documents"]}, input_tokens=tokens)


def _score(cases, docs, paths, cid, mutate=None):
    out = _oracle(cases, docs, paths, cid)
    if mutate:
        mutate(out)
    return cg.score_certificate_case(cases[cid], docs, out, {f: paths[f] for f in cases[cid]["documents"]})


def _gate(*scores):
    m = cg.aggregate_certificate(list(scores))
    return m, cg.evaluate_certificate_gates(m)


def _all(cases, docs, paths, mutate_by_case=None):
    mutate_by_case = mutate_by_case or {}
    return _gate(*[_score(cases, docs, paths, cid, mutate_by_case.get(cid)) for cid in sorted(cases)])


def _claim(out, key):
    return next(c for c in out["certificate"]["claims"] if c["key"] == key)


def _not_supported_params():
    out = []
    for case in cgen.CASES:
        for c in case.claims:
            if c.status == cg.NOT_SUPPORTED:
                out.append(pytest.param(case.case_id, c.key, id=f"{case.case_id}:{c.key}"))
    return out


# ---------------------------------------------------------------------------
# Fixtures and ground truth
# ---------------------------------------------------------------------------


def test_catalogue(cert_cases, cert_docs, cert_manifest):
    assert sorted(cert_cases) == ["cert_contractor", "cert_scanned", "cert_traps", "cert_written_contract"]
    assert sorted(f for c in cert_cases.values() for f in c["documents"]) == sorted(cert_docs)
    for c in cert_cases.values():
        assert c["workflow"] == cg.CERTIFICATE_SLUG and c["gate_bucket"] == "certificate"
        assert 1 <= len(c["documents"]) <= 8  # one slot, 1 to 8 PDFs (spec section 3)
        assert set(c["parameters"]) == set(cg.PARAMETER_KEYS)
        assert set(c["parameters"]["wording"]) == set(cg.WORDINGS)
        assert c["synthetic"] is True
    assert cert_manifest["seed"] == cgen.DEFAULT_SEED and cert_manifest["synthetic"] is True
    assert sum(len(c["claims"]) for c in cert_cases.values()) == 32
    assert sum(1 for c in cert_cases.values() for x in c["claims"] if x["status"] == cg.NOT_SUPPORTED) == 17


def test_section_6_fixture_list_is_covered(cert_cases, cert_docs):
    """Spec section 6: GL, auto, umbrella, WC declarations; AI scheduled and blanket, waiver,
    primary non-contributory; traps: expired policy, AI endorsement for another policy number,
    one scanned document, one package policy. O1/D14: a written-contract blanket form."""
    lines = {t["line"] for t in cert_docs.values() if t["doc_kind"] == "declarations"}
    assert lines == {"general_liability", "auto", "umbrella", "workers_comp"}
    types = {f["value"] for t in cert_docs.values() for k, f in t["facts"].items()
             if f and k.endswith(":type")}
    assert types == set(cg.ENDORSEMENT_TYPES)
    assert sum(1 for t in cert_docs.values() if t["layout"] == "scanned") == 1
    assert sum(1 for t in cert_docs.values() if t["doc_kind"] == "package") == 1
    reasons = {(c["reason"]) for case in cert_cases.values() for c in case["claims"]}
    assert {"expired", "not_yet_effective", "conditional", "scheduled_other_party", "insured_mismatch",
            "unverified_dates", "unverified_insured", "sign_off_required", "no_endorsement",
            "scheduled_names_holder", "blanket_form_cited", "effective_day", "expiration_day"} <= reasons
    notices = [(n["kind"], n["fixture_id"]) for case in cert_cases.values() for n in case["notices"]]
    assert sorted(notices) == [("endorsement_unmatched", "cert_traps_ai_other_policy"), ("package", "cert_traps_package")]
    written = [f for t in cert_docs.values() for k, f in t["facts"].items() if f and k.endswith(":condition")]
    assert len(written) == 2 and all("written contract" in f["value"] for f in written)
    exercised = {x for c in cert_cases.values() for x in c["exercises"]}
    assert {"written_contract_blanket_ai", "conditional_waiver", "package_policy", "scanned_document",
            "expired_policy", "ai_endorsement_other_policy_number"} <= exercised


def test_truth_verdicts_follow_the_spec_rules(cert_cases, cert_docs):
    """The hand-written verdicts equal the spec's rules re-derived from the document truths
    (section 3 Checks, D7 join, D11 description, D14 conditions and per-line claims)."""
    for cid, case in cert_cases.items():
        ref = {(c["key"], c["status"], c["reason"]) for c in cg.reference_claims(case, cert_docs)}
        hand = {(c["key"], c["status"], c["reason"]) for c in case["claims"]}
        assert hand == ref, (cid, sorted(hand ^ ref))
        assert len({c["key"] for c in case["claims"]}) == len(case["claims"]), cid
        assert case["notices"] == cg.reference_notices(case, cert_docs), cid


def test_claim_keys_use_the_vocabulary(cert_cases):
    for case in cert_cases.values():
        policies = {cg.normalize_policy_number(p["policy_number"]) for p in case["shown_policies"]}
        for c in case["claims"]:
            k = c["key"]
            assert cg.normalize_claim_key(k) == k.lower() or k.startswith("in_force:"), k
            if c["kind"] == "wording":
                assert k == cg.claim_wording(c["wording"], c["line"])
                assert case["parameters"]["wording"][c["wording"]]
                assert c["line"] in cg.WORDING_LINES[c["wording"]]
            elif c["kind"] == "in_force":
                assert k == cg.claim_in_force(c["policy_number"]) and k.split(":", 1)[1] in policies
            else:
                assert k in (cg.CLAIM_INSURED, cg.CLAIM_DESCRIPTION)


def test_one_wording_claim_per_requested_wording_per_printed_line(cert_cases):
    """D14: AI on GL and on auto are two rows; a wording not requested makes no row."""
    for case in cert_cases.values():
        want = {cg.claim_wording(w, p["line"]) for w, on in case["parameters"]["wording"].items() if on
                for p in case["shown_policies"] if p["line"] in cg.WORDING_LINES[w]}
        got = {c["key"] for c in case["claims"] if c["kind"] == "wording"}
        assert got == want, case["case_id"]
        assert {c["key"] for c in case["claims"] if c["kind"] == "in_force"} == {
            cg.claim_in_force(p["policy_number"]) for p in case["shown_policies"]}


def test_supported_claims_carry_evidence_pages(cert_cases, cert_docs):
    for case in cert_cases.values():
        for c in case["claims"]:
            if c["status"] == cg.NOT_SUPPORTED:
                assert c["evidence"] == [], c["key"]
                continue
            assert c["evidence"], c["key"]
            for e in c["evidence"]:
                fact = cert_docs[e["fixture_id"]]["facts"][e["key"]]
                assert fact["source"] == "text" and e["page"] in fact["pages"], (c["key"], e)


def test_traps_in_the_documents(cert_cases, cert_docs, paths):
    traps = cert_cases["cert_traps"]
    on = cg.parse_date(traps["parameters"]["certificate_date"])
    gl = cert_docs["cert_traps_gl_expired"]["facts"]
    assert (on - cg.parse_date(gl["cert:expiration_date"]["value"])).days == 1  # expired the day before
    auto = cert_docs["cert_traps_auto_other_insured"]["facts"]
    end = cert_docs["cert_traps_ai_other_policy"]["facts"]
    shown = cg.normalize_policy_number(auto["cert:policy_number"]["value"])
    named = cg.normalize_policy_number(end["cert:endorsement:OM2049:policy_number"]["value"])
    assert shown != named and sorted(shown) == sorted(named)  # same digits, two transposed
    # The unmatched endorsement schedules the holder: only the join keeps it from supporting AI.
    assert (cg.normalize_name(end["cert:endorsement:OM2049:named_party"]["value"])
            == cg.normalize_name(traps["parameters"]["holder_name"]))
    assert cg.normalize_name(auto["cert:named_insured"]["value"]) != cg.normalize_name(traps["parameters"]["client_name"])
    wc = cert_cases["cert_written_contract"]
    auto2 = cert_docs["cert_written_contract_auto"]["facts"]
    assert (cg.parse_date(auto2["cert:effective_date"]["value"]) - cg.parse_date(wc["parameters"]["certificate_date"])).days == 1
    c1 = cert_cases["cert_contractor"]["parameters"]["certificate_date"]
    assert cert_docs["cert_contractor_auto"]["facts"]["cert:effective_date"]["value"] == "11/03/2026" == \
        "{1}/{2}/{0}".format(*c1.split("-"))
    assert cert_docs["cert_contractor_wc"]["facts"]["cert:expiration_date"]["value"] == "11/03/2026"


def test_package_policy_contributes_no_facts_but_prints_several_lines(cert_docs, paths):
    t = cert_docs["cert_traps_package"]
    assert t["doc_kind"] == "package" and all(f is None for f in t["facts"].values())
    parts = {u["part"] for u in t["printed_unused"] if "part" in u}
    assert parts == {"Commercial General Liability Coverage Part", "Commercial Property Coverage Part"}
    text = " ".join(p.get_text() for p in fitz.open(paths["cert_traps_package"]))
    for u in t["printed_unused"]:
        assert u["value"] in text


def test_policy_number_join_is_normalized(cert_docs):
    """D7 table: dashes, spaces, dots, slashes and case do not matter; digits do."""
    for a, b in [("GL-4410-2281", "GL 4410 2281"), ("GL-4410-2281", "gl4410-2281"), ("WC 88-310-57", "WC8831057"),
                 ("FHA GL 5520117", "FHA-GL-5520117"), ("QLI/GL.207731", "QLI-GL-207731")]:
        assert cg.normalize_policy_number(a) == cg.normalize_policy_number(b), (a, b)
    assert cg.normalize_policy_number("CA 7719-0024") != cg.normalize_policy_number("CA 7719-0042")
    # The fixtures print the same policy number differently across documents.
    gl = cert_docs["cert_contractor_gl"]["facts"]["cert:policy_number"]["printed"]
    ai = cert_docs["cert_contractor_ai_scheduled"]["facts"]["cert:endorsement:OM2026:policy_number"]["printed"]
    assert gl != ai and cg.normalize_policy_number(gl) == cg.normalize_policy_number(ai)


def test_parse_date_forms():
    from datetime import date
    assert cg.parse_date("11/03/2026") == cg.parse_date("2026-11-03") == cg.parse_date("November 3, 2026") \
        == date(2026, 11, 3)
    assert cg.parse_date("January 1, 2027") == date(2027, 1, 1)
    assert cg.parse_date("next year") is None and cg.parse_date(None) is None


def test_same_seed_same_bytes(fresh, tmp_path):
    _, m1 = fresh
    m2 = cgen.generate(tmp_path)
    assert [d["sha256"] for d in m1["documents"]] == [d["sha256"] for d in m2["documents"]]


def test_committed_fixtures_match_generator(fresh, cert_manifest):
    out, m = fresh
    assert cert_manifest["generator_version"] == cgen.GENERATOR_VERSION
    same_lib = cert_manifest["pymupdf_version"] == fitz.VersionBind
    assert [d["fixture_id"] for d in cert_manifest["documents"]] == [d["fixture_id"] for d in m["documents"]]
    for e, f in zip(cert_manifest["documents"], m["documents"]):
        committed = (CERT_DIR / e["file"]).read_bytes()
        assert hashlib.sha256(committed).hexdigest() == e["sha256"], e["fixture_id"]
        if same_lib:
            assert e["sha256"] == f["sha256"], f"{e['fixture_id']}: regenerate certificate fixtures"
        tc = json.loads((CERT_DIR / e["truth"]).read_text(encoding="utf-8"))
        tf = json.loads((out / f["truth"]).read_text(encoding="utf-8"))
        tc.pop("size_bytes"), tf.pop("size_bytes")
        assert tc == tf, e["fixture_id"]
    for c in cert_manifest["cases"]:
        assert (json.loads((CERT_DIR / c["truth"]).read_text(encoding="utf-8"))
                == json.loads((out / c["truth"]).read_text(encoding="utf-8")))
    assert cert_manifest["cert_fields"] == cg.CERT_FIELDS


def test_truth_lists_every_scalar_key_and_only_known_keys(cert_docs):
    for fid, t in cert_docs.items():
        scalar = {k for k in t["facts"] if not k.startswith(cg.ENDORSEMENT_PREFIX)}
        assert scalar == set(cg.CERT_SCALAR_KEYS), fid
        for k in t["facts"]:
            assert k in cg.CERT_FIELDS or re.fullmatch(r"cert:endorsement:[0-9A-Z]+:[a-z_]+", k), (fid, k)
            assert k in cg.CERT_FIELDS or cg.split_endorsement_key(k), (fid, k)
        if t["doc_kind"] == "declarations":
            for k in cg.POLICY_FACT_KEYS:
                assert t["facts"][k] is not None, (fid, k)
            assert t["facts"]["cert:line"]["value"] == t["line"] in cg.LINES
            limits = {k for k, f in t["facts"].items() if f and k.startswith(("cert:gl_", "cert:auto_",
                                                                              "cert:umbrella_", "cert:wc_"))}
            assert limits == set(cgen.LIMIT_KEYS[t["line"]]), fid


def test_fact_values_sit_on_their_truth_pages(cert_docs, paths):
    for fid, t in cert_docs.items():
        doc = fitz.open(paths[fid])
        assert doc.page_count == t["page_count"], fid
        if t["layout"] == "scanned":
            assert all(not p.get_text().strip() and p.get_images() for p in doc), fid
            assert t["expect"]["ocr_pages"] == list(range(1, doc.page_count + 1))
            assert all(f["source"] == "image" for f in t["facts"].values() if f)
            continue
        texts = [p.get_text() for p in doc]
        for key, fact in t["facts"].items():
            if fact is None:
                continue
            assert fact["source"] == "text"
            assert fact["printed"] in fact["quote"], (fid, key)
            assert fact["pages"] == [i for i, tx in enumerate(texts, 1) if fact["printed"] in tx], (fid, key)
            assert fact["page"] in fact["pages"] and fact["quote"] in texts[fact["page"] - 1], (fid, key)
            if fact["value"] != fact["printed"]:
                assert fact["printed"] in fact["accepted_values"], (fid, key)


def test_endorsement_facts_per_form(cert_docs):
    for fid, t in cert_docs.items():
        forms = {}
        for k, f in t["facts"].items():
            parts = cg.split_endorsement_key(k)
            if parts:
                forms.setdefault(parts[0], {})[parts[1]] = f
        for form, attrs in forms.items():
            assert set(attrs) == set(cg.ENDORSEMENT_ATTRS), (fid, form)
            for a in ("form_number", "type", "policy_number"):
                assert attrs[a] is not None, (fid, form, a)
            assert cg.normalize_form(attrs["form_number"]["value"]).startswith(form)
            assert attrs["type"]["value"] in cg.ENDORSEMENT_TYPES
            scheduled = attrs["type"]["value"] == "additional_insured_scheduled"
            assert (attrs["named_party"] is not None) == scheduled, (fid, form)
    gl4 = cert_docs["cert_scanned_gl_with_pnc"]
    assert gl4["doc_kind"] == "declarations" and gl4["facts"]["cert:endorsement:OM2002:type"]["page"] == 3


def test_every_page_is_labelled_synthetic(cert_docs, paths):
    for fid in cert_docs:
        doc = fitz.open(paths[fid])
        assert doc.metadata["author"] == "synthetic" and "synthetic" in doc.metadata["subject"]
        for page in doc:
            text = page.get_text()
            if text.strip():
                assert cgen.SYNTHETIC_FOOTER in text.replace("\n", " "), (fid, page.number)
        if cert_docs[fid]["layout"] == "scanned":
            for spec in cgen.build_fixture(next(c for c in cgen.CASES if c.case_id == cert_docs[fid]["case_id"]),
                                           next(d for c in cgen.CASES for d in c.documents
                                                if cgen.doc_fixture_id(c.case_id, d.name) == fid))[0].pages:
                assert any(r.text == cgen.SYNTHETIC_FOOTER for r in spec.runs)  # rasterized into the scan


def test_no_em_dashes_and_no_real_carriers(cert_docs, cert_cases, paths):
    blob = json.dumps([cert_docs, cert_cases], ensure_ascii=False)
    for fid in cert_docs:
        blob += " ".join(p.get_text() for p in fitz.open(paths[fid]))
    assert "—" not in blob
    for real in ("Chubb", "Travelers", "Liberty Mutual", "Allstate", "State Farm", "Nationwide", "Hartford",
                 "Erie", "Progressive", "USAA", "Farmers", "Zurich", "AIG", "Markel", "CNA", "Cincinnati",
                 "ACORD", "ISO Properties"):
        assert real not in blob, real


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def test_oracle_passes_the_gate(cert_cases, cert_docs, paths):
    m, rep = _all(cert_cases, cert_docs, paths)
    assert rep.passed, cg.format_certificate_report(m, rep)
    assert m.recall == 1.0 and m.claim_accuracy == 1.0 and m.supported_accuracy == 1.0
    assert m.hard_fails == [] and m.misses == [] and m.false_alarms == [] and m.unscored_claims == []
    assert m.not_supported == 17 and m.truth_claims == 32
    assert m.notices_found == m.notices_expected == 2 and m.package_facts_shown == []
    assert m.citation_issues == [] and m.fact_accuracy == 1.0
    gated = {r.name for r in rep.results if r.gated}
    assert {cg.GATE_HARD, cg.GATE_RECALL, cg.GATE_EMPTY} <= gated
    fields = {r.name for r in rep.results if r.name.startswith("field_accuracy[")}
    assert "field_accuracy[cert:effective_date]" in fields
    assert "field_accuracy[cert:endorsement:*:condition]" in fields
    assert all(not r.gated for r in rep.results if r.name.startswith("field_accuracy["))


@pytest.mark.parametrize("cid,key", _not_supported_params())
def test_every_not_supported_claim_reported_supported_is_a_hard_fail(cert_cases, cert_docs, paths, cid, key):
    def flip(out):
        _claim(out, key)["status"] = cg.SUPPORTED

    sc = _score(cert_cases, cert_docs, paths, cid, flip)
    assert [h["key"] for h in sc.hard_fails] == [key]
    m, rep = _gate(sc)
    assert not rep.passed and cg.GATE_HARD in [r.name for r in rep.failures]
    assert f"HARD FAIL {cid}.{key}" in cg.format_certificate_report(m, rep)


def test_written_contract_reported_supported_fails_the_gate(cert_cases, cert_docs, paths):
    """O1/D14: the blanket AI applies only where a written contract requires it; reporting it
    Supported certifies conditional coverage as unconditional."""
    def trust_blanket(out):
        _claim(out, "wording:additional_insured:general_liability")["status"] = cg.SUPPORTED

    m, rep = _all(cert_cases, cert_docs, paths, {"cert_written_contract": trust_blanket})
    assert not rep.passed and len(m.hard_fails) == 1
    assert m.hard_fails[0]["truth_reason"] == "conditional" and "written contract" in m.hard_fails[0]["detail"]


def test_empty_result_fails_the_gate(cert_cases, cert_docs, paths):
    def empty(out):
        out["certificate"]["claims"] = []

    m, rep = _all(cert_cases, cert_docs, paths, {cid: empty for cid in cert_cases})
    assert m.hard_fails == [] and not rep.passed
    names = [r.name for r in rep.failures]
    assert cg.GATE_EMPTY in names and cg.GATE_RECALL in names
    assert m.recall == 0.0 and sorted(m.empty_cases) == sorted(cert_cases)
    # One empty case among good ones fails even if recall alone would pass.
    m, rep = _all(cert_cases, cert_docs, paths, {"cert_scanned": empty})
    assert m.recall == 14 / 17 and cg.GATE_EMPTY in [r.name for r in rep.failures]


def test_no_cases_is_no_pass():
    rep = cg.evaluate_certificate_gates(cg.aggregate_certificate([]))
    assert not rep.passed and {r.note for r in rep.failures} == {"no data"}


def test_recall_boundary(cert_cases, cert_docs, paths):
    def drop(keys):
        def mutate(out):
            out["certificate"]["claims"] = [c for c in out["certificate"]["claims"] if c["key"] not in keys]
        return mutate

    one = {"cert_traps": drop({"insured_consistent"})}
    m, rep = _all(cert_cases, cert_docs, paths, one)
    assert m.recall == 16 / 17 and rep.passed, cg.format_certificate_report(m, rep)
    assert m.misses[0]["reason"] == "no claim" and m.hard_fails == []
    two = {"cert_traps": drop({"insured_consistent"}), "cert_contractor": drop({"description_reviewed"})}
    m, rep = _all(cert_cases, cert_docs, paths, two)
    assert m.recall == 15 / 17 < cg.CERT_RECALL_MIN
    assert [r.name for r in rep.failures] == [cg.GATE_RECALL]


def test_unknown_status_is_a_miss_not_a_pass(cert_cases, cert_docs, paths):
    def odd(out):
        _claim(out, "in_force:FHAGL5520117")["status"] = "open"

    sc = _score(cert_cases, cert_docs, paths, "cert_traps", odd)
    assert sc.hard_fails == [] and sc.misses == [{"case_id": "cert_traps", "key": "in_force:FHAGL5520117",
                                                  "truth_reason": "expired", "reason": "status 'open'"}]


def test_false_alarm_lowers_accuracy_only(cert_cases, cert_docs, paths):
    def alarm(out):
        _claim(out, "in_force:WC8831057")["status"] = cg.NOT_SUPPORTED  # expiration-day boundary misread

    m, rep = _all(cert_cases, cert_docs, paths, {"cert_contractor": alarm})
    assert rep.passed and m.hard_fails == []
    assert [f["key"] for f in m.false_alarms] == ["in_force:WC8831057"]
    assert m.supported_accuracy == 14 / 15 and m.claim_accuracy == 31 / 32


def test_claim_keys_are_matched_after_normalization(cert_cases, cert_docs, paths):
    def respell(out):
        _claim(out, "in_force:GL44102281")["key"] = "in_force:GL-4410-2281"
        _claim(out, "insured_consistent")["key"] = "Insured_Consistent"

    sc = _score(cert_cases, cert_docs, paths, "cert_contractor", respell)
    assert sc.hard_fails == [] and sc.misses == [] and sc.false_alarms == [] and sc.unscored_claims == []


def test_extra_claims_are_reported_unscored(cert_cases, cert_docs, paths):
    def extra(out):
        out["certificate"]["claims"].append({"key": "wording:additional_insured:workers_comp", "status": "supported",
                                             "reason": "x", "citations": []})

    m, rep = _all(cert_cases, cert_docs, paths, {"cert_traps": extra})
    assert rep.passed and [u["key"] for u in m.unscored_claims] == ["wording:additional_insured:workers_comp"]
    assert "UNSCORED cert_traps.wording:additional_insured:workers_comp" in cg.format_certificate_report(m, rep)


def test_supported_claim_citation_issues_are_reported(cert_cases, cert_docs, paths):
    def cites(out):
        _claim(out, "insured_consistent")["citations"] = []
        _claim(out, "in_force:GL44102281")["citations"] = [{"document": "elsewhere.pdf", "page": 1}]

    m, rep = _all(cert_cases, cert_docs, paths, {"cert_contractor": cites})
    assert rep.passed and sorted(c["issue"] for c in m.citation_issues) == [
        "citation outside the run's documents", "supported with no citation"]


def test_package_facts_and_missing_notices_are_reported(cert_cases, cert_docs, paths):
    p = paths["cert_traps_package"]

    def leak(out):
        out["documents"][p]["facts"] = [{"field": "cert:gl_each_occurrence", "value": "$1,000,000", "page": 2,
                                         "quote": "Each Occurrence Limit: $1,000,000", "state": "verified"}]
        out["certificate"]["notices"] = []

    m, rep = _all(cert_cases, cert_docs, paths, {"cert_traps": leak})
    assert rep.passed  # reported, not gated: claims decide the gate
    assert m.package_facts_shown == [{"case_id": "cert_traps", "fixture_id": "cert_traps_package",
                                      "field": "cert:gl_each_occurrence", "value": "$1,000,000"}]
    assert m.notices_found == 0 and len(m.notices_missing) == 2


def test_notice_matching_by_form_or_document(cert_cases, cert_docs, paths):
    def other_words(out):
        out["certificate"]["notices"] = [
            {"kind": "unmatched", "document": None, "message": "Form OM 20 49 modifies a policy not shown."},
            {"kind": "x", "document": paths["cert_traps_package"], "message": "Package policy"}]

    sc = _score(cert_cases, cert_docs, paths, "cert_traps", other_words)
    assert sc.notices_found == 2


def test_field_accuracy_is_per_field_and_reported(cert_cases, cert_docs, paths):
    p = paths["cert_contractor_gl"]

    def misread(out):
        for f in out["documents"][p]["facts"]:
            if f["field"] == "cert:gl_medical_expense":
                f["value"] = "$15,000"

    m, rep = _all(cert_cases, cert_docs, paths, {"cert_contractor": misread})
    assert rep.passed
    fa = m.field_accuracy["cert:gl_medical_expense"]
    assert fa["present"] == 4 and fa["correct"] == 3  # four GL declarations
    assert m.field_accuracy["cert:line"]["accuracy"] == 1.0


def test_line_and_type_accept_printed_or_canonical_values(cert_docs):
    t = cert_docs["cert_contractor_gl"]["facts"]["cert:line"]
    assert g.values_match("general_liability", t["value"], t["accepted_values"])
    assert g.values_match("Commercial General Liability", t["value"], t["accepted_values"])
    f = cert_docs["cert_contractor_ai_scheduled"]["facts"]["cert:endorsement:OM2026:form_number"]
    assert g.values_match("OM 20 26", f["value"], f["accepted_values"])


def test_failed_document_is_reported(cert_cases, cert_docs, paths):
    p = paths["cert_scanned_umbrella_scanned"]

    def failed(out):
        out["documents"][p] = {"status": "failed", "failure_reason": "corrupt", "facts": []}

    m, rep = _all(cert_cases, cert_docs, paths, {"cert_scanned": failed})
    assert m.documents_failed == ["cert_scanned_umbrella_scanned"] and rep.passed


def test_oracle_output_is_fresh_per_call(cert_cases, cert_docs, paths):
    a = _oracle(cert_cases, cert_docs, paths, "cert_traps")
    b = copy.deepcopy(a)
    a["certificate"]["claims"][0]["status"] = "changed"
    assert _oracle(cert_cases, cert_docs, paths, "cert_traps") == b


# ---------------------------------------------------------------------------
# Suite runner and CLI
# ---------------------------------------------------------------------------


def test_suite_calls_the_certificate_workflow_with_parameters(cert_cases, cert_docs, monkeypatch):
    monkeypatch.delenv(cg.CERT_CASES_ENV, raising=False)
    calls = []
    all_paths = {fid: str(CERT_DIR / t["file"]) for fid, t in cert_docs.items()}

    def adapter(paths, *, workflow=None, parameters=None):
        calls.append({"paths": paths, "workflow": workflow, "parameters": parameters})
        cid = next(c for c, case in cert_cases.items() if [all_paths[f] for f in case["documents"]] == paths)
        return cg.certificate_oracle_output(cert_cases[cid], cert_docs, all_paths, input_tokens=4000)

    m, rep = cg.run_certificate_suite(adapter, CERT_DIR, cert_cases, cert_docs)
    assert rep.passed, cg.format_certificate_report(m, rep)
    assert [c["workflow"] for c in calls] == ["certificate"] * 4
    for c in calls:
        assert set(c["parameters"]) == set(cg.PARAMETER_KEYS)
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", c["parameters"]["certificate_date"])
        assert 1 <= len(c["paths"]) <= 8
    assert m.max_run_input_tokens == 4000
    d = cg.certificate_report_dict(m, rep)
    assert d["passed"] and len(d["metrics"]["cases"]) == 4
    json.dumps(d, default=str)


def test_suite_subset_and_unknown_names(cert_cases, cert_docs, monkeypatch):
    monkeypatch.setenv(cg.CERT_CASES_ENV, "cert_scanned")
    assert cg.selected_certificate_cases(sorted(cert_cases)) == ["cert_scanned"]
    seen = []
    all_paths = {fid: str(CERT_DIR / t["file"]) for fid, t in cert_docs.items()}

    def adapter(paths, **kw):
        seen.append(paths)
        return cg.certificate_oracle_output(cert_cases["cert_scanned"], cert_docs, all_paths)

    cg.run_certificate_suite(adapter, CERT_DIR, cert_cases, cert_docs)
    assert len(seen) == 1
    monkeypatch.setenv(cg.CERT_CASES_ENV, "nope")
    with pytest.raises(ValueError):
        cg.selected_certificate_cases(sorted(cert_cases))


def test_adapter_without_the_workflow_propagates(cert_cases, cert_docs):
    def adapter(paths, **kw):
        raise NotImplementedError("workflow 'certificate' is not seeded in this backend")

    with pytest.raises(NotImplementedError, match="not seeded"):
        cg.run_certificate_suite(adapter, CERT_DIR, cert_cases, cert_docs, ids=["cert_traps"])


def test_cli_writes_certificate_fixtures(tmp_path):
    assert cgen.main(["--out", str(tmp_path)]) == 0
    m = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert len(m["cases"]) == 4 and len(m["documents"]) == 19
    assert all(re.fullmatch(r"[0-9a-f]{64}", d["sha256"]) for d in m["documents"])


# ---------------------------------------------------------------------------
# Lane A reconciliation (skip until the backend has the certificate code)
# ---------------------------------------------------------------------------


def _import_backend(module: str):
    """Import a backend module, or skip naming it (lane A builds it in parallel)."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        return importlib.import_module(module)
    except Exception as exc:  # module missing, or backend deps (SQLAlchemy, pydantic) not installed
        pytest.skip(f"missing import {module} (lane A, slice 4 C3/C4): {exc!r}")


def _schemas_cert_literals() -> set[str]:
    """Scalar ``cert:`` key literals in backend/schemas.py, read without importing it."""
    src = REPO_ROOT / "backend" / "schemas.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and re.fullmatch(r"cert:[a-z][a-z0-9_]*", n.value)}


def test_cert_field_keys_match_backend_schema():
    """CERT_FIELDS (the assumed vocabulary) must equal lane A's ``cert:`` schema keys."""
    found = _schemas_cert_literals()
    if not found:
        _import_backend("backend.pipeline.coi_checks")  # skips naming the missing module
        pytest.skip("missing import: backend/schemas.py defines no cert: keys (lane A, slice 4 C3)")
    assert found == set(cg.CERT_SCALAR_KEYS), (
        f"reconcile certificate_gate.CERT_FIELDS: eval only {sorted(set(cg.CERT_SCALAR_KEYS) - found)}, "
        f"backend only {sorted(found - set(cg.CERT_SCALAR_KEYS))}")


def test_backend_vocabulary_matches_the_gate():
    """Slug, endorsement types, wording names and claim keys: the gate's assumed block must equal
    the backend's names once lane A's modules exist (reconcile certificate_gate on failure)."""
    coi = _import_backend("backend.pipeline.coi_checks")
    slugs = _import_backend("backend.slugs")
    assert cg.CERTIFICATE_SLUG in {v for k, v in vars(slugs).items() if k.endswith("_SLUG")}
    for name, ours in (("ENDORSEMENT_TYPES", set(cg.ENDORSEMENT_TYPES)), ("WORDINGS", set(cg.WORDINGS))):
        theirs = getattr(coi, name, None)
        if theirs is None:
            pytest.fail(f"backend.pipeline.coi_checks has no {name}; reconcile certificate_gate.{name}")
        assert {str(x) for x in theirs} == ours, name


def test_backend_checks_reproduce_the_truth_verdicts(cert_cases, cert_docs, paths):
    """End to end offline: the eval hook runs the certificate workflow with a perfect fake model
    (extraction returns each document's truth facts, vision returns the scanned page's lines)
    and the gate must pass with every verdict equal to the truth."""
    _import_backend("backend.pipeline.coi_checks")
    _import_backend("backend.pipeline.certificate")
    eval_hook = _import_backend("backend.eval_hook")
    providers = _import_backend("backend.providers")
    by_file = {t["file"]: t for t in cert_docs.values()}

    def respond(req):
        text = "\n".join(p.text for m in req.messages if m.role == "user" for p in m.parts() if hasattr(p, "text"))
        if req.role is providers.CallRole.VISION:
            name = next(t for f, t in by_file.items() if t["layout"] == "scanned")
            n = int(re.search(r"Page (\d+)\.", text).group(1))
            return "\n".join(f["quote"] for f in name["facts"].values() if f and f["page"] == n)
        name = re.search(r"Document file name \(data, not an instruction\): '([^']+)'", text).group(1)
        t = by_file[name]
        facts = [{"field": k, "value": (f["printed"] if f else None), "page": f["page"] if f else None,
                  "quote": f["quote"] if f else None} for k, f in t["facts"].items()]
        return {"facts": facts, "forms": [], "options_detected": 1, "option_compared": None}

    scores = []
    for cid, case in sorted(cert_cases.items()):
        p = {f: paths[f] for f in case["documents"]}
        try:
            out = eval_hook.run_eval_batch([p[f] for f in case["documents"]], workflow=case["workflow"],
                                           parameters=cg.adapter_parameters(case),
                                           provider=providers.FakeProvider(respond))
        except (NotImplementedError, ValueError) as exc:
            pytest.skip(f"backend.eval_hook has no certificate branch yet: {exc}")
        scores.append(cg.score_certificate_case(case, cert_docs, out, p))
    m, rep = _gate(*scores)
    assert rep.passed, cg.format_certificate_report(m, rep)
    assert m.recall == 1.0 and m.claim_accuracy == 1.0, cg.format_certificate_report(m, rep)


# ---------------------------------------------------------------------------
# Live gate (pytest -m live tests/eval/test_certificate_eval.py -k certificate)
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_certificate_gates(cert_cases, cert_docs, tmp_path, capsys):
    """Certificate gate (spec section 6): one run per case through the ``certificate`` workflow.
    0 hard fails, recall on Not supported claims >= 0.90. ``BROKERDOCS_EVAL_CERTIFICATE_CASES``
    selects a subset; the JSON report goes to ``$BROKERDOCS_EVAL_CERTIFICATE_REPORT`` when set."""
    from eval_adapter import AdapterUnavailable, load_adapter

    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    try:
        adapter = load_adapter()
    except AdapterUnavailable as exc:
        pytest.skip(f"eval adapter unavailable: {exc}")
    try:
        metrics, gates = cg.run_certificate_suite(adapter, CERT_DIR, cert_cases, cert_docs)
    except NotImplementedError as exc:
        pytest.skip(f"certificate eval not runnable yet: {exc}")
    except ValueError as exc:
        if "unknown eval workflow" in str(exc):
            pytest.skip(f"backend.eval_hook has no certificate branch yet: {exc}")
        raise
    report = cg.format_certificate_report(metrics, gates)
    target = Path(os.environ.get("BROKERDOCS_EVAL_CERTIFICATE_REPORT") or tmp_path / "certificate_report.json")
    target.write_text(json.dumps(cg.certificate_report_dict(metrics, gates), indent=2, default=str), encoding="utf-8")
    with capsys.disabled():
        print("\n" + report + f"\nreport: {target}")
    assert gates.passed, "certificate gate failed:\n" + report
    assert len(metrics.hard_fails) == 0 and metrics.recall >= cg.CERT_RECALL_MIN
