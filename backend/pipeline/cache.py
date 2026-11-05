r"""Extraction cache (slice 4, C2; closes TODO 2c): reuse an earlier read of the same file.

Applies to every workflow (C8). Before a run reads a document:

    document.sha256 + extraction_key(fields, prompts, models, EXTRACTION_VERSION)
        │
        ├─ cache off (BROKERDOCS_EXTRACTION_CACHE=off, or RunManager(extraction_cache=False)) ─► read live,
        │                                       and store no key: an off read never serves a later hit
        ├─ AMS export (facts come from its key table, no model call) ──────────────────────────► read live
        ▼
    latest run_document in ANOTHER run that is DONE, has the same key, and whose document has the
    same sha256 (only DONE documents serve hits: FAILED, CANCELLED and budget-failed ones never do)
        │ none ──► read live; the document is stored with its key so a later run can reuse it
        ▼
    same Document row ───────► copy the facts (passages are per document, so they are shared)
    another Document row ────► copy its Page rows and the cited passages onto the new document and
                               remap every fact's passage ids (D3), so chips open the broker's own upload;
                               OCR text is copied only when the source page's ocr_key is the current
                               OCR method (D17); otherwise only the text layer is copied, and the run
                               reads the document live (OCR runs again)
        ▼
    run_document DONE with reused_from_run_id (the source run) and read_at (the first live read)

Key (D8, D17): sha256 of the field definitions, the extraction prompt texts (system prompt, window,
forms and endorsements notes), the workflow instructions, the extract model id, the vision model id, the
OCR prompt and EXTRACTION_VERSION. Bump EXTRACTION_VERSION whenever verify, merge or windows behaviour
changes: nothing else notices a code change, so old cached facts would keep the old behaviour.

Evals always read live: every eval batch runs on a fresh in-memory database and eval_hook turns the
cache off (D9). The dev seed runs its scenario runs with BROKERDOCS_EXTRACTION_CACHE=off (D9), so its
seeded failure, budget and fallback states still come from real reads.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from backend import models
from backend.pipeline import extract
from backend.providers import CallRole, ProviderConfig
from backend.schemas import FactField, RunDocumentStatus

# Before slice 6: "2026-11-04.2". .1 (slice 5a): loss-run rows, sheets and printed totals join the read
# .2: loss-run PDF verify places rows read twice / miscopied on their printed line, reads total lines in code
#     and re-attributes a total's metric by its column (stored rows and totals from .1 would differ)
EXTRACTION_VERSION = "2026-11-05.2"  # .1 (slice 6): Compare Quotes reads endorsement wording, exclusion titles, the
#     schedule heading and the document kind in the same call (COMPARE_TERMS_NOTE, R1/R9)
# .2: verify's amount rule (merge.labelled_amount): a money field's printed label is dropped from its value, or the
#     value is not cited when the label is another money field's (live eval 2026-11-05, long_120.retention)
ENV_SWITCH = "BROKERDOCS_EXTRACTION_CACHE"
_OFF = {"off", "0", "false", "no", "disabled"}


def enabled_from_env() -> bool:
    return os.environ.get(ENV_SWITCH, "on").strip().lower() not in _OFF


def _digest(payload: object) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ocr_key(config: ProviderConfig) -> str:
    """The OCR method: vision model id and OCR prompt (D17). Stored on each OCR'd page."""
    return _digest({"vision_model": config.model_for(CallRole.VISION), "ocr_prompt": extract.OCR_SYSTEM_PROMPT})


