from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class LegiScanClient:
    """Small adapter for LegiScan's getMasterList/getBill API."""

    base_url = "https://api.legiscan.com/"

    def __init__(self, api_key: str | None = None, timeout: int = 30, retries: int = 2):
        self.api_key = api_key or os.environ.get("LEGISCAN_API_KEY")
        self.timeout = timeout
        self.retries = max(1, retries)

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def status(self) -> dict[str, Any]:
        return ({"ok": True, "status": "ready"} if self.ready else
                {"ok": False, "status": "missing_api_key", "message": "Set LEGISCAN_API_KEY to enable state bill ingestion."})

    def request(self, operation: str, **params: Any) -> dict[str, Any]:
        if not self.ready:
            raise RuntimeError("missing_api_key")
        if operation not in {"getMasterList", "getBill"}:
            raise ValueError("unsupported LegiScan operation")
        query = urllib.parse.urlencode({"key": self.api_key, "op": operation, **params})
        req = urllib.request.Request(f"{self.base_url}?{query}", headers={"User-Agent": "CivicsRadar/1.0"})
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                if not isinstance(data, dict):
                    raise RuntimeError("LegiScan response was not an object")
                if data.get("status") == "ERROR":
                    raise RuntimeError("LegiScan API returned an error")
                return data
            except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
                last = exc
                if attempt + 1 < self.retries:
                    time.sleep(attempt + 1)
        detail = type(last).__name__ if last else "unknown error"
        raise RuntimeError(f"LegiScan request failed: {detail}")

    def fetch_bills(self, state: str, limit: int = 20) -> list[dict[str, Any]]:
        state = state.upper()
        if not re.fullmatch(r"[A-Z]{2}", state):
            raise ValueError("state must be a two-letter code")
        master = self.request("getMasterList", state=state).get("masterlist", {})
        refs = [v for k, v in master.items() if k != "session" and isinstance(v, dict)][:max(1, min(int(limit), 250))]
        return [self.request("getBill", id=ref["bill_id"]).get("bill", {}) for ref in refs if ref.get("bill_id")]

    def fetch_recent_bills(self, state: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.fetch_bills(state, limit)


def normalize_legiscan_bill(item: dict[str, Any]) -> dict[str, Any]:
    bill_id = str(item.get("bill_id") or "")
    state = str(item.get("state") or "").upper()
    number = str(item.get("bill_number") or "")
    if not bill_id or len(state) != 2 or not number:
        raise ValueError("LegiScan bill is missing bill_id/state/bill_number")
    texts = item.get("texts") or []
    text = texts[-1] if texts else {}
    fallback_url = f"https://legiscan.com/{state}/bill/{urllib.parse.quote(number, safe='')}/id/{urllib.parse.quote(bill_id, safe='')}"
    source_candidate = str(item.get("url") or "")
    source_parts = urllib.parse.urlsplit(source_candidate)
    source_url = source_candidate if source_parts.scheme == "https" and source_parts.netloc else fallback_url
    text_candidate = str(text.get("state_link") or text.get("url") or "")
    text_parts = urllib.parse.urlsplit(text_candidate)
    text_url = text_candidate if text_parts.scheme == "https" and text_parts.netloc else source_url
    history = item.get("history") or []
    latest = history[-1] if history else {}
    payload_hash = hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "canonical_key": f"{state.lower()}-legiscan-{bill_id}", "jurisdiction_kind": "state",
        "jurisdiction_code": state, "session": str((item.get("session") or {}).get("session_name") or item.get("session_id") or ""),
        "chamber": str(item.get("body") or "Unknown"), "bill_number": number,
        "title": str(item.get("title") or number), "summary": str(item.get("description") or item.get("title") or number),
        "status": str(latest.get("action") or item.get("status") or "Unknown"), "source_name": "LegiScan",
        "source_url": source_url, "text_url": text_url, "introduced_at": str(item.get("status_date") or latest.get("date") or ""),
        "updated_at": str(item.get("change_hash") or latest.get("date") or ""), "text_hash": f"legiscan:{payload_hash[:32]}",
        "raw_payload": item,
    }
