"""dev_seed's scripted model must keep producing the states the e2e journey checks.

Seeded run 5 (partial) needs one cell in state CONFLICTING: the e2e test (frontend/e2e/journey.spec.ts,
"seeded states") clicks the "Conflicting values" filter and expects two chips. The scripted model gets
it from long_120, so the conflict must be one the merge rules keep (not boilerplate, not contained).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend import dev_seed
from backend.db import COMPARE_QUOTES
from backend.pipeline import extract, merge, parse, verify, windows
from backend.schemas import EvidenceState, FactField

FIELDS = [FactField.model_validate(f) for f in COMPARE_QUOTES["fact_schema"]]
LONG_120 = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "quotes" / "long_120.pdf"


@pytest.fixture(scope="module")
def long_120_merged() -> dict[str, merge.MergedFact]:
    pages = parse.parse_pdf(LONG_120.read_bytes()).pages
    cands = []
    for w in windows.plan_windows(len(pages)):
        msgs = extract.build_extraction_messages(FIELDS, [p for p in pages if p.page_no in w],
                                                 document_name="long_120.pdf")
        user = "\n".join(p.text for p in msgs[1].parts())
        facts, _, _ = extract.normalize_output(dev_seed.extract(user), FIELDS)  # one fact per field per window
        cands += [verify.CandidateFact(f.field, f.value, f.page if f.page in w else w.first, f.quote)
                  for f in facts if f.value is not None]
    checked = verify.verify_facts(cands, pages)
    text = {p.page_no: p.text for p in pages}
    return {m.field: m for m in merge.merge_document(FIELDS, checked, text)}


def test_seeded_long_120_has_a_conflict_the_merge_rules_keep(long_120_merged):
    conflicting = [m for m in long_120_merged.values() if m.state == EvidenceState.CONFLICTING]
    assert [m.field for m in conflicting] == ["each_occurrence_limit"]
    m = conflicting[0]
    assert all(c.state == EvidenceState.VERIFIED for c in m.conflicts)
    assert [(c.value, c.page_no) for c in m.conflicts] == [("$1,000,000", 3), ("$2,000,000", 60)]


def test_seeded_long_120_other_values_are_unaffected(long_120_merged):
    assert long_120_merged["retention"].value == "$10,000 per occurrence"
    assert long_120_merged["policy_form"].state == EvidenceState.VERIFIED
    assert long_120_merged["aggregate_limit"].value == "$2,000,000"
