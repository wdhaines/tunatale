"""Regression tests: local grades survive pull+push round-trip without data loss.

Covers the bug where sync_pull was overwriting dirty local FSRS state with
stale Anki values and clearing dirty_fsrs, causing sync_push to have nothing
to flush — grades reviewed in TunaTale were silently discarded.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta

from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.sync import AnkiSync, CardRecord, NoteRecord, OfflineWriter
from app.srs.anki_mirror.rollover import anki_today
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
        self.update_memory_state_calls: list[tuple[int, float, float, int | None]] = []

    def update_note_fields(self, note_id: int, fields: dict) -> bool:
        return True

    def suspend(self, card_ids: list[int]) -> None:
        self.suspend_calls.append(list(card_ids))

    def unsuspend(self, card_ids: list[int]) -> None:
        pass

    def set_due_date(self, card_ids: list[int], days: str, ivl: int | None = None) -> None:
        self.set_due_date_calls.append((list(card_ids), days))

    def set_learning_state(self, card_id: int, left: int, due_at: int, *, type_: int = 1) -> None:
        self.set_learning_state_calls.append((card_id, left, due_at))

    def max_revlog_id_for_card(self, card_id: int) -> int:
        return 0

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
        reps_bump: int | None = None,
        lapses_bump: int | None = None,
    ) -> None:
        self.write_revlog_calls.append(cid)

    def set_specific_value_of_card(self, card_id: int, keys: list, new_values: list) -> None:
        self.set_specific_value_calls.append((card_id, list(keys), list(new_values)))

    def update_card_memory_state(
        self,
        card_id: int,
        *,
        stability: float,
        difficulty: float,
        last_review_secs: int | None = None,
        desired_retention: float | None = None,
    ) -> None:
        self.update_memory_state_calls.append((card_id, stability, difficulty, last_review_secs))

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
                        direction=Direction.RECOGNITION,
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
                        direction=Direction.PRODUCTION,
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
                        direction=Direction.RECOGNITION,
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
                        direction=Direction.PRODUCTION,
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
    due_at = datetime.combine(anki_today() + timedelta(days=max_ivl), time(4, 0), tzinfo=UTC)

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
                        direction=Direction.RECOGNITION,
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

    # Must also push FSRS data (force_fsrs for KNOWN state). Layer 70: the data
    # JSON moved to update_card_memory_state (merge-write); set_specific keeps
    # ivl/factor only.
    assert len(writer.set_specific_value_calls) == 2, (
        f"expected 2 set_specific_value calls, got {writer.set_specific_value_calls}"
    )
    for _cid, keys, _values in writer.set_specific_value_calls:
        assert keys == ["ivl", "factor"]
    assert len(writer.update_memory_state_calls) == 2
    for _cid, stability, _difficulty, _lrt in writer.update_memory_state_calls:
        assert stability == float(max_ivl), f"expected stability={max_ivl}, got {stability}"

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
                        direction=Direction.RECOGNITION,
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
                    direction=Direction.RECOGNITION,
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

    from app.plugins.anki_sync.import_seed import refresh_media_from_conn

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

    from app.cards.vocab_notetype import SLOVENE_VOCAB

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
        (SLOVENE_VOCAB.name,),
    )
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?, ?, x'')",
        [(1000001, i, name) for i, name in enumerate(list(SLOVENE_VOCAB.field_names))],
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
    due_at = datetime.combine(anki_today() + timedelta(days=max_ivl), time(4, 0), tzinfo=UTC)
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
    days_since_crt = (anki_today() - date.fromtimestamp(col_crt)).days
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
    due_at = datetime.combine(anki_today() + timedelta(days=max_ivl), time(4, 0), tzinfo=UTC)
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
    stability != the inflated value so the data override is OBSERVABLE.

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
    # Layer 89: the interval is last review to due date. This card was reviewed two
    # days ago and its restored due date is in the past, so it lands due today
    # with ivl 2 — neither the known interval nor round(stability).
    assert card["ivl"] == 2, f"expected ivl=2, got ivl={card['ivl']}"
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


# ── Layer 70: TT grade's FSRS memory state survives push+pull ──────────────────


def test_tt_grade_memory_state_survives_push_pull_round_trip(fake_anki_db):
    """The cid=428 loss (2026-06-10), end-to-end against a real collection file.

    A TT-native grade is pushed (scheduling + revlog + — Layer 70 — cards.data)
    and the same sync's pull must NOT revert TT's stability/difficulty/
    last_review to Anki's pre-grade values. Pre-Layer-70: push skipped
    cards.data, pull's fsrs_known branch took Anki's stale s/d/lrt verbatim,
    and the grade's FSRS effect was erased on both sides.
    """
    import json as _json
    import sqlite3 as _sqlite3

    from app.plugins.anki_sync.sync import OfflineReader

    conn = _sqlite3.connect(str(fake_anki_db))
    conn.row_factory = _sqlite3.Row  # production uses safe_open, which sets this
    try:
        nid = conn.execute("SELECT id FROM notes WHERE flds LIKE 'banka%'").fetchone()[0]
        cids = [r[0] for r in conn.execute("SELECT id FROM cards WHERE nid = ? ORDER BY ord", (nid,))]
        rec_cid, prod_cid = cids[0], cids[1]

        # Anki's memory state predates the TT grade by 9 days (graded in Anki then).
        stale_lrt = int((datetime.now(UTC) - timedelta(days=9)).timestamp())
        conn.execute(
            "UPDATE cards SET data = ?, queue = 2, reps = 10, ivl = 10 WHERE id = ?",
            (
                _json.dumps({"pos": 872, "s": 8.2442, "d": 8.385, "dr": 0.86, "decay": 0.5, "lrt": stale_lrt}),
                rec_cid,
            ),
        )
        conn.commit()

        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
        db.add_collocation(unit)
        guid = db.get_collocation("banka").guid
        db.set_anki_ids(guid, nid, {Direction.RECOGNITION: rec_cid, Direction.PRODUCTION: prod_cid})

        # TT-native grade two hours ago: lapse arc resolved to REVIEW at s=18.2671.
        graded_at = datetime.now(UTC) - timedelta(hours=2)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=graded_at + timedelta(days=27),
                stability=18.2671,
                difficulty=8.883,
                reps=11,
                lapses=2,
                state=SRSState.REVIEW,
                dirty_fsrs=True,
                anki_card_id=rec_cid,
                last_review=graded_at,
                last_review_time_ms=int(graded_at.timestamp() * 1000),
                last_rating=3,
            ),
        )

        sync = AnkiSync(
            db=db, _reader=OfflineReader(conn, "0. Slovene", language_code="sl"), _writer=OfflineWriter(conn)
        )
        sync.sync_push()
        sync.sync_pull()

        # TT side: the grade survived the round trip.
        after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert after.stability == 18.2671, f"TT grade reverted to Anki's stale stability: {after.stability}"
        assert after.difficulty == 8.883
        assert after.last_review == graded_at

        # Anki side: cards.data now carries the grade's memory state, merged
        # (pos/decay/dr preserved — the force-path used to drop them).
        data = _json.loads(conn.execute("SELECT data FROM cards WHERE id = ?", (rec_cid,)).fetchone()[0])
        assert data["s"] == 18.2671
        assert data["d"] == 8.883
        assert data["lrt"] == int(graded_at.timestamp())
        assert data["pos"] == 872
        assert data["decay"] == 0.5
        assert data["dr"] == 0.86
    finally:
        conn.close()


# ── Suspending a never-reviewed card (tunatale-jk64) ──────────────────────────
#
# Real collection, real reader, real writer, push then pull as run_full_sync
# orders them. The fake-writer tests in test_anki_sync_push.py pinned each call
# and never held a card that was new AND suspended, which is where both of
# these lived.

_NO_DECK = "0. 6000 Most Frequent Norwegian Words [Part 1]"
_NEW_CARD = 30020  # løfte (noun): type 0, never reviewed
_NEW_NOTE = 3002
_POSITION = 4242  # its place in the new queue; no day number comes near it
_SCHEDULE = "type, queue, due, ivl, factor, reps, lapses, left, odue, odid, data"


def _norwegian_deck_with_a_new_card(tmp_path, *, queue: int, reviewed_due: int | None = None):
    """(db, conn, row_id) for a TT deck seeded from a collection holding one new card.

    *reviewed_due* moves the fixture's reviewed card (30010, due on col-day 10 and
    so long overdue) to that col-day before TT is seeded from the collection.
    """
    import sqlite3

    from app.plugins.anki_sync.import_seed import import_seed
    from tests.conftest import build_norwegian_anki_db

    col = build_norwegian_anki_db(tmp_path, with_homographs=True)
    setup = sqlite3.connect(str(col))
    setup.execute("UPDATE cards SET due = ?, queue = ? WHERE id = ?", (_POSITION, queue, _NEW_CARD))
    if reviewed_due is not None:
        setup.execute("UPDATE cards SET due = ? WHERE id = ?", (reviewed_due, _REVIEWED_CARD))
    setup.commit()
    setup.close()
    db_path = str(tmp_path / "tunatale_no.db")
    import_seed(
        anki_collection_path=col,
        anki_backup_dir=tmp_path / "bak",
        anki_media_path=tmp_path / "fake_media",
        deck_name=_NO_DECK,
        language_code="no",
        tunatale_db_path=db_path,
        media_dir=tmp_path / "media",
        fallback_log_path=tmp_path / "fallback.log",
    )
    db = SRSDatabase(db_path)
    guid = db.get_collocation_by_anki_note_id(_NEW_NOTE).guid
    rows, _ = db.list_collocations()
    row_id = next(rid for rid, item, *_ in rows if item.guid == guid)
    conn = sqlite3.connect(str(col))
    conn.row_factory = sqlite3.Row
    return db, conn, row_id


def _sync(db, conn) -> None:
    from app.plugins.anki_sync.sync import OfflineReader

    sync = AnkiSync(
        db=db,
        _reader=OfflineReader(conn, _NO_DECK, language_code="no"),
        _writer=OfflineWriter(conn),
        language_code="no",
    )
    sync.sync_push()
    sync.sync_pull()


def _schedule(conn) -> dict:
    return dict(conn.execute(f"SELECT {_SCHEDULE} FROM cards WHERE id = ?", (_NEW_CARD,)).fetchone())


def _tt_direction(db) -> DirectionState:
    return db.get_collocation_by_anki_note_id(_NEW_NOTE).directions[Direction.RECOGNITION]


def test_unsuspending_a_never_reviewed_card_reaches_anki(tmp_path):
    """The un-suspend was lost: Anki kept queue=-1 and the pull re-suspended TT.

    Found on live data 2026-10-01 (Cebuano ``sayal``). A dirty NEW direction with
    no reps took sync_push's reset-to-new branch, which forgets a reviewed card
    and otherwise does nothing — so a card that was new AND suspended in Anki was
    left suspended, marked clean, and read back as SUSPENDED by the same sync.
    """
    db, conn, row_id = _norwegian_deck_with_a_new_card(tmp_path, queue=-1)
    try:
        assert _tt_direction(db).state == SRSState.SUSPENDED  # the control: seeded as suspended
        before = _schedule(conn)

        db.set_suspended(row_id, False)
        _sync(db, conn)

        assert _schedule(conn) == {**before, "queue": 0}
        assert conn.execute("SELECT usn FROM cards WHERE id = ?", (_NEW_CARD,)).fetchone()[0] == -1
        after = _tt_direction(db)
        assert after.state == SRSState.NEW
        assert after.dirty_fsrs is False

        _sync(db, conn)  # and it holds: a second sync changes nothing
        assert _schedule(conn) == {**before, "queue": 0}
        assert _tt_direction(db).state == SRSState.NEW
    finally:
        conn.close()


def test_suspending_a_never_reviewed_card_keeps_its_place_in_the_new_queue(tmp_path):
    """Suspending is ``queue = -1`` and nothing else, as it is in Anki.

    The push went on to set_due_date, which for a suspended card keeps queue and
    type but still writes ``due`` and ``ivl``. On a new card ``due`` is the
    position in the new queue, so the card came back from suspension at today's
    day number with ``ivl = 1`` (``sayal``: due 4653, ivl 1 on a type-0 card).
    """
    db, conn, row_id = _norwegian_deck_with_a_new_card(tmp_path, queue=0)
    try:
        assert _tt_direction(db).state == SRSState.NEW  # the control: seeded as new
        before = _schedule(conn)
        assert (before["type"], before["due"], before["ivl"]) == (0, _POSITION, 0)

        db.set_suspended(row_id, True)
        _sync(db, conn)

        assert _schedule(conn) == {**before, "queue": -1}
        assert _tt_direction(db).state == SRSState.SUSPENDED
        assert _tt_direction(db).dirty_fsrs is False

        db.set_suspended(row_id, False)
        _sync(db, conn)

        assert _schedule(conn) == before  # the round trip is lossless
        assert _tt_direction(db).state == SRSState.NEW
    finally:
        conn.close()


# ── Suspending a REVIEWED card (tunatale-htjr) ────────────────────────────────

_REVIEWED_CARD = 30010  # være: type 2, reps 5, overdue, with its own review history
_REVIEWED_NOTE = 3001
_HISTORY = [1_750_000_000_000 + i * 86_400_000 for i in range(5)]


def _reviewed_card_with_history(tmp_path, *, reviewed_due: int | None = None):
    """(db, conn, row_id, guid) with the reviewed card's five reviews in Anki's revlog."""
    db, conn, _ = _norwegian_deck_with_a_new_card(tmp_path, queue=0, reviewed_due=reviewed_due)
    conn.executemany(
        "INSERT INTO revlog VALUES (?, ?, 0, 3, 21, 10, 2500, 4000, 1)", [(rid, _REVIEWED_CARD) for rid in _HISTORY]
    )
    conn.commit()
    guid = db.get_collocation_by_anki_note_id(_REVIEWED_NOTE).guid
    rows, _ = db.list_collocations()
    row_id = next(rid for rid, item, *_ in rows if item.guid == guid)
    return db, conn, row_id, guid


def _reviewed_schedule(conn) -> dict:
    return dict(conn.execute(f"SELECT {_SCHEDULE} FROM cards WHERE id = ?", (_REVIEWED_CARD,)).fetchone())


def _reviewed_revlog(conn) -> list[tuple]:
    return [
        tuple(r) for r in conn.execute("SELECT id, ease, type FROM revlog WHERE cid = ? ORDER BY id", (_REVIEWED_CARD,))
    ]


def _reviewed_direction(db) -> DirectionState:
    return db.get_collocation_by_anki_note_id(_REVIEWED_NOTE).directions[Direction.RECOGNITION]


def test_suspending_and_unsuspending_a_reviewed_card_changes_only_its_queue(tmp_path):
    """A suspension is not a grade: no review row, no new due date, no new interval.

    Every dirty direction with reps took the grade push. With nothing graded
    there were no tt_revlog rows to send, so the fallback invented one (ease 3,
    a "Good" nobody pressed) and bumped ``cards.reps``; set_due_date then moved
    an overdue card to today with ``ivl = 1``. Live: Cebuano ``saa`` gained a
    seventh review on 2026-10-01 by being suspended.
    """
    db, conn, row_id, _ = _reviewed_card_with_history(tmp_path)
    try:
        assert _reviewed_direction(db).state == SRSState.REVIEW  # the control: seeded as a review card
        before, history = _reviewed_schedule(conn), _reviewed_revlog(conn)
        assert (before["type"], before["reps"], len(history)) == (2, 5, 5)

        db.set_suspended(row_id, True)
        _sync(db, conn)

        assert _reviewed_schedule(conn) == {**before, "queue": -1}
        assert _reviewed_revlog(conn) == history
        assert _reviewed_direction(db).state == SRSState.SUSPENDED
        assert _reviewed_direction(db).reps == 5

        db.set_suspended(row_id, False)
        _sync(db, conn)

        assert _reviewed_schedule(conn) == before
        assert _reviewed_revlog(conn) == history
        after = _reviewed_direction(db)
        assert (after.state, after.reps, after.dirty_fsrs) == (SRSState.REVIEW, 5, False)
    finally:
        conn.close()


def test_a_grade_waiting_under_a_suspension_still_reaches_anki(tmp_path):
    """The regression guard: graded in TT, then suspended before the sync.

    ``last_rating`` is what says a grade is waiting (mark_direction_clean clears
    it), and a suspension leaves it alone, so this card still takes the whole
    grade push: the review row, the memory state, and the suspension.
    """
    db, conn, row_id, guid = _reviewed_card_with_history(tmp_path)
    try:
        history = _reviewed_revlog(conn)
        graded_at = datetime.now(UTC) - timedelta(hours=2)
        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=graded_at + timedelta(days=27),
                stability=18.2671,
                difficulty=8.883,
                reps=6,
                lapses=0,
                state=SRSState.REVIEW,
                dirty_fsrs=True,
                anki_card_id=_REVIEWED_CARD,
                last_review=graded_at,
                last_review_time_ms=int(graded_at.timestamp() * 1000),
                last_rating=3,
            ),
        )

        db.set_suspended(row_id, True)
        assert _reviewed_direction(db).last_rating == 3  # the control: the suspension kept the grade marker
        _sync(db, conn)

        assert _reviewed_revlog(conn) == [*history, (int(graded_at.timestamp() * 1000), 3, 1)]
        card = _reviewed_schedule(conn)
        assert (card["type"], card["queue"], card["reps"]) == (2, -1, 6)
        assert json.loads(card["data"])["s"] == 18.2671
        assert _reviewed_direction(db).state == SRSState.SUSPENDED
    finally:
        conn.close()


# ── A schedule change nobody graded (tunatale-htjr, Layer 88) ─────────────────
#
# Marking a reviewed card known, restoring it, or relabelling it is not a review.
# The push used to send each through the revlog fallback, which invented a "Good"
# and bumped ``cards.reps``. What Anki writes for its own Set Due Date is a
# ``type = 4`` row (pinned against the binary in test_parity_manual_revlog_row.py),
# and only when the schedule actually moves.

_COL_CRT = 1704067200  # the fixture collection's crt
_DIFFICULTY_FACTOR = 955  # manual_revlog_row's factor for the fixture card's d = 8.7
_MAX_IVL = 3650


def _manual_rows(conn) -> list[dict]:
    """The reviewed card's revlog rows after its five seeded reviews, in full."""
    rows = conn.execute(
        "SELECT id, usn, ease, ivl, lastIvl, factor, time, type FROM revlog WHERE cid = ? AND id > ? ORDER BY id",
        (_REVIEWED_CARD, _HISTORY[-1]),
    )
    return [dict(r) for r in rows]


