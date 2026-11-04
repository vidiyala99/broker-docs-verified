# Live demo deploy (AWS ECS Express Mode)

Date: 2026-11-04. Status: design approved in chat by the owner; this spec awaits owner review, then `/plan-eng-review`.

## 1. Purpose and decisions

A link the owner sends to the BrokerDocs reviewers, where they can browse the seeded demo and try the product on
their own documents with the real model.

Owner decisions (do not re-ask):
- **D1 Host:** AWS, ECS Express Mode, one service (owner, 2026-11-04; Hetzner/Oracle/Vercel/Cloudflare weighed in chat).
- **D2 Live model:** the real OpenAI key, so reviewers can upload their own documents.
- **D3 Open link:** no passcode; the owner sends the URL only to BrokerDocs reviewers.
- **D4 Billing:** the owner accepts ~$25 to $30/month while the service exists; scale-to-0 and teardown scripts exist
  to cut it.

Defaults taken (owner did not answer; may veto): 5 runs per visitor (client IP) and 30 per day overall; demo run
token budget 500k (loss reviews included); an "Unofficial portfolio demo" label; a privacy line at every upload.

## 2. Architecture

One container image, one ECS Express Mode service, desired count 1 (never more: SQLite, files on disk and the
in-process RunManager are single-instance state).

```
browser ──HTTPS──> ALB (Express Mode) ──HTTP──> Fargate task (1 vCPU / 2 GB)
                                                  uvicorn 0.0.0.0:8080
                                                  FastAPI: /api/* + built frontend (static)
                                                  SQLite + uploads on the task's ephemeral disk
                                                  OPENAI_API_KEY from Secrets Manager
```

- **Image (multi-stage Dockerfile at the repo root):** stage 1 `node` builds `frontend/` with `VITE_API_BASE=""`
  (same origin); stage 2 `python:3.12-slim` installs `backend/` from `backend/pyproject.toml` (`pymupdf<2`), copies
  `frontend/dist`. `.dockerignore` excludes `.env`, `.venv*`, `node_modules`, `tests/`, `data/`, `.claude/`,
  `qa-screenshots/`, recordings. No secret is ever in the image.
- **Start command (`backend/deploy_start.py` or a shell entry):** `dev_seed seed` into the task's data dir (fake
  model, cache off, as today), then `backend.main` with the OpenAI provider. Every task start is a clean demo.
- **Static files:** FastAPI mounts `frontend/dist` at `/` only in demo mode, after the `/api` router, with an SPA
  fallback to `index.html` (the app uses hash routes, so the fallback is only for `/`).

## 3. Demo mode (`BROKERDOCS_DEMO=1`, off by default; local behaviour unchanged)

The backend is local-only on purpose today: `Settings` refuses any host but loopback, `TrustedHostMiddleware`
allows localhost names only, uvicorn runs with `proxy_headers=False`, `LocalGuard` checks Origin on unsafe methods.
Demo mode changes exactly these, and only when the flag is set:

| Today (local) | Demo mode |
|---|---|
| host 127.0.0.1 only | host `0.0.0.0`, port `BROKERDOCS_PORT` (8080) |
| allowed hosts: localhost names | `BROKERDOCS_PUBLIC_HOST` (the Express Mode URL host) + the ALB health-check host |
| UI origins: localhost:5173 | `https://<BROKERDOCS_PUBLIC_HOST>` (same origin) |
| proxy headers ignored | client IP from `X-Forwarded-For`, trusted only from the VPC range (`forwarded_allow_ips`) |

Demo guards (new module `backend/demo.py`, pure functions + one small table or in-memory counter):
- **Run caps:** `POST /api/runs` (every workflow, incl. loss reviews and retries) counts per client IP per UTC day and
  overall per UTC day; over cap → 429 `demo_limit` with a friendly message the frontend shows ("This demo allows 5
  runs per visitor per day. Seeded examples stay open."). Counters reset with the task (ephemeral; acceptable).
  Chat messages count against a separate soft cap (20 per visitor per day) since chat also calls the model.
- **Budget:** run token budget 500k for every workflow in demo mode (overrides 1.5M and the 4M loss-run budget).
- **Labels:** `GET /api/health` exposes `demo: true`; the frontend shows "Unofficial portfolio demo, not an BrokerDocs
  product" in the app shell and "Demo: don't upload confidential documents. Files are read by OpenAI and deleted when
  the demo restarts." at every upload step. Seeded runs keep their "Synthetic sample data" labels.
- Upload limits stay as today (per-run caps, 300 pages, 50 MB).

## 4. AWS resources and scripts (`deploy/`)

- ECR repository `brokerdocs-demo`; image tagged with the git SHA.
- Secrets Manager secret `brokerdocs-demo/openai` (owner pastes a **separate OpenAI project key with a hard monthly
  budget** set in the OpenAI dashboard; this is the real spend cap).
- ECS Express Mode service: 1 vCPU / 2 GB, desired 1, health check `GET /api/health` (200), logs to CloudWatch
  (14-day retention), task role with no AWS permissions beyond reading that secret (execution role).
- Scripts (bash, run from the repo root, region from `AWS_REGION`, default us-east-1):
  `deploy/deploy.sh` (build, push, create or update the service, print the URL), `deploy/scale.sh 0|1`,
  `deploy/teardown.sh` (service, ALB, ECR images; keeps the secret unless `--all`).
- Region: us-east-1 (cheapest; reviewers are US-based). No custom domain in this slice.

## 5. Errors and edge cases

- Task restart mid-run: runs in progress are lost with the disk; on start the DB is new, so nothing half-finished
  shows. Reviewers may see "This demo restarted" only if their tab was open (the existing SSE reconnect fails with a
  not-found run; frontend shows the existing load error, acceptable).
- Missing key in demo mode: start fails loudly (no UnconfiguredProvider fallback), so a misconfigured deploy never
  serves a demo that cannot read.
- OpenAI spend limit reached: provider errors surface as the existing failed-run states.
- Two tasks by mistake: the deploy script pins desired count 1 and max 1.

## 6. Testing

- Backend: Settings in demo mode (host, hosts, origins, forwarded IP trust), non-demo behaviour unchanged (existing
  `test_api_security.py` stays green), caps (per IP, overall, retry counts, 429 body), demo budget, static mount +
  SPA fallback, start fails without a key.
- Frontend: demo banner and upload notice render when `health.demo` is true; `demo_limit` message.
- Container: build locally, run with `BROKERDOCS_DEMO=1` and a fake key against the fake provider switch, smoke e2e
  (load queue, open a seeded run, see the banner).
- Post-deploy (owner approval, one paid run): open the URL, upload one fixture, watch the run finish.

## 7. Not in this slice

Custom domain, CI/CD, persistent database, passcode or accounts, multi-instance, WAF. Revisit if the link is shared
beyond the reviewers (then add a passcode first).

## 8. Tasks

- [ ] T1 demo Settings + middleware changes (Opus: security model)
- [ ] T2 `backend/demo.py` caps + budget + health flag (Opus)
- [ ] T3 static mount + start entry (Sonnet)
- [ ] T4 frontend banner, upload notice, demo_limit message (design stack, small; Sonnet with the design rules)
- [ ] T5 Dockerfile + .dockerignore + local container smoke (Sonnet)
- [ ] T6 `deploy/` scripts + first deploy (owner present: key paste, spend limit, approval of the paid check)
