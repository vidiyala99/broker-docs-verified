"""Slice 3 T8: the D12 regression contract for interfaces Policy Check changed.

1. Runs without slots still land in slot "quotes" and dedupe exactly as before.
2. Every stored artifact version still validates after the Block union grew.
3. Export (DOCX, PDF, XLSX) and chat tolerate a DiscrepancyBlock, on a real Policy Check run.
4. The PDF upload still rejects non-PDF (not_pdf); the AMS path accepts only JSON or CSV.
"""

from __future__ import annotations

import io
import json

import fitz
import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from api_support import DOC_A, DOC_B, context_payload, make_api, user_text
from backend import db, models
from backend.providers import CallRole
from backend.schemas import ArtifactContent, DiscrepancyBlock, RunStatus
from pipeline_fakes import GatedProvider, policy_responder, smart_responder
from test_policy_check_run import AMS, EXPIRING, RENEWAL
from test_runner import Harness, go

# --------------------------------------------------------------------- 1. no slots


@pytest.mark.parametrize("slug", [db.COMPARE_QUOTES_SLUG, db.PROPOSAL_BUILDER_SLUG])
def test_runs_without_slots_land_in_quotes_and_dedupe_as_before(pdf_factory, slug):
    h = Harness(pdf_factory)
    try:
        with h.factory() as s:
            wf_id = s.scalar(select(models.Workflow.id).where(models.Workflow.slug == slug))
        a, b = h.add_doc(DOC_A), h.add_doc(DOC_B)
        gated = GatedProvider(smart_responder())

        async def main():
            m = h.manager(gated)
            first = m.submit(wf_id, [a, b])
            again = m.submit(wf_id, [a, b])
            swapped = m.submit(wf_id, [b, a])
            gated.gate.set()
            for rid in {first[0], swapped[0]}:
                await m.wait(rid)
            await m.shutdown()
            return first, again, swapped

        (r1, c1), (r2, c2), (r3, c3) = go(main())
        assert c1 and not c2 and r2 == r1
        assert c3 and r3 != r1
        with h.factory() as s:
            for rid in (r1, r3):
                run = s.get(models.Run, rid)
                assert [rd.slot for rd in run.documents] == ["quotes", "quotes"]
                assert run.status == RunStatus.DONE
                # a slot-less run never takes the Policy Check path
                blocks = [blk for _, blk in run.artifact.latest_content().iter_blocks()]
                assert blocks and not any(isinstance(blk, DiscrepancyBlock) for blk in blocks)
    finally:
        h.engine.dispose()


# --------------------------------------------------------------------- 2. stored versions

LEGACY_VERSION = {  # a slice 1 version as stored: no notice, conflicts or discrepancy keys anywhere
    "sections": [
        {"id": "executive_summary", "number": "1", "title": "Executive Summary", "blocks": [
            {"type": "paragraph", "id": "p1", "segments": [{"text": "Two quotes were compared."}]}]},
        {"id": "common_declarations", "number": "2", "title": "Common Declarations", "blocks": [
            {"type": "table", "id": "common_declarations-table", "title": None,
             "columns": [{"key": "A", "label": "Harborline"}],
             "rows": [{"key": "total_premium", "label": "Total Premium",
                       "cells": {"A": {"value": "$26,391.14", "state": "verified"}}}]},
            {"type": "issue", "id": "i1", "code": "ISSUE-001", "title": "Premium gap"}]},
    ],
}


@pytest.fixture
def pc_api(tmp_path, monkeypatch):
    policy = policy_responder()

    def respond(req):
        if req.role is CallRole.CHAT:
            item = context_payload(req)["report"]["discrepancies"][0]
            slot = next(s for s in item["slots"] if s["role"] == "renewal")
            return {"answer": [{"text": f"The renewal states {slot['value']}.",
                                "passage_ids": slot["passage_ids"][:1]}], "proposed_edits": []}
        if req.role is CallRole.SUGGEST:
            return {"suggestions": ["What changed at renewal?"]}
        if req.role is CallRole.EXTRACT and "Also return forms" in user_text(req):
            return policy(req)  # the policy-check combined pass (D13)
        return smart_responder()(req)  # Compare Quotes extraction and compose

    a = make_api(tmp_path, monkeypatch, respond)
    yield a
    a.client.__exit__(None, None, None)


def _policy_run(api) -> int:
    exp, ren = api.upload_pages(EXPIRING, "exp.pdf"), api.upload_pages(RENEWAL, "ren.pdf")
    r = api.client.post("/api/documents/ams", files=[("files", ("ams.json", json.dumps(AMS).encode(),
                                                                  "application/json"))])
    amsd = r.json()["documents"][0]["id"]
    r = api.client.post("/api/runs", json={"document_ids": [exp, ren, amsd], "workflow": "policy-check",
                                           "slots": {str(exp): "expiring", str(ren): "renewal", str(amsd): "ams"}})
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    return run_id


