from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

from civics_app.audit_schema import openai_json_schema, validate_audit_payload

AUDIT_MODEL = "gpt-5.6-sol"
PROMPT_VERSION = "bill-audit-v1"
PROMPT_PATH = Path(__file__).with_name("prompts") / "bill_audit_v1.md"


class AuditProvider:
    name = "base"
    model = ""

    def audit(self, bill: Mapping[str, Any], categories: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        raise NotImplementedError


class KeywordAuditProvider(AuditProvider):
    name = "keyword-deterministic"
    model = "keyword-screen-v1"

    def audit(self, bill: Mapping[str, Any], categories: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        haystack = f"{bill['title']} {bill['summary']}".lower()
        results: list[dict[str, Any]] = []
        for category in categories:
            words = [word.strip().lower() for word in category["examples_positive"].split(",") if word.strip()]
            hits = [word for word in words if word in haystack]
            state = "yes" if hits else "no"
            severity = "high" if len(hits) >= 3 else "medium" if len(hits) == 2 else "low"
            confidence = min(0.95, 0.58 + 0.12 * len(hits)) if hits else 0.15
            results.append({
                "category_slug": category["slug"], "category_id": category["id"],
                "flag_state": state, "severity": severity, "confidence": confidence,
                "rationale": f"Matched category terms: {', '.join(hits)}." if hits else "No category terms were found in the available provider text.",
                "citation": f"Provider summary excerpt (not verified bill text): {bill['summary'][:200]}",
                "user_summary": (f"This bill appears relevant to {category['name']} because it mentions {', '.join(hits)}."
                                 if hits else f"No clear {category['name']} concern detected in the available provider text."),
                "affected_groups": [], "concerns": hits,
            })
        return results


class OpenAIAuditProvider(AuditProvider):
    name = "openai"
    model = AUDIT_MODEL

    def __init__(self, api_key: str | None = None, timeout: int = 90):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        configured_model = os.environ.get("CIVICS_AUDIT_MODEL", AUDIT_MODEL)
        if configured_model != AUDIT_MODEL:
            raise ValueError(f"CIVICS_AUDIT_MODEL must be {AUDIT_MODEL}")
        self.timeout = timeout

    def audit(self, bill: Mapping[str, Any], categories: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("missing_api_key")
        category_data = [{"slug": row["slug"], "name": row["name"], "description": row["description"]} for row in categories]
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        input_text = prompt.replace("{{TAXONOMY_JSON}}", json.dumps(category_data, ensure_ascii=False)).replace(
            "{{BILL_JSON}}", json.dumps({key: bill[key] for key in (
                "bill_number", "title", "summary", "jurisdiction_code", "source_name", "source_url", "text_url"
            )}, ensure_ascii=False))
        slugs = [item["slug"] for item in category_data]
        payload = {
            "model": self.model,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": input_text}]}],
            "text": {"format": {"type": "json_schema", "name": "bill_audit", "strict": True,
                                "schema": openai_json_schema(slugs)}},
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses", data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "User-Agent": "CivicsRadar/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"OpenAI audit request failed: {type(exc).__name__}") from exc
        output_text = response_data.get("output_text") or next(
            (part.get("text") for item in response_data.get("output", []) for part in item.get("content", [])
             if part.get("type") == "output_text"), None)
        if not output_text:
            raise RuntimeError("OpenAI audit response contained no output text")
        parsed = validate_audit_payload(json.loads(output_text), set(slugs))
        category_ids = {row["slug"]: row["id"] for row in categories}
        return [{**item.model_dump(), "category_id": category_ids[item.category_slug]} for item in parsed.categories]


def configured_audit_provider() -> AuditProvider:
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIAuditProvider()
    return KeywordAuditProvider()
