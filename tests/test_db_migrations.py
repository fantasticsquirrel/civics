import sqlite3

from civics_app.db import connect, init_db


def test_schema_is_idempotent_and_has_production_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICS_DB", str(tmp_path / "civics.db"))
    init_db(); init_db()
    with connect() as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"audit_feedback", "bill_actions", "schema_migrations"} <= names
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        versions = [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == [1, 2, 3, 4]


def test_legacy_ingestion_table_is_upgraded_before_index_creation(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE ingestion_runs (
            id INTEGER PRIMARY KEY, source_name TEXT NOT NULL, status TEXT NOT NULL,
            requested_limit INTEGER NOT NULL DEFAULT 0, bills_seen INTEGER NOT NULL DEFAULT 0,
            bills_upserted INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL, completed_at TEXT
        )""")
    monkeypatch.setenv("CIVICS_DB", str(path))
    init_db()
    with connect() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(ingestion_runs)")}
        indexes = {row[1] for row in db.execute("PRAGMA index_list(ingestion_runs)")}
    assert "jurisdiction_code" in columns
    assert "idx_ingestion_source" in indexes
