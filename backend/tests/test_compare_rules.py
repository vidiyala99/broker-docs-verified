"""Slice 6 (Compare Quotes match): pipeline.compare_rules unit truth tables.

T3: classifier (R1), watch-list synonyms, "None of these" only with a printed schedule (R9).
T4: mark_cells (R2, R9): Not stated, unverified, single comparable value, missing premium, ties, edited cells.
T5: Key difference check (R10, R11), template (F9) and staleness (R3).
"""

from __future__ import annotations

import pytest

from backend.pipeline import compare_rules as cr
from backend.pipeline.compose import ComposeEdit, ComposeFact
from backend.schemas import (
    ArtifactContent,
    Cell,
    CellRef,
    CellSegment,
    Column,
    EvidenceState,
    Row,
    Section,
    TableBlock,
)

V, NF, AB, SCAN = EvidenceState.VERIFIED, EvidenceState.NOT_FOUND, EvidenceState.ABSENT, EvidenceState.SCAN_READ
AI, WOS, PNC, EXC, PREM = cr.AI_KEY, cr.WAIVER_KEY, cr.PNC_KEY, cr.EXCLUSIONS_KEY, cr.PREMIUM_KEY

# --------------------------------------------------------------------- T3 classifier truth table (R1)


@pytest.mark.parametrize("key, wording, expected", [
    # additional insured
    (AI, "Additional Insured: Blanket where required by written contract", cr.BLANKET),
    (AI, "Blanket Additional Insured - Owners, Lessees or Contractors", cr.BLANKET),
    (AI, "Additional Insured - Automatic Status When Required in Construction Agreement With You", cr.BLANKET),
    (AI, "Additional insured status where required by a written contract", cr.BLANKET),
    (AI, "Additional Insured - Designated Person or Organization", cr.SCHEDULED),
    (AI, "Additional Insured: Scheduled", cr.SCHEDULED),
    (AI, "Additional insureds as named in the schedule", cr.SCHEDULED),
    (AI, "Additional Insured: Not included", cr.NOT_INCLUDED),
    (AI, "Additional insureds: Excluded", cr.NOT_INCLUDED),
    (AI, "Additional Insured: No", cr.NOT_INCLUDED),
    (AI, "Additional Insured: N/A", cr.NOT_INCLUDED),
    (AI, "Additional Insured: Included", None),  # blanket or scheduled is not said: Not stated
    (AI, "See endorsement schedule attached? Blanket and scheduled", None),  # both: ambiguous
    (AI, "Blanket Waiver of Subrogation", None),  # wording about another term only
    (AI, "Additional Insured: Blanket", cr.BLANKET),
    (AI, "", None),
    (AI, None, None),
    # waiver of subrogation
    (WOS, "Blanket Waiver of Subrogation", cr.BLANKET),
    (WOS, "Waiver of Transfer of Rights of Recovery Against Others to Us - where required by written contract",
     cr.BLANKET),
    (WOS, "Waiver of Subrogation: Scheduled", cr.SCHEDULED),
    (WOS, "Waiver of Transfer of Rights of Recovery Against Others to Us (Schedule)", cr.SCHEDULED),
    (WOS, "Waiver of Subrogation: Not available", cr.NOT_INCLUDED),
    (WOS, "Waiver of Subrogation: Included", None),
    (WOS, "Additional Insured - Designated Person or Organization", None),
    # primary and non-contributory
    (PNC, "Primary and Non-Contributory: Included", cr.INCLUDED),
    (PNC, "Primary & Non-Contributory - Yes", cr.INCLUDED),
    (PNC, "Primary and non-contributory where required by written contract", cr.INCLUDED),
    (PNC, "Primary & Non-Contributory - Not available", cr.NOT_INCLUDED),
    (PNC, "Primary and Non-Contributory: No", cr.NOT_INCLUDED),
    (PNC, "Primary and Non-Contributory: Excluded", cr.NOT_INCLUDED),
    (PNC, "Blanket Waiver of Subrogation", None),
    # edited values naming the category exactly
    (AI, "scheduled only", cr.SCHEDULED),
    (WOS, "blanket", cr.BLANKET),
    (PNC, "not included", cr.NOT_INCLUDED),
    ("retention", "Blanket", None),  # not a term row
])
def test_classifier_truth_table(key, wording, expected):
    assert cr.classify_term(key, wording) == expected


