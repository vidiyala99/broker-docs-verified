"""Certificate routes (slice 4, C6 to C8). A certificate run is created with POST /api/runs
(workflow "certificate"); claim decisions use PATCH /api/runs/{id}/discrepancies/{item_id}.

    GET  /api/certificates/queue                      the Certificates page queue (C8)
    GET  /api/certificates/holders?client=            the client's past holders, last used first (C2)
    GET  /api/certificates/documents?client=          the client's documents, deduped by sha256, "read on"
    GET  /api/certificates/{run_id}                   draft or issued certificate with its claims
    POST /api/certificates/{run_id}/documents         Add document (D2): reopen the draft, read only these
    POST /api/certificates/{run_id}/issue             Issue the version on screen (D5, D13, D16)
    POST /api/certificates/{run_id}/new-version       an editable copy of an issued version
    GET  /api/certificates/{run_id}/issues/{v}/pdf    the issued PDF (bytes from the issue row)
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend import db, export_certificate, models, work_queue
from backend.context import AppContext
from backend.pipeline import cache, certificate, coi_checks
from backend.routes.common import ApiError, get_ctx, not_found
from backend.routes.runs import _ACTIVE, _item_out, issue_for_current
from backend.schemas import FactField
from backend.views import chips, document_out, run_out

log = logging.getLogger("brokerdocs.api")
router = APIRouter(tags=["certificates"])


def _ts(value) -> str | None:
    return value.isoformat() if value is not None else None


def _certificate_run(s, run_id: int) -> models.Run:
    run = s.get(models.Run, run_id)
    if run is None:
        raise not_found("run", run_id)
    if run.workflow is None or run.workflow.slug != db.CERTIFICATE_SLUG:
        raise ApiError(404, "not_a_certificate", f"Run {run_id} is not a certificate.")
    return run


def _issue_out(issue: models.CertificateIssue) -> dict[str, Any]:
    return {"id": issue.id, "run_id": issue.run_id, "artifact_id": issue.artifact_id, "version": issue.version,
            "issued_at": _ts(issue.issued_at), "holder": issue.holder, "claims": issue.claims,
            "pdf_sha256": issue.pdf_sha256, "pdf_bytes": len(issue.pdf_bytes or b""),
            "pdf_url": f"/api/certificates/{issue.run_id}/issues/{issue.version}/pdf"}


# --------------------------------------------------------------------- queue, holders, documents


@router.get("/certificates/queue")
async def certificate_queue(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        return work_queue.load_certificate_queue(s, is_active=ctx.manager.is_active)


@router.get("/certificates/holders")
async def holders(client: str = Query(..., min_length=1, max_length=models.MAX_CLIENT_NAME_CHARS),
                  ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    key = work_queue.client_key(client)
    with ctx.factory() as s:
        rows = s.scalars(select(models.Holder).where(models.Holder.workspace_id == db.DEFAULT_WORKSPACE_ID,
                                                     models.Holder.client_key == key)
                         .order_by(models.Holder.last_used_at.desc(), models.Holder.id.desc()))
        return {"client_key": key, "holders": [{"id": h.id, "name": h.name, "address": h.address,
                                                "last_used_at": _ts(h.last_used_at)} for h in rows]}


@router.get("/certificates/documents")
async def client_documents(client: str = Query(..., min_length=1, max_length=models.MAX_CLIENT_NAME_CHARS),
                           ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Documents of the client's runs (broker name or extracted named insured), newest upload per sha256.
    `read_at` is when the certificate read of the same bytes happened (it will be reused, C3), else null."""
    key = work_queue.client_key(client)
    with ctx.factory() as s:
        wf = s.scalar(select(models.Workflow).where(models.Workflow.slug == db.CERTIFICATE_SLUG))
        runs = list(s.scalars(select(models.Run).order_by(models.Run.id.desc()).limit(500)))
        picked: dict[str, models.Document] = {}
        for run in runs:
            if work_queue.client_key(work_queue.run_client(s, run)[0]) != key:
                continue
            for rd in run.documents:
                doc = rd.document
                if doc.kind == "ams" or doc.parse_error or doc.source_format in ("docx", "xlsx"):
                    continue  # slice 6 (R5): a certificate reads PDFs only, so its picker hides Word/Excel quotes
                if doc.sha256 not in picked or picked[doc.sha256].id < doc.id:
                    picked[doc.sha256] = doc
        read_at: dict[str, Any] = {}
        if wf is not None and picked:
            ckey = cache.extraction_key([FactField.model_validate(f) for f in wf.fact_schema], wf.instructions or "",
                                        ctx.provider.config)
            for sha, at in s.execute(
                    select(models.Document.sha256, models.RunDocument.read_at)
                    .join(models.RunDocument, models.RunDocument.document_id == models.Document.id)
                    .where(models.Document.sha256.in_(list(picked)), models.RunDocument.extraction_key == ckey,
                           models.RunDocument.status == "done")):
                if at is not None and (sha not in read_at or at < read_at[sha]):
                    read_at[sha] = at
        docs = sorted(picked.values(), key=lambda d: d.id, reverse=True)
        return {"client_key": key, "documents": [{**document_out(d), "read_at": _ts(read_at.get(d.sha256)),
                                                  "already_read": d.sha256 in read_at} for d in docs]}


