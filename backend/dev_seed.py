"""Dev-only: a scripted model so the frontend can be built and checked without spending tokens.

NOT imported by the app. Nothing here reads .env or OPENAI_API_KEY; the key is removed from
this process's environment before the app starts, and every model call goes to a FakeProvider.

    # from broker-docs-verified/, with a scratch data folder (required, so data/ is never touched)
    set BROKERDOCS_DATA_DIR=C:\\path\\to\\scratch
    backend/.venv/Scripts/python -m backend.dev_seed seed     # builds runs into a fresh dev database
    backend/.venv/Scripts/python -m backend.dev_seed serve    # serves it on 127.0.0.1:8000

seed   uploads fixture PDFs from tests/fixtures/quotes and runs these comparisons:
         1. failed run (every document corrupt; Retry offered)
         2. cancelled run (cancelled after it started)
         3. budget run (tiny token budget: "Budget exhausted" row, columns failed)
         4. fallback run (prose rejected twice: template report and Retry prose)
         5. partial run (one corrupt document, one multi-option document, a conflicting value)
         6. main run (A clean, B scanned, C clean with one unverifiable value, D clean), with one
            cell edit so the edited state and a stale section show
         7. Policy Check, all four roles from tests/fixtures/policy (pc_homeowners: expiring,
            renewal, a scanned quote read from scan, an AMS JSON whose two liability fields
            disagree so its slot needs review); one item resolved with a note, one flagged
            "changed since resolved"
         8. Policy Check with a corrupt quote (its slots read Not read) and no AMS export (N/A)
         9. Proposal Builder built from run 4 (`source_run_id`) whose document is corrupt: it fails,
            so run 4 shows in the work queue's Ready group with "Proposal didn't finish"
        10. Certificate issued for a past holder (Calloway Build Group LLC, GL only), so the holder is
            remembered for the client and the Certificates page has a Done recently row
        11. Certificate draft for the same client from tests/fixtures/certificates (cert_contractor): a mix of
            Supported and Not supported claims, one Overridden with a reason, a description sign-off open
       Runs 7 and 8 (homeowners) are tagged `line: personal`, the others commercial (slice 3b).
       Client names (work queue): runs 2, 3, 4, 6, 7 and 8 have a broker-typed name (CLIENTS), each a
       different client; run 6 is the Cedar Hollow file itself. Run 5 uses the extracted named insured
       (Cedar Hollow, tagged "name from the documents"), so Cedar Hollow shows twice and the queue tells
       the two rows apart by date and carriers. Run 1 has no name at all ("Unnamed client").
       Two tasks: one free-standing, due three days ago (overdue), and one linked to run 7's
       first discrepancy.
serve  runs the real API with the same scripted model, slowed down (BROKERDOCS_DEV_DELAY seconds
       per call, default 1.2) so run progress is visible. Upload a file whose name contains
       "compose_fail" to force the template fallback; ask a chat question containing "fail" to
       see "Couldn't answer"; ask one containing "set", "change" or "correct" to get a proposed
       edit. Set BROKERDOCS_RUN_TOKEN_BUDGET (for example 9000) to force budget exhaustion.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from backend.providers import CallRole, FakeProvider, ProviderRequest
from backend.providers.fake import FakeRefusal
from backend.providers.types import TextPart

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "quotes"
POLICY_FIXTURES = ROOT / "tests" / "fixtures" / "policy"
CERT_FIXTURES = ROOT / "tests" / "fixtures" / "certificates"

# Label in the fixture PDFs -> fact key in the Compare Quotes schema.
ALIASES = {
    "Carrier": "carrier_name", "Named Insured": "named_insured", "Mailing Address": "address",
    "Address": "address", "Admitted Status": "admitted_status", "AM Best Rating": "am_best_rating",
    "Policy Period": "policy_term", "Policy Term": "policy_term", "Coverage Territory": "territory",
    "Territory": "territory", "Producer": "producer", "Policy Form": "policy_form", "Rate": "rate",
    "Total Payable": "total_premium", "Total Premium": "total_premium", "Taxes and Fees": "taxes_and_fees",
    "Minimum Earned Premium": "minimum_earned_premium", "Each Occurrence Limit": "each_occurrence_limit",
    "General Aggregate Limit": "aggregate_limit", "Aggregate Limit": "aggregate_limit",
    "Deductible": "retention", "Amended Deductible": "retention", "Quote Valid Until": "quote_expiration",
}
# Broker-typed client names (work queue, W4). Run 1 (failed) and run 5 have none: run 1 reads "Unnamed
# client" (nothing was extracted), run 5 falls back to the extracted named insured. Queue navigation
# (2026-11-03): run 3 used to fall back too, so Cedar Hollow (every quote fixture's named insured) showed three
# times with nothing to tell the rows apart; it is now a client of its own.
CLIENTS = {
    "cancelled": "Brightwater Logistics Inc.",  # run 2
    "budget": "Halvorsen Dental Partners",  # run 3
    "fallback": "Copperfield Marine Services",  # run 4
    "main": "Cedar Hollow Hospitality LLC",  # run 6
    "policy_check": "Avery and Jordan Pellham",  # run 7
    "policy_check_quote_unread": "Jordan Pellham (lake house)",  # run 8
}

SECTION_OF = {
    "carrier_name": "common_declarations", "named_insured": "common_declarations", "address": "common_declarations",
    "admitted_status": "common_declarations", "am_best_rating": "common_declarations",
    "policy_term": "common_declarations", "territory": "common_declarations", "producer": "common_declarations",
    "policy_form": "common_declarations", "rate": "rate", "total_premium": "premium", "taxes_and_fees": "premium",
    "minimum_earned_premium": "premium", "each_occurrence_limit": "coverage", "aggregate_limit": "coverage",
    "retention": "coverage", "subjectivities": "subjectivities", "quote_expiration": "subjectivities",
}


def _user_text(req: ProviderRequest) -> str:
    return "\n".join(p.text for m in req.messages if m.role == "user" for p in m.parts() if isinstance(p, TextPart))


def _block(text: str, tag: str) -> tuple[str, str]:
    m = re.search(rf"<<<{tag} (\w+)\n(.*)\n{tag} \1>>>", text, re.S)
    if not m:
        raise ValueError(f"no {tag} block")
    return m.group(1), m.group(2)


def extract(text: str) -> dict[str, Any]:
    start, end = text.index("<<<DOCUMENT"), text.rindex(">>>")
    block = text[start:end]
    page, found, conflicts = 0, {}, []
    numbered: tuple[int, str] | None = None
    for line in block.splitlines():
        m = re.match(r"\[page (\d+)\]", line.strip())
        if m:
            page = int(m.group(1))
            continue
        mm = re.match(r"\s*([A-Z][A-Za-z /&]+):\s*(.+?)\s*$", line)
        if mm and mm.group(1) in ALIASES:
            key = ALIASES[mm.group(1)]
            fact = {"field": key, "value": mm.group(2), "page": page, "quote": line.strip()}
            if key in found:
                conflicts.append(fact)
            else:
                found[key] = fact
        n = re.match(r"\s*1\.\s+(.+?)\s*$", line)
        if n and numbered is None:
            numbered = (page, n.group(0).strip())
    if numbered:
        found["subjectivities"] = {"field": "subjectivities", "value": numbered[1][3:].strip(),
                                   "page": numbered[0], "quote": numbered[1]}
    if "Northbeam" in block:
        # A value the model "saw" that is not on the page: verify drops it to Couldn't verify.
        found["minimum_earned_premium"] = {"field": "minimum_earned_premium", "value": "35% of annual premium",
                                           "page": 2, "quote": "Minimum Earned Premium: 35% of annual premium"}
    if "each_occurrence_limit" not in found:
        # Later window of a long document (long_120 page 60): the model misreads the aggregate
        # limit under a "Limits Summary" heading as the each-occurrence limit, so merge reports
        # conflicting values ($1,000,000 on page 3, $2,000,000 on page 60; cell notice). The
        # decoy must be a conflict merge keeps: a one-off line with an amount (the old decoy, a
        # section heading repeated on 3+ pages, is dropped as boilerplate; a wordy mention of
        # the real policy form is folded into it by the containment rule).
        cur, summary = 0, False
        for line in block.splitlines():
            m = re.match(r"\[page (\d+)\]", line.strip())
            if m:
                cur, summary = int(m.group(1)), False
                continue
            if line.strip() == "Limits Summary":
                summary = True
                continue
            amount = re.search(r"\$[\d,]+", line) if summary else None
            if amount:
                found["each_occurrence_limit"] = {"field": "each_occurrence_limit", "value": amount.group(0),
                                                  "page": cur, "quote": line.strip()}
                break
    multi = "Juniper Peak" in block
    return {"facts": list(found.values()) + conflicts, "options_detected": 3 if multi else 1,
            "option_compared": "Option 1" if multi else None}


def compose(text: str) -> dict[str, Any]:
    _, raw = _block(text, "FACTS")
    payload = json.loads(raw)
    labels = {d["document"]: d["label"] for d in payload["documents"]}
    if any("compose_fail" in (d.get("file") or "") for d in payload["documents"]):
        return {"sections": [{"section_id": "executive_summary", "paragraphs": [
            {"segments": [{"text": "A quotes $1.", "passage_ids": [999999]}]}]}]}
    if "facts" not in payload:  # proposal-builder: facts come grouped per outline section
        return {"sections": [_proposal_section(s, labels) for s in payload["sections"]]}
    by_section: dict[str, list[dict]] = {}
    for f in payload["facts"]:
        by_section.setdefault(SECTION_OF.get(f["field"], "executive_summary"), []).append(f)
    sections = []
    docs = ", ".join(f"{k} ({v})" for k, v in labels.items())
    first = [f for f in payload["facts"] if f["field"] == "total_premium"]
    summary = [{"text": f"This report compares {len(labels)} quotes: {docs}.", "passage_ids": []}]
    for f in first:
        scan = " (read from a scan, unverified)" if f["evidence"] != "verified" else ""
        summary.append({"text": f" {f['document']} states a total payable of {f['value']}{scan}.",
                        "passage_ids": [f["passage_id"]]})
    for fd in payload.get("failed_documents", []):
        summary.append({"text": f" Extraction failed for {fd['document']}, so it has no values.", "passage_ids": []})
    sections.append({"section_id": "executive_summary", "paragraphs": [{"segments": summary}]})
    for sid, facts in by_section.items():
        if sid == "executive_summary":
            continue
        segs = []
        for f in facts[:4]:
            if f.get("conflicting_values"):
                vals = " and ".join(c["value"] for c in f["conflicting_values"])
                segs.append({"text": f"{f['document']} states more than one {f['label'].lower()}: {vals}. ",
                             "passage_ids": [f["passage_id"]]})
                continue
            segs.append({"text": f"{f['document']} lists {f['label'].lower()} as {f['value']}. ",
                         "passage_ids": [f["passage_id"]]})
        sections.append({"section_id": sid, "paragraphs": [{"segments": segs}]})
    missing = [m for m in payload.get("fields_without_value", []) if m["status"] == "could not be verified"]
    if missing:
        sections.append({"section_id": "consistency", "paragraphs": [{"segments": [
            {"text": "; ".join(f"{m['document']} {m['label'].lower()} could not be verified" for m in missing) + ".",
             "passage_ids": []}]}]})
    return {"sections": sections}


def _proposal_section(sec: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    """Scripted prose for one proposal section, citing only that section's own facts."""
    facts = [f for f in sec.get("facts", []) if not f.get("conflicting_values")]
    if not facts:
        segs = [{"text": f"This section covers {len(labels)} quotes; no values were found for it.", "passage_ids": []}]
    else:
        segs = [{"text": f"{f['document']} lists {f['label'].lower()} as {f['value']}. ",
                 "passage_ids": [f["passage_id"]]} for f in facts[:3]]
    return {"section_id": sec["section_id"], "paragraphs": [{"segments": segs}]}


