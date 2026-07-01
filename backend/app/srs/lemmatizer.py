"""Lemmatizer protocol and default implementation."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from functools import lru_cache
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.srs.database import SRSDatabase

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenAnalysis:
    """Result of analyzing a single token in sentence context."""

    surface: str
    lemma: str
    upos: str = ""
    case: str = ""
    number: str = ""
    person: str = ""
    gender: str = ""


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

    def analyze_sentence(self, sentence: str, language_code: str) -> list[TokenAnalysis]:
        """Analyze every token in *sentence*, returning a list of TokenAnalysis.

        The default implementation splits on whitespace and runs ``analyze()``
        per token. Language-specific subclasses should override with a
        full NLP pipeline for sentence-context-aware analysis.
        """


class LowercaseLemmatizer:
    """Simple lemmatizer that lowercases the word.

    Language-agnostic default. Replace with a language-specific lemmatizer
    (e.g. classla for Slovene) for proper conjugation/declension collapsing.
    """

    _cache_version: str = ""

    def lemmatize(self, word: str, language_code: str) -> str:
        return word.lower()

    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
        return word.lower(), "", ""

    def analyze_sentence(self, sentence: str, language_code: str) -> list[TokenAnalysis]:
        tokens = sentence.split()
        return [
            TokenAnalysis(surface=t, lemma=t.lower(), upos="", case="", number="", person="", gender="") for t in tokens
        ]


# ── Stanza-family morphological analyzers (classla / stanza, opt-in) ──────────

# TT language codes → Stanza model codes. Stanza's codes mostly match TT's, except
# Norwegian: TT stores "no" (the macrolanguage) but Stanza ships separate Bokmål
# ("nb") and Nynorsk ("nn") models. The user's content is Bokmål, so "no" → "nb".
# Codes not listed pass through unchanged (Stanza uses ISO codes for most langs).
_STANZA_LANG_CODES: dict[str, str] = {
    "no": "nb",
    "nb": "nb",
    "nn": "nn",
}


class _StanzaFamilyLemmatizer:  # pragma: no cover — requires PyTorch pipeline; opt-in only
    """Shared sentence-aware lemmatizer logic for the Stanza family.

    CLASSLA is a fork of Stanford Stanza, so both expose the identical document
    API — ``doc.sentences[i].words[j]`` with ``.lemma`` / ``.upos`` / ``.feats`` in
    the Universal Dependencies scheme — and ``_parse_morphology`` / ``_parse_person``
    apply unchanged to both. Subclasses supply only the package name (for
    cache-version keying) and the ``_ensure_pipeline`` builder.

    Not imported in CI — instantiate only when the user explicitly opts in via
    config (``lemmatizer_type="classla"|"stanza"``) with the model downloaded.
    """

    _package_name: str = ""

    def __init__(self, language_code: str) -> None:
        self._language_code = language_code
        self._nlp: object | None = None
        # Resolve the persistent-cache version eagerly (the package version is
        # available without loading the ~15s model). Callers read this via
        # model_version_for() *before* the first analyze; computing it lazily in
        # _ensure_pipeline would leave it "" until the model loaded, defeating the
        # startup warmup and the first post-restart request's cache lookup.
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _pkg_ver

        try:
            self._cache_version: str = _pkg_ver(self._package_name)
        except PackageNotFoundError:
            self._cache_version = f"{self._package_name}-unknown"
        # Lesson text is stable across requests; cache analyses by sentence so the
        # transcript endpoint doesn't re-run the NLP pipeline on every state-change
        # refetch (~3.6s → DB-only once warmed). Keyed by exact text, so edited
        # sentences re-analyze. Bounded by the user's distinct lesson sentences.
        self._sentence_cache: dict[str, list[TokenAnalysis]] = {}

    def _ensure_pipeline(self) -> object:
        raise NotImplementedError  # subclass builds the language-specific pipeline

    def lemmatize(self, word: str, language_code: str) -> str:
        if language_code != self._language_code:
            return word.lower()
        nlp = self._ensure_pipeline()
        doc = nlp(word)
        try:
            return doc.sentences[0].words[0].lemma or word.lower()
        except IndexError, AttributeError:
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
            case, number, _gender = _parse_morphology(feats)
            return lemma, case, number
        except IndexError, AttributeError:
            return word.lower(), "", ""

    def analyze_sentence(self, sentence: str, language_code: str) -> list[TokenAnalysis]:
        if language_code != self._language_code:
            return [
                TokenAnalysis(surface=t, lemma=t.lower(), upos="", case="", number="", person="", gender="")
                for t in sentence.split()
            ]
        cached = self._sentence_cache.get(sentence)
        if cached is not None:
            return cached
        nlp = self._ensure_pipeline()
        doc = nlp(sentence)
        results: list[TokenAnalysis] = []
        for sent in doc.sentences:
            for token in sent.words:
                feats = token.feats or ""
                case, number, gender = _parse_morphology(feats)
                person = _parse_person(feats)
                results.append(
                    TokenAnalysis(
                        surface=token.text,
                        lemma=token.lemma or token.text.lower(),
                        upos=token.upos or "",
                        case=case,
                        number=number,
                        person=person,
                        gender=gender,
                    )
                )
        self._sentence_cache[sentence] = results
        return results


class ClasslaLemmatizer(_StanzaFamilyLemmatizer):  # pragma: no cover — requires classla/PyTorch; opt-in only
    """Slovene lemmatizer backed by CLASSLA-Stanza.

    Requires ``classla`` (PyTorch-based pipeline for South Slavic languages).
    Not imported in CI — instantiate only when the user explicitly opts in
    via config (lemmatizer_type="classla") and has the model downloaded.

    Usage::

        lem = ClasslaLemmatizer()
        lem.analyze("mize", "sl")  # → ("miza", "Gen", "Sing")
        lem.lemmatize("mize", "sl")  # → "miza"
    """

    _package_name = "classla"

    def __init__(self, language_code: str = "sl") -> None:
        super().__init__(language_code)

    def _ensure_pipeline(self) -> object:
        if self._nlp is None:
            import classla

            # Models must already be present under CLASSLA_RESOURCES_DIR (default
            # ~/classla_resources); run `classla.download(self._language_code)`
            # once if missing. Pipeline does not reliably auto-fetch across versions.
            self._nlp = classla.Pipeline(
                self._language_code,
                processors="tokenize,pos,lemma",
            )
        return self._nlp


class StanzaLemmatizer(_StanzaFamilyLemmatizer):  # pragma: no cover — requires stanza/PyTorch; opt-in only
    """Sentence-aware lemmatizer backed by Stanford Stanza.

    Stanza is the upstream project CLASSLA forks; it ships UD models for many
    languages CLASSLA lacks, notably Norwegian Bokmål (``nb``) / Nynorsk (``nn``).
    Used for Norwegian the way ClasslaLemmatizer is used for Slovene — so
    ``tenker``→``tenke``, ``vil``→``ville``, ``kan``→``kunne`` collapse onto one
    lemma card instead of spawning a separate card per inflected surface.

    TT language codes are mapped to Stanza's (``no`` → ``nb``). Not imported in
    CI — instantiate only when the user opts in (lemmatizer_type="stanza") and has
    run ``stanza.download("nb")``.

    Usage::

        lem = StanzaLemmatizer("no")
        lem.lemmatize("tenker", "no")  # → "tenke"
    """

    _package_name = "stanza"

    def __init__(self, language_code: str = "no") -> None:
        super().__init__(language_code)
        self._stanza_code = _STANZA_LANG_CODES.get(language_code, language_code)

    def _ensure_pipeline(self) -> object:
        if self._nlp is None:
            import stanza

            # Models must already be present in Stanza's default cache (macOS:
            # ~/Library/Caches/stanza/<ver>); run `stanza.download(self._stanza_code)`
            # once if missing. download_method=None disables Stanza's per-construct
            # network update check (mirrors classla's pre-downloaded contract);
            # verbose=False silences per-load logging.
            self._nlp = stanza.Pipeline(
                lang=self._stanza_code,
                processors="tokenize,pos,lemma",
                download_method=None,
                verbose=False,
            )
        return self._nlp


def _parse_morphology(feats: str) -> tuple[str, str, str]:
    """Extract ``(Case, Number, Gender)`` from a UD FEATS string.

    Example: ``Case=Gen|Gender=Fem|Number=Sing`` → ``("Gen", "Sing", "Fem")``.
    Returns ``("", "", "")`` when all features are absent.
    """
    case = ""
    number = ""
    gender = ""
    for part in feats.split("|"):
        part = part.strip()
        if part.startswith("Case="):
            case = part.removeprefix("Case=")
        elif part.startswith("Number="):
            number = part.removeprefix("Number=")
        elif part.startswith("Gender="):
            gender = part.removeprefix("Gender=")
    return case, number, gender


def _parse_person(feats: str) -> str:
    """Extract ``Person`` from a UD FEATS string.

    Returns ``""`` when absent.
    """
    for part in feats.split("|"):
        part = part.strip()
        if part.startswith("Person="):
            return part.removeprefix("Person=")
    return ""


# ── Factory ────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_lemmatizer() -> Lemmatizer:
    """Return a cached lemmatizer based on ``settings.lemmatizer_type``.

    * ``"lowercase"`` (default) — ``LowercaseLemmatizer``
    * ``"classla"`` — ``ClasslaLemmatizer`` (Slovene), falling back to
      ``LowercaseLemmatizer`` with a logged warning if classla is not importable.
    * ``"stanza"`` — ``StanzaLemmatizer`` wired to ``settings.target_language``
      (Norwegian and other Stanza-supported languages), same fallback.

    One lemmatizer per process: the app runs a single ``target_language`` per
    process (``.env`` flips ``target_language`` + ``database_url`` together), so the
    Slovene process sets ``classla`` and the Norwegian process sets ``stanza``.
    """
    from app.config import settings

    lemmatizer_type = settings.lemmatizer_type
    if lemmatizer_type == "classla":
        try:
            import classla  # noqa: F401 — check importability at factory time

            return ClasslaLemmatizer()
        except ImportError:
            _logger.warning(
                "classla not installed; falling back to LowercaseLemmatizer. "
                "Install the opt-in extra: `uv sync --all-groups --extra classla` "
                "(pins classla==2.2.1; the torch==2.12.0 override for Python 3.14 is "
                "baked into pyproject.toml). Then set lemmatizer_type=classla. "
                "See docs/walkthrough.md §22.2."
            )
    elif lemmatizer_type == "stanza":
        try:
            import stanza  # noqa: F401 — check importability at factory time

            return StanzaLemmatizer(settings.target_language)
        except ImportError:
            _logger.warning(
                "stanza not installed; falling back to LowercaseLemmatizer. "
                "Install the opt-in extra: `uv sync --all-groups --extra stanza` "
                "(the torch==2.12.0 override for Python 3.14 is shared with classla). "
                'Then download the model — `uv run python -c "import stanza; '
                "stanza.download('nb')\"` — and set lemmatizer_type=stanza."
            )
    return LowercaseLemmatizer()


# ── Persistent analysis cache ─────────────────────────────────────────────


def model_version_for(lemmatizer: Lemmatizer) -> str:
    """Return a version string for keying the sentence-analysis cache.

    Expensive lemmatizers (``ClasslaLemmatizer``) set ``_cache_version`` to the
    package version so a model upgrade invalidates stale rows. Cheap lemmatizers
    return ``""`` and skip the DB round-trip.
    """
    return getattr(lemmatizer, "_cache_version", "")


def _serialize_analyses(analyses: list[TokenAnalysis]) -> str:
    return json.dumps([asdict(a) for a in analyses], ensure_ascii=False)


def _deserialize_analyses(data: str) -> list[TokenAnalysis]:
    return [TokenAnalysis(**{f.name: d.get(f.name, "") for f in fields(TokenAnalysis)}) for d in json.loads(data)]


def analyze_sentence_cached(
    db: SRSDatabase | None,
    lemmatizer: Lemmatizer,
    sentence: str,
    language_code: str,
    model_version: str = "",
) -> list[TokenAnalysis]:
    """Persistent sentence-analysis cache with on-demand compute.

    When *db* and a non-empty *model_version* are provided, looks up
    ``(sentence, language_code, model_version)`` in the DB. On miss, runs
    ``lemmatizer.analyze_sentence`` and persists the result. Skips the DB entirely for
    cheap lemmatizers (empty *model_version*).
    """
    if db is None or not model_version:
        return lemmatizer.analyze_sentence(sentence, language_code)
    cached = db.get_sentence_analysis(sentence, language_code, model_version)
    if cached is not None:
        return _deserialize_analyses(cached)
    analyses = lemmatizer.analyze_sentence(sentence, language_code)
    db.set_sentence_analysis(sentence, language_code, model_version, _serialize_analyses(analyses))
    return analyses


# ── Sentence-context surface lemmatization ────────────────────────────────


def lemmatize_surfaces_in_context(
    surfaces: list[str],
    sentence: str,
    lemmatizer: Lemmatizer,
    language_code: str,
    db: SRSDatabase | None = None,
    model_version: str = "",
) -> list[str]:
    """Lemmatize each surface using its *sentence* context, with a single-word fallback.

    Slovene lemmas are POS-dependent: classla reads the bare token ``dobro`` as the
    adverb (lemma ``dobro``) and bare ``hotel`` as the verb ``hoteti`` — but ``dobro``
    in *"Vse je dobro"* as the adjective (lemma ``dober``) and ``hotel`` in *"To je
    hotel"* as the noun. Lemmatizing tokens in isolation therefore mis-keys them and
    they never match the dictionary-form cards in the DB. We instead analyze the whole
    *sentence* once and map each *surface* to its in-context lemma, falling back to
    single-word ``lemmatize`` when a surface isn't found in the analysis (tokenization
    or punctuation mismatch).

    For ``LowercaseLemmatizer`` ``analyze_sentence`` is a per-token lowercasing, so the
    result is identical to the old single-word path — this change is a no-op for the
    default lemmatizer and only sharpens the real (classla) engine.

    Lemmas are lowercased to match the card keyspace (``import_seed`` stores
    ``lemma = front.lower()``). classla capitalizes proper-noun lemmas
    (``Ženeve`` → ``Ženeva``), which would otherwise miss the lowercase
    ``ženeva`` card on a case-sensitive ``lemma =`` lookup.

    When *db* and *model_version* are provided the sentence analysis is routed through
    the persistent ``lemma_analysis_cache`` table so the result survives restarts.
    """
    # note: this dict collapses on lowercase key. If the sentence contains multiple
    # surface forms that lowercase to the same key, the last analysis wins. This is
    # usually correct (same surface → same lemma) but can lose distinct lemmas when
    # genuinely different words share a lowercase form.
    analysis = analyze_sentence_cached(db, lemmatizer, sentence, language_code, model_version)
    context = {ta.surface.lower(): ta.lemma.lower() for ta in analysis}
    result: list[str] = []
    for surface in surfaces:
        key = surface.lower()
        if key in context:
            result.append(context[key])
        else:
            result.append(lemmatizer.lemmatize(surface, language_code).lower())
    return result
