"""Work queue M8: the startup schema check (D10 of slice 3b) also requires the four work-queue
columns on `run`, the queue_state check constraint and the (line, queue_state) index. One test per
missing element, plus a database exactly as slice 3b left it (all of them missing)."""

from __future__ import annotations

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import db
from backend.context import Settings
from backend.main import create_app
from backend.providers import FakeProvider

COLUMN_LINE = r"\n\t{name} [^\n]*?,"


def _current(path) -> None:
    engine = db.make_engine(f"sqlite:///{path}")
    db.init_db(engine)
    engine.dispose()


def _rebuild_run(path, *, drop_columns=(), drop_constraint=False, keep_index=True) -> None:
    """Recreate `run` from its own CREATE TABLE text minus some columns and/or the queue_state
    check, the way a database made before the work queue has it."""
    con = sqlite3.connect(path)
    try:
        sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='run'").fetchone()[0]
        for name in drop_columns:
            sql, n = re.subn(COLUMN_LINE.format(name=name), "", sql)
            assert n == 1, name
        if drop_constraint:
            sql, n = re.subn(r", \n\tCONSTRAINT ck_run_queue_state CHECK \([^\n]*\)\)", "", sql)
            assert n == 1
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("DROP TABLE run")
        con.execute(sql)
        if keep_index:
            con.execute("CREATE INDEX ix_run_line_queue_state ON run (line, queue_state)")
        con.commit()
    finally:
        con.close()


def _drop_index(path) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("DROP INDEX ix_run_line_queue_state")
        con.commit()
    finally:
        con.close()


def _message(path) -> str:
    engine = db.make_engine(f"sqlite:///{path}")
    try:
        with pytest.raises(db.SchemaOutdated) as info:
            db.init_db(engine)
    finally:
        engine.dispose()
    message = str(info.value)
    assert str(path) in message and "delete" in message.lower()
    assert "python -m backend.dev_seed seed" in message
    return message


@pytest.mark.parametrize("column", ["client_name", "proposal_run_id", "queue_changed_at"])
def test_a_missing_queue_column_fails_fast(tmp_path, column):
    path = tmp_path / "brokerdocs.db"
    _current(path)
    _rebuild_run(path, drop_columns=[column])
    message = _message(path)
    assert f"run.{column} column" in message
    assert "queue_state" not in message  # only the missing piece is named


def test_a_missing_queue_state_column_fails_fast(tmp_path):
    path = tmp_path / "brokerdocs.db"
    _current(path)
    _rebuild_run(path, drop_columns=["queue_state"], drop_constraint=True, keep_index=False)
    message = _message(path)
    assert "run.queue_state column" in message


def test_a_missing_queue_state_constraint_fails_fast(tmp_path):
    path = tmp_path / "brokerdocs.db"
    _current(path)
    _rebuild_run(path, drop_constraint=True)
    message = _message(path)
    assert "ck_run_queue_state" in message and " column" not in message


def test_a_missing_line_queue_state_index_fails_fast(tmp_path):
    path = tmp_path / "brokerdocs.db"
    _current(path)
    _drop_index(path)
    message = _message(path)
    assert "ix_run_line_queue_state" in message and "ck_run_queue_state" not in message


def test_a_slice_3b_database_names_every_missing_piece(tmp_path):
    path = tmp_path / "brokerdocs.db"
    _current(path)
    _rebuild_run(path, drop_columns=["client_name", "queue_state", "proposal_run_id", "queue_changed_at"],
                 drop_constraint=True, keep_index=False)
    message = _message(path)
    for piece in ("run.client_name", "run.queue_state column", "run.proposal_run_id", "run.queue_changed_at",
                  "ck_run_queue_state", "ix_run_line_queue_state"):
        assert piece in message
    assert "run.line" not in message and "task table" not in message


def test_app_startup_on_a_pre_queue_database_fails_with_the_message(tmp_path, monkeypatch):
    monkeypatch.setenv("BROKERDOCS_DATA_DIR", str(tmp_path))
    path = tmp_path / "brokerdocs.db"
    _current(path)
    _rebuild_run(path, drop_columns=["client_name"])
    app = create_app(Settings(database_url=f"sqlite:///{path}"), provider=FakeProvider(lambda r: {}))
    with pytest.raises(db.SchemaOutdated, match="run.client_name"):
        with TestClient(app, base_url="http://127.0.0.1"):
            pass


def test_a_current_database_has_the_queue_columns_constraint_and_index(tmp_path):
    path = tmp_path / "brokerdocs.db"
    for _ in range(2):  # new, then current (idempotent)
        _current(path)
    con = sqlite3.connect(path)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(run)")}
        indexes = {row[1]: [c[2] for c in con.execute(f"PRAGMA index_info({row[1]})")]
                   for row in con.execute("PRAGMA index_list(run)")}
        with pytest.raises(sqlite3.IntegrityError, match="ck_run_queue_state"):
            con.execute("INSERT INTO run (workspace_id, workflow_id, line, parameters, status, retryable, tokens_in, "
                        "tokens_out, cost_usd, created_at, updated_at, queue_state) VALUES "
                        "(1, 1, 'commercial', '{}', 'done', 0, 0, 0, 0, '2026', '2026', 'archived')")
    finally:
        con.close()
    assert set(db.QUEUE_COLUMNS) <= cols
    assert indexes["ix_run_line_queue_state"] == ["line", "queue_state"]
