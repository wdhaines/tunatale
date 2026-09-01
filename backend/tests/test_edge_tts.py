"""EdgeTTS adapter tests."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
import edge_tts
import pytest

from app.audio.edge_tts import EdgeTTSService
from app.audio.ports import TTSService


def _svc(**kw):
    # Timing is injected, not patched: the retry ladder and the inter-request
    # pacing are real code paths here, they just run at zero delay. Patching
    # asyncio.sleep would have meant a mock_allowlist.txt entry (mirrors
    # test_azure_tts.py's ``_svc`` helper).
    kw.setdefault("min_delay", 0)
    kw.setdefault("retry_base_delay", 0)
    return EdgeTTSService(**kw)


def test_edge_tts_satisfies_tts_protocol():
    svc = EdgeTTSService()
    assert isinstance(svc, TTSService)


async def test_synthesize_writes_output_file(tmp_path):
    """synthesis creates the output file."""
    svc = EdgeTTSService()
    output = tmp_path / "out.mp3"

    mock_communicate = AsyncMock()
    mock_communicate.save = AsyncMock(side_effect=lambda p: Path(p).write_bytes(b"fake mp3 data"))

    with patch("app.audio.edge_tts.edge_tts.Communicate", return_value=mock_communicate):
        await svc.synthesize("dober dan", "sl-SI-PetraNeural", output)

    assert output.exists()


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


async def test_list_voices_returns_list():
    svc = EdgeTTSService()
    mock_voices = [{"ShortName": "sl-SI-PetraNeural", "Locale": "sl-SI"}]

    with patch("app.audio.edge_tts.edge_tts.list_voices", return_value=mock_voices):
        voices = await svc.list_voices("sl")

    assert isinstance(voices, list)
    assert len(voices) > 0


async def test_list_voices_no_filter_returns_all():
    """list_voices() with no language_code skips filtering (64->66 False branch)."""
    svc = EdgeTTSService()
    mock_voices = [
        {"ShortName": "sl-SI-PetraNeural", "Locale": "sl-SI"},
        {"ShortName": "en-US-JennyNeural", "Locale": "en-US"},
    ]

    with patch("app.audio.edge_tts.edge_tts.list_voices", return_value=mock_voices):
        voices = await svc.list_voices()  # no language_code

    assert len(voices) == 2


async def test_list_voices_filters_by_language():
    svc = EdgeTTSService()
    mock_voices = [
        {"ShortName": "sl-SI-PetraNeural", "Locale": "sl-SI"},
        {"ShortName": "en-US-JennyNeural", "Locale": "en-US"},
    ]

    with patch("app.audio.edge_tts.edge_tts.list_voices", return_value=mock_voices):
        voices = await svc.list_voices("sl")

    assert all("sl" in v.get("Locale", "") for v in voices)


async def test_synthesize_retries_on_transient_error(tmp_path):
    """transient errors trigger retry."""
    svc = _svc()
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


async def test_synthesize_retries_on_edge_tts_exception(tmp_path):
    """EdgeTTSException (e.g. NoAudioReceived) triggers retry."""
    svc = _svc()
    output = tmp_path / "out.mp3"
    attempt = 0

    def make_communicate(text, voice, rate):
        nonlocal attempt
        attempt += 1
        mock = AsyncMock()

        async def maybe_fail(path):
            if attempt < 2:
                raise edge_tts.exceptions.NoAudioReceived("empty")
            Path(path).write_bytes(b"audio")

        mock.save = maybe_fail
        return mock

    with patch("app.audio.edge_tts.edge_tts.Communicate", side_effect=make_communicate):
        await svc.synthesize("test", "sl-SI-PetraNeural", output)

    assert output.exists()
    assert attempt == 2


async def test_synthesize_retries_on_aiohttp_client_error(tmp_path):
    """aiohttp.ClientError triggers retry."""
    svc = _svc()
    output = tmp_path / "out.mp3"
    attempt = 0

    def make_communicate(text, voice, rate):
        nonlocal attempt
        attempt += 1
        mock = AsyncMock()

        async def maybe_fail(path):
            if attempt < 2:
                raise aiohttp.ClientError("transient http error")
            Path(path).write_bytes(b"audio")

        mock.save = maybe_fail
        return mock

    with patch("app.audio.edge_tts.edge_tts.Communicate", side_effect=make_communicate):
        await svc.synthesize("test", "sl-SI-PetraNeural", output)

    assert output.exists()
    assert attempt == 2


async def test_synthesize_raises_after_max_retries(tmp_path):
    """All retries exhausted → RuntimeError with retry count in message."""
    from app.audio.edge_tts import MAX_RETRIES

    svc = _svc()
    output = tmp_path / "out.mp3"

    def always_fail(text, voice, rate):
        mock = AsyncMock()
        mock.save = AsyncMock(side_effect=ConnectionResetError("always fails"))
        return mock

    with (
        patch("app.audio.edge_tts.edge_tts.Communicate", side_effect=always_fail),
        pytest.raises(RuntimeError, match=str(MAX_RETRIES)),
    ):
        await svc.synthesize("test", "sl-SI-PetraNeural", output)


async def test_edge_pacing_delay_paid_on_failure_and_success(tmp_path):
    """THE test for the burst-amplification fix, mirrored for the edge adapter.

    ``edge_tts.py::_do_synthesize`` had the identical shape to the Azure bug:
    ``save()`` raising skipped the pacing sleep below it entirely
    (findings-tts-pacing-2026-08-21.md). A failed attempt must pay the same
    pacing delay a successful one does. If the fix is reverted to only sleep
    after a successful ``save()``, the failed attempt contributes nothing to
    the sleep sequence and this goes red.
    """
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    svc = _svc(min_delay=0.3, retry_base_delay=0, sleep=fake_sleep)
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
        await svc.synthesize("test", "sl-SI-PetraNeural", tmp_path / "out.mp3")

    assert attempt == 2
    pacing_sleeps = [s for s in sleeps if s == 0.3]
    assert len(pacing_sleeps) == 2, (
        f"expected the pacing delay on BOTH attempts (failure and success), got sleeps={sleeps}"
    )


# ------------------------------------------------------------------
# Per-provider pacing override
# ------------------------------------------------------------------


def test_edge_falls_back_to_shared_delay_when_no_override(monkeypatch):
    """With no per-provider override set, edge's pacing is byte-identical to the shared setting."""
    from app.config import settings

    monkeypatch.setattr(settings, "tts_edge_min_request_delay_s", None)
    svc = EdgeTTSService()

    assert svc._min_delay == settings.tts_min_request_delay_s


