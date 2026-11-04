"""Slice 4 C4: the deterministic certificate checks (pipeline.coi_checks), table-driven, plus the cert:
extraction normalization, the endorsement merge expansion, the block traversal, slugs, the schema check
and the issued PDF (escape, one page at the D6 maxima)."""

from __future__ import annotations

import re
from datetime import date

import pytest
from sqlalchemy import text

from backend import db, export_certificate, slugs, work_queue
from backend.pipeline import coi_checks as cc
from backend.pipeline import extract, merge, verify
from backend.schemas import (
    CERTIFICATE_FIELDS,
    ArtifactContent,
    CertificateBlock,
    CertificateField,
    CertificateInsurer,
    CertificatePolicy,
    Cell,
    ClaimBlock,
    EvidenceState,
    Section,
    endorsement_key,
    is_endorsement_key,
    split_endorsement_key,
)

V = EvidenceState.VERIFIED


def ev(value, state=V, pid=1):
    return cc.Evidence(value=value, state=state, passage_ids=(pid,) if value else ())


def policy(letter="A", *, number="GL-1234567", line="Commercial General Liability", eff="01/01/2026",
           exp="12/31/2026", insured="Cedar Hollow LLC", endorsements=(), states=None):
    states = states or {}
    facts = {"cert:policy_number": ev(number, states.get("number", V)), "cert:line": ev(line),
             "cert:effective_date": ev(eff, states.get("eff", V)), "cert:expiration_date": ev(exp, states.get("exp", V)),
             "cert:named_insured": ev(insured, states.get("insured", V))}
    return cc.PolicyDoc(letter=letter, document_id=1, filename=f"{letter}.pdf", facts=facts,
                        endorsements=tuple(endorsements))


def endorsement(number="CG2026", *, title="ADDITIONAL INSURED - DESIGNATED PERSON OR ORGANIZATION",
                policy_number="GL 1234567", party="Smith & Sons", condition=None, form_state=V, letter="B"):
    return cc.Endorsement(document_letter=letter, number=number, form=ev(f"{number} 04 13", form_state),
                          type=ev(title) if title else cc.MISSING,
                          policy_number=ev(policy_number) if policy_number else cc.MISSING,
                          named_party=ev(party) if party else cc.MISSING,
                          condition=ev(condition) if condition else cc.MISSING)


REQ = cc.Request(certificate_date=date(2026, 6, 1), holder_name="Smith & Sons", wording=cc.WORDINGS)


# --------------------------------------------------------------------- normalizers


@pytest.mark.parametrize("a,b", [("GL-1234567", "GL 1234567"), ("GL-1234567", "gl1234567"),
                                 ("GL.123/4567", "GL1234567")])
def test_policy_numbers_join_after_normalization(a, b):
    assert cc.normalize_policy_number(a) == cc.normalize_policy_number(b)


def test_names_ignore_case_spacing_dots_and_commas():
    assert cc.normalize_name("Smith & Sons, L.L.C.") == cc.normalize_name("smith  &  sons LLC")
    assert cc.normalize_name("Smith and Sons") != cc.normalize_name("Smith & Sons")


@pytest.mark.parametrize("value,expected", [
    ("Commercial General Liability", ("general_liability", False)), ("Business Auto", ("auto", False)),
    ("Commercial Umbrella Liability", ("umbrella", False)), ("Commercial Excess Liability", ("umbrella", False)),
    ("Workers Compensation and Employers Liability", ("workers_comp", False)),
    ("Commercial Property", ("property", False)), ("Inland Marine", ("other", False)),
    ("Commercial Package Policy", (None, True)), ("Businessowners Policy", (None, True)),
    ("General Liability and Business Auto", (None, True)), ("", (None, False)),
])
def test_line_classification(value, expected):
    assert cc.classify_line(value) == expected


@pytest.mark.parametrize("value,expected", [("01/01/2026", date(2026, 1, 1)), ("2026-01-01", date(2026, 1, 1)),
                                            ("January 1, 2026", date(2026, 1, 1)), ("01/01/2026 to 01/01/2027", None),
                                            ("soon", None)])
