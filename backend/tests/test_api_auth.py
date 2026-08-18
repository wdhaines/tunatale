"""Endpoint-level tests for the auth API.

These cover the endpoints' own edges against a real ``AuthDatabase(":memory:")``
seeded onto ``app.state``.  No mocking of ``app.*``.

The locked files cover the joins (login→me→logout round-trip, session
invalidation, no-user-enumeration).  These cover the boundary cases the
locked sweep structurally cannot: malformed bodies, deactivated users,
whitespace-insensitive login, password-hash exclusion, no-cookie logout,
double-logout, and expired sessions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.database import AuthDatabase
from app.auth.session import COOKIE_NAME
from app.config import settings
from app.main import app

EMAIL = "test@example.com"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def auth_db(monkeypatch: pytest.MonkeyPatch) -> AuthDatabase:
    """A real in-memory auth store, seeded with one user, bound to the app."""
    db = AuthDatabase(":memory:")
    db.create_user(EMAIL, PASSWORD)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(app.state, "auth_db", db, raising=False)
    yield db
    db.close()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


class TestLoginEdgeCases:
    async def test_missing_password_returns_422(self, auth_db: AuthDatabase) -> None:
        """A malformed body (missing ``password``) is a schema error → 422."""
        async with _client() as client:
            response = await client.post("/api/auth/login", json={"email": EMAIL})
        assert response.status_code == 422
        assert COOKIE_NAME not in response.cookies

    async def test_deactivated_user_returns_401(self, auth_db: AuthDatabase) -> None:
        """Deactivated users get the same 401 as any other failed login."""
        user = auth_db.get_user_by_email(EMAIL)
        assert user is not None
        auth_db.set_active(user.id, False)
        async with _client() as client:
            response = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid credentials"}

    async def test_email_is_case_and_whitespace_insensitive(self, auth_db: AuthDatabase) -> None:
        """Login normalisation happens in the store — prove it survives the endpoint."""
        async with _client() as client:
            for email in (EMAIL.upper(), f"  {EMAIL}  ", "TEST@EXAMPLE.COM"):
                response = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
                assert response.status_code == 200, f"failed for {email!r}"


class TestMeEdgeCases:
    async def test_me_returns_email_no_password_hash(self, auth_db: AuthDatabase) -> None:
        """The ``me`` response carries email but NOT password_hash — assert the
        key is absent from the JSON, not merely that the value differs."""
        async with _client() as client:
            await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
            me = await client.get("/api/auth/me")
        assert me.status_code == 200
        body = me.json()
        assert body["email"] == EMAIL
        assert "password_hash" not in body


class TestLogoutEdgeCases:
    async def test_logout_without_cookie_succeeds(self, auth_db: AuthDatabase) -> None:
        """Someone clicking "log out" with no cookie should not get a 401."""
        async with _client() as client:
            response = await client.post("/api/auth/logout")
        assert response.status_code == 200

    async def test_logout_twice_in_a_row_succeeds(self, auth_db: AuthDatabase) -> None:
        """Two consecutive logouts both return 200."""
        async with _client() as client:
            await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
            first = await client.post("/api/auth/logout")
            assert first.status_code == 200
            second = await client.post("/api/auth/logout")
            assert second.status_code == 200


class TestExpiredSession:
    async def test_expired_cookie_returns_401(self, auth_db: AuthDatabase) -> None:
        """An expired session cookie must be rejected by ``me``."""
        async with _client() as client:
            login = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
            token = login.cookies[COOKIE_NAME]
        assert token is not None

        # Artificially expire the session row by backdating expires_at
        from app.auth.tokens import hash_token

        token_h = hash_token(token)
        past = datetime.now(UTC) - timedelta(days=1)
        with auth_db._get_conn() as conn:
            conn.execute(
                "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                (past.isoformat(), token_h),
            )
            conn.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://test",
            cookies={COOKIE_NAME: token},
        ) as replay:
            response = await replay.get("/api/auth/me")
        assert response.status_code == 401


class TestAuthStoreUnavailable:
    async def test_login_returns_503_when_the_auth_db_is_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No auth store ⇒ 503 on login, not 401 and not a 500 traceback.

        This is the deployment-misconfiguration path — the store failed to open
        at startup — and the three plausible answers are meaningfully different:

        - **500** would be an unhandled ``AttributeError``, i.e. a crash.
        - **401** would say "your credentials are wrong", which is a lie that
          sends the operator hunting for a bad password instead of a bad mount.
        - **503** says the server cannot serve this right now, which is true.

        Note the deliberate asymmetry with ``require_user``, which answers 401
        in the same situation: a *gate* with no store must fail closed, because
        401 there is the safe answer. Login has nothing to protect yet, so it
        can afford to be honest. Neither path lets anyone in.
        """
        monkeypatch.setattr(settings, "auth_enabled", True)
        monkeypatch.delattr(app.state, "auth_db", raising=False)
        async with _client() as client:
            response = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 503
        assert COOKIE_NAME not in response.cookies
