"""Language configuration registry.

A ``LanguageConfig`` wraps a ``Language`` domain model plus phase-specific
wiring (preprocessor factory, deck name, notetype profile). The registry is
the single source of truth for "which languages are wired" — adding a language
means adding one entry to ``_CONFIGS``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.anki.vocab_notetype import NORWEGIAN_VOCAB, SLOVENE_VOCAB, VocabNotetype
from app.audio.preprocessing.base import TextPreprocessor
from app.audio.preprocessing.norwegian import NorwegianPreprocessor
from app.audio.preprocessing.slovene import SlovenePreprocessor
from app.models.language import Language

if TYPE_CHECKING:
    from app.config import Settings


@dataclass
class LanguageConfig:
    """Per-language wiring.

    Fields are added in the phase that first needs them:

    * Phase 0: ``language``, ``preprocessor_factory``
    * Phase 1: ``deck_name``
    * Phase 3: ``vocab_notetype`` — the TT-managed notetype new cards are minted
      into (recognition + production). ``None`` for languages TT doesn't mint
      into (``en``).

    ``lemmatizer_type`` names the morphological engine the language's transcripts
    are analyzed with (``classla`` for Slovene, ``stanza`` for Norwegian,
    ``lowercase`` otherwise). It is a **property of the language**, not of the
    process: multi-language mode (``settings.database_urls``) runs both languages
    in one process, so a global ``settings.lemmatizer_type`` singleton would give
    every request the same engine (a Norwegian transcript analyzed by the Slovene
    model). ``settings.lemmatizer_type == "lowercase"`` is a global off-switch;
    see ``app.srs.lemmatizer.get_lemmatizer``.

    ``en`` (English) is the gloss/translation language — it has a ``Language``
    entry but **no** preprocessor and **no** TT-managed Anki deck of its own
    (both fields are ``None``). ``get_preprocessor("en")`` / ``get_deck_name("en")``
    raise ``ValueError``.
    """

    language: Language
    preprocessor_factory: type[TextPreprocessor] | None = None
    deck_name: str | None = None
    vocab_notetype: VocabNotetype | None = None
    lemmatizer_type: str = "lowercase"
    # ``True`` when the Pimsleur word breakdown uses compound/morpheme-aware
    # segmentation (Norwegian) instead of the generic per-syllable backward buildup.
    compound_word_breakdown: bool = False
    # Morphology-drill profile injected into the story prompt (``"slavic"`` = the
    # case/dual tagging block); ``None`` omits the block.
    morphology_profile: str | None = None


_CONFIGS: dict[str, LanguageConfig] = {
    "sl": LanguageConfig(
        language=Language.slovene(),
        preprocessor_factory=SlovenePreprocessor,
        deck_name="1. Slovene",
        vocab_notetype=SLOVENE_VOCAB,
        lemmatizer_type="classla",
        morphology_profile="slavic",
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
        lemmatizer_type="stanza",
        compound_word_breakdown=True,
    ),
}


def get_language(code: str) -> Language:
    """Return the ``Language`` domain object for *code*.

    Raises ``KeyError`` when *code* is not a known language.
    """
    if code not in _CONFIGS:
        raise KeyError(f"Unknown language code: {code!r}. Valid: {sorted(_CONFIGS)}")
    return _CONFIGS[code].language


def known_language_codes() -> frozenset[str]:
    """The set of language codes the registry knows (the keys of ``_CONFIGS``).

    The single source for "is this a valid language?" request-validation checks —
    adding a language to ``_CONFIGS`` widens it automatically, so no caller
    hardcodes ``{"sl", "en", "no"}``.
    """
    return frozenset(_CONFIGS)


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


def get_lemmatizer_type(code: str) -> str:
    """Return the morphological-engine name for *code* (``classla`` / ``stanza`` /
    ``lowercase``).

    Unknown codes and languages with no dedicated engine default to ``lowercase``.
    This picks *which* engine a language wants; whether it is actually built (vs.
    forced to lowercase) is the global ``settings.lemmatizer_type`` gate in
    ``app.srs.lemmatizer.get_lemmatizer``.
    """
    config = _CONFIGS.get(code)
    return config.lemmatizer_type if config else "lowercase"


def get_vocab_notetype(code: str) -> VocabNotetype | None:
    """Return the TT-managed vocab notetype TT mints *code*'s cards into.

    ``None`` for an unknown code or a language TT doesn't mint into (``en``) —
    callers fall back to the deck-discovered notetype.
    """
    config = _CONFIGS.get(code)
    return config.vocab_notetype if config else None


def uses_compound_word_breakdown(code: str) -> bool:
    """Whether *code*'s Pimsleur word breakdown uses compound/morpheme-aware
    segmentation (Norwegian) rather than the generic per-syllable backward buildup.

    Unknown codes → ``False`` (the generic path). Replaces the hardcoded
    ``if language_code == "no"`` branches in ``generation/section_builder.py``.
    """
    config = _CONFIGS.get(code)
    return config.compound_word_breakdown if config else False


def get_morphology_profile(code: str) -> str | None:
    """The morphology-drill profile for *code* (e.g. ``"slavic"`` for the case/dual
    tagging block injected into the story prompt), or ``None`` when the language gets
    no morphology block. Unknown codes → ``None``.
    """
    config = _CONFIGS.get(code)
    return config.morphology_profile if config else None


@dataclass(frozen=True)
class LanguageContext:
    """Resolved per-language wiring for a single sync/render operation.

    Bundles the runtime, mode-dependent facets (``db_url`` / ``deck_name`` /
    ``target_language`` — which differ between single-language mode and
    ``settings.database_urls`` multi-language mode) with the static registry facets
    (``language``, ``preprocessor_factory``, ``lemmatizer_type``, ``vocab_notetype``).
    One object threads a language's identity end-to-end so a caller no longer
    re-derives each facet with a separate ad-hoc lookup (the pattern the old
    ``_tt_settings`` embodied — architectural weakness #4).

    ``db_url`` is the RAW registry/settings value; a caller needing a
    CWD-independent path (the sync adapter) absolutizes it itself — keeping this
    module free of filesystem-anchoring concerns.
    """

    code: str | None
    db_url: str
    deck_name: str | None
    target_language: str
    language: Language | None = None
    preprocessor_factory: type[TextPreprocessor] | None = None
    lemmatizer_type: str = "lowercase"
    vocab_notetype: VocabNotetype | None = None


def resolve_language_context(code: str | None, settings: Settings) -> LanguageContext:
    """Resolve the full per-language wiring for *code* against *settings*.

    Mirrors the sync path's rule exactly (the former ``_tt_settings`` body): when
    *code* names a configured multi-language (a truthy ``settings.database_urls``
    entry), use that db, the registry deck, and ``target_language = code``.
    Otherwise — ``None`` (the CLI path), an unconfigured code, or single-language
    mode — fall back to the singular ``settings`` defaults unchanged. Static
    registry facets are attached whenever *code* is a known language, else
    ``None`` / the ``lowercase`` default.
    """
    config = _CONFIGS.get(code) if code else None
    configured_db = settings.database_urls.get(code) if code else None
    if configured_db:
        db_url, deck_name, target_language = configured_db, get_deck_name(code), code
    else:
        db_url = settings.database_url
        deck_name = settings.anki_deck_name
        target_language = settings.target_language
    return LanguageContext(
        code=code,
        db_url=db_url,
        deck_name=deck_name,
        target_language=target_language,
        language=config.language if config else None,
        preprocessor_factory=config.preprocessor_factory if config else None,
        lemmatizer_type=config.lemmatizer_type if config else "lowercase",
        vocab_notetype=config.vocab_notetype if config else None,
    )