def test_parse_date(value, expected):
    assert cc.parse_date(value) == expected


# --------------------------------------------------------------------- in force


@pytest.mark.parametrize("on,verdict,why", [
    (date(2026, 1, 1), "supported", None),  # effective day
    (date(2026, 12, 31), "supported", None),  # expiration day
    (date(2025, 12, 31), "not_supported", "starts on"),
    (date(2027, 1, 1), "not_supported", "expired on"),
])
def test_in_force_boundaries(on, verdict, why):
    c = cc.in_force_claim(policy(), on)
    assert str(c.verdict) == verdict and (why is None or why in c.reason)


@pytest.mark.parametrize("states,why", [({"eff": EvidenceState.SCAN_READ}, "read from a scan"),
                                        ({"exp": EvidenceState.NOT_FOUND}, "could not be verified")])
def test_in_force_needs_verified_dates(states, why):
    c = cc.in_force_claim(policy(states=states), date(2026, 6, 1))
    assert str(c.verdict) == "not_supported" and why in c.reason


def test_in_force_with_conflicting_dates():
    p = policy()
    facts = dict(p.facts)
    facts["cert:expiration_date"] = cc.Evidence(state=EvidenceState.CONFLICTING, conflicts=("12/31/2026", "06/30/2026"))
    c = cc.in_force_claim(cc.PolicyDoc("A", 1, "a.pdf", facts), date(2026, 6, 1))
    assert str(c.verdict) == "not_supported" and "conflicting values" in c.reason


# --------------------------------------------------------------------- wording (D14)


def _wording(e, wording=cc.ADDITIONAL_INSURED, p=None, req=REQ):
    p = p or policy()
    joined = cc.join([p, cc.PolicyDoc("B", 2, "b.pdf", {}, (e,))])
    return cc.wording_claim(p, wording, joined.endorsements["A"], req)


@pytest.mark.parametrize("kw,verdict,why", [
    ({}, "supported", "names the holder"),  # scheduled, names the holder
    ({"party": "Someone Else LLC"}, "not_supported", "not the holder"),
    ({"party": None}, "not_supported", "names"),  # scheduled by title, no party
    ({"title": "BLANKET ADDITIONAL INSURED", "party": None}, "supported", "blanket"),
    ({"title": "BLANKET ADDITIONAL INSURED", "party": None, "condition": "where required by written contract"},
     "not_supported", "applies only when where required by written contract"),
    ({"form_state": EvidenceState.SCAN_READ}, "not_supported", "read from a scan"),
    ({"title": "WAIVER OF TRANSFER OF RIGHTS OF RECOVERY", "party": None}, "not_supported", "no additional insured"),
])
def test_additional_insured_cases(kw, verdict, why):
    c = _wording(endorsement(**kw))
    assert str(c.verdict) == verdict and why in c.reason, c.reason


def test_conditional_claim_quotes_the_condition():
    c = _wording(endorsement(title="BLANKET ADDITIONAL INSURED", party=None, condition="where required by contract"))
    assert c.condition == "where required by contract" and c.prose[0].passage_ids


@pytest.mark.parametrize("title,wording", [
    ("WAIVER OF TRANSFER OF RIGHTS OF RECOVERY AGAINST OTHERS TO US", cc.WAIVER),
    ("PRIMARY AND NONCONTRIBUTORY - OTHER INSURANCE CONDITION", cc.PRIMARY_NONCONTRIBUTORY),
    ("Primary and Non-Contributory", cc.PRIMARY_NONCONTRIBUTORY),
])
def test_waiver_and_primary_noncontributory_blanket(title, wording):
    c = _wording(endorsement(title=title, party=None), wording)
    assert str(c.verdict) == "supported"


def test_form_table_types_an_untitled_iso_form():
    e = endorsement("CG2404", title=None, party="Smith & Sons")
    assert e.types == [cc.WAIVER] and e.scheduled
    assert str(_wording(e, cc.WAIVER).verdict) == "supported"
    assert endorsement().types == [cc.AI_SCHEDULED]
    assert endorsement(title="BLANKET ADDITIONAL INSURED", party=None).types == [cc.AI_BLANKET]
    assert set(cc.ENDORSEMENT_TYPES) == {"additional_insured_blanket", "additional_insured_scheduled",
                                         "waiver_of_subrogation", "primary_noncontributory"}


