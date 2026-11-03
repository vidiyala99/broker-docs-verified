"""Checked-certificate eval gate (slice 4, task C10; spec docs/specs/2026-11-03-certificates-design.md
section 6, O1/D14).

One case is one certificate run: the client's policy documents (declarations and endorsements,
1 to 8 PDFs), the holder, the requested wording and the certificate date. The truth
(``tests/fixtures/certificates/<case>.case.json``) lists every claim the deterministic checks
(``backend/pipeline/coi_checks.py``) must produce, each Supported or Not supported.

Gate (spec section 6):
  * hard fails == 0, where a hard fail is a claim reported Supported whose truth is Not supported
    (the E&O failure: the certificate states coverage the documents do not give);
  * recall on Not supported claims >= 0.90 (a truth Not supported claim reported Not supported;
    a claim missing from the output is a miss, never a pass);
  * a case with no claim at all fails (no data is not a pass, as the Policy Check D16 gate);
  * field accuracy is reported per field, not gated.

ASSUMED BACKEND VOCABULARY. Lane A builds the ``cert:`` schema, ``certificate.build_content``
and ``coi_checks`` in parallel; the names below come from the spec, not from lane A's code.
Everything the gate needs from the backend is in the block below so it can be reconciled in one
place after lane A merges (``test_certificate_eval`` compares it with the backend when importable).

Adapter contract (the backend's ``backend.eval_hook.run_eval_batch`` needs a certificate branch;
until then the live test skips with the hook's error)::

    run_eval_batch(paths, workflow="certificate", parameters={
        "client_name": str, "holder_name": str, "holder_address": str,
        "wording": {"additional_insured": bool, "waiver_of_subrogation": bool,
                    "primary_noncontributory": bool},
        "certificate_date": "YYYY-MM-DD", "description": str})
    -> the Compare Quotes dict ({"input_tokens", "documents": {path: {..., "facts": [...]}}})
       plus "certificate": {
           "workflow": "certificate", "run_status": str, "warnings": [str],
           "claims": [{"key": str,                       # CLAIM KEYS below
                       "status": "supported" | "not_supported",   # the check verdict, before any
                                                          # broker decision (open/removed/resolved)
                       "reason": str | None,
                       "citations": [{"document": path, "page": int}]}],
           "notices": [{"kind": str, "message": str, "document": path | None}],
       }

Facts use the ``cert:`` keys of CERT_FIELDS; endorsement facts use ``endorsement_key(form, attr)``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Callable, Iterable, Mapping

import eval_gates as g

# =============================================================================================
# ASSUMED BACKEND VOCABULARY (reconcile with lane A after merge; one place on purpose)
# =============================================================================================

CERTIFICATE_SLUG = "certificate"  # Workflow.slug (backend/slugs.py, D4)

# Scalar ``cert:`` fact keys of the combined extraction pass (spec section 3), key -> meaning.
CERT_FIELDS: dict[str, str] = {
    # per policy document
    "cert:named_insured": "Named insured",
    "cert:insured_address": "Named insured address",
    "cert:carrier": "Insurer (carrier) name",
    "cert:naic": "Insurer NAIC code",
    "cert:policy_number": "Policy number",
    "cert:line": "Line of coverage (general_liability, auto, umbrella, workers_comp, property, other)",
    "cert:effective_date": "Policy effective date",
    "cert:expiration_date": "Policy expiration date",
    # general liability limits
    "cert:gl_each_occurrence": "GL each occurrence limit",
    "cert:gl_general_aggregate": "GL general aggregate limit",
    "cert:gl_products_completed_ops_aggregate": "GL products-completed operations aggregate limit",
    "cert:gl_personal_advertising_injury": "GL personal and advertising injury limit",
    "cert:gl_damage_to_rented_premises": "GL damage to rented premises limit",
    "cert:gl_medical_expense": "GL medical expense limit",
    # auto
    "cert:auto_combined_single_limit": "Auto combined single limit",
    # umbrella
    "cert:umbrella_each_occurrence": "Umbrella each occurrence limit",
    "cert:umbrella_aggregate": "Umbrella aggregate limit",
    # workers compensation and employer's liability
    "cert:wc_statutory": "Workers compensation statutory limits",
    "cert:wc_el_each_accident": "Employer's liability each accident",
    "cert:wc_el_disease_each_employee": "Employer's liability disease, each employee",
    "cert:wc_el_disease_policy_limit": "Employer's liability disease, policy limit",
}
CERT_SCALAR_KEYS = tuple(CERT_FIELDS)
POLICY_FACT_KEYS = ("cert:named_insured", "cert:insured_address", "cert:carrier", "cert:naic",
                    "cert:policy_number", "cert:line", "cert:effective_date", "cert:expiration_date")

LINES = ("general_liability", "auto", "umbrella", "workers_comp", "property", "other")

# Endorsements (per form, dynamic keys like Policy Check's form:<n>):
#   cert:endorsement:<FORM>:<attr>, FORM = letters and digits of the form number, upper case.
ENDORSEMENT_PREFIX = "cert:endorsement:"
ENDORSEMENT_ATTRS = ("form_number", "type", "policy_number", "named_party", "condition")
ENDORSEMENT_TYPES = ("additional_insured_blanket", "additional_insured_scheduled",
                     "waiver_of_subrogation", "primary_noncontributory")

# Requested wording (wizard checkboxes) and the endorsement types that support each.
WORDINGS = ("additional_insured", "waiver_of_subrogation", "primary_noncontributory")
WORDING_TYPES: dict[str, frozenset[str]] = {
    "additional_insured": frozenset({"additional_insured_blanket", "additional_insured_scheduled"}),
    "waiver_of_subrogation": frozenset({"waiver_of_subrogation"}),
    "primary_noncontributory": frozenset({"primary_noncontributory"}),
}
# D14: one claim per wording per shown policy line it is printed on. Which lines a wording is
# printed on is not in the spec: ACORD 25 practice (AI and primary/non-contributory on GL, auto,
# umbrella; waiver also on workers comp) [assumed, reconcile with lane A].
WORDING_LINES: dict[str, tuple[str, ...]] = {
    "additional_insured": ("general_liability", "auto", "umbrella"),
    "waiver_of_subrogation": ("general_liability", "auto", "umbrella", "workers_comp"),
    "primary_noncontributory": ("general_liability", "auto", "umbrella"),
}

# Claim keys.
CLAIM_INSURED = "insured_consistent"
CLAIM_DESCRIPTION = "description_reviewed"  # D11 sign-off row
SUPPORTED, NOT_SUPPORTED = "supported", "not_supported"
CLAIM_STATUSES = (SUPPORTED, NOT_SUPPORTED)

# Run parameters (wizard step "Who").
PARAMETER_KEYS = ("client_name", "holder_name", "holder_address", "wording", "certificate_date", "description")

# =============================================================================================


def normalize_form(number: str) -> str:
    return "".join(ch for ch in number.upper() if ch.isalnum())


def endorsement_key(form_number: str, attr: str) -> str:
    if attr not in ENDORSEMENT_ATTRS:
        raise ValueError(f"unknown endorsement attribute {attr!r}")
    return f"{ENDORSEMENT_PREFIX}{normalize_form(form_number)}:{attr}"


def split_endorsement_key(key: str) -> tuple[str, str] | None:
    """``cert:endorsement:OM2026:type`` -> (``OM2026``, ``type``)."""
    if not key.startswith(ENDORSEMENT_PREFIX):
        return None
    form, _, attr = key[len(ENDORSEMENT_PREFIX):].rpartition(":")
    return (form, attr) if form and attr in ENDORSEMENT_ATTRS else None


def normalize_policy_number(value: str | None) -> str:
    """D7: upper case, drop spaces, dashes, dots and slashes."""
    return re.sub(r"[\s\-./]", "", (value or "").upper())


def normalize_name(value: str | None) -> str:
    """Case- and space-insensitive compare (spec section 3, as the queue's client rule)."""
    return " ".join((value or "").split()).casefold()


def claim_wording(wording: str, line: str) -> str:
    return f"wording:{wording}:{line}"


def claim_in_force(policy_number: str) -> str:
    return f"in_force:{normalize_policy_number(policy_number)}"


def normalize_claim_key(key: str | None) -> str:
    k = (key or "").strip()
    if k.lower().startswith("in_force:"):
        return claim_in_force(k.split(":", 1)[1])
    return k.lower()


# =============================================================================================
# Dates
# =============================================================================================

_MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"), 1)}


def parse_date(value: str | None) -> date | None:
    """MM/DD/YYYY, YYYY-MM-DD or 'Month D, YYYY'; None when it is not one date."""
    s = (value or "").strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return date(int(m[3]), int(m[1]), int(m[2]))
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m[1].lower() in _MONTHS:
        return date(int(m[3]), _MONTHS[m[1].lower()], int(m[2]))
    return None


# =============================================================================================
# Reference checks: the spec's rules applied to the truth facts (independent of the backend)
# =============================================================================================


@dataclass
class RefPolicy:
    fixture_id: str
    policy_number: str
    line: str
    facts: dict


@dataclass
class RefEndorsement:
    fixture_id: str
    form: str
    attrs: dict  # attr -> truth fact (or None)


def _verified(fact: Mapping | None) -> bool:
    """A value the checks may rely on: stated, with a citation the verifier can confirm in the
    text layer. Scan-read (OCR) values are shown unverified (PRODUCT.md), so they never support
    a claim (spec section 3: 'both dates verified')."""
    return fact is not None and fact.get("source") == "text"


def shown_policies(case: Mapping, doc_truths: Mapping[str, Mapping]) -> list[RefPolicy]:
    out = []
    for fid in case["documents"]:
        t = doc_truths[fid]
        if t["doc_kind"] not in ("declarations",):
            continue
        f = t["facts"]
        out.append(RefPolicy(fid, f["cert:policy_number"]["value"], f["cert:line"]["value"], f))
    return out


def endorsements(case: Mapping, doc_truths: Mapping[str, Mapping]) -> list[RefEndorsement]:
    out = []
    for fid in case["documents"]:
        t = doc_truths[fid]
        if t["doc_kind"] == "package":
            continue  # C7: a package policy contributes no facts
        forms: dict[str, dict] = {}
        for k, fact in t["facts"].items():
            parts = split_endorsement_key(k)
            if parts:
                forms.setdefault(parts[0], {})[parts[1]] = fact
        out += [RefEndorsement(fid, form, attrs) for form, attrs in sorted(forms.items())]
    return out


def join_endorsements(policies: list[RefPolicy], ends: list[RefEndorsement]
                      ) -> tuple[dict[str, list[RefEndorsement]], list[dict]]:
    """D7: normalized exact join; an unmatched number or a collision is a notice and joins nothing."""
    by_number: dict[str, list[RefPolicy]] = {}
    for p in policies:
        by_number.setdefault(normalize_policy_number(p.policy_number), []).append(p)
    joined: dict[str, list[RefEndorsement]] = {p.fixture_id: [] for p in policies}
    notices = []
    for e in ends:
        number = (e.attrs.get("policy_number") or {}).get("value")
        hits = by_number.get(normalize_policy_number(number), [])
        if len(hits) == 1:
            joined[hits[0].fixture_id].append(e)
        else:
            notices.append({"kind": "endorsement_unmatched" if not hits else "endorsement_collision",
                            "fixture_id": e.fixture_id, "form": e.form,
                            "form_number": e.attrs["form_number"]["value"],
                            "policy_number": number})
    return joined, notices


def reference_claims(case: Mapping, doc_truths: Mapping[str, Mapping]) -> list[dict]:
    """Every claim with its verdict and reason, by the spec's rules (section 3 Checks, D7, D11, D14)."""
    params = case["parameters"]
    holder = normalize_name(params["holder_name"])
    on = parse_date(params["certificate_date"])
    policies = shown_policies(case, doc_truths)
    joined, _ = join_endorsements(policies, endorsements(case, doc_truths))
    claims: list[dict] = []

    for wording in WORDINGS:
        if not params["wording"].get(wording):
            continue
        for p in policies:
            if p.line not in WORDING_LINES[wording]:
                continue
            reasons = []
            status = NOT_SUPPORTED
            for e in joined[p.fixture_id]:
                etype = (e.attrs.get("type") or {}).get("value")
                if etype not in WORDING_TYPES[wording]:
                    continue
                if not (_verified(e.attrs.get("form_number")) and _verified(e.attrs.get("type"))
                        and _verified(e.attrs.get("policy_number"))):
                    reasons.append("unverified_endorsement")
                    continue
                if e.attrs.get("condition") is not None:
                    reasons.append("conditional")
                    continue
                if etype == "additional_insured_scheduled":
                    party = e.attrs.get("named_party")
                    if not _verified(party) or normalize_name(party["value"]) != holder:
                        reasons.append("scheduled_other_party")
                        continue
                    status, reasons = SUPPORTED, ["scheduled_names_holder"]
                    break
                status = SUPPORTED
                reasons = ["blanket_form_cited" if etype == "additional_insured_blanket" else "endorsement_cited"]
                break
            claims.append({"key": claim_wording(wording, p.line), "kind": "wording", "wording": wording,
                           "line": p.line, "status": status,
                           "reason": reasons[0] if reasons else "no_endorsement"})

    for p in policies:
        eff, exp = p.facts.get("cert:effective_date"), p.facts.get("cert:expiration_date")
        if not (_verified(eff) and _verified(exp)):
            status, reason = NOT_SUPPORTED, "unverified_dates"
        else:
            d0, d1 = parse_date(eff["value"]), parse_date(exp["value"])
            if on < d0:
                status, reason = NOT_SUPPORTED, "not_yet_effective"
            elif on > d1:
                status, reason = NOT_SUPPORTED, "expired"
            else:
                status = SUPPORTED
                reason = ("effective_day" if on == d0 else "expiration_day" if on == d1 else "in_force")
        claims.append({"key": claim_in_force(p.policy_number), "kind": "in_force", "line": p.line,
                       "policy_number": p.policy_number, "status": status, "reason": reason})

    names = [p.facts.get("cert:named_insured") for p in policies]
    if not all(_verified(n) for n in names):
        status, reason = NOT_SUPPORTED, "unverified_insured"
    elif len({normalize_name(n["value"]) for n in names}) > 1:
        status, reason = NOT_SUPPORTED, "insured_mismatch"
    else:
        status, reason = SUPPORTED, "insured_match"
    claims.append({"key": CLAIM_INSURED, "kind": "insured", "status": status, "reason": reason})

    if (params.get("description") or "").strip():
        claims.append({"key": CLAIM_DESCRIPTION, "kind": "sign_off", "status": NOT_SUPPORTED,
                       "reason": "sign_off_required"})
    return claims


def reference_notices(case: Mapping, doc_truths: Mapping[str, Mapping]) -> list[dict]:
    policies = shown_policies(case, doc_truths)
    _, notices = join_endorsements(policies, endorsements(case, doc_truths))
    pkg = [{"kind": "package", "fixture_id": fid} for fid in case["documents"]
           if doc_truths[fid]["doc_kind"] == "package"]
    return pkg + notices


# =============================================================================================
# Scoring
# =============================================================================================


@dataclass
class CertScore:
    case_id: str
    run_status: str | None = None
    input_tokens: int = 0
    claims_out: int = 0
    truth_claims: int = 0
    not_supported: int = 0
    caught: int = 0
    supported: int = 0
    supported_agree: int = 0
    hard_fails: list[dict] = field(default_factory=list)
    misses: list[dict] = field(default_factory=list)
    false_alarms: list[dict] = field(default_factory=list)
    unscored_claims: list[dict] = field(default_factory=list)
    citation_issues: list[dict] = field(default_factory=list)
    notices_expected: int = 0
    notices_found: int = 0
    notices_missing: list[dict] = field(default_factory=list)
    package_facts_shown: list[dict] = field(default_factory=list)
    documents_failed: list[str] = field(default_factory=list)
    fact_scores: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _doc_result(output: Mapping, path: str, fid: str) -> g.DocResult:
    docs = output.get("documents") or {}
    raw = docs.get(path)
    if raw is None:
        raw = next((v for k, v in docs.items() if g._norm_path(k) == g._norm_path(path)), None)
    raw = raw or {"status": "failed", "failure_reason": "missing_from_adapter_output"}
    return g.DocResult.from_dict({**raw, "fixture_id": fid})


def _notice_found(expected: Mapping, notices: list[Mapping], path_by_fid: Mapping[str, str]) -> bool:
    for n in notices:
        msg = n.get("message") or ""
        doc = g._norm_path(n.get("document"))
        same_doc = doc is not None and doc == g._norm_path(path_by_fid.get(expected["fixture_id"]))
        if expected["kind"] == "package":
            if same_doc or (doc is None and "several lines" in msg.lower()):
                return True
        elif expected["form"] in normalize_form(msg) or (
                same_doc and normalize_policy_number(expected["policy_number"]) in normalize_policy_number(msg)):
            return True  # names the form (spec section 5), or sits on the endorsement naming its number
    return False


def score_certificate_case(case: Mapping, doc_truths: Mapping[str, Mapping], output: Mapping,
                           paths: Mapping[str, str]) -> CertScore:
    """Score one certificate run. ``paths`` maps fixture id to the path given to the adapter."""
    cid = case["case_id"]
    block = output.get("certificate") or {}
    sc = CertScore(cid, run_status=block.get("run_status"), input_tokens=int(output.get("input_tokens") or 0),
                   warnings=list(block.get("warnings") or []))
    fid_for = {g._norm_path(paths[f]): f for f in case["documents"]}

    for fid in case["documents"]:
        result = _doc_result(output, paths[fid], fid)
        if result.status != "ok":
            sc.documents_failed.append(fid)
        ds = g.score_document(doc_truths[fid], result)
        sc.fact_scores.append(ds)
        if doc_truths[fid]["doc_kind"] == "package":
            sc.package_facts_shown += [{"case_id": cid, "fixture_id": fid, "field": f.field, "value": f.value}
                                       for f in result.facts if f.displayed]

    claims = list(block.get("claims") or [])
    sc.claims_out = len(claims)
    by_key: dict[str, Mapping] = {}
    for c in claims:
        by_key.setdefault(normalize_claim_key(c.get("key")), c)
    truth = {normalize_claim_key(c["key"]): c for c in case["claims"]}
    sc.truth_claims = len(truth)

    for key, t in truth.items():
        got = by_key.get(key)
        status = got.get("status") if got else None
        if t["status"] == NOT_SUPPORTED:
            sc.not_supported += 1
            if status == SUPPORTED:
                sc.hard_fails.append({"case_id": cid, "key": t["key"], "kind": "supported_but_not_supported",
                                      "truth_reason": t["reason"], "reported_reason": got.get("reason"),
                                      "detail": t["note"]})
            elif status == NOT_SUPPORTED:
                sc.caught += 1
            else:
                sc.misses.append({"case_id": cid, "key": t["key"], "truth_reason": t["reason"],
                                  "reason": "no claim" if got is None else f"status {status!r}"})
        else:
            sc.supported += 1
            if status == SUPPORTED:
                sc.supported_agree += 1
                cites = list(got.get("citations") or [])
                if not cites:
                    sc.citation_issues.append({"case_id": cid, "key": t["key"], "issue": "supported with no citation"})
                for c in cites:
                    fid = fid_for.get(g._norm_path(c.get("document")))
                    if fid is None:
                        sc.citation_issues.append({"case_id": cid, "key": t["key"],
                                                   "issue": "citation outside the run's documents"})
            else:
                sc.false_alarms.append({"case_id": cid, "key": t["key"], "truth_reason": t["reason"],
                                        "reported": "no claim" if got is None else status,
                                        "reported_reason": got.get("reason") if got else None})
    for key, c in by_key.items():
        if key not in truth:
            sc.unscored_claims.append({"case_id": cid, "key": c.get("key"), "status": c.get("status"),
                                       "reason": c.get("reason")})

    notices = list(block.get("notices") or [])
    for n in case.get("notices") or []:
        sc.notices_expected += 1
        if _notice_found(n, notices, paths):
            sc.notices_found += 1
        else:
            sc.notices_missing.append({"case_id": cid, **n})
    return sc


@dataclass
class CertMetrics:
    cases: list[dict]
    truth_claims: int
    not_supported: int
    caught: int
    recall: float | None
    supported: int
    supported_agree: int
    supported_accuracy: float | None
    claim_accuracy: float | None
    hard_fails: list[dict]
    misses: list[dict]
    false_alarms: list[dict]
    unscored_claims: list[dict]
    citation_issues: list[dict]
    notices_expected: int
    notices_found: int
    notices_missing: list[dict]
    package_facts_shown: list[dict]
    empty_cases: list[str]
    field_accuracy: dict[str, dict]
    fact_accuracy: float | None
    documents_failed: list[str]
    max_run_input_tokens: int | None
    warnings: list[str] = field(default_factory=list)


def field_group(key: str) -> str:
    """Per-field reporting bucket: endorsement keys group by attribute across forms."""
    parts = split_endorsement_key(key)
    return f"{ENDORSEMENT_PREFIX}*:{parts[1]}" if parts else key


def _field_accuracy(fscores: Iterable) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for ds in fscores:
        for o in ds.outcomes:
            if o.truth is None:
                continue
            b = out.setdefault(field_group(o.field), {"present": 0, "correct": 0, "accuracy": None})
            b["present"] += 1
            b["correct"] += int(o.correct)
    for b in out.values():
        b["accuracy"] = b["correct"] / b["present"]
    return dict(sorted(out.items()))


def aggregate_certificate(scores: list[CertScore]) -> CertMetrics:
    ns = sum(s.not_supported for s in scores)
    caught = sum(s.caught for s in scores)
    sup = sum(s.supported for s in scores)
    agree = sum(s.supported_agree for s in scores)
    fscores = [f for s in scores for f in s.fact_scores]
    return CertMetrics(
        cases=[{"case_id": s.case_id, "run_status": s.run_status, "claims": s.claims_out,
                "truth_claims": s.truth_claims, "not_supported": s.not_supported, "caught": s.caught,
                "recall": g._ratio(s.caught, s.not_supported), "hard_fails": len(s.hard_fails),
                "input_tokens": s.input_tokens} for s in scores],
        truth_claims=sum(s.truth_claims for s in scores), not_supported=ns, caught=caught,
        recall=g._ratio(caught, ns), supported=sup, supported_agree=agree,
        supported_accuracy=g._ratio(agree, sup), claim_accuracy=g._ratio(caught + agree, ns + sup),
        hard_fails=[h for s in scores for h in s.hard_fails],
        misses=[m for s in scores for m in s.misses],
        false_alarms=[f for s in scores for f in s.false_alarms],
        unscored_claims=[u for s in scores for u in s.unscored_claims],
        citation_issues=[c for s in scores for c in s.citation_issues],
        notices_expected=sum(s.notices_expected for s in scores),
        notices_found=sum(s.notices_found for s in scores),
        notices_missing=[n for s in scores for n in s.notices_missing],
        package_facts_shown=[p for s in scores for p in s.package_facts_shown],
        empty_cases=[s.case_id for s in scores if s.claims_out == 0],
        field_accuracy=_field_accuracy(fscores),
        fact_accuracy=g._ratio(sum(f.correct for f in fscores), sum(f.present for f in fscores)),
        documents_failed=[d for s in scores for d in s.documents_failed],
        max_run_input_tokens=max((s.input_tokens for s in scores), default=None),
        warnings=[f"{s.case_id}: {w}" for s in scores for w in s.warnings],
    )


CERT_HARD_FAIL_MAX = 0
CERT_RECALL_MIN = 0.90
GATE_HARD = "certificate_hard_fails (Supported where the truth is Not supported)"
GATE_RECALL = "not_supported_recall[certificate]"
GATE_EMPTY = "empty_result[certificate] (cases with no claim)"


def evaluate_certificate_gates(m: CertMetrics) -> g.GateReport:
    hard = len(m.hard_fails)
    res = [
        g.GateResult(GATE_HARD, hard, f"== {CERT_HARD_FAIL_MAX}", True, hard <= CERT_HARD_FAIL_MAX,
                     ", ".join(h["key"] for h in m.hard_fails)),
        g.GateResult(GATE_RECALL, m.recall, f">= {CERT_RECALL_MIN:.2f}", True,
                     m.recall is not None and m.recall >= CERT_RECALL_MIN,
                     "no data" if m.recall is None else f"{m.caught}/{m.not_supported}"),
        g.GateResult(GATE_EMPTY, len(m.empty_cases) if m.cases else None, "== 0", True,
                     bool(m.cases) and not m.empty_cases, ", ".join(m.empty_cases) if m.cases else "no data"),
    ]
    if m.max_run_input_tokens is not None:
        res.append(g.GateResult("max_run_input_tokens", m.max_run_input_tokens, f"<= {g.RUN_INPUT_TOKEN_BUDGET}",
                                True, m.max_run_input_tokens <= g.RUN_INPUT_TOKEN_BUDGET))
    res += [
        g.GateResult("claim_accuracy[certificate]", m.claim_accuracy, "reported", False, True,
                     "every truth claim with the truth verdict"),
        g.GateResult("supported_accuracy[certificate]", m.supported_accuracy, "reported", False, True,
                     f"{m.supported_agree}/{m.supported} truth-Supported claims reported Supported"),
        g.GateResult("unscored_claims", len(m.unscored_claims), "reported", False, True,
                     "output claims the truth does not list (key vocabulary drift?)"),
        g.GateResult("supported_citation_issues", len(m.citation_issues), "reported", False, True),
        g.GateResult("notices_found", m.notices_found, "reported", False, True,
                     f"of {m.notices_expected} (package policy, unmatched endorsement)"),
        g.GateResult("package_facts_shown", len(m.package_facts_shown), "reported", False, True,
                     "a package policy contributes no facts (C7)"),
        g.GateResult("fact_accuracy[certificate documents]", m.fact_accuracy, "reported", False, True),
        g.GateResult("documents_failed", len(m.documents_failed), "reported", False, True,
                     ", ".join(m.documents_failed)),
    ]
    for name, b in m.field_accuracy.items():
        res.append(g.GateResult(f"field_accuracy[{name}]", b["accuracy"], "reported", False, True,
                                f"{b['correct']}/{b['present']}"))
    return g.GateReport(res)


def format_certificate_report(m: CertMetrics, rep: g.GateReport) -> str:
    lines = ["Certificate eval gates (slice 4, C10)", "-" * 78]
    for r in rep.results:
        flag = ("PASS" if r.passed else "FAIL") if r.gated else "info"
        note = f"  ({r.note})" if r.note else ""
        lines.append(f"{flag:5} {r.name:66} {g._fmt(r.value):>8}  {r.threshold}{note}")
    lines.append("-" * 78)
    lines.append("case                      status    claims  truth  not_sup  caught  recall  hard  tokens")
    for c in m.cases:
        lines.append(f"{c['case_id']:25} {str(c['run_status']):9} {c['claims']:6} {c['truth_claims']:6} "
                     f"{c['not_supported']:8} {c['caught']:7} {g._fmt(c['recall']):>7} {c['hard_fails']:5} "
                     f"{c['input_tokens']:7}")
    for h in m.hard_fails:
        lines.append(f"HARD FAIL {h['case_id']}.{h['key']}: Supported, truth Not supported ({h['truth_reason']}): "
                     f"{h['detail']}")
    for d in m.misses:
        lines.append(f"MISS {d['case_id']}.{d['key']} ({d['truth_reason']}): {d['reason']}")
    for f in m.false_alarms:
        lines.append(f"FALSE ALARM {f['case_id']}.{f['key']}: reported {f['reported']} ({f['reported_reason']}), "
                     f"truth Supported ({f['truth_reason']})")
    for u in m.unscored_claims:
        lines.append(f"UNSCORED {u['case_id']}.{u['key']}: {u['status']} ({u['reason']})")
    for n in m.notices_missing:
        lines.append(f"NOTICE MISSING {n['case_id']}: {n['kind']} {n['fixture_id']}")
    for w in m.warnings:
        lines.append(f"WARNING {w}")
    lines.append(f"overall: {'PASS' if rep.passed else 'FAIL'}")
    return "\n".join(lines)


def certificate_report_dict(m: CertMetrics, rep: g.GateReport) -> dict:
    return {"passed": rep.passed, "gates": [asdict(r) for r in rep.results], "metrics": asdict(m)}


def certificate_oracle_output(case: Mapping, doc_truths: Mapping[str, Mapping], paths: Mapping[str, str], *,
                              input_tokens: int = 1000) -> dict:
    """A perfect adapter output for one case: oracle facts per document (none for a package
    policy) and every truth claim with its verdict and evidence pages. Self-tests the scorer
    and documents the adapter shape."""
    documents: dict[str, dict] = {}
    for fid in case["documents"]:
        r = g.oracle_result(doc_truths[fid])
        documents[paths[fid]] = {"status": r.status, "failure_reason": r.failure_reason,
                                 "option_count": r.option_count, "ocr_pages": r.ocr_pages,
                                 "facts": [vars(f) for f in r.facts]}
    claims = [{"key": c["key"], "status": c["status"], "reason": c["reason"],
               "citations": [{"document": paths[e["fixture_id"]], "page": e["page"]} for e in c["evidence"]]}
              for c in case["claims"]]
    notices = []
    for n in case.get("notices") or []:
        if n["kind"] == "package":
            notices.append({"kind": "package", "document": paths[n["fixture_id"]],
                            "message": "This document covers several lines; not supported yet"})
        else:
            notices.append({"kind": n["kind"], "document": paths[n["fixture_id"]],
                            "message": f"Endorsement {n['form_number']} names policy {n['policy_number']}, "
                                       "which matches no policy on this certificate"})
    return {"input_tokens": input_tokens, "documents": documents,
            "certificate": {"workflow": case.get("workflow"), "run_status": "done", "claims": claims,
                            "notices": notices, "warnings": []}}


# =============================================================================================
# Suite runner (one adapter call per case)
# =============================================================================================

CERT_CASES_ENV = "BROKERDOCS_EVAL_CERTIFICATE_CASES"


def selected_certificate_cases(known: list[str], env: Mapping | None = None) -> list[str]:
    import os

    raw = (os.environ if env is None else env).get(CERT_CASES_ENV, "")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        return list(known)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(f"{CERT_CASES_ENV}: unknown case(s) {unknown}; known: {known}")
    return list(dict.fromkeys(names))


def run_certificate_suite(adapter: Callable[..., dict], cert_dir, cases: Mapping, doc_truths: Mapping,
                          ids: list[str] | None = None):
    """Call ``adapter`` once per case, score its claims, apply the gate. NotImplementedError
    (and the hook's ValueError for an unknown workflow) propagate to the caller."""
    from pathlib import Path

    ids = ids if ids is not None else selected_certificate_cases(sorted(cases))
    scores = []
    for cid in ids:
        case = cases[cid]
        paths = {fid: str(Path(cert_dir) / doc_truths[fid]["file"]) for fid in case["documents"]}
        out = adapter([paths[f] for f in case["documents"]], workflow=case["workflow"],
                      parameters=adapter_parameters(case))
        scores.append(score_certificate_case(case, doc_truths, out, paths))
    m = aggregate_certificate(scores)
    return m, evaluate_certificate_gates(m)


def adapter_parameters(case: Mapping) -> dict:
    p = case["parameters"]
    return {k: (dict(p[k]) if isinstance(p[k], dict) else p[k]) for k in PARAMETER_KEYS}
