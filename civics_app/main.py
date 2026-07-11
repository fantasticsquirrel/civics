from __future__ import annotations

import json
import sqlite3
import hashlib
import base64
import re
from contextlib import asynccontextmanager

from civics_app.auth import current_user, require_admin, require_system_admin, validate_bootstrap_token
from civics_app.audit_providers import PROMPT_VERSION, configured_audit_provider
from civics_app.congress import CongressGovClient, normalize_congress_bill
from civics_app.db import connect, decode_json_field, init_db, rows_to_dicts, utcnow
from civics_app.legiscan import LegiScanClient, normalize_legiscan_bill
from civics_app.services.accounts import create_account
from civics_app.services.audits import latest_bill_versions, taxonomy_version
from civics_app.services.bills import audit_bill_for_version, immutable_version_payload, provider_actions, validated_sort
from civics_app.services.notifications import delivery_channels
from civics_app.services.providers import safe_provider_error
from civics_app.ui import HTML as APP_HTML
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

APP_TITLE = "Civics Radar"


DEFAULT_CATEGORIES = [
    {
        "slug": "education",
        "name": "Education",
        "description": "Schools, student services, curriculum, libraries, teacher pay, higher education, or vocational training.",
        "examples_positive": "teacher, school, student, curriculum, university, scholarship",
    },
    {
        "slug": "healthcare",
        "name": "Healthcare",
        "description": "Hospitals, public health, insurance, Medicaid/Medicare, mental health, prescription drugs, or healthcare access.",
        "examples_positive": "health, hospital, Medicaid, Medicare, prescription, mental health",
    },
    {
        "slug": "housing",
        "name": "Housing & Homelessness",
        "description": "Rent, zoning, affordable housing, homelessness services, eviction, mortgages, or tenant protections.",
        "examples_positive": "housing, rent, eviction, homeless, zoning, mortgage",
    },
    {
        "slug": "civil-rights",
        "name": "Civil Rights & Voting",
        "description": "Voting access, discrimination, privacy, free speech, due process, policing, or equal protection issues.",
        "examples_positive": "voting, discrimination, privacy, police, rights, election",
    },
    {
        "slug": "tax-budget",
        "name": "Taxes & Budget",
        "description": "Appropriations, revenue, taxes, fees, debt, fiscal notes, or budget authorizations.",
        "examples_positive": "appropriation, tax, budget, revenue, fee, grant",
    },
]

DEMO_BILLS = [
    {
        "canonical_key": "us-119-hr-1234",
        "jurisdiction_kind": "federal",
        "jurisdiction_code": "US",
        "session": "119",
        "chamber": "House",
        "bill_number": "H.R. 1234",
        "title": "Civic Classroom and Library Grant Act",
        "summary": "Authorizes grants for school civic education programs, library media literacy training, and teacher professional development.",
        "status": "Introduced",
        "source_name": "Congress.gov",
        "source_url": "https://www.congress.gov/",
        "text_url": "https://www.congress.gov/",
        "introduced_at": "2026-06-01",
        "updated_at": "2026-06-18T12:00:00Z",
        "text_hash": "demo-fed-education-001",
    },
    {
        "canonical_key": "ca-2025-ab-42",
        "jurisdiction_kind": "state",
        "jurisdiction_code": "CA",
        "session": "2025-2026",
        "chamber": "Assembly",
        "bill_number": "AB 42",
        "title": "Tenant Stability and Emergency Rental Assistance Act",
        "summary": "Creates a state emergency rental assistance fund and new reporting requirements for eviction diversion programs.",
        "status": "In committee",
        "source_name": "OpenStates",
        "source_url": "https://openstates.org/",
        "text_url": "https://openstates.org/",
        "introduced_at": "2026-05-21",
        "updated_at": "2026-06-17T15:00:00Z",
        "text_hash": "demo-ca-housing-001",
    },
    {
        "canonical_key": "tx-89-sb-77",
        "jurisdiction_kind": "state",
        "jurisdiction_code": "TX",
        "session": "89",
        "chamber": "Senate",
        "bill_number": "SB 77",
        "title": "Prescription Drug Transparency and Rural Clinic Support Act",
        "summary": "Requires prescription drug price transparency reports and creates grants for rural health clinics.",
        "status": "Passed Senate",
        "source_name": "OpenStates",
        "source_url": "https://openstates.org/",
        "text_url": "https://openstates.org/",
        "introduced_at": "2026-04-12",
        "updated_at": "2026-06-16T10:30:00Z",
        "text_hash": "demo-tx-health-001",
    },
]

SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}


class AccountCreateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    account_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    role: str = Field(default="user", pattern=r"^(user|admin)$")


class CategoryIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(min_length=5, max_length=1000)
    examples_positive: str = Field(default="", max_length=2000)


class CategoryUpdateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    active: bool | None = None
    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, min_length=5, max_length=1000)
    examples_positive: str | None = Field(default=None, max_length=2000)


class InterestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_slugs: list[str] = Field(max_length=50)
    min_severity: str = Field(default="low", pattern=r"^(low|medium|high)$")
    jurisdictions: list[str] = Field(default_factory=lambda: ["all"])

    @field_validator("jurisdictions")
    @classmethod
    def valid_jurisdictions(cls, values: list[str]) -> list[str]:
        cleaned = [v.upper() if v.lower() != "all" else "all" for v in values]
        if not cleaned or any(v != "all" and (len(v) != 2 or not v.isalpha()) for v in cleaned):
            raise ValueError("jurisdictions must contain 'all' or two-letter codes")
        return list(dict.fromkeys(cleaned))


class SavedViewIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    filters: dict[str, Any]

    @field_validator("filters")
    @classmethod
    def valid_filters(cls, filters: dict[str, Any]) -> dict[str, Any]:
        allowed = {"search", "jurisdiction", "status", "category", "severity", "sort", "order"}
        if set(filters) - allowed:
            raise ValueError("saved-view filters contain unsupported fields")
        if any(not isinstance(value, str) or len(value) > 200 for value in filters.values()):
            raise ValueError("saved-view filter values must be short strings")
        if len(json.dumps(filters)) > 2_000:
            raise ValueError("saved-view filters are too large")
        return filters


class NotificationPreferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest_frequency: str = Field(default="instant", pattern=r"^(instant|daily|weekly|off)$")
    channels: list[str] = Field(default_factory=lambda: ["in_app"])
    notification_email: EmailStr | None = None
    telegram_chat_id: str | None = Field(default=None, max_length=100, pattern=r"^-?[0-9]{1,20}$")

    @field_validator("channels")
    @classmethod
    def valid_channels(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(values))
        if not cleaned or any(value not in {"in_app", "email", "telegram"} for value in cleaned):
            raise ValueError("channels must contain in_app, email, and/or telegram")
        return cleaned


class AuditFeedbackIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    feedback_type: str = Field(pattern=r"^(wrong_tag|severity_too_high|severity_too_low|not_relevant)$")
    note: str = Field(default="", max_length=1000)


