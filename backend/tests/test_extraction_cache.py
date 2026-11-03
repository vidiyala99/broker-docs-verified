"""Slice 4 C2: the extraction cache key (D8, D17), the cross-document copy (D3) and the OCR stamp."""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import select

from api_support import DOC_A, make_api
from backend import db, models
from backend.pipeline import cache, extract
from backend.providers import CallRole, ProviderConfig
from backend.schemas import CERTIFICATE_FIELDS, POLICY_CHECK_FIELDS, FactField
from conftest import make_pdf
from pipeline_fakes import any_responder

BASE = ProviderConfig()


def _key(fields=POLICY_CHECK_FIELDS, instructions="x", config=BASE, **kw):
    return cache.extraction_key(fields, instructions, config, **kw)


def test_key_is_stable_and_every_input_change_misses(monkeypatch):
    k = _key()
    assert k == _key() and len(k) == 64
    changed_field = [FactField(**{**POLICY_CHECK_FIELDS[0].model_dump(), "description": "other"}),
                     *POLICY_CHECK_FIELDS[1:]]
    models_ = dict(BASE.models)
    other_extract = dataclasses.replace(BASE, models={**models_, CallRole.EXTRACT: "another-model"})
    other_vision = dataclasses.replace(BASE, models={**models_, CallRole.VISION: "another-vision"})
    variants = {
        "fields": _key(fields=changed_field),
        "workflow": _key(fields=CERTIFICATE_FIELDS),
        "instructions": _key(instructions="y"),
        "extract model": _key(config=other_extract),
        "vision model": _key(config=other_vision),
        "version": _key(version="next"),
    }
    assert len({k, *variants.values()}) == len(variants) + 1, variants
    monkeypatch.setattr(extract, "SYSTEM_PROMPT", extract.SYSTEM_PROMPT + " ")
    assert _key() != k  # the extraction prompt text is part of the key
    monkeypatch.undo()
    monkeypatch.setattr(extract, "OCR_SYSTEM_PROMPT", "transcribe")
    assert _key() != k  # the OCR prompt is part of the key (D17)


def test_ocr_key_covers_vision_model_and_prompt(monkeypatch):
    base = cache.ocr_key(BASE)
    assert cache.ocr_key(dataclasses.replace(BASE, models={**BASE.models, CallRole.VISION: "v2"})) != base
    monkeypatch.setattr(extract, "OCR_SYSTEM_PROMPT", "transcribe it")
    assert cache.ocr_key(BASE) != base


@pytest.mark.parametrize("value,on", [("off", False), ("OFF", False), ("0", False), ("false", False),
                                      ("on", True), ("", True), (None, True)])
def test_env_switch(monkeypatch, value, on):
    if value is None:
        monkeypatch.delenv(cache.ENV_SWITCH, raising=False)
    else:
        monkeypatch.setenv(cache.ENV_SWITCH, value)
    assert cache.enabled_from_env() is on


SCANNED_TEXT = {1: "Carrier: Harborline Specialty\nNamed Insured: Cedar Hollow LLC\nTotal Premium: $26,391.14"}


@pytest.fixture
def scan_api(tmp_path, monkeypatch):
    monkeypatch.delenv(cache.ENV_SWITCH, raising=False)
    a = make_api(tmp_path, monkeypatch, any_responder(ocr_pages=SCANNED_TEXT))
    yield a
    a.client.__exit__(None, None, None)


def _scan_pdf() -> bytes:
    return make_pdf([None], image_pages={1: (36, 36, 576, 756)})


def _run(a, ids):
    r = a.client.post("/api/runs", json={"document_ids": ids})
    assert r.status_code == 201, r.text
    a.events(r.json()["run"]["id"])
    return a.client.get(f"/api/runs/{r.json()['run']['id']}").json()


def _calls(a, role):
    return sum(1 for r in a.provider.requests if r.role is role)


