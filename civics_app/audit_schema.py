from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CategoryAuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category_slug: str = Field(min_length=2, max_length=64)
    flag_state: Literal["yes", "possible", "no"]
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=4000)
    citation: str = Field(min_length=1, max_length=2000)
    user_summary: str = Field(min_length=1, max_length=2000)
    affected_groups: list[str] = Field(default_factory=list, max_length=50)
    concerns: list[str] = Field(default_factory=list, max_length=50)


class BillAuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categories: list[CategoryAuditResult] = Field(min_length=1, max_length=100)


def validate_audit_payload(payload: object, allowed_slugs: set[str]) -> BillAuditResult:
    result = BillAuditResult.model_validate(payload)
    actual = [item.category_slug for item in result.categories]
    unknown = set(actual) - allowed_slugs
    missing = allowed_slugs - set(actual)
    if unknown:
        raise ValueError(f"unknown category slugs: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"missing category slugs: {', '.join(sorted(missing))}")
    if len(actual) != len(set(actual)):
        raise ValueError("category slugs must be unique")
    return result


def openai_json_schema(category_slugs: list[str]) -> dict[str, object]:
    item_properties: dict[str, object] = {
        "category_slug": {"type": "string", "enum": category_slugs},
        "flag_state": {"type": "string", "enum": ["yes", "possible", "no"]},
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string", "minLength": 1},
        "citation": {"type": "string", "minLength": 1},
        "user_summary": {"type": "string", "minLength": 1},
        "affected_groups": {"type": "array", "items": {"type": "string"}},
        "concerns": {"type": "array", "items": {"type": "string"}},
    }
    return {
        "type": "object",
        "properties": {"categories": {"type": "array", "items": {
            "type": "object", "properties": item_properties,
            "required": list(item_properties), "additionalProperties": False,
        }}},
        "required": ["categories"],
        "additionalProperties": False,
    }
