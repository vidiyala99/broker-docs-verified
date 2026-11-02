"""Offline tests for the synthetic quote generator and the committed fixtures."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import fitz
import pytest

import quote_generator as qg

EXPECTED_IDS = {
    *(f"carrier_{c}_{v}" for c in "abcd" for v in ("clean", "multicol", "scanned")),
    "multi_option", "conflicting_endorsement", "injection",
    "parse_sparse", "parse_mixed", "password_protected",
    "corrupt_garbage", "corrupt_truncated", "not_a_pdf", "over_cap_pages",
}


@pytest.fixture(scope="module")
def fresh(tmp_path_factory) -> tuple[Path, dict]:
    out = tmp_path_factory.mktemp("gen")
    return out, qg.generate(out)


def _open(path: Path) -> fitz.Document:
    return fitz.open(path)


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _image_coverage(page: fitz.Page) -> float:
    area = 0.0
    for info in page.get_image_info():
        r = fitz.Rect(info["bbox"]) & page.rect
        area += r.width * r.height
    return area / (page.rect.width * page.rect.height)


# ---------------------------------------------------------------------------
# Catalogue and determinism
# ---------------------------------------------------------------------------

def test_catalogue_is_complete(manifest):
    ids = {e["fixture_id"] for e in manifest["fixtures"]}
    assert ids == EXPECTED_IDS
    buckets = {e["fixture_id"]: e["gate_bucket"] for e in manifest["fixtures"]}
    assert sum(b == "clean" for b in buckets.values()) == 4
    assert sum(b == "multi_column" for b in buckets.values()) == 4
    assert sum(b == "scanned" for b in buckets.values()) == 4


def test_same_seed_same_bytes(fresh, tmp_path):
    _, m1 = fresh
    m2 = qg.generate(tmp_path)
    for a, b in zip(m1["fixtures"], m2["fixtures"]):
        if a["deterministic_bytes"]:
            assert a["sha256"] == b["sha256"], a["fixture_id"]


def test_password_fixture_content_is_deterministic(fresh, tmp_path):
    out1, _ = fresh
    qg.generate(tmp_path)
    texts = []
    for d in (out1, tmp_path):
        doc = _open(d / "password_protected.pdf")
        assert doc.authenticate("synthetic-quote")
        texts.append([p.get_text() for p in doc])
    assert texts[0] == texts[1]


def test_different_seed_changes_values(tmp_path):
    a = qg.build_fixtures(1)
    b = qg.build_fixtures(2)
    ta = qg.page_texts(next(f for f in a if f.fixture_id == "carrier_a_clean"))
    tb = qg.page_texts(next(f for f in b if f.fixture_id == "carrier_a_clean"))
    assert ta != tb


def test_committed_fixtures_match_generator(fresh, manifest, fixture_dir):
    """Drift guard: the committed PDFs and truth files are what the generator
    produces for the default seed."""
    out, m = fresh
    assert manifest["seed"] == qg.DEFAULT_SEED
    assert manifest["generator_version"] == qg.GENERATOR_VERSION
    same_lib = manifest["pymupdf_version"] == fitz.VersionBind
    for e, f in zip(manifest["fixtures"], m["fixtures"]):
        committed = (fixture_dir / e["file"]).read_bytes()
        assert hashlib.sha256(committed).hexdigest() == e["sha256"] or not e["deterministic_bytes"]
        if same_lib and e["deterministic_bytes"]:
            assert e["sha256"] == f["sha256"], f"{e['fixture_id']}: regenerate fixtures"
        t_committed = json.loads((fixture_dir / e["truth"]).read_text(encoding="utf-8"))
        t_fresh = json.loads((out / f["truth"]).read_text(encoding="utf-8"))
        t_committed.pop("size_bytes"), t_fresh.pop("size_bytes")
        assert t_committed == t_fresh, e["fixture_id"]


# ---------------------------------------------------------------------------
# Truth schema
# ---------------------------------------------------------------------------

def test_truth_schema(truths):
    for fid, t in truths.items():
        assert t["schema_version"] == qg.TRUTH_SCHEMA_VERSION
        assert set(t["facts"]) == set(qg.FIELDS), fid
        assert t["gate_bucket"] in {"clean", "multi_column", "scanned", "report_only", "parse_only"}
        for f, fact in t["facts"].items():
            if fact is None:
                continue
            assert fact["value"] in fact["quote"], (fid, f)  # M2 holds in truth
            assert fact["page"] in fact["pages"], (fid, f)
            assert 1 <= fact["page"] <= t["page_count"], (fid, f)
            assert fact["source"] in {"text", "image"}


def test_no_em_dashes_in_fixture_text(truths, fixture_dir):
    for fid, t in truths.items():
        assert "\u2014" not in json.dumps(t, ensure_ascii=False), fid
    for fx in qg.build_fixtures(qg.DEFAULT_SEED):
        assert all("\u2014" not in s for s in qg.page_texts(fx)), fx.fixture_id


def test_absent_fields_per_carrier(truths):
    absent = {f"carrier_{c['letter']}": set(c["absent"]) for c in qg.CARRIERS}
    for prefix, fields in absent.items():
        for v in ("clean", "multicol", "scanned"):
            t = truths[f"{prefix}_{v}"]
            nulls = {k for k, x in t["facts"].items() if x is None}
            assert nulls == fields, (prefix, v)
    assert any(absent.values()), "at least one carrier must exercise null-for-absent (M5)"


def test_policy_period_formats_vary_across_carriers(truths):
    periods = {truths[f"carrier_{c}_clean"]["facts"]["policy_term"]["value"] for c in "abcd"}
    assert len(periods) == 4


# ---------------------------------------------------------------------------
# PDF content agrees with truth
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fid", [f"carrier_{c}_{v}" for c in "abcd" for v in ("clean", "multicol")]
                         + ["multi_option", "conflicting_endorsement", "injection"])
def test_digital_quotes_findable_on_truth_page(fid, truths, fixture_dir):
    t = truths[fid]
    doc = _open(fixture_dir / t["file"])
    assert doc.page_count == t["page_count"]
    for f, fact in t["facts"].items():
        if fact is None:
            continue
        page = doc[fact["page"] - 1]
        assert fact["quote"] in page.get_text(), (fid, f)
        assert page.search_for(fact["value"]), (fid, f)  # what the verifier will call
        assert fact["source"] == "text"
        for other in set(range(1, doc.page_count + 1)) - set(fact["pages"]):
            assert fact["value"] not in doc[other - 1].get_text(), (fid, f, other)


def test_absent_values_do_not_leak_into_text(truths, fixture_dir):
    """A field null in truth must have no labeled line in the document."""
    for c in qg.CARRIERS:
        for v in ("clean", "multicol"):
            t = truths[f"carrier_{c['letter']}_{v}"]
            text = " ".join(p.get_text() for p in _open(fixture_dir / t["file"])).lower()
            for f in c["absent"]:
                assert qg.LABELS[f].lower() not in text, (t["fixture_id"], f)


def test_multicolumn_extraction_order_differs_from_reading_order(fixture_dir):
    text = _open(fixture_dir / "carrier_a_multicol.pdf")[1].get_text()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    first_value = next(i for i, ln in enumerate(lines) if ln.startswith("$"))
    label = lines.index("Annual Premium")
    assert first_value < label


@pytest.mark.parametrize("c", "abcd")
def test_scanned_pages_are_image_only(c, truths, fixture_dir):
    t = truths[f"carrier_{c}_scanned"]
    doc = _open(fixture_dir / t["file"])
    for page in doc:
        assert len(re.sub(r"\s", "", page.get_text())) == 0
        assert _image_coverage(page) > 0.99
    assert t["expect"]["ocr_pages"] == [1, 2, 3]
    assert all(f is None or f["source"] == "image" for f in t["facts"].values())


def _ocr_trigger(page: fitz.Page) -> bool:
    """M3/M8 rule as written in the spec (the backend implements its own)."""
    chars = len(re.sub(r"\s", "", page.get_text()))
    return chars < 25 or (_image_coverage(page) >= 0.40 and chars < 600)


@pytest.mark.parametrize("fid", ["parse_sparse", "parse_mixed"] +
                         [f"carrier_{c}_{v}" for c in "abcd" for v in ("clean", "multicol", "scanned")])
def test_ocr_page_expectations_follow_m3(fid, truths, fixture_dir):
    t = truths[fid]
    doc = _open(fixture_dir / t["file"])
    triggered = [i for i, p in enumerate(doc, 1) if _ocr_trigger(p)]
    assert triggered == t["expect"]["ocr_pages"], fid


def test_mixed_fixture_sits_on_both_sides_of_the_thresholds(fixture_dir):
    doc = _open(fixture_dir / "parse_mixed.pdf")
    chars = [len(re.sub(r"\s", "", p.get_text())) for p in doc]
    cov = [_image_coverage(p) for p in doc]
    assert cov[1] >= 0.40 and 25 <= chars[1] < 600  # OCR by image rule only
    assert cov[2] < 0.40 and chars[2] >= 600        # small logo: no OCR
    assert cov[3] >= 0.40 and chars[3] >= 600       # big image but enough text: no OCR


def test_sparse_and_mixed_image_facts(truths):
    for fid, page in (("parse_sparse", 2), ("parse_mixed", 2)):
        img = [f for f, x in truths[fid]["facts"].items() if x and x["source"] == "image"]
        assert img, fid
        assert all(truths[fid]["facts"][f]["page"] == page for f in img)


def test_password_fixture(fixture_dir, truths):
    doc = _open(fixture_dir / "password_protected.pdf")
    assert doc.needs_pass
    assert truths["password_protected"]["expect"]["failure_reason"] == "password_protected"


@pytest.mark.parametrize("fid", ["corrupt_garbage", "corrupt_truncated"])
def test_corrupt_fixtures_have_magic_but_do_not_open(fid, fixture_dir):
    data = (fixture_dir / f"{fid}.pdf").read_bytes()
    assert data.startswith(b"%PDF-")
    with pytest.raises(Exception):
        fitz.open(stream=data, filetype="pdf")


def test_not_a_pdf_lacks_magic(fixture_dir):
    assert not (fixture_dir / "not_a_pdf.pdf").read_bytes().startswith(b"%PDF-")


def test_over_cap_pages(fixture_dir, truths):
    doc = _open(fixture_dir / "over_cap_pages.pdf")
    assert doc.page_count == qg.CAP_PAGES + 1
    assert truths["over_cap_pages"]["expect"]["cap"] == "pages"


def test_oversize_fixture_on_demand(tmp_path):
    m = qg.generate(tmp_path, include_oversize=True)
    e = next(x for x in m["fixtures"] if x["fixture_id"] == "over_cap_size")
    assert (tmp_path / e["file"]).stat().st_size > qg.CAP_BYTES


def test_multi_option_fixture(truths, fixture_dir):
    t = truths["multi_option"]
    assert t["expect"]["option_count"] == 2 and t["primary_option"] == "Option 1"
    text = _open(fixture_dir / t["file"])[1].get_text()
    for d in t["decoys"]:
        assert d["value"] in text and d["kind"] == "other_option"
        assert d["value"] != t["facts"][d["field"]]["value"]


def test_conflicting_endorsement_fixture(truths):
    t = truths["conflicting_endorsement"]
    ded = t["facts"]["retention"]
    (decoy,) = t["decoys"]
    assert ded["value"] == "$10,000" and ded["page"] == 3
    assert decoy["value"] == "$5,000" and decoy["kind"] == "superseded" and decoy["pages"] == [2]


def test_injection_fixture(truths, fixture_dir):
    t = truths["injection"]
    inj = t["injection"]
    text = _norm_ws(_open(fixture_dir / t["file"])[inj["page"] - 1].get_text())
    assert "ignore all previous instructions" in text
    for f, v in inj["canaries"].items():
        assert v in text
        truth_val = t["facts"][f]["value"] if t["facts"][f] else None
        assert truth_val != v
    assert t["facts"]["territory"] is None


def test_cli_writes_fixtures(tmp_path):
    assert qg.main(["--out", str(tmp_path), "--seed", "5"]) == 0
    m = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert m["seed"] == 5 and len(m["fixtures"]) == len(EXPECTED_IDS)