def extraction_key(fields: Sequence[FactField], instructions: str, config: ProviderConfig, *,
                   version: str = EXTRACTION_VERSION, extra: dict | None = None) -> str:
    """What must match before cached facts are reused (D8 + D17). `extra` (slice 5a, R8): what else the read
    depends on, e.g. the loss-run row prompt, window config, header synonym hash and sheet parser version; None
    keeps the key exactly as before for every other workflow."""
    payload = {
        "fields": [f.model_dump(mode="json") for f in fields],
        "prompts": [extract.SYSTEM_PROMPT, extract.WINDOW_NOTE, extract.FORMS_NOTE, extract.ENDORSEMENTS_NOTE,
                    extract.COMPARE_TERMS_NOTE],
        "instructions": instructions or "",
        "extract_model": config.model_for(CallRole.EXTRACT),
        "ocr": ocr_key(config),
        "version": version,
    }
    if extra is not None:
        payload["extra"] = extra
    return _digest(payload)


@dataclass(frozen=True)
class Source:
    run_document_id: int
    run_id: int
    document_id: int


def find_source(session: Session, *, run_id: int, document_id: int, key: str) -> Source | None:
    """The latest DONE run_document of another run read under `key` from a file with the same sha256."""
    doc = session.get(models.Document, document_id)
    if doc is None or not doc.sha256:
        return None
    RD, Doc = models.RunDocument, models.Document
    row = session.execute(
        select(RD.id, RD.run_id, RD.document_id)
        .join(Doc, Doc.id == RD.document_id)
        .where(RD.extraction_key == key, RD.status == str(RunDocumentStatus.DONE), RD.run_id != run_id,
               Doc.sha256 == doc.sha256, Doc.kind != "ams")
        .order_by(RD.id.desc()).limit(1)).first()
    return Source(*row) if row else None


class OcrMismatch(Exception):
    """The source pages were OCR'd by another method: copy the text layer only and read live (D17)."""


def _copy_pages(session: Session, source: models.Document, target: models.Document, current_ocr: str) -> bool:
    """Copy page rows to `target` when it has none. Returns False when a source OCR page was read by
    another OCR method (its OCR text is not copied; the caller reads live)."""
    ocr_ok = all(not p.ocr_text or p.ocr_key == current_ocr for p in source.pages)
    if target.pages:
        return ocr_ok
    target.pages = [
        models.Page(page_no=p.page_no, text=p.text, words=p.words, width=p.width, height=p.height,
                    char_count=p.char_count, image_ratio=p.image_ratio, needs_ocr=p.needs_ocr,
                    ocr_reason=p.ocr_reason,
                    ocr=bool(p.ocr) and ocr_ok, ocr_text=p.ocr_text if ocr_ok else None,
                    ocr_key=p.ocr_key if ocr_ok else None)
        for p in source.pages]
    target.page_count = source.page_count
    target.parse_error = target.parse_message = None
    session.flush()
    return ocr_ok


def copy_read(session: Session, source: Source, target_rd: models.RunDocument, *, current_ocr: str) -> None:
    """Copy the source's facts (and, for another Document, its pages and cited passages) to
    `target_rd`, then mark it DONE as reused. Raises OcrMismatch (after copying the text layer)
    when the OCR method changed. Runs inside the caller's transaction."""
    src_rd = session.get(models.RunDocument, source.run_document_id)
    src_doc, dst_doc = src_rd.document, target_rd.document
    same = src_doc.id == dst_doc.id
    if not same and not _copy_pages(session, src_doc, dst_doc, current_ocr):
        raise OcrMismatch()
    if same and any(p.ocr_text and p.ocr_key != current_ocr for p in src_doc.pages):
        raise OcrMismatch()
    facts = list(session.scalars(select(models.Fact).where(models.Fact.run_id == src_rd.run_id,
                                                          models.Fact.document_id == src_doc.id)))
    remap: dict[int, int] = {}

    def mapped(pid: int | None) -> int | None:
        if pid is None or same:
            return pid
        if pid not in remap:
            p = session.get(models.Passage, pid)
            if p is None:
                return None
            remap[pid] = models.upsert_passage(session, dst_doc.id, p.page_no, verified=bool(p.verified),
                                               rects=p.rects or []).id
        return remap[pid]

    for old in session.scalars(select(models.Fact).where(models.Fact.run_id == target_rd.run_id,
                                                        models.Fact.document_id == dst_doc.id)):
        session.delete(old)
    session.flush()
    for f in facts:
        session.add(models.Fact(
            run_id=target_rd.run_id, document_id=dst_doc.id, field=f.field, value=f.value, cited_page=f.cited_page,
            page_no=f.page_no, quote=f.quote, matched_text=f.matched_text, method=f.method, score=f.score,
            rects=list(f.rects or []), state=f.state, reason=f.reason, repointed_from=f.repointed_from,
            passage_id=mapped(f.passage_id),
            extra_passage_ids=[x for x in (mapped(p) for p in f.extra_passage_ids or []) if x is not None],
            conflicts=[{**c, "passage_id": mapped(c.get("passage_id"))} for c in f.conflicts or []]))
    _copy_loss_read(session, src_rd, target_rd, same=same)
    for step in (RunDocumentStatus.READING, RunDocumentStatus.EXTRACTING, RunDocumentStatus.VERIFYING,
                 RunDocumentStatus.DONE):
        models.transition_run_document(target_rd, step)
    target_rd.option_count, target_rd.option_label = src_rd.option_count or 1, src_rd.option_label
    target_rd.document_kind = src_rd.document_kind
    target_rd.extraction_key = src_rd.extraction_key
    target_rd.reused_from_run_id = src_rd.run_id
    target_rd.read_at = src_rd.read_at
    target_rd.progress = target_rd.error = target_rd.error_code = None
    session.flush()


