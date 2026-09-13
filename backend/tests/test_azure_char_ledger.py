"""AzureCharacterLedger — calendar-month tally behind the F0 quota refusal.

STAGE 2 pins the ledger's six semantic properties against absolute epoch
oracles (brief-6zzu1-azure-char-ledger-2026-09-13.md); STAGE 5 pins the refusal
behaviour through the real AzureTTSService. Nothing here patches ``app.*`` —
the ledger takes absolute ``now=`` arguments and the service takes injectable
``ledger=`` / ``cache_dir=`` / ``sleep=`` seams.

⚠️ Never seed a ledger relative to ``time.time()``: a month-bucketing test
seeded relative to now passes or fails depending on the day the suite runs.
Every oracle below is an absolute epoch measured 2026-09-13.
"""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from app.audio.azure_tts import AzureTTSService
from app.audio.char_ledger import AzureCharacterLedger
from app.audio.ports import TTSExhausted, TTSQuotaExceeded

# Oracle epochs — 2026 by construction; a mismatch with the wall clock is the
# whole point (the bucket is calendar-month, so the tests must not lean on the
# day the suite happens to run).
AUG_31_2359_UTC = 1788220740.0  # 2026-08-31 23:59 UTC -> August bucket
SEP_1_0000_UTC = 1788220800.0  # 2026-09-01 00:00 UTC -> September bucket
SEP_1_0100_UTC = 1788224400.0  # 2026-09-01 01:00 UTC -> Sep in UTC, Aug in America/New_York
SEP_13_1200_UTC = 1789300800.0  # 2026-09-13 12:00 UTC (the refusal-message pin)
SEP_30_2300_UTC = 1790809200.0  # 2026-09-30 23:00 UTC
OCT_1_0000_UTC = 1790812800.0  # 2026-10-01 00:00 UTC
DEC_13_1200_UTC = 1797163200.0  # 2026-12-13 12:00 UTC
JAN_1_2027_UTC = 1798761600.0  # 2027-01-01 00:00 UTC


def test_month_boundary_drops_the_previous_month(tmp_path):
    """Sixty seconds apart, two different buckets: Aug 31 23:59 vs Sep 1 00:00.

    Oracle integrity first: the two epochs really are a minute apart, so any
    failure below is a bucketing failure, not a table-typo failure.
    """
    assert SEP_1_0000_UTC - AUG_31_2359_UTC == 60

    ledger = AzureCharacterLedger(tmp_path / "ledger.log")

    ledger.record(100, now=AUG_31_2359_UTC)
    assert ledger.chars_used(now=SEP_13_1200_UTC) == 0

    ledger.record(100, now=SEP_1_0000_UTC)
    assert ledger.chars_used(now=SEP_13_1200_UTC) == 100


def test_entries_in_the_same_month_sum(tmp_path):
    ledger = AzureCharacterLedger(tmp_path / "ledger.log")

    ledger.record(100, now=SEP_1_0000_UTC)
    ledger.record(50, now=SEP_1_0100_UTC)

    assert ledger.chars_used(now=SEP_13_1200_UTC) == 150


def test_reset_in_s_is_the_real_distance_to_the_next_month(tmp_path):
    """At 2026-09-13 12:00 UTC the next boundary is 2026-10-01 00:00 UTC.

    That is exactly 17d12h — and it must be reported even though nothing has
    been spent: a calendar boundary exists whether or not you used anything,
    and 0.0 there would read as "already reset".
    """
    ledger = AzureCharacterLedger(tmp_path / "ledger.log")

    status = ledger.budget(chars_limit=500_000, now=SEP_13_1200_UTC)

    assert status.reset_in_s == 1512000.0
    assert status.chars_used == 0