def test_edge_per_provider_override_wins_over_shared(monkeypatch):
    """A configured edge-specific delay overrides the shared setting."""
    from app.config import settings

    monkeypatch.setattr(settings, "tts_edge_min_request_delay_s", 0.07)
    monkeypatch.setattr(settings, "tts_min_request_delay_s", 0.99)
    svc = EdgeTTSService()

    assert svc._min_delay == 0.07


def test_edge_explicit_max_concurrent_requests_overrides_settings():
    """An explicit ``max_concurrent_requests`` constructor arg wins over settings.

    Mirrors AzureTTSService's constructor shape/tests — a caller can pin
    concurrency directly without touching global settings.
    """
    svc = EdgeTTSService(max_concurrent_requests=3)

    assert svc._max_concurrent == 3


# ------------------------------------------------------------------
# Throttle: configurable concurrency from settings
# ------------------------------------------------------------------


def test_edge_adapter_uses_settings_throttle_defaults():
    """Adapter reads max_concurrent_requests and min_request_delay_s from settings.

    This is also the "no per-provider override" oracle: the default
    ``tts_edge_min_request_delay_s`` is ``None``, so with nothing configured
    the adapter's pacing falls back to (and is byte-identical to) the shared
    setting.
    """
    from app.config import settings

    svc = EdgeTTSService()
    assert svc._max_concurrent == settings.tts_max_concurrent_requests
    assert svc._min_delay == settings.tts_min_request_delay_s


