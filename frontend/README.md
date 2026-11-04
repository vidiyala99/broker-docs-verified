# BrokerDocs frontend (slice 1)

React + TypeScript + Vite. Plain CSS on `src/styles/tokens.css` (see `../DESIGN.md`).
Contract: `../backend/API.md`. Design decisions: `DESIGN-BRIEF.md`.

## Run it without spending tokens (dev backend)

The dev backend is the real API with a scripted model (`backend/dev_seed.py`, a FakeProvider).
It never reads `.env` or calls OpenAI, and it refuses to use `broker-docs-verified/data/`.

From `broker-docs-verified/` (PowerShell):

```
# once: Python 3.12 venv for the backend
py -3.12 -m venv backend/.venv
backend/.venv/Scripts/python -m pip install "fastapi>=0.115,<0.200" "uvicorn>=0.30" "python-multipart>=0.0.9" "sqlalchemy>=2.0.35,<2.1" "pymupdf==1.28.0" "pydantic>=2.7,<3" "openai>=3.42,<4" "openpyxl>=3.1,<4" "reportlab>=4.2,<5" "httpx>=0.27,<1"

# every time: a scratch data folder, seed it, serve it
$env:BROKERDOCS_DATA_DIR = "$env:TEMP\brokerdocs-dev"
backend/.venv/Scripts/python -m backend.dev_seed seed    # about 20 s; recreates the dev database
backend/.venv/Scripts/python -m backend.dev_seed serve   # http://127.0.0.1:8000
```

Then, in `frontend/`:

```
npm install
npm run dev        # http://localhost:5173 (the only origins the backend allows)
```

`seed` creates six runs, one per state: failed (corrupt PDFs), cancelled, budget exhausted,
prose fallback ("Retry prose"), partial (failed column, multi-option warning, conflicting values
in a 120-page document) and a main run with scan-read and couldn't-verify cells and one edit.

`serve` slows each model call (`BROKERDOCS_DEV_DELAY`, default 1.2 s) so progress is visible.
Scripted behaviours: a file named `compose_fail...pdf` forces the template fallback; a chat
question containing "fail" gets "Couldn't answer"; one containing "set", "change" or "correct"
gets a proposed edit; `BROKERDOCS_RUN_TOKEN_BUDGET=52000` forces budget exhaustion.

`seed` also adds Policy Check runs, a failed proposal (run 9) and two certificate runs for
Calloway Build Group LLC (run 10 issued, run 11 a draft with open, overridden and sign-off checks).

Extraction cache: a document read once (same file bytes, same fields, prompts, models and
`EXTRACTION_VERSION`) is reused by later runs and shows "Read on <date>". Set
`BROKERDOCS_EXTRACTION_CACHE=off` to always read live (debugging, evals). The seed reads with the
cache off, so seeded documents never serve an e2e upload; reseed before every e2e pass, because a
rerun on a used database reuses its own earlier reads and the reading mark never shows.

To use the real model instead, start `python -m backend.main` with `OPENAI_API_KEY` set in the
environment (see `backend/API.md`).

## Checks

```
npm run typecheck   # tsc strict
npm run build       # typecheck + production build
npx playwright install chromium   # once
npm run e2e         # journey + seeded states, axe; needs the dev backend seeded and serving
```

`npm run e2e` writes screenshots to `qa-screenshots/` (1440 and 1024 widths, plus 900).
Run `seed` again before a fresh e2e pass if you want the seeded runs unedited.

`VITE_API_BASE` overrides the API address (default `http://127.0.0.1:8000`).