def test_reset_tz_discriminates_between_months_on_the_boundary(tmp_path):
    """1788224400.0 (01:00 UTC Sep 1) is September in UTC and August in New York.

    Same ledger file, same timestamp — the tz setting alone changes the
    answer. Without this test the setting is decoration.
    """
    path = tmp_path / "ledger.log"
    AzureCharacterLedger(path).record(100, now=SEP_1_0100_UTC)

    utc = AzureCharacterLedger(path, reset_tz="UTC")
    new_york = AzureCharacterLedger(path, reset_tz="America/New_York")

    assert utc.chars_used(now=SEP_13_1200_UTC) == 100
    assert new_york.chars_used(now=SEP_13_1200_UTC) == 0


def test_tally_survives_a_process_restart(tmp_path):
    """Record through one instance, tally through a second on the same path."""
    path = tmp_path / "ledger.log"
    AzureCharacterLedger(path).record(100, now=SEP_1_0000_UTC)
    AzureCharacterLedger(path).record(50, now=SEP_1_0100_UTC)

    restarted = AzureCharacterLedger(path)
    assert restarted.chars_used(now=SEP_13_1200_UTC) == 150


def test_exceeded_flips_exactly_at_the_limit(tmp_path):
    """None one below the cap, 'characters per month' AT the cap.

    The ``>=`` is deliberate: at exactly the cap, the next character is over.
    """
    ledger = AzureCharacterLedger(tmp_path / "ledger.log")
    ledger.record(999, now=SEP_1_0000_UTC)

    below = ledger.budget(chars_limit=1000, now=SEP_13_1200_UTC)
    assert below.exceeded is None
    assert below.chars_used == 999

    ledger.record(1, now=SEP_1_0100_UTC)
    at_cap = ledger.budget(chars_limit=1000, now=SEP_13_1200_UTC)
    assert at_cap.exceeded == "characters per month"
    assert at_cap.chars_used == 1000


def test_malformed_lines_are_skipped_on_load(tmp_path):
    """A hand-edited or truncated file must not crash or corrupt the ledger."""
    path = tmp_path / "ledger.log"
    path.write_text("this is not a record\n1788220800.0 100\n42 only-ints\n")

    ledger = AzureCharacterLedger(path)

    assert ledger.chars_used(now=SEP_13_1200_UTC) == 100


def test_over_max_entries_rewrites_dropping_prior_month_entries(tmp_path):
    """Past the cap the file is rewritten down to this month's entries only.

    The rewrite must both trim the in-memory tally AND persist the trimmed
    form — a fresh instance on the same path must read the same answer.
    """
    path = tmp_path / "ledger.log"
    ledger = AzureCharacterLedger(path, max_entries=2)
    ledger.record(100, now=AUG_31_2359_UTC)  # August — the entry the rewrite drops
    ledger.record(100, now=SEP_1_0000_UTC)
    ledger.record(100, now=SEP_1_0100_UTC)  # third entry -> exceeds max_entries

    assert ledger.chars_used(now=SEP_13_1200_UTC) == 200
    assert AzureCharacterLedger(path).chars_used(now=SEP_13_1200_UTC) == 200
    assert len(path.read_text().splitlines()) == 2


def test_reset_over_the_year_boundary(tmp_path):
    """December rolls into January — the next boundary is Jan 1 of the new year.

    Covers the month == 12 rollover, which the September oracle table cannot.
    """
    ledger = AzureCharacterLedger(tmp_path / "ledger.log")

    status = ledger.budget(chars_limit=500_000, now=DEC_13_1200_UTC)

    assert status.reset_in_s == JAN_1_2027_UTC - DEC_13_1200_UTC == 1598400.0


def test_now_defaults_to_the_wall_clock(tmp_path):
    """record()/chars_used()/budget() with no explicit now use time.time().

    Deliberately asserts only that the fallbacks are usable, never the tally:
    querying with the real wall clock would make the pinned month assertions
    pass or fail depending on the day the suite runs. The pinned properties
    all pass an absolute oracle timestamp instead; this exists so the
    ``now=None`` fallback is exercised, not so the month logic is re-tested.
    """
    ledger = AzureCharacterLedger(tmp_path / "ledger.log")

    ledger.record(10)
    used = ledger.chars_used()
    status = ledger.budget(chars_limit=500_000)

    assert isinstance(used, int)
    assert isinstance(status.chars_used, int)
    assert status.exceeded is None


