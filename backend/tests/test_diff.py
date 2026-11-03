"""Slice 3 T4: pipeline/diff.py, the renewal-centered discrepancy engine (D10, D14, D15).

Table-driven: each row is (expiring, renewal, quote, ams) values for one key and the item the
diff must produce (or None). Values are RoleFact objects; a bare string means a verified value.
"""

from __future__ import annotations

import json
import random

import pytest

from backend.pipeline import diff
from backend.pipeline.diff import ItemState, RoleFact, RoleInput, Severity, SlotState
from backend.schemas import POLICY_CHECK_FIELDS, ConflictValue, EvidenceState, scalar_fields

V = EvidenceState.VERIFIED
ABSENT = RoleFact(None, EvidenceState.ABSENT)
UNVERIFIED = RoleFact("$250,000", EvidenceState.NOT_FOUND)
CONFLICT = RoleFact(None, EvidenceState.CONFLICTING, passage_ids=(41, 42), conflicts=(
    ConflictValue(value="$300,000", page_no=2, passage_id=41),
    ConflictValue(value="$270,000", page_no=30, passage_id=42)))


def fact(value, pid=1):
    if value is None or isinstance(value, RoleFact):
        return value
    return RoleFact(value, V, passage_ids=(pid,), fact_ids=(pid + 1000,))


_PIDS = {"expiring": 10, "renewal": 20, "quote": 30, "ams": 40}


def roles(key, expiring=None, renewal=None, quote="__none__", ams="__none__", **extra):
    """Role inputs with one key; quote/ams default to 'no document uploaded'."""
    out = {}
    for role, value in (("expiring", expiring), ("renewal", renewal), ("quote", quote), ("ams", ams)):
        if value == "__none__":
            continue
        facts = {} if value is None else {key: fact(value, _PIDS[role])}
        out[role] = RoleInput(facts=facts)
    for role, ri in extra.items():
        out[role] = ri
    return out


def one(key, **kw):
    items = diff.diff(roles(key, **kw))
    assert len(items) <= 1, items
    return items[0] if items else None


# --------------------------------------------------------------------- the table


