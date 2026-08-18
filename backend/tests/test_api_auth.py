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

from app.auth import throttle
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


class TestAuthStatus:
    """``GET /api/auth/status`` — the one thing the SPA must know before it can
    interpret a 401.

    ``me`` answers 401 identically whether the gate is on or off (pinned by
    ``test_me_answers_identically_with_the_flag_off``), so a frontend that read
    401 as "logged out, go to the login page" would bounce a developer running
    with ``auth_enabled=False`` to a page that cannot help them — the login
    would succeed and the next request would 401 again forever.

    This endpoint is a **pre-auth, unauthenticated read**: it is reachable by
    anyone who can reach the port, so it says only on/off. The tests below pin
    that narrowness by asserting the exact key-set, not just the flag.
    """

    async def test_status_reports_enabled_without_a_cookie(self, auth_db: AuthDatabase) -> None:
        """Flag on, anonymous caller: 200 ``{"auth_enabled": true}``.

        ``auth_db`` sets ``auth_enabled=True``. No cookie is sent — that is the
        point: the SPA asks this question *before* it has a session.
        """
        async with _client() as client:
            response = await client.get("/api/auth/status")
        assert response.status_code == 200
        assert response.json() == {"auth_enabled": True}

    async def test_status_reports_disabled_when_the_flag_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Flag off: 200 ``{"auth_enabled": false}`` — and no auth store needed.

        Deliberately does NOT use the ``auth_db`` fixture: with the gate off
        there may be no store at all, and the endpoint must still answer rather
        than 503. Otherwise the SPA's very first request on a dev box would fail
        and it would have no way to learn the gate is off.
        """
        monkeypatch.setattr(settings, "auth_enabled", False)
        monkeypatch.setattr(app.state, "auth_db", None, raising=False)
        async with _client() as client:
            response = await client.get("/api/auth/status")
        assert response.status_code == 200
        assert response.json() == {"auth_enabled": False}

    async def test_status_leaks_nothing_beyond_the_flag(self, auth_db: AuthDatabase) -> None:
        """Exactly one key. Asserted as a key-set, not a field lookup.

        A field lookup passes just as happily when someone later adds
        ``user_count`` or ``bootstrap_required`` beside it, and those are
        exactly the "helpful" additions that turn a status probe into
        reconnaissance on an unauthenticated endpoint.
        """
        async with _client() as client:
            response = await client.get("/api/auth/status")
        assert set(response.json()) == {"auth_enabled"}


class TestLoginThrottling:
    """``POST /api/auth/login`` under ``app.auth.throttle`` (P1.6).

    The policy itself — thresholds, the doubling backoff, which scope wins,
    and the proxy-header question — is covered in ``test_auth_throttle.py``.
    What is *here* is only what the route adds: the order of the check
    relative to password verification, the status code and headers a
    throttled caller sees, and the two properties an attacker would probe for.

    ⚠️ **Only FAILED attempts are recorded**, which is why the E2E suite is
    unaffected: it performs several successful logins and exactly one
    deliberate failure per run from one address (``tests/global-setup.ts`` and
    ``tests/auth-login.spec.ts``, which runs with ``AUTH_ENABLED=true``). The
    alternative — counting every attempt and then loosening the limit until
    Playwright went green — would have tuned the security control to fit the
    test suite. Pinned by ``test_successful_logins_are_never_throttled``.
    """

    @staticmethod
    async def _fail_n(client: AsyncClient, n: int, *, email: str = EMAIL) -> None:
        for _ in range(n):
            await client.post("/api/auth/login", json={"email": email, "password": "not the password"})

    async def test_the_first_failures_are_ordinary_401s(self, auth_db: AuthDatabase) -> None:
        """Below the threshold the response is byte-identical to any other
        failed login — no counter, no hint, no ``Retry-After``.

        A throttle that announced itself from attempt one would be its own
        oracle: it would tell an attacker exactly which addresses and accounts
        are being watched, and how close they are to the line.
        """
        async with _client() as client:
            for _ in range(throttle.ACCOUNT_THRESHOLD - 1):
                response = await client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong"})
                assert response.status_code == 401
                assert response.json() == {"detail": "Invalid credentials"}
                assert "retry-after" not in response.headers

    async def test_the_threshold_attempt_locks_with_429_and_retry_after(self, auth_db: AuthDatabase) -> None:
        """Once locked the answer *does* change — 429 with a real wait.

        Differing here is safe precisely because the lock is keyed on the
        submitted email whether or not it names a real account (see
        ``test_an_unknown_email_locks_out_exactly_like_a_real_one``), so the
        429 discloses that this *client* has been failing, which they already
        know, and nothing about who exists.
        """
        async with _client() as client:
            await self._fail_n(client, throttle.ACCOUNT_THRESHOLD)
            response = await client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong"})
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) > 0

    async def test_the_lock_refuses_the_CORRECT_password(self, auth_db: AuthDatabase) -> None:
        """A lock outranks correct credentials — no session, no cookie.

        Without this, "locked out" would mean only "cannot guess", and an
        attacker who found the password on attempt 4 would still be let in on
        attempt 6.

        ⚠️ **What this does NOT pin, stated because the docstring used to claim
        it did:** that the throttle is consulted *before* ``verify_credentials``
        rather than after. Sabotage-drilled 2026-08-18 — moving the check below
        the argon2 call, but still above the 401 branch, keeps all 18 tests in
        this file green, because the two orders are indistinguishable from the
        response. The reason to keep the check first is CPU: every guess would
        otherwise still cost a real ~50 ms argon2 verification, which is the
        exhaustion half of the same attack. That claim rests on reading the
        handler, and the only test that could pin it would have to assert
        ``verify_credentials`` was never called — i.e. a mock of ``app.*``,
        which this suite forbids.

        The order that *is* pinned is the dangerous one: consulting the lock
        only on the success path (so failed guesses skip it entirely) reddens
        three tests here, ``test_the_threshold_attempt_locks_with_429_and_retry_after``
        among them.
        """
        async with _client() as client:
            await self._fail_n(client, throttle.ACCOUNT_THRESHOLD)
            response = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 429
        assert COOKIE_NAME not in response.cookies

    async def test_an_unknown_email_locks_out_exactly_like_a_real_one(self, auth_db: AuthDatabase) -> None:
        """No user enumeration through the throttle.

        Attempts are recorded against the *submitted* address, so a nonexistent
        account accrues and locks on the same schedule. Were failures recorded
        only for accounts that exist, the 429 would answer "does this user
        exist?" for anyone willing to send five requests — reintroducing, at
        the throttle, exactly the leak ``verify_credentials`` equalises timing
        to avoid.
        """
        async with _client() as client:
            await self._fail_n(client, throttle.ACCOUNT_THRESHOLD, email="ghost@example.com")
            ghost = await client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "wrong"})
        assert ghost.status_code == 429

    async def test_a_success_clears_the_account_counter(self, auth_db: AuthDatabase) -> None:
        """Mistype four times, get it right, and the slate is clean.

        Without this a user who fumbles their password near the limit each day
        would accumulate across days and eventually lock themselves out on a
        single typo.
        """
        async with _client() as client:
            await self._fail_n(client, throttle.ACCOUNT_THRESHOLD - 1)
            good = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
            assert good.status_code == 200
            # Back to a full budget: another (threshold - 1) failures do not lock.
            await self._fail_n(client, throttle.ACCOUNT_THRESHOLD - 1)
            after = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert after.status_code == 200

    async def test_successful_logins_are_never_throttled(self, auth_db: AuthDatabase) -> None:
        """Nothing counts a *success*, at either scope.

        This is the property that keeps the Playwright suite — which signs in
        repeatedly from one address — out of the throttle's way, and it is the
        honest design rather than a tuned-around limit: knowing the password
        is not an attack.
        """
        async with _client() as client:
            for _ in range(throttle.IP_THRESHOLD + 5):
                response = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
                assert response.status_code == 200

    async def test_a_deactivated_user_still_only_gets_401_until_the_threshold(self, auth_db: AuthDatabase) -> None:
        """A disabled account fails through the same path, with the same shape.

        ``verify_credentials`` returns ``None`` for it, so it must record like
        any other failure — otherwise a deactivated address would be an
        unthrottled probing target.
        """
        user = auth_db.get_user_by_email(EMAIL)
        assert user is not None
        auth_db.set_active(user.id, False)
        async with _client() as client:
            first = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
            assert first.status_code == 401
            await self._fail_n(client, throttle.ACCOUNT_THRESHOLD)
            locked = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert locked.status_code == 429
