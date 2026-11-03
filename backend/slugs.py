"""Workflow slugs: the one definition (slice 4, D4).

Import-free on purpose, so any module (db, work_queue, the runner) can use the same names
without an import loop. `db` re-exports them for the modules that already read `db.*_SLUG`.
"""

COMPARE_QUOTES_SLUG = "compare-quotes"
PROPOSAL_BUILDER_SLUG = "proposal-builder"
POLICY_CHECK_SLUG = "policy-check"
CERTIFICATE_SLUG = "certificate"

ALL_SLUGS = (COMPARE_QUOTES_SLUG, PROPOSAL_BUILDER_SLUG, POLICY_CHECK_SLUG, CERTIFICATE_SLUG)
