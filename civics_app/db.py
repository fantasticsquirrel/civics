from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from civics_app.auth import hash_api_token, token_prefix

DEFAULT_DB = "/opt/civics/data/civics.db"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path() -> str:
    return os.environ.get("CIVICS_DB", DEFAULT_DB)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = Path(db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _add_column(db: sqlite3.Connection, table: str, definition: str) -> None:
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL, description TEXT NOT NULL,
                examples_positive TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id), email TEXT UNIQUE NOT NULL,
                role TEXT NOT NULL DEFAULT 'user', api_token TEXT UNIQUE, api_token_hash TEXT,
                api_token_prefix TEXT UNIQUE, active INTEGER NOT NULL DEFAULT 1,
                notification_email TEXT NOT NULL DEFAULT '', telegram_chat_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_interests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
                category_id INTEGER NOT NULL REFERENCES categories(id), min_severity TEXT NOT NULL DEFAULT 'low',
                jurisdictions TEXT NOT NULL DEFAULT 'all', active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(user_id, category_id)
            );
            CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_key TEXT UNIQUE NOT NULL,
                jurisdiction_kind TEXT NOT NULL, jurisdiction_code TEXT NOT NULL, session TEXT NOT NULL,
                chamber TEXT NOT NULL, bill_number TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
                status TEXT NOT NULL, source_name TEXT NOT NULL, source_url TEXT NOT NULL, text_url TEXT NOT NULL,
                introduced_at TEXT NOT NULL, updated_at TEXT NOT NULL, text_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, bill_id INTEGER NOT NULL REFERENCES bills(id),
                bill_version_id INTEGER REFERENCES bill_versions(id), taxonomy_version TEXT NOT NULL,
                prompt_version TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT,
                UNIQUE(bill_version_id, taxonomy_version, prompt_version)
            );
            CREATE TABLE IF NOT EXISTS audit_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT, audit_run_id INTEGER NOT NULL REFERENCES audit_runs(id),
                bill_id INTEGER NOT NULL REFERENCES bills(id), category_id INTEGER NOT NULL REFERENCES categories(id),
                flag_state TEXT NOT NULL, severity TEXT NOT NULL, confidence REAL NOT NULL,
                rationale TEXT NOT NULL, citation TEXT NOT NULL, user_summary TEXT NOT NULL,
                affected_groups TEXT NOT NULL DEFAULT '[]', concerns TEXT NOT NULL DEFAULT '[]',
                UNIQUE(audit_run_id, category_id)
            );
            CREATE TABLE IF NOT EXISTS bill_user_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
                bill_id INTEGER NOT NULL REFERENCES bills(id), audit_run_id INTEGER NOT NULL REFERENCES audit_runs(id),
                category_id INTEGER NOT NULL REFERENCES categories(id), status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL, UNIQUE(user_id, bill_id, category_id)
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
                match_id INTEGER NOT NULL REFERENCES bill_user_matches(id), channel TEXT NOT NULL DEFAULT 'in_app',
                title TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, delivered_at TEXT, UNIQUE(user_id, match_id, channel)
            );
            CREATE TABLE IF NOT EXISTS ingestion_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_name TEXT NOT NULL,
                jurisdiction_code TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                requested_limit INTEGER NOT NULL DEFAULT 0, bills_seen INTEGER NOT NULL DEFAULT 0,
                bills_upserted INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                provider_health TEXT NOT NULL DEFAULT '{}', started_at TEXT NOT NULL, completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS bill_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, bill_id INTEGER NOT NULL REFERENCES bills(id),
                text_hash TEXT NOT NULL, source_url TEXT NOT NULL, text_url TEXT NOT NULL,
                raw_payload TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, UNIQUE(bill_id, text_hash)
            );
            CREATE TABLE IF NOT EXISTS bill_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, bill_id INTEGER NOT NULL REFERENCES bills(id),
                action_date TEXT NOT NULL, description TEXT NOT NULL, source_name TEXT NOT NULL,
                source_url TEXT NOT NULL DEFAULT '', UNIQUE(bill_id, action_date, description)
            );
            CREATE TABLE IF NOT EXISTS audit_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, bill_id INTEGER NOT NULL REFERENCES bills(id),
                bill_version_id INTEGER NOT NULL REFERENCES bill_versions(id), taxonomy_version TEXT NOT NULL,
                prompt_version TEXT NOT NULL, provider TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                started_at TEXT, completed_at TEXT,
                UNIQUE(bill_version_id, taxonomy_version, prompt_version, provider)
            );
            CREATE TABLE IF NOT EXISTS notification_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
                digest_frequency TEXT NOT NULL DEFAULT 'instant', channels TEXT NOT NULL DEFAULT '["in_app"]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS saved_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
                name TEXT NOT NULL, filters TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(user_id, name)
            );
            CREATE TABLE IF NOT EXISTS audit_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
                audit_flag_id INTEGER NOT NULL REFERENCES audit_flags(id), feedback_type TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                UNIQUE(user_id, audit_flag_id, feedback_type)
            );
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_matches_user ON bill_user_matches(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_notifications_delivery ON notifications(status, channel, created_at);
            CREATE INDEX IF NOT EXISTS idx_interests_user ON user_interests(user_id, active);
            CREATE INDEX IF NOT EXISTS idx_audit_jobs_status ON audit_jobs(status, created_at);
            """
        )
        additions = {
            "users": ["api_token TEXT", "api_token_hash TEXT", "api_token_prefix TEXT", "active INTEGER NOT NULL DEFAULT 1",
                      "notification_email TEXT NOT NULL DEFAULT ''", "telegram_chat_id TEXT NOT NULL DEFAULT ''"],
            "categories": ["updated_at TEXT"],
            "ingestion_runs": ["provider_health TEXT NOT NULL DEFAULT '{}'", "jurisdiction_code TEXT NOT NULL DEFAULT ''"],
            "audit_runs": ["bill_version_id INTEGER REFERENCES bill_versions(id)", "model TEXT NOT NULL DEFAULT ''"],
            "audit_flags": ["affected_groups TEXT NOT NULL DEFAULT '[]'", "concerns TEXT NOT NULL DEFAULT '[]'"],
            "notifications": ["attempts INTEGER NOT NULL DEFAULT 0", "last_error TEXT NOT NULL DEFAULT ''"],
        }
        for table, definitions in additions.items():
            for definition in definitions:
                _add_column(db, table, definition)
        # Compatibility columns must exist before indexes that reference them.
        # Older databases otherwise fail startup before the ALTER path can run.
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ingestion_source "
            "ON ingestion_runs(source_name, jurisdiction_code, id DESC)"
        )
        audit_runs_sql = (db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='audit_runs'").fetchone() or [""])[0]
        if "UNIQUE(bill_id, taxonomy_version, prompt_version)" in (audit_runs_sql or ""):
            db.commit()
            db.execute("PRAGMA foreign_keys = OFF")
            db.executescript(
                """
                CREATE TABLE audit_runs_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, bill_id INTEGER NOT NULL REFERENCES bills(id),
                    bill_version_id INTEGER REFERENCES bill_versions(id), taxonomy_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT,
                    UNIQUE(bill_version_id, taxonomy_version, prompt_version)
                );
                INSERT INTO audit_runs_new(id,bill_id,bill_version_id,taxonomy_version,prompt_version,provider,model,status,created_at,completed_at)
                SELECT ar.id,ar.bill_id,
                    (SELECT bv.id FROM bill_versions bv WHERE bv.bill_id=ar.bill_id ORDER BY bv.id DESC LIMIT 1),
                    ar.taxonomy_version,ar.prompt_version,ar.provider,ar.model,ar.status,ar.created_at,ar.completed_at
                FROM audit_runs ar;
                DROP TABLE audit_runs;
                ALTER TABLE audit_runs_new RENAME TO audit_runs;
                """
            )
            db.execute("PRAGMA foreign_keys = ON")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_token_prefix ON users(api_token_prefix) WHERE api_token_prefix IS NOT NULL")
        for row in db.execute("SELECT id,api_token FROM users WHERE api_token IS NOT NULL AND api_token != ''").fetchall():
            legacy = row["api_token"]
            encoded = None if legacy == "demo-token" else hash_api_token(legacy)
            prefix = None if legacy == "demo-token" else token_prefix(legacy)
            db.execute("UPDATE users SET api_token_hash=?,api_token_prefix=?,api_token=NULL WHERE id=?", (encoded, prefix, row["id"]))
        migrations = (
            (1, "baseline"), (2, "hashed-token-rbac-indexes"),
            (3, "audit-delivery-timeline-feedback"), (4, "version-scoped-audit-runs"),
        )
        for version, name in migrations:
            db.execute("INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) VALUES (?,?,?)", (version, name, utcnow()))


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def decode_json_field(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (json.JSONDecodeError, TypeError):
        return fallback