def test_wording_applies_by_line():
    req = REQ
    assert cc.applicable_wording("workers_comp", req) == [cc.WAIVER]
    assert cc.applicable_wording("general_liability", req) == list(cc.WORDINGS)
    assert cc.applicable_wording("property", req) == []


def test_unread_documents_are_named_in_the_reason():
    p = policy()
    c = cc.wording_claim(p, cc.ADDITIONAL_INSURED, [], REQ, unread=2)
    assert str(c.verdict) == "not_supported" and "2 documents not read" in c.reason


# --------------------------------------------------------------------- join (D7)


def test_join_normalized_unmatched_and_collision():
    a, b = policy("A"), policy("B", number="GL 1234567", line="Business Auto")
    e = endorsement(letter="C")
    joined = cc.join([a, b, cc.PolicyDoc("C", 3, "c.pdf", {}, (e,))])
    assert joined.endorsements == {"A": [], "B": []}
    kinds = [n.kind for n in joined.notices]
    assert kinds.count("endorsement_collision") == 2  # the two policies, and the endorsement that joins neither
    other = cc.join([policy("A"), cc.PolicyDoc("B", 2, "b.pdf", {}, (endorsement(policy_number="GL-9"),))])
    assert other.endorsements["A"] == [] and other.notices[0].kind == "endorsement_unmatched"


def test_endorsement_without_a_number_joins_its_own_document():
    p = policy(endorsements=(endorsement(policy_number=None, letter="A"),))
    joined = cc.join([p])
    assert len(joined.endorsements["A"]) == 1 and joined.notices == []


def _endorsement_doc(letter, e, *, number, line="Commercial General Liability Coverage Part", **more):
    """An endorsement-only document as the live model reads it: the coverage part it modifies as the
    line and "forms part of Policy No. X" as the policy number, no declarations facts."""
    facts = {"cert:line": ev(line), "cert:policy_number": ev(number), "cert:carrier": ev("Thistlewood"),
             **{k: ev(v) for k, v in more.items()}}
    return cc.PolicyDoc(letter, 2, f"{letter}.pdf", facts, (e,))


def test_endorsement_only_document_is_not_a_policy_row():
    decl = policy("A", number="GL-4410-2281")
    e = endorsement(policy_number="GL 4410 2281", party="Smith & Sons")
    joined = cc.join([decl, _endorsement_doc("B", e, number="GL 4410 2281")])
    assert [p.letter for p in joined.policies] == ["A"]  # no second row, no collision
    assert joined.endorsements == {"A": [e]} and joined.notices == []
    assert str(cc.wording_claim(decl, cc.ADDITIONAL_INSURED, joined.endorsements["A"], REQ).verdict) == "supported"


def test_endorsement_only_document_naming_another_policy_is_unmatched():
    auto = policy("A", number="CA 7719-0024", line="Business Auto")
    e = endorsement(number="OM2049", policy_number="CA 7719-0042")
    joined = cc.join([auto, _endorsement_doc("B", e, number="CA 7719-0042", line="Business Auto Coverage Form")])
    assert [p.letter for p in joined.policies] == ["A"] and joined.endorsements == {"A": []}
    assert [n.kind for n in joined.notices] == ["endorsement_unmatched"] and "OM2049" in joined.notices[0].message


@pytest.mark.parametrize("key,value", [("cert:named_insured", "Cedar Hollow LLC"), ("cert:effective_date", "01/01/2026"),
                                       ("cert:gl_each_occurrence", "$1,000,000")])
def test_a_document_with_endorsements_and_a_declarations_fact_is_a_policy(key, value):
    e = endorsement(policy_number=None, letter="A")
    doc = _endorsement_doc("A", e, number="GL-1", **{key: value})
    joined = cc.join([doc])
    assert [p.letter for p in joined.policies] == ["A"] and joined.endorsements == {"A": [e]}


