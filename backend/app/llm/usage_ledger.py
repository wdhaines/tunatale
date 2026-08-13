"""File-backed leaky-bucket tally of Groq spend, for the rate-limit status endpoint.

Groq's free-tier daily token cap (TPD) appears in no response header, so TT
counts its own spend. One "<unix_ts> <total_tokens>" line per request;
file-backed so the tally survives uvicorn --reload restarts. Single-process
append-only use — no locking needed.

The bucket is CONTINUOUS, not a rolling-24h sum and not a calendar day.
Measured against the live API 2026-08-13 (``openai/gpt-oss-120b``, free plan),
``x-ratelimit-reset-requests`` read ``1m26.4s`` / ``2m52.8s`` / ``4m19.2s``
after 1/2/3 requests — exactly ``86400/1000`` / ``2*86400/1000`` /
``3*86400/1000`` — i.e. capacity = the daily limit refilling at
``limit / 86400`` per second, and the header means "time until the bucket is
full again". A rolling window was wrong, and a fixed midnight boundary more so.
The integer boundary CEILS: an entry is in the bucket until fully drained
(measured: one request costs 86.4s of recovery, so at 86.3s it must still
count), never truncates to zero early.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path

DAY_S = 86_400


@dataclass(frozen=True)
class BudgetStatus:
    """One budget snapshot across both Groq day ceilings.

    ``exceeded`` names the binding ceiling ("tokens per day" wins when both are
    blown); ``reset_in_s`` is the refill ETA for that ceiling, 0.0 when nothing
    is exceeded.
    """

    tokens_used: int
    tokens_limit: int
    tokens_reset_in_s: float
    requests_used: int
    requests_limit: int
    requests_reset_in_s: float
    exceeded: str | None
    reset_in_s: float


class UsageLedger:
    def __init__(self, path: Path, max_entries: int = 10_000) -> None:
        self._path = path
        self._max_entries = max_entries
        self._entries: list[tuple[float, int]] = self._load()

    def _load(self) -> list[tuple[float, int]]:
        if not self._path.exists():
            return []
        entries = []
        for line in self._path.read_text().splitlines():
            parts = line.split()
            try:
                ts, tokens = float(parts[0]), int(parts[1])
            except IndexError, ValueError:
                continue
            entries.append((ts, tokens))
        # A hand-edited or interleaved file must not drain backwards: the drain
        # below walks the log in chronological order.
        return sorted(entries)

    def record(self, total_tokens: int, now: float | None = None) -> None:
        ts = time.time() if now is None else now
        self._entries.append((ts, total_tokens))
        if len(self._entries) > self._max_entries:
            self._entries = sorted((t, n) for t, n in self._entries if t >= ts - DAY_S)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("".join(f"{t} {n}\n" for t, n in self._entries))
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a") as f:
                f.write(f"{ts} {total_tokens}\n")

    def _consumed(self, limit: int, now: float | None, count_requests: bool) -> float:
        """Current leaky-bucket fill level, in ``limit``'s units.

        Each entry consumes its recorded token total (or 1 for a request) and
        the bucket refills at ``limit / DAY_S`` per second between entries and
        up to ``now``. Idle time never banks credit (``max(0.0, ...)`` at every
        step), so a quiet week does not buy a doubled day.
        """
        now = time.time() if now is None else now
        rate = limit / DAY_S
        consumed = 0.0
        last = None
        for ts, tokens in self._entries:
            if last is not None:
                consumed = max(0.0, consumed - (ts - last) * rate)
            consumed += 1.0 if count_requests else tokens
            last = ts
        if last is not None:
            consumed = max(0.0, consumed - (now - last) * rate)
        return consumed

    def tokens_used(self, limit: int, now: float | None = None) -> int:
        return math.ceil(self._consumed(limit, now, count_requests=False))

    def requests_used(self, limit: int, now: float | None = None) -> int:
        return math.ceil(self._consumed(limit, now, count_requests=True))

    def _reset_in_s(self, limit: int, now: float | None, count_requests: bool) -> float:
        consumed = self._consumed(limit, now, count_requests=count_requests)
        if consumed == 0.0:
            return 0.0
        return consumed / (limit / DAY_S)

    def tokens_reset_in_s(self, limit: int, now: float | None = None) -> float:
        return self._reset_in_s(limit, now, count_requests=False)

    def requests_reset_in_s(self, limit: int, now: float | None = None) -> float:
        return self._reset_in_s(limit, now, count_requests=True)

    def budget(
        self,
        *,
        tokens_limit: int,
        requests_limit: int,
        now: float | None = None,
    ) -> BudgetStatus:
        now = time.time() if now is None else now
        tokens_consumed = self._consumed(tokens_limit, now, count_requests=False)
        requests_consumed = self._consumed(requests_limit, now, count_requests=True)
        tokens_used = math.ceil(tokens_consumed)
        requests_used = math.ceil(requests_consumed)
        tokens_reset_in_s = tokens_consumed / (tokens_limit / DAY_S) if tokens_consumed > 0 else 0.0
        requests_reset_in_s = requests_consumed / (requests_limit / DAY_S) if requests_consumed > 0 else 0.0
        exceeded = None
        reset_in_s = 0.0
        if tokens_used >= tokens_limit:
            exceeded = "tokens per day"
            reset_in_s = tokens_reset_in_s
        elif requests_used >= requests_limit:
            exceeded = "requests per day"
            reset_in_s = requests_reset_in_s
        return BudgetStatus(
            tokens_used=tokens_used,
            tokens_limit=tokens_limit,
            tokens_reset_in_s=tokens_reset_in_s,
            requests_used=requests_used,
            requests_limit=requests_limit,
            requests_reset_in_s=requests_reset_in_s,
            exceeded=exceeded,
            reset_in_s=reset_in_s,
        )
