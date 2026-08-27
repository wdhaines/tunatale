"""Detect stored captions whose text no longer matches its own syllable span.

A stored chunk phrase carries ``source_word``, ``syllable_span`` and ``text``,
where ``text`` is the caption the audio was rendered from. When a boundary rule
improves, the span comes to denote DIFFERENT letters than it did at render time,
and the stored text no longer matches what the span now selects. ``plan_chunk``
refuses to attach IPA to such a chunk (correctly — the stored audio was rendered
for the old caption), so the chunk silently degrades to plain synthesis. Nothing
reports it.

This module is the detector: it walks already-loaded lesson content, pure with
no database or I/O, and returns one record per stale chunk. It REPORTS; it never
writes, because rewriting the stored text would make the caption disagree with
the audio file already on disk — a worse failure than the silence it replaces.

The span indexes the syllabifier the renderer sliced with
(``AlignmentConfig.syllabify_fn``), not necessarily the plain ``syllabifier_fn``
— Norwegian's file a compound with per-part lexicon resolution whose output is
``None`` when the pieces fail to rejoin the surface. Both are resolved through
the ``app.languages`` registry; no hardcoded language logic lives here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.languages import get_alignment, get_syllabifier

if TYPE_CHECKING:
    from app.models.lesson import Lesson


@dataclass(frozen=True)
class StaleCaption:
    """A chunk whose stored caption names different letters than its span now denotes.

    ``stored_text`` is what the audio file says; ``now_text`` is what the span
    selects under the current syllabifier. A human decides whether to re-render.
    """

    lesson_id: str
    section_index: int
    phrase_index: int
    source_word: str
    syllable_span: tuple[int, int]
    stored_text: str
    now_text: str


@dataclass(frozen=True)
class UnbreakableCaption:
    """A chunk whose ``source_word`` no longer syllabifies, so its span denotes nothing.

    Distinct from a moved boundary on purpose: not "the span means different
    letters" but "the span no longer resolves". Reported separately so the two
    failures are not conflated and neither is silently skipped.
    """

    lesson_id: str
    section_index: int
    phrase_index: int
    source_word: str
    syllable_span: tuple[int, int]
    stored_text: str


def _resolve_span_syllabifier(language_code: str) -> Callable[[str], list[str] | None]:
    """The function whose output ``syllable_span`` indexes, for *language_code*.

    The spans a language's breakdown emits carry provenance into the syllabifier
    the aligner slices with — ``AlignmentConfig.syllabify_fn`` — which is not
    necessarily the plain ``syllabifier_fn`` (again Norwegian, whose breakdown
    segments compounds and resolves each part against the lexicon). When the
    language has alignment wiring that is the authoritative indexer; otherwise
    the plain syllabifier is what the generic per-syllable buildup indexed.
    """

    alignment = get_alignment(language_code)
    if alignment is not None:
        return alignment.syllabify_fn
    return get_syllabifier(language_code)


def find_stale_captions(
    lesson_id: str,
    lesson: Lesson,
    *,
    syllabify: Callable[[str], list[str] | None] | None = None,
) -> tuple[list[StaleCaption], list[UnbreakableCaption]]:
    """Return ``(stale, unbreakable)`` records for every checkable chunk of *lesson*.

    A chunk is checkable when it carries both ``source_word`` and
    ``syllable_span``; a chunk missing either is not checkable and is skipped,
    never reported. The stored text is compared case-insensitively because it is
    not reliably cased.

    *syllabify* is injectable for tests; when omitted it is resolved through the
    registry for ``lesson.language_code``.
    """

    if syllabify is None:
        syllabify = _resolve_span_syllabifier(lesson.language_code)

    stale: list[StaleCaption] = []
    unbreakable: list[UnbreakableCaption] = []
    for si, section in enumerate(lesson.sections):
        for pi, phrase in enumerate(section.phrases):
            if phrase.source_word is None or phrase.syllable_span is None:
                continue
            start, stop = phrase.syllable_span
            syllables = syllabify(phrase.source_word)
            if syllables is None or not syllables:
                unbreakable.append(
                    UnbreakableCaption(
                        lesson_id=lesson_id,
                        section_index=si,
                        phrase_index=pi,
                        source_word=phrase.source_word,
                        syllable_span=phrase.syllable_span,
                        stored_text=phrase.text,
                    )
                )
                continue
            now_text = "".join(syllables[start:stop])
            if now_text.lower() != phrase.text.lower():
                stale.append(
                    StaleCaption(
                        lesson_id=lesson_id,
                        section_index=si,
                        phrase_index=pi,
                        source_word=phrase.source_word,
                        syllable_span=phrase.syllable_span,
                        stored_text=phrase.text,
                        now_text=now_text,
                    )
                )
    return stale, unbreakable