_ROW_COPY_SKIP = {"id", "run_id", "document_id", "created_at"}


def _copy_loss_read(session: Session, src_rd: models.RunDocument, target_rd: models.RunDocument, *,
                    same: bool) -> None:
    """Slice 5a (O6/D12): a loss-run read also carries the document's extracted claim rows and printed totals
    (citations stay with the target document: PDF pages get their passages); never broker decisions or added rows.
    Roles and effective values are recomputed by the target run's ledger. A source with no loss rows (every other
    workflow) copies nothing, so its reuse is unchanged."""
    src_run, dst_run = src_rd.run_id, target_rd.run_id
    src_doc, dst_doc = src_rd.document_id, target_rd.document_id
    CR, PT = models.ClaimRow, models.PrintedTotal
    rows = list(session.scalars(select(CR).where(CR.run_id == src_run, CR.document_id == src_doc,
                                                  CR.source == "extracted").order_by(CR.id)))
    totals = list(session.scalars(select(PT).where(PT.run_id == src_run, PT.document_id == src_doc).order_by(PT.id)))
    if not rows and not totals:
        return
    session.execute(delete(CR).where(CR.run_id == dst_run, CR.document_id == dst_doc, CR.source == "extracted"))
    session.execute(delete(PT).where(PT.run_id == dst_run, PT.document_id == dst_doc))
    payload = []
    for r in rows:
        item = {c.key: getattr(r, c.key) for c in CR.__table__.columns if c.key not in _ROW_COPY_SKIP}
        item.update(run_id=dst_run, document_id=dst_doc, created_at=models.utcnow(), role="counted", counted=False,
                    group_key="", decision=None, decision_id=None, decision_stale=False, role_reason=None, flags=[])
        payload.append(item)
    for start in range(0, len(payload), 5000):
        session.execute(insert(CR), payload[start:start + 5000])
    for t in totals:
        session.add(PT(**{c.key: getattr(t, c.key) for c in PT.__table__.columns if c.key not in _ROW_COPY_SKIP},
                       run_id=dst_run, document_id=dst_doc))
    if not same:
        for page_no, verified in {(r.page_no, r.verify_state == "verified") for r in rows if r.page_no}:
            models.upsert_passage(session, dst_doc, page_no, verified=verified, rects=[])
    session.flush()


__all__ = ["ENV_SWITCH", "EXTRACTION_VERSION", "OcrMismatch", "Source", "copy_read", "enabled_from_env",
           "extraction_key", "find_source", "ocr_key"]
