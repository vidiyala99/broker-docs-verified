"""Slice 6 (Compare Quotes match), lane A2: the new fact fields in the extraction call (T3), marks computed on
read and in compose (T4), and the Key difference (T5), end to end through the API with a FakeProvider."""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from api_support import api_factory, api_responder, user_text  # noqa: F401
from backend import db
from backend.pipeline import compare_rules as cr
from backend.pipeline import compose, extract, merge, verify
from backend.pipeline.compose import ComposeColumn, ComposeEdit, ComposeFact, ComposeInput
from backend.providers import CallRole, FakeProvider, ProviderRequest, RunBudget
from backend.providers.fake import FakeRefusal
from backend.schemas import (
    ArtifactContent,
    EvidenceState,
    FactField,
    KeyDifferenceBlock,
    SectionPlanItem,
)
from conftest import make_pdf
from pipeline_fakes import extraction_reply

COMPARE_FIELDS = [FactField.model_validate(f) for f in db.COMPARE_QUOTES["fact_schema"]]
V = EvidenceState.VERIFIED

DOC_A = ["Carrier: Harborline Specialty\nNamed Insured: Cedar Hollow LLC\nTotal Premium: $12,500\n"
         "Additional insured: Designated Person or Organization\nWaiver of subrogation: Blanket\n"
         "Primary & non-contributory: Included",
         "Schedule of Forms and Endorsements\nExclusion - Action Over\nExclusion - Asbestos"]
DOC_B = ["Carrier: Juniper Peak\nNamed Insured: Cedar Hollow LLC\nTotal Premium: $14,490.00\n"
         "Additional insured: Blanket where required by written contract\nWaiver of subrogation: Blanket\n"
         "Primary & non-contributory: Included",
         "Schedule of Forms and Endorsements\nExclusion - Asbestos"]


def compare_extraction_reply(text: str) -> dict:
    """The smart fake's "Label: value" facts, plus the slice 6 arrays read from the document lines."""
    out = extraction_reply(text)
    block = text[text.index("<<<DOCUMENT"): text.rindex(">>>")]
    page, exclusions, schedule = 0, [], {"value": None, "page": None, "quote": None}
    for line in block.splitlines():
        m = re.match(r"\[page (\d+)\]", line.strip())
        if m:
            page = int(m.group(1))
            continue
        if line.strip().startswith("Exclusion - "):
            exclusions.append({"title": line.strip(), "page": page, "quote": line.strip()})
        if line.strip() == "Schedule of Forms and Endorsements" and schedule["value"] is None:
            schedule = {"value": line.strip(), "page": page, "quote": line.strip()}
    return {**out, "exclusions": exclusions, "exclusions_schedule": schedule, "document_kind": "quote"}


def responder(compose_fn=None):
    base = api_responder(compose=compose_fn)

    def respond(req: ProviderRequest):
        if req.role is CallRole.EXTRACT:
            return compare_extraction_reply(user_text(req))
        return base(req)

    return respond


def start(api, *, parameters=None, docs=(DOC_A, DOC_B)) -> tuple[int, dict]:
    ids = [api.upload_pages(pages, name=f"{chr(97 + i)}.pdf") for i, pages in enumerate(docs)]
    body = {"document_ids": ids}
    if parameters is not None:
        body["parameters"] = parameters
    r = api.client.post("/api/runs", json=body)
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    return run_id, api.artifact(run_id)


def cells(art: dict) -> dict[tuple[str, str], dict]:
    out = {}
    for sec in art["content"]["sections"]:
        for b in sec["blocks"]:
            if b["type"] == "table":
                for row in b["rows"]:
                    for col, cell in row["cells"].items():
                        out[(row["key"], col)] = {**cell, "block_id": b["id"]}
    return out


def marks(art: dict) -> dict[tuple[str, str], str]:
    return {k: c["mark"] for k, c in cells(art).items() if c.get("mark")}


def key_difference(art: dict) -> dict | None:
    sec = art["content"]["sections"][0]
    return sec["blocks"][0] if sec["id"] == "key_difference" else None