def _due_in_days(days: int) -> int:
    from app.srs.anki_mirror.protobuf_wire import anki_today_col_day

    return anki_today_col_day(_COL_CRT) + days


def _mark_known(db, row_id) -> None:
    from app.srs.anki_mirror.rollover import due_at_rollover_utc

    db.mark_known(
        row_id, due_at=due_at_rollover_utc(anki_today() + timedelta(days=_MAX_IVL)), stability=float(_MAX_IVL)
    )


def test_marking_a_reviewed_card_known_logs_a_manual_change_not_a_review(tmp_path):
    """Known moves the due date, so Anki gets one ``type = 4`` row and no review.

    The row carries the interval before and after, ``ease = 0``, ``time = 0``, the
    moment of the push as its id, and a NON-ZERO factor: ``type = 4`` with
    ``factor = 0`` is Anki's Forget marker, which makes its FSRS code drop the
    card's history before the row.
    """
    db, conn, row_id, _ = _reviewed_card_with_history(tmp_path, reviewed_due=_due_in_days(9))
    try:
        before, history = _reviewed_schedule(conn), _reviewed_revlog(conn)
        assert (before["type"], before["reps"], before["ivl"], len(history)) == (2, 5, 21, 5)  # the control

        _mark_known(db, row_id)
        started_ms = int(datetime.now(UTC).timestamp() * 1000)
        _sync(db, conn)

        card = _reviewed_schedule(conn)
        assert (card["type"], card["queue"], card["reps"], card["lapses"]) == (2, 2, 5, 0)
        assert card["due"] - before["due"] == _MAX_IVL - 9
        assert card["ivl"] == _MAX_IVL
        (row,) = _manual_rows(conn)
        assert row["id"] >= started_ms
        assert {k: row[k] for k in ("usn", "ease", "ivl", "lastIvl", "factor", "time", "type")} == {
            "usn": -1,
            "ease": 0,
            "ivl": _MAX_IVL,
            "lastIvl": 21,
            "factor": _DIFFICULTY_FACTOR,
            "time": 0,
            "type": 4,
        }
        known = _reviewed_direction(db)
        assert (known.reps, known.dirty_fsrs) == (5, False)

        _sync(db, conn)  # and it holds: nothing left to push, nothing more logged
        assert _reviewed_schedule(conn) == card
        assert len(_manual_rows(conn)) == 1
        assert _reviewed_direction(db).reps == 5
    finally:
        conn.close()


