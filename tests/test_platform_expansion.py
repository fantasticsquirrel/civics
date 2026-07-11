import json
import urllib.error

import pytest
from fastapi.testclient import TestClient

from civics_app.auth import hash_api_token, verify_api_token
from civics_app.legiscan import LegiScanClient, normalize_legiscan_bill
from civics_app.main import app, connect


@pytest.fixture()
def tenants(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICS_DB", str(tmp_path / "civics.db"))
    monkeypatch.setenv("CIVICS_BOOTSTRAP_ADMIN_TOKEN", "bootstrap-secret-at-least-32-bytes")
    c = TestClient(app, backend_options={"use_uvloop": True}); root = {"Authorization": "Bearer bootstrap-secret-at-least-32-bytes"}
    def make(name, email, role="user"):
        out = c.post("/api/admin/accounts", headers=root, json={"account_name": name, "email": email, "role": role}).json()
        return {"Authorization": f"Bearer {out['api_token']}"}
    return c, root, make("One", "one@example.com", "admin"), make("Two", "two@example.com")


def test_auth_rbac_hashing_and_no_secret_leakage(tenants):
    c, root, admin, user = tenants
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/me", headers={"Authorization": "Bearer bad"}).status_code == 401
    assert c.post("/api/admin/categories", headers=user, json={}).status_code == 403
    assert c.post("/api/admin/accounts", headers=admin, json={}).status_code == 403
    me = c.get("/api/me", headers=admin).json()["user"]
    assert not {"api_token", "api_token_hash", "api_token_prefix"} & me.keys()
    encoded = hash_api_token("secret")
    assert "secret" not in encoded and verify_api_token("secret", encoded) and not verify_api_token("wrong", encoded)


def test_saved_views_notifications_and_read_state_are_isolated(tenants):
    c, _, one, two = tenants
    view = c.post("/api/saved-views", headers=one, json={"name": "Education", "filters": {"category": "education"}}).json()
    assert c.get("/api/saved-views", headers=two).json() == []
    assert c.delete(f"/api/saved-views/{view['id']}", headers=two).status_code == 404
    assert c.delete(f"/api/saved-views/{view['id']}", headers=one).status_code == 204
    prefs = c.post("/api/notification-preferences", headers=one,
                   json={"digest_frequency": "daily", "channels": ["in_app", "email"]}).json()
    assert prefs["digest_frequency"] == "daily"


def test_validation_security_headers_and_rate_limit(tenants):
    c, _, admin, _ = tenants
    bad = c.post("/api/interests", headers=admin,
                 json={"category_slugs": [], "min_severity": "critical", "jurisdictions": ["MOO"]})
    assert bad.status_code == 422
    response = c.get("/", headers=admin)
    for header in ("content-security-policy", "x-content-type-options", "x-frame-options", "referrer-policy"):
        assert header in response.headers


def test_legiscan_adapter_fetches_and_normalizes(monkeypatch):
    payloads = [{"status":"OK","masterlist":{"session":{},"1":{"bill_id":7}}}, {"status":"OK","bill":{
        "bill_id":7,"state":"MO","bill_number":"HB 7","title":"School Act","description":"School funding",
        "session":{"session_name":"2026"},"body":"House","url":"https://legiscan.com/MO/bill/HB7/2026",
        "texts":[{"state_link":"https://house.mo.gov/bill.pdf"}],"history":[{"date":"2026-01-01","action":"Introduced"}]}}]
    class R:
        def __init__(self,p): self.p=p
        def __enter__(self): return self
        def __exit__(self,*a): return False
        def read(self): return json.dumps(self.p).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: R(payloads.pop(0)))
    bills = LegiScanClient("key").fetch_bills("mo", 1)
    normalized = normalize_legiscan_bill(bills[0])
    assert normalized["canonical_key"] == "mo-legiscan-7"
    assert normalized["text_url"] == "https://house.mo.gov/bill.pdf"


def test_provider_urls_reject_active_content_schemes():
    item = {
        "bill_id": 7, "state": "MO", "bill_number": "HB 7", "title": "School Act",
        "url": "javascript:alert(1)", "texts": [{"state_link": "data:text/html,bad"}],
    }
    normalized = normalize_legiscan_bill(item)
    assert normalized["source_url"].startswith("https://legiscan.com/")
    assert normalized["text_url"] == normalized["source_url"]


def test_legiscan_missing_key_records_per_state_status(tenants, monkeypatch):
    monkeypatch.delenv("LEGISCAN_API_KEY", raising=False)
    client, _, admin, _ = tenants
    response = client.post("/api/admin/sync-legiscan?state=MO&limit=2", headers=admin)
    assert response.status_code == 200
    assert response.json()["status"] == "missing_api_key"
    states = client.get("/api/admin/provider-health", headers=admin).json()["providers"]["legiscan"]["states"]
    assert states[0]["source_name"] == "LegiScan:MO"
    assert states[0]["jurisdiction_code"] == "MO"
