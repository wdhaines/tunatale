"""FSRS scheduling parity (Phase 2.2.1, layer cluster: 6, 11, 15, 38, 40, 45).

Layer 45: day-level elapsed must use col-day computation, not UTC date.

Pins TT's FSRS next-state computations against Anki's V3Scheduler ground truth.
For a review card with a known (stability, difficulty, last_review) state, Anki
returns the predicted next state (stability, difficulty, scheduled_days) for
each of the 4 ratings. TT computes the same transitions via its FSRS pure
functions and we assert equality.

What this test covers:
- ``_next_stability_recall`` for Hard/Good/Easy (Layer 6, recall formula)
- ``_next_stability_lapse`` for Again (lapse stability)
- ``_next_difficulty`` for all ratings
- ``_stability_short_term`` for same-day re-grades (elapsed=0 path)
- ``scheduled_days`` matching: cascade-constrained intervals match Anki's
  ``ReviewState.scheduled_days`` (Layer N, review interval cascade).

Findings surfaced by Phase 2.2.1, fixed in Layer 42:
- **Lapse stability ceiling missing**. fsrs-rs applies
  ``new_s = min(new_s, last_s / exp(w[17] * w[18]))`` in
  ``stability_after_failure``. TT's ``_next_stability_lapse`` was missing this
  bound, producing too-high lapse stabilities for low-s cards. Fix landed as
  Layer 42; the low-stability seed below now passes alongside the others.

Key gotcha surfaced during Phase 2.2.1 development: Anki's
``next_states(days_elapsed)`` reads ``days_elapsed`` from ``card.last_review_time``
(persisted as ``cards.data.lrt``), NOT from the ReviewState's ``elapsed_days``
field. Without ``lrt`` set, Anki silently uses ``elapsed=0`` and routes through
``stability_short_term`` instead of ``stability_after_success`` — a quiet
formula switch that earlier test attempts mistook for a TT bug.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from app.models.srs_item import Rating
from app.srs.fsrs import (
    DEFAULT_FSRS5_PARAMS,
    FSRSParams,
    _forgetting_curve,
    _next_difficulty,
    _next_stability_lapse,
    _next_stability_recall,
    _stability_short_term,
)
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import (
    DEFAULT_DESIRED_RETENTION,
    SyntheticCollection,
)

# Use TT's production FSRS-5 weights, not synthetic_collection's defaults.
# fsrs-rs clamps several weight indices on load (w[16] to [1.0, 6.0],
# w[17]/w[18] to [0, ceiling], etc. — see parameter_clipper.rs). Synthetic's
# default weights have w[16]=0.1222 which gets clipped to 1.0 by Anki, creating
# a synthetic-only divergence with TT. TT's production weights sit inside
# every valid range, so both sides see identical values without clipping.
FSRS_WEIGHTS = DEFAULT_FSRS5_PARAMS.weights


# (s, d) pairs covering the FSRS parameter space:
#   (10.0, 4.0)  — mid-stability, low difficulty (well-known card)
#   (1.5, 7.5)   — low stability, high difficulty (lapse-ceiling exercise)
#   (50.0, 2.0)  — high stability, very low difficulty (deeply known card)
SEED_STATES = [
    (10.0, 4.0),
    (1.5, 7.5),
    (50.0, 2.0),
]

# 30 days back is enough elapsed for R to be meaningfully <1 across all SEED_STATES
# without being so far that stability_after_failure floors kick in.
_ELAPSED_DAYS = 30


def _seed_review_card(
    coll: SyntheticCollection,
    card_id: int,
    stability: float,
    difficulty: float,
    last_review_secs: int,
) -> None:
    """Seed one review card with the given FSRS state, due today."""
    note_id = card_id // 10
    coll.add_note(id=note_id, guid=f"g-{card_id}", fields=[f"front-{card_id}", "back"])
    coll.add_card(
        id=card_id,
        note_id=note_id,
        ord=0,
        type=2,  # review
        queue=2,
        due=0,
        ivl=10,
        reps=5,
        stability=stability,
        difficulty=difficulty,
        last_review_secs=last_review_secs,
    )


@pytest.mark.oracle
def test_fsrs_next_state_matches_anki_for_review_cards(synthetic_collection: SyntheticCollection) -> None:
    """For each (s, d) seed and each rating, TT's next-state matches Anki's."""
    # Drive both apps with TT's production FSRS-5 weights.
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)

    now_secs = int(time.time())
    last_review_secs = now_secs - _ELAPSED_DAYS * 86400

    for i, (stability, difficulty) in enumerate(SEED_STATES):
        _seed_review_card(
            synthetic_collection,
            card_id=10010 + i * 10,
            stability=stability,
            difficulty=difficulty,
            last_review_secs=last_review_secs,
        )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )

    raw = result.raw()["get_queue_0"]
    anki_cards = {c["card_id"]: c for c in raw["cards"]}
    assert len(anki_cards) == len(SEED_STATES), f"expected {len(SEED_STATES)} cards, got {list(anki_cards)}"

    params = FSRSParams(weights=FSRS_WEIGHTS, desired_retention=DEFAULT_DESIRED_RETENTION)
    w = params.weights

    rating_map = {
        "again": Rating.AGAIN,
        "hard": Rating.HARD,
        "good": Rating.GOOD,
        "easy": Rating.EASY,
    }

    # Use Anki's reported next-state elapsed value to drive TT's R computation.
    # Anki rounds (now - lrt) to integer days; we read it back so any
    # ±1-day boundary disagreement doesn't surface as a parity failure here
    # (it would be a separate Layer to chase).
    failures: list[str] = []

    for i, (s, d) in enumerate(SEED_STATES):
        card_id = 10010 + i * 10
        anki_card = anki_cards[card_id]
        states = anki_card["states"]
        # The TT-relevant elapsed is what Anki passes to next_states() —
        # derived from cards.data.lrt. Anki's exact value isn't directly
        # exposed; round-trip via the value we wrote.
        elapsed_days = _ELAPSED_DAYS
        r = _forgetting_curve(elapsed_days, s, decay=-0.5)

        for rating_name, tt_rating in rating_map.items():
            anki_next = states[rating_name]
            anki_s = anki_next["stability"]
            anki_d = anki_next["difficulty"]

            if tt_rating == Rating.AGAIN:
                tt_s = _next_stability_lapse(d, s, r, w)
            else:
                tt_s = _next_stability_recall(d, s, r, tt_rating, w)
            tt_d = _next_difficulty(d, tt_rating, w)

            # f32 vs f64 + integer-day rounding tolerance: ~1% relative is safe
            # for stability (Anki rounds elapsed to int days; TT uses the same
            # int). Difficulty is independent of elapsed so a tighter bound
            # applies.
            s_tol = 1e-2
            d_tol = 1e-3
            if abs(tt_s - anki_s) / max(abs(anki_s), 1e-9) > s_tol:
                failures.append(
                    f"card_id={card_id} (s={s},d={d},R={r:.4f}) rating={rating_name}: "
                    f"stability TT={tt_s:.6f} vs Anki={anki_s:.6f}"
                )
            if abs(tt_d - anki_d) / max(abs(anki_d), 1e-9) > d_tol:
                failures.append(
                    f"card_id={card_id} (s={s},d={d},R={r:.4f}) rating={rating_name}: "
                    f"difficulty TT={tt_d:.6f} vs Anki={anki_d:.6f}"
                )

    assert not failures, "FSRS parity divergence:\n  " + "\n  ".join(failures)


@pytest.mark.oracle
def test_fsrs_short_term_stability_matches_anki(synthetic_collection: SyntheticCollection) -> None:
    """Same-day re-grade path: cards with elapsed=0 use ``stability_short_term``.

    Reproduces the path that surfaced during 2.2.1 development: when Anki sees
    ``days_elapsed=0`` (because ``cards.data.lrt`` is missing or fresh), it
    skips ``stability_after_success`` and uses ``stability_short_term``. This
    test pins TT's ``_stability_short_term`` against that path.
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    s, d = 10.0, 4.0
    # No last_review_secs → Anki sees elapsed=0 → short-term formula
    synthetic_collection.add_note(id=1001, guid="short-term", fields=["f", "b"])
    synthetic_collection.add_card(
        id=10010,
        note_id=1001,
        ord=0,
        type=2,
        queue=2,
        due=0,
        ivl=10,
        reps=5,
        stability=s,
        difficulty=d,
    )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 5}],
    )
    states = result.raw()["get_queue_0"]["cards"][0]["states"]

    params = FSRSParams(weights=FSRS_WEIGHTS, desired_retention=DEFAULT_DESIRED_RETENTION)

    for rating_name, tt_rating in [
        ("hard", Rating.HARD),
        ("good", Rating.GOOD),
        ("easy", Rating.EASY),
    ]:
        anki_s = states[rating_name]["stability"]
        tt_s = _stability_short_term(s, tt_rating, params)
        assert abs(tt_s - anki_s) / max(abs(anki_s), 1e-9) < 1e-3, (
            f"short-term stability mismatch for {rating_name}: TT={tt_s:.6f} vs Anki={anki_s:.6f}"
        )


