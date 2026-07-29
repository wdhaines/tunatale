"""One-shot migration: attach slicing provenance to already-stored lessons.

``Phrase.source_word`` / ``Phrase.syllable_span`` were added after these lessons
were generated, so a stored lesson carries no provenance and the renderer
synthesises every breakdown chunk in isolation even on a slicing-capable
install. The information is recoverable: the KEY_PHRASES section is a pure
function of ``lesson.key_phrases``, so re-running the builder reproduces the
same chunks — with provenance this time.

**It only writes where recomputation reproduces the stored text exactly.**
That check is the migration, not a safety belt around it. A span indexes the
CURRENT syllabifier's output, and lessons generated before it changed contain
different chunks (``le|i|lig|het|en`` for ``leiligheten``, from before Norwegian
diphthongs were handled; ``mi|stenkt``; the pre-respelling ``de`` for ``deh``).
Attaching today's spans to yesterday's text would point the slicer at the wrong
syllables — trading "not sliced" for "sliced wrongly", which is strictly worse
and silent.

A lesson the check rejects is left untouched and reported. The remedy for those
is regeneration, not a looser migration: their stored breakdown is what the old
syllabifier produced, and their audio says it out loud.

Idempotent: a lesson that already carries the provenance it would be given is
not rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.generation.section_builder import build_word_breakdown_spans
from app.models.lesson import SectionType

if TYPE_CHECKING:
    from app.models.lesson import Lesson, Section

    from .store import ContentStore


@dataclass
class BackfillReport:
    """What the run did. ``skipped`` is ``(lesson_id, reason)`` pairs."""

    examined: int = 0
    updated: int = 0
    spans_written: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _key_phrases_section(lesson: Lesson) -> Section | None:
    for section in lesson.sections:
        if section.section_type == SectionType.KEY_PHRASES:
            return section
    return None


def _plan(lesson: Lesson, section: Section) -> tuple[list[tuple[int, str, tuple[int, int]]], str | None]:
    """Provenance to write as ``(phrase_index, source_word, span)``, or a reason not to.

    Walks the section the way the builder laid it out — a title, then
    ``(L2 phrase, translation, *chunks)`` per key phrase — and refuses at the
    first divergence rather than resynchronising. Resynchronising is how a
    migration ends up confidently writing the wrong answer.
    """
    plan: list[tuple[int, str, tuple[int, int]]] = []
    idx = 1  # phrases[0] is the narrator section title
    for kp in lesson.key_phrases:
        chunks = build_word_breakdown_spans(kp.phrase, lesson.language_code)
        expected = [kp.phrase, kp.translation, *[c.text for c in chunks]]
        actual = [p.text for p in section.phrases[idx : idx + len(expected)]]
        if actual != expected:
            return [], f"recomputed text differs at key phrase {kp.phrase!r}"
        for offset, chunk in enumerate(chunks):
            if chunk.source_word is not None and chunk.span is not None:
                plan.append((idx + 2 + offset, chunk.source_word, chunk.span))
        idx += len(expected)

    if idx != len(section.phrases):
        return [], f"trailing phrases: {len(section.phrases) - idx} unaccounted for after the last key phrase"
    return plan, None


def backfill_breakdown_provenance(store: ContentStore) -> BackfillReport:
    """Attach ``source_word``/``syllable_span`` to every eligible stored lesson."""
    report = BackfillReport()
    for lesson_id, _curriculum_id, _day, lesson in store.list_lessons():
        report.examined += 1
        section = _key_phrases_section(lesson)
        if section is None:
            report.skipped.append((lesson_id, "no key_phrases section"))
            continue

        plan, reason = _plan(lesson, section)
        if reason is not None:
            report.skipped.append((lesson_id, reason))
            continue

        changed = False
        for index, source_word, span in plan:
            phrase = section.phrases[index]
            if phrase.source_word == source_word and phrase.syllable_span == span:
                continue
            phrase.source_word = source_word
            phrase.syllable_span = span
            changed = True
        if not changed:
            continue

        # UPDATE, never save_lesson: that is INSERT OR REPLACE, which assigns a
        # new rowid and resets created_at — and `get_lesson_days` resolves "the
        # lesson for day N" as MAX(rowid), so a rewrite could change which
        # lesson the UI shows on a day that has more than one.
        store.update_lesson_data(lesson_id, lesson)
        report.updated += 1
        report.spans_written += len(plan)
    return report
