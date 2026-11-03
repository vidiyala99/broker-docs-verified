"""Synthetic checked-certificate fixtures with claim ground truth (slice 4, task C10).

SYNTHETIC SAMPLE DATA. Every page carries a footer saying so, the PDF metadata says so, and
every carrier, insured, holder, address, NAIC code and form number is invented (the form
numbers use a made-up "OM" series, not ISO forms).

A *case* is one certificate run (spec docs/specs/2026-11-03-certificates-design.md, section 3,
section 6, ledger D7/D11/D14): the client's policy documents, the holder, the requested wording
and the certificate date. Ground truth has two levels, like the Policy Check fixtures:

* per document (``<case>_<name>.truth.json``): every ``cert:`` fact the document states (value,
  page, pages, quote, source) in the slice 1 truth shape; ``null`` for a key it does not state.
  Endorsement facts use ``cert:endorsement:<FORM>:<attr>`` keys. A package policy's truth has
  every key ``null`` (C7: it contributes no facts) even though its pages print values.
* per case (``<case>.case.json``): the run parameters, the shown policies, every claim the
  checks must produce with its verdict (supported / not_supported) and reason, and the
  notices (package policy, unmatched endorsement). The verdicts are written by hand below and
  ``test_certificate_eval`` re-derives them from the document truths with the spec's rules
  (``certificate_gate.reference_claims``); the two must agree.

Layouts reuse ``quote_generator``: clean single column, two columns whose value column comes
first in extraction order, and scanned (image-only, noisy JPEG). Same seed and PyMuPDF 1.28.0
give byte-identical PDFs.

Cases (see CASES):

* ``cert_contractor``: GL (clean), auto (clean), WC (two-column) declarations; scheduled AI
  naming the holder (upper case: normalized compare) with a policy number printed differently
  from the declarations (D7 join), unconditional WC waiver, unconditional primary and
  non-contributory. Auto in force from the certificate date (effective-day boundary), WC
  expiring on it (expiration-day boundary). AI, waiver and P&NC requested; a description of
  operations (D11 sign-off row).
* ``cert_written_contract``: GL (clean) with a two-form endorsement packet: blanket AI and
  blanket waiver, both "only where required by a written contract" (D14: Not supported, the
  broker must override with a reason); auto (two-column) not yet in force (effective the day
  after the certificate date) with a scheduled AI naming another party.
* ``cert_traps``: GL expired the day before the certificate date with an unconditional blanket
  AI (the form alone supports the AI claim); auto under a different named insured (insured
  mismatch) with an AI endorsement whose policy number matches no shown policy (two digits
  transposed: notice, not joined); WC (two-column) and umbrella (clean, long-form dates); a
  package policy (GL + property in one document) that contributes no facts.
* ``cert_scanned``: GL (clean) with primary and non-contributory attached as its last page;
  umbrella declarations scanned (OCR values are scan-read, never verified: in force and insured
  consistency are Not supported).

Usage::

    python tests/eval/certificate_generator.py --out tests/fixtures/certificates
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

import certificate_gate as cg
import quote_generator as qg
from quote_generator import MARGIN, PAGE_H, PAGE_W, Cursor, Fixture, PageSpec, Run

GENERATOR_VERSION = 1
DEFAULT_SEED = 20261104
TRUTH_SCHEMA_VERSION = 1
GATE_BUCKET = "certificate"
FIXED_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "certificates"
FIXED_PDF_DATE = "D:20261103000000Z"

SYNTHETIC_FOOTER = ("Synthetic sample document for software testing. Not an insurance policy; "
                    "all names and numbers are invented.")

# Printed labels (what a declarations page says), key -> label.
PRINT_LABELS: dict[str, str] = {
    "cert:naic": "NAIC Company Code",
    "cert:policy_number": "Policy Number",
    "cert:named_insured": "Named Insured",
    "cert:insured_address": "Mailing Address",
    "cert:effective_date": "Policy Effective Date",
    "cert:expiration_date": "Policy Expiration Date",
    "cert:line": "Coverage Part",
    "cert:gl_each_occurrence": "Each Occurrence Limit",
    "cert:gl_general_aggregate": "General Aggregate Limit",
    "cert:gl_products_completed_ops_aggregate": "Products-Completed Operations Aggregate Limit",
    "cert:gl_personal_advertising_injury": "Personal and Advertising Injury Limit",
    "cert:gl_damage_to_rented_premises": "Damage to Premises Rented to You Limit",
    "cert:gl_medical_expense": "Medical Expense Limit (any one person)",
    "cert:auto_combined_single_limit": "Covered Autos Liability, Combined Single Limit",
    "cert:umbrella_each_occurrence": "Each Occurrence Limit",
    "cert:umbrella_aggregate": "Aggregate Limit",
    "cert:wc_statutory": "Part One, Workers Compensation",
    "cert:wc_el_each_accident": "Employers Liability, Bodily Injury by Accident, each accident",
    "cert:wc_el_disease_each_employee": "Employers Liability, Bodily Injury by Disease, each employee",
    "cert:wc_el_disease_policy_limit": "Employers Liability, Bodily Injury by Disease, policy limit",
}
DECL_KEYS = ["cert:naic", "cert:policy_number", "cert:named_insured", "cert:insured_address",
             "cert:effective_date", "cert:expiration_date", "cert:line"]
LIMIT_KEYS = {
    "general_liability": ["cert:gl_each_occurrence", "cert:gl_general_aggregate",
                          "cert:gl_products_completed_ops_aggregate", "cert:gl_personal_advertising_injury",
                          "cert:gl_damage_to_rented_premises", "cert:gl_medical_expense"],
    "auto": ["cert:auto_combined_single_limit"],
    "umbrella": ["cert:umbrella_each_occurrence", "cert:umbrella_aggregate"],
    "workers_comp": ["cert:wc_statutory", "cert:wc_el_each_accident", "cert:wc_el_disease_each_employee",
                     "cert:wc_el_disease_policy_limit"],
}
LINE_LABELS = {
    "general_liability": ("Commercial General Liability", "Commercial General Liability Declarations"),
    "auto": ("Business Auto", "Business Auto Declarations"),
    "umbrella": ("Commercial Umbrella Liability", "Commercial Umbrella Liability Declarations"),
    "workers_comp": ("Workers Compensation and Employers Liability",
                     "Workers Compensation and Employers Liability Declarations"),
}

FILLER = [
    "These declarations, together with the common policy conditions, coverage forms and endorsements "
    "listed in the schedule, complete the policy described above.",
    "The insurance afforded applies only to the coverages for which a limit of insurance is shown. "
    "Limits apply as stated in the coverage form.",
    "The limits shown are the most the insurer will pay regardless of the number of insureds, claims "
    "made or suits brought, or persons or organizations making claims.",
    "This endorsement is part of the policy. Except as stated here, all terms of the policy remain "
    "unchanged.",
]


@dataclass(frozen=True)
class Endorsement:
    number: str  # form number as printed, "OM 20 26"
    edition: str  # "04 24"
    type: str  # cg.ENDORSEMENT_TYPES
    title: str
    modifies: str  # coverage part named in the modifies line
    policy_number: str  # as printed on the endorsement
    body: tuple[str, ...]
    named_party: str | None = None
    party_label: str = "Name of Additional Insured Person or Organization"
    condition: str | None = None  # the condition phrase, printed inside condition_line
    condition_line: str | None = None

    @property
    def form_number(self) -> str:
        return f"{self.number} {self.edition}"

    def key(self, attr: str) -> str:
        return cg.endorsement_key(self.number, attr)


@dataclass(frozen=True)
class DocSpec:
    name: str  # fixture id suffix
    kind: str  # declarations | endorsement | package
    layout: str  # clean | multicol | scanned
    carrier: str
    title: str
    line: str | None = None  # canonical line (declarations)
    values: dict[str, str] = field(default_factory=dict)  # cert key -> printed value
    endorsements: tuple[Endorsement, ...] = ()  # one page each, after the declarations pages
    package_parts: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()  # (part, ((label, value), ...))
    note: str = ""


@dataclass(frozen=True)
class ClaimSpec:
    key: str
    status: str
    reason: str
    note: str


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    description: str
    parameters: dict
    documents: tuple[DocSpec, ...]
    claims: tuple[ClaimSpec, ...]
    exercises: tuple[str, ...]


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------

CERT_DATE = "2026-11-03"
WORD_ALL = {"additional_insured": True, "waiver_of_subrogation": True, "primary_noncontributory": True}

_AI_SCHEDULED_BODY = (
    "Section II, Who Is An Insured, is amended to include as an additional insured the person or",
    "organization shown in the Schedule, but only with respect to liability for bodily injury, property",
    "damage or personal and advertising injury caused by your acts or omissions in the performance of",
    "your ongoing operations for that additional insured.",
)
_AI_BLANKET_BODY = (
    "Section II, Who Is An Insured, is amended to include as an additional insured any person or",
    "organization for whom you are performing operations, but only with respect to liability arising",
    "out of your ongoing operations or your work for that person or organization.",
)
_WAIVER_BODY = (
    "We waive any right of recovery we may have against any person or organization because of",
    "payments we make for injury or damage arising out of your ongoing operations or your work.",
)
_WC_WAIVER_BODY = (
    "We have the right to recover our payments from anyone liable for an injury covered by this policy.",
    "We will not enforce our right against any person or organization for whom you perform work.",
)
_PNC_BODY = (
    "This insurance is primary to and will not seek contribution from any other insurance available",
    "to an additional insured under this policy for a claim arising out of your operations.",
)
_AI_COND = "only where required by a written contract signed before the loss"
_WAIVER_COND = "only where you agreed in a written contract, before the loss, to waive these rights"

GL_LIMITS = {"cert:gl_each_occurrence": "$1,000,000", "cert:gl_general_aggregate": "$2,000,000",
             "cert:gl_products_completed_ops_aggregate": "$2,000,000",
             "cert:gl_personal_advertising_injury": "$1,000,000",
             "cert:gl_damage_to_rented_premises": "$300,000", "cert:gl_medical_expense": "$10,000"}
WC_LIMITS = {"cert:wc_statutory": "Statutory", "cert:wc_el_each_accident": "$1,000,000",
             "cert:wc_el_disease_each_employee": "$1,000,000", "cert:wc_el_disease_policy_limit": "$1,000,000"}


def _decl(name, layout, carrier, naic, line, number, insured, address, eff, exp, limits, *, title=None,
          endorsements=(), note=""):
    values = {"cert:naic": naic, "cert:policy_number": number, "cert:named_insured": insured,
              "cert:insured_address": address, "cert:effective_date": eff, "cert:expiration_date": exp,
              "cert:line": LINE_LABELS[line][0], **limits}
    return DocSpec(name, "declarations", layout, carrier, title or LINE_LABELS[line][1], line, values,
                   tuple(endorsements), note=note)


# Case 1 --------------------------------------------------------------------
C1_CLIENT = "Calloway Build Group LLC"
C1_HOLDER = "Marlowe Property Group LLC"
C1_ADDR = "1180 Ferrous Way, Tacoma, WA 98402"
CARRIER_A = "Thistlewood Casualty Company"
CARRIER_B = "Brannock Mutual Insurance Company"
CARRIER_C = "Quarry Lane Indemnity Company"

CASE_CONTRACTOR = CaseSpec(
    "cert_contractor",
    "General contractor certificate for a landlord: GL, auto and WC declarations with a scheduled "
    "additional insured naming the holder, an unconditional WC waiver and an unconditional primary and "
    "non-contributory endorsement. AI, waiver and P&NC requested; description of operations given.",
    {"client_name": C1_CLIENT, "holder_name": C1_HOLDER,
     "holder_address": "2200 Larkspur Avenue, Suite 400, Tacoma, WA 98402",
     "wording": dict(WORD_ALL), "certificate_date": CERT_DATE,
     "description": "Tenant improvements at 2200 Larkspur Avenue, Suite 140."},
    (
        _decl("gl", "clean", CARRIER_A, "90412", "general_liability", "GL-4410-2281", C1_CLIENT, C1_ADDR,
              "03/01/2026", "03/01/2027", GL_LIMITS),
        _decl("auto", "clean", CARRIER_A, "90412", "auto", "CA-4410-2290", C1_CLIENT, C1_ADDR,
              "11/03/2026", "11/03/2027", {"cert:auto_combined_single_limit": "$1,000,000"},
              note="effective on the certificate date (boundary, in force)"),
        _decl("wc", "multicol", CARRIER_B, "90577", "workers_comp", "WC 88-310-57", "CALLOWAY BUILD GROUP LLC",
              C1_ADDR, "11/03/2025", "11/03/2026", WC_LIMITS,
              note="expires on the certificate date (boundary, in force); insured printed in upper case"),
        DocSpec("ai_scheduled", "endorsement", "clean", CARRIER_A, "Policy Endorsement", endorsements=(
            Endorsement("OM 20 26", "04 24", "additional_insured_scheduled",
                        "ADDITIONAL INSURED - DESIGNATED PERSON OR ORGANIZATION",
                        "Commercial General Liability Coverage Part", "GL 4410 2281", _AI_SCHEDULED_BODY,
                        named_party="MARLOWE PROPERTY GROUP LLC"),),
            note="policy number printed with spaces (declarations use dashes): joins after D7 normalization"),
        DocSpec("waiver_wc", "endorsement", "clean", CARRIER_B, "Policy Endorsement", endorsements=(
            Endorsement("OM 00 03", "01 25", "waiver_of_subrogation",
                        "WAIVER OF OUR RIGHT TO RECOVER FROM OTHERS",
                        "Workers Compensation and Employers Liability Insurance Policy", "WC8831057",
                        _WC_WAIVER_BODY),)),
        DocSpec("pnc_gl", "endorsement", "clean", CARRIER_A, "Policy Endorsement", endorsements=(
            Endorsement("OM 20 01", "04 24", "primary_noncontributory",
                        "PRIMARY AND NONCONTRIBUTORY - OTHER INSURANCE CONDITION",
                        "Commercial General Liability Coverage Part", "GL4410-2281", _PNC_BODY),)),
    ),
    (
        ClaimSpec("wording:additional_insured:general_liability", "supported", "scheduled_names_holder",
                  "OM 20 26 schedules MARLOWE PROPERTY GROUP LLC, the holder (case-insensitive)."),
        ClaimSpec("wording:additional_insured:auto", "not_supported", "no_endorsement",
                  "No additional insured endorsement modifies the auto policy."),
        ClaimSpec("wording:waiver_of_subrogation:general_liability", "not_supported", "no_endorsement",
                  "No waiver endorsement modifies the GL policy."),
        ClaimSpec("wording:waiver_of_subrogation:auto", "not_supported", "no_endorsement",
                  "No waiver endorsement modifies the auto policy."),
        ClaimSpec("wording:waiver_of_subrogation:workers_comp", "supported", "endorsement_cited",
                  "OM 00 03 waives recovery on WC8831057 = WC 88-310-57 after normalization."),
        ClaimSpec("wording:primary_noncontributory:general_liability", "supported", "endorsement_cited",
                  "OM 20 01 on GL4410-2281 = GL-4410-2281 after normalization."),
        ClaimSpec("wording:primary_noncontributory:auto", "not_supported", "no_endorsement",
                  "No primary and non-contributory endorsement modifies the auto policy."),
        ClaimSpec("in_force:GL44102281", "supported", "in_force", "03/01/2026 to 03/01/2027."),
        ClaimSpec("in_force:CA44102290", "supported", "effective_day", "Effective on the certificate date."),
        ClaimSpec("in_force:WC8831057", "supported", "expiration_day", "Expires on the certificate date."),
        ClaimSpec("insured_consistent", "supported", "insured_match",
                  "Same named insured on all three policies (WC in upper case)."),
        ClaimSpec("description_reviewed", "not_supported", "sign_off_required",
                  "A typed description is open until the broker confirms it (D11)."),
    ),
    ("gl_declarations", "auto_declarations", "wc_declarations", "ai_scheduled_names_holder",
     "waiver_of_subrogation", "primary_noncontributory", "policy_number_normalized_join",
     "in_force_effective_day", "in_force_expiration_day", "insured_case_insensitive", "description_sign_off",
     "multicol_layout"),
)

# Case 2 --------------------------------------------------------------------
C2_CLIENT = "Harrow & Finch Landscaping Inc"
C2_ADDR = "48 Orchard Spur Road, Boise, ID 83702"

CASE_WRITTEN_CONTRACT = CaseSpec(
    "cert_written_contract",
    "Landscaper certificate for a municipality: blanket AI and blanket waiver that apply only where "
    "required by a written contract (D14), and an auto policy that starts the day after the certificate "
    "date with a scheduled AI naming another party. AI and waiver requested.",
    {"client_name": C2_CLIENT, "holder_name": "City of Saltmarsh Parks Department",
     "holder_address": "300 Civic Plaza, Saltmarsh, ID 83616",
     "wording": {"additional_insured": True, "waiver_of_subrogation": True, "primary_noncontributory": False},
     "certificate_date": CERT_DATE, "description": ""},
    (
        _decl("gl", "clean", CARRIER_C, "90633", "general_liability", "QLI-GL-207731", C2_CLIENT, C2_ADDR,
              "07/15/2026", "07/15/2027", GL_LIMITS),
        DocSpec("endorsement_packet", "endorsement", "clean", CARRIER_C, "Policy Endorsements", endorsements=(
            Endorsement("OM 20 38", "04 24", "additional_insured_blanket",
                        "ADDITIONAL INSURED - OWNERS, LESSEES OR CONTRACTORS - AUTOMATIC STATUS",
                        "Commercial General Liability Coverage Part", "QLI-GL-207731", _AI_BLANKET_BODY,
                        condition=_AI_COND, condition_line=f"This endorsement applies {_AI_COND}."),
            Endorsement("OM 24 04", "04 24", "waiver_of_subrogation",
                        "WAIVER OF TRANSFER OF RIGHTS OF RECOVERY AGAINST OTHERS TO US",
                        "Commercial General Liability Coverage Part", "QLI-GL-207731", _WAIVER_BODY,
                        condition=_WAIVER_COND, condition_line=f"This waiver applies {_WAIVER_COND}."),
        ), note="two forms in one document, both conditional on a written contract"),
        _decl("auto", "multicol", CARRIER_C, "90633", "auto", "QLI-BA-207744", C2_CLIENT, C2_ADDR,
              "11/04/2026", "11/04/2027", {"cert:auto_combined_single_limit": "$1,000,000"},
              note="effective the day after the certificate date (not yet in force)"),
        DocSpec("ai_auto_other_party", "endorsement", "clean", CARRIER_C, "Policy Endorsement", endorsements=(
            Endorsement("OM 20 48", "10 23", "additional_insured_scheduled",
                        "DESIGNATED INSURED - ADDITIONAL INSURED",
                        "Business Auto Coverage Form", "QLI-BA-207744", _AI_SCHEDULED_BODY,
                        named_party="Ostrander Equipment Leasing Corp"),),
            note="scheduled AI naming a party that is not the holder"),
    ),
    (
        ClaimSpec("wording:additional_insured:general_liability", "not_supported", "conditional",
                  f"OM 20 38 applies {_AI_COND}: confirm and give the reason (D14)."),
        ClaimSpec("wording:additional_insured:auto", "not_supported", "scheduled_other_party",
                  "OM 20 48 schedules Ostrander Equipment Leasing Corp, not the holder."),
        ClaimSpec("wording:waiver_of_subrogation:general_liability", "not_supported", "conditional",
                  f"OM 24 04 applies {_WAIVER_COND} (D14)."),
        ClaimSpec("wording:waiver_of_subrogation:auto", "not_supported", "no_endorsement",
                  "No waiver endorsement modifies the auto policy."),
        ClaimSpec("in_force:QLIGL207731", "supported", "in_force", "07/15/2026 to 07/15/2027."),
        ClaimSpec("in_force:QLIBA207744", "not_supported", "not_yet_effective",
                  "Effective 11/04/2026, the day after the certificate date."),
        ClaimSpec("insured_consistent", "supported", "insured_match", "Same named insured on both policies."),
    ),
    ("written_contract_blanket_ai", "conditional_waiver", "ai_scheduled_other_party", "not_yet_effective",
     "two_forms_one_document", "multicol_layout"),
)

# Case 3 --------------------------------------------------------------------
C3_CLIENT = "Brightline Paving LLC"
C3_ADDR = "9 Kestrel Industrial Court, Dayton, OH 45402"
CARRIER_D = "Fenwick Hollow Assurance Company"

CASE_TRAPS = CaseSpec(
    "cert_traps",
    "Paving contractor certificate with the traps: an expired GL policy (expired the day before), an auto "
    "policy under another named insured, an AI endorsement whose policy number matches no shown policy, "
    "and a package policy that contributes no facts. AI requested.",
    {"client_name": C3_CLIENT, "holder_name": "Ridgefield Commons Owners Association",
     "holder_address": "77 Ridgefield Commons Drive, Dayton, OH 45459",
     "wording": {"additional_insured": True, "waiver_of_subrogation": False, "primary_noncontributory": False},
     "certificate_date": CERT_DATE, "description": ""},
    (
        _decl("gl_expired", "clean", CARRIER_D, "90781", "general_liability", "FHA GL 5520117", C3_CLIENT, C3_ADDR,
              "11/02/2025", "11/02/2026", GL_LIMITS, note="expired the day before the certificate date"),
        DocSpec("ai_blanket", "endorsement", "clean", CARRIER_D, "Policy Endorsement", endorsements=(
            Endorsement("OM 20 33", "04 24", "additional_insured_blanket",
                        "ADDITIONAL INSURED - BLANKET - OWNERS, LESSEES OR CONTRACTORS",
                        "Commercial General Liability Coverage Part", "FHA-GL-5520117", _AI_BLANKET_BODY),),
            note="unconditional blanket AI: the cited form supports the claim"),
        _decl("auto_other_insured", "clean", CARRIER_D, "90781", "auto", "CA 7719-0024", "Ferro Ridge Hauling Inc",
              "15 Slag Pit Road, Dayton, OH 45417", "01/01/2026", "01/01/2027",
              {"cert:auto_combined_single_limit": "$2,000,000"},
              note="named insured differs from the other policies"),
        DocSpec("ai_other_policy", "endorsement", "clean", CARRIER_D, "Policy Endorsement", endorsements=(
            Endorsement("OM 20 49", "10 23", "additional_insured_scheduled",
                        "ADDITIONAL INSURED - DESIGNATED PERSON OR ORGANIZATION",
                        "Business Auto Coverage Form", "CA 7719-0042", _AI_SCHEDULED_BODY,
                        named_party="Ridgefield Commons Owners Association"),),
            note="names policy CA 7719-0042 (the auto policy is CA 7719-0024): matches no shown policy"),
        _decl("wc", "multicol", CARRIER_B, "90577", "workers_comp", "WC-6604-1183", C3_CLIENT, C3_ADDR,
              "06/01/2026", "06/01/2027", WC_LIMITS),
        _decl("umbrella", "clean", CARRIER_D, "90781", "umbrella", "FHA-UMB-0098", C3_CLIENT, C3_ADDR,
              "January 1, 2026", "January 1, 2027",
              {"cert:umbrella_each_occurrence": "$5,000,000", "cert:umbrella_aggregate": "$5,000,000"},
              note="dates printed in long form"),
        DocSpec("package", "package", "clean", CARRIER_D, "Commercial Package Policy Declarations",
                values={"cert:naic": "90781", "cert:policy_number": "FHA-CPP-3301", "cert:named_insured": C3_CLIENT,
                        "cert:insured_address": C3_ADDR, "cert:effective_date": "04/01/2026",
                        "cert:expiration_date": "04/01/2027"},
                package_parts=(
                    ("Commercial General Liability Coverage Part",
                     (("Each Occurrence Limit", "$1,000,000"), ("General Aggregate Limit", "$3,000,000"))),
                    ("Commercial Property Coverage Part",
                     (("Building Limit, 9 Kestrel Industrial Court", "$1,450,000"),
                      ("Business Personal Property Limit", "$380,000"))),
                ), note="two lines in one document (C7): notice, no facts"),
    ),
    (
        ClaimSpec("wording:additional_insured:general_liability", "supported", "blanket_form_cited",
                  "OM 20 33 is an unconditional blanket AI on FHA-GL-5520117 = FHA GL 5520117."),
        ClaimSpec("wording:additional_insured:auto", "not_supported", "no_endorsement",
                  "OM 20 49 names CA 7719-0042, not the auto policy CA 7719-0024: it joins no policy."),
        ClaimSpec("wording:additional_insured:umbrella", "not_supported", "no_endorsement",
                  "No additional insured endorsement modifies the umbrella policy."),
        ClaimSpec("in_force:FHAGL5520117", "not_supported", "expired",
                  "Expired 11/02/2026, the day before the certificate date."),
        ClaimSpec("in_force:CA77190024", "supported", "in_force", "01/01/2026 to 01/01/2027."),
        ClaimSpec("in_force:WC66041183", "supported", "in_force", "06/01/2026 to 06/01/2027."),
        ClaimSpec("in_force:FHAUMB0098", "supported", "in_force", "January 1, 2026 to January 1, 2027."),
        ClaimSpec("insured_consistent", "not_supported", "insured_mismatch",
                  "The auto policy names Ferro Ridge Hauling Inc, the others Brightline Paving LLC."),
    ),
    ("expired_policy", "ai_endorsement_other_policy_number", "insured_mismatch", "package_policy",
     "ai_blanket_unconditional", "umbrella_declarations", "long_form_dates", "multicol_layout"),
)

# Case 4 --------------------------------------------------------------------
C4_CLIENT = "Pemberton Event Staffing Co"
C4_ADDR = "610 Wharf Lantern Street, Savannah, GA 31401"
CARRIER_E = "Glasshouse Specialty Insurance Company"

CASE_SCANNED = CaseSpec(
    "cert_scanned",
    "Event staffing certificate for a venue: GL with primary and non-contributory attached as its last "
    "page, and scanned umbrella declarations (scan-read values are not verified). P&NC requested.",
    {"client_name": C4_CLIENT, "holder_name": "Tidewater Hall Venue Partners LP",
     "holder_address": "1 Tidewater Hall Plaza, Savannah, GA 31401",
     "wording": {"additional_insured": False, "waiver_of_subrogation": False, "primary_noncontributory": True},
     "certificate_date": CERT_DATE, "description": ""},
    (
        _decl("gl_with_pnc", "clean", CARRIER_E, "90915", "general_liability", "GSI-GL-40021", C4_CLIENT, C4_ADDR,
              "05/01/2026", "05/01/2027", GL_LIMITS, endorsements=(
                  Endorsement("OM 20 02", "04 24", "primary_noncontributory",
                              "PRIMARY AND NONCONTRIBUTORY - OTHER INSURANCE CONDITION",
                              "Commercial General Liability Coverage Part", "GSI-GL-40021", _PNC_BODY),),
              note="declarations with the endorsement attached as page 3 (one combined pass)"),
        _decl("umbrella_scanned", "scanned", CARRIER_E, "90915", "umbrella", "GSI-UMB-40022", C4_CLIENT, C4_ADDR,
              "05/01/2026", "05/01/2027",
              {"cert:umbrella_each_occurrence": "$2,000,000", "cert:umbrella_aggregate": "$4,000,000"},
              note="image-only pages: every value is scan-read"),
    ),
    (
        ClaimSpec("wording:primary_noncontributory:general_liability", "supported", "endorsement_cited",
                  "OM 20 02 attached to the GL declarations."),
        ClaimSpec("wording:primary_noncontributory:umbrella", "not_supported", "no_endorsement",
                  "No primary and non-contributory endorsement modifies the umbrella policy."),
        ClaimSpec("in_force:GSIGL40021", "supported", "in_force", "05/01/2026 to 05/01/2027."),
        ClaimSpec("in_force:GSIUMB40022", "not_supported", "unverified_dates",
                  "The umbrella dates are scan-read only; in force needs both dates verified."),
        ClaimSpec("insured_consistent", "not_supported", "unverified_insured",
                  "The umbrella's named insured is scan-read only, so consistency is not verified."),
    ),
    ("scanned_document", "endorsement_attached_to_declarations", "umbrella_declarations"),
)

CASES: tuple[CaseSpec, ...] = (CASE_CONTRACTOR, CASE_WRITTEN_CONTRACT, CASE_TRAPS, CASE_SCANNED)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@dataclass
class Tagged:
    """Canonical values that differ from the printed text (line, endorsement type)."""
    canonical: dict[str, str] = field(default_factory=dict)


def _header(c: Cursor, carrier: str, title: str, page_no: int, n_pages: int, *, tag: bool) -> None:
    c.line(carrier, size=13, bold=True, field_name="cert:carrier" if tag else None, value=carrier if tag else None)
    c.line(f"{title}    Page {page_no} of {n_pages}", size=9)
    c.y += 10


def _footer(runs: list[Run]) -> list[Run]:
    return runs + [Run(MARGIN, PAGE_H - 36, SYNTHETIC_FOOTER, size=7)]


def _fact(c: Cursor, spec: DocSpec, key: str) -> None:
    v = spec.values.get(key)
    if v is not None:
        c.line(f"{PRINT_LABELS[key]}: {v}", field_name=key, value=v)


def _endorsement_page(e: Endorsement, carrier: str, title: str, page_no: int, n: int, tagged: Tagged,
                      *, tag_carrier: bool = False) -> PageSpec:
    c = Cursor()
    _header(c, carrier, title, page_no, n, tag=tag_carrier)
    c.line(f"Form {e.form_number}", size=9, field_name=e.key("form_number"), value=e.form_number)
    c.y += 4
    c.line(e.title, size=11, bold=True, field_name=e.key("type"), value=e.title)
    tagged.canonical[e.key("type")] = e.type
    c.line("THIS ENDORSEMENT CHANGES THE POLICY. PLEASE READ IT CAREFULLY.", size=9, bold=True)
    c.y += 6
    c.line(f"This endorsement modifies insurance provided under the {e.modifies}.", size=9)
    c.line(f"This endorsement forms part of Policy No. {e.policy_number}", field_name=e.key("policy_number"),
           value=e.policy_number)
    c.y += 8
    if e.named_party:
        c.line("SCHEDULE", size=10, bold=True)
        c.line(f"{e.party_label}: {e.named_party}", field_name=e.key("named_party"), value=e.named_party)
        c.y += 8
    for ln in e.body:
        c.line(ln)
    c.y += 6
    if e.condition_line:
        c.line(e.condition_line, field_name=e.key("condition"), value=e.condition)
        c.y += 6
    c.para(FILLER[3])
    return PageSpec(runs=_footer(c.runs))


def _decl_clean(spec: DocSpec, n: int, tagged: Tagged) -> list[PageSpec]:
    p1 = Cursor()
    _header(p1, spec.carrier, spec.title, 1, n, tag=True)
    p1.line("Declarations", size=12, bold=True)
    p1.y += 4
    for k in DECL_KEYS:
        _fact(p1, spec, k)
    if spec.line:
        tagged.canonical["cert:line"] = spec.line
    p1.y += 10
    p1.para(FILLER[0])
    p1.para(FILLER[1])
    p2 = Cursor()
    _header(p2, spec.carrier, spec.title, 2, n, tag=False)
    p2.line("Limits of Insurance", size=12, bold=True)
    p2.y += 4
    for k in LIMIT_KEYS[spec.line]:
        _fact(p2, spec, k)
    p2.y += 8
    p2.para(FILLER[2])
    return [PageSpec(runs=_footer(p1.runs)), PageSpec(runs=_footer(p2.runs))]


def _decl_multicol(spec: DocSpec, n: int, tagged: Tagged) -> list[PageSpec]:
    """Declarations as label-over-value in two columns; limits as a label | value table whose
    value column precedes its labels in content-stream order."""
    left_x, right_x = MARGIN, 330.0
    p1 = Cursor()
    _header(p1, spec.carrier, spec.title, 1, n, tag=True)
    top = p1.y
    left, right = Cursor(left_x, top), Cursor(right_x, top)
    keys = [k for k in DECL_KEYS if spec.values.get(k)]
    short = [k for k in keys if len(spec.values[k]) <= 34]
    wide = [k for k in keys if len(spec.values[k]) > 34]
    half = (len(short) + 1) // 2
    for col, ks in ((left, short[:half]), (right, short[half:])):
        for k in ks:
            col.line(PRINT_LABELS[k].upper(), size=8, bold=True)
            col.line(spec.values[k], field_name=k, value=spec.values[k])
            col.y += 6
    band = Cursor(left_x, max(left.y, right.y) + 6)
    for k in wide:
        band.line(PRINT_LABELS[k].upper(), size=8, bold=True)
        band.line(spec.values[k], size=9, field_name=k, value=spec.values[k])
        band.y += 6
    if spec.line:
        tagged.canonical["cert:line"] = spec.line
    body = Cursor(left_x, band.y + 10)
    body.para(FILLER[0])
    p1_runs = p1.runs + left.runs + right.runs + band.runs + body.runs
    p2 = Cursor()
    _header(p2, spec.carrier, spec.title, 2, n, tag=False)
    p2.line("LIMITS OF INSURANCE", size=11, bold=True)
    p2.y += 6
    y = p2.y
    labels, values = [], []
    vx = 440.0
    for k in LIMIT_KEYS[spec.line]:
        v = spec.values.get(k)
        if v is not None:
            labels.append(Run(left_x, y, PRINT_LABELS[k], size=9))
            values.append(Run(vx, y, v, field=k, value=v))
            y += 16
    tail = Cursor(left_x, y + 12)
    tail.para(FILLER[2])
    return [PageSpec(runs=_footer(p1_runs)), PageSpec(runs=_footer(p2.runs + values + labels + tail.runs))]


def _package_pages(spec: DocSpec, n: int) -> list[PageSpec]:
    p1 = Cursor()
    _header(p1, spec.carrier, spec.title, 1, n, tag=False)
    p1.line("Common Policy Declarations", size=12, bold=True)
    p1.y += 4
    for k in DECL_KEYS:
        v = spec.values.get(k)
        if v is not None:
            p1.line(f"{PRINT_LABELS[k]}: {v}")
    p1.line("Coverage Parts: " + "; ".join(part for part, _ in spec.package_parts))
    p1.y += 10
    p1.para(FILLER[0])
    p2 = Cursor()
    _header(p2, spec.carrier, spec.title, 2, n, tag=False)
    for part, rows in spec.package_parts:
        p2.line(part, size=12, bold=True)
        p2.y += 4
        for label, value in rows:
            p2.line(f"{label}: {value}")
        p2.y += 10
    p2.para(FILLER[2])
    return [PageSpec(runs=_footer(p1.runs)), PageSpec(runs=_footer(p2.runs))]


def doc_pages(spec: DocSpec) -> tuple[list[PageSpec], Tagged]:
    tagged = Tagged()
    if spec.kind == "endorsement":
        n = len(spec.endorsements)
        pages = [_endorsement_page(e, spec.carrier, spec.title, i, n, tagged, tag_carrier=i == 1)
                 for i, e in enumerate(spec.endorsements, 1)]
    elif spec.kind == "package":
        pages = _package_pages(spec, 2)
    else:
        n = 2 + len(spec.endorsements)
        base = _decl_multicol if spec.layout == "multicol" else _decl_clean
        pages = base(spec, n, tagged)
        pages += [_endorsement_page(e, spec.carrier, spec.title, 3 + i, n, tagged)
                  for i, e in enumerate(spec.endorsements)]
    if spec.layout == "scanned":
        pages = qg._scanned(pages)
    return pages, tagged


# ---------------------------------------------------------------------------
# Rendering (quote_generator's renderer with certificate metadata)
# ---------------------------------------------------------------------------


def render_pdf(fx: Fixture, rng: random.Random) -> bytes:
    doc = fitz.open()
    for spec in fx.pages:
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        if spec.image_only:
            page.insert_image(page.rect, stream=qg._raster_of_runs(spec.runs, PAGE_W, PAGE_H, 0, 0, rng))
            continue
        qg._draw_runs(page, spec.runs)
    doc.set_metadata({
        "title": fx.fixture_id, "author": "synthetic", "subject": "synthetic sample document for testing",
        "creator": f"brokerdocs-demo certificate_generator v{GENERATOR_VERSION}",
        "producer": "brokerdocs-demo certificate_generator", "keywords": "synthetic, not an insurance policy",
        "creationDate": FIXED_PDF_DATE, "modDate": FIXED_PDF_DATE,
    })
    out = doc.tobytes(garbage=4, deflate=True, no_new_id=True)
    doc.close()
    return out


# ---------------------------------------------------------------------------
# Truth
# ---------------------------------------------------------------------------


def doc_fixture_id(case_id: str, name: str) -> str:
    return f"{case_id}_{name}"


def _accepted(key: str, printed: str, canonical: str) -> list[str]:
    out = [printed] if printed != canonical else []
    parts = cg.split_endorsement_key(key)
    if parts and parts[1] == "form_number":
        number, mm, yy = printed.rsplit(" ", 2)
        out += [number, f"{number} ({mm}/{yy})", f"{number} {mm}/{yy}"]
    return out


def truth_facts(fx: Fixture, spec: DocSpec, tagged: Tagged) -> dict[str, dict | None]:
    texts = qg.page_texts(fx)
    facts: dict[str, dict | None] = {k: None for k in cg.CERT_SCALAR_KEYS}
    for e in [*spec.endorsements]:
        for attr in cg.ENDORSEMENT_ATTRS:
            facts[e.key(attr)] = None
    if spec.kind == "package":
        return facts  # C7: a package policy contributes no facts
    for pno, page in enumerate(fx.pages, 1):
        for r in page.runs:
            if not r.field:
                continue
            if r.value is None or r.value not in r.text:
                raise ValueError(f"{fx.fixture_id}: {r.field} value not inside its line")
            if facts.get(r.field) is not None:
                raise ValueError(f"{fx.fixture_id}: {r.field} tagged twice")
            if r.field not in facts:
                raise ValueError(f"{fx.fixture_id}: {r.field} is not a known key")
            canonical = tagged.canonical.get(r.field, r.value)
            facts[r.field] = {
                "value": canonical, "printed": r.value, "page": pno,
                "pages": [i for i, t in enumerate(texts, 1) if r.value in t],
                "quote": r.text, "source": "image" if page.image_only else "text",
                "accepted_values": _accepted(r.field, r.value, canonical),
            }
    return facts


def build_fixture(case: CaseSpec, spec: DocSpec) -> tuple[Fixture, Tagged]:
    pages, tagged = doc_pages(spec)
    fid = doc_fixture_id(case.case_id, spec.name)
    return Fixture(fid, f"certificate_{spec.kind}_{spec.layout}", GATE_BUCKET, spec.note, pages,
                   carrier=spec.carrier,
                   expect={"ocr_pages": list(range(1, len(pages) + 1))} if spec.layout == "scanned" else {}), tagged


def doc_truth(case: CaseSpec, spec: DocSpec, fx: Fixture, tagged: Tagged, seed: int, data: bytes) -> dict:
    facts = truth_facts(fx, spec, tagged)
    stated = [k for k, f in facts.items() if f is not None]
    printed_unused = []
    if spec.kind == "package":
        printed_unused = [{"label": PRINT_LABELS[k], "value": v} for k, v in spec.values.items()]
        printed_unused += [{"part": part, "label": label, "value": value}
                           for part, rows in spec.package_parts for label, value in rows]
    description = (f"{spec.kind.capitalize()} document of {case.case_id} ({spec.layout} layout). "
                   + (f"{spec.note}. " if spec.note else "")
                   + ("Contributes no facts (package policy, C7)." if spec.kind == "package"
                      else f"States {len(stated)} keys."))
    return {
        "schema_version": TRUTH_SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "synthetic": True,
        "fixture_id": fx.fixture_id,
        "case_id": case.case_id,
        "file": f"{fx.fixture_id}.pdf",
        "doc_kind": spec.kind,
        "layout": spec.layout,
        "line": spec.line,
        "variant": fx.variant,
        "gate_bucket": GATE_BUCKET,
        "description": description,
        "carrier": spec.carrier,
        "page_count": len(fx.pages),
        "size_bytes": len(data),
        "expect": {"parse": "ok", "failure_reason": None, "ocr_pages": [], "option_count": 1, **fx.expect},
        "facts": facts,
        "printed_unused": printed_unused,
        "decoys": [],
        "injection": None,
        "notes": "",
    }


def _evidence(claim: dict, case_t: dict, truths: dict[str, dict]) -> list[dict]:
    """Pages a Supported claim should cite."""
    if claim["status"] != cg.SUPPORTED:
        return []
    policies = cg.shown_policies(case_t, truths)
    out = []
    if claim["kind"] == "in_force":
        p = next(p for p in policies if cg.claim_in_force(p.policy_number) == claim["key"])
        for k in ("cert:effective_date", "cert:expiration_date"):
            out.append({"fixture_id": p.fixture_id, "page": p.facts[k]["page"], "key": k})
    elif claim["kind"] == "insured":
        out = [{"fixture_id": p.fixture_id, "page": p.facts["cert:named_insured"]["page"],
                "key": "cert:named_insured"} for p in policies]
    elif claim["kind"] == "wording":
        joined, _ = cg.join_endorsements(policies, cg.endorsements(case_t, truths))
        p = next(p for p in policies if p.line == claim["line"])
        for e in joined[p.fixture_id]:
            if e.attrs["type"]["value"] in cg.WORDING_TYPES[claim["wording"]] and e.attrs.get("condition") is None:
                f = e.attrs["form_number"]
                out.append({"fixture_id": e.fixture_id, "page": f["page"], "key": cg.endorsement_key(e.form, "form_number")})
                break
    return out


def case_truth(case: CaseSpec, seed: int, truths: dict[str, dict]) -> dict:
    fids = [doc_fixture_id(case.case_id, d.name) for d in case.documents]
    t = {
        "schema_version": TRUTH_SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "synthetic": True,
        "case_id": case.case_id,
        "workflow": cg.CERTIFICATE_SLUG,
        "gate_bucket": GATE_BUCKET,
        "description": case.description,
        "exercises": list(case.exercises),
        "parameters": json.loads(json.dumps(case.parameters)),
        "documents": fids,
    }
    t["shown_policies"] = [{"fixture_id": p.fixture_id, "policy_number": p.policy_number, "line": p.line}
                           for p in cg.shown_policies(t, truths)]
    ref = {c["key"]: c for c in cg.reference_claims(t, truths)}
    claims = []
    for c in case.claims:
        r = ref.get(c.key, {})
        claim = {"key": c.key, "kind": r.get("kind"), "status": c.status, "reason": c.reason, "note": c.note}
        for extra in ("wording", "line", "policy_number"):
            if extra in r:
                claim[extra] = r[extra]
        claim["evidence"] = _evidence({**claim, "kind": r.get("kind")}, t, truths) if r else []
        claims.append(claim)
    t["claims"] = claims
    t["notices"] = cg.reference_notices(t, truths)
    return t


def generate(out_dir: Path, seed: int = DEFAULT_SEED) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "pymupdf_version": fitz.VersionBind,
        "synthetic": True,
        "synthetic_notice": SYNTHETIC_FOOTER,
        "cert_fields": cg.CERT_FIELDS,
        "cases": [],
        "documents": [],
    }
    i = 0
    for case in CASES:
        truths: dict[str, dict] = {}
        for spec in case.documents:
            fx, tagged = build_fixture(case, spec)
            data = render_pdf(fx, random.Random(seed * 1031 + i))
            i += 1
            t = doc_truth(case, spec, fx, tagged, seed, data)
            truths[t["fixture_id"]] = t
            (out_dir / t["file"]).write_bytes(data)
            (out_dir / f"{t['fixture_id']}.truth.json").write_text(
                json.dumps(t, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
            manifest["documents"].append({
                "fixture_id": t["fixture_id"], "case_id": case.case_id, "file": t["file"],
                "truth": f"{t['fixture_id']}.truth.json", "doc_kind": spec.kind, "variant": t["variant"],
                "gate_bucket": GATE_BUCKET, "sha256": hashlib.sha256(data).hexdigest(),
                "deterministic_bytes": True, "description": t["description"],
            })
        ct = case_truth(case, seed, truths)
        name = f"{case.case_id}.case.json"
        (out_dir / name).write_text(json.dumps(ct, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8", newline="\n")
        manifest["cases"].append({
            "case_id": case.case_id, "truth": name, "documents": ct["documents"],
            "claims": len(ct["claims"]),
            "not_supported": sum(1 for c in ct["claims"] if c["status"] == cg.NOT_SUPPORTED),
            "exercises": ct["exercises"], "description": case.description})
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=FIXED_DIR)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)
    m = generate(args.out, args.seed)
    print(f"wrote {len(m['cases'])} cases ({len(m['documents'])} documents) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