ROWS = [
    # id, key, kwargs, expected (state, change, severity) or None
    ("equal", "coverage_a_limit", dict(expiring="$300,000", renewal="$300,000"), None),
    ("equal_after_normalization", "coverage_a_limit", dict(expiring="$300,000.00", renewal="300,000"), None),
    ("equal_million_words", "coverage_e_limit", dict(expiring="$1,000,000", renewal="$1 million"), None),
    ("limit_down_20pct", "coverage_a_limit", dict(expiring="$300,000", renewal="$240,000"),
     ("discrepancy", "decrease", "critical")),
    ("limit_down_exactly_10pct_is_high", "coverage_a_limit", dict(expiring="$300,000", renewal="$270,000"),
     ("discrepancy", "decrease", "high")),
    ("limit_down_just_over_10pct", "coverage_a_limit", dict(expiring="$300,000", renewal="$269,999"),
     ("discrepancy", "decrease", "critical")),
    ("limit_down_5pct", "coverage_c_limit", dict(expiring="$150,000", renewal="$142,500"),
     ("discrepancy", "decrease", "high")),
    ("limit_up", "coverage_a_limit", dict(expiring="$300,000", renewal="$330,000"),
     ("discrepancy", "increase", "high")),
    ("limit_removed", "coverage_d_limit", dict(expiring="$60,000", renewal=None),
     ("discrepancy", "removed", "high")),
    ("limit_added", "coverage_f_limit", dict(expiring=None, renewal="$5,000"),
     ("discrepancy", "added", "medium")),
    ("both_absent", "coverage_f_limit", dict(expiring=None, renewal=None), None),
    ("limit_unparseable_change", "coverage_b_limit", dict(expiring="10% of Coverage A", renewal="$30,000"),
     ("discrepancy", "changed", "high")),
    ("deductible_up", "deductible_all_peril", dict(expiring="$1,000", renewal="$2,500"),
     ("discrepancy", "increase", "critical")),
    ("deductible_down", "deductible_all_peril", dict(expiring="$2,500", renewal="$1,000"),
     ("discrepancy", "decrease", "medium")),
    ("deductible_percent_up", "deductible_wind_hail", dict(expiring="1%", renewal="2% of Coverage A"),
     ("discrepancy", "increase", "critical")),
    ("deductible_percent_equal", "deductible_wind_hail", dict(expiring="2%", renewal="2% of Coverage A"), None),
    ("deductible_basis_changed", "deductible_wind_hail", dict(expiring="$2,500", renewal="2%"),
     ("discrepancy", "changed", "high")),
    ("deductible_added", "deductible_wind_hail", dict(expiring=None, renewal="2%"),
     ("discrepancy", "added", "critical")),
    ("premium_up", "total_premium", dict(expiring="$1,850", renewal="$2,100"),
     ("discrepancy", "increase", "medium")),
    ("discount_dropped", "discount_multi_policy", dict(expiring="-$125", renewal=None),
     ("discrepancy", "removed", "high")),
    ("discount_changed", "discount_multi_policy", dict(expiring="-$125", renewal="-$110"),
     ("discrepancy", "decrease", "low")),
    ("named_insured_formatting_only", "named_insured", dict(expiring="JANE  DOE", renewal="Jane Doe"), None),
    ("address_abbreviation_only", "insured_address",
     dict(expiring="12 Elm St., Springfield", renewal="12 Elm Street Springfield"), None),
    ("named_insured_changed", "named_insured", dict(expiring="Jane Doe", renewal="Jane and John Doe"),
     ("discrepancy", "changed", "medium")),
    ("policy_number_changed_is_low", "policy_number", dict(expiring="HO-1234-01", renewal="HO-1234-02"),
     ("discrepancy", "changed", "low")),
    # D10: the term always moves at renewal; it is compared only with quote and AMS
    ("term_not_compared_with_expiring", "policy_term",
     dict(expiring="01/01/2026 - 01/01/2027", renewal="01/01/2027 - 01/01/2028"), None),
    ("term_matches_ams_written_differently", "policy_term",
     dict(expiring="01/01/2026 - 01/01/2027", renewal="01/01/2027 - 01/01/2028",
          ams="January 1, 2027 to January 1, 2028"), None),
    ("term_mismatch_ams_is_high", "policy_term",
     dict(expiring="01/01/2026 - 01/01/2027", renewal="01/01/2027 - 01/01/2028", ams="02/01/2027 - 02/01/2028"),
     ("discrepancy", "mismatch", "high")),
    # D10: absent in AMS or quote never counts
    ("absent_in_ams_never_counts", "coverage_a_limit", dict(expiring="$300,000", renewal="$300,000", ams=None), None),
    ("absent_in_quote_never_counts", "coverage_a_limit", dict(expiring="$300,000", renewal="$300,000", quote=None),
     None),
    ("quote_mismatch_flat_high", "coverage_a_limit",
     dict(expiring="$300,000", renewal="$300,000", quote="$310,000"), ("discrepancy", "mismatch", "high")),
    ("ams_mismatch_flat_high_even_when_small", "total_premium",
     dict(expiring="$2,100", renewal="$2,100", ams="$2,101"), ("discrepancy", "mismatch", "high")),
    ("ams_present_renewal_absent", "coverage_f_limit", dict(expiring=None, renewal=None, ams="$5,000"),
     ("discrepancy", "mismatch", "high")),
    ("expiring_change_wins_over_flat_high", "coverage_a_limit",
     dict(expiring="$300,000", renewal="$240,000", ams="$300,000"), ("discrepancy", "decrease", "critical")),
    # D15: uncertain values
    ("renewal_conflicting_needs_review", "coverage_a_limit", dict(expiring="$300,000", renewal=CONFLICT),
     ("needs_review", "needs_review", "low")),
    ("expiring_conflicting_needs_review", "coverage_a_limit", dict(expiring=CONFLICT, renewal="$300,000"),
     ("needs_review", "needs_review", "low")),
    ("renewal_unverified_never_removed", "coverage_a_limit", dict(expiring="$300,000", renewal=UNVERIFIED),
     ("needs_review", "needs_review", "low")),
    ("expiring_unverified_never_added", "coverage_a_limit", dict(expiring=UNVERIFIED, renewal=None),
     ("needs_review", "needs_review", "low")),
    ("ams_conflicting_never_a_mismatch", "coverage_a_limit",
     dict(expiring="$300,000", renewal="$300,000", ams=CONFLICT), None),
    ("quote_unverified_never_a_mismatch", "coverage_a_limit",
     dict(expiring="$300,000", renewal="$300,000", quote=UNVERIFIED), None),
    ("scan_read_counts_as_a_value", "coverage_a_limit",
     dict(expiring="$300,000", renewal=RoleFact("$240,000", EvidenceState.SCAN_READ, passage_ids=(5,))),
     ("discrepancy", "decrease", "critical")),
    # forms align by number: an edition change is a change, not an add plus a remove
    ("form_edition_changed", "form:HO0003", dict(expiring="HO 00 03 04 91", renewal="HO 00 03 05 11"),
     ("discrepancy", "edition_changed", "medium")),
    ("form_same_edition_written_differently", "form:HO0003",
     dict(expiring="HO 00 03 05 11", renewal="HO 00 03 (05/11)"), None),
    ("form_removed", "form:HO0490", dict(expiring="HO 04 90 05 11", renewal=None),
     ("discrepancy", "removed", "high")),
    ("form_added", "form:HO0416", dict(expiring=None, renewal="HO 04 16 05 11"),
     ("discrepancy", "added", "medium")),
    # spec section 8: unknown keys go to the Other bucket with Low severity, never dropped
    ("unknown_key_other_bucket", "sinkhole_limit", dict(expiring="$10,000", renewal="$5,000"),
     ("discrepancy", "decrease", "low")),
]


