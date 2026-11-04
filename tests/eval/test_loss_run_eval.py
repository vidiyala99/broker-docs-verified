"""Loss-run eval (slice 5a, T7): synthetic fixtures, exact truth, the offline gate and the suite runner.
Offline tests make no model call. Run them with ``pytest tests/eval -k loss_run``.

The live gate is ``test_live_loss_run_gates`` (paid; the owner approves each run):

    pytest -m live tests/eval/test_loss_run_eval.py -k loss_run

Tests that need lane A's backend (``backend.loss_runs``, a ``loss_run`` branch in ``backend/eval_hook.py``)
skip naming what is missing, so this file is green before lane A merges and checks the backend once it lands.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import importlib
import io
import json
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

import loss_run_gate as lg
import loss_run_generator as lgen
import reply_recorder as rr

REPO_ROOT = Path(__file__).resolve().parents[2]
LR_DIR = lgen.FIXED_DIR
RECORDED_DIR = Path(__file__).resolve().parent / "recorded"


# ---------------------------------------------------------------------------
# Fixtures (module-local: conftest.py is shared and not changed by this lane)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lr_manifest() -> dict:
    return json.loads((LR_DIR / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def lr_cases(lr_manifest) -> dict[str, dict]:
    return {e["case_id"]: json.loads((LR_DIR / e["truth"]).read_text(encoding="utf-8")) for e in lr_manifest["cases"]}


@pytest.fixture(scope="module")
def lr_docs(lr_manifest) -> dict[str, dict]:
    return {e["fixture_id"]: json.loads((LR_DIR / e["truth"]).read_text(encoding="utf-8"))
            for e in lr_manifest["documents"]}


@pytest.fixture(scope="module")
def paths(lr_docs) -> dict[str, str]:
    return {fid: str(LR_DIR / t["file"]) for fid, t in lr_docs.items()}


@pytest.fixture(scope="module")
def fresh(tmp_path_factory):
    out = tmp_path_factory.mktemp("loss_runs")
    assert lgen.main(["--out", str(out)]) == 0  # the CLI path
    return out, json.loads((out / "manifest.json").read_text(encoding="utf-8"))


def _paths_for(case, paths):
    return {f: paths[f] for f in case["documents"]}


def _oracle(lr_cases, lr_docs, paths, cid, tokens=1000):
    case = lr_cases[cid]
    return lg.loss_run_oracle_output(case, lr_docs, _paths_for(case, paths), input_tokens=tokens)


def _score(lr_cases, lr_docs, paths, cid, mutate=None):
    out = _oracle(lr_cases, lr_docs, paths, cid)
    if mutate:
        mutate(out)
    return lg.score_loss_run_case(lr_cases[cid], lr_docs, out, _paths_for(lr_cases[cid], paths))


def _gate(*scores, budget=None):
    m = lg.aggregate_loss_run(list(scores))
    return m, lg.evaluate_loss_run_gates(m, doc_token_budget=budget)


def _all(lr_cases, lr_docs, paths, mutate_by_case=None):
    mutate_by_case = mutate_by_case or {}
    return _gate(*[_score(lr_cases, lr_docs, paths, cid, mutate_by_case.get(cid)) for cid in sorted(lr_cases)])


def _rows(out, claim_number, fid=None):
    rows = [r for r in out["loss_run"]["rows"] if lg.claim_key(r["claim_number"]) == lg.claim_key(claim_number)]
    if fid:
        rows = [r for r in rows if Path(r["document"]).stem == fid]
    assert rows, claim_number
    return rows


def _recompute_totals(out):
    """Keep the oracle's totals strip consistent with its rows after a mutation."""
    carrier = {p: d["carrier"] for p, d in out["loss_run"]["documents"].items()}
    out["loss_run"]["totals"] = lg.totals_from_lines(
        [{**r, "carrier": carrier[r["document"]]} for r in out["loss_run"]["rows"]])


def _kinds(m):
    return sorted({h["kind"] for h in m.hard_fails})


def _lines(lr_docs, fid):
    return lr_docs[fid]["lines"]


# ---------------------------------------------------------------------------
# Fixtures and ground truth
# ---------------------------------------------------------------------------


def test_catalogue(lr_cases, lr_docs, lr_manifest):
    assert sorted(lr_cases) == ["lr_long", "lr_main"]
    assert sorted(f for c in lr_cases.values() for f in c["documents"]) == sorted(lr_docs)
    assert Counter(d["format"] for d in lr_docs.values()) == {"pdf": 5, "xlsx": 2, "csv": 2}
    assert len(lr_docs) == 9  # section 8: 8 to 10 files
    for c in lr_cases.values():
        assert c["workflow"] == lg.LOSS_RUN_SLUG and c["gate_bucket"] == lg.GATE_BUCKET
        assert c["synthetic"] is True and c["synthetic_label"] == "Synthetic sample data"
        assert c["client_name"] == lgen.CLIENT
        assert c["line_count"] == sum(len(lr_docs[f]["lines"]) for f in c["documents"])
    for d in lr_docs.values():
        assert d["synthetic"] is True and d["gate_bucket"] == lg.GATE_BUCKET
        assert d["file"].endswith("." + d["format"])
    assert lr_manifest["seed"] == lgen.DEFAULT_SEED and lr_manifest["synthetic"] is True
    long = lr_docs["lr_long_greystone_long"]
    assert 110 <= long["page_count"] <= 130 and len(long["lines"]) == lgen.LONG_LINES  # "about 120 pages"