def kd_text(art: dict) -> str:
    return "".join(s["text"] for s in key_difference(art)["segments"])


def edit(api, art, row, col, value):
    c = cells(art)[(row, col)]
    r = api.client.patch(f"/api/artifacts/{art['id']}/cells",
                         json={"block_id": c["block_id"], "row_key": row, "column_key": col, "value": value})
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------- T3 extraction call


def test_extraction_schema_and_prompt_ask_for_the_new_fields_only_for_compare():
    schema = extract.build_extraction_schema(COMPARE_FIELDS)
    props = schema.json_schema["properties"]
    assert props["exclusions"]["items"]["required"] == ["title", "page", "quote"]
    assert props["exclusions_schedule"]["required"] == ["value", "page", "quote"]
    assert props["document_kind"]["enum"] == ["quote", "proposal", "summary", "other"]
    enum = props["facts"]["items"]["properties"]["field"]["enum"]
    assert {"additional_insured", "waiver_of_subrogation", "primary_noncontributory"} <= set(enum)
    assert "notable_exclusions" not in enum  # built in code from the titles (R1)
    msgs = extract.build_extraction_messages(COMPARE_FIELDS, [], document_name="q.pdf")
    user = msgs[1].parts()[0].text
    assert extract.COMPARE_TERMS_NOTE in user and "watch" not in user.lower()  # the watch list lives in code
    proposal = [FactField.model_validate(f) for f in db.PROPOSAL_BUILDER["fact_schema"]]
    assert "additional_insured" not in {f.key for f in proposal}  # Compare only
    assert extract.COMPARE_TERMS_NOTE not in extract.build_extraction_messages(
        proposal, [], document_name="q.pdf")[1].parts()[0].text
    assert "exclusions" not in extract.build_extraction_schema(proposal).json_schema["properties"]


def test_a_reply_without_the_new_keys_still_validates():
    """Scripted replies and recordings made before slice 6 read as "none printed"; a present key is checked."""
    schema = extract.build_extraction_schema(COMPARE_FIELDS)
    assert schema.validate({"facts": [], "options_detected": 1, "option_compared": None}) == []
    assert schema.validate({"facts": [], "options_detected": 1, "option_compared": None,
                            "document_kind": "brochure"})


def test_normalize_reads_titles_schedule_and_document_kind():
    data = {"facts": [], "options_detected": 1, "option_compared": None, "document_kind": "proposal",
            "exclusions_schedule": {"value": "Schedule of Forms", "page": 2, "quote": "Schedule of Forms"},
            "exclusions": [{"title": "Exclusion - Action  Over", "page": 2, "quote": "Exclusion - Action Over"},
                           {"title": "exclusion - action over", "page": 3, "quote": "dup"},
                           {"title": "  ", "page": 2, "quote": None}]}
    facts, _, _ = extract.normalize_output(data, COMPARE_FIELDS)
    extra = [f for f in facts if f.field.startswith("exclusion")]
    assert [(f.field, f.value, f.page) for f in extra] == [
        ("exclusions_schedule", "Schedule of Forms", 2), ("exclusion:EXCLUSIONACTIONOVER", "Exclusion - Action Over", 2)]
    assert extract.document_kind(data) == "proposal" and extract.document_kind({"document_kind": "x"}) is None
    flat = {"facts": [{"field": "exclusion:ANY", "value": "Wrap-Up Exclusion", "page": 1, "quote": "Wrap-Up Exclusion"},
                      {"field": "exclusions_schedule", "value": "Exclusions", "page": 1, "quote": "Exclusions"}]}
    got = {f.field: f.value for f in extract.exclusion_facts(flat)}
    assert got == {"exclusions_schedule": "Exclusions", "exclusion:WRAPUPEXCLUSION": "Wrap-Up Exclusion"}


