"""FSRS load-balancer parity (Layer 53).

Pins TT's load-balancer port (`app/srs/load_balancer.py` + the fuzz hooks in
`fsrs.py`) against Anki's V3Scheduler with the FSRS load balancer enabled. With
a controlled due histogram, Anki's `get_scheduling_states` relocates the chosen
interval to a less-loaded day within the fuzz range; TT's `find_interval` must
land on the identical day.

Why self-calibrating: the harness subprocess runs at real wall-clock time, so
`days_elapsed` for the seed card can differ from the test process's value by ±1
at a rollover boundary (see anki-oracle-harness.md "Time-travel"). We therefore
gate the load-balanced assertion on TT's *pure-fuzz* baseline already matching
Anki's LB-off result — when it does, inputs are aligned and any LB-on mismatch
is a real balancer-port bug; when it doesn't (rare boundary), we skip rather
than report a spurious failure. Pure-function coverage of the port itself lives
in the non-oracle `test_load_balancer.py`.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models.srs_item import Rating
from app.srs.database import SRSDatabase
from app.srs.fsrs import (
    DEFAULT_FSRS5_PARAMS,
    FSRSParams,
    _forgetting_curve,
    _next_interval_raw,
    _next_stability_recall,
    _passing_intervals_with_fuzz,
    _quantize_stability,
)
from app.srs.load_balancer import LoadBalancer
from app.srs.queue_stats import build_live_load_balancer
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import COL_CRT, DEFAULT_DESIRED_RETENTION, SyntheticCollection

FSRS_WEIGHTS = DEFAULT_FSRS5_PARAMS.weights
_ELAPSED_DAYS = 22
_TEST_AKID = 4242424242  # fixed so the fuzz seed (id + reps) is stable
_TEST_NID = 424242
_REPS = 21
_IVL = 22
_STABILITY = 14.194
_DIFFICULTY = 9.755

# Heavy uniform load on every day 1..98 EXCEPT two valleys, so the balancer is
# pulled to a valley inside whichever fuzz window the seed card lands in.
_LOAD_PER_DAY = 60
_VALLEYS = {26, 43}


def _build(path: Path, *, load_balancer_enabled: bool) -> int:
    """Build the collection; returns the col-day used as 'today' when placing cards."""
    coll = SyntheticCollection(path)
    coll.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    coll.set_config_value("loadBalancerEnabled", load_balancer_enabled)

    now_secs = int(time.time())
    today = (now_secs - COL_CRT) // 86400  # placement frame; reconciled to Anki's `today` later

    # Seed card under test (due today, known FSRS state).
    coll.add_note(id=_TEST_NID, guid="g-test", fields=["front", "back"])
    coll.add_card(
        id=_TEST_AKID,
        note_id=_TEST_NID,
        ord=0,
        type=2,
        queue=2,
        due=today,
        ivl=_IVL,
        reps=_REPS,
        stability=_STABILITY,
        difficulty=_DIFFICULTY,
        last_review_secs=now_secs - _ELAPSED_DAYS * 86400,
    )

    # Pile cards forming the histogram (review cards due today+offset).
    cid = 5_000_000
    for offset in range(1, 99):
        if offset in _VALLEYS:
            continue
        for _ in range(_LOAD_PER_DAY):
            coll.add_note(id=cid, guid=f"g-{cid}", fields=[f"f{cid}", "b"])
            coll.add_card(
                id=cid,
                note_id=cid,
                ord=0,
                type=2,
                queue=2,
                due=today + offset,
                ivl=offset,
                reps=3,
                stability=float(max(offset, 1)),
                difficulty=5.0,
                last_review_secs=now_secs - offset * 86400,
            )
            cid += 1
    coll.save()
    return today


def _tt_intervals(load_balancer: LoadBalancer | None) -> tuple[int, int, int]:
    """TT's (hard, good, easy) for the seed card via the cascade + (optional) balancer."""
    params = FSRSParams(weights=FSRS_WEIGHTS, desired_retention=DEFAULT_DESIRED_RETENTION)
    neg_decay = -params.decay
    r = _forgetting_curve(_ELAPSED_DAYS, _STABILITY, neg_decay)
    raws = []
    for rating in (Rating.HARD, Rating.GOOD, Rating.EASY):
        s = _next_stability_recall(_DIFFICULTY, _STABILITY, r, rating, params.weights)
        raws.append(_next_interval_raw(_quantize_stability(max(0.001, s)), params.desired_retention, neg_decay))
    return _passing_intervals_with_fuzz(
        raws[0],
        raws[1],
        raws[2],
        _IVL,
        _TEST_AKID,
        _REPS,
        params.maximum_review_interval,
        load_balancer=load_balancer,
        note_id=_TEST_NID,
    )


def _make_balancer(placement_today: int, anki_today: int) -> LoadBalancer:
    """Rebuild the histogram in Anki's `today` frame.

    Cards were placed at ``placement_today + offset`` (a naive col-day); Anki
    buckets by ``due - anki_today``. The frames can differ by a day (rollover /
    timezone), so we shift every entry by ``placement_today - anki_today``.
    """
    shift = placement_today - anki_today
    lb = LoadBalancer(None, COL_CRT)
    lb.add_card(_TEST_AKID, _TEST_NID, shift)  # test card was due `placement_today`
    cid = 5_000_000
    for offset in range(1, 99):
        if offset in _VALLEYS:
            continue
        for _ in range(_LOAD_PER_DAY):
            lb.add_card(cid, cid, offset + shift)
            cid += 1
    return lb


