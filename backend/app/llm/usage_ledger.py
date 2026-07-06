"""File-backed tally of Groq tokens spent, for the rate-limit status endpoint.

Groq's free-tier daily token cap (TPD) is the binding limit for this app but
appears in no response header (headers carry RPD + TPM only), so TT counts its
own spend. One "<unix_ts> <total_tokens>" line per completion; file-backed so
the tally survives uvicorn --reload restarts. Single-process append-only use —
no locking needed.
"""

from __future__ import annotations

import time
from pathlib import Path

_WINDOW_S = 86_400


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
        return entries

    def record(self, total_tokens: int, now: float | None = None) -> None:
        ts = time.time() if now is None else now
        self._entries.append((ts, total_tokens))
        if len(self._entries) > self._max_entries:
            self._entries = [(t, n) for t, n in self._entries if t >= ts - _WINDOW_S]
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("".join(f"{t} {n}\n" for t, n in self._entries))
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a") as f:
                f.write(f"{ts} {total_tokens}\n")

    def tokens_used_last_24h(self, now: float | None = None) -> int:
        cutoff = (time.time() if now is None else now) - _WINDOW_S
        return sum(n for t, n in self._entries if t >= cutoff)
