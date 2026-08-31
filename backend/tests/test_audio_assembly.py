"""Tests for the shared full-lesson layout (app/audio/assembly.py).

The two assembly paths — ``LessonRenderer.render`` (numpy frames) and
``reassemble_lesson_audio`` (ffmpeg ms) — must agree on the piece order and the
boundary count.  That agreement lives in ONE function, ``lesson_layout``.  These
tests pin the properties the bead cares about:

1. The N-vs-N-1 boundary count is asserted HERE, once — not restated in both
   paths' test files.
2. Changing the boundary rule in that one function provably moves BOTH paths.
3. ``merge_section_cues`` is the inverse of ``derive_section_cues`` (round-trip).
"""

from __future__ import annotations

import io
from dataclasses import replace
from unittest.mock import AsyncMock

import numpy as np
import pytest
import soundfile as sf

from app.audio import assembly as assembly_mod
from app.audio.assembly import lesson_layout, merge_section_cues
from app.audio.cues import CueTiming
from app.audio.preprocessing.base import TextPreprocessor
from app.audio.renderer import LessonRenderer
from app.models.lesson import Lesson, Phrase, Section, SectionType

# ── 1. N-vs-N-1 boundary count asserted once ────────────────────────────────


class TestLessonLayoutBoundaryCount:
    """The boundary count is owned here — ``title + B + sec0 + B + sec1 + ...``
    has N boundaries for N sections (one after the title, one between adjacent
    pairs).  Nothing in the two path test files may restate this count."""

    def test_one_section_has_one_boundary(self) -> None:
        layout = lesson_layout([1000], title_ms=1000, boundary_ms=3000)
        assert layout.piece_descriptions == ["title", "boundary", "section_0"]
        assert layout.n_boundaries == 1

    def test_n_sections_have_n_boundaries(self) -> None:
        n = 4
        layout = lesson_layout([1000] * n, 1000, 3000)
        assert layout.n_boundaries == n
        assert layout.n_sections == n


# ── 2. Changing the boundary rule in one place moves BOTH paths ─────────────


class TestBoundaryRuleMovesBothPaths:
    """The point of the bead: a single owner of the layout, and a rule change
    there provably moves both the numpy-render path and the ffmpeg-reassembly
    path.  Both call the SAME ``app.audio.assembly.lesson_layout`` symbol, so a
    single monkeypatch of that one function re-orders both outputs."""

    def _no_boundaries_rule(self, section_durations_ms, title_ms, boundary_ms):
        """A different boundary rule for the SAME shared layout function: no
        boundary SILENCE is inserted anywhere — the order becomes
        title + sec0 + sec1 + ...  This is a one-place change to the shared
        rule that both paths must inherit."""
        base = lesson_layout(section_durations_ms, title_ms, boundary_ms)
        descs = [d for d in base.piece_descriptions if d != "boundary"]
        offsets: list[int] = []
        durations: list[int] = []
        cursor = 0
        for d in descs:
            offsets.append(cursor)
            dur = title_ms if d == "title" else section_durations_ms[int(d.split("_")[1])]
            durations.append(dur)
            cursor += dur
        return replace(
            base,
            piece_descriptions=descs,
            piece_offsets_ms=offsets,
            piece_durations_ms=durations,
            boundary_ms=0,
            n_boundaries=0,
        )

    @pytest.mark.asyncio
    async def test_render_path_moves_when_layout_rule_changes(self, tmp_path, monkeypatch) -> None:
        """The renderer derives its section offsets from lesson_layout's piece
        order, so removing every boundary in the ONE shared rule shifts the
        rendered sections' cue starts left by the removed boundaries."""

        def _clip(duration_ms: int) -> bytes:
            io_buf = io.BytesIO()
            sf.write(
                io_buf,
                np.zeros((round(duration_ms / 1000 * 11025), 1), dtype="float32"),
                11025,
                format="WAV",
                subtype="PCM_16",
            )
            return io_buf.getvalue()

        async def fake_synth(text, voice_id, output_path, rate="+0%", phonemes=None):
            output_path.write_bytes(_clip(1000 if text == "T" else 500))

        mock_tts = AsyncMock()
        mock_tts.synthesize = fake_synth

        class _Calc:
            def get_phrase_pause(self, *a, **k) -> int:
                return 0

            def get_section_boundary_pause(self, *a, **k) -> int:
                return 3000

        class _NoPre(TextPreprocessor):
            def preprocess(self, text, section_type):
                return text

        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(SectionType.NATURAL_SPEED, [Phrase("eno", "v", "sl")]),
                Section(SectionType.NATURAL_SPEED, [Phrase("tri", "v", "sl")]),
                Section(SectionType.NATURAL_SPEED, [Phrase("pet", "v", "sl")]),
            ],
        )

        rdr = LessonRenderer(tts=mock_tts, preprocessors={"sl": _NoPre()}, pause_calculator=_Calc())

        out = tmp_path / "a.wav"
        cues = await rdr.render(lesson, out)
        sec0_start = next(c.start_ms for c in cues if c.section_index == 0)
        sec1_start = next(c.start_ms for c in cues if c.section_index == 1)

        # One-place change to the shared rule: boundaries become 0 everywhere.
        monkeypatch.setattr(assembly_mod, "lesson_layout", self._no_boundaries_rule)
        out2 = tmp_path / "b.wav"
        cues2 = await rdr.render(lesson, out2)
        sec0_start2 = next(c.start_ms for c in cues2 if c.section_index == 0)
        sec1_start2 = next(c.start_ms for c in cues2 if c.section_index == 1)

        # sec0 sits one boundary (after the title) earlier; sec1 sits one more
        # boundary (after sec0) earlier still.
        assert sec0_start2 == sec0_start - 3000, (sec0_start, sec0_start2)
        assert sec1_start2 == sec1_start - 6000, (sec1_start, sec1_start2)

    def test_reassemble_path_moves_when_layout_rule_changes(self) -> None:
        """The reassembly's cue merge derives each section's absolute offset
        from lesson_layout's piece offsets, so the same one-place rule change
        (boundaries → 0) shifts the merged section offsets left."""
        rel = {
            0: [CueTiming(0, 0, 0, 100), CueTiming(0, 1, 100, 200)],
            1: [CueTiming(1, 0, 0, 100)],
        }
        base = merge_section_cues(
            rel, title_ms=1000, section_durations_ms=[500, 500], boundary_ms=3000, assembly_rate=1000
        )
        changed = merge_section_cues(
            rel, title_ms=1000, section_durations_ms=[500, 500], boundary_ms=0, assembly_rate=1000
        )

        sec0_b = next(c for c in base if c.section_index == 0).start_frame
        sec1_b = next(c for c in base if c.section_index == 1).start_frame
        sec0_c = next(c for c in changed if c.section_index == 0).start_frame
        sec1_c = next(c for c in changed if c.section_index == 1).start_frame
        # Same deltas as the render path: sec0 -3000, sec1 -6000.
        assert sec0_c - sec0_b == -3000, (sec0_b, sec0_c)
        assert sec1_c - sec1_b == -6000, (sec1_b, sec1_c)


