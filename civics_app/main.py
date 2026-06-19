from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

from civics_app.congress import CongressGovClient, normalize_congress_bill
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

APP_TITLE = "Civics Radar"
DEFAULT_DB = "/opt/civics/data/civics.db"
PROMPT_VERSION = "mvp-2026-06-19"
TAXONOMY_VERSION = "mvp-default"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path() -> str:
    return os.environ.get("CIVICS_DB", DEFAULT_DB)


@contextmanager
def connect() -> Any:
    path = Path(db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                examples_positive TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id),
                email TEXT UNIQUE NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_interests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                category_id INTEGER NOT NULL REFERENCES categories(id),
                min_severity TEXT NOT NULL DEFAULT 'low',
                jurisdictions TEXT NOT NULL DEFAULT 'all',
                active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(user_id, category_id)
            );
            CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_key TEXT UNIQUE NOT NULL,
                jurisdiction_kind TEXT NOT NULL,
                jurisdiction_code TEXT NOT NULL,
                session TEXT NOT NULL,
                chamber TEXT NOT NULL,
                bill_number TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                status TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                text_url TEXT NOT NULL,
                introduced_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                text_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_id INTEGER NOT NULL REFERENCES bills(id),
                taxonomy_version TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE(bill_id, taxonomy_version, prompt_version)
            );
            CREATE TABLE IF NOT EXISTS audit_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_run_id INTEGER NOT NULL REFERENCES audit_runs(id),
                bill_id INTEGER NOT NULL REFERENCES bills(id),
                category_id INTEGER NOT NULL REFERENCES categories(id),
                flag_state TEXT NOT NULL,
                severity TEXT NOT NULL,
                confidence REAL NOT NULL,
                rationale TEXT NOT NULL,
                citation TEXT NOT NULL,
                user_summary TEXT NOT NULL,
                UNIQUE(audit_run_id, category_id)
            );
            CREATE TABLE IF NOT EXISTS bill_user_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                bill_id INTEGER NOT NULL REFERENCES bills(id),
                audit_run_id INTEGER NOT NULL REFERENCES audit_runs(id),
                category_id INTEGER NOT NULL REFERENCES categories(id),
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL,
                UNIQUE(user_id, bill_id, category_id)
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                match_id INTEGER NOT NULL REFERENCES bill_user_matches(id),
                channel TEXT NOT NULL DEFAULT 'in_app',
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL,
                delivered_at TEXT,
                UNIQUE(user_id, match_id, channel)
            );
            CREATE TABLE IF NOT EXISTS ingestion_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_limit INTEGER NOT NULL DEFAULT 0,
                bills_seen INTEGER NOT NULL DEFAULT 0,
                bills_upserted INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                completed_at TEXT
            );
            """
        )


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


class CategoryIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]+$")
    name: str
    description: str
    examples_positive: str = ""


class InterestIn(BaseModel):
    email: str = "demo@example.com"
    category_slugs: list[str]
    min_severity: str = "low"


class CongressSampleSyncIn(BaseModel):
    bills: list[dict[str, Any]]


class AuditProvider:
    name = "base"

    def audit(self, bill: sqlite3.Row, categories: list[sqlite3.Row]) -> list[dict[str, Any]]:
        raise NotImplementedError


class KeywordAuditor(AuditProvider):
    """Deterministic MVP auditor. Replace with OpenAI/Codex provider behind this interface."""

    name = "keyword-mvp"

    def audit(self, bill: sqlite3.Row, categories: list[sqlite3.Row]) -> list[dict[str, Any]]:
        haystack = f"{bill['title']} {bill['summary']}".lower()
        out: list[dict[str, Any]] = []
        for c in categories:
            words = [w.strip().lower() for w in c["examples_positive"].split(",") if w.strip()]
            hits = [w for w in words if w in haystack]
            if hits:
                severity = "high" if len(hits) >= 3 else "medium" if len(hits) == 2 else "low"
                confidence = min(0.95, 0.58 + 0.12 * len(hits))
                state = "yes"
                rationale = f"Matched category terms: {', '.join(hits)}."
                summary = f"This bill appears relevant to {c['name']} because it mentions {', '.join(hits)}."
            else:
                severity = "low"
                confidence = 0.15
                state = "no"
                rationale = "No category terms were found in the MVP text sample."
                summary = f"No clear {c['name']} concern detected in the current MVP text sample."
            out.append(
                {
                    "category_id": c["id"],
                    "flag_state": state,
                    "severity": severity,
                    "confidence": confidence,
                    "rationale": rationale,
                    "citation": bill["summary"][:240],
                    "user_summary": summary,
                }
            )
        return out


def seed_defaults() -> dict[str, int]:
    init_db()
    with connect() as db:
        now = utcnow()
        for c in DEFAULT_CATEGORIES:
            db.execute(
                "INSERT OR IGNORE INTO categories(slug,name,description,examples_positive,active,created_at) VALUES (?,?,?,?,1,?)",
                (c["slug"], c["name"], c["description"], c["examples_positive"], now),
            )
        db.execute("INSERT OR IGNORE INTO accounts(id,name,created_at) VALUES (1,'Demo Account',?)", (now,))
        db.execute(
            "INSERT OR IGNORE INTO users(id,account_id,email,role,created_at) VALUES (1,1,'demo@example.com','user',?)",
            (now,),
        )
        categories = db.execute("SELECT id FROM categories WHERE slug IN ('education','healthcare','housing')").fetchall()
        for c in categories:
            db.execute(
                "INSERT OR IGNORE INTO user_interests(user_id,category_id,min_severity,jurisdictions,active) VALUES (1,?,'low','all',1)",
                (c["id"],),
            )
        return {"categories": len(DEFAULT_CATEGORIES), "demo_user": 1}


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
        return upserted


def sync_demo_bills() -> int:
    return upsert_bills(DEMO_BILLS)


def record_ingestion_run(source_name: str, status: str, requested_limit: int, bills_seen: int = 0, bills_upserted: int = 0, message: str = "") -> dict[str, Any]:
    init_db()
    with connect() as db:
        cur = db.execute(
            """
            INSERT INTO ingestion_runs(source_name,status,requested_limit,bills_seen,bills_upserted,message,started_at,completed_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (source_name, status, requested_limit, bills_seen, bills_upserted, message, utcnow(), utcnow()),
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
        raw_bills = client.fetch_recent_bills(limit=limit)
        normalized = [normalize_congress_bill(item) for item in raw_bills]
        upserted = upsert_bills(normalized)
        run = record_ingestion_run("Congress.gov", "completed", limit, len(raw_bills), upserted, "")
        audit_result = run_audits(seed_demo=False)
        match_result = generate_matches(seed_demo=False)
        return {
            "ok": True,
            "status": "completed",
            "bills_seen": len(raw_bills),
            "bills_upserted": upserted,
            "ingestion_run": run,
            **audit_result,
            **match_result,
        }
    except Exception as exc:
        run = record_ingestion_run("Congress.gov", "failed", limit, message=str(exc))
        return {"ok": False, "status": "failed", "message": str(exc), "ingestion_run": run}


def run_audits(seed_demo: bool = True) -> dict[str, int]:
    if seed_demo:
        sync_demo_bills()
    provider = KeywordAuditor()
    with connect() as db:
        bills = db.execute("SELECT * FROM bills ORDER BY id").fetchall()
        categories = db.execute("SELECT * FROM categories WHERE active=1 ORDER BY id").fetchall()
        created = 0
        flags = 0
        for bill in bills:
            existing = db.execute(
                "SELECT id FROM audit_runs WHERE bill_id=? AND taxonomy_version=? AND prompt_version=?",
                (bill["id"], TAXONOMY_VERSION, PROMPT_VERSION),
            ).fetchone()
            if existing:
                continue
            cur = db.execute(
                "INSERT INTO audit_runs(bill_id,taxonomy_version,prompt_version,provider,status,created_at,completed_at) VALUES (?,?,?,?,?,?,?)",
                (bill["id"], TAXONOMY_VERSION, PROMPT_VERSION, provider.name, "completed", utcnow(), utcnow()),
            )
            audit_run_id = cur.lastrowid
            created += 1
            for result in provider.audit(bill, categories):
                db.execute(
                    """
                    INSERT INTO audit_flags(audit_run_id,bill_id,category_id,flag_state,severity,confidence,rationale,citation,user_summary)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        audit_run_id,
                        bill["id"],
                        result["category_id"],
                        result["flag_state"],
                        result["severity"],
                        result["confidence"],
                        result["rationale"],
                        result["citation"],
                        result["user_summary"],
                    ),
                )
                flags += 1
        return {"audit_runs_created": created, "flags_created": flags}


def generate_matches(seed_demo: bool = True) -> dict[str, int]:
    run_audits(seed_demo=seed_demo)
    created_matches = 0
    created_notifications = 0
    with connect() as db:
        rows = db.execute(
            """
            SELECT ui.user_id, ui.min_severity, af.bill_id, af.audit_run_id, af.category_id, af.severity,
                   af.user_summary, b.bill_number, b.title, c.name AS category_name
            FROM user_interests ui
            JOIN audit_flags af ON af.category_id = ui.category_id
            JOIN bills b ON b.id = af.bill_id
            JOIN categories c ON c.id = af.category_id
            WHERE ui.active=1 AND af.flag_state IN ('yes','possible')
            """
        ).fetchall()
        for r in rows:
            if SEVERITY_RANK[r["severity"]] < SEVERITY_RANK[r["min_severity"]]:
                continue
            cur = db.execute(
                "INSERT OR IGNORE INTO bill_user_matches(user_id,bill_id,audit_run_id,category_id,status,created_at) VALUES (?,?,?,?, 'new', ?)",
                (r["user_id"], r["bill_id"], r["audit_run_id"], r["category_id"], utcnow()),
            )
            if cur.rowcount:
                created_matches += 1
            match = db.execute(
                "SELECT id FROM bill_user_matches WHERE user_id=? AND bill_id=? AND category_id=?",
                (r["user_id"], r["bill_id"], r["category_id"]),
            ).fetchone()
            if match:
                ncur = db.execute(
                    "INSERT OR IGNORE INTO notifications(user_id,match_id,channel,title,body,status,created_at,delivered_at) VALUES (?,?, 'in_app', ?, ?, 'delivered', ?, ?)",
                    (
                        r["user_id"],
                        match["id"],
                        f"{r['category_name']} flag: {r['bill_number']}",
                        f"{r['title']} — {r['user_summary']}",
                        utcnow(),
                        utcnow(),
                    ),
                )
                if ncur.rowcount:
                    created_notifications += 1
        return {"matches_created": created_matches, "notifications_created": created_notifications}


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


app = FastAPI(title=APP_TITLE)


@app.on_event("startup")
def startup() -> None:
    init_db()


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
def api_seed() -> dict[str, Any]:
    return {"ok": True, **seed_defaults()}


@app.get("/api/categories")
def list_categories() -> list[dict[str, Any]]:
    seed_defaults()
    with connect() as db:
        return rows_to_dicts(db.execute("SELECT * FROM categories ORDER BY id").fetchall())


@app.post("/api/admin/categories")
def create_category(category: CategoryIn) -> dict[str, Any]:
    seed_defaults()
    with connect() as db:
        try:
            cur = db.execute(
                "INSERT INTO categories(slug,name,description,examples_positive,active,created_at) VALUES (?,?,?,?,1,?)",
                (category.slug, category.name, category.description, category.examples_positive, utcnow()),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="category slug already exists") from exc
        return dict(db.execute("SELECT * FROM categories WHERE id=?", (cur.lastrowid,)).fetchone())


@app.post("/api/admin/sync-demo-bills")
def api_sync_demo_bills() -> dict[str, Any]:
    return {"ok": True, "bills_upserted": sync_demo_bills()}


@app.post("/api/admin/sync-congress")
def api_sync_congress(limit: int = Query(default=20, ge=1, le=250)) -> dict[str, Any]:
    return sync_congress_bills(limit=limit)


@app.post("/api/admin/sync-congress-sample")
def api_sync_congress_sample(payload: CongressSampleSyncIn) -> dict[str, Any]:
    normalized = [normalize_congress_bill(item) for item in payload.bills]
    upserted = upsert_bills(normalized)
    run = record_ingestion_run("Congress.gov", "sample_completed", len(payload.bills), len(payload.bills), upserted, "sample fixture sync")
    audit_result = run_audits(seed_demo=False)
    match_result = generate_matches(seed_demo=False)
    return {"ok": True, "bills_upserted": upserted, "ingestion_run": run, **audit_result, **match_result}


@app.get("/api/admin/ingestion-runs")
def api_ingestion_runs() -> list[dict[str, Any]]:
    init_db()
    with connect() as db:
        return rows_to_dicts(db.execute("SELECT * FROM ingestion_runs ORDER BY id DESC LIMIT 25").fetchall())


@app.post("/api/admin/run-audits")
def api_run_audits() -> dict[str, Any]:
    return {"ok": True, **run_audits()}


@app.post("/api/admin/generate-matches")
def api_generate_matches() -> dict[str, Any]:
    return {"ok": True, **generate_matches()}


@app.post("/api/interests")
def set_interests(payload: InterestIn) -> dict[str, Any]:
    seed_defaults()
    with connect() as db:
        user = db.execute("SELECT id FROM users WHERE email=?", (payload.email,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="user not found")
        db.execute("UPDATE user_interests SET active=0 WHERE user_id=?", (user["id"],))
        categories = db.execute(
            f"SELECT id, slug FROM categories WHERE slug IN ({','.join(['?']*len(payload.category_slugs))})",
            payload.category_slugs,
        ).fetchall() if payload.category_slugs else []
        for c in categories:
            db.execute(
                """
                INSERT INTO user_interests(user_id,category_id,min_severity,jurisdictions,active) VALUES (?,?,?,?,1)
                ON CONFLICT(user_id, category_id) DO UPDATE SET min_severity=excluded.min_severity, active=1
                """,
                (user["id"], c["id"], payload.min_severity, "all"),
            )
        return {"ok": True, "active_interests": len(categories)}


@app.get("/api/bills")
def list_bills(category: str | None = Query(default=None)) -> list[dict[str, Any]]:
    generate_matches()
    with connect() as db:
        if category:
            rows = db.execute(
                """
                SELECT DISTINCT b.* FROM bills b
                JOIN audit_flags af ON af.bill_id=b.id
                JOIN categories c ON c.id=af.category_id
                WHERE c.slug=? AND af.flag_state IN ('yes','possible')
                ORDER BY b.updated_at DESC
                """,
                (category,),
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM bills ORDER BY updated_at DESC").fetchall()
        return rows_to_dicts(rows)


@app.get("/api/bills/{bill_id}")
def bill_detail(bill_id: int) -> dict[str, Any]:
    generate_matches()
    with connect() as db:
        bill = db.execute("SELECT * FROM bills WHERE id=?", (bill_id,)).fetchone()
        if not bill:
            raise HTTPException(status_code=404, detail="bill not found")
        flags = db.execute(
            """
            SELECT af.*, c.slug, c.name, c.description FROM audit_flags af
            JOIN categories c ON c.id=af.category_id
            WHERE af.bill_id=? ORDER BY CASE af.flag_state WHEN 'yes' THEN 1 WHEN 'possible' THEN 2 ELSE 3 END, c.name
            """,
            (bill_id,),
        ).fetchall()
        return {"bill": dict(bill), "flags": rows_to_dicts(flags), "representative_links": representative_links(bill["jurisdiction_code"])}


@app.get("/api/dashboard")
def dashboard(email: str = "demo@example.com") -> dict[str, Any]:
    generate_matches()
    with connect() as db:
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="user not found")
        matches = db.execute(
            """
            SELECT m.id AS match_id, m.status AS match_status, b.id AS bill_id, b.bill_number, b.title, b.jurisdiction_code, b.status,
                   c.slug AS category_slug, c.name AS category_name, af.severity, af.confidence, af.user_summary, af.citation
            FROM bill_user_matches m
            JOIN bills b ON b.id=m.bill_id
            JOIN categories c ON c.id=m.category_id
            JOIN audit_flags af ON af.audit_run_id=m.audit_run_id AND af.category_id=m.category_id
            WHERE m.user_id=? ORDER BY m.created_at DESC, b.updated_at DESC
            """,
            (user["id"],),
        ).fetchall()
        notifications = db.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 20",
            (user["id"],),
        ).fetchall()
        interests = db.execute(
            """
            SELECT c.slug, c.name, ui.min_severity FROM user_interests ui JOIN categories c ON c.id=ui.category_id
            WHERE ui.user_id=? AND ui.active=1 ORDER BY c.name
            """,
            (user["id"],),
        ).fetchall()
        return {
            "user": dict(user),
            "interests": rows_to_dicts(interests),
            "matches": rows_to_dicts(matches),
            "notifications": rows_to_dicts(notifications),
        }


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
def api_representative_links(jurisdiction: str = "US") -> list[dict[str, str]]:
    return representative_links(jurisdiction.upper())


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return HTML


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
    button,.chip{border:1px solid rgba(56,189,248,.35);background:rgba(56,189,248,.12);color:#e0f2fe;padding:9px 12px;border-radius:999px;cursor:pointer}button:hover{background:rgba(56,189,248,.25)}
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
  const [health,dash,cats,bills]=await Promise.all([get('/api/health'),get('/api/dashboard'),get('/api/categories'),get('/api/bills')]);
  document.getElementById('billCount').textContent=bills.length;
  document.getElementById('matchCount').textContent=dash.matches.length;
  document.getElementById('notificationCount').textContent=dash.notifications.length;
  document.getElementById('interests').innerHTML=dash.interests.map(i=>`<span class="pill">${esc(i.name)} · ${esc(i.min_severity)}+</span>`).join('') || '<p class="muted">No interests selected.</p>';
  document.getElementById('categories').innerHTML=cats.map(c=>`<span class="pill" title="${esc(c.description)}">${esc(c.name)}</span>`).join('');
  document.getElementById('notifications').innerHTML=dash.notifications.map(n=>`<div class="flag"><strong>${esc(n.title)}</strong><p class="small">${esc(n.body)}</p></div>`).join('') || '<p class="muted">No notifications yet.</p>';
  document.getElementById('matches').innerHTML=dash.matches.map(m=>`<div class="panel bill" onclick="detail(${m.bill_id})"><div class="row"><strong>${esc(m.bill_number)}</strong><span class="pill">${esc(m.jurisdiction_code)}</span><span class="pill severity-${esc(m.severity)}">${esc(m.category_name)} · ${esc(m.severity)}</span></div><h3>${esc(m.title)}</h3><p>${esc(m.user_summary)}</p><p class="small muted">Citation: ${esc(m.citation)}</p></div>`).join('') || '<p class="muted">No matched bills yet.</p>';
}
async function detail(id){
 const data=await get('/api/bills/'+id); const b=data.bill;
 document.getElementById('detail').innerHTML=`<h2>${esc(b.bill_number)} · ${esc(b.title)}</h2><p>${esc(b.summary)}</p><div class="row"><span class="pill">${esc(b.status)}</span><span class="pill">${esc(b.source_name)}</span><a href="${esc(b.source_url)}" target="_blank">official source</a><a href="${esc(b.text_url)}" target="_blank">bill text</a></div><h3>Audit flags</h3>${data.flags.map(f=>`<div class="flag ${esc(f.flag_state)}"><strong>${esc(f.name)}: ${esc(f.flag_state)}</strong> <span class="severity-${esc(f.severity)}">${esc(f.severity)} · ${Math.round(f.confidence*100)}%</span><p>${esc(f.rationale)}</p><p class="small muted">${esc(f.citation)}</p></div>`).join('')}<h3>Representative links</h3>${data.representative_links.map(l=>`<div><a href="${esc(l.url)}" target="_blank">${esc(l.label)}</a></div>`).join('')}`;
}
document.getElementById('seed').onclick=async()=>{ await post('/api/admin/seed'); await post('/api/admin/sync-demo-bills'); await post('/api/admin/run-audits'); await post('/api/admin/generate-matches'); await load(); };
document.getElementById('refresh').onclick=load;
load().catch(async e=>{ document.getElementById('matches').innerHTML='<pre>'+esc(e.message)+'</pre>'; });
</script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8844)
