"""Deterministic GUID computation shared by SRS and Anki sync.

The GUID is a 16-character hex prefix of
    sha1(language_code + NFC(casefold(text)) + "\x1f" + NFC(casefold(disambig)))

NFC is applied *after* casefold.  The "\x1f" separator ensures
compute_guid("ab", lang, "c") != compute_guid("abc", lang, "").
Passing no disambig (or "") is still a different hash from the
pre-H2 formula, which is intentional: H3 migration rewrites all
stored guids in one pass.
"""

from __future__ import annotations

import hashlib
import unicodedata


def compute_guid(text: str, language_code: str, disambig: str = "") -> str:
    """Return a 16-char hex GUID for (text, language_code, disambig).

    disambig separates homonyms that share the same L2 text (e.g. "barva"
    meaning "color" vs "paint").  An empty disambig is still part of the hash.
    """
    normalized = unicodedata.normalize("NFC", text.casefold())
    norm_disambig = unicodedata.normalize("NFC", disambig.casefold())
    payload = f"{language_code}{normalized}\x1f{norm_disambig}".encode()
    return hashlib.sha1(payload).hexdigest()[:16]
