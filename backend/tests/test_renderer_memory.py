"""A render holds ONE section's audio at a time, not the whole lesson.

bd tunatale-guzo.5. After encode-once (guzo.3) a render still assembled every
section concurrently and kept every section's float32 buffer until the exports:
measured 2026-09-28, peak ru_maxrss 445 MB for a 45.8-min lesson whose audio
alone is 264 MB of float32, on a 1 GB box. The lever: synthesize every section
concurrently as before (the network-bound part), then assemble, write and
RELEASE one section at a time, keeping only its frame count and relative cues.

⚠️ MEASURED, not reasoned: tracemalloc DOES see numpy's buffers on this numpy
(2.5.2) — a control allocating a 5.76 MB array and its 11.52 MB concatenation
under tracemalloc reported a peak of exactly their sum, 17,280,320 bytes. So a
peak-memory unit test is a real instrument here, not decoration. (An older
docstring in test_renderer_streaming_export.py said the opposite; it was
corrected in the same change.)

The bound is stated in multiples of ONE section's float32 size so it does not
depend on the fixture's absolute numbers: holding every section costs at least
``N`` sections, and one-at-a-time costs a small constant of one.
"""

from __future__ import annotations

import tracemalloc
from io import BytesIO
from unittest.mock import AsyncMock

import numpy as np
import pytest
import soundfile as sf

from app.audio.renderer import LessonRenderer, NaturalPauseCalculator
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.plugins.languages.sl.preprocessor import SlovenePreprocessor

_RATE = 24000
_PHRASE_S = 4.0
_PHRASES_PER_SECTION = 4
_SECTIONS = 6


def _wav_bytes(seconds: float) -> bytes:
    buf = BytesIO()
    frames = round(seconds * _RATE)
    sf.write(buf, np.full((frames, 1), 0.25, dtype="float32"), _RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture
def tts():
    clip = _wav_bytes(_PHRASE_S)

    async def synthesize(text, voice_id, output_path, rate="+0%", phonemes=None, speak_locale=None, enunciation=None):
        output_path.write_bytes(clip)

    mock = AsyncMock()
    mock.synthesize = synthesize
    return mock


def _lesson() -> Lesson:
    # Distinct texts per section so the render-scoped synthesis memo cannot
    # collapse them into one clip (it keys on text).
    return Lesson(
        title="Memory",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text=f"beseda{s}x{p}", voice_id="sl-SI-PetraNeural", language_code="sl")
                    for p in range(_PHRASES_PER_SECTION)
                ],
            )
            for s in range(_SECTIONS)
        ],
    )


def _renderer(tts) -> LessonRenderer:
    return LessonRenderer(
        tts=tts,
        preprocessors={"sl": SlovenePreprocessor()},
        pause_calculator=NaturalPauseCalculator(),
        delivery_codec="wav",
    )


async def _peak(tts, tmp_path, *, full: bool) -> tuple[int, int]:
    """(tracemalloc peak during render, largest section's float32 bytes)."""
    paths = [tmp_path / f"s{i}.wav" for i in range(_SECTIONS)]
    renderer = _renderer(tts)
    # WARM-UP, untraced. The first render in a process pays ~8.5 MB of one-off
    # setup (lazy imports and library init), measured: 6.60x one section on a
    # cold first call, then 1.67x on every call after it in the same process.
    # Without this the bound depends on test ORDER, not on the renderer.
    (tmp_path / "warm").mkdir()
    await renderer.render(_lesson(), None, section_paths=[tmp_path / "warm" / f"s{i}.wav" for i in range(_SECTIONS)])
    tracemalloc.start()
    try:
        await renderer.render(_lesson(), tmp_path / "full.wav" if full else None, section_paths=paths)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    section_bytes = max(sf.info(str(p)).frames for p in paths) * 4  # float32 mono
    return peak, section_bytes


class TestOneSectionAtATime:
    async def test_a_sections_only_render_holds_about_one_section(self, tts, tmp_path):
        """THE guzo.5 oracle. Six equal sections: holding them all is >= 6x one
        section; one-at-a-time must stay under 3x (the assembly of one section
        plus the phrase decodes feeding it)."""
        peak, section_bytes = await _peak(tts, tmp_path, full=False)
        assert peak < 3 * section_bytes, (
            f"peak {peak / 1e6:.1f} MB = {peak / section_bytes:.1f}x one section "
            f"({section_bytes / 1e6:.1f} MB): the render is holding more than one section"
        )

    async def test_the_bound_is_not_vacuous(self, tts, tmp_path):
        """CONTROL. The full-lesson export still has to hold every section (it
        streams them into ONE encode), so it must blow the same bound — if it
        did not, the bound above would be measuring nothing."""
        peak, section_bytes = await _peak(tts, tmp_path, full=True)
        assert peak >= _SECTIONS * section_bytes


class TestNothingElseMoved:
    async def test_sections_only_and_full_renders_give_identical_cues_and_files(self, tts, tmp_path):
        """Releasing buffers early must not move a single frame: the cue
        manifest comes from section LENGTHS, and the section files are the same
        PCM either way."""
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        a_paths = [tmp_path / "a" / f"s{i}.wav" for i in range(_SECTIONS)]
        b_paths = [tmp_path / "b" / f"s{i}.wav" for i in range(_SECTIONS)]
        cues_a = await _renderer(tts).render(_lesson(), None, section_paths=a_paths)
        cues_b = await _renderer(tts).render(_lesson(), tmp_path / "b" / "full.wav", section_paths=b_paths)

        assert cues_a == cues_b
        for pa, pb in zip(a_paths, b_paths, strict=True):
            assert pa.read_bytes() == pb.read_bytes()
