"""Tests for S3.8: edge-tts audio generation."""

from __future__ import annotations

from app.cards.media.tts import DEFAULT_VOICE, generate_tts_audio


class TestGenerateTtsAudio:
    async def test_returns_mp3_bytes_when_stream_succeeds(self, monkeypatch):
        fake_data = b"\xff\xfbfake_mp3_data"

        async def fake_stream(self):
            yield {"type": "audio", "data": fake_data[:4]}
            yield {"type": "WordBoundary", "data": "ignored"}
            yield {"type": "audio", "data": fake_data[4:]}

        monkeypatch.setattr("edge_tts.Communicate.stream", fake_stream)
        result = await generate_tts_audio("voda")
        assert result == fake_data

    async def test_returns_none_when_no_audio_chunks(self, monkeypatch):
        async def fake_stream(self):
            yield {"type": "WordBoundary", "data": "boundary"}

        monkeypatch.setattr("edge_tts.Communicate.stream", fake_stream)
        result = await generate_tts_audio("voda")
        assert result is None

    async def test_returns_none_on_exception(self, monkeypatch):
        async def fake_stream(self):
            raise RuntimeError("TTS network error")
            yield  # make it a generator

        monkeypatch.setattr("edge_tts.Communicate.stream", fake_stream)
        result = await generate_tts_audio("voda")
        assert result is None

    async def test_default_voice_is_petra(self):
        assert DEFAULT_VOICE == "sl-SI-PetraNeural"

    async def test_accepts_custom_voice(self, monkeypatch):
        used_voice: list[str] = []

        class FakeCommunicate:
            def __init__(self, text, voice):
                used_voice.append(voice)

            async def stream(self):
                yield {"type": "audio", "data": b"x"}

        monkeypatch.setattr("edge_tts.Communicate", FakeCommunicate)
        await generate_tts_audio("voda", voice="sl-SI-RokNeural")
        assert used_voice == ["sl-SI-RokNeural"]