def test_restoring_a_known_card_logs_the_way_back_and_returns_its_due_date(tmp_path):
    """Known then restore: two manual rows, the original due date, five reviews still.

    Pinned to UTC because it asserts an absolute due day. The pull reads a due
    day against the creation time's UTC date and the push writes it against the
    local one; they agree for a real collection (created at 04:00 local) and
    differ by a day for this fixture's midnight-UTC creation time read from a
    zone west of UTC.
    """
    from tests._helpers.localtz import local_timezone

    with local_timezone("UTC"):
        db, conn, row_id, _ = _reviewed_card_with_history(tmp_path, reviewed_due=_due_in_days(9))
        try:
            before, history = _reviewed_schedule(conn), _reviewed_revlog(conn)
            _mark_known(db, row_id)
            _sync(db, conn)
            assert _reviewed_schedule(conn)["ivl"] == _MAX_IVL  # the control: it really was pushed as known

            db.restore_known(row_id)
            _sync(db, conn)

            card = _reviewed_schedule(conn)
            assert (card["type"], card["queue"], card["reps"], card["lapses"]) == (2, 2, 5, 0)
            assert card["due"] == before["due"]
            assert card["ivl"] == before["ivl"]  # Layer 89: last review to due date, not round(stability)
            assert json.loads(card["data"])["s"] == 15.78
            marked, restored = _manual_rows(conn)
            assert (restored["type"], restored["ease"], restored["time"]) == (4, 0, 0)
            assert (restored["lastIvl"], restored["ivl"]) == (_MAX_IVL, card["ivl"])
            assert restored["factor"] == _DIFFICULTY_FACTOR
            assert restored["id"] > marked["id"]
            assert [r for r in _reviewed_revlog(conn) if r[2] != 4] == history  # no review was added
            after = _reviewed_direction(db)
            assert (after.state, after.reps, after.dirty_fsrs) == (SRSState.REVIEW, 5, False)
        finally:
            conn.close()