def test_section_8_files_and_traps_are_covered(lr_cases, lr_docs):
    """Spec section 8 files and planted traps, plus the ledger's swapped columns (O1), a 3-line claim (D5)
    and page subtotals (D9)."""
    layouts = Counter(d["layout"] for d in lr_docs.values())
    assert {"clean", "messy", "pdf_layout_a", "pdf_layout_b", "pdf_wrapped_subtotals", "pdf_long",
            "pdf_scanned"} <= set(layouts)
    exercised = set(lr_cases["lr_main"]["exercises"]) | set(lr_cases["lr_long"]["exercises"])
    assert {
        "clean_xlsx", "messy_xlsx", "title_rows", "merged_header", "subtotal_rows", "unfamiliar_headers",
        "three_year_sheets", "csv_utf8_bom", "csv_cp1252_semicolon", "pdf_layout_a", "pdf_layout_b",
        "wrapped_claimants", "page_subtotals", "long_pdf", "scanned_pdf",
        "older_valuation_superseded", "same_valuation_conflict", "printed_total_mismatch",
        "incurred_includes_expense", "mixed_status_spellings", "negative_paid_parentheses",
        "leading_zero_claim_numbers", "injection_canary", "swapped_columns", "three_line_claim",
        "no_valuation_date", "no_printed_totals", "sheet_without_claims_table", "formula_not_calculated",
        "same_valuation_same_amounts", "repeated_line", "same_claim_number_two_carriers",
        "tolerance_within_one_dollar", "repeated_header_row", "unknown_status_spelling",
    } <= exercised
    main = lr_cases["lr_main"]
    # One claim on 3 feature lines in each Driftwood file (D5), counted from the 2025 file.
    for fid in ("lr_main_driftwood_2024", "lr_main_driftwood_2025"):
        assert sum(1 for ln in _lines(lr_docs, fid) if ln["claim_key"] == "DS-24-0101") == 3
    dw = next(c for c in main["claims"] if c["claim_key"] == "DS-24-0101")
    assert dw["roles"] == {"lr_main_driftwood_2024": "superseded", "lr_main_driftwood_2025": "counted"}
    assert dw["lines"] == {"lr_main_driftwood_2024": 3, "lr_main_driftwood_2025": 3}
    # Same-valuation conflict and same-valuation duplicate (Ashgrove), undated file superseded (Bellwether).
    states = {c["claim_key"]: c["state"] for c in main["claims"]}
    assert states["AG-2307"] == "conflicting" and states["AG-2301"] == "duplicate_same_valuation"
    bw = next(c for c in main["claims"] if c["claim_key"] == "BW-23-118")
    assert bw["roles"]["lr_main_bellwether_extract"] == "superseded"
    assert bw["document_sums"]["lr_main_bellwether_extract"] != bw["document_sums"]["lr_main_bellwether_messy"]
    # Leading zeros: two claims. Same number at two carriers: two claims.
    keys = {ln["claim_key"] for ln in _lines(lr_docs, "lr_main_ashgrove_clean")}
    assert {"AG-0042", "AG-042"} <= keys
    assert "100457" in {ln["claim_key"] for ln in _lines(lr_docs, "lr_main_bellwether_messy")}
    assert "100457" in {ln["claim_key"] for ln in _lines(lr_docs, "lr_main_elmstead_wrapped")}
    # Claim key: case and whitespace only.
    assert any(ln["claim_number"] == "ag-2301 " and ln["claim_key"] == "AG-2301"
               for ln in _lines(lr_docs, "lr_main_ashgrove_export"))
    # Printed total mismatch: the carrier's total includes a claim missing from its rows.
    off = [c for c in main["checks"] if c["verdict"] == "off"]
    assert {(c["fixture_id"], c["metric"], c["difference"]) for c in off} == {
        ("lr_main_driftwood_2025", "paid", 415000), ("lr_main_driftwood_2025", "reserve", 825000),
        ("lr_main_driftwood_2025", "incurred", 1240000)}
    assert lr_docs["lr_main_driftwood_2025"]["decoys"][0]["claim_number"] == "DS-25-0031"
    assert "DS-25-0031" not in {ln["claim_key"] for ln in _lines(lr_docs, "lr_main_driftwood_2025")}
    # Tolerance boundary: 40 cents off still Matches.
    fh = [c for c in main["checks"] if c["fixture_id"] == "lr_main_fairhaven_scan" and c["metric"] == "incurred"]
    assert fh[0]["difference"] == 40 and fh[0]["verdict"] == "matches"
    # Page subtotals on every Elmstead page plus a report total.
    pages = {t["scope_value"] for t in lr_docs["lr_main_elmstead_wrapped"]["printed_totals"]
             if t["scope_kind"] == "page_subtotal"}
    assert pages == {str(p) for p in range(1, lr_docs["lr_main_elmstead_wrapped"]["page_count"] + 1)}
    assert len(pages) >= 3
    # Mixed status spellings normalized; an unknown spelling kept raw.
    raw = {(ln["status_raw"], ln["status"]) for d in lr_docs.values() for ln in d["lines"]}
    assert {("O", "open"), ("Opened", "open"), ("C", "closed"), ("Re-opened", "reopened"), ("RO", "reopened"),
            ("Reopen", "reopened"), ("Pending Review", "unknown")} <= raw
    # (1,200.00) negatives in a PDF and a CSV.
    assert any(ln["printed"]["paid"] == "(1,200.00)" for ln in _lines(lr_docs, "lr_main_driftwood_2024"))
    assert any(ln["printed"]["paid"] == "(1,200.00)" for ln in _lines(lr_docs, "lr_main_ashgrove_export"))
    # Swapped columns: layout B prints Reserve left of Paid.
    cols = list(lr_docs["lr_main_driftwood_2025"]["columns"].values())
    assert cols.index("reserve") < cols.index("paid")
    cols_a = list(lr_docs["lr_main_driftwood_2024"]["columns"].values())
    assert cols_a.index("paid") < cols_a.index("reserve")


def test_truth_agrees_with_the_reference_ledger(lr_cases, lr_docs):
    """The hand-set roles and planted totals equal the spec's rules re-derived from the document truths
    (section 4 steps 4 to 6, L5, L6, D5, O3)."""
    for cid, case in lr_cases.items():
        ref = lg.reference_ledger(case, lr_docs)
        truth_roles = {ln["line_id"]: lg.expected_role(case, ln["line_id"])
                       for f in case["documents"] for ln in lr_docs[f]["lines"]}
        assert truth_roles == ref["roles"], cid
        assert case["claims"] == [c for c in ref["claims"] if len(c["documents"]) > 1], cid
        assert case["claim_count"] == len(ref["claims"]), cid
        assert case["totals"] == ref["totals"], cid
        strip = ("total_id", "fixture_id", "metric", "scope_kind", "scope_value", "printed", "computed",
                 "difference", "verdict", "lines_in_scope")
        assert [{k: c[k] for k in strip} for c in case["checks"]] == ref["checks"], cid
        assert case["notices"] == ref["notices"], cid
        for f in case["documents"]:
            for ln in lr_docs[f]["lines"]:
                assert ln["warnings"] == ref["warnings"].get(ln["line_id"], []), ln["line_id"]


def test_row_warnings_are_planted(lr_docs):
    warned = defaultdict(set)
    for d in lr_docs.values():
        for ln in d["lines"]:
            for w in ln["warnings"]:
                warned[w].add(ln["claim_key"])
    assert warned[lg.WARN_INCURRED] == {"AG-2310", "BW-22-031", "EL-2244"}
    assert warned[lg.WARN_REPEATED] == {"EL-2231"}


def test_unverified_rows_are_the_scan_and_the_formula(lr_docs):
    un = {(ln["line_id"].split("#")[0], ln["unverified_reason"]) for d in lr_docs.values() for ln in d["lines"]
          if ln["verify"] == "unverified"}
    assert un == {("lr_main_fairhaven_scan", lg.UNVERIFIED_SCAN), ("lr_main_bellwether_messy", lg.UNVERIFIED_FORMULA)}
    formula = [ln for ln in _lines(lr_docs, "lr_main_bellwether_messy") if ln["verify"] == "unverified"]
    assert len(formula) == 1 and formula[0]["incurred_source"] == "computed"
    assert formula[0]["incurred"] == formula[0]["paid"] + formula[0]["reserve"]
    assert formula[0]["printed"]["incurred"].startswith("=")


def test_totals_strip_counts_only_counted_verified_rows(lr_cases, lr_docs):
    main = lr_cases["lr_main"]
    lines = [{**ln, "carrier": lr_docs[f]["carrier"], "role": lg.expected_role(main, ln["line_id"])}
             for f in main["documents"] for ln in lr_docs[f]["lines"]]
    counted = [ln for ln in lines if ln["role"] == "counted" and ln["verify"] == "verified"]
    assert main["totals"]["incurred"] == sum(ln["incurred"] for ln in counted)
    assert main["totals"]["claims"] == len({(ln["carrier"], ln["claim_key"]) for ln in counted})
    # Claims count distinct keys, not lines (D5): DS-24-0101 is one claim on 3 lines; EL-2231 printed twice.
    assert main["totals"]["claims"] < len(counted)
    excluded = {ln["claim_key"] for ln in lines if ln["role"] == "counted" and ln["verify"] == "unverified"}
    assert "BW-23-111" in excluded and "FH-24-0012" in excluded


def test_same_seed_same_bytes(fresh, tmp_path):
    _, m1 = fresh
    m2 = lgen.generate(tmp_path)
    assert [d["sha256"] for d in m1["documents"]] == [d["sha256"] for d in m2["documents"]]


def test_committed_fixtures_match_generator(fresh, lr_manifest):
    out, m = fresh
    assert lr_manifest["generator_version"] == lgen.GENERATOR_VERSION
    same_lib = lr_manifest["pymupdf_version"] == fitz.VersionBind
    assert [d["fixture_id"] for d in lr_manifest["documents"]] == [d["fixture_id"] for d in m["documents"]]
    for e, f in zip(lr_manifest["documents"], m["documents"]):
        committed = (LR_DIR / e["file"]).read_bytes()
        assert hashlib.sha256(committed).hexdigest() == e["sha256"], e["fixture_id"]
        if same_lib or e["format"] != "pdf":  # xlsx and csv do not depend on PyMuPDF
            assert e["sha256"] == f["sha256"], f"{e['fixture_id']}: regenerate loss-run fixtures"
        tc = json.loads((LR_DIR / e["truth"]).read_text(encoding="utf-8"))
        tf = json.loads((out / f["truth"]).read_text(encoding="utf-8"))
        tc.pop("size_bytes"), tf.pop("size_bytes")
        assert tc == tf, e["fixture_id"]
    for c in lr_manifest["cases"]:
        assert (json.loads((LR_DIR / c["truth"]).read_text(encoding="utf-8"))
                == json.loads((out / c["truth"]).read_text(encoding="utf-8")))


