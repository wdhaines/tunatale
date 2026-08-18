"""Login throttling — dual-scope rate limiting with exponential backoff.

Two independent scopes protect the login endpoint:

- **Per-account**: N failed attempts against one email address lock that
  account, regardless of the source IP.  This stops a targeted brute-force.
- **Per-IP**: N failed attempts from one address lock it, regardless of which
  accounts were targeted.  This stops a distributed sweep (user-enumeration).

A *successful* login clears only the account counter.  It must not clear the
IP counter, because an attacker who owns one valid account could otherwise
use that account's successes to reset the per-IP budget while guessing every
other password from the same address.

``client_ip`` reads the **rightmost** ``X-Forwarded-For`` entry when a
trusted proxy header is configured.  Caddy *appends* the peer it actually
saw to any inbound ``X-Forwarded-For``, so a client that sends its own
header produces ``<whatever they claimed>, <their real address>``.  Reading
the leftmost entry — the obvious choice — would let any caller mint a fresh
throttle bucket per request and never be limited at all, while charging the
failures to an address they named.  Rightmost is also correct under the
stricter ``header_up X-Forwarded-For {remote_host}`` (replace)
configuration, so it does not depend on which of the two Caddy is set to.

The accepted trade-off: because the account lock follows the account across
addresses (required — see the tests), anyone who can reach the login endpoint
can lock a *known* account out for up to ``MAX_LOCKOUT`` by failing on
purpose.  That is the standard cost of account lockout, it is time-bounded,
and it is preferred here over leaving distributed guessing unthrottled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request

from app.auth.database import SCOPE_ACCOUNT, SCOPE_IP, AuthDatabase

WINDOW = timedelta(hours=1)
ACCOUNT_THRESHOLD = 5
IP_THRESHOLD = 20
BASE_LOCKOUT = timedelta(seconds=60)
MAX_LOCKOUT = timedelta(minutes=30)
UNKNOWN_IP = "unknown"


@dataclass(frozen=True)
class Lockout:
    retry_after: int  # whole seconds, always >= 1


def _lockout_for(failures: int, threshold: int) -> timedelta:
    """Compute the lockout duration for *failures* against *threshold*.

    Below the threshold the account/IP is not yet locked and the duration is
    zero.  Above it, each additional failure doubles the wait starting from
    ``BASE_LOCKOUT``, capped at ``MAX_LOCKOUT``.

    The inner ``min`` on the exponent keeps the intermediate integer small —
    without it, a caller with thousands of failures would compute 2**1000.
    """
    if failures < threshold:
        return timedelta(0)
    exponent = min(failures - threshold, 16)
    return min(BASE_LOCKOUT * 2**exponent, MAX_LOCKOUT)


def check(
    auth_db: AuthDatabase,
    *,
    ip: str,
    email: str,
    now: datetime | None = None,
) -> Lockout | None:
    """Return a ``Lockout`` if the caller is currently locked, else ``None``.

    Both scopes are checked and the **longest** lock wins — a ``Retry-After``
    shorter than the other scope's lock would send the caller back to a
    refusal.
    """
    now = now or datetime.now(UTC)
    state = auth_db.failed_login_state(ip=ip, email=email, since=now - WINDOW)

    max_wait = 0
    for scope, threshold in ((SCOPE_IP, IP_THRESHOLD), (SCOPE_ACCOUNT, ACCOUNT_THRESHOLD)):
        count, last = state[scope]
        if last is None:
            continue
        unlock = last + _lockout_for(count, threshold)
        if unlock > now:
            remaining = math.ceil((unlock - now).total_seconds())
            if remaining > max_wait:
                max_wait = remaining

    return Lockout(max_wait) if max_wait > 0 else None


def record_failure(
    auth_db: AuthDatabase,
    *,
    ip: str,
    email: str,
    now: datetime | None = None,
) -> None:
    """Record a failed login attempt against both scopes."""
    auth_db.record_failed_login(ip=ip, email=email, now=now)


def clear_account(auth_db: AuthDatabase, *, email: str) -> None:
    """Clear the account scope after a successful login.

    Must not touch IP-scope rows — see module docstring.
    """
    auth_db.clear_failed_logins_for_account(email)


def client_ip(request: Request) -> str:
    """Extract the client IP, honouring the trusted proxy header.

    The attribute is read **per call**, matching ``require_user`` — tests
    monkeypatch ``settings.trusted_proxy_header`` after import, and a value
    captured at import time would ignore them.  (The import itself is local
    only to match ``AuthDatabase.create_session``'s house style; it is the
    attribute read, not the import, that makes the patch take.)
    """
    from app.config import settings

    header = settings.trusted_proxy_header.strip()
    if header:
        raw = request.headers.get(header, "")
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if parts:
            return parts[-1]

    if request.client is not None:
        return request.client.host

    return UNKNOWN_IP
