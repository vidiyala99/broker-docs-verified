"""Loss review routes (slice 5a, T5). A loss review is created with POST /api/runs (workflow "loss-run"); its files
are uploaded with POST /api/documents (PDF) and POST /api/documents/spreadsheets (.xlsx, .csv).

    GET    /api/loss-runs                                  Analytics: loss reviews by client
    GET    /api/loss-runs/{run_id}                         the review: totals strip, checks, notices, documents, chips
    GET    /api/loss-runs/{run_id}/claims                  the claims table, paged, sorted and filtered in SQL on the
                                                           ledger's effective values (D2, D10)
    POST   /api/loss-runs/{run_id}/rows/{row_id}/decision  edit amounts | confirm (reason) | leave out (note)
    DELETE /api/loss-runs/{run_id}/decisions/{id}          Undo a row decision (the one it replaced comes back)
    POST   /api/loss-runs/{run_id}/claims                  add a missed claim ("Added by you", no citation)
    DELETE /api/loss-runs/{run_id}/claims/{row_id}         Undo an added claim
    PATCH  /api/loss-runs/{run_id}/checks/{item_id}        accept a gap (note) | pick a conflicting value | reopen

Every action rebuilds the ledger (derived columns + a new report version, D1/D2) and answers with the review.
Extracted rows are never deleted (the evidence stays); "leave out" is the visible alternative.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, func, or_, select

from backend import db, models, uploads, work_queue
from backend.context import AppContext
from backend.loss_runs import headers, ledger, normalize, sheets
from backend.loss_runs import report as loss_report
from backend.routes.common import ApiError, get_ctx, not_found
from backend.routes.runs import _ACTIVE, _NOTE_CONTROL
from backend.schemas import LossCheckBlock, LossTotalsBlock
from backend.views import run_out

router = APIRouter(tags=["loss-runs"])
MONEY = ledger.MONEY
CR = models.ClaimRow


def _ts(value) -> str | None:
    return value.isoformat() if value is not None else None


def _loss_run(s, run_id: int) -> models.Run:
    run = s.get(models.Run, run_id)
    if run is None:
        raise not_found("run", run_id)
    if run.workflow is None or run.workflow.slug != db.LOSS_RUN_SLUG:
        raise ApiError(404, "not_a_loss_review", f"Run {run_id} is not a loss review.")
    return run


def _editable(ctx: AppContext, run: models.Run) -> None:
    if ctx.manager.is_active(run.id) or str(run.status) in _ACTIVE:
        raise ApiError(409, "run_active", "This loss review is still being read. Decide when it finishes.",
                       run_status=str(run.status))
    if run.artifact is None:
        raise ApiError(409, "no_report", "This loss review has no report yet.")


def _clean_note(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(_NOTE_CONTROL.sub("", value).split())[: models.MAX_LOSS_NOTE_CHARS] or None


# --------------------------------------------------------------------- chips (O7/D13)


class _Chips:
    """Chip label and location for loss-run evidence: a PDF page (rects in PDF points) or a sheet page rendered on
    demand with the row's box (R5). Built from claim_row / printed_total evidence, never from passages."""

    def __init__(self, s, run: models.Run):
        self.s = s
        self.docs = {rd.document_id: rd for rd in run.documents}
        self.carriers = {d: f.get("lr:carrier") for d, f in loss_report.document_facts(s, run.id).items()}
        self._meta: dict[int, dict | None] = {}
        self._sizes: dict[tuple[int, int], tuple[float, float]] = {}

    def _sheet_meta(self, doc_id: int) -> dict | None:
        if doc_id not in self._meta:
            rd = self.docs.get(doc_id)
            self._meta[doc_id] = uploads.sheet_meta(rd.document.stored_name) if rd is not None else None
        return self._meta[doc_id]

    def _size(self, doc_id: int, page_no: int) -> tuple[float | None, float | None]:
        key = (doc_id, page_no)
        if key not in self._sizes:
            pg = self.s.scalar(select(models.Page).where(models.Page.document_id == doc_id,
                                                         models.Page.page_no == page_no))
            self._sizes[key] = (pg.width, pg.height) if pg is not None else (None, None)
        return self._sizes[key]

    def chip(self, document_id: int | None, *, page_no: int | None, sheet_name: str | None, sheet_row: int | None,
             location: str | None, rects: list | None) -> dict[str, Any] | None:
        rd = self.docs.get(document_id) if document_id is not None else None
        if rd is None:
            return None
        base = {"document_id": document_id, "letter": rd.letter, "filename": rd.document.filename}
        if sheet_row is not None:
            meta = self._sheet_meta(document_id)
            sheet_index = None
            if location and location.startswith("s") and ":" in location:
                try:
                    sheet_index = int(location[1:location.index(":")])
                except ValueError:
                    sheet_index = None
            found = sheets.locate(meta, sheet_index, sheet_row) if meta and sheet_index is not None else None
            if found is None:
                return {**base, "kind": "sheet", "label": f"Sheet {sheet_name}, row {sheet_row}", "page_no": page_no,
                        "rects": [], "page_width": sheets.PAGE_W, "page_height": sheets.PAGE_H, "url": None}
            n, box = found
            return {**base, "kind": "sheet", "label": f"Sheet {sheet_name}, row {sheet_row}", "page_no": n,
                    "rects": [box], "page_width": sheets.PAGE_W, "page_height": sheets.PAGE_H,
                    "url": f"/api/documents/{document_id}/sheet-pages/{n}"}
        if page_no is None:
            return None
        name = self.carriers.get(document_id) or rd.document.filename
        w, h = self._size(document_id, page_no)
        return {**base, "kind": "page", "label": f"{name} p.{page_no}", "page_no": page_no, "rects": rects or [],
                "page_width": w, "page_height": h, "url": f"/api/documents/{document_id}/file"}


