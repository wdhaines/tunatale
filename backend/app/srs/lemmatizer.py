"""Lemmatizer protocol and default implementation."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Lemmatizer(Protocol):
    """Reduces a word to its canonical base form."""

    def lemmatize(self, word: str, language_code: str) -> str: ...

    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
        """Return ``(lemma, case, number)`` for an inflected word.

        Returns empty strings for case/number when the language or token
        is not inflected (the default implementation returns
        ``(word.lower(), "", "")``).
        """


class LowercaseLemmatizer:
    """Simple lemmatizer that lowercases the word.

    Language-agnostic default. Replace with a language-specific lemmatizer
    (e.g. classla for Slovene) for proper conjugation/declension collapsing.
    """

    def lemmatize(self, word: str, language_code: str) -> str:
        return word.lower()

    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
        return word.lower(), "", ""


# ── Slovene morphological analyzer (classla, opt-in) ──────────────────────────


class ClasslaLemmatizer:  # pragma: no cover — requires classla/PyTorch; opt-in only
    """Slovene lemmatizer backed by CLASSLA-Stanza.

    Requires ``classla`` (PyTorch-based pipeline for South Slavic languages).
    Not imported in CI — instantiate only when the user explicitly opts in
    via config (lemmatizer_type="classla") and has the model downloaded.

    Usage::

        lem = ClasslaLemmatizer()
        lem.analyze("mize", "sl")  # → ("miza", "Gen", "Sing")
        lem.lemmatize("mize", "sl")  # → "miza"
    """

    def __init__(self, language_code: str = "sl") -> None:
        self._language_code = language_code
        self._nlp: ClasslaPipeline | None = None

    def _ensure_pipeline(self) -> ClasslaPipeline:
        if self._nlp is None:
            import classla

            classla.download(self._language_code)
            self._nlp = classla.Pipeline(
                self._language_code,
                processors="tokenize,pos,lemma",
            )
        return self._nlp

    def lemmatize(self, word: str, language_code: str) -> str:
        if language_code != self._language_code:
            return word.lower()
        nlp = self._ensure_pipeline()
        doc = nlp(word)
        try:
            return doc.sentences[0].words[0].lemma or word.lower()
        except (IndexError, AttributeError):
            return word.lower()

    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
        if language_code != self._language_code:
            return word.lower(), "", ""
        nlp = self._ensure_pipeline()
        doc = nlp(word)
        try:
            token = doc.sentences[0].words[0]
            lemma = token.lemma or word.lower()
            feats = token.feats or ""
            case, number = _parse_morphology(feats)
            return lemma, case, number
        except (IndexError, AttributeError):
            return word.lower(), "", ""


def _parse_morphology(feats: str) -> tuple[str, str]:
    """Extract ``(Case, Number)`` from a UD FEATS string like ``Case=Gen|Gender=Fem|Number=Sing``.

    Returns ``("", "")`` when a feature is absent.
    """
    case = ""
    number = ""
    for part in feats.split("|"):
        part = part.strip()
        if part.startswith("Case="):
            case = part.removeprefix("Case=")
        elif part.startswith("Number="):
            number = part.removeprefix("Number=")
    return case, number


# Avoid importing classla at module level (CI guard).
# The type alias lets us reference the type without a top-level import.
try:
    from classla import Pipeline as ClasslaPipeline
except ImportError:
    ClasslaPipeline = None  # type: ignore[misc,assignment]
