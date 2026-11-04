# CLAUDE.md: BrokerDocs

Read `PRODUCT.md`, `DESIGN.md` and the latest entries of `HANDOFF.md` before working.

## Extraction cache rule

Bump `EXTRACTION_VERSION` in `backend/pipeline/cache.py` whenever verify, merge, windows or
extraction-prompt behaviour changes, so cached reads made under the old rules are never reused.
`BROKERDOCS_EXTRACTION_CACHE=off` reads live; evals and the dev seed run with it off.

## Decisions with Fastino GLiDE (testing, owner 2026-11-03)

**PAUSED by owner 2026-11-03: do not use or call Fastino until the owner resumes it** (account billing blocks inference).

Skill: `.claude/skills/fastino-glide` (hosted API `POST https://api.fastino.ai/v1/systemone`, key in `FASTINO_API_KEY`).
Use it whenever the product needs a decision: classify (document kind, role, claim status), route, approve or reject,
or score, with calibrated confidence instead of generated text. Testing stage: run it beside the current OpenAI path
and compare on our eval truth before any swap; tune thresholds on fixtures, never a fixed 0.5. It sends text to an
external service: synthetic fixtures only. It never makes owner decisions (scope, design, approvals).

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. Route only to skills in the session's available-skills list; answer directly for quick questions or small scoped edits.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
