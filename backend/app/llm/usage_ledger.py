"""File-backed leaky-bucket tally of Groq spend, for the rate-limit status endpoint.

Groq's free-tier daily token cap (TPD) appears in no response header, so TT
counts its own spend. One line per request: the legacy two-field form
``<unix_ts> <total_tokens>`` plus, since 2026-09 (bead 6zzu2), a six-field form
``<unix_ts> <total_tokens> <prompt_tokens> <completion_tokens>
<reasoning_tokens> <call_site>``. Field 1 stays ``total_tokens`` forever, so
old and new lines are read by identical code; a two-field line means "total
known, split unknown". File-backed so the tally survives uvicorn --reload
restarts. Single-process append-only use — no locking needed.

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


@dataclass(frozen=True)
class UsageSplit:
    """Prompt/completion/reasoning breakdown of the ledger's recent token spend.

    Observability only — the budget arithmetic never reads these. ``None`` at
    the aggregate level (see ``UsageLedger.split_used``) means "no split data",
    never a guessed 0.
    """

    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int


@dataclass(frozen=True)
class _Entry:
    """One ledger line. ``prompt/completion/reasoning`` are ``None`` for a
    legacy two-field line — "total known, split unknown" — never 0."""

    ts: float
    total: int
    prompt: int | None = None
    completion: int | None = None
    reasoning: int | None = None


def _format_line(entry: _Entry) -> str:
    """Serialize one entry: 6 fields when the split is fully known, else 2.

    A partial split is treated as unknown (all-or-nothing) — never written as
    0 or a ``None`` literal that read as a number.
    """
    if entry.prompt is not None and entry.completion is not None and entry.reasoning is not None:
        return f"{entry.ts} {entry.total} {entry.prompt} {entry.completion} {entry.reasoning} -\n"
    return f"{entry.ts} {entry.total}\n"


class UsageLedger:
    def __init__(self, path: Path, max_entries: int = 10_000) -> None:
        self._path = path
        self._max_entries = max_entries
        self._entries: list[_Entry] = self._load()

    def _load(self) -> list[_Entry]:
        if not self._path.exists():
            return []
        entries = []
        for line in self._path.read_text().splitlines():
            parts = line.split()
            try:
                ts, tokens = float(parts[0]), int(parts[1])
            except IndexError, ValueError:
                continue
            prompt = completion = reasoning = None
            if len(parts) >= 6:
                try:  # noqa: SIM105
                    prompt, completion, reasoning = int(parts[2]), int(parts[3]), int(parts[4])
                except ValueError, IndexError:
                    pass  # unparseable split → unknown (None); the total still counts
            entries.append(_Entry(ts, tokens, prompt, completion, reasoning))
        # A hand-edited or interleaved file must not drain backwards: the drain
        # below walks the log in chronological order.
        return sorted(entries, key=lambda e: e.ts)

    def record(
        self,
        total_tokens: int,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        now: float | None = None,
    ) -> None:
        ts = time.time() if now is None else now
        entry = _Entry(ts, total_tokens, prompt_tokens, completion_tokens, reasoning_tokens)
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            self._entries = sorted((e for e in self._entries if e.ts >= ts - DAY_S), key=lambda e: e.ts)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("".join(_format_line(e) for e in self._entries))
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a") as f:
                f.write(_format_line(entry))

    def split_used(self, now: float | None = None) -> UsageSplit | None:
        """Raw token split summed over the last ``DAY_S`` seconds.

        ⚠️ This is a FLAT ROLLING-24h SUM and is deliberately **not** the leaky
        bucket that ``tokens_used`` reports — the two answer different
        questions and will not agree. ``tokens_used`` is bucket FILL (headroom:
        how close the cap is), which drains continuously at ``limit / DAY_S``;
        this is actual SPEND (volume: which half to attack), which does not
        drain. Measured 2026-09-13: 100k tokens spread evenly over 24h gives
        ``tokens_used == 0`` and a split summing to 100,000. Spend is the right
        unit for choosing between prompt hygiene and reasoning effort, so the
        sum stays — the API field names carry the ``_24h`` window to stop anyone
        comparing them with the ``_day`` bucket figures.

        Returns ``None`` when no loaded entry carries a known split (a legacy
        two-field log) — "unknown", never a guessed 0. Otherwise sums the known
        splits; entries with an unknown split contribute nothing.
        """
        now = time.time() if now is None else now
        prompt = completion = reasoning = 0
        has_split = False
        for entry in self._entries:
            if entry.ts < now - DAY_S:
                continue
            if entry.prompt is not None and entry.completion is not None and entry.reasoning is not None:
                prompt += entry.prompt
                completion += entry.completion
                reasoning += entry.reasoning
                has_split = True
        return UsageSplit(prompt, completion, reasoning) if has_split else None

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
        for entry in self._entries:
            if last is not None:
                consumed = max(0.0, consumed - (entry.ts - last) * rate)
            consumed += 1.0 if count_requests else entry.total
            last = entry.ts
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
