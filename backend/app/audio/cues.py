"""Pure cue-manifest builder — derives refs from Lesson structure + frame timing."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.generation.section_builder import key_phrase_groups
from app.models.lesson import Lesson, SectionType


@dataclass
class Cue:
    index: int
    start_ms: int
    end_ms: int
    section_index: int | None
    section_type: str | None
    phrase_index: int
    role: str
    language_code: str
    text: str
    ref: dict | None = field(default=None)


@dataclass
class CueTiming:
    section_index: int | None
    phrase_index: int
    start_frame: int
    end_frame: int


_L2_FIRST_TRANSLATED = (SectionType.TRANSLATED, SectionType.SLOW_TRANSLATED)
_EN_FIRST_TRANSLATED = (SectionType.EN_TRANSLATED, SectionType.SLOW_EN_TRANSLATED)


def _next_is_l2(section, timing: list[CueTiming], idx: int, l2_code: str) -> bool:
    """Whether the phrase after ``timing[idx]`` is an L2 line (translation-first lookahead)."""
    if idx + 1 >= len(timing):
        return False
    next_phrase = section.phrases[timing[idx + 1].phrase_index]
    return next_phrase.language_code == l2_code


def _build_dialogue_refs(lesson: Lesson, timing: list[CueTiming], section_idx: int) -> list[Cue]:
    """Build cues for a dialogue section.

    Covers natural_speed / slow_speed (L2 lines only) and the bilingual
    sections in both orderings: translated / slow_translated pair the narrator
    translation with the *preceding* L2 line; en_translated / slow_en_translated
    pair it with the *following* L2 line (translation-first). Either way the
    translation cue shares its L2 line's ``line`` ref so the player groups them.
    """
    section = lesson.sections[section_idx]
    cues: list[Cue] = []
    l2_code = lesson.language_code
    line_n = 0
    # In L2-first translated, track whether we're "awaiting" a translation for
    # the last L2 line.
    pending_line: int | None = None

    for i, te in enumerate(timing):
        phrase = section.phrases[te.phrase_index]
        is_l2 = phrase.language_code == l2_code
        is_narrator = phrase.role == "narrator"

        if is_l2:
            ref: dict = {"kind": "line", "target_index": line_n}
            pending_line = line_n
            line_n += 1
        elif section.section_type in _L2_FIRST_TRANSLATED and is_narrator and pending_line is not None:
            ref = {"kind": "line", "target_index": pending_line}
            pending_line = None
        elif section.section_type in _EN_FIRST_TRANSLATED and is_narrator and _next_is_l2(section, timing, i, l2_code):
            # Translation-first: this narrator line translates the L2 line that
            # follows it, which will take the upcoming line index (line_n).
            ref = {"kind": "line", "target_index": line_n}
        else:
            ref = {"kind": "narration"}
            pending_line = None

        cues.append(
            Cue(
                index=0,
                start_ms=0,
                end_ms=0,
                section_index=section_idx,
                section_type=section.section_type.value,
                phrase_index=te.phrase_index,
                role=phrase.role,
                language_code=phrase.language_code,
                text=phrase.text,
                ref=ref,
            )
        )
    return cues


def _build_key_phrases_refs(lesson: Lesson, timing: list[CueTiming], section_idx: int) -> list[Cue]:
    """Build cues for the key_phrases section from the section's own group structure.

    The groups come from :func:`key_phrase_groups`, which reads the stored
    section's shape. It does NOT re-run the breakdown rules over
    ``lesson.key_phrases`` to work out how many phrases each one occupies: a
    stored section is a snapshot laid out under the rules as they were then, and
    re-deriving the count from today's rules makes every re-render of an older
    lesson die with a phrase-count mismatch — after all the TTS is done. Measured
    2026-09-29, 8 of 11 stored Norwegian lessons failed that arithmetic.

    Each timing entry is mapped to its group by ``te.phrase_index``, so timing is
    free to skip phrases without the group boundaries shifting underneath it.
    A structural inconsistency is still loud: a group count that disagrees with
    ``lesson.key_phrases``, or a timing entry landing in no group at all, raises.
    """
    section = lesson.sections[section_idx]
    cues: list[Cue] = []
    l2_code = lesson.language_code

    groups = key_phrase_groups(section, l2_code)
    if len(groups) != len(lesson.key_phrases):
        raise ValueError(
            f"Key phrase phrase-count mismatch: section has {len(groups)} key-phrase "
            f"group(s), lesson declares {len(lesson.key_phrases)} key phrase(s)"
        )

    group_of: dict[int, int] = {phrase_index: k for k, group in enumerate(groups) for phrase_index in group}

    phrase_idx = 0  # index into timing entries

    # Section title (first phrase) → narration
    if timing and timing[0].phrase_index == 0:
        phrase = section.phrases[0]
        cues.append(
            Cue(
                index=0,
                start_ms=0,
                end_ms=0,
                section_index=section_idx,
                section_type=SectionType.KEY_PHRASES.value,
                phrase_index=0,
                role=phrase.role,
                language_code=phrase.language_code,
                text=phrase.text,
                ref={"kind": "narration"},
            )
        )
        phrase_idx += 1

    for te in timing[phrase_idx:]:
        k = group_of.get(te.phrase_index)
        if k is None:
            raise ValueError(
                f"Key phrase phrase-count mismatch: phrase {te.phrase_index} of section "
                f"{section_idx} belongs to no key-phrase group"
            )
        phrase = section.phrases[te.phrase_index]
        cues.append(
            Cue(
                index=0,
                start_ms=0,
                end_ms=0,
                section_index=section_idx,
                section_type=SectionType.KEY_PHRASES.value,
                phrase_index=te.phrase_index,
                role=phrase.role,
                language_code=phrase.language_code,
                text=phrase.text,
                ref={"kind": "key_phrase", "target_index": k},
            )
        )

    return cues


def build_cue_manifest(lesson: Lesson, timing: list[CueTiming], rate: int) -> list[Cue]:
    """Build the full cue manifest from lesson structure and frame timing.

    Args:
        lesson: The lesson being rendered.
        timing: Per-phrase timing entries in render (chronological) order.
        rate: Sample rate of the audio buffer (frames per second).

    Returns:
        List of Cue objects in chronological order.
    """
    all_cues: list[Cue] = []

    # Group timing by section_index
    section_groups: dict[int | None, list[CueTiming]] = {}
    for te in timing:
        section_groups.setdefault(te.section_index, []).append(te)

    # Title (section_index is None)
    title_timing = section_groups.get(None, [])
    if title_timing:
        all_cues.append(
            Cue(
                index=0,
                start_ms=0,
                end_ms=0,
                section_index=None,
                section_type=None,
                phrase_index=0,
                role="narrator",
                language_code="en",
                text=lesson.title,
                ref={"kind": "narration"},
            )
        )

    # Process sections in order
    for section_idx, section in enumerate(lesson.sections):
        sec_timing = section_groups.get(section_idx, [])
        if not sec_timing:
            continue

        if section.section_type == SectionType.KEY_PHRASES:
            cues = _build_key_phrases_refs(lesson, sec_timing, section_idx)
        else:
            cues = _build_dialogue_refs(lesson, sec_timing, section_idx)

        all_cues.extend(cues)

    # Assign index, start_ms, end_ms, fill in title's phrase/language
    for i, cue in enumerate(all_cues):
        cue.index = i
        te = timing[i]
        cue.start_ms = round(te.start_frame / rate * 1000)
        cue.end_ms = round(te.end_frame / rate * 1000)

    # Set title cue language to narrator voice language
    if all_cues and all_cues[0].section_index is None:
        all_cues[0].language_code = "en"

    return all_cues