@pytest.mark.oracle
def test_load_balancer_matches_anki(synthetic_collection: SyntheticCollection, tmp_path: Path) -> None:
    """TT's load-balanced interval matches Anki's, and the balancer actually engaged."""
    on_path = synthetic_collection.path
    placement_today = _build(on_path, load_balancer_enabled=True)
    off_path = tmp_path / "lb_off.anki2"
    _build(off_path, load_balancer_enabled=False)

    anki_on = run_oracle(on_path, [{"op": "scheduling_states", "deck_id": 1, "card_id": _TEST_AKID}]).raw()[
        "scheduling_states_0"
    ]
    anki_off = run_oracle(off_path, [{"op": "scheduling_states", "deck_id": 1, "card_id": _TEST_AKID}]).raw()[
        "scheduling_states_0"
    ]

    tt_off = _tt_intervals(None)
    tt_on = _tt_intervals(_make_balancer(placement_today, anki_on["today"]))

    # Self-calibration: only trust the comparison when the pure-fuzz baselines
    # agree (⇒ days_elapsed and the fuzz window are aligned across processes).
    if (tt_off[1], tt_off[2]) != (anki_off["good"], anki_off["easy"]):
        pytest.skip(
            f"pure-fuzz baseline misaligned (rollover boundary): TT_off={tt_off} Anki_off={anki_off}; "
            "balancer correctness is pinned by test_load_balancer.py"
        )

    # The balancer must reproduce Anki's relocated pick bit-exact.
    assert tt_on[1] == anki_on["good"], f"GOOD: TT={tt_on[1]} Anki={anki_on['good']}"
    assert tt_on[2] == anki_on["easy"], f"EASY: TT={tt_on[2]} Anki={anki_on['easy']}"

    # And it must have actually engaged (moved the pick off the pure-fuzz value),
    # otherwise the test would pass trivially without exercising the balancer.
    assert (anki_on["good"], anki_on["easy"]) != (anki_off["good"], anki_off["easy"]), (
        "load balancer did not change the interval — scenario not exercising it"
    )


def _mirror_histogram_into_tt(placement_today: int) -> SRSDatabase:
    """Build an in-memory TT DB whose collocation_directions mirror the synthetic
    Anki histogram (same anki_due col-day values, one note per pile card).

    This is the production input to build_live_load_balancer: its histogram query
    reads these rows, so a balancer built from it must match one built from the
    Anki collection directly.
    """
    db = SRSDatabase(":memory:")
    db.set_anki_state_cache("load_balancer_enabled", "true")
    db.set_anki_state_cache("col_crt", str(COL_CRT))
    coll_rows: list[tuple] = [(1, "seed", _TEST_NID)]
    dir_rows: list[tuple] = [(1, "recognition", "2026-01-01T00:00:00+00:00", _TEST_AKID, placement_today)]
    cid = 5_000_000
    for offset in range(1, 99):
        if offset in _VALLEYS:
            continue
        for _ in range(_LOAD_PER_DAY):
            coll_rows.append((cid, f"c{cid}", cid))
            dir_rows.append((cid, "recognition", "2026-01-01T00:00:00+00:00", cid, placement_today + offset))
            cid += 1
    with db._get_conn() as conn:
        conn.executemany("INSERT INTO collocations (id, text, anki_note_id) VALUES (?, ?, ?)", coll_rows)
        conn.executemany(
            "INSERT INTO collocation_directions "
            "(collocation_id, direction, due_at, anki_card_id, anki_due, state) "
            "VALUES (?, ?, ?, ?, ?, 'review')",
            dir_rows,
        )
        conn.commit()
    return db


@pytest.mark.oracle
def test_live_builder_matches_anki(synthetic_collection: SyntheticCollection, tmp_path: Path) -> None:
    """build_live_load_balancer, fed from TT state mirroring the Anki collection,
    reproduces Anki's relocated interval bit-exact (Layer 55 — live grade path)."""
    on_path = synthetic_collection.path
    placement_today = _build(on_path, load_balancer_enabled=True)
    off_path = tmp_path / "lb_off.anki2"
    _build(off_path, load_balancer_enabled=False)

    anki_on = run_oracle(on_path, [{"op": "scheduling_states", "deck_id": 1, "card_id": _TEST_AKID}]).raw()[
        "scheduling_states_0"
    ]
    anki_off = run_oracle(off_path, [{"op": "scheduling_states", "deck_id": 1, "card_id": _TEST_AKID}]).raw()[
        "scheduling_states_0"
    ]
    anki_today = anki_on["today"]

    db = _mirror_histogram_into_tt(placement_today)
    # Pick a `now` mid-way through Anki's `today` so build_live_load_balancer's
    # computed day index == anki_today, aligning TT's histogram frame to Anki's
    # (mirrors _make_balancer's placement_today→anki_today shift without rounding).
    now = datetime.fromtimestamp(COL_CRT + anki_today * 86400 + 8 * 3600, tz=UTC)
    balancer = build_live_load_balancer(db, now=now, col_crt=COL_CRT)
    assert balancer is not None

    tt_off = _tt_intervals(None)
    tt_on = _tt_intervals(balancer)

    if (tt_off[1], tt_off[2]) != (anki_off["good"], anki_off["easy"]):
        pytest.skip(f"pure-fuzz baseline misaligned (rollover boundary): TT_off={tt_off} Anki_off={anki_off}")

    assert tt_on[1] == anki_on["good"], f"GOOD: TT={tt_on[1]} Anki={anki_on['good']}"
    assert tt_on[2] == anki_on["easy"], f"EASY: TT={tt_on[2]} Anki={anki_on['easy']}"
    assert (anki_on["good"], anki_on["easy"]) != (anki_off["good"], anki_off["easy"]), (
        "load balancer did not change the interval — scenario not exercising it"
    )
