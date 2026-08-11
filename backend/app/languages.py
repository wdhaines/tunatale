"""Language configuration registry.

A ``LanguageConfig`` wraps a ``Language`` domain model plus phase-specific
wiring (preprocessor factory, deck name, notetype profile). The registry is
populated by language plugin packages under ``app.plugins.languages`` — each
plugin imports its concrete wiring and calls :func:`register` at import time.

English (``en``) is registered directly in :func:`discover` (no plugin package).
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import app.plugins.languages as _plugins_pkg
from app.audio.preprocessing.base import TextPreprocessor
from app.models.language import Language

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.audio.alignment import CharAligner
    from app.cards.vocab_notetype import VocabNotetype
    from app.config import Settings
    from app.models.breakdown import BreakdownChunk


@dataclass(frozen=True)
class AlignmentConfig:
    """Everything core needs to cut syllables out of a whole-word render.

    The model id is a language literal, and the model itself is an optional
    dependency, so both stay behind the plugin: core receives an id plus a
    factory it calls at most once per process, never an import.

    ``vowels`` is the language's vowel inventory, used to find where a syllable's
    vowel begins (the only trustworthy ceiling a peaky CTC alignment offers).
    ``syllabify_fn`` must be the SAME function whose output ``BreakdownChunk.span``
    indexes — it returns ``None`` for a word whose pieces do not rejoin its
    surface form, which means "do not slice this".
    """

    model_id: str
    vowels: frozenset[str]
    aligner_factory: Callable[[str], CharAligner]
    syllabify_fn: Callable[[str], list[str] | None]


@dataclass
class PlannerExample:
    """One worked planner day, with its ``collocations`` in *language_code*.

    The planner system prompt shows exactly one of these. It is deliberately in a
    language **different from the curriculum's target**: the example teaches the
    *shape* of correct output, and an example in the target language gets copied
    into the model's reply (the planner-language-contamination class). The
    non-target property used to be implicit in a hardcoded Norwegian literal,
    which no test asserted and which was simply wrong when the target was
    Norwegian.

    Every field except ``collocations`` is English, matching the JSON contract the
    prompt states just above the example.
    """

    language_code: str
    day: int
    title: str
    focus: str
    collocations: tuple[str, ...]
    learning_objective: str
    story_guidance: str


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
    # Onset-maximization syllabifier function for this language.
    # ``None`` for languages with no syllabifier wiring (``en``); callers
    # fall back to the generic core default.
    syllabifier_fn: Callable[[str], list[str]] | None = None
    # Morphology-drill profile injected into the story prompt (``"slavic"`` = the
    # case/dual tagging block); ``None`` omits the block.
    morphology_profile: str | None = None
    # Slowed-word function for the slow-speed section (Norwegian morpheme pauses).
    # ``None`` when the language has no slow-word specialisation.
    slow_word_fn: Callable[[str], str] | None = None
    # Predicate: does this L2 surface carry a definite-article suffix? Used to stop
    # a generated card's gloss contradicting its headword (`morder` glossed "the
    # murderer"). ``None`` for languages that do not mark definiteness by suffix —
    # the gloss aligner is then a no-op, never a guess.
    definite_form_fn: Callable[[str], bool] | None = None
    # Predicate: is the lemmatizer's lemma a plausible headword for the surface it
    # was produced from? Used to stop a truncated lemma fragment (`trøtt` → `trø`)
    # from becoming a card front that teaches a non-word. ``None`` means "cannot
    # tell" — callers keep the lemma as-is, never a guess.
    lemma_plausible_fn: Callable[[str, str], bool] | None = None
    # Fixed two-word expressions whose SECOND word must not be carded standalone
    # ("i går" = yesterday, not the verb `gå`). Empty for languages with no list.
    multiword_traps_fn: Callable[[], frozenset[tuple[str, str]]] | None = None
    # Character that separates alternate accepted spellings of ONE word on a card
    # front (Norwegian's ``mot, imot`` — both spellings of "against/towards").
    # ``None`` (the default) means the language has no such convention, so a card
    # front is always a single surface form. See ``card_surface_variants``.
    variant_separator: str | None = None
    # Infinitive-marker word prepended to a VERB headword when minting a TT vocab
    # card ("å" + lemma → "å lyve", matching the convention already used across
    # the user's Anki collection). None (the default) means the language has no
    # such marker and a verb's headword is just its bare lemma.
    infinitive_marker: str | None = None
    # Gender → indefinite-article map for NOUN headwords when minting a TT vocab
    # card ("Masc" → "en morder", like the imported deck fronts). None (the
    # default) means the language has no articles and a noun's headword is its
    # bare lemma. Display-only: the article never enters ``text``, which feeds
    # the card GUID.
    gender_articles: dict[str, str] | None = None
    # Per-language authenticity rules injected into the story system prompt.
    # Loaded from the plugin's ``data/style.md`` at import time; empty string
    # when the language has no style file (``en``).
    style_notes: str = ""
    # Path to the per-language function-word JSON config, or ``None`` when the
    # language has no curated function-word policy.
    function_words_path: Path | None = None
    # Breakdown variant that also reports which syllables of which word each
    # chunk is, so the renderer can cut it out of one whole-word render instead
    # of synthesizing the fragment alone. ``None`` (every language but Norwegian)
    # means the chunks carry no provenance and every one falls back to TTS.
    breakdown_spans_fn: Callable[[str], list[BreakdownChunk]] | None = None
    # Forced-alignment wiring for that slicing. ``None`` when the language has no
    # aligner, which is also a complete off-switch for the feature.
    alignment: AlignmentConfig | None = None
    # A worked planner example day written IN THIS language, shown to the planner
    # when some OTHER language is the target. See ``get_planner_example``.
    # ``None`` for languages that supply no example (``en``).
    planner_example: PlannerExample | None = None
    # wordfreq lookup code for pedagogical ranking; None disables frequency
    # ranking (creation candidates fall back to in-lesson occurrence count).
    wordfreq_lang: str | None = None


_CONFIGS: dict[str, LanguageConfig] = {}


def register(code: str, config: LanguageConfig) -> None:
    """Register a language plugin.  Raises ``ValueError`` on duplicate *code*."""
    if code in _CONFIGS:
        raise ValueError(f"Language {code!r} is already registered")
    _CONFIGS[code] = config


_discovered = False


def discover() -> None:
    """Import every subpackage of ``app.plugins.languages`` so they self-register.

    English (``en``) is registered first as a core language — no plugin package
    needed.  Idempotent — guarded by the module-level ``_discovered`` flag.
    Raises ``RuntimeError`` when no language plugin (other than ``en``) is present.

    This is called lazily by every public accessor that reads ``_CONFIGS``, and
    eagerly by ``app.main.lifespan`` so a zero-plugin install hard-fails at
    startup.
    """
    global _discovered  # noqa: PLW0603
    if _discovered:
        return
    _discovered = True

    # English is always available — registered in core, not via a plugin package.
    register("en", LanguageConfig(language=Language.english()))

    for _importer, modname, _ispkg in pkgutil.iter_modules(_plugins_pkg.__path__, prefix=_plugins_pkg.__name__ + "."):
        importlib.import_module(modname)

    non_en = {c for c in _CONFIGS if c != "en"}
    if not non_en:
        raise RuntimeError(
            "No language plugin registered.  Place at least one language plugin "
            "package under app/plugins/languages/ (e.g. sl/ or no/) so that at "
            "least one language besides 'en' is available."
        )


def get_language(code: str) -> Language:
    """Return the ``Language`` domain object for *code*.

    Raises ``KeyError`` when *code* is not a known language.
    """
    discover()
    if code not in _CONFIGS:
        raise KeyError(f"Unknown language code: {code!r}. Valid: {sorted(_CONFIGS)}")
    return _CONFIGS[code].language


def get_planner_example(code: str) -> PlannerExample | None:
    """Return a worked planner example in a language **other than** *code*.

    Picks the lowest-sorting registered language that both differs from *code*
    and supplies an example, so the choice is deterministic and — structurally —
    can never be *code* itself. That is the whole point: the caller cannot
    accidentally show a target-language example, because the target is excluded
    before selection rather than checked afterwards.

    Returns ``None`` when no other language supplies one (a single-language
    install). Callers omit the example block entirely in that case; a
    same-language example would be worse than none.

    Raises ``KeyError`` when *code* is not a known language.
    """
    discover()
    if code not in _CONFIGS:
        raise KeyError(f"Unknown language code: {code!r}. Valid: {sorted(_CONFIGS)}")
    return _select_planner_example(code, _CONFIGS)


def _select_planner_example(code: str, configs: dict[str, LanguageConfig]) -> PlannerExample | None:
    """Pure selector behind :func:`get_planner_example`.

    Split out so the single-language case (no other language supplies an example)
    is testable by passing a one-entry mapping, rather than by monkeypatching the
    module-level registry — a test that mutates ``_CONFIGS`` would leak into every
    later test in the session.
    """
    for other in sorted(configs):
        if other == code:
            continue
        example = configs[other].planner_example
        if example is not None:
            return example
    return None


def known_language_codes() -> frozenset[str]:
    """The set of language codes the registry knows (the keys of ``_CONFIGS``).

    The single source for "is this a valid language?" request-validation checks —
    adding a language to ``_CONFIGS`` widens it automatically, so no caller
    hardcodes ``{"sl", "en", "no"}``.
    """
    discover()
    return frozenset(_CONFIGS)


def get_preprocessor(code: str) -> TextPreprocessor:
    """Return a ``TextPreprocessor`` instance for *code*.

    Raises ``KeyError`` for unknown codes and ``ValueError`` for codes that
    have no preprocessor configured (e.g. ``en``).
    """
    discover()
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
    discover()
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
    discover()
    config = _CONFIGS.get(code)
    return config.lemmatizer_type if config else "lowercase"


def get_syllabifier(code: str) -> Callable[[str], list[str]]:
    """Return the onset-maximization syllabifier function for *code*.

    Unknown codes and languages with no syllabifier wiring fall back to the
    generic core default (English-like vowels, no onset rules).
    """
    discover()
    config = _CONFIGS.get(code)
    fn = config.syllabifier_fn if config else None
    if fn is not None:
        return fn
    from app.generation.syllabify import default_syllabifier

    return default_syllabifier


def get_vocab_notetype(code: str) -> VocabNotetype | None:
    """Return the TT-managed vocab notetype TT mints *code*'s cards into.

    ``None`` for an unknown code or a language TT doesn't mint into (``en``) —
    callers fall back to the deck-discovered notetype.
    """
    discover()
    config = _CONFIGS.get(code)
    return config.vocab_notetype if config else None


def get_l2_css_class(code: str) -> str:
    """Return the CSS class that wraps the L2 word on *code*'s cards.

    This is the marker the Anki field parsers key on to find the target-language
    text (``app/plugins/anki_sync/sqlite_reader.py``). Empty string when the
    language has no TT vocab notetype (``en``, an unknown code) — the parsers
    then fall through to their positional/HTML heuristics, which is the correct
    behaviour when there is no markup to look for.
    """
    notetype = get_vocab_notetype(code)
    return notetype.l2_css_class if notetype is not None else ""


def get_breakdown_spans(code: str) -> Callable[[str], list[BreakdownChunk]] | None:
    """Return *code*'s provenance-carrying breakdown function, or ``None``.

    ``None`` means the language has no language-specific breakdown, so callers
    fall back to the generic per-syllable buildup in
    ``section_builder._generic_breakdown_spans``. Unknown codes → ``None``.
    """
    discover()
    config = _CONFIGS.get(code)
    return config.breakdown_spans_fn if config else None


def get_alignment(code: str) -> AlignmentConfig | None:
    """Return *code*'s forced-alignment wiring, or ``None`` when it has none.

    ``None`` is the language-level off-switch for syllable slicing; the
    process-level gate is ``app.audio.slicer.alignment_installed``.
    """
    discover()
    config = _CONFIGS.get(code)
    return config.alignment if config else None


def get_slow_word(code: str) -> Callable[[str], str] | None:
    """Return the slow-word function for *code*, or ``None``.

    Norwegian uses morpheme-aware micro-pauses; other languages slow by simple
    whitespace splitting.
    """
    discover()
    config = _CONFIGS.get(code)
    return config.slow_word_fn if config else None


def get_definite_form_checker(code: str) -> Callable[[str], bool] | None:
    """Return the language's definite-form predicate, or ``None`` if it has none.

    ``None`` is the honest answer for a language that does not suffix its definite
    article (Slovene) or is not an L2 (``en``); callers must treat it as "cannot
    tell" and leave the gloss untouched rather than guessing.
    """
    discover()
    config = _CONFIGS.get(code)
    return config.definite_form_fn if config else None


def get_lemma_plausible(code: str) -> Callable[[str, str], bool] | None:
    """Return the language's lemma-plausibility predicate, or ``None`` if it has none.

    ``None`` is the honest answer for a language with no registered predicate
    (Slovene, ``en``); callers must treat it as "cannot tell" and keep the
    lemmatizer's lemma as the headword rather than guessing at a fallback.
    """
    discover()
    config = _CONFIGS.get(code)
    return config.lemma_plausible_fn if config else None


def get_multiword_traps(code: str) -> frozenset[tuple[str, str]]:
    """Return the language's fixed two-word traps, or an empty set.

    Empty is the correct default: suppressing a word needs positive evidence
    that the language has such an expression, never a guess.
    """
    discover()
    config = _CONFIGS.get(code)
    if config is None or config.multiword_traps_fn is None:
        return frozenset()
    return config.multiword_traps_fn()


def get_variant_separator(code: str) -> str | None:
    """The character separating alternate spellings on *code*'s card fronts, or
    ``None`` when the language has no multi-spelling convention.

    Unknown codes → ``None``. Norwegian uses ``","`` (``mot, imot``); every other
    wired language returns ``None``, so ``card_surface_variants`` is a no-op there.
    """
    discover()
    config = _CONFIGS.get(code)
    return config.variant_separator if config else None


def get_infinitive_marker(code: str) -> str | None:
    """The infinitive-marker word prepended to a VERB headword for *code*, or
    ``None`` when the language has no such marker.

    Unknown codes → ``None``. Norwegian uses ``"å"``; every other wired
    language returns ``None``.
    """
    discover()
    config = _CONFIGS.get(code)
    return config.infinitive_marker if config else None


def get_gender_article(code: str, gender: str) -> str:
    """The indefinite article for a NOUN headword of *gender* in *code*, or
    ``""`` when the language has no such map.

    Empty is the correct default: an unknown language, an unregistered gender,
    or a blank gender all mean "no article", and never a guess. The caller gates
    on ``upos == "NOUN"`` — verbs keep their ``infinitive_marker`` path.
    """
    discover()
    config = _CONFIGS.get(code)
    if config is None or config.gender_articles is None or not gender:
        return ""
    return config.gender_articles.get(gender, "")


def format_vocab_headword(lemma: str, upos: str | None, code: str) -> str:
    """Format *lemma* as it should appear on a TT-minted vocab card front.

    Prepends the language's infinitive marker (see ``get_infinitive_marker``)
    when *upos* is ``"VERB"`` and the language has one registered; otherwise
    returns *lemma* unchanged.
    """
    marker = get_infinitive_marker(code) if upos == "VERB" else None
    return f"{marker} {lemma}" if marker else lemma


def get_style_notes(code: str) -> str:
    """Return the per-language authenticity rules for the story system prompt.

    Empty string when the language has no style file or is unknown.
    """
    discover()
    config = _CONFIGS.get(code)
    return config.style_notes if config else ""


def get_function_words_path(code: str) -> Path | None:
    """Return the path to the per-language function-word JSON config, or ``None``
    when the language has no curated function-word policy.
    """
    discover()
    config = _CONFIGS.get(code)
    return config.function_words_path if config else None


def get_wordfreq_lang(code: str) -> str | None:
    """Return the wordfreq lookup code for *code*, or ``None`` when the language
    has no wordfreq code (frequency ranking is disabled).
    """
    discover()
    config = _CONFIGS.get(code)
    return config.wordfreq_lang if config else None


def card_surface_variants(code: str, text: str) -> list[str]:
    """Alternate accepted surface forms encoded in a card front *text*.

    A card front listing separator-delimited single-word spellings (Norwegian
    ``mot, imot``) is ONE lexical item with multiple surfaces — not a multi-word
    collocation. Returns each stripped variant when *text* is such a list, else
    ``[text]`` unchanged. The "every part is a single token" guard keeps genuine
    phrases that merely contain the separator (``hei, hvordan går det``) whole,
    and languages without a ``variant_separator`` always return ``[text]``.
    """
    sep = get_variant_separator(code)
    if not sep or sep not in text:
        return [text]
    parts = [p.strip() for p in text.split(sep)]
    parts = [p for p in parts if p]
    if len(parts) > 1 and all(len(p.split()) == 1 for p in parts):
        return parts
    return [text]


def get_morphology_profile(code: str) -> str | None:
    """The morphology-drill profile for *code* (e.g. ``"slavic"`` for the case/dual
    tagging block injected into the story prompt), or ``None`` when the language gets
    no morphology block. Unknown codes → ``None``.
    """
    discover()
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
    discover()
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


def resolve_db_path(code: str | None, settings: Settings) -> Path:
    """Filesystem path of the TT database for *code*.

    The sanctioned way to answer "which db does this language use?". Callers
    that hand-roll it as ``settings.database_url.removeprefix("sqlite:///")``
    get the SINGULAR setting instead — one fixed language regardless of *code* —
    and the failure is silent: a query filtered by ``language_code`` simply
    matches nothing. That is how ``grave_ignored_lemma_cards --language no``
    reported "Nothing to grave" for a month while reading the Slovene db.
    ``scripts/check_singular_database_url.py`` fails the gate on that shape.
    """
    return Path(resolve_language_context(code, settings).db_url.removeprefix("sqlite:///"))
