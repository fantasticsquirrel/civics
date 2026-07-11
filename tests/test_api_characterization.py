from fastapi.testclient import TestClient

from civics_app.db import connect
from civics_app.main import app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICS_DB", str(tmp_path / "civics.db"))
    monkeypatch.setenv("CIVICS_BOOTSTRAP_ADMIN_TOKEN", "characterization-bootstrap-secret-32-bytes")
    client = TestClient(app, backend_options={"use_uvloop": True})
    root = {"Authorization": "Bearer characterization-bootstrap-secret-32-bytes"}
    created = client.post("/api/admin/accounts", headers=root, json={
        "account_name": "Characterization", "email": "characterization@example.com", "role": "admin",
    }).json()
    auth = {"Authorization": f"Bearer {created['api_token']}"}
    return client, auth


def test_health_provider_dashboard_and_bill_search_contracts(tmp_path, monkeypatch):
    client, auth = _client(tmp_path, monkeypatch)
    assert client.post("/api/admin/sync-demo-bills", headers=auth).status_code == 200
    assert client.post("/api/admin/run-audits", headers=auth).status_code == 200
    assert client.post("/api/admin/generate-matches", headers=auth).status_code == 200

    health = client.get("/api/health").json()
    assert health["ok"] is True and set(health["counts"]) == {
        "categories", "bills", "audit_runs", "notifications",
    }
    providers = client.get("/api/admin/provider-health", headers=auth).json()["providers"]
    assert {"congress_gov", "legiscan"} <= set(providers)
    dashboard = client.get("/api/dashboard", headers=auth).json()
    assert {"user", "interests", "matches", "notifications"} <= set(dashboard)
    bills = client.get("/api/bills?search=library&jurisdiction=US", headers=auth).json()
    assert bills["total"] == 1 and bills["items"][0]["canonical_key"] == "us-119-hr-1234"


def test_read_endpoints_do_not_seed_demo_data_or_queue_audits(tmp_path, monkeypatch):
    client, auth = _client(tmp_path, monkeypatch)
    assert client.get("/api/bills", headers=auth).json()["total"] == 0
    assert client.get("/api/dashboard", headers=auth).status_code == 200
    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM bills").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM audit_jobs").fetchone()[0] == 0


def test_saved_view_filters_are_bounded_and_preferences_round_trip(tmp_path, monkeypatch):
    client, auth = _client(tmp_path, monkeypatch)
    invalid = client.post("/api/saved-views", headers=auth, json={
        "name": "Unsafe", "filters": {"unexpected": "value"},
    })
    assert invalid.status_code == 422
    saved = client.post("/api/notification-preferences", headers=auth, json={
        "digest_frequency": "daily", "channels": ["in_app", "email"],
        "notification_email": "alerts@example.com",
    }).json()
    loaded = client.get("/api/notification-preferences", headers=auth).json()
    assert loaded["digest_frequency"] == saved["digest_frequency"] == "daily"
    assert loaded["channels"] == ["in_app", "email"]
    assert loaded["notification_email"] == "alerts@example.com"
