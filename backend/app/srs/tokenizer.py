"""Word tokenizer for SRS transcript processing."""

from __future__ import annotations

import re

_PUNCT = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Split text on whitespace and strip leading/trailing punctuation from each token.

    Interior punctuation (e.g. hyphens in compound words) is preserved.
    Returns only non-empty tokens.
    """
    return [t for raw in text.split() if (t := _PUNCT.sub("", raw))]
