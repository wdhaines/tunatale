"""Card-pronunciation TTS — now provider-agnostic (was S3.8: edge-tts only).

``generate_tts_audio`` renders through whichever adapter TTS_PROVIDER selects,
so these exercise it under BOTH providers rather than reaching for edge_tts
directly. The azure leg uses respx (transport-level); the edge leg patches
``edge_tts.Communicate``, the allowlisted network boundary.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.cards.media.tts import generate_tts_audio
from app.config import settings
from app.languages import get_tts_voice

SYNTH_URL = "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1"
_ML_VOICE = "en-US-EmmaMultilingualNeural"


@pytest.fixture
def azure(monkeypatch):
    """Select the Azure provider with usable credentials."""
    monkeypatch.setattr(settings, "tts_provider", "azure")
    monkeypatch.setattr(settings, "azure_speech_key", "test-key")
    monkeypatch.setattr(settings, "azure_speech_region", "eastus")


@pytest.fixture
def edge(monkeypatch):
    monkeypatch.setattr(settings, "tts_provider", "edge")


class TestGenerateTtsAudioAzure:
    @respx.mock
    async def test_returns_mp3_bytes(self, azure):
        respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"\xff\xfbfake_mp3"))
        assert await generate_tts_audio("voda") == b"\xff\xfbfake_mp3"

    @respx.mock
    async def test_returns_none_when_response_is_empty(self, azure):
        """Empty audio is "no audio", not a zero-byte file handed to Anki."""
        respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b""))
        assert await generate_tts_audio("voda") is None

    @respx.mock
    async def test_returns_none_on_error_but_logs_it(self, azure, caplog):
        """Failure stays non-fatal for callers, but must not be silent."""
        respx.post(SYNTH_URL).mock(return_value=httpx.Response(401))
        assert await generate_tts_audio("voda") is None
        assert "TTS generation failed" in caplog.text

    async def test_missing_key_is_reported_not_swallowed(self, monkeypatch, caplog):
        """The conftest pins the key empty; that must be diagnosable.

        Without the log line this looks identical to "the text had no audio",
        which is how an unset key hides as mysteriously silent cards.
        """
        monkeypatch.setattr(settings, "tts_provider", "azure")
        monkeypatch.setattr(settings, "azure_speech_key", "")
        assert await generate_tts_audio("voda") is None
        assert "AZURE_SPEECH_KEY" in caplog.text

    @respx.mock
    async def test_accepts_custom_voice(self, azure):
        route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"x"))
        await generate_tts_audio("voda", voice="sl-SI-RokNeural")
        assert 'name="sl-SI-RokNeural"' in route.calls[0].request.content.decode()


class TestGenerateTtsAudioEdge:
    async def test_returns_mp3_bytes_when_stream_succeeds(self, edge, monkeypatch):
        fake_data = b"\xff\xfbfake_mp3_data"

        async def fake_stream(self):
            yield {"type": "audio", "data": fake_data[:4]}
            yield {"type": "WordBoundary", "data": "ignored"}
            yield {"type": "audio", "data": fake_data[4:]}

        monkeypatch.setattr("edge_tts.Communicate.stream", fake_stream)
        assert await generate_tts_audio("voda") == fake_data

    async def test_returns_none_on_exception(self, edge, monkeypatch):
        async def fake_stream(self):
            raise RuntimeError("TTS network error")
            yield  # make it a generator

        monkeypatch.setattr("edge_tts.Communicate.stream", fake_stream)
        assert await generate_tts_audio("voda") is None

    async def test_accepts_custom_voice(self, edge, monkeypatch):
        used_voice: list[str] = []

        class FakeCommunicate:
            def __init__(self, text, voice, rate="+0%"):
                used_voice.append(voice)

            async def save(self, path):
                from pathlib import Path

                Path(path).write_bytes(b"x")

        monkeypatch.setattr("edge_tts.Communicate", FakeCommunicate)
        await generate_tts_audio("voda", voice="sl-SI-RokNeural")
        assert used_voice == ["sl-SI-RokNeural"]


class TestSpeakLocaleOnTheDefaultVoice:
    """The locale is declared only for the voice this module resolves itself."""

    @respx.mock
    async def test_a_foreign_default_voice_declares_the_target_locale(self, azure, monkeypatch):
        """The case this exists for: a language whose role-1 voice is not native.

        Both wired languages have a native female-1, so the wrapper is never
        emitted here today — this swaps in the shape a future language would
        have (sl-SI already needs Multilingual voices for two of four roles) and
        checks the declaration actually reaches the wire.
        """
        from app.languages import get_language

        monkeypatch.setitem(get_language(settings.target_language).tts_voice_map, "female-1", _ML_VOICE)
        route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"\xff\xfbmp3"))

        await generate_tts_audio("voda")

        assert '<lang xml:lang="sl-SI">' in route.calls[0].request.content.decode()

    @respx.mock
    async def test_a_caller_supplied_voice_declares_nothing(self, azure):
        """A caller passing its own voice owns the locale question — it may be
        handing us English text with an English voice, where declaring the
        target locale is worse than declaring nothing."""
        route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"\xff\xfbmp3"))

        await generate_tts_audio("voda", voice=_ML_VOICE)

        assert "<lang" not in route.calls[0].request.content.decode()

    @respx.mock
    async def test_a_native_default_voice_still_sends_the_old_ssml(self, azure):
        """The 331 MB guard at this seam: the real default is native, so the
        posted SSML — and therefore the cache key — must be untouched."""
        route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"\xff\xfbmp3"))

        await generate_tts_audio("voda")

        assert "<lang" not in route.calls[0].request.content.decode()


async def test_default_voice_uses_settings_language():
    voice = get_tts_voice(settings.target_language)
    assert voice == "sl-SI-PetraNeural"