def chat(text: str) -> object:
    _, raw = _block(text, "CONTEXT")
    payload = json.loads(raw)
    question = text.rsplit("Question:", 1)[-1].lower()
    if "fail" in question:
        return FakeRefusal("Scripted refusal for the dev backend.")
    cells = payload["report"]["table_cells"]
    rows: dict[str, list[dict]] = {}
    for c in cells:
        rows.setdefault(c["row_key"], []).append(c)
    words = set(re.findall(r"[a-z]+", question))
    best = max(rows, key=lambda k: len(words & set(re.findall(r"[a-z]+", rows[k][0]["field"].lower()))))
    if not words & set(re.findall(r"[a-z]+", rows[best][0]["field"].lower())):
        best = "total_premium" if "total_premium" in rows else best
    segs = []
    for c in rows[best]:
        if c["passage_ids"]:
            note = " (read from a scan, unverified)" if c["evidence"] == "scan_read" else ""
            segs.append({"text": f"{c['column_key']}: {c['field']} is {c['value']}{note}. ",
                         "passage_ids": c["passage_ids"][:1]})
        elif c.get("edited_by_user"):
            segs.append({"text": f"{c['column_key']}: {c['field']} is {c['value']} (edited by you). ", "passage_ids": []})
        else:
            segs.append({"text": f"{c['column_key']}: {c['value']}. ", "passage_ids": []})
    edits = []
    if words & {"set", "change", "correct", "update"}:
        target = next((c for c in rows[best] if c["passage_ids"]), rows[best][0])
        m = re.search(r"\$[\d,]+(?:\.\d\d)?", text.rsplit("Question:", 1)[-1])
        edits.append({"block_id": target["block_id"], "row_key": target["row_key"],
                      "column_key": target["column_key"], "value": m.group(0) if m else "Confirmed with carrier",
                      "reason": f"You asked to change {target['column_key']}'s {target['field'].lower()}."})
    return {"answer": segs, "proposed_edits": edits}


