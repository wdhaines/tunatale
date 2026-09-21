"""Background work must be observable after the request returns (tunatale-rwkz.6).

A sync's prestage kept the box at load 5.58 for minutes after the POST returned
200, and nothing said so: an agent waiting for an idle box had to infer it from
log volume, and got it wrong twice. These pin the tracker that makes the end of
that work readable, and the route that reads it.
"""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.background_work import BackgroundWork, background_work
from app.main import app


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class TestTracker:
    async def test_counts_a_job_while_it_runs_and_releases_it_after(self):
        work = BackgroundWork()
        started, release = asyncio.Event(), asyncio.Event()

        async def job() -> None:
            started.set()
            await release.wait()

        task = asyncio.create_task(work.track("prestage_images", job)())
        await started.wait()

        mid = work.snapshot()
        assert mid["idle"] is False
        assert mid["inflight"] == {"prestage_images": 1}

        release.set()
        await task

        after = work.snapshot()
        assert after["idle"] is True
        assert after["inflight"] == {}
        assert after["completed"] == {"prestage_images": 1}
        assert after["failed"] == {}

    async def test_two_kinds_in_flight_are_counted_separately(self):
        work = BackgroundWork()
        release = asyncio.Event()
        running = 0
        both = asyncio.Event()

        async def job() -> None:
            nonlocal running
            running += 1
            if running == 2:
                both.set()
            await release.wait()

        tasks = [
            asyncio.create_task(work.track("prestage_images", job)()),
            asyncio.create_task(work.track("prestage_cloze", job)()),
        ]
        await both.wait()
        assert work.snapshot()["inflight"] == {"prestage_images": 1, "prestage_cloze": 1}
        release.set()
        await asyncio.gather(*tasks)
        assert work.snapshot()["idle"] is True

    async def test_two_of_one_kind_count_down_one_at_a_time(self):
        """Two syncs in quick succession each schedule a prestage; the first to
        finish must leave the second still counted, not clear the kind."""
        work = BackgroundWork()
        release_second = asyncio.Event()
        started = 0
        both = asyncio.Event()

        async def quick() -> None:
            nonlocal started
            started += 1
            if started == 2:
                both.set()
            await both.wait()

        async def slow() -> None:
            nonlocal started
            started += 1
            if started == 2:
                both.set()
            await release_second.wait()

        a = asyncio.create_task(work.track("prestage_images", quick)())
        b = asyncio.create_task(work.track("prestage_images", slow)())
        await a
        assert work.snapshot()["inflight"] == {"prestage_images": 1}
        release_second.set()
        await b
        assert work.snapshot()["completed"] == {"prestage_images": 2}

    async def test_a_failing_job_still_releases_and_the_error_propagates(self):
        """The exception must escape exactly as before — Starlette logs it — and
        the counter must not be left stuck at 1, which would read as busy forever."""
        work = BackgroundWork()

        async def job() -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await work.track("prestage_cloze", job)()

        snap = work.snapshot()
        assert snap["idle"] is True
        assert snap["failed"] == {"prestage_cloze": 1}
        assert snap["completed"] == {}
        assert snap["last_finished"]["ok"] is False

    async def test_last_finished_reports_kind_duration_and_age(self):
        clock, wall = _Clock(), _Clock()
        work = BackgroundWork(clock=clock, wall_clock=wall)

        async def job() -> None:
            clock.now += 42.25

        await work.track("listen_media", job)()
        wall.now += 7.0

        last = work.snapshot()["last_finished"]
        assert last == {"kind": "listen_media", "ok": True, "elapsed_s": 42.2, "finished_ago_s": 7.0}

    def test_nothing_finished_yet_reports_none(self):
        assert BackgroundWork().snapshot() == {
            "idle": True,
            "inflight": {},
            "completed": {},
            "failed": {},
            "last_finished": None,
        }

    async def test_a_sync_callable_runs_off_the_event_loop(self):
        """Starlette runs a plain function in the threadpool. Wrapping it in an
        async tracker must not silently move it onto the event loop, where a
        blocking fetch would stall every request."""
        work = BackgroundWork()
        seen: dict = {}

        def job(x: int, *, y: int) -> None:
            seen["thread"] = threading.current_thread()
            seen["args"] = (x, y)

        await work.track("sync_job", job)(1, y=2)

        assert seen["args"] == (1, 2)
        assert seen["thread"] is not threading.main_thread()

    async def test_each_finish_logs_one_greppable_line(self, caplog):
        """The log line is the instrument for anyone reading `docker compose logs`
        rather than calling the route."""
        work = BackgroundWork()

        async def job() -> None:
            return None

        with caplog.at_level(logging.INFO, logger="app.common.background_work"):
            await work.track("prestage_images", job)()

        lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("BACKGROUND_DONE")]
        assert len(lines) == 1
        assert "kind=prestage_images" in lines[0]
        assert "ok=1" in lines[0]
        assert "inflight=0" in lines[0]


class TestAppAccessor:
    def test_creates_one_tracker_per_app_and_reuses_it(self):
        from fastapi import FastAPI

        fresh = FastAPI()
        first = background_work(fresh)
        assert isinstance(first, BackgroundWork)
        assert background_work(fresh) is first


class TestRoute:
    async def test_reports_the_apps_tracker(self):
        work = background_work(app)
        release = asyncio.Event()
        started = asyncio.Event()

        async def job() -> None:
            started.set()
            await release.wait()

        task = asyncio.create_task(work.track("rwkz6_route_probe", job)())
        await started.wait()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            busy = (await client.get("/api/admin/background-work")).json()
            release.set()
            await task
            idle = (await client.get("/api/admin/background-work")).json()

        assert busy["idle"] is False
        assert busy["inflight"]["rwkz6_route_probe"] == 1
        assert "rwkz6_route_probe" not in idle["inflight"]
        assert idle["last_finished"]["kind"] == "rwkz6_route_probe"
        assert idle["last_finished"]["ok"] is True