@pytest.mark.oracle
def test_parity_graduation_after_many_agains(synthetic_collection: SyntheticCollection) -> None:
    """After many AGAINs on a LEARNING card, a subsequent GOOD graduation must
    produce a post-graduation stability that matches Anki's fsrs-rs output,
    not TT's incorrect floor of 0.1.

    The bug: ``_graduate_to_review`` and ``schedule()`` both clamp stability
    to ``max(0.1, new_stability)`` while fsrs-rs uses ``S_MIN=0.001``
    (``fsrs-rs/src/simulation.rs:41``).  For a LEARNING card whose stability
    has decayed below 0.1 through repeated AGAINs, the GOOD graduation path
    returns 0.1 instead of the correct sub-0.1 value.  Latent since 68a479c
    (initial FSRS port), surfaced after heavy learning-step sessions.
    """
    from app.models.srs_item import Direction, SRSItem
    from app.models.syntactic_unit import SyntacticUnit
    from app.srs.fsrs import schedule

    # Use explicit learning steps so both Anki and TT use the same 2-step ladder.
    # Without explicit steps the synthetic protobuf encodes an empty list,
    # which Anki interprets differently from its internal defaults.
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_learning_steps(learn_steps=[1.0, 10.0], relearn_steps=[10.0])

    # Create a NEW card
    synthetic_collection.add_note(id=1001, guid="g-many-agains", fields=["front", "back"])
    synthetic_collection.add_card(
        id=10010,
        note_id=1001,
        ord=0,
        type=0,
        queue=0,
        due=0,
        ivl=0,
        reps=0,
        lapses=0,
        left=0,
    )
    synthetic_collection.save()

    # Oracle: 8×AGAIN (drive stability below 0.1) + 2×GOOD (step, then graduate)
    n_agains = 8
    operations = [{"op": "answer_card", "card_id": 10010, "rating": 1}] * n_agains
    operations.append({"op": "answer_card", "card_id": 10010, "rating": 3})
    operations.append({"op": "answer_card", "card_id": 10010, "rating": 3})
    operations.append({"op": "get_card", "card_id": 10010})

    result = run_oracle(synthetic_collection.path, operations)
    raw = result.raw()
    # answer_card operations are indexed sequentially; get_card is last
    get_card_key = [k for k in raw if k.startswith("get_card_")][0]
    anki_state = raw[get_card_key]
    anki_s = anki_state["stability"]

    # TT side: chain schedule() with matching ratings.
    # TT's default learning steps resolve to [1.0, 10.0] (same as oracle).
    unit = SyntacticUnit(text="test", translation="test", word_count=1, difficulty=1, source="test")
    item = SRSItem(syntactic_unit=unit, guid="g-many-agains", anki_note_id=1001)

    for _ in range(n_agains):
        item = schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
    for _ in range(2):  # advance step, then graduate
        item = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)

    tt_s = item.directions[Direction.RECOGNITION].stability

    # Three fixes land this exact-equal to Anki's 4dp-rounded value:
    #   1. Lower stability floor from 0.1 to fsrs-rs's S_MIN=0.001 at
    #      ``fsrs.py:459/819`` (pre-fix gap was 50×).
    #   2. ``_graduate_to_review`` for non-NEW prev.state now calls
    #      ``_stability_short_term`` to mirror fsrs-rs `model.rs:163`:
    #      ``step()`` overrides success/failure with short-term whenever
    #      ``delta_t == 0``. Previously TT used ``_next_stability_recall``
    #      with r=1, which collapses to the identity ``s * 1`` and skipped
    #      the graduation grade's stability bump.
    #   3. Quantize stability to 4dp / difficulty to 3dp at every schedule
    #      write site to mirror Anki's per-grade rounding in
    #      ``rslib/src/storage/card/data.rs:95-98``.
    assert tt_s == anki_s, f"Graduated stability: TT={tt_s:.6f} vs Anki={anki_s:.6f}"


