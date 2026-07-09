"""Daily-caps parity (Phase 2.2.5, layer cluster: 16, 36).

Pins Anki's daily cap behavior for the queue counts: ``new_per_day`` (deck
config proto field 9) and ``reviews_per_day`` (field 10) bound the queue's
``counts.new`` and ``counts.review`` values regardless of how many cards
are technically due.

What this test covers:
- Anki caps ``counts.new`` at ``new_per_day``
- Anki caps ``counts.review`` at ``reviews_per_day``
- TT reads both caps from the same deck_config protobuf via
  ``queue_stats.resolve_daily_new_cap`` / ``resolve_daily_review_cap``.

What this test does NOT cover (covered by existing TT unit tests):
- TT-side queue assembly cap application (render-only per Layer 36 —
  queue assembly itself doesn't cap; only the badge does)
- ``count_reviews_completed_today`` accounting (Layer 36 internal)
- ``count_new_introduced_today`` with ``introduced_at`` column (Layer 26)
"""

from __future__ import annotations

import time

import pytest

from app.srs.fsrs import DEFAULT_FSRS5_PARAMS
from app.srs.queue_stats import (
    _read_new_per_day_from_anki,
    _read_reviews_per_day_from_anki,
)
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import (
    DEFAULT_DESIRED_RETENTION,
    SyntheticCollection,
)

FSRS_WEIGHTS = DEFAULT_FSRS5_PARAMS.weights


@pytest.mark.oracle
def test_anki_caps_review_count_at_reviews_per_day(synthetic_collection: SyntheticCollection) -> None:
    """Anki caps ``counts.review`` at ``deck_config.reviews_per_day``.

    Setup: 15 due review cards, `reviews_per_day=5`. Anki should report
    counts.review = 5 (capped), and queue should contain at most 5 review
    cards. TT mirrors via Layer 36's ``resolve_daily_review_cap`` reading
    the same proto field.
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_daily_limits(new=20, reviews=5)

    now_secs = int(time.time())
    last_review_secs = now_secs - 5 * 86400

    # 15 review cards, all overdue
    for i in range(15):
        cid = 10010 + i * 10
        note_id = cid // 10
        synthetic_collection.add_note(id=note_id, guid=f"g-{cid}", fields=[f"front-{cid}", "back"])
        synthetic_collection.add_card(
            id=cid,
            note_id=note_id,
            ord=0,
            type=2,
            queue=2,
            due=0,
            ivl=10,
            reps=5,
            stability=10.0,
            difficulty=5.0,
            last_review_secs=last_review_secs,
            desired_retention=DEFAULT_DESIRED_RETENTION,
        )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    counts = result.raw()["get_queue_0"]["counts"]

    assert counts["review"] == 5, (
        f"Anki should cap review count at reviews_per_day=5, got counts.review={counts['review']}. counts={counts}"
    )


@pytest.mark.oracle
def test_anki_caps_new_count_at_new_per_day(synthetic_collection: SyntheticCollection) -> None:
    """Anki caps ``counts.new`` at ``deck_config.new_per_day``.

    Setup: 20 new cards, `new_per_day=3`. Anki should report counts.new = 3.
    TT mirrors via Layer 16's ``resolve_daily_new_cap`` (reads the same proto
    field 9 from deck_config).
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_daily_limits(new=3, reviews=200)

    # 20 new cards (type=0, queue=0)
    for i in range(20):
        cid = 10010 + i * 10
        note_id = cid // 10
        synthetic_collection.add_note(id=note_id, guid=f"g-{cid}", fields=[f"front-{cid}", "back"])
        synthetic_collection.add_card(
            id=cid,
            note_id=note_id,
            ord=0,
            type=0,
            queue=0,
            due=i,  # ascending position
            ivl=0,
            reps=0,
        )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    counts = result.raw()["get_queue_0"]["counts"]

    assert counts["new"] == 3, (
        f"Anki should cap new count at new_per_day=3, got counts.new={counts['new']}. counts={counts}"
    )


