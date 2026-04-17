"""Deterministic GUID computation shared by SRS and Anki sync.

The GUID is a 16-character hex prefix of sha1(language_code + NFC(casefold(text))).
NFC is applied *after* casefold so characters that casefold to multi-char forms
(e.g. German ß → ss) stay stable across inputs.
"""

from __future__ import annotations

import hashlib
import unicodedata


def compute_guid(text: str, language_code: str) -> str:
    """Return a 16-char hex GUID for (text, language_code)."""
    normalized = unicodedata.normalize("NFC", text.casefold())
    payload = f"{language_code}{normalized}".encode()
    return hashlib.sha1(payload).hexdigest()[:16]
