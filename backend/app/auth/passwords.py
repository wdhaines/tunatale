"""Password hashing via argon2-cffi (argon2id).

The cost comes from ``settings.password_hash_cost``, read on every call:
``default`` is argon2-cffi's own parameters (every deployment), ``minimal`` is
the test suite's (conftest pins it). A hash made at another cost verifies, and
``needs_rehash`` reports it, so a login upgrades it.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import settings

#: (time_cost, memory_cost KiB, parallelism): argon2's floor, for the test suite only.
MINIMAL_PARAMETERS = (1, 8, 1)

_HASHERS = {
    "default": PasswordHasher(),
    "minimal": PasswordHasher(*MINIMAL_PARAMETERS),
}


def _hasher() -> PasswordHasher:
    return _HASHERS[settings.password_hash_cost]


def hash_password(password: str) -> str:
    """Return an argon2id hash of *password*."""
    return _hasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Return True if *password* matches *password_hash*.

    Never raises — returns False for a wrong password and for a malformed
    stored hash.
    """
    try:
        return _hasher().verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False


def needs_rehash(password_hash: str) -> bool:
    """Return True if *password_hash* was made with other parameters than the current cost's."""
    return _hasher().check_needs_rehash(password_hash)
