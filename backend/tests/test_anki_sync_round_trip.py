"""Regression tests: local grades survive pull+push round-trip without data loss.

Covers the bug where sync_pull was overwriting dirty local FSRS state with
stale Anki values and clearing dirty_fsrs, causing sync_push to have nothing
to flush — grades reviewed in TunaTale were silently discarded.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from app.anki.sync import AnkiSync, CardRecord, NoteRecord, OfflineWriter
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

    def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
        return []


class _FakeWriter:
    """Captures writer calls for assertions."""

    def __init__(self) -> None:
        self.set_due_date_calls: list[tuple[list[int], str]] = []
        self.write_revlog_calls: list[int] = []
        self.suspend_calls: list[list[int]] = []
        self.set_learning_state_calls: list[tuple[int, int, int]] = []
        self.set_specific_value_calls: list[tuple[int, list[str], list[str]]] = []

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
        self.write_revlog_calls.append(cid)

    def set_specific_value_of_card(self, card_id: int, keys: list, new_values: list) -> None:
        self.set_specific_value_calls.append((card_id, list(keys), list(new_values)))

    def get_current_card_state(self, card_id: int) -> dict | None:
        return None

    def bury_siblings(
        self,
        *,
        graded_card_id: int,
        graded_queue: int,
        bury_new: bool = False,
        bury_reviews: bool = False,
        bury_interday_learning: bool = False,
    ) -> int:
        return 0


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
            due_at=datetime.combine(due_after_grade, time(4, 0), tzinfo=UTC),
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
            note="",
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
                    due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
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
    assert rec.due_at.date() == due_after_grade, f"due_date reverted: got {rec.due_at.date()}"
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
            due_at=datetime.combine(due_after_grade, time(4, 0), tzinfo=UTC),
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
            note="",
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
                    due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
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
                note="",
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
                        due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
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
                        due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
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
                note="",
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
                        due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
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
                        due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
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


def test_known_direction_pushes_far_future_due_date():
    """KNOWN direction pushes as a review card with due_date = max_ivl days.

    mark_known sets dirty_fsrs=1; sync_push must set_due_date with the
    far-future interval and then clear the dirty flag.
    """
    db, guid, row_id = _make_db_with_banka()

    max_ivl = 3650
    due_at = datetime.combine(date.today() + timedelta(days=max_ivl), time(4, 0), tzinfo=UTC)
    import json as _json

    db.mark_known(row_id, due_at=due_at, stability=float(max_ivl))

    # Verify dirty direction appears in list_dirty
    dirty = db.list_dirty()
    assert any(d[0] == guid and d[2].state == SRSState.KNOWN for d in dirty), (
        f"KNOWN direction should be dirty, got {[(d[0], d[2].state) for d in dirty]}"
    )

    stale = _FakeReader(
        [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                note="",
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
                        due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                        fsrs_known=True,
                    ),
                ],
            )
        ]
    )
    writer = _FakeWriter()
    AnkiSync(db=db, _reader=stale, _writer=writer).sync_push()

    # Must push the far-future interval as the due date for both directions
    assert len(writer.set_due_date_calls) == 2, (
        f"expected 2 set_due_date calls (recognition + production), got {writer.set_due_date_calls}"
    )
    for _cids, days in writer.set_due_date_calls:
        assert days == str(max_ivl), f"expected days={max_ivl}, got {days!r}"
    all_cids = [cid for _cids, _ in writer.set_due_date_calls for cid in _cids]
    assert 90010 in all_cids
    assert 90011 in all_cids

    # Must also push FSRS data (force_fsrs for KNOWN state)
    assert len(writer.set_specific_value_calls) == 2, (
        f"expected 2 set_specific_value calls, got {writer.set_specific_value_calls}"
    )
    for _cid, keys, values in writer.set_specific_value_calls:
        assert keys == ["data", "ivl", "factor"]
        data = _json.loads(values[0])
        assert data["s"] == float(max_ivl), f"expected stability={max_ivl}, got {data['s']}"

    # After push, dirty_fsrs cleared
    dirty_after = db.list_dirty()
    assert not any(d[0] == guid for d in dirty_after), "dirty_fsrs should be cleared after push"

    # Idempotent: second push sends nothing
    writer2 = _FakeWriter()
    AnkiSync(db=db, _reader=stale, _writer=writer2).sync_push()
    assert writer2.set_due_date_calls == []


def test_pull_syncs_note_field():
    """sync_pull writes the note field from Anki records to the local DB."""
    db, guid, _ = _make_db_with_banka()

    reader = _FakeReader(
        [
            NoteRecord(
                anki_note_id=9001,
                anki_guid=guid,
                l2_text="banka",
                translation="bank",
                note="pronunciation info",
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
                        due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                        fsrs_known=True,
                    ),
                ],
            ),
        ]
    )
    sync = AnkiSync(db=db, _reader=reader, _writer=_FakeWriter())
    sync.sync_pull()
    after = db.get_collocation_by_guid(guid)
    assert after.syntactic_unit.note == "pronunciation info"


def test_sync_pull_state_not_clobbered_by_media_refresh():
    """TT state='new' stays as REVIEW after sync_pull + full pipeline.

    Regression: import_seed's stale parse_fsrs_data (reps==0 → NEW) was
    clobbering sync_pull's correct REVIEW for (queue=2, reps=0) cards.
    Phase 1 fixed parse_fsrs_data; Phase 2 replaced import_seed in the
    sync endpoint with refresh_media_for_deck (media-only, no direction
    writes). Verify state survives the full flow.
    """
    db, guid, _ = _make_db_with_banka()

    # Set TT state='new' to match old (buggy) import state
    db.update_direction(
        guid,
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=1.0,
            difficulty=5.0,
            reps=0,
            lapses=0,
            state=SRSState.NEW,
            anki_card_id=90010,
        ),
    )

    # Anki side has queue=2, reps=0 (Forget Card / manual reschedule)
    records = [
        NoteRecord(
            anki_note_id=9001,
            anki_guid=guid,
            l2_text="banka",
            translation="bank",
            note="",
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
                    due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                    anki_due=10,
                    anki_card_mod=0,
                ),
            ],
        )
    ]

    reader = _FakeReader(records)
    writer = _FakeWriter()
    sync = AnkiSync(db=db, _reader=reader, _writer=writer)
    sync.sync_pull()

    after = db.get_collocation_by_guid(guid)
    rec = after.directions[Direction.RECOGNITION]
    assert rec.state == SRSState.REVIEW, f"state should be REVIEW after sync_pull, got {rec.state}"

    # Now simulate the media-refresh step that the sync pipeline runs after pull.
    # refresh_media_from_conn must not touch direction state (writes only media table).
    from pathlib import Path

    from app.anki.import_seed import refresh_media_from_conn

    anki_conn = _make_anki_conn()
    refresh_media_from_conn(
        anki_conn,
        deck_name="0. Slovene",
        anki_media_path=Path("/nonexistent"),
        media_dir=Path("/nonexistent"),
        db=db,
    )

    after2 = db.get_collocation_by_guid(guid)
    rec2 = after2.directions[Direction.RECOGNITION]
    assert rec2.state == SRSState.REVIEW, f"state clobbered after media refresh: got {rec2.state}"


def _make_anki_conn():
    """In-memory Anki collection with Slovene notetype, one note, one card."""
    import sqlite3

    from app.anki.notetype import SLOVENE_VOCAB_FIELD_NAMES, SLOVENE_VOCAB_NOTETYPE_NAME

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,
            dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT,
            decks TEXT, dconf TEXT, tags TEXT);
        CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT UNIQUE, mid INTEGER, mod INTEGER,
            usn INTEGER, tags TEXT, flds TEXT, sfld TEXT, csum INTEGER,
            flags INTEGER, data TEXT);
        CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER,
            mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER, due INTEGER,
            ivl INTEGER, factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER,
            odue INTEGER, odid INTEGER, flags INTEGER, data TEXT);
        CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER,
            ivl INTEGER, lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER);
        CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER,
            usn INTEGER, config BLOB);
        CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER,
            usn INTEGER, config BLOB, PRIMARY KEY (ntid, ord));
        CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB,
            PRIMARY KEY (ntid, ord));
        CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER,
            usn INTEGER, common BLOB);
    """)
    conn.execute("INSERT INTO col VALUES (1, 1704067200, 0, 1000, 18, 0, 0, 0, '{}', '{}', '{}', '{}', '{}')")
    conn.execute("INSERT INTO decks VALUES (12345, '0. Slovene', 0, 0, x'')")
    conn.execute(
        "INSERT INTO notetypes VALUES (1000001, ?, 0, 0, x'')",
        (SLOVENE_VOCAB_NOTETYPE_NAME,),
    )
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?, ?, x'')",
        [(1000001, i, name) for i, name in enumerate(SLOVENE_VOCAB_FIELD_NAMES)],
    )
    conn.executemany(
        "INSERT INTO templates VALUES (?, ?, ?, 0, 0, x'')",
        [(1000001, 0, "Recognition"), (1000001, 1, "Production")],
    )
    conn.execute(
        "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
        "VALUES (9001, 'test-guid', 1000001, 0, 0, '', 'banka\\x1fbank\\x1f\\x1f\\x1f', 'banka', 0, 0, '')",
    )
    conn.execute(
        "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, data) "
        "VALUES (90010, 9001, 12345, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')",
    )
    conn.commit()
    return conn


