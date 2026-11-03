"""Slice 3 T9 support: GET /discrepancies carries per-slot `sources` (fact rects, AMS field name).

The UI needs the field an AMS chip cites ("AMS · Cov A") and the fact's own highlight box on the
rendered AMS page (a passage's rects are every fact on that page). Both come from the verified
fact; the artifact is unchanged.
"""

from __future__ import annotations

import json

import pytest

from backend import ams
from backend.views import ams_field_name
from test_d12_regression import _policy_run, pc_api  # noqa: F401  (fixture)


@pytest.mark.parametrize(("quote", "value", "name"), [
    ("Cov A: $350,000", "$350,000", "Cov A"),
    ("Policy: No: HO-1", "HO-1", "Policy: No"),  # a name holding ": " survives
    ("Dwelling Limit: $360,000", None, "Dwelling Limit"),
    ("Agent Notes:", None, "Agent Notes"),
    ("no separator here", "x", None),
    (None, "x", None),
])
def test_ams_field_name_is_the_text_before_the_value(quote, value, name):
    assert ams_field_name(quote, value) == name


def test_ams_conflict_notice_names_the_field_label_not_the_key():
    data = json.dumps({"Coverage E": "$300,000", "Personal Liability": "$500,000"}).encode()
    notices = ams.map_facts(ams.render_pdf(ams.parse_ams(data))).notices
    conflict = next(n for n in notices if "give different values" in n)
    assert "Coverage E (Personal Liability)" in conflict
    assert "coverage_e_limit" not in conflict


def test_discrepancy_items_carry_slot_sources_with_the_ams_field(pc_api):  # noqa: F811
    run_id = _policy_run(pc_api)
    body = pc_api.client.get(f"/api/runs/{run_id}/discrepancies").json()
    item = next(i for i in body["items"] if i["key"] == "coverage_a_limit")
    ams = item["sources"]["ams"]
    assert [s["field"] for s in ams] == ["Cov A"]
    assert ams[0]["passage_id"] in item["slots"]["ams"]["passage_ids"]
    assert ams[0]["rects"] and len(ams[0]["rects"][0]) == 4
    renewal = item["sources"]["renewal"]
    assert renewal and all(s["field"] is None for s in renewal)
    # PATCH returns the same shape
    r = pc_api.client.patch(f"/api/runs/{run_id}/discrepancies/{item['id']}", json={"status": "resolved"})
    assert r.status_code == 200
    assert r.json()["sources"] == item["sources"]


def test_slots_without_a_cited_fact_have_no_sources(pc_api):  # noqa: F811
    run_id = _policy_run(pc_api)
    for item in pc_api.client.get(f"/api/runs/{run_id}/discrepancies").json()["items"]:
        for role, entries in item["sources"].items():
            cell = item["slots"][role]
            assert cell["state"] in ("verified", "scan_read")
            assert {e["passage_id"] for e in entries} <= set(cell["passage_ids"])