class CongressSampleSyncIn(BaseModel):
    bills: list[dict[str, Any]] = Field(max_length=250)


def seed_defaults() -> dict[str, int]:
    init_db()
    with connect() as db:
        now = utcnow()
        for c in DEFAULT_CATEGORIES:
            db.execute(
                "INSERT OR IGNORE INTO categories(slug,name,description,examples_positive,active,created_at) VALUES (?,?,?,?,1,?)",
                (c["slug"], c["name"], c["description"], c["examples_positive"], now),
            )
        return {"categories": len(DEFAULT_CATEGORIES)}


def upsert_bills(bills: list[dict[str, Any]]) -> int:
    seed_defaults()
    with connect() as db:
        upserted = 0
        for b in bills:
            cur = db.execute(
                """
                INSERT INTO bills(canonical_key,jurisdiction_kind,jurisdiction_code,session,chamber,bill_number,title,summary,status,source_name,source_url,text_url,introduced_at,updated_at,text_hash)
                VALUES (:canonical_key,:jurisdiction_kind,:jurisdiction_code,:session,:chamber,:bill_number,:title,:summary,:status,:source_name,:source_url,:text_url,:introduced_at,:updated_at,:text_hash)
                ON CONFLICT(canonical_key) DO UPDATE SET
                    title=excluded.title, summary=excluded.summary, status=excluded.status,
                    chamber=excluded.chamber, source_url=excluded.source_url, text_url=excluded.text_url,
                    updated_at=excluded.updated_at, text_hash=excluded.text_hash
                """,
                b,
            )
            # SQLite reports 1 for insert and update; this is useful as touched/upserted count.
            upserted += max(cur.rowcount, 0)
            bill_row = db.execute("SELECT id FROM bills WHERE canonical_key=?", (b["canonical_key"],)).fetchone()
            if bill_row:
                raw_payload = b.get("raw_payload", b)
                raw_json = immutable_version_payload(b)
                db.execute(
                    """
                    INSERT OR IGNORE INTO bill_versions(bill_id,text_hash,source_url,text_url,raw_payload,created_at)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (bill_row["id"], b["text_hash"], b["source_url"], b["text_url"], raw_json, utcnow()),
                )
                version = db.execute(
                    "SELECT id FROM bill_versions WHERE bill_id=? AND text_hash=?",
                    (bill_row["id"], b["text_hash"]),
                ).fetchone()
                if version:
                    provider = configured_audit_provider()
                    db.execute(
                        """
                        INSERT INTO audit_jobs(bill_id,bill_version_id,taxonomy_version,prompt_version,provider,status,created_at)
                        VALUES (?,?,?,?,?,'queued',?)
                        ON CONFLICT(bill_version_id,taxonomy_version,prompt_version,provider) DO UPDATE SET
                          status='queued',completed_at=NULL,message='',created_at=excluded.created_at
                        WHERE audit_jobs.status='superseded'
                        """,
                        (bill_row["id"], version["id"], taxonomy_version(db), PROMPT_VERSION, provider.name, utcnow()),
                    )
                for action in provider_actions(raw_payload):
                    db.execute(
                        "INSERT OR IGNORE INTO bill_actions(bill_id,action_date,description,source_name,source_url) VALUES (?,?,?,?,?)",
                        (bill_row["id"], action["action_date"], action["description"], b["source_name"], b["source_url"]),
                    )
        return upserted


def sync_demo_bills() -> int:
    return upsert_bills(DEMO_BILLS)


def record_ingestion_run(source_name: str, status: str, requested_limit: int, bills_seen: int = 0,
                         bills_upserted: int = 0, message: str = "", jurisdiction_code: str = "") -> dict[str, Any]:
    init_db()
    with connect() as db:
        cur = db.execute(
            """
            INSERT INTO ingestion_runs(source_name,jurisdiction_code,status,requested_limit,bills_seen,bills_upserted,message,started_at,completed_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (source_name, jurisdiction_code.upper(), status, requested_limit, bills_seen, bills_upserted, message[:500], utcnow(), utcnow()),
        )
        row = db.execute("SELECT * FROM ingestion_runs WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)


def sync_congress_bills(limit: int = 20) -> dict[str, Any]:
    seed_defaults()
    client = CongressGovClient()
    if not client.ready:
        status = client.status()
        run = record_ingestion_run("Congress.gov", status["status"], limit, message=status["message"])
        return {**status, "ingestion_run": run}
    try:
        raw_bills = client.fetch_recent_bills(limit=limit, enrich=True)
        normalized = [normalize_congress_bill(item) for item in raw_bills]
        upserted = upsert_bills(normalized)
        run = record_ingestion_run("Congress.gov", "completed", limit, len(raw_bills), upserted, "")
        audit_result = process_audit_jobs(limit=max(10, min(limit, 100)))
        return {
            "ok": True,
            "status": "completed",
            "bills_seen": len(raw_bills),
            "bills_upserted": upserted,
            "ingestion_run": run,
            **audit_result,
        }
    except Exception as exc:
        safe_message = safe_provider_error(exc, "federal sync")
        run = record_ingestion_run("Congress.gov", "failed", limit, message=safe_message)
        return {"ok": False, "status": "failed", "message": safe_message, "ingestion_run": run}


def queue_pending_audits() -> int:
    init_db()
    provider = configured_audit_provider()
    with connect() as db:
        version = taxonomy_version(db)
        db.execute(
            """UPDATE audit_jobs SET status='superseded',completed_at=?,message='taxonomy superseded'
               WHERE status IN ('queued','retry') AND taxonomy_version != ?""",
            (utcnow(), version),
        )
        rows = latest_bill_versions(db)
        created = 0
        for row in rows:
            cur = db.execute(
                """INSERT INTO audit_jobs(
                    bill_id,bill_version_id,taxonomy_version,prompt_version,provider,status,created_at
                ) VALUES (?,?,?,?,?,'queued',?)
                ON CONFLICT(bill_version_id,taxonomy_version,prompt_version,provider) DO UPDATE SET
                  status='queued',completed_at=NULL,message='',created_at=excluded.created_at
                WHERE audit_jobs.status='superseded'""",
                (row["bill_id"], row["bill_version_id"], version, PROMPT_VERSION, provider.name, utcnow()),
            )
            created += max(cur.rowcount, 0)
        return created


def run_audits(seed_demo: bool = False) -> dict[str, int]:
    if seed_demo:
        sync_demo_bills()
    queued = queue_pending_audits()
    processed = process_audit_jobs(limit=100, generate_after=False)
    return {"audit_runs_created": processed["jobs_completed"], "flags_created": processed["flags_created"],
            "jobs_queued": queued}


def generate_matches(seed_demo: bool = False, ensure_audits: bool = False) -> dict[str, int]:
    if ensure_audits:
        run_audits(seed_demo=seed_demo)
    created_matches = 0
    created_notifications = 0
    with connect() as db:
        rows = db.execute(
            """
            SELECT ui.user_id, ui.min_severity, ui.jurisdictions, af.bill_id, af.audit_run_id, af.category_id, af.severity,
                   af.user_summary, b.bill_number, b.title, b.jurisdiction_code, c.name AS category_name
            FROM user_interests ui
            JOIN audit_flags af ON af.category_id = ui.category_id
            JOIN audit_runs ar ON ar.id=af.audit_run_id
            JOIN (
                SELECT bill_id,MAX(id) AS latest_id FROM audit_runs
                WHERE status='completed' GROUP BY bill_id
            ) latest ON latest.latest_id=ar.id
            JOIN bills b ON b.id = af.bill_id
            JOIN categories c ON c.id = af.category_id
            WHERE ui.active=1 AND af.flag_state IN ('yes','possible')
            """
        ).fetchall()
        for r in rows:
            if SEVERITY_RANK[r["severity"]] < SEVERITY_RANK[r["min_severity"]]:
                continue
            jurisdictions = {item.strip().upper() for item in r["jurisdictions"].split(",")}
            if "ALL" not in jurisdictions and r["jurisdiction_code"].upper() not in jurisdictions:
                continue
            cur = db.execute(
                """INSERT INTO bill_user_matches(user_id,bill_id,audit_run_id,category_id,status,created_at)
                   VALUES (?,?,?,?, 'new', ?)
                   ON CONFLICT(user_id,bill_id,category_id) DO UPDATE SET
                     audit_run_id=excluded.audit_run_id,status='new',created_at=excluded.created_at
                   WHERE bill_user_matches.audit_run_id != excluded.audit_run_id""",
                (r["user_id"], r["bill_id"], r["audit_run_id"], r["category_id"], utcnow()),
            )
            if cur.rowcount:
                created_matches += 1
            match = db.execute(
                "SELECT id FROM bill_user_matches WHERE user_id=? AND bill_id=? AND category_id=?",
                (r["user_id"], r["bill_id"], r["category_id"]),
            ).fetchone()
            if match and cur.rowcount:
                preference = db.execute(
                    "SELECT digest_frequency,channels FROM notification_preferences WHERE user_id=?", (r["user_id"],)
                ).fetchone()
                for channel in delivery_channels(preference):
                    frequency = preference["digest_frequency"] if preference else "instant"
                    status = ("delivered" if channel == "in_app" else
                              "queued" if frequency == "instant" else "digest_pending")
                    delivered_at = utcnow() if channel == "in_app" else None
                    ncur = db.execute(
                        """INSERT INTO notifications(
                            user_id,match_id,channel,title,body,status,created_at,delivered_at
                        ) VALUES (?,?,?,?,?,?,?,?)
                        ON CONFLICT(user_id,match_id,channel) DO UPDATE SET
                          title=excluded.title,body=excluded.body,status=excluded.status,
                          created_at=excluded.created_at,delivered_at=excluded.delivered_at,last_error=''""",
                        (r["user_id"], match["id"], channel, f"{r['category_name']} flag: {r['bill_number']}",
                         f"{r['title']} — {r['user_summary']}", status, utcnow(), delivered_at),
                    )
                    if ncur.rowcount:
                        created_notifications += 1
        return {"matches_created": created_matches, "notifications_created": created_notifications}


def provider_health_snapshot() -> dict[str, Any]:
    congress_status = CongressGovClient().status()
    legiscan_status = legiscan_status_snapshot()
    init_db()
    with connect() as db:
        latest = db.execute("SELECT * FROM ingestion_runs ORDER BY id DESC LIMIT 10").fetchall()
        state_rows = db.execute(
            """SELECT ir.* FROM ingestion_runs ir JOIN (
                SELECT jurisdiction_code,MAX(id) AS latest_id FROM ingestion_runs
                WHERE source_name LIKE 'LegiScan:%' GROUP BY jurisdiction_code
            ) latest ON latest.latest_id=ir.id ORDER BY ir.jurisdiction_code"""
        ).fetchall()
    return {
        "providers": {
            "congress_gov": congress_status,
            "legiscan": {**legiscan_status, "states": rows_to_dicts(state_rows)},
        },
        "recent_ingestion_runs": rows_to_dicts(latest),
        "representative_lookup": representative_links("US"),
    }


def legiscan_status_snapshot() -> dict[str, Any]:
    return LegiScanClient().status()


def sync_legiscan_bills(state: str, limit: int = 20) -> dict[str, Any]:
    seed_defaults()
    state = state.upper()
    client = LegiScanClient()
    status = client.status()
    if not status["ok"]:
        run = record_ingestion_run(f"LegiScan:{state}", status["status"], limit, message=status["message"], jurisdiction_code=state)
        return {**status, "ingestion_run": run}
    try:
        raw = client.fetch_bills(state, limit)
        normalized = [normalize_legiscan_bill(item) for item in raw]
        upserted = upsert_bills(normalized)
        run = record_ingestion_run(f"LegiScan:{state}", "completed", limit, len(raw), upserted, jurisdiction_code=state)
        return {"ok": True, "status": "completed", "bills_seen": len(raw), "bills_upserted": upserted,
                "ingestion_run": run, **process_audit_jobs(limit=max(10, min(limit, 100)))}
    except Exception as exc:
        safe_message = safe_provider_error(exc, "state sync")
        run = record_ingestion_run(f"LegiScan:{state}", "failed", limit, message=safe_message, jurisdiction_code=state)
        return {"ok": False, "status": "failed", "message": safe_message, "ingestion_run": run}


def process_audit_jobs(limit: int = 10, generate_after: bool = True) -> dict[str, int]:
    seed_defaults()
    jobs_started = 0
    jobs_completed = 0
    jobs_retried = 0
    jobs_failed = 0
    flags_created = 0
    bounded_limit = max(1, min(int(limit), 100))
    with connect() as db:
        stale_before = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(timespec="seconds")
        db.execute(
            """UPDATE audit_jobs SET status='retry',message='recovered stale worker claim',completed_at=?
               WHERE status='running' AND started_at < ?""",
            (utcnow(), stale_before),
        )
        current_taxonomy = taxonomy_version(db)
        db.execute(
            """UPDATE audit_jobs SET status='superseded',completed_at=?,message='taxonomy superseded'
               WHERE status IN ('queued','retry') AND taxonomy_version != ?""",
            (utcnow(), current_taxonomy),
        )
        job_ids = [row["id"] for row in db.execute(
            "SELECT id FROM audit_jobs WHERE status IN ('queued','retry') ORDER BY id LIMIT ?",
            (bounded_limit,),
        ).fetchall()]

    for job_id in job_ids:
        with connect() as db:
            job = db.execute(
                "SELECT * FROM audit_jobs WHERE id=? AND status IN ('queued','retry')", (job_id,)
            ).fetchone()
            if not job:
                continue
            claimed = db.execute(
                """UPDATE audit_jobs SET status='running',attempts=attempts+1,started_at=?,completed_at=NULL,message=''
                   WHERE id=? AND status IN ('queued','retry')""",
                (utcnow(), job_id),
            )
            if not claimed.rowcount:
                continue
            jobs_started += 1
            if job["taxonomy_version"] != taxonomy_version(db):
                db.execute("UPDATE audit_jobs SET status='superseded',completed_at=?,message='taxonomy superseded' WHERE id=?",
                           (utcnow(), job_id))
                continue
            bill = db.execute("SELECT * FROM bills WHERE id=?", (job["bill_id"],)).fetchone()
            if not bill:
                db.execute("UPDATE audit_jobs SET status='failed', message='bill missing', completed_at=? WHERE id=?", (utcnow(), job_id))
                jobs_failed += 1
                continue
            existing = db.execute(
                "SELECT id FROM audit_runs WHERE bill_version_id=? AND taxonomy_version=? AND prompt_version=?",
                (job["bill_version_id"], job["taxonomy_version"], job["prompt_version"]),
            ).fetchone()
            if existing:
                db.execute("UPDATE audit_jobs SET status='completed', message='audit already existed', completed_at=? WHERE id=?", (utcnow(), job_id))
                jobs_completed += 1
                continue
            version = db.execute("SELECT raw_payload FROM bill_versions WHERE id=?", (job["bill_version_id"],)).fetchone()
            audit_bill = audit_bill_for_version(bill, version["raw_payload"] if version else None)
            categories = [dict(row) for row in db.execute("SELECT * FROM categories WHERE active=1 ORDER BY id").fetchall()]
            job_data = dict(job)

        try:
            provider = configured_audit_provider()
            # Provider I/O intentionally runs without an open SQLite transaction.
            results = provider.audit(audit_bill, categories)
            with connect() as db:
                existing = db.execute(
                    "SELECT id FROM audit_runs WHERE bill_version_id=? AND taxonomy_version=? AND prompt_version=?",
                    (job_data["bill_version_id"], job_data["taxonomy_version"], job_data["prompt_version"]),
                ).fetchone()
                if existing:
                    db.execute("UPDATE audit_jobs SET status='completed',message='audit already existed',completed_at=? WHERE id=?",
                               (utcnow(), job_id))
                    jobs_completed += 1
                    continue
                cur = db.execute(
                    """INSERT INTO audit_runs(
                        bill_id,bill_version_id,taxonomy_version,prompt_version,provider,model,status,created_at,completed_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (audit_bill["id"], job_data["bill_version_id"], job_data["taxonomy_version"], job_data["prompt_version"],
                     provider.name, provider.model, "completed", utcnow(), utcnow()),
                )
                audit_run_id = cur.lastrowid
                for result in results:
                    db.execute(
                        """INSERT INTO audit_flags(
                            audit_run_id,bill_id,category_id,flag_state,severity,confidence,rationale,citation,user_summary,affected_groups,concerns
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (audit_run_id, audit_bill["id"], result["category_id"], result["flag_state"], result["severity"],
                         result["confidence"], result["rationale"], result["citation"], result["user_summary"],
                         json.dumps(result.get("affected_groups", [])), json.dumps(result.get("concerns", []))),
                    )
                    flags_created += 1
                db.execute("UPDATE audit_jobs SET status='completed', completed_at=?, message='completed' WHERE id=?", (utcnow(), job_id))
                jobs_completed += 1
        except Exception as exc:
            with connect() as db:
                attempt_row = db.execute("SELECT attempts FROM audit_jobs WHERE id=?", (job_id,)).fetchone()
                retry_status = "failed" if attempt_row and attempt_row["attempts"] >= 3 else "retry"
                message = f"{type(exc).__name__}: audit failed"
                db.execute("UPDATE audit_jobs SET status=?,completed_at=?,message=? WHERE id=?",
                           (retry_status, utcnow(), message, job_id))
            if retry_status == "failed":
                jobs_failed += 1
            else:
                jobs_retried += 1
    match_result = generate_matches(seed_demo=False, ensure_audits=False) if generate_after else {"matches_created": 0, "notifications_created": 0}
    return {"jobs_started": jobs_started, "jobs_completed": jobs_completed, "jobs_retried": jobs_retried,
            "jobs_failed": jobs_failed, "flags_created": flags_created, **match_result}


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_bootstrap_token()
    init_db()
    yield


app = FastAPI(title=APP_TITLE, lifespan=lifespan)
_STYLE = re.search(r"<style>(.*?)</style>", APP_HTML, re.DOTALL).group(1)
_SCRIPT = re.search(r"<script>(.*?)</script>", APP_HTML, re.DOTALL).group(1)
_STYLE_HASH = base64.b64encode(hashlib.sha256(_STYLE.encode()).digest()).decode()
_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
RATE_LIMITS: dict[tuple[str, str], list[float]] = {}
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_REQUESTS = 120


def _secure_response(request: Request, response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; script-src 'self' 'sha256-{_SCRIPT_HASH}'; style-src 'self' 'sha256-{_STYLE_HASH}'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.middleware("http")
async def security_headers_and_rate_limit(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > 1_048_576:
                return _secure_response(request, JSONResponse(status_code=413, content={"detail": "request body too large"}))
        except ValueError:
            return _secure_response(request, JSONResponse(status_code=400, content={"detail": "invalid content-length header"}))
    client_host = request.client.host if request.client else "unknown"
    route_key = "admin" if request.url.path.startswith("/api/admin") else "default"
    now_ts = datetime.now(timezone.utc).timestamp()
    bucket = RATE_LIMITS.setdefault((client_host, route_key), [])
    while bucket and bucket[0] <= now_ts - RATE_LIMIT_WINDOW_SECONDS:
        bucket.pop(0)
    if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
        return _secure_response(request, JSONResponse(status_code=429, content={"detail": "rate limit exceeded"}))
    bucket.append(now_ts)
    response = await call_next(request)
    return _secure_response(request, response)


@app.get("/api/health")
def health() -> dict[str, Any]:
    init_db()
    with connect() as db:
        counts = {
            "categories": db.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
            "bills": db.execute("SELECT COUNT(*) FROM bills").fetchone()[0],
            "audit_runs": db.execute("SELECT COUNT(*) FROM audit_runs").fetchone()[0],
            "notifications": db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0],
        }
    return {"ok": True, "service": "civics", "time": utcnow(), "counts": counts}


@app.post("/api/admin/seed")
def api_seed(_: sqlite3.Row | dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {"ok": True, **seed_defaults()}


@app.post("/api/admin/accounts", status_code=201)
def api_create_account(payload: AccountCreateIn, _: sqlite3.Row | dict[str, Any] = Depends(require_system_admin)) -> dict[str, Any]:
    try:
        return create_account(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/me")
def api_me(user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as db:
        account = db.execute("SELECT * FROM accounts WHERE id=?", (user["account_id"],)).fetchone()
    safe_user = dict(user)
    safe_user.pop("api_token", None)
    safe_user.pop("api_token_hash", None)
    safe_user.pop("api_token_prefix", None)
    return {"user": safe_user, "account": dict(account) if account else None}


@app.get("/api/admin/provider-health")
def api_provider_health(_: sqlite3.Row | dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return provider_health_snapshot()


@app.get("/api/admin/ops")
def api_admin_ops(_: sqlite3.Row | dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    from civics_app.notification_delivery import SMTPDelivery, TelegramDelivery

    with connect() as db:
        audit_queue = {row["status"]: row["count"] for row in db.execute(
            "SELECT status,COUNT(*) AS count FROM audit_jobs GROUP BY status"
        ).fetchall()}
        notification_queue = {row["status"]: row["count"] for row in db.execute(
            "SELECT status,COUNT(*) AS count FROM notifications GROUP BY status"
        ).fetchall()}
        recent = rows_to_dicts(db.execute("SELECT * FROM ingestion_runs ORDER BY id DESC LIMIT 25").fetchall())
        feedback_count = db.execute("SELECT COUNT(*) FROM audit_feedback").fetchone()[0]
    return {
        "providers": provider_health_snapshot()["providers"],
        "recent_ingestion_runs": recent,
        "audit_queue": audit_queue,
        "notification_queue": notification_queue,
        "delivery": {"email": "ready" if SMTPDelivery().ready else "not_configured",
                     "telegram": "ready" if TelegramDelivery().ready else "not_configured"},
        "feedback_count": feedback_count,
        "service_health": {"status": "managed_externally", "detail": "Inspect systemd timers on the host."},
    }


@app.get("/api/categories")
def list_categories(_: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    seed_defaults()
    with connect() as db:
        return rows_to_dicts(db.execute("SELECT * FROM categories ORDER BY id").fetchall())


@app.post("/api/admin/categories")
def create_category(category: CategoryIn, _: sqlite3.Row | dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    seed_defaults()
    with connect() as db:
        try:
            cur = db.execute(
                "INSERT INTO categories(slug,name,description,examples_positive,active,created_at) VALUES (?,?,?,?,1,?)",
                (category.slug, category.name, category.description, category.examples_positive, utcnow()),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="category slug already exists") from exc
        result = dict(db.execute("SELECT * FROM categories WHERE id=?", (cur.lastrowid,)).fetchone())
        result["taxonomy_version"] = taxonomy_version(db)
    queue_pending_audits()
    return result


@app.patch("/api/admin/categories/{category_id}")
def update_category(category_id: int, payload: CategoryUpdateIn,
                    _: sqlite3.Row | dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="at least one field is required")
    with connect() as db:
        row = db.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="category not found")
        assignments = ",".join(f"{key}=?" for key in updates)
        values = [int(value) if key == "active" else value for key, value in updates.items()]
        db.execute(f"UPDATE categories SET {assignments},updated_at=? WHERE id=?", [*values, utcnow(), category_id])
        result = dict(db.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone())
        result["taxonomy_version"] = taxonomy_version(db)
    queue_pending_audits()
    return result


@app.post("/api/admin/sync-demo-bills")
def api_sync_demo_bills(_: sqlite3.Row | dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {"ok": True, "bills_upserted": sync_demo_bills()}


@app.post("/api/admin/sync-congress")
def api_sync_congress(limit: int = Query(default=20, ge=1, le=250), _: sqlite3.Row | dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return sync_congress_bills(limit=limit)


@app.post("/api/admin/sync-legiscan")
def api_sync_legiscan(state: str = Query(default="MO", pattern=r"^[A-Za-z]{2}$"), limit: int = Query(default=20, ge=1, le=250), _: sqlite3.Row | dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return sync_legiscan_bills(state=state, limit=limit)


@app.post("/api/admin/process-audit-jobs")
def api_process_audit_jobs(limit: int = Query(default=10, ge=1, le=100), _: sqlite3.Row | dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {"ok": True, **process_audit_jobs(limit=limit)}


@app.post("/api/admin/sync-congress-sample")
def api_sync_congress_sample(payload: CongressSampleSyncIn, _: sqlite3.Row | dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    normalized = [normalize_congress_bill(item) for item in payload.bills]
    upserted = upsert_bills(normalized)
    run = record_ingestion_run("Congress.gov", "sample_completed", len(payload.bills), len(payload.bills), upserted, "sample fixture sync")
    audit_result = process_audit_jobs(limit=max(10, len(payload.bills)))
    return {"ok": True, "bills_upserted": upserted, "ingestion_run": run, **audit_result}


@app.get("/api/admin/ingestion-runs")
def api_ingestion_runs(_: sqlite3.Row | dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    init_db()
    with connect() as db:
        return rows_to_dicts(db.execute("SELECT * FROM ingestion_runs ORDER BY id DESC LIMIT 25").fetchall())


@app.post("/api/admin/run-audits")
def api_run_audits(_: sqlite3.Row | dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {"ok": True, **run_audits()}


@app.post("/api/admin/generate-matches")
def api_generate_matches(_: sqlite3.Row | dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    return {"ok": True, **generate_matches()}


@app.post("/api/interests")
def set_interests(payload: InterestIn, user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    seed_defaults()
    with connect() as db:
        db.execute("UPDATE user_interests SET active=0 WHERE user_id=?", (user["id"],))
        categories = db.execute(
            f"SELECT id, slug FROM categories WHERE active=1 AND slug IN ({','.join(['?']*len(payload.category_slugs))})",
            payload.category_slugs,
        ).fetchall() if payload.category_slugs else []
        found = {category["slug"] for category in categories}
        missing = set(payload.category_slugs) - found
        if missing:
            raise HTTPException(status_code=422, detail=f"unknown categories: {', '.join(sorted(missing))}")
        jurisdictions = ",".join(j.upper() for j in payload.jurisdictions) if payload.jurisdictions else "all"
        for c in categories:
            db.execute(
                """
                INSERT INTO user_interests(user_id,category_id,min_severity,jurisdictions,active) VALUES (?,?,?,?,1)
                ON CONFLICT(user_id, category_id) DO UPDATE SET min_severity=excluded.min_severity, jurisdictions=excluded.jurisdictions, active=1
                """,
                (user["id"], c["id"], payload.min_severity, jurisdictions),
            )
        return {"ok": True, "active_interests": len(categories)}


@app.get("/api/bills")
def list_bills(
    category: str | None = Query(default=None, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
    search: str | None = Query(default=None, max_length=200),
    jurisdiction: str | None = Query(default=None, pattern=r"^[A-Za-z]{2}$"),
    status: str | None = Query(default=None, max_length=100),
    severity: str | None = Query(default=None, pattern=r"^(low|medium|high)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort: str = Query(default="updated_at", pattern=r"^(updated_at|introduced_at|title|bill_number)$"),
    order: str = Query(default="desc", pattern=r"^(asc|desc)$"),
    _: sqlite3.Row | dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    sort, sql_order = validated_sort(sort, order)
    with connect() as db:
        params: list[Any] = []
        joins = ""
        where = ["1=1"]
        if category or severity:
            joins += " JOIN audit_flags af ON af.bill_id=b.id JOIN categories c ON c.id=af.category_id"
            where.append("af.flag_state IN ('yes','possible')")
        if category:
            where.append("c.slug=?")
            params.append(category)
        if severity:
            where.append("af.severity=?")
            params.append(severity)
        if search:
            where.append("(lower(b.title) LIKE ? OR lower(b.summary) LIKE ? OR lower(b.bill_number) LIKE ?)")
            term = f"%{search.lower()}%"
            params.extend([term, term, term])
        if jurisdiction:
            where.append("b.jurisdiction_code=?")
            params.append(jurisdiction.upper())
        if status:
            where.append("lower(b.status) LIKE ?")
            params.append(f"%{status.lower()}%")
        base = f"FROM bills b {joins} WHERE {' AND '.join(where)}"
        total = db.execute(f"SELECT COUNT(DISTINCT b.id) {base}", params).fetchone()[0]
        rows = db.execute(
            f"SELECT DISTINCT b.* {base} ORDER BY b.{sort} {sql_order}, b.id DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        return {"items": rows_to_dicts(rows), "page": page, "page_size": page_size, "total": total,
                "pages": (total + page_size - 1) // page_size}


@app.get("/api/bills/{bill_id}")
def bill_detail(bill_id: int, _: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as db:
        bill = db.execute("SELECT * FROM bills WHERE id=?", (bill_id,)).fetchone()
        if not bill:
            raise HTTPException(status_code=404, detail="bill not found")
        flags = db.execute(
            """
            SELECT af.*, c.slug, c.name, c.description FROM audit_flags af
            JOIN categories c ON c.id=af.category_id
            WHERE af.audit_run_id=(
                SELECT id FROM audit_runs WHERE bill_id=? AND status='completed' ORDER BY id DESC LIMIT 1
            )
            ORDER BY CASE af.flag_state WHEN 'yes' THEN 1 WHEN 'possible' THEN 2 ELSE 3 END, c.name
            """,
            (bill_id,),
        ).fetchall()
        versions = db.execute("SELECT id,text_hash,source_url,text_url,created_at FROM bill_versions WHERE bill_id=? ORDER BY id DESC", (bill_id,)).fetchall()
        runs = db.execute("SELECT id,bill_version_id,taxonomy_version,prompt_version,provider,model,status,created_at,completed_at FROM audit_runs WHERE bill_id=? ORDER BY id DESC", (bill_id,)).fetchall()
        timeline = db.execute("SELECT action_date,description,source_name,source_url FROM bill_actions WHERE bill_id=? ORDER BY action_date DESC,id DESC", (bill_id,)).fetchall()
        decoded_flags = rows_to_dicts(flags)
        for flag in decoded_flags:
            flag["affected_groups"] = decode_json_field(flag.get("affected_groups"), [])
            flag["concerns"] = decode_json_field(flag.get("concerns"), [])
        return {"bill": dict(bill), "official_sources": {"record": bill["source_url"], "text": bill["text_url"], "provider": bill["source_name"]},
                "versions": rows_to_dicts(versions), "audit_runs": rows_to_dicts(runs), "timeline": rows_to_dicts(timeline), "flags": decoded_flags,
                "audit_disclaimer": "Automated relevance screening is informational, may be incomplete, and is not legal advice.",
                "representative_links": representative_links(bill["jurisdiction_code"])}


@app.post("/api/audit-flags/{audit_flag_id}/feedback", status_code=201)
def create_audit_feedback(audit_flag_id: int, payload: AuditFeedbackIn,
                          user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as db:
        if not db.execute("SELECT id FROM audit_flags WHERE id=?", (audit_flag_id,)).fetchone():
            raise HTTPException(status_code=404, detail="audit flag not found")
        db.execute(
            """INSERT INTO audit_feedback(user_id,audit_flag_id,feedback_type,note,created_at) VALUES (?,?,?,?,?)
               ON CONFLICT(user_id,audit_flag_id,feedback_type) DO UPDATE SET note=excluded.note,created_at=excluded.created_at""",
            (user["id"], audit_flag_id, payload.feedback_type, payload.note, utcnow()),
        )
        return dict(db.execute(
            "SELECT * FROM audit_feedback WHERE user_id=? AND audit_flag_id=? AND feedback_type=?",
            (user["id"], audit_flag_id, payload.feedback_type),
        ).fetchone())


@app.get("/api/dashboard")
def dashboard(page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
              user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as db:
        matches = db.execute(
            """
            SELECT m.id AS match_id, m.status AS match_status, b.id AS bill_id, b.bill_number, b.title, b.jurisdiction_code, b.status,
                   c.slug AS category_slug, c.name AS category_name, af.severity, af.confidence, af.user_summary, af.citation
            FROM bill_user_matches m
            JOIN bills b ON b.id=m.bill_id
            JOIN categories c ON c.id=m.category_id
            JOIN audit_flags af ON af.audit_run_id=m.audit_run_id AND af.category_id=m.category_id
            WHERE m.user_id=? ORDER BY m.created_at DESC, b.updated_at DESC LIMIT ? OFFSET ?
            """,
            (user["id"], page_size, (page - 1) * page_size),
        ).fetchall()
        notifications = db.execute(
            "SELECT * FROM notifications WHERE user_id=? AND channel='in_app' ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user["id"], page_size, (page - 1) * page_size),
        ).fetchall()
        interests = db.execute(
            """
            SELECT c.slug, c.name, ui.min_severity, ui.jurisdictions FROM user_interests ui JOIN categories c ON c.id=ui.category_id
            WHERE ui.user_id=? AND ui.active=1 ORDER BY c.name
            """,
            (user["id"],),
        ).fetchall()
        return {
            "user": {k: v for k, v in dict(user).items() if k not in {"api_token", "api_token_hash", "api_token_prefix"}},
            "page": page, "page_size": page_size,
            "interests": rows_to_dicts(interests),
            "matches": rows_to_dicts(matches),
            "notifications": rows_to_dicts(notifications),
        }


@app.post("/api/notification-preferences")
def api_notification_preferences(payload: NotificationPreferenceIn, user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    channels = payload.channels
    with connect() as db:
        if payload.notification_email is not None or payload.telegram_chat_id is not None:
            db.execute(
                "UPDATE users SET notification_email=COALESCE(?,notification_email),telegram_chat_id=COALESCE(?,telegram_chat_id) WHERE id=?",
                (str(payload.notification_email).lower() if payload.notification_email else None,
                 payload.telegram_chat_id, user["id"]),
            )
        db.execute(
            """
            INSERT INTO notification_preferences(user_id,digest_frequency,channels,created_at,updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET digest_frequency=excluded.digest_frequency, channels=excluded.channels, updated_at=excluded.updated_at
            """,
            (user["id"], payload.digest_frequency, json.dumps(channels), utcnow(), utcnow()),
        )
        if payload.digest_frequency == "off":
            db.execute(
                """UPDATE notifications SET status='cancelled',last_error=''
                   WHERE user_id=? AND channel IN ('email','telegram')
                     AND status IN ('queued','digest_pending','not_configured')""",
                (user["id"],),
            )
        elif payload.digest_frequency == "instant":
            db.execute(
                "UPDATE notifications SET status='queued' WHERE user_id=? AND status='digest_pending'",
                (user["id"],),
            )
        else:
            db.execute(
                """UPDATE notifications SET status='digest_pending'
                   WHERE user_id=? AND channel IN ('email','telegram') AND status='queued'""",
                (user["id"],),
            )
        row = db.execute("SELECT * FROM notification_preferences WHERE user_id=?", (user["id"],)).fetchone()
        delivery = db.execute("SELECT notification_email,telegram_chat_id FROM users WHERE id=?", (user["id"],)).fetchone()
    out = dict(row)
    out["channels"] = decode_json_field(out["channels"], [])
    out["notification_email"] = delivery["notification_email"]
    out["telegram_chat_configured"] = bool(delivery["telegram_chat_id"])
    return out


@app.get("/api/notification-preferences")
def api_get_notification_preferences(user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT * FROM notification_preferences WHERE user_id=?", (user["id"],)).fetchone()
        delivery = db.execute("SELECT notification_email,telegram_chat_id FROM users WHERE id=?", (user["id"],)).fetchone()
    result = dict(row) if row else {
        "user_id": user["id"], "digest_frequency": "instant", "channels": '["in_app"]',
        "created_at": None, "updated_at": None,
    }
    result["channels"] = decode_json_field(result["channels"], ["in_app"])
    result["notification_email"] = delivery["notification_email"] if delivery else ""
    result["telegram_chat_configured"] = bool(delivery and delivery["telegram_chat_id"])
    return result


@app.get("/api/notifications/digest")
def api_notification_digest(user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as db:
        rows = db.execute(
            """
            SELECT n.*, b.bill_number, b.title AS bill_title, b.jurisdiction_code, c.name AS category_name
            FROM notifications n
            JOIN bill_user_matches m ON m.id=n.match_id
            JOIN bills b ON b.id=m.bill_id
            JOIN categories c ON c.id=m.category_id
            WHERE n.user_id=? ORDER BY n.created_at DESC LIMIT 50
            """,
            (user["id"],),
        ).fetchall()
    sections: dict[str, list[dict[str, Any]]] = {}
    for row in rows_to_dicts(rows):
        sections.setdefault(row["category_name"], []).append(row)
    return {"user_id": user["id"], "notification_count": len(rows), "sections": sections}


@app.patch("/api/notifications/{notification_id}/read")
def api_mark_notification_read(notification_id: int, user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as db:
        cur = db.execute("UPDATE notifications SET status='read' WHERE id=? AND user_id=?", (notification_id, user["id"]))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="notification not found")
        row = db.execute("SELECT * FROM notifications WHERE id=? AND user_id=?", (notification_id, user["id"])).fetchone()
    return dict(row)


@app.patch("/api/matches/{match_id}/read")
def api_mark_match_read(match_id: int, user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as db:
        cur = db.execute("UPDATE bill_user_matches SET status='read' WHERE id=? AND user_id=?", (match_id, user["id"]))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="match not found")
        row = db.execute("SELECT * FROM bill_user_matches WHERE id=? AND user_id=?", (match_id, user["id"])).fetchone()
    return dict(row)


@app.post("/api/saved-views")
def api_create_saved_view(payload: SavedViewIn, user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    with connect() as db:
        db.execute(
            """
            INSERT INTO saved_views(user_id,name,filters,created_at) VALUES (?,?,?,?)
            ON CONFLICT(user_id, name) DO UPDATE SET filters=excluded.filters
            """,
            (user["id"], payload.name, json.dumps(payload.filters, sort_keys=True), utcnow()),
        )
        row = db.execute("SELECT * FROM saved_views WHERE user_id=? AND name=?", (user["id"], payload.name)).fetchone()
    out = dict(row)
    out["filters"] = decode_json_field(out["filters"], {})
    return out


@app.get("/api/saved-views")
def api_list_saved_views(user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    with connect() as db:
        rows = rows_to_dicts(db.execute("SELECT * FROM saved_views WHERE user_id=? ORDER BY name", (user["id"],)).fetchall())
    for row in rows:
        row["filters"] = decode_json_field(row["filters"], {})
    return rows


@app.delete("/api/saved-views/{view_id}", status_code=204)
def api_delete_saved_view(view_id: int, user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> Response:
    with connect() as db:
        cur = db.execute("DELETE FROM saved_views WHERE id=? AND user_id=?", (view_id, user["id"]))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="saved view not found")
    return Response(status_code=204)


def representative_links(jurisdiction_code: str) -> list[dict[str, str]]:
    links = [
        {"label": "USA.gov: find elected officials", "url": "https://www.usa.gov/elected-officials"},
        {"label": "Congress.gov: find your member", "url": "https://www.congress.gov/members/find-your-member"},
    ]
    state_links = {
        "CA": "https://findyourrep.legislature.ca.gov/",
        "TX": "https://wrm.capitol.texas.gov/home",
        "NY": "https://nyassembly.gov/mem/search/",
        "FL": "https://www.myfloridahouse.gov/findyourrepresentative",
    }
    if jurisdiction_code in state_links:
        links.append({"label": f"{jurisdiction_code} state legislator lookup", "url": state_links[jurisdiction_code]})
    return links


@app.get("/api/representatives/links")
def api_representative_links(jurisdiction: str = Query(default="US", pattern=r"^[A-Za-z]{2}$")) -> list[dict[str, str]]:
    return representative_links(jurisdiction.upper())


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return APP_HTML


HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Civics Radar</title>
  <style>
    :root { color-scheme: dark; --bg:#0f172a; --panel:#111827; --muted:#94a3b8; --text:#e5e7eb; --brand:#38bdf8; --good:#22c55e; --warn:#f59e0b; --bad:#ef4444; }
    *{box-sizing:border-box} body{margin:0;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;background:linear-gradient(135deg,#0f172a,#172554 55%,#0c4a6e);color:var(--text)}
    header{padding:34px 22px 22px;max-width:1180px;margin:auto} h1{font-size:clamp(2.1rem,5vw,4.2rem);margin:.2rem 0} p{line-height:1.55}.muted{color:var(--muted)}
    main{max-width:1180px;margin:auto;padding:0 22px 40px;display:grid;gap:18px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:16px}.panel{background:rgba(15,23,42,.82);border:1px solid rgba(148,163,184,.28);border-radius:20px;padding:18px;box-shadow:0 18px 60px rgba(0,0,0,.25)}
    button,.chip,input,select{border:1px solid rgba(56,189,248,.35);background:rgba(56,189,248,.12);color:#e0f2fe;padding:9px 12px;border-radius:999px}button,.chip{cursor:pointer}button:hover{background:rgba(56,189,248,.25)}
    .pill{display:inline-flex;margin:4px 6px 4px 0;padding:5px 9px;border-radius:999px;background:#1e293b;color:#dbeafe;font-size:.85rem}.yes{border-left:4px solid var(--good)}.possible{border-left:4px solid var(--warn)}.no{opacity:.72}.bill{cursor:pointer}.bill:hover{outline:1px solid var(--brand)}
    a{color:#7dd3fc}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.kpi{font-size:2rem;font-weight:800}.small{font-size:.9rem}pre{white-space:pre-wrap;background:#020617;padding:10px;border-radius:10px;overflow:auto}.flag{padding:10px;border-radius:12px;background:rgba(30,41,59,.7);margin:8px 0}.severity-high{color:#fecaca}.severity-medium{color:#fde68a}.severity-low{color:#bbf7d0}
  </style>
</head>
<body>
  <header>
    <div class="chip">MVP live dashboard</div>
    <h1>Civics Radar</h1>
    <p class="muted">Monitor federal and state bills, audit every bill once against admin categories, and notify each account only about the civic flags they care about.</p>
    <div class="row"><button id="refresh">Refresh data</button><button id="seed">Seed / run MVP pipeline</button><a href="/api/health">API health</a><a href="https://www.usa.gov/elected-officials" target="_blank">Find representatives</a></div>
  </header>
  <main>
    <section class="grid">
      <div class="panel"><div class="muted">Tracked bills</div><div id="billCount" class="kpi">—</div></div>
      <div class="panel"><div class="muted">Matched interests</div><div id="matchCount" class="kpi">—</div></div>
      <div class="panel"><div class="muted">Notifications</div><div id="notificationCount" class="kpi">—</div></div>
      <div class="panel"><div class="muted">Provider status</div><div id="providerStatus" class="small">—</div></div>
    </section>
    <section class="grid">
      <div class="panel">
        <h2>Your interests</h2>
        <div id="interests"></div>
        <p class="small muted">Demo account uses admin presets. MVP supports per-account interest matching from one shared audit output.</p>
      </div>
      <div class="panel">
        <h2>Notification inbox</h2>
        <div id="notifications"></div>
      </div>
    </section>
    <section class="grid">
      <div class="panel">
        <h2>Matched bills</h2>
        <div class="row"><input id="search" placeholder="Search bills" /><select id="jurisdiction"><option value="">All jurisdictions</option><option>US</option><option>CA</option><option>TX</option><option>MO</option></select><button id="applyFilters">Apply filters</button></div>
        <div id="matches"></div>
      </div>
      <div class="panel">
        <h2>Bill detail</h2>
        <div id="detail" class="muted">Select a bill to inspect source links, audit flags, citations, and representative links.</div>
      </div>
    </section>
    <section class="panel">
      <h2>Admin categories</h2>
      <div id="categories"></div>
    </section>
  </main>
<script>
async function post(url){ const res=await fetch(url,{method:'POST'}); if(!res.ok) throw new Error(await res.text()); return res.json(); }
async function get(url){ const res=await fetch(url); if(!res.ok) throw new Error(await res.text()); return res.json(); }
function esc(s){return String(s ?? '').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
async function load(){
  const [health,dash,cats,bills,provider]=await Promise.all([get('/api/health'),get('/api/dashboard'),get('/api/categories'),get('/api/bills'),get('/api/admin/provider-health')]);
  renderDashboard(dash,cats,bills,provider);
}
function renderDashboard(dash,cats,bills,provider){
  document.getElementById('billCount').textContent=bills.length;
  document.getElementById('matchCount').textContent=dash.matches.length;
  document.getElementById('notificationCount').textContent=dash.notifications.length;
  document.getElementById('providerStatus').innerHTML=Object.entries(provider.providers).map(([k,v])=>`<div><strong>${esc(k)}</strong>: ${esc(v.status)}${v.message?' — '+esc(v.message):''}</div>`).join('');
  document.getElementById('interests').innerHTML=dash.interests.map(i=>`<span class="pill">${esc(i.name)} · ${esc(i.min_severity)}+ · ${esc(i.jurisdictions||'all')}</span>`).join('') || '<p class="muted">No interests selected.</p>';
  document.getElementById('categories').innerHTML=cats.map(c=>`<span class="pill" title="${esc(c.description)}">${esc(c.name)}</span>`).join('');
  document.getElementById('notifications').innerHTML=dash.notifications.map(n=>`<div class="flag"><strong>${esc(n.title)}</strong><p class="small">${esc(n.body)}</p></div>`).join('') || '<p class="muted">No notifications yet.</p>';
  document.getElementById('matches').innerHTML=dash.matches.map(m=>`<div class="panel bill" onclick="detail(${m.bill_id})"><div class="row"><strong>${esc(m.bill_number)}</strong><span class="pill">${esc(m.jurisdiction_code)}</span><span class="pill severity-${esc(m.severity)}">${esc(m.category_name)} · ${esc(m.severity)}</span></div><h3>${esc(m.title)}</h3><p>${esc(m.user_summary)}</p><p class="small muted">Citation: ${esc(m.citation)}</p></div>`).join('') || '<p class="muted">No matched bills yet.</p>';
}
async function applyFilters(){
 const params=new URLSearchParams(); const q=document.getElementById('search').value; const j=document.getElementById('jurisdiction').value; if(q) params.set('search',q); if(j) params.set('jurisdiction',j);
 const bills=await get('/api/bills?'+params.toString());
 document.getElementById('matches').innerHTML=bills.map(b=>`<div class="panel bill" onclick="detail(${b.id})"><div class="row"><strong>${esc(b.bill_number)}</strong><span class="pill">${esc(b.jurisdiction_code)}</span><span class="pill">${esc(b.status)}</span></div><h3>${esc(b.title)}</h3><p>${esc(b.summary)}</p></div>`).join('') || '<p class="muted">No bills match those filters.</p>';
}
async function detail(id){
 const data=await get('/api/bills/'+id); const b=data.bill;
 document.getElementById('detail').innerHTML=`<h2>${esc(b.bill_number)} · ${esc(b.title)}</h2><p>${esc(b.summary)}</p><div class="row"><span class="pill">${esc(b.status)}</span><span class="pill">${esc(b.source_name)}</span><a href="${esc(b.source_url)}" target="_blank">official source</a><a href="${esc(b.text_url)}" target="_blank">bill text</a></div><h3>Audit flags</h3>${data.flags.map(f=>`<div class="flag ${esc(f.flag_state)}"><strong>${esc(f.name)}: ${esc(f.flag_state)}</strong> <span class="severity-${esc(f.severity)}">${esc(f.severity)} · ${Math.round(f.confidence*100)}%</span><p>${esc(f.rationale)}</p><p class="small muted">${esc(f.citation)}</p></div>`).join('')}<h3>Representative links</h3>${data.representative_links.map(l=>`<div><a href="${esc(l.url)}" target="_blank">${esc(l.label)}</a></div>`).join('')}`;
}
document.getElementById('seed').onclick=async()=>{ await post('/api/admin/seed'); await post('/api/admin/sync-demo-bills'); await post('/api/admin/run-audits'); await post('/api/admin/generate-matches'); await load(); };
document.getElementById('refresh').onclick=load;
document.getElementById('applyFilters').onclick=applyFilters;
load().catch(async e=>{ document.getElementById('matches').innerHTML='<pre>'+esc(e.message)+'</pre>'; });
</script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8844)