def test_marking_an_overdue_reviewed_card_known_logs_one_manual_change(tmp_path):
    """The same on a card that is long overdue, where the fallback row hurt most.

    ``lastIvl`` is the interval Anki held before the push, not one derived from
    TunaTale's stability.
    """
    db, conn, row_id, _ = _reviewed_card_with_history(tmp_path)
    try:
        before = _reviewed_schedule(conn)
        assert before["due"] < _due_in_days(0)  # the control: overdue

        _mark_known(db, row_id)
        _sync(db, conn)

        card = _reviewed_schedule(conn)
        assert (card["reps"], card["ivl"], card["due"]) == (5, _MAX_IVL, _due_in_days(_MAX_IVL))
        (row,) = _manual_rows(conn)
        assert (row["type"], row["ease"], row["lastIvl"], row["ivl"], row["factor"]) == (
            4,
            0,
            21,
            _MAX_IVL,
            _DIFFICULTY_FACTOR,
        )
        assert _reviewed_direction(db).reps == 5
    finally:
        conn.close()


def test_relabelling_a_reviewed_card_as_review_changes_nothing_in_anki(tmp_path):
    """A label with no reschedule behind it writes nothing: no row, same due, same ivl.

    The state route's "review" is label-only in TunaTale. Anki already holds the
    card as a review card with a schedule of its own, so there is nothing to send;
    the push used to move this overdue card to today with ``ivl = 1`` and log a
    review.
    """
    db, conn, row_id, _ = _reviewed_card_with_history(tmp_path)
    try:
        before, history = _reviewed_schedule(conn), _reviewed_revlog(conn)

        db.set_state_by_id(row_id, SRSState.REVIEW)
        assert _reviewed_direction(db).dirty_fsrs is True  # the control: the push has something to look at
        _sync(db, conn)

        assert _reviewed_schedule(conn) == before
        assert _reviewed_revlog(conn) == history
        after = _reviewed_direction(db)
        assert (after.state, after.reps, after.dirty_fsrs) == (SRSState.REVIEW, 5, False)
    finally:
        conn.close()


