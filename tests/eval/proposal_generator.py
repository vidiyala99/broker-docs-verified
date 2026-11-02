"""Synthetic multi-quote fixtures for the Proposal Builder eval (slice 2, task T1).

Each *proposal fixture* is one proposal run: two or three carrier quotes for
the same client (a Cyber and Technology E&O program), the wizard parameters,
and cell-level ground truth. A cell is one (document, field) pair of the
proposal's comparison tables, so the truth of every document's fields is the
truth of every table cell in that document's column.

Rendering reuses ``quote_generator`` (same PyMuPDF calls, fixed metadata,
``no_new_id``), so the same seed and PyMuPDF 1.28.0 give byte-identical PDFs.
The files live in their own folder with their own manifest so the slice 1
catalogue (and its byte hashes) is untouched.

Fields: the slice 1 eval fields plus the per-coverage facts the cyber and E&O
sub-tables need (``PROPOSAL_ONLY_FIELDS``). These keys are the eval's half of
the contract with the ``proposal-builder`` fact schema (lane A); the scorer is
fail-closed for proposal fixtures, so a backend field missing from this list
is reported and fails the gate until the truth lists it.

Usage::

    python tests/eval/proposal_generator.py --out tests/fixtures/proposals

All names (carriers, insureds, brokers) are fictional.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import fitz  # PyMuPDF

import quote_generator as qg
from quote_generator import MARGIN, Cursor, Fixture, PageSpec, Run

GENERATOR_VERSION = 1
DEFAULT_SEED = 20261102
TRUTH_SCHEMA_VERSION = 1
GATE_BUCKET = "proposal"
WORKFLOW = "proposal-builder"
FIXED_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "proposals"

# Per-coverage facts for the cyber and technology E&O sub-tables. Proposed
# backend FactField keys for the proposal-builder fact schema (spec section 2.1).
PROPOSAL_ONLY_FIELDS: dict[str, str] = {
    "minimum_earned_premium": "Minimum earned premium, as an amount or percent",
    "retroactive_date": "Retroactive date for claims-made coverage, or Full Prior Acts",
    "tech_eo_limit": "Technology errors and omissions liability limit, each claim",
    "tech_eo_retention": "Technology errors and omissions liability retention, each claim",
    "cyber_liability_limit": "Network security and privacy liability limit, each claim",
    "cyber_liability_retention": "Network security and privacy liability retention, each claim",
    "cyber_crime_limit": "Cyber crime (funds transfer fraud, social engineering) sublimit",
    "cyber_crime_retention": "Cyber crime retention",
}
FIELDS: dict[str, str] = {**qg.FIELDS, **PROPOSAL_ONLY_FIELDS}

# Backend fact fields that exist in the Compare Quotes schema but have no cell
# truth here (free-text lists). Listed so the fail-closed scorer knows them;
# their cells are still checked for an uncited value and a citation into the
# wrong document.
UNSCORED_FIELDS: dict[str, str] = {
    "key_endorsements": "free-text list, not scored",
    "key_exclusions": "free-text list, not scored",
    "subjectivities": "free-text list, not scored",
}

# Fields a cyber or E&O quote does not state as one policy-wide figure. Truth is
# null: a value shown in these rows would put a sub-coverage figure (for
# example a retention of one insuring agreement) in a policy-wide row.
POLICY_WIDE_NULL = ("each_occurrence_limit", "retention", "rate")

LABELS: dict[str, str] = {
    **qg.LABELS,
    "aggregate_limit": "Maximum Policy Aggregate Limit",
    "minimum_earned_premium": "Minimum Earned Premium",
    "retroactive_date": "Retroactive Date",
    "tech_eo_limit": "Technology Errors and Omissions Liability Limit",
    "tech_eo_retention": "Technology Errors and Omissions Liability Retention",
    "cyber_liability_limit": "Network Security and Privacy Liability Limit",
    "cyber_liability_retention": "Network Security and Privacy Liability Retention",
    "cyber_crime_limit": "Cyber Crime Sublimit",
    "cyber_crime_retention": "Cyber Crime Retention",
}

DECL = ["named_insured", "address", "producer", "carrier_name", "admitted_status",
        "am_best_rating", "policy_term", "policy_form", "territory", "retroactive_date"]
PREMIUM = ["premium", "taxes_and_fees", "total_premium", "minimum_earned_premium"]
AGREEMENTS = ["tech_eo_limit", "tech_eo_retention", "cyber_liability_limit",
              "cyber_liability_retention", "cyber_crime_limit", "cyber_crime_retention"]

FORMS = [
    "TEC 100 07 24 Technology Errors and Omissions and Cyber Liability Policy (Claims-Made)",
    "CYB 2000 01 25 Cyber and Technology Professional Liability Coverage Form",
]
SUBJECTIVITIES = [
    "Signed and dated application",
    "Confirmation that multi-factor authentication is enabled for remote access and email",
    "Description of offline backup procedures, tested within the last six months",
    "Signed statement of no known circumstances",
]
FILLER = [
    "This quotation is based on the application and supplemental cyber questionnaire "
    "submitted by the producer and is subject to the terms of the policy as issued.",
    "Coverage is written on a claims-made and reported basis. Claims must be first made "
    "against the insured and reported to the carrier during the policy period.",
    "Defense costs are within and reduce the limits of liability. Each retention applies "
    "to damages and claim expenses for each claim under the insuring agreement shown.",
    "Subjectivities must be received within thirty days of binding. Failure to satisfy them "
    "may result in cancellation of the policy for underwriting reasons.",
]


@dataclass(frozen=True)
class CarrierSpec:
    letter: str
    name: str
    admitted: bool
    layout: str  # clean | table
    retro: str
    limits: dict[str, str]
    absent: tuple[str, ...] = ()
    rating: str = "A (Excellent), XII"
    meprem: str = "25%"
    amended: dict | None = None  # {"field", "declared", "amended", "endorsement_no"}


@dataclass(frozen=True)
class ProposalSpec:
    proposal_id: str
    template: str
    insured: tuple[str, str]
    description: str
    carriers: tuple[CarrierSpec, ...]
    no_ai_prose: bool = False
    additional_context: str = ""


INSUREDS = [
    ("Halcyon Data Systems LLC", "2210 Brookfield Parkway, Suite 400, Madison, WI 53703"),
    ("Orchard Ridge Software Inc.", "77 Millbrook Street, Floor 3, Burlington, VT 05401"),
    ("Pinecrest Analytics Corp.", "4100 Wexford Lane, Building B, Boise, ID 83702"),
]
BROKER = "Dana Whitfield"
BROKERAGE = "Meridian Risk Partners LLC"

PROPOSALS: tuple[ProposalSpec, ...] = (
    ProposalSpec(
        "prop_three_carrier", "standard", INSUREDS[0],
        "Three carriers quoting one Cyber and Technology E&O program; two single-column "
        "quotes and one whose insuring agreement schedule is a two-column table.",
        (
            CarrierSpec("a", "Tidewater Cyber Insurance Company", False, "clean", "Full Prior Acts",
                        {"aggregate_limit": "$3,000,000", "tech_eo_limit": "$2,000,000",
                         "tech_eo_retention": "$25,000", "cyber_liability_limit": "$1,000,000",
                         "cyber_liability_retention": "$15,000", "cyber_crime_limit": "$250,000",
                         "cyber_crime_retention": "$10,000"}),
            CarrierSpec("b", "Lakemont Specialty Indemnity Company", True, "clean", "03/01/2019",
                        {"aggregate_limit": "$5,000,000", "tech_eo_limit": "$5,000,000",
                         "tech_eo_retention": "$50,000", "cyber_liability_limit": "$3,000,000",
                         "cyber_liability_retention": "$35,000", "cyber_crime_limit": "$500,000",
                         "cyber_crime_retention": "$20,000"},
                        rating="A+ (Superior), XIV", meprem="35%"),
            CarrierSpec("c", "Brassridge Technology Insurance Company", True, "table",
                        "January 1, 2021",
                        {"aggregate_limit": "$4,000,000", "tech_eo_limit": "$3,000,000",
                         "tech_eo_retention": "$30,000", "cyber_liability_limit": "$2,000,000",
                         "cyber_liability_retention": "$20,000", "cyber_crime_limit": "$150,000",
                         "cyber_crime_retention": "$7,500"},
                        rating="A- (Excellent), IX", meprem="$2,500"),
        ),
        additional_context="The client asked for a side-by-side view of the cyber crime coverage.",
    ),
    ProposalSpec(
        "prop_two_carrier_absent", "premier", INSUREDS[1],
        "Two carriers, no-AI-prose mode. One quote states no cyber crime coverage and no AM "
        "Best rating (null cells that must show no value); the other is a two-column table.",
        (
            CarrierSpec("d", "Quarrystone Surplus Insurance Company", False, "table", "06/15/2020",
                        {"aggregate_limit": "$2,000,000", "tech_eo_limit": "$1,000,000",
                         "tech_eo_retention": "$10,000", "cyber_liability_limit": "$750,000",
                         "cyber_liability_retention": "$12,500", "cyber_crime_limit": "$100,000",
                         "cyber_crime_retention": "$5,000"},
                        rating="A (Excellent), X", meprem="$1,500"),
            CarrierSpec("e", "Silverfen Casualty Company", True, "clean", "Full Prior Acts",
                        {"aggregate_limit": "$1,000,000", "tech_eo_limit": "$1,000,000",
                         "tech_eo_retention": "$15,000", "cyber_liability_limit": "$500,000",
                         "cyber_liability_retention": "$7,500"},
                        absent=("am_best_rating", "cyber_crime_limit", "cyber_crime_retention"),
                        meprem="30%"),
        ),
        no_ai_prose=True,
    ),
    ProposalSpec(
        "prop_amended", "standard", INSUREDS[2],
        "Two carriers; one quote's declarations state a cyber crime sublimit that a later "
        "endorsement replaces (truth is the amended figure, the declared one is a decoy).",
        (
            CarrierSpec("f", "Kestrel Bay Assurance Company", True, "clean", "09/01/2018",
                        {"aggregate_limit": "$3,000,000", "tech_eo_limit": "$3,000,000",
                         "tech_eo_retention": "$40,000", "cyber_liability_limit": "$2,000,000",
                         "cyber_liability_retention": "$25,000", "cyber_crime_limit": "$500,000",
                         "cyber_crime_retention": "$15,000"},
                        rating="A (Excellent), XI", meprem="25%",
                        amended={"field": "cyber_crime_limit", "declared": "$250,000",
                                 "amended": "$500,000", "endorsement_no": 2}),
            CarrierSpec("g", "Fernhollow Mutual Insurance Company", False, "clean", "11/01/2022",
                        {"aggregate_limit": "$2,000,000", "tech_eo_limit": "$2,000,000",
                         "tech_eo_retention": "$20,000", "cyber_liability_limit": "$1,500,000",
                         "cyber_liability_retention": "$17,500", "cyber_crime_limit": "$200,000",
                         "cyber_crime_retention": "$12,000"},
                        rating="A- (Excellent), VIII", meprem="40%"),
        ),
        additional_context="Keep the executive summary short and plain.",
    ),
)


# ---------------------------------------------------------------------------
# Profiles and pages
# ---------------------------------------------------------------------------


def build_profile(rng: random.Random, spec: CarrierSpec, idx: int,
                  insured: tuple[str, str]) -> dict[str, str | None]:
    """Field -> value text exactly as printed (or None when absent)."""
    premium = Decimal(rng.randrange(6000, 42000, 25))
    if spec.admitted:
        fees = Decimal(rng.choice([150, 250, 350]))
    else:
        fees = (premium * Decimal("0.0485")).quantize(Decimal("0.01")) + Decimal(100)
    total = premium + fees
    profile: dict[str, str | None] = {
        "named_insured": insured[0],
        "address": insured[1],
        "carrier_name": spec.name,
        "admitted_status": "Admitted" if spec.admitted else "Non-Admitted",
        "am_best_rating": spec.rating,
        "policy_term": qg.PERIOD_FORMATS[idx % len(qg.PERIOD_FORMATS)],
        "territory": "Worldwide, provided the claim is brought in the United States",
        "producer": BROKERAGE,
        "policy_form": FORMS[idx % len(FORMS)],
        "premium": qg._money(premium, idx % 2 == 0),
        "taxes_and_fees": qg._money(fees, True),
        "total_premium": qg._money(total, True),
        "quote_expiration": qg.VALID_UNTIL_FORMATS[idx % len(qg.VALID_UNTIL_FORMATS)],
        "minimum_earned_premium": spec.meprem,
        "retroactive_date": spec.retro,
        **spec.limits,
    }
    for f in POLICY_WIDE_NULL:
        profile[f] = None
    for f in spec.absent:
        profile[f] = None
    return profile


def _fact(c: Cursor, profile: dict, f: str) -> None:
    v = profile.get(f)
    if v is None:
        return
    scored = f in FIELDS  # "premium" is printed but not scored (no backend field)
    c.line(f"{LABELS[f]}: {v}", field_name=f if scored else None, value=v if scored else None)


def _header(c: Cursor, carrier: str, page_no: int, n_pages: int) -> None:
    c.line(carrier, size=13, bold=True)
    c.line(f"Cyber and Technology E&O Quote Proposal    Page {page_no} of {n_pages}", size=9)
    c.y += 10


def _agreements_clean(c: Cursor, profile: dict, amended: dict | None) -> None:
    c.line("Insuring Agreements", size=12, bold=True)
    c.y += 4
    for f in AGREEMENTS:
        if amended and f == amended["field"]:
            # Declared figure, later replaced by an endorsement: printed, not tagged.
            c.line(f"{LABELS[f]} (per Declarations): {amended['declared']}")
            continue
        _fact(c, profile, f)


def _agreements_table(c: Cursor, profile: dict) -> list[Run]:
    """Two-column schedule: labels left, values right on shared baselines, value
    column first in content-stream order (extraction order differs from reading order)."""
    c.line("SCHEDULE OF INSURING AGREEMENTS", size=11, bold=True)
    c.y += 6
    label_runs, value_runs = [], []
    y = c.y
    for f in AGREEMENTS:
        v = profile.get(f)
        if v is None:
            continue
        label_runs.append(Run(MARGIN, y, LABELS[f]))
        value_runs.append(Run(420.0, y, v, field=f, value=v))
        y += 16
    c.y = y + 8
    return value_runs + label_runs


def carrier_pages(profile: dict, spec: CarrierSpec) -> list[PageSpec]:
    n = 4 if spec.amended else 3
    p1 = Cursor()
    _header(p1, spec.name, 1, n)
    p1.line("Declarations", size=12, bold=True)
    p1.y += 4
    for f in DECL:
        _fact(p1, profile, f)
    p1.y += 10
    p1.para(FILLER[0])
    p1.para(FILLER[1])

    p2 = Cursor()
    _header(p2, spec.name, 2, n)
    p2.line("Premium Summary", size=12, bold=True)
    p2.y += 4
    for f in PREMIUM:
        _fact(p2, profile, f)
    p2.y += 10
    p2.line("Limits of Liability", size=12, bold=True)
    p2.y += 4
    _fact(p2, profile, "aggregate_limit")
    p2.y += 8
    extra: list[Run] = []
    if spec.layout == "table":
        extra = _agreements_table(p2, profile)
    else:
        _agreements_clean(p2, profile, spec.amended)
    p2.y += 10
    p2.para(FILLER[2])
    p2_runs = p2.runs[:]
    if extra:
        # Header and limits first, then the value column, then labels, then the tail paragraph.
        head = [r for r in p2.runs if r.y < extra[0].y]
        tail = [r for r in p2.runs if r.y >= extra[0].y]
        p2_runs = head + extra + tail

    p3 = Cursor()
    _header(p3, spec.name, 3, n)
    p3.line("Subjectivities and Binding Terms", size=12, bold=True)
    p3.y += 4
    for i, s in enumerate(SUBJECTIVITIES, 1):
        p3.line(f"{i}. {s}")
    p3.y += 8
    _fact(p3, profile, "quote_expiration")
    p3.y += 8
    p3.para(FILLER[3])
    pages = [PageSpec(runs=p1.runs), PageSpec(runs=p2_runs), PageSpec(runs=p3.runs)]

    if spec.amended:
        a = spec.amended
        p4 = Cursor()
        _header(p4, spec.name, 4, n)
        p4.line(f"Endorsement No. {a['endorsement_no']}: Amendment of {LABELS[a['field']]}",
                size=12, bold=True)
        p4.y += 4
        p4.line("This endorsement modifies insurance provided under the policy form shown in "
                "the Declarations.", size=9)
        p4.line(f"The {LABELS[a['field']]} shown in the Declarations is deleted and replaced "
                "with the following:", size=9)
        p4.line(f"Amended {LABELS[a['field']]}: {a['amended']} each claim",
                field_name=a["field"], value=a["amended"])
        pages.append(PageSpec(runs=p4.runs))
    return pages


# ---------------------------------------------------------------------------
# Fixtures and truth
# ---------------------------------------------------------------------------


@dataclass
class DocFixture:
    fx: Fixture
    proposal_id: str
    column: str  # document letter in the run, A to H


def doc_fixture_id(proposal_id: str, letter: str) -> str:
    return f"{proposal_id}_{letter}"


def build_doc_fixtures(seed: int = DEFAULT_SEED) -> list[DocFixture]:
    out: list[DocFixture] = []
    for p_idx, p in enumerate(PROPOSALS):
        for c_idx, spec in enumerate(p.carriers):
            idx = p_idx * 3 + c_idx
            prof = build_profile(random.Random(seed * 37 + idx), spec, idx, p.insured)
            acc = qg._accepted(prof)
            decoys = []
            notes = ""
            if spec.amended:
                decoys.append({"field": spec.amended["field"], "value": spec.amended["declared"],
                               "kind": "superseded"})
                notes = "Effective value chosen as truth: a later endorsement amends the declarations."
            absent = sorted(set(POLICY_WIDE_NULL) | set(spec.absent))
            fx = Fixture(
                doc_fixture_id(p.proposal_id, spec.letter), f"proposal_{spec.layout}", GATE_BUCKET,
                f"{spec.name} Cyber and Technology E&O quote for {p.insured[0]} ({spec.layout} "
                f"layout). Absent fields (must show no value): {', '.join(absent)}.",
                carrier_pages(prof, spec), carrier=spec.name, accepted_values=acc,
                decoys=decoys, notes=notes)
            out.append(DocFixture(fx, p.proposal_id, chr(ord("A") + c_idx)))
    return out


def build_doc_truth(df: DocFixture, seed: int, pdf: bytes) -> dict:
    """Same shape as quote_generator truth files, over the proposal FIELDS, plus the
    fail-closed flag and the unscored field list the scorer reads."""
    fx = df.fx
    texts = qg.page_texts(fx)
    facts: dict[str, dict | None] = {f: None for f in FIELDS}
    for pno, spec in enumerate(fx.pages, 1):
        for r in spec.runs:
            if not r.field:
                continue
            if r.field not in FIELDS:
                raise ValueError(f"{fx.fixture_id}: unknown field {r.field}")
            if r.value is None or r.value not in r.text:
                raise ValueError(f"{fx.fixture_id}: {r.field} value not inside its quote")
            if facts[r.field] is not None:
                raise ValueError(f"{fx.fixture_id}: field {r.field} tagged twice")
            facts[r.field] = {
                "value": r.value,
                "page": pno,
                "pages": [i for i, t in enumerate(texts, 1) if r.value in t],
                "quote": r.text,
                "source": "text",
                "accepted_values": fx.accepted_values.get(r.field, []),
            }
    for d in fx.decoys:
        d.setdefault("pages", [i for i, t in enumerate(texts, 1) if d["value"] in t])
    return {
        "schema_version": TRUTH_SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "fixture_id": fx.fixture_id,
        "proposal_id": df.proposal_id,
        "column": df.column,
        "file": f"{fx.fixture_id}.pdf",
        "variant": fx.variant,
        "gate_bucket": fx.gate_bucket,
        "description": fx.description,
        "carrier": fx.carrier,
        "page_count": len(fx.pages),
        "size_bytes": len(pdf),
        "expect": {"parse": "ok", "failure_reason": None, "ocr_pages": [], "option_count": 1},
        "fail_closed": True,
        "unscored_fields": UNSCORED_FIELDS,
        "facts": facts,
        "decoys": fx.decoys,
        "notes": fx.notes,
    }


def proposal_truth(p: ProposalSpec, seed: int) -> dict:
    return {
        "schema_version": TRUTH_SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "proposal_id": p.proposal_id,
        "workflow": WORKFLOW,
        "gate_bucket": GATE_BUCKET,
        "description": p.description,
        "template": p.template,
        # Wizard step 2 values; the adapter adds template_id and outline (spec section 2).
        "parameters": {
            "broker_name": BROKER,
            "brokerage_name": BROKERAGE,
            "additional_context": p.additional_context,
            "no_ai_prose": p.no_ai_prose,
        },
        "documents": [doc_fixture_id(p.proposal_id, c.letter) for c in p.carriers],
        "scored_fields": list(FIELDS),
        "unscored_fields": UNSCORED_FIELDS,
        "fail_closed": True,
    }


def generate(out_dir: Path, seed: int = DEFAULT_SEED) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "pymupdf_version": fitz.VersionBind,
        "fields": FIELDS,
        "unscored_fields": UNSCORED_FIELDS,
        "proposals": [],
        "documents": [],
    }
    for p in PROPOSALS:
        t = proposal_truth(p, seed)
        name = f"{p.proposal_id}.proposal.json"
        (out_dir / name).write_text(json.dumps(t, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8", newline="\n")
        manifest["proposals"].append({"proposal_id": p.proposal_id, "truth": name,
                                      "documents": t["documents"], "description": p.description})
    for i, df in enumerate(build_doc_fixtures(seed)):
        pdf = qg.render_pdf(df.fx, random.Random(seed * 1013 + i))
        truth = build_doc_truth(df, seed, pdf)
        (out_dir / truth["file"]).write_bytes(pdf)
        (out_dir / f"{df.fx.fixture_id}.truth.json").write_text(
            json.dumps(truth, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        manifest["documents"].append({
            "fixture_id": df.fx.fixture_id,
            "proposal_id": df.proposal_id,
            "file": truth["file"],
            "truth": f"{df.fx.fixture_id}.truth.json",
            "variant": df.fx.variant,
            "gate_bucket": GATE_BUCKET,
            "sha256": hashlib.sha256(pdf).hexdigest(),
            "deterministic_bytes": True,
            "description": df.fx.description,
        })
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=FIXED_DIR)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)
    m = generate(args.out, args.seed)
    print(f"wrote {len(m['proposals'])} proposals ({len(m['documents'])} documents) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
