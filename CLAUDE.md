# CLAUDE.md: BrokerDocs

Read `PRODUCT.md`, `DESIGN.md` and the latest entries of `HANDOFF.md` before working.

## Extraction cache rule

Bump `EXTRACTION_VERSION` in `backend/pipeline/cache.py` whenever verify, merge, windows or
extraction-prompt behaviour changes, so cached reads made under the old rules are never reused.
`BROKERDOCS_EXTRACTION_CACHE=off` reads live; evals and the dev seed run with it off.

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
