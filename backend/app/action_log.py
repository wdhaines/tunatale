"""Durable, dated record of successful write requests (tunatale-5i1p.1).

WHY THIS EXISTS. The live server's ``run/api.log`` restarts with the server and
carries no dates, so nobody could say which actions the owner actually uses —
and a feature is not removed on a guess. One line per successful, learner-facing
write request answers that with evidence: which route, which day, how often.

Pure functions only: no ``settings`` import (``main`` imports it lazily), no
module-level side effects. The appending middleware lives in
``app/main.py::_count_write_actions``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: Not learner actions: sign-in traffic, and the opt-in browser debug channel.
EXCLUDED_PREFIXES = ("/api/auth/", "/api/client-log")


def is_counted(method: str, route: str, status_code: int) -> bool:
    """True only for a successful write to a learner-facing API route."""
    return (
        method in WRITE_METHODS
        and route.startswith("/api/")
        and not route.startswith(EXCLUDED_PREFIXES)
        and status_code < 400
    )


def record(
    path: Path,
    *,
    when: datetime,
    method: str,
    route: str,
    language: str | None,
    user_id: int | None,
) -> None:
    """Append exactly one dated line. Creates the parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        f"{when.isoformat(timespec='seconds')} {method} {route} "
        f"lang={language or '-'} user={'-' if user_id is None else user_id}\n"
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def daily_counts(path: Path) -> tuple[dict[tuple[str, str, str], int], int]:
    """``(day, method, route) -> count``, plus how many lines were malformed.

    A missing file is ``({}, 0)``. A line with fewer than three
    space-separated fields is not counted — it is reported in the second value
    rather than silently dropped.
    """
    counts: dict[tuple[str, str, str], int] = {}
    malformed = 0
    if not path.exists():
        return counts, malformed
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 3:
            malformed += 1
            continue
        key = (line[:10], fields[1], fields[2])
        counts[key] = counts.get(key, 0) + 1
    return counts, malformed


def format_counts(counts: dict[tuple[str, str, str], int]) -> list[str]:
    """One line per key, sorted by (day, method, route)."""
    return [f"{day} {count:>5}  {method} {route}" for (day, method, route), count in sorted(counts.items())]


def main(argv: list[str] | None = None) -> int:
    """Print the per-day counts; ``argv[0]`` overrides the configured path."""
    args = argv or []
    if args:
        path = Path(args[0])
    else:
        from app.config import settings

        path = settings.action_log
    counts, malformed = daily_counts(path)
    for line in format_counts(counts):
        print(line)
    if malformed > 0:
        print(f"malformed lines: {malformed}")
    return 0
