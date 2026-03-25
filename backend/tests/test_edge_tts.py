"""EdgeTTS adapter tests."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.audio.edge_tts import EdgeTTSService
from app.audio.ports import TTSService


def test_edge_tts_satisfies_tts_protocol():
    svc = EdgeTTSService()
    assert isinstance(svc, TTSService)


@pytest.mark.asyncio
async def test_synthesize_writes_output_file(tmp_path):
    """synthesis creates the output file."""
    svc = EdgeTTSService()
    output = tmp_path / "out.mp3"

    mock_communicate = AsyncMock()
    mock_communicate.save = AsyncMock(side_effect=lambda p: Path(p).write_bytes(b"fake mp3 data"))

    with patch("app.audio.edge_tts.edge_tts.Communicate", return_value=mock_communicate):
        await svc.synthesize("dober dan", "sl-SI-PetraNeural", output)

    assert output.exists()


@pytest.mark.asyncio
async def test_synthesize_respects_rate_parameter(tmp_path):
    """rate parameter is passed to Communicate constructor."""
    svc = EdgeTTSService()
    output = tmp_path / "out.mp3"
    calls = []

    def capture_communicate(text, voice, rate):
        calls.append({"text": text, "voice": voice, "rate": rate})
        mock = AsyncMock()
        mock.save = AsyncMock(side_effect=lambda p: Path(p).write_bytes(b"data"))
        return mock

    with patch("app.audio.edge_tts.edge_tts.Communicate", side_effect=capture_communicate):
        await svc.synthesize("test", "sl-SI-PetraNeural", output, rate="-20%")

    assert calls[0]["rate"] == "-20%"


@pytest.mark.asyncio
async def test_synthesize_uses_cache_on_second_call(tmp_path):
    """second call with same args skips synthesis and reuses existing file."""
    svc = EdgeTTSService(cache_dir=tmp_path / "cache")
    output1 = tmp_path / "out1.mp3"

    synthesize_count = 0

    def make_communicate(text, voice, rate):
        nonlocal synthesize_count
        synthesize_count += 1
        mock = AsyncMock()

        async def fake_save(path):
            Path(path).write_bytes(b"audio data")

        mock.save = fake_save
        return mock

    with patch("app.audio.edge_tts.edge_tts.Communicate", side_effect=make_communicate):
        await svc.synthesize("dober dan", "sl-SI-PetraNeural", output1)
        # Second call: same text+voice, output path will be reused from cache
        output2 = tmp_path / "out2.mp3"
        await svc.synthesize("dober dan", "sl-SI-PetraNeural", output2)

    assert synthesize_count == 1  # Only synthesized once


@pytest.mark.asyncio
async def test_list_voices_returns_list():
    svc = EdgeTTSService()
    mock_voices = [{"ShortName": "sl-SI-PetraNeural", "Locale": "sl-SI"}]

    with patch("app.audio.edge_tts.edge_tts.list_voices", return_value=mock_voices):
        voices = await svc.list_voices("sl")

    assert isinstance(voices, list)
    assert len(voices) > 0


@pytest.mark.asyncio
async def test_list_voices_filters_by_language():
    svc = EdgeTTSService()
    mock_voices = [
        {"ShortName": "sl-SI-PetraNeural", "Locale": "sl-SI"},
        {"ShortName": "en-US-JennyNeural", "Locale": "en-US"},
    ]

    with patch("app.audio.edge_tts.edge_tts.list_voices", return_value=mock_voices):
        voices = await svc.list_voices("sl")

    assert all("sl" in v.get("Locale", "") for v in voices)


@pytest.mark.asyncio
async def test_synthesize_retries_on_transient_error(tmp_path):
    """transient errors trigger retry."""
    svc = EdgeTTSService()
    output = tmp_path / "out.mp3"
    attempt = 0

    def make_communicate(text, voice, rate):
        nonlocal attempt
        attempt += 1
        mock = AsyncMock()

        async def maybe_fail(path):
            if attempt < 2:
                raise ConnectionResetError("transient")
            Path(path).write_bytes(b"audio")

        mock.save = maybe_fail
        return mock

    with patch("app.audio.edge_tts.edge_tts.Communicate", side_effect=make_communicate):
        await svc.synthesize("test", "sl-SI-PetraNeural", output)

    assert output.exists()
    assert attempt == 2
