"""Stage 3b — the collapsed ``_pull_merge_direction`` resolution + its invariant.

``_pull_merge_direction`` (sync_engine.py) was a 9-branch tree; Stage 3b
collapsed it to one take-Anki resolution with two keep-TT guards (the Layer-70
recency guard + fsrs_unknown), with suspend/bury falling out of
``_queue_to_state`` / ``_bury_kind_from_queue`` (plan:
``ticklish-questing-fountain.md``). The former ``dirty_fsrs`` branches were
*deleted* once proven unreachable in a real sync.

This module pins two things:

1. The **who-wins of the stored DirectionState** — for each input shape, whether
   each authoritative field (stability/difficulty/state/last_review/reps/…) comes
   from TT's local row or from Anki's ``cards.data``: the Layer-70 recency guard
   keeps TT's memory state + grade timestamp, fsrs_unknown keeps TT's s/d,
   otherwise Anki is authoritative (take-Anki-verbatim).
2. The **"dirty branches are dead" invariant** that justified deleting them:
   ``sync_push`` (which runs before pull in ``run_full_sync``) clears
   ``dirty_fsrs`` for every Anki-linked direction, so pull never sees dirty in a
   real sync. The DIRTY_AT_PULL guard in the caller makes any violation loud.

Deliberately NOT pinned: ``report.conflicts`` / ``recompute_divergences``
telemetry (covered by ``test_anki_sync_pull_event_mode.py``); which branch
produced the result — only the resulting DirectionState matters.

Driven sociably through ``sync_pull`` / ``sync_push`` (the real wiring), only the
Anki driver faked, matching the mock-boundary policy.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.anki.sync import AnkiSync
from app.models.srs_item import Direction, DirectionState, SRSState
from app.srs.database import SRSDatabase
from tests.conftest import make_card_record, make_note_record
from tests.test_anki_sync_pull import FakeReader, FakeWriter, _add_banka, _make_tt_db
from tests.test_anki_sync_push import FakeReader as PushFakeReader
from tests.test_anki_sync_push import FakeWriter as PushFakeWriter

# Matches make_card_record's default anki_card_id so the seeded direction and the
# Anki record refer to the same card.
_CARD_ID = 90010


def _seed(
    db: SRSDatabase,
    guid: str,
    *,
    state: SRSState = SRSState.REVIEW,
    stability: float = 5.0,
    difficulty: float = 4.5,
    reps: int = 3,
    lapses: int = 0,
    dirty_fsrs: bool = False,
    left: int | None = None,
    last_rating: int | None = None,
    last_review: datetime | None = None,
    days_ago: int = 10,
) -> DirectionState:
    """Seed banka RECOGNITION with the given local state; return what was stored.

    ``last_review`` defaults to ``days_ago`` days back at the current wall-clock
    time-of-day (sub-second precision → never day-level, so it doesn't trip the
    Layer-72 day-level guard unless a test asks for it).
    """
    if last_review is None:
        last_review = datetime.now(UTC) - timedelta(days=days_ago)
    ds = DirectionState(
        direction=Direction.RECOGNITION,
        due_at=last_review + timedelta(days=10),
        stability=stability,
        difficulty=difficulty,
        reps=reps,
        lapses=lapses,
        state=state,
        last_review=last_review,
        last_review_time_ms=int(last_review.timestamp() * 1000),
        anki_card_id=_CARD_ID,
        dirty_fsrs=dirty_fsrs,
        left=left,
        last_rating=last_rating,
        last_synced_at=last_review.isoformat(),
    )
    db.update_direction(guid, Direction.RECOGNITION, ds)
    return ds


def _pull(db: SRSDatabase, guid: str, card) -> DirectionState:
    """Run a sync_pull for one banka card; return the resulting RECOGNITION state."""
    reader = FakeReader([make_note_record(anki_guid=guid, cards=[card])])
    AnkiSync(db=db, _reader=reader, _writer=FakeWriter()).sync_pull()
    item = db.get_collocation_by_guid(guid)
    assert item is not None
    return item.directions[Direction.RECOGNITION]


# ── the merge resolution: who-wins per field (TT local vs Anki cards.data) ──────


def test_clean_tt_memory_newer_keeps_tt_fsrs_takes_anki_scheduling():
    """Layer-70 recency guard (the cid=869 shape): clean local row, TT graded
    after Anki's lrt → keep TT's stability/difficulty/last_review; take Anki's
    reps/state/due. This is the branch the collapse MUST preserve."""
    db = _make_tt_db()
    guid = _add_banka(db)
    # Sub-second precision now → not day-level; recent so it postdates Anki's lrt.
    tt_now = datetime.now(UTC)
    seed = _seed(db, guid, stability=6.869, difficulty=3.226, last_review=tt_now, last_rating=4)

    stale_lrt = tt_now - timedelta(days=20)
    card = make_card_record(
        anki_card_id=_CARD_ID,
        queue=2,
        reps=4,
        stability=4.1946,
        difficulty=4.939,
        last_review=stale_lrt,
        last_review_ms=int(stale_lrt.timestamp() * 1000),
    )
    after = _pull(db, guid, card)

    assert after.stability == 6.869  # TT kept (recency guard)
    assert after.difficulty == 3.226  # TT kept
    assert after.last_review == seed.last_review  # TT kept
    assert after.reps == 4  # Anki scheduling
    assert after.state == SRSState.REVIEW


def test_clean_fsrs_known_takes_anki_fsrs():
    """Standard take-Anki: clean local row, Anki not stale → take Anki's
    stability/difficulty + resolved last_review."""
    db = _make_tt_db()
    guid = _add_banka(db)
    _seed(db, guid, stability=5.0, difficulty=4.5, days_ago=10)

    anki_lr = datetime.now(UTC) - timedelta(days=1)  # newer than TT's → not TT-ahead
    card = make_card_record(
        anki_card_id=_CARD_ID,
        queue=2,
        reps=4,
        stability=7.25,
        difficulty=4.6,
        last_review=anki_lr,
        last_review_ms=int(anki_lr.timestamp() * 1000),
    )
    after = _pull(db, guid, card)

    assert after.stability == 7.25  # Anki
    assert after.difficulty == 4.6  # Anki
    assert after.last_review == anki_lr  # resolved-from-Anki
    assert after.reps == 4
    assert after.state == SRSState.REVIEW


def test_clean_fsrs_unknown_keeps_local_fsrs_takes_anki_scheduling():
    """Default branch: clean local row, Anki has no FSRS data (fsrs_known=False,
    placeholder s/d) → keep TT's stability/difficulty; take Anki reps/state."""
    db = _make_tt_db()
    guid = _add_banka(db)
    _seed(db, guid, stability=5.0, difficulty=4.5)

    card = make_card_record(
        anki_card_id=_CARD_ID,
        queue=2,
        reps=4,
        stability=1.0,  # placeholder
        difficulty=5.0,  # placeholder
        fsrs_known=False,
    )
    after = _pull(db, guid, card)

    assert after.stability == 5.0  # TT kept (Anki had no real FSRS data)
    assert after.difficulty == 4.5  # TT kept
    assert after.reps == 4  # Anki scheduling
    assert after.state == SRSState.REVIEW


