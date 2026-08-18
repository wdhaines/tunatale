"""Pure domain models for auth — no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class User:
    """A registered user.

    ``password_hash`` exists because the store round-trips it, and it must
    **never** appear in an API ``response_model`` — P1.3 builds a separate
    public shape.
    """

    id: int
    email: str
    password_hash: str
    created_at: datetime
    is_active: bool


@dataclass(frozen=True, slots=True)
class Session:
    """A login session.

    ``token_hash`` is the SHA-256 of the bearer token; the plaintext is
    returned to the caller once and never stored.
    """

    token_hash: str
    user_id: int
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