def test_ocr_text_is_stamped_and_copied_with_the_read(scan_api):
    a = scan_api
    pdf = _scan_pdf()
    first = _run(a, [a.upload(("scan.pdf", pdf))["documents"][0]["id"]])
    assert first["status"] == "done" and _calls(a, CallRole.VISION) == 1
    with a.ctx.factory() as s:
        page = s.scalar(select(models.Page))
        assert page.ocr and page.ocr_key == cache.ocr_key(a.provider.config)
    doc2 = a.upload(("scan again.pdf", pdf))["documents"][0]["id"]
    second = _run(a, [doc2])
    assert second["documents"][0]["reused_from_run_id"] == first["id"]
    assert _calls(a, CallRole.VISION) == 1 and _calls(a, CallRole.EXTRACT) == 1
    with a.ctx.factory() as s:
        copied = s.scalars(select(models.Page).where(models.Page.document_id == doc2)).all()
        assert [p.ocr_text for p in copied] == [SCANNED_TEXT[1]] and copied[0].ocr_key


def test_another_ocr_method_copies_the_text_layer_only_and_reads_live(scan_api):
    a = scan_api
    pdf = _scan_pdf()
    first = _run(a, [a.upload(("scan.pdf", pdf))["documents"][0]["id"]])
    with db.session_scope(a.ctx.factory) as s:  # the cached page was OCR'd by another method
        for p in s.scalars(select(models.Page)):
            p.ocr_key = "another-ocr-method"
    doc2 = a.upload(("scan again.pdf", pdf))["documents"][0]["id"]
    second = _run(a, [doc2])
    assert second["status"] == "done" and second["documents"][0]["reused_from_run_id"] is None
    assert _calls(a, CallRole.VISION) == 2 and _calls(a, CallRole.EXTRACT) == 2  # re-OCR'd, read live
    assert first["id"] != second["id"]
    with a.ctx.factory() as s:
        page = s.scalar(select(models.Page).where(models.Page.document_id == doc2))
        assert page.ocr_key == cache.ocr_key(a.provider.config)


def test_cache_copy_failure_falls_back_to_a_live_read(scan_api, monkeypatch):
    a = scan_api
    pdf = make_pdf(DOC_A)
    _run(a, [a.upload(("a.pdf", pdf))["documents"][0]["id"]])

    def broken(*_a, **_k):
        raise RuntimeError("copy bug")

    monkeypatch.setattr(cache, "copy_read", broken)
    second = _run(a, [a.upload(("a2.pdf", pdf))["documents"][0]["id"]])
    assert second["status"] == "done" and second["documents"][0]["reused_from_run_id"] is None
    assert _calls(a, CallRole.EXTRACT) == 2


def test_a_read_made_with_the_cache_off_never_serves_a_later_hit(scan_api, monkeypatch):
    """Off means no reuse in either direction: a run read with the cache off stores no key, so a later
    run with the cache on still reads live. This is what keeps the dev seed's reads (made off, D9) from
    serving e2e uploads of the same fixture bytes on the dev server, where the cache is on."""
    a = scan_api
    pdf = make_pdf(DOC_A)
    monkeypatch.setenv(cache.ENV_SWITCH, "off")
    first = _run(a, [a.upload(("a.pdf", pdf))["documents"][0]["id"]])
    assert first["status"] == "done"
    with a.ctx.factory() as s:
        assert s.scalar(select(models.RunDocument.extraction_key)) is None
    monkeypatch.delenv(cache.ENV_SWITCH)
    second = _run(a, [a.upload(("a2.pdf", pdf))["documents"][0]["id"]])
    assert second["status"] == "done" and second["documents"][0]["reused_from_run_id"] is None
    assert _calls(a, CallRole.EXTRACT) == 2
    third = _run(a, [a.upload(("a3.pdf", pdf))["documents"][0]["id"]])  # the cache-on read does serve
    assert third["documents"][0]["reused_from_run_id"] == second["id"]
    assert _calls(a, CallRole.EXTRACT) == 2


def test_another_workflow_does_not_reuse_a_compare_read(scan_api):
    a = scan_api
    pdf = make_pdf(DOC_A)
    doc = a.upload(("a.pdf", pdf))["documents"][0]["id"]
    _run(a, [doc])
    r = a.client.post("/api/runs", json={"document_ids": [doc], "workflow": "proposal-builder",
                                         "parameters": {"template_id": "standard"}})
    assert r.status_code == 201, r.text
    a.events(r.json()["run"]["id"])
    run = a.client.get(f"/api/runs/{r.json()['run']['id']}").json()
    assert run["documents"][0]["reused_from_run_id"] is None  # other fields: another key
    assert _calls(a, CallRole.EXTRACT) == 2