# --------------------------------------------------------------------- the review


def _check_out(block: LossCheckBlock, rows: dict[str, models.DiscrepancyState], chips: _Chips) -> dict[str, Any]:
    out = block.model_dump(mode="json")
    row = rows.get(block.id)
    _, note = loss_report.split_note(row.note if row else None)
    out["note"] = note if block.decided_by == "broker" else None
    out["updated_at"] = _ts(row.updated_at) if row is not None and block.decided_by == "broker" else None
    out["chips"] = [c for c in (chips.chip(e.document_id, page_no=e.page_no, sheet_name=e.sheet_name,
                                           sheet_row=e.sheet_row, location=_evidence_location(chips.s, e),
                                           rects=e.rects) for e in block.evidence) if c]
    return out


def _evidence_location(s, e) -> str | None:
    if e.claim_row_id is not None:
        return s.scalar(select(CR.location).where(CR.id == e.claim_row_id))
    if e.printed_total_id is not None:
        return s.scalar(select(models.PrintedTotal.location).where(models.PrintedTotal.id == e.printed_total_id))
    return None


def review_out(ctx: AppContext, s, run: models.Run) -> dict[str, Any]:
    out: dict[str, Any] = {"run": run_out(run, active=ctx.manager.is_active(run.id))}
    out["client"], out["client_source"] = work_queue.run_client(s, run)
    notes = loss_report.read_notes(s, run.id)
    out["sheets"] = {str(d): n.get("mappings") or [] for d, n in notes.items()}
    out["synthetic"] = bool((run.parameters or {}).get("synthetic"))  # "Synthetic sample data" label (L9)
    art = run.artifact
    if art is None:
        out.update({"artifact_id": None, "version": None, "totals": None, "checks": [], "notices": [],
                    "counts": None})
        return out
    content = art.latest_content()
    totals = next((b for _, b in content.iter_blocks() if isinstance(b, LossTotalsBlock)), None)
    blocks = models.loss_check_blocks(content)
    rows = {r.item_id: r for r in s.scalars(select(models.DiscrepancyState)
                                            .where(models.DiscrepancyState.run_id == run.id))}
    chips = _Chips(s, run)
    checks = [_check_out(b, rows, chips) for b in blocks]
    order = {"open": 0, "resolved": 1, "match": 2}
    out.update({
        "artifact_id": art.id, "version": art.current_version,
        "totals": totals.model_dump(mode="json") if totals else None,
        "checks": sorted(checks, key=lambda c: (order.get(c["status"], 3), 0 if c["kind"] != "total" else 1)),
        "check_counts": {"to_resolve": sum(1 for c in checks if c["needs_action"]),
                         "handled": sum(1 for c in checks if c["status"] == "resolved"),
                         "matches": sum(1 for c in checks if c["status"] == "match")},
        "notices": [n.model_dump(mode="json") for n in content.notices],
        "counts": totals.counts if totals else None,
    })
    return out


