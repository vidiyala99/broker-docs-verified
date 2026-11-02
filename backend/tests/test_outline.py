"""Slice 2 T2: proposal outline validation (kinds, caps, locked blocks, unique ids, D4).

The outline is untrusted input; every rejection carries a field path relative
to `parameters` so the API can return it under `fields`.
"""

from __future__ import annotations

import pytest

from backend.schemas import (
    MAX_INSTRUCTION_CHARS,
    MAX_OUTLINE_BLOCKS,
    MAX_TITLE_CHARS,
    BlockKind,
    OutlineBlock,
    ParameterErrors,
    outline_errors,
    parse_proposal_parameters,
)

KNOWN = {"common_declarations", "rate", "premium", "coverage", "endorsements", "subjectivities", "cyber", "eo"}


def blk(id_, kind, title=None, fact_sections=(), **kw):
    return {"id": id_, "kind": kind, "title": title or id_.replace("_", " ").title(),
            "fact_sections": list(fact_sections), **kw}


def base_outline():
    return [
        blk("cover", "cover_page", "Cover Page"),
        blk("toc", "table_of_contents", "Table of Contents"),
        blk("executive_summary", "narrative", fact_sections=["common_declarations", "premium"]),
        blk("client_details", "client_details", fact_sections=["common_declarations"]),
        blk("premium", "premium_comparison", fact_sections=["rate", "premium"]),
    ]


def params(outline=None, **kw):
    return {"template_id": 1, "outline": base_outline() if outline is None else outline, **kw}


def errors_of(raw, known=KNOWN):
    with pytest.raises(ParameterErrors) as exc:
        parse_proposal_parameters(raw, known)
    return exc.value.errors


def locs(errs):
    return [loc for loc, _ in errs]


def test_valid_outline_parses_and_locked_is_derived_from_kind():
    outline = base_outline()
    outline[2]["locked"] = True  # a client cannot lock a section
    outline[0]["locked"] = False  # nor unlock the cover
    p = parse_proposal_parameters(params(outline, broker_name="  Dana Ruiz "), KNOWN)
    assert [b.locked for b in p.outline] == [True, True, False, False, False]
    assert p.broker_name == "Dana Ruiz"
    assert p.outline[4].has_table and not p.outline[2].has_table


def test_outline_may_be_omitted_to_use_the_template_blocks():
    p = parse_proposal_parameters({"template_id": "standard"}, KNOWN)
    assert p.outline is None and p.template_id == "standard" and p.no_ai_prose is False


def test_unknown_kind_has_its_field_path():
    outline = base_outline()
    outline[2]["kind"] = "poem"
    assert ("outline", 2, "kind") in locs(errors_of(params(outline)))


def test_too_many_blocks():
    outline = base_outline() + [blk(f"note_{i}", "narrative") for i in range(MAX_OUTLINE_BLOCKS)]
    assert ("outline",) in locs(errors_of(params(outline)))


@pytest.mark.parametrize("field,value", [
    ("title", "x" * (MAX_TITLE_CHARS + 1)),
    ("title", "   "),
    ("title", "Two\nlines"),
    ("ai_instruction", "y" * (MAX_INSTRUCTION_CHARS + 1)),
    ("ai_instruction", "bell\x07"),
])
def test_length_and_text_caps(field, value):
    outline = base_outline()
    outline[3][field] = value
    assert ("outline", 3, field) in locs(errors_of(params(outline)))


def test_instruction_may_have_line_breaks():
    outline = base_outline()
    outline[2]["ai_instruction"] = "Lead with the premium.\nKeep it short."
    assert parse_proposal_parameters(params(outline), KNOWN).outline[2].ai_instruction.count("\n") == 1


@pytest.mark.parametrize("bad_id", ["Has-Caps", "with-hyphen", "1starts_digit", "", "x" * 41])
def test_block_id_pattern(bad_id):
    outline = base_outline()
    outline[3]["id"] = bad_id
    assert ("outline", 3, "id") in locs(errors_of(params(outline)))


