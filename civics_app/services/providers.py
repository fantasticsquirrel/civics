from __future__ import annotations


def safe_provider_error(exc: BaseException, operation: str) -> str:
    """Return an operational error that cannot echo credential-bearing URLs."""
    return f"{type(exc).__name__}: {operation} failed"
