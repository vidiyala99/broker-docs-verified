"""Database setup: SQLite via SQLAlchemy, plus the seeded workflows (Compare Quotes,
Proposal Builder) and proposal templates (Standard, Premier).

The database lives in `broker-docs-verified/data/` (gitignored) unless
BROKERDOCS_DATA_DIR points elsewhere. Tests use in-memory SQLite.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend.schemas import FactField, OutlineBlock, SectionPlanItem

DEFAULT_WORKSPACE_ID = 1
COMPARE_QUOTES_SLUG = "compare-quotes"


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


def init_db(engine: Engine) -> None:
    """Create tables and seed the default workspace and workflows (idempotent)."""
    models.Base.metadata.create_all(engine)
    with session_scope(make_session_factory(engine)) as session:
        seed(session)


def seed(session: Session) -> None:
    """Insert or update the seeded rows (S6): a database created before a seed change gets
    the current definitions at the next start. Rows are matched by slug; ids never change."""
    if session.get(models.Workspace, DEFAULT_WORKSPACE_ID) is None:
        session.add(models.Workspace(id=DEFAULT_WORKSPACE_ID, name="Local workspace"))
        session.flush()
    for spec in (COMPARE_QUOTES, PROPOSAL_BUILDER):
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
                           "generic title (such as 'Commercial General Liability Policy'). If no form number "
                           "or edition is printed on the pages read, return null.")),
    FactField(key="rate", label="Rate", section="rate",
              description="Rate as stated, for example per $1,000 of exposure."),
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

COMPARE_QUOTES: dict = {
    "slug": COMPARE_QUOTES_SLUG,
    "name": "Compare Quotes",
    "category": "Quoting",
    "input_slots": [
        {"key": "quotes", "label": "Quotes", "accepts": "application/pdf", "min": 1, "max": 8}
    ],
    "instructions": (
        "Compare the uploaded insurance quotes side by side. Report only values "
        "stated in the documents, each with the page and an exact quote."
    ),
    "section_plan": [s.model_dump() for s in _SECTIONS],
    "fact_schema": [f.model_dump() for f in _FIELDS],
}


# --------------------------------------------------------------------- Proposal Builder seed (slice 2)
#
# The Compare Quotes fact schema plus per-coverage facts for the cyber and E&O
# sub-tables. Its `section_plan` is the catalogue of fact sections that outline
# blocks bind to (D3); a proposal run always composes from its outline.

PROPOSAL_BUILDER_SLUG = "proposal-builder"

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
        {"key": "quotes", "label": "Quotes", "accepts": "application/pdf", "min": 1, "max": 8}
    ],
    "instructions": (
        "Read the uploaded carrier quotes for a client proposal. Report only values stated in the "
        "documents, each with the page and an exact quote. A coverage the quote does not include "
        "has no value."
    ),
    "section_plan": [s.model_dump() for s in _PROPOSAL_FACT_SECTIONS],
    "fact_schema": [f.model_dump() for f in [*_FIELDS, *_CYBER_FIELDS, *_EO_FIELDS]],
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
