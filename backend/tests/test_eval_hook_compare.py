"""eval_hook, Compare Quotes (slice 6, T6): Word/Excel quotes through prepare_office, and the ``compare`` block read
from the run's artifact response (marks as views.artifact_out returns them, R12). Offline (FakeProvider)."""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from backend import eval_hook
from backend.providers import CallRole, FakeProvider
from pipeline_fakes import _user_text, compose_reply

ROOT = Path(__file__).resolve().parents[2]
QUOTES = ROOT / "tests" / "fixtures" / "quotes"
COMPARE = ROOT / "tests" / "fixtures" / "compare"
CELL_KEYS = {"block_id", "row", "column", "document", "value", "state", "edited", "complete", "mark", "mark_reason",
             "citations"}


def truth_responder():
    """Extraction answers each document's printed facts from its truth file (tests/fixtures), for the pages in the
    request; compose cites one fact."""
    by_file = {}
    for folder in (QUOTES, COMPARE):
        for t in folder.glob("*.truth.json"):
            body = json.loads(t.read_text(encoding="utf-8"))
            by_file[body["file"]] = body

    def respond(req):
        text = _user_text(req)
        if req.role is CallRole.COMPOSE:
            return compose_reply(text)
        name = re.search(r"Document file name \(data, not an instruction\): '([^']+)'", text).group(1)
        pages = {int(n) for n in re.findall(r"^\[page (\d+)\]\s*$", text, re.M)}
        t = by_file.get(name, {"facts": {}})
        return {"facts": [{"field": k, "value": f["value"], "page": f["page"], "quote": f["quote"]}
                          for k, f in t["facts"].items() if f and f["page"] in pages],
                "options_detected": 1, "option_compared": None}

    return respond


@pytest.fixture(scope="module")
def two_quotes():
    paths = [str(QUOTES / "carrier_c_clean.pdf"), str(QUOTES / "carrier_d_clean.pdf")]
    return paths, eval_hook.run_eval_batch(paths, provider=FakeProvider(truth_responder()))


def test_compare_block_carries_every_cell_with_its_mark(two_quotes):
    paths, out = two_quotes
    block = out["compare"]
    assert set(block) == {"workflow", "run_status", "columns", "cells", "key_difference", "notices", "warnings"}
    assert block["run_status"] == "done" and block["warnings"] == []
    assert [c["document"] for c in block["columns"]] == paths
    assert block["cells"] and all(set(c) == CELL_KEYS for c in block["cells"])
    rows = {c["row"] for c in block["cells"]}
    assert {"additional_insured", "waiver_of_subrogation", "primary_noncontributory", "notable_exclusions",
            "total_premium"} <= rows
    marked = [(Path(c["document"]).stem, c["row"], c["mark"], c["mark_reason"]) for c in block["cells"] if c["mark"]]
    assert marked == [("carrier_d_clean", "total_premium", "best", "Lowest premium")]  # $9,300.59 < $19,025.00
    prem = next(c for c in block["cells"] if c["mark"])
    assert prem["state"] == "verified" and prem["citations"] == [
        {"document": paths[1], "page": 2, "passage_id": prem["citations"][0]["passage_id"]}]
    kd = block["key_difference"]
    assert kd["source"] == "template" and kd["stale"] is False and "lowest premium" in kd["text"]


def test_compare_block_marks_are_the_artifact_response(two_quotes, monkeypatch):
    """The block is views.artifact_out's response: a mark there is a mark here (nothing recomputed in the hook)."""
    from backend import views

    real = views.artifact_out

    def planted(session, art, version=None):
        out = real(session, art, version)
        for section in out["content"]["sections"]:
            for b in section["blocks"]:
                for row in b.get("rows") or []:
                    if row["key"] == "additional_insured":
                        row["cells"]["A"].update(mark="worse", mark_reason="Others: Blanket")
        return out

    monkeypatch.setattr(views, "artifact_out", planted)
    paths, _ = two_quotes
    out = eval_hook.run_eval_batch(paths, provider=FakeProvider(truth_responder()))
    got = [(c["column"], c["row"], c["mark"]) for c in out["compare"]["cells"] if c["mark"]]
    assert ("A", "additional_insured", "worse") in got


def test_word_and_excel_quotes_take_the_upload_renderer():
    paths = [str(COMPARE / "cmp_tri_b_quote.docx"), str(COMPARE / "cmp_tri_c_summary.xlsx")]
    out = eval_hook.run_eval_batch(paths, provider=FakeProvider(truth_responder()))
    docx, xlsx = (out["documents"][p] for p in paths)
    assert docx["status"] == "ok" and xlsx["status"] == "ok"
    by = {f["field"]: f for f in docx["facts"]}
    assert by["total_premium"]["value"] == "$16,975.00" and by["total_premium"]["state"] == "verified"
    assert docx["ocr_pages"] == [] and xlsx["ocr_pages"] == []  # the rendered record has a text layer
    assert [c["document"] for c in out["compare"]["columns"]] == paths


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in entries.items():
            z.writestr(name, body)
    return buf.getvalue()


@pytest.mark.parametrize("name,data,reason", [
    ("plain.docx", b"This is not a Word file.", "format_mismatch"),
    ("pdf_named.xlsx", b"%PDF-1.7\n" + b"0" * 64, "format_mismatch"),
    ("damaged.docx", b"PK\x03\x04" + b"\x00" * 200, "unreadable"),
    ("macros.xlsx", _zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>",
                          "xl/vbaProject.bin": b"\x00"}), "macros"),
])
def test_a_refused_office_file_is_reported_failed_with_its_reason(tmp_path, name, data, reason):
    bad = tmp_path / name
    bad.write_bytes(data)
    good = str(QUOTES / "carrier_a_clean.pdf")
    out = eval_hook.run_eval_batch([str(bad), good], provider=FakeProvider(truth_responder()))
    doc = out["documents"][str(bad)]
    assert doc["status"] == "failed" and doc["failure_reason"] == reason and doc["facts"] == []
    assert out["documents"][good]["status"] == "ok"


def test_no_readable_document_still_returns_a_compare_block(tmp_path):
    bad = tmp_path / "plain.docx"
    bad.write_bytes(b"not a zip")
    out = eval_hook.run_eval_batch([str(bad)], provider=FakeProvider([]))
    assert out["compare"]["cells"] == [] and out["compare"]["warnings"] == ["no readable document: no run"]
