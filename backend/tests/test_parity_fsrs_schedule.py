"""FSRS scheduling parity (Phase 2.2.1, layer cluster: 6, 11, 15, 38, 40).

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

What this test does NOT cover (deferred):
- ``scheduled_days`` matching: Anki adds review-interval fuzz; TT's
  ``_next_interval`` doesn't. Tracked under "Pending work" in
  ``docs/anki-parity-layers.md``.

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
