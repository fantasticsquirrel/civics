from __future__ import annotations

import argparse
import json
import sys

from civics_app.main import sync_legiscan_bills


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync state bills from LegiScan")
    parser.add_argument("--state", default="MO")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    result = sync_legiscan_bills(args.state, args.limit)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") or result.get("status") == "missing_api_key" else 1


if __name__ == "__main__":
    sys.exit(main())
