"""Only one lesson renders at a time in a process.

bd tunatale-rwkz.2. On 2026-09-19 two review-session renders ran concurrently on
the 953 MB box and took 47 and 58 minutes. A render holds a whole lesson as
float32 24 kHz PCM — ~340 MB for the 58.6-minute session that triggered this —
plus its section buffers and a WAV copy handed to ffmpeg, so ONE render nearly
fills that machine and two ran out of swap. The box was starved hard enough that
Docker's own DNS timed out and Caddy answered 502 to the learner's phone.

⚠️ THE EXISTING GUARD DOES NOT COVER THIS, which is the whole reason for a
second one. ``review_sessions._renders_in_flight`` refuses a second render OF THE
SAME SESSION ID; the 2026-09-19 pair were two DIFFERENT ids, so it correctly let
both through. This gate is about the machine, not about the id.

It lives inside ``render_lesson_audio`` rather than at its four call sites (the
auto-render in publishing, the manual render route, the audio endpoint, the
curriculum pipeline) for the reason ``publish_lesson``'s docstring gives about
its own ordering: a rule kept at call sites is a rule the fifth call site will
not know about.
"""

from __future__ import annotations

import asyncio

import pytest

from app.audio import render_service
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore


def _lesson() -> Lesson:
    return Lesson(
        title="A Fishing Trip",
        language_code="no",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="Hei", language_code="no", voice_id="female-1")],
            )
        ],
    )


class _RecordingRenderer:
    """Records overlap: how many renders were inside ``render`` at once.

    The assertion is on the PEAK, not on ordering. A test that only checked
    "both finished" would pass just as happily with both running at once, which
    is the state this bead exists to prevent.
    """

    def __init__(self, hold: asyncio.Event | None = None) -> None:
        self.active = 0
        self.peak = 0
        self.started = asyncio.Event()
        self._hold = hold

    async def render(self, lesson, full_path, section_paths=None):
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.set()
        try:
            if self._hold is not None:
                await self._hold.wait()
            else:
                await asyncio.sleep(0)
            full_path.write_bytes(b"audio")
            for p in section_paths or []:
                p.write_bytes(b"audio")
            return []
        finally:
            self.active -= 1


class _FailingRenderer:
    async def render(self, lesson, full_path, section_paths=None):
        msg = "ffmpeg died"
        raise RuntimeError(msg)


@pytest.fixture
def store():
    return ContentStore(":memory:")


async def _render(store, renderer, audio_dir, lesson_id):
    return await render_service.render_lesson_audio(
        store=store,
        renderer=renderer,
        audio_dir=audio_dir,
        lesson_id=lesson_id,
        lesson=_lesson(),
    )


class TestOnlyOneRenderAtATime:
    async def test_two_renders_never_overlap(self, store, tmp_path):
        """The 2026-09-19 shape: two DIFFERENT lessons, rendering at once."""
        renderer = _RecordingRenderer()

        await asyncio.gather(
            _render(store, renderer, tmp_path, "lesson-a"),
            _render(store, renderer, tmp_path, "lesson-b"),
        )

        assert renderer.peak == 1, f"{renderer.peak} renders ran concurrently"

    async def test_the_second_render_still_completes(self, store, tmp_path):
        """Serialising must queue the second render, never drop or refuse it.

        A gate that turned the second one into an error would trade a slow box
        for a lesson with no audio, which is the failure the auto-render exists
        to avoid.
        """
        renderer = _RecordingRenderer()

        await asyncio.gather(
            _render(store, renderer, tmp_path, "lesson-a"),
            _render(store, renderer, tmp_path, "lesson-b"),
        )

        assert store.list_audio_files_for_lesson("lesson-a")
        assert store.list_audio_files_for_lesson("lesson-b")

    async def test_a_failed_render_releases_the_gate(self, store, tmp_path):
        """⚠️ Without a release on the failure path, one crashed render wedges
        every later render in the process — worse than what it replaces, and
        invisible until the next render simply never starts."""
        with pytest.raises(RuntimeError):
            await _render(store, _FailingRenderer(), tmp_path, "lesson-a")

        renderer = _RecordingRenderer()
        await _render(store, renderer, tmp_path, "lesson-b")

        assert store.list_audio_files_for_lesson("lesson-b")

    async def test_a_waiting_render_does_not_block_the_event_loop(self, store, tmp_path):
        """The queued render must WAIT, not spin: the API has to keep answering
        while a render runs, or the learner's page reads as down. On 2026-09-19
        the box was starved enough to 502 — a gate that blocked the loop would
        reproduce that symptom from a different cause.
        """
        hold = asyncio.Event()
        renderer = _RecordingRenderer(hold=hold)

        first = asyncio.create_task(_render(store, renderer, tmp_path, "lesson-a"))
        await renderer.started.wait()
        second = asyncio.create_task(_render(store, renderer, tmp_path, "lesson-b"))

        # The loop is still live while one render holds the gate and another waits.
        await asyncio.sleep(0)
        assert not second.done()

        hold.set()
        await asyncio.gather(first, second)
        assert renderer.peak == 1
