#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    source = Path(os.environ.get("CIVICS_DB", "/opt/civics/data/civics.db"))
    destination_dir = Path(os.environ.get("CIVICS_BACKUP_DIR", "/opt/civics/backups"))
    retention = max(1, int(os.environ.get("CIVICS_BACKUP_RETENTION", "14")))
    if not source.is_file():
        raise SystemExit(f"database not found: {source}")
    destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"civics-{stamp}.db"
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit("backup integrity check failed")
    destination.chmod(0o600)
    backups = sorted(destination_dir.glob("civics-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for expired in backups[retention:]:
        expired.unlink()
    print(destination)


if __name__ == "__main__":
    main()