@pytest.mark.oracle
def test_parity_relearning_graduation_interval_LAYER_52(
    synthetic_collection: SyntheticCollection,
) -> None:
    """Layer 52: graduation from RELEARNING uses simple per-rating fuzz with
    ``minimum=1``, NOT the passing-review cascade (``good_min = hard_fuzzed + 1``).

    Mirrors Anki's ``rslib/.../states/relearning.rs:104-130`` (HARD) and
    :155-184 (GOOD): each rating calls
    ``with_review_fuzz(interval.round().max(1.0), 1, max)`` independently —
    no cascade dependency on the previous rating's fuzzed result.
    (EASY is special: floors against good's float-fuzz; covered in a separate
    test below.)

    Pre-Layer-52 TT routed graduation through ``_passing_intervals_with_fuzz``
    with ``scheduled_days=0``, which still constrained ``good_min = hard + 1``.
    For stability values where ``hard_fuzzed + 1`` exceeded Anki's effective
    floor of 1, TT's GOOD interval landed +1 day higher than Anki's. The
    multi-grade drill (2026-05-23) showed this systematic +1 bias on 28/40
    REVIEW→AGAIN→GOOD-graduation cases.
    """
    from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState
    from app.models.syntactic_unit import SyntacticUnit
    from app.srs.fsrs import schedule

    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_learning_steps(learn_steps=[1.0, 10.0], relearn_steps=[10.0])

    # Use a REVIEW card with stability that triggers the cascade-vs-non-cascade
    # divergence. s=8.093 produced one of the off cases in the empirical drill
    # (cid 1775264032528). After AGAIN it lapses to short-term stability; after
    # GOOD it short-term-boosts back; raw_good lands at ~6.0, where TT's cascade
    # (good_min = hard_fuzzed + 1) shifts the fuzz lower bound and produces a
    # +1 day interval relative to Anki's simple-fuzz.
    s = 8.093
    d = 8.553
    now_secs = int(time.time())
    last_review_secs = now_secs - 30 * 86400  # 30 days back to ensure REVIEW lapse

    card_id = 10070
    note_id = 1007
    synthetic_collection.add_note(id=note_id, guid="g-grad-52", fields=["front", "back"])
    synthetic_collection.add_card(
        id=card_id,
        note_id=note_id,
        ord=0,
        type=2,
        queue=2,
        due=0,
        ivl=30,
        reps=8,
        lapses=1,
        stability=s,
        difficulty=d,
        last_review_secs=last_review_secs,
    )
    synthetic_collection.save()

    # Oracle: REVIEW + AGAIN (→ relearning), then GOOD (→ graduate to review).
    result = run_oracle(
        synthetic_collection.path,
        [
            {"op": "answer_card", "card_id": card_id, "rating": 1},
            {"op": "answer_card", "card_id": card_id, "rating": 3},
            {"op": "get_card", "card_id": card_id},
        ],
    )
    raw = result.raw()
    anki_card = raw[[k for k in raw if k.startswith("get_card_")][0]]
    anki_ivl = anki_card["ivl"]

    # TT side: chain schedule() with matching ratings + same last_review timing.
    from datetime import datetime as _dt
    from datetime import timedelta

    last_review_dt = _dt.fromtimestamp(last_review_secs, tz=UTC)
    direction = DirectionState(
        direction=Direction.RECOGNITION,
        due_at=last_review_dt + timedelta(days=30),
        stability=s,
        difficulty=d,
        reps=8,
        lapses=1,
        state=SRSState.REVIEW,
        last_review=last_review_dt,
        anki_card_id=card_id,
        anki_due=last_review_secs // 86400,  # any plausible col_day; not used for graduation
    )
    unit = SyntacticUnit(text="t", translation="t", word_count=1, difficulty=1, source="t")
    item = SRSItem(
        syntactic_unit=unit, directions={Direction.RECOGNITION: direction}, guid="g-grad-52", anki_note_id=note_id
    )

    # AGAIN: REVIEW → RELEARNING.
    grade1_dt = _dt.fromtimestamp(now_secs, tz=UTC)
    item = schedule(item, Rating.AGAIN, review_date=grade1_dt.date(), now=grade1_dt)
    # GOOD: RELEARNING → REVIEW (graduate).
    grade2_dt = grade1_dt + timedelta(seconds=60)
    item = schedule(item, Rating.GOOD, review_date=grade2_dt.date(), now=grade2_dt)

    # Use date-arithmetic — sub-day-precise last_review would truncate
    # ``(due_at - last_review).days`` and hide the off-by-1 bug.
    new_dir = item.directions[Direction.RECOGNITION]
    tt_interval = (new_dir.due_at.date() - new_dir.last_review.date()).days

    assert tt_interval == anki_ivl, (
        f"Layer 52: relearning graduation interval must match Anki. "
        f"TT={tt_interval} Anki={anki_ivl}. Pre-fix: TT applied passing-review "
        f"cascade (good_min = hard_fuzzed + 1) at graduation, producing +1 day."
    )