def test_missing_locked_block():
    outline = [b for b in base_outline() if b["kind"] != "table_of_contents"]
    errs = errors_of(params(outline))
    assert ("outline",) in locs(errs) and any("Table of Contents" in m for _, m in errs)


def test_locked_block_moved_or_repeated():
    outline = base_outline()
    outline[0], outline[2] = outline[2], outline[0]
    assert ("outline", 2, "kind") in locs(errors_of(params(outline)))
    outline = base_outline() + [blk("cover2", "cover_page")]
    assert ("outline", 5, "kind") in locs(errors_of(params(outline)))


def test_locked_block_ids_are_fixed_and_reserved():
    outline = base_outline()
    outline[0]["id"] = "front"
    assert ("outline", 0, "id") in locs(errors_of(params(outline)))
    outline = base_outline()
    outline[3]["id"] = "toc"
    errs = errors_of(params(outline))
    assert ("outline", 3, "id") in locs(errs)


def test_duplicate_ids():
    outline = base_outline()
    outline[4]["id"] = "client_details"
    errs = errors_of(params(outline))
    assert ("outline", 4, "id") in locs(errs) and any("already used" in m for _, m in errs)


def test_one_table_per_fact_section_d4():
    outline = base_outline() + [blk("premium_again", "premium_comparison", fact_sections=["premium"])]
    errs = errors_of(params(outline))
    assert ("outline", 5, "fact_sections", 0) in locs(errs)
    assert any("only one table" in m for _, m in errs)


def test_narrative_blocks_may_repeat_fact_sections():
    outline = base_outline() + [blk("closing", "narrative", fact_sections=["premium", "rate"])]
    parse_proposal_parameters(params(outline), KNOWN)


def test_fact_section_rules():
    outline = base_outline()
    outline[3]["fact_sections"] = ["common_declarations", "common_declarations"]
    assert ("outline", 3, "fact_sections", 1) in locs(errors_of(params(outline)))
    outline = base_outline()
    outline[3]["fact_sections"] = ["mystery"]
    assert ("outline", 3, "fact_sections", 0) in locs(errors_of(params(outline)))
    outline = base_outline()
    outline[3]["fact_sections"] = []
    assert ("outline", 3, "fact_sections") in locs(errors_of(params(outline)))
    outline = base_outline()
    outline[0]["fact_sections"] = ["premium"]
    assert ("outline", 0, "fact_sections") in locs(errors_of(params(outline)))


def test_needs_a_section_besides_the_locked_blocks():
    errs = errors_of(params(base_outline()[:2]))
    assert ("outline",) in locs(errs)


@pytest.mark.parametrize("raw,loc", [
    ({"template_id": 1, "surprise": 1}, ("surprise",)),
    ({"template_id": 1, "no_ai_prose": "yes"}, ("no_ai_prose",)),
    ({"template_id": 1, "additional_context": "z" * 4001}, ("additional_context",)),
    ({"template_id": 1, "broker_name": "a\nb"}, ("broker_name",)),
    ({"template_id": 1, "template": {"id": 9}}, ("template",)),  # a client cannot send the snapshot
    ({}, ("template_id",)),
])
def test_parameter_fields(raw, loc):
    assert any(l[:len(loc)] == loc for l in locs(errors_of(raw)))


def test_extra_block_fields_are_rejected():
    outline = base_outline()
    outline[2]["passage_ids"] = [1]
    assert ("outline", 2, "passage_ids") in locs(errors_of(params(outline)))


def test_outline_errors_without_catalogue_skips_known_check():
    blocks = [OutlineBlock.model_validate(b) for b in base_outline()]
    blocks.append(OutlineBlock(id="x", kind=BlockKind.LINE_COMPARISON, title="X", fact_sections=["anything"]))
    assert outline_errors(blocks) == []
    assert outline_errors(blocks, KNOWN) and outline_errors(blocks, KNOWN)[0][0] == (5, "fact_sections", 0)
