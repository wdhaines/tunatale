"""Audio port protocol compliance tests."""

from pathlib import Path

from app.audio.ports import TTSService


class MockTTSService:
    """Mock implementation satisfying the TTSService Protocol."""

    async def synthesize(self, text: str, voice_id: str, output_path: Path, rate: str = "+0%") -> None:
        output_path.write_bytes(b"fake audio")

    async def list_voices(self, language_code: str | None = None) -> list[dict]:
        return [{"id": "sl-SI-PetraNeural", "language": "sl-SI"}]


def test_mock_tts_satisfies_protocol():
    mock = MockTTSService()
    assert isinstance(mock, TTSService)


async def test_tts_list_voices_returns_list():
    tts = MockTTSService()
    voices = await tts.list_voices("sl")
    assert isinstance(voices, list)
    assert len(voices) > 0