def test_known_push_via_offline_writer_writes_review_card():
    """KNOWN push through OfflineWriter lands as queue=2 review with max_ivl."""
    db = SRSDatabase(":memory:")
    unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit)
    item = db.get_collocation("banka")
    assert item is not None
    guid = item.guid
    rows, _ = db.list_collocations()
    row_id = rows[0][0]
    db.set_anki_ids(guid, 9001, {Direction.RECOGNITION: 90010})

    max_ivl = 3650
    due_at = datetime.combine(date.today() + timedelta(days=max_ivl), time(4, 0), tzinfo=UTC)
    db.mark_known(row_id, due_at=due_at, stability=float(max_ivl), direction=Direction.RECOGNITION)

    anki_conn = _make_anki_conn()
    col_crt = anki_conn.execute("SELECT crt FROM col").fetchone()[0]
    writer = OfflineWriter(anki_conn)

    class _EmptyReader:
        def get_note_records(self):
            return []

        def get_revlog_for_card(self, card_id, after_ms=0):
            return []

    AnkiSync(db=db, _reader=_EmptyReader(), _writer=writer, _anki_col_crt=col_crt).sync_push()

    card = anki_conn.execute("SELECT * FROM cards WHERE id = 90010").fetchone()
    assert card is not None, "card must exist in Anki"
    assert card["queue"] == 2, f"expected queue=2 (review), got queue={card['queue']}"
    assert card["type"] == 2, f"expected type=2 (review), got type={card['type']}"
    days_since_crt = (date.today() - date.fromtimestamp(col_crt)).days
    expected_due = days_since_crt + max_ivl
    assert card["due"] == expected_due, f"expected due={expected_due}, got due={card['due']}"
    assert card["ivl"] == max_ivl, f"expected ivl={max_ivl}, got ivl={card['ivl']}"


