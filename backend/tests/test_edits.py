"""Unit tests: D9 staleness and edit merging, compose with user edits, chat validation."""

from __future__ import annotations

from backend.chat import ChatContext, validate_answer
from backend.edits import apply_cell_edit, compose_edits, edit_keys, mark_stale, merge_edits
from backend.pipeline import compose
from backend.schemas import (
    ArtifactContent,
    Cell,
    Column,
    EvidenceState,
    FactField,
    ListBlock,
    ParagraphBlock,
    Row,
    Section,
    SectionPlanItem,
    Segment,
    TableBlock,
)


def content(summary_pids=(1,), list_pids=(), table_pid=1, other_pid=2) -> ArtifactContent:
    table = TableBlock(id="t", columns=[Column(key="A", label="Harborline")], rows=[
        Row(key="premium", label="Premium", cells={"A": Cell(value="$100", passage_ids=[table_pid],
                                                             state=EvidenceState.VERIFIED)}),
        Row(key="limit", label="Limit", cells={"A": Cell(value="$1,000,000", passage_ids=[other_pid],
                                                         state=EvidenceState.VERIFIED)}),
        Row(key="territory", label="Territory", cells={"A": Cell(value="Not specified",
                                                                 state=EvidenceState.ABSENT)}),
    ])
    return ArtifactContent(sections=[
        Section(id="summary", number="1", title="Summary", blocks=[
            ParagraphBlock(id="p", segments=[Segment(text="A costs $100.", passage_ids=list(summary_pids))])]),
        Section(id="facts", number="2", title="Facts", blocks=[
            ListBlock(id="l", items=[[Segment(text="limit", passage_ids=list(list_pids))]]), table]),
    ])


def test_edit_marks_only_sections_whose_prose_cites_the_original_passage():
    new, stale = apply_cell_edit(content(), "t", "premium", "A", "$200")
    assert stale == ["summary"] and new.sections[0].stale and not new.sections[1].stale
    _, stale = apply_cell_edit(content(), "t", "limit", "A", "$2,000,000")
    assert stale == []  # passage 2 is cited only by the table itself
    _, stale = apply_cell_edit(content(list_pids=(2,)), "t", "limit", "A", "$2,000,000")
    assert stale == ["facts"]  # list items are prose too
    _, stale = apply_cell_edit(content(), "t", "territory", "A", "Worldwide")
    assert stale == []  # a cell with no chip cannot make prose stale


def test_mark_stale_with_nothing_is_a_no_op():
    c = content()
    assert mark_stale(c, set()) == [] and not any(s.stale for s in c.sections)


def test_merge_edits_reapplies_edits_and_flags_only_unknown_ones():
    edited, _ = apply_cell_edit(content(), "t", "premium", "A", "$200")
    fresh = content()  # a recomposed report: new prose, unedited tables
    known = merge_edits(edited, fresh, known=edit_keys(edited))
    cell = known.find_cell("t", "premium", "A")
    assert cell.edited and cell.value == "$200" and cell.original_value == "$100" and cell.original_passage_ids == [1]
    assert cell.passage_ids == [] and not any(s.stale for s in known.sections)
    unknown = merge_edits(edited, fresh, known=None)
    assert unknown.sections[0].stale
    assert fresh.find_cell("t", "premium", "A").edited is False  # inputs are not mutated
    gone = ArtifactContent(sections=[Section(id="summary", number="1", title="S")])
    assert merge_edits(edited, gone).sections[0].stale is False  # the cell no longer exists: skipped