def test_merge_keeps_one_schedule_heading_and_every_title():
    def vr(field, value, page, state=V):
        return verify.VerifyResult(field=field, value=value, state=state, cited_page=page, page_no=page, quote=value,
                                   matched_text=value, method="exact", score=1.0)
    out = merge.merge_document(COMPARE_FIELDS, [
        vr("exclusions_schedule", "Exclusions", 4), vr("exclusions_schedule", "Schedule of Forms", 2),
        vr("exclusion:ACTIONOVER", "Action Over", 4), vr("exclusion:ASBESTOS", "Asbestos", 5)])
    by = {m.field: m for m in out}
    assert by["exclusions_schedule"].state == V and by["exclusions_schedule"].value == "Schedule of Forms"
    assert by["exclusion:ACTIONOVER"].state == V and by["exclusion:ASBESTOS"].state == V
    assert "notable_exclusions" not in by


# --------------------------------------------------------------------- T3/T4 through the API


def test_compare_report_shows_categories_marks_and_a_template_key_difference(api_factory):
    api = api_factory(responder())
    run_id, art = start(api)
    run = api.client.get(f"/api/runs/{run_id}").json()
    assert [d["document_kind"] for d in run["documents"]] == ["quote", "quote"]
    table = next(b for s in art["content"]["sections"] for b in s["blocks"] if b["type"] == "table")
    assert [c["document_kind"] for c in table["columns"]] == ["quote", "quote"]
    got = cells(art)
    assert got[("additional_insured", "A")]["value"] == cr.SCHEDULED
    assert got[("additional_insured", "B")]["value"] == cr.BLANKET
    assert got[("waiver_of_subrogation", "A")]["value"] == cr.BLANKET
    assert got[("primary_noncontributory", "B")]["value"] == cr.INCLUDED
    assert got[("notable_exclusions", "A")]["value"] == "Action over" and got[("notable_exclusions", "A")]["complete"]
    assert got[("notable_exclusions", "B")]["value"] == cr.NONE_OF_THESE
    assert got[("notable_exclusions", "A")]["passage_ids"]  # chips: the title and the schedule heading
    # endorsement rows open the Endorsements section, above the free-text rows
    endorsements = next(b for s in art["content"]["sections"] for b in s["blocks"] if b["id"] == "endorsements-table")
    assert [r["key"] for r in endorsements["rows"]][:5] == ["additional_insured", "waiver_of_subrogation",
                                                            "primary_noncontributory", "notable_exclusions",
                                                            "key_endorsements"]
    assert marks(art) == {("total_premium", "A"): "best", ("additional_insured", "A"): "worse",
                          ("notable_exclusions", "A"): "worse"}
    assert got[("additional_insured", "A")]["mark_reason"] == "Others: Blanket"
    assert got[("notable_exclusions", "A")]["mark_reason"] == "Excludes action over"
    assert got[("total_premium", "A")]["mark_reason"] == "Lowest premium"
    assert all("mark" in c for c in got.values())  # every Compare cell carries the keys (null when unmarked)
    kd = key_difference(art)
    assert kd["source"] == "template" and kd["stale"] is False  # the fake compose reply writes no key_difference
    assert kd_text(art) == ("A (Harborline Specialty) has the lowest premium ($12,500) but additional insured is "
                            "Scheduled only (others: Blanket), excludes action over.")
    assert art["content"]["sections"][0]["number"] == "" and art["stale_sections"] == []
    # chips: the cited cells' passages are counted (R3 traversal)
    content = ArtifactContent.model_validate(art["content"])
    block = content.sections[0].blocks[0]
    assert isinstance(block, KeyDifferenceBlock)
    kd_pids = content.key_difference_passage_ids(block)
    assert kd_pids and kd_pids <= {int(k) for k in art["chips"]}
    from backend.edits import prose_passage_ids
    assert prose_passage_ids(content.sections[0], content) == kd_pids
    assert prose_passage_ids(content.sections[0]) == set()  # without the content the cells cannot be read


