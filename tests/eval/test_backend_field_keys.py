"""Guard: the eval's field keys must be backend FactField keys.

The backend's canonical fact fields (FactField(key=...)) are defined in
backend/db.py (``_FIELDS``, typed by backend/schemas.py). The eval scores a
subset of them, so every key of ``quote_generator.FIELDS`` must exist there.
Skipped when the backend (or its dependencies) is not importable.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

import quote_generator as qg

REPO_ROOT = Path(__file__).resolve().parents[2]


def _backend_keys() -> set[str]:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from backend.db import _FIELDS
        from backend.schemas import FactField
    except Exception as exc:  # backend or its deps (SQLAlchemy, pydantic) missing
        keys = _keys_from_source()
        if keys is None:
            pytest.skip(f"backend not importable: {exc!r}")
        return keys
    assert all(isinstance(f, FactField) for f in _FIELDS)
    return {f.key for f in _FIELDS}


def _keys_from_source() -> set[str] | None:
    """Fallback when backend deps are not installed: read the FactField(key=...)
    literals out of backend/db.py without importing it."""
    src = REPO_ROOT / "backend" / "db.py"
    if not src.is_file():
        return None
    keys = set()
    for node in ast.walk(ast.parse(src.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "FactField":
            for kw in node.keywords:
                if kw.arg == "key" and isinstance(kw.value, ast.Constant):
                    keys.add(kw.value.value)
    return keys or None


def test_eval_fields_are_subset_of_backend_fact_field_keys():
    backend = _backend_keys()
    extra = set(qg.FIELDS) - backend
    assert not extra, f"eval FIELDS keys not in backend FactField keys: {sorted(extra)}"


def test_total_cost_is_not_scored_and_backend_has_20_fields():
    backend = _backend_keys()
    assert "total_cost" not in qg.FIELDS
    assert len(backend) == 20
