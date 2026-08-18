"""Auth endpoints — login, logout, me.

``/api/auth/me`` is the only route that carries ``require_user`` as a
dependency; login and logout are unauthenticated.  The router itself is
mounted **without** a router-level dependency (see ``main.py``) because
``include_router(dependencies=...)`` would apply to every route including
login, which obviously cannot require being logged in.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.api.models import (
    AuthStatusResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    MeResponse,
)
from app.auth.dependencies import require_user
from app.auth.models import User
from app.auth.session import (
    COOKIE_HTTPONLY,
    COOKIE_NAME,
    COOKIE_SAMESITE,
    COOKIE_SECURE,
    get_session_user,
)
from app.config import settings

router = APIRouter()


@router.get("/api/auth/status", response_model=AuthStatusResponse)
async def auth_status() -> dict[str, bool]:
    """Report whether this deployment requires a login.

    **Why this exists at all:** ``/api/auth/me`` answers 401 for an anonymous
    caller whether ``auth_enabled`` is True or False, so a 401 alone does not
    mean "you are logged out" — with the gate off it means "no cookie, and none
    is needed". A frontend that redirected on every 401 would send a developer
    running with the flag off to a login page that cannot fix anything: the
    login would succeed and the next request would 401 again. The SPA therefore
    reads this first and only treats a 401 as "go to /login" when the answer is
    True.

    **Unauthenticated on purpose, and narrow on purpose.** It is asked before a
    session exists, so it cannot be gated; that makes it readable by anyone who
    can reach the port, so it answers exactly one boolean. Do not add user
    counts, a bootstrap-needed flag, or the configured email — those turn a
    status probe into reconnaissance. It is exempt in
    ``tests/test_auth_route_coverage.py``; the key-set is pinned by
    ``test_status_leaks_nothing_beyond_the_flag``.

    Reads ``settings`` per request rather than at import, matching
    ``require_user`` — the flag is monkeypatched in tests after import.
    """
    return {"auth_enabled": settings.auth_enabled}


@router.post("/api/auth/login", response_model=LoginResponse)
async def login(request: Request, body: LoginRequest) -> Response:
    """Verify credentials and set a session cookie.

    Both unknown-email and wrong-password produce the same 401 body — no
    user enumeration.
    """
    auth_db = getattr(request.app.state, "auth_db", None)
    if auth_db is None:
        raise HTTPException(status_code=503, detail="Auth unavailable")

    user = auth_db.verify_credentials(body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token, _session = auth_db.create_session(user.id)

    body_obj = LoginResponse(email=user.email)
    response = JSONResponse(content=body_obj.model_dump())
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=COOKIE_HTTPONLY,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=settings.session_ttl_days * 86400,
    )
    return response


@router.post("/api/auth/logout", response_model=LogoutResponse)
async def logout(request: Request) -> Response:
    """Delete the session row, then clear the cookie.

    Succeeds even when no cookie is present or the session is already gone —
    someone clicking "log out" on an expired session should not get an error.
    """
    auth_db = getattr(request.app.state, "auth_db", None)
    token = request.cookies.get(COOKIE_NAME)
    if auth_db is not None and token is not None:
        auth_db.delete_session(token)

    response = JSONResponse(content=LogoutResponse(status="logged out").model_dump())
    response.delete_cookie(key=COOKIE_NAME)
    return response


@router.get("/api/auth/me", response_model=MeResponse, dependencies=[Depends(require_user)])
async def me(request: Request) -> dict[str, str]:
    """Return the current user's email.

    ``require_user`` is declared on this route alone — the router is mounted
    without a router-level dependency, since that would apply to login too.

    **This endpoint answers the same way whether ``auth_enabled`` is True or
    False**: 200 with the email for a valid cookie, 401 for an anonymous
    caller. Measured 2026-08-18, both flag states. That is deliberate — "who
    am I" is a question about the cookie, not about the gate — but it has a
    consequence P1.4 must handle: a dev running with auth disabled still gets
    401 here, so the frontend must not read a 401 from ``me`` as "redirect to
    the login page" without checking whether auth is on at all.

    The cookie lookup is repeated here rather than injected, because
    ``require_user`` sits in ``dependencies=`` and its return value therefore
    is not passed in. Switching to the injected form
    (``user: User | None = Depends(require_user)``) would remove the second
    lookup, but it would also make this endpoint 401 with a VALID cookie
    whenever the flag is off — ``require_user`` short-circuits to ``None``
    there — which is a semantic change, not a refactor. The duplicate read is
    the cheaper of the two costs; both paths call ``get_session_user``, so
    they cannot disagree about what a valid session is.
    """
    token = request.cookies.get(COOKIE_NAME)
    auth_db = getattr(request.app.state, "auth_db", None)
    user: User | None = None
    if auth_db is not None and token is not None:
        user = get_session_user(auth_db, token)

    if user is None:
        raise HTTPException(status_code=401)

    return {"email": user.email}
