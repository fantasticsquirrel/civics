from __future__ import annotations

import argparse
import json
import sys

from civics_app.notification_delivery import deliver_notifications


def main() -> int:
    parser = argparse.ArgumentParser(description="Deliver queued Civics Radar notifications")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    result = deliver_notifications(limit=max(1, min(args.limit, 500)))
    print(json.dumps(result, sort_keys=True))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
