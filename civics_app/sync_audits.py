from __future__ import annotations

import argparse
import json
import sys

from civics_app.main import process_audit_jobs


def main() -> int:
    parser = argparse.ArgumentParser(description="Process queued Civics audit jobs")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    result = {"ok": True, **process_audit_jobs(limit=args.limit)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