def test_marks_follow_edits_undo_and_rewrite(api_factory):
    api = api_factory(responder())
    run_id, art = start(api)
    # an edit that moves no mark: the Key difference is not stale
    out = edit(api, art, "retention", "B", "$5,000")
    assert key_difference(out["artifact"])["stale"] is False and "key_difference" not in out["stale_sections"]
    # an uncited premium edit that moves the best mark to B: marks recompute, the Key difference is stale (R3)
    out = edit(api, out["artifact"], "total_premium", "B", "$9,000")
    a2 = out["artifact"]
    assert marks(a2)[("total_premium", "B")] == "best" and ("total_premium", "A") not in marks(a2)
    assert key_difference(a2)["stale"] is True
    assert "key_difference" in a2["stale_sections"] and "key_difference" in out["stale_sections"]
    # undo: back to the stored version's cells, marks and a fresh (not stale) Key difference
    r = api.client.post(f"/api/artifacts/{art['id']}/undo", json={"version": out["previous_version"]})
    a3 = r.json()["artifact"]
    assert marks(a3)[("total_premium", "A")] == "best" and key_difference(a3)["stale"] is False
    # redo the edit, then Rewrite: compose marks the edited cells (O3), the new sentence is current
    a4 = edit(api, a3, "total_premium", "B", "$9,000")["artifact"]
    r = api.client.post(f"/api/artifacts/{art['id']}/regenerate", json={})
    assert r.status_code == 200, r.text
    a5 = r.json()["artifact"]
    assert marks(a5) == marks(a4) and marks(a5)[("total_premium", "B")] == "best"
    assert key_difference(a5)["stale"] is False and "key_difference" not in a5["stale_sections"]
    assert kd_text(a5).startswith("B (Juniper Peak) has the lowest premium ($9,000)")
    assert cells(a5)[("total_premium", "B")]["edited"] is True
    assert {(m["row"], m["column"], m["kind"]) for m in key_difference(a5)["marks"]} == {
        (r_, c_, k) for (r_, c_), k in marks(a5).items()}


def test_a_cited_cell_edit_stales_the_key_difference(api_factory):
    api = api_factory(responder())
    _, art = start(api)
    out = edit(api, art, "additional_insured", "A", "Blanket")  # moves the AI mark away, and is cited
    assert ("additional_insured", "A") not in marks(out["artifact"])
    assert key_difference(out["artifact"])["stale"] is True


def test_retry_recompose_without_edits_leaves_the_key_difference_stale_after_merge():
    """merge_edits_after_retry re-applies edits the sentence was not written against: stale on read."""
    from backend.edits import merge_edits_after_retry
    inp = compose_input()
    fresh = compose.template_report(inp)
    edited = fresh.edit_cell("premium-table", "total_premium", "B", "$9,000")
    merged = merge_edits_after_retry(edited, fresh)
    kd = merged.sections[0].blocks[0]
    assert isinstance(kd, KeyDifferenceBlock) and cr.key_difference_stale(kd, merged)


# --------------------------------------------------------------------- T5 compose: the model's Key difference


def kd_compose(segments):
    def fn(req: ProviderRequest):
        text = user_text(req)
        payload = json.loads(text[text.index("<<<FACTS"):].split("\n", 1)[1].rsplit("FACTS", 1)[0])
        f = payload["facts"][0]
        return {"sections": [{"section_id": "executive_summary", "paragraphs": [{"segments": [
            {"text": f"{f['document']} states {f['label']} {f['value']}.", "passage_ids": [f["passage_id"]]}]}]}],
            "key_difference": {"segments": segments}}
    return fn


GOOD_KD = [{"text": "A (Harborline Specialty) has the lowest premium ($12,500)",
            "cells": [{"row": "total_premium", "column": "A"}]},
           {"text": " but only scheduled additional insured", "cells": [{"row": "additional_insured", "column": "A"}]},
           {"text": ".", "cells": []}]


