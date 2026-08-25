"""Render resumption — a failed render must not discard the clips it already synthesized.

tunatale-5lp6. Since the burst-throttle fix (fb0a17f) a lesson render is a long
serial run of TTS calls — concurrency 1 at 0.6s apart, ~24 minutes for a real
day — so "one transient clip fails and you start over" costs minutes, where it
used to cost seconds. Three separate mechanisms threw the work away: the
synthesis memo is render-scoped, the mp3s live in a temp dir that dies with the
render, and the adapters' sha256 file cache was never wired to anything in
production.

Network is intercepted with respx at the httpx transport layer, exactly as in
test_azure_tts.py: the boundary being faked is a socket, not one of our own
functions, so nothing here needs a mock_allowlist.txt entry.
"""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from pathlib import Path

import httpx
import numpy as np
import pytest
import respx
import soundfile as sf
from fastapi import FastAPI

from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.renderer import LessonRenderer
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.plugins.languages.sl.preprocessor import SlovenePreprocessor

SYNTH_URL = "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1"
_VOICE = "sl-SI-PetraNeural"

# Distinct, preprocessor-invariant tokens, so a request body can be attributed to
# exactly one phrase by looking for the SSML text node ">word<".
_WORDS = ("alfa", "bravo", "charlie", "delta", "echo", "foxtrot")


def _wav_bytes(duration_ms: int = 100, rate: int = 11025) -> bytes:
    """Minimal real audio. soundfile sniffs content, not the .mp3 extension."""
    buf = BytesIO()
    frames = round(duration_ms / 1000 * rate)
    sf.write(buf, np.zeros((frames, 1), dtype="float32"), rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _lesson(words: tuple[str, ...] = _WORDS) -> Lesson:
    return Lesson(
        title="Test",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=w, voice_id=_VOICE, language_code="sl") for w in words],
            )
        ],
    )


def _words_in(calls) -> set[str]:
    """Which phrase words those requests actually asked the network to synthesize."""
    bodies = [c.request.content.decode() for c in calls]
    return {w for w in _WORDS if any(f">{w}<" in b for b in bodies)}


def _make_renderer(tts) -> LessonRenderer:
    return LessonRenderer(
        tts=tts,
        preprocessors={"sl": SlovenePreprocessor()},
        pause_calculator=NaturalPauseCalculator(),
    )


# ---------------------------------------------------------------------------
# THE oracle, pinned on tunatale-5lp6, driven through the real lifespan wiring
# ---------------------------------------------------------------------------


@respx.mock
async def test_rerun_after_a_failed_render_does_not_resynthesize_what_succeeded(tmp_path, monkeypatch):
    """A render that dies on one clip must leave the rest banked on disk.

    Deliberately driven through ``lifespan`` rather than a hand-wired adapter.
    A test that constructs ``AzureTTSService(cache_dir=...)`` itself passes today
    — the adapter cache has worked all along; what was missing is that NOTHING in
    production ever passed a cache_dir (app/main.py built the renderer with a
    bare ``get_tts_service()``). So the wiring is the claim, and only a test that
    lets the app build its own renderer can make it.
    """
    from app.config import settings
    from app.main import lifespan

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "llm_mode", "mock")
    monkeypatch.setattr(settings, "tts_provider", "azure")
    monkeypatch.setattr(settings, "azure_speech_key", "test-key")
    monkeypatch.setattr(settings, "azure_speech_region", "eastus")
    monkeypatch.setattr(settings, "tts_min_request_delay_s", 0.0)
    monkeypatch.setattr(settings, "tts_cache_dir", tmp_path / "tts-cache")

    audio = _wav_bytes()
    broken = "delta"
    fixed = [False]

    def _respond(request: httpx.Request) -> httpx.Response:
        if not fixed[0] and f">{broken}<" in request.content.decode():
            return httpx.Response(500)
        return httpx.Response(200, content=audio)

    route = respx.post(SYNTH_URL).mock(side_effect=_respond)
    lesson = _lesson()

    test_app = FastAPI()
    async with lifespan(test_app):
        rdr = test_app.state.renderer

        with pytest.raises(RuntimeError):
            await rdr.render(lesson, tmp_path / "one.wav")

        first_calls = list(route.calls)
        banked = {w for w in _words_in(first_calls) if w != broken}
        assert banked, "the first render must have completed at least one clip, or this proves nothing"

        fixed[0] = True
        await rdr.render(lesson, tmp_path / "two.wav")

    second = _words_in(list(route.calls)[len(first_calls) :])

    assert second.isdisjoint(banked), (
        f"re-render re-synthesized {sorted(second & banked)}, which the failed render had already paid for"
    )
    assert len(second) < len(_WORDS), "the re-run must be cheaper than a cold render, not identical to one"


