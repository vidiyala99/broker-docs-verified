"""Slice 4: a certificate run end to end through the API (fake model, pipeline_fakes.cert_responder).

Create (D6 bounds), draft + claims, decisions through PATCH /discrepancies (D10, D13, D15), cell edits
(D12), Add document (D2), Issue (D5, D13, D16), New version, holders (C2), the Certificates queue (C8).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from api_support import DOC_A, make_api
from backend import db, export_certificate, models
from backend.pipeline import cache
from backend.providers import CallRole
from backend.schemas import RunStatus
from pipeline_fakes import any_responder

DATE = "2026-06-01"
HOLDER = "Smith & Sons Construction"


def dec(*, number="GL-1234567", line="Commercial General Liability", eff="01/01/2026", exp="01/01/2027",
        insured="Cedar Hollow LLC", carrier="Harborline Specialty", limits=None) -> list[str]:
    limits = limits if limits is not None else {"Each Occurrence": "$1,000,000", "General Aggregate": "$2,000,000"}
    lines = ["DECLARATIONS", f"Named Insured: {insured}", "Insured Address: 12 Mill Road, Albany NY",
             "Producer: Ruiz and Partners", f"Insurer: {carrier}", "NAIC Number: 12345",
             f"Policy Number: {number}", f"Line of Coverage: {line}", f"Effective Date: {eff}",
             f"Expiration Date: {exp}", *(f"{k}: {v}" for k, v in limits.items())]
    return ["\n".join(lines)]


AI_SCHEDULED = ["Form CG 20 26 04 13\nADDITIONAL INSURED - DESIGNATED PERSON OR ORGANIZATION\n"
                f"Policy Number: GL 1234567\nAdditional Insured: {HOLDER}"]
AI_BLANKET_CONDITIONAL = ["Form CG 20 33 04 13\nBLANKET ADDITIONAL INSURED - AUTOMATIC STATUS\n"
                          "Policy Number: GL1234567\nApplies: where required by a written contract"]
AI_OTHER_POLICY = ["Form CG 20 26 04 13\nADDITIONAL INSURED - DESIGNATED PERSON OR ORGANIZATION\n"
                   f"Policy Number: GL-9999999\nAdditional Insured: {HOLDER}"]
AUTO_EXPIRED = dec(number="BA-555", line="Business Auto", eff="01/01/2025", exp="01/01/2026",
                   limits={"Combined Single Limit": "$1,000,000"})


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.delenv(cache.ENV_SWITCH, raising=False)
    a = make_api(tmp_path, monkeypatch, any_responder())
    yield a
    a.client.__exit__(None, None, None)


def params(**kw):
    out = {"holder_name": HOLDER, "holder_address": "4 Elm Street\nAlbany NY", "certificate_date": DATE,
           "wording": {"additional_insured": True}}
    out.update(kw)
    return out


def create(api, docs: list[list[str]], *, client="Cedar Hollow LLC", status=201, **kw):
    ids = [api.upload_pages(pages, f"doc{i}.pdf") for i, pages in enumerate(docs)]
    r = api.client.post("/api/runs", json={"document_ids": ids, "workflow": "certificate",
                                           "client_name": client, "parameters": params(**kw)})
    assert r.status_code == status, r.text
    if status != 201:
        return r.json()
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    return cert(api, run_id)


def cert(api, run_id):
    r = api.client.get(f"/api/certificates/{run_id}")
    assert r.status_code == 200, r.text
    return r.json()


def claim(view, key):
    return next(c for c in view["claims"] if c["key"] == key)


def patch(api, view, key, body, status=200):
    c = claim(view, key)
    r = api.client.patch(f"/api/runs/{view['run']['id']}/discrepancies/{c['id']}",
                         json={"expected_fingerprint": c["fingerprint"], **body})
    assert r.status_code == status, r.text
    return r.json()


def issue(api, view, version=None, status=200):
    r = api.client.post(f"/api/certificates/{view['run']['id']}/issue",
                        json={"version": version or view["version"]})
    assert r.status_code == status, r.text
    return r.json()


# --------------------------------------------------------------------- create


@pytest.mark.parametrize("field,value,name", [("holder_name", "x" * 121, "Holder name"),
                                              ("holder_address", "x" * 301, "Holder address"),
                                              ("description", "x" * 1501, "Description of operations"),
                                              ("holder_name", "", "Holder name")])
def test_free_text_bounds_are_422_naming_the_field(api, field, value, name):
    body = create(api, [dec()], status=422, **{field: value})
    assert body["error"]["code"] == "invalid_request" and name in body["error"]["message"]
    assert body["error"]["fields"][0]["loc"][:3] == ["body", "parameters", field]


def test_unknown_parameter_and_wording_are_422(api):
    assert create(api, [dec()], status=422, extra=1)["error"]["code"] == "invalid_request"
    assert create(api, [dec()], status=422, wording={"additional_insured": "yes"})["error"]["code"] == "invalid_request"


def test_bounds_at_the_maximum_are_accepted_and_the_date_defaults_to_today(api):
    view = create(api, [dec()], holder_name="h" * 120, holder_address="a" * 300, description="d" * 1500,
                  certificate_date=None)
    stored = view["run"]["parameters"]
    assert len(stored["holder_name"]) == 120 and stored["certificate_date"]


# --------------------------------------------------------------------- draft and claims


def test_draft_prints_verified_values_with_chips_and_checks_each_claim(api):
    view = create(api, [dec(), AI_SCHEDULED], description="Roofing at 4 Elm Street")
    assert view["run"]["status"] == "done"
    assert {c["key"]: c["verdict"] for c in view["claims"]} == {
        "in_force:A": "supported", "additional_insured:A": "supported", "insured": "supported",
        "description": "needs_signoff"}
    assert view["counts"] == {"total": 4, "open": 1, "supported": 3, "overridden": 0, "removed": 0}
    block = view["certificate"]
    assert block["holder_name"] == HOLDER and block["description"] == "Roofing at 4 Elm Street"
    (policy,) = block["policies"]
    assert policy["line"] == "general_liability" and policy["wording"] == ["additional_insured"]
    fields = {f["key"]: f["cell"] for f in policy["fields"]}
    assert fields["cert:policy_number"]["value"] == "GL-1234567" and fields["cert:policy_number"]["passage_ids"]
    assert fields["cert:gl_each_occurrence"]["value"] == "$1,000,000"
    assert fields["cert:gl_medical_expense"]["value"] == "Not stated"  # a gap, not a claim row
    assert block["insurers"] == [{"letter": "A", "name": "Harborline Specialty", "naic": "12345"}]
    assert view["chips"] and view["blocked"] == ["claims_open"] and not view["can_issue"]
    ai = claim(view, "additional_insured:A")
    assert "names the holder" in ai["reason"] and ai["prose"][0]["passage_ids"]
    # every cited passage resolves to a chip (CertificateBlock and ClaimBlock join the traversal)
    ids = {p for c in view["claims"] for s in c["prose"] for p in s["passage_ids"]}
    ids |= {p for f in policy["fields"] for p in f["cell"]["passage_ids"]}
    assert ids <= {int(k) for k in view["chips"]}
    assert len([r for r in api.provider.requests if r.role is CallRole.COMPOSE]) == 0  # C5: no compose call


def test_conditional_blanket_endorsement_needs_an_override_with_a_reason(api):
    view = create(api, [dec(), AI_BLANKET_CONDITIONAL])
    ai = claim(view, "additional_insured:A")
    assert ai["verdict"] == "not_supported" and ai["condition"] == "where required by a written contract"
    assert ai["reason"].startswith("applies only when where required")
    patch(api, view, "additional_insured:A", {"status": "resolved"}, status=422)  # note_required
    done = patch(api, view, "additional_insured:A", {"status": "resolved", "note": "Contract on file, 3/2"})
    assert done["status"] == "resolved" and done["needs_action"] is False
    assert cert(api, view["run"]["id"])["can_issue"]


def test_endorsement_for_another_policy_is_a_notice_and_supports_nothing(api):
    view = create(api, [dec(), AI_OTHER_POLICY])
    assert claim(view, "additional_insured:A")["verdict"] == "not_supported"
    assert any("GL-9999999" in n["message"] for n in view["notices"])


def test_package_policy_is_noticed_and_contributes_nothing(api):
    view = create(api, [dec(line="Commercial Package Policy")])
    assert view["certificate"]["policies"] == [] and not [c for c in view["claims"] if c["kind"] == "in_force"]
    assert any("several lines" in n["message"] for n in view["notices"])
    assert "no_coverage" in view["blocked"]


def test_scanned_values_never_support_a_claim(tmp_path, monkeypatch):
    from conftest import make_pdf

    text = dec()[0]
    a = make_api(tmp_path, monkeypatch, any_responder(ocr_pages={1: text}))
    try:
        doc = a.upload(("scan.pdf", make_pdf([None], image_pages={1: (36, 36, 576, 756)})))["documents"][0]["id"]
        r = a.client.post("/api/runs", json={"document_ids": [doc], "workflow": "certificate",
                                             "parameters": params(wording={})})
        a.events(r.json()["run"]["id"])
        view = cert(a, r.json()["run"]["id"])
        in_force = claim(view, "in_force:A")
        assert in_force["verdict"] == "not_supported" and "read from a scan" in in_force["reason"]
        fields = {f["key"]: f["cell"] for f in view["certificate"]["policies"][0]["fields"]}
        assert fields["cert:gl_each_occurrence"]["value"] == "Not stated"
        assert fields["cert:gl_each_occurrence"]["state"] == "scan_read"
    finally:
        a.client.__exit__(None, None, None)


# --------------------------------------------------------------------- decisions (D10, D13, D15)


def test_claim_patch_rules(api):
    view = create(api, [dec(), AI_SCHEDULED], description="Roofing")
    c = claim(view, "description")
    url = f"/api/runs/{view['run']['id']}/discrepancies/{c['id']}"
    r = api.client.patch(url, json={"status": "resolved", "note": "ok"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "fingerprint_required"
    r = api.client.patch(url, json={"status": "resolved", "note": "ok", "expected_fingerprint": "0" * 64})
    assert r.status_code == 409 and r.json()["error"]["code"] == "item_changed"
    assert r.json()["error"]["item"]["fingerprint"] == c["fingerprint"]
    r = api.client.patch(url, json={"status": "resolved", "expected_fingerprint": c["fingerprint"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "note_required"
    ins = claim(view, "insured")
    r = api.client.patch(f"/api/runs/{view['run']['id']}/discrepancies/{ins['id']}",
                         json={"status": "removed", "expected_fingerprint": ins["fingerprint"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_removable"
    ok = patch(api, view, "description", {"status": "resolved", "note": "Wording from the contract"})
    assert ok["status"] == "resolved" and ok["note"] == "Wording from the contract"


def test_remove_wording_takes_it_off_the_certificate_and_open_puts_it_back(api):
    view = create(api, [dec(), AI_BLANKET_CONDITIONAL])
    out = patch(api, view, "additional_insured:A", {"status": "removed"})
    assert out["status"] == "removed"
    after = cert(api, view["run"]["id"])
    assert after["version"] == view["version"] + 1  # D13: a new version without the asserted wording
    assert after["certificate"]["policies"][0]["wording"] == []
    assert claim(after, "additional_insured:A")["status"] == "removed" and after["can_issue"]
    back = patch(api, after, "additional_insured:A", {"status": "open"})
    assert back["status"] == "open"
    again = cert(api, view["run"]["id"])
    assert again["version"] == after["version"] + 1
    assert again["certificate"]["policies"][0]["wording"] == ["additional_insured"]


def test_remove_an_expired_policy_drops_its_row_and_its_wording_claims(api):
    view = create(api, [dec(), AUTO_EXPIRED, AI_SCHEDULED])
    assert claim(view, "in_force:B")["verdict"] == "not_supported"
    assert "expired on 01/01/2026" in claim(view, "in_force:B")["reason"]
    assert claim(view, "additional_insured:B")["verdict"] == "not_supported"
    patch(api, view, "in_force:B", {"status": "removed"})
    after = cert(api, view["run"]["id"])
    assert [p["letter"] for p in after["certificate"]["policies"]] == ["A"]
    assert "additional_insured:B" not in {c["key"] for c in after["claims"]}
    assert claim(after, "in_force:B")["status"] == "removed" and after["can_issue"]


def test_insured_mismatch_is_fixed_by_removing_the_mismatching_policy(api):
    other = dec(number="BA-777", line="Business Auto", insured="Cedar Hollow Holdings Inc",
                limits={"Combined Single Limit": "$1,000,000"})
    view = create(api, [dec(), other], wording={})
    ins = claim(view, "insured")
    assert ins["verdict"] == "not_supported" and "differs" in ins["reason"]
    patch(api, view, "in_force:B", {"status": "removed"})
    after = cert(api, view["run"]["id"])
    assert claim(after, "insured")["verdict"] == "supported"


def test_an_override_reopens_when_its_evidence_changes(api):
    view = create(api, [dec(), AI_BLANKET_CONDITIONAL])
    patch(api, view, "additional_insured:A", {"status": "resolved", "note": "Contract on file"})
    with db.session_scope(api.ctx.factory) as s:  # the condition is read differently (a new read)
        f = s.scalar(select(models.Fact).where(models.Fact.field.like("%:condition")))
        f.value = "where required by a written contract executed before the loss"
        f.quote = f.value
    r = api.client.post(f"/api/artifacts/{view['artifact_id']}/regenerate", json={})
    assert r.status_code == 200, r.text
    after = claim(cert(api, view["run"]["id"]), "additional_insured:A")
    assert after["status"] == "open" and after["changed_since_resolved"] and after["needs_action"]


# --------------------------------------------------------------------- edits (D12)


def test_edited_value_needs_a_signoff_and_remove_restores_it(api):
    view = create(api, [dec()], wording={})
    assert view["can_issue"]
    r = api.client.patch(f"/api/artifacts/{view['artifact_id']}/cells", json={
        "block_id": "certificate", "row_key": "cert:gl_each_occurrence", "column_key": "A", "value": "$2,000,000"})
    assert r.status_code == 200, r.text
    after = cert(api, view["run"]["id"])
    edited = claim(after, "edited:A:cert:gl_each_occurrence")
    assert edited["verdict"] == "needs_signoff" and "$1,000,000" in edited["reason"]
    cell = next(f["cell"] for f in after["certificate"]["policies"][0]["fields"] if f["key"] == "cert:gl_each_occurrence")
    assert cell["value"] == "$2,000,000" and cell["edited"] and cell["original_value"] == "$1,000,000"
    assert "claims_open" in after["blocked"]
    patch(api, after, "edited:A:cert:gl_each_occurrence", {"status": "removed"})
    restored = cert(api, view["run"]["id"])
    cell = next(f["cell"] for f in restored["certificate"]["policies"][0]["fields"]
                if f["key"] == "cert:gl_each_occurrence")
    assert cell["value"] == "$1,000,000" and not cell["edited"] and restored["can_issue"]


def test_compare_quotes_edit_behaviour_is_unchanged(api):
    run_doc = api.upload_pages(DOC_A)
    r = api.client.post("/api/runs", json={"document_ids": [run_doc]})
    api.events(r.json()["run"]["id"])
    art = api.artifact(r.json()["run"]["id"])
    e = api.client.patch(f"/api/artifacts/{art['id']}/cells", json={
        "block_id": "premium-table", "row_key": "total_premium", "column_key": "A", "value": "$1"})
    assert e.status_code == 200 and e.json()["version"] == 2
    assert api.client.get(f"/api/runs/{r.json()['run']['id']}/discrepancies").json()["items"] == []


# --------------------------------------------------------------------- Add document (D2)


def test_add_document_reads_only_the_new_one_and_keeps_decisions(api):
    view = create(api, [dec()], description="Roofing")
    assert claim(view, "additional_insured:A")["verdict"] == "not_supported"
    patch(api, view, "description", {"status": "resolved", "note": "From the contract"})
    calls = len([r for r in api.provider.requests if r.role is CallRole.EXTRACT])
    doc = api.upload_pages(AI_SCHEDULED, "ai.pdf")
    r = api.client.post(f"/api/certificates/{view['run']['id']}/documents", json={"document_ids": [doc]})
    assert r.status_code == 202, r.text
    api.events(view["run"]["id"])
    after = cert(api, view["run"]["id"])
    assert len([r for r in api.provider.requests if r.role is CallRole.EXTRACT]) == calls + 1
    assert after["run"]["status"] == "done" and after["version"] == view["version"] + 1
    assert claim(after, "additional_insured:A")["verdict"] == "supported"
    assert claim(after, "description")["status"] == "resolved"  # carried by reconcile
    assert [d["letter"] for d in after["run"]["documents"]] == ["A", "B"]


def test_add_document_guards(api):
    view = create(api, [dec()], wording={})
    doc = api.upload_pages(AI_SCHEDULED, "ai.pdf")
    url = f"/api/certificates/{view['run']['id']}/documents"
    assert api.client.post(url, json={"document_ids": [view["run"]["documents"][0]["document_id"]]}).status_code == 422
    assert api.client.post(url, json={"document_ids": [9999]}).status_code == 404
    issue(api, view)
    r = api.client.post(url, json={"document_ids": [doc]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "issued"  # an issued version never reopens
    compare = api.client.post("/api/runs", json={"document_ids": [api.upload_pages(DOC_A)]}).json()["run"]["id"]
    api.events(compare)
    r = api.client.post(f"/api/certificates/{compare}/documents", json={"document_ids": [doc]})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_a_certificate"


def test_reopen_guard_is_certificate_only():
    run = models.Run(status=RunStatus.DONE)
    run.workflow = models.Workflow(slug=db.COMPARE_QUOTES_SLUG)
    with pytest.raises(models.InvalidTransition):
        models.reopen_certificate_run(run, db.CERTIFICATE_SLUG)
    with pytest.raises(models.InvalidTransition):
        models.transition_run(run, RunStatus.QUEUED)  # DONE stays terminal in the shared table
    cert_run = models.Run(status=RunStatus.DONE)
    cert_run.workflow = models.Workflow(slug=db.CERTIFICATE_SLUG)
    models.reopen_certificate_run(cert_run, db.CERTIFICATE_SLUG)
    assert cert_run.status == RunStatus.QUEUED
    failed = models.Run(status=RunStatus.FAILED)
    failed.workflow = models.Workflow(slug=db.CERTIFICATE_SLUG)
    with pytest.raises(models.InvalidTransition):
        models.reopen_certificate_run(failed, db.CERTIFICATE_SLUG)


# --------------------------------------------------------------------- Issue (D5, D13, D16)


def test_issue_gates_and_idempotency(api):
    view = create(api, [dec(), AI_SCHEDULED], description="Roofing")
    body = issue(api, view, status=409)
    assert body["error"]["code"] == "claims_open" and body["error"]["open"] == 1
    patch(api, view, "description", {"status": "resolved", "note": "Contract wording"})
    view = cert(api, view["run"]["id"])
    assert issue(api, view, version=view["version"] + 5, status=409)["error"]["code"] == "version_changed"
    first = issue(api, view)
    assert first["created"] is True and first["issue"]["version"] == view["version"]
    second = issue(api, view)
    assert second["created"] is False and second["issue"]["id"] == first["issue"]["id"]
    with api.ctx.factory() as s:
        rows = s.scalars(select(models.CertificateIssue)).all()
        assert len(rows) == 1
        snap = {c["key"]: c for c in rows[0].claims}
        assert snap["description"]["note"] == "Contract wording" and snap["description"]["status"] == "resolved"
        assert rows[0].holder == {"name": HOLDER, "address": "4 Elm Street\nAlbany NY"}
    pdf = api.client.get(first["issue"]["pdf_url"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and export_certificate.page_count(pdf.content) == 1
    after = second["certificate"]
    assert after["issue"]["id"] == first["issue"]["id"] and "issued" in after["blocked"]
    # read-only: edits, decisions and Add document refuse
    r = api.client.patch(f"/api/artifacts/{view['artifact_id']}/cells", json={
        "block_id": "certificate", "row_key": "cert:gl_each_occurrence", "column_key": "A", "value": "$5"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "issued"
    c = claim(after, "description")
    r = api.client.patch(f"/api/runs/{view['run']['id']}/discrepancies/{c['id']}",
                         json={"status": "open", "expected_fingerprint": c["fingerprint"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "issued"


def test_issue_while_running_is_409(api):
    view = create(api, [dec()], wording={})
    with db.session_scope(api.ctx.factory) as s:
        s.get(models.Run, view["run"]["id"]).status = RunStatus.RUNNING
    assert issue(api, view, status=409)["error"]["code"] == "run_active"


def test_no_coverage_blocks_an_issue_of_overridden_policies_only(api):
    view = create(api, [AUTO_EXPIRED], wording={})
    patch(api, view, "in_force:A", {"status": "resolved", "note": "Renewal bound, paper pending"})
    view = cert(api, view["run"]["id"])
    assert issue(api, view, status=409)["error"]["code"] == "no_coverage"


def test_render_failure_leaves_no_record(api, monkeypatch):
    view = create(api, [dec()], wording={})

    def broken(_data):
        raise RuntimeError("renderer down")

    monkeypatch.setattr(export_certificate, "render", broken)
    assert issue(api, view, status=500)["error"]["code"] == "issue_failed"
    with api.ctx.factory() as s:
        assert s.scalars(select(models.CertificateIssue)).all() == []
        assert s.scalars(select(models.Holder)).all() == []


def test_new_version_makes_an_editable_draft(api):
    view = create(api, [dec()], wording={})
    url = f"/api/certificates/{view['run']['id']}/new-version"
    assert api.client.post(url).status_code == 409  # not issued yet
    issue(api, view)
    fresh = api.client.post(url)
    assert fresh.status_code == 200, fresh.text
    body = fresh.json()
    assert body["version"] == view["version"] + 1 and body["issue"] is None and body["can_issue"]
    assert len(body["issues"]) == 1 and body["issues"][0]["version"] == view["version"]
    again = issue(api, body)
    assert again["created"] and again["issue"]["version"] == body["version"]


# --------------------------------------------------------------------- holders, documents, queue


def test_holder_is_remembered_per_client(api):
    view = create(api, [dec()], wording={})
    issue(api, view)
    view2 = create(api, [dec(number="GL-2")], wording={}, client="  cedar   hollow llc ",
                   holder_address="New address")
    issue(api, view2)
    out = api.client.get("/api/certificates/holders", params={"client": "Cedar Hollow LLC"}).json()
    assert [(h["name"], h["address"]) for h in out["holders"]] == [(HOLDER, "New address")]
    assert api.client.get("/api/certificates/holders", params={"client": "Other"}).json()["holders"] == []


def test_client_documents_say_when_they_were_read(api):
    view = create(api, [dec()], wording={})
    out = api.client.get("/api/certificates/documents", params={"client": "cedar hollow llc"}).json()
    (doc,) = out["documents"]
    assert doc["id"] == view["run"]["documents"][0]["document_id"] and doc["already_read"] and doc["read_at"]
    # picking it again reuses the read (C3): 0 extract calls
    calls = len([r for r in api.provider.requests if r.role is CallRole.EXTRACT])
    r = api.client.post("/api/runs", json={"document_ids": [doc["id"]], "workflow": "certificate",
                                           "parameters": params(holder_name="Another Holder", wording={})})
    api.events(r.json()["run"]["id"])
    assert len([r for r in api.provider.requests if r.role is CallRole.EXTRACT]) == calls
    assert cert(api, r.json()["run"]["id"])["run"]["documents"][0]["reused_from_run_id"] == view["run"]["id"]


def test_certificate_queue_and_main_queue(api):
    view = create(api, [dec(), AI_SCHEDULED], description="Roofing")
    main = api.client.get("/api/queue", params={"line": "commercial"}).json()
    assert view["run"]["id"] not in [i["run_id"] for g in ("in_progress", "needs_review", "ready", "needs_attention",
                                                           "done_recently") for i in main[g]]
    q = api.client.get("/api/certificates/queue").json()
    (item,) = q["needs_attention"]
    assert item["run_id"] == view["run"]["id"] and item["summary"] == "1 check to resolve"
    assert item["client"] == "Cedar Hollow LLC"
    patch(api, view, "description", {"status": "resolved", "note": "ok"})
    q = api.client.get("/api/certificates/queue").json()
    assert [i["summary"] for i in q["ready"]] == ["Ready to issue"]
    issue(api, cert(api, view["run"]["id"]))
    q = api.client.get("/api/certificates/queue").json()
    assert [i["summary"] for i in q["done_recently"]] == ["Issued"] and q["ready"] == []
    acts = api.client.get("/api/runs").json()["runs"]
    assert view["run"]["id"] in [r["id"] for r in acts]  # Activity lists it like any run


@pytest.mark.parametrize("fmt", ["pdf", "docx", "xlsx"])
def test_report_export_tolerates_the_certificate_blocks(api, fmt):
    view = create(api, [dec(), AI_SCHEDULED])
    r = api.client.get(f"/api/artifacts/{view['artifact_id']}/export", params={"format": fmt})
    assert r.status_code == 200, r.text
    from backend import chat
    content = models.ArtifactContent.model_validate(api.artifact(view["run"]["id"])["content"])
    assert "Additional insured" in "\n".join(chat._section_prose(s) for s in content.sections)
    assert chat._outline(content)["discrepancies"]


def test_certificate_client_comes_from_the_documents_when_not_typed(api):
    view = create(api, [dec()], wording={}, client=None)
    assert view["client"] == "Cedar Hollow LLC" and view["client_source"] == "extracted"