def test_compose_edits_feed_the_prompt_and_the_number_check():
    fields = [FactField(key="premium", label="Premium", section="s")]
    inp = compose.ComposeInput(
        title="T", fields=fields, sections=[SectionPlanItem(id="s", number="1", title="S")],
        columns=[compose.ComposeColumn(key="A", label="Harborline", document_id=1, filename="a.pdf")],
        facts=[compose.ComposeFact(column_key="A", field="premium", value="$100", state=EvidenceState.VERIFIED,
                                   passage_id=7, page_no=1)])
    draft = {"sections": [{"section_id": "s", "paragraphs": [{"segments": [
        {"text": "A premium is $200, edited by the user.", "passage_ids": []}]}]}]}
    assert compose.validate_draft(draft, inp)  # $200 is not a fact value
    plain = compose.build_compose_messages(inp, boundary="B0")
    assert "user_edits" not in plain[1].content and compose.EDITS_NOTE not in plain[1].content

    edited, _ = apply_cell_edit(content(), "t", "premium", "A", "$200")
    edits = compose_edits(edited)
    assert edits == [compose.ComposeEdit(column_key="A", field="premium", value="$200", original_value="$100")]
    inp2 = compose.ComposeInput(title=inp.title, fields=inp.fields, sections=inp.sections, columns=inp.columns,
                                facts=inp.facts, edits=edits)
    assert compose.validate_draft(draft, inp2) == []
    msg = compose.build_compose_messages(inp2, boundary="B0")[1].content
    assert '"user_edits"' in msg and '"original_value": "$100"' in msg and compose.EDITS_NOTE in msg
    # the system prompt is unchanged by edits
    assert compose.build_compose_messages(inp2, boundary="B0")[0].content == plain[0].content


def _cctx() -> ChatContext:
    return ChatContext(run_id=1, artifact_id=1, version=3, payload={},
                       passages={7: (1, 1), 8: (1, 2)},
                       page_texts={(1, 1): "Total Premium: $100", (1, 2): "Inspection fee: $250"},
                       report_values=["$100", "$200"], cells={("t", "premium", "A")},
                       current_values={("t", "premium", "A"): "$200"})


def test_chat_validation_numbers_come_from_report_question_or_cited_page():
    ok = {"answer": [{"text": "The fee is $250.", "passage_ids": [8]}], "proposed_edits": []}
    assert validate_answer(ok, _cctx(), "fee?")[0] == []
    uncited = {"answer": [{"text": "The fee is $250.", "passage_ids": [7]}], "proposed_edits": []}
    assert validate_answer(uncited, _cctx(), "fee?")[0]  # $250 is not on page 1
    asked = {"answer": [{"text": "No quote shows $9,999.", "passage_ids": []}], "proposed_edits": []}
    assert validate_answer(asked, _cctx(), "Is anything $9,999?")[0] == []
    bad_pid = {"answer": [{"text": "See it.", "passage_ids": [99]}], "proposed_edits": []}
    errors, segs, _ = validate_answer(bad_pid, _cctx(), "q")
    assert errors and segs[0]["passage_ids"] == []
    empty = {"answer": [{"text": "  ", "passage_ids": []}], "proposed_edits": []}
    assert "the answer is empty" in validate_answer(empty, _cctx(), "q")[0]


def test_chat_validation_keeps_segment_text_unchanged():
    # The model writes the spaces between segments; stripping them glued sentences together.
    data = {"answer": [{"text": "The fee is $250", "passage_ids": [8]}, {"text": " per inspection. ", "passage_ids": []},
                       {"text": " ", "passage_ids": []}, {"text": "See the quote.", "passage_ids": [8]}],
            "proposed_edits": []}
    errors, segs, _ = validate_answer(data, _cctx(), "fee?")
    assert errors == []
    assert [s["text"] for s in segs] == ["The fee is $250", " per inspection. ", "See the quote."]
    assert "".join(s["text"] for s in segs) == "The fee is $250 per inspection. See the quote."


def test_chat_validation_keeps_only_proposed_edits_for_real_cells():
    data = {"answer": [{"text": "Done as a proposal.", "passage_ids": []}], "proposed_edits": [
        {"block_id": "t", "row_key": "premium", "column_key": "A", "value": " $300 ", "reason": "asked"},
        {"block_id": "t", "row_key": "nope", "column_key": "A", "value": "1", "reason": "x"}]}
    errors, _, edits = validate_answer(data, _cctx(), "set premium to $300")
    assert errors == []
    assert edits == [{"id": 0, "block_id": "t", "row_key": "premium", "column_key": "A", "value": "$300",
                      "reason": "asked", "current_value": "$200", "proposed_on_version": 3, "status": "proposed"}]
