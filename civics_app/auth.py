from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
from typing import Any

from fastapi import Depends, Header, HTTPException, status

TOKEN_BYTES = 32
PBKDF2_ITERATIONS = 200_000


def validate_bootstrap_token() -> None:
    token = os.environ.get("CIVICS_BOOTSTRAP_ADMIN_TOKEN")
    if token and len(token.encode("utf-8")) < 32:
        raise RuntimeError("CIVICS_BOOTSTRAP_ADMIN_TOKEN must contain at least 32 bytes")


def generate_api_token() -> tuple[str, str]:
    # The public selector permits one indexed lookup; only the secret portion is
    # authenticated and only its slow hash is stored.
    token = f"cr_{secrets.token_hex(8)}_{secrets.token_urlsafe(TOKEN_BYTES)}"
    return token, hash_api_token(token)


def token_prefix(token: str) -> str | None:
    parts = token.split("_", 2)
    return "_".join(parts[:2]) if len(parts) == 3 and parts[0] == "cr" else None


def hash_api_token(token: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_api_token(token: str, encoded_hash: str | None) -> bool:
    if not token or not encoded_hash:
        return False
    try:
        algorithm, iteration_text, salt_text, digest_text = encoded_hash.split("$", 3)
        iterations = int(iteration_text)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        salt = base64.b64decode(salt_text.encode("ascii"), altchars=b"-_", validate=True)
        expected = base64.b64decode(digest_text.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        token.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(digest, expected)


def bearer_token_from_header(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer token required")
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or len(token) > 512 or any(char.isspace() for char in token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer token required")
    return token


def lookup_user_by_bearer_token(token: str) -> sqlite3.Row | dict[str, Any]:
    bootstrap_token = os.environ.get("CIVICS_BOOTSTRAP_ADMIN_TOKEN")
    if bootstrap_token and hmac.compare_digest(token, bootstrap_token):
        return {"id": None, "account_id": None, "email": "bootstrap-admin@local", "role": "system_admin", "bootstrap": True}

    from civics_app.db import connect, init_db

    init_db()
    prefix = token_prefix(token)
    if not prefix:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")
    with connect() as db:
        user = db.execute(
            "SELECT * FROM users WHERE api_token_prefix=? AND api_token_hash IS NOT NULL AND active=1",
            (prefix,),
        ).fetchone()
    if user and verify_api_token(token, user["api_token_hash"]):
        return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")


def current_user(authorization: str | None = Header(default=None, alias="Authorization")) -> sqlite3.Row | dict[str, Any]:
    token = bearer_token_from_header(authorization)
    return lookup_user_by_bearer_token(token)


def require_admin(user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> sqlite3.Row | dict[str, Any]:
    if user["role"] not in {"admin", "system_admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin access required")
    return user


def require_system_admin(user: sqlite3.Row | dict[str, Any] = Depends(current_user)) -> sqlite3.Row | dict[str, Any]:
    if user["role"] != "system_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="system administrator access required")
    return user
