# broker-docs-verified

A document workbench for independent insurance brokers. It reads carrier quotes, policies, certificates and loss
runs, and **every value it shows is checked against the page it came from before it is shown**. Click any value
and the cited page opens with the exact line highlighted. If a value cannot be verified, the app says so instead
of guessing.

> **Unofficial portfolio project.** Built as part of a job application to BrokerDocs and inspired by their product.
> Not affiliated with or endorsed by BrokerDocs. All documents in this repo are **synthetic sample data**.

**Demo video:** _(Loom link)_

## What it does

| Workflow | What the broker gets |
|---|---|
| **Compare Quotes** | Side-by-side coverage table across carriers, every cell cited; long documents up to 300 pages; conflicting values shown, never silently picked |
| **Proposal Builder** | A client proposal from a comparison: outline editing, DOCX export |
| **Policy Check** | Expiring vs renewal vs quote vs the agency system (AMS): discrepancies with severity, each side cited |
| **Certificates (COI)** | Every claim on a certificate checked against the policies before it can be issued |
| **Loss runs** | Claims from spreadsheets and PDFs in one ledger; printed totals reconciled to the cent; anything unverifiable marked Needs review _(backend done, UI in progress)_ |
| **Work queue** | Home screen: each client's work grouped by what needs doing next |

## How it is built

```
React + TypeScript (Vite)  ──>  FastAPI (Python 3.12)  ──>  OpenAI (structured outputs)
                                 │  parse (PyMuPDF, OCR on scans) → extract per window
                                 │  → verify every value against the page text → merge
                                 └  SQLite + files on disk, background runs streamed over SSE
```

- **Verify before cite** (`backend/pipeline/verify.py`): a value is shown as verified only if its quote is found
  on the cited page; otherwise it is "couldn't verify", never presented as fact.
- **Prompt-injection fences**: document text is passed as data inside random per-call boundaries; canary
  documents in the evals check that instructions inside a PDF are never followed.
- **Extraction cache** keyed on file bytes, fields, prompts, models and `EXTRACTION_VERSION`.
- **Specs with decision ledgers** for every slice in `docs/specs/` (design, engineering review, amendments).

## Evals and tests

- Live eval gates per workflow (`tests/eval/`), run against synthetic fixtures with exact truth
  (`tests/fixtures/`). Hard fails (a wrong value shown as verified, an injection followed, a wrong total verdict)
  must be zero. Latest live results: Compare Quotes 268/286 fields, 0 hard fails; Policy Check recall 13/13;
  Certificates 15/15 supported claims; Loss runs 85/85 printed totals, 1,844/1,844 PDF rows.
- Live model replies are recorded and replayed offline (`tests/eval/reply_recorder.py`), so a live failure can be
  debugged without paying for reruns.
- Backend: ~1,700 tests (`backend/tests/`). Frontend: Playwright end-to-end with axe accessibility checks
  (`frontend/e2e/`).

## Run it locally (no API key needed)

The dev server runs the real API with a scripted model and seeds demo runs. From the repo root (PowerShell):

```
py -3.12 -m venv backend/.venv
backend/.venv/Scripts/python -m pip install "fastapi>=0.115,<0.200" "uvicorn>=0.30" "python-multipart>=0.0.9" "sqlalchemy>=2.0.35,<2.1" "pymupdf==1.28.0" "pydantic>=2.7,<3" "openai>=3.42,<4" "openpyxl>=3.1,<4" "reportlab>=4.2,<5" "httpx>=0.27,<1"

$env:BROKERDOCS_DATA_DIR = "$env:TEMP\brokerdocs-dev"
backend/.venv/Scripts/python -m backend.dev_seed seed     # ~20 s, creates the demo runs
backend/.venv/Scripts/python -m backend.dev_seed serve    # http://127.0.0.1:8000
```

Then in `frontend/`: `npm install` and `npm run dev`, and open http://localhost:5173.
To use the real model, run `python -m backend.main` with `OPENAI_API_KEY` set (see `backend/API.md`).

## Where to look

| Path | What |
|---|---|
| `PRODUCT.md`, `DESIGN.md` | Who it is for, design system and voice |
| `docs/specs/` | One spec per slice, with the engineering-review decision ledger |
| `backend/API.md` | API contract |
| `backend/pipeline/` | Parse, windows, extract, verify, merge, cache |
| `backend/loss_runs/` | Loss-run sheets, PDF rows, the ledger that owns every number |
| `tests/eval/` | Eval gates, fixture generators, recorded live runs |
| `HANDOFF.md` | Running build log: what shipped, what failed, what was decided |
