"""Stage 3: an Anki-side grade pulled by sync clears the card's pending row.

The pending bucket is TT-only, so Anki has no idea a provisional grade is
waiting. If the user grades the card in Anki instead of in "Check your work",
the next ``sync_pull`` brings that real grade in — and the stale provisional row
must go with it. Leaving it would double-grade the card the next time the user
hits "Sync it", and would keep the card hidden from TT's main queue on the
strength of a grade that has already happened.

Only a *grade* clears it. A pull that merely re-bury-flips or bumps ``mod``
leaves the pending row alone: nothing has been reviewed, so the provisional
grade is still the truth about what the listen staged.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.srs_item import Direction, DirectionState, SRSState
from app.plugins.anki_sync.sync import AnkiSync
from tests._helpers.anki_sync_pull import FakeReader, FakeWriter, _add_banka, _make_tt_db
from tests.conftest import make_card_record, make_note_record

ANKI_CARD_ID = 90010


def _seed_local(db, guid: str, *, reps: int, last_review: datetime | None) -> int:
    """Point TT's recognition direction at an Anki card with a known review history."""
    item = db.get_collocation("banka")
    db.update_direction(
        guid,
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_at=datetime.now(UTC) + timedelta(days=1),
            stability=5.0,
            difficulty=4.5,
            reps=reps,
            lapses=0,
            last_review=last_review,
            anki_card_id=ANKI_CARD_ID,
        ),
    )
    cid = db.get_collocation_id_by_guid(item.guid)
    assert cid is not None
    return cid


def _pull(db, **card_kwargs) -> None:
    records = [
        make_note_record(
            anki_guid=db.get_collocation("banka").guid,
            cards=[make_card_record(anki_card_id=ANKI_CARD_ID, **card_kwargs)],
        )
    ]
    AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull()


class TestSyncPullClearsPendingOnAnkiGrade:
    def test_a_higher_rep_count_from_anki_clears_the_pending_row(self):
        db = _make_tt_db()
        guid = _add_banka(db)
        yesterday = datetime.now(UTC) - timedelta(days=1)
        cid = _seed_local(db, guid, reps=5, last_review=yesterday)
        db.stage_pending_grade("lesson-1", cid, Direction.RECOGNITION.value, "good", "due")

        _pull(db, reps=6, last_review=datetime.now(UTC))

        assert db.get_pending_grade(cid, Direction.RECOGNITION.value) is None

    def test_a_newer_last_review_from_anki_clears_the_pending_row(self):
        """reps can arrive equal (Anki's reps is not always ahead — e.g. a
        re-grade after an undo), so a forward jump in last_review counts too."""
        db = _make_tt_db()
        guid = _add_banka(db)
        cid = _seed_local(db, guid, reps=5, last_review=datetime.now(UTC) - timedelta(days=3))
        db.stage_pending_grade("lesson-1", cid, Direction.RECOGNITION.value, "good", "due")

        _pull(db, reps=5, last_review=datetime.now(UTC))

        assert db.get_pending_grade(cid, Direction.RECOGNITION.value) is None

    def test_first_ever_review_from_anki_clears_the_pending_row(self):
        db = _make_tt_db()
        guid = _add_banka(db)
        cid = _seed_local(db, guid, reps=0, last_review=None)
        db.stage_pending_grade("lesson-1", cid, Direction.RECOGNITION.value, "good", "due")

        _pull(db, reps=1, last_review=datetime.now(UTC))

        assert db.get_pending_grade(cid, Direction.RECOGNITION.value) is None

    def test_a_pull_with_no_new_review_keeps_the_pending_row(self):
        """A bury flip / mod bump is not a grade — the staged grade still stands."""
        db = _make_tt_db()
        guid = _add_banka(db)
        graded_at = datetime.now(UTC) - timedelta(days=1)
        cid = _seed_local(db, guid, reps=5, last_review=graded_at)
        db.stage_pending_grade("lesson-1", cid, Direction.RECOGNITION.value, "good", "due")

        # Same review history, different queue → a write, but not a grade.
        _pull(db, reps=5, last_review=graded_at, queue=-2)

        assert db.get_pending_grade(cid, Direction.RECOGNITION.value) is not None

    def test_only_the_pulled_direction_is_cleared(self):
        db = _make_tt_db()
        guid = _add_banka(db)
        cid = _seed_local(db, guid, reps=5, last_review=datetime.now(UTC) - timedelta(days=1))
        db.stage_pending_grade("lesson-1", cid, Direction.RECOGNITION.value, "good", "due")
        db.stage_pending_grade("lesson-1", cid, Direction.PRODUCTION.value, "good", "due")

        _pull(db, reps=6, last_review=datetime.now(UTC))

        assert db.get_pending_grade(cid, Direction.RECOGNITION.value) is None
        assert db.get_pending_grade(cid, Direction.PRODUCTION.value) is not None

    def test_a_dry_run_pull_does_not_clear(self):
        """Dry run writes nothing; clearing a pending row is a write.

        Belt and braces: the clear sits inside the pull's ``if not dry_run``
        branch AND ``sync_pull`` wraps the whole pass in
        ``begin_transaction(dry_run=...)``, which rolls back. The rollback alone
        makes this test green even if the clear were moved out of the branch, so
        this test cannot be used to prove the branch placement — don't conclude
        the placement is redundant and hoist it out.
        """
        db = _make_tt_db()
        guid = _add_banka(db)
        cid = _seed_local(db, guid, reps=5, last_review=datetime.now(UTC) - timedelta(days=1))
        db.stage_pending_grade("lesson-1", cid, Direction.RECOGNITION.value, "good", "due")

        records = [
            make_note_record(
                anki_guid=guid,
                cards=[make_card_record(anki_card_id=ANKI_CARD_ID, reps=6, last_review=datetime.now(UTC))],
            )
        ]
        AnkiSync(db=db, _reader=FakeReader(records), _writer=FakeWriter()).sync_pull(dry_run=True)

        assert db.get_pending_grade(cid, Direction.RECOGNITION.value) is not None
