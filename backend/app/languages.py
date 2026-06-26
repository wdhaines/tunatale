"""Language configuration registry.

A ``LanguageConfig`` wraps a ``Language`` domain model plus phase-specific
wiring (preprocessor factory, deck name, notetype profile). The registry is
the single source of truth for "which languages are wired" — adding a language
means adding one entry to ``_CONFIGS``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.audio.preprocessing.base import TextPreprocessor
from app.audio.preprocessing.norwegian import NorwegianPreprocessor
from app.audio.preprocessing.slovene import SlovenePreprocessor
from app.models.language import Language


@dataclass
class LanguageConfig:
    """Per-language wiring.

    Fields are added in the phase that first needs them:

    * Phase 0: ``language``, ``preprocessor_factory``
    * Phase 1: ``deck_name``
    * Phase 3: ``notetype_profile``, …

    ``en`` (English) is the gloss/translation language — it has a ``Language``
    entry but **no** preprocessor (``preprocessor_factory`` is ``None``).
    Calling ``get_preprocessor("en")`` raises ``ValueError``.
    """

    language: Language
    preprocessor_factory: type[TextPreprocessor] | None = None


_CONFIGS: dict[str, LanguageConfig] = {
    "sl": LanguageConfig(
        language=Language.slovene(),
        preprocessor_factory=SlovenePreprocessor,
    ),
    "en": LanguageConfig(
        language=Language.english(),
        preprocessor_factory=None,
    ),
    "no": LanguageConfig(
        language=Language.norwegian(),
        preprocessor_factory=NorwegianPreprocessor,
    ),
}


def get_language(code: str) -> Language:
    """Return the ``Language`` domain object for *code*.

    Raises ``KeyError`` when *code* is not a known language.
    """
    if code not in _CONFIGS:
        raise KeyError(f"Unknown language code: {code!r}. Valid: {sorted(_CONFIGS)}")
    return _CONFIGS[code].language


def get_preprocessor(code: str) -> TextPreprocessor:
    """Return a ``TextPreprocessor`` instance for *code*.

    Raises ``KeyError`` for unknown codes and ``ValueError`` for codes that
    have no preprocessor configured (e.g. ``en``).
    """
    if code not in _CONFIGS:
        raise KeyError(f"Unknown language code: {code!r}. Valid: {sorted(_CONFIGS)}")
    factory = _CONFIGS[code].preprocessor_factory
    if factory is None:
        raise ValueError(f"Language {code!r} has no preprocessor configured")
    return factory()