@pytest.mark.oracle
def test_anki_review_count_charges_new_cards_studied_today(synthetic_collection: SyntheticCollection) -> None:
    """Anki subtracts today's new-card introductions from ``counts.review``.

    Pins ``rslib/src/decks/limits.rs:104-108`` (Anki 25.09): the review-per-day
    limit is charged by BOTH reviews done today AND new cards introduced today
    (``review_limit -= new_today_count`` when ``new_cards_ignore_review_limit``
    is off — the default). So introducing new cards shrinks the review count
    even though new and review are nominally separate limits.

    The interaction is inherently cross-rebuild: it only shows once reviews
    saturate the limit, but saturated reviews prevent new cards from being
    gathered/answered in the *same* fresh build (Anki caps ``new`` to the
    remaining review budget). So the scenario is: answer 2 NEW cards while no
    reviews are present (``new_studied`` → 2), THEN inject 10 overdue reviews
    (``add_review_cards`` op) in the same process, THEN count. Anki reports
    ``counts.review = 5 − 0 reviews − 2 new = 3``. Layer 76: TT mirrors via
    ``effective_review_budget`` subtracting ``count_new_introduced_today``.

    What this does NOT cover:
    - ``new_cards_ignore_review_limit`` enabled (TT assumes the default off).
    - TT's own count pipeline (pinned by the endpoint regression test).
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_daily_limits(new=20, reviews=5)

    # 5 new cards only (type=0, queue=0). No reviews yet, so they gather to the
    # top of the queue and are answerable by id.
    new_card_ids = []
    for i in range(5):
        cid = 20010 + i * 10
        note_id = cid // 10
        synthetic_collection.add_note(id=note_id, guid=f"g-{cid}", fields=[f"front-{cid}", "back"])
        synthetic_collection.add_card(id=cid, note_id=note_id, ord=0, type=0, queue=0, due=i, ivl=0, reps=0)
        new_card_ids.append(cid)
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [
            {"op": "get_queue", "deck_id": 1, "fetch_limit": 50},
            {"op": "answer_card", "card_id": new_card_ids[0], "rating": 3},
            {"op": "answer_card", "card_id": new_card_ids[1], "rating": 3},
            {"op": "add_review_cards", "count": 10},
            {"op": "get_queue", "deck_id": 1, "fetch_limit": 50},
        ],
    )
    before = result.raw()["get_queue_0"]["counts"]
    ans1 = result.raw()["answer_card_1"]
    ans2 = result.raw()["answer_card_2"]
    after = result.raw()["get_queue_4"]["counts"]

    assert "error" not in ans1, f"answering new card 0 failed: {ans1}"
    assert "error" not in ans2, f"answering new card 1 failed: {ans2}"
    assert before["review"] == 0, f"no reviews present at baseline, got {before}"
    assert after["review"] == 3, (
        f"after studying 2 new cards then adding 10 reviews, review budget should be 5-2=3, got counts={after}. "
        "Anki charges new-card intros against the review limit (limits.rs:104-108)."
    )


def test_tt_reads_caps_from_synthetic_deck_config(synthetic_collection: SyntheticCollection) -> None:
    """TT's ``resolve_daily_*_cap`` reads the same proto fields the oracle uses.

    Validates the round-trip: synthetic_collection writes deck_config with
    `new_per_day=7, reviews_per_day=42`; TT's queue_stats reader extracts
    the same values via the modern-protobuf path. Doesn't need ``--run-oracle``
    — this is a TT-only assertion against the synthetic file format.
    """
    import sqlite3

    synthetic_collection.set_daily_limits(new=7, reviews=42)
    synthetic_collection.save()

    with sqlite3.connect(str(synthetic_collection.path)) as conn:
        new_cap = _read_new_per_day_from_anki(conn, "Default")
        review_cap = _read_reviews_per_day_from_anki(conn, "Default")

    assert new_cap == 7, f"TT reads new_per_day=7 but got {new_cap}"
    assert review_cap == 42, f"TT reads reviews_per_day=42 but got {review_cap}"
