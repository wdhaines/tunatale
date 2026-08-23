"""AzureTTSService — the official Speech endpoint behind the TTSService port.

Network is intercepted with respx at the httpx transport layer, so nothing here
patches ``app.*``: the boundary being faked is a real socket, not one of our own
functions. That is why this file needs no ``mock_allowlist.txt`` entry.
"""

from __future__ import annotations

import asyncio

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


# ------------------------------------------------------------------
# Throttle: configurable concurrency from settings
# ------------------------------------------------------------------


@respx.mock
async def test_adapter_uses_settings_throttle_defaults(tmp_path):
    """Adapter reads max_concurrent_requests and min_request_delay_s from settings."""
    from app.config import settings

    svc = AzureTTSService()
    assert svc._max_concurrent == settings.tts_max_concurrent_requests
    assert svc._min_delay == settings.tts_min_request_delay_s


@respx.mock
async def test_adapter_respects_configured_concurrency_limit(tmp_path):
    """OBSERVED in-flight requests never exceed the configured limit.

    Deliberately asserts on concurrency measured inside the real request path
    rather than on ``_semaphore._value``. A semaphore that is constructed with
    the right number but never *held* satisfies the value check while leaving
    the burst bug — the whole defect this guards — completely intact. Drilled
    2026-08-19: dropping the ``async with self._semaphore`` in
    ``_do_synthesize`` leaves every value-based assertion green.
    """
    max_concurrent = 2
    in_flight = 0
    observed_max = 0

    async def _tracking(request):
        nonlocal in_flight, observed_max
        in_flight += 1
        observed_max = max(observed_max, in_flight)
        # Yield with the slot still held, so genuine overlap is observable.
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, content=b"audio")

    respx.post(SYNTH_URL).mock(side_effect=_tracking)

    svc = _svc(max_concurrent_requests=max_concurrent)
    await asyncio.gather(
        *[svc.synthesize(f"phrase {i}", "nb-NO-FinnNeural", tmp_path / f"{i}.mp3") for i in range(max_concurrent * 6)]
    )

    assert observed_max > 0, "no request reached the transport — the test proves nothing"
    assert observed_max <= max_concurrent, f"adapter allowed {observed_max} in flight, limit is {max_concurrent}"


@respx.mock
async def test_429_retries_with_longer_backoff(tmp_path):
    """A429 is retried (not fatal like 401/403) and reaches eventual success."""
    route = respx.post(SYNTH_URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(200, content=b"ok"),
        ]
    )

    await _svc().synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.call_count == 3
    assert (tmp_path / "o.mp3").read_bytes() == b"ok"


@respx.mock
async def test_401_is_not_retried(tmp_path):
    """A 401 is fatal — fail immediately, no retry."""
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(401))

    with pytest.raises(RuntimeError, match="401"):
        await _svc().synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.call_count == 1


@respx.mock
async def test_403_is_not_retried(tmp_path):
    """A 403 is fatal — fail immediately, no retry."""
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(403))

    with pytest.raises(RuntimeError, match="403"):
        await _svc().synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.call_count == 1


@respx.mock
async def test_pacing_delay_paid_on_429_and_on_success(tmp_path):
    """THE test for the burst-amplification fix.

    A 429 must pay its pacing delay before the semaphore slot frees, exactly
    like a success does — otherwise a throttled request exits instantly and
    the next request piles straight into the same window
    (findings-tts-pacing-2026-08-21.md). If ``_do_synthesize`` only sleeps on
    the success path (the bug), the failed attempt contributes nothing to the
    sleep sequence and this goes red.
    """
    route = respx.post(SYNTH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"date": "x"}, content=b"Downstream Service Throttled"),
            httpx.Response(200, content=b"ok"),
        ]
    )
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    svc = _svc(min_delay=0.3, retry_base_delay=0, sleep=fake_sleep)
    await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.call_count == 2
    pacing_sleeps = [s for s in sleeps if s == 0.3]
    assert len(pacing_sleeps) == 2, f"expected the pacing delay on BOTH attempts (429 and success), got sleeps={sleeps}"


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
async def test_fatal_status_records_zero_sleeps(tmp_path, status):
    """401/403 raise immediately, naming the status, and pay NO pacing delay.

    Retrying (or pacing for a retry) cannot fix bad credentials — the fix for
    item 1 must not accidentally make every attempt sleep unconditionally.
    """
    respx.post(SYNTH_URL).mock(return_value=httpx.Response(status))
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    svc = _svc(min_delay=0.3, retry_base_delay=0.3, sleep=fake_sleep)
    with pytest.raises(RuntimeError, match=str(status)):
        await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert sleeps == [], f"a fatal status must record ZERO sleeps, got {sleeps}"


