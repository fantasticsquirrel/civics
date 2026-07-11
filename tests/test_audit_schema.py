import pytest
from pydantic import ValidationError

from civics_app.audit_schema import validate_audit_payload


def valid_payload():
    return {"categories": [{
        "category_slug": "education", "flag_state": "possible", "severity": "medium", "confidence": 0.72,
        "rationale": "The summary mentions schools.", "citation": "Provider summary: schools",
        "user_summary": "The bill may affect schools.", "affected_groups": ["students"], "concerns": ["funding"],
    }]}


def test_valid_audit_payload_parses():
    result = validate_audit_payload(valid_payload(), {"education"})
    assert result.categories[0].confidence == 0.72


@pytest.mark.parametrize("mutation", [
    lambda item: item.update(category_slug="unknown"),
    lambda item: item.update(severity="critical"),
    lambda item: item.pop("citation"),
])
def test_invalid_audit_payload_is_rejected(mutation):
    payload = valid_payload()
    mutation(payload["categories"][0])
    with pytest.raises((ValidationError, ValueError)):
        validate_audit_payload(payload, {"education"})
