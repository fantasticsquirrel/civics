from __future__ import annotations

import hashlib
import json
import sqlite3


def taxonomy_version(db: sqlite3.Connection) -> str:
    rows = db.execute(
        "SELECT slug,name,description,examples_positive FROM categories WHERE active=1 ORDER BY slug"
    ).fetchall()
    payload = json.dumps([dict(row) for row in rows], sort_keys=True, separators=(",", ":"))
    return "taxonomy-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def latest_bill_versions(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        """SELECT bv.id AS bill_version_id,bv.bill_id FROM bill_versions bv
           JOIN (SELECT bill_id,MAX(id) AS latest_id FROM bill_versions GROUP BY bill_id) latest
             ON latest.latest_id=bv.id"""
    ).fetchall()
