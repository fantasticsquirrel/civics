from __future__ import annotations

import sqlite3
from typing import Any, Protocol

from civics_app.auth import generate_api_token, token_prefix
from civics_app.db import connect, init_db, utcnow


class AccountPayload(Protocol):
    account_name: str
    email: Any
    role: str


def create_account(payload: AccountPayload) -> dict[str, Any]:
    init_db()
    token, token_hash = generate_api_token()
    try:
        with connect() as db:
            account_cur = db.execute("INSERT INTO accounts(name,created_at) VALUES (?,?)", (payload.account_name, utcnow()))
            user_cur = db.execute(
                """INSERT INTO users(
                    account_id,email,role,api_token,api_token_hash,api_token_prefix,notification_email,created_at
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (account_cur.lastrowid, str(payload.email).lower(), payload.role, None, token_hash, token_prefix(token),
                 str(payload.email).lower(), utcnow()),
            )
            account = dict(db.execute("SELECT * FROM accounts WHERE id=?", (account_cur.lastrowid,)).fetchone())
            user = dict(db.execute("SELECT * FROM users WHERE id=?", (user_cur.lastrowid,)).fetchone())
    except sqlite3.IntegrityError as exc:
        raise ValueError("account email already exists") from exc
    for field in ("api_token", "api_token_hash", "api_token_prefix"):
        user.pop(field, None)
    return {"account": account, "user": user, "api_token": token}
