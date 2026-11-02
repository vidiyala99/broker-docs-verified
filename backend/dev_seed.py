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
        if req.role is CallRole.EXTRACT:
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

    def upload(self, *names: str, as_names: dict[str, str] | None = None) -> list[int]:
        files = [("files", ((as_names or {}).get(n, n), (FIXTURES / n).read_bytes(), "application/pdf")) for n in names]
        r = self.client.post("/api/documents", files=files)
        r.raise_for_status()
        body = r.json()
        if body["rejected"]:
            print("  rejected:", [x["filename"] for x in body["rejected"]])
        return [d["id"] for d in body["documents"]]

    def start(self, ids: list[int]) -> int:
        r = self.client.post("/api/runs", json={"document_ids": ids})
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

    def run(self, ids: list[int]) -> dict:
        run = self.wait(self.start(ids))
        print(f"  run {run['id']}: {run['status']} tokens={run['tokens_in'] + run['tokens_out']}")
        return run


def seed() -> None:
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
        rid = s.start(s.upload("carrier_c_clean.pdf", "carrier_d_clean.pdf", "long_120.pdf"))
        time.sleep(1.6)
        s.client.post(f"/api/runs/{rid}/cancel").raise_for_status()
        print(f"  run {rid}: {s.wait(rid)['status']}")
    finally:
        s.close()

    s = Seeder(budget=52000)
    try:
        print("3. budget run")
        s.run(s.upload("carrier_a_clean.pdf", "carrier_b_clean.pdf", "carrier_c_clean.pdf"))
    finally:
        s.close()

    s = Seeder()
    try:
        print("4. compose fallback run")
        s.run(s.upload("carrier_a_clean.pdf", "carrier_d_clean.pdf",
                       as_names={"carrier_d_clean.pdf": "compose_fail_copperfield.pdf"}))
        print("5. partial run")
        s.run(s.upload("carrier_a_clean.pdf", "multi_option.pdf", "conflicting_endorsement.pdf",
                       "corrupt_truncated.pdf", "long_120.pdf"))
        print("6. main run")
        run = s.run(s.upload("carrier_a_clean.pdf", "carrier_b_scanned.pdf", "carrier_c_clean.pdf",
                             "carrier_d_clean.pdf"))
        art = s.client.get(f"/api/runs/{run['id']}/artifact").json()
        r = s.client.patch(f"/api/artifacts/{art['id']}/cells", json={
            "block_id": "premium-table", "row_key": "taxes_and_fees", "column_key": "D",
            "value": "$1,120.00", "base_version": art["version"]})
        print("  edit:", r.status_code, r.json().get("stale_sections") if r.is_success else r.text)
        print("  counts:", art["counts"])
    finally:
        s.close()
    print("Done. Start the dev server with: python -m backend.dev_seed serve")


def serve() -> None:
    import uvicorn

    from backend.main import uvicorn_options

    _guard_env()
    delay = float(os.environ.get("BROKERDOCS_DEV_DELAY", "1.2"))
    app, settings = build_app(delay)
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