@pytest.mark.oracle
def test_parity_day_level_elapsed_matches_anki(synthetic_collection: SyntheticCollection) -> None:
    """Day-level elapsed uses col-day computation, matching Anki's (Layer 45).

    Seeds a review card without ``lrt`` in ``cards.data`` using raw
    ``due_raw`` / ``ivl`` values (not derived from
    ``compute_anki_day_index``).  TT exercises the real
    ``_compute_last_review`` → ``_elapsed_days_for_fsrs`` pipeline,
    then compares elapsed_days against Anki's ground truth.

    The day-level fallback path (tested via ``_compute_last_review``) is
    also covered by
    ``test_anki_sqlite_reader.py::test_parse_fsrs_data_last_review_col_day_matches_anki``.
    """
    from app.anki.protobuf_wire import compute_anki_day_index
    from app.anki.sqlite_reader import _compute_last_review
    from app.srs.fsrs import _elapsed_days_for_fsrs

    col_crt = -572400
    synthetic_collection.col_crt = col_crt
    synthetic_collection.enable_fsrs(
        weights=DEFAULT_FSRS5_PARAMS.weights,
        retention=DEFAULT_DESIRED_RETENTION,
    )

    # Use arbitrary due_raw / ivl (not derived from compute_anki_day_index)
    # such that due_raw ≈ today_col_day, so the card appears in the queue
    due_raw = 20589
    ivl = 10
    review_col_day = due_raw - ivl  # = 20579

    synthetic_collection.add_note(id=1001, guid="g-day-level", fields=["f", "b"])
    synthetic_collection.add_card(
        id=10010,
        note_id=1001,
        ord=0,
        type=2,
        queue=2,
        due=due_raw,
        ivl=ivl,
        reps=5,
        stability=10.0,
        difficulty=4.0,
    )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    raw = result.raw()
    queue_key = [k for k in raw if k.startswith("get_queue_")][0]
    cards = raw[queue_key]["cards"]
    assert len(cards) >= 1

    anki_card = cards[0]
    anki_elapsed = anki_card["states"]["current"]["elapsed_days"]

    # TT side: use the same _compute_last_review pipeline as sync
    ref_now = datetime.now(tz=UTC)
    last_review = _compute_last_review(2, due_raw, ivl, col_crt)
    tt_elapsed = _elapsed_days_for_fsrs(last_review, ref_now, col_crt=col_crt)

    assert anki_elapsed == tt_elapsed, (
        f"elapsed_days mismatch: Anki={anki_elapsed} vs TT={tt_elapsed}\n"
        f"  col_crt={col_crt}, due_raw={due_raw}, ivl={ivl}, "
        f"review_col_day={review_col_day}\n"
        f"  last_review={last_review} "
        f"(col_day={compute_anki_day_index(col_crt, 4, last_review)})\n"
        f"  ref_now={ref_now}"
    )


