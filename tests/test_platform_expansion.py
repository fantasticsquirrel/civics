import json
import urllib.error

from fastapi.testclient import TestClient

from civics_app.congress import CongressGovClient
from civics_app.main import app, connect, sync_demo_bills, upsert_bills


def test_congress_client_retries_transient_failures(monkeypatch):
    calls = {"count": 0}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"bills": [{"congress": 119, "type": "hr", "number": "1"}]}).encode()

    def fake_urlopen(req, timeout):
        calls["count"] += 1
        if calls["count"] < 3:
            raise TimeoutError("temporary timeout")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _: None)

    client = CongressGovClient(api_key="test-key", timeout=1, retries=3, backoff_seconds=0)

    assert client.fetch_recent_bills(limit=1)[0]["number"] == "1"
    assert calls["count"] == 3


def test_provider_health_reports_congress_and_legiscan_status(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICS_DB", str(tmp_path / "civics.db"))
    monkeypatch.delenv("CONGRESS_API_KEY", raising=False)
    monkeypatch.delenv("LEGISCAN_API_KEY", raising=False)
    client = TestClient(app)

    health = client.get("/api/admin/provider-health").json()

    assert health["providers"]["congress_gov"]["status"] == "missing_api_key"
    assert health["providers"]["legiscan"]["status"] == "missing_api_key"
    assert "representative_lookup" in health


def test_bill_versions_and_audit_jobs_created_once_per_text_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICS_DB", str(tmp_path / "civics.db"))
    client = TestClient(app)
    bill = {
        "canonical_key": "us-119-hr-999",
        "jurisdiction_kind": "federal",
        "jurisdiction_code": "US",
        "session": "119",
        "chamber": "House",
        "bill_number": "H.R. 999",
        "title": "School Privacy Act",
        "summary": "Protects student privacy and school records.",
        "status": "Introduced",
        "source_name": "Congress.gov",
        "source_url": "https://www.congress.gov/bill/119th-congress/house-bill/999",
        "text_url": "https://api.congress.gov/v3/bill/119/hr/999?format=json",
        "introduced_at": "2026-01-01",
        "updated_at": "2026-01-02T00:00:00Z",
        "text_hash": "hash-one",
        "raw_payload": {"number": "999"},
    }

    assert upsert_bills([bill]) == 1
    assert upsert_bills([bill]) == 1

    with connect() as db:
        versions = db.execute("SELECT * FROM bill_versions").fetchall()
        jobs = db.execute("SELECT * FROM audit_jobs").fetchall()
    assert len(versions) == 1
    assert len(jobs) == 1
    assert jobs[0]["status"] == "queued"

    worker = client.post("/api/admin/process-audit-jobs?limit=5").json()
    assert worker["jobs_completed"] == 1
    again = client.post("/api/admin/process-audit-jobs?limit=5").json()
    assert again["jobs_completed"] == 0


def test_accounts_auth_interests_saved_views_and_search(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICS_DB", str(tmp_path / "civics.db"))
    client = TestClient(app)
    client.post("/api/admin/seed")
    created = client.post(
        "/api/admin/accounts",
        json={"account_name": "Watch Org", "email": "watch@example.com", "role": "admin"},
    ).json()
    token = created["api_token"]

    me = client.get("/api/me", headers={"X-API-Token": token}).json()
    assert me["user"]["email"] == "watch@example.com"
    assert me["account"]["name"] == "Watch Org"

    interests = client.post(
        "/api/interests",
        headers={"X-API-Token": token},
        json={"category_slugs": ["education", "civil-rights"], "min_severity": "medium", "jurisdictions": ["US", "MO"]},
    ).json()
    assert interests["active_interests"] == 2

    saved = client.post(
        "/api/saved-views",
        headers={"X-API-Token": token},
        json={"name": "My education watch", "filters": {"category": "education", "jurisdiction": "US"}},
    ).json()
    assert saved["name"] == "My education watch"
    assert client.get("/api/saved-views", headers={"X-API-Token": token}).json()[0]["filters"]["category"] == "education"

    client.post("/api/admin/sync-demo-bills")
    bills = client.get("/api/bills?search=library&jurisdiction=US&status=Introduced").json()
    assert len(bills) == 1
    assert bills[0]["jurisdiction_code"] == "US"


def test_notification_preferences_and_digest_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICS_DB", str(tmp_path / "civics.db"))
    client = TestClient(app)
    client.post("/api/admin/generate-matches")

    prefs = client.post(
        "/api/notification-preferences",
        json={"email": "demo@example.com", "digest_frequency": "daily", "channels": ["in_app", "email"]},
    ).json()
    assert prefs["digest_frequency"] == "daily"
    digest = client.get("/api/notifications/digest?email=demo@example.com").json()
    assert digest["notification_count"] >= 3
    assert "sections" in digest


def test_legiscan_missing_key_records_status(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICS_DB", str(tmp_path / "civics.db"))
    monkeypatch.delenv("LEGISCAN_API_KEY", raising=False)
    client = TestClient(app)

    body = client.post("/api/admin/sync-legiscan?state=MO&limit=2").json()

    assert body["ok"] is False
    assert body["status"] == "missing_api_key"
    with connect() as db:
        row = db.execute("SELECT source_name, status FROM ingestion_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["source_name"] == "LegiScan"
    assert row["status"] == "missing_api_key"
