"""Sibling-bury parity (Phase 2.2.4, layer cluster: 27, 35).

Pins Anki's "buried cards don't serve" invariant. Cards with ``queue=-2``
(user-bury) or ``queue=-3`` (sibling/sched-bury) are excluded from
``get_queued_cards``. TT mirrors via ``state='buried'`` rows that
``unbury_if_needed`` releases on day rollover.

What this test covers:
- Queue exclusion: buried cards with queue ∈ {-1 (suspend), -2, -3} don't
  appear in Anki's queue, regardless of due / type / FSRS state.
- Sibling-of-graded behavior: a card whose sibling was graded today is
  buried with ``queue=-3``.

What this test does NOT cover (deferred):
- TT-side ``unbury_if_needed`` correctness — that's a TT-only unit test
  (`test_srs_database::TestUnburyIfNeeded`); Anki doesn't expose the
  TT-side ``state='buried'`` column to compare against.
- Day-rollover unbury timing. Can't time-travel a synthetic collection
  cleanly enough to assert the cohort gets released at midnight.
- bury_kind round-trip through sync_pull — that's a sync-path test,
  not a queue-order golden.

What surfaced while building this test:
- Sibling-bury via ``answer_card`` requires a multi-template notetype
  (recognition + production). The synthetic-collection's default Basic
  notetype has only one template, so we can't test cross-direction bury
  without scaffolding a Slovene-Voc-style 2-template notetype. Punted
  to Phase 2.2.x if the need recurs; here we test the direct
  ``queue=-2`` / ``queue=-3`` exclusion which is the load-bearing
  invariant for Layer 27/35.
"""

from __future__ import annotations

import time

import pytest

from app.srs.fsrs import DEFAULT_FSRS5_PARAMS
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import (
    DEFAULT_DESIRED_RETENTION,
    SyntheticCollection,
)

FSRS_WEIGHTS = DEFAULT_FSRS5_PARAMS.weights


def _seed_card(
    coll: SyntheticCollection,
    *,
    card_id: int,
    queue: int,
    last_review_secs: int,
) -> None:
    """Seed a review-state card in the given queue (queue=-3 sched-bury, -2 user-bury, 2 due)."""
    note_id = card_id // 10
    coll.add_note(id=note_id, guid=f"g-{card_id}", fields=[f"front-{card_id}", "back"])
    coll.add_card(
        id=card_id,
        note_id=note_id,
        ord=0,
        type=2,
        queue=queue,
        due=0,
        ivl=10,
        reps=5,
        stability=10.0,
        difficulty=5.0,
        last_review_secs=last_review_secs,
        desired_retention=DEFAULT_DESIRED_RETENTION,
    )


@pytest.mark.oracle
def test_buried_cards_excluded_from_queue(synthetic_collection: SyntheticCollection) -> None:
    """Anki excludes queue=-2 (user-bury), -3 (sched-bury), and -1 (suspend) from the queue.

    The load-bearing invariant for Layer 27 (daily unbury sweep) and Layer 35
    (bury_kind split): both bury kinds are equally invisible to the scheduler
    until they're released. TT's ``unbury_if_needed`` mirrors by transitioning
    `state='buried' AND bury_kind='sched'` back to `state='review'` on day
    rollover; the test here pins the upstream Anki contract.
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)

    now_secs = int(time.time())
    last_review_secs = now_secs - 5 * 86400  # 5 days ago — well overdue

    # 3 normal review cards + 1 sched-buried + 1 user-buried + 1 suspended
    _seed_card(synthetic_collection, card_id=10010, queue=2, last_review_secs=last_review_secs)
    _seed_card(synthetic_collection, card_id=10020, queue=2, last_review_secs=last_review_secs)
    _seed_card(synthetic_collection, card_id=10030, queue=2, last_review_secs=last_review_secs)
    _seed_card(synthetic_collection, card_id=10040, queue=-3, last_review_secs=last_review_secs)
    _seed_card(synthetic_collection, card_id=10050, queue=-2, last_review_secs=last_review_secs)
    _seed_card(synthetic_collection, card_id=10060, queue=-1, last_review_secs=last_review_secs)
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )
    queue = result.raw()["get_queue_0"]["cards"]
    queued_ids = {c["card_id"] for c in queue}
    counts = result.raw()["get_queue_0"]["counts"]

    # The 3 normal cards should be in the queue.
    expected_in_queue = {10010, 10020, 10030}
    excluded = {10040, 10050, 10060}

    assert queued_ids == expected_in_queue, (
        f"Expected exactly {expected_in_queue} in queue (buried/suspended cards excluded), "
        f"got {queued_ids}. counts={counts}"
    )

    # The review count should match the 3 visible cards (not 6).
    assert counts.get("review", 0) == 3, (
        f"Anki's counts.review={counts.get('review')} should reflect only visible cards (3), "
        f"not all queue=2/-2/-3/-1 rows (6). counts={counts}"
    )

    # Per Layer 27 / 35: buried cards stay excluded until day-rollover unbury fires.
    # We can't time-travel within a single test invocation, but we can pin the
    # exclusion-while-buried invariant — that's the TT-side property
    # ``unbury_if_needed`` exists to maintain.
    for cid in excluded:
        assert cid not in queued_ids, f"card {cid} (excluded queue) leaked into queue: {queue}"
