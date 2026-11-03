"""JSON views of ORM rows for the REST API (shapes documented in backend/API.md).

A run's documents use the same keys as the SSE `document.status` event, so the
frontend renders both with one component.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import models
from backend.edits import all_passage_ids, iter_cells
from backend.pipeline.runner import is_retryable
from backend.schemas import ArtifactContent, RunStatus

TERMINAL_RUN_STATUSES = {str(RunStatus.DONE), str(RunStatus.PARTIAL), str(RunStatus.FAILED),
                         str(RunStatus.CANCELLED)}


def _ts(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def document_out(doc: models.Document) -> dict[str, Any]:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "size_bytes": doc.size_bytes,
        "page_count": doc.page_count,
        "sha256": doc.sha256,
        "kind": doc.kind,
        "parse_error": doc.parse_error,
        "warning": doc.parse_message if doc.parse_error else None,
        "created_at": _ts(doc.created_at),
    }


def run_document_out(rd: models.RunDocument) -> dict[str, Any]:
    # Same keys as pipeline.runner's document.status event payload.
    return {
        "run_document_id": rd.id, "document_id": rd.document_id, "position": rd.position,
        "letter": rd.letter, "slot": rd.slot, "filename": rd.document.filename, "status": str(rd.status),
        "progress": rd.progress, "error": rd.error, "error_code": rd.error_code,
        "option_count": rd.option_count, "option_label": rd.option_label,
    }


def run_out(run: models.Run, *, active: bool) -> dict[str, Any]:
    art = run.artifact
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "workflow_name": run.workflow.name if run.workflow else None,
        "line": run.line or "commercial",  # slice 3b; REST only, the SSE snapshot is unchanged (O9)
        "status": str(run.status),
        "active": active,
        "terminal": str(run.status) in TERMINAL_RUN_STATUSES and not active,
        "error": run.error,
        "retryable": is_retryable(run.status, active=active),  # POST /retry would be accepted
        "parameters": run.parameters or {},
        "tokens_in": run.tokens_in or 0,
        "tokens_out": run.tokens_out or 0,
        "cost_usd": run.cost_usd or 0.0,
        "created_at": _ts(run.created_at),
        "started_at": _ts(run.started_at),
        "finished_at": _ts(run.finished_at),
        "documents": [run_document_out(rd) for rd in run.documents],
        "artifact": {"id": art.id, "version": art.current_version} if art else None,
    }


def chips(session: Session, run: models.Run, content: ArtifactContent) -> dict[str, dict[str, Any]]:
    """Chip label and PDF location for every passage the content cites (DG11 labels like "B2").

    Labels are derived over current chips plus original-source chips, so an
    edit never renumbers the other chips of a column.
    """
    ids = all_passage_ids(content)
    if not ids:
        return {}
    rds = {rd.document_id: rd for rd in run.documents}
    passages = [p for p in session.scalars(select(models.Passage).where(models.Passage.id.in_(ids)))
                if p.document_id in rds]
    labels = models.assign_chip_labels((p.id, rds[p.document_id].letter, p.page_no) for p in passages)
    pages = {(pg.document_id, pg.page_no): pg for pg in session.scalars(
        select(models.Page).where(models.Page.document_id.in_({p.document_id for p in passages})))}
    out: dict[str, dict[str, Any]] = {}
    for p in sorted(passages, key=lambda x: (labels[x.id][0], x.page_no)):
        pg = pages.get((p.document_id, p.page_no))
        out[str(p.id)] = {
            "passage_id": p.id, "label": labels[p.id], "letter": rds[p.document_id].letter,
            "document_id": p.document_id, "filename": rds[p.document_id].document.filename,
            "page_no": p.page_no, "verified": bool(p.verified), "rects": p.rects or [],
            "page_width": pg.width if pg else None, "page_height": pg.height if pg else None,
        }
    return out


AMS_FIELD_MAX = 100  # ams.AmsLimits.max_name_chars


def ams_field_name(quote: str | None, value: str | None) -> str | None:
    """The AMS field a verified AMS fact cites. The rendered AMS PDF prints one line per field,
    "name: value" (ams.render_pdf), and that line is the fact's quote, so the name is the part
    before ": " (the value is matched from the end first, so a name holding ": " survives)."""
    if not quote:
        return None
    if value and quote.endswith(f": {value}"):
        name = quote[: -(len(value) + 2)]
    elif ": " in quote:
        name = quote.split(": ", 1)[0]
    elif quote.endswith(":"):
        name = quote[:-1]
    else:
        return None
    name = name.strip()
    return name[:AMS_FIELD_MAX] or None


def discrepancy_sources(session: Session, run: models.Run, blocks: Iterable[Any]) -> dict[str, dict[str, list]]:
    """Per item and role, the cited facts behind a slot's chips: passage, page, the fact's own
    highlight rects (a passage's rects are every fact on that page) and, for an AMS document,
    the field name. Read-only; the artifact is unchanged."""
    blocks = list(blocks)
    ids = {fid for b in blocks for cell in b.slots.values() for fid in cell.fact_ids}
    if not ids:
        return {}
    facts = {f.id: f for f in session.scalars(select(models.Fact).where(models.Fact.id.in_(ids),
                                                                         models.Fact.run_id == run.id))}
    kinds = {rd.document_id: rd.document.kind for rd in run.documents}
    out: dict[str, dict[str, list]] = {}
    for b in blocks:
        per_role: dict[str, list] = {}
        for role, cell in b.slots.items():
            entries = []
            for fid in cell.fact_ids:
                f = facts.get(fid)
                if f is None or f.passage_id is None or f.passage_id not in cell.passage_ids:
                    continue
                field = ams_field_name(f.quote, f.value) if kinds.get(f.document_id) == "ams" else None
                entries.append({"passage_id": f.passage_id, "page_no": f.page_no, "rects": f.rects or [],
                                "field": field})
            if entries:
                per_role[str(role)] = entries
        if per_role:
            out[b.id] = per_role
    return out


def cell_counts(content: ArtifactContent) -> dict[str, int]:
    """Trust summary for the outline header (DG2), counted over table cells."""
    counts: Counter[str] = Counter()
    for *_, cell in iter_cells(content):
        counts[str(cell.state) if cell.state else "absent"] += 1
        if cell.edited:
            counts["edited"] += 1
    return {k: counts.get(k, 0)
            for k in ("verified", "scan_read", "not_found", "absent", "failed", "conflicting", "edited")}


def artifact_out(session: Session, art: models.Artifact, version: int | None = None) -> dict[str, Any]:
    number = art.current_version if version is None else version
    row = art.version_row(number)  # LookupError for an unknown version
    content = ArtifactContent.model_validate(row.content)
    fb = next((f for f in art.feedback if f.version == number), None)
    return {
        "id": art.id,
        "run_id": art.run_id,
        "title": art.title,
        "kind": art.kind,
        "version": number,
        "current_version": art.current_version,
        "is_current": number == art.current_version,
        "note": row.note,
        "created_at": _ts(row.created_at),
        "content": content.model_dump(mode="json"),
        "chips": chips(session, art.run, content),
        "counts": cell_counts(content),
        "stale_sections": [s.id for s in content.sections if s.stale],
        "feedback": {"rating": fb.rating, "comment": fb.comment} if fb else None,
    }


def version_out(v: models.ArtifactVersion, current: int) -> dict[str, Any]:
    return {"version": v.version, "note": v.note, "created_at": _ts(v.created_at), "current": v.version == current}


def message_out(m: models.Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "run_id": m.run_id,
        "role": m.role,
        "content": m.content,
        "segments": m.segments or ([{"text": m.content, "passage_ids": []}] if m.role == "user" else []),
        "passage_ids": m.passage_ids or [],
        "proposed_edits": m.proposed_edits or [],
        "created_at": _ts(m.created_at),
    }
