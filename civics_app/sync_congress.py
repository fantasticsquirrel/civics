from __future__ import annotations

import argparse
import json

from civics_app.main import sync_congress_bills


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync recent bills from Congress.gov into Civics Radar")
    parser.add_argument("--limit", type=int, default=25, help="Number of recent bills to request from Congress.gov")
    args = parser.parse_args()
    result = sync_congress_bills(limit=args.limit)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") or result.get("status") == "missing_api_key" else 1


if __name__ == "__main__":
    raise SystemExit(main())