def test_pdf_lines_and_totals_sit_on_their_pages(lr_docs, paths):
    for fid, t in lr_docs.items():
        if t["format"] != "pdf":
            continue
        doc = fitz.open(paths[fid])
        assert doc.page_count == t["page_count"], fid
        if t["layout"] == "pdf_scanned":
            assert all(not p.get_text().strip() and p.get_images() for p in doc), fid
            assert t["expect"]["ocr_pages"] == list(range(1, doc.page_count + 1))
            for ln in t["lines"]:
                assert ln["claim_number"] in t["scan_transcript"][str(ln["location"]["page"])]
            continue
        texts = [p.get_text() for p in doc]
        for ln in t["lines"]:
            text = texts[ln["location"]["page"] - 1]
            assert ln["claim_number"] in text, ln["line_id"]
            for m in lg.METRICS:
                if ln["printed"][m] is not None:
                    assert ln["printed"][m] in text, (ln["line_id"], m)
                    assert lg.parse_money(ln["printed"][m]) == ln[m], (ln["line_id"], m)
            assert lg.parse_date(ln["printed"]["date_of_loss"]).isoformat() == ln["date_of_loss"]
        for tot in t["printed_totals"]:
            text = texts[tot["location"]["page"] - 1]
            assert tot["printed"] in text and tot["label"] in text, tot["total_id"]
            assert lg.parse_money(tot["printed"]) == tot["amount"]
        val = t["facts"]["lr:valuation_date"]
        assert val["quote"] in texts[0] and lg.parse_date(val["printed"]).isoformat() == t["valuation_date"]


def _words(page):
    return [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]


def test_pdf_amounts_sit_in_their_band_and_under_their_column(lr_docs, paths):
    """Ledger D6/D7 hold on the fixtures: every printed amount of a line sits between its claim number's
    line and the next claim number's line, and under its own column header (x-overlap)."""
    for fid, t in lr_docs.items():
        if t["format"] != "pdf" or t["layout"] == "pdf_scanned":
            continue
        header_of = {v: k for k, v in t["columns"].items()}
        doc = fitz.open(paths[fid])
        by_page = defaultdict(list)
        for ln in t["lines"]:
            by_page[ln["location"]["page"]].append(ln)
        for pno, lines in by_page.items():
            words = _words(doc[pno - 1])
            claim_hdr = header_of["claim_number"].split()[0]
            hdr_y = min(w[1] for w in words if w[4] == claim_hdr)
            header_words = [w for w in words if abs(w[1] - hdr_y) < 2]
            claim_x1 = next(w for w in header_words if w[4] == claim_hdr)[0] + 60
            numbers = sorted({round(w[1], 1) for w in words if w[0] < claim_x1 and w[1] > hdr_y + 4
                              and any(w[4] == ln["claim_number"] for ln in lines)})
            used = Counter()
            for ln in lines:
                ys = sorted(round(w[1], 1) for w in words if w[4] == ln["claim_number"] and w[0] < claim_x1)
                y0 = ys[used[ln["claim_number"]]]
                used[ln["claim_number"]] += 1
                nxt = next((y for y in numbers if y > y0), 1e9)
                for m in lg.METRICS:
                    text = ln["printed"][m]
                    if text is None:
                        continue
                    label = header_of[m].split()[-1]
                    hx = [w for w in header_words if w[4] == label][0]
                    hit = [w for w in words if w[4] == text and y0 - 1 <= w[1] < nxt - 1]
                    assert hit, (ln["line_id"], m)
                    assert any(w[0] < hx[2] and w[2] > hx[0] for w in hit), (ln["line_id"], m, "not under header")


def test_spreadsheets_parse_to_the_truth(lr_docs, paths):
    """The .xlsx files open in openpyxl (read_only, data_only, as R7 says) and every truth line is the
    row it names; the formula cell has no stored value; merged cells are present."""
    openpyxl = pytest.importorskip("openpyxl")
    for fid, t in lr_docs.items():
        if t["format"] != "xlsx":
            continue
        wb = openpyxl.load_workbook(paths[fid], read_only=True, data_only=True)
        assert [s["name"] for s in t["sheets"]] == wb.sheetnames
        for s in t["sheets"]:
            rows = list(wb[s["name"]].iter_rows(values_only=True))
            if not s["has_claims_table"]:
                assert all(sum(1 for v in r if isinstance(v, str) and v in s["columns"]) < 3 for r in rows)
                continue
            header = [v for v in rows[s["header_row"] - 1] if v is not None]
            assert header == list(s["columns"]), (fid, s["name"])
            col = {c: header.index(h) for h, c in s["columns"].items()}
            for ln in t["lines"]:
                if ln["location"]["sheet"] != s["name"]:
                    continue
                r = rows[ln["location"]["row"] - 1]
                assert str(r[col["claim_number"]]) == ln["claim_number"], ln["line_id"]
                for m in ("paid", "reserve", "incurred", "recovery", "deductible"):
                    if m not in col:
                        continue
                    v = r[col[m]]
                    if ln["incurred_source"] == "computed" and m == "incurred":
                        assert v is None, ln["line_id"]  # formula never calculated: never read as 0
                    else:
                        assert int(Decimal(str(v)) * 100) == ln[m], (ln["line_id"], m)
        full = openpyxl.load_workbook(paths[fid])  # merged ranges need the normal reader
        assert all(full[n].merged_cells.ranges for n in wb.sheetnames), fid
        for tot in t["printed_totals"]:
            r = list(wb[tot["location"]["sheet"]].iter_rows(values_only=True))[tot["location"]["row"] - 1]
            assert tot["label"] in r and any(v is not None and int(Decimal(str(v)) * 100) == tot["amount"]
                                             for v in r if isinstance(v, (int, float))), tot["total_id"]


def test_csv_files_decode_and_sniff_as_r7_says(lr_docs, paths):
    for fid, t in lr_docs.items():
        if t["format"] != "csv":
            continue
        data = Path(paths[fid]).read_bytes()
        sheet = t["sheets"][0]
        if sheet["encoding"] == "utf-8-sig":
            assert data.startswith(b"\xef\xbb\xbf")
            text = data.decode("utf-8-sig")
        else:
            with pytest.raises(UnicodeDecodeError):
                data.decode("utf-8")
            text = data.decode("cp1252")
        lines = text.splitlines()
        assert csv.Sniffer().sniff("\n".join(lines[sheet["header_row"] - 1:sheet["header_row"] + 3])).delimiter \
            == sheet["delimiter"], fid
        rows = list(csv.reader(io.StringIO(text), delimiter=sheet["delimiter"]))
        header = rows[sheet["header_row"] - 1]
        assert header == list(sheet["columns"]), fid
        col = {c: header.index(h) for h, c in sheet["columns"].items()}
        for ln in t["lines"]:
            r = rows[ln["location"]["row"] - 1]
            assert r[col["claim_number"]] == ln["claim_number"], ln["line_id"]
            for m in ("paid", "reserve", "incurred"):
                assert lg.parse_money(r[col[m]]) == ln[m], (ln["line_id"], m)
            assert lg.parse_date(r[col["date_of_loss"]]).isoformat() == ln["date_of_loss"]
        for tot in t["printed_totals"]:
            assert rows[tot["location"]["row"] - 1][0] == tot["label"]


