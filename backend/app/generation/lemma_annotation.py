"""Lemma and part-of-speech work on a freshly built lesson.

Moved out of ``app.api.generation`` (tunatale-ux66.5 item 1): ``publish_lesson``
awaits the tagging before its write and fires the pre-warm after it, and seven
writers depend on that ordering. It used to reach these helpers through the HTTP
router with function-level imports, because a module-level one was a cycle. Now
the router depends on generation, never the reverse.
"""

from __future__ import annotations

import logging
from functools import partial

import anyio

from app.models.lesson import Lesson, SectionType
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import analyze_sentence_cached, get_lemmatizer, model_version_for

_logger = logging.getLogger(__name__)


async def _prewarm_lesson(lesson: Lesson, srs_db: SRSDatabase) -> None:
    """Background pre-warm: cache a freshly generated lesson's sentences.

    Runs the new lesson's natural-speed L2 sentences through
    ``analyze_sentence_cached`` so the transcript view never triggers a
    classla load for this content.
    """
    try:
        lemmatizer = get_lemmatizer(lesson.language_code)
        model_version = model_version_for(lemmatizer)
        if not model_version:
            return
        natural_speed = next(
            (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
            None,
        )
        if natural_speed is None:
            return
        phrases = [(p.text, p.language_code) for p in natural_speed.phrases if p.language_code == lesson.language_code]
        await anyio.to_thread.run_sync(
            _prewarm_phrases, phrases, srs_db, lemmatizer, model_version, lesson.language_code
        )
    except Exception:
        _logger.warning("Pre-warm failed for new lesson", exc_info=True)


def _prewarm_phrases(
    phrases: list[tuple[str, str]],
    srs_db: SRSDatabase,
    lemmatizer: object,
    model_version: str,
    language_code: str,
) -> None:
    for text, _ in phrases:
        analyze_sentence_cached(srs_db, lemmatizer, text, language_code, model_version)


async def annotate_chunk_upos_for_lesson(
    lesson: Lesson,
    srs_db: SRSDatabase,
    *,
    lemmatizer: object | None = None,
    model_version: str | None = None,
) -> int:
    """Resolve the lemmatizer and tag *lesson*'s chunk phrases. Never raises.

    The caller must AWAIT this BEFORE saving the lesson. Firing it as a detached
    task races the save: the lesson lands untagged and the tags are computed into
    an in-memory object nobody persists again — silently inert, with every test
    still green because they exercise ``annotate_chunk_upos`` directly.

    *lemmatizer* and *model_version* are injectable for the same reason
    ``annotate_chunk_upos`` takes them: resolving them for real loads stanza,
    which CI deliberately does not install (``--no-group lemmatizers``), so the
    only way to exercise the success path in the gate is to supply a double.
    """
    try:
        if lemmatizer is None:
            lemmatizer = get_lemmatizer(lesson.language_code)
        if model_version is None:
            model_version = model_version_for(lemmatizer)
        if not model_version:
            return 0
        return await anyio.to_thread.run_sync(
            partial(annotate_chunk_upos, lesson, srs_db, lemmatizer=lemmatizer, model_version=model_version)
        )
    except Exception:
        _logger.warning("UPOS annotation failed for lesson", exc_info=True)
        return 0


def annotate_chunk_upos(lesson: Lesson, srs_db: SRSDatabase, *, lemmatizer: object, model_version: str) -> int:
    """Post-generation pass: tag chunk phrases with their key phrase's UPOS.

    Mirrors ``_prewarm_lesson``'s shape: ``get_lemmatizer`` + ``model_version_for``
    externally, ``anyio.to_thread.run_sync`` for the NLP work, and failures
    swallowed with a warning — tagging must never break generation.

    Returns the number of phrases tagged. Walks the KEY_PHRASES section using the
    same arithmetic as ``_build_key_phrases_refs``: one title phrase, then
    ``2 + len(breakdown)`` per key phrase. This attaches each chunk to *its own*
    key phrase — never a lesson-wide surface→upos map, because a word can be a
    noun in one key phrase and a verb in another.
    """
    if not model_version:
        return 0

    from app.generation.section_builder import build_word_breakdown_spans
    from app.srs.lemmatizer import TokenAnalysis, analyze_sentence_cached

    kp_section = next(
        (s for s in lesson.sections if s.section_type == SectionType.KEY_PHRASES),
        None,
    )
    if kp_section is None:
        return 0

    l2_code = lesson.language_code
    phrases = kp_section.phrases
    count = 0
    phrase_idx = 0

    # Skip title phrase (first phrase in the section)
    if phrases and phrases[0].language_code != l2_code:
        phrase_idx = 1

    for kp in lesson.key_phrases:
        breakdown = build_word_breakdown_spans(kp.phrase, l2_code)
        expected = 2 + len(breakdown)

        remaining = len(phrases) - phrase_idx
        if remaining < expected:
            import warnings

            warnings.warn(
                f"annotate_chunk_upos: key phrase arithmetic mismatch for {kp.phrase!r}: "
                f"expected {expected} phrases, {remaining} remaining — skipping",
                UserWarning,
                stacklevel=2,
            )
            return 0

        # Analyze this key phrase's sentence
        try:
            analyses: list[TokenAnalysis] = analyze_sentence_cached(
                srs_db, lemmatizer, kp.phrase, l2_code, model_version
            )
            surface_to_upos: dict[str, str] = {ta.surface.lower(): ta.upos for ta in analyses if ta.upos}
        except Exception:
            import warnings

            warnings.warn(
                f"annotate_chunk_upos: analysis failed for {kp.phrase!r}",
                UserWarning,
                stacklevel=2,
            )
            phrase_idx += expected
            continue

        # Walk the expected phrases: L2 text (idx 0), EN translation (idx 1),
        # then breakdown chunks (idx 2..expected-1)
        for i in range(2, expected):
            chunk_phrase = phrases[phrase_idx + i]
            if chunk_phrase.source_word is not None:
                upos = surface_to_upos.get(chunk_phrase.source_word.lower(), "")
                if upos:
                    chunk_phrase.upos = upos
                    count += 1

        phrase_idx += expected

    return count
