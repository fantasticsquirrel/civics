from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


def _redact_api_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("[redacted]" if key.lower() in {"api_key", "apikey", "key"} else _redact_api_keys(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_api_keys(item) for item in value]
    if isinstance(value, str) and "api_key=" in value:
        parts = urllib.parse.urlsplit(value)
        query = [(key, "[redacted]" if key.lower() == "api_key" else item) for key, item in urllib.parse.parse_qsl(parts.query)]
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment))
    return value


@dataclass(frozen=True)
class CongressBill:
    canonical_key: str
    jurisdiction_kind: str
    jurisdiction_code: str
    session: str
    chamber: str
    bill_number: str
    title: str
    summary: str
    status: str
    source_name: str
    source_url: str
    text_url: str
    introduced_at: str
    updated_at: str
    text_hash: str


def _bill_type_slug(bill_type: str) -> str:
    mapping = {
        "hr": "house-bill",
        "s": "senate-bill",
        "hjres": "house-joint-resolution",
        "sjres": "senate-joint-resolution",
        "hconres": "house-concurrent-resolution",
        "sconres": "senate-concurrent-resolution",
        "hres": "house-resolution",
        "sres": "senate-resolution",
    }
    return mapping.get(bill_type.lower(), bill_type.lower())


def _bill_number_display(bill_type: str, number: str) -> str:
    mapping = {
        "hr": "H.R.",
        "s": "S.",
        "hjres": "H.J.Res.",
        "sjres": "S.J.Res.",
        "hconres": "H.Con.Res.",
        "sconres": "S.Con.Res.",
        "hres": "H.Res.",
        "sres": "S.Res.",
    }
    return f"{mapping.get(bill_type.lower(), bill_type.upper())} {number}"


def _ordinal_congress(congress: int | str) -> str:
    n = int(congress)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}-congress"


def congress_public_url(congress: int | str, bill_type: str, number: str) -> str:
    return f"https://www.congress.gov/bill/{_ordinal_congress(congress)}/{_bill_type_slug(bill_type)}/{number}"


def _safe_https_url(value: Any, fallback: str) -> str:
    candidate = str(value or "")
    parts = urllib.parse.urlsplit(candidate)
    return candidate if parts.scheme == "https" and bool(parts.netloc) else fallback


def normalize_congress_bill(item: dict[str, Any]) -> dict[str, Any]:
    congress = str(item.get("congress") or "")
    bill_type = str(item.get("type") or "").lower()
    number = str(item.get("number") or "")
    if not congress or not bill_type or not number:
        raise ValueError("Congress bill item is missing congress/type/number")

    latest_action = item.get("latestAction") or {}
    status = latest_action.get("text") or item.get("status") or "Latest action unavailable"
    introduced_at = latest_action.get("actionDate") or item.get("introducedDate") or item.get("updateDate") or ""
    updated_at = item.get("updateDate") or item.get("updateDateIncludingText") or introduced_at
    title = item.get("title") or item.get("shortTitle") or _bill_number_display(bill_type, number)
    api_url = item.get("url") or f"https://api.congress.gov/v3/bill/{congress}/{bill_type}/{number}?format=json"
    public_url = congress_public_url(congress, bill_type, number)
    summary = item.get("summary") or title
    canonical_key = f"us-{congress}-{bill_type}-{number}".lower()
    text_hash = "congress-gov:" + hashlib.sha256(
        json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]

    return {
        "canonical_key": canonical_key,
        "jurisdiction_kind": "federal",
        "jurisdiction_code": "US",
        "session": congress,
        "chamber": item.get("originChamber") or item.get("chamber") or "Unknown",
        "bill_number": _bill_number_display(bill_type, number),
        "title": title,
        "summary": summary,
        "status": status,
        "source_name": "Congress.gov",
        "source_url": public_url,
        "text_url": _safe_https_url(item.get("text_url") or api_url, public_url),
        "introduced_at": introduced_at,
        "updated_at": updated_at,
        "text_hash": text_hash,
        "raw_payload": item,
    }


class CongressGovClient:
    base_url = "https://api.congress.gov/v3"

    def __init__(self, api_key: str | None = None, timeout: int = 30, retries: int = 2, backoff_seconds: float = 1.0):
        self.api_key = api_key or os.environ.get("CONGRESS_API_KEY")
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def status(self) -> dict[str, Any]:
        if not self.ready:
            return {
                "ok": False,
                "status": "missing_api_key",
                "message": "Set CONGRESS_API_KEY in the civics service environment to enable live Congress.gov ingestion.",
            }
        return {"ok": True, "status": "ready"}

    def _get_json(self, url: str) -> dict[str, Any]:
        if not self.ready:
            raise RuntimeError("missing_api_key")
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urllib.parse.urlencode({'format': 'json', 'api_key': self.api_key})}"
        req = urllib.request.Request(url, headers={"User-Agent": "CivicsRadar/0.1"})
        last_exc: BaseException | None = None
        for attempt in range(1, self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise RuntimeError("Congress.gov response was not an object")
                return payload
            except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                last_exc = exc
                if attempt >= self.retries:
                    break
                time.sleep(self.backoff_seconds * attempt)
        detail = type(last_exc).__name__ if last_exc else "unknown error"
        raise RuntimeError(f"Congress.gov request failed: {detail}")

    def fetch_recent_bills(self, limit: int = 20, offset: int = 0, enrich: bool = False) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"limit": max(1, min(int(limit), 250)), "offset": max(0, int(offset))})
        payload = self._get_json(f"{self.base_url}/bill?{query}")
        bills = payload.get("bills")
        if not isinstance(bills, list):
            raise RuntimeError("Congress.gov response did not include a bills list")
        return [self.enrich_bill(item) for item in bills] if enrich else bills

    def enrich_bill(self, item: dict[str, Any]) -> dict[str, Any]:
        congress, bill_type, number = item.get("congress"), str(item.get("type") or "").lower(), item.get("number")
        if not congress or not bill_type or not number:
            return item
        root = f"{self.base_url}/bill/{congress}/{bill_type}/{number}"
        enriched = dict(item)
        payloads: dict[str, Any] = {"list_item": item}
        for name, suffix in (("detail", ""), ("summaries", "/summaries"), ("text", "/text"), ("actions", "/actions")):
            try:
                payloads[name] = self._get_json(root + suffix)
            except RuntimeError:
                payloads[name] = {}
        if isinstance(payloads["detail"].get("bill"), dict):
            enriched.update(payloads["detail"]["bill"])
        summaries = payloads["summaries"].get("summaries") or []
        if summaries:
            enriched["summary"] = str(summaries[0].get("text") or enriched.get("summary") or enriched.get("title") or "")
        versions = payloads["text"].get("textVersions") or []
        for version in versions:
            formats = version.get("formats") or []
            preferred = next((fmt for kind in ("Formatted Text", "PDF", "Formatted XML") for fmt in formats if fmt.get("type") == kind), None)
            if preferred and preferred.get("url"):
                enriched["text_url"] = _safe_https_url(preferred["url"], congress_public_url(congress, bill_type, number))
                break
        enriched["history"] = payloads["actions"].get("actions") or []
        enriched["raw_payload"] = _redact_api_keys(payloads)
        return enriched