def test_unverified_declarations_fact_still_makes_a_policy_row():
    """A declarations page whose dates did not verify stays a row (its claims fail), never vanishes."""
    e = endorsement(policy_number=None, letter="A")
    doc = cc.PolicyDoc("A", 1, "a.pdf", {"cert:line": ev("Commercial General Liability"),
                                         "cert:expiration_date": cc.Evidence(state=EvidenceState.NOT_FOUND)}, (e,))
    assert cc.join([doc]).policies == [doc]
    assert cc.join([cc.PolicyDoc("B", 2, "b.pdf", {"cert:line": ev("Business Auto")})]).policies[0].letter == "B"


def test_package_and_unused_documents_are_noticed():
    pkg = policy(line="Commercial Package Policy")
    bare = cc.PolicyDoc("B", 2, "b.pdf", {})
    joined = cc.join([pkg, bare])
    assert joined.policies == [] and [n.kind for n in joined.notices] == ["package", "not_used"]


# --------------------------------------------------------------------- insured, sign-offs


def test_insured_claim():
    same = cc.insured_claim([policy("A"), policy("B", insured="CEDAR HOLLOW, LLC")])
    assert str(same.verdict) == "supported" and same.removable is False
    diff = cc.insured_claim([policy("A"), policy("B", insured="Cedar Hollow Holdings")])
    assert str(diff.verdict) == "not_supported" and "differs" in diff.reason
    unverified = cc.insured_claim([policy(states={"insured": EvidenceState.SCAN_READ})])
    assert str(unverified.verdict) == "not_supported"
    assert cc.insured_claim([]) is None


def test_description_and_edits_need_a_signoff():
    assert cc.description_claim("") is None
    assert str(cc.description_claim("Roofing").verdict) == "needs_signoff"
    e = cc.edited_claim("A", "cert:gl_each_occurrence", "Each Occurrence", "$2", "$1")
    assert e.key == "edited:A:cert:gl_each_occurrence" and str(e.verdict) == "needs_signoff"


# --------------------------------------------------------------------- extraction (C3)


def test_endorsement_keys():
    assert endorsement_key("CG 20 10", "type") == "cert:endorsement:CG2010:type"
    assert endorsement_key("CG 20 10", "colour") is None
    assert is_endorsement_key("cert:endorsement:CG2010:form_number")
    assert not is_endorsement_key("cert:endorsement:CG2010:colour") and not is_endorsement_key("cert:line")
    assert split_endorsement_key("cert:endorsement:WC000313:named_party") == ("WC000313", "named_party")


def test_extraction_schema_and_normalized_endorsements():
    schema = extract.build_extraction_schema(CERTIFICATE_FIELDS).json_schema
    assert "endorsements" in schema["required"] and "forms" not in schema["properties"]
    data = {"facts": [{"field": "cert:line", "value": "Business Auto", "page": 1, "quote": "Business Auto"}],
            "endorsements": [{"form_number": "CG 20 10", "edition": "04 13", "page": 2, "quote": "CG 20 10 04 13",
                              "type": {"value": "ADDITIONAL INSURED", "page": 2, "quote": "ADDITIONAL INSURED"},
                              "policy_number": {"value": None, "page": None, "quote": None},
                              "named_party": {"value": "Smith & Sons", "page": 2, "quote": "Name: Smith & Sons"},
                              "condition": {"value": None, "page": None, "quote": None}},
                             {"form_number": "CG2010", "edition": None, "page": 3, "quote": "dup"}]}
    facts, _, _ = extract.normalize_output(data, CERTIFICATE_FIELDS)
    ends = [f for f in facts if is_endorsement_key(f.field)]
    assert [(f.field, f.value) for f in ends] == [("cert:endorsement:CG2010:form_number", "CG 20 10 04 13"),
                                                  ("cert:endorsement:CG2010:named_party", "Smith & Sons"),
                                                  ("cert:endorsement:CG2010:type", "ADDITIONAL INSURED")]
    assert len([f for f in facts if not is_endorsement_key(f.field)]) == 22  # one per scalar field
    # a scripted provider may send endorsement facts as dynamic keys inside facts
    flat = {"facts": [{"field": "cert:endorsement:OM2026:type", "value": "ADDITIONAL INSURED", "page": 1,
                       "quote": "ADDITIONAL INSURED"}], "endorsements": []}
    facts, _, _ = extract.normalize_output(flat, CERTIFICATE_FIELDS)
    assert "cert:endorsement:OM2026:type" in {f.field for f in facts}


