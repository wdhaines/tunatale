"""A render survives a throttling episode instead of handing it back to a human.

tunatale-uxm0. Since tunatale-5lp6 wired ``settings.tts_cache_dir``, a failed
render is cheap to re-run — every clip that synthesized is already on disk, so
a second pass only does what the first one missed. What was missing is that
anything re-runs it: one clip exhausting its ladder aborted the whole lesson
and a human had to notice and press the button again.

⚠️ The layer under this one (``AzureTTSService._synthesize_with_retry``) is what
keeps a clip alive through a throttling window; see test_azure_tts.py. This file
is only about what happens once that ladder has genuinely run out.
"""

from __future__ import annotations

import pytest

from app.audio.ports import TTSExhausted
from app.audio.render_service import _with_render_retries


async def _never_slept(_delay: float) -> None:  # pragma: no cover - guard, see below
    raise AssertionError("a cooldown was paid when no retry was needed")


async def test_a_render_that_exhausts_once_is_re_run_and_succeeds():
    """One transient failure must not end the render — pass 2 finishes it.

    This is the whole point of the bead: the cache makes pass 2 nearly free,
    so the only thing between "failed" and "finished" was the re-trigger.
    """
    attempts = 0
    cooldowns: list[float] = []

    async def attempt():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TTSExhausted("Azure TTS synthesis failed after 6 attempts")
        return "rendered"

    async def fake_sleep(delay):
        cooldowns.append(delay)

    result = await _with_render_retries(attempt, "day 8", max_attempts=3, cooldown_s=15.0, sleep=fake_sleep)

    assert result == "rendered"
    assert attempts == 2
    assert cooldowns == [15.0], f"exactly one cooldown, between the two passes; got {cooldowns}"


async def test_a_render_that_never_recovers_is_bounded_and_reraises_the_last_error():
    """Try hard is not try forever: the loop is bounded and the caller still
    sees the adapter's own message, which api/audio.py turns into a 503."""
    attempts = 0

    async def attempt():
        nonlocal attempts
        attempts += 1
        raise TTSExhausted(f"exhausted on pass {attempts}")

    async def fake_sleep(_delay):
        return None

    with pytest.raises(TTSExhausted, match="exhausted on pass 3"):
        await _with_render_retries(attempt, "day 8", max_attempts=3, cooldown_s=0.0, sleep=fake_sleep)

    assert attempts == 3


async def test_a_render_that_succeeds_first_time_pays_no_cooldown():
    """The retry loop must be invisible on the happy path — no added wall-clock."""
    attempts = 0

    async def attempt():
        nonlocal attempts
        attempts += 1
        return "rendered"

    result = await _with_render_retries(attempt, "day 8", max_attempts=3, cooldown_s=15.0, sleep=_never_slept)

    assert result == "rendered"
    assert attempts == 1


async def test_a_configuration_error_is_not_retried():
    """A missing AZURE_SPEECH_KEY is not going to fix itself in 15 seconds.

    Only ``TTSExhausted`` — the adapter saying "the provider kept pushing back"
    — is transient. ``_require_credentials`` raises a bare ``RuntimeError``, and
    retrying it three times just delays a diagnosis by half a minute and buries
    the message under two "retrying" warnings.
    """
    attempts = 0

    async def attempt():
        nonlocal attempts
        attempts += 1
        raise RuntimeError("AZURE_SPEECH_KEY is not set.")

    with pytest.raises(RuntimeError, match="AZURE_SPEECH_KEY"):
        await _with_render_retries(attempt, "day 8", max_attempts=3, cooldown_s=15.0, sleep=_never_slept)

    assert attempts == 1, f"a configuration error was retried {attempts} times"


async def test_each_pass_is_reported_so_a_long_render_is_not_silent(caplog):
    """A render that takes three passes must say so, and name which pass it is on.

    Without this the operator-visible behaviour of "trying hard" is a terminal
    that has printed nothing for two minutes — indistinguishable from a hang.
    """
    import logging

    async def attempt():
        raise TTSExhausted("nope")

    async def fake_sleep(_delay):
        return None

    with caplog.at_level(logging.WARNING, logger="app.audio.render_service"), pytest.raises(TTSExhausted):
        await _with_render_retries(attempt, "day 8", max_attempts=3, cooldown_s=0.0, sleep=fake_sleep)

    messages = [r.getMessage() for r in caplog.records]
    assert sum("day 8" in m for m in messages) >= 2, f"the retries were silent; got {messages}"
    assert any("2" in m and "3" in m for m in messages), f"no message named the pass number; got {messages}"


