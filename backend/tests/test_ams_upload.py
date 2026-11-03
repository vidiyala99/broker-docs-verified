"""Slice 3 T1 (R1, D4, D12): the AMS upload path beside the PDF-only one.

POST /api/documents/ams accepts only JSON or CSV, renders it to a PDF that the
normal file, parse and verify path serves unchanged, and keeps the original
file. POST /api/documents still rejects anything that is not a PDF.
"""

from __future__ import annotations

import json

from backend import ams, models, uploads
from backend.pipeline import parse
from backend.pipeline.runner import files_dir
from api_support import api, make_pdf  # noqa: F401

SAMPLE = {"PolicyNumber": "HO-4471-2026", "Named Insured": "Cedar Hollow LLC", "Cov A": "$350,000",
          "Agent Notes": "Called about the roof"}


def _post_ams(api, *files, content_type="application/json"):
    r = api.client.post("/api/documents/ams",
                        files=[("files", (name, data, content_type)) for name, data in files])
    assert r.status_code == 200, r.text
    return r.json()


def test_json_upload_is_rendered_to_a_pdf_and_the_original_is_kept(api):
    data = json.dumps(SAMPLE).encode()
    out = _post_ams(api, ("ams_export.json", data))
    assert out["rejected"] == []
    doc = out["documents"][0]
    assert doc["kind"] == "ams" and doc["filename"] == "ams_export.json"
    assert doc["page_count"] == 1 and doc["parse_error"] is None
    assert doc["ams"] == {"format": "json", "field_count": 4, "unmapped": ["Agent Notes"], "notices": []}

    served = api.client.get(f"/api/documents/{doc['id']}/file")
    assert served.status_code == 200 and served.headers["content-type"] == "application/pdf"
    assert parse.is_pdf_bytes(served.content)
    text = "\n".join(p.text for p in parse.parse_pdf(served.content).pages)
    assert "Cov A: $350,000" in text

    with api.ctx.factory() as s:
        row = s.get(models.Document, doc["id"])
        stored = row.stored_name
        assert row.size_bytes == len(data)
    assert uploads.load_original(stored) == data
    assert ams.facts_from_original(uploads.load_original(stored)).facts[0].field == "policy_number"


def test_csv_upload_is_accepted(api):
    data = b"PolicyNumber,Cov A\nHO-1,\"$350,000\"\n"
    out = _post_ams(api, ("export.CSV", data), content_type="text/csv")
    assert out["rejected"] == [] and out["documents"][0]["ams"]["format"] == "csv"


def test_ams_path_rejects_a_pdf_and_other_types(api):
    pdf = make_pdf(["Total Premium: $1"])
    out = _post_ams(api, ("policy.pdf", pdf), ("notes.txt", b"a,b\n1,2\n"), ("fake.json", pdf),
                    ("x.csv", b"{\"a\": 1}"), ("y.json", b"a,b\n1,2\n"))
    assert out["documents"] == []
    assert [r["code"] for r in out["rejected"]] == ["not_ams"] * 5
    assert all("JSON or CSV" in r["message"] for r in out["rejected"])


def test_ams_path_rejects_malformed_and_oversized_input_with_named_limits(api):
    big = json.dumps({"a": "x" * (ams.DEFAULT_AMS_LIMITS.max_bytes + 10)}).encode()
    many = json.dumps({f"f{i}": "1" for i in range(ams.DEFAULT_AMS_LIMITS.max_fields + 1)}).encode()
    out = _post_ams(api, ("big.json", big), ("bad.json", b"{\"a\": "), ("many.json", many),
                    ("empty.json", b""))
    codes = {r["filename"]: r for r in out["rejected"]}
    assert codes["big.json"]["code"] == "too_large"
    assert codes["big.json"]["limit"] == ams.DEFAULT_AMS_LIMITS.max_bytes
    assert codes["bad.json"]["code"] == "invalid_ams"
    assert codes["many.json"]["code"] == "too_many_fields"
    assert codes["many.json"]["limit"] == ams.DEFAULT_AMS_LIMITS.max_fields
    assert codes["empty.json"]["code"] == "empty"
    assert out["documents"] == []
    assert [p.name for p in files_dir().iterdir()] == []  # nothing stored for a rejection


def test_ams_path_caps_files_per_request(api):
    n = api.settings.limits.max_documents_per_run
    files = [(f"a{i}.json", json.dumps({"Policy Number": f"P-{i}"}).encode()) for i in range(n + 1)]
    out = _post_ams(api, *files)
    assert len(out["documents"]) == n
    assert [r["code"] for r in out["rejected"]] == ["too_many_documents"]


def test_pdf_path_still_rejects_json_and_csv(api):
    """D12 regression contract: the plain PDF upload stays PDF-only (uploads.py not_pdf)."""
    out = api.upload(("ams.json", json.dumps(SAMPLE).encode()), ("ams.csv", b"a,b\n1,2\n"))
    assert out["documents"] == []
    assert [r["code"] for r in out["rejected"]] == ["not_pdf", "not_pdf"]


def test_pdf_path_still_accepts_a_pdf_as_a_quote(api):
    out = api.upload(("q.pdf", make_pdf(["Total Premium: $1"])))
    assert out["rejected"] == [] and out["documents"][0]["kind"] == "quote"
    assert "ams" not in out["documents"][0]


def test_deleting_an_ams_document_removes_the_pdf_and_the_original(api):
    out = _post_ams(api, ("a.json", json.dumps(SAMPLE).encode()))
    doc_id = out["documents"][0]["id"]
    assert len(list(files_dir().iterdir())) == 2
    assert api.client.delete(f"/api/documents/{doc_id}").status_code == 204
    assert list(files_dir().iterdir()) == []


def test_display_name_is_sanitized_and_never_reaches_the_pdf(api):
    out = _post_ams(api, ("../../evil<script>.json", json.dumps(SAMPLE).encode()))
    doc = out["documents"][0]
    assert doc["filename"] == "evilscript.json"
    pdf = api.client.get(f"/api/documents/{doc['id']}/file").content
    text = "\n".join(p.text for p in parse.parse_pdf(pdf).pages)
    assert "evil" not in text