def test_promoting_a_reviewed_card_to_learning_logs_one_manual_change(tmp_path):
    """promote_to_learning reschedules to today: one manual row, and it is not a Forget.

    Its own tt_revlog row (``review_kind = 4``) is TunaTale's record of the change.
    Pushed as it stood (``factor = 0``, counted in ``reps``) it reached Anki as a
    Forget marker plus a sixth "review".
    """
    db, conn, row_id, _ = _reviewed_card_with_history(tmp_path, reviewed_due=_due_in_days(9))
    try:
        history = _reviewed_revlog(conn)

        db.promote_to_learning(row_id, Direction.RECOGNITION)
        _sync(db, conn)

        card = _reviewed_schedule(conn)
        assert (card["reps"], card["due"]) == (5, _due_in_days(0))
        (row,) = _manual_rows(conn)
        assert (row["type"], row["ease"], row["time"], row["lastIvl"], row["ivl"]) == (4, 0, 0, 21, card["ivl"])
        assert row["factor"] == _DIFFICULTY_FACTOR
        assert [r for r in _reviewed_revlog(conn) if r[2] != 4] == history
        assert _reviewed_direction(db).reps == 5
    finally:
        conn.close()


def test_marking_a_known_card_known_again_logs_nothing(tmp_path):
    """A push that leaves ``due`` and ``ivl`` where they were is not a change to log."""
    db, conn, row_id, _ = _reviewed_card_with_history(tmp_path, reviewed_due=_due_in_days(9))
    try:
        _mark_known(db, row_id)
        _sync(db, conn)
        card = _reviewed_schedule(conn)
        assert len(_manual_rows(conn)) == 1  # the control: the first mark was logged

        _mark_known(db, row_id)
        assert _reviewed_direction(db).dirty_fsrs is True  # the control: the push runs again
        _sync(db, conn)

        assert _reviewed_schedule(conn) == card
        assert len(_manual_rows(conn)) == 1
    finally:
        conn.close()


