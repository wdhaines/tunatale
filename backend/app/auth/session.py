"""Session cookie constants and lookup helper.

P1.2 only *reads* the cookie; P1.3 will set it.  The constants here carry
the attributes P1.3 needs so the cookie shape is defined once.
"""

from __future__ import annotations

from app.auth.database import AuthDatabase
from app.auth.models import User

COOKIE_NAME = "tt_session"
COOKIE_HTTPONLY = True
COOKIE_SECURE = True
COOKIE_SAMESITE = "lax"


def get_session_user(db: AuthDatabase | None, token: str) -> User | None:
    """Look up the user owning *token*, or ``None``.

    Returns ``None`` when:
    - *db* is ``None`` (auth DB not available — fail closed),
    - the token matches no session row,
    - the session is expired, or
    - the owning user is missing or deactivated.

    Does **not** call ``touch_session`` — see the brief's design decision #4.
    """
    if db is None:
        return None
    session = db.get_session(token)
    if session is None:
        return None
    user = db.get_user_by_id(session.user_id)
    if user is None or not user.is_active:
        return None
    return user
