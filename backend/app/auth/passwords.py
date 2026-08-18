"""Password hashing via argon2-cffi (argon2id, library defaults)."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Return an argon2id hash of *password*."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Return True if *password* matches *password_hash*.

    Never raises — returns False for a wrong password and for a malformed
    stored hash.
    """
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False


def needs_rehash(password_hash: str) -> bool:
    """Return True if *password_hash* was made with weaker parameters."""
    return _hasher.check_needs_rehash(password_hash)