# --------------------------------------------------------------------- T3 watch list synonyms


@pytest.mark.parametrize("title, expected", [
    ("Exclusion - Action Over", ["Action over"]),
    ("Action-Over Exclusion", ["Action over"]),
    ("Third Party Over Action Exclusion", ["Action over"]),
    ("Exclusion - Injury to Employees, Workers or Contract Laborers of Insureds or Contracted Organizations",
     ["Action over"]),
    ("Residential Construction Exclusion", ["Residential / habitational work"]),
    ("Habitational Exclusion", ["Residential / habitational work"]),
    ("Exclusion - Condominiums, Townhouses and Tract Homes", ["Residential / habitational work"]),
    ("Earth Movement Exclusion", ["Subsidence / earth movement"]),
    ("Subsidence Exclusion", ["Subsidence / earth movement"]),
    ("Exclusion - Landslide, Mudslide and Sinkhole", ["Subsidence / earth movement"]),
    ("Exclusion - Designated Ongoing Operations", ["Designated ongoing operations"]),
    ("Exclusion - Designated Operations Covered By A Consolidated (Wrap-Up) Insurance Program", ["Wrap-up / OCIP"]),
    ("Wrap-Up Exclusion", ["Wrap-up / OCIP"]),
    ("OCIP / CCIP Exclusion", ["Wrap-up / OCIP"]),
    ("Owner Controlled Insurance Program Exclusion", ["Wrap-up / OCIP"]),
    ("Asbestos Exclusion", []),
    ("Exclusion - Total Pollution", []),
    ("Professional Services Exclusion", []),
    ("Action Over and Residential Work Exclusion", ["Action over", "Residential / habitational work"]),
])
def test_watch_list_synonyms(title, expected):
    assert cr.watch_matches(title) == expected


def test_watch_list_labels_round_trip_as_edited_values():
    """A broker who types the cell's own labels gets the same exclusions back."""
    for w in cr.WATCH_LIST:
        assert cr.watch_matches(w.label) == [w.label]
    joined = ", ".join(w.label for w in cr.WATCH_LIST)
    assert cr.watch_matches(joined) == [w.label for w in cr.WATCH_LIST]


# --------------------------------------------------------------------- T3 notable_exclusions cell (R9)


def fact(field, value, *, col="A", state=V, pid=None, fid=None):
    return ComposeFact(column_key=col, field=field, value=value, state=state, passage_id=pid, page_no=1, fact_id=fid)


SCHEDULE = fact("exclusions_schedule", "Schedule of Forms and Endorsements", pid=9, fid=90)


def test_exclusions_cell_lists_watch_list_matches_with_their_chips():
    cell = cr.exclusions_cell([SCHEDULE, fact("exclusion:ACTIONOVER", "Exclusion - Action Over", pid=3, fid=30),
                               fact("exclusion:ASBESTOS", "Asbestos Exclusion", pid=4, fid=40)])
    assert cell.value == "Action over" and cell.state == V and cell.complete is True
    assert cell.passage_ids == [3, 9] and cell.fact_ids == [30, 90]


def test_none_of_these_only_with_a_printed_schedule_and_a_verified_title():
    titles = [fact("exclusion:ASBESTOS", "Asbestos Exclusion", pid=4)]
    full = cr.exclusions_cell([SCHEDULE, *titles])
    assert full.value == cr.NONE_OF_THESE and full.state == V and full.complete is True
    partial = cr.exclusions_cell(titles)  # a partial list (no schedule heading): absence is not claimed
    assert partial.value == cr.NOT_STATED and partial.state == AB and partial.complete is False
    heading_only = cr.exclusions_cell([SCHEDULE])
    assert heading_only.value == cr.NOT_STATED and heading_only.state == AB
    assert cr.exclusions_cell([]).value == cr.NOT_STATED


