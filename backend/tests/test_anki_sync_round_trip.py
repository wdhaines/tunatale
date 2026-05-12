"""Regression tests: local grades survive pull+push round-trip without data loss.

Covers the bug where sync_pull was overwriting dirty local FSRS state with
stale Anki values and clearing dirty_fsrs, causing sync_push to have nothing
to flush — grades reviewed in TunaTale were silently discarded.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.anki.sync import AnkiSync, CardRecord, NoteRecord
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


def _make_db_with_banka() -> tuple[SRSDatabase, str, int]:
    """Create in-memory DB with 'banka' linked to Anki IDs. Returns (db, guid, row_id)."""
    db = SRSDatabase(":memory:")
    unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit)
    item = db.get_collocation("banka")
    assert item is not None
    guid = item.guid
    rows, _ = db.list_collocations()
    row_id = rows[0][0]
    db.set_anki_ids(guid, 9001, {Direction.RECOGNITION: 90010, Direction.PRODUCTION: 90011})
    return db, guid, row_id


class _FakeReader:
    def __init__(self, records: list[NoteRecord]) -> None:
        self._records = records

    def get_note_records(self) -> list[NoteRecord]:
        return self._records


class _FakeWriter:
    """Captures writer calls for assertions."""

    def __init__(self) -> None:
        self.set_due_date_calls: list[tuple[list[int], str]] = []
        self.write_revlog_calls: list[int] = []
        self.suspend_calls: list[list[int]] = []
        self.set_learning_state_calls: list[tuple[int, int, int]] = []

    def update_note_fields(self, note_id: int, fields: dict) -> None:
        pass

    def suspend(self, card_ids: list[int]) -> None:
        self.suspend_calls.append(list(card_ids))

    def unsuspend(self, card_ids: list[int]) -> None:
        pass

    def set_due_date(self, card_ids: list[int], days: str) -> None:
        self.set_due_date_calls.append((list(card_ids), days))

    def set_learning_state(self, card_id: int, left: int, due_at: int, *, type_: int = 1) -> None:
        self.set_learning_state_calls.append((card_id, left, due_at))

    def write_revlog(
        self, *, cid: int, ease: int, ivl: int, last_ivl: int, factor: int, time_ms: int, type_, preferred_id=None
    ) -> None:
        self.write_revlog_calls.append(cid)

    def set_specific_value_of_card(self, card_id: int, keys: list, new_values: list) -> None:
        pass

    def get_current_card_state(self, card_id: int) -> dict | None:
        return None


def test_pull_then_push_after_local_grade_flushes_review_to_anki():
    """Regression: grading in TunaTale then syncing must push grade to Anki.

    The bug: sync_pull would overwrite local FSRS state with Anki's stale values
    and clear dirty_fsrs, so sync_push had nothing to send.
    """
    db, guid, _ = _make_db_with_banka()

    # Simulate TunaTale grade: user reviewed and got due_date=today+5
    due_after_grade = date.today() + timedelta(days=5)
    db.update_direction(
        guid,
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            due_date=due_after_grade,
            stability=5.0,
            difficulty=4.5,
            reps=1,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=90010,
            last_rating=3,
        ),
    )

    # Anki still has stale data: queue=2, reps=0, due=today
    stale_anki_records = [
        NoteRecord(
            anki_note_id=9001,
            anki_guid=guid,
            l2_text="banka",
            translation="bank",
            disambig_key="",
            mod=0,
            cards=[
                CardRecord(
                    anki_card_id=90010,
                    ord=0,
                    queue=2,
                    reps=0,
                    lapses=0,
                    stability=0.0,
                    difficulty=0.0,
                    due_date=date.today(),
                    fsrs_known=True,
                ),
            ],
        )
    ]

    writer = _FakeWriter()
    sync = AnkiSync(db=db, _reader=_FakeReader(stale_anki_records), _writer=writer)

    pull_report = sync.sync_pull()
    sync.sync_push()

    # Pull must NOT log a conflict — dirty local data is queued work, not a divergence
    assert pull_report.conflicts == [], f"unexpected conflicts: {pull_report.conflicts}"

    # Push must have flushed the graded due_date to Anki
    assert len(writer.set_due_date_calls) == 1, f"expected 1 set_due_date call, got {writer.set_due_date_calls}"
    pushed_cids, pushed_days = writer.set_due_date_calls[0]
    assert 90010 in pushed_cids
    assert pushed_days == "5"

    # Local state must not have been reverted by pull
    after = db.get_collocation_by_guid(guid)
    rec = after.directions[Direction.RECOGNITION]
    assert rec.due_date == due_after_grade, f"due_date reverted: got {rec.due_date}"
    assert rec.reps == 1, f"reps reverted: got {rec.reps}"

    # dirty_fsrs cleared by push (grade was sent)
    assert rec.dirty_fsrs is False


def test_dirty_new_card_preserves_review_state_through_pull():
    """Dirty card whose local state=REVIEW must not be downgraded to NEW by pull.

    Anki may send reps=0 for a card TunaTale has already graded (race).
    Pull must not derive new_state=NEW from reps=0 when dirty_fsrs=True.
    """
    db, guid, _ = _make_db_with_banka()

    due_after_grade = date.today() + timedelta(days=3)
    db.update_direction(
        guid,
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            due_date=due_after_grade,
            stability=3.0,
            difficulty=5.0,
            reps=1,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=90010,
        ),
    )

    # Anki sends reps=0 (card was new when Anki last saw it)
    stale_anki_records = [
        NoteRecord(
            anki_note_id=9001,
            anki_guid=guid,
            l2_text="banka",
            translation="bank",
            disambig_key="",
            mod=0,
            cards=[
                CardRecord(
                    anki_card_id=90010,
                    ord=0,
                    queue=2,
                    reps=0,
                    lapses=0,
                    stability=0.0,
                    difficulty=0.0,
                    due_date=date.today(),
                    fsrs_known=True,
                ),
            ],
        )
    ]

    sync = AnkiSync(db=db, _reader=_FakeReader(stale_anki_records), _writer=_FakeWriter())
    pull_report = sync.sync_pull()

    after = db.get_collocation_by_guid(guid)
    rec = after.directions[Direction.RECOGNITION]
    # State must not be downgraded to NEW
    assert rec.state == SRSState.REVIEW, f"state downgraded: got {rec.state}"
    assert rec.dirty_fsrs is True
    assert pull_report.conflicts == []


def test_promote_to_learning_dirty_cleared_by_push():
    """promote_to_learning marks direction dirty; sync_push clears dirty_fsrs."""
    db, guid, row_id = _make_db_with_banka()
    db.promote_to_learning(row_id)

    # Dirty row appears in list_dirty
    dirty = db.list_dirty()
    assert any(d[2].state == SRSState.LEARNING and d[2].dirty_fsrs for d in dirty)

    # Push flushes: writer gets set_due_date (no left/due_at), direction cleaned
    stale = _FakeReader(
        [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=0,
                        lapses=0,
                        stability=0.0,
                        difficulty=0.0,
                        due_date=date.today(),
                        fsrs_known=True,
                    ),
                    CardRecord(
                        anki_card_id=90011,
                        ord=1,
                        queue=2,
                        reps=0,
                        lapses=0,
                        stability=0.0,
                        difficulty=0.0,
                        due_date=date.today(),
                        fsrs_known=True,
                    ),
                ],
            )
        ]
    )
    writer = _FakeWriter()
    AnkiSync(db=db, _reader=stale, _writer=writer).sync_push()
    assert len(writer.set_due_date_calls) >= 1

    # After push, dirty_fsrs cleared
    dirty = db.list_dirty()
    assert not any(d[0] == guid for d in dirty), "dirty_fsrs should be cleared after push"

    # Idempotent: second push sends nothing
    writer2 = _FakeWriter()
    AnkiSync(db=db, _reader=stale, _writer=writer2).sync_push()
    assert writer2.set_due_date_calls == []


def test_untrack_suspend_dirty_cleared_by_push():
    """untrack_collocation marks direction dirty; sync_push clears dirty_fsrs."""
    db, guid, row_id = _make_db_with_banka()
    db.untrack_collocation(row_id)

    # Dirty row appears in list_dirty as SUSPENDED
    dirty = db.list_dirty()
    assert any(d[2].state == SRSState.SUSPENDED and d[2].dirty_fsrs for d in dirty)

    # Push flushes: writer gets suspend, direction cleaned
    stale = _FakeReader(
        [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                disambig_key="",
                mod=0,
                cards=[
                    CardRecord(
                        anki_card_id=90010,
                        ord=0,
                        queue=2,
                        reps=0,
                        lapses=0,
                        stability=0.0,
                        difficulty=0.0,
                        due_date=date.today(),
                        fsrs_known=True,
                    ),
                    CardRecord(
                        anki_card_id=90011,
                        ord=1,
                        queue=2,
                        reps=0,
                        lapses=0,
                        stability=0.0,
                        difficulty=0.0,
                        due_date=date.today(),
                        fsrs_known=True,
                    ),
                ],
            )
        ]
    )
    writer = _FakeWriter()
    AnkiSync(db=db, _reader=stale, _writer=writer).sync_push()
    assert len(writer.suspend_calls) >= 1

    # After push, dirty_fsrs cleared
    dirty = db.list_dirty()
    assert not any(d[0] == guid for d in dirty), "dirty_fsrs should be cleared after push"

    # Idempotent: second push sends nothing
    writer2 = _FakeWriter()
    AnkiSync(db=db, _reader=stale, _writer=writer2).sync_push()
    assert writer2.suspend_calls == []