@pytest.mark.parametrize("key,kw,expected", [r[1:] for r in ROWS], ids=[r[0] for r in ROWS])
def test_rule_table(key, kw, expected):
    item = one(key, **kw)
    if expected is None:
        assert item is None, item and item.to_dict()
        return
    assert item is not None
    assert (str(item.state), item.change, str(item.severity)) == expected
    assert item.key == key


# --------------------------------------------------------------------- slots


def test_slots_are_in_role_order_and_carry_verified_citations_only():
    item = one("coverage_a_limit", expiring="$300,000", renewal="$240,000", quote=None)
    by = {s.role: s for s in item.slots}
    assert [s.role for s in item.slots] == ["expiring", "quote", "renewal", "ams"]
    assert (by["expiring"].state, by["expiring"].value, by["expiring"].passage_ids) == (SlotState.VALUE, "$300,000", (10,))
    assert by["expiring"].fact_ids == (1010,)
    assert by["quote"].state == SlotState.NOT_STATED and by["quote"].value is None
    assert by["ams"].state == SlotState.NO_DOCUMENT and by["ams"].passage_ids == ()


def test_needs_review_slot_shows_the_conflict_list_and_no_guessed_value():
    item = one("coverage_a_limit", expiring="$300,000", renewal=CONFLICT)
    r = next(s for s in item.slots if s.role == "renewal")
    assert r.state == SlotState.NEEDS_REVIEW and r.value is None
    assert [(c.value, c.page_no, c.passage_id) for c in r.conflicts] == [("$300,000", 2, 41), ("$270,000", 30, 42)]
    assert r.passage_ids == (41, 42)
    assert "removed" not in item.title.lower() and "equal" not in item.explanation.lower()
    assert "$300,000 (page 2)" in item.explanation and "$270,000 (page 30)" in item.explanation


def test_unverified_slot_never_shows_the_unverified_value():
    item = one("coverage_a_limit", expiring="$300,000", renewal=UNVERIFIED)
    r = next(s for s in item.slots if s.role == "renewal")
    assert r.state == SlotState.NEEDS_REVIEW and r.value is None and r.passage_ids == ()
    assert "$250,000" not in item.explanation and "$250,000" not in item.title