def test_exclusions_cell_partial_list_still_shows_a_printed_watch_list_exclusion():
    cell = cr.exclusions_cell([fact("exclusion:WRAPUP", "Wrap-Up Exclusion", pid=2)])
    assert cell.value == "Wrap-up / OCIP" and cell.state == V and cell.complete is False


def test_exclusions_cell_unverified_titles_do_not_count_and_scan_reads_stay_scan_read():
    assert cr.exclusions_cell([SCHEDULE, fact("exclusion:X", "Action Over Exclusion", state=NF)]).value \
        == cr.NOT_STATED
    scan = cr.exclusions_cell([fact("exclusion:X", "Action Over Exclusion", state=SCAN, pid=5)])
    assert scan.state == SCAN and scan.value == "Action over"
    assert cr.exclusions_cell([], failed=True).state == EvidenceState.FAILED


def test_term_cell_shows_category_or_not_stated():
    verified = Cell(value="Additional Insured - Designated Person or Organization", passage_ids=[1], fact_ids=[7],
                    state=V)
    assert cr.term_cell(AI, verified).value == cr.SCHEDULED and cr.term_cell(AI, verified).passage_ids == [1]
    unclear = cr.term_cell(AI, Cell(value="Additional Insured: Included", passage_ids=[1], fact_ids=[7], state=V))
    assert unclear.value == cr.NOT_STATED and unclear.state == AB and unclear.passage_ids == [] \
        and unclear.fact_ids == [7]
    assert cr.term_cell(AI, Cell(value="Not specified", state=AB)).value == cr.NOT_STATED
    nf = Cell(value="Couldn't verify", state=NF)
    assert cr.term_cell(AI, nf) == nf


# --------------------------------------------------------------------- T4 marks truth table (R2)


def table(rows: dict[str, dict[str, Cell]], columns=("A", "B", "C")) -> TableBlock:
    return TableBlock(id="t", columns=[Column(key=c, label=f"Carrier {c}") for c in columns],
                      rows=[Row(key=k, label=k, cells=cells) for k, cells in rows.items()])


def c(value, state=V, **kw):
    return Cell(value=value, state=state, **kw)


def test_endorsement_rows_rank_and_reason():
    t = table({AI: {"A": c(cr.BLANKET), "B": c(cr.SCHEDULED), "C": c(cr.NOT_INCLUDED)},
               PNC: {"A": c(cr.INCLUDED), "B": c(cr.NOT_INCLUDED), "C": c(cr.INCLUDED)}})
    marks = cr.mark_cells(t)
    assert marks == {(AI, "B"): cr.Mark("worse", "Others: Blanket"), (AI, "C"): cr.Mark("worse", "Others: Blanket"),
                     (PNC, "B"): cr.Mark("worse", "Others: Included")}


def test_not_stated_and_unverified_cells_are_never_marked_or_counted():
    t = table({AI: {"A": c(cr.SCHEDULED), "B": c(cr.NOT_STATED, AB), "C": c("Couldn't verify", NF)},
               WOS: {"A": c(cr.SCHEDULED), "B": c(cr.BLANKET, SCAN), "C": c("Conflicting values",
                                                                            EvidenceState.CONFLICTING)}})
    assert cr.mark_cells(t) == {}  # one comparable value per row: nothing to compare against


def test_single_comparable_value_and_all_equal_rows_are_unmarked():
    t = table({AI: {"A": c(cr.BLANKET), "B": c(cr.BLANKET), "C": c(cr.NOT_STATED, AB)},
               WOS: {"A": c(cr.NOT_INCLUDED), "B": c(cr.NOT_STATED, AB), "C": c(cr.NOT_STATED, AB)}})
    assert cr.mark_cells(t) == {}


