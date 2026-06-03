"""Lemmatizer protocol and default implementation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, runtime_checkable

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

    def lemmatize(self, word: str, language_code: str) -> str:
        return word.lower()

    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
        return word.lower(), "", ""

    def analyze_sentence(self, sentence: str, language_code: str) -> list[TokenAnalysis]:
        tokens = sentence.split()
        return [
            TokenAnalysis(surface=t, lemma=t.lower(), upos="", case="", number="", person="", gender="") for t in tokens
        ]


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
        # Lesson text is stable across requests; cache analyses by sentence so the
        # transcript endpoint doesn't re-run the NLP pipeline on every state-change
        # refetch (~3.6s → DB-only once warmed). Keyed by exact text, so edited
        # sentences re-analyze. Bounded by the user's distinct lesson sentences.
        self._sentence_cache: dict[str, list[TokenAnalysis]] = {}

    def _ensure_pipeline(self) -> ClasslaPipeline:
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

    def analyze_sentence(self, sentence: str, language_code: str) -> list[TokenAnalysis]:  # pragma: no cover
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


# Avoid importing classla at module level (CI guard).
# The type alias lets us reference the type without a top-level import.
try:
    from classla import Pipeline as ClasslaPipeline
except ImportError:  # pragma: no cover — optional dep; classla presence is environment-dependent
    ClasslaPipeline = None  # type: ignore[misc,assignment]


# ── Factory ────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_lemmatizer() -> Lemmatizer:
    """Return a cached lemmatizer based on ``settings.lemmatizer_type``.

    * ``"lowercase"`` (default) — ``LowercaseLemmatizer``
    * ``"classla"`` — ``ClasslaLemmatizer``, falling back to ``LowercaseLemmatizer``
      with a logged warning if classla is not importable.
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
    return LowercaseLemmatizer()


def lemmatize_surfaces_in_context(
    surfaces: list[str],
    sentence: str,
    lemmatizer: Lemmatizer,
    language_code: str,
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
    """
    # note: this dict collapses on lowercase key. If the sentence contains multiple
    # surface forms that lowercase to the same key, the last analysis wins. This is
    # usually correct (same surface → same lemma) but can lose distinct lemmas when
    # genuinely different words share a lowercase form.
    context = {ta.surface.lower(): ta.lemma.lower() for ta in lemmatizer.analyze_sentence(sentence, language_code)}
    result: list[str] = []
    for surface in surfaces:
        key = surface.lower()
        if key in context:
            result.append(context[key])
        else:
            result.append(lemmatizer.lemmatize(surface, language_code).lower())
    return result