def test_clean_bury_via_queue_to_state():
    """Clean path has no dedicated bury branch: queue=-2 flows through the
    fsrs_known branch and is mapped to BURIED via _queue_to_state + bury_kind."""
    db = _make_tt_db()
    guid = _add_banka(db)
    _seed(db, guid, stability=5.0)

    card = make_card_record(anki_card_id=_CARD_ID, queue=-2, reps=4, stability=7.25)
    after = _pull(db, guid, card)

    assert after.state == SRSState.BURIED
    assert after.bury_kind == "sched"


def test_clean_suspend_via_queue_to_state():
    """Clean path: queue=-1 → SUSPENDED via _queue_to_state, no bury_kind."""
    db = _make_tt_db()
    guid = _add_banka(db)
    _seed(db, guid, stability=5.0)

    card = make_card_record(anki_card_id=_CARD_ID, queue=-1, reps=4, stability=7.25)
    after = _pull(db, guid, card)

    assert after.state == SRSState.SUSPENDED
    assert after.bury_kind is None


# ── "dirty branches are dead in production" invariant ─────────────────────────
#
# _pull_merge_direction's dirty branches (1, 2a–2f) only matter if a direction
# can still be dirty_fsrs=True when pull processes it. These tests pin the
# invariant that makes those branches unreachable in a real sync — the
# foundation for deleting them (a behavior-preserving deletion, justified by
# proof rather than a soak): sync_push (which runs before pull in run_full_sync)
# clears dirty_fsrs for every Anki-linked direction, so a real non-dry-run sync
# never reaches them. They survive only for dry-run and direct-pull tests.


