"""Shared layout for the full-lesson piece order and offset arithmetic.

Both ``LessonRenderer.render`` (numpy frames) and
``reassemble_lesson_audio`` (ffmpeg ms) arrange the full lesson as::

    title + boundary + sec0 + boundary + sec1 + ... + boundary + sec_{N-1}

This module owns that rule in one place.  Each caller converts the returned
millisecond offsets into its own domain (frames or milliseconds).

The *concatenation* itself stays domain-specific: numpy buffers in the render
path, file paths via ``ffmpeg -c copy`` in the reassembly path.  This module
knows nothing about audio bytes — only about ordering and spacing.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.audio.cues import CueTiming


def _ms_to_frames(ms: int, rate: int) -> int:
    """Milliseconds to frames. ``build_cue_manifest`` divides by the same
    rate, so the value cancels; only using ONE rate throughout matters."""
    return int(round(ms * rate / 1000))


@dataclass(frozen=True)
class LessonLayout:
    """The piece order and timing for a full-lesson assembly.

    ``piece_offsets_ms[i]`` is the absolute millisecond start of
    ``piece_descriptions[i]``.  The total lesson duration is
    ``piece_offsets_ms[-1] + piece_durations_ms[-1]``.
    """

    piece_descriptions: list[str]
    piece_offsets_ms: list[int]
    piece_durations_ms: list[int]
    title_ms: int
    boundary_ms: int
    n_sections: int
    n_boundaries: int


def lesson_layout(
    section_durations_ms: list[int],
    title_ms: int,
    boundary_ms: int,
) -> LessonLayout:
    """Compute the piece order and absolute offsets for a full-lesson assembly.

    The layout is ``title + boundary + sec0 + boundary + sec1 + ...``.
    There are ``N`` boundaries for ``N`` sections (one after the title,
    one between each adjacent pair).

    Args:
        section_durations_ms: Duration of each section in milliseconds.
        title_ms: Duration of the lesson title in milliseconds.
        boundary_ms: Duration of the boundary silence in milliseconds.

    Returns:
        A :class:`LessonLayout` with piece descriptions, offsets and durations.
    """
    n_sections = len(section_durations_ms)

    descriptions: list[str] = ["title", "boundary"]
    offsets_ms: list[int] = [0, title_ms]
    durations_ms: list[int] = [title_ms, boundary_ms]

    cursor_ms = title_ms + boundary_ms
    for i, sec_dur in enumerate(section_durations_ms):
        descriptions.append(f"section_{i}")
        offsets_ms.append(cursor_ms)
        durations_ms.append(sec_dur)

        cursor_ms += sec_dur

        if i < n_sections - 1:
            descriptions.append("boundary")
            offsets_ms.append(cursor_ms)
            durations_ms.append(boundary_ms)
            cursor_ms += boundary_ms

    return LessonLayout(
        piece_descriptions=descriptions,
        piece_offsets_ms=offsets_ms,
        piece_durations_ms=durations_ms,
        title_ms=title_ms,
        boundary_ms=boundary_ms,
        n_sections=n_sections,
        n_boundaries=descriptions.count("boundary"),
    )


def merge_section_cues(
    section_cues: dict[int, list[CueTiming]],
    title_ms: int,
    section_durations_ms: list[int],
    boundary_ms: int,
    assembly_rate: int,
) -> list[CueTiming]:
    """Merge per-section cues back into a full-lesson timing list.

    This is the inverse of the operation performed by
    :func:`derive_section_cues` (which splits a full manifest into
    per-section cues).  It does NOT reconstruct the title cue —
    ``derive_section_cues`` excludes it, and so does this.

    Section cue ``start_frame`` / ``end_frame`` are in the section's own
    frame domain (relative to the section start).  The absolute offset
    for each section is computed in milliseconds from the layout, converted
    to frames via *assembly_rate*, and added to the relative frame position.

    Args:
        section_cues: ``{section_index: [CueTiming, ...]}`` — section-relative
            cues (start_frame/end_frame relative to the section start).
        title_ms: Duration of the lesson title in milliseconds.
        section_durations_ms: Duration of each section in milliseconds.
        boundary_ms: Duration of the boundary silence in milliseconds.
        assembly_rate: Sample rate for ms-to-frames conversion.

    Returns:
        Full-lesson timing entries in chronological order, with absolute
        frame positions.
    """
    layout = lesson_layout(section_durations_ms, title_ms, boundary_ms)

    result: list[CueTiming] = []
    for sec_idx in sorted(section_cues):
        # Section i sits at layout index 2 + 2*i (title=0, boundary=1, sec0=2,
        # boundary=3, sec1=4, ...). The boundary BEFORE each section is included
        # in its absolute offset.
        section_offset_frames = _ms_to_frames(layout.piece_offsets_ms[2 + 2 * sec_idx], assembly_rate)
        for cq in section_cues[sec_idx]:
            result.append(
                CueTiming(
                    section_index=cq.section_index,
                    phrase_index=cq.phrase_index,
                    start_frame=cq.start_frame + section_offset_frames,
                    end_frame=cq.end_frame + section_offset_frames,
                )
            )
    return result