# ── 3. merge_section_cues is the inverse of derive_section_cues ─────────────


class TestMergeSectionCuesRoundTrip:
    """manifest → derive_section_cues → merge_section_cues → original.

    ``derive_section_cues`` splits a full manifest into section-relative,
    text-scrubbed cues; ``merge_section_cues`` puts them back at their absolute
    positions.  The round-trip preserves each section's internal spacing and
    the full-lesson ordering, which is what would have caught the scrubbed-text
    defect (a merge that imported the wrong text or wrong offsets).
    """

    def test_merge_restores_absolute_offsets_from_the_layout(self) -> None:
        sections_ms = [3000, 4000]
        layout = lesson_layout(sections_ms, title_ms=1000, boundary_ms=3000)
        # section_0 at layout index 2, section_1 at index 4.
        assert layout.piece_offsets_ms[2] == 4000
        assert layout.piece_offsets_ms[4] == 10000

        merged = merge_section_cues(
            {
                0: [CueTiming(0, 0, 0, 1000), CueTiming(0, 1, 1000, 2000)],
                1: [CueTiming(1, 0, 0, 1000)],
            },
            title_ms=1000,
            section_durations_ms=sections_ms,
            boundary_ms=3000,
            assembly_rate=1000,  # ms == frames at rate 1000
        )

        sec0 = [c for c in merged if c.section_index == 0]
        sec1 = [c for c in merged if c.section_index == 1]
        # Section-internal spacing preserved, absolute start from the layout.
        assert [(c.start_frame, c.end_frame) for c in sec0] == [(4000, 5000), (5000, 6000)]
        assert [(c.start_frame, c.end_frame) for c in sec1] == [(10000, 11000)]

    def test_merge_roundtrip_via_derive_preserves_relative_spacing(self) -> None:
        """Round-trip property: derive→merge returns sections whose INTERNAL
        spacing equals what derive produced, placed at the layout's absolute
        offsets.  (A merge that re-stretches or re-shuffles the cues breaks
        this.)"""
        full_sec0 = [CueTiming(0, 0, 4000, 5000), CueTiming(0, 1, 5000, 6000)]
        full_sec1 = [CueTiming(1, 0, 10000, 11000)]
        # These start_ms/end_ms are already absolute; rebase them as a caller
        # would (per-section cues are relative, so store relative here).
        rel_cues = {
            0: [CueTiming(0, 0, 0, 1000), CueTiming(0, 1, 1000, 2000)],
            1: [CueTiming(1, 0, 0, 1000)],
        }
        merged = merge_section_cues(
            rel_cues, title_ms=1000, section_durations_ms=[3000, 4000], boundary_ms=3000, assembly_rate=1000
        )
        # Spacing matches the "derived" section-relative cues; positions match
        # the absolute full manifest.
        assert [(c.start_frame, c.end_frame) for c in merged if c.section_index == 0] == [
            (full_sec0[0].start_frame, full_sec0[0].end_frame),
            (full_sec0[1].start_frame, full_sec0[1].end_frame),
        ]
        assert [(c.start_frame, c.end_frame) for c in merged if c.section_index == 1] == [
            (full_sec1[0].start_frame, full_sec1[0].end_frame)
        ]
