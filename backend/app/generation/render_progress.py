"""Render-rate projection for the pipeline card (tunatale-hbnd).

Pure: it is handed clip counts and wall-clock readings, and answers a question
about them. Nothing here knows what a clip is, where a render runs, or who is
watching.
"""

from __future__ import annotations

# A completion older than this has left the rate window. One minute is a render
# step, not a tick: a 171-clip Cebuano lesson takes minutes, and a shorter
# window on a throttled provider measures jitter instead of throughput.
_WINDOW_S = 60.0

# Below this the render has not been running long enough to project from. The
# first seconds are the least representative ones in the whole render (every
# cache hit lands there), so a number then is worse than no number.
_MIN_RUN_S = 30.0

# Fewer completions than this inside the window is a stall, not a rate: three
# clips is the difference between "I timed something" and "I extrapolated".
_MIN_COMPLETIONS = 3


class RenderProgress:
    """Completed-vs-total clip counts, and a rate measured over a window.

    The rate is deliberately NOT a running average. A render's cache hits all
    finish in its first moments (a re-render after a small change is almost
    all hits), so an average from the start reports a render of seconds for
    one whose real work is throttled to Google's per-minute limit. The window drops
    those hits as soon as they age out, leaving a rate that describes the clips
    actually still to come.

    :meth:`eta_seconds` returns ``None`` rather than a guess whenever the
    reading would not be honest: too young, too few samples, or nothing left.
    """

    def __init__(self, started_at: float) -> None:
        self._started_at = started_at
        self.done = 0
        self.total = 0
        # One entry per completed clip, in completion order. Timestamps, not
        # counts, so the window is a time range and not "the last N updates" —
        # the two differ whenever total climbs without done moving.
        self._completions: list[float] = []

    def update(self, done: int, total: int, now: float) -> None:
        """Record *done* of *total* clips as of *now* (a wall-clock reading).

        One timestamp per clip that finished since the last call, so a caller
        that skipped updates still contributes its whole jump rather than
        undercounting the rate.
        """
        self.done = done
        self.total = total
        while len(self._completions) < done:
            self._completions.append(now)

    def eta_seconds(self, now: float) -> int | None:
        """Seconds until the last clip, or ``None`` when that cannot be said."""
        elapsed = now - self._started_at
        if elapsed < _MIN_RUN_S:
            return None
        # ``>=`` on the window edge: a completion exactly 60 s old is still a
        # rate sample, and dropping it would make the rate jump at the boundary.
        in_window = sum(1 for ts in self._completions if ts >= now - _WINDOW_S)
        if in_window < _MIN_COMPLETIONS or self.done >= self.total:
            return None
        # The denominator is the window, not the elapsed time: past a minute the
        # rate is per-minute, so a render that has run 10 minutes with 6 clips
        # in its last minute is 6/min and not 0.6/min.
        rate = in_window / min(_WINDOW_S, elapsed)
        return round((self.total - self.done) / rate)
