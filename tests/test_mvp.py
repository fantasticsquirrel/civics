import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICS_DB", str(tmp_path / "civics-test.db"))
    from civics_app.main import app

    return TestClient(app)


def test_pipeline_audits_each_bill_once_and_fans_out_matches(client):
    assert client.post("/api/admin/seed").status_code == 200
    assert client.post("/api/admin/sync-demo-bills").json()["bills_upserted"] == 3

    first_audit = client.post("/api/admin/run-audits").json()
    assert first_audit["audit_runs_created"] == 3
    assert first_audit["flags_created"] == 15

    second_audit = client.post("/api/admin/run-audits").json()
    assert second_audit["audit_runs_created"] == 0
    assert second_audit["flags_created"] == 0

    matches = client.post("/api/admin/generate-matches").json()
    assert matches["matches_created"] >= 3
    assert matches["notifications_created"] >= 3

    dashboard = client.get("/api/dashboard").json()
    assert {m["category_slug"] for m in dashboard["matches"]} >= {"education", "healthcare", "housing"}
    assert len(dashboard["notifications"]) == len(dashboard["matches"])


def test_category_creation_and_interest_selection(client):
    client.post("/api/admin/seed")
    response = client.post(
        "/api/admin/categories",
        json={
            "slug": "energy",
            "name": "Energy & Utilities",
            "description": "Energy generation, utilities, grid reliability, and rates.",
            "examples_positive": "energy, utility, grid, electricity",
        },
    )
    assert response.status_code == 200
    assert response.json()["slug"] == "energy"

    interests = client.post(
        "/api/interests",
        json={"email": "demo@example.com", "category_slugs": ["energy", "healthcare"], "min_severity": "medium"},
    )
    assert interests.status_code == 200
    assert interests.json()["active_interests"] == 2

    dashboard = client.get("/api/dashboard").json()
    active = {i["slug"] for i in dashboard["interests"]}
    assert active == {"energy", "healthcare"}


def test_bill_detail_exposes_audit_citations_and_representative_links(client):
    client.post("/api/admin/generate-matches")
    bills = client.get("/api/bills").json()
    assert bills

    detail = client.get(f"/api/bills/{bills[0]['id']}").json()
    assert detail["bill"]["source_url"].startswith("https://")
    assert detail["flags"]
    assert all("citation" in flag for flag in detail["flags"])
    labels = {link["label"] for link in detail["representative_links"]}
    assert "USA.gov: find elected officials" in labels