def test_a_recovered_card_gets_its_manual_rows_back_without_counting_them_as_reviews(tmp_path):
    """Recovery restores a re-minted card's history row for row; ``reps`` counts reviews.

    A manual row in that history came from Anki (its own Set Due Date or Forget)
    and goes back as it was. It is not a review, and counting it gave the fresh
    card one rep more than it had.
    """
    from app.models.srs_item import RevlogRow
    from app.plugins.anki_sync.sync import OfflineReader

    db, conn, _ = _norwegian_deck_with_a_new_card(tmp_path, queue=0)
    try:
        item = db.get_collocation_by_anki_note_id(_REVIEWED_NOTE)
        rows, _ = db.list_collocations()
        row_id = next(rid for rid, it, *_ in rows if it.guid == item.guid)
        # The freshly minted card: no history and no reps of its own yet.
        conn.execute("UPDATE cards SET reps = 0 WHERE id = ?", (_REVIEWED_CARD,))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM revlog WHERE cid = ?", (_REVIEWED_CARD,)).fetchone()[0] == 0
        for rid, ease, kind, factor in ((_HISTORY[0], 3, 1, 956), (_HISTORY[1], 0, 4, 955), (_HISTORY[2], 3, 1, 956)):
            db.append_revlog(
                RevlogRow(
                    id=rid,
                    collocation_id=row_id,
                    direction=Direction.RECOGNITION,
                    button_chosen=ease,
                    interval=21,
                    last_interval=10,
                    factor=factor,
                    taken_millis=4000 if ease else 0,
                    review_kind=kind,
                    anki_card_id=_REVIEWED_CARD,
                )
            )
        ds = item.directions[Direction.RECOGNITION]
        ds.reps = 2
        ds.dirty_fsrs = True
        db.update_direction(item.guid, Direction.RECOGNITION, ds)

        sync = AnkiSync(
            db=db,
            _reader=OfflineReader(conn, _NO_DECK, language_code="no"),
            _writer=OfflineWriter(conn),
            language_code="no",
        )
        sync._recovered_directions = {(item.guid, Direction.RECOGNITION.value)}
        sync.sync_push()

        assert _reviewed_revlog(conn) == [(_HISTORY[0], 3, 1), (_HISTORY[1], 0, 4), (_HISTORY[2], 3, 1)]
        assert _reviewed_schedule(conn)["reps"] == 2
    finally:
        conn.close()


