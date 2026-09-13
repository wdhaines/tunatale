"""File-backed calendar-month tally of Azure TTS spend, for quota enforcement.

Azure's free tier (F0) grants 500,000 TTS characters a month and **throttles**
at the cap instead of billing — so the first symptom of exhaustion is a render
that mysteriously stops working. This ledger counts our own spend, one
"<unix_ts> <chars>" line per successfully-processed request, file-backed so
the tally survives uvicorn --reload restarts. Single-process append-only use —
no locking needed.

The bucket is a CALENDAR MONTH, unlike ``UsageLedger``'s continuous leaky
bucket (which was measured against Groq's ``x-ratelimit-reset-requests``
header and refills at a fixed rate). Azure has no refill rate to measure: an
entry either is in the current month bucket or it is not.

The month boundary's TIMEZONE is a knob (``reset_tz``) because Azure documents
the monthly allowance but NOT when the month turns over — the quotas page
defers to the pricing page, which states the monthly figure and says nothing
about the boundary. Rather than guess, the knob exists and defaults to UTC.

Azure also documents: "Each Chinese character is counted as two characters for
billing." TT has no CJK language, so the doubling is deliberately NOT
implemented; this sentence records the rule so nobody has to rediscover it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class AzureBudgetStatus:
    """One snapshot of the Azure character budget for the current month.

    ``exceeded`` names the binding ceiling ("characters per month") or is
    ``None``; ``reset_in_s`` is ALWAYS the real distance to the month boundary
    — a calendar boundary exists whether or not anything was spent, so 0.0
    there would read as "already reset".
    """

    chars_used: int
    chars_limit: int
    reset_in_s: float
    exceeded: str | None


class AzureCharacterLedger:
    """File-backed tally of billable Azure TTS characters for the month."""

    def __init__(self, path: Path, reset_tz: str = "UTC", max_entries: int = 100_000) -> None:
        self._path = path
        self._tz = ZoneInfo(reset_tz)
        self._max_entries = max_entries
        self._entries: list[tuple[float, int]] = self._load()

    def _load(self) -> list[tuple[float, int]]:
        if not self._path.exists():
            return []
        entries = []
        for line in self._path.read_text().splitlines():
            parts = line.split()
            try:
                ts, chars = float(parts[0]), int(parts[1])
            except IndexError, ValueError:
                continue
            entries.append((ts, chars))
        # A hand-edited or interleaved file must not sum in an arbitrary order;
        # chronological order also makes the max_entries rewrite below drop the
        # oldest entries, which are the ones from before the current month.
        return sorted(entries)

    def record(self, chars: int, now: float | None = None) -> None:
        ts = time.time() if now is None else now
        self._entries.append((ts, chars))
        if len(self._entries) > self._max_entries:
            # The calendar-month trim: an entry from before the current month
            # can never count again, so the rewrite keeps only this month's.
            self._entries = sorted((t, c) for t, c in self._entries if self._month_of(t) == self._month_of(ts))
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("".join(f"{t} {c}\n" for t, c in self._entries))
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a") as f:
                f.write(f"{ts} {chars}\n")

    def _month_of(self, ts: float) -> tuple[int, int]:
        """The (year, month) bucket of *ts* in the ledger's reset timezone."""
        local = datetime.fromtimestamp(ts, self._tz)
        return (local.year, local.month)

    def chars_used(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        month = self._month_of(now)
        return sum(chars for ts, chars in self._entries if self._month_of(ts) == month)

    def budget(self, *, chars_limit: int, now: float | None = None) -> AzureBudgetStatus:
        now = time.time() if now is None else now
        chars_used = self.chars_used(now)
        reset_in_s = self._reset_in_s(now)
        exceeded = "characters per month" if chars_used >= chars_limit else None
        return AzureBudgetStatus(
            chars_used=chars_used,
            chars_limit=chars_limit,
            reset_in_s=reset_in_s,
            exceeded=exceeded,
        )

    def _reset_in_s(self, now: float) -> float:
        """Seconds from *now* to midnight on the 1st of the next month.

        The boundary is LOCAL midnight on the 1st, constructed in the ledger's
        own zone. An alternative "midnight UTC converted to the zone" route
        shifts the boundary by the zone's UTC offset — verified 2026-09-13 that
        converting 2026-10-01 00:00 UTC to America/New_York lands at 20:00 on
        Sep 30, four hours short of local midnight. zoneinfo resolves the DST
        offset for the constructed wall time correctly.
        """
        local = datetime.fromtimestamp(now, self._tz)
        year, month = local.year, local.month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
        next_boundary = datetime(year, month, 1, tzinfo=self._tz)
        return next_boundary.timestamp() - now