def test_sync_push_clears_every_dirty_linked_direction():
    """Foundation of the invariant: sync_push clears dirty_fsrs for every
    Anki-LINKED dirty direction, in every state. This is what guarantees pull
    never sees a dirty direction in a real (push-then-pull) sync."""
    for state, left in (
        (SRSState.REVIEW, None),
        (SRSState.LEARNING, 1001),
        (SRSState.SUSPENDED, None),
    ):
        db = _make_tt_db()
        guid = _add_banka(db)
        _seed(db, guid, state=state, dirty_fsrs=True, left=left, reps=2, last_rating=3)

        AnkiSync(db=db, _reader=PushFakeReader(), _writer=PushFakeWriter()).sync_push()

        after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert after.dirty_fsrs is False, f"sync_push left a {state.value} direction dirty"


def test_sync_push_leaves_unlinked_dirty_direction_dirty():
    """The one exception: a dirty direction with no anki_card_id is NOT cleared
    by push (sync_create_new owns minting it). It's also never routed through
    _pull_merge_direction — Anki has no card for it, so pull emits no card_rec."""
    db = _make_tt_db()
    guid = _add_banka(db)
    ds = DirectionState(
        direction=Direction.RECOGNITION,
        due_at=datetime.now(UTC) + timedelta(days=1),
        state=SRSState.REVIEW,
        reps=1,
        dirty_fsrs=True,
        anki_card_id=None,
        last_review=datetime.now(UTC),
    )
    db.update_direction(guid, Direction.RECOGNITION, ds)

    AnkiSync(db=db, _reader=PushFakeReader(), _writer=PushFakeWriter()).sync_push()

    after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
    assert after.dirty_fsrs is True  # push skipped it (anki_card_id is None)


def test_dirty_at_pull_guard_warns_when_push_skipped(caplog):
    """Teeth for the DIRTY_AT_PULL guard: a dirty direction reaching pull
    without a preceding push (the impossible-in-production state the dirty
    branches handle) is flagged loudly."""
    db = _make_tt_db()
    guid = _add_banka(db)
    coll_id = db.get_collocation_id_by_guid(guid)
    _seed(db, guid, dirty_fsrs=True, stability=5.0)

    card = make_card_record(anki_card_id=_CARD_ID, queue=2, reps=4, stability=7.25)
    with caplog.at_level(logging.WARNING, logger="app.anki.sync"):
        AnkiSync(
            db=db,
            _reader=FakeReader([make_note_record(anki_guid=guid, cards=[card])]),
            _writer=FakeWriter(),
        ).sync_pull()

    assert f"DIRTY_AT_PULL cid={coll_id}" in caplog.text


def test_real_sync_sequence_leaves_no_dirty_at_pull(caplog):
    """The invariant end-to-end: in a real push-then-pull sequence, push clears
    dirty so pull never reaches a dirty branch — no DIRTY_AT_PULL warning."""
    db = _make_tt_db()
    guid = _add_banka(db)
    _seed(db, guid, dirty_fsrs=True, stability=5.0, reps=2, last_rating=3)

    # Push first, as run_full_sync does — this clears dirty_fsrs.
    AnkiSync(db=db, _reader=PushFakeReader(), _writer=PushFakeWriter()).sync_push()
    assert db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION].dirty_fsrs is False

    # Then pull — the direction is clean, so the guard stays silent.
    card = make_card_record(anki_card_id=_CARD_ID, queue=2, reps=4, stability=7.25)
    with caplog.at_level(logging.WARNING, logger="app.anki.sync"):
        AnkiSync(
            db=db,
            _reader=FakeReader([make_note_record(anki_guid=guid, cards=[card])]),
            _writer=FakeWriter(),
        ).sync_pull()

    assert "DIRTY_AT_PULL" not in caplog.text
