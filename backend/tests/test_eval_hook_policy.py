"""eval_hook: the Policy Check path (slice 3, T7), offline (FakeProvider, no OpenAI call).

Proves the eval and the backend agree:
* the eval's mirrored severity table equals pipeline/diff.py;
* the seeded ground truth is exactly what the backend diff reports on perfect facts;
* the AMS truth equals what backend/ams.py renders and maps;
* the hook runs a real Policy Check run (AMS rendered to PDF, slots per role) and its
  DiscrepancyBlock items pass the D16 gate with a perfect model, and fail it (empty
  result) with a model that reads nothing.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest

from backend import ams, eval_hook
from backend.pipeline import diff
from backend.providers import CallRole, FakeProvider
from backend.schemas import ConflictValue, EvidenceState
from pipeline_fakes import _user_text

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "tests" / "fixtures" / "policy"
EVAL = ROOT / "tests" / "eval"


def _eval_module(name: str):
    sys.path.insert(0, str(EVAL))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(EVAL))


@pytest.fixture(scope="module")
def g():
    return _eval_module("eval_gates")


@pytest.fixture(scope="module")
def truth():
    manifest = json.loads((POLICY / "manifest.json").read_text(encoding="utf-8"))
    cases = {e["case_id"]: json.loads((POLICY / e["truth"]).read_text(encoding="utf-8")) for e in manifest["cases"]}
    docs = {e["fixture_id"]: json.loads((POLICY / e["truth"]).read_text(encoding="utf-8"))
            for e in manifest["documents"]}
    return cases, docs


def _paths(case, docs):
    return {fid: str(POLICY / docs[fid]["file"]) for fid in case["roles"].values()}


# --------------------------------------------------------------------- eval mirrors the backend


def test_eval_severity_table_mirrors_the_diff(g):
    assert g.POLICY_FACT_TYPES == diff.FACT_TYPES
    assert g.POLICY_SEVERITY_TABLE == {t: {c: str(s) for c, s in v.items()} for t, v in diff.SEVERITY_TABLE.items()}
    assert g.POLICY_UNIVERSAL_SEVERITY == {c: str(s) for c, s in diff.UNIVERSAL_SEVERITY.items()}
    assert g.POLICY_NOT_COMPARED_WITH_EXPIRING == diff.NOT_COMPARED_WITH_EXPIRING
    assert g.POLICY_ROLES == diff.ROLES and g.POLICY_OPTIONAL_ROLES == diff.OPTIONAL_ROLES


def _role_inputs(case, docs) -> dict[str, diff.RoleInput]:
    out = {}
    for role, fid in case["roles"].items():
        t = docs[fid]
        facts = {k: diff.RoleFact(value=f["value"], state=EvidenceState.VERIFIED, passage_ids=(1,))
                 for k, f in t["facts"].items() if f is not None}
        for k, conf in t["conflicts"].items():
            facts[k] = diff.RoleFact(value=None, state=EvidenceState.CONFLICTING, passage_ids=(1, 2),
                                     conflicts=tuple(ConflictValue(value=c["value"], page_no=c["page"])
                                                     for c in conf))
        out[role] = diff.RoleInput(facts=facts)
    return out


def test_seeded_truth_is_the_backend_diff_on_perfect_facts(truth):
    cases, docs = truth
    for cid, case in cases.items():
        items = diff.diff(_role_inputs(case, docs))
        got = sorted((i.key, str(i.state), i.change, str(i.severity)) for i in items)
        want = sorted((d["key"], d["item_state"], d["change"], d["severity"]) for d in case["discrepancies"])
        assert got == want, cid
        no_item = {d["key"] for d in case["no_item_keys"]}
        assert not no_item & {i.key for i in items}, cid
        for i in items:  # expected slots are the diff's slots
            seed = next(d for d in case["discrepancies"] if d["key"] == i.key)
            for s in i.slots:
                assert str(s.state) == seed["slots"][s.role]["state"], (cid, i.key, s.role)
                assert s.value == seed["slots"][s.role]["value"], (cid, i.key, s.role)


def test_ams_truth_is_what_the_backend_renders_and_maps(truth):
    _, docs = truth
    t = docs["pc_homeowners_ams"]
    data = (POLICY / t["file"]).read_bytes()
    rendered = ams.render_pdf(ams.parse_ams(data))
    assert rendered.page_count == t["page_count"] == 1
    mapped = ams.map_facts(rendered)
    facts = {f.field: f for f in mapped.facts}
    want = {k: f for k, f in t["facts"].items() if f is not None}
    assert set(facts) == set(want)
    for k, f in want.items():
        assert (facts[k].value, facts[k].page, facts[k].quote) == (f["value"], f["page"], f["quote"]), k
    assert {k: [(c.value, c.page, c.quote) for c in v] for k, v in mapped.conflicts.items()} == {
        k: [(c["value"], c["page"], c["quote"]) for c in v] for k, v in t["conflicts"].items()}
    assert mapped.unmapped == ["Carrier", "Agent Notes"]
    pgen = _eval_module("policy_generator")
    assert {n: ams.canonical_key(n) for n in pgen.AMS_KEYS} == pgen.AMS_KEYS


# --------------------------------------------------------------------- the hook, end to end


def _oracle_responder(docs):
    """A perfect model: extraction returns the truth facts of the document named in the
    prompt; vision returns the truth lines of the scanned page (only the quote is scanned)."""
    by_file = {t["file"]: t for t in docs.values()}
    scanned = next(t for t in docs.values() if t["variant"] == "policy_scanned")
    calls = []

    def respond(req):
        text = _user_text(req)
        calls.append(req.role)
        if req.role is CallRole.VISION:
            n = int(re.search(r"Page (\d+)\.", text).group(1))
            return "\n".join(f["quote"] for f in scanned["facts"].values() if f and f["page"] == n)
        if req.role is not CallRole.EXTRACT:
            raise AssertionError(f"policy check made an unexpected {req.role} call")
        name = re.search(r"Document file name \(data, not an instruction\): '([^']+)'", text).group(1)
        t = by_file[name]
        facts, forms = [], []
        for k, f in t["facts"].items():
            if k.startswith("form:"):
                if f:
                    number, mm, yy = f["value"].rsplit(" ", 2)
                    forms.append({"number": number, "edition": f"{mm} {yy}", "title": None, "page": f["page"],
                                  "quote": f["quote"]})
                continue
            facts.append({"field": k, "value": f["value"], "page": f["page"], "quote": f["quote"]} if f else
                         {"field": k, "value": None, "page": None, "quote": None})
        return {"facts": facts, "forms": forms, "options_detected": 1, "option_compared": None}

    respond.calls = calls
    return respond


def _blind_responder(req):
    if req.role is CallRole.VISION:
        return ""
    return {"facts": [], "forms": [], "options_detected": 1, "option_compared": None}


def _run_case(case, docs, provider):
    paths = _paths(case, docs)
    slots = {paths[fid]: role for role, fid in case["roles"].items()}
    return paths, eval_hook.run_eval_batch(list(slots), workflow="policy-check", slots=slots, provider=provider)


@pytest.fixture(scope="module")
def oracle_runs(truth):
    cases, docs = truth
    out = {}
    for cid, case in cases.items():
        responder = _oracle_responder(docs)
        out[cid] = (*_run_case(case, docs, FakeProvider(responder)), responder.calls)
    return out


def test_hook_reports_discrepancy_items_with_resolved_slots(truth, oracle_runs):
    cases, docs = truth
    paths, out, calls = oracle_runs["pc_homeowners"]
    block = out["policy_check"]
    assert block["workflow"] == "policy-check" and block["run_status"] == "done"
    assert block["roles"] == {role: paths[fid] for role, fid in cases["pc_homeowners"]["roles"].items()}
    assert set(out["documents"]) == set(paths.values())
    assert all(d["status"] == "ok" for d in out["documents"].values())
    assert out["documents"][paths["pc_homeowners_quote"]]["ocr_pages"] == [1, 2]
    assert CallRole.EXTRACT in calls and CallRole.VISION in calls and CallRole.COMPOSE not in calls
    assert block["items"], "no items"
    for item in block["items"]:
        assert set(item) == {"id", "key", "title", "severity", "item_state", "explanation", "slots"}
        for role, slot in item["slots"].items():
            assert set(slot) == {"document", "state", "value", "citations", "conflicts"}
            assert slot["document"] == block["roles"][role]
            if slot["state"] in ("verified", "scan_read"):
                assert slot["value"] and slot["citations"]
                assert all(c["document"] == slot["document"] and isinstance(c["page"], int) for c in slot["citations"])
            else:
                assert slot["value"] is None
    ams_path = paths["pc_homeowners_ams"]
    premium = next(i for i in block["items"] if i["key"] == "total_premium")
    assert premium["slots"]["ams"]["value"] == "$2,388.00"
    assert premium["slots"]["ams"]["citations"] == [{"document": ams_path, "page": 1,
                                                     "passage_id": premium["slots"]["ams"]["citations"][0]["passage_id"]}]
    assert any("Agent Notes" in n["message"] for n in block["notices"])
    condo = oracle_runs["pc_condo_injection"][1]["policy_check"]
    assert all(set(i["slots"]) <= {"expiring", "renewal"} for i in condo["items"])  # quote, AMS: N/A


def test_hook_output_passes_the_gate_with_a_perfect_model(g, truth, oracle_runs):
    cases, docs = truth
    scores = [g.score_policy_case(cases[cid], docs, out, paths) for cid, (paths, out, _) in sorted(oracle_runs.items())]
    m = g.aggregate_policy(scores)
    rep = g.evaluate_policy_gates(m)
    assert rep.passed, g.format_policy_report(m, rep)
    assert m.recall == 1.0 and m.precision == 1.0 and m.hard_fails == [], g.format_policy_report(m, rep)
    assert m.slot_accuracy == 1.0 and m.severity_agreement == 1.0


def test_hook_output_fails_the_gate_when_the_model_reads_nothing(g, truth):
    cases, docs = truth
    case = cases["pc_condo_injection"]
    paths, out = _run_case(case, docs, FakeProvider(_blind_responder))
    assert out["policy_check"]["items"] == [] and out["policy_check"]["run_status"] in ("done", "partial")
    m = g.aggregate_policy([g.score_policy_case(case, docs, out, paths)])
    rep = g.evaluate_policy_gates(m)
    assert not rep.passed and m.empty_cases == ["pc_condo_injection"]
    assert "empty_result[policy_check] (cases with no item)" in [r.name for r in rep.failures]


def test_hook_output_fails_the_gate_on_a_mislabeled_field(g, truth):
    """The model files the renewal's Coverage D value as Coverage C: verify passes (the
    value and quote are on page 2), only the ground-truth field check catches it."""
    cases, docs = truth
    case = cases["pc_homeowners"]
    base = _oracle_responder(docs)

    def mislabel(req):
        reply = base(req)
        if req.role is CallRole.EXTRACT and "pc_homeowners_renewal.pdf" in _user_text(req):
            d = next(f for f in reply["facts"] if f["field"] == "coverage_d_limit")
            c = next(f for f in reply["facts"] if f["field"] == "coverage_c_limit")
            c.update(value=d["value"], page=d["page"], quote=d["quote"])
        return reply

    paths, out = _run_case(case, docs, FakeProvider(mislabel))
    sc = g.score_policy_case(case, docs, out, paths)
    kinds = {(h["key"], h["role"], h["kind"]) for h in sc.hard_fails}
    assert ("coverage_c_limit", "renewal", "wrong_field") in kinds, sc.hard_fails
    assert not g.evaluate_policy_gates(g.aggregate_policy([sc])).passed


# --------------------------------------------------------------------- hook arguments


def test_policy_check_needs_slots_with_known_unique_roles(truth):
    _, docs = truth
    e = str(POLICY / docs["pc_condo_injection_expiring"]["file"])
    r = str(POLICY / docs["pc_condo_injection_renewal"]["file"])
    fake = FakeProvider([])
    with pytest.raises(ValueError, match="needs slots"):
        eval_hook.run_eval_batch([e, r], workflow="policy-check", provider=fake)
    with pytest.raises(ValueError, match="no policy-check role"):
        eval_hook.run_eval_batch([e, r], workflow="policy-check", slots={e: "expiring", r: "binder"}, provider=fake)
    with pytest.raises(ValueError, match="more than one"):
        eval_hook.run_eval_batch([e, r], workflow="policy-check", slots={e: "renewal", r: "renewal"}, provider=fake)
    with pytest.raises(ValueError, match="only used by the policy-check"):
        eval_hook.run_eval_batch([e], slots={e: "expiring"}, provider=fake)


def test_rejected_ams_file_is_reported_failed_and_the_run_still_completes(truth, tmp_path):
    cases, docs = truth
    case = cases["pc_condo_injection"]
    paths = _paths(case, docs)
    bad = tmp_path / "ams.json"
    bad.write_text('{"Coverage A": {"nested": 1}}', encoding="utf-8")
    slots = {paths[fid]: role for role, fid in case["roles"].items()} | {str(bad): "ams"}
    out = eval_hook.run_eval_batch(list(slots), workflow="policy-check", slots=slots,
                                   provider=FakeProvider(_oracle_responder(docs)))
    assert out["documents"][str(bad)] == {"status": "failed", "failure_reason": "invalid_ams", "option_count": None,
                                          "ocr_pages": None, "facts": []}
    assert out["policy_check"]["run_status"] == "done" and out["policy_check"]["items"]


def test_missing_required_role_reports_no_run(truth):
    _, docs = truth
    e = str(POLICY / docs["pc_condo_injection_expiring"]["file"])
    out = eval_hook.run_eval_batch([e], workflow="policy-check", slots={e: "expiring"},
                                   provider=FakeProvider(_oracle_responder(docs)))
    block = out["policy_check"]
    assert block["items"] == [] and block["run_status"] is None
    assert block["warnings"] and block["warnings"][0].startswith("run not created")