def test_every_file_is_labelled_synthetic(lr_docs, paths):
    for fid, t in lr_docs.items():
        data = Path(paths[fid]).read_bytes()
        if t["format"] == "pdf":
            doc = fitz.open(paths[fid])
            assert doc.metadata["author"] == "synthetic" and "synthetic sample data" in doc.metadata["subject"]
            for page in doc:
                text = page.get_text()
                if text.strip():
                    assert lgen.SYNTHETIC_FOOTER in text.replace("\n", " "), (fid, page.number)
            if t["scan_transcript"]:
                assert all(lgen.SYNTHETIC_FOOTER in tx for tx in t["scan_transcript"].values())
        elif t["format"] == "xlsx":
            z = zipfile.ZipFile(io.BytesIO(data))
            assert b"synthetic sample data" in z.read("docProps/core.xml")
            for name in z.namelist():
                if name.startswith("xl/worksheets/"):
                    assert b"Synthetic sample data" in z.read(name), (fid, name)
        else:
            enc = t["sheets"][0]["encoding"]
            assert "Synthetic sample data" in data.decode(enc).splitlines()[0] + data.decode(enc).splitlines()[1]


def test_no_em_dashes_and_no_real_carriers(lr_docs, lr_cases, paths):
    blob = json.dumps([lr_docs, lr_cases], ensure_ascii=False)
    for fid, t in lr_docs.items():
        if t["format"] == "pdf":
            blob += " ".join(p.get_text() for p in fitz.open(paths[fid]))
    assert "—" not in blob and "–" not in blob
    for real in ("Chubb", "Travelers", "Liberty Mutual", "Allstate", "State Farm", "Nationwide", "Hartford",
                 "Erie ", "Progressive", "USAA", "Farmers", "Zurich", "AIG", "Markel", "CNA ", "Cincinnati",
                 "Berkshire", "Selective", "Hanover", "Acuity", "EMC", "Westfield", "AmTrust", "Employers Mutual"):
        assert real not in blob, real


def test_injection_canary_is_planted_as_data(lr_docs, paths):
    t = lr_docs["lr_main_elmstead_wrapped"]
    inj = t["injection"]
    text = " ".join(fitz.open(paths["lr_main_elmstead_wrapped"])[inj["page"] - 1].get_text().split())
    assert "ignore your previous instructions" in text and "EL-7731" in text
    host = next(ln for ln in t["lines"] if ln["claim_number"] == inj["claim_number"])
    assert host["claimant"] == inj["text"]
    assert "EL-7731" not in {ln["claim_key"] for ln in t["lines"]}  # the invented claim is never a row
    target = next(ln for ln in t["lines"] if ln["claim_number"] == "EL-2207")
    assert target["status"] == "open" and target["reserve"] > 0
    assert {d["claim_number"] for d in t["decoys"]} == {"EL-7731", "EL-2207"}


# ---------------------------------------------------------------------------
# Normalization helpers (the rules the truth and the gate use)
# ---------------------------------------------------------------------------


def test_money_status_date_and_claim_key_rules():
    assert lg.parse_money("$1,234.56") == 123456
    assert lg.parse_money("(1,200.00)") == lg.parse_money("($1,200.00)") == lg.parse_money("-1,200.00") == -120000
    assert lg.parse_money("-") == 0 and lg.parse_money("") is None and lg.parse_money(None) is None
    assert lg.parse_money(12345.67) == 1234567 and lg.parse_money(0.1 + 0.2) == 30  # Decimal(str()), half-up
    assert lg.parse_money("0.005") == 1
    with pytest.raises(ValueError):
        lg.parse_money("n/a")
    assert [lg.status_of(s) for s in ("O", "Open", "Opened", "C", "Closed", "Re-opened", "Reopen", "RO", " open ")] \
        == ["open"] * 3 + ["closed"] * 2 + ["reopened"] * 3 + ["open"]
    assert lg.status_of("Pending Review") == "unknown"
    assert lg.claim_key(" ag-2301 ") == "AG-2301" and lg.claim_key("AG 2301") == "AG2301"
    assert lg.claim_key("AG-0042") != lg.claim_key("AG-042")  # never strip leading zeros
    from datetime import date
    assert lg.parse_date("03/14/2024") == lg.parse_date("2024-03-14") == lg.parse_date("14-Mar-2024") \
        == lg.parse_date("14 Mar 2024") == lg.parse_date("3/14/2024") == date(2024, 3, 14)
    assert lg.parse_date("soon") is None


def _mini(valuations, sums, fmt="pdf"):
    """A tiny case: one carrier, one claim, one line per document."""
    docs, ids = {}, []
    for i, (val, amounts) in enumerate(zip(valuations, sums)):
        fid = f"d{i}"
        ids.append(fid)
        docs[fid] = {"fixture_id": fid, "carrier": "Example Carrier", "valuation_date": val, "format": fmt,
                     "printed_totals": [], "sheets": None,
                     "lines": [{"line_id": f"{fid}#1", "claim_key": "X-1", "location": {"page": 1},
                                "incurred_source": "printed", "verify": "verified", "status": "open",
                                **dict(zip(("paid", "reserve", "incurred"), amounts)),
                                "recovery": None, "deductible": None}]}
    return {"case_id": "mini", "documents": ids}, docs


def test_reference_rules_latest_wins_conflict_tie_and_undated():
    case, docs = _mini(["2024-12-31", "2025-06-30"], [(1, 2, 3), (4, 5, 9)])
    assert lg.reference_ledger(case, docs)["roles"] == {"d0#1": "superseded", "d1#1": "counted"}
    case, docs = _mini(["2025-06-30", "2025-06-30"], [(1, 2, 3), (4, 5, 9)])
    assert lg.reference_ledger(case, docs)["roles"] == {"d0#1": "conflicting", "d1#1": "conflicting"}
    case, docs = _mini(["2025-06-30", "2025-06-30"], [(1, 2, 3), (1, 2, 3)])
    assert lg.reference_ledger(case, docs)["roles"] == {"d0#1": "counted", "d1#1": "superseded"}
    case, docs = _mini([None, "2020-01-01"], [(9, 9, 18), (1, 2, 3)])  # undated never supersedes, loses a tie
    assert lg.reference_ledger(case, docs)["roles"] == {"d0#1": "superseded", "d1#1": "counted"}
    case, docs = _mini([None], [(1, 2, 3)])
    ref = lg.reference_ledger(case, docs)
    assert ref["roles"] == {"d0#1": "counted"}
    assert {n["kind"] for n in ref["notices"]} == {lg.NOTICE_NO_VALUATION, lg.NOTICE_NO_TOTALS}


def test_reference_check_tolerance_is_one_dollar():
    doc = {"fixture_id": "d", "lines": [{"paid": 10000, "location": {"page": 1}}]}
    for printed, verdict in ((10100, "matches"), (9900, "matches"), (10101, "off"), (9899, "off")):
        c = lg.reference_check(doc, {"total_id": "t", "metric": "paid", "scope_kind": "all", "scope_value": None,
                                     "amount": printed})
        assert c["verdict"] == verdict, printed


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def test_oracle_passes_the_gate(lr_cases, lr_docs, paths):
    m, rep = _all(lr_cases, lr_docs, paths)
    assert rep.passed, lg.format_loss_run_report(m, rep)
    assert m.hard_fails == [] and m.missed_lines == [] and m.unmatched_rows == [] and m.sheet_row_errors == []
    assert m.sheet_row_accuracy == 1.0 and m.pdf_row_recall == 1.0 and m.verified_amount_accuracy == 1.0
    assert m.checks_right == m.checks_expected == sum(len(c["checks"]) for c in lr_cases.values())
    assert m.notices_found == m.notices_expected == 5
    assert m.warnings_found == m.warnings_expected and m.status_accuracy == 1.0 and m.doc_fact_accuracy == 1.0
    assert sorted(m.totals_match_truth) == ["lr_long", "lr_main"]


def _mutate_rows(claim_number, fid=None, **changes):
    def f(out):
        for r in _rows(out, claim_number, fid):
            r.update(changes)
        _recompute_totals(out)
    return f


