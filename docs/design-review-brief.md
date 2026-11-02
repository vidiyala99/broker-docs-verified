# Design review brief: BrokerDocs slice 1 (live app vs the real product)

Purpose: run `/design-review` on the built frontend and compare it, screen by screen, with the real BrokerDocs product as
seen in the demo frames. Output: a prioritized list of fixes, applied and re-checked. Max 3 review rounds, then stop and bring
the open findings and a recommendation to the owner.

## Ground truth (the real product)
- Frames: `../rea-out/frames/*.jpg` (six demo videos), `../rea-out/yt-reference/watch-eg5_xfp8/frames/` (38 frames of the official
  overview video, no narration). Notes and tokens: `docs/reference/brokerdocs-ui-reference.md`, sign-in screenshot
  `docs/reference/app-signin-1440.png`. There is NO login to app.brokerdocs.ai: only the sign-in page and the videos exist.
- Useful frames: `3kVG8k-01.jpg` (D&O comparison report, outline, toolbar, numbered chips), `vLezj1-03.jpg` (Proposal builder
  shell). View the rest to find the Compare Quotes launch, upload accordion, run progress and chat frames.

## Things that are deliberately different (do not "fix" these without the owner)
- Text wordmark "BrokerDocs" with a plain violet glyph, not the orange mark (DG7). Name comes from `frontend/src/config.ts`.
- Chips show document letter plus number ("A1", "B2"), with silhouettes per evidence state, never color alone (DG3, DG11).
- Disabled future rail items with the tooltip "coming in a later slice" (DG1).
- Launch page with Recent runs and a drop zone stating limits (DG6): no frame exists for it.
- Tab label "All runs" (the original says "All Artifacts"); top bar shows "Local workspace" not Share/Feedback.
- Input border `#848D99` (DG10 contrast), not the original's `#CED5DE`.

## Run it
Backend (scripted model, no tokens) and frontend, see `frontend/README.md`:
1. `BROKERDOCS_DATA_DIR=%TEMP%\brokerdocs-dev`, `python -m backend.dev_seed seed`, then `python -m backend.dev_seed serve` (port 8000).
2. In `frontend/`: `npm install`, `npm run dev` (port 5173 only; the backend allows only that origin).
3. Python venv: 3.12, `pymupdf>=1.24,<2` (2.0 crashes here). `OPENAI_API_KEY` must NOT be set for the dev server.
Seed runs: 1 failed, 2 cancelled, 3 partial, 4 prose-fallback, 5 partial with conflicting values, 6 main (scan-read, couldn't-verify, one edit).
Note: another project (BFL clone) also uses port 5173; stop it first or the page will show that project instead.

## Required before reviewing (global rules)
Load and apply ALL of: gstack, ui-ux-pro-max, taste-core, taste-redesign, impeccable, emil-design-eng, frontend-design.
Name what each skill said for each finding before fixing it. Drive the live page with the Playwright MCP (resize to 1440, 1024,
phone widths; tap, tab, scroll), no silent fallback to scripts. Text-only mockups, no image generation (owner decision).

## Review plan
1. Side-by-side per screen: report view, outline and trust counts, comparison table, chips, toolbar, left panel (Upload, Input
   parameters, Recent runs), chat panel and suggestions, PDF side-by-side, run progress. For each: list concrete differences in
   layout, spacing, type scale, radius, color, iconography, density, copy. Rate each as: match, intentional, or drift.
2. States from the spec state table (loading, empty, error, success, partial): check every seeded state at 1440 and 1024.
3. Interaction: chip click and Esc, cell edit and undo toast, stale tag and regenerate, cancel, retry, keyboard-only pass.
4. Accessibility: axe on every screen, focus order, contrast per `DESIGN.md`, reduced motion.
5. Taste pass: AI-slop check (impeccable, taste-core), motion (emil-design-eng), hierarchy and density against the real product.

## Known gaps to include in the review
- "Sample quotes" link (DG6) not built: needs an endpoint.
- Version history not shown (only the number).
- Real upload rejections for too-large and too-many-pages files untested in the browser.
- Chromium only.
- Backend items being fixed separately: access-log traceback (RedactSecrets), SSE replay after retry, `retryable` flag.

## Deliverables
- `docs/design-review-report.md`: findings table (screen, difference, severity, intentional or drift, fix), before/after screenshots
  under `frontend/qa-screenshots/review/`, axe results, what was fixed, what remains and why.
- Fixes committed in small commits. Update `DESIGN.md` if a token changes.
- Report open items and a recommendation; do not run a 4th round.

## Constraints
No em dashes in any UI copy or docs. Never read or print `.env`. Do not touch `backend/` except for a minimal additive change
the UI needs (list it). The BFL project's folders and servers are not part of this work.
