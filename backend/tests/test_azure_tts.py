"""AzureTTSService — the official Speech endpoint behind the TTSService port.

Network is intercepted with respx at the httpx transport layer, so nothing here
patches ``app.*``: the boundary being faked is a real socket, not one of our own
functions. That is why this file needs no ``mock_allowlist.txt`` entry.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.audio.azure_tts import AzureTTSService

SYNTH_URL = "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1"
VOICES_URL = "https://eastus.tts.speech.microsoft.com/cognitiveservices/voices/list"


def _svc(**kw):
    kw.setdefault("key", "test-key")
    kw.setdefault("region", "eastus")
    # Timing is injected, not patched: the retry ladder and the inter-request
    # pacing are real code paths here, they just run at zero delay. Patching
    # asyncio.sleep would have meant a mock_allowlist.txt entry.
    kw.setdefault("min_delay", 0)
    kw.setdefault("retry_base_delay", 0)
    return AzureTTSService(**kw)


@respx.mock
async def test_synthesize_writes_audio_bytes(tmp_path):
    """A 200 with audio bytes lands on disk at output_path."""
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"ID3-audio"))
    out = tmp_path / "nested" / "o.mp3"

    await _svc().synthesize("Dober dan", "sl-SI-PetraNeural", out)

    assert out.read_bytes() == b"ID3-audio"
    assert route.called


@respx.mock
async def test_synthesize_sends_key_and_output_format(tmp_path):
    """The subscription key and the mp3 output format travel as headers."""
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"a"))

    await _svc(key="secret-abc").synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    headers = route.calls[0].request.headers
    assert headers["Ocp-Apim-Subscription-Key"] == "secret-abc"
    # 24 kHz / 48 kbit mono mp3 is what Edge Read Aloud returns, so switching
    # providers does not change the container or bitrate of rendered audio.
    assert headers["X-Microsoft-OutputFormat"] == "audio-24khz-48kbitrate-mono-mp3"
    assert "ssml" in headers["Content-Type"]


@respx.mock
async def test_ssml_carries_voice_rate_and_derived_locale(tmp_path):
    """Voice, rate, and an xml:lang derived from the voice id all reach the body."""
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"a"))

    await _svc().synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3", rate="-20%")

    body = route.calls[0].request.content.decode()
    assert 'name="nb-NO-FinnNeural"' in body
    assert 'rate="-20%"' in body
    # Locale is sliced off the voice id rather than looked up, so no language
    # literal enters app/ (scripts/check_language_literals.py).
    assert 'xml:lang="nb-NO"' in body


@respx.mock
async def test_ssml_escapes_text(tmp_path):
    """Ampersands and angle brackets are escaped, not injected into the markup."""
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"a"))

    await _svc().synthesize('a & b <c> "d"', "sl-SI-RokNeural", tmp_path / "o.mp3")

    body = route.calls[0].request.content.decode()
    assert "&amp;" in body and "&lt;c&gt;" in body
    # The raw form must not survive anywhere in the body.
    assert "a & b" not in body


@pytest.mark.parametrize(
    "kwargs,missing",
    [({"key": ""}, "AZURE_SPEECH_KEY"), ({"region": ""}, "AZURE_SPEECH_REGION")],
)
async def test_missing_credentials_raise_loudly(tmp_path, kwargs, missing):
    """Absent credentials fail at the call site and NAME the missing setting.

    The bead's oracle: no silent fallback to unrendered audio, and no silent
    routing to the edge provider. The message has to say which setting is
    missing, or the failure is a mystery at 2am.
    """
    with pytest.raises(RuntimeError, match=missing):
        await _svc(**kwargs).synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert not (tmp_path / "o.mp3").exists()


@respx.mock
async def test_cache_hit_skips_the_network(tmp_path):
    """A second identical render is served from cache without a request."""
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"cached-bytes"))
    svc = _svc(cache_dir=tmp_path / "cache")

    await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "one.mp3")
    await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "two.mp3")

    assert route.call_count == 1
    assert (tmp_path / "two.mp3").read_bytes() == b"cached-bytes"


@respx.mock
async def test_cache_key_separates_voice_and_rate(tmp_path):
    """Same text at a different voice or rate is a different cache entry."""
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"a"))
    svc = _svc(cache_dir=tmp_path / "cache")

    await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "1.mp3")
    await svc.synthesize("hei", "nb-NO-PernilleNeural", tmp_path / "2.mp3")
    await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "3.mp3", rate="-20%")

    assert route.call_count == 3


@respx.mock
async def test_transient_error_is_retried_then_succeeds(tmp_path):
    """A 503 is retried; the eventual 200 is what gets written."""
    route = respx.post(SYNTH_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, content=b"second-try"),
        ]
    )

    await _svc().synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.call_count == 2
    assert (tmp_path / "o.mp3").read_bytes() == b"second-try"


@respx.mock
async def test_connect_error_is_retried(tmp_path):
    """A transport-level failure is transient too, not a hard stop."""
    route = respx.post(SYNTH_URL).mock(side_effect=[httpx.ConnectError("boom"), httpx.Response(200, content=b"ok")])

    await _svc().synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.call_count == 2


@respx.mock
async def test_retry_exhaustion_raises_and_names_the_status(tmp_path, caplog):
    """Persistent failure raises rather than leaving a silently empty file."""
    respx.post(SYNTH_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        await _svc().synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert not (tmp_path / "o.mp3").exists()
    assert "503" in caplog.text


@respx.mock
async def test_auth_failure_is_not_retried(tmp_path):
    """A 401 is a config error, not a transient one — fail fast and say so.

    Retrying a bad key three times just delays the diagnosis and burns quota.
    """
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(401))

    with pytest.raises(RuntimeError, match="401"):
        await _svc().synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.call_count == 1


@respx.mock
async def test_list_voices_filters_by_language(tmp_path):
    """list_voices mirrors the edge adapter's filtering contract."""
    respx.get(VOICES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"ShortName": "nb-NO-FinnNeural", "Locale": "nb-NO"},
                {"ShortName": "sl-SI-PetraNeural", "Locale": "sl-SI"},
            ],
        )
    )

    everything = await _svc().list_voices()
    filtered = await _svc().list_voices("sl-SI")

    assert len(everything) == 2
    assert [v["ShortName"] for v in filtered] == ["sl-SI-PetraNeural"]


@respx.mock
async def test_list_voices_requires_credentials():
    """The credential check guards voice listing too, not just synthesis."""
    with pytest.raises(RuntimeError, match="AZURE_SPEECH_KEY"):
        await _svc(key="").list_voices()