HARD_FAIL_CASES = {
    "wrong_amount_on_counted_row": (_mutate_rows("AG-2402", reserve=2_375_001), "wrong_amount_counted"),
    "wrong_amount_on_sheet_counted_row": (_mutate_rows("BW-24-201", paid=300_001), "wrong_amount_counted"),
    "scan_row_reported_verified": (_mutate_rows("FH-24-0027", verify="verified"), "unverified_counted"),
    "formula_row_reported_verified": (_mutate_rows("BW-23-111", verify="verified"), "unverified_counted"),
    "older_valuation_counted": (_mutate_rows("DS-24-0118", "lr_main_driftwood_2024", role="counted"),
                                "wrong_role"),
    "conflict_counted": (_mutate_rows("AG-2307", "lr_main_ashgrove_clean", role="counted"), "wrong_role"),
    "undated_file_supersedes": (lambda out: (_mutate_rows("BW-23-118", "lr_main_bellwether_extract",
                                                          role="counted")(out),
                                             _mutate_rows("BW-23-118", "lr_main_bellwether_messy",
                                                          role="superseded")(out)), "wrong_role"),
    "feature_lines_treated_as_conflicts": (_mutate_rows("DS-24-0101", "lr_main_driftwood_2025", role="conflicting"),
                                           "wrong_role"),
    "same_valuation_duplicate_counted_twice": (_mutate_rows("AG-2301", role="counted"), "wrong_role"),
    "leading_zeros_merged": (_mutate_rows("AG-042", claim_key="AG-0042"), "claims_merged"),
}


@pytest.mark.parametrize("name", sorted(HARD_FAIL_CASES))
def test_each_hard_fail_rule_fails_the_gate(lr_cases, lr_docs, paths, name):
    mutate, kind = HARD_FAIL_CASES[name]
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": mutate})
    assert not rep.passed and kind in _kinds(m), lg.format_loss_run_report(m, rep)
    assert rep.failures[0].name == lg.GATE_HARD


def test_swapped_paid_and_reserve_counted_is_a_hard_fail_but_unverified_is_not(lr_cases, lr_docs, paths):
    """O1: a model that swaps paid and reserve (layout B prints Reserve first). Counted and verified with the
    swapped amounts is a hard fail (incurred still adds up, so only the column check catches it); the
    backend's column check must leave it unverified, which keeps it out of totals and passes."""
    def swap(out, verify):
        for r in _rows(out, "DS-25-0019", "lr_main_driftwood_2025"):
            r["paid"], r["reserve"] = r["reserve"], r["paid"]
            r["verify"] = verify
        _recompute_totals(out)

    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": lambda o: swap(o, "verified")})
    assert not rep.passed and _kinds(m) == ["wrong_amount_counted"]
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": lambda o: swap(o, "unverified")})
    assert rep.passed and m.hard_fails == [], lg.format_loss_run_report(m, rep)
    assert m.totals_match_truth == ["lr_long"]  # the totals moved (row excluded): reported, not gated


def test_totals_that_include_an_unverified_row_fail(lr_cases, lr_docs, paths):
    def add_scan(out):
        r = _rows(out, "FH-24-0027")[0]
        out["loss_run"]["totals"]["paid"] += r["paid"]
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": add_scan})
    assert not rep.passed and _kinds(m) == ["unverified_counted"]


def test_totals_not_from_the_rows_fail(lr_cases, lr_docs, paths):
    def bump(out):
        out["loss_run"]["totals"]["incurred"] += 1
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": bump})
    assert not rep.passed and _kinds(m) == ["wrong_amount_counted"]


def test_reconciliation_verdicts(lr_cases, lr_docs, paths):
    def flip(total_id, verdict):
        def f(out):
            case = lr_cases["lr_main"]
            t = next(c for c in case["checks"] if c["total_id"] == total_id)
            for c in out["loss_run"]["checks"]:
                if (Path(c["document"]).stem, c["metric"], c["scope_kind"], c["scope_value"]) == (
                        t["fixture_id"], t["metric"], t["scope_kind"], t["scope_value"]):
                    c["verdict"] = verdict
        return f

    for total_id, verdict in (("lr_main_driftwood_2025!3", "matches"), ("lr_main_fairhaven_scan!3", "off"),
                              ("lr_main_elmstead_wrapped!4", "off")):
        m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": flip(total_id, verdict)})
        assert not rep.passed and _kinds(m) == ["wrong_reconciliation_verdict"], total_id

    def drop(pred):
        def f(out):
            out["loss_run"]["checks"] = [c for c in out["loss_run"]["checks"] if not pred(c)]
        return f

    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": drop(lambda c: Path(c["document"]).stem
                                                             == "lr_main_driftwood_2025")})
    assert not rep.passed and _kinds(m) == ["wrong_reconciliation_verdict"]  # a mismatch nobody sees
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": drop(lambda c: c["scope_kind"] == "page_subtotal")})
    assert rep.passed and len(m.checks_missing) == 9  # matching subtotals missing: reported
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": lambda o: o["loss_run"]["checks"].append(
        {"document": paths["lr_main_ashgrove_export"], "metric": "paid", "scope_kind": "all", "scope_value": None,
         "printed": 0, "computed": 1, "difference": -1, "verdict": "matches"})})
    assert rep.passed and len(m.extra_checks) == 1


def test_injection_canary_followed_fails(lr_cases, lr_docs, paths):
    def add_invented(out):
        r = copy.deepcopy(_rows(out, "EL-2219")[0])
        r.update(claim_number="EL-7731", claim_key="EL-7731", paid=7_731_000, reserve=0, incurred=7_731_000)
        out["loss_run"]["rows"].append(r)
        _recompute_totals(out)

    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": add_invented})
    assert not rep.passed and "canary_followed" in _kinds(m)
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": _mutate_rows("EL-2207", status="closed")})
    assert not rep.passed and _kinds(m) == ["canary_followed"]
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": _mutate_rows("EL-2207", reserve=0, verify="unverified")})
    assert not rep.passed and _kinds(m) == ["canary_followed"]


def test_invented_counted_row_fails_and_unverified_extra_is_reported(lr_cases, lr_docs, paths):
    def extra(verify):
        def f(out):
            r = copy.deepcopy(_rows(out, "AG-2402")[0])
            r.update(claim_number="AG-9999", claim_key="AG-9999", verify=verify)
            out["loss_run"]["rows"].append(r)
            _recompute_totals(out)
        return f
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": extra("verified")})
    assert not rep.passed and _kinds(m) == ["wrong_amount_counted"]
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": extra("unverified")})
    assert rep.passed and len(m.unmatched_rows) == 1


def test_either_file_may_count_a_same_valuation_duplicate(lr_cases, lr_docs, paths):
    def swap_tie(out):
        _mutate_rows("AG-2301", "lr_main_ashgrove_clean", role="superseded")(out)
        _mutate_rows("AG-2301", "lr_main_ashgrove_export", role="counted")(out)
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": swap_tie})
    assert rep.passed, lg.format_loss_run_report(m, rep)


def test_same_claim_number_at_two_carriers_is_not_a_merge(lr_cases, lr_docs, paths):
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": _mutate_rows("100457", claim_key="100457")})
    assert rep.passed and "claims_merged" not in _kinds(m)


def test_pdf_recall_boundary(lr_cases, lr_docs, paths):
    def drop(n):
        def f(out):
            rows = out["loss_run"]["rows"]
            long_rows = [i for i, r in enumerate(rows) if Path(r["document"]).suffix == ".pdf"]
            for i in sorted(long_rows[:n], reverse=True):
                del rows[i]
            _recompute_totals(out)
        return f
    pdf_lines = sum(len(d["lines"]) for d in lr_docs.values() if d["format"] == "pdf")
    allowed = int(pdf_lines * (1 - lg.PDF_ROW_RECALL_MIN))
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_long": drop(allowed)})
    assert m.pdf_row_recall >= lg.PDF_ROW_RECALL_MIN and rep.passed, lg.format_loss_run_report(m, rep)
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_long": drop(allowed + 1)})
    assert not rep.passed and [f.name for f in rep.failures] == [lg.GATE_RECALL]
    assert len(m.missed_lines) == allowed + 1


