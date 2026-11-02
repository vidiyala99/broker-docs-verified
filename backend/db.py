"""Database setup: SQLite via SQLAlchemy, plus the seeded Compare Quotes workflow.

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
from backend.schemas import FactField, SectionPlanItem

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
    if session.get(models.Workspace, DEFAULT_WORKSPACE_ID) is None:
        session.add(models.Workspace(id=DEFAULT_WORKSPACE_ID, name="Local workspace"))
        session.flush()
    exists = session.scalar(
        select(models.Workflow).where(models.Workflow.slug == COMPARE_QUOTES_SLUG)
    )
    if exists is None:
        session.add(models.Workflow(workspace_id=DEFAULT_WORKSPACE_ID, **COMPARE_QUOTES))


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
              description="Policy form name or form number, for example occurrence or claims-made."),
    FactField(key="rate", label="Rate", section="rate",
              description="Rate as stated, for example per $1,000 of exposure."),
    FactField(key="total_premium", label="Total Premium", section="premium",
              description="Total premium payable, including taxes and fees if stated as one figure."),
    FactField(key="taxes_and_fees", label="Taxes and Fees", section="premium",
              description="Surplus lines taxes, policy fees or inspection fees as stated."),
    FactField(key="minimum_earned_premium", label="Minimum Earned Premium", section="premium",
              description="Minimum earned premium, as an amount or percent."),
    FactField(key="each_occurrence_limit", label="Each Occurrence Limit", section="coverage",
              description="Per-occurrence or per-claim limit."),
    FactField(key="aggregate_limit", label="Aggregate Limit", section="coverage",
              description="General or policy aggregate limit."),
    FactField(key="retention", label="Deductible / Retention", section="coverage",
              description="Deductible or self-insured retention."),
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