def test_edited_cells_are_eligible_and_classified():
    t = table({AI: {"A": c(cr.BLANKET), "B": c("scheduled only", None, edited=True),
                    "C": c("Not stated", AB, edited=False)}})
    assert cr.mark_cells(t) == {(AI, "B"): cr.Mark("worse", "Others: Blanket")}
    # an edited cell that states no category counts as nothing
    t2 = table({AI: {"A": c(cr.BLANKET), "B": c("ask the carrier", None, edited=True), "C": c(cr.SCHEDULED)}})
    assert set(cr.mark_cells(t2)) == {(AI, "C")}


def test_premium_best_only_when_every_column_has_one_and_no_tie():
    def prem(a, b, c_):
        return table({PREM: {"A": a, "B": b, "C": c_}})
    assert cr.mark_cells(prem(c("$12,500"), c("$14,490.00"), c("$13,000"))) == {
        (PREM, "A"): cr.Mark("best", "Lowest premium")}
    assert cr.mark_cells(prem(c("$12,500"), c("Not specified", AB), c("$13,000"))) == {}  # missing premium
    assert cr.mark_cells(prem(c("$12,500"), c("Couldn't verify", NF), c("$13,000"))) == {}  # unverified
    assert cr.mark_cells(prem(c("$12,500"), c("$12,500.00"), c("$13,000"))) == {}  # tie for lowest
    assert cr.mark_cells(prem(c("$12,500"), c("$9,000", None, edited=True), c("$13,000"))) == {
        (PREM, "B"): cr.Mark("best", "Lowest premium")}  # an edited premium counts
    assert cr.mark_cells(prem(c("$12,500 plus $500 fees"), c("$14,490"), c("$13,000"))) == {}  # two amounts
    assert cr.mark_cells(table({PREM: {"A": c("$12,500")}}, columns=("A",))) == {}  # one quote


def test_exclusions_worse_only_against_a_complete_schedule():
    complete_none = c(cr.NONE_OF_THESE, complete=True)
    has_action = c("Action over", complete=True)
    partial_none = c("Wrap-up / OCIP", complete=False)
    # A prints action over; B prints a complete schedule without it -> A worse
    assert cr.mark_cells(table({EXC: {"A": has_action, "B": complete_none}}, columns=("A", "B"))) == {
        (EXC, "A"): cr.Mark("worse", "Excludes action over")}
    # B's list is partial (no schedule heading): A is not marked for action over; B is worse for wrap-up only
    # because A's schedule is complete and lacks it
    marks = cr.mark_cells(table({EXC: {"A": has_action, "B": partial_none}}, columns=("A", "B")))
    assert marks == {(EXC, "B"): cr.Mark("worse", "Excludes wrap-up / OCIP")}
    # partial vs partial: no claim of absence anywhere
    assert cr.mark_cells(table({EXC: {"A": c("Action over", complete=False), "B": partial_none}},
                               columns=("A", "B"))) == {}
    # Not stated is not comparable
    assert cr.mark_cells(table({EXC: {"A": has_action, "B": c(cr.NOT_STATED, AB, complete=False)}},
                               columns=("A", "B"))) == {}
    # an edited exclusions cell counts as complete
    edited = c("None of these", None, edited=True)
    assert cr.mark_cells(table({EXC: {"A": c("Action over, Wrap-up / OCIP", complete=False), "B": edited}},
                               columns=("A", "B"))) == {
        (EXC, "A"): cr.Mark("worse", "Excludes action over and wrap-up / OCIP")}


def test_other_rows_are_never_marked():
    t = table({"retention": {"A": c("$5,000"), "B": c("$25,000"), "C": c("$10,000")},
               "each_occurrence_limit": {"A": c("$1,000,000"), "B": c("$2,000,000"), "C": c("$1,000,000")},
               "rate": {"A": c("1.2"), "B": c("1.5"), "C": c("1.1")}})
    assert cr.mark_cells(t) == {}


