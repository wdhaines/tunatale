"""Login → cookie → authenticated request → logout, through the real app.

**This is the positive path, and nothing else in the suite can provide it.**

``test_auth_route_coverage.py`` proves every endpoint is *gated*; it runs
without a lifespan and asserts 401 everywhere, so it reads a permanently
broken auth store as total success — that blind spot is real and was measured
(P1.2 shipped with no lifespan wiring at all and the sweep stayed 76/76 green).
``test_auth_dependencies.py`` proves ``require_user``'s branches against a
hand-built request object, which never exercises routing, the cookie header, or
FastAPI's dependency resolution.

Only a round trip through the actual ASGI app can answer "can anyone actually
get in, and does getting out actually work". Every assertion here is about the
join, not about a component.

The auth DB is seeded onto ``app.state`` directly rather than through the
lifespan: the lifespan opens content DBs, an LLM client and the generation
pipeline, none of which this file needs, and ``test_main_lifespan.py`` already
owns the "the lifespan wires auth_db" claim.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.database import AuthDatabase
from app.auth.session import COOKIE_NAME
from app.config import settings
from app.main import app

EMAIL = "roundtrip@example.com"
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
    """A client over **https**, and the scheme is load-bearing.

    The session cookie is set with ``Secure``, so over ``http://`` httpx stores
    it in the jar and then never sends it back — login succeeds, every
    subsequent request is anonymous, and logout cannot identify the row to
    delete. That reads exactly like a broken implementation and is not one.
    Measured 2026-08-18: identical code answers ``me`` 401 over http and 200
    over https. ASGITransport ignores the scheme otherwise, so this costs
    nothing and matches production, where the app sits behind TLS.
    """
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


class TestRoundTrip:
    async def test_login_then_me_then_logout(self, auth_db: AuthDatabase) -> None:
        """The whole loop, in one test, because the loop is the claim.

        Split into three tests it would be possible for each to pass against a
        broken join — a login that sets a cookie nothing reads, or a logout that
        clears the client's cookie while leaving the row alive server-side.
        """
        async with _client() as client:
            # Anonymous /me is 401 — the precondition, so a later 200 means something.
            assert (await client.get("/api/auth/me")).status_code == 401

            login = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
            assert login.status_code == 200, login.text
            assert COOKIE_NAME in login.cookies, "login did not set the session cookie"

            # The client now carries the cookie; the protected endpoint opens.
            me = await client.get("/api/auth/me")
            assert me.status_code == 200, me.text
            assert me.json()["email"] == EMAIL

            logout = await client.post("/api/auth/logout")
            assert logout.status_code in (200, 204), logout.text

            # And the door is shut again.
            assert (await client.get("/api/auth/me")).status_code == 401

    async def test_logout_kills_the_session_SERVER_side(self, auth_db: AuthDatabase) -> None:
        """Replaying the old cookie after logout must fail.

        The distinction this draws is the whole point: clearing the cookie in
        the browser is a client-side courtesy, and a logout that only does that
        leaves a stolen cookie valid until it expires. So the token is captured
        before logout and replayed on a FRESH client afterwards, which never saw
        the ``Set-Cookie`` that cleared it.
        """
        async with _client() as client:
            login = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
            token = login.cookies[COOKIE_NAME]
            assert (await client.post("/api/auth/logout")).status_code in (200, 204)

        # Cookies go on the client instance: httpx deprecates per-request
        # cookies because the persistence semantics are ambiguous.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test", cookies={COOKIE_NAME: token}
        ) as replay:
            response = await replay.get("/api/auth/me")
        assert response.status_code == 401, "the session row outlived logout — a stolen cookie would still work"

    async def test_login_response_body_never_carries_the_token(self, auth_db: AuthDatabase) -> None:
        """The token lives in Set-Cookie and nowhere else.

        A token echoed into the JSON body is readable by any script on the page,
        which is precisely what HttpOnly exists to prevent — the cookie flag
        buys nothing if the value is also in the response.
        """
        async with _client() as client:
            login = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
            token = login.cookies[COOKIE_NAME]
        assert token not in login.text
        # The hash must not leak either — it is what the DB stores, so publishing
        # it hands over the lookup key for every session row.
        assert "token" not in {k.lower() for k in login.json()}


class TestNoUserEnumeration:
    """A failed login must not reveal whether the email exists.

    Only the response SHAPE is asserted. The timing half of that claim is not
    established by any test here: ``verify_credentials`` runs a dummy argon2
    verify on the unknown-email path to flatten the obvious signal, but proving
    constant time needs statistics this suite has no business running, and a
    threshold-based timing assertion would be a flake generator. Stated rather
    than quietly implied by a green test.
    """

    async def test_unknown_email_and_wrong_password_are_indistinguishable(self, auth_db: AuthDatabase) -> None:
        async with _client() as client:
            unknown = await client.post("/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
            wrong = await client.post("/api/auth/login", json={"email": EMAIL, "password": "not the password"})

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json() == wrong.json()
        assert COOKIE_NAME not in unknown.cookies
        assert COOKIE_NAME not in wrong.cookies

    async def test_failed_login_says_nothing_about_the_account(self, auth_db: AuthDatabase) -> None:
        """The error text must not name the email or hint at which half failed."""
        async with _client() as client:
            response = await client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong"})
        body = response.text.lower()
        assert EMAIL.lower() not in body
        for leak in ("no such user", "unknown email", "not found", "wrong password", "incorrect password"):
            assert leak not in body, f"login error leaks which half failed: {leak!r}"


class TestDisabledAuthIsUnchanged:
    async def test_login_still_works_with_auth_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With the flag off the endpoints still function.

        ``auth_enabled=False`` turns the *gate* off, not the login machinery.
        If login 500s in that mode, flipping the flag on in production becomes
        the first time anyone exercises it — which is the wrong time to find out.
        """
        db = AuthDatabase(":memory:")
        db.create_user(EMAIL, PASSWORD)
        monkeypatch.setattr(settings, "auth_enabled", False)
        monkeypatch.setattr(app.state, "auth_db", db, raising=False)
        try:
            async with _client() as client:
                login = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
                assert login.status_code == 200, login.text
                assert COOKIE_NAME in login.cookies
        finally:
            db.close()

    async def test_me_answers_identically_with_the_flag_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`/api/auth/me` does not change behaviour with the gate disabled.

        Pinned because it is easy to "simplify" into a change. Injecting the
        dependency (``user: User | None = Depends(require_user)``) instead of
        declaring it in ``dependencies=`` removes a duplicate lookup and looks
        strictly better — but ``require_user`` short-circuits to ``None`` when
        the flag is off, so the injected form would 401 a caller holding a
        perfectly valid cookie. This test is what turns that from a silent
        regression into a failure.
        """
        db = AuthDatabase(":memory:")
        db.create_user(EMAIL, PASSWORD)
        monkeypatch.setattr(app.state, "auth_db", db, raising=False)
        try:
            monkeypatch.setattr(settings, "auth_enabled", True)
            async with _client() as client:
                await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
                monkeypatch.setattr(settings, "auth_enabled", False)
                assert (await client.get("/api/auth/me")).status_code == 200

            async with _client() as anon:
                assert (await anon.get("/api/auth/me")).status_code == 401
        finally:
            db.close()