def test_a_checked_model_key_difference_is_kept_with_cell_citations(api_factory):
    seen = []

    def fn(req):
        seen.append(user_text(req))
        return kd_compose(GOOD_KD)(req)

    api = api_factory(responder(fn))
    _, art = start(api)
    kd = key_difference(art)
    assert kd["source"] == "model" and kd_text(art) == "A (Harborline Specialty) has the lowest premium ($12,500) " \
                                                       "but only scheduled additional insured."
    assert kd["segments"][0]["cells"] == [{"row": "total_premium", "column": "A"}]
    assert {(v["row"], v["column"]) for v in kd["cited_values"]} == {("total_premium", "A"), ("additional_insured", "A")}
    prompt = seen[0]
    assert compose.KEY_DIFFERENCE_NOTE in prompt and '"key_difference_cells"' in prompt
    payload = json.loads(prompt[prompt.index("<<<FACTS"):].split("\n", 1)[1].rsplit("FACTS", 1)[0])
    refs = {(c["row"], c["column"], c["mark"]) for c in payload["key_difference_cells"]}
    assert refs == {("total_premium", "A", "best"), ("additional_insured", "A", "worse"),
                    ("notable_exclusions", "A", "worse"), ("total_premium", "B", None)}  # marks + every premium


def test_a_swapped_premium_falls_back_to_the_template(api_factory):
    swapped = [{"text": "A (Harborline Specialty) has the lowest premium ($14,490.00)",
                "cells": [{"row": "total_premium", "column": "A"}]}, *GOOD_KD[1:]]
    api = api_factory(responder(kd_compose(swapped)))
    _, art = start(api)
    assert key_difference(art)["source"] == "template"
    assert kd_text(art).startswith("A (Harborline Specialty) has the lowest premium ($12,500)")
    assert not any(n["kind"] == "compose_fallback" for n in art["content"]["notices"])  # the prose itself passed


def test_no_marks_means_no_key_difference_and_no_key_difference_field(api_factory):
    same_b = [DOC_A[0].replace("Harborline Specialty", "Juniper Peak").replace("$12,500", "$12,500.00"), DOC_A[1]]
    schemas = []
    base = api_responder()

    def fn(req):
        schemas.append(req.schema.name if req.schema else None)
        return base(req)

    api = api_factory(lambda req: compare_extraction_reply(user_text(req)) if req.role is CallRole.EXTRACT
                      else fn(req))
    _, art = start(api, docs=(DOC_A, same_b))
    assert marks(art) == {} and key_difference(art) is None
    assert schemas == ["report_prose"]


def compose_input(edits=(), context="") -> ComposeInput:
    cols = [ComposeColumn(key="A", label="Harborline", document_id=1, filename="a.pdf"),
            ComposeColumn(key="B", label="Juniper Peak", document_id=2, filename="b.pdf")]
    facts = [ComposeFact("A", "total_premium", "$12,500", V, passage_id=1, page_no=1, fact_id=1),
             ComposeFact("B", "total_premium", "$14,490.00", V, passage_id=2, page_no=1, fact_id=2),
             ComposeFact("A", "carrier_name", "Harborline", V, passage_id=1, page_no=1, fact_id=3)]
    return ComposeInput(title="Compare Quotes", fields=COMPARE_FIELDS,
                        sections=[SectionPlanItem.model_validate(s) for s in db.COMPARE_QUOTES["section_plan"]],
                        columns=cols, facts=facts, edits=list(edits), compare=True, additional_context=context)


def test_compose_fallback_carries_the_template_key_difference():
    res = asyncio.run(compose.compose_report(FakeProvider([FakeRefusal()]), RunBudget(), compose_input()))
    assert res.fallback
    kd = res.content.sections[0].blocks[0]
    assert isinstance(kd, KeyDifferenceBlock) and kd.source == "template"
    assert "".join(s.text for s in kd.segments) == "A (Harborline) has the lowest premium ($12,500)."
    assert any(n.kind == "compose_fallback" for n in res.content.notices)