def test_optional_role_not_read_is_distinct_from_not_stated():
    rs = roles("coverage_a_limit", expiring="$300,000", renewal="$240,000")
    rs["quote"] = RoleInput(facts={}, read=False)
    (item,) = diff.diff(rs)
    q = next(s for s in item.slots if s.role == "quote")
    assert q.state == SlotState.NOT_READ and q.value is None


def test_quote_not_read_never_counts_as_a_mismatch():
    rs = roles("coverage_a_limit", expiring="$300,000", renewal="$300,000")
    rs["quote"] = RoleInput(facts={"coverage_a_limit": fact("$1", 30)}, read=False)
    assert diff.diff(rs) == []


@pytest.mark.parametrize("missing", ["expiring", "renewal"])
def test_required_roles_must_be_present_and_read(missing):
    rs = roles("coverage_a_limit", expiring="$300,000", renewal="$300,000")
    del rs[missing]
    with pytest.raises(ValueError):
        diff.diff(rs)
    rs = roles("coverage_a_limit", expiring="$300,000", renewal="$300,000")
    rs[missing] = RoleInput(facts={}, read=False)
    with pytest.raises(ValueError):
        diff.diff(rs)


def test_unknown_role_is_rejected():
    rs = roles("coverage_a_limit", expiring="$1", renewal="$1")
    rs["binder"] = RoleInput(facts={})
    with pytest.raises(ValueError):
        diff.diff(rs)


# --------------------------------------------------------------------- deterministic text (D14)


TEXT_ROWS = [
    ("coverage_a_limit", dict(expiring="$300,000", renewal="$240,000"),
     "Coverage A (Dwelling) decreased by 20.0%",
     "Expiring states $300,000; renewal states $240,000, a decrease of $60,000 (20.0%)."),
    ("coverage_a_limit", dict(expiring="$300,000", renewal="$330,000"),
     "Coverage A (Dwelling) increased by 10.0%",
     "Expiring states $300,000; renewal states $330,000, an increase of $30,000 (10.0%)."),
    ("deductible_wind_hail", dict(expiring="1%", renewal="2% of Coverage A"),
     "Wind/Hail Deductible increased",
     "Expiring states 1%; renewal states 2% of Coverage A, an increase of 1 percentage point."),
    ("coverage_d_limit", dict(expiring="$60,000", renewal=None),
     "Coverage D (Loss of Use) not stated in renewal",
     "Expiring states $60,000; renewal does not state it."),
    ("coverage_f_limit", dict(expiring=None, renewal="$5,000"),
     "Coverage F (Medical Payments) added at renewal",
     "Renewal states $5,000; expiring does not state it."),
    ("form:HO0003", dict(expiring="HO 00 03 04 91", renewal="HO 00 03 05 11"),
     "Form HO 00 03 edition changed",
     "Expiring states HO 00 03 04 91; renewal states HO 00 03 05 11 (edition 04/91 to 05/11)."),
    ("coverage_a_limit", dict(expiring="$300,000", renewal="$300,000", ams="$310,000"),
     "Coverage A (Dwelling) differs from AMS",
     "Renewal states $300,000; AMS states $310,000."),
    ("coverage_f_limit", dict(expiring=None, renewal=None, ams="$5,000"),
     "Coverage F (Medical Payments) differs from AMS",
     "AMS states $5,000; renewal does not state it."),
    ("named_insured", dict(expiring="Jane Doe", renewal="Jane and John Doe"),
     "Named Insured changed",
     "Expiring states Jane Doe; renewal states Jane and John Doe."),
]


@pytest.mark.parametrize("key,kw,title,explanation", TEXT_ROWS, ids=[f"{r[0]}-{i}" for i, r in enumerate(TEXT_ROWS)])
def test_title_and_explanation_are_built_in_code_from_the_slots(key, kw, title, explanation):
    item = one(key, **kw)
    assert item.title == title
    assert item.explanation == explanation


