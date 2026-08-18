"""Unit tests for require_user and session helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.auth.database import AuthDatabase
from app.auth.dependencies import require_user
from app.auth.models import User
from app.config import settings

# ── Minimal request mock (no app.* mocking) ─────────────────────────────────


@dataclass
class _FakeState:
    auth_db: AuthDatabase | None = None


@dataclass
class _FakeApp:
    state: _FakeState


@dataclass
class _FakeRequest:
    cookies: dict[str, str]
    app: _FakeApp

    @classmethod
    def with_db(cls, db: AuthDatabase | None, *, cookie: str | None = None) -> _FakeRequest:
        cookies = {}
        if cookie is not None:
            cookies["tt_session"] = cookie
        return cls(cookies=cookies, app=_FakeApp(state=_FakeState(auth_db=db)))

    @classmethod
    def no_db(cls, *, cookie: str = "x") -> _FakeRequest:
        return cls(cookies={"tt_session": cookie}, app=_FakeApp(state=_FakeState()))


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def auth_db() -> AuthDatabase:
    return AuthDatabase(":memory:")


@pytest.fixture(autouse=True)
def _auth_disabled():
    """Ensure auth is OFF so each test must opt in."""
    original = settings.auth_enabled
    settings.auth_enabled = False
    yield
    settings.auth_enabled = original


def _make_user(db: AuthDatabase, *, email: str = "u@example.com") -> User:
    return db.create_user(email, "Password123!")


def _make_session(db: AuthDatabase, user_id: int, *, ttl: timedelta | None = None) -> str:
    token, _ = db.create_session(user_id, ttl=ttl)
    return token


# ── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_disabled_returns_none(auth_db: AuthDatabase) -> None:
    """Case 1: auth_enabled=False → returns None, cookie ignored."""
    token = _make_session(auth_db, _make_user(auth_db).id)
    req = _FakeRequest.with_db(auth_db, cookie=token)
    result = await require_user(req)
    assert result is None


@pytest.mark.asyncio
async def test_auth_disabled_ignores_cookie(auth_db: AuthDatabase) -> None:
    """Case 1 (cont): even a bogus cookie is fine when auth is off."""
    req = _FakeRequest.with_db(auth_db, cookie="bogus")
    result = await require_user(req)
    assert result is None


@pytest.mark.asyncio
async def test_auth_enabled_no_cookie_raises_401() -> None:
    """Case 2: no cookie → 401."""
    settings.auth_enabled = True
    req = _FakeRequest.no_db()
    with pytest.raises(HTTPException) as exc_info:
        await require_user(req)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_valid_cookie_returns_user(auth_db: AuthDatabase) -> None:
    """Case 3: valid cookie → returns the correct User."""
    settings.auth_enabled = True
    user = _make_user(auth_db)
    token = _make_session(auth_db, user.id)
    req = _FakeRequest.with_db(auth_db, cookie=token)
    result = await require_user(req)
    assert isinstance(result, User)
    assert result.id == user.id
    assert result.email == user.email


@pytest.mark.asyncio
async def test_expired_session_raises_401(auth_db: AuthDatabase) -> None:
    """Case 4: expired session → 401."""
    settings.auth_enabled = True
    user = _make_user(auth_db)
    token, session = auth_db.create_session(user.id, ttl=timedelta(seconds=-1))
    req = _FakeRequest.with_db(auth_db, cookie=token)
    with pytest.raises(HTTPException) as exc_info:
        await require_user(req)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_bogus_token_raises_401(auth_db: AuthDatabase) -> None:
    """Case 5: token not in DB → 401."""
    settings.auth_enabled = True
    _make_user(auth_db)
    req = _FakeRequest.with_db(auth_db, cookie="token_that_never_existed")
    with pytest.raises(HTTPException) as exc_info:
        await require_user(req)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_deactivated_user_session_rejected(auth_db: AuthDatabase) -> None:
    """Case 6: session for a deactivated user → 401.

    ``set_active(False)`` deletes sessions, so we bypass it by directly
    setting ``is_active = 0`` on the user row — the session survives but
    the user is deactivated.  This exercises the ``user.is_active`` check
    in ``get_session_user`` that the normal flow can never reach.
    """
    settings.auth_enabled = True
    user = _make_user(auth_db)
    token, _ = auth_db.create_session(user.id)
    # Deactivate without deleting sessions (bypasses set_active's cleanup)
    with auth_db._get_conn() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user.id,))
        auth_db._commit(conn)
    req = _FakeRequest.with_db(auth_db, cookie=token)
    with pytest.raises(HTTPException) as exc_info:
        await require_user(req)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_no_auth_db_in_state_raises_401_not_500() -> None:
    """Case 7: app.state.auth_db absent → 401, not AttributeError → 500."""
    settings.auth_enabled = True
    req = _FakeRequest.no_db()
    with pytest.raises(HTTPException) as exc_info:
        await require_user(req)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_all_rejections_are_401_http_exception(auth_db: AuthDatabase) -> None:
    """Case 8: every rejection path raises HTTPException(status_code=401)."""
    settings.auth_enabled = True
    cases = [
        _FakeRequest.no_db(),
        _FakeRequest.with_db(auth_db, cookie="no_such_token"),
        _FakeRequest.with_db(auth_db),  # no cookie at all
    ]
    for req in cases:
        with pytest.raises(HTTPException) as exc_info:
            await require_user(req)
        assert exc_info.value.status_code == 401