def test_compose_marks_after_applying_edits():
    """O3: Rewrite composes with the run's edits, so the sentence is written against the marks the broker sees."""
    inp = compose_input(edits=[ComposeEdit(column_key="B", field="total_premium", value="$9,000",
                                           original_value="$14,490.00")])
    res = asyncio.run(compose.compose_report(FakeProvider([FakeRefusal()]), RunBudget(), inp))
    kd = res.content.sections[0].blocks[0]
    assert [(m.row, m.column, m.kind) for m in kd.marks] == [("total_premium", "B", "best")]
    assert "".join(s.text for s in kd.segments) == "B (Juniper Peak) has the lowest premium ($9,000)."
    assert kd.cited_values[0].edited is True


def test_other_workflows_get_no_marks_and_no_key_difference(api_factory):
    api = api_factory(api_responder())  # Proposal Builder's extraction schema has no slice 6 keys
    plain = [[DOC_A[0].split("\nAdditional")[0]], [DOC_B[0].split("\nAdditional")[0]]]  # Proposal fields only
    ids = [api.upload_pages(plain[0], name="a.pdf"), api.upload_pages(plain[1], name="b.pdf")]
    r = api.client.post("/api/runs", json={"document_ids": ids, "workflow": "proposal-builder",
                                           "parameters": {"template_id": "standard", "no_ai_prose": True}})
    assert r.status_code == 201, r.text
    run_id = r.json()["run"]["id"]
    api.events(run_id)
    art = api.artifact(run_id)
    got = cells(art)
    assert got and all("mark" not in c for c in got.values())
    assert all(s["id"] != "key_difference" for s in art["content"]["sections"])
    assert ("total_premium", "A") in got  # the premium row is there, it is just never marked


# --------------------------------------------------------------------- T5 Additional context (BRIEF channel)


def test_additional_context_reaches_only_the_compose_brief(api_factory):
    prompts: dict[str, list[str]] = {"extract": [], "compose": []}
    base = responder()

    def fn(req):
        prompts["extract" if req.role is CallRole.EXTRACT else "compose"].append(
            "\n".join(m.parts()[0].text for m in req.messages))
        return base(req)

    api = api_factory(fn)
    run_id, _ = start(api, parameters={"additional_context": "  Client cares most about\x07 action over.  "})
    run = api.client.get(f"/api/runs/{run_id}").json()
    compose_prompt = prompts["compose"][0]
    assert "<<<BRIEF " in compose_prompt and "Client cares most about action over." in compose_prompt
    assert "8. The broker's additional context" in compose_prompt
    assert all("Client cares" not in p for p in prompts["extract"])
    assert run["status"] == "done"


def test_additional_context_limits(api_factory):
    api = api_factory(responder())
    ids = [api.upload_pages(DOC_A, name="a.pdf")]
    too_long = api.client.post("/api/runs", json={"document_ids": ids,
                                                  "parameters": {"additional_context": "x" * 1001}})
    assert too_long.status_code == 422 and too_long.json()["error"]["code"] == "invalid_request"
    not_text = api.client.post("/api/runs", json={"document_ids": ids, "parameters": {"additional_context": 5}})
    assert not_text.status_code == 422
    ok = api.client.post("/api/runs", json={"document_ids": ids, "parameters": {"additional_context": "x" * 1000}})
    assert ok.status_code == 201
    blank = api.client.post("/api/runs", json={"document_ids": ids, "parameters": {"additional_context": "   "}})
    assert blank.status_code in (200, 201)


def test_no_brief_block_without_context():
    msgs = compose.build_compose_messages(compose_input())
    assert "<<<BRIEF" not in msgs[1].parts()[0].text
    msgs = compose.build_compose_messages(compose_input(context="Lead with price."), boundary="B0UNDARY")
    text = msgs[1].parts()[0].text
    assert '<<<BRIEF B0UNDARY\n{\n "additional_context": "Lead with price."\n}\nBRIEF B0UNDARY>>>' in text
    with pytest.raises(ValueError):
        compose.build_compose_messages(compose_input(context="B0UNDARY"), boundary="B0UNDARY")
