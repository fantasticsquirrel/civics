from __future__ import annotations

import json
from typing import Any


ALLOWED_SORT_COLUMNS = frozenset({"updated_at", "introduced_at", "title", "bill_number"})


def immutable_version_payload(bill: dict[str, Any]) -> str:
    """Store both the normalized audit input and untouched provider provenance."""
    normalized = {key: value for key, value in bill.items() if key != "raw_payload"}
    payload = {"normalized_bill": normalized, "provider_payload": bill.get("raw_payload", bill)}
    return json.dumps(payload, sort_keys=True, default=str)


def audit_bill_for_version(current_bill: Any, raw_payload: str | None) -> dict[str, Any]:
    current = dict(current_bill)
    try:
        payload = json.loads(raw_payload or "{}")
    except (json.JSONDecodeError, TypeError):
        return current
    snapshot = payload.get("normalized_bill") if isinstance(payload, dict) else None
    if not isinstance(snapshot, dict):
        return current
    # The immutable snapshot is authoritative, while the current row supplies
    # compatibility defaults for databases created before snapshots existed.
    return {**current, **snapshot}


def validated_sort(sort: str, order: str) -> tuple[str, str]:
    """Defense-in-depth for identifiers interpolated into SQLite ORDER BY."""
    if sort not in ALLOWED_SORT_COLUMNS or order not in {"asc", "desc"}:
        raise ValueError("invalid bill sort")
    return sort, order.upper()


def provider_actions(raw_payload: Any) -> list[dict[str, str]]:
    if not isinstance(raw_payload, dict):
        return []
    candidates = raw_payload.get("history") or ([raw_payload.get("latestAction")] if raw_payload.get("latestAction") else [])
    actions = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        date = str(item.get("date") or item.get("actionDate") or "")
        description = str(item.get("action") or item.get("text") or "")
        if date and description:
            actions.append({"action_date": date, "description": description})
    return actions
