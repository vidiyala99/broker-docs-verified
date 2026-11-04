"""Phase A comps only: turn the lr_main fixture truth into the shape the review page would get from the API.

Run: python frontend/comps/loss-runs/build_data.py  (writes data.json beside this file).
Every number comes from tests/fixtures/loss_runs/*.truth.json and lr_main.case.json; nothing is typed by hand.
Two broker states are staged on top of the truth so the comps can show them (LOSS-RUNS-BRIEF.md, "Comp data"):
  1. BW-23-111 (formula not calculated, unverified) confirmed by the broker with a note: it counts.
  2. Elmstead's page 2 subtotal is shown as needs_review (rows on no printed line), the verdict the fix lane added.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE.parents[2] / "tests" / "fixtures" / "loss_runs"
MONEY = ("paid", "reserve", "incurred", "recovery", "deductible")

SHORT = {
    "lr_main_ashgrove_clean": "Ashgrove workbook",
    "lr_main_ashgrove_export": "Ashgrove export",
    "lr_main_bellwether_messy": "Bellwether workbook",
    "lr_main_bellwether_extract": "Bellwether extract",
    "lr_main_driftwood_2024": "Driftwood 2024",
    "lr_main_driftwood_2025": "Driftwood 2025",
    "lr_main_elmstead_wrapped": "Elmstead",
    "lr_main_fairhaven_scan": "Fairhaven",
}
CONFIRMED = {"lr_main_bellwether_messy#5": "Incurred cell is a formula with no stored value. Paid + reserve is $8,000.00 and the 2023 subtotal on the same sheet ties with it."}
NEEDS_REVIEW = {("lr_main_elmstead_wrapped", "page_subtotal", "2"): "EL-2231 is read twice on page 2 and only one printed line holds it, so the rows inside this subtotal are not certain."}

case = json.loads((FIX / "lr_main.case.json").read_text(encoding="utf-8"))
docs, rows = [], []
for i, fid in enumerate(case["documents"]):
    t = json.loads((FIX / f"{fid}.truth.json").read_text(encoding="utf-8"))
    letter = chr(65 + i)
    docs.append({
        "fid": fid, "letter": letter, "short": SHORT[fid], "filename": t["file"], "format": t["format"],
        "carrier": t["carrier"], "valuation_date": t["valuation_date"], "pages": t["page_count"],
        "sheets": [s.get("name") for s in (t["sheets"] or []) if s.get("name")],
        "printed_totals": t["printed_totals"],
    })
    for ln in t["lines"]:
        loc = ln["location"]
        if "page" in loc:
            chip = f"{SHORT[fid]} p.{loc['page']}"
        elif loc.get("sheet"):
            chip = f"Sheet {loc['sheet']}, row {loc['row']}"
        else:
            chip = f"Row {loc['row']}"
        rows.append({
            "id": ln["line_id"], "fid": fid, "letter": letter, "carrier": t["carrier"], "chip": chip,
            "scan": t["format"] == "pdf" and bool(t.get("scan_transcript")),
            "claim_number": ln["claim_number"].strip(), "claim_key": ln["claim_key"],
            "claimant": ln["claimant"].split(". NOTE")[0],  # the planted canary text stays out of the comps
            "date_of_loss": ln["date_of_loss"], "status_raw": ln["status_raw"], "status": ln["status"],
            "policy_number": ln["policy_number"], "line": ln["line"], "page": loc.get("page"),
            **{m: ln[m] for m in MONEY},
            "verify": ln["verify"], "verify_reason": ln["unverified_reason"], "warnings": ln["warnings"],
            "role": case["roles"].get(ln["line_id"], "counted"),
        })

for r in rows:
    if r["id"] in CONFIRMED:
        r["decision"] = {"kind": "confirmed", "note": CONFIRMED[r["id"]]}
    r["counts"] = r["role"] == "counted" and (r["verify"] == "verified" or "decision" in r)

# claims = carrier + key; counted claim amounts = sum of the counted document's lines (D5)
claims = defaultdict(list)
for r in rows:
    claims[(r["carrier"], r["claim_key"])].append(r)
for key, lines in claims.items():
    live = [r for r in lines if r["role"] in ("counted", "conflicting")]
    for r in lines:
        same_doc = [x for x in live if x["fid"] == r["fid"]] or [r]
        r["claim_incurred"] = sum((x["incurred"] or 0) for x in same_doc)
        r["claim_open"] = any(x["status"] in ("open", "reopened") for x in same_doc)

counted = [r for r in rows if r["counts"]]
claim_keys = {(r["carrier"], r["claim_key"]) for r in counted}
totals = {"claims": len(claim_keys),
          "open": len({(r["carrier"], r["claim_key"]) for r in counted if r["claim_open"]}),
          "as_of": max(d["valuation_date"] for d in docs if d["valuation_date"])}
for m in MONEY:
    totals[m] = sum(r[m] or 0 for r in counted)
    totals[f"{m}_from"] = len({(r["carrier"], r["claim_key"]) for r in counted if r[m] is not None})

# checks: one per printed total (as the API sends them), grouped by printed line for display
checks = []
by_line = defaultdict(list)
for c in case["checks"]:
    by_line[(c["fixture_id"], c["label"], c["scope_kind"], str(c["scope_value"]))].append(c)
doc_by = {d["fid"]: d for d in docs}
for (fid, label, sk, sv), metrics in by_line.items():
    d = doc_by[fid]
    pt = next(p for p in d["printed_totals"] if p["label"] == label and str(p["scope_value"]) == sv)
    loc = pt["location"]
    chip = f"{d['short']} p.{loc['page']}" if "page" in loc else (f"Sheet {loc['sheet']}, row {loc['row']}" if loc.get("sheet") else f"Row {loc['row']}")
    verdicts = {m["verdict"] for m in metrics}
    verdict = "off" if "off" in verdicts else "matches"
    reason = NEEDS_REVIEW.get((fid, sk, sv))
    if reason:
        verdict = "needs_review"
    checks.append({
        "kind": "total", "fid": fid, "letter": d["letter"], "doc": d["short"], "label": label, "scope_kind": sk,
        "scope_value": None if sv == "None" else sv, "chip": chip, "quote": pt.get("quote"), "verdict": verdict,
        "reason": reason, "lines_in_scope": metrics[0]["lines_in_scope"],
        "metrics": [{"metric": m["metric"], "printed": m["printed"],
                     "computed": None if reason else m["computed"],
                     "difference": None if reason else m["difference"]} for m in metrics],
    })
conflict = [r for r in rows if r["role"] == "conflicting"]
checks.append({"kind": "conflict", "verdict": "conflicting", "claim_number": conflict[0]["claim_number"],
               "claimant": conflict[0]["claimant"], "options": [{k: r[k] for k in ("letter", "fid", "chip", "paid", "reserve", "incurred", "status_raw")} | {"doc": doc_by[r["fid"]]["short"], "valuation_date": doc_by[r["fid"]]["valuation_date"]} for r in conflict]})
for d in docs:
    unv = [r for r in rows if r["fid"] == d["fid"] and r["verify"] == "unverified"]
    if unv:
        handled = all("decision" in r for r in unv)
        checks.append({"kind": "unverified", "verdict": "handled" if handled else "unverified", "fid": d["fid"],
                       "letter": d["letter"], "doc": d["short"], "rows": len(unv),
                       "reason": unv[0]["verify_reason"], "row_ids": [r["id"] for r in unv],
                       "note": unv[0].get("decision", {}).get("note")})

notices = []
for n in case["notices"]:
    d = doc_by[n["fixture_id"]]
    text = {"no_printed_totals": f"{d['short']} prints no totals: nothing to reconcile.",
            "no_claims_table": f"No claims table found on sheet {n['sheet']} of {d['short']}.",
            "no_valuation_date": f"No valuation date found in {d['short']}: its rows count but never replace another file's."}.get(n["kind"])
    if text:
        notices.append(text)

out = {"client": case["client_name"], "synthetic_label": case["synthetic_label"], "docs": [{k: v for k, v in d.items() if k != "printed_totals"} for d in docs],
       "rows": rows, "totals": totals, "checks": checks, "notices": notices, "run": 58, "version": 4,
       "long": json.loads((FIX / "lr_long.case.json").read_text(encoding="utf-8"))["totals"]}
(HERE / "data.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print(json.dumps(totals), len(rows), "rows", len(checks), "checks")
print({c["verdict"] for c in checks}, sum(1 for c in checks if c["verdict"] == "matches"), "matching lines")
