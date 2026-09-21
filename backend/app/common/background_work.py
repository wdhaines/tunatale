"""In-flight accounting for fire-and-forget work (tunatale-rwkz.6).

A peer-sync schedules image and cloze prestage as Starlette background tasks,
and ``/listen`` defers its media fetches the same way. That is deliberate — the
request returns promptly — but it made the END of the work unobservable: on
2026-09-19 the sync POST returned 200 and the box sat at load 5.58 for minutes,
with 77 LLM calls in two minutes and nothing anywhere saying work was still
running. An agent waiting for an idle box inferred idleness from log volume and
was wrong twice.

So every such job is wrapped by :meth:`BackgroundWork.track`, which counts it
while it runs and logs one ``BACKGROUND_DONE`` line when it finishes. The counts
are read from ``GET /api/admin/background-work``.

It also marks the job's context as background (:func:`in_background`), which
is how the LLM client knows to let a foreground call go first (tunatale-rwkz.3).
It never throttles the work, and it must not make prestage synchronous with the
sync.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

# True inside a tracked job. The LLM client reads it to give such calls lower
# priority than a request someone is waiting on (tunatale-rwkz.3), so anything
# scheduled through track() is deprioritised without each call site declaring it.
_IN_BACKGROUND: ContextVar[bool] = ContextVar("tt_in_background", default=False)


def in_background() -> bool:
    """Whether the current task is running inside :meth:`BackgroundWork.track`."""
    return _IN_BACKGROUND.get()


class BackgroundWork:
    """Counts background jobs by kind: running now, finished, failed."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self._inflight: dict[str, int] = {}
        self._completed: dict[str, int] = {}
        self._failed: dict[str, int] = {}
        self._last: dict[str, Any] | None = None

    def track(self, kind: str, fn: Callable[..., Any]) -> Callable[..., Awaitable[None]]:
        """Wrap *fn* so each call is counted under *kind* for as long as it runs.

        A plain function still runs in the threadpool, as Starlette would have
        run it — wrapping it in a coroutine must not move blocking work onto the
        event loop. An exception propagates unchanged after being counted.
        """

        async def run(*args: Any, **kwargs: Any) -> None:
            self._inflight[kind] = self._inflight.get(kind, 0) + 1
            start = self._clock()
            ok = False
            token = _IN_BACKGROUND.set(True)
            try:
                if inspect.iscoroutinefunction(fn):
                    await fn(*args, **kwargs)
                else:
                    await run_in_threadpool(fn, *args, **kwargs)
                ok = True
            finally:
                _IN_BACKGROUND.reset(token)
                self._finish(kind, ok, self._clock() - start)

        return run

    def _finish(self, kind: str, ok: bool, elapsed: float) -> None:
        remaining = self._inflight[kind] - 1
        if remaining:
            self._inflight[kind] = remaining
        else:
            del self._inflight[kind]
        tally = self._completed if ok else self._failed
        tally[kind] = tally.get(kind, 0) + 1
        self._last = {"kind": kind, "ok": ok, "elapsed_s": round(elapsed, 1), "finished_at": self._wall_clock()}
        logger.info(
            "BACKGROUND_DONE kind=%s ok=%d elapsed_s=%.1f inflight=%d",
            kind,
            ok,
            elapsed,
            sum(self._inflight.values()),
        )

    def snapshot(self) -> dict[str, Any]:
        last = None
        if self._last is not None:
            last = {
                "kind": self._last["kind"],
                "ok": self._last["ok"],
                "elapsed_s": self._last["elapsed_s"],
                "finished_ago_s": round(self._wall_clock() - self._last["finished_at"], 1),
            }
        return {
            "idle": not self._inflight,
            "inflight": dict(self._inflight),
            "completed": dict(self._completed),
            "failed": dict(self._failed),
            "last_finished": last,
        }


def background_work(app: Any) -> BackgroundWork:
    """The app's tracker, created on first use.

    Lazily, rather than in the lifespan, so tests that build ``app.state`` by
    hand — most of them — get one without seeding it.
    """
    work = getattr(app.state, "background_work", None)
    if work is None:
        work = BackgroundWork()
        app.state.background_work = work
    return work
