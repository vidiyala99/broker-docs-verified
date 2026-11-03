"""Slice 3b T3 (D10): an old local database fails fast at startup with a clear instruction.

There is no migration framework: `create_all` never adds `run.line` to an existing table, so a
database made before slice 3b would start and then fail on the first runs query. init_db checks
for `run.line` and the `task` table first and stops with a message naming the file to delete and
the reseed command.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import db, models
from backend.context import Settings
from backend.main import create_app
from backend.providers import FakeProvider


def _old_db(path, *, drop: str) -> None:
    """A database with the current schema minus one slice 3b piece."""
    engine = db.make_engine(f"sqlite:///{path}")
    db.init_db(engine)
    engine.dispose()
    con = sqlite3.connect(path)
    try:
        if drop == "task":
            con.execute("DROP TABLE task")
        else:  # rebuild run without the line column, the way a pre-3b database has it
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute("CREATE TABLE run_old (id INTEGER PRIMARY KEY, workspace_id INTEGER, workflow_id INTEGER)")
            con.execute("DROP TABLE run")
            con.execute("ALTER TABLE run_old RENAME TO run")
        con.commit()
    finally:
        con.close()


@pytest.mark.parametrize("drop", ["line", "task"])
def test_init_db_on_an_old_database_names_the_file_and_the_reseed_command(tmp_path, drop):
    path = tmp_path / "brokerdocs.db"
    _old_db(path, drop=drop)
    engine = db.make_engine(f"sqlite:///{path}")
    try:
        with pytest.raises(db.SchemaOutdated) as info:
            db.init_db(engine)
    finally:
        engine.dispose()
    message = str(info.value)
    assert str(path) in message
    assert "delete" in message.lower()
    assert "python -m backend.dev_seed seed" in message
    assert ("run.line" if drop == "line" else "task") in message


def test_app_startup_on_an_old_database_fails_with_the_message(tmp_path, monkeypatch):
    monkeypatch.setenv("BROKERDOCS_DATA_DIR", str(tmp_path))
    path = tmp_path / "brokerdocs.db"
    _old_db(path, drop="line")
    app = create_app(Settings(database_url=f"sqlite:///{path}"), provider=FakeProvider(lambda r: {}))
    with pytest.raises(db.SchemaOutdated, match="backend.dev_seed seed"):
        with TestClient(app, base_url="http://127.0.0.1"):
            pass


def test_a_new_or_current_database_starts(tmp_path):
    path = tmp_path / "brokerdocs.db"
    for _ in range(2):  # new, then current (idempotent)
        engine = db.make_engine(f"sqlite:///{path}")
        db.init_db(engine)
        engine.dispose()
    con = sqlite3.connect(path)
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(run)")}
        task_cols = {row[1] for row in con.execute("PRAGMA table_info(task)")}
        indexes = {row[1] for row in con.execute("PRAGMA index_list(task)")}
    finally:
        con.close()
    assert "line" in cols
    assert {"id", "workspace_id", "title", "due_date", "status", "run_id", "discrepancy_item_id",
            "discrepancy_title", "created_at", "updated_at", "completed_at"} == task_cols
    assert any("run_id" in name for name in indexes)  # P1: task.run_id is indexed


def test_task_run_id_is_not_a_foreign_key():
    """D2: a plain integer, so removing a run never erases the link (run_missing)."""
    assert not models.Task.__table__.c.run_id.foreign_keys