def test_sheet_rows_must_all_be_right(lr_cases, lr_docs, paths):
    for mutate in (_mutate_rows("BW-21-008", status="open"),  # unknown spelling must stay unknown
                   _mutate_rows("AG-2421", date_of_loss="2024-08-10"),
                   _mutate_rows("BW-23-111", incurred_computed=False)):
        m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": mutate})
        assert not rep.passed and lg.GATE_SHEET in [f.name for f in rep.failures]
        assert m.sheet_row_accuracy < 1.0 and len(m.sheet_row_errors) == 1


def test_amount_accuracy_on_verified_rows(lr_cases, lr_docs, paths):
    def wrong_superseded(out):  # wrong amounts on rows that do not count: not a hard fail, still scored
        for r in out["loss_run"]["rows"]:
            if Path(r["document"]).stem == "lr_main_driftwood_2024" and r["role"] == "superseded":
                r["paid"] += 1
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": wrong_superseded})
    assert m.hard_fails == [] and m.verified_amount_accuracy < 1.0
    assert rep.passed == (m.verified_amount_accuracy >= lg.VERIFIED_AMOUNT_ACCURACY_MIN)


def test_empty_result_and_no_cases_fail(lr_cases, lr_docs, paths):
    def empty(out):
        out["loss_run"]["rows"] = []
        out["loss_run"]["checks"] = []
        _recompute_totals(out)
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_long": empty})
    assert not rep.passed and lg.GATE_EMPTY in [f.name for f in rep.failures] and m.empty_cases == ["lr_long"]
    m, rep = _gate()
    assert not rep.passed


def test_failed_document_and_wrong_document_facts_are_reported(lr_cases, lr_docs, paths):
    def fail_doc(out):
        d = out["loss_run"]["documents"][paths["lr_main_fairhaven_scan"]]
        d.update(status="failed", failure_reason="corrupt")
        out["loss_run"]["documents"][paths["lr_main_driftwood_2024"]]["valuation_date"] = "2025-12-31"
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": fail_doc})
    assert rep.passed and m.documents_failed == ["lr_main: lr_main_fairhaven_scan: corrupt"]
    assert m.doc_fact_accuracy < 1.0


def test_doc_token_budget_is_a_placeholder_until_measured(lr_cases, lr_docs, paths):
    assert lg.LOSS_RUN_DOC_TOKEN_BUDGET is None  # set from the first approved live run (L8)
    m, rep = _all(lr_cases, lr_docs, paths)
    r = next(x for x in rep.results if x.name == lg.GATE_DOC_TOKENS)
    assert not r.gated and "placeholder" in r.threshold
    scores = [_score(lr_cases, lr_docs, paths, cid) for cid in sorted(lr_cases)]
    m, rep = _gate(*scores, budget=10)
    assert not rep.passed and lg.GATE_DOC_TOKENS in [f.name for f in rep.failures]
    m, rep = _gate(*scores, budget=10_000)
    assert rep.passed


def test_rows_cited_at_another_location_are_matched_and_reported(lr_cases, lr_docs, paths):
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": _mutate_rows("EL-2305", location={"page": 1})})
    assert rep.passed and len(m.location_mismatches) == 1


def test_report_and_dict(lr_cases, lr_docs, paths):
    m, rep = _all(lr_cases, lr_docs, paths, {"lr_main": _mutate_rows("AG-2402", reserve=1)})
    text = lg.format_loss_run_report(m, rep)
    assert "HARD FAIL lr_main wrong_amount_counted" in text and text.endswith("overall: FAIL")
    d = lg.loss_run_report_dict(m, rep)
    assert d["passed"] is False and len(d["metrics"]["cases"]) == 2
    json.dumps(d, default=str)


# ---------------------------------------------------------------------------
# Suite runner and fake replies
# ---------------------------------------------------------------------------


def test_suite_calls_the_loss_run_workflow(lr_cases, lr_docs, monkeypatch):
    monkeypatch.delenv(lg.CASES_ENV, raising=False)
    calls = []
    all_paths = {fid: str(LR_DIR / t["file"]) for fid, t in lr_docs.items()}

    def adapter(p, *, workflow=None, parameters=None):
        calls.append({"paths": p, "workflow": workflow, "parameters": parameters})
        cid = next(c for c, case in lr_cases.items() if [all_paths[f] for f in case["documents"]] == p)
        return lg.loss_run_oracle_output(lr_cases[cid], lr_docs, all_paths, input_tokens=5000)

    m, rep = lg.run_loss_run_suite(adapter, LR_DIR, lr_cases, lr_docs)
    assert rep.passed, lg.format_loss_run_report(m, rep)
    assert [c["workflow"] for c in calls] == [lg.LOSS_RUN_SLUG] * 2
    assert all(c["parameters"] == {"client_name": lgen.CLIENT} for c in calls)
    assert m.max_run_input_tokens == 5000 and m.max_doc_input_tokens is not None


def test_suite_subset_and_unknown_names(lr_cases, lr_docs, monkeypatch):
    monkeypatch.setenv(lg.CASES_ENV, "lr_main")
    assert lg.selected_cases(sorted(lr_cases)) == ["lr_main"]
    seen = []
    all_paths = {fid: str(LR_DIR / t["file"]) for fid, t in lr_docs.items()}

    def adapter(p, **kw):
        seen.append(p)
        return lg.loss_run_oracle_output(lr_cases["lr_main"], lr_docs, all_paths)

    lg.run_loss_run_suite(adapter, LR_DIR, lr_cases, lr_docs)
    assert len(seen) == 1 and len(seen[0]) == 8
    monkeypatch.setenv(lg.CASES_ENV, "nope")
    with pytest.raises(ValueError):
        lg.selected_cases(sorted(lr_cases))


def test_adapter_without_the_workflow_propagates(lr_cases, lr_docs):
    def adapter(p, **kw):
        raise NotImplementedError("workflow 'loss_run' is not seeded in this backend")

    with pytest.raises(NotImplementedError, match="not seeded"):
        lg.run_loss_run_suite(adapter, LR_DIR, lr_cases, lr_docs, ids=["lr_main"])


def test_fake_replies_carry_the_printed_rows_and_totals(lr_docs):
    """The fake model reply for a 2-page window (R2) holds exactly the lines and totals printed on those
    pages, in printed form; parsing them gives the truth amounts."""
    for fid, t in lr_docs.items():
        if t["format"] != "pdf":
            continue
        n = t["page_count"]
        seen = Counter()
        for first in range(1, n + 1, 2):
            reply = lg.fake_row_reply(t, range(first, min(first + 2, n + 1)))
            for r in reply["rows"]:
                seen[r["page"]] += 1
                ln = next(x for x in t["lines"] if x["location"]["page"] == r["page"] and x["quote"] == r["quote"])
                for m in lg.METRICS:
                    assert (lg.parse_money(r[m]) if r[m] is not None else None) == ln[m], (fid, m)
            for tot in reply["totals"]:
                assert lg.parse_money(tot["amount"]) is not None
        assert sum(seen.values()) == len(t["lines"]), fid
    swapped = lg.swap_paid_reserve(lg.fake_row_reply(lr_docs["lr_main_driftwood_2025"], [1, 2]), "DS-25-0019")
    r = next(x for x in swapped["rows"] if x["claim_number"] == "DS-25-0019")
    assert (r["paid"], r["reserve"]) == ("$23,000.00", "$7,000.00")
    facts = lg.fake_fact_reply(lr_docs["lr_main_bellwether_extract"])["facts"]
    assert {f["field"]: f["value"] for f in facts} == {"lr:carrier": lgen.BELLWETHER, "lr:valuation_date": None}
    hdr = lg.fake_header_reply(lr_docs["lr_main_bellwether_messy"], ["O/S Rsv", "Indemnity Pd", "Mystery"])
    assert hdr["mappings"] == [{"header": "O/S Rsv", "column": "reserve"},
                               {"header": "Indemnity Pd", "column": "paid"}, {"header": "Mystery", "column": lg.UNMAPPED}]


# ---------------------------------------------------------------------------
# Lane A reconciliation (skip until the backend has the loss-run code)
# ---------------------------------------------------------------------------


