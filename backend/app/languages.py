"""Language configuration registry.

A ``LanguageConfig`` wraps a ``Language`` domain model plus phase-specific
wiring (preprocessor factory, deck name, notetype profile). The registry is
the single source of truth for "which languages are wired" — adding a language
means adding one entry to ``_CONFIGS``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.anki.vocab_notetype import NORWEGIAN_VOCAB, SLOVENE_VOCAB, VocabNotetype
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
    * Phase 3: ``vocab_notetype`` — the TT-managed notetype new cards are minted
      into (recognition + production). ``None`` for languages TT doesn't mint
      into (``en``).

    ``en`` (English) is the gloss/translation language — it has a ``Language``
    entry but **no** preprocessor and **no** TT-managed Anki deck of its own
    (both fields are ``None``). ``get_preprocessor("en")`` / ``get_deck_name("en")``
    raise ``ValueError``.
    """

    language: Language
    preprocessor_factory: type[TextPreprocessor] | None = None
    deck_name: str | None = None
    vocab_notetype: VocabNotetype | None = None


_CONFIGS: dict[str, LanguageConfig] = {
    "sl": LanguageConfig(
        language=Language.slovene(),
        preprocessor_factory=SlovenePreprocessor,
        deck_name="1. Slovene",
        vocab_notetype=SLOVENE_VOCAB,
    ),
    "en": LanguageConfig(
        language=Language.english(),
        preprocessor_factory=None,
        deck_name=None,
        vocab_notetype=None,
    ),
    "no": LanguageConfig(
        language=Language.norwegian(),
        preprocessor_factory=NorwegianPreprocessor,
        deck_name="0. 6000 Most Frequent Norwegian Words [Part 1]",
        vocab_notetype=NORWEGIAN_VOCAB,
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


def get_deck_name(code: str) -> str:
    """Return the TT-managed Anki deck name for *code*.

    Raises ``KeyError`` for unknown codes and ``ValueError`` for codes that have
    no TT-managed deck (e.g. ``en``).
    """
    if code not in _CONFIGS:
        raise KeyError(f"Unknown language code: {code!r}. Valid: {sorted(_CONFIGS)}")
    deck_name = _CONFIGS[code].deck_name
    if deck_name is None:
        raise ValueError(f"Language {code!r} has no TT-managed deck configured")
    return deck_name


def get_tts_voice(code: str, role: str = "female-1") -> str:
    """Return the EdgeTTS voice for *code*'s *role* (default the primary female voice).

    The single place card-media / cloze audio resolves which voice to synthesize
    in, so a non-Slovene card never gets Slovene TTS. Raises ``KeyError`` for an
    unknown code and ``ValueError`` when the language defines no voice for *role*.
    """
    voice = get_language(code).tts_voice_map.get(role)
    if not voice:
        raise ValueError(f"Language {code!r} has no {role!r} TTS voice configured")
    return voice


def get_vocab_notetype(code: str) -> VocabNotetype | None:
    """Return the TT-managed vocab notetype TT mints *code*'s cards into.

    ``None`` for an unknown code or a language TT doesn't mint into (``en``) —
    callers fall back to the deck-discovered notetype.
    """
    config = _CONFIGS.get(code)
    return config.vocab_notetype if config else None
