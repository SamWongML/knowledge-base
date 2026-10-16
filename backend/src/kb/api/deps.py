from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from kb.config import get_settings


def check_token(authorization: str | None) -> bool:
    expected = get_settings().api_token
    if expected is None or not expected.get_secret_value():
        return True
    if not authorization or not authorization.lower().startswith("bearer "):
        return False
    return secrets.compare_digest(authorization[7:].strip(), expected.get_secret_value())


async def require_token(authorization: str | None = Header(default=None)) -> None:
    """Guard for write endpoints. Reads are open when the server only listens on localhost."""
    if not check_token(authorization):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing bearer token")
