"""Queue-build sort-key parity (Phase 2.2.3, layer cluster: 25, 28, 32, 37, 38).

Pins TT's review-queue ordering against Anki's V3Scheduler. For a deck of
review cards with diverse FSRS state, Anki returns them sorted by
retrievability ascending; TT mirrors the same sort via
``compute_retrievability`` + FNV tiebreaker. We compare orderings.

What this test covers:
- R-asc primary sort: ``forgetting_curve(elapsed, s)`` agrees per card.
- Layer 38: ``data='{}'`` cards (no memory_state) sort at the position
  ``desired_retention`` would occupy — NOT at NULLs-first head.
- Layer 37: ``fnvhash(cards.id, cards.mod)`` tiebreaker for R-tied cards.

What this test does NOT cover (deferred or owned elsewhere):
- Layer 28's `_merge_directions` cross-direction gather + sibling-bury.
  That's about *new-card* ordering, which depends on note pairs with both
  recognition and production directions — the synthetic-collection minimal
  notetype only has one template, so this would need a separate fixture
  with a Slovene-Voc-style 2-template notetype.
- Layer 32's `_NEW_OVERFETCH` (per-direction fetch limit). Covered by an
  existing TT unit test; here the queue is fully fetched.
- Layer 25's new-bucket sort (`anki_due DESC NULLS FIRST`). New-card
  ordering belongs with the Layer 28 setup above.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime

import pytest

from app.models.srs_item import Direction, DirectionState, Rating, SRSState
from app.srs.fsrs import (
    DEFAULT_FSRS5_PARAMS,
    compute_retrievability,
)
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import (
    DEFAULT_DESIRED_RETENTION,
    SyntheticCollection,
)

assert Rating  # silence unused-import linter (kept for future test parametrization)

FSRS_WEIGHTS = DEFAULT_FSRS5_PARAMS.weights


def _seed_review_card(
    coll: SyntheticCollection,
    *,
    card_id: int,
    stability: float | None,
    difficulty: float | None,
    last_review_secs: int | None,
    mod: int = 0,
    empty_fsrs_data: bool = False,
    desired_retention: float = DEFAULT_DESIRED_RETENTION,
) -> None:
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
        mod=mod,
        empty_fsrs_data=empty_fsrs_data,
        desired_retention=desired_retention,
    )


def _tt_compute_R(s: float, last_review_secs: int, now_secs: int, *, dr: float = DEFAULT_DESIRED_RETENTION) -> float:
    """Replicate TT's compute_retrievability with explicit elapsed-days."""
    dstate = DirectionState(
        direction=Direction.RECOGNITION,
        due_date=date.today(),
        state=SRSState.REVIEW,
        stability=s,
        difficulty=5.0,
        reps=5,
        last_review=datetime.fromtimestamp(last_review_secs, tz=UTC),
    )
    return compute_retrievability(
        dstate,
        date.today(),
        now=datetime.fromtimestamp(now_secs, tz=UTC),
        desired_retention=dr,
    )