def _policy_truth(filename: str) -> dict[str, Any] | None:
    """Ground truth of a tests/fixtures/policy document, by its upload name (pc_<case>_<role>.pdf)."""
    if not filename.startswith("pc_"):
        return None
    path = POLICY_FIXTURES / (Path(filename).stem + ".truth.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def policy_extract(text: str, truth: dict[str, Any] | None) -> dict[str, Any]:
    """The Policy Check combined pass (D13), scripted from the fixture's ground truth: scalar facts
    plus a forms array, only for the pages this window shows. Unknown documents state nothing."""
    pages = {int(n) for n in re.findall(r"\[page (\d+)\]", text)}
    facts, forms = [], []
    for key, f in ((truth or {}).get("facts") or {}).items():
        if not f or f.get("page") not in pages:
            continue
        if key.startswith("form:"):
            parts = f["value"].split(" ")
            forms.append({"number": " ".join(parts[:-2]), "edition": " ".join(parts[-2:]), "title": None,
                          "page": f["page"], "quote": f["quote"]})
        else:
            value, quote = f["value"], f["quote"]
            if (truth or {}).get("fixture_id") == "pc_homeowners_renewal" and key == "deductible_wind_hail":
                # A value the model "saw" that is not on the page: verify drops it, so the renewal
                # slot needs review (D15) and the dev UI shows that item state.
                value, quote = "3%", "Wind/Hail Deductible 3% of Coverage A"
            facts.append({"field": key, "value": value, "page": f["page"], "quote": quote})
    return {"facts": facts, "forms": forms, "options_detected": 1, "option_compared": None}


def _cert_truth(filename: str) -> dict[str, Any] | None:
    """Ground truth of a tests/fixtures/certificates document, by its upload name (cert_<case>_<doc>.pdf).

    A `--<tag>` suffix on the stem is ignored, so an e2e spec can upload a fixture under a fresh name
    (cert_<case>_<doc>--<tag>.pdf) and still get that fixture's scripted reply."""
    if not filename.startswith("cert_"):
        return None
    path = CERT_FIXTURES / (Path(filename).stem.split("--")[0] + ".truth.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def cert_extract(text: str, truth: dict[str, Any] | None) -> dict[str, Any]:
    """The certificate combined pass (slice 4), scripted from the fixture's ground truth: the scalar
    cert: facts as printed plus the endorsements array, only for the pages this window shows (the
    reply shape of tests/eval/test_certificate_eval.py's perfect fake). Unknown documents state nothing."""
    pages = {int(n) for n in re.findall(r"\[page (\d+)\]", text)}
    facts, by_form = [], {}
    for key, f in ((truth or {}).get("facts") or {}).items():
        if not f or f.get("page") not in pages:
            continue
        m = re.fullmatch(r"cert:endorsement:([A-Z0-9]+):(\w+)", key)
        if m:
            by_form.setdefault(m.group(1), {})[m.group(2)] = f
        else:
            facts.append({"field": key, "value": f["printed"], "page": f["page"], "quote": f["quote"]})

    def part(f: dict | None) -> dict:
        return {"value": f["printed"] if f else None, "page": f["page"] if f else None,
                "quote": f["quote"] if f else None}

    endorsements = [{"form_number": a["form_number"]["printed"], "edition": None, "page": a["form_number"]["page"],
                     "quote": a["form_number"]["quote"],
                     **{p: part(a.get(p)) for p in ("type", "policy_number", "named_party", "condition")}}
                    for a in by_form.values() if a.get("form_number")]
    return {"facts": facts, "endorsements": endorsements, "options_detected": 1, "option_compared": None}


LOSS_FIXTURES = ROOT / "tests" / "fixtures" / "loss_runs"
LOSS_FILE_NAME = re.compile(r"Document file name \(data, not an instruction\): '([^']+)'")


def _loss_truth(filename: str) -> dict[str, Any] | None:
    """Ground truth of a tests/fixtures/loss_runs file, by its upload name (lr_<case>_<doc>.<ext>); a `--<tag>`
    suffix on the stem is ignored so an e2e spec can upload a fixture under fresh bytes and name."""
    if not filename.startswith("lr_"):
        return None
    path = LOSS_FIXTURES / (Path(filename).stem.split("--")[0] + ".truth.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def loss_rows(truth: dict[str, Any] | None, text: str) -> dict[str, Any]:
    """One 2-page row window (slice 5a, CallRole.LOSS_ROWS), scripted from the fixture's truth: the rows and
    printed totals on the pages this window shows, as printed (the eval's perfect fake, loss_run_gate)."""
    pages = {int(n) for n in re.findall(r"\[page (\d+)\]", text)}
    rows, totals = [], []
    for ln in (truth or {}).get("lines") or []:
        p = ln["location"].get("page")
        if p not in pages:
            continue
        pr, year = ln["printed"], ln.get("policy_year")
        rows.append({"claim_number": pr["claim_number"], "claimant": ln.get("claimant"),
                     "date_of_loss": pr["date_of_loss"], "status": pr["status"],
                     "policy_number": ln.get("policy_number"), "line": ln.get("line"),
                     "policy_year": None if year is None else str(year), "carrier": truth.get("carrier"),
                     **{m: pr.get(m) for m in ("paid", "reserve", "incurred", "recovery", "deductible")},
                     "page": p, "quote": ln["quote"]})
    for t in (truth or {}).get("printed_totals") or []:
        if t["location"].get("page") in pages:
            totals.append({"label": t["label"], "metric": t["metric"], "scope_kind": t["scope_kind"],
                           "scope_value": None if t["scope_value"] is None else str(t["scope_value"]),
                           "amount": str(t["printed"]), "page": t["location"]["page"], "quote": t["quote"]})
    return {"rows": rows, "totals": totals}


def loss_facts(truth: dict[str, Any] | None, text: str) -> dict[str, Any]:
    """The lr: document facts (carrier, valuation date) a loss-run PDF's fact pass asks for, as printed."""
    facts = []
    for key in dict.fromkeys(re.findall(r"- (lr:[a-z_]+)", text)):
        f = ((truth or {}).get("facts") or {}).get(key)
        facts.append({"field": key, "value": f["printed"] if f else None, "page": f.get("page") if f else None,
                      "quote": f["quote"] if f else None})
    return {"facts": facts, "options_detected": 1, "option_compared": None}


def loss_headers(text: str) -> dict[str, Any]:
    """Unknown spreadsheet headers: map the ones a fixture names (its `columns`), leave the rest unmapped."""
    m = LOSS_FILE_NAME.search(text)
    truth = _loss_truth(m.group(1)) if m else None
    known = {h: col for s in (truth or {}).get("sheets") or [] for h, col in (s.get("columns") or {}).items()}
    return {"mappings": [{"header": h, "column": col} for h, col in known.items() if h in text]}


class DevProvider(FakeProvider):
    """FakeProvider with a per-call delay and OCR text taken from the clean twin of a scanned fixture."""

    def __init__(self, delay: float = 0.0):
        super().__init__(self.respond)
        self.delay = delay
        self.filename_of = lambda _doc_id: ""

    async def _send(self, request: ProviderRequest):
        if self.delay:
            await asyncio.sleep(self.delay * (0.4 if request.role is CallRole.VISION else 1.0))
        return await super()._send(request)

    def respond(self, req: ProviderRequest):
        text = _user_text(req)
        if req.role is CallRole.LOSS_ROWS:
            m = LOSS_FILE_NAME.search(text)
            return loss_rows(_loss_truth(m.group(1)) if m else None, text)
        if req.role is CallRole.LOSS_HEADERS:
            return loss_headers(text)
        if req.role is CallRole.EXTRACT:
            if "- lr:" in text:  # the loss-run document facts (slice 5a)
                m = re.match(r"extract:(\d+)", req.label or "")
                return loss_facts(_loss_truth(self.filename_of(int(m.group(1)))) if m else None, text)
            if "- cert:" in text:  # the certificate combined schema (slice 4)
                m = re.match(r"extract:(\d+)", req.label or "")
                return cert_extract(text, _cert_truth(self.filename_of(int(m.group(1)))) if m else None)
            if "Also return forms" in text:  # the policy-check combined schema
                m = re.match(r"extract:(\d+)", req.label or "")
                return policy_extract(text, _policy_truth(self.filename_of(int(m.group(1)))) if m else None)
            return extract(text)
        if req.role is CallRole.COMPOSE:
            return compose(text)
        if req.role is CallRole.CHAT:
            return chat(text)
        if req.role is CallRole.SUGGEST:
            return {"suggestions": ["Which quote has the lowest total premium?",
                                    "Compare the deductibles across the quotes.",
                                    "Which values were read from a scan?"]}
        if req.role is CallRole.VISION:
            m = re.match(r"vision:(\d+):p(\d+)", req.label or "")
            return self.ocr(int(m.group(1)), int(m.group(2))) if m else ""
        raise AssertionError(f"unexpected role {req.role}")

    def ocr(self, doc_id: int, page_no: int) -> str:
        import fitz  # PyMuPDF

        name = self.filename_of(doc_id)
        loss = _loss_truth(name)
        if loss and loss.get("scan_transcript"):  # a scanned loss run: its page as the scan reads it
            return loss["scan_transcript"].get(str(page_no), "")
        truth = _policy_truth(name)
        if truth:  # a scanned policy fixture: its printed lines, as a scan would read them
            return "\n".join(f["quote"] for f in (truth.get("facts") or {}).values() if f and f.get("page") == page_no)
        twin = FIXTURES / name.replace("_scanned", "_clean")
        if "_scanned" not in name or not twin.exists():
            return ""
        with fitz.open(twin) as d:
            return d[page_no - 1].get_text() if page_no <= len(d) else ""


# --------------------------------------------------------------------- app plumbing


def _guard_env() -> Path:
    os.environ.pop("OPENAI_API_KEY", None)  # never call OpenAI from the dev backend
    root = os.environ.get("BROKERDOCS_DATA_DIR")
    if not root:
        sys.exit("Set BROKERDOCS_DATA_DIR to a scratch folder first (the dev backend never uses data/).")
    path = Path(root).resolve()
    if path == (ROOT / "data").resolve():
        sys.exit("BROKERDOCS_DATA_DIR must not be broker-docs-verified/data; use a scratch folder.")
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_app(delay: float, budget: int | None = None):
    from backend.context import Settings
    from backend.main import create_app

    settings = Settings.from_env()
    if budget is not None:
        from dataclasses import replace
        settings = replace(settings, budget_limit=budget)
    provider = DevProvider(delay)
    app = create_app(settings, provider=provider)

    def filename_of(doc_id: int) -> str:
        from backend import models
        with app.state.ctx.factory() as s:
            doc = s.get(models.Document, doc_id)
            return doc.filename if doc else ""

    provider.filename_of = filename_of
    return app, settings


class Seeder:
    def __init__(self, delay: float = 0.0, budget: int | None = None):
        from fastapi.testclient import TestClient

        self.app, _ = build_app(delay, budget)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.client.__enter__()

    def close(self) -> None:
        self.client.__exit__(None, None, None)

    def upload(self, *names: str, as_names: dict[str, str] | None = None, folder: Path = FIXTURES) -> list[int]:
        files = [("files", ((as_names or {}).get(n, n), (folder / n).read_bytes(), "application/pdf")) for n in names]
        r = self.client.post("/api/documents", files=files)
        r.raise_for_status()
        body = r.json()
        if body["rejected"]:
            print("  rejected:", [x["filename"] for x in body["rejected"]])
        return [d["id"] for d in body["documents"]]

    def upload_ams(self, name: str) -> int:
        r = self.client.post("/api/documents/ams", files=[
            ("files", (name, (POLICY_FIXTURES / name).read_bytes(), "application/json"))])
        r.raise_for_status()
        return r.json()["documents"][0]["id"]

    def policy_check(self, slots: dict[int, str], line: str = "commercial", client: str | None = None) -> dict:
        r = self.client.post("/api/runs", json={"document_ids": list(slots), "workflow": "policy-check",
                                                "slots": {str(k): v for k, v in slots.items()}, "line": line,
                                                "client_name": client})
        r.raise_for_status()
        run = self.wait(r.json()["run"]["id"])
        print(f"  run {run['id']}: {run['status']}")
        return run

    def start(self, ids: list[int], client: str | None = None) -> int:
        r = self.client.post("/api/runs", json={"document_ids": ids, "client_name": client})
        r.raise_for_status()
        return r.json()["run"]["id"]

    def wait(self, run_id: int, timeout: float = 120) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            run = self.client.get(f"/api/runs/{run_id}").json()
            if run["terminal"]:
                return run
            time.sleep(0.2)
        raise TimeoutError(f"run {run_id} did not finish")

    def run(self, ids: list[int], client: str | None = None) -> dict:
        run = self.wait(self.start(ids, client))
        print(f"  run {run['id']}: {run['status']} tokens={run['tokens_in'] + run['tokens_out']}")
        return run


def scenario_environment() -> None:
    """Slice 4 (D9): the seed's runs read live, so a later run on the same fixture bytes cannot copy an
    earlier read and skip the read that produces its seeded state (budget, fallback, partial, failed)."""
    from backend.pipeline import cache

    os.environ[cache.ENV_SWITCH] = "off"


def seed() -> None:
    scenario_environment()
    data = _guard_env()
    db_file = data / "brokerdocs.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_file) + suffix).unlink(missing_ok=True)
    print(f"Seeding {db_file}")

    s = Seeder()
    try:
        print("1. failed run")
        s.run(s.upload("corrupt_truncated.pdf", "corrupt_garbage.pdf"))
    finally:
        s.close()

    s = Seeder(delay=1.0)
    try:
        print("2. cancelled run")
        rid = s.start(s.upload("carrier_c_clean.pdf", "carrier_d_clean.pdf", "long_120.pdf"),
                      client=CLIENTS["cancelled"])
        time.sleep(1.6)
        s.client.post(f"/api/runs/{rid}/cancel").raise_for_status()
        print(f"  run {rid}: {s.wait(rid)['status']}")
    finally:
        s.close()

    s = Seeder(budget=52000)
    try:
        print("3. budget run")
        s.run(s.upload("carrier_a_clean.pdf", "carrier_b_clean.pdf", "carrier_c_clean.pdf"),
              client=CLIENTS["budget"])
    finally:
        s.close()

    s = Seeder()
    try:
        print("4. compose fallback run")
        fallback = s.run(s.upload("carrier_a_clean.pdf", "carrier_d_clean.pdf",
                                  as_names={"carrier_d_clean.pdf": "compose_fail_copperfield.pdf"}),
                         client=CLIENTS["fallback"])
        print("5. partial run")
        s.run(s.upload("carrier_a_clean.pdf", "multi_option.pdf", "conflicting_endorsement.pdf",
                       "corrupt_truncated.pdf", "long_120.pdf"))
        print("6. main run")
        run = s.run(s.upload("carrier_a_clean.pdf", "carrier_b_scanned.pdf", "carrier_c_clean.pdf",
                             "carrier_d_clean.pdf"), client=CLIENTS["main"])
        art = s.client.get(f"/api/runs/{run['id']}/artifact").json()
        r = s.client.patch(f"/api/artifacts/{art['id']}/cells", json={
            "block_id": "premium-table", "row_key": "taxes_and_fees", "column_key": "D",
            "value": "$1,120.00", "base_version": art["version"]})
        print("  edit:", r.status_code, r.json().get("stale_sections") if r.is_success else r.text)
        print("  counts:", art["counts"])
        first, _ = seed_policy_checks(s)
        seed_tasks(s, first)
        seed_failed_proposal(s, fallback["id"])
        seed_certificates(s)
        seed_loss_review(s)
    finally:
        s.close()
    print("Done. Start the dev server with: python -m backend.dev_seed serve")


def seed_policy_checks(s: Seeder) -> tuple[int, int]:
    """Runs 7 and 8 of a full seed: homeowners renewals, so both are tagged personal (slice 3b)."""
    from sqlalchemy import select

    from backend import db, models

    print("7. policy check, four roles (personal lines)")
    exp, ren, quo = s.upload("pc_homeowners_expiring.pdf", "pc_homeowners_renewal.pdf", "pc_homeowners_quote.pdf",
                             folder=POLICY_FIXTURES)
    ams_id = s.upload_ams("pc_homeowners_ams.json")
    run = s.policy_check({exp: "expiring", quo: "quote", ren: "renewal", ams_id: "ams"}, line="personal",
                         client=CLIENTS["policy_check"])
    items = s.client.get(f"/api/runs/{run['id']}/discrepancies").json()["items"]
    print("  items:", [(i["key"], i["severity"], i["item_state"]) for i in items])
    by_key = {i["key"]: i for i in items}
    resolve = by_key.get("form:HO0481")
    if resolve:
        s.client.patch(f"/api/runs/{run['id']}/discrepancies/{resolve['id']}", json={
            "status": "resolved", "note": "Insured asked for actual cash value on the roof."}).raise_for_status()
    flagged = by_key.get("total_premium")
    if flagged:
        # Dev-only: a decision whose values moved (normally set when a regenerate changes them).
        s.client.patch(f"/api/runs/{run['id']}/discrepancies/{flagged['id']}", json={
            "note": "Carrier confirmed the premium on 10/28."}).raise_for_status()
        with db.session_scope(s.app.state.ctx.factory) as session:
            row = session.scalar(select(models.DiscrepancyState).where(
                models.DiscrepancyState.run_id == run["id"], models.DiscrepancyState.item_id == flagged["id"]))
            row.changed_since_resolved = True

    print("8. policy check, quote not read, no AMS (personal lines)")
    exp2, ren2 = s.upload("pc_homeowners_expiring.pdf", "pc_homeowners_renewal.pdf", folder=POLICY_FIXTURES)
    bad = s.upload("corrupt_truncated.pdf", as_names={"corrupt_truncated.pdf": "carrier_quote_scan.pdf"})[0]
    second = s.policy_check({exp2: "expiring", bad: "quote", ren2: "renewal"}, line="personal",
                            client=CLIENTS["policy_check_quote_unread"])
    return run["id"], second["id"]


CERT_CLIENT = "Calloway Build Group LLC"
CERT_PAST_HOLDER = ("Larkspur Commons Owners Association", "88 Harbor View Road\nTacoma, WA 98403")
CERT_HOLDER = ("Marlowe Property Group LLC", "2200 Larkspur Avenue, Suite 400\nTacoma, WA 98402")
CERT_DESCRIPTION = "Tenant improvements at 2200 Larkspur Avenue, Suite 140."
CERT_OVERRIDE_NOTE = "Lease section 14.3 waives subrogation for GL; carrier confirmed by email on 11/02."


def _certificate(s: Seeder, files: list[str], holder: tuple[str, str], wording: dict[str, bool],
                 description: str = "") -> dict:
    ids = s.upload(*files, folder=CERT_FIXTURES)
    r = s.client.post("/api/runs", json={
        "document_ids": ids, "workflow": "certificate", "client_name": CERT_CLIENT,
        "parameters": {"holder_name": holder[0], "holder_address": holder[1], "wording": wording,
                       "certificate_date": "2026-11-03", "description": description}})
    r.raise_for_status()
    run = s.wait(r.json()["run"]["id"])
    print(f"  run {run['id']}: {run['status']}")
    return s.client.get(f"/api/certificates/{run['id']}").json()


def seed_certificates(s: Seeder) -> tuple[int, int]:
    """Runs 10 and 11 of a full seed (slice 4), both for one client (tests/fixtures/certificates,
    case cert_contractor; certificate date 2026-11-03):
    10. issued: the GL declarations only, no wording, for a past holder, so the holder is remembered for
        the client and the Certificates page shows a Done recently row.
    11. the draft: GL, auto and WC declarations plus three endorsements, all three wordings requested and a
        description. Supported: three in force, insured, AI and P&NC on GL, waiver on WC; Not supported: AI,
        waiver and P&NC on auto, waiver on GL; the description is a sign-off. Waiver on GL is overridden with
        a reason, so the draft shows every claim state and Issue waits on 4 rows."""
    print("10. certificate, issued for a past holder")
    issued = _certificate(s, ["cert_contractor_gl.pdf"], CERT_PAST_HOLDER, {})
    r = s.client.post(f"/api/certificates/{issued['run']['id']}/issue", json={"version": issued["version"]})
    r.raise_for_status()
    print("  issued version", r.json()["issue"]["version"])

    print("11. certificate draft, mixed claims, one overridden")
    draft = _certificate(s, ["cert_contractor_gl.pdf", "cert_contractor_auto.pdf", "cert_contractor_wc.pdf",
                             "cert_contractor_ai_scheduled.pdf", "cert_contractor_waiver_wc.pdf",
                             "cert_contractor_pnc_gl.pdf"], CERT_HOLDER,
                         {"additional_insured": True, "waiver_of_subrogation": True, "primary_noncontributory": True},
                         CERT_DESCRIPTION)
    print("  claims:", {c["key"]: c["verdict"] for c in draft["claims"]})
    target = next((c for c in draft["claims"] if c["key"] == "waiver_of_subrogation:A"), None)
    if target:
        s.client.patch(f"/api/runs/{draft['run']['id']}/discrepancies/{target['id']}", json={
            "status": "resolved", "note": CERT_OVERRIDE_NOTE,
            "expected_fingerprint": target["fingerprint"]}).raise_for_status()
    return issued["run"]["id"], draft["run"]["id"]


LOSS_CLIENT = "Harrow Lane Bakery Group LLC"  # the insured printed on every lr_main file
LOSS_FILES = ("lr_main_ashgrove_clean.xlsx", "lr_main_ashgrove_export.csv", "lr_main_bellwether_messy.xlsx",
              "lr_main_bellwether_extract.csv", "lr_main_driftwood_2024.pdf", "lr_main_driftwood_2025.pdf",
              "lr_main_elmstead_wrapped.pdf", "lr_main_fairhaven_scan.pdf")
LOSS_CONFIRM_NOTE = "Incurred cell is a formula with no stored value; paid + reserve is $8,000.00 and the 2023 subtotal ties."


def seed_loss_review(s: Seeder) -> int:
    """Run 12 of a full seed (slice 5a T9): one loss review over the eight lr_main files (tests/fixtures/loss_runs),
    labelled synthetic sample data, read with the cache off. It shows every review state the fixtures plant: the
    Driftwood 2025 total off by $12,400.00, the AG-2307 conflict, 8 scanned Fairhaven rows not verified, superseded
    valuations, notices. BW-23-111 (formula not calculated) is confirmed with a note, so Handled by you has a row."""
    print("12. loss review, eight carrier files (synthetic)")
    pdfs = [n for n in LOSS_FILES if n.endswith(".pdf")]
    sheets = [n for n in LOSS_FILES if not n.endswith(".pdf")]
    ids = s.upload(*pdfs, folder=LOSS_FIXTURES)
    r = s.client.post("/api/documents/spreadsheets", files=[
        ("files", (n, (LOSS_FIXTURES / n).read_bytes(), "application/octet-stream")) for n in sheets])
    r.raise_for_status()
    by_name = {d["filename"]: d["id"] for d in r.json()["documents"]}
    ids += [by_name[n] for n in sheets if n in by_name]
    r = s.client.post("/api/runs", json={"document_ids": ids, "workflow": "loss-run", "client_name": LOSS_CLIENT,
                                         "parameters": {"synthetic": True}})
    r.raise_for_status()
    run = s.wait(r.json()["run"]["id"])
    print(f"  run {run['id']}: {run['status']}")
    rows = s.client.get(f"/api/loss-runs/{run['id']}/claims", params={"state": "unverified", "limit": 200}).json()
    target = next((x for x in rows["rows"] if x["claim_number"] == "BW-23-111"), None)
    if target:
        s.client.post(f"/api/loss-runs/{run['id']}/rows/{target['id']}/decision",
                      json={"kind": "confirm", "note": LOSS_CONFIRM_NOTE}).raise_for_status()
    review = s.client.get(f"/api/loss-runs/{run['id']}").json()
    print("  checks:", review.get("check_counts"), "totals:", (review.get("totals") or {}).get("claims"))
    return run["id"]


def seed_failed_proposal(s: Seeder, comparison_id: int) -> int:
    """Run 9 of a full seed (work queue, D3): a Proposal Builder run built from comparison
    `comparison_id` (run 4) whose only document cannot be read, so it fails and the comparison sits
    in Ready with "Proposal didn't finish"."""
    print("9. failed proposal built from run", comparison_id)
    bad = s.upload("corrupt_truncated.pdf", as_names={"corrupt_truncated.pdf": "copperfield_quote_scan.pdf"})[0]
    r = s.client.post("/api/runs", json={
        "document_ids": [bad], "workflow": "proposal-builder", "source_run_id": comparison_id,
        "parameters": {"template_id": "standard", "client_name": CLIENTS["fallback"],
                       "broker_name": "Dana Ruiz", "brokerage_name": "Ruiz and Partners"}})
    r.raise_for_status()
    run = s.wait(r.json()["run"]["id"])
    print(f"  run {run['id']}: {run['status']}")
    return run["id"]


def seed_tasks(s: Seeder, policy_run_id: int) -> None:
    """Two tasks (slice 3b spec 1.4): a free-standing one already past its due date (shows overdue)
    and one linked to the first discrepancy of the four-role Policy Check run."""
    import datetime as dt

    print("tasks: one overdue, one linked to a discrepancy")
    overdue = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    s.client.post("/api/tasks", json={"title": "Send the Cedar Hollow renewal summary to the insured",
                                      "due_date": overdue}).raise_for_status()
    items = s.client.get(f"/api/runs/{policy_run_id}/discrepancies").json()["items"]
    if not items:
        print("  no discrepancy to link")
        return
    item = items[0]
    s.client.post("/api/tasks", json={"title": f"Follow up: {item['title']}"[:200], "run_id": policy_run_id,
                                      "discrepancy_item_id": item["id"]}).raise_for_status()


def serve() -> None:
    import uvicorn

    from backend.main import uvicorn_options

    _guard_env()
    delay = float(os.environ.get("BROKERDOCS_DEV_DELAY", "1.2"))
    app, settings = build_app(delay)
    from backend import db
    db.exit_if_outdated(settings.database_url)  # D10: an old dev database stops here with the instruction
    print(f"Dev backend (scripted model, {delay}s per call) on http://127.0.0.1:{settings.port}")
    uvicorn.run(app, **uvicorn_options(settings))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "seed":
        seed()
    elif cmd == "serve":
        serve()
    else:
        sys.exit("usage: python -m backend.dev_seed seed|serve")
