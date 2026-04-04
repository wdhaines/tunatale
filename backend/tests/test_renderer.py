"""LessonRenderer integration tests."""

import wave
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from pydub import AudioSegment

from app.audio.assembler import AudioAssembler
from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.preprocessing.slovene import SlovenePreprocessor
from app.audio.renderer import LessonRenderer
from app.models.lesson import Lesson, Phrase, Section, SectionType


def _make_wav_bytes(duration_ms: int = 100) -> bytes:
    """Generate minimal valid WAV bytes using pydub (no ffmpeg needed for WAV)."""
    buf = BytesIO()
    AudioSegment.silent(duration=duration_ms).export(buf, format="wav")
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
        preprocessor=SlovenePreprocessor(),
        pause_calculator=NaturalPauseCalculator(),
    )


def test_audio_assembler_satisfies_protocol():
    from app.audio.ports import AudioProcessor

    asm = AudioAssembler()
    assert isinstance(asm, AudioProcessor)


def test_assembler_concatenate_returns_bytes():
    asm = AudioAssembler()
    result = asm.concatenate([b"chunk1", b"chunk2"])
    assert isinstance(result, bytes)
    assert len(result) > 0


def test_assembler_add_silence_returns_bytes():
    asm = AudioAssembler()
    silence = asm.add_silence(500)
    assert isinstance(silence, bytes)
    assert len(silence) > 0


def test_assembler_normalize_returns_bytes():
    asm = AudioAssembler()
    result = asm.normalize(b"\x00" * 1000)
    assert isinstance(result, bytes)


def test_assembler_trim_silence_returns_bytes():
    asm = AudioAssembler()
    result = asm.trim_silence(b"\x00" * 1000)
    assert isinstance(result, bytes)


@pytest.mark.asyncio
async def test_render_produces_output_file(tmp_path):
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


@pytest.mark.asyncio
async def test_render_produces_valid_wav(tmp_path):
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


@pytest.mark.asyncio
async def test_render_calls_tts_for_each_phrase(tmp_path):
    """render() calls synthesize once per phrase."""
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
    assert len(synthesize_calls) == phrase_count
