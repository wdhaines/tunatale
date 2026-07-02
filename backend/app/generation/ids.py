"""Content-id minting shared by generation endpoints and lesson authoring."""

from __future__ import annotations

import re
import uuid


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:50]


def mint_id(text: str) -> str:
    """Mint a content id: ``{slug}-{uuid4hex8}`` (curricula and lessons)."""
    return f"{slugify(text)}-{uuid.uuid4().hex[:8]}"