@respx.mock
async def test_429_headers_and_body_logged_once_per_instance(tmp_path, caplog):
    """The first 429 dumps headers+body; a second 429 on the same instance does not.

    The body is the only signal Azure's istio-envoy throttle gives
    (findings-tts-pacing-2026-08-21.md) — it must reach the log, but a burst
    of dozens of 429s must not repeat the dump dozens of times.
    """
    body = b"Downstream Service Throttled. Please try again in some time"
    route = respx.post(SYNTH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"server": "istio-envoy"}, content=body),
            httpx.Response(200, content=b"ok"),
            httpx.Response(429, headers={"server": "istio-envoy"}, content=body),
            httpx.Response(200, content=b"ok"),
        ]
    )
    with caplog.at_level("WARNING"):
        svc = _svc()
        await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o1.mp3")
        await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o2.mp3")

    assert route.call_count == 4
    assert caplog.text.count("Downstream Service Throttled") == 1
    assert "istio-envoy" in caplog.text


@respx.mock
async def test_retry_after_seconds_exceeding_ladder_wins(tmp_path):
    """Retry-After: 5, when it exceeds the ladder value, wins — max(header, ladder)."""
    route = respx.post(SYNTH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "5"}, content=b"throttled"),
            httpx.Response(200, content=b"ok"),
        ]
    )
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    # ladder at attempt 0 = retry_base_delay * 4 * 2**0 = 0.5*4 = 2.0 < 5
    svc = _svc(min_delay=0, retry_base_delay=0.5, sleep=fake_sleep)
    await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.call_count == 2
    assert sleeps[1] == pytest.approx(5.0)


@respx.mock
async def test_retry_after_seconds_below_ladder_is_ignored(tmp_path):
    """Retry-After: 0.1, below the ladder, does not shorten the wait."""
    route = respx.post(SYNTH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0.1"}, content=b"throttled"),
            httpx.Response(200, content=b"ok"),
        ]
    )
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    # ladder at attempt 0 = 0.5*4*2**0 = 2.0 > 0.1
    svc = _svc(min_delay=0, retry_base_delay=0.5, sleep=fake_sleep)
    await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.call_count == 2
    assert sleeps[1] == pytest.approx(2.0)


@respx.mock
async def test_retry_after_http_date_waits_the_right_delta(tmp_path):
    """Retry-After as an HTTP-date (the other RFC 9110 wire form) is honoured."""
    import datetime
    from email.utils import format_datetime

    target = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=10)
    route = respx.post(SYNTH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": format_datetime(target, usegmt=True)}, content=b"throttled"),
            httpx.Response(200, content=b"ok"),
        ]
    )
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    # ladder at attempt 0 = 0.1*4 = 0.4, well under the ~10s date delta.
    svc = _svc(min_delay=0, retry_base_delay=0.1, sleep=fake_sleep)
    await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.call_count == 2
    # abs must exceed 1.0, and that is not slack — it is what the wire format
    # costs. An HTTP-date has ONE-SECOND resolution, so `format_datetime` throws
    # away `target`'s fractional second; the parsed delta is therefore up to a
    # full second short, plus however long the request round trip took. At
    # abs=1.0 the tolerance was exactly the truncation with zero margin left,
    # and CI duly failed at 8.995752 (= 10 - 0.9998 truncation - 0.0044 elapsed)
    # on 2026-08-23 whenever the clock happened to sit near a second boundary.
    assert sleeps[1] == pytest.approx(10.0, abs=2.0)


@respx.mock
async def test_retry_after_http_date_without_timezone_is_treated_as_utc(tmp_path):
    """An HTTP-date with no zone offset is naive; RFC 9110 dates are always GMT.

    ``email.utils.parsedate_to_datetime`` returns a naive ``datetime`` for a
    date string with no zone component — this pins that ``_parse_retry_after``
    fills in UTC rather than comparing naive-to-aware and raising.
    """
    import datetime

    target = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=10)
    # No "GMT"/zone suffix — the wire form email.utils treats as tz-naive.
    naive_date = target.strftime("%a, %d %b %Y %H:%M:%S")
    route = respx.post(SYNTH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": naive_date}, content=b"throttled"),
            httpx.Response(200, content=b"ok"),
        ]
    )
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    svc = _svc(min_delay=0, retry_base_delay=0.1, sleep=fake_sleep)
    await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.call_count == 2
    # abs must exceed 1.0, and that is not slack — it is what the wire format
    # costs. An HTTP-date has ONE-SECOND resolution, so `format_datetime` throws
    # away `target`'s fractional second; the parsed delta is therefore up to a
    # full second short, plus however long the request round trip took. At
    # abs=1.0 the tolerance was exactly the truncation with zero margin left,
    # and CI duly failed at 8.995752 (= 10 - 0.9998 truncation - 0.0044 elapsed)
    # on 2026-08-23 whenever the clock happened to sit near a second boundary.
    assert sleeps[1] == pytest.approx(10.0, abs=2.0)


@respx.mock
async def test_retry_after_malformed_is_ignored_not_raised(tmp_path):
    """A nonsense Retry-After value falls back to the ladder and never raises."""
    route = respx.post(SYNTH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "soon"}, content=b"throttled"),
            httpx.Response(200, content=b"ok"),
        ]
    )
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    svc = _svc(min_delay=0, retry_base_delay=0.5, sleep=fake_sleep)
    await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.call_count == 2
    assert sleeps[1] == pytest.approx(2.0)


