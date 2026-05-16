"""Tests for missing coverage branches."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from app.anki.sync import AnkiSync, CardRecord, NoteRecord
from app.models.srs_item import Direction, DirectionState, SRSState
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


class TestConflictRecordDryRun:
    """Tests for dry_run=True in conflict recording (614->687)."""

    def test_anki_wins_dry_run_no_db_record(self):
        """When dry_run=True, conflict not recorded to DB (line 614->687)."""
        db, guid = _make_db_with_banka()
        # Grade locally
        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_date=grade_time.date(),
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
                anki_guid=guid,
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
                        due_date=date(2026, 5, 10),
                        fsrs_known=True,
                        last_review_ms=int(anki_time.timestamp() * 1000),
                    )
                ],
            )
        ]
        sync = AnkiSync(db=db, _reader=_FakeReader(records), _writer=_FakeWriter())
        report = sync.sync_pull(dry_run=True)

        # Conflict should be in report
        assert len(report.conflicts) == 1
        # But NOT recorded to DB (dry_run=True)
        conflicts = db.list_sync_conflicts()
        assert len(conflicts) == 0