@pytest.mark.oracle
def test_queue_order_R_ascending_matches_anki(synthetic_collection: SyntheticCollection) -> None:
    """Anki sorts review cards by R asc; TT's compute_retrievability agrees per card.

    Setup: 5 cards with diverse (stability, elapsed_days). Anki's queue head
    is the card with lowest R; assert TT computes the same minimum and
    overall ordering.
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)

    now_secs = int(time.time())

    seeds = [
        # (card_id, stability, elapsed_days) — chosen for distinct R values
        (10010, 1.0, 30),  # low s, long elapsed → very low R (most forgotten)
        (10020, 5.0, 5),  # mid s, short elapsed → high R
        (10030, 20.0, 15),  # high s, medium elapsed → high R
        (10040, 2.0, 10),  # mid-low s, mid elapsed → low R
        (10050, 50.0, 1),  # very high s, short elapsed → highest R
    ]

    for card_id, s, elapsed_days in seeds:
        last_review_secs = now_secs - elapsed_days * 86400
        _seed_review_card(
            synthetic_collection,
            card_id=card_id,
            stability=s,
            difficulty=5.0,
            last_review_secs=last_review_secs,
        )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    anki_order = [c["card_id"] for c in result.raw()["get_queue_0"]["cards"]]

    # TT-side: compute R for each card, sort ascending.
    tt_records = []
    for card_id, s, elapsed_days in seeds:
        last_review_secs = now_secs - elapsed_days * 86400
        r = _tt_compute_R(s, last_review_secs, now_secs)
        tt_records.append((r, card_id))
    tt_order = [cid for _, cid in sorted(tt_records, key=lambda t: (t[0], t[1]))]

    assert anki_order == tt_order, (
        f"Queue order divergence:\n  Anki: {anki_order}\n  TT:   {tt_order}\n"
        f"  TT R values: {sorted([(round(r, 4), cid) for r, cid in tt_records])}"
    )


@pytest.mark.oracle
@pytest.mark.xfail(
    reason=(
        "Layer 43 finding: current Anki binary places NULL-R cards at the TAIL "
        "(NULLs-last), not at the desired_retention position as Layer 38 documented. "
        "Anki's `extract_fsrs_relative_retrievability` falls back to the SM2 path "
        "when cards.data lacks `s`/`d` — returning approximately -0.0001 — which "
        "sorts after every FSRS-path card. TT's `compute_retrievability` returns "
        "`desired_retention` for null-state cards, placing them mid-pool. "
        "Investigation needed: which is the authoritative Anki behavior (this "
        "binary vs the 25.09.4 binary Layer 38 was pinned to)? Don't fix TT "
        "until the discrepancy is resolved."
    ),
    strict=True,
)
def test_null_R_card_places_at_desired_retention_LAYER_38(synthetic_collection: SyntheticCollection) -> None:
    """Layer 38 (xfail): TT places NULL-R at dr position; current Anki binary tail-sorts.

    Setup: 3 cards with explicit R values bracketing desired_retention (0.9),
    plus 1 card with ``data='{}'`` (no memory_state). Original Layer 38 claim:
    Anki places the NULL-R card at the position R=0.9 would occupy in R-asc.
    Current Anki (this binary): puts NULL-R at the tail.
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)

    now_secs = int(time.time())

    # Compute the elapsed days that yields R values bracketing 0.9.
    # For s=10.0: R = (1 + 19/81 * elapsed/10)^-0.5
    #   elapsed=1  → R ≈ 0.978  (above dr)
    #   elapsed=5  → R ≈ 0.901  (just above dr)
    #   elapsed=10 → R ≈ 0.823  (below dr)
    seeds_with_state = [
        (10010, 10.0, 1, "R≈0.978"),
        (10020, 10.0, 5, "R≈0.901"),
        (10030, 10.0, 10, "R≈0.823"),
    ]
    for card_id, s, elapsed_days, _label in seeds_with_state:
        last_review_secs = now_secs - elapsed_days * 86400
        _seed_review_card(
            synthetic_collection,
            card_id=card_id,
            stability=s,
            difficulty=5.0,
            last_review_secs=last_review_secs,
        )

    # The NULL-R card: data='{}'. Anki places it at R=desired_retention=0.9.
    _seed_review_card(
        synthetic_collection,
        card_id=10099,
        stability=None,
        difficulty=None,
        last_review_secs=None,
        empty_fsrs_data=True,
    )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    anki_order = [c["card_id"] for c in result.raw()["get_queue_0"]["cards"]]

    # Lowest R first → highest R last.
    #   10030 (R≈0.823) — lowest, head
    #   10099 (R=0.900 from dr)
    #   10020 (R≈0.901)
    #   10010 (R≈0.978) — highest, tail
    expected = [10030, 10099, 10020, 10010]
    assert anki_order == expected, (
        f"Layer 38 NULL-R placement: expected {expected}, got {anki_order}.\n"
        f"  NULL-R card should land between R<dr and R>dr cards, not at NULLs-first head."
    )

    # TT-side check: compute_retrievability returns desired_retention for null-stability.
    null_dstate = DirectionState(
        direction=Direction.RECOGNITION,
        due_date=date.today(),
        state=SRSState.REVIEW,
        stability=None,
        difficulty=None,
        reps=0,
        last_review=None,
    )
    tt_null_R = compute_retrievability(
        null_dstate,
        date.today(),
        now=datetime.fromtimestamp(now_secs, tz=UTC),
        desired_retention=DEFAULT_DESIRED_RETENTION,
    )
    assert tt_null_R == DEFAULT_DESIRED_RETENTION, (
        f"TT compute_retrievability(null-state) should return desired_retention={DEFAULT_DESIRED_RETENTION}, got {tt_null_R}"
    )


@pytest.mark.oracle
def test_fnv_tiebreaker_on_card_mod_LAYER_37(synthetic_collection: SyntheticCollection) -> None:
    """Layer 37: cards with identical R sort by ``fnvhash(cards.id, cards.mod)``.

    Setup: 3 cards with IDENTICAL stability + last_review (→ identical R).
    Different mod values. Anki's queue order should be `fnv(id, mod)` ascending.
    """
    from app.api.srs import _fnv1a_64_i64

    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)

    now_secs = int(time.time())
    last_review_secs = now_secs - 5 * 86400  # all same elapsed → same R

    # Three cards with same R, distinct mod values.
    tied_cards = [
        (10010, 1700000001),
        (10020, 1700000002),
        (10030, 1700000003),
    ]
    for card_id, mod in tied_cards:
        _seed_review_card(
            synthetic_collection,
            card_id=card_id,
            stability=10.0,
            difficulty=5.0,
            last_review_secs=last_review_secs,
            mod=mod,
        )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    anki_order = [c["card_id"] for c in result.raw()["get_queue_0"]["cards"]]

    # Compute TT's expected FNV ordering.
    tt_records = [(_fnv1a_64_i64(card_id, mod), card_id) for card_id, mod in tied_cards]
    tt_order = [cid for _, cid in sorted(tt_records)]

    assert anki_order == tt_order, (
        f"FNV tiebreaker divergence:\n  Anki: {anki_order}\n  TT:   {tt_order}\n  TT FNV: {sorted(tt_records)}"
    )