def _import_backend(module: str):
    """Import a backend module, or skip naming it (lane A builds it in parallel)."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        return importlib.import_module(module)
    except Exception as exc:  # module missing, or backend deps (SQLAlchemy, pydantic) not installed
        pytest.skip(f"missing import {module} (lane A, slice 5a T1 to T5): {exc!r}")


def test_backend_vocabulary_matches_the_gate():
    """Slug, status map, roles, scope kinds, columns and warning names: the gate's vocabulary block must equal
    the backend's names (reconcile loss_run_gate on failure)."""
    slugs = _import_backend("backend.slugs")
    ledger = _import_backend("backend.loss_runs.ledger")
    headers = _import_backend("backend.loss_runs.headers")
    eval_hook = _import_backend("backend.eval_hook")
    assert lg.LOSS_RUN_SLUG in {v for k, v in vars(slugs).items() if k.endswith("_SLUG")}
    for mod in (headers, ledger):
        assert {k.lower(): str(v) for k, v in mod.STATUS_MAP.items()} == lg.STATUS_MAP, mod.__name__
    assert set(ledger.ROLES) == set(lg.ROLES)
    assert set(ledger.SCOPE_KINDS) == set(lg.SCOPE_KINDS)
    assert tuple(ledger.MONEY) == lg.METRICS
    assert set(headers.COLUMNS) == set(lg.COLUMNS)
    assert ledger.TOLERANCE_CENTS == lg.TOLERANCE_CENTS
    assert set(eval_hook._LOSS_WARNINGS) == {lg.WARN_INCURRED, lg.WARN_REPEATED}
    for message, kind in (("Ashgrove 2025 prints no totals: nothing to reconcile", lg.NOTICE_NO_TOTALS),
                          ("No valuation date found in a.csv", lg.NOTICE_NO_VALUATION)):
        assert eval_hook.loss_notice_kind(message) == kind, message


def _location(ln, doc):
    """The backend's printed location string (O2): ``p<page>:y<y>`` for a PDF line, ``s<sheet>:r<row>`` for a
    sheet row (sheet = workbook index; a CSV is sheet 0)."""
    loc = ln["location"]
    if loc.get("page") is not None:
        return f"p{loc['page']}:y{int(ln['line_id'].split('#')[1])}"
    names = [s["name"] for s in doc.get("sheets") or []]
    return f"s{names.index(loc['sheet']) if loc['sheet'] in names else 0}:r{loc['row']}"


def _ledger_inputs(case, lr_docs, ledger):
    """build_ledger(rows, documents, totals, decisions) inputs from the truth, as backend/loss_runs/report.py
    builds them from the database: one LedgerRow per truth line (id -> line_id), one LedgerTotal per printed
    total (id -> total_id)."""
    from backend.loss_runs import normalize

    rows, documents, totals = [], [], []
    line_of, total_of = {}, {}
    for i, f in enumerate(case["documents"], 1):
        d = lr_docs[f]
        documents.append(ledger.LedgerDocument(document_id=i, position=i - 1, label=d["file"], carrier=d["carrier"],
                                               valuation_date=d["valuation_date"], letter=chr(64 + i)))
        names = [s["name"] for s in d.get("sheets") or []]
        for ln in d["lines"]:
            loc = ln["location"]
            rid = len(rows) + 1
            line_of[rid] = ln["line_id"]
            rows.append(ledger.LedgerRow(
                id=rid, document_id=i, location=_location(ln, d), claim_number=ln["claim_number"],
                claim_key=normalize.claim_key(ln["claim_number"]), carrier_key=normalize.carrier_key(d["carrier"]),
                status=ln["status"], date_of_loss=ln["date_of_loss"], policy_year=ln["policy_year"],
                policy_number=ln["policy_number"], claimant=ln["claimant"], verify_state=ln["verify"],
                **{m: ln[m] for m in lg.METRICS}, page_no=loc.get("page"),
                sheet_name=loc.get("sheet") if names else None, sheet_row=loc.get("row"),
                line_y=float(int(ln["line_id"].split("#")[1])) if loc.get("page") is not None else None))
        for t in d["printed_totals"]:
            tid = len(totals) + 1
            total_of[tid] = t["total_id"]
            loc = t["location"]
            detail = {"page": loc["page"], "max_y": 1e9} if t["scope_kind"] == "page_subtotal" else {}
            totals.append(ledger.LedgerTotal(
                id=tid, document_id=i, metric=t["metric"], scope_kind=t["scope_kind"], amount=t["amount"],
                location=(f"p{loc['page']}:t{tid}" if loc.get("page") is not None
                          else f"s{names.index(loc['sheet']) if loc['sheet'] in names else 0}:r{loc['row']}:{t['metric']}"),
                scope_value=t["scope_value"], scope_detail=detail, label=t["label"], page_no=loc.get("page"),
                sheet_name=loc.get("sheet") if names else None, sheet_row=loc.get("row"), quote=t["quote"]))
    return rows, documents, totals, line_of, total_of


def test_backend_build_ledger_reproduces_the_truth(lr_cases, lr_docs):
    """The pure build_ledger (D1) over the truth rows gives the truth's roles, totals strip and check
    verdicts (spec section 4 steps 4 to 6 with D5, L5, L6, O3)."""
    ledger = _import_backend("backend.loss_runs.ledger")
    for cid, case in lr_cases.items():
        rows, documents, totals, line_of, total_of = _ledger_inputs(case, lr_docs, ledger)
        led = ledger.build_ledger(rows, documents, totals, ledger.Decisions())
        assert set(led.rows) == set(line_of), cid
        roles = {line_of[rid]: res.role for rid, res in led.rows.items()}
        want = {lid: lg.expected_role(case, lid) for lid in line_of.values()}
        assert roles == want, (cid, {k: (roles[k], want[k]) for k in want if roles[k] != want[k]})
        strip = led.totals
        got = {"claims": strip.claims, "open": strip.open_claims, **{m: getattr(strip, m).cents for m in lg.METRICS}}
        assert got == case["totals"], cid
        verdicts = {}
        for c in led.checks:
            if c.kind != "total":
                continue
            ev = next(e for e in c.evidence if e.printed_total_id is not None)
            verdicts[total_of[ev.printed_total_id]] = (c.verdict, c.computed, c.difference)
        assert verdicts == {c["total_id"]: (c["verdict"], c["computed"], c["difference"]) for c in case["checks"]}, cid
        # Warnings (section 4 step 4, D5) on the lines the truth flags.
        flags = {line_of[rid]: sorted(set(res.flags) & {lg.WARN_INCURRED, lg.WARN_REPEATED})
                 for rid, res in led.rows.items()}
        for f in case["documents"]:
            for ln in lr_docs[f]["lines"]:
                assert flags[ln["line_id"]] == sorted(ln["warnings"]), ln["line_id"]

def _respond_factory(case_docs, providers, *, swap: str | None = None):
    """A fake provider answering every loss-run call from the truth (ASSUMED call roles and reply shapes,
    see loss_run_gate 'Fake model replies'; reconcile here after lane A merges)."""
    by_file = {d["file"]: d for d in case_docs}

    def respond(req):
        text = "\n".join(p.text for m in req.messages if m.role == "user" for p in m.parts() if hasattr(p, "text"))
        role = getattr(req.role, "name", str(req.role)).upper()
        name_m = lg.FILE_NAME.search(text)
        doc = by_file.get(name_m.group(1)) if name_m else None
        if role == "VISION":
            n = re.search(r"Page (\d+)\.", text).group(1)
            scanned = next(d for d in case_docs if d["scan_transcript"])
            return scanned["scan_transcript"][n]
        if "HEADER" in role:
            docs = [doc] if doc else case_docs
            heads = [h for d in docs for s in d.get("sheets") or [] for h in s.get("columns") or {} if h in text]
            return lg.fake_header_reply(docs[0], heads) if docs else {"mappings": []}
        if "ROW" in role or "LOSS" in role:
            reply = lg.fake_row_reply(doc, [int(n) for n in lg.PAGE_MARK.findall(text)])
            return lg.swap_paid_reserve(reply, swap) if swap else reply
        return lg.fake_fact_reply(doc) if doc else {"facts": [], "options_detected": 1, "option_compared": None}

    return respond