def test_every_stored_version_still_validates_and_serves(pc_api):
    run, _ = pc_api.run([pc_api.upload_pages(DOC_A)])
    with db.session_scope(pc_api.ctx.factory) as s:  # a version stored before the union grew
        art = s.get(models.Run, run["id"]).artifact
        s.add(models.ArtifactVersion(artifact_id=art.id, version=2, content=LEGACY_VERSION, note="legacy"))
        art.current_version = 2
        art_id = art.id
    policy_run = _policy_run(pc_api)
    for v in (1, 2):
        r = pc_api.client.get(f"/api/artifacts/{art_id}", params={"version": v})
        assert r.status_code == 200, r.text
        for fmt in ("pdf", "docx", "xlsx"):
            assert pc_api.client.get(f"/api/artifacts/{art_id}/export",
                                     params={"format": fmt, "version": v}).status_code == 200
    assert pc_api.client.get(f"/api/runs/{policy_run}/artifact").status_code == 200
    with pc_api.ctx.factory() as s:
        rows = s.scalars(select(models.ArtifactVersion)).all()
        assert len(rows) >= 3
        for row in rows:
            ArtifactContent.model_validate(row.content)
        legacy = s.scalar(select(models.ArtifactVersion).where(models.ArtifactVersion.note == "legacy"))
        assert ArtifactContent.model_validate(legacy.content).passage_ids() == set()


# --------------------------------------------------------------------- 3. export and chat


def test_exports_render_the_discrepancies_of_a_policy_check_run(pc_api):
    run_id = _policy_run(pc_api)
    art = pc_api.artifact(run_id)
    title = next(b["title"] for sec in art["content"]["sections"] for b in sec["blocks"]
                 if b["type"] == "discrepancy")
    for fmt in ("docx", "pdf", "xlsx"):
        r = pc_api.client.get(f"/api/artifacts/{art['id']}/export", params={"format": fmt})
        assert r.status_code == 200, (fmt, r.text)
        if fmt == "pdf":
            doc = fitz.open(stream=r.content, filetype="pdf")
            text = " ".join(" ".join(p.get_text().split()) for p in doc)
            doc.close()
            assert title in text and "Critical" in text and "N/A" in text and "$350,000" in text
        if fmt == "xlsx":
            wb = load_workbook(io.BytesIO(r.content))
            assert "Discrepancies" in wb.sheetnames
            ws = wb["Discrepancies"]
            values = [c.value for row in ws.iter_rows() for c in row if c.value]
            assert title in values and "Critical" in values and "N/A" in values


def test_chat_answers_on_a_policy_check_run(pc_api):
    run_id = _policy_run(pc_api)
    r = pc_api.client.post(f"/api/runs/{run_id}/messages", json={"content": "What changed?"})
    assert r.status_code == 201, r.text
    bot = r.json()["assistant_message"]
    assert bot["content"].startswith("The renewal states") and bot["passage_ids"]


# --------------------------------------------------------------------- 4. uploads


def test_pdf_upload_still_rejects_non_pdf(pc_api):
    for name, data in (("ams.json", json.dumps(AMS).encode()), ("ams.csv", b"Policy Number\nP-1\n"),
                       ("notes.txt", b"hello")):
        r = pc_api.client.post("/api/documents", files=[("files", (name, data, "application/octet-stream"))])
        assert r.status_code == 200
        out = r.json()
        assert out["documents"] == [] and out["rejected"][0]["code"] == "not_pdf", name


@pytest.mark.parametrize("name,data,ok", [
    ("ams.json", json.dumps(AMS).encode(), True),
    ("ams.csv", b"Policy Number,Cov A\nP-1,$1\n", True),
    ("ams.txt", json.dumps(AMS).encode(), False),
    ("ams.xml", b"<ams><policy>1</policy></ams>", False),
    ("ams.pdf", b"%PDF-1.4\n%fake", False),
    ("ams.json", b"Policy Number,Cov A\nP-1,$1\n", False),  # content disagrees with the extension
])
def test_ams_path_accepts_only_json_or_csv(pc_api, name, data, ok):
    r = pc_api.client.post("/api/documents/ams", files=[("files", (name, data, "application/octet-stream"))])
    assert r.status_code == 200
    out = r.json()
    if ok:
        assert out["rejected"] == [] and out["documents"][0]["kind"] == "ams"
    else:
        assert out["documents"] == [] and out["rejected"][0]["code"] == "not_ams"
