"""Audio port protocol compliance tests."""

from pathlib import Path

import pytest

from app.audio.ports import AudioProcessor, TTSService


class MockTTSService:
    """Mock implementation satisfying the TTSService Protocol."""

    async def synthesize(self, text: str, voice_id: str, output_path: Path, rate: str = "+0%") -> None:
        output_path.write_bytes(b"fake audio")

    async def list_voices(self, language_code: str | None = None) -> list[dict]:
        return [{"id": "sl-SI-PetraNeural", "language": "sl-SI"}]


class MockAudioProcessor:
    """Mock implementation satisfying the AudioProcessor ABC."""

    def concatenate(self, audio_bytes_list: list[bytes], silence_ms: int = 300) -> bytes:
        return b"".join(audio_bytes_list)

    def normalize(self, audio_bytes: bytes) -> bytes:
        return audio_bytes

    def add_silence(self, duration_ms: int) -> bytes:
        return b"\x00" * (duration_ms * 16)

    def trim_silence(self, audio_bytes: bytes, threshold_db: float = -40.0) -> bytes:
        return audio_bytes


def test_mock_tts_satisfies_protocol():
    mock = MockTTSService()
    assert isinstance(mock, TTSService)


def test_mock_audio_processor_satisfies_protocol():
    mock = MockAudioProcessor()
    assert isinstance(mock, AudioProcessor)


def test_audio_processor_concatenate_returns_bytes():
    proc = MockAudioProcessor()
    result = proc.concatenate([b"a", b"b", b"c"])
    assert isinstance(result, bytes)


def test_audio_processor_add_silence_returns_bytes():
    proc = MockAudioProcessor()
    result = proc.add_silence(500)
    assert isinstance(result, bytes)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_tts_list_voices_returns_list():
    tts = MockTTSService()
    voices = await tts.list_voices("sl")
    assert isinstance(voices, list)
    assert len(voices) > 0