@pytest.mark.oracle
def test_parity_review_interval_cascade_matches_anki(synthetic_collection: SyntheticCollection) -> None:
    """TT cascade + fuzz exactly reproduces Anki's get_queue scheduled_days.

    Anki's ``get_queue`` calls ``next_states`` with ``for_reschedule=true``, which
    seeds fuzz with ``card.reps - 1`` rather than the current ``card.reps`` (see
    ``rslib/src/scheduler/answering/mod.rs:649-668`` — "to match the previous
    review"). TT's at-grade ``schedule()`` uses ``prev.reps`` (== ``card.reps``
    pre-increment), which matches Anki's at-grade ``for_reschedule=false`` path.
    To mirror the *preview* path the oracle exposes, the parity test passes
    ``reps - 1`` to ``_review_interval_fuzz``.

    With ``ivl=1, s=0.5, d=5.0, dr=0.9, elapsed=30d, card_id=10050, reps=5``,
    Anki returns ``(hard=10, good=19, easy=54)``. Don't weaken to an invariant
    check — a divergence in cascade math or stability computation would still
    satisfy the invariant but fail this equality.
    """
    from app.srs.fsrs import (
        _constrain_passing_intervals,
        _forgetting_curve,
        _next_interval,
        _next_stability_recall,
        _quantize_stability,
        _review_interval_fuzz,
    )

    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)

    now_secs = int(time.time())
    elapsed_days = 30
    last_review_secs = now_secs - elapsed_days * 86400
    card_id = 10050
    note_id = 1005
    reps = 5
    s = 0.5
    d = 5.0
    scheduled_days = 1

    synthetic_collection.add_note(id=note_id, guid="g-cascade", fields=["front", "back"])
    synthetic_collection.add_card(
        id=card_id,
        note_id=note_id,
        ord=0,
        type=2,
        queue=2,
        due=0,
        ivl=scheduled_days,
        reps=reps,
        stability=s,
        difficulty=d,
        last_review_secs=last_review_secs,
    )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    states = result.raw()["get_queue_0"]["cards"][0]["states"]
    anki = (
        states["hard"]["scheduled_days"],
        states["good"]["scheduled_days"],
        states["easy"]["scheduled_days"],
    )

    params = FSRSParams(weights=FSRS_WEIGHTS, desired_retention=DEFAULT_DESIRED_RETENTION)
    neg_decay = -params.decay
    r = _forgetting_curve(elapsed_days, s, decay=-0.5)
    s_h = _next_stability_recall(d, s, r, Rating.HARD, params.weights)
    s_g = _next_stability_recall(d, s, r, Rating.GOOD, params.weights)
    s_e = _next_stability_recall(d, s, r, Rating.EASY, params.weights)
    raw_h = _next_interval(_quantize_stability(max(0.001, s_h)), params.desired_retention, neg_decay)
    raw_g = _next_interval(_quantize_stability(max(0.001, s_g)), params.desired_retention, neg_decay)
    raw_e = _next_interval(_quantize_stability(max(0.001, s_e)), params.desired_retention, neg_decay)
    h, g, e = _constrain_passing_intervals(raw_h, raw_g, raw_e, scheduled_days=scheduled_days)

    fuzz_reps = reps - 1
    tt = (
        _review_interval_fuzz(h, card_id, fuzz_reps),
        _review_interval_fuzz(g, card_id, fuzz_reps),
        _review_interval_fuzz(e, card_id, fuzz_reps),
    )

    assert tt == anki, (
        f"TT cascade+fuzz {tt} != Anki get_queue scheduled_days {anki} "
        f"(raw={raw_h}/{raw_g}/{raw_e}, cascade={h}/{g}/{e}, fuzz_reps={fuzz_reps})"
    )