def test_combined_findings_explain_every_role_and_name_both_in_the_title():
    item = one("coverage_a_limit", expiring="$300,000", renewal="$240,000", quote="$240,000", ams="$300,000")
    assert item.severity == Severity.CRITICAL and item.change == "decrease"
    assert item.title == "Coverage A (Dwelling) decreased by 20.0%"
    assert item.explanation == ("Expiring states $300,000; renewal states $240,000, a decrease of $60,000 (20.0%). "
                                "AMS states $300,000.")
    item = one("coverage_a_limit", expiring="$300,000", renewal="$300,000", quote="$310,000", ams="$320,000")
    assert item.title == "Coverage A (Dwelling) differs from quote and AMS"
    assert item.explanation == "Renewal states $300,000; quote states $310,000; AMS states $320,000."


def test_needs_review_text():
    item = one("coverage_a_limit", expiring="$300,000", renewal=UNVERIFIED)
    assert item.title == "Coverage A (Dwelling) needs review"
    assert item.explanation == ("The renewal value could not be verified against its document. "
                                "Check the source before relying on this comparison.")


def test_no_text_uses_em_dashes():
    rows = [r for r in ROWS if r[3] is not None]
    for _, key, kw, _ in rows:
        item = one(key, **kw)
        assert "—" not in item.title + item.explanation and "–" not in item.title + item.explanation


# --------------------------------------------------------------------- severity table in code


def test_every_catalogue_key_has_a_fact_type():
    for f in scalar_fields(POLICY_CHECK_FIELDS):
        assert f.key in diff.FACT_TYPES, f.key
    assert diff.fact_type("form:HO0003") == "form" and diff.fact_type("made_up_key") == "other"


def test_severity_table_covers_every_change_a_fact_type_can_produce():
    for ft, changes in diff.CHANGES_BY_TYPE.items():
        for change in changes:
            assert isinstance(diff.severity(ft, change), Severity), (ft, change)
    assert diff.severity("limit", "mismatch") == Severity.HIGH == diff.severity("other", "mismatch")
    assert diff.severity("form", "needs_review") == Severity.LOW
    assert diff.LIMIT_DROP_CRITICAL == diff.Fraction(1, 10)


# --------------------------------------------------------------------- ids, order, bytes


def test_item_id_is_derived_from_the_key_and_stable():
    a = one("coverage_a_limit", expiring="$300,000", renewal="$240,000")
    b = one("coverage_a_limit", expiring="$300,000", renewal="$100,000", ams="$1")
    assert a.id == b.id == "disc-coverage-a-limit"
    assert one("form:HO0003", expiring="HO 00 03 04 91", renewal="HO 00 03 05 11").id == "disc-form-ho0003"


def _policy(order_seed: int | None = None) -> dict[str, RoleInput]:
    exp = {"coverage_a_limit": fact("$300,000", 1), "deductible_all_peril": fact("$1,000", 2),
           "discount_multi_policy": fact("-$125", 3), "form:HO0003": fact("HO 00 03 04 91", 4),
           "form:HO0490": fact("HO 04 90 05 11", 5), "named_insured": fact("Jane Doe", 6),
           "total_premium": fact("$1,850", 7), "coverage_c_limit": CONFLICT}
    ren = {"coverage_a_limit": fact("$240,000", 11), "deductible_all_peril": fact("$2,500", 12),
           "form:HO0003": fact("HO 00 03 05 11", 14), "named_insured": fact("JANE DOE", 16),
           "total_premium": fact("$2,100", 17), "coverage_c_limit": fact("$150,000", 18),
           "form:HO0416": fact("HO 04 16 05 11", 19)}
    ams = {"total_premium": fact("$2,000", 27), "coverage_a_limit": fact("$300,000", 21)}
    out = {"expiring": exp, "renewal": ren, "ams": ams}
    if order_seed is not None:
        rnd = random.Random(order_seed)
        shuffled = {}
        for role in rnd.sample(list(out), len(out)):
            items = list(out[role].items())
            rnd.shuffle(items)
            shuffled[role] = dict(items)
        out = shuffled
    return {role: RoleInput(facts=facts) for role, facts in out.items()}


