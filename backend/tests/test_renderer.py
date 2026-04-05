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


@pytest.mark.asyncio
async def test_render_speaks_lesson_title_first(tmp_path):
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


def test_lesson_narrator_voice_default():
    """Lesson.narrator_voice defaults to en-US-GuyNeural."""
    lesson = Lesson(title="X", language_code="sl")
    assert lesson.narrator_voice == "en-US-GuyNeural"


def test_lesson_narrator_voice_serialization():
    """narrator_voice roundtrips through to_json/from_json."""
    lesson = Lesson(title="X", language_code="sl", narrator_voice="en-US-AriaNeural")
    restored = Lesson.from_json(lesson.to_json())
    assert restored.narrator_voice == "en-US-AriaNeural"


@pytest.mark.asyncio
async def test_render_passes_phrase_rate_to_tts(tmp_path):
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