# ---------------------------------------------------------------------------
# STAGE 3/5 — the counting and the refusal through the REAL AzureTTSService
# (brief-6zzu1, stages 3 and 5)
# ---------------------------------------------------------------------------
#
# Every seam is injectable (ledger=, cache_dir=, sleep=, chars_per_month_limit=)
# and the clock is pinned by monkeypatching time.time — the established
# clock-fake pattern from test_anki_sync_concurrent_review.py, not an app.*
# patch, so nothing here needs mock_allowlist.txt.

SYNTH_URL = "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1"


def _svc(**kw):
    kw.setdefault("key", "test-key")
    kw.setdefault("region", "eastus")
    # Timing is injected, not patched: the retry ladder and the inter-request
    # pacing are real code paths here, they just run at zero delay.
    kw.setdefault("min_delay", 0)
    kw.setdefault("retry_base_delay", 0)
    return AzureTTSService(**kw)


async def _noop_sleep(_delay):
    """A zero-cost sleep for exercising the retry ladder and pacing."""
    return None


def test_chars_per_month_limit_resolves_from_settings_when_a_ledger_is_present(monkeypatch, tmp_path):
    """chars_per_month_limit=None resolves lazily, like min_delay and friends.

    Consulted only when a ledger is present; with ledger=None it stays None.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "azure_tts_chars_per_month_limit", 1234)
    ledger = AzureCharacterLedger(tmp_path / "ledger.log")

    assert _svc(ledger=ledger)._chars_per_month_limit == 1234
    assert _svc()._chars_per_month_limit is None


def test_get_tts_service_wires_a_ledger_for_azure_but_not_edge(tmp_path):
    """Production's only builder constructs the ledger for azure, and only azure.

    Edge is a different (unmetered) endpoint — EdgeTTSService takes no such
    argument, and passing one would change its signature for nothing.
    """
    from app.audio.edge_tts import EdgeTTSService
    from app.audio.tts_factory import get_tts_service

    azure_svc = get_tts_service(cache_dir=tmp_path / "cache", provider="azure")
    edge_svc = get_tts_service(cache_dir=tmp_path / "cache", provider="edge")

    assert isinstance(azure_svc, AzureTTSService)
    assert azure_svc._ledger is not None
    assert isinstance(edge_svc, EdgeTTSService)
    assert not hasattr(edge_svc, "_ledger")


@respx.mock
async def test_cache_hit_neither_counts_nor_calls_azure(tmp_path, monkeypatch):
    """A cache hit makes no API call, so Azure bills nothing and the tally must not grow.

    The single most likely-to-be-got-wrong acceptance criterion: counting cache
    hits inflates the tally ~3.1x and makes the ledger lie.
    """
    monkeypatch.setattr(time, "time", lambda: SEP_13_1200_UTC)
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"audio"))
    ledger = AzureCharacterLedger(tmp_path / "ledger.log")
    svc = _svc(cache_dir=tmp_path / "cache", ledger=ledger, chars_per_month_limit=500_000)

    await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "one.mp3")
    assert route.call_count == 1
    assert ledger.chars_used() == 33

    await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "two.mp3")

    assert route.call_count == 1, "the cache hit made an HTTP request"
    assert ledger.chars_used() == 33, "the cache hit incremented the tally"
    assert (tmp_path / "two.mp3").read_bytes() == b"audio"


@respx.mock
async def test_cache_hit_still_succeeds_at_the_cap(tmp_path, monkeypatch):
    """The discriminator: check the quota in the RIGHT place — after the cache.

    A warm cache is the only thing that lets a render finish at the cap, so a
    cache hit must keep working even when the ledger says the month is spent.
    """
    monkeypatch.setattr(time, "time", lambda: SEP_13_1200_UTC)
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"audio"))
    ledger = AzureCharacterLedger(tmp_path / "ledger.log")
    svc = _svc(cache_dir=tmp_path / "cache", ledger=ledger, chars_per_month_limit=500_000)

    await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "one.mp3")  # warms the cache
    assert route.call_count == 1
    assert ledger.chars_used() == 33

    ledger.record(500_000 - 33)  # now at exactly the cap

    out = tmp_path / "two.mp3"
    await svc.synthesize("hei", "nb-NO-FinnNeural", out)

    assert route.call_count == 1, "the cached clip was re-synthesized at the cap"
    assert out.read_bytes() == b"audio"


@respx.mock
async def test_at_the_cap_the_call_is_refused_with_no_http(tmp_path, monkeypatch):
    """The loud refusal — message pinned verbatim.

    At the cap on 2026-09-13 12:00 UTC with the default 500k limit, exactly:
    "Monthly Azure TTS budget exhausted: characters per month (500,000 of
    500,000); quota resets in 17d12h"
    """
    monkeypatch.setattr(time, "time", lambda: SEP_13_1200_UTC)
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"audio"))
    ledger = AzureCharacterLedger(tmp_path / "ledger.log")
    ledger.record(500_000)
    svc = _svc(cache_dir=tmp_path / "cache", ledger=ledger, chars_per_month_limit=500_000)

    with pytest.raises(TTSQuotaExceeded) as excinfo:
        await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert str(excinfo.value) == (
        "Monthly Azure TTS budget exhausted: characters per month (500,000 of 500,000); quota resets in 17d12h"
    )
    assert not route.called, "the refusal is pre-call; no request reached the endpoint"
    assert not (tmp_path / "o.mp3").exists()


@respx.mock
async def test_a_failed_synthesis_does_not_count(tmp_path, monkeypatch):
    """FACT A consequence 3: only a successfully processed request bills.

    A persistent 429 exhausts the ladder into TTSExhausted; the tally must
    still read 0 because the ``else:`` branch of ``_do_synthesize`` — the
    "raise_for_status did not raise" branch — never ran.
    """
    monkeypatch.setattr(time, "time", lambda: SEP_13_1200_UTC)
    respx.post(SYNTH_URL).mock(return_value=httpx.Response(429))
    ledger = AzureCharacterLedger(tmp_path / "ledger.log")
    svc = _svc(
        cache_dir=tmp_path / "cache",
        ledger=ledger,
        chars_per_month_limit=500_000,
        sleep=_noop_sleep,
    )

    with pytest.raises(TTSExhausted):
        await svc.synthesize("hei", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert ledger.chars_used() == 0
    assert not (tmp_path / "o.mp3").exists()


@respx.mock
async def test_a_successful_synthesis_increments_by_the_billable_count(tmp_path, monkeypatch):
    """One synthesis of oracle row 3 moves the tally by exactly 54."""
    monkeypatch.setattr(time, "time", lambda: SEP_13_1200_UTC)
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"audio"))
    ledger = AzureCharacterLedger(tmp_path / "ledger.log")
    svc = _svc(cache_dir=tmp_path / "cache", ledger=ledger, chars_per_month_limit=500_000)

    await svc.synthesize("Kavno pivo je na meniju.", "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.call_count == 1
    assert ledger.chars_used() == 54
    lines = (tmp_path / "ledger.log").read_text().splitlines()
    assert len(lines) == 1
    assert int(lines[0].split()[1]) == 54


@respx.mock
async def test_ledger_none_never_counts_or_refuses(tmp_path, monkeypatch):
    """ledger=None means no accounting at all — even text that blows any cap.

    Mirrors how cache_dir=None already means "no cache": every test that
    constructs the adapter directly stays inert unless it asks for a ledger.
    """
    monkeypatch.setattr(time, "time", lambda: SEP_13_1200_UTC)
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"audio"))
    svc = _svc(cache_dir=tmp_path / "cache")  # no ledger

    await svc.synthesize("x" * 600_000, "nb-NO-FinnNeural", tmp_path / "o.mp3")

    assert route.called
    assert (tmp_path / "o.mp3").read_bytes() == b"audio"
    assert not (tmp_path / "ledger.log").exists()
