"""Suppress a word that only appeared inside a fixed multiword expression.

The `går` bug: *"Jeg så spor i snøen i går"* — "i går" is *yesterday*. Stanza
tags the second token as a standalone NOUN with lemma `går`, so TT minted a card
fronted `går` glossed "go / walk", i.e. the unrelated verb `gå`.

The trap list is a fact about the language and lives in its plugin; this module
only reaches it through the registry, per the no-hardcoded-language-logic rule.
A language with no list registered never suppresses anything.
"""

from __future__ import annotations

from app.languages import get_multiword_traps

_STRIP = ".,!?;:\"'«»()"


def is_trapped_occurrence(previous_surface: str, surface: str, language_code: str) -> bool:
    """True when *surface* directly follows *previous_surface* as a fixed pair.

    Only this occurrence is suppressed, not the word everywhere: "Han går hjem"
    still yields the verb. Surfaces are casefolded and stripped of adjacent
    punctuation so sentence-final "i går." matches.
    """
    if not previous_surface or not surface:
        return False
    traps = get_multiword_traps(language_code)
    if not traps:
        return False
    prev = previous_surface.casefold().strip(_STRIP)
    cur = surface.casefold().strip(_STRIP)
    return (prev, cur) in traps
