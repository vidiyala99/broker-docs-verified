"""eval_hook: the proposal-aware path (slice 2, T1), offline (FakeProvider).

Lane A adds the real `proposal-builder` workflow and the template table; until
then these tests seed a stand-in workflow (the Compare Quotes schema under the
proposal slug) and a stand-in template table to exercise the hook end to end.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import JSON, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from backend import db, eval_hook, models
from backend.providers import FakeProvider
from test_proposal_runner import responder as proposal_responder
from pipeline_fakes import smart_responder

ROOT = Path(__file__).resolve().parents[2]
QUOTES = ROOT / "tests" / "fixtures" / "quotes"
PROPOSALS = ROOT / "tests" / "fixtures" / "proposals"


def _gates():
    sys.path.insert(0, str(ROOT / "tests" / "eval"))
    try:
        return importlib.import_module("eval_gates")
    finally:
        sys.path.remove(str(ROOT / "tests" / "eval"))


def _workflow_seeded() -> bool:
    try:
        eval_hook._require_workflow(eval_hook.PROPOSAL_SLUG)
    except eval_hook.WorkflowUnavailable:
        return False
    return True


@pytest.fixture
def seeded_proposal_workflow(monkeypatch):
    """Seed a stand-in `proposal-builder` workflow in every database the hook creates."""
    if _workflow_seeded():  # lane A merged: use the real seed
        return
    real_seed = db.seed

    def seed(session):
        real_seed(session)
        session.flush()
        if session.query(models.Workflow).filter_by(slug=eval_hook.PROPOSAL_SLUG).first() is None:
            session.add(models.Workflow(workspace_id=db.DEFAULT_WORKSPACE_ID,
                                        **{**db.COMPARE_QUOTES, "slug": eval_hook.PROPOSAL_SLUG,
                                           "name": "Proposal Builder (eval stand-in)"}))

    monkeypatch.setattr(db, "seed", seed)


# --------------------------------------------------------------------- workflow selection


def test_absent_proposal_workflow_raises_before_any_model_call(monkeypatch):
    if _workflow_seeded():
        pytest.skip("lane A merged: the proposal workflow exists")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # no provider is built first
    calls = []
    with pytest.raises(eval_hook.WorkflowUnavailable) as exc:
        eval_hook.run_eval_batch([str(PROPOSALS / "prop_amended_f.pdf")], workflow="proposal-builder",
                                 provider=FakeProvider(lambda req: calls.append(req)))
    assert isinstance(exc.value, NotImplementedError)
    assert "proposal-builder" in str(exc.value) and "not seeded" in str(exc.value)
    assert calls == []
    with pytest.raises(eval_hook.WorkflowUnavailable):
        eval_hook.run_eval_batch([str(PROPOSALS / "prop_amended_f.pdf")], workflow="proposal-builder")


def test_unknown_workflow_is_rejected():
    with pytest.raises(ValueError):
        eval_hook.run_eval_batch([str(QUOTES / "carrier_a_clean.pdf")], workflow="coi-check",
                                 provider=FakeProvider([]))


def test_default_call_is_compare_quotes_without_a_proposal_block():
    out = eval_hook.run_eval_batch([str(QUOTES / "carrier_a_clean.pdf")],
                                   provider=FakeProvider(smart_responder()))
    # slice 6 (T6): a Compare run also returns its artifact response's cells and marks ("compare")
    assert set(out) == {"input_tokens", "documents", "compare"}


# --------------------------------------------------------------------- proposal run


@pytest.fixture(scope="module")
def proposal_truths():
    manifest = json.loads((PROPOSALS / "manifest.json").read_text(encoding="utf-8"))
    props = {e["proposal_id"]: json.loads((PROPOSALS / e["truth"]).read_text(encoding="utf-8"))
             for e in manifest["proposals"]}
    docs = {e["fixture_id"]: json.loads((PROPOSALS / e["truth"]).read_text(encoding="utf-8"))
            for e in manifest["documents"]}
    return props, docs


def test_proposal_run_reports_cells_and_prose(seeded_proposal_workflow, proposal_truths):
    props, docs = proposal_truths
    p = props["prop_amended"]
    paths = {fid: str(PROPOSALS / docs[fid]["file"]) for fid in p["documents"]}
    calls = []

    def respond(req):
        calls.append(req.role)
        return proposal_responder(req)

    out = eval_hook.run_eval_batch(list(paths.values()), workflow="proposal-builder",
                                   parameters=dict(p["parameters"]), template=p["template"],
                                   provider=FakeProvider(respond))
    block = out["proposal"]
    assert block["workflow"] == "proposal-builder" and block["run_status"] in ("done", "partial")
    assert set(out["documents"]) == set(paths.values())
    by_doc = {}
    for c in block["cells"]:
        assert set(c) == {"block_id", "row", "column", "document", "value", "state", "citations",
                          "conflicts"}
        by_doc.setdefault(c["document"], []).append(c)
        for cite in c["citations"]:
            assert cite["document"] == c["document"] and isinstance(cite["page"], int)
        if c["state"] in ("verified", "scan_read"):
            assert c["value"] and c["citations"]
        else:
            assert c["value"] is None
    assert set(by_doc) == set(paths.values())
    # A verified cell carries the page the truth has (fake model copies the line).
    f_path = paths["prop_amended_f"]
    named = next(c for c in by_doc[f_path] if c["row"] == "named_insured")
    assert named["value"] == docs["prop_amended_f"]["facts"]["named_insured"]["value"]
    assert [x["page"] for x in named["citations"]] == [1]
    assert block["prose"] and any(seg["citations"] for seg in block["prose"])
    if eval_hook._template_model() is None:
        assert any("static section plan" in w for w in block["warnings"])
    assert calls  # the pipeline ran through the fake model

    g = _gates()
    sc = g.score_proposal(p, docs, out, paths)
    assert sc.hard_fails == [], sc.hard_fails
    assert sc.unlisted == [], sc.unlisted
    assert sc.cells_correct > 0 and sc.prose_segments > 0


def test_no_ai_prose_flag_is_passed_through(seeded_proposal_workflow, proposal_truths, monkeypatch):
    props, docs = proposal_truths
    p = props["prop_two_carrier_absent"]
    seen = {}
    real_submit = eval_hook.RunManager.submit

    def submit(self, workflow_id, document_ids, parameters=None):
        seen["parameters"] = parameters
        return real_submit(self, workflow_id, document_ids, parameters)

    monkeypatch.setattr(eval_hook.RunManager, "submit", submit)
    eval_hook.run_eval_batch([str(PROPOSALS / docs[f]["file"]) for f in p["documents"]],
                             workflow="proposal-builder", parameters=dict(p["parameters"]),
                             template=p["template"], provider=FakeProvider(smart_responder()))
    assert seen["parameters"]["no_ai_prose"] is True
    assert seen["parameters"]["broker_name"] == p["parameters"]["broker_name"]


# --------------------------------------------------------------------- template and outline


class _Base(DeclarativeBase):
    pass


class _Template(_Base):
    __tablename__ = "proposal_template"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(50))
    blocks: Mapped[list] = mapped_column(JSON)


_BLOCKS = [
    {"id": "cover", "kind": "cover", "title": "Cover Page", "description": "d", "ai_instruction": "",
     "locked": True},
    {"id": "client", "kind": "client_details", "title": "Client Details", "description": "d",
     "ai_instruction": "Summarize the insured.", "locked": False, "fact_sections": ["common_declarations"]},
]


def test_outline_is_a_copy_of_the_template_blocks():
    assert eval_hook.outline_from_blocks(_BLOCKS) == [
        {"id": "cover", "kind": "cover", "title": "Cover Page", "ai_instruction": ""},
        {"id": "client", "kind": "client_details", "title": "Client Details",
         "ai_instruction": "Summarize the insured.", "fact_sections": ["common_declarations"]},
    ]


def test_parameters_are_validated_and_snapshotted_like_the_route():
    engine = db.make_engine("sqlite://")
    db.init_db(engine)
    with db.session_scope(db.make_session_factory(engine)) as s:
        params, warnings = eval_hook.proposal_parameters(
            s, {"broker_name": "B", "no_ai_prose": True, "stray": 1}, "standard")
        assert warnings == [] and "stray" not in params
        assert params["template"]["slug"] == "standard" and params["no_ai_prose"] is True
        assert [b["id"] for b in params["outline"]][:2] == ["cover", "toc"]
        with pytest.raises(ValueError):
            eval_hook.proposal_parameters(s, {}, "missing")
