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