# ── The interval a pushed due date leaves behind (tunatale-7qdt, Layer 89) ────


def test_a_grade_pushed_days_after_it_was_made_keeps_its_interval(tmp_path):
    """Graded three days ago with a ten-day interval: Anki gets ``ivl = 10``, not 7.

    The push wrote ``ivl = days from today``, which is the interval only when the
    push runs on the day of the grade. Anki's own Set Due Date counts from the
    last review (pinned in test_parity_set_due_date_interval.py).
    """
    db, conn, _, guid = _reviewed_card_with_history(tmp_path)
    try:
        graded_at = datetime.now(UTC) - timedelta(days=3)
        from app.srs.anki_mirror.rollover import due_at_rollover_utc

        db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=due_at_rollover_utc(anki_today() + timedelta(days=7)),
                stability=11.4,
                difficulty=8.6,
                reps=6,
                lapses=0,
                state=SRSState.REVIEW,
                dirty_fsrs=True,
                anki_card_id=_REVIEWED_CARD,
                last_review=graded_at,
                last_review_time_ms=4000,
                last_rating=3,
            ),
        )

        _sync(db, conn)

        card = _reviewed_schedule(conn)
        assert (card["reps"], card["due"]) == (6, _due_in_days(7))  # the control: the grade was pushed
        assert card["ivl"] == 10
    finally:
        conn.close()