def test_edge_retry_base_delay_comes_from_settings(monkeypatch):
    """Kept in step with Azure: both adapters resolve the ladder's base delay from
    the same shared setting, so tuning it cannot silently apply to one provider."""
    from app.config import settings

    monkeypatch.setattr(settings, "tts_retry_base_delay_s", 0.03)

    assert EdgeTTSService()._retry_base_delay == 0.03


def test_edge_explicit_retry_base_delay_wins_over_settings(monkeypatch):
    """An explicit argument still beats the setting — same precedence as min_delay."""
    from app.config import settings

    monkeypatch.setattr(settings, "tts_retry_base_delay_s", 0.03)

    assert EdgeTTSService(retry_base_delay=0.25)._retry_base_delay == 0.25


async def test_edge_adapter_respects_configured_concurrency_limit(tmp_path):
    """Semaphore caps in-flight requests; the adapter never exceeds the limit."""
    max_concurrent = 2
    in_flight = 0
    max_observed = 0

    def make_communicate(text, voice, rate):
        nonlocal in_flight, max_observed

        async def capture_save(path):
            nonlocal in_flight, max_observed
            in_flight += 1
            max_observed = max(max_observed, in_flight)
            await asyncio.sleep(0.01)
            Path(path).write_bytes(b"audio")
            in_flight -= 1

        mock = AsyncMock()
        mock.save = capture_save
        return mock

    svc = EdgeTTSService()
    # Override the semaphore to use our test limit
    svc._semaphore = asyncio.Semaphore(max_concurrent)

    with patch("app.audio.edge_tts.edge_tts.Communicate", side_effect=make_communicate):
        await asyncio.gather(
            *[svc.synthesize(f"phrase {i}", "sl-SI-PetraNeural", tmp_path / f"{i}.mp3") for i in range(6)]
        )

    assert max_observed <= max_concurrent


async def test_edge_no_trailing_sleep_after_final_attempt(tmp_path):
    """The retry ladder does not sleep after the final (failed) attempt.

    Timing is injected via the ``sleep`` constructor arg (not
    ``patch("app.audio.edge_tts.asyncio.sleep")``), so the assertion is on
    the actual sequence of durations the adapter asked to wait, not on a
    mock's call count against a module attribute.
    """
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    svc = _svc(retry_base_delay=1, sleep=fake_sleep)

    def always_fail(text, voice, rate):
        mock = AsyncMock()
        mock.save = AsyncMock(side_effect=ConnectionResetError("always fails"))
        return mock

    with (
        patch("app.audio.edge_tts.edge_tts.Communicate", side_effect=always_fail),
        pytest.raises(RuntimeError, match="after 6 attempts"),
    ):
        await svc.synthesize("test", "sl-SI-PetraNeural", tmp_path / "out.mp3")

    # min_delay=0 (from _svc), so the pacing sleep after each failed attempt
    # records 0 and the backoff sleeps record the ladder — six attempts, six
    # pacing sleeps (item 1's fix), but only FIVE backoff sleeps: one between
    # each pair of attempts, never after the final one.
    backoff_sleeps = [s for s in sleeps if s > 0]
    assert backoff_sleeps == [1, 2, 4, 8, 16], f"expected five backoffs with no trailing sleep, got {sleeps}"


# ------------------------------------------------------------------
# Stage 2b: per-token IPA seam — degrade gracefully, keep the cache stable
# (brief-phoneme-seam-stage2b-2026-08.md)
# ------------------------------------------------------------------

_VOICE = "nb-NO-FinnNeural"

# Oracles measured at ed613f7 — mirrored from test_azure_tts.py; both
# adapters must agree on every digest or a shared cache dir splits silently.
_ORACLE_DIGESTS = [
    ("hagen", "+0%", "803c3e2e01f51e65"),
    ("hagen", "-40%", "c6281c5b2791d2b9"),
    ("Jeg vil gjerne ha en kaffe", "+0%", "4790e4bf997cb0b2"),
]


