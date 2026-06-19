import os

from fastapi.testclient import TestClient

from civics_app.congress import CongressBill, CongressGovClient, normalize_congress_bill
from civics_app.main import app, connect


SAMPLE_LIST_ITEM = {
    "congress": 119,
    "type": "HR",
    "number": "123",
    "originChamber": "House",
    "title": "School Meal Modernization Act",
    "url": "https://api.congress.gov/v3/bill/119/hr/123?format=json",
    "updateDate": "2026-06-19T10:00:00Z",
    "latestAction": {"text": "Referred to the Committee on Education and Workforce.", "actionDate": "2026-06-18"},
}


def test_normalize_congress_bill_preserves_source_provenance():
    normalized = normalize_congress_bill(SAMPLE_LIST_ITEM)

    assert normalized["canonical_key"] == "us-119-hr-123"
    assert normalized["jurisdiction_kind"] == "federal"
    assert normalized["jurisdiction_code"] == "US"
    assert normalized["session"] == "119"
    assert normalized["chamber"] == "House"
    assert normalized["bill_number"] == "H.R. 123"
    assert normalized["title"] == "School Meal Modernization Act"
    assert normalized["status"] == "Referred to the Committee on Education and Workforce."
    assert normalized["source_name"] == "Congress.gov"
    assert normalized["source_url"] == "https://www.congress.gov/bill/119th-congress/house-bill/123"
    assert normalized["text_url"] == "https://api.congress.gov/v3/bill/119/hr/123?format=json"
    assert normalized["text_hash"].startswith("congress-gov:")


def test_congress_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("CONGRESS_API_KEY", raising=False)
    client = CongressGovClient()

    assert client.ready is False
    assert client.status()["status"] == "missing_api_key"


def test_sync_congress_endpoint_records_missing_key_status(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICS_DB", str(tmp_path / "civics.db"))
    monkeypatch.delenv("CONGRESS_API_KEY", raising=False)
    client = TestClient(app)

    response = client.post("/api/admin/sync-congress?limit=2")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["status"] == "missing_api_key"
    with connect() as db:
        row = db.execute("SELECT source_name, status FROM ingestion_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["source_name"] == "Congress.gov"
    assert row["status"] == "missing_api_key"


def test_upsert_congress_bills_from_sample(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICS_DB", str(tmp_path / "civics.db"))
    client = TestClient(app)

    response = client.post("/api/admin/sync-congress-sample", json={"bills": [SAMPLE_LIST_ITEM]})

    assert response.status_code == 200
    assert response.json()["bills_upserted"] == 1
    bills = client.get("/api/bills").json()
    assert any(b["canonical_key"] == "us-119-hr-123" for b in bills)
    bill = next(b for b in bills if b["canonical_key"] == "us-119-hr-123")
    assert bill["source_name"] == "Congress.gov"
    assert bill["source_url"].startswith("https://www.congress.gov/bill/")
