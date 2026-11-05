"""Database setup: SQLite via SQLAlchemy, plus the seeded workflows (Compare Quotes,
Proposal Builder, Policy Check) and proposal templates (Standard, Premier).

The database lives in `broker-docs-verified/data/` (gitignored) unless
BROKERDOCS_DATA_DIR points elsewhere. Tests use in-memory SQLite.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.schemas import (
    CERTIFICATE_FIELDS,
    LOSS_RUN_FIELDS,
    POLICY_CHECK_FIELDS,
    FactField,
    OutlineBlock,
    SectionPlanItem,
)
from backend.slugs import (
    CERTIFICATE_SLUG,
    COMPARE_QUOTES_SLUG,
    LOSS_RUN_SLUG,
    POLICY_CHECK_SLUG,
    PROPOSAL_BUILDER_SLUG,
)

# Re-exported for the modules that read db.*_SLUG; the one definition is backend/slugs.py (D4).
__all__ = ["CERTIFICATE_SLUG", "COMPARE_QUOTES_SLUG", "LOSS_RUN_SLUG", "POLICY_CHECK_SLUG", "PROPOSAL_BUILDER_SLUG"]

DEFAULT_WORKSPACE_ID = 1


def data_dir() -> Path:
    """Folder for uploaded files and the database (created on demand)."""
    root = os.environ.get("BROKERDOCS_DATA_DIR")
    path = Path(root) if root else Path(__file__).resolve().parent.parent / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_url(directory: Path | None = None) -> str:
    return f"sqlite:///{(directory or data_dir()) / 'brokerdocs.db'}"


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    memory = url in ("sqlite://", "sqlite:///:memory:")
    kwargs: dict = {"connect_args": {"check_same_thread": False}}  # parse/verify thread pool (M6)
    if memory:
        kwargs["poolclass"] = StaticPool  # one shared in-memory database
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        if not memory:
            cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit on success, roll back on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


RESEED_COMMAND = "backend/.venv/Scripts/python -m backend.dev_seed seed"


class SchemaOutdated(RuntimeError):
    """The database predates a schema change; there are no migrations, so it must be recreated."""


QUEUE_COLUMNS = ("client_name", "queue_state", "proposal_run_id", "queue_changed_at")  # work queue (M8)
QUEUE_STATE_CONSTRAINT = "ck_run_queue_state"
QUEUE_INDEX = "ix_run_line_queue_state"


def check_schema(engine: Engine) -> None:
    """Fail fast on a database made before a schema change (D10 of slice 3b, extended for the work
    queue by M8): `create_all` adds missing tables and never a missing column, constraint or index
    on an existing table, so an old `run` table would start and then fail on the first runs query
    (or silently lack the queue_state rule). Required on `run`: `line`, the four work-queue columns,
    the queue_state check constraint and the (line, queue_state) index; plus the `task` table.
    A new (empty) database passes."""
    insp = inspect(engine)
    names = set(insp.get_table_names())
    if "run" not in names:
        return
    missing = []
    columns = {c["name"] for c in insp.get_columns("run")}
    for column in ("line", *QUEUE_COLUMNS):
        if column not in columns:
            missing.append(f"the run.{column} column")
    if QUEUE_STATE_CONSTRAINT not in {c.get("name") for c in insp.get_check_constraints("run")}:
        missing.append(f"the run.queue_state check constraint ({QUEUE_STATE_CONSTRAINT})")
    if QUEUE_INDEX not in {i.get("name") for i in insp.get_indexes("run")}:
        missing.append(f"the run (line, queue_state) index ({QUEUE_INDEX})")
    if "task" not in names:
        missing.append("the task table")
    missing += _certificate_schema_gaps(insp, names)
    if "document" in names and "source_format" not in {c["name"] for c in insp.get_columns("document")}:
        missing.append("the document.source_format column")  # slice 6 (D1, F7)
    if not missing:
        return
    where = engine.url.database or "the in-memory database"
    raise SchemaOutdated(
        f"The database {where} was created before a schema change (slice 3b, the work queue, slice 4 "
        f"certificates or slice 6 Word and Excel quotes) "
        f"and lacks {', '.join(missing)}. There are no migrations: stop the server, delete {where} (and its "
        f"-wal and -shm files if present), then start again for an empty database, or reseed dev data with: "
        f"{RESEED_COMMAND} (with BROKERDOCS_DATA_DIR set to a scratch folder).")


CACHE_COLUMNS = ("extraction_key", "reused_from_run_id", "read_at")  # run_document (slice 4, D3/D8)
CACHE_INDEX = "ix_run_document_extraction_key"
DISCREPANCY_STATUS_CONSTRAINT = "ck_discrepancy_status"


def _certificate_schema_gaps(insp, names: set[str]) -> list[str]:
    """Slice 4 (D10 extended): the cache columns and index on run_document, page.ocr_key, the widened
    discrepancy_state status rule (open | resolved | removed) and its reset_from column, and the holder
    and certificate_issue tables."""
    missing = []
    if "run_document" in names:
        columns = {c["name"] for c in insp.get_columns("run_document")}
        missing += [f"the run_document.{c} column" for c in CACHE_COLUMNS if c not in columns]
        if "document_kind" not in columns:  # slice 6, Compare Quotes column label
            missing.append("the run_document.document_kind column")
        if CACHE_INDEX not in {i.get("name") for i in insp.get_indexes("run_document")}:
            missing.append(f"the run_document extraction_key index ({CACHE_INDEX})")
    if "page" in names and "ocr_key" not in {c["name"] for c in insp.get_columns("page")}:
        missing.append("the page.ocr_key column")
    if "discrepancy_state" in names:
        if "reset_from" not in {c["name"] for c in insp.get_columns("discrepancy_state")}:
            missing.append("the discrepancy_state.reset_from column")
        rule = next((c for c in insp.get_check_constraints("discrepancy_state")
                     if c.get("name") == DISCREPANCY_STATUS_CONSTRAINT), None)
        if rule is None or "removed" not in str(rule.get("sqltext", "")):
            missing.append("the discrepancy_state status rule with 'removed'")
    for table in ("holder", "certificate_issue"):
        if table not in names:
            missing.append(f"the {table} table")
    return missing + _loss_run_schema_gaps(insp, names)


# Slice 5a: the loss-run tables are new (create_all adds them to an older database), but a database made by an
# earlier build of them must carry every column the ledger writes and the indexes the paged claims API uses.
LOSS_RUN_COLUMNS = {
    "claim_row": ("location", "carrier_key", "role", "counted", "group_key", "eff_paid", "eff_reserve",
                  "eff_incurred", "eff_recovery", "eff_deductible", "claim_paid", "claim_reserve", "claim_incurred",
                  "claim_open", "claim_date", "decision", "decision_id", "decision_stale", "role_reason", "flags"),
    "claim_decision": ("row_key", "kind", "amounts", "basis", "active", "replaced_id"),
    "printed_total": ("metric", "scope_kind", "scope_detail", "location", "verify_state"),
    "loss_window": ("first_page", "last_page", "status"),
}
LOSS_RUN_INDEXES = {"claim_row": ("ix_claim_row_run_role", "ix_claim_row_run_claim_key", "ix_claim_row_run_group",
                                  "ix_claim_row_run_claim_incurred", "ix_claim_row_run_claim_date")}


def _loss_run_schema_gaps(insp, names: set[str]) -> list[str]:
    missing = []
    for table, columns in LOSS_RUN_COLUMNS.items():
        if table not in names:
            continue  # create_all adds it
        have = {c["name"] for c in insp.get_columns(table)}
        missing += [f"the {table}.{c} column" for c in columns if c not in have]
        indexes = {i.get("name") for i in insp.get_indexes(table)}
        missing += [f"the {table} index {i}" for i in LOSS_RUN_INDEXES.get(table, ()) if i not in indexes]
    return missing


def exit_if_outdated(url: str | None = None) -> None:
    """For the server entry points: print the SchemaOutdated message and exit before uvicorn starts
    (a failure inside the lifespan would bury it in a traceback)."""
    engine = make_engine(url)
    try:
        check_schema(engine)
    except SchemaOutdated as exc:
        raise SystemExit(str(exc)) from None
    finally:
        engine.dispose()


def init_db(engine: Engine) -> None:
    """Check the schema is current (D10), create tables and seed the default workspace and
    workflows (idempotent)."""
    check_schema(engine)
    models.Base.metadata.create_all(engine)
    with session_scope(make_session_factory(engine)) as session:
        seed(session)


def seed(session: Session) -> None:
    """Insert or update the seeded rows (S6): a database created before a seed change gets
    the current definitions at the next start. Rows are matched by slug; ids never change."""
    if session.get(models.Workspace, DEFAULT_WORKSPACE_ID) is None:
        session.add(models.Workspace(id=DEFAULT_WORKSPACE_ID, name="Local workspace"))
        session.flush()
    for spec in (COMPARE_QUOTES, PROPOSAL_BUILDER, POLICY_CHECK, CERTIFICATE, LOSS_RUN):
        _upsert(session, models.Workflow, spec, workspace_id=DEFAULT_WORKSPACE_ID)
    for spec in PROPOSAL_TEMPLATES:
        _upsert(session, models.ProposalTemplate, spec, workspace_id=DEFAULT_WORKSPACE_ID)


def _upsert(session: Session, model: type, spec: dict, **extra) -> None:
    row = session.scalar(select(model).where(model.slug == spec["slug"]))
    if row is None:
        session.add(model(**extra, **spec))
        session.flush()
        return
    for key, value in spec.items():
        if getattr(row, key) != value:
            setattr(row, key, value)


# --------------------------------------------------------------------- Compare Quotes seed

_FIELDS = [
    FactField(key="carrier_name", label="Carrier", section="common_declarations",
              description="Insurance company issuing the quote (not the broker)."),
    FactField(key="named_insured", label="Named Insured", section="common_declarations",
              description="Legal name of the insured as stated on the quote."),
    FactField(key="address", label="Address", section="common_declarations",
              description="Mailing or principal address of the named insured."),
    FactField(key="admitted_status", label="Admitted Status", section="common_declarations",
              description="Admitted or non-admitted (surplus lines) as stated."),
    FactField(key="am_best_rating", label="AM Best Rating", section="common_declarations",
              description="Carrier's AM Best financial strength rating, for example A- or A+."),
    FactField(key="policy_term", label="Policy Term", section="common_declarations",
              description="Policy period or term, for example dates or a number of months."),
    FactField(key="territory", label="Territory", section="common_declarations",
              description="Coverage territory."),
    FactField(key="producer", label="Producer", section="common_declarations",
              description="Producing broker or agent named on the quote."),
    FactField(key="policy_form", label="Policy Form", section="common_declarations",
              description=("The coverage form number and edition date followed by the form title, exactly as "
                           "printed in the policy, for example 'CG 00 01 04 13 Commercial General Liability "
                           "Coverage Form (Occurrence)'. Not a running page header and not the policy's "
                           "generic title (such as 'Commercial General Liability Policy'), and not the coverage "
                           "part an endorsement says it modifies (such as 'Commercial General Liability "
                           "Coverage Part' in 'This endorsement modifies insurance provided under the ...'): "
                           "that sentence names the coverage the endorsement attaches to, it does not state or "
                           "amend the policy form. If no form number or edition is printed on the pages read, "
                           "return null.")),
    FactField(key="rate", label="Rate", section="rate",
              description="Rate as stated, for example per $1,000 of exposure. Null when only a premium amount is given (a premium is not a rate)."),
    FactField(key="total_premium", label="Total Premium", section="premium",
              description="Total premium payable, including taxes and fees if stated as one figure."),
    FactField(key="taxes_and_fees", label="Taxes and Fees", section="premium",
              description="Surplus lines taxes, policy fees or inspection fees as stated."),
    FactField(key="minimum_earned_premium", label="Minimum Earned Premium", section="premium",
              description="Minimum earned premium, as an amount or percent."),
    FactField(key="each_occurrence_limit", label="Each Occurrence Limit", section="coverage",
              description="Per-occurrence or per-claim limit of the general or policy-wide coverage. Null for cyber or E&O limits; use the cyber and E&O fields."),
    FactField(key="aggregate_limit", label="Aggregate Limit", section="coverage",
              description="General or policy aggregate limit."),
    FactField(key="retention", label="Deductible / Retention", section="coverage",
              description="Deductible or self-insured retention of the general or policy-wide coverage. Null for cyber or E&O retentions; use the cyber and E&O fields."),
    FactField(key="key_endorsements", label="Key Endorsements", section="endorsements",
              description="Notable endorsements that broaden or restrict coverage."),
    FactField(key="key_exclusions", label="Key Exclusions", section="endorsements",
              description="Notable exclusions."),
    FactField(key="subjectivities", label="Subjectivities", section="subjectivities",
              description="Conditions that must be met before or after binding."),
    FactField(key="quote_expiration", label="Quote Valid Until", section="subjectivities",
              description="Date the quote expires or must be bound by."),
]

# Slice 6 (R1, R9): Compare Quotes only (Proposal Builder keeps _FIELDS). The three endorsement terms are the
# printed wording, classified in code (pipeline.compare_rules); notable_exclusions asks the same call for the
# printed exclusion titles, the schedule heading and the document kind. They open the Endorsements section.
_COMPARE_TERM_FIELDS = [
    FactField(key="additional_insured", label="Additional insured", section="endorsements",
              description=("How the quote grants additional insured status, copied as printed together with its "
                           "label, for example 'Additional Insured: Blanket where required by written contract', "
                           "'Additional Insured - Designated Person or Organization' or 'Additional Insured: Not "
                           "included'. Null when the document does not mention additional insureds.")),
    FactField(key="waiver_of_subrogation", label="Waiver of subrogation", section="endorsements",
              description=("Waiver of subrogation (waiver of transfer of rights of recovery), copied as printed "
                           "together with its label, for example 'Blanket Waiver of Subrogation', 'Waiver of "
                           "Subrogation: Scheduled' or 'Waiver of Subrogation: Not included'. Null when the "
                           "document does not mention it.")),
    FactField(key="primary_noncontributory", label="Primary & non-contributory", section="endorsements",
              description=("Primary and non-contributory wording, copied as printed together with its label, for "
                           "example 'Primary and Non-Contributory: Included' or 'Primary & Non-Contributory - Not "
                           "available'. Null when the document does not mention it.")),
    FactField(key="notable_exclusions", label="Notable exclusions", section="endorsements", kind="exclusions",
              description="The exclusion titles the document prints and the heading of its exclusions schedule."),
]


def _compare_fields() -> list[FactField]:
    at = next(i for i, f in enumerate(_FIELDS) if f.key == "key_endorsements")
    return [*_FIELDS[:at], *_COMPARE_TERM_FIELDS, *_FIELDS[at:]]


_SECTIONS = [
    SectionPlanItem(id="executive_summary", number="1", title="Executive Summary",
                    guidance="Which quotes were compared and the main differences."),
    SectionPlanItem(id="common_declarations", number="2", title="Common Declarations",
                    guidance="Table of declarations, one column per carrier."),
    SectionPlanItem(id="rate_premium", number="3", title="Rate Analysis and Premium Summary"),
    SectionPlanItem(id="rate", number="3.1", title="Rate"),
    SectionPlanItem(id="premium", number="3.2", title="Premium"),
    SectionPlanItem(id="coverage", number="4", title="Coverage Specifications"),
    SectionPlanItem(id="endorsements", number="5", title="Key Endorsements and Exclusions"),
    SectionPlanItem(id="subjectivities", number="6", title="Subjectivities and Binding Terms"),
    SectionPlanItem(id="consistency", number="7", title="Cross-Quote Consistency Flags",
                    guidance="Differences in named insured, address or term between quotes."),
]

# Upload formats a slot can list in `accepts` (F10: one constant per format; the upload page derives its picker from
# the slot). Word and Excel quotes go only in the Compare Quotes and Proposal Builder `quotes` slots (R5).
PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
QUOTE_TYPES = f"{PDF_TYPE},{DOCX_TYPE},{XLSX_TYPE}"

COMPARE_QUOTES: dict = {
    "slug": COMPARE_QUOTES_SLUG,
    "name": "Compare Quotes",
    "category": "Quoting",
    "input_slots": [
        {"key": "quotes", "label": "Quotes", "accepts": QUOTE_TYPES, "min": 1, "max": 8}
    ],
    "instructions": (
        "Compare the uploaded insurance quotes side by side. Report only values "
        "stated in the documents, each with the page and an exact quote."
    ),
    "section_plan": [s.model_dump() for s in _SECTIONS],
    "fact_schema": [f.model_dump() for f in _compare_fields()],
}


# --------------------------------------------------------------------- Proposal Builder seed (slice 2)
#
# The Compare Quotes fact schema plus per-coverage facts for the cyber and E&O
# sub-tables. Its `section_plan` is the catalogue of fact sections that outline
# blocks bind to (D3); a proposal run always composes from its outline.

_CYBER_FIELDS = [
    FactField(key="cyber_aggregate_limit", label="Cyber Aggregate Limit", section="cyber",
              description="Cyber liability policy aggregate limit. Null if the quote has no cyber coverage."),
    FactField(key="cyber_retention", label="Cyber Retention", section="cyber",
              description="Cyber retention or deductible. Null if the quote has no cyber coverage."),
    FactField(key="cyber_ransomware_sublimit", label="Ransomware / Extortion Sublimit", section="cyber",
              description="Cyber extortion or ransomware sublimit, as stated."),
    FactField(key="cyber_business_interruption", label="Business Interruption Sublimit", section="cyber",
              description="Cyber business interruption or system failure sublimit, and waiting period if stated."),
    FactField(key="cyber_social_engineering", label="Social Engineering Sublimit", section="cyber",
              description="Social engineering or funds transfer fraud sublimit, as stated."),
]

_EO_FIELDS = [
    FactField(key="eo_each_claim_limit", label="E&O Each Claim Limit", section="eo",
              description="Errors and omissions (professional liability) each claim limit. Null if no E&O coverage."),
    FactField(key="eo_aggregate_limit", label="E&O Aggregate Limit", section="eo",
              description="Errors and omissions aggregate limit."),
    FactField(key="eo_retention", label="E&O Retention", section="eo",
              description="Errors and omissions retention or deductible."),
    FactField(key="eo_retroactive_date", label="E&O Retroactive Date", section="eo",
              description="Retroactive date for claims-made E&O coverage, or 'Full prior acts' as stated."),
    FactField(key="eo_defense_costs", label="E&O Defense Costs", section="eo",
              description="Whether defense costs are inside or outside the E&O limit, as stated."),
]

# Fact-section catalogue (D3): ids blocks bind to, with the title a table takes
# when its block shows more than one fact section.
_PROPOSAL_FACT_SECTIONS = [
    SectionPlanItem(id="common_declarations", number="1", title="Client and Carrier Details"),
    SectionPlanItem(id="rate", number="2", title="Rate"),
    SectionPlanItem(id="premium", number="3", title="Premium"),
    SectionPlanItem(id="coverage", number="4", title="General Liability Coverage"),
    SectionPlanItem(id="endorsements", number="5", title="Key Endorsements and Exclusions"),
    SectionPlanItem(id="cyber", number="6", title="Cyber Liability"),
    SectionPlanItem(id="eo", number="7", title="Errors and Omissions"),
    SectionPlanItem(id="subjectivities", number="8", title="Subjectivities and Binding Terms"),
]

PROPOSAL_BUILDER: dict = {
    "slug": PROPOSAL_BUILDER_SLUG,
    "name": "Proposal Builder",
    "category": "Quoting",
    "input_slots": [
        {"key": "quotes", "label": "Quotes", "accepts": QUOTE_TYPES, "min": 1, "max": 8}  # Build proposal (R5)
    ],
    "instructions": (
        "Read the uploaded carrier quotes for a client proposal. Report only values stated in the "
        "documents, each with the page and an exact quote. A coverage the quote does not include "
        "has no value."
    ),
    "section_plan": [s.model_dump() for s in _PROPOSAL_FACT_SECTIONS],
    "fact_schema": [f.model_dump() for f in [*_FIELDS, *_CYBER_FIELDS, *_EO_FIELDS]],
}


# --------------------------------------------------------------------- Policy Check seed (slice 3)
#
# Four document roles as input slots (D8 A): a run maps each document to one slot,
# min and max are enforced at run creation. A required slot (min >= 1) whose document
# fails fails the run; an optional one shows "Not read" (D11 A). The fact schema is the
# combined extraction schema (T3, schemas.POLICY_CHECK_FIELDS): scalar fields plus the forms
# field. PDF roles extract it; the AMS role fills the scalar keys from the AMS key table (D5).

POLICY_CHECK: dict = {
    "slug": POLICY_CHECK_SLUG,
    "name": "Policy Check",
    "category": "Policy Review",
    "input_slots": [
        {"key": "expiring", "label": "Expiring Policy", "accepts": "application/pdf", "min": 1, "max": 1},
        {"key": "renewal", "label": "Renewal Policy", "accepts": "application/pdf", "min": 1, "max": 1},
        {"key": "quote", "label": "Quote", "accepts": "application/pdf", "min": 0, "max": 1},
        {"key": "ams", "label": "AMS Export", "accepts": "application/json,text/csv", "min": 0, "max": 1},
    ],
    "instructions": (
        "Read an insurance policy document for a renewal check. Report only values stated in the "
        "document, each with the page and an exact quote. A coverage the document does not state "
        "has no value."
    ),
    "section_plan": [
        SectionPlanItem(id="discrepancies", number="1", title="Discrepancies",
                        guidance="Differences between the renewal and the other documents.").model_dump(),
    ],
    "fact_schema": [f.model_dump() for f in POLICY_CHECK_FIELDS],
}


# --------------------------------------------------------------------- Certificate seed (slice 4)
#
# One slot, 1 to 8 PDFs: each a policy's declarations or an endorsement (C7, D2). The fact schema is the
# combined cert: pass (schemas.CERTIFICATE_FIELDS). COMPOSING builds the draft in code
# (pipeline.certificate, C5: no model call after extraction).

CERTIFICATE: dict = {
    "slug": CERTIFICATE_SLUG,
    "name": "Certificate of Insurance",
    "category": "Certificates",
    "input_slots": [
        {"key": "policies", "label": "Policies", "accepts": "application/pdf", "min": 1, "max": 8},
    ],
    "instructions": (
        "Read an insurance policy document for a certificate of insurance: its declarations and any "
        "additional insured, waiver of subrogation or primary and non-contributory endorsements. Report only "
        "values stated in the document, each with the page and an exact quote."
    ),
    "section_plan": [
        SectionPlanItem(id="certificate", number="1", title="Certificate").model_dump(),
        SectionPlanItem(id="checks", number="2", title="Checks",
                        guidance="Each claim the certificate makes, checked against the policies.").model_dump(),
    ],
    "fact_schema": [f.model_dump() for f in CERTIFICATE_FIELDS],
}


# --------------------------------------------------------------------- Loss run seed (slice 5a)
#
# One slot, 1 to 8 loss-run files: PDFs and spreadsheets (.xlsx, .csv). PDF document facts come from the lr: fact
# pass (LOSS_RUN_FIELDS); claim rows and printed totals from loss_runs.pdf_rows (PDF) or loss_runs.sheets (code).
# COMPOSING builds the review in code (loss_runs.report): no compose call.

SPREADSHEET_TYPES = f"text/csv,{XLSX_TYPE}"

LOSS_RUN: dict = {
    "slug": LOSS_RUN_SLUG,
    "name": "Loss Review",
    "category": "Analytics",
    "input_slots": [
        {"key": "loss_runs", "label": "Loss runs", "accepts": f"application/pdf,{SPREADSHEET_TYPES}",
         "min": 1, "max": 8},
    ],
    "instructions": (
        "Read an insurance carrier's loss run: the carrier, the insured and the date the amounts are valued as of. "
        "Report only values stated in the document, each with the page and an exact quote."
    ),
    "section_plan": [
        SectionPlanItem(id="totals", number="1", title="Totals").model_dump(),
        SectionPlanItem(id="checks", number="2", title="Checks",
                        guidance="Printed totals reconciled, conflicting claims, unverified rows.").model_dump(),
    ],
    "fact_schema": [f.model_dump() for f in LOSS_RUN_FIELDS],
}


# --------------------------------------------------------------------- proposal templates (slice 2)


def _block(id_: str, kind: str, title: str, description: str, ai_instruction: str = "",
           fact_sections: tuple[str, ...] = ()) -> dict:
    return OutlineBlock(id=id_, kind=kind, title=title, description=description, ai_instruction=ai_instruction,
                        fact_sections=list(fact_sections)).model_dump(mode="json")


_COVER = _block("cover", "cover_page", "Cover Page", "Proposal title, client and broker.")
_TOC = _block("toc", "table_of_contents", "Table of Contents", "The sections of this proposal.")
_EXEC = _block("executive_summary", "narrative", "Executive Summary",
               "Which quotes were received and how they differ.",
               "Summarize the quotes received and the main differences in premium and limits.",
               ("common_declarations", "premium", "coverage"))
_CLIENT = _block("client_details", "client_details", "Client and Carrier Details",
                 "Named insured, carrier, rating and policy term per quote.",
                 "Note any difference in named insured, address or term between quotes.",
                 ("common_declarations",))
_COVERAGE = _block("coverage", "line_comparison", "Coverage Comparison",
                   "Limits and retentions side by side.",
                   "Point out where limits or retentions differ.", ("coverage",))
_PREMIUM = _block("premium", "premium_comparison", "Premium Comparison",
                  "Rate, premium, taxes and fees side by side.",
                  "State each quote's total premium and any minimum earned premium.", ("rate", "premium"))
_SUBJ = _block("subjectivities", "subjectivities", "Subjectivities and Binding Terms",
               "Conditions to bind and quote expiry.",
               "List what each carrier needs before binding and when each quote expires.", ("subjectivities",))
_NEXT = _block("next_steps", "narrative", "Next Steps", "What the client needs to do to bind.",
               "Explain the steps to bind, including each quote's expiry date.", ("subjectivities",))

PROPOSAL_TEMPLATES: list[dict] = [
    {
        "slug": "standard",
        "name": "Standard",
        "description": "A concise proposal: summary, client details, coverage, premium and binding terms.",
        "blocks": [_COVER, _TOC, _EXEC, _CLIENT, _COVERAGE, _PREMIUM, _SUBJ, _NEXT],
        "theme": {"font_family": "Calibri", "body_size_pt": 10.5, "heading_color": "#1F3A5F",
                  "heading_sizes_pt": {"1": 20, "2": 15, "3": 12}, "accent_color": "#2F6FB0"},
        "variables": ["agency_name", "broker_name", "client_name"],
    },
    {
        "slug": "premier",
        "name": "Premier",
        "description": "A full proposal that adds endorsements, cyber and E&O comparisons.",
        "blocks": [
            _COVER, _TOC, _EXEC, _CLIENT, _COVERAGE,
            _block("endorsements", "line_comparison", "Endorsements and Exclusions",
                   "Notable endorsements and exclusions per quote.",
                   "Point out endorsements or exclusions that broaden or restrict coverage.", ("endorsements",)),
            _block("cyber", "line_comparison", "Cyber Liability", "Cyber limits, retention and sublimits.",
                   "Compare cyber limits and sublimits; say plainly when a quote has no cyber coverage.",
                   ("cyber",)),
            _block("eo", "line_comparison", "Errors and Omissions", "E&O limits, retention and retro date.",
                   "Compare E&O limits, retention and retroactive date.", ("eo",)),
            _PREMIUM, _SUBJ, _NEXT,
        ],
        "theme": {"font_family": "Georgia", "body_size_pt": 11, "heading_color": "#4A1F3D",
                  "heading_sizes_pt": {"1": 24, "2": 16, "3": 12.5}, "accent_color": "#9C6B30"},
        "variables": ["agency_name", "broker_name", "client_name"],
    },
]
