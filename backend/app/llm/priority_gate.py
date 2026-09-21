"""One-at-a-time admission to Groq, foreground first (tunatale-rwkz.3).

Before this, pacing was a single timestamp, ``LLMClient._next_call_at``: every
caller computed its own wait, slept until that same instant, and then they all
fired at once in whatever order they woke. So on 2026-09-19 a user-facing import
queued behind ~30 prestage calls from a sync that had just finished, with nothing
to prefer it, and the simultaneous burst drew a 429 that pushed the lemma
resolver onto its fallback.

The gate fixes both halves. It admits ONE caller at a time, so a paced burst is
spaced rather than simultaneous. And it chooses WHO at the moment the slot
opens, not when the wait began: a foreground call arriving during a 57-second
pacing wait takes that slot ahead of every background caller already waiting.

⚠️ This orders the work; it does not pace less. The pacing delays are what keep
TT inside Groq's free tier and they are applied exactly as before, now by the
gate instead of by each caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass, field

FOREGROUND = 0
BACKGROUND = 1


@dataclass(order=True)
class _Waiter:
    priority: int
    ticket: int
    wake: asyncio.Event = field(compare=False, default_factory=asyncio.Event)


class PriorityGate:
    """A lock whose next holder is the lowest ``(priority, ticket)`` waiter.

    ``ready_at`` returns the monotonic instant before which nobody may be
    admitted (the pacing deadline). The waiter at the head of the queue sleeps
    until then; any arrival or release wakes every waiter so the head is
    re-chosen, which is how a later foreground call overtakes.
    """

    def __init__(self, ready_at: Callable[[], float]) -> None:
        self._ready_at = ready_at
        self._held = False
        self._queue: list[_Waiter] = []
        self._tickets = itertools.count()

    def ticket(self) -> int:
        """A place in line, kept across a caller's retries so FIFO survives them."""
        return next(self._tickets)

    def waiting(self) -> int:
        return len(self._queue)

    async def acquire(self, priority: int, ticket: int) -> None:
        """Wait until this caller is at the head, the gate is free, and the
        pacing deadline has passed; then hold the gate."""
        me = _Waiter(priority, ticket)
        heapq.heappush(self._queue, me)
        self._wake_all()
        try:
            while True:
                if not self._held and self._queue[0] is me:
                    wait = self._ready_at() - time.monotonic()
                    if wait <= 0:
                        heapq.heappop(self._queue)
                        self._held = True
                        return
                    # Sleep out the deadline, but wake early on any arrival or
                    # release so the head is re-chosen: that is the overtake.
                    me.wake.clear()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(me.wake.wait(), timeout=wait)
                else:
                    me.wake.clear()
                    await me.wake.wait()
        except BaseException:
            # Always still queued here: the pop above returns without an await,
            # so a cancellation can only land while this waiter is in the queue.
            self._queue.remove(me)
            heapq.heapify(self._queue)
            self._wake_all()
            raise

    def release(self) -> None:
        self._held = False
        self._wake_all()

    def _wake_all(self) -> None:
        for waiter in self._queue:
            waiter.wake.set()
