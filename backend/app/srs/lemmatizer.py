"""Lemmatizer protocol and default implementation."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Lemmatizer(Protocol):
    """Reduces a word to its canonical base form."""

    def lemmatize(self, word: str, language_code: str) -> str: ...


class LowercaseLemmatizer:
    """Simple lemmatizer that lowercases the word.

    Language-agnostic default. Replace with a language-specific lemmatizer
    (e.g. stanza for Slovene) for proper conjugation/declension collapsing.
    """

    def lemmatize(self, word: str, language_code: str) -> str:
        return word.lower()
