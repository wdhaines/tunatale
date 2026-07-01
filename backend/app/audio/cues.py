"""Pure cue-manifest builder — derives refs from Lesson structure + frame timing."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.generation.section_builder import build_word_breakdown
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


def _build_dialogue_refs(lesson: Lesson, timing: list[CueTiming], section_idx: int) -> list[Cue]:
    """Build cues for a dialogue section (natural_speed/slow_speed/translated)."""
    section = lesson.sections[section_idx]
    cues: list[Cue] = []
    l2_code = lesson.language_code
    line_n = 0
    # In translated, track whether we're "awaiting" a translation for the last L2
    pending_line: int | None = None

    for te in timing:
        phrase = section.phrases[te.phrase_index]
        is_l2 = phrase.language_code == l2_code
        is_narrator = phrase.role == "narrator"

        if is_l2:
            ref: dict = {"kind": "line", "target_index": line_n}
            pending_line = line_n
            line_n += 1
        elif section.section_type == SectionType.TRANSLATED and is_narrator and pending_line is not None:
            ref = {"kind": "line", "target_index": pending_line}
            pending_line = None
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
    """Build cues for the key_phrases section by consuming expected counts."""
    section = lesson.sections[section_idx]
    cues: list[Cue] = []
    l2_code = lesson.language_code

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

    for kp_idx, kp in enumerate(lesson.key_phrases):
        breakdown = build_word_breakdown(kp.phrase, l2_code)
        expected = 2 + len(breakdown)

        remaining = len(timing) - phrase_idx
        if remaining < expected:
            raise ValueError(
                f"Key phrase phrase-count mismatch: expected {expected} phrases "
                f"for key_phrase[{kp_idx}], got {remaining} remaining phrases"
            )

        ref = {"kind": "key_phrase", "target_index": kp_idx}
        for _ in range(expected):
            te = timing[phrase_idx]
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
                    ref=ref,
                )
            )
            phrase_idx += 1

    if phrase_idx < len(timing):
        raise ValueError(
            f"Key phrase phrase-count mismatch: {len(timing) - phrase_idx} "
            f"extra phrases remain after consuming {len(lesson.key_phrases)} key phrases"
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
