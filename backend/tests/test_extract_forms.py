"""Slice 3 T3 (D13 A): one combined extraction pass returns the scalar fields plus a forms array.

Each form becomes a fact keyed form:<normalized number>; merge_document is given the dynamic
keys; no second read of the document; special provisions are not extracted as items.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from backend.db import PROPOSAL_BUILDER
from backend.pipeline import extract, verify, windows
from backend.providers import FakeProvider, RunBudget
from backend.providers.fake import FakeReply
from backend.providers.schema import fact_extraction_schema
from backend.schemas import (
    FORM_KEY_PREFIX,
    POLICY_CHECK_FIELDS,
    EvidenceState,
    FactField,
    form_key,
    is_form_key,
    scalar_fields,
)

LONG_300 = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "quotes" / "long_300.pdf"


@dataclass
class _Page:
    page_no: int
    text: str
    ocr_text: str | None = None
    words: list | None = None


FORMS = FactField(key="forms", label="Forms and Endorsements", kind="forms",
                  description="Every form or endorsement listed or printed.")
FIELDS = [
    FactField(key="policy_number", label="Policy Number", description="Policy number."),
    FactField(key="coverage_a_limit", label="Coverage A - Dwelling", description="Dwelling limit."),
    FORMS,
]
PAGES = [
    _Page(1, "Policy Number: HO-1234\nCoverage A - Dwelling: $300,000"),
    _Page(2, "Forms and Endorsements\nHO 00 03 05 11 Homeowners 3 Special Form\nHO 04 90 05 11 Personal Property Replacement Cost"),
]


def run(coro):
    return asyncio.run(coro)


def reply(facts, forms=None, options=1, option=None, with_forms=True):
    out = {"facts": facts, "options_detected": options, "option_compared": option}
    if with_forms:
        out["forms"] = forms or []
    return out


def form(number, edition, page, quote, title=None):
    return {"number": number, "edition": edition, "title": title, "page": page, "quote": quote}


# --------------------------------------------------------------------- keys


@pytest.mark.parametrize("raw", ["HO 00 03", "ho-00-03", "HO0003", " HO  00 03 ", "HO.00.03"])
def test_form_key_ignores_case_spacing_and_separators(raw):
    assert form_key(raw) == "form:HO0003"
    assert is_form_key(form_key(raw)) and FORM_KEY_PREFIX == "form:"


@pytest.mark.parametrize("raw", [None, "", "   ", "--", "X" * 41])
def test_form_key_rejects_empty_and_oversized_numbers(raw):
    assert form_key(raw) is None


def test_is_form_key_only_for_the_prefix():
    assert not is_form_key("coverage_a_limit") and not is_form_key("forms") and not is_form_key("form:")


# --------------------------------------------------------------------- the field catalogue


def test_policy_check_catalogue_has_unique_snake_case_keys_and_one_forms_field():
    keys = [f.key for f in POLICY_CHECK_FIELDS]
    assert len(keys) == len(set(keys))
    forms = [f for f in POLICY_CHECK_FIELDS if f.kind == "forms"]
    assert len(forms) == 1
    for f in scalar_fields(POLICY_CHECK_FIELDS):
        assert re.fullmatch(r"[a-z][a-z0-9_]*", f.key), f.key
        assert f.kind == "scalar" and f.label and f.description
    # spec section 3 categories, D13: no special provisions
    for key in ("policy_number", "policy_term", "named_insured", "insured_address", "coverage_a_limit",
                "coverage_b_limit", "coverage_c_limit", "coverage_d_limit", "deductible_all_peril",
                "deductible_wind_hail", "total_premium"):
        assert key in keys, key
    assert any(k.startswith("discount_") for k in keys)
    assert not any("provision" in k for k in keys)


def test_fact_field_kind_defaults_to_scalar_so_existing_workflows_are_unchanged():
    # Proposal Builder: the Compare Quotes fields without slice 6's exclusions field
    fields = [FactField.model_validate(f) for f in PROPOSAL_BUILDER["fact_schema"]]
    assert all(f.kind == "scalar" for f in fields)
    assert scalar_fields(fields) == fields
    with pytest.raises(ValueError):
        FactField(key="x", label="X", kind="provisions")


# --------------------------------------------------------------------- schema and prompt


def test_schema_without_a_forms_field_is_exactly_the_old_one():
    # Proposal Builder: the Compare Quotes fields without slice 6's exclusions field
    fields = [FactField.model_validate(f) for f in PROPOSAL_BUILDER["fact_schema"]]
    assert extract.build_extraction_schema(fields) == fact_extraction_schema([f.key for f in fields])


def test_combined_schema_adds_a_strict_forms_array_and_keeps_the_forms_field_out_of_the_enum():
    schema = extract.build_extraction_schema(FIELDS)
    js = schema.json_schema
    assert js["properties"]["facts"]["items"]["properties"]["field"]["enum"] == ["policy_number", "coverage_a_limit"]
    item = js["properties"]["forms"]["items"]
    assert set(item["properties"]) == {"number", "edition", "title", "page", "quote"}
    assert set(item["required"]) == set(item["properties"]) and item["additionalProperties"] is False
    assert set(js["required"]) == {"facts", "forms", "options_detected", "option_compared"}
    assert schema.name != "fact_extraction"
    good = reply([], [form("HO 00 03", "05 11", 2, "HO 00 03 05 11 Homeowners 3 Special Form")])
    assert schema.validate(good) == []
    assert schema.validate(reply([], with_forms=False))  # forms is required


def test_forms_note_is_in_the_user_message_outside_the_data_block_and_only_when_asked():
    user = extract.build_extraction_messages(FIELDS, PAGES, document_name="p.pdf", boundary="ZZ12")[1].content
    start = user.index("<<<DOCUMENT ZZ12")
    head, block = user[:start], user[start:]
    low = " ".join(head.lower().split())
    assert "forms" in low and "form number" in low and "edition" in low and "verbatim" in low
    assert "each form at most once" in low
    assert extract.FORMS_NOTE.strip()[:40] not in block
    assert "- forms (" not in head  # the forms field is not a scalar field line
    old = extract.build_extraction_messages(FIELDS[:2], PAGES, document_name="p.pdf", boundary="ZZ12")[1].content
    assert extract.FORMS_NOTE.strip()[:40] not in old
    # a window call carries the note too, after the window note
    part = extract.DocumentPart(index=2, total=3, first=14, last=28, page_count=40)
    win = extract.build_extraction_messages(FIELDS, PAGES, document_name="p.pdf", boundary="ZZ12", part=part)[1].content
    assert win.index("IMPORTANT:") < win.index(extract.FORMS_NOTE.strip()[:40]) < win.index("<<<DOCUMENT ZZ12")


def test_system_prompt_is_unchanged_for_the_combined_pass():
    a = extract.build_extraction_messages(FIELDS, PAGES, document_name="p.pdf", boundary="ZZ12")[0].content
    b = extract.build_extraction_messages(FIELDS[:2], PAGES, document_name="p.pdf", boundary="ZZ12")[0].content
    assert a == b == extract.SYSTEM_PROMPT.format(b="ZZ12")


# --------------------------------------------------------------------- normalization


def test_forms_become_facts_keyed_by_normalized_number_after_the_scalars():
    data = reply(
        [{"field": "policy_number", "value": "HO-1234", "page": 1, "quote": "Policy Number: HO-1234"}],
        [form("HO 04 90", "05 11", 2, "HO 04 90 05 11 Personal Property Replacement Cost"),
         form("HO 00 03", "05 11", 2, "HO 00 03 05 11 Homeowners 3 Special Form")])
    facts, count, label = extract.normalize_output(data, FIELDS)
    assert [f.field for f in facts] == ["policy_number", "coverage_a_limit", "form:HO0003", "form:HO0490"]
    f = facts[2]
    assert (f.value, f.page, f.quote) == ("HO 00 03 05 11", 2, "HO 00 03 05 11 Homeowners 3 Special Form")
    assert facts[1].value is None and (count, label) == (1, None)


def test_form_normalization_edge_cases():
    data = reply([], [
        form("HO 00 03", "05 11", 2, "first"),
        form("ho-00-03", "04 91", 2, "second"),  # same number: first one wins (one per key)
        form("  ", "05 11", 2, "no number"),  # dropped
        form("HO 04 90", None, 2, "HO 04 90 Personal Property"),  # no edition: the number alone
        form("HO 17 32", "  ", True, "x"),  # blank edition, bool page -> None
        form("HO 05 00", "01 01", 2, None),  # no quote: kept, verify will reject it
    ])
    facts, _, _ = extract.normalize_output(data, FIELDS)
    by = {f.field: f for f in facts if is_form_key(f.field)}
    assert set(by) == {"form:HO0003", "form:HO0490", "form:HO1732", "form:HO0500"}
    assert by["form:HO0003"].value == "HO 00 03 05 11" and by["form:HO0003"].quote == "first"
    assert by["form:HO0490"].value == "HO 04 90"
    assert by["form:HO1732"].value == "HO 17 32" and by["form:HO1732"].page is None
    assert by["form:HO0500"].quote is None


def test_forms_are_capped_per_call():
    many = [form(f"HO {i:04d}", "01 01", 1, f"HO {i:04d} 01 01") for i in range(extract.MAX_FORMS_PER_CALL + 25)]
    facts, _, _ = extract.normalize_output(reply([], many), FIELDS)
    assert sum(is_form_key(f.field) for f in facts) == extract.MAX_FORMS_PER_CALL


def test_forms_are_ignored_when_the_workflow_has_no_forms_field():
    data = reply([], [form("HO 00 03", "05 11", 2, "x")])
    facts, _, _ = extract.normalize_output(data, FIELDS[:2])
    assert [f.field for f in facts] == ["policy_number", "coverage_a_limit"]


# --------------------------------------------------------------------- end to end, no network


def test_one_call_returns_scalars_and_forms_and_the_forms_verify_on_their_page():
    fake = FakeProvider([reply(
        [{"field": "coverage_a_limit", "value": "$300,000", "page": 1, "quote": "Coverage A - Dwelling: $300,000"}],
        [form("HO 00 03", "05 11", 2, "HO 00 03 05 11 Homeowners 3 Special Form"),
         form("HO 04 90", "05 11", 1, "HO 04 90 05 11 Personal Property Replacement Cost")])])
    out = run(extract.extract_document(fake, RunBudget(), FIELDS, PAGES, document_name="p.pdf"))
    assert len(fake.requests) == 1  # D13: no second read
    checked = {r.field: r for r in verify.verify_facts(out.facts, PAGES)}
    assert checked["coverage_a_limit"].state == EvidenceState.VERIFIED
    assert checked["form:HO0003"].state == EvidenceState.VERIFIED and checked["form:HO0003"].page_no == 2
    # the model gave page 1, the form is on page 2: re-pointed like any fact
    assert checked["form:HO0490"].page_no == 2 and checked["form:HO0490"].repointed_from == 1


def _forms_responder(req):
    """A careful model for a long document: scalars by label, forms by a 'HO nn nn mm yy' line."""
    text = "\n".join(p.text for m in req.messages if m.role == "user" for p in m.parts())
    block = text[text.index("<<<DOCUMENT"):text.rindex(">>>")]
    page, facts, forms = 0, [], []
    for line in block.splitlines():
        m = re.match(r"\[page (\d+)\]", line.strip())
        if m:
            page = int(m.group(1))
            continue
        mm = re.match(r"(HO \d\d \d\d) (\d\d \d\d)\b", line.strip())
        if mm:
            forms.append(form(mm.group(1), mm.group(2), page, line.strip()))
        if line.startswith("Policy Number: "):
            facts.append({"field": "policy_number", "value": line.split(": ", 1)[1], "page": page,
                          "quote": line.strip()})
    return FakeReply(content=json.dumps(reply(facts, forms)), completion_tokens=500)


def test_long_document_windows_merge_dynamic_form_keys_once_each():
    pages = [_Page(n, f"Policy wording page {n}.") for n in range(1, 41)]
    pages[0] = _Page(1, "Policy Number: HO-1234\nSchedule of forms\nHO 00 03 05 11 Homeowners 3 Special Form")
    pages[13] = _Page(14, "HO 00 03 05 11 Homeowners 3 Special Form continued")  # inside the 2-page overlap
    pages[29] = _Page(30, "HO 04 90 05 11 Personal Property Replacement Cost")
    fake = FakeProvider(_forms_responder)
    out = run(windows.extract_document(fake, RunBudget(), FIELDS, pages, document_name="long.pdf"))
    assert isinstance(out, windows.WindowedExtraction) and len(fake.requests) == len(out.windows) == 3
    merged = windows.verify_document(out, pages)
    keys = [m.field for m in merged]
    assert keys == ["policy_number", "coverage_a_limit", "form:HO0003", "form:HO0490"]
    by = {m.field: m for m in merged}
    assert by["form:HO0003"].state == EvidenceState.VERIFIED and by["form:HO0003"].page_no == 1
    assert {e.page_no for e in by["form:HO0003"].extras} == {14}
    assert by["form:HO0490"].value == "HO 04 90 05 11" and by["coverage_a_limit"].state == EvidenceState.ABSENT


# --------------------------------------------------------------------- budget (D13: inside the 500k ceiling)


def _prose(pages: int, chars: int) -> list[_Page]:
    line = "Each insured shall provide access to books and records that relate to this insurance on request."
    out = []
    for n in range(1, pages + 1):
        rows = [f"Policy No. HO-773595    Page {n}"]
        while sum(len(r) + 1 for r in rows) < chars:
            rows.append(line)
        out.append(_Page(n, "\n".join(rows)))
    return out


def test_combined_schema_plans_a_300_page_policy_inside_the_document_ceiling():
    fake = FakeProvider([])
    pages = _prose(300, 3900)
    need = windows.precheck_tokens(fake, POLICY_CHECK_FIELDS, pages, windows.plan_windows(300),
                                   document_name="policy.pdf")
    assert need < fake.config.document_token_budget


def test_the_long_300_fixture_fits_the_ceiling_with_the_combined_schema():
    if not LONG_300.exists():
        pytest.skip("long_300 fixture not present")
    from backend.pipeline import parse

    pages = parse.parse_pdf(LONG_300.read_bytes()).pages
    fake = FakeProvider([])
    need = windows.precheck_tokens(fake, POLICY_CHECK_FIELDS, pages, windows.plan_windows(len(pages)),
                                   document_name="long_300.pdf")
    assert 400_000 < need < fake.config.document_token_budget
