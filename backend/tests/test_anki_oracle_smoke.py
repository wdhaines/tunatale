"""Smoke test for the Anki oracle harness (Phase 2.1 verification).

Proves the full scaffolding works end-to-end: ``SyntheticCollection`` builds a
file Anki can open, ``oracle.py`` runs Anki's scheduler against it, and
``run_oracle`` round-trips the results through pytest fixtures.

Skipped unless ``--run-oracle`` is passed (and skipped with a clear reason
when the ``anki`` package can't be installed in the subprocess).
"""

from __future__ import annotations

import pytest

from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import SyntheticCollection


@pytest.mark.oracle
def test_oracle_returns_seeded_review_card(synthetic_collection: SyntheticCollection) -> None:
    """Add one review card; oracle returns it at the queue head."""
    synthetic_collection.add_note(
        id=1001,
        guid="smoke-guid",
        fields=["front-text", "back-text"],
    )
    synthetic_collection.add_card(
        id=10010,
        note_id=1001,
        ord=0,
        type=2,
        queue=2,
        due=0,
        ivl=10,
        reps=5,
        stability=10.0,
        difficulty=4.0,
    )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )

    queue = result.queue()
    assert len(queue) >= 1, f"expected ≥1 queued card, got {queue}"
    card_ids = [c["card_id"] for c in queue]
    assert 10010 in card_ids, f"card 10010 not in queue: {card_ids}"

    head = next(c for c in queue if c["card_id"] == 10010)
    assert head["queue"] == 2
    assert head["reps"] == 5
    assert head["memory_state"] == {"stability": 10.0, "difficulty": 4.0}


@pytest.mark.oracle
def test_oracle_counts_reflect_seeded_state(synthetic_collection: SyntheticCollection) -> None:
    """Oracle's ``counts`` dict separates new from review cards."""
    synthetic_collection.add_note(id=2001, guid="new-guid", fields=["new-front", ""])
    synthetic_collection.add_card(id=20010, note_id=2001, ord=0, type=0, queue=0, due=0)

    synthetic_collection.add_note(id=2002, guid="rev-guid", fields=["rev-front", ""])
    synthetic_collection.add_card(
        id=20020,
        note_id=2002,
        ord=0,
        type=2,
        queue=2,
        due=0,
        ivl=10,
        reps=3,
        stability=12.0,
        difficulty=4.0,
    )
    synthetic_collection.save()

    result = run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    )

    counts = result.counts()
    assert counts.get("new", 0) >= 1, f"expected ≥1 new card, got {counts}"
    assert counts.get("review", 0) >= 1, f"expected ≥1 review card, got {counts}"