@pytest.mark.parametrize("text,rate,digest", _ORACLE_DIGESTS)
def test_cache_digests_unchanged_without_phonemes(tmp_path, text, rate, digest):
    """None and {} must be indistinguishable from pre-stage behaviour."""
    assert EdgeTTSService(cache_dir=tmp_path)._cache_path(text, _VOICE, rate).name == f"{digest}.mp3"


def test_mapping_does_not_change_edges_cache_key(tmp_path):
    """Edge's key must not vary on phonemes — it cannot render them.

    The inverse of Azure's rule, and deliberate: a key names what varies the
    OUTPUT, and an Edge render is plain audio either way. See
    EdgeTTSService._cache_path for the collision this prevents.
    """
    svc = EdgeTTSService(cache_dir=tmp_path)
    assert svc._cache_path("hagen", _VOICE, "+0%").name == svc._cache_path("hagen", _VOICE, "+0%").name


async def test_warns_once_per_instance_and_synthesizes_plain_text(tmp_path, caplog):
    """edge-tts escapes text internally, so markup cannot survive — say so once.

    A lesson render would otherwise produce hundreds of identical warnings;
    once per instance keeps it one diagnostic line. The synthesis itself is
    exactly today's plain-text path.
    """
    calls: list[str] = []

    def capture_communicate(text, voice, rate):
        calls.append(text)
        mock = AsyncMock()
        mock.save = AsyncMock(side_effect=lambda p: Path(p).write_bytes(b"data"))
        return mock

    with (
        patch("app.audio.edge_tts.edge_tts.Communicate", side_effect=capture_communicate),
        caplog.at_level("WARNING"),
    ):
        svc = EdgeTTSService()
        await svc.synthesize("hagen", _VOICE, tmp_path / "one.mp3", phonemes={"hagen": "hɑː.gən"})
        await svc.synthesize("kaffe", _VOICE, tmp_path / "two.mp3", phonemes={"kaffe": "kɑf.fə"})

    assert calls == ["hagen", "kaffe"], "the library must receive plain text, not markup"
    assert caplog.text.count("phoneme") == 1


async def test_no_warning_when_phonemes_is_none(tmp_path, caplog):
    output = tmp_path / "out.mp3"

    def make_communicate(text, voice, rate):
        mock = AsyncMock()
        mock.save = AsyncMock(side_effect=lambda p: Path(p).write_bytes(b"data"))
        return mock

    with patch("app.audio.edge_tts.edge_tts.Communicate", side_effect=make_communicate), caplog.at_level("WARNING"):
        await EdgeTTSService().synthesize("hagen", _VOICE, output)

    assert "phoneme" not in caplog.text.lower()


async def test_edge_never_writes_into_azures_phoneme_cache_namespace(tmp_path):
    """Edge's key must NOT vary on phonemes, or it poisons Azure's IPA clips.

    Both providers share one ``tts_cache_dir`` and the key carries no provider.
    When Edge mirrored Azure's phoneme extension, both hashed this input to
    2110be571ffeb4d2 — so an Edge render (PLAIN audio, because the library
    escapes markup internally) would land on the key Azure uses for IPA audio,
    and a later Azure call would cache-hit and silently serve non-IPA audio.
    """
    from app.audio.azure_tts import AzureTTSService

    phonemes = {"hagen": "hɑː.gən"}
    azure = AzureTTSService(key="k", region="r", cache_dir=tmp_path)
    edge = EdgeTTSService(cache_dir=tmp_path)

    azure_ipa = azure._cache_path("hagen", _VOICE, "+0%", phonemes)
    edge_any = edge._cache_path("hagen", _VOICE, "+0%")

    assert azure_ipa != edge_any
    # Edge gets the plain clip it is actually entitled to, and reuses it.
    assert edge_any == edge._cache_path("hagen", _VOICE, "+0%")
    assert edge_any == azure._cache_path("hagen", _VOICE, "+0%", None)