@pytest.mark.oracle
def test_parity_review_interval_cascade_binds_matches_anki(synthetic_collection: SyntheticCollection) -> None:
    """Cascade-binding case: TT and Anki agree when ``raw_good <= scheduled_days``.

    Companion to ``test_parity_review_interval_cascade_matches_anki`` which uses
    inputs where raw intervals are large enough that the cascade is a no-op. Here
    we use poljubiti-shape inputs (low pre-grade stability, high difficulty,
    1-day prev interval) where raw values land at 1/1/2 — the cascade actually
    binds and bumps good to ``hard + 1``. Reverting Layer 48 makes this test fail.
    """
    from app.srs.fsrs import (
        _constrain_passing_intervals,
        _forgetting_curve,
        _next_interval,
        _next_stability_recall,
        _quantize_stability,
        _review_interval_fuzz,
    )

    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)

    now_secs = int(time.time())
    elapsed_days = 1
    last_review_secs = now_secs - elapsed_days * 86400
    card_id = 10051
    note_id = 1006
    reps = 5
    s = 0.3
    d = 9.7
    scheduled_days = 1

    synthetic_collection.add_note(id=note_id, guid="g-bind", fields=["front", "back"])
    synthetic_collection.add_card(
        id=card_id,
        note_id=note_id,
        ord=0,
        type=2,
        queue=2,
        due=0,
        ivl=scheduled_days,
        reps=reps,
        stability=s,
        difficulty=d,
        last_review_secs=last_review_secs,
    )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    states = result.raw()["get_queue_0"]["cards"][0]["states"]
    anki = (
        states["hard"]["scheduled_days"],
        states["good"]["scheduled_days"],
        states["easy"]["scheduled_days"],
    )

    params = FSRSParams(weights=FSRS_WEIGHTS, desired_retention=DEFAULT_DESIRED_RETENTION)
    neg_decay = -params.decay
    r = _forgetting_curve(elapsed_days, s, decay=-0.5)
    s_h = _next_stability_recall(d, s, r, Rating.HARD, params.weights)
    s_g = _next_stability_recall(d, s, r, Rating.GOOD, params.weights)
    s_e = _next_stability_recall(d, s, r, Rating.EASY, params.weights)
    raw_h = _next_interval(_quantize_stability(max(0.001, s_h)), params.desired_retention, neg_decay)
    raw_g = _next_interval(_quantize_stability(max(0.001, s_g)), params.desired_retention, neg_decay)
    raw_e = _next_interval(_quantize_stability(max(0.001, s_e)), params.desired_retention, neg_decay)
    h, g, e = _constrain_passing_intervals(raw_h, raw_g, raw_e, scheduled_days=scheduled_days)

    assert (raw_h, raw_g, raw_e) != (h, g, e), (
        f"Cascade did not bind for this test's inputs (raw={raw_h}/{raw_g}/{raw_e} "
        f"== cascade={h}/{g}/{e}). Test no longer exercises Layer 48 — adjust inputs."
    )

    fuzz_reps = reps - 1
    tt = (
        _review_interval_fuzz(h, card_id, fuzz_reps),
        _review_interval_fuzz(g, card_id, fuzz_reps),
        _review_interval_fuzz(e, card_id, fuzz_reps),
    )

    assert tt == anki, (
        f"TT cascade+fuzz {tt} != Anki get_queue scheduled_days {anki} "
        f"(raw={raw_h}/{raw_g}/{raw_e}, cascade={h}/{g}/{e}, fuzz_reps={fuzz_reps})"
    )
