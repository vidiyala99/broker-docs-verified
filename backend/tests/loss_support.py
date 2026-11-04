"""Builders for loss-run tests: small spreadsheets and loss-run PDFs with real word boxes, plus a fake model that
reads claim rows the way a careful model would (no network)."""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable, Sequence
from typing import Any

import fitz
import openpyxl

from backend.providers import CallRole, ProviderRequest
from backend.providers.types import TextPart


def xlsx_bytes(sheets: dict[str, Sequence[Sequence[Any]]], *, merges: dict[str, list[str]] | None = None) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(list(row))
        for rng in (merges or {}).get(name, []):
            ws.merge_cells(rng)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


HEADER = ["Claim #", "Claimant", "Date of Loss", "Status", "Paid", "Reserve", "Total Incurred"]


def claims_sheet(rows: Sequence[Sequence[Any]], *, title: Sequence[Sequence[Any]] = (), header=HEADER):
    return [*title, header, *rows]


# --------------------------------------------------------------------- PDFs with a claims table

COLS = {"claim": 40, "claimant": 120, "dol": 230, "status": 300, "paid": 360, "reserve": 440, "incurred": 520}
HEAD_TEXT = {"claim": "Claim #", "claimant": "Claimant", "dol": "Date of Loss", "status": "Status", "paid": "Paid",
             "reserve": "Reserve", "incurred": "Incurred"}


def loss_pdf(pages: Sequence[Sequence[dict[str, Any]]], *, carrier: str = "Travelers",
             valued: str | None = "06/30/2025", totals: dict[int, list[tuple[str, str]]] | None = None,
             header_cols: dict[str, int] | None = None) -> bytes:
    """pages[i] = rows of page i+1; a row is {"claim", "claimant", "dol", "status", "paid", "reserve", "incurred"}
    with optional "wrap" (a second claimant line). `totals[page] = [(label, incurred text)]` prints total lines."""
    cols = dict(COLS)
    head_cols = header_cols or cols
    doc = fitz.open()
    for pi, rows in enumerate(pages, start=1):
        page = doc.new_page(width=612, height=792)
        page.insert_text((40, 40), f"Carrier: {carrier}", fontsize=9)
        if valued:
            page.insert_text((300, 40), f"Valued as of: {valued}", fontsize=9)
        y = 80
        for key, x in head_cols.items():
            page.insert_text((x, y), HEAD_TEXT[key], fontsize=8)
        y += 18
        for row in rows:
            for key, x in cols.items():
                value = row.get(key)
                if value:
                    page.insert_text((x, y), str(value), fontsize=8)
            y += 12
            if row.get("wrap"):
                page.insert_text((cols["claimant"], y), row["wrap"], fontsize=8)
                y += 12
        for label, amount in (totals or {}).get(pi, []):
            page.insert_text((cols["claim"], y), label, fontsize=8)
            page.insert_text((cols["incurred"], y), amount, fontsize=8)
            y += 12
    data = doc.tobytes()
    doc.close()
    return data


def user_text(req: ProviderRequest) -> str:
    return "\n".join(p.text for m in req.messages if m.role == "user" for p in m.parts() if isinstance(p, TextPart))


_CLAIM_RE = re.compile(r"^[A-Z]{2,3}-\d{2,}")


def rows_reply_from_truth(truth: dict[int, list[dict[str, Any]]], totals: dict[int, list[dict[str, Any]]] | None = None
                          ) -> Callable[[str], dict[str, Any]]:
    """A row-window reply built from known rows per page: returns the rows of the pages the window shows."""

    def reply(text: str) -> dict[str, Any]:
        pages = [int(p) for p in re.findall(r"\[page (\d+)\]", text)]
        rows, tots = [], []
        for p in pages:
            for r in truth.get(p, []):
                rows.append({"claim_number": r["claim"], "claimant": r.get("claimant"), "date_of_loss": r.get("dol"),
                             "status": r.get("status"), "policy_number": r.get("policy_number"), "line": None,
                             "policy_year": r.get("policy_year"), "carrier": r.get("carrier"),
                             "paid": r.get("paid"), "reserve": r.get("reserve"), "incurred": r.get("incurred"),
                             "recovery": None, "deductible": None, "page": p, "quote": r["claim"]})
            for t in (totals or {}).get(p, []):
                tots.append({"label": t["label"], "metric": t.get("metric", "incurred"),
                             "scope_kind": t.get("scope_kind", "all"), "scope_value": t.get("scope_value"),
                             "amount": t["amount"], "page": p, "quote": f"{t['label']} {t['amount']}"})
        return {"rows": rows, "totals": tots}

    return reply


def loss_responder(rows_reply: Callable[[str], dict[str, Any]] | None = None, *,
                   facts: dict[str, str] | None = None, header_map: dict[str, str] | None = None):
    """Fact pass: "Carrier: X" / "Valued as of: Y" lines. Row windows: `rows_reply(text)`. Header map: by name."""
    from pipeline_fakes import any_responder  # noqa: PLC0415

    other = any_responder()

    def respond(req: ProviderRequest):
        text = user_text(req)
        name = req.schema.name if req.schema is not None else ""
        if name == "loss_rows":
            return rows_reply(text) if rows_reply else {"rows": [], "totals": []}
        if name == "loss_header_map":
            payload = json.loads(text[text.index("{"): text.rindex("}") + 1])
            return {"mappings": [{"header": h["header"], "column": (header_map or {}).get(h["header"], "unmapped")}
                                 for h in payload["headers"]]}
        if req.role is CallRole.EXTRACT and "- lr:carrier" in text:
            block = text[text.index("<<<DOCUMENT"): text.rindex(">>>")]
            page, found = 0, {}
            labels = {"lr:carrier": "Carrier", "lr:valuation_date": "Valued as of", "lr:named_insured": "Insured"}
            for line in block.splitlines():
                m = re.match(r"\[page (\d+)\]", line.strip())
                if m:
                    page = int(m.group(1))
                    continue
                for key, label in labels.items():
                    mm = re.search(rf"{re.escape(label)}:\s*(\S[^:]*?)\s*(?=\s{{2,}}|$|Valued)", line)
                    if mm and key not in found:
                        found[key] = {"field": key, "value": mm.group(1).strip(), "page": page,
                                      "quote": f"{label}: {mm.group(1).strip()}"}
            return {"facts": list(found.values()), "options_detected": 1, "option_compared": None}
        return other(req)

    return respond