# ── A reset pushed to Anki is a Forget (tunatale-qrbk, Layer 90) ──────────────


def test_resetting_a_reviewed_card_logs_ankis_forget_row(tmp_path):
    """A TunaTale reset forgets the card in Anki and logs the reset row Anki logs.

    The card was forgotten but nothing was logged, so Anki's revlog still read as
    five live reviews of a card both apps call new. ``type = 4, factor = 0`` is
    the row that tells Anki's FSRS code to ignore them.
    """
    db, conn, row_id, _ = _reviewed_card_with_history(tmp_path)
    try:
        history = _reviewed_revlog(conn)
        assert _reviewed_schedule(conn)["ivl"] == 21  # the control

        db.reset_collocation(row_id)
        _sync(db, conn)

        card = _reviewed_schedule(conn)
        assert (card["type"], card["queue"], card["ivl"], card["reps"], card["lapses"]) == (0, 0, 0, 0, 0)
        (row,) = _manual_rows(conn)
        assert {k: row[k] for k in ("usn", "ease", "ivl", "lastIvl", "factor", "time", "type")} == {
            "usn": -1,
            "ease": 0,
            "ivl": 0,
            "lastIvl": 21,
            "factor": 0,
            "time": 0,
            "type": 4,
        }
        assert [r for r in _reviewed_revlog(conn) if r[2] != 4] == history  # the old reviews stay in the log
        after = _reviewed_direction(db)
        assert (after.state, after.reps, after.dirty_fsrs) == (SRSState.NEW, 0, False)

        _sync(db, conn)  # and it holds: a card Anki already holds as new is not forgotten twice
        assert len(_manual_rows(conn)) == 1
        assert _reviewed_direction(db).state == SRSState.NEW
    finally:
        conn.close()
