"""Every API route is behind a session, and a new one cannot slip through.

This is the guard the P1.2 work exists for. The dependency wiring in
``app/main.py`` is the easy half; this file is the half that keeps it true
after somebody adds an endpoint six months from now and never reads this rule.

⚠️ TWO WAYS THIS TEST CAN PASS WHILE GUARDING NOTHING. Both were live traps on
FastAPI 0.140 / Starlette 1.3 and both are defended against below — do not
"simplify" either one away.

1. **``app.routes`` does not contain the endpoints.** Included routers are kept
   as lazy ``_IncludedRouter`` objects, so a walk of ``app.routes`` yields
   exactly TWO ``APIRoute`` objects (``/api/health`` and ``/api/languages``) out
   of the 72 this app actually serves. The naive enumeration finds only routes
   that are already exempt and reports total success. ``_iter_api_routes``
   recurses through ``original_router`` instead, and ``test_enumeration_finds_
   the_whole_surface`` pins a floor so a future FastAPI that breaks the
   traversal turns this file RED rather than vacuously green.

2. **``route.dependencies`` is empty even for a guarded route.** An
   ``include_router(..., dependencies=[...])`` does NOT copy the dependency onto
   the route object; it lives on the ``_IncludedRouter``'s ``include_context``.
   So a structural test asserting ``require_user in route.dependencies`` reads
   an always-empty list and can only be made to pass by asserting the wrong
   thing. That is why the primary oracle here is BEHAVIOURAL: make the request,
   demand a 401.

Verified 2026-08-18: a router-level dependency runs BEFORE body validation and
before path-parameter coercion — a guarded ``POST`` with no body at all answers
401, not 422. That is what makes a blind sweep over every route possible
without constructing a valid request for any of them.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app

# Paths that legitimately answer without a session. Keep this SHORT and keep
# every entry justified — it is the only way an endpoint escapes the sweep.
#
#   /api/health        — the liveness probe runs before anyone logs in.
#   /api/auth/login    — login cannot require being logged in.
#   /api/auth/logout   — logging out with an already-expired session should
#                        succeed quietly, not 401 at someone clicking "log out".
#
# ⚠️ This is an exact-path list, NOT the "/api/auth/" prefix it started as.
# A prefix would blanket-exempt /api/auth/me — the one endpoint that most needs
# sweeping, since it reports who you are and an anonymous caller must get 401
# rather than a null user. Widening this back to a prefix silently drops that
# route out of the sweep.
#
# Deliberately NOT exempt: /api/languages. It is read-only metadata, but the
# rule here is default-deny, and "the login page needs it" is a P1.4 question to
# answer by moving the data, not by opening the endpoint.
EXEMPT_PATHS = frozenset({"/api/health", "/api/auth/login", "/api/auth/logout"})
EXEMPT_PREFIXES = ()

# The non-APIRoute paths FastAPI mounts for its own docs. They are not covered
# by router dependencies, so they are recorded here rather than silently
# ignored — see test_docs_surface_is_known.
DOCS_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})

# The app served 72 APIRoute objects when this guard was written. The floor is
# deliberately close to that number: it exists to catch a BROKEN TRAVERSAL (the
# 2-route trap above), not to police growth. If a real refactor drops the count
# below this, read the docstring before lowering it — a shrinking number here is
# far more likely to mean the walk stopped working than that the API shrank.
MIN_EXPECTED_ROUTES = 60

_PATH_PARAM_RE = re.compile(r"\{[^}]+\}")


def _iter_api_routes(routes) -> list[APIRoute]:
    """Every APIRoute reachable from *routes*, recursing into included routers.

    ``_IncludedRouter`` exposes the router it wraps as ``original_router``; it
    has no ``.routes`` of its own. See this module's docstring for why walking
    ``app.routes`` directly is not enough.
    """
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        elif type(route).__name__ == "_IncludedRouter":
            found.extend(_iter_api_routes(route.original_router.routes))
        elif hasattr(route, "routes"):
            found.extend(_iter_api_routes(route.routes))
    return found


def _is_exempt(path: str) -> bool:
    return path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES)


def _concrete_path(path: str) -> str:
    """Fill path parameters with a placeholder.

    The value is never parsed: auth rejects the request before FastAPI coerces
    path parameters, which is the verified behaviour this sweep relies on.
    """
    return _PATH_PARAM_RE.sub("1", path)


def _sweepable() -> list[tuple[str, str]]:
    """(method, path) for every non-exempt route, deduplicated and sorted."""
    pairs = set()
    for route in _iter_api_routes(app.routes):
        if _is_exempt(route.path):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            pairs.add((method, route.path))
    return sorted(pairs)


class TestEnumeration:
    """The traversal itself is load-bearing, so it gets its own assertions."""

    def test_enumeration_finds_the_whole_surface(self) -> None:
        """A traversal that silently stops working must be RED, not green.

        This is the anti-vacuity guard. Walking ``app.routes`` without recursing
        into ``original_router`` yields 2 routes, both of them exempt, and every
        other test in this file would then pass while asserting nothing.
        """
        routes = _iter_api_routes(app.routes)
        assert len(routes) >= MIN_EXPECTED_ROUTES, (
            f"found only {len(routes)} APIRoutes; expected at least "
            f"{MIN_EXPECTED_ROUTES}. The traversal is probably broken — read "
            f"this module's docstring before lowering the floor."
        )

    def test_sweep_is_not_empty(self) -> None:
        """The exempt list must not be able to swallow the entire surface."""
        assert len(_sweepable()) >= MIN_EXPECTED_ROUTES

    def test_docs_surface_is_known(self) -> None:
        """Record the non-APIRoute paths so a new one cannot appear unnoticed.

        Router-level dependencies do not cover these. Whether /docs and
        /openapi.json should be reachable unauthenticated in production is a
        real question — they publish the whole API surface — but it is P1.3's
        to answer. This test only guarantees nobody adds a fifth one quietly.
        """
        non_api = {r.path for r in app.routes if not isinstance(r, APIRoute) and hasattr(r, "path")}
        assert non_api == DOCS_PATHS


class TestEveryRouteRequiresASession:
    @pytest.fixture(autouse=True)
    def _auth_on(self, monkeypatch: pytest.MonkeyPatch):
        """Turn auth on for this class only.

        ``auth_enabled`` must be read per-request inside the dependency, not at
        ``include_router`` time — if the flag were consulted while wiring, it
        would be frozen at import and this fixture could not move it.
        """
        monkeypatch.setattr(settings, "auth_enabled", True)

    @pytest.mark.parametrize(("method", "path"), _sweepable())
    async def test_no_cookie_is_401(self, method: str, path: str) -> None:
        """Every non-exempt endpoint rejects an anonymous request with 401.

        Not 403 (that would mean "authenticated but not allowed"), and not 500
        (which is what an auth check that reaches for missing app state does).
        No request body and a placeholder path param are deliberate: auth runs
        ahead of validation, so a bare request is enough to prove the gate.
        """
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, _concrete_path(path))
        assert response.status_code == 401, (
            f"{method} {path} answered {response.status_code}, not 401 — it is not behind require_user"
        )

    async def test_a_bogus_token_is_401(self) -> None:
        """A well-formed cookie that matches no session row is still rejected."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", cookies={"tt_session": "a" * 43}) as client:
            response = await client.get("/api/languages")
        assert response.status_code == 401


class TestExemptPathsStayOpen:
    async def test_health_answers_without_a_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The liveness probe runs before anyone can log in.

        Asserts only that it is not 401 — /api/health answers 503 with no
        lifespan, which is correct and is test_api_core's business, not this
        file's.
        """
        monkeypatch.setattr(settings, "auth_enabled", True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
        assert response.status_code != 401
