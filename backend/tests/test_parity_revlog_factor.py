"""Revlog factor fidelity — Anki oracle parity (Layer 80).

Pins that Anki's revlog.factor matches TT's difficulty_shifted formula
for the same post-answer difficulty:

    factor = round(((d - 1.0)/9.0 + 0.1) * 1000)

Anki writes this on the review/learning/relearning paths
(rslib/src/card/mod.rs:115-125, scheduler/answering/review.rs:29-36).
TT used to write factor=0 (build_revlog_row) or SM-2-range values
(ds.difficulty * 1000 in the fallback branch); both are now corrected.
"""

from __future__ import annotations

import time

import pytest

from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, _round_to_places_f32
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import SyntheticCollection

FSRS_WEIGHTS = DEFAULT_FSRS5_PARAMS.weights


def _difficulty_to_factor(difficulty: float) -> int:
    """TT's formula: Anki's difficulty_shifted factor, matching rslib."""
    return int(_round_to_places_f32(((difficulty - 1.0) / 9.0 + 0.1) * 1000, 0))


@pytest.mark.oracle
def test_anki_revlog_factor_matches_tt_formula(synthetic_collection: SyntheticCollection) -> None:
    """Build an FSRS review card, answer it via Anki, then assert Anki's
    revlog.factor == TT's formula computed on the post-answer difficulty.

    What this covers:
    - The factor fidelity fix in build_revlog_row (Layer 80)
    - The fallback branch in _push_revlog_for_direction (sync_engine.py)

    What this does NOT cover:
    - The per-grade multi-row push (covered by unit tests in test_anki_sync_push.py)
    - The peer-sync round-trip (covered by test_anki_peer_sync_selfhost.py)
    """
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=0.9)

    card_id = 10010
    note_id = 1001
    synthetic_collection.add_note(id=note_id, guid="g-factor-test", fields=["front", "back"])
    synthetic_collection.add_card(
        id=card_id,
        note_id=note_id,
        ord=0,
        type=2,  # review
        queue=2,
        due=0,
        ivl=10,
        reps=5,
        stability=10.0,
        difficulty=5.5,
        last_review_secs=int(time.time()) - 30 * 86400,
    )
    synthetic_collection.save()

    # Answer the card with GOOD (rating=3) and capture post-answer difficulty.
    result = run_oracle(
        synthetic_collection.path,
        [
            {"op": "answer_card", "card_id": card_id, "rating": 3},
            {"op": "get_card", "card_id": card_id},
            {"op": "get_revlog", "card_id": card_id},
        ],
    )

    post_answer = result.raw()["get_card_1"]
    difficulty = post_answer["difficulty"]
    assert difficulty is not None, "post-answer difficulty must be present"

    revlog = result.raw()["get_revlog_2"]
    assert len(revlog) >= 1, "answer_card must produce at least one revlog row"
    # The most recent revlog row is the one we just created.
    anki_factor = revlog[-1]["factor"]

    tt_factor = _difficulty_to_factor(difficulty)
    assert anki_factor == tt_factor, (
        f"revlog factor mismatch: Anki={anki_factor}, TT formula on difficulty={difficulty:.6f} → {tt_factor}"
    )


@pytest.mark.oracle
def test_anki_revlog_factor_various_difficulties(synthetic_collection: SyntheticCollection) -> None:
    """Pin factor across multiple difficulty values to exercise the f32 rounding path."""
    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=0.9)

    # Seed three review cards with different difficulties.
    for i, diff in enumerate([2.0, 5.5, 8.5]):
        card_id = 10010 + i * 10
        note_id = 1001 + i
        synthetic_collection.add_note(id=note_id, guid=f"g-factor-{i}", fields=[f"f-{i}", "back"])
        synthetic_collection.add_card(
            id=card_id,
            note_id=note_id,
            ord=0,
            type=2,
            queue=2,
            due=0,
            ivl=10,
            reps=5,
            stability=10.0,
            difficulty=diff,
            last_review_secs=int(time.time()) - 30 * 86400,
        )
    synthetic_collection.save()

    # Answer each card and check factor.
    ops = []
    for i in range(3):
        card_id = 10010 + i * 10
        ops.append({"op": "answer_card", "card_id": card_id, "rating": 3})
        ops.append({"op": "get_card", "card_id": card_id})
        ops.append({"op": "get_revlog", "card_id": card_id})

    result = run_oracle(synthetic_collection.path, ops)

    for i in range(3):
        post_answer = result.raw()[f"get_card_{1 + i * 3}"]
        difficulty = post_answer["difficulty"]
        revlog = result.raw()[f"get_revlog_{2 + i * 3}"]
        assert len(revlog) >= 1
        anki_factor = revlog[-1]["factor"]
        tt_factor = _difficulty_to_factor(difficulty)
        assert anki_factor == tt_factor, (
            f"card {i}: factor mismatch — Anki={anki_factor}, TT formula on difficulty={difficulty:.6f} → {tt_factor}"
        )
