"""One-shot migration: rebuild each lesson's KEY_PHRASES section from scratch.

The sibling module :mod:`backfill_breakdown_provenance` is the conservative
one — it attaches provenance only where recomputation reproduces the stored
text, so it can never disturb a lesson whose rendered audio says something
else. That refusal is exactly what makes it useless for the older lessons: the
Norwegian syllabifier has since been fixed (``le|i|lig|het|en`` ->
``lei|lig|het|en``; ``mi|stenkt`` -> ``mis|tenkt``) and the ``de`` -> ``deh``
respelling dropped, so their stored breakdown is what an earlier algorithm
produced and no amount of careful matching will improve it.

This module does the invasive thing instead: it regenerates the section from
``lesson.key_phrases`` — the same input the generator used — so the breakdown
becomes what today's code would build, provenance included.

**The caller MUST re-render the lesson's audio afterwards.** Until then the
stored audio speaks the old breakdown while the text and captions claim the new
one. This is the trade the conservative migration exists to avoid, taken
deliberately and only where a human asked for it.

Untouched: the story, ``lesson.key_phrases`` themselves, and every other
section. Voices are read back off the stored section rather than re-resolved,
so a rebuild cannot silently re-voice a lesson.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.generation.section_builder import build_key_phrases_section
from app.models.lesson import SectionType

if TYPE_CHECKING:
    from app.models.lesson import Lesson, Section

    from .store import ContentStore


@dataclass
class ResyncReport:
    """``diffs`` maps lesson id → the ``(stored, rebuilt)`` chunk texts that differ."""

    examined: int = 0
    updated: int = 0
    diffs: dict[str, list[tuple[str, str]]] = field(default_factory=dict)


def _key_phrases_section(lesson: Lesson) -> tuple[int, Section] | None:
    for index, section in enumerate(lesson.sections):
        if section.section_type == SectionType.KEY_PHRASES:
            return index, section
    return None


def _voices(section: Section, language_code: str) -> tuple[dict[str, str], str]:
    """Recover ``(l2_voice_map, narrator_voice)`` from the stored section.

    Read back rather than re-resolved through the registry: a migration that
    quietly moved a lesson onto a different voice than the one already in its
    audio would be a worse bug than the one being fixed.
    """
    narrator = next((p.voice_id for p in section.phrases if p.role == "narrator"), "")
    female_1 = next((p.voice_id for p in section.phrases if p.language_code == language_code), narrator)
    return {"female-1": female_1}, narrator


def resync_key_phrases(store: ContentStore, *, dry_run: bool = False) -> ResyncReport:
    """Rebuild every lesson's KEY_PHRASES section with the current builder."""
    report = ResyncReport()
    for lesson_id, _curriculum_id, _day, lesson in store.list_lessons():
        report.examined += 1
        found = _key_phrases_section(lesson)
        if found is None or not lesson.key_phrases:
            continue
        index, section = found

        voice_map, narrator = _voices(section, lesson.language_code)
        rebuilt = build_key_phrases_section(
            [{"phrase": kp.phrase, "translation": kp.translation} for kp in lesson.key_phrases],
            voice_map,
            narrator,
            lesson.language_code,
        )

        before = [(p.text, p.source_word, p.syllable_span) for p in section.phrases]
        after = [(p.text, p.source_word, p.syllable_span) for p in rebuilt.phrases]
        if before == after:
            continue

        report.diffs[lesson_id] = [
            (old[0], new[0]) for old, new in zip(before, after, strict=False) if old[0] != new[0]
        ]
        report.updated += 1
        if dry_run:
            continue

        lesson.sections[index] = rebuilt
        store.update_lesson_data(lesson_id, lesson)
    return report