async def test_lifespan_wires_the_tts_cache_so_syntheses_outlive_one_render(tmp_path, monkeypatch):
    """The renderer's adapter carries a cache_dir.

    This is the whole of mechanism (3) on the bead: both adapters implement a
    sha256-keyed mp3 cache and take a ``cache_dir``, and the only production
    caller passing one anywhere was slicer.py — for the alignment cache, a
    different artifact entirely.
    """
    from app.config import settings
    from app.main import lifespan

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "llm_mode", "mock")
    monkeypatch.setattr(settings, "tts_cache_dir", tmp_path / "tts-cache")

    test_app = FastAPI()
    async with lifespan(test_app):
        assert test_app.state.renderer._tts._cache_dir == tmp_path / "tts-cache"


def test_tts_cache_dir_defaults_under_the_tunatale_home(monkeypatch):
    """An absolute default, like the alignment cache beside it — a relative path
    would put the cache wherever the process happened to be started from."""
    from app.config import settings

    assert settings.tts_cache_dir.is_absolute()


# ---------------------------------------------------------------------------
# Mechanism (1): one failure must not leave its siblings running unsupervised
# ---------------------------------------------------------------------------


class _BlockingTTS:
    """Records in-flight syntheses; every clip blocks except the one that raises."""

    def __init__(self, failing_text: str) -> None:
        self._failing = failing_text
        self.in_flight = 0
        self.started = 0
        self.cancelled = 0
        self.completed = 0

    async def synthesize(self, text: str, voice_id: str, output_path: Path, rate: str = "+0%", phonemes=None) -> None:
        self.started += 1
        if text == self._failing:
            raise RuntimeError(f"TTS exploded on {text!r}")
        if text not in _WORDS:
            # The lesson title, synthesized before the sections and not what
            # these tests are about. Blocking here would just slow them down.
            output_path.write_bytes(_wav_bytes())
            return
        self.in_flight += 1
        try:
            await asyncio.sleep(5)
            output_path.write_bytes(_wav_bytes())
            self.completed += 1
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.in_flight -= 1

    async def list_voices(self, language_code: str | None = None) -> list[dict]:
        return []


async def test_a_failed_render_leaves_no_synthesis_running_unsupervised(tmp_path):
    """render() must not return while sibling syntheses are still in flight.

    ``asyncio.gather`` does not cancel siblings when it propagates — it just stops
    waiting. Those orphans then keep writing into the ``TemporaryDirectory`` the
    render is in the middle of deleting, and any that fail surface as a bare
    "Task exception was never retrieved" with no lesson context attached.

    Asserted with no intervening await on purpose: settling the cancellations is
    render()'s job, not the caller's.
    """
    tts = _BlockingTTS(failing_text="delta")
    rdr = _make_renderer(tts)

    with pytest.raises(RuntimeError, match="delta"):
        await rdr.render(_lesson(), tmp_path / "out.wav")

    assert tts.started > 1, "the failing clip must have had siblings, or this proves nothing"
    assert tts.in_flight == 0, f"{tts.in_flight} synthesis task(s) still running after render() raised"
    # Cancelled, not merely waited for. Sabotage drill 2026-08-21: with the
    # cancel loop neutered but the settling gather left in place, in_flight
    # still reached 0 — render() just sat through every remaining clip first
    # (19s -> 32s on this file) and NOTHING went red. Draining a provider that
    # has already started failing is the behaviour this line exists to forbid.
    assert tts.cancelled >= 1 and tts.completed == 0, (
        f"{tts.completed} sibling synthesis(es) ran to completion; they should have been cancelled"
    )


async def test_the_original_failure_survives_the_cancellation_of_its_siblings(tmp_path):
    """The caller sees the TTS error, not a CancelledError or an ExceptionGroup.

    api/audio.py maps the adapter's RuntimeError to a 503 carrying its message
    (tunatale-0a5.1). Cancelling siblings must not replace that message with the
    bookkeeping exception cancellation raises.
    """
    tts = _BlockingTTS(failing_text="charlie")
    rdr = _make_renderer(tts)

    with pytest.raises(RuntimeError) as exc:
        await rdr.render(_lesson(), tmp_path / "out.wav")

    assert "charlie" in str(exc.value)


# ---------------------------------------------------------------------------
# Which clip died — the operator-facing half
# ---------------------------------------------------------------------------


async def test_a_failed_clip_is_logged_with_the_phrase_and_voice_that_died(tmp_path, caplog):
    """ "Azure TTS synthesis failed after 3 attempts" says nothing about WHICH of
    155 phrases exhausted its ladder. The renderer is the only layer that knows."""
    tts = _BlockingTTS(failing_text="echo")
    rdr = _make_renderer(tts)

    with caplog.at_level(logging.ERROR, logger="app.audio.renderer"), pytest.raises(RuntimeError):
        await rdr.render(_lesson(), tmp_path / "out.wav")

    failures = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("echo" in m and _VOICE in m for m in failures), (
        f"no log line named the failing phrase and voice; got {failures}"
    )