# ---------------------------------------------------------------------------
# Wiring — the loop is useless if the two entry points do not go through it
# ---------------------------------------------------------------------------


async def test_render_lesson_audio_retries_a_throttled_lesson(tmp_path, monkeypatch):
    """POST /api/audio/render must survive one exhausted clip, end to end.

    Asserted through ``render_lesson_audio`` rather than on the helper, because
    a helper nothing calls is the failure mode this guards: every unit test
    above passes with the loop present and unwired.
    """
    from app.audio import render_service
    from app.storage.store import ContentStore
    from tests.test_reassemble_lesson import _build_test_lesson

    monkeypatch.setattr(render_service.settings, "tts_render_retry_cooldown_s", 0.0)
    lesson = _build_test_lesson()
    store = ContentStore(":memory:")
    attempts = 0

    class OnceThrottledRenderer:
        async def render(self, lesson, output_path, section_paths=None):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TTSExhausted("Azure TTS synthesis failed after 6 attempts")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"full")
            for sp in section_paths or []:
                sp.parent.mkdir(parents=True, exist_ok=True)
                sp.write_bytes(b"sec")
            return []

    result = await render_service.render_lesson_audio(
        store=store,
        renderer=OnceThrottledRenderer(),
        audio_dir=tmp_path / "audio",
        lesson_id=lesson.title,
        lesson=lesson,
    )

    assert attempts == 2, f"the render was not retried (attempts={attempts})"
    assert result["sections"], "the retried render produced no section rows"


async def test_reassemble_retries_a_throttled_key_phrases_rebuild(tmp_path, monkeypatch):
    """The selective KEY_PHRASES rebuild is a render too, and dies the same way."""
    from app.audio import render_service
    from app.storage.store import ContentStore
    from tests.test_reassemble_lesson import (
        _build_test_lesson,
        _CountingTTS,
        _make_fake_renderer,
        _populate_store,
    )

    monkeypatch.setattr(render_service.settings, "tts_render_retry_cooldown_s", 0.0)
    lesson = _build_test_lesson()
    store = ContentStore(":memory:")
    audio_dir = tmp_path / "audio"
    _populate_store(store, lesson, audio_dir, [2.0, 5.0, 8.0, 5.0])

    inner = _make_fake_renderer()
    attempts = 0

    class OnceThrottledRenderer:
        pause_calculator = inner.pause_calculator

        async def render(self, *a, **kw):
            return await inner.render(*a, **kw)

        async def render_section(self, *a, **kw):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TTSExhausted("Azure TTS synthesis failed after 6 attempts")
            return await inner.render_section(*a, **kw)

    await render_service.reassemble_lesson_audio(
        store=store,
        renderer=OnceThrottledRenderer(),
        tts=_CountingTTS(),
        audio_dir=audio_dir,
        lesson_id=lesson.title,
        lesson=lesson,
    )

    assert attempts == 2, f"the KEY_PHRASES rebuild was not retried (attempts={attempts})"


async def test_an_exhausted_clip_reaches_the_retry_loop_as_TTSExhausted(tmp_path):
    """The renderer must not flatten the adapter's type on the way out.

    ``render()`` cancels every sibling synthesis when one fails and settles them
    through a second ``gather``. If that bookkeeping replaced the original
    exception — or if the type were widened to a bare ``RuntimeError`` anywhere
    on the way — ``_with_render_retries`` would classify a throttling episode as
    a configuration error and refuse to retry, silently reverting this whole
    bead.

    ⚠️ The wiring tests above cannot see this: their fake renderers raise
    ``TTSExhausted`` themselves. This one goes through the REAL
    ``LessonRenderer``, so the cancel-and-settle path is in the picture.
    """
    from tests.test_renderer_resume import _BlockingTTS, _lesson, _make_renderer

    class ExhaustingTTS(_BlockingTTS):
        async def synthesize(self, text, voice_id, output_path, rate="+0%", phonemes=None):
            if text == self._failing:
                raise TTSExhausted("Azure TTS synthesis failed after 6 attempts")
            await super().synthesize(text, voice_id, output_path, rate, phonemes)

    rdr = _make_renderer(ExhaustingTTS(failing_text="delta"))

    with pytest.raises(TTSExhausted):
        await rdr.render(_lesson(), tmp_path / "out.wav")
