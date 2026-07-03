"""LessonRenderer integration tests."""

import wave
from io import BytesIO
from unittest.mock import AsyncMock

import numpy as np
import pytest
import soundfile as sf

from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.preprocessing.slovene import SlovenePreprocessor
from app.audio.renderer import LessonRenderer
from app.models.lesson import Lesson, Phrase, Section, SectionType


def _make_wav_bytes(duration_ms: int = 100, rate: int = 11025) -> bytes:
    """Generate minimal valid (silent) WAV bytes via soundfile."""
    buf = BytesIO()
    frames = round(duration_ms / 1000 * rate)
    sf.write(buf, np.zeros((frames, 1), dtype="float32"), rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _minimal_lesson() -> Lesson:
    return Lesson(
        title="Test",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl"),
                    Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl"),
                ],
            )
        ],
    )


def _make_renderer(mock_tts):
    return LessonRenderer(
        tts=mock_tts,
        preprocessors={"sl": SlovenePreprocessor()},
        pause_calculator=NaturalPauseCalculator(),
    )


class TestLessonRenderer:
    """Tests for LessonRenderer end-to-end rendering behaviour."""

    async def test_render_produces_output_file(self, tmp_path):
        """render() writes a file to the output path."""
        lesson = _minimal_lesson()
        fake_audio = _make_wav_bytes()

        mock_tts = AsyncMock()

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            output_path.write_bytes(fake_audio)

        mock_tts.synthesize = fake_synthesize

        rdr = _make_renderer(mock_tts)
        output = tmp_path / "lesson.wav"
        await rdr.render(lesson, output)

        assert output.exists()
        assert output.stat().st_size > 0

    async def test_render_produces_valid_wav(self, tmp_path):
        """render() output is a valid WAV file with audio frames."""
        lesson = _minimal_lesson()
        fake_audio = _make_wav_bytes(duration_ms=200)

        mock_tts = AsyncMock()

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            output_path.write_bytes(fake_audio)

        mock_tts.synthesize = fake_synthesize

        rdr = _make_renderer(mock_tts)
        output = tmp_path / "lesson.wav"
        await rdr.render(lesson, output)

        with wave.open(str(output), "rb") as wf:
            assert wf.getnframes() > 0
            assert wf.getnchannels() >= 1
            assert wf.getframerate() > 0

    async def test_render_opus_produces_compressed_ogg_output(self, tmp_path):
        """delivery_codec='opus' writes an Ogg-framed file far smaller than WAV."""
        lesson = _minimal_lesson()
        fake_audio = _make_wav_bytes(duration_ms=500)

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            output_path.write_bytes(fake_audio)

        mock_tts = AsyncMock()
        mock_tts.synthesize = fake_synthesize

        # WAV baseline for size comparison
        wav_rdr = LessonRenderer(
            tts=mock_tts,
            preprocessors={"sl": SlovenePreprocessor()},
            pause_calculator=NaturalPauseCalculator(),
        )
        wav_out = tmp_path / "lesson.wav"
        await wav_rdr.render(lesson, wav_out)

        opus_rdr = LessonRenderer(
            tts=mock_tts,
            preprocessors={"sl": SlovenePreprocessor()},
            pause_calculator=NaturalPauseCalculator(),
            delivery_codec="opus",
            delivery_bitrate="28k",
        )
        opus_out = tmp_path / "lesson.opus"
        await opus_rdr.render(lesson, opus_out)

        data = opus_out.read_bytes()
        assert data[:4] == b"OggS"
        assert len(data) < wav_out.stat().st_size

    async def test_render_opus_section_files_are_compressed(self, tmp_path):
        """Per-section files honour the delivery codec too."""
        lesson = _minimal_lesson()
        fake_audio = _make_wav_bytes(duration_ms=300)

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            output_path.write_bytes(fake_audio)

        mock_tts = AsyncMock()
        mock_tts.synthesize = fake_synthesize

        rdr = LessonRenderer(
            tts=mock_tts,
            preprocessors={"sl": SlovenePreprocessor()},
            pause_calculator=NaturalPauseCalculator(),
            delivery_codec="opus",
        )
        section_paths = [tmp_path / f"s{i}.opus" for i in range(len(lesson.sections))]
        await rdr.render(lesson, tmp_path / "full.opus", section_paths=section_paths)

        for sp in section_paths:
            assert sp.read_bytes()[:4] == b"OggS"

    async def test_render_calls_tts_for_each_phrase(self, tmp_path):
        """render() calls synthesize once per phrase plus once for the lesson title."""
        lesson = _minimal_lesson()
        fake_audio = _make_wav_bytes()

        synthesize_calls = []

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            synthesize_calls.append(text)
            output_path.write_bytes(fake_audio)

        mock_tts = AsyncMock()
        mock_tts.synthesize = fake_synthesize

        rdr = _make_renderer(mock_tts)
        await rdr.render(lesson, tmp_path / "out.wav")

        phrase_count = sum(len(s.phrases) for s in lesson.sections)
        assert len(synthesize_calls) == phrase_count + 1  # +1 for lesson title

    async def test_render_speaks_lesson_title_first(self, tmp_path):
        """render() synthesizes the lesson title as the very first TTS call."""
        lesson = _minimal_lesson()
        fake_audio = _make_wav_bytes()

        synthesize_calls = []

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            synthesize_calls.append((text, voice_id))
            output_path.write_bytes(fake_audio)

        mock_tts = AsyncMock()
        mock_tts.synthesize = fake_synthesize

        rdr = _make_renderer(mock_tts)
        await rdr.render(lesson, tmp_path / "out.wav")

        assert synthesize_calls[0][0] == lesson.title
        assert synthesize_calls[0][1] == lesson.narrator_voice

    async def test_render_section_skips_silence_when_pause_is_zero(self, tmp_path):
        """_render_section skips adding silence when pause_ms == 0 (81->70 False branch)."""
        from unittest.mock import MagicMock

        lesson = _minimal_lesson()
        fake_audio = _make_wav_bytes()

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            output_path.write_bytes(fake_audio)

        mock_tts = AsyncMock()
        mock_tts.synthesize = fake_synthesize

        # Use a pause calculator that always returns 0
        zero_calc = MagicMock()
        zero_calc.get_phrase_pause.return_value = 0
        zero_calc.get_section_boundary_pause.return_value = 0

        rdr = LessonRenderer(
            tts=mock_tts,
            preprocessors={"sl": SlovenePreprocessor()},
            pause_calculator=zero_calc,
        )
        output = tmp_path / "nopause.wav"
        await rdr.render(lesson, output)
        assert output.exists()

    async def test_render_passes_phrase_rate_to_tts(self, tmp_path):
        """render() uses phrase.rate rather than hardcoding '+0%'."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl", rate="-20%")],
                )
            ],
        )
        fake_audio = _make_wav_bytes()
        rate_calls = []

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            rate_calls.append((text, rate))
            output_path.write_bytes(fake_audio)

        mock_tts = AsyncMock()
        mock_tts.synthesize = fake_synthesize

        rdr = _make_renderer(mock_tts)
        await rdr.render(lesson, tmp_path / "out.wav")

        # Find the call for the phrase (not the title)
        phrase_call = next(c for c in rate_calls if c[0] == "hvala")
        assert phrase_call[1] == "-20%"

    async def test_render_raises_on_mismatched_sample_rates(self, tmp_path):
        """Mismatched sample rates fail loudly rather than silently re-speeding.

        pydub's ``+`` used to implicitly resample (``_sync``) and hide a format
        mismatch; the soundfile/numpy assembly asserts a uniform rate instead.
        """
        lesson = _minimal_lesson()

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            # Title at 22.05 kHz, phrases at 11.025 kHz → mismatch in the full mix.
            sample_rate = 22050 if text == lesson.title else 11025
            output_path.write_bytes(_make_wav_bytes(rate=sample_rate))

        mock_tts = AsyncMock()
        mock_tts.synthesize = fake_synthesize

        rdr = _make_renderer(mock_tts)
        with pytest.raises(ValueError, match="mismatched"):
            await rdr.render(lesson, tmp_path / "out.wav")


class TestLessonRendererSectionOutput:
    """Tests for per-section file generation."""

    def _make_multi_section_lesson(self):
        return Lesson(
            title="Day 1: Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="Key Phrases", voice_id="en-US-GuyNeural", language_code="en")],
                ),
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
                ),
            ],
        )

    async def test_render_produces_section_files(self, tmp_path):
        """render() writes one WAV per section when section_paths are provided."""
        lesson = self._make_multi_section_lesson()
        fake_audio = _make_wav_bytes(100)
        mock_tts = AsyncMock()

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            output_path.write_bytes(fake_audio)

        mock_tts.synthesize = fake_synthesize
        rdr = _make_renderer(mock_tts)

        full_path = tmp_path / "full.wav"
        section_paths = [tmp_path / f"s{i}.wav" for i in range(len(lesson.sections))]
        await rdr.render(lesson, full_path, section_paths=section_paths)

        for sp in section_paths:
            assert sp.exists(), f"{sp} not found"
            assert sp.stat().st_size > 0

    async def test_section_files_are_valid_wavs(self, tmp_path):
        """Each per-section file produced by render() is a valid WAV."""
        import wave

        lesson = self._make_multi_section_lesson()
        fake_audio = _make_wav_bytes(100)
        mock_tts = AsyncMock()

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            output_path.write_bytes(fake_audio)

        mock_tts.synthesize = fake_synthesize
        rdr = _make_renderer(mock_tts)

        full_path = tmp_path / "full.wav"
        section_paths = [tmp_path / f"s{i}.wav" for i in range(len(lesson.sections))]
        await rdr.render(lesson, full_path, section_paths=section_paths)

        for sp in section_paths:
            with wave.open(str(sp), "rb") as wf:
                assert wf.getnframes() > 0

    async def test_section_file_count_matches_sections(self, tmp_path):
        """render() creates exactly len(lesson.sections) section files."""
        lesson = self._make_multi_section_lesson()
        fake_audio = _make_wav_bytes(100)
        mock_tts = AsyncMock()

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            output_path.write_bytes(fake_audio)

        mock_tts.synthesize = fake_synthesize
        rdr = _make_renderer(mock_tts)

        full_path = tmp_path / "full.wav"
        section_paths = [tmp_path / f"s{i}.wav" for i in range(len(lesson.sections))]
        await rdr.render(lesson, full_path, section_paths=section_paths)

        assert len(section_paths) == len(lesson.sections)
        assert all(sp.exists() for sp in section_paths)

    async def test_full_wav_longer_than_section_files(self, tmp_path):
        """Full lesson WAV includes title + boundary silences so it's longer than any single section."""
        lesson = self._make_multi_section_lesson()
        fake_audio = _make_wav_bytes(200)
        mock_tts = AsyncMock()

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            output_path.write_bytes(fake_audio)

        mock_tts.synthesize = fake_synthesize
        rdr = _make_renderer(mock_tts)

        full_path = tmp_path / "full.wav"
        section_paths = [tmp_path / f"s{i}.wav" for i in range(len(lesson.sections))]
        await rdr.render(lesson, full_path, section_paths=section_paths)

        with wave.open(str(full_path), "rb") as wf:
            full_duration = wf.getnframes() / wf.getframerate()
        for sp in section_paths:
            with wave.open(str(sp), "rb") as wf:
                sec_duration = wf.getnframes() / wf.getframerate()
            assert full_duration > sec_duration, "Full WAV should be longer than individual section"

    async def test_render_without_section_paths_still_works(self, tmp_path):
        """render() with no section_paths kwarg still produces the full WAV (backward compat)."""
        lesson = _minimal_lesson()
        fake_audio = _make_wav_bytes(100)
        mock_tts = AsyncMock()

        async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
            output_path.write_bytes(fake_audio)

        mock_tts.synthesize = fake_synthesize
        rdr = _make_renderer(mock_tts)

        output = tmp_path / "lesson.wav"
        await rdr.render(lesson, output)  # no section_paths kwarg
        assert output.exists()


class TestLessonNarratorVoice:
    """Tests for Lesson.narrator_voice serialization."""

    def test_narrator_voice_default(self):
        """Lesson.narrator_voice defaults to en-US-GuyNeural."""
        lesson = Lesson(title="X", language_code="sl")
        assert lesson.narrator_voice == "en-US-GuyNeural"

    def test_narrator_voice_serialization(self):
        """narrator_voice roundtrips through to_json/from_json."""
        lesson = Lesson(title="X", language_code="sl", narrator_voice="en-US-AriaNeural")
        restored = Lesson.from_json(lesson.to_json())
        assert restored.narrator_voice == "en-US-AriaNeural"