@pytest.mark.parametrize("number,edition,expected", [
    ("OM 20 26 04 24", "04 24", ("OM 20 26", "OM 20 26 04 24")),  # the live model's doubled edition
    ("OM 20 26 (04/24)", "04/24", ("OM 20 26", "OM 20 26 (04/24)")),
    ("OM 20 26 04/24", "04 24", ("OM 20 26", "OM 20 26 04/24")),  # separators ignored
    ("CG 20 10", "04 13", ("CG 20 10", "CG 20 10 04 13")),  # the asked-for shape
    ("CG 20 10", None, ("CG 20 10", "CG 20 10")),
    ("WC 00 03 13", "13", ("WC 00 03 13", "WC 00 03 13 13")),  # too short to be an edition date
    ("CG2010", "2010", ("CG2010", "CG2010 2010")),  # not its own token: part of the number
])
def test_split_edition(number, edition, expected):
    assert extract.split_edition(number, edition) == expected


def test_doubled_edition_keys_as_the_form_and_verifies():
    data = {"facts": [], "endorsements": [{
        "form_number": "OM 20 02 04 24", "edition": "04 24", "page": 1, "quote": "Form OM 20 02 04 24",
        **{p: {"value": None, "page": None, "quote": None} for p in ("type", "policy_number", "named_party",
                                                                      "condition")}}]}
    facts, _, _ = extract.normalize_output(data, CERTIFICATE_FIELDS)
    form = next(f for f in facts if is_endorsement_key(f.field))
    assert (form.field, form.value) == ("cert:endorsement:OM2002:form_number", "OM 20 02 04 24")
    assert form.value in form.quote


def test_endorsements_note_says_what_a_condition_is():
    note = extract.ENDORSEMENTS_NOTE
    assert "without its edition date" in note and "written contract" in note
    assert "are not a condition" in note


def test_merge_expands_endorsement_keys_in_the_field_order():
    r = verify.VerifyResult(field="cert:endorsement:CG2010:type", value="AI", state=V, cited_page=1, page_no=1,
                            quote="AI", matched_text="AI", method="exact", score=1.0, rects=[])
    out = merge.merge_document(CERTIFICATE_FIELDS, [r])
    keys = [m.field for m in out]
    assert "cert:endorsement:CG2010:type" in keys and "endorsements" not in keys
    assert keys.index("cert:endorsement:CG2010:type") == len(keys) - 1


def test_certificate_and_claim_blocks_join_the_citation_traversal():
    cell = Cell(value="$1", passage_ids=[7], state=V)
    block = CertificateBlock(certificate_date="2026-06-01", holder_name="H",
                             policies=[CertificatePolicy(letter="A", line="auto", line_label="Auto",
                                                         fields=[CertificateField(key="k", label="K", cell=cell)])])
    claim = ClaimBlock(id="claim-x", key="x", kind="insured", title="t", verdict="supported",
                       prose=[{"text": "t", "passage_ids": [9]}])
    content = ArtifactContent(sections=[Section(id="s", number="1", title="S", blocks=[block, claim])])
    assert content.passage_ids() == {7, 9}
    back = ArtifactContent.model_validate(content.model_dump(mode="json"))
    assert back.find_cell("certificate", "k", "A").value == "$1"
    edited = back.edit_cell("certificate", "k", "A", "$2")
    assert edited.find_cell("certificate", "k", "A").original_passage_ids == [7]


# --------------------------------------------------------------------- slugs, schema check (D4, D10)


def test_one_slug_set():
    assert db.CERTIFICATE_SLUG == slugs.CERTIFICATE_SLUG == work_queue.CERTIFICATE_SLUG == "certificate"
    assert db.COMPARE_QUOTES_SLUG is slugs.COMPARE_QUOTES_SLUG and work_queue.POLICY_CHECK_SLUG is slugs.POLICY_CHECK_SLUG
    src = open(work_queue.__file__, encoding="utf-8").read()
    assert not re.search(r'^[A-Z_]+_SLUG = "', src, re.M)  # no hand-copied slug left