def test_apply_edits_marks_what_the_broker_sees():
    t = table({PREM: {"A": c("$12,500"), "B": c("$14,490"), "C": c("$13,000")}})
    edited = cr.apply_edits([t], [ComposeEdit(column_key="B", field=PREM, value="$9,000", original_value="$14,490")])
    assert cr.mark_cells(edited) == {(PREM, "B"): cr.Mark("best", "Lowest premium")}
    assert cr.mark_cells(t) == {(PREM, "A"): cr.Mark("best", "Lowest premium")}  # the input is unchanged
    cell = edited[0].rows[0].cells["B"]
    assert cell.edited and cell.original_value == "$14,490" and cell.passage_ids == []


# --------------------------------------------------------------------- T5 Key difference check (R11)

COLUMNS = ("A", "B")


def kd_table(**over) -> TableBlock:
    rows = {PREM: {"A": c("$12,500", passage_ids=[1]), "B": c("$14,490.00", passage_ids=[2])},
            AI: {"A": c(cr.SCHEDULED, passage_ids=[3]), "B": c(cr.BLANKET, passage_ids=[4])}}
    rows.update(over)
    return TableBlock(id="t", columns=[Column(key="A", label="Harborline"), Column(key="B", label="Juniper Peak")],
                      rows=[Row(key=k, label=k, cells=v) for k, v in rows.items()])


def seg(text, *cells):
    return CellSegment(text=text, cells=[CellRef(row=r, column=col) for r, col in cells])


GOOD = [seg("A (Harborline) has the lowest premium ($12,500)", (PREM, "A")), seg(" but "),
        seg("only scheduled additional insured, where B (Juniper Peak) is blanket", (AI, "A"), (AI, "B")),
        seg(".")]


def test_correct_key_difference_passes():
    t = kd_table()
    assert cr.check_key_difference(GOOD, [t], cr.mark_cells(t)) == []


def test_swapped_premium_fails():
    t = kd_table()
    bad = [seg("A (Harborline) has the lowest premium ($14,490.00)", (PREM, "A")), *GOOD[1:]]
    errors = cr.check_key_difference(bad, [t], cr.mark_cells(t))
    assert any("14,490.00" in e for e in errors)


def test_uncited_carrier_and_uncited_term_fail():
    t = kd_table()
    marks = cr.mark_cells(t)
    uncited = [GOOD[0], seg(" but "), seg("only scheduled additional insured, unlike Juniper Peak", (AI, "A")),
               seg(".")]
    assert any("names document B" in e for e in cr.check_key_difference(uncited, [t], marks))
    no_term_cell = [GOOD[0], seg(" but its additional insured is narrower", (PREM, "A"))]
    assert any("additional_insured" in e for e in cr.check_key_difference(no_term_cell, [t], marks))


def test_direction_words_must_agree_with_the_marks():
    t = kd_table()
    marks = cr.mark_cells(t)
    broader = [GOOD[0], seg(" and broader additional insured", (AI, "A")), seg(".")]
    assert any("broader or better" in e for e in cr.check_key_difference(broader, [t], marks))
    cheap_b = [seg("B (Juniper Peak) is the cheapest at $14,490.00", (PREM, "B")), seg(" "), *GOOD[2:]]
    assert any("not marked best" in e for e in cr.check_key_difference(cheap_b, [t], marks))
    two_premiums = [seg("A (Harborline) is cheaper than B (Juniper Peak) at $12,500 against $14,490.00",
                        (PREM, "A"), (PREM, "B")), seg(" "), *GOOD[2:]]
    assert any("not marked best" in e for e in cr.check_key_difference(two_premiums, [t], marks))


