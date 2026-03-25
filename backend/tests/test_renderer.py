"""LessonRenderer integration tests."""

from unittest.mock import AsyncMock

import pytest

from app.audio.assembler import AudioAssembler
from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.preprocessing.slovene import SlovenePreprocessor
from app.audio.renderer import LessonRenderer
from app.models.lesson import Lesson, Phrase, Section, SectionType


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


@pytest.fixture
def assembler():
    return AudioAssembler()


@pytest.fixture
def renderer(assembler):
    return LessonRenderer(
        tts=None,  # replaced per-test with mock
        preprocessor=SlovenePreprocessor(),
        pause_calculator=NaturalPauseCalculator(),
        assembler=assembler,
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
    fake_audio = b"\x00" * 2000  # fake WAV-like bytes

    mock_tts = AsyncMock()

    async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
        output_path.write_bytes(fake_audio)

    mock_tts.synthesize = fake_synthesize

    asm = AudioAssembler()
    rdr = LessonRenderer(
        tts=mock_tts,
        preprocessor=SlovenePreprocessor(),
        pause_calculator=NaturalPauseCalculator(),
        assembler=asm,
    )

    output = tmp_path / "lesson.wav"
    await rdr.render(lesson, output)

    assert output.exists()
    assert output.stat().st_size > 0


@pytest.mark.asyncio
async def test_render_calls_tts_for_each_phrase(tmp_path):
    """render() calls synthesize once per phrase."""
    lesson = _minimal_lesson()
    fake_audio = b"\x00" * 1000

    synthesize_calls = []

    async def fake_synthesize(text, voice_id, output_path, rate="+0%"):
        synthesize_calls.append(text)
        output_path.write_bytes(fake_audio)

    mock_tts = AsyncMock()
    mock_tts.synthesize = fake_synthesize

    asm = AudioAssembler()
    rdr = LessonRenderer(
        tts=mock_tts,
        preprocessor=SlovenePreprocessor(),
        pause_calculator=NaturalPauseCalculator(),
        assembler=asm,
    )

    await rdr.render(lesson, tmp_path / "out.wav")

    phrase_count = sum(len(s.phrases) for s in lesson.sections)
    assert len(synthesize_calls) == phrase_count
