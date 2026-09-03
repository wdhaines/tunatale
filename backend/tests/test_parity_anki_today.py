"""Oracle parity for Anki's study-day index (``col.sched.today``).

Why this file exists
--------------------
``compute_anki_day_index`` was documented as "matching what Anki writes to
``decks.common`` field 3" and was used for *both* day-index questions TT asks.
It is a pure arithmetic expression — ``(now - crt + rollover) // 86400`` — with
no notion of a local calendar, so its day boundary lands at the UTC instant
``col.crt - rollover_hour``. Anki's boundary is ``rollover_hour`` **local**,
counted in calendar dates, and is therefore independent of ``col.crt``'s
time-of-day. The two coincide only when ``col.crt``'s time-of-day happens to be
the rollover hour in the reader's current zone.

That made every consumer of "today" wrong inside a daily window whose width is
the mismatch between those two boundaries. For a real collection read in its own
zone the window is ``[local midnight, 04:00)``. For CI (``TZ=UTC``) reading the
UTC-5 collection this suite's fixtures are modelled on, it is ``[04:00, 05:00)``
UTC — which is exactly where ``anki-gates`` went red on 2026-09-03 with
``elapsed_days``: Anki 127 vs TT 126.

The matrix below is the evidence for the replacement rule, not a smoke test: it
is the control that says the old formula really was wrong and the new one really
is right, measured against the Anki backend rather than against our own
arithmetic restated.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests._helpers.localtz import local_timezone
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import SyntheticCollection

# Four zones chosen to separate the two candidate rules rather than to be exotic:
# UTC is what CI runs; Etc/GMT+8 is far enough west that its local clock is still
# *before* its 4 AM rollover for much of the UTC day (the case that pins the
# "subtract one until rollover has passed" half of the rule); Etc/GMT-3 is inside
# the UTC+2..+4 band this repo's only real offset bug lives in; America/New_York
# is the user's own zone, and the one whose historical (winter) offset differs
# from its current (summer) one — the DST case.
_ZONES = ("UTC", "Etc/GMT+8", "Etc/GMT-3", "America/New_York")

# col.crt times-of-day. 4 is the "well-behaved" collection (crt AT the rollover);
# the others are what a collection looks like when read from a zone other than
# the one it was created in — which is the whole failure mode.
_CRT_HOURS_UTC = (0, 4, 9, 12, 20)


@pytest.mark.oracle
def test_anki_today_col_day_matches_oracle_across_zones(synthetic_collection: SyntheticCollection) -> None:
    """``anki_today_col_day`` == ``col.sched.today`` for every zone × crt time-of-day.

    The old ``compute_anki_day_index`` is measured alongside and asserted to be
    wrong *somewhere* in the matrix: if it ever agrees everywhere, this matrix
    has stopped discriminating (most likely because it is being run at a wall
    clock where every zone sits on the same side of both boundaries) and the
    parity claim it backs is vacuous.
    """
    from app.srs.anki_mirror.protobuf_wire import anki_today_col_day, compute_anki_day_index

    mismatches: list[str] = []
    old_rule_disagreements = 0

    for zone in _ZONES:
        with local_timezone(zone):
            for crt_hour in _CRT_HOURS_UTC:
                col_crt = int(datetime(2020, 1, 1, crt_hour, tzinfo=UTC).timestamp())
                synthetic_collection.col_crt = col_crt
                synthetic_collection.save()

                raw = run_oracle(synthetic_collection.path, [{"op": "get_today"}]).raw()
                key = next(k for k in raw if k.startswith("get_today"))
                anki_today = raw[key]["today"]

                now = datetime.now(tz=UTC)
                new_rule = anki_today_col_day(col_crt, now)
                old_rule = compute_anki_day_index(col_crt, 4, now)

                if new_rule != anki_today:
                    mismatches.append(
                        f"tz={zone} crt={crt_hour:02d}:00Z anki={anki_today} "
                        f"anki_today_col_day={new_rule} now={now.isoformat()}"
                    )
                if old_rule != anki_today:
                    old_rule_disagreements += 1

    assert not mismatches, "anki_today_col_day diverged from col.sched.today:\n  " + "\n  ".join(mismatches)
    assert old_rule_disagreements > 0, (
        "compute_anki_day_index agreed with Anki in all "
        f"{len(_ZONES) * len(_CRT_HOURS_UTC)} cases, so this matrix proved nothing. "
        "Both rules coincide when every zone is on the same side of both day "
        "boundaries; widen the zone set rather than trusting the pass."
    )
