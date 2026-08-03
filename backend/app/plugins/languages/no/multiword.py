"""Fixed multiword expressions whose second word must not be carded alone.

A fact about Norwegian, so it lives in the plugin; core reaches it through
``app.languages.get_multiword_traps``.

Scope is deliberately two-word pairs. The failure being prevented is a
lemmatizer reading the second token of a fixed expression as a standalone word
("i går" → NOUN `går`, carded as the verb `gå`), and that shows up on adjacent
pairs. Longer idioms would need span matching over the whole sentence; nothing
has demanded it yet.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

_DATA = Path(__file__).parent / "data" / "multiword_traps.txt"


def parse_pairs(text: str) -> frozenset[tuple[str, str]]:
    """Parse the trap file body. Comments, blanks and non-pairs are ignored.

    Split out from ``trapped_pairs`` so the malformed-line handling is testable
    without shipping a malformed line in the data file.
    """
    pairs = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.casefold().split()
        if len(parts) == 2:
            pairs.add((parts[0], parts[1]))
    return frozenset(pairs)


@cache
def trapped_pairs() -> frozenset[tuple[str, str]]:
    """Return the ``(first_word, second_word)`` pairs, lowercased."""
    return parse_pairs(_DATA.read_text(encoding="utf-8"))


@cache
def trap_second_words() -> frozenset[str]:
    """The set of words that can be suppressed — a cheap pre-filter."""
    return frozenset(second for _, second in trapped_pairs())
