import pytest
from fastapi.testclient import TestClient

from civics_app.db import connect
from civics_app.main import DEMO_BILLS, app, process_audit_jobs, upsert_bills


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICS_DB", str(tmp_path / "civics.db"))
    monkeypatch.setenv("CIVICS_BOOTSTRAP_ADMIN_TOKEN", "test-bootstrap-secret-at-least-32-bytes")
    client = TestClient(app, backend_options={"use_uvloop": True})
    system = {"Authorization": "Bearer test-bootstrap-secret-at-least-32-bytes"}
    client.post("/api/admin/seed", headers=system)
    created = client.post("/api/admin/accounts", headers=system,
                          json={"account_name": "Watch Org", "email": "watch@example.com", "role": "admin"}).json()
    user = {"Authorization": f"Bearer {created['api_token']}"}
    return client, system, user


def test_pipeline_is_idempotent_and_feed_is_tenant_scoped(api):
    client, _, user = api
    assert client.post("/api/admin/sync-demo-bills", headers=user).json()["bills_upserted"] == 3
    first = client.post("/api/admin/run-audits", headers=user).json()
    second = client.post("/api/admin/run-audits", headers=user).json()
    assert first["audit_runs_created"] == 3
    assert second["audit_runs_created"] == 0
    assert client.get("/api/dashboard", headers=user).json()["user"]["email"] == "watch@example.com"


def test_categories_interests_and_bill_provenance(api):
    client, _, user = api
    response = client.post("/api/admin/categories", headers=user, json={
        "slug": "energy", "name": "Energy & Utilities",
        "description": "Energy generation and grid reliability.", "examples_positive": "energy, grid"})
    assert response.status_code == 200
    assert client.post("/api/interests", headers=user, json={
        "category_slugs": ["energy", "healthcare"], "min_severity": "medium", "jurisdictions": ["US", "MO"]
    }).json()["active_interests"] == 2
    client.post("/api/admin/sync-demo-bills", headers=user)
    client.post("/api/admin/run-audits", headers=user)
    client.post("/api/admin/generate-matches", headers=user)
    bills = client.get("/api/bills?page_size=2&sort=title&order=asc", headers=user).json()
    assert bills["total"] == 3 and len(bills["items"]) == 2
    detail = client.get(f"/api/bills/{bills['items'][0]['id']}", headers=user).json()
    assert detail["official_sources"]["record"].startswith("https://")
    assert detail["audit_disclaimer"] and detail["audit_runs"]
    assert detail["audit_runs"][0]["provider"] == "keyword-deterministic"


def test_ui_is_accessible_multi_view_shell(api):
    client, _, _ = api
    html = client.get("/").text
    for label in ("Feed", "Bills", "Alerts", "Saved Views", "Settings", "Admin"):
        assert label in html
    assert 'href="#content"' in html and 'aria-label="Primary"' in html


def test_audits_use_the_immutable_bill_version_snapshot(api):
    _, _, _ = api
    first = {**DEMO_BILLS[0], "canonical_key": "us-version-test", "text_hash": "version-one",
             "title": "School Support Act", "summary": "Provides school grants."}
    second = {**first, "text_hash": "version-two", "title": "Road Maintenance Act",
              "summary": "Repairs highways and bridges."}
    upsert_bills([first])
    upsert_bills([second])
    process_audit_jobs(limit=100, generate_after=False)
    with connect() as db:
        states = [row[0] for row in db.execute("""
            SELECT af.flag_state FROM audit_flags af
            JOIN audit_runs ar ON ar.id=af.audit_run_id
            JOIN bill_versions bv ON bv.id=ar.bill_version_id
            JOIN categories c ON c.id=af.category_id
            JOIN bills b ON b.id=ar.bill_id
            WHERE b.canonical_key='us-version-test' AND c.slug='education'
            ORDER BY bv.id
        """).fetchall()]
    assert states == ["yes", "no"]


def test_taxonomy_changes_supersede_old_jobs_and_queue_latest_versions(api):
    client, _, admin = api
    client.post("/api/admin/sync-demo-bills", headers=admin)
    created = client.post("/api/admin/categories", headers=admin, json={
        "slug": "water", "name": "Water", "description": "Water systems and quality.",
        "examples_positive": "water, utility",
    }).json()
    deactivated = client.patch(
        f"/api/admin/categories/{created['id']}", headers=admin, json={"active": False},
    ).json()
    assert created["taxonomy_version"] != deactivated["taxonomy_version"]
    with connect() as db:
        statuses = {row[0]: row[1] for row in db.execute(
            "SELECT status,COUNT(*) FROM audit_jobs GROUP BY status"
        ).fetchall()}
    assert statuses["superseded"] >= 3
    assert statuses["queued"] == 3
