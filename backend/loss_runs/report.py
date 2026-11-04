r"""The loss review report: the database around the pure ledger (D1, D2, D4, D10).

    claim_row (extracted + added) ─┐
    printed_total ─────────────────┤
    claim_decision (active) ───────┼─► ledger.build_ledger ─► derived claim_row columns (only the rows that changed)
    discrepancy_state (checks) ────┤                        └► ArtifactContent: totals strip + checks + notices
    run documents + lr: facts ─────┘                           (no claims block: the table pages from claim_row)

`content_for_run` is what the runner composes with; `rebuild` stores a new artifact version after a broker action
(edit, add, confirm, leave out, pick, accept a gap, Undo). Both write the derived columns first, so the paged
claims API and the totals strip always come from the same build.

Read notes (lane choice): a spreadsheet read's column mapping and notices (no claims table on a sheet, rows with no
claim number, a second table) are kept as one fact `lr:read_notes` (state absent, value JSON) of that document, so
the extraction cache copies them with the read like any fact.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend import models
from backend.loss_runs import ledger as L
from backend.loss_runs import normalize
from backend.schemas import ArtifactContent, EvidenceState, Notice, RunDocumentStatus, Section

READ_NOTES_FIELD = "lr:read_notes"
PICK_PREFIX = "pick:"
CITED = (str(EvidenceState.VERIFIED), str(EvidenceState.SCAN_READ))

_ROW_COLUMNS = ("id", "document_id", "location", "claim_number", "claim_key", "carrier_key", "source", "status",
                "date_of_loss", "policy_year", "policy_number", "claimant", "verify_state", "paid", "reserve",
                "incurred", "recovery", "deductible", "page_no", "sheet_name", "sheet_row", "line_y", "rects")


# --------------------------------------------------------------------- notes and picks


def pick_note(document_id: int, note: str | None) -> str:
    return f"{PICK_PREFIX}{document_id}" + (f"\n{note}" if note else "")


def split_note(note: str | None) -> tuple[int | None, str | None]:
    """(picked document id, the broker's own note) of a discrepancy_state note."""
    if not note or not note.startswith(PICK_PREFIX):
        return None, note
    head, _, rest = note.partition("\n")
    value = head[len(PICK_PREFIX):]
    return (int(value) if value.isdigit() else None), (rest or None)


def read_notes(session: Session, run_id: int) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for doc_id, value in session.execute(select(models.Fact.document_id, models.Fact.value)
                                         .where(models.Fact.run_id == run_id,
                                                models.Fact.field == READ_NOTES_FIELD)):
        try:
            out[doc_id] = json.loads(value or "{}")
        except ValueError:
            continue
    return out


def document_facts(session: Session, run_id: int) -> dict[int, dict[str, str]]:
    """Cited lr: facts per document (carrier, valuation date, named insured)."""
    out: dict[int, dict[str, str]] = defaultdict(dict)
    for doc_id, field, value in session.execute(
            select(models.Fact.document_id, models.Fact.field, models.Fact.value)
            .where(models.Fact.run_id == run_id, models.Fact.field.like("lr:%"), models.Fact.state.in_(CITED))):
        if value and field not in out[doc_id]:
            out[doc_id][field] = value
    return out


# --------------------------------------------------------------------- windows (O5)


def read_page_set(session: Session, run_id: int, document_id: int) -> set[int]:
    pages: set[int] = set()
    for first, last in session.execute(select(models.LossWindow.first_page, models.LossWindow.last_page)
                                       .where(models.LossWindow.run_id == run_id,
                                              models.LossWindow.document_id == document_id,
                                              models.LossWindow.status == "done")):
        pages.update(range(first, last + 1))
    return pages


def unread_pages(session: Session, run_id: int, document_id: int, page_count: int | None) -> list[int]:
    read = read_page_set(session, run_id, document_id)
    return [p for p in range(1, (page_count or 0) + 1) if p not in read]


def has_rows(session: Session, run_id: int, document_id: int) -> bool:
    return session.scalar(select(models.ClaimRow.id).where(models.ClaimRow.run_id == run_id,
                                                           models.ClaimRow.document_id == document_id,
                                                           models.ClaimRow.source == "extracted").limit(1)) is not None


def usable(session: Session, rd: models.RunDocument) -> bool:
    """A document whose rows belong in the review: read, or stopped part-way with the pages it read (O5)."""
    status = RunDocumentStatus(rd.status)
    if status == RunDocumentStatus.DONE:
        return True
    return status == RunDocumentStatus.FAILED and has_rows(session, rd.run_id, rd.document_id)


# --------------------------------------------------------------------- inputs


def documents_of(session: Session, run: models.Run) -> list[L.LedgerDocument]:
    facts = document_facts(session, run.id)
    notes = read_notes(session, run.id)
    out = []
    for rd in run.documents:
        f = facts.get(rd.document_id, {})
        status = str(rd.status)
        partial = status == "failed" and has_rows(session, run.id, rd.document_id)
        unread = tuple(unread_pages(session, run.id, rd.document_id, rd.document.page_count)) if partial else ()
        out.append(L.LedgerDocument(
            document_id=rd.document_id, position=rd.position, label=rd.document.filename,
            kind=rd.document.kind or "quote", carrier=f.get("lr:carrier"),
            valuation_date=normalize.to_iso_date(f.get("lr:valuation_date")), status=status,
            fully_read=not partial, unread_pages=unread, error=rd.error, letter=rd.letter,
            notices=tuple(notes.get(rd.document_id, {}).get("notices") or ())))
    return out


def _ledger_row(values: dict[str, Any]) -> L.LedgerRow:
    return L.LedgerRow(**{k: values[k] for k in _ROW_COLUMNS if k != "rects"},
                       rects=tuple(tuple(x) for x in values.get("rects") or ()))


def load(session: Session, run: models.Run) -> tuple[list[L.LedgerRow], dict[int, tuple], list[L.LedgerDocument],
                                                     list[L.LedgerTotal], L.Decisions]:
    CR = models.ClaimRow
    cols = [getattr(CR, c) for c in _ROW_COLUMNS] + [getattr(CR, c) for c in L.DERIVED_COLUMNS]
    rows: list[L.LedgerRow] = []
    current: dict[int, tuple] = {}
    n = len(_ROW_COLUMNS)
    for rec in session.execute(select(*cols).where(CR.run_id == run.id).order_by(CR.id)):
        values = dict(zip(_ROW_COLUMNS, rec[:n], strict=True))
        rows.append(_ledger_row(values))
        current[values["id"]] = tuple(rec[n:])
    PT = models.PrintedTotal
    totals = [L.LedgerTotal(id=t.id, document_id=t.document_id, metric=t.metric, scope_kind=t.scope_kind,
                            amount=t.amount, location=t.location, scope_value=t.scope_value,
                            scope_detail=t.scope_detail or {}, label=t.label, verify_state=t.verify_state,
                            page_no=t.page_no, sheet_name=t.sheet_name, sheet_row=t.sheet_row, quote=t.quote,
                            rects=tuple(tuple(x) for x in t.rects or ()))
              for t in session.scalars(select(PT).where(PT.run_id == run.id).order_by(PT.id))]
    decisions = [L.RowDecision(id=d.id, key=d.row_key, kind=d.kind, amounts=d.amounts or {}, basis=d.basis or {},
                               note=d.note)
                 for d in session.scalars(select(models.ClaimDecision).where(
                     models.ClaimDecision.run_id == run.id, models.ClaimDecision.active.is_(True))
                     .order_by(models.ClaimDecision.id))]
    checks = {}
    for s in session.scalars(select(models.DiscrepancyState).where(models.DiscrepancyState.run_id == run.id)):
        pick, note = split_note(s.note)
        checks[s.item_id] = L.CheckState(item_id=s.item_id, status=s.status, fingerprint=s.fingerprint,
                                         resolved_fingerprint=s.resolved_fingerprint,
                                         changed_since_resolved=bool(s.changed_since_resolved), reset_from=s.reset_from,
                                         note=note, pick_document_id=pick)
    return rows, current, documents_of(session, run), totals, L.Decisions(rows=decisions, checks=checks)


# --------------------------------------------------------------------- build and store


def build(session: Session, run: models.Run) -> L.Ledger:
    """Build the ledger and write the derived columns of every row whose derived values changed."""
    rows, current, documents, totals, decisions = load(session, run)
    led = L.build_ledger(rows, documents, totals, decisions)
    changed = []
    for row_id, res in led.rows.items():
        new = res.derived()
        if tuple(new[c] for c in L.DERIVED_COLUMNS) != current.get(row_id):
            changed.append({"id": row_id, **new})
    if changed:
        session.execute(update(models.ClaimRow), changed)
    session.flush()
    return led


def content(led: L.Ledger) -> ArtifactContent:
    notices = []
    for message in led.notices:
        failed = "not read" in message or "not fully read" in message and "Retry" in message
        notices.append(Notice(kind="documents_failed" if failed else "info", message=message, retryable=failed))
    return ArtifactContent(sections=[
        Section(id="totals", number="1", title="Totals", blocks=[led.totals]),
        Section(id="checks", number="2", title="Checks", blocks=list(led.checks)),
    ], notices=notices)


def content_for_run(session: Session, run: models.Run) -> ArtifactContent:
    return content(build(session, run))


def rebuild(session: Session, run: models.Run, *, note: str) -> models.ArtifactVersion:
    """A new version after a broker action. Needs the run's artifact."""
    return models.snapshot_version(session, run.artifact, content_for_run(session, run), note=note[:200])


# --------------------------------------------------------------------- storing a read (the runner)


def replace_extracted(session: Session, run_id: int, document_id: int) -> None:
    """A fresh read replaces the document's extracted rows and printed totals (never added rows or decisions)."""
    session.execute(delete(models.ClaimRow).where(models.ClaimRow.run_id == run_id,
                                                  models.ClaimRow.document_id == document_id,
                                                  models.ClaimRow.source == "extracted"))
    session.execute(delete(models.PrintedTotal).where(models.PrintedTotal.run_id == run_id,
                                                      models.PrintedTotal.document_id == document_id))


_INSERT_ROW_KEYS = ("location", "page_no", "sheet_name", "sheet_row", "line_y", "claim_number", "claim_key",
                    "carrier", "policy_number", "line_of_business", "policy_year", "claimant", "date_of_loss",
                    "status_raw", "status", "paid", "reserve", "incurred", "recovery", "deductible", "quote",
                    "cells", "rects", "verify_state", "verify_reason")
_INSERT_TOTAL_KEYS = ("metric", "scope_kind", "scope_value", "scope_detail", "label", "amount", "location",
                      "page_no", "sheet_name", "sheet_row", "line_y", "quote", "cells", "rects", "verify_state",
                      "verify_reason")


def insert_rows(session: Session, run_id: int, document_id: int, rows: list[dict[str, Any]],
                doc_carrier: str | None, *, skip_locations: set[str] | None = None) -> int:
    """Bulk insert extracted rows (claim_row columns). `skip_locations` are already stored (a resumed read)."""
    from sqlalchemy import insert  # noqa: PLC0415

    payload = []
    seen = set(skip_locations or ())
    for r in rows:
        key = f"{r['location']}|{r['claim_key']}"
        if key in seen:
            continue
        seen.add(key)
        carrier = r.get("carrier") or doc_carrier
        item = {k: r.get(k) for k in _INSERT_ROW_KEYS}
        item.update(run_id=run_id, document_id=document_id, source="extracted", carrier=carrier,
                    carrier_key=normalize.carrier_key(carrier), cells=r.get("cells") or {}, rects=r.get("rects") or [],
                    warnings=[], role="counted", counted=False, group_key="", flags=[], claim_open=False,
                    decision_stale=False, created_at=models.utcnow())
        payload.append(item)
    for start in range(0, len(payload), 5000):
        session.execute(insert(models.ClaimRow), payload[start:start + 5000])
    return len(payload)


def insert_totals(session: Session, run_id: int, document_id: int, totals: list[dict[str, Any]],
                  *, skip_locations: set[str] | None = None) -> None:
    seen = set(skip_locations or ())
    for t in totals:
        if t["location"] in seen:
            continue
        seen.add(t["location"])
        session.add(models.PrintedTotal(run_id=run_id, document_id=document_id,
                                        **{k: t.get(k) for k in _INSERT_TOTAL_KEYS if t.get(k) is not None}))


def stored_locations(session: Session, run_id: int, document_id: int) -> tuple[set[str], set[str]]:
    rows = {f"{loc}|{key}" for loc, key in session.execute(
        select(models.ClaimRow.location, models.ClaimRow.claim_key).where(
            models.ClaimRow.run_id == run_id, models.ClaimRow.document_id == document_id,
            models.ClaimRow.source == "extracted"))}
    totals = set(session.scalars(select(models.PrintedTotal.location).where(
        models.PrintedTotal.run_id == run_id, models.PrintedTotal.document_id == document_id)))
    return rows, totals


__all__ = ["PICK_PREFIX", "READ_NOTES_FIELD", "build", "content", "content_for_run", "document_facts", "documents_of",
           "has_rows", "insert_rows", "insert_totals", "load", "pick_note", "read_notes", "read_page_set", "rebuild",
           "replace_extracted", "split_note", "stored_locations", "unread_pages", "usable"]