@router.get("/loss-runs")
async def list_loss_reviews(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Analytics: every loss review, newest first, with its client, files, latest valuation date and open checks."""
    with ctx.factory() as s:
        runs = list(s.scalars(select(models.Run).join(models.Workflow, models.Workflow.id == models.Run.workflow_id)
                              .where(models.Workflow.slug == db.LOSS_RUN_SLUG)
                              .order_by(models.Run.id.desc()).limit(500)))
        items = []
        for run in runs:
            client, source = work_queue.run_client(s, run)
            totals, open_checks = None, None
            if run.artifact is not None:
                content = run.artifact.latest_content()
                totals = next((b for _, b in content.iter_blocks() if isinstance(b, LossTotalsBlock)), None)
                open_checks = sum(1 for b in models.loss_check_blocks(content) if b.needs_action)
            items.append({"run_id": run.id, "client": client, "client_source": source, "status": str(run.status),
                          "active": ctx.manager.is_active(run.id), "queue_state": run.queue_state,
                          "files": len(run.documents), "created_at": _ts(run.created_at),
                          "finished_at": _ts(run.finished_at),
                          "latest_valuation_date": totals.as_of if totals else None,
                          "claims": totals.claims if totals else None,
                          "incurred": totals.incurred.cents if totals else None, "open_checks": open_checks})
        return {"reviews": items}


@router.get("/loss-runs/{run_id}")
async def get_loss_review(run_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with ctx.factory() as s:
        return review_out(ctx, s, _loss_run(s, run_id))


# --------------------------------------------------------------------- the claims table (D2, D10)

SORTS = {"claim_number": CR.claim_key, "claimant": CR.claimant, "date_of_loss": CR.claim_date, "status": CR.status,
         "carrier": CR.carrier_key, "paid": CR.eff_paid, "reserve": CR.eff_reserve, "incurred": CR.eff_incurred,
         "claim_incurred": CR.claim_incurred, "policy_year": CR.policy_year, "document": CR.document_id,
         "role": CR.role, "row": CR.id}
OVER_100K_CENTS = 100_000_00


def _months_before(iso: str, months: int) -> str:
    d = dt.date.fromisoformat(iso)
    y, m = divmod(d.month - 1 - months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return dt.date(year, month, day).isoformat()
        except ValueError:
            continue
    return d.isoformat()  # pragma: no cover


def _row_out(r: models.ClaimRow, chips: _Chips) -> dict[str, Any]:
    rd = chips.docs.get(r.document_id) if r.document_id is not None else None
    return {
        "id": r.id, "document_id": r.document_id, "letter": rd.letter if rd else None,
        "filename": rd.document.filename if rd else None, "source": r.source, "location": r.location,
        "claim_number": r.claim_number, "claim_key": r.claim_key, "claimant": r.claimant,
        "date_of_loss": r.date_of_loss, "status": r.status, "status_raw": r.status_raw,
        "policy_number": r.policy_number, "policy_year": r.policy_year, "line": r.line_of_business,
        "carrier": r.carrier, "extracted": {m: getattr(r, m) for m in MONEY},
        "effective": {m: getattr(r, f"eff_{m}") for m in MONEY},
        "claim": {"paid": r.claim_paid, "reserve": r.claim_reserve, "incurred": r.claim_incurred,
                  "open": bool(r.claim_open), "date_of_loss": r.claim_date},
        "role": r.role, "role_reason": r.role_reason, "counted": bool(r.counted), "group_key": r.group_key,
        "verify_state": r.verify_state, "verify_reason": r.verify_reason,
        "decision": {"id": r.decision_id, "kind": r.decision} if r.decision else None,
        "changed_since_resolved": bool(r.decision_stale), "flags": list(r.flags or []), "note": r.note,
        "chip": chips.chip(r.document_id, page_no=r.page_no, sheet_name=r.sheet_name, sheet_row=r.sheet_row,
                           location=r.location, rects=r.rects) if r.source == "extracted" else None,
    }


@router.get("/loss-runs/{run_id}/claims")
async def list_claims(
        run_id: int, ctx: AppContext = Depends(get_ctx),
        offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
        sort: Literal[tuple(SORTS)] = Query("claim_incurred"), dir: Literal["asc", "desc"] = Query("desc"),
        over_100k: bool = False, open_only: bool = False, last_3_months: bool = False, open_reserve: bool = False,
        document_id: int | None = None, role: Literal["counted", "superseded", "conflicting", "left_out"] | None = None,
        state: Literal["unverified", "edited", "confirmed", "added", "warning", "changed"] | None = None,
        claim_key: str | None = Query(None, max_length=60), group_key: str | None = Query(None, max_length=300),
        include_superseded: bool = False, scope_kind: str | None = Query(None, max_length=20),
        scope_value: str | None = Query(None, max_length=200), scope_detail: str | None = Query(None, max_length=500),
        q: str | None = Query(None, max_length=100)) -> dict[str, Any]:
    """One page of the claims table. Quick filters are claim level on effective values (O4): Over $100K (the claim's
    incurred, all its lines), Open only, Last 3 months (date of loss within 3 months of the review's latest
    valuation date), Open reserve. Superseded valuations fold under their claim as `history` unless asked for."""
    with ctx.factory() as s:
        run = _loss_run(s, run_id)
        conds = [CR.run_id == run.id]
        if over_100k:
            conds.append(CR.claim_incurred > OVER_100K_CENTS)
        if open_only:
            conds.append(CR.claim_open.is_(True))
        if open_reserve:
            conds.append(CR.claim_reserve > 0)
        as_of = None
        if run.artifact is not None:
            totals = next((b for _, b in run.artifact.latest_content().iter_blocks()
                           if isinstance(b, LossTotalsBlock)), None)
            as_of = totals.as_of if totals else None
        if last_3_months:
            anchor = as_of or dt.date.today().isoformat()
            conds += [CR.claim_date.is_not(None), CR.claim_date >= _months_before(anchor, 3), CR.claim_date <= anchor]
        if document_id is not None:
            conds.append(CR.document_id == document_id)
        if role is not None:
            conds.append(CR.role == role)
        elif not include_superseded and not claim_key and not group_key:
            conds.append(CR.role != "superseded")
        if claim_key:
            conds.append(CR.claim_key == normalize.claim_key(claim_key))
        if group_key:
            conds.append(CR.group_key == group_key)
        if state == "unverified":
            conds += [CR.verify_state == "unverified", CR.source == "extracted", CR.decision.is_(None)]
        elif state in ("edited", "confirmed"):
            conds.append(CR.decision == state)
        elif state == "added":
            conds.append(CR.source == "added")
        elif state == "warning":
            conds.append(or_(CR.flags.like('%incurred_not_paid_plus_reserve%'), CR.flags.like('%repeated_line%')))
        elif state == "changed":
            conds.append(CR.decision_stale.is_(True))
        if q:
            like = f"%{q.strip()}%"
            conds.append(or_(CR.claim_number.like(like), CR.claimant.like(like)))
        conds += _scope_conditions(scope_kind, scope_value, scope_detail)
        column = SORTS[sort]
        ordering = [column.desc().nulls_last() if dir == "desc" else column.asc().nulls_last(), CR.id.asc()]
        where = and_(*conds)
        total = s.scalar(select(func.count()).select_from(CR).where(where))
        page = list(s.scalars(select(CR).where(where).order_by(*ordering).offset(offset).limit(limit)))
        chips = _Chips(s, run)
        out_rows = [_row_out(r, chips) for r in page]
        if not include_superseded and role is None:
            groups = {r.group_key for r in page if r.role != "superseded"}
            history: dict[str, list] = {}
            if groups:
                for h in s.scalars(select(CR).where(CR.run_id == run.id, CR.role == "superseded",
                                                    CR.group_key.in_(groups)).order_by(CR.id)):
                    history.setdefault(h.group_key, []).append(_row_out(h, chips))
            for o in out_rows:
                o["history"] = history.get(o["group_key"], []) if o["role"] != "superseded" else []
        return {"run_id": run.id, "total": total, "offset": offset, "limit": limit, "sort": sort, "dir": dir,
                "as_of": as_of, "rows": out_rows}


def _scope_conditions(kind: str | None, value: str | None, detail: str | None) -> list:
    if not kind or kind == "all":
        return []
    if kind == "policy_year":
        year = normalize.policy_year_of(value)
        return [CR.policy_year == year] if year is not None else [CR.id < 0]
    if kind == "policy_number":
        return [func.upper(func.replace(CR.policy_number, " ", "")) == normalize.claim_key(value)]
    if kind == "carrier":
        return [CR.carrier_key == normalize.carrier_key(value)]
    try:
        d = json.loads(detail) if detail else {}
    except ValueError:
        raise ApiError(422, "invalid_request", "scope_detail must be the check's JSON filter.") from None
    if kind == "sheet":
        if "sheet" in d:
            return [CR.location.like(f"s{int(d['sheet'])}:%")]
        return [CR.sheet_name == value]
    if kind == "page_subtotal":
        if "page" in d:
            return [CR.page_no == int(d["page"]), CR.line_y.is_not(None), CR.line_y < float(d.get("max_y", 1e9))]
        if "sheet" in d:
            return [CR.location.like(f"s{int(d['sheet'])}:%"), CR.sheet_row >= int(d.get("from", 0)),
                    CR.sheet_row <= int(d.get("to", 0))]
    raise ApiError(422, "invalid_request", f"Unknown scope {kind!r}.")


# --------------------------------------------------------------------- row decisions (D4, O2)


Cents = int | str | None


def _cents(value: Cents, field: str) -> int | None:
    if value is None or isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        return normalize.to_cents(value)
    except normalize.MoneyError:
        raise ApiError(422, "invalid_amount", f"{headers.COLUMNS[field]} is not an amount.", field=field) from None


class RowDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["edit", "confirm", "leave_out"]
    amounts: dict[Literal["paid", "reserve", "incurred", "recovery", "deductible"], Cents] | None = None
    note: str | None = Field(None, max_length=models.MAX_LOSS_NOTE_CHARS)


def _ledger_row(r: models.ClaimRow) -> ledger.LedgerRow:
    return ledger.LedgerRow(id=r.id, document_id=r.document_id, location=r.location, claim_number=r.claim_number,
                            claim_key=r.claim_key, source=r.source, status=r.status,
                            **{m: getattr(r, m) for m in MONEY})


def _decision_out(d: models.ClaimDecision) -> dict[str, Any]:
    return {"id": d.id, "kind": d.kind, "amounts": d.amounts or {}, "note": d.note, "active": d.active,
            "replaced_id": d.replaced_id, "created_at": _ts(d.created_at)}


def _active_decision(s, run_id: int, key: str) -> models.ClaimDecision | None:
    return s.scalar(select(models.ClaimDecision).where(models.ClaimDecision.run_id == run_id,
                                                       models.ClaimDecision.row_key == key,
                                                       models.ClaimDecision.active.is_(True)))


@router.post("/loss-runs/{run_id}/rows/{row_id}/decision", status_code=201)
async def decide_row(run_id: int, row_id: int, body: RowDecisionIn, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Edit amounts (the extracted values stay beside the edit), confirm an unverified row (a reason is required),
    or leave a row out of the totals (a note is required). The decision replaces the row's earlier one; Undo brings
    that one back."""
    note = _clean_note(body.note)
    with db.session_scope(ctx.factory) as s:
        run = _loss_run(s, run_id)
        _editable(ctx, run)
        row = s.get(CR, row_id)
        if row is None or row.run_id != run.id:
            raise ApiError(404, "row_not_found", "This loss review has no such claim row.")
        amounts: dict[str, Any] = {}
        if body.kind == "edit":
            if not body.amounts:
                raise ApiError(422, "amounts_required", "Send the amounts to change, for example {\"paid\": 120000}.")
            amounts = {k: _cents(v, k) for k, v in body.amounts.items()}
        elif body.amounts:
            raise ApiError(422, "invalid_request", "Only an edit carries amounts.")
        if body.kind == "confirm" and not note:
            raise ApiError(422, "note_required", "Give the reason you trust this row; it is kept with the decision.")
        if body.kind == "confirm" and row.verify_state == "verified" and row.source == "extracted":
            raise ApiError(422, "already_verified", "This row is verified; there is nothing to confirm.")
        if body.kind == "leave_out" and not note:
            raise ApiError(422, "note_required", "Say why this row is left out of the totals.")
        lr = _ledger_row(row)
        previous = _active_decision(s, run.id, lr.decision_key)
        if previous is not None:
            previous.active = False
        decision = models.ClaimDecision(run_id=run.id, row_key=lr.decision_key, kind=body.kind, amounts=amounts,
                                        basis=lr.basis(), note=note, active=True,
                                        replaced_id=previous.id if previous else None)
        s.add(decision)
        s.flush()
        loss_report.rebuild(s, run, note=f"{body.kind} {row.claim_number}")
        s.flush()
        return {"decision": _decision_out(decision), "review": review_out(ctx, s, run)}


@router.delete("/loss-runs/{run_id}/decisions/{decision_id}")
async def undo_decision(run_id: int, decision_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db.session_scope(ctx.factory) as s:
        run = _loss_run(s, run_id)
        _editable(ctx, run)
        d = s.get(models.ClaimDecision, decision_id)
        if d is None or d.run_id != run.id:
            raise ApiError(404, "decision_not_found", "This loss review has no such decision.")
        if not d.active:
            raise ApiError(409, "not_current", "A later decision replaced this one; undo that one first.")
        restored = s.get(models.ClaimDecision, d.replaced_id) if d.replaced_id else None
        if restored is not None:
            restored.active = True
        s.delete(d)
        s.flush()
        loss_report.rebuild(s, run, note=f"undo {d.kind}")
        s.flush()
        return {"restored": _decision_out(restored) if restored else None, "review": review_out(ctx, s, run)}


# --------------------------------------------------------------------- added claims


class AddClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_number: str = Field(..., min_length=1, max_length=models.MAX_CLAIM_NUMBER_CHARS)
    document_id: int | None = None  # the file the claim is missing from (its reconciliation and valuation date)
    carrier: str | None = Field(None, max_length=200)
    claimant: str | None = Field(None, max_length=models.MAX_CLAIMANT_CHARS)
    date_of_loss: str | None = Field(None, max_length=30)
    status: str | None = Field(None, max_length=50)
    policy_number: str | None = Field(None, max_length=100)
    policy_year: int | None = Field(None, ge=1900, le=2100)
    line: str | None = Field(None, max_length=100)
    paid: Cents = None
    reserve: Cents = None
    incurred: Cents = None
    recovery: Cents = None
    deductible: Cents = None
    note: str | None = Field(None, max_length=models.MAX_LOSS_NOTE_CHARS)

    @field_validator("claim_number", "carrier", "claimant", "status", "policy_number", "line")
    @classmethod
    def _screen(cls, v: str | None) -> str | None:
        return normalize.clean_text(v, 200) if v is not None else None


@router.post("/loss-runs/{run_id}/claims", status_code=201)
async def add_claim(run_id: int, body: AddClaimIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """A missed claim ("Added by you", no citation chip). Added to a file, it counts in that file's reconciliation
    and takes its valuation date; it is never replaced by a re-read."""
    with db.session_scope(ctx.factory) as s:
        run = _loss_run(s, run_id)
        _editable(ctx, run)
        if not body.claim_number:
            raise ApiError(422, "invalid_request", "Give the claim number.")
        carrier = body.carrier
        if body.document_id is not None:
            if body.document_id not in {rd.document_id for rd in run.documents}:
                raise ApiError(422, "invalid_request", "That file is not part of this loss review.")
            carrier = carrier or loss_report.document_facts(s, run.id).get(body.document_id, {}).get("lr:carrier")
        amounts = {m: _cents(getattr(body, m), m) for m in MONEY}
        if all(v is None for v in amounts.values()):
            raise ApiError(422, "amounts_required", "Give at least one amount.")
        row = CR(run_id=run.id, document_id=body.document_id, source="added", location=f"added:{uuid.uuid4().hex[:16]}",
                 claim_number=body.claim_number, claim_key=normalize.claim_key(body.claim_number), carrier=carrier,
                 carrier_key=normalize.carrier_key(carrier), claimant=body.claimant,
                 date_of_loss=normalize.to_iso_date(body.date_of_loss), status_raw=body.status,
                 status=normalize.normalize_status(body.status), policy_number=body.policy_number,
                 policy_year=body.policy_year, line_of_business=body.line, verify_state="verified",
                 verify_reason="added by you", note=_clean_note(body.note), cells={}, rects=[], warnings=[],
                 flags=[], **amounts)
        s.add(row)
        s.flush()
        loss_report.rebuild(s, run, note=f"add {row.claim_number}")
        s.flush()
        chips = _Chips(s, run)
        return {"row": _row_out(s.get(CR, row.id), chips), "review": review_out(ctx, s, run)}


@router.delete("/loss-runs/{run_id}/claims/{row_id}")
async def remove_added_claim(run_id: int, row_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Undo an added claim. Extracted rows are never deleted: leave them out instead."""
    with db.session_scope(ctx.factory) as s:
        run = _loss_run(s, run_id)
        _editable(ctx, run)
        row = s.get(CR, row_id)
        if row is None or row.run_id != run.id:
            raise ApiError(404, "row_not_found", "This loss review has no such claim row.")
        if row.source != "added":
            raise ApiError(409, "extracted_row", "A row read from a file is kept as evidence; leave it out of the "
                                                 "totals instead.")
        key = _ledger_row(row).decision_key
        for d in s.scalars(select(models.ClaimDecision).where(models.ClaimDecision.run_id == run.id,
                                                              models.ClaimDecision.row_key == key)):
            s.delete(d)
        s.delete(row)
        s.flush()
        loss_report.rebuild(s, run, note=f"remove added {row.claim_number}")
        s.flush()
        return {"review": review_out(ctx, s, run)}


# --------------------------------------------------------------------- checks (L6, L5)


class CheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["accept", "pick", "reopen"]
    note: str | None = Field(None, max_length=models.MAX_LOSS_NOTE_CHARS)
    document_id: int | None = None
    expected_fingerprint: str | None = Field(None, max_length=64)


@router.patch("/loss-runs/{run_id}/checks/{item_id}")
async def decide_check(run_id: int, item_id: str, body: CheckIn, ctx: AppContext = Depends(get_ctx)) -> dict:
    """accept: a printed-total gap, with a required note. pick: the conflicting value to count (document_id); the
    other becomes superseded, "Not used (you picked File B)". reopen: Undo of either. An unverified-rows check is
    decided row by row (422). A stale fingerprint is 409 item_changed."""
    note = _clean_note(body.note)
    with db.session_scope(ctx.factory) as s:
        run = _loss_run(s, run_id)
        _editable(ctx, run)
        block = next((b for b in models.loss_check_blocks(run.artifact.latest_content()) if b.id == item_id), None)
        if block is None:
            raise ApiError(404, "check_not_found", "This loss review has no such check.")
        if block.decided_by != "broker":
            raise ApiError(422, "decided_by_rows", "Confirm, fix or leave out each unverified row instead.")
        if body.expected_fingerprint is not None and body.expected_fingerprint != block.fingerprint:
            raise ApiError(409, "item_changed", "This check changed since it was loaded. Review it again.")
        if not s.scalar(select(models.DiscrepancyState.id).where(models.DiscrepancyState.run_id == run.id,
                                                                 models.DiscrepancyState.item_id == item_id)):
            models.reconcile_discrepancy_states(s, run.id, run.artifact.latest_content())
        if body.action == "accept":
            if block.kind != "total" or block.verdict not in ("off", "needs_review"):
                raise ApiError(422, "invalid_action",
                               "Only a printed total that does not match or needs review can be accepted.")
            if not note:
                raise ApiError(422, "note_required", "Say why the gap is accepted; the note is kept with the check.")
            models.update_discrepancy_state(s, run.id, item_id, status="resolved", note=note)
            what = "accept gap"
        elif body.action == "pick":
            if block.kind != "conflict":
                raise ApiError(422, "invalid_action", "Only a conflicting claim takes a pick.")
            if body.document_id not in {o.document_id for o in block.options}:
                raise ApiError(422, "invalid_request", "Pick one of the files that value this claim.")
            models.update_discrepancy_state(s, run.id, item_id, status="resolved",
                                            note=loss_report.pick_note(body.document_id, note))
            what = "pick value"
        else:
            models.update_discrepancy_state(s, run.id, item_id, status="open", note=None)
            what = "reopen check"
        loss_report.rebuild(s, run, note=f"{what} {item_id}"[:200])
        s.flush()
        return review_out(ctx, s, run)


# --------------------------------------------------------------------- run creation guard (L8)


def review_row_count(s, document_ids: list[int]) -> int:
    """Spreadsheet claim rows a new loss review would read (the 200,000 per review cap)."""
    total = 0
    for doc in s.scalars(select(models.Document).where(models.Document.id.in_(document_ids),
                                                       models.Document.kind == "spreadsheet")):
        meta = uploads.sheet_meta(doc.stored_name)
        total += int((meta or {}).get("data_rows") or 0)
    return total


__all__ = ["review_out", "review_row_count", "router"]
