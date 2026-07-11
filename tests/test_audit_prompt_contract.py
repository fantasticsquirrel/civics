from civics_app.audit_providers import PROMPT_PATH, PROMPT_VERSION


def test_versioned_prompt_has_neutrality_citation_and_json_contract():
    prompt = PROMPT_PATH.read_text(encoding="utf-8").lower()
    assert PROMPT_VERSION == "bill-audit-v1"
    assert "neutral civic language" in prompt
    assert "do not advocate" in prompt
    assert "cite" in prompt and "provider-supplied text" in prompt
    assert "return json only" in prompt
    assert "{{taxonomy_json}}" in prompt and "{{bill_json}}" in prompt
