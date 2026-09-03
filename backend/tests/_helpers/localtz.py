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
