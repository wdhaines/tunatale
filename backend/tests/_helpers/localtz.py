"""Pin the process-local timezone for a block of test code.

Anki's study-day index is a **local wall-clock** quantity: ``col.sched.today``
counts local calendar dates and rolls over at 4 AM local (see
``app.srs.anki_mirror.protobuf_wire.anki_today_col_day``). So any test asserting
an absolute day index — or an ``elapsed_days`` derived from one — is only
meaningful once it says which zone it means. Pinning is *declaring* the zone,
which is the opposite of the wall-clock assumption ``backend-hostile-tz`` and
``backend-hostile-hour`` exist to catch.

``TZ`` is set in the environment (not just in a ``tzinfo`` object) because
``datetime.astimezone()`` with no argument consults the C library's zone, and
because ``run_oracle`` spawns Anki in a subprocess that must agree with us about
what day it is.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime


def timezone_with_local_hour(hour: int, now: datetime | None = None) -> str:
    """Name an ``Etc/GMT`` zone whose LOCAL clock is inside *hour* right now.

    Lets a test sit in a wall-clock band deterministically instead of waiting for
    the machine's clock to wander into it — the same trick CI's
    ``.github/actions/hostile-hour-tz`` uses for the 04:00 rollover, which
    existed for a year as the only defence and was never pointed at the Anki
    parity suite (see that action for the 1-run-in-598 measurement).

    ⚠️ ``Etc/GMT`` signs are INVERTED versus the offset they name: ``Etc/GMT-3``
    is UTC+3. The assertion below is what proves the sign was applied the right
    way round rather than producing a plausible zone in the wrong direction.
    """
    utc_hour = (now or datetime.now(UTC)).hour
    offset = (hour - utc_hour) % 24
    if offset > 14:  # Etc/GMT tops out at +14/-12; fold to the negative side
        offset -= 24
    name = f"Etc/GMT-{offset}" if offset >= 0 else f"Etc/GMT+{-offset}"
    with local_timezone(name):
        resolved = datetime.now().hour
    if resolved != hour:  # pragma: no cover - guards a sign error, not a branch
        raise AssertionError(f"{name} gives local hour {resolved}, wanted {hour} — the probe is broken, not the suite")
    return name


@contextmanager
def local_timezone(name: str) -> Iterator[None]:
    """Run the block with ``TZ=name`` applied to both this process and children."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = previous
        time.tzset()
