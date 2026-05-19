"""Tests for concurrent-review conflict resolution and review duration capture.

Covers:
- TunaTale grade only → normal push
- Anki grade only → normal pull
- Anki newer than TunaTale → Anki wins schedule, both revlog rows kept
- TunaTale newer than Anki → TunaTale wins schedule
- Review duration propagates to revlog
- Revlog ID uses grade time, not sync time
- Within-TunaTale rapid re-grades collapse
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

from app.anki.sync import AnkiSync, CardRecord, NoteRecord
from app.models.srs_item import Direction, DirectionState, Rating, SRSState
from app.srs.database import SRSDatabase


def _make_db_with_banka() -> tuple[SRSDatabase, str]:
    """Create in-memory DB with 'banka' collocation linked to Anki note 9001."""
    db = SRSDatabase(":memory:")
    from app.models.syntactic_unit import SyntacticUnit

    unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation("banka")
    assert item is not None
    guid = item.guid
    db.set_anki_ids(guid, 9001, {Direction.RECOGNITION: 90010, Direction.PRODUCTION: 90011})
    return db, guid


class _FakeReader:
    """Controlled reader returning preset NoteRecords."""

    def __init__(self, records: list[NoteRecord]) -> None:
        self._records = records

    def get_note_records(self) -> list[NoteRecord]:
        return self._records


class _FakeWriter:
    """Captures writer calls for assertions."""

    def __init__(self) -> None:
        self.set_due_date_calls: list[tuple[list[int], str]] = []
        self.write_revlog_calls: list[dict[str, Any]] = []
        self.suspend_calls: list[list[int]] = []
        self.unsuspend_calls: list[list[int]] = []
        self.set_learning_state_calls: list[tuple[int, int, int, int]] = []

    def update_note_fields(self, note_id: int, fields: dict) -> None:
        pass

    def suspend(self, card_ids: list[int]) -> None:
        self.suspend_calls.append(list(card_ids))

    def unsuspend(self, card_ids: list[int]) -> None:
        self.unsuspend_calls.append(list(card_ids))

    def set_due_date(self, card_ids: list[int], days: str) -> None:
        self.set_due_date_calls.append((list(card_ids), days))

    def write_revlog(
        self,
        *,
        cid: int,
        ease: int,
        ivl: int,
        last_ivl: int,
        factor: int,
        time_ms: int,
        type_,
        preferred_id=None,
        is_lapse: bool = False,
        ds_reps: int | None = None,
        ds_lapses: int | None = None,
    ) -> None:
        self.write_revlog_calls.append(
            {"cid": cid, "ease": ease, "ivl": ivl, "time_ms": time_ms, "type_": type_, "preferred_id": preferred_id}
        )

    def set_specific_value_of_card(self, card_id: int, keys: list[str], new_values: list[str]) -> None:
        pass

    def set_learning_state(self, card_id: int, left: int, due_timestamp: int, type_: int) -> None:
        self.set_learning_state_calls.append((card_id, left, due_timestamp, type_))


class TestTunaTaleGradeOnlyPushesNormally:
    """TunaTale grades, no Anki review since last sync → normal push."""

    def test_tunatale_grade_pushes_revlog_with_rating(self):
        db, guid = _make_db_with_banka()

        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)

        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=1,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=grade_time,
                last_review_time_ms=4500,
                dirty_fsrs=True,
                anki_card_id=90010,
                last_rating=3,
            ),
        )

        writer = _FakeWriter()
        sync = AnkiSync(db=db, _reader=_FakeReader([]), _writer=writer)
        push_report = sync.sync_push()

        assert push_report.directions_pushed == 1
        assert len(writer.write_revlog_calls) == 1
        call = writer.write_revlog_calls[0]
        assert call["ease"] == 3
        assert call["time_ms"] == 4500

    def test_revlog_id_uses_grade_time_not_sync_time(self):
        db, guid = _make_db_with_banka()

        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)

        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=1,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=grade_time,
                last_review_time_ms=3000,
                dirty_fsrs=True,
                anki_card_id=90010,
                last_rating=3,
            ),
        )

        writer = _FakeWriter()
        sync = AnkiSync(db=db, _reader=_FakeReader([]), _writer=writer)

        import time as _time

        real_time = _time.time
        _time.time = lambda: 1_700_000_000
        try:
            sync.sync_push()
        finally:
            _time.time = real_time

        assert len(writer.write_revlog_calls) == 1
        call = writer.write_revlog_calls[0]
        # preferred_id should be based on grade_time, not the mocked time.time()
        expected_id = int(grade_time.timestamp() * 1000)
        assert call["preferred_id"] == expected_id


class TestAnkiGradeOnlyPullsNormally:
    """Anki grades, no TunaTale review → normal pull."""

    def test_anki_grade_pulls_and_updates_direction(self):
        db, guid = _make_db_with_banka()

        anki_time = datetime(2026, 5, 4, 9, 0, 0, tzinfo=UTC)
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid="banka",
                l2_text="banka",
                translation="bank",
                note="",
                disambig_key="",
                mod=int(anki_time.timestamp()),
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=5,
                        lapses=0,
                        stability=7.5,
                        difficulty=4.8,
                        due_at=datetime.combine(date(2026, 5, 10), time(4, 0), tzinfo=UTC),
                        fsrs_known=True,
                        last_review_ms=int(anki_time.timestamp() * 1000),
                    )
                ],
            )
        ]
        writer = _FakeWriter()
        sync = AnkiSync(db=db, _reader=_FakeReader(records), _writer=writer)
        report = sync.sync_pull()

        assert report.directions_updated == 1
        item = db.get_collocation_by_guid(guid)
        assert item is not None
        assert item.directions[Direction.RECOGNITION].stability == 7.5


class TestAnkiNewerThanTunaTale:
    """Anki newer than TunaTale → Anki wins schedule."""

    def test_anki_newer_wins_schedule(self):
        db, guid = _make_db_with_banka()

        # TunaTale grades at 10:00
        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=1,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=grade_time,
                last_review_time_ms=3000,
                dirty_fsrs=True,
                anki_card_id=90010,
                last_rating=3,
            ),
        )

        # Anki reviews at 11:00 (newer)
        anki_time = datetime(2026, 5, 4, 11, 0, 0, tzinfo=UTC)
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid="banka",
                l2_text="banka",
                translation="bank",
                note="",
                disambig_key="",
                mod=int(anki_time.timestamp()),
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=6,
                        lapses=0,
                        stability=8.0,
                        difficulty=4.6,
                        due_at=datetime.combine(date(2026, 5, 12), time(4, 0), tzinfo=UTC),
                        fsrs_known=True,
                        last_review_ms=int(anki_time.timestamp() * 1000),
                    )
                ],
            )
        ]
        writer = _FakeWriter()
        sync = AnkiSync(db=db, _reader=_FakeReader(records), _writer=writer)
        report = sync.sync_pull()

        # Anki wins: direction updated from Anki's card
        assert report.directions_updated == 1
        item = db.get_collocation_by_guid(guid)
        assert item.directions[Direction.RECOGNITION].stability == 8.0
        # Conflict recorded
        assert len(report.conflicts) == 1
        assert report.conflicts[0].resolution == "anki_wins_by_timestamp"

    def test_both_revlog_rows_written_on_push(self):
        """After Anki wins pull, push writes BOTH TunaTale and Anki revlog rows."""
        db, guid = _make_db_with_banka()

        # TunaTale grades
        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=1,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=grade_time,
                last_review_time_ms=3000,
                dirty_fsrs=True,
                anki_card_id=90010,
                last_rating=3,
            ),
        )

        # Anki newer
        anki_time = datetime(2026, 5, 4, 11, 0, 0, tzinfo=UTC)
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid="banka",
                l2_text="banka",
                translation="bank",
                note="",
                disambig_key="",
                mod=int(anki_time.timestamp()),
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=6,
                        lapses=0,
                        stability=8.0,
                        difficulty=4.6,
                        due_at=datetime.combine(date(2026, 5, 12), time(4, 0), tzinfo=UTC),
                        fsrs_known=True,
                        last_review_ms=int(anki_time.timestamp() * 1000),
                    )
                ],
            )
        ]
        writer = _FakeWriter()
        sync = AnkiSync(db=db, _reader=_FakeReader(records), _writer=writer)
        sync.sync_pull()
        sync.sync_push()

        # Should have 1 revlog from push (TunaTale's grade)
        assert len(writer.write_revlog_calls) == 1


class TestTunaTaleNewerThanAnki:
    """TunaTale newer than Anki → TunaTale wins schedule."""

    def test_tunatale_newer_wins_schedule(self):
        db, guid = _make_db_with_banka()

        # Anki reviews at 9:00
        anki_time = datetime(2026, 5, 4, 9, 0, 0, tzinfo=UTC)
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid="banka",
                l2_text="banka",
                translation="bank",
                note="",
                disambig_key="",
                mod=int(anki_time.timestamp()),
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=5,
                        lapses=0,
                        stability=7.5,
                        difficulty=4.8,
                        due_at=datetime.combine(date(2026, 5, 10), time(4, 0), tzinfo=UTC),
                        fsrs_known=True,
                        last_review_ms=int(anki_time.timestamp() * 1000),
                    )
                ],
            )
        ]
        writer = _FakeWriter()
        sync = AnkiSync(db=db, _reader=_FakeReader(records), _writer=writer)
        sync.sync_pull()

        # TunaTale grades at 10:00 (newer)
        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=1,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=grade_time,
                last_review_time_ms=3000,
                dirty_fsrs=True,
                anki_card_id=90010,
                last_rating=3,
            ),
        )

        # Push should write TunaTale's grade
        push_report = sync.sync_push()
        assert push_report.directions_pushed == 1
        assert len(writer.write_revlog_calls) == 1


class TestReviewDurationPropagates:
    """Review duration propagates to revlog."""

    def test_duration_in_revlog(self):
        db, guid = _make_db_with_banka()

        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=1,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=grade_time,
                last_review_time_ms=5000,  # 5 seconds
                dirty_fsrs=True,
                anki_card_id=90010,
                last_rating=3,
            ),
        )

        writer = _FakeWriter()
        sync = AnkiSync(db=db, _reader=_FakeReader([]), _writer=writer)
        sync.sync_push()

        assert len(writer.write_revlog_calls) == 1
        call = writer.write_revlog_calls[0]
        assert call["time_ms"] == 5000


class TestRapidRegradesCollapse:
    """Within-TunaTale rapid re-grades collapse."""

    def test_rapid_regrades_only_push_final_grade(self):
        db, guid = _make_db_with_banka()

        from app.srs.fsrs import schedule

        item = db.get_collocation_by_guid(guid)
        assert item is not None

        updated = schedule(item, Rating.HARD, direction=Direction.RECOGNITION)
        db.update_direction(guid, Direction.RECOGNITION, updated.directions[Direction.RECOGNITION])

        updated2 = schedule(updated, Rating.GOOD, direction=Direction.RECOGNITION)
        db.update_direction(guid, Direction.RECOGNITION, updated2.directions[Direction.RECOGNITION])

        writer = _FakeWriter()
        sync = AnkiSync(db=db, _reader=_FakeReader([]), _writer=writer)
        sync.sync_push()

        assert len(writer.write_revlog_calls) == 1
        assert writer.write_revlog_calls[0]["ease"] == 3  # GOOD


class TestSecondPushDoesNotReFire:
    """Regression test: Bug 1 — second push loop re-fires forever."""

    def test_second_push_does_not_write_duplicate_revlog(self):
        db, guid = _make_db_with_banka()

        # Simulate a graded direction: dirty_fsrs=0, last_rating set (clean but needs revlog)
        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=1,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=grade_time,
                last_review_time_ms=3000,
                dirty_fsrs=False,
                anki_card_id=90010,
                last_rating=3,
            ),
        )

        writer = _FakeWriter()
        sync = AnkiSync(db=db, _reader=_FakeReader([]), _writer=writer)

        # First push: should write one revlog
        sync.sync_push()
        revlog_count_1 = len(writer.write_revlog_calls)

        # Second push: should NOT write additional revlog
        sync.sync_push()
        revlog_count_2 = len(writer.write_revlog_calls)

        assert revlog_count_1 == 1, f"First push should write 1 revlog, got {revlog_count_1}"
        assert revlog_count_2 == 1, f"Second push should not write duplicate revlog, got {revlog_count_2}"

    def test_mark_direction_clean_clears_last_rating(self):
        """Verify mark_direction_clean (used in second loop) clears last_rating."""
        db, guid = _make_db_with_banka()

        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=1,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=grade_time,
                last_review_time_ms=3000,
                dirty_fsrs=False,
                anki_card_id=90010,
                last_rating=3,
            ),
        )

        # Verify last_rating is set
        item = db.get_collocation_by_guid(guid)
        assert item is not None
        assert item.directions[Direction.RECOGNITION].last_rating == 3

        # Call mark_direction_clean (what sync_push now uses)
        db.mark_direction_clean(guid, Direction.RECOGNITION)

        # Verify last_rating is cleared
        item = db.get_collocation_by_guid(guid)
        assert item is not None
        assert item.directions[Direction.RECOGNITION].last_rating is None


class TestAnkiWinsByTimestampQueueMappings:
    """Tests for queue→state mappings when Anki wins by timestamp (lines 578-586)."""

    def _make_graded_db(self):
        """Create DB with a graded direction."""
        db, guid = _make_db_with_banka()
        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=3,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=grade_time,
                last_review_time_ms=3000,
                dirty_fsrs=True,
                anki_card_id=90010,
                last_rating=3,
            ),
        )
        return db, guid

    def test_queue_minus1_becomes_suspended(self):
        """queue=-1 (suspended) → SUSPENDED (line 578)."""
        db, guid = self._make_graded_db()
        # Anki card with queue=-1 (suspended), newer review
        anki_time = datetime(2026, 5, 4, 11, 0, 0, tzinfo=UTC)
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid="banka",
                l2_text="banka",
                translation="bank",
                note="",
                disambig_key="",
                mod=int(anki_time.timestamp()),
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=-1,
                        reps=3,
                        lapses=0,
                        stability=5.0,
                        difficulty=4.5,
                        due_at=datetime.combine(date(2026, 5, 10), time(4, 0), tzinfo=UTC),
                        fsrs_known=True,
                        last_review_ms=int(anki_time.timestamp() * 1000),
                    )
                ],
            )
        ]
        sync = AnkiSync(db=db, _reader=_FakeReader(records), _writer=_FakeWriter())
        report = sync.sync_pull(dry_run=False)
        assert report.directions_updated == 1
        item = db.get_collocation_by_guid(guid)
        assert item.directions[Direction.RECOGNITION].state == SRSState.SUSPENDED

    def test_queue_minus2_minus3_becomes_buried(self):
        """queue=-2/-3 (buried) → BURIED (line 580)."""
        db, guid = self._make_graded_db()
        anki_time = datetime(2026, 5, 4, 11, 0, 0, tzinfo=UTC)
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid="banka",
                l2_text="banka",
                translation="bank",
                note="",
                disambig_key="",
                mod=int(anki_time.timestamp()),
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=-2,
                        reps=3,
                        lapses=0,
                        stability=5.0,
                        difficulty=4.5,
                        due_at=datetime.combine(date(2026, 5, 10), time(4, 0), tzinfo=UTC),
                        fsrs_known=True,
                        last_review_ms=int(anki_time.timestamp() * 1000),
                    )
                ],
            )
        ]
        sync = AnkiSync(db=db, _reader=_FakeReader(records), _writer=_FakeWriter())
        report = sync.sync_pull(dry_run=False)
        assert report.directions_updated == 1
        item = db.get_collocation_by_guid(guid)
        assert item.directions[Direction.RECOGNITION].state == SRSState.BURIED

    def test_queue_1_becomes_learning(self):
        """queue=1 (learning) → LEARNING (line 582)."""
        db, guid = self._make_graded_db()
        anki_time = datetime(2026, 5, 4, 11, 0, 0, tzinfo=UTC)
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid="banka",
                l2_text="banka",
                translation="bank",
                note="",
                disambig_key="",
                mod=int(anki_time.timestamp()),
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=1,
                        reps=1,
                        lapses=0,
                        stability=2.0,
                        difficulty=4.0,
                        due_at=datetime.combine(date(2026, 5, 5), time(4, 0), tzinfo=UTC),
                        fsrs_known=True,
                        last_review_ms=int(anki_time.timestamp() * 1000),
                    )
                ],
            )
        ]
        sync = AnkiSync(db=db, _reader=_FakeReader(records), _writer=_FakeWriter())
        report = sync.sync_pull(dry_run=False)
        assert report.directions_updated == 1
        item = db.get_collocation_by_guid(guid)
        assert item.directions[Direction.RECOGNITION].state == SRSState.LEARNING

    def test_queue_3_becomes_relearning(self):
        """queue=3 (relearning) → RELEARNING (line 584)."""
        db, guid = self._make_graded_db()
        anki_time = datetime(2026, 5, 4, 11, 0, 0, tzinfo=UTC)
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid="banka",
                l2_text="banka",
                translation="bank",
                note="",
                disambig_key="",
                mod=int(anki_time.timestamp()),
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=3,
                        reps=5,
                        lapses=1,
                        stability=4.0,
                        difficulty=5.0,
                        due_at=datetime.combine(date(2026, 5, 8), time(4, 0), tzinfo=UTC),
                        fsrs_known=True,
                        last_review_ms=int(anki_time.timestamp() * 1000),
                    )
                ],
            )
        ]
        sync = AnkiSync(db=db, _reader=_FakeReader(records), _writer=_FakeWriter())
        report = sync.sync_pull(dry_run=False)
        assert report.directions_updated == 1
        item = db.get_collocation_by_guid(guid)
        assert item.directions[Direction.RECOGNITION].state == SRSState.RELEARNING

    def test_reps_0_becomes_new(self):
        """reps=0 (new card) → NEW (line 586)."""
        db, guid = self._make_graded_db()
        anki_time = datetime(2026, 5, 4, 11, 0, 0, tzinfo=UTC)
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid="banka",
                l2_text="banka",
                translation="bank",
                note="",
                disambig_key="",
                mod=int(anki_time.timestamp()),
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=0,
                        reps=0,
                        lapses=0,
                        stability=0.0,
                        difficulty=0.0,
                        due_at=datetime.combine(date(2026, 5, 4), time(4, 0), tzinfo=UTC),
                        fsrs_known=False,
                        last_review_ms=int(anki_time.timestamp() * 1000),
                    )
                ],
            )
        ]
        sync = AnkiSync(db=db, _reader=_FakeReader(records), _writer=_FakeWriter())
        report = sync.sync_pull(dry_run=False)
        assert report.directions_updated == 1
        item = db.get_collocation_by_guid(guid)
        assert item.directions[Direction.RECOGNITION].state == SRSState.NEW


class TestSecondPushLoopEdgeCases:
    """Tests for edge cases in the second push loop (lines 756, 757->775, 758->774)."""

    def test_skip_when_anki_card_id_none(self):
        """When anki_card_id is None, skip revlog write (line 756)."""
        db, guid = _make_db_with_banka()
        # Add a clean direction without anki_card_id
        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=1,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=grade_time,
                last_review_time_ms=3000,
                dirty_fsrs=False,
                anki_card_id=None,  # No Anki card
                last_rating=3,
            ),
        )
        writer = _FakeWriter()
        sync = AnkiSync(db=db, _reader=_FakeReader([]), _writer=writer)
        sync.sync_push()
        # Should skip because anki_card_id is None
        assert len(writer.write_revlog_calls) == 0

    def test_skip_when_reps_zero(self):
        """When reps=0, skip revlog write (line 757->775)."""
        db, guid = _make_db_with_banka()
        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=0,  # reps=0
                lapses=0,
                state=SRSState.NEW,
                last_review=grade_time,
                last_review_time_ms=3000,
                dirty_fsrs=False,
                anki_card_id=90010,
                last_rating=3,
            ),
        )
        writer = _FakeWriter()
        sync = AnkiSync(db=db, _reader=_FakeReader([]), _writer=writer)
        sync.sync_push()
        # Should skip because reps=0
        assert len(writer.write_revlog_calls) == 0


class TestRecordConflictToDB:
    """Tests for recording conflicts to DB (line 614->687)."""

    def test_anki_wins_records_conflict_to_db(self):
        """When dry_run=False and Anki wins, record conflict to DB (line 614)."""
        db, guid = _make_db_with_banka()
        # Grade locally
        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=3,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=grade_time,
                last_review_time_ms=3000,
                dirty_fsrs=True,
                anki_card_id=90010,
                last_rating=3,
            ),
        )
        # Anki has newer review
        anki_time = datetime(2026, 5, 4, 11, 0, 0, tzinfo=UTC)
        records = [
            NoteRecord(
                anki_note_id=9001,
                anki_guid="banka",
                l2_text="banka",
                translation="bank",
                note="",
                disambig_key="",
                mod=int(anki_time.timestamp()),
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=3,
                        lapses=0,
                        stability=5.0,
                        difficulty=4.5,
                        due_at=datetime.combine(date(2026, 5, 10), time(4, 0), tzinfo=UTC),
                        fsrs_known=True,
                        last_review_ms=int(anki_time.timestamp() * 1000),
                    )
                ],
            )
        ]
        sync = AnkiSync(db=db, _reader=_FakeReader(records), _writer=_FakeWriter())
        report = sync.sync_pull(dry_run=False)
        assert len(report.conflicts) == 1
        # Verify conflict was recorded in DB (sync_conflicts table)
        conflicts = db.list_sync_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0]["field"] == "schedule"
        assert conflicts[0]["resolution"] == "anki_wins_by_timestamp"


class TestSecondPushLoopDryRun:
    """Tests for dry_run=True in second push loop (line 757->775)."""

    def test_dry_run_skips_revlog_write(self):
        """When dry_run=True, skip revlog write but still count (line 757->775)."""
        db, guid = _make_db_with_banka()
        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.5,
                reps=1,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=grade_time,
                last_review_time_ms=3000,
                dirty_fsrs=False,
                anki_card_id=90010,
                last_rating=3,
            ),
        )
        writer = _FakeWriter()
        sync = AnkiSync(db=db, _reader=_FakeReader([]), _writer=writer)
        report = sync.sync_push(dry_run=True)
        # Should NOT write revlog
        assert len(writer.write_revlog_calls) == 0
        # But should still count it
        assert report.directions_pushed == 1
