"""Session-token minting and SHA-256 digest.

sha256 here, argon2 next door, and the asymmetry is deliberate.  A session
token is 256 bits from ``secrets.token_urlsafe``, so there is no guessable
space for a slow KDF to defend; argon2 would add its deliberate ~50 ms to
**every authenticated request** and buy nothing.  The property the store needs
is preimage resistance — a read of ``auth.db`` must not yield a usable
cookie — and sha256 has it.  Password hashes are the opposite case:
human-chosen, low entropy, verified rarely.
"""

from __future__ import annotations

import hashlib
import secrets

TOKEN_BYTES = 32


def mint_token() -> str:
    """Return a cryptographically random URL-safe token (256 bits)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of *token*."""
    return hashlib.sha256(token.encode()).hexdigest()