def test_known_push_persists_stability_to_card_data():
    """The force_fsrs path must actually write data.s (and override ivl from stability).

    Regression: OfflineWriter.set_specific_value_of_card was a no-op stub, so a KNOWN
    push never wrote the matched stability — data.s stayed None and ivl kept the
    set_due_date value. We use stability != max_ivl so the force_fsrs ivl override is
    OBSERVABLE; the prior test set them equal, which masked the stub.
    """
    import json

    db = SRSDatabase(":memory:")
    unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit)
    item = db.get_collocation("banka")
    assert item is not None
    guid = item.guid
    rows, _ = db.list_collocations()
    row_id = rows[0][0]
    db.set_anki_ids(guid, 9001, {Direction.RECOGNITION: 90010})

    max_ivl = 36500
    stability = 24319.0  # deliberately != max_ivl so force_fsrs's ivl override is visible
    due_at = datetime.combine(date.today() + timedelta(days=max_ivl), time(4, 0), tzinfo=UTC)
    db.mark_known(row_id, due_at=due_at, stability=stability, direction=Direction.RECOGNITION)

    anki_conn = _make_anki_conn()
    col_crt = anki_conn.execute("SELECT crt FROM col").fetchone()[0]
    writer = OfflineWriter(anki_conn)

    class _EmptyReader:
        def get_note_records(self):
            return []

        def get_revlog_for_card(self, card_id, after_ms=0):
            return []

    AnkiSync(db=db, _reader=_EmptyReader(), _writer=writer, _anki_col_crt=col_crt).sync_push()

    card = anki_conn.execute("SELECT * FROM cards WHERE id = 90010").fetchone()
    assert card is not None
    data = json.loads(card["data"]) if card["data"] else {}
    assert data.get("s") == stability, f"expected data.s={stability}, got data={card['data']!r}"
    # force_fsrs runs after set_due_date and overrides ivl with round(stability)
    assert card["ivl"] == round(stability), f"expected ivl={round(stability)}, got ivl={card['ivl']}"
    assert card["queue"] == 2
    assert card["usn"] == -1, "row must be marked dirty (usn=-1) for AnkiWeb"