def test_output_is_ordered_by_severity_then_key():
    items = diff.diff(_policy())
    got = [(str(i.severity), i.key) for i in items]
    assert got == [
        ("critical", "coverage_a_limit"),
        ("critical", "deductible_all_peril"),
        ("high", "discount_multi_policy"),
        ("high", "form:HO0490"),
        ("high", "total_premium"),  # increase is medium, the AMS mismatch is flat High
        ("medium", "form:HO0003"),
        ("medium", "form:HO0416"),
        ("low", "coverage_c_limit"),  # needs review
    ]
    assert next(i for i in items if i.key == "coverage_c_limit").state == ItemState.NEEDS_REVIEW


def test_reruns_are_byte_identical_whatever_the_input_order():
    first = diff.to_json(diff.diff(_policy()))
    for seed in range(5):
        assert diff.to_json(diff.diff(_policy(seed))) == first
    payload = json.loads(first)
    assert payload[0]["id"] == "disc-coverage-a-limit" and payload[0]["slots"][0]["role"] == "expiring"


def test_lob_is_stamped_on_every_item():
    items = diff.diff(_policy(), lob="homeowners")
    assert {i.lob for i in items} == {"homeowners"}
    assert items[0].to_dict()["lob"] == "homeowners"


# --------------------------------------------------------------------- normalization helpers


@pytest.mark.parametrize("raw,expected", [
    ("$300,000", ("300000", False, True)),
    ("300,000.00", ("300000", False, False)),
    ("$1 million", ("1000000", False, True)),
    ("$300K", ("300000", False, True)),
    ("2% of Coverage A", ("2", True, False)),
    ("-$125", ("-125", False, True)),
    ("$5,000 per occurrence", ("5000", False, True)),
    ("12 months", ("12", False, False)),
])
def test_parse_amount(raw, expected):
    from decimal import Decimal

    a = diff.parse_amount(raw)
    assert (a.value, a.percent, a.money) == (Decimal(expected[0]), expected[1], expected[2])


@pytest.mark.parametrize("raw", [None, "", "Included", "$1,000,000 / $2,000,000", "2% or $2,500"])
def test_parse_amount_none_for_no_or_several_amounts(raw):
    assert diff.parse_amount(raw) is None


def test_parse_dates():
    assert diff.parse_dates("01/01/2027 - 01/01/28") == ("2027-01-01", "2028-01-01")
    assert diff.parse_dates("Jan. 1st, 2027 to 2028-01-01") == ("2027-01-01", "2028-01-01")
    assert diff.parse_dates("Page 4, 2027") == () and diff.parse_dates("13/40/2027") == ()


def test_address_words_expand_only_for_the_address():
    assert diff.normalize_text("12 N Elm St.", address=True) == "12 north elm street"
    assert one("named_insured", expiring="John S Doe", renewal="John South Doe").change == "changed"


def test_renewal_fact_failed_is_needs_review_not_removed():
    item = one("coverage_a_limit", expiring="$300,000", renewal=RoleFact(None, EvidenceState.FAILED))
    assert item.state == ItemState.NEEDS_REVIEW
    r = next(s for s in item.slots if s.role == "renewal")
    assert r.state == SlotState.NOT_READ
    assert item.explanation.startswith("The renewal value was not read.")


def test_percent_limit_drop_is_never_the_10pct_critical_rule():
    item = one("coverage_b_limit", expiring="10% of Coverage A", renewal="5% of Coverage A")
    assert (item.change, item.severity) == ("decrease", Severity.HIGH)


def test_edited_cell_compares_its_cited_original():
    from backend.schemas import Cell

    cell = Cell(value="$999", passage_ids=[], state=V, edited=True, original_value="$300,000",
                original_passage_ids=[3])
    rf = RoleFact.from_cell(cell)
    assert (rf.value, rf.passage_ids) == ("$300,000", (3,))


def test_role_fact_from_cell():
    from backend.schemas import Cell

    cell = Cell(value=None, passage_ids=[41, 42], fact_ids=[7], state=EvidenceState.CONFLICTING,
                conflicts=list(CONFLICT.conflicts))
    rf = RoleFact.from_cell(cell)
    assert (rf.value, rf.state, rf.passage_ids, rf.fact_ids) == (None, EvidenceState.CONFLICTING, (41, 42), (7,))
    assert rf.conflicts == CONFLICT.conflicts
