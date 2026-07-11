import json

import pytest

from civics_app.audit_providers import AUDIT_MODEL, KeywordAuditProvider, OpenAIAuditProvider, configured_audit_provider


BILL = {"bill_number": "HB 1", "title": "School Support Act", "summary": "Provides school grants.",
        "jurisdiction_code": "MO", "source_name": "LegiScan", "source_url": "https://example.test/record",
        "text_url": "https://example.test/text"}
CATEGORIES = [{"id": 4, "slug": "education", "name": "Education", "description": "Schools",
               "examples_positive": "school, student"}]


def test_deterministic_provider_is_default_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(configured_audit_provider(), KeywordAuditProvider)
    assert configured_audit_provider().audit(BILL, CATEGORIES)[0]["category_id"] == 4


def test_openai_provider_uses_sol_and_validates_structured_output(monkeypatch):
    output = {"categories": [{"category_slug": "education", "flag_state": "yes", "severity": "medium",
        "confidence": 0.9, "rationale": "School grants are explicit.", "citation": "Provider summary: school grants",
        "user_summary": "Provides school grants.", "affected_groups": ["students"], "concerns": ["funding"]}]}
    captured = {}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps({"output_text": json.dumps(output)}).encode()
    def fake_open(request, timeout):
        captured.update(json.loads(request.data))
        return Response()
    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    result = OpenAIAuditProvider(api_key="not-a-real-key").audit(BILL, CATEGORIES)
    assert captured["model"] == AUDIT_MODEL == "gpt-5.6-sol"
    assert captured["text"]["format"]["strict"] is True
    assert result[0]["category_id"] == 4


def test_other_audit_models_are_refused(monkeypatch):
    monkeypatch.setenv("CIVICS_AUDIT_MODEL", "other-model")
    with pytest.raises(ValueError):
        OpenAIAuditProvider(api_key="not-a-real-key")