# --------------------------------------------------------------------- one certificate


def certificate_out(ctx: AppContext, s, run: models.Run) -> dict[str, Any]:
    art = run.artifact
    out: dict[str, Any] = {"run": run_out(run, active=ctx.manager.is_active(run.id))}
    out["client"], out["client_source"] = work_queue.run_client(s, run)
    issues = list(s.scalars(select(models.CertificateIssue).where(models.CertificateIssue.run_id == run.id)
                            .order_by(models.CertificateIssue.version)))
    out["issues"] = [_issue_out(i) for i in issues]
    if art is None:
        out.update({"artifact_id": None, "version": None, "certificate": None, "claims": [], "counts": None,
                    "notices": [], "chips": {}, "issue": None, "can_issue": False, "blocked": ["no_draft"]})
        return out
    content = art.latest_content()
    cert = content.find_certificate()
    claims = models.claim_blocks(content)
    rows = {r.item_id: r for r in s.scalars(select(models.DiscrepancyState)
                                            .where(models.DiscrepancyState.run_id == run.id))}
    tally = certificate.claim_counts(claims, rows)
    current = next((i for i in issues if i.artifact_id == art.id and i.version == art.current_version), None)
    blocked = []
    if ctx.manager.is_active(run.id) or str(run.status) in _ACTIVE:
        blocked.append("run_active")
    if tally.open:
        blocked.append("claims_open")
    if not certificate.in_force_coverage(content):
        blocked.append("no_coverage")
    if current is not None:
        blocked.append("issued")
    tasks = models.item_tasks(s, run.id)
    out.update({
        "artifact_id": art.id, "version": art.current_version,
        "certificate": cert.model_dump(mode="json") if cert else None,
        "claims": [_item_out(b, rows.get(b.id), None, tasks.get(b.id)) for b in claims],
        "counts": {"total": tally.total, "open": tally.open, "supported": tally.supported,
                   "overridden": tally.overridden, "removed": tally.removed},
        "notices": [n.model_dump(mode="json") for n in content.notices],
        "chips": chips(s, run, content),
        "issue": _issue_out(current) if current else None,
        "can_issue": not blocked, "blocked": blocked,
    })
    return out


