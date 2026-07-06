"""In-memory ring-buffer activity log for LLM calls and pipeline events."""

from __future__ import annotations

import time
from collections import deque


class ActivityLog:
    """Bounded event log with monotonic sequence numbers.

    Synchronous — safe for event-loop use from a single thread.
    """

    def __init__(self, maxlen: int = 300) -> None:
        self._events: deque[dict] = deque(maxlen=maxlen)
        self._seq: int = 0

    def record_llm_call(self, info: dict) -> None:
        self._seq += 1
        self._events.append({**info, "seq": self._seq, "kind": "llm_call"})

    def record_pipeline(
        self,
        curriculum_id: str,
        day: int,
        state: str,
        message: str,
    ) -> None:
        self._seq += 1
        self._events.append(
            {
                "seq": self._seq,
                "kind": "pipeline",
                "timestamp": time.time(),
                "curriculum_id": curriculum_id,
                "day": day,
                "state": state,
                "message": message,
            }
        )

    def events_since(self, seq: int) -> tuple[list[dict], int]:
        events = [e for e in self._events if e["seq"] > seq]
        return events, self._seq
