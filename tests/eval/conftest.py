"""pytest wiring for the eval suite.

* Registers the ``live`` marker (no pyproject change needed).
* Live tests run only when selected with ``-m live`` (a bare ``pytest`` never
  calls the model, even with a key in the environment).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "quotes"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "live: calls the real model; runs only with -m live and OPENAI_API_KEY set")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    markexpr = config.getoption("-m", default="") or ""
    if "live" in markexpr and "not live" not in markexpr:
        return
    skip = pytest.mark.skip(reason="live eval runs only with: pytest -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture(scope="session")
def manifest() -> dict:
    return json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def truths(manifest: dict) -> dict[str, dict]:
    return {
        e["fixture_id"]: json.loads((FIXTURE_DIR / e["truth"]).read_text(encoding="utf-8"))
        for e in manifest["fixtures"]
    }


# Proposal Builder fixtures (slice 2, T1): their own folder and manifest.
PROPOSAL_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "proposals"


@pytest.fixture(scope="session")
def proposal_dir() -> Path:
    return PROPOSAL_DIR


@pytest.fixture(scope="session")
def proposal_manifest() -> dict:
    return json.loads((PROPOSAL_DIR / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def proposals(proposal_manifest: dict) -> dict[str, dict]:
    return {
        e["proposal_id"]: json.loads((PROPOSAL_DIR / e["truth"]).read_text(encoding="utf-8"))
        for e in proposal_manifest["proposals"]
    }


@pytest.fixture(scope="session")
def proposal_doc_truths(proposal_manifest: dict) -> dict[str, dict]:
    return {
        e["fixture_id"]: json.loads((PROPOSAL_DIR / e["truth"]).read_text(encoding="utf-8"))
        for e in proposal_manifest["documents"]
    }


# Policy Check fixtures (slice 3, T7): their own folder and manifest.
POLICY_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "policy"


@pytest.fixture(scope="session")
def policy_dir() -> Path:
    return POLICY_DIR


@pytest.fixture(scope="session")
def policy_manifest() -> dict:
    return json.loads((POLICY_DIR / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def policy_cases(policy_manifest: dict) -> dict[str, dict]:
    return {
        e["case_id"]: json.loads((POLICY_DIR / e["truth"]).read_text(encoding="utf-8"))
        for e in policy_manifest["cases"]
    }


@pytest.fixture(scope="session")
def policy_doc_truths(policy_manifest: dict) -> dict[str, dict]:
    return {
        e["fixture_id"]: json.loads((POLICY_DIR / e["truth"]).read_text(encoding="utf-8"))
        for e in policy_manifest["documents"]
    }