@router.get("/certificates/{run_id}")
async def get_certificate(run_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        return certificate_out(ctx, s, _certificate_run(s, run_id))


class AddDocuments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[int] = Field(..., min_length=1, max_length=8)


@router.post("/certificates/{run_id}/documents", status_code=202)
async def add_documents(run_id: int, body: AddDocuments, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Add document (D2): certificate drafts only; DONE or PARTIAL; the current version not issued."""
    with ctx.factory() as s:
        run = _certificate_run(s, run_id)
        if ctx.manager.is_active(run_id) or str(run.status) in _ACTIVE:
            raise ApiError(409, "run_active", "This certificate is still being read.", run_status=str(run.status))
        if issue_for_current(s, run) is not None:
            raise ApiError(409, "issued", "This version is issued and read-only. Start a new version to add a "
                                          "document.")
        if str(run.status) not in ("done", "partial"):
            raise ApiError(409, "not_reopenable", f"This certificate is {run.status}; retry it instead.",
                           run_status=str(run.status))
    try:
        ctx.manager.add_documents(run_id, body.document_ids)
    except models.InvalidTransition as exc:
        raise ApiError(409, "not_reopenable", str(exc)) from None
    except LookupError as exc:
        raise ApiError(404, "not_found", str(exc).capitalize() + ".") from None
    except ValueError as exc:
        raise ApiError(422, "invalid_run", str(exc)) from None
    with ctx.factory() as s:
        return certificate_out(ctx, s, s.get(models.Run, run_id))


class IssueIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(..., ge=1)  # the version on the broker's screen (D5)


def _holder_upsert(s, run: models.Run, params: certificate.CertificateParameters, now) -> None:
    client = work_queue.run_client(s, run)[0]
    if not client:
        return  # no client name: nothing to remember the holder by
    ckey, nkey = work_queue.client_key(client), coi_checks.normalize_name(params.holder_name)
    row = s.scalar(select(models.Holder).where(models.Holder.workspace_id == run.workspace_id,
                                               models.Holder.client_key == ckey, models.Holder.name_key == nkey))
    if row is None:
        s.add(models.Holder(workspace_id=run.workspace_id, client_key=ckey, name=params.holder_name, name_key=nkey,
                            address=params.holder_address, created_at=now, last_used_at=now))
    else:
        row.name, row.address, row.last_used_at = params.holder_name, params.holder_address, now


@router.post("/certificates/{run_id}/issue")
async def issue(run_id: int, body: IssueIn, ctx: AppContext = Depends(get_ctx)) -> Any:
    """Issue exactly the version on screen (D5), in one transaction with its PDF bytes (D16):

        same version already issued ───────► 200, the existing issue (idempotent, a double click)
        run reading ───────────────────────► 409 run_active
        not the current version ───────────► 409 version_changed
        a claim needing action ────────────► 409 claims_open
        no printed policy in force ────────► 409 no_coverage (D13)
        render fails ──────────────────────► 500 issue_failed, nothing stored
    """
    try:
        with db.session_scope(ctx.factory) as s:
            run = _certificate_run(s, run_id)
            art = run.artifact
            if art is None:
                raise ApiError(409, "no_draft", "This certificate has no draft yet.")
            existing = s.scalar(select(models.CertificateIssue).where(
                models.CertificateIssue.artifact_id == art.id, models.CertificateIssue.version == body.version))
            if existing is not None:
                return {"issue": _issue_out(existing), "created": False, "certificate": certificate_out(ctx, s, run)}
            if ctx.manager.is_active(run_id) or str(run.status) in _ACTIVE:
                raise ApiError(409, "run_active", "This certificate is still being read.", run_status=str(run.status))
            if body.version != art.current_version:
                raise ApiError(409, "version_changed", f"The certificate changed (now version {art.current_version}). "
                               "Review it again before issuing.", current_version=art.current_version)
            content = art.latest_content()
            claims = models.claim_blocks(content)
            rows = certificate.state_rows(s, run.id)
            tally = certificate.claim_counts(claims, rows)
            if tally.open:
                raise ApiError(409, "claims_open", f"{tally.open} check{'s' if tally.open != 1 else ''} to resolve.",
                               open=tally.open)
            if not certificate.in_force_coverage(content):
                raise ApiError(409, "no_coverage", "No printed policy is shown in force on the certificate date.")
            cert = content.find_certificate()
            now = models.utcnow()
            try:
                pdf = export_certificate.render(export_certificate.CertificatePdf(
                    certificate=cert, issued_on=now.date().isoformat(),
                    reference=f"Run {run.id}, version {art.current_version}"))
            except Exception as exc:
                log.error("certificate render failed run=%s error=%s", run_id, type(exc).__name__)
                raise ApiError(500, "issue_failed", "The certificate PDF could not be made. Nothing was issued; "
                                                    "retry.") from None
            params = certificate.stored_parameters(run.parameters)
            snapshot = [{"key": b.key, "title": b.title, "kind": str(b.kind), "verdict": str(b.verdict),
                         "reason": b.reason, "condition": b.condition,
                         "status": rows[b.id].status if b.id in rows else "open",
                         "note": rows[b.id].note if b.id in rows else None} for b in claims]
            row = models.CertificateIssue(
                run_id=run.id, artifact_id=art.id, version=art.current_version, issued_at=now,
                holder={"name": params.holder_name, "address": params.holder_address}, claims=snapshot,
                certificate=cert.model_dump(mode="json"), pdf_bytes=pdf, pdf_sha256=hashlib.sha256(pdf).hexdigest())
            s.add(row)
            _holder_upsert(s, run, params, now)
            run.queue_changed_at = now
            s.flush()
            result = {"issue": _issue_out(row), "created": True, "certificate": certificate_out(ctx, s, run)}
    except IntegrityError:  # a concurrent Issue of the same version won: return it (idempotent)
        with ctx.factory() as s:
            run = _certificate_run(s, run_id)
            existing = s.scalar(select(models.CertificateIssue).where(
                models.CertificateIssue.artifact_id == run.artifact.id, models.CertificateIssue.version == body.version))
            if existing is None:
                raise
            return {"issue": _issue_out(existing), "created": False, "certificate": certificate_out(ctx, s, run)}
    return result


@router.post("/certificates/{run_id}/new-version")
async def new_version(run_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """An editable copy of the issued current version (the issued one stays read-only on its record)."""
    with db.session_scope(ctx.factory) as s:
        run = _certificate_run(s, run_id)
        current = issue_for_current(s, run)
        if current is None:
            raise ApiError(409, "not_issued", "This version is not issued; it can be changed as it is.")
        certificate.rebuild(s, run, note=f"new version after issue v{current.version}")
        run.queue_state, run.queue_changed_at = "open", models.utcnow()
        s.flush()
        return certificate_out(ctx, s, run)


@router.get("/certificates/{run_id}/issues/{version}/pdf")
async def issue_pdf(run_id: int, version: int, ctx: AppContext = Depends(get_ctx)) -> Response:
    with ctx.factory() as s:
        run = _certificate_run(s, run_id)
        row = s.scalar(select(models.CertificateIssue).where(models.CertificateIssue.run_id == run.id,
                                                            models.CertificateIssue.version == version))
        if row is None:
            raise ApiError(404, "issue_not_found", f"Version {version} of this certificate was not issued.")
        data = bytes(row.pdf_bytes)
    name = f"certificate-run{run_id}-v{version}.pdf"
    return Response(data, media_type=export_certificate.PDF_MIME,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


__all__ = ["certificate_out", "router"]
