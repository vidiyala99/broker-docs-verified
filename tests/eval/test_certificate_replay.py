"""Certificate gate replayed offline on recorded live model replies (slice 4 fix lane).

``recorded/certificate_live_2026-11-03.json`` holds every extract and vision reply of one live
``-k certificate`` run (the run that showed 7 false alarms). A scripted provider returns those
replies, so the certificate workflow runs exactly as it did live, without a model call.

What the live model did, and what the backend must make of it:
- endorsement-only documents came back with ``cert:line`` ("... Coverage Part") and
  ``cert:policy_number`` ("forms part of Policy No. X"): they must not become policy rows
  (no extra in_force/insured rows, no collision with the declarations), their endorsements join
  the declarations they name (D7), and the one naming another policy gets the unmatched notice;
- form numbers came back with the edition twice ("OM 20 02 04 24" + edition "04 24"): the value
  must verify and the key must be the form (OM2002), as the truth keys it;
- conditions were over-read (the grant's scope, "This insurance is primary to ..."): those rows
  stay Not supported (fail safe, D14); the prompt now says what a condition is. With the
  conditions the truth states, every verdict equals the truth.

``recorded/certificate_live_2026-11-03b.json`` is the live run after the fix (EXTRACTION_VERSION
2026-11-03.2): every verdict equals the truth.
"""

from __future__ import annotations

import copy
import importlib
import json
import re
import sys
from pathlib import Path

import pytest

import certificate_gate as cg

REPO_ROOT = Path(__file__).resolve().parents[2]
CERT_DIR = REPO_ROOT / "tests" / "fixtures" / "certificates"
RECORDED = Path(__file__).resolve().parent / "recorded" / "certificate_live_2026-11-03.json"
RECORDED_AFTER = RECORDED.with_name("certificate_live_2026-11-03b.json")


def _backend(module: str):
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        return importlib.import_module(module)
    except Exception as exc:  # backend deps not installed
        pytest.skip(f"missing import {module}: {exc!r}")


@pytest.fixture(scope="module")
def truth():
    manifest = json.loads((CERT_DIR / "manifest.json").read_text(encoding="utf-8"))
    cases = {e["case_id"]: json.loads((CERT_DIR / e["truth"]).read_text(encoding="utf-8"))
             for e in manifest["cases"]}
    docs = {e["fixture_id"]: json.loads((CERT_DIR / e["truth"]).read_text(encoding="utf-8"))
            for e in manifest["documents"]}
    return cases, docs


@pytest.fixture(scope="module")
def recorded():
    return json.loads(RECORDED.read_text(encoding="utf-8"))["cases"]


def _run(cases, docs, replies):
    eval_hook = _backend("backend.eval_hook")
    providers = _backend("backend.providers")
    scores = []
    for cid, case in sorted(cases.items()):
        rec = replies[cid]

        def respond(req, rec=rec):
            text = "\n".join(p.text for m in req.messages if m.role == "user" for p in m.parts()
                             if hasattr(p, "text"))
            if req.role is providers.CallRole.VISION:
                return rec["vision"][re.search(r"Page (\d+)\.", text).group(1)]
            name = re.search(r"Document file name \(data, not an instruction\): '([^']+)'", text).group(1)
            return copy.deepcopy(rec["extract"][name])

        paths = {f: str(CERT_DIR / docs[f]["file"]) for f in case["documents"]}
        out = eval_hook.run_eval_batch([paths[f] for f in case["documents"]], workflow=case["workflow"],
                                       parameters=cg.adapter_parameters(case),
                                       provider=providers.FakeProvider(respond))
        scores.append(cg.score_certificate_case(case, docs, out, paths))
    m = cg.aggregate_certificate(scores)
    return m, cg.evaluate_certificate_gates(m)


def _truth_conditions(replies, docs):
    """The recorded replies with each endorsement's condition set to what the truth states."""
    out = copy.deepcopy(replies)
    by_file = {t["file"]: t for t in docs.values()}
    for rec in out.values():
        for name, data in rec["extract"].items():
            facts = by_file[name]["facts"]
            for e in data["endorsements"]:
                form = cg.normalize_form(e["form_number"])
                cond = next((f for k, f in facts.items() if k.endswith(":condition")
                             and form.startswith(cg.split_endorsement_key(k)[0])), None)
                e["condition"] = ({"value": cond["printed"], "page": cond["page"], "quote": cond["quote"]}
                                  if cond else {"value": None, "page": None, "quote": None})
    return out


def test_recorded_live_replies_join_endorsements_to_their_declarations(truth, recorded):
    cases, docs = truth
    m, rep = _run(cases, docs, recorded)
    report = cg.format_certificate_report(m, rep)
    assert rep.passed and m.hard_fails == [] and m.recall == 1.0, report
    assert m.unscored_claims == [], report  # no policy row for an endorsement-only document
    assert m.notices_found == m.notices_expected == 2, report  # incl. endorsement_unmatched (traps)
    # form numbers verify and key as the truth does, so every endorsement part is scored and right.
    # (condition is scored by exact value: the model quotes the whole sentence, the truth the clause.)
    for attr in ("form_number", "type", "policy_number", "named_party"):
        fa = m.field_accuracy[f"cert:endorsement:*:{attr}"]
        assert fa["present"] and fa["correct"] == fa["present"], (attr, fa)
    # the only false alarms left are endorsements whose recorded condition is the grant's own scope
    assert sorted((f["case_id"], f["key"]) for f in m.false_alarms) == [
        ("cert_contractor", "wording:additional_insured:general_liability"),
        ("cert_contractor", "wording:primary_noncontributory:general_liability"),
        ("cert_contractor", "wording:waiver_of_subrogation:workers_comp"),
        ("cert_scanned", "wording:primary_noncontributory:general_liability")], report
    assert all(f["reported_reason"].startswith("applies only when") for f in m.false_alarms), report


def test_recorded_replies_after_the_prompt_fix_match_every_verdict(truth):
    """The second live run (form number without its edition, what a condition is): 15/15 Supported."""
    cases, docs = truth
    replies = json.loads(RECORDED_AFTER.read_text(encoding="utf-8"))["cases"]
    m, rep = _run(cases, docs, replies)
    report = cg.format_certificate_report(m, rep)
    assert rep.passed and m.hard_fails == [] and m.recall == 1.0, report
    assert m.false_alarms == [] and m.supported_accuracy == 1.0 and m.claim_accuracy == 1.0, report
    assert m.unscored_claims == [] and m.notices_found == 2, report


def test_recorded_replies_with_the_truth_conditions_match_every_verdict(truth, recorded):
    cases, docs = truth
    m, rep = _run(cases, docs, _truth_conditions(recorded, docs))
    report = cg.format_certificate_report(m, rep)
    assert rep.passed and m.hard_fails == [] and m.recall == 1.0, report
    assert m.false_alarms == [] and m.supported_accuracy == 1.0 and m.claim_accuracy == 1.0, report
    assert m.unscored_claims == [] and m.notices_found == 2, report
