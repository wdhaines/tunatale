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


async def require_owner(request: Request) -> None:
    """Refuse (403) anyone but the deployment's owner — see app.storage.user_dbs.

    For what is process-global and therefore the OWNER's by construction: Anki
    sync (one collection path, one AnkiWeb login — ``run_full_sync`` driven by
    another account would push that account's reviews into the owner's Anki)
    and the admin tools. ``is_owner`` is bound by main.py's
    ``_resolve_language_state`` on every request, True whenever auth is off;
    its absence fails CLOSED. Listed AFTER ``require_user`` on a router, so an
    anonymous caller still gets the 401 that says "log in".
    """
    if not getattr(request.state, "is_owner", False):
        raise HTTPException(status_code=403, detail="Only this deployment's owner can do that")


async def require_owner_for_writes(request: Request) -> None:
    """``require_owner`` for everything but reads.

    For the lesson surfaces. Another account reads its own (empty) lesson store
    — that is how its pages render their empty states — but generation runs
    through the background ``LessonPipeline``, which is keyed by language alone
    and writes the OWNER's stores. Until it is keyed by user, lesson writes are
    the owner's.
    """
    if request.method in ("GET", "HEAD"):
        return
    await require_owner(request)
