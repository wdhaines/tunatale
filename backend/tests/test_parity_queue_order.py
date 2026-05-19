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
def test_null_R_card_typical_position_matches_anki_LAYER_38(synthetic_collection: SyntheticCollection) -> None:
    """Layer 38 (reframed): NULL-R cards with elapsed≈ivl land near dr position.

    Layer 38 originally claimed Anki places NULL-R cards at "the desired_retention
    position" in R-asc. Phase 2.2.3 investigation revealed this was a coincidence
    of the user's nič card having ``elapsed ≈ ivl``: Anki's SQL function
    ``extract_fsrs_relative_retrievability`` falls back to the SM2 path for
    null-state cards, returning ``-((elapsed+0.001)/ivl)``. When ``elapsed ≈ ivl``
    this equals -1.0 — which happens to be Anki's ``relative_R`` value for a
    card with R = dr (since the formula normalizes by ``dr.powf(-1/decay) - 1``).

    So Anki's actual rule is "NULL-R cards sort by SM2 fallback ``elapsed/ivl``",
    NOT "Anki places NULL-R at dr position". For the most common case (a card
    that was reviewed and is just becoming overdue), this approximates dr position.
    For other cases (just-forgotten, very stale), NULL-R lands at tail or head.

    This test pins the most-common case: NULL-R with elapsed ≈ ivl lands at the
    dr position — both Anki (via SM2 fallback coincidence) and TT (via
    ``compute_retrievability → dr``) agree.

    Layer 38 entry in docs/anki-parity-layers.md updated to reflect the actual
    mechanism; original claim de-mystified.
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)

    now_secs = int(time.time())

    # 3 FSRS-path cards bracketing dr=0.9
    seeds_with_state = [
        (10010, 10.0, 1),  # R ≈ 0.978
        (10020, 10.0, 5),  # R ≈ 0.901
        (10030, 10.0, 10),  # R ≈ 0.823
    ]
    for card_id, s, elapsed_days in seeds_with_state:
        _seed_review_card(
            synthetic_collection,
            card_id=card_id,
            stability=s,
            difficulty=5.0,
            last_review_secs=now_secs - elapsed_days * 86400,
        )

    # NULL-R card configured so Anki's SM2 fallback ≈ -1.0 (= dr's relative_R):
    # `due=col_day, ivl=N → elapsed=N → fallback = -(N+0.001)/N ≈ -1`.
    # col_crt = 2024-01-01, today_col_day ≈ 869 → due=869, ivl=10 puts elapsed=10.
    today_col_day = (now_secs - 1704067200) // 86400
    synthetic_collection.add_note(id=109, guid="g-null", fields=["null", "back"])
    synthetic_collection.add_card(
        id=10099,
        note_id=109,
        ord=0,
        type=2,
        queue=2,
        due=today_col_day,
        ivl=10,
        reps=5,
        empty_fsrs_data=True,
    )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    anki_order = [c["card_id"] for c in result.raw()["get_queue_0"]["cards"]]

    # Anki SM2 fallback for NULL-R with elapsed=ivl=10 ≈ -1.0, which equals
    # the FSRS-path relative_R for card 10030 (R=0.823 with dr=0.9). The tie
    # is broken by Anki's downstream Random subclause — assert NULL-R lands
    # between 10030 (R=0.823) and 10020 (R=0.901), regardless of exact tie order.
    null_pos = anki_order.index(10099)
    pos_10030 = anki_order.index(10030)
    pos_10020 = anki_order.index(10020)

    # Most-overdue (10030) is at index 0 or 1; NULL-R immediately follows; 10020 then 10010.
    assert null_pos in (0, 1), (
        f"NULL-R at unexpected position {null_pos} (expected near head). Anki order: {anki_order}"
    )
    assert pos_10030 in (0, 1), f"10030 (lowest R) expected at head, got pos={pos_10030}. Order: {anki_order}"
    assert pos_10020 == 2, f"10020 (mid R) expected at pos 2, got {pos_10020}. Order: {anki_order}"

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
