"""Same-day HARD stability is non-decreasing, and no deck/collection flag gates it.

Anki 26.08.1 adopted upstream fsrs's non-decreasing ``SInc(Hard)``; 26.05 had not.
``fsrs.py::_stability_short_term`` mirrors it by clamping at ``rating >= 2``
(``785673f``). This pins that against Anki's **actual scheduler** — the
``get_scheduling_states`` → ``answer_card_raw`` path that a real grade takes —
rather than against ``fsrs_rs_python.next_states``, which is the library TT also
derives from and so cannot independently corroborate it.

What this covers:
- Anki returns exactly ``last_s`` for a same-day HARD (the clamp binding), and
  TT's ``_stability_short_term`` reproduces it.
- The same holds with ``fsrsShortTermWithStepsEnabled`` **unset, false, and
  true**. ``785673f`` could only make a bounded claim here ("two natural
  locations, not Anki's internals") because the key is absent from a fresh
  collection — Anki writes it only once changed from default. Setting it
  explicitly turns that into a measurement.
- GOOD on an identical card, as the discriminating control (see below).

What this does NOT cover (owned elsewhere):
- The interday (``delta_t > 0``) recall path — ``test_parity_fsrs_*``.
- Same-day AGAIN, which is deliberately still unclamped so a lapse can lose
  stability — pinned as a unit test in ``test_srs_fsrs.py``.
- Whether TT *routes* a given grade to the short-term path; that is
  ``test_parity_same_day_review.py``. Here the routing is forced by construction
  (``last_review_secs=now`` ⇒ ``days_elapsed == 0``).

⚠️ **Two ways this test could pass vacuously, both closed on purpose.**

1. **The clamp makes HARD a fixed point**, so "Anki returned ``last_s``" is also
   what you would see if the short-term path never ran, or if ``answer_card``
   silently no-opped. GOOD is therefore graded on an identical card in the same
   oracle run: it must *move* (10.0 → 12.4838), which is only true on the
   short-term path — the interday recall value at ``elapsed=0`` differs. A HARD
   fixed point is evidence only while its GOOD sibling moves.

2. **``set_col_config`` writes the legacy ``col.conf`` JSON blob, which modern
   Anki ignores** (harness gotcha #2). Setting the flag that way leaves the
   ``config`` table untouched, so all three parametrisations would run identical
   collections and "the flag changes nothing" would be a tautology about a flag
   that was never set. ``set_config_value`` is the correct setter, and the test
   reads the raw bytes back out of the saved collection before trusting the run.
   Measured 2026-08-31: the first probe of this used ``set_col_config`` and was
   vacuous exactly this way.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, Rating, _stability_short_term
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import (
    DEFAULT_DESIRED_RETENTION,
    SyntheticCollection,
)

FSRS_WEIGHTS = DEFAULT_FSRS5_PARAMS.weights

_FLAG_KEY = "fsrsShortTermWithStepsEnabled"

# Start state. s=10.0 keeps the card well clear of the learning-step regime and
# far from any interval cap, so the only thing under test is the stability rule.
_S0 = 10.0
_D0 = 5.0

_HARD_CARD = 10010
_GOOD_CARD = 10020

# Anki stores stability on a 4dp grid (``_quantize_stability``); parity is
# asserted at that grid rather than in f32 ULPs.
_TOL = 1e-4


def _seed(coll: SyntheticCollection, flag: bool | None) -> None:
    """Two identical same-day review cards, one per rating, + the flag under test."""
    coll.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    if flag is not None:
        # NOT set_col_config — see the vacuity note (2) in the module docstring.
        coll.set_config_value(_FLAG_KEY, flag)

    # last_review == now ⇒ days_elapsed == 0 ⇒ Anki routes through
    # stability_short_term (harness gotcha #1, used deliberately here).
    now_secs = int(time.time())
    for cid in (_HARD_CARD, _GOOD_CARD):
        note_id = cid // 10
        coll.add_note(id=note_id, guid=f"g-{cid}", fields=[f"front-{cid}", "back"])
        coll.add_card(
            id=cid,
            note_id=note_id,
            ord=0,
            type=2,
            queue=2,
            due=0,
            ivl=10,
            reps=5,
            stability=_S0,
            difficulty=_D0,
            last_review_secs=now_secs,
            desired_retention=DEFAULT_DESIRED_RETENTION,
        )
    coll.save()


def _assert_flag_written(coll: SyntheticCollection, flag: bool | None) -> None:
    """Read the flag back out of the saved collection's modern ``config`` table."""
    conn = sqlite3.connect(coll.path)
    try:
        row = conn.execute("SELECT val FROM config WHERE key = ?", (_FLAG_KEY,)).fetchone()
    finally:
        conn.close()

    if flag is None:
        assert row is None, f"expected {_FLAG_KEY} absent, found {row!r}"
    else:
        expected = b"true" if flag else b"false"
        assert row is not None, (
            f"{_FLAG_KEY} never reached the `config` table — the flag-invariance "
            "assertion below would be vacuous. Did set_col_config get used instead "
            "of set_config_value? (harness gotcha #2)"
        )
        assert row[0] == expected, f"{_FLAG_KEY} = {row[0]!r}, expected {expected!r}"


@pytest.mark.oracle
@pytest.mark.parametrize("flag", [None, False, True], ids=["unset", "false", "true"])
def test_parity_same_day_hard_is_non_decreasing(synthetic_collection: SyntheticCollection, flag: bool | None) -> None:
    """Anki 26.08.1 returns exactly ``last_s`` for a same-day HARD, at every flag setting.

    The GOOD card in the same run is the control that keeps the HARD assertion
    from being a statement about a no-op.
    """
    _seed(synthetic_collection, flag)
    _assert_flag_written(synthetic_collection, flag)

    result = run_oracle(
        synthetic_collection.path,
        [
            {"op": "answer_card", "card_id": _HARD_CARD, "rating": Rating.HARD.value},
            {"op": "answer_card", "card_id": _GOOD_CARD, "rating": Rating.GOOD.value},
        ],
    )
    anki_hard = result.raw()["answer_card_0"]["stability"]
    anki_good = result.raw()["answer_card_1"]["stability"]

    # Control first: if GOOD did not move, the run says nothing about HARD.
    assert anki_good > _S0 + _TOL, (
        f"control failed: same-day GOOD left stability at {anki_good} (start {_S0}). "
        "Either answer_card no-opped or the short-term path was not taken, which "
        "makes the HARD fixed point below meaningless."
    )

    assert abs(anki_hard - _S0) <= _TOL, (
        f"Anki reduced stability on a same-day HARD ({_S0} -> {anki_hard}) with "
        f"{_FLAG_KEY}={flag}. That is the pre-26.08.1 unclamped SInc(Hard); if this "
        "fires after an Anki upgrade, _stability_short_term's `rating >= 2` clamp "
        "must move back with it (see .claude/rules/anki-queue-parity.md, "
        "'trust the binary')."
    )

    tt_hard = _stability_short_term(_S0, Rating.HARD, DEFAULT_FSRS5_PARAMS)
    tt_good = _stability_short_term(_S0, Rating.GOOD, DEFAULT_FSRS5_PARAMS)

    assert abs(tt_hard - anki_hard) <= _TOL, f"same-day HARD divergence: TT={tt_hard} Anki={anki_hard}"
    assert abs(tt_good - anki_good) <= _TOL, f"same-day GOOD divergence: TT={tt_good} Anki={anki_good}"