def test_restore_known_push_force_writes_restored_stability_to_card_data():
    """Un-mark known: after restore, push force-writes the *restored* stability.

    The whole point of reversible-known: restoring stability in TT alone does
    not stick — the next take-Anki-verbatim pull would re-clobber it with Anki's
    inflated cards.data.s. restore_known sets fsrs_force_next=1, and the push
    loop's row_force_fsrs must honor it so the restored stability lands in Anki's
    cards.data (proving it survives sync, not just TT). We use a restored
    stability != the inflated value so the ivl/data override is OBSERVABLE.

    Verifies via a REAL OfflineWriter against an in-memory collection (a
    _FakeWriter would only prove the method was called — the stub bug hid behind
    exactly that false-green twice).
    """
    import json

    db = SRSDatabase(":memory:")
    unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit)
    item = db.get_collocation("banka")
    assert item is not None
    guid = item.guid
    rows, _ = db.list_collocations()
    row_id = rows[0][0]
    db.set_anki_ids(guid, 9001, {Direction.RECOGNITION: 90010})

    # Pre-known: a review card with a real schedule worth restoring. Re-fetch
    # after set_anki_ids so the DirectionState carries the linked anki_card_id
    # (update_direction would otherwise clobber it back to None).
    item = db.get_collocation("banka")
    restored_stability = 7.5
    prior_due = datetime.combine(date.today() - timedelta(days=2), time(4, 0), tzinfo=UTC)
    ds = item.directions[Direction.RECOGNITION]
    ds.state = SRSState.REVIEW
    ds.stability = restored_stability
    ds.reps = 3
    ds.due_at = prior_due
    ds.last_review = datetime.now(UTC) - timedelta(days=2)
    db.update_direction(guid, Direction.RECOGNITION, ds)

    # Mark known (inflated), then un-mark known.
    inflated = 24319.0
    known_due = datetime.combine(date.today() + timedelta(days=36500), time(4, 0), tzinfo=UTC)
    db.mark_known(row_id, due_at=known_due, stability=inflated, direction=Direction.RECOGNITION)
    db.restore_known(row_id, direction=Direction.RECOGNITION)

    anki_conn = _make_anki_conn()
    col_crt = anki_conn.execute("SELECT crt FROM col").fetchone()[0]
    writer = OfflineWriter(anki_conn)

    class _EmptyReader:
        def get_note_records(self):
            return []

        def get_revlog_for_card(self, card_id, after_ms=0):
            return []

    AnkiSync(db=db, _reader=_EmptyReader(), _writer=writer, _anki_col_crt=col_crt).sync_push()

    card = anki_conn.execute("SELECT * FROM cards WHERE id = 90010").fetchone()
    assert card is not None
    data = json.loads(card["data"]) if card["data"] else {}
    assert data.get("s") == restored_stability, f"expected restored data.s={restored_stability}, got {card['data']!r}"
    assert card["ivl"] == round(restored_stability), f"expected ivl={round(restored_stability)}, got ivl={card['ivl']}"
    assert card["usn"] == -1, "row must be marked dirty (usn=-1) for AnkiWeb"

    # Force is one-shot: the direction is clean and the flag is cleared post-push.
    reloaded = db.get_collocation("banka")
    rec = reloaded.directions[Direction.RECOGNITION]
    assert rec.fsrs_force_next is False
    assert rec.dirty_fsrs is False


def test_set_specific_value_of_card_rejects_disallowed_column():
    """Guard the dynamic column interpolation: unknown columns raise, not inject."""
    import pytest

    writer = OfflineWriter(_make_anki_conn())
    with pytest.raises(ValueError, match="disallowed card column"):
        writer.set_specific_value_of_card(90010, ["nid"], ["1"])


def test_set_specific_value_of_card_empty_keys_is_noop():
    """No keys → no write (row untouched, usn unchanged)."""
    conn = _make_anki_conn()
    writer = OfflineWriter(conn)
    before = conn.execute("SELECT mod, usn FROM cards WHERE id = 90010").fetchone()
    writer.set_specific_value_of_card(90010, [], [])
    after = conn.execute("SELECT mod, usn FROM cards WHERE id = 90010").fetchone()
    assert (after["mod"], after["usn"]) == (before["mod"], before["usn"])
