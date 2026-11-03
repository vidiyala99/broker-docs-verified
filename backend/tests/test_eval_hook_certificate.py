"""Slice 4: the eval hook's certificate branch (the C10 gate contract in tests/eval/certificate_gate.py)."""

from __future__ import annotations

from backend import eval_hook
from backend.providers import FakeProvider
from conftest import make_pdf
from pipeline_fakes import cert_responder
from test_certificate_api import AI_OTHER_POLICY, AI_SCHEDULED, AUTO_EXPIRED, HOLDER, dec


def test_certificate_batch_reports_claims_with_gate_keys_and_notices(tmp_path):
    files = {"gl.pdf": dec(), "auto.pdf": AUTO_EXPIRED, "ai.pdf": AI_SCHEDULED, "other.pdf": AI_OTHER_POLICY,
             "pkg.pdf": dec(number="CPP-1", line="Commercial Package Policy")}
    paths = []
    for name, pages in files.items():
        p = tmp_path / name
        p.write_bytes(make_pdf(pages))
        paths.append(str(p))
    out = eval_hook.run_eval_batch(paths, workflow="certificate", provider=FakeProvider(cert_responder()), parameters={
        "client_name": "Cedar Hollow LLC", "holder_name": HOLDER, "holder_address": "4 Elm Street",
        "wording": {"additional_insured": True, "waiver_of_subrogation": True, "primary_noncontributory": False},
        "certificate_date": "2026-06-01", "description": "Roofing"})
    block = out["certificate"]
    assert block["run_status"] == "done" and block["workflow"] == "certificate"
    status = {c["key"]: c["status"] for c in block["claims"]}
    assert status == {
        "in_force:GL1234567": "supported", "wording:additional_insured:general_liability": "supported",
        "wording:waiver_of_subrogation:general_liability": "not_supported",
        "in_force:BA555": "not_supported", "wording:additional_insured:auto": "not_supported",
        "wording:waiver_of_subrogation:auto": "not_supported",
        "insured_consistent": "supported", "description_reviewed": "not_supported"}
    ai = next(c for c in block["claims"] if c["key"] == "wording:additional_insured:general_liability")
    assert {c["document"] for c in ai["citations"]} == {str(tmp_path / "ai.pdf")}
    kinds = {(n["kind"], n["document"]) for n in block["notices"]}
    assert ("package", str(tmp_path / "pkg.pdf")) in kinds
    assert ("endorsement_unmatched", str(tmp_path / "other.pdf")) in kinds
    facts = {f["field"] for f in out["documents"][str(tmp_path / "ai.pdf")]["facts"]}
    assert {"cert:endorsement:CG2026:form_number", "cert:endorsement:CG2026:type",
            "cert:endorsement:CG2026:named_party"} <= facts


def test_unknown_parameters_are_a_value_error(tmp_path):
    p = tmp_path / "gl.pdf"
    p.write_bytes(make_pdf(dec()))
    try:
        eval_hook.run_eval_batch([str(p)], workflow="certificate", provider=FakeProvider(cert_responder()),
                                 parameters={"holder_name": ""})
    except ValueError as exc:
        assert "certificate parameters" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