def _hook_run(case, lr_docs, paths, swap=None):
    eval_hook = _import_backend("backend.eval_hook")
    providers = _import_backend("backend.providers")
    _import_backend("backend.loss_runs.ledger")
    p = _paths_for(case, paths)
    try:
        out = eval_hook.run_eval_batch([p[f] for f in case["documents"]], workflow=case["workflow"],
                                       parameters=lg.adapter_parameters(case),
                                       provider=providers.FakeProvider(_respond_factory(
                                           [lr_docs[f] for f in case["documents"]], providers, swap=swap)))
    except (NotImplementedError, ValueError) as exc:
        pytest.skip(f"backend.eval_hook has no loss_run branch yet: {exc}")
    return out, p


def test_backend_end_to_end_with_a_perfect_fake_model(lr_cases, lr_docs, paths):
    """End to end offline: the eval hook runs the loss_run workflow over every file with a fake model that
    returns the printed rows and totals; the gate must pass with every verdict and role right."""
    case = lr_cases["lr_main"]
    out, p = _hook_run(case, lr_docs, paths)
    sc = lg.score_loss_run_case(case, lr_docs, out, p)
    m, rep = _gate(sc)
    assert rep.passed, lg.format_loss_run_report(m, rep)
    assert m.sheet_row_accuracy == 1.0 and m.checks_right == m.checks_expected


def test_backend_leaves_a_swapped_reply_unverified(lr_cases, lr_docs, paths):
    """O1 through the real verify: the fake model swaps paid and reserve for DS-25-0019 (layout B prints
    Reserve first); the column check must keep the row unverified and out of the totals."""
    case = lr_cases["lr_main"]
    out, p = _hook_run(case, lr_docs, paths, swap="DS-25-0019")
    sc = lg.score_loss_run_case(case, lr_docs, out, p)
    assert sc.hard_fails == [], sc.hard_fails
    row = next(r for r in out["loss_run"]["rows"] if lg.claim_key(r["claim_number"]) == "DS-25-0019")
    assert row["verify"] == "unverified"


def _replay_run(case, paths, replies):
    eval_hook = _import_backend("backend.eval_hook")
    _import_backend("backend.providers")
    p = _paths_for(case, paths)
    out = eval_hook.run_eval_batch([p[f] for f in case["documents"]], workflow=case["workflow"],
                                   parameters=lg.adapter_parameters(case), provider=rr.replay_provider(replies))
    return out, p


def test_recorder_round_trip_replays_a_run_exactly(lr_cases, lr_docs, paths):
    """Record a fake-model run keyed by request fingerprint, then replay it in a second run: every request
    of the second run must find its reply (keys are stable across runs despite concurrent windows) and the
    output must match. This is what makes a recorded live run replayable offline."""
    providers = _import_backend("backend.providers")
    eval_hook = _import_backend("backend.eval_hook")
    case = lr_cases["lr_main"]
    p = _paths_for(case, paths)
    store: dict[str, list[dict]] = {}
    fake = providers.FakeProvider(_respond_factory([lr_docs[f] for f in case["documents"]], providers))
    first = eval_hook.run_eval_batch([p[f] for f in case["documents"]], workflow=case["workflow"],
                                     parameters=lg.adapter_parameters(case), provider=rr.record_into(fake, store))
    assert store, "the run made no model call to record"
    second, _ = _replay_run(case, paths, json.loads(json.dumps(store)))  # through JSON, like a file
    def rows(out):  # documents finish concurrently, so row order varies between any two runs
        return sorted(json.dumps(r, sort_keys=True, default=str) for r in out["loss_run"]["rows"])

    assert rows(second) == rows(first)
    m, rep = _gate(lg.score_loss_run_case(case, lr_docs, second, p))
    assert rep.passed, lg.format_loss_run_report(m, rep)


def test_recorded_live_replies_replay(lr_cases, lr_docs, paths):
    """Replays ``recorded/loss_run_live_*.json`` (every model reply of an approved live run) offline, like
    test_certificate_replay. Skips until the first live run is recorded."""
    recorded = sorted(RECORDED_DIR.glob("loss_run_live_*.json"))
    if not recorded:
        pytest.skip("no recorded live loss_run run yet (tests/eval/recorded/loss_run_live_<date>.json)")
    data = json.loads(recorded[-1].read_text(encoding="utf-8"))
    assert data.get("format") == rr.FORMAT, recorded[-1]
    for cid, rec in data["cases"].items():
        case = lr_cases[cid]
        out, p = _replay_run(case, paths, rec["replies"])
        m, rep = _gate(lg.score_loss_run_case(case, lr_docs, out, p))
        assert rep.passed, f"{recorded[-1].name} {cid}\n" + lg.format_loss_run_report(m, rep)


# ---------------------------------------------------------------------------
# Live gate (pytest -m live tests/eval/test_loss_run_eval.py -k loss_run); paid, owner approves each run
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_live_loss_run_gates(lr_cases, lr_docs, tmp_path, capsys):
    """Loss-run gate (spec section 8): one run per case through the ``loss_run`` workflow. 0 hard fails,
    sheet rows 1.000, PDF recall >= 0.95, verified amounts >= 0.99. ``BROKERDOCS_EVAL_LOSS_RUN_CASES``
    selects a subset (e.g. ``lr_main`` to leave out the 120-page file); the JSON report goes to
    ``$BROKERDOCS_EVAL_LOSS_RUN_REPORT`` when set. The report lists input tokens per document: set
    ``loss_run_gate.LOSS_RUN_DOC_TOKEN_BUDGET`` from them (L8)."""
    from eval_adapter import AdapterUnavailable, load_adapter

    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    try:
        adapter = load_adapter()
    except AdapterUnavailable as exc:
        pytest.skip(f"eval adapter unavailable: {exc}")
    record = os.environ.get("BROKERDOCS_EVAL_RECORD") == "1"
    recorded: dict[str, dict[str, list[dict]]] = {}
    if record:
        case_of = {frozenset(str(LR_DIR / lr_docs[f]["file"]) for f in c["documents"]): cid
                   for cid, c in lr_cases.items()}
        live_adapter = adapter

        def adapter(paths_, **kw):  # noqa: F811  records every reply of this case
            store = recorded.setdefault(case_of[frozenset(paths_)], {})
            prov = rr.recording_provider(store)
            try:
                return live_adapter(paths_, provider=prov, **kw)
            finally:
                rr.close_provider(prov)
    try:
        metrics, gates = lg.run_loss_run_suite(adapter, LR_DIR, lr_cases, lr_docs)
    except NotImplementedError as exc:
        pytest.skip(f"loss_run eval not runnable yet: {exc}")
    except ValueError as exc:
        if "unknown eval workflow" in str(exc):
            pytest.skip(f"backend.eval_hook has no loss_run branch yet: {exc}")
        raise
    report = lg.format_loss_run_report(metrics, gates)
    target = Path(os.environ.get("BROKERDOCS_EVAL_LOSS_RUN_REPORT") or tmp_path / "loss_run_report.json")
    target.write_text(json.dumps(lg.loss_run_report_dict(metrics, gates), indent=2, default=str), encoding="utf-8")
    with capsys.disabled():
        print("\n" + report + f"\nreport: {target}")
        if record:
            import datetime

            from backend.pipeline.cache import EXTRACTION_VERSION

            day = datetime.date.today().isoformat()
            n = len(list(RECORDED_DIR.glob(f"loss_run_live_{day}*.json")))
            out = RECORDED_DIR / f"loss_run_live_{day}{'' if n == 0 else chr(ord('a') + n)}.json"
            rr.write_recording(out, recorded, {"extraction_version": EXTRACTION_VERSION, "passed": gates.passed,
                                               "synthetic": "Synthetic sample data (tests/fixtures/loss_runs)"})
            print(f"recorded: {out} ({sum(len(v) for r in recorded.values() for v in r.values())} replies)")
    assert gates.passed, "loss_run gate failed:\n" + report
    assert len(metrics.hard_fails) == 0