# ------------------------------------------------------------------
# Per-provider pacing override
# ------------------------------------------------------------------


async def test_azure_falls_back_to_shared_delay_when_no_override(monkeypatch):
    """With no per-provider override set, Azure's pacing is byte-identical to the shared setting."""
    from app.config import settings

    monkeypatch.setattr(settings, "tts_azure_min_request_delay_s", None)
    svc = AzureTTSService(key="k", region="r")

    assert svc._min_delay == settings.tts_min_request_delay_s


async def test_azure_per_provider_override_wins_over_shared(monkeypatch):
    """A configured Azure-specific delay overrides the shared setting."""
    from app.config import settings

    monkeypatch.setattr(settings, "tts_azure_min_request_delay_s", 0.05)
    monkeypatch.setattr(settings, "tts_min_request_delay_s", 0.99)
    svc = AzureTTSService(key="k", region="r")

    assert svc._min_delay == 0.05


@respx.mock
async def test_no_trailing_sleep_after_final_attempt(tmp_path):
    """The retry ladder does not sleep after the final (failed) attempt.

    With retry_base_delay=1 the exponential backoff is 1s, 2s between attempts,
    and a trailing sleep would add a third 1s delay after the final failure.
    The total elapsed time must be under 3.5s (sum of 1+2 backoff sleeps).
    """
    import time

    respx.post(SYNTH_URL).mock(return_value=httpx.Response(503))

    svc = _svc(retry_base_delay=1)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")
    elapsed = time.monotonic() - t0

    # Backoff sleeps: 1s + 2s = 3s total. If a trailing sleep exists,
    # elapsed would be ~4s. Use 3.5s as the boundary.
    assert elapsed < 3.5, f"elapsed {elapsed:.1f}s suggests a trailing sleep after final attempt"


# ------------------------------------------------------------------
# Discriminating test: renderer completes a multi-section lesson
# despite a concurrency-limited fake TTSService
# ------------------------------------------------------------------


async def test_renderer_completes_through_a_throttled_tts_port(tmp_path):
    """The renderer drives a multi-section lesson to completion through the port.

    ⚠️ This is a SMOKE test, not the concurrency guard. The throttle lives in
    the adapter, and this test replaces the adapter with a fake — so nothing
    here can prove the real semaphore is held. An earlier version asserted
    ``observed_max <= SAFE_LIMIT`` while the fake itself constructed the
    semaphore that produced that number, which made the assertion a test of
    ``asyncio.Semaphore`` rather than of any code in this repo. Verified
    2026-08-19: disabling the real throttle entirely left it green.

    The real guard is ``test_adapter_respects_configured_concurrency_limit``,
    which measures in-flight count inside the adapter's own request path.
    """
    from app.audio.pause_calculator import NaturalPauseCalculator
    from app.audio.renderer import LessonRenderer
    from app.config import settings
    from app.models.lesson import Lesson, Phrase, Section, SectionType

    observed_max = 0
    in_flight = 0

    class FakeTTS:
        def __init__(self):
            self._semaphore = asyncio.Semaphore(settings.tts_max_concurrent_requests)

        async def synthesize(self, text, voice_id, output_path, rate="+0%"):
            nonlocal in_flight, observed_max
            async with self._semaphore:
                in_flight += 1
                observed_max = max(observed_max, in_flight)
                try:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    import struct

                    rate_hz = 24000
                    n_frames = rate_hz // 10
                    data = b"\x00\x00" * n_frames
                    header = struct.pack(
                        "<4sI4s4sIHHIIHH4sI",
                        b"RIFF",
                        36 + len(data),
                        b"WAVE",
                        b"fmt ",
                        16,
                        1,
                        1,
                        rate_hz,
                        rate_hz * 2,
                        2,
                        16,
                        b"data",
                        len(data),
                    )
                    output_path.write_bytes(header + data)
                    await asyncio.sleep(0)
                finally:
                    in_flight -= 1

        async def list_voices(self, language_code=None):
            return []

    lesson = Lesson(
        title="Day 1",
        language_code="sl",
        narrator_voice="en-US-GuyNeural",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl"),
                    Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl"),
                ],
            ),
            Section(
                section_type=SectionType.TRANSLATED,
                phrases=[
                    Phrase(text="nasvidenje", voice_id="sl-SI-PetraNeural", language_code="sl"),
                    Phrase(text="prosim", voice_id="sl-SI-PetraNeural", language_code="sl"),
                ],
            ),
        ],
    )

    renderer = LessonRenderer(
        tts=FakeTTS(),
        preprocessors={"sl": type("P", (), {"preprocess": lambda self, text, st: text})()},
        pause_calculator=NaturalPauseCalculator(),
    )
    out = tmp_path / "lesson.wav"
    cues = await renderer.render(lesson, out)

    assert out.exists()
    assert len(cues) > 0
    assert observed_max > 0, "the fake port was never called — the smoke test proves nothing"
