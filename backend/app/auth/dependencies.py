"""FastAPI dependency that gates every route behind an active session."""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.auth.models import User
from app.auth.session import COOKIE_NAME, get_session_user
from app.config import settings


async def require_user(request: Request) -> User | None:
    """Return the authenticated user or raise 401.

    ``auth_enabled`` is read **per request** so tests can monkeypatch it
    after import — this is the pinned design decision the locked test
    relies on.
    """
    if not settings.auth_enabled:
        return None

    token = request.cookies.get(COOKIE_NAME)
    if token is None:
        raise HTTPException(status_code=401)

    auth_db = getattr(request.app.state, "auth_db", None)
    user = get_session_user(auth_db, token)
    if user is None:
        raise HTTPException(status_code=401)

    return user