def test_schema_check_names_the_slice_4_gaps(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path / 'old.db'}")
    db.init_db(engine)
    with engine.begin() as conn:  # an old database: no cache columns, old status rule, no new tables
        conn.execute(text("DROP INDEX ix_run_document_extraction_key"))
        conn.execute(text("ALTER TABLE run_document DROP COLUMN read_at"))
        conn.execute(text("DROP TABLE certificate_issue"))
    with pytest.raises(db.SchemaOutdated) as err:
        db.check_schema(engine)
    msg = str(err.value)
    assert "run_document.read_at" in msg and "extraction_key index" in msg and "certificate_issue" in msg
    engine.dispose()
    fresh = db.make_engine("sqlite://")
    db.init_db(fresh)  # a new database passes
    fresh.dispose()


# --------------------------------------------------------------------- PDF (D6, A3)


def _max_certificate(policies: int = 8) -> CertificateBlock:
    def f(key, label, value):
        return CertificateField(key=key, label=label, cell=Cell(value=value, state=V))
    rows = []
    for i in range(policies):
        line = ("general_liability", "auto", "umbrella", "workers_comp")[i % 4]
        limits = [f(k, k.split(":")[1], "$1,000,000") for k in cc.LINE_LIMITS[line]]
        rows.append(CertificatePolicy(letter="ABCDEFGH"[i], insurer="A", line=line, line_label=cc.LINE_LABELS[line],
                                      wording=list(cc.WORDINGS), fields=[
                                          f("cert:policy_number", "Policy number", f"POL-{i:07d}"),
                                          f("cert:effective_date", "Effective", "01/01/2026"),
                                          f("cert:expiration_date", "Expiration", "01/01/2027"), *limits]))
    return CertificateBlock(
        certificate_date="2026-06-01", holder_name=("Smith & <Sons> " * 9)[:120], holder_address="a & b " * 50,
        description=("<b>not bold</b> & more " * 70)[:1500], producer=f("cert:producer", "Producer", "Ruiz & Partners"),
        insured=[f("cert:named_insured", "Insured", "Cedar & <Hollow> LLC"),
                 f("cert:insured_address", "Insured address", "12 Mill Road")],
        insurers=[CertificateInsurer(letter=l, name=f"Insurer {l} & Co", naic="12345") for l in "ABCDEF"],
        policies=rows)


def test_pdf_escapes_free_text_and_stays_one_page_at_the_maxima():
    block = _max_certificate()
    assert len(block.holder_name) == 120 and len(block.holder_address) == 300 and len(block.description) == 1500
    pdf = export_certificate.render(export_certificate.CertificatePdf(block, "2026-06-01", "Run 1, version 1"))
    assert export_certificate.page_count(pdf) == 1
    import fitz

    with fitz.open(stream=pdf, filetype="pdf") as d:
        words = " ".join(d[0].get_text().split())
    assert "Smith & <Sons>" in words and "Cedar & <Hollow> LLC" in words and "<b>not bold</b>" in words


def test_pdf_prints_not_stated_for_missing_values():
    block = CertificateBlock(certificate_date="2026-06-01", holder_name="H")
    pdf = export_certificate.render(export_certificate.CertificatePdf(block, "2026-06-01", "Run 1, version 1"))
    import fitz

    with fitz.open(stream=pdf, filetype="pdf") as d:
        assert "Not stated" in d[0].get_text() and d.page_count == 1


def test_pdf_marks_the_legal_wording_as_a_sample():
    """Owner, 2026-11-03: the disclaimer and cancellation clause are the clone's own wording, marked as a sample."""
    block = CertificateBlock(certificate_date="2026-06-01", holder_name="H")
    pdf = export_certificate.render(export_certificate.CertificatePdf(block, "2026-06-01", "Run 1, version 1"))
    import fitz

    with fitz.open(stream=pdf, filetype="pdf") as d:
        words = " ".join(d[0].get_text().split())
    assert words.count("Sample wording") == 2, words
