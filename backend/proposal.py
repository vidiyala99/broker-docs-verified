"""Proposal Builder (slice 2): templates, the kind library, and run parameters.

A proposal run is a run of the seeded `proposal-builder` workflow. Its
parameters arrive from the wizard as `schemas.ProposalParameters` (untrusted),
are validated against the workflow's fact-section catalogue, and are stored
with the resolved template copied in (D6):

    client parameters ──parse_proposal_parameters──► ProposalParameters
                       ──resolve template (id or slug)──► outline = client outline or template blocks
                       ──► StoredProposal (+ TemplateSnapshot) ──► run.parameters

From then on the run, retry, regenerate and export read only run.parameters
(`runner.compose_plan`), never the live template row.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import models
from backend.schemas import (
    MAX_CONTEXT_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_FACT_SECTIONS_PER_BLOCK,
    MAX_INSTRUCTION_CHARS,
    MAX_NAME_CHARS,
    MAX_OUTLINE_BLOCKS,
    MAX_TITLE_CHARS,
    BlockKind,
    OutlineBlock,
    ParameterErrors,
    StoredProposal,
    TemplateSnapshot,
    outline_errors,
    parse_proposal_parameters,
)

# Blocks the wizard can add (locked kinds are never added). `fact_sections` is the
# default binding for a new block of that kind; the client may change it.
KIND_LIBRARY: list[dict[str, Any]] = [
    {"kind": str(BlockKind.CLIENT_DETAILS), "title": "Client and Carrier Details", "table": True,
     "description": "Named insured, carrier, rating and policy term per quote.",
     "fact_sections": ["common_declarations"]},
    {"kind": str(BlockKind.LINE_COMPARISON), "title": "Coverage Comparison", "table": True,
     "description": "One line of coverage side by side.", "fact_sections": ["coverage"]},
    {"kind": str(BlockKind.PREMIUM_COMPARISON), "title": "Premium Comparison", "table": True,
     "description": "Rate, premium, taxes and fees side by side.", "fact_sections": ["rate", "premium"]},
    {"kind": str(BlockKind.SUBJECTIVITIES), "title": "Subjectivities and Binding Terms", "table": True,
     "description": "Conditions to bind and quote expiry.", "fact_sections": ["subjectivities"]},
    {"kind": str(BlockKind.NARRATIVE), "title": "Narrative", "table": False,
     "description": "Prose only, from the facts of the fact sections it names.", "fact_sections": []},
]

LIMITS = {"max_blocks": MAX_OUTLINE_BLOCKS, "max_title_chars": MAX_TITLE_CHARS,
          "max_description_chars": MAX_DESCRIPTION_CHARS, "max_instruction_chars": MAX_INSTRUCTION_CHARS,
          "max_fact_sections_per_block": MAX_FACT_SECTIONS_PER_BLOCK, "max_context_chars": MAX_CONTEXT_CHARS,
          "max_name_chars": MAX_NAME_CHARS}


def find_template(s: Session, key: str | int) -> models.ProposalTemplate | None:
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        return s.get(models.ProposalTemplate, int(key))
    return s.scalar(select(models.ProposalTemplate).where(models.ProposalTemplate.slug == key))


def template_summary(t: models.ProposalTemplate) -> dict[str, Any]:
    return {"id": t.id, "slug": t.slug, "name": t.name, "description": t.description,
            "block_count": len(t.blocks or [])}


def fact_section_catalogue(wf: models.Workflow) -> list[dict[str, Any]]:
    """Fact sections a block can bind to, each with its fields (for the wizard)."""
    return [{"id": sec["id"], "title": sec["title"],
             "fields": [{"key": f["key"], "label": f["label"]} for f in wf.fact_schema if f.get("section") == sec["id"]]}
            for sec in wf.section_plan]


def template_full(t: models.ProposalTemplate, wf: models.Workflow) -> dict[str, Any]:
    blocks = [OutlineBlock.model_validate(b).model_dump(mode="json") for b in t.blocks or []]
    return {**template_summary(t), "workflow": wf.slug, "blocks": blocks, "theme": t.theme or {},
            "variables": list(t.variables or []), "kinds": KIND_LIBRARY,
            "fact_sections": fact_section_catalogue(wf), "limits": LIMITS}


def prepare_parameters(s: Session, wf: models.Workflow, raw: Any) -> dict[str, Any]:
    """Validate a proposal run's parameters and snapshot the template (D6).

    Returns the JSON to store in run.parameters. Raises ParameterErrors (locations
    relative to `parameters`) for anything invalid, including an unknown template.
    """
    known = {sec["id"] for sec in wf.section_plan}
    params = parse_proposal_parameters(raw, known)
    tpl = find_template(s, params.template_id)
    if tpl is None:
        raise ParameterErrors([(("template_id",), f"No template {params.template_id}.")])
    blocks = [OutlineBlock.model_validate(b) for b in tpl.blocks or []]
    outline = params.outline if params.outline is not None else blocks
    problems = outline_errors(outline, known)
    if problems:  # only reachable for a broken template row: the client outline was checked above
        raise ParameterErrors([(("outline", *loc), msg) for loc, msg in problems])
    stored = StoredProposal(
        template_id=tpl.id, broker_name=params.broker_name, brokerage_name=params.brokerage_name,
        client_name=params.client_name, additional_context=params.additional_context,
        no_ai_prose=params.no_ai_prose, outline=list(outline),
        template=TemplateSnapshot(id=tpl.id, slug=tpl.slug, name=tpl.name, blocks=blocks,
                                  theme=dict(tpl.theme or {}), variables=list(tpl.variables or [])))
    return stored.model_dump(mode="json")
