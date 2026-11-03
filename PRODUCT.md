# PRODUCT.md: BrokerDocs

Status: owner-confirmed 2026-11-03. Design lanes read this before DESIGN.md.

## What it is
A broker workspace: it reads insurance documents (carrier quotes, expiring and
renewal policies, AMS exports), extracts the values a broker needs, and shows every value next to the page it
came from. Built slice by slice from evidence of the real product (`docs/reference/`, `docs/slice-map.md`).

## Why it exists
A portfolio demonstration for the owner's job applications: it should show product judgement and
engineering depth (verified extraction, long documents, evals), not just a visual copy.

## Who uses it
- Primary: an insurance broker or account manager at an independent agency, working commercial and personal
  lines renewals and new business, at a small to mid-size agency with one person per client file.
- Their day: a queue of clients with quotes to compare, renewals to check, proposals to send. They think in
  clients and next actions, not in runs, files or model output (owner, 2026-11-02).
- Context: desktop first, long sessions, many PDFs open; phone is for checking status, not for review work.

## The promise
Every number is traceable. A value without a verified citation is shown as unverified, conflicting or missing,
never guessed. Trust beats speed: the product earns its place by being right and showing why.

## Voice
- Feels like: precise, calm, paper-literate (the broker's own documents are the picture: "Paper trail", 2026-11-03).
- People in serif (client names, page and section titles); the system in Inter.
- Copy is plain and specific: "2 documents not read", "Proposal didn't finish". No hype, no exclamation marks,
  no AI personality, no em dashes.
- Never feels like: a generic SaaS dashboard, a chatbot, a toy. No invented data shown as real
  (mocks say they are mocks).

## Principles for design decisions
1. One next action per item; the queue is the home, raw runs live in Activity.
2. Show the source: citations, page chips and the document beside the value.
3. Honest states over smooth ones: failed, partial, conflicting and unverified are visible and recoverable
   (Retry, Undo), never hidden.
4. Keep the reference product's structure (rail, panels, report table); express the product through voice, marks and motion.
5. Motion explains change (an item moved, a value landed); it never decorates. Keyboard is instant;
   reduced motion keeps fades only.
6. Accessible by default: axe 0 at 1440, 1024 and 390; contrast per DESIGN.md.

## Out of scope for now
CRM/client entity pages, real integrations, notifications, multi-user agencies, dark theme.