def test_unknown_cell_and_no_marked_cell_fail():
    t = kd_table()
    marks = cr.mark_cells(t)
    unknown = [seg("A (Harborline) has the lowest premium ($12,500)", (PREM, "A"), ("made_up", "A"))]
    assert any("not in the tables" in e for e in cr.check_key_difference(unknown, [t], marks))
    unmarked = [seg("B (Juniper Peak) quotes $14,490.00", (PREM, "B"))]
    assert "the key difference cites no marked cell" in cr.check_key_difference(unmarked, [t], marks)
    assert cr.check_key_difference([seg("  ")], [t], marks) == ["the key difference has no text"]


def test_an_edited_marked_cell_is_citable():
    t = kd_table(**{PREM: {"A": c("$12,500", passage_ids=[1]),
                           "B": c("$9,000", None, edited=True, original_value="$14,490.00")}})
    marks = cr.mark_cells(t)
    assert marks[(PREM, "B")].kind == "best"
    kd = [seg("B (Juniper Peak) has the lowest premium ($9,000, edited by you)", (PREM, "B"))]
    assert cr.check_key_difference(kd, [t], marks) == []


# --------------------------------------------------------------------- T5 template (F9) and staleness (R3)


def test_template_is_checked_and_never_empty_when_marked():
    t = kd_table(**{EXC: {"A": c("Action over", complete=True), "B": c(cr.NONE_OF_THESE, complete=True)},
                    WOS: {"A": c(cr.BLANKET), "B": c(cr.NOT_INCLUDED)}})
    marks = cr.mark_cells(t)
    segs = cr.template_key_difference([t], marks)
    text = "".join(s.text for s in segs)
    assert text == ("A (Harborline) has the lowest premium ($12,500) but additional insured is Scheduled only "
                    "(others: Blanket), excludes action over. B (Juniper Peak): waiver of subrogation is Not "
                    "included (others: Blanket).")
    assert cr.check_key_difference(segs, [t], marks) == []
    assert cr.template_key_difference([t], {}) == []


def test_template_without_a_premium_mark():
    t = kd_table(**{PREM: {"A": c("$12,500"), "B": c("$12,500")}})
    marks = cr.mark_cells(t)
    segs = cr.template_key_difference([t], marks)
    assert "".join(s.text for s in segs) == "A (Harborline): additional insured is Scheduled only (others: Blanket)."
    assert cr.check_key_difference(segs, [t], marks) == []


def content_of(t: TableBlock, kd=None) -> ArtifactContent:
    sections = [Section(id="premium", number="1", title="Premium", blocks=[t])]
    if kd is not None:
        sections.insert(0, Section(id="key_difference", number="", title="Key difference", blocks=[kd]))
    return ArtifactContent(sections=sections)


def test_stale_when_marks_move_or_a_cited_cell_changes():
    t = kd_table()
    marks = cr.mark_cells(t)
    kd = cr.build_key_difference(GOOD, [t], marks, source="model")
    assert {(m.row, m.column, m.kind) for m in kd.marks} == {(PREM, "A", "best"), (AI, "A", "worse")}
    content = content_of(t, kd)
    assert not cr.key_difference_stale(kd, content)
    # an uncited edit that moves no mark: not stale
    moved_nothing = content.edit_cell("t", PREM, "B", "$15,000.00")
    assert not cr.key_difference_stale(kd, moved_nothing)
    # an edit to a premium the sentence does not cite... here B is cited only via AI; move the best mark to B
    moved = content.edit_cell("t", PREM, "B", "$9,000")
    assert cr.key_difference_stale(kd, moved)
    # a cited cell edited to the same category it showed: still stale (the broker changed what it cites)
    cited = content.edit_cell("t", AI, "B", "Blanket")
    assert cr.key_difference_stale(kd, cited)
    assert kd.cell_refs() == [CellRef(row=PREM, column="A"), CellRef(row=AI, column="A"), CellRef(row=AI, column="B")]
    assert content.key_difference_passage_ids(kd) == {1, 3, 4}
    assert content.passage_ids() >= {1, 3, 4}
