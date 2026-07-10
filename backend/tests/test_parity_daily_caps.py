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
- TT-side queue assembly cap application — since Layer 75 the caps limit the
  SERVED queue too, not just the badge (`_compute_live_main` slices both the
  review and new pools); pinned by the `/review-queue` cap tests in
  ``test_api_srs.py``
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


@pytest.mark.oracle
def test_anki_caps_new_cards_to_remaining_review_budget(synthetic_collection: SyntheticCollection) -> None:
    """Anki caps gathered NEW cards at the review budget left after review gather.

    Pins the dynamic half of ``rslib/src/decks/limits.rs``: construction caps
    ``new_limit = min(new_limit, review_limit)`` (limits.rs:104-108), then every
    review gathered into the SAME build decrements the review limit and re-mins
    the new limit (``decrement()``, limits.rs:131-141). So with reviews_per_day=5,
    2 due reviews, 10 new cards and new_per_day=20, Anki gathers 2 reviews and
    min(20, 10, 5−2) = 3 new — not 10. Layer 77: TT mirrors in
    ``_compute_live_main`` by capping the new slice at
    ``review_budget − len(review slice)``.

    What this does NOT cover:
    - ``new_cards_ignore_review_limit`` enabled (TT assumes the default off).
    - TT's served-queue pipeline (pinned by the `/review-queue` endpoint tests).
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_daily_limits(new=20, reviews=5)

    now_secs = int(time.time())
    last_review_secs = now_secs - 5 * 86400

    # 2 overdue review cards
    for i in range(2):
        cid = 30010 + i * 10
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
    # 10 new cards
    for i in range(10):
        cid = 40010 + i * 10
        note_id = cid // 10
        synthetic_collection.add_note(id=note_id, guid=f"g-{cid}", fields=[f"front-{cid}", "back"])
        synthetic_collection.add_card(id=cid, note_id=note_id, ord=0, type=0, queue=0, due=i, ivl=0, reps=0)
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    counts = result.raw()["get_queue_0"]["counts"]

    assert counts["review"] == 2, f"2 due reviews under a cap of 5, got {counts}"
    assert counts["new"] == 3, (
        f"new gather must stop at the remaining review budget 5−2=3 (new_per_day=20, 10 available), "
        f"got counts.new={counts['new']}. counts={counts}"
    )


@pytest.mark.oracle
def test_anki_new_cards_ignore_review_limit_flips_new_cap(synthetic_collection: SyntheticCollection) -> None:
    """Anki's ``new_cards_ignore_review_limit`` deck option lifts the review cap on new cards.

    Ground-truth pin for brief #4a. Resolves the storage-location ambiguity
    EMPIRICALLY: the source reads ``col.get_config_bool(BoolKey::NewCardsIgnoreReviewLimit)``
    (``rslib/src/scheduler/queue/builder/mod.rs:132``, ``rslib/src/decks/limits.rs:106``),
    a COLLECTION-level config-table bool — the UI presents it under deck options
    but persists it at collection scope. The exact stored key was captured against
    the real binary by driving ``update_deck_configs`` and dumping the ``config``
    table: key ``newCardsIgnoreReviewLimit``, val JSON ``true``. That is the field
    #37-vs-#40 (Layer 38) footgun this test exists to foreclose.

    Scenario (same collection, two states): reviews_per_day=5 saturated by 5 due
    reviews, new_per_day=10 with 10 new available.
      - flag OFF (key absent): review budget exhausted ⇒ ``counts.new == 0``.
      - flag ON  (key ``true``): new cards ignore the review budget ⇒
        ``counts.new == 10`` (min(new_per_day, available)).

    What this does NOT cover:
    - TT's own count/queue pipeline (pinned by the flag-ON endpoint tests in
      ``test_api_srs.py``).
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_daily_limits(new=10, reviews=5)

    now_secs = int(time.time())
    last_review_secs = now_secs - 5 * 86400

    # 5 overdue review cards — saturate reviews_per_day.
    for i in range(5):
        cid = 50010 + i * 10
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
    # 10 new cards.
    for i in range(10):
        cid = 60010 + i * 10
        note_id = cid // 10
        synthetic_collection.add_note(id=note_id, guid=f"g-{cid}", fields=[f"front-{cid}", "back"])
        synthetic_collection.add_card(id=cid, note_id=note_id, ord=0, type=0, queue=0, due=i, ivl=0, reps=0)
    synthetic_collection.save()

    off = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    ).raw()["get_queue_0"]["counts"]
    assert off["review"] == 5, f"5 due reviews under a cap of 5, got {off}"
    assert off["new"] == 0, (
        f"with new_cards_ignore_review_limit OFF and the review budget saturated, "
        f"new must be capped to 0, got counts.new={off['new']}. counts={off}"
    )

    # Flip the flag ON at collection scope (the storage the source reads).
    synthetic_collection.set_config_value("newCardsIgnoreReviewLimit", True)
    synthetic_collection.save()

    on = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    ).raw()["get_queue_0"]["counts"]
    assert on["review"] == 5, f"5 due reviews under a cap of 5, got {on}"
    assert on["new"] == 10, (
        f"with new_cards_ignore_review_limit ON, new ignores the review budget and "
        f"caps only at new_per_day=10 (10 available), got counts.new={on['new']}. counts={on}"
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


@pytest.mark.oracle
def test_anki_interday_learning_charges_review_limit(synthetic_collection: SyntheticCollection) -> None:
    """Interday learning cards (queue=3, DayLearn) consume the review-per-day budget.

    Ground-truth pin for brief #4b (Layer 79). Anki's queue builder gathers
    interday learning under the REVIEW limit: ``gather_due_cards`` hardcodes
    ``LimitKind::Review`` for BOTH ``DueCardKind::Learning`` and ``::Review``
    (``rslib/src/scheduler/queue/builder/gathering.rs:35-61``), in gather order
    intraday-learning → interday-learning → reviews → new (``gathering.rs:14-21``).
    Each gathered interday card runs the same ``decrement(LimitKind::Review)``
    that re-mins the new headroom (``rslib/src/decks/limits.rs:131-143``). The
    cards still DISPLAY in the learning count, not the review count
    (``builder/mod.rs:189-218``: ``day_learning`` feeds ``learn_count``).

    Scenario: reviews_per_day=3, new_per_day=20; 2 interday learning cards due
    today + 5 due reviews + 4 new cards. Expected counts:
      - learning == 2  (interday cards, displayed as learning)
      - review   == 1  (3 budget − 2 consumed by interday learning)
      - new      == 0  (headroom 3 − 2 interday − 1 review = 0)

    Pre-Layer-79 TT reported review=3 / learning=2 here (interday exempt from
    the budget) — the review discrepancy is the discriminator.

    What this does NOT cover:
    - interday count EXCEEDING the budget (Anki then gathers only budget-many
      interday cards, shrinking the learning count too — known TT residual,
      documented in the Layer 79 entry).
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_daily_limits(new=20, reviews=3)

    now_secs = int(time.time())
    last_review_secs = now_secs - 5 * 86400

    # 5 overdue review cards.
    for i in range(5):
        cid = 60010 + i * 10
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

    # 4 new cards.
    for i in range(4):
        cid = 61010 + i * 10
        note_id = cid // 10
        synthetic_collection.add_note(id=note_id, guid=f"g-{cid}", fields=[f"front-{cid}", "back"])
        synthetic_collection.add_card(id=cid, note_id=note_id, ord=0, type=0, queue=0, due=i, ivl=0, reps=0)

    # Save, then ask Anki for its authoritative day index (gotcha #10: never
    # compute `today` Python-side — the 4AM local rollover shifts it ±1).
    synthetic_collection.save()
    anki_today = run_oracle(synthetic_collection.path, [{"op": "get_today"}]).raw()["get_today_0"]["today"]

    # 2 interday learning cards: queue=3 (DAY_LEARN), day-level due = today.
    from app.srs.fsrs import _pack_left

    for i in range(2):
        cid = 62010 + i * 10
        note_id = cid // 10
        synthetic_collection.add_note(id=note_id, guid=f"g-{cid}", fields=[f"front-{cid}", "back"])
        synthetic_collection.add_card(
            id=cid,
            note_id=note_id,
            ord=0,
            type=1,
            queue=3,
            due=anki_today,
            ivl=0,
            reps=1,
            left=_pack_left(1),
            stability=1.0,
            difficulty=5.0,
            last_review_secs=now_secs - 86400,
            desired_retention=DEFAULT_DESIRED_RETENTION,
        )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    counts = result.raw()["get_queue_0"]["counts"]

    assert counts["learning"] == 2, f"interday learning cards must display in the learning count, got {counts}"
    assert counts["review"] == 1, (
        f"2 interday learning cards must consume 2 of the 3-review budget, leaving review=1, got {counts}"
    )
    assert counts["new"] == 0, (
        f"interday learning + the gathered review exhaust the new headroom (3-2-1=0), got {counts}"
    )
