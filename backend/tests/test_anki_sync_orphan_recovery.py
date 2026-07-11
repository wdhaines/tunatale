"""Self-healing sync for cards that vanished from Anki.

When AnkiWeb does a force-full-download, "Empty Cards", or the user manually
deletes a note, TT keeps stale `anki_card_id`/`anki_note_id` pointers forever.
`detect_and_reset_orphans` runs at the top of a sync, diffs TT's id sets
against the live Anki collection, resets the dead pointers, and (for graded
rows) re-arms `dirty_fsrs` so the subsequent push writes a fresh revlog and
preserves FSRS state on the recreated card.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.anki.sync import AnkiSync, OrphanThresholdExceededError
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from tests.conftest import make_card_record, make_note_record


class FakeWriter:
    """Minimal writer stub. Records calls so tests can assert on them."""

    def __init__(self) -> None:
        self.suspended: list[int] = []
        self.unsuspended: list[int] = []
        self.due_dates: list[tuple[list[int], str]] = []
        self.learning_state_calls: list[tuple] = []
        self.revlogs: list[dict] = []
        self.specific_value_calls: list[tuple] = []
        self.memory_state_calls: list[tuple] = []

    def update_note_fields(self, note_id, fields):
        pass

    def suspend(self, card_ids):
        self.suspended.extend(card_ids)

    def unsuspend(self, card_ids):
        self.unsuspended.extend(card_ids)

    def set_due_date(self, card_ids, days):
        self.due_dates.append((list(card_ids), days))

    def set_learning_state(self, card_id, left, due, type_):
        self.learning_state_calls.append((card_id, left, due, type_))

    def write_revlog(
        self,
        *,
        cid,
        ease,
        ivl,
        last_ivl,
        factor,
        time_ms,
        type_,
        preferred_id=None,
        is_lapse=False,
        ds_reps=None,
        ds_lapses=None,
        reps_bump=None,
        lapses_bump=None,
    ):
        self.revlogs.append(
            {
                "cid": cid,
                "ease": ease,
                "ivl": ivl,
                "last_ivl": last_ivl,
                "factor": factor,
                "time_ms": time_ms,
                "type_": type_,
                "preferred_id": preferred_id,
            }
        )

    def set_specific_value_of_card(self, card_id, *, keys, new_values):
        self.specific_value_calls.append((card_id, keys, new_values))

    def update_card_memory_state(
        self,
        card_id,
        *,
        stability,
        difficulty,
        last_review_secs=None,
        desired_retention=None,
    ):
        self.memory_state_calls.append((card_id, stability, difficulty, last_review_secs))

    def max_revlog_id_for_card(self, card_id: int) -> int:
        return 0

    def bury_siblings(
        self,
        *,
        graded_card_id,
        graded_queue,
        bury_new=False,
        bury_reviews=False,
        bury_interday_learning=False,
    ):
        return 0


class FakeReader:
    def __init__(self, records, grave_note_ids=None):
        self._records = records
        self._grave_note_ids = set(grave_note_ids or ())

    def get_note_records(self):
        return self._records

    def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
        return []

    def get_grave_note_ids(self) -> set[int]:
        return self._grave_note_ids


def _make_db_with_link(text: str, *, note_id: int, rec_cid: int, prod_cid: int) -> tuple[SRSDatabase, str]:
    db = SRSDatabase(":memory:")
    unit = SyntacticUnit(text=text, translation=text + "_t", word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit)
    item = db.get_collocation(text)
    db.set_anki_ids(item.guid, note_id, {Direction.RECOGNITION: rec_cid, Direction.PRODUCTION: prod_cid})
    return db, item.guid


def _add_anchor_rows(db: SRSDatabase, count: int, *, base_note: int = 5000, base_card: int = 6000) -> list:
    """Insert N fully-linked collocations that all live in the Anki side.

    Used to keep tests below the 25% orphan threshold so the reset path runs.
    Returns the matching NoteRecords for the FakeReader.
    """
    records = []
    for i in range(count):
        text = f"anchor_{i}"
        unit = SyntacticUnit(text=text, translation=text + "_t", word_count=1, difficulty=1, source="corpus")
        db.add_collocation(unit)
        item = db.get_collocation(text)
        nid = base_note + i
        rec_cid = base_card + 2 * i
        prod_cid = base_card + 2 * i + 1
        db.set_anki_ids(
            item.guid,
            nid,
            {Direction.RECOGNITION: rec_cid, Direction.PRODUCTION: prod_cid},
        )
        records.append(
            make_note_record(
                anki_note_id=nid,
                l2_text=text,
                translation=text + "_t",
                cards=[
                    make_card_record(anki_card_id=rec_cid, ord=0),
                    make_card_record(anki_card_id=prod_cid, ord=1),
                ],
            )
        )
    return records


class TestDetectAndResetOrphans:
    def test_resets_card_ids_when_card_missing_from_anki(self):
        db, _ = _make_db_with_link("zdravo", note_id=999, rec_cid=998, prod_cid=997)
        anchors = _add_anchor_rows(db, count=4)  # 8/10 live → 20% orphans, below threshold
        sync = AnkiSync(db=db, _reader=FakeReader(anchors), _writer=FakeWriter())

        sync.detect_and_reset_orphans()

        item = db.get_collocation("zdravo")
        assert item.anki_note_id is None
        assert item.directions[Direction.RECOGNITION].anki_card_id is None
        assert item.directions[Direction.PRODUCTION].anki_card_id is None
        # Anchor rows untouched.
        anchor = db.get_collocation("anchor_0")
        assert anchor.anki_note_id == 5000

    def test_keeps_links_when_anki_records_match(self):
        db, guid = _make_db_with_link("zdravo", note_id=999, rec_cid=998, prod_cid=997)
        rec = make_note_record(
            anki_note_id=999,
            l2_text="zdravo",
            translation="zdravo_t",
            cards=[
                make_card_record(anki_card_id=998, ord=0),
                make_card_record(anki_card_id=997, ord=1),
            ],
        )
        sync = AnkiSync(db=db, _reader=FakeReader([rec]), _writer=FakeWriter())

        sync.detect_and_reset_orphans()

        item = db.get_collocation("zdravo")
        assert item.anki_note_id == 999
        assert item.directions[Direction.RECOGNITION].anki_card_id == 998

    def test_aborts_when_more_than_25_percent_orphaned(self):
        db = SRSDatabase(":memory:")
        # 4 collocations, 8 directions linked → live set must include >75% (>=6).
        for i in range(4):
            unit = SyntacticUnit(text=f"w{i}", translation="t", word_count=1, difficulty=1, source="corpus")
            db.add_collocation(unit)
            item = db.get_collocation(f"w{i}")
            db.set_anki_ids(
                item.guid,
                1000 + i,
                {Direction.RECOGNITION: 2000 + 2 * i, Direction.PRODUCTION: 2001 + 2 * i},
            )
        # Live: only first collocation's 2 cards present → 6/8 = 75% orphaned.
        rec = make_note_record(
            anki_note_id=1000,
            l2_text="w0",
            cards=[
                make_card_record(anki_card_id=2000, ord=0),
                make_card_record(anki_card_id=2001, ord=1),
            ],
        )
        sync = AnkiSync(db=db, _reader=FakeReader([rec]), _writer=FakeWriter())

        with pytest.raises(OrphanThresholdExceededError):
            sync.detect_and_reset_orphans()

        # No mutation when threshold trips.
        item = db.get_collocation("w3")
        assert item.directions[Direction.RECOGNITION].anki_card_id == 2006

    def test_threshold_check_inactive_for_empty_db(self):
        """Fresh DB with no linked rows must not divide-by-zero."""
        db = SRSDatabase(":memory:")
        sync = AnkiSync(db=db, _reader=FakeReader([]), _writer=FakeWriter())
        sync.detect_and_reset_orphans()  # no exception, no-op

    def test_populates_recovered_directions_for_push_to_force_fsrs(self):
        db, guid = _make_db_with_link("zdravo", note_id=999, rec_cid=998, prod_cid=997)
        anchors = _add_anchor_rows(db, count=4)
        # Bump reps>0 on recognition so reset arms dirty_fsrs.
        item = db.get_collocation("zdravo")
        rec = item.directions[Direction.RECOGNITION]
        rec.reps = 1
        rec.state = SRSState.LEARNING
        rec.dirty_fsrs = False
        db.update_direction(guid, Direction.RECOGNITION, rec)

        sync = AnkiSync(db=db, _reader=FakeReader(anchors), _writer=FakeWriter())
        sync.detect_and_reset_orphans()

        # Recognition direction landed in the recovery set; push will force FSRS.
        assert (guid, Direction.RECOGNITION.value) in sync._recovered_directions


class TestSyncPushHonorsRecoveryFlag:
    def test_push_writes_force_fsrs_for_recovered_direction(self, monkeypatch):
        """A recovered direction with reps>0 was marked dirty in the reset.
        After sync_create_new assigns a fresh anki_card_id, sync_push must
        write force_fsrs (cards.data) regardless of the global flag.
        """
        from app.anki.sync import KNOWN_ANKI_SCHEMA_VER

        db, guid = _make_db_with_link("zdravo", note_id=999, rec_cid=998, prod_cid=997)
        item = db.get_collocation("zdravo")
        rec = item.directions[Direction.RECOGNITION]
        rec.reps = 1
        rec.state = SRSState.LEARNING
        rec.stability = 2.5
        rec.difficulty = 8.0
        rec.last_review = datetime.now(UTC) - timedelta(minutes=5)
        rec.last_rating = 3
        rec.dirty_fsrs = True  # already-recovered state
        db.update_direction(guid, Direction.RECOGNITION, rec)

        writer = FakeWriter()
        sync = AnkiSync(
            db=db,
            _reader=FakeReader([]),
            _writer=writer,
            _anki_col_ver=KNOWN_ANKI_SCHEMA_VER,
        )
        # Simulate detect_and_reset_orphans then create_new: card_id was reset
        # then re-assigned. Mark this direction as recovered.
        sync._recovered_directions = {(guid, Direction.RECOGNITION.value)}

        sync.sync_push(force_fsrs=False)

        # force_fsrs path: data goes through update_card_memory_state (Layer 70
        # merge-write); set_specific_value_of_card keeps ivl/factor.
        assert writer.specific_value_calls, "force_fsrs not invoked for recovered row"
        card_id, keys, _values = writer.specific_value_calls[0]
        assert card_id == 998  # the (still-linked-in-this-test) anki_card_id
        assert keys == ["ivl", "factor"]
        assert writer.memory_state_calls and writer.memory_state_calls[0][0] == 998

    def test_push_does_not_force_fsrs_for_non_recovered_direction(self):
        """Without a recovery flag, force_fsrs stays off (existing behavior)."""
        db, guid = _make_db_with_link("zdravo", note_id=999, rec_cid=998, prod_cid=997)
        item = db.get_collocation("zdravo")
        rec = item.directions[Direction.RECOGNITION]
        rec.reps = 1
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(minutes=5)
        rec.last_rating = 3
        rec.dirty_fsrs = True
        rec.due_at = datetime.combine(date.today(), time(4, 0), tzinfo=UTC)
        db.update_direction(guid, Direction.RECOGNITION, rec)

        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader([]), _writer=writer)
        sync.sync_push(force_fsrs=False)

        assert writer.specific_value_calls == []


class TestGraveHonoring:
    """A note in Anki's `graves` table was deleted on purpose; honor it by
    hard-deleting the TT collocation instead of resurrecting it (the user's
    choice over recovery). A note merely missing — with no grave — is still
    treated as a wipe and recovered, preserving the force-full-download net.
    """

    def test_hard_deletes_collocation_when_note_in_graves(self):
        db, _ = _make_db_with_link("zdravo", note_id=999, rec_cid=998, prod_cid=997)
        anchors = _add_anchor_rows(db, count=4)
        # Note 999 is gone from Anki AND recorded in graves → intentional delete.
        sync = AnkiSync(db=db, _reader=FakeReader(anchors, grave_note_ids={999}), _writer=FakeWriter())

        sync.detect_and_reset_orphans()

        # Hard-deleted (cascades to directions), not resurrected.
        assert db.get_collocation("zdravo") is None
        # Anchors untouched.
        assert db.get_collocation("anchor_0").anki_note_id == 5000

    def test_recovers_when_note_missing_but_not_graved(self):
        db, _ = _make_db_with_link("zdravo", note_id=999, rec_cid=998, prod_cid=997)
        anchors = _add_anchor_rows(db, count=4)
        # Missing from Anki but NO grave → wipe/recovery: reset pointers, keep row.
        sync = AnkiSync(db=db, _reader=FakeReader(anchors, grave_note_ids=set()), _writer=FakeWriter())

        sync.detect_and_reset_orphans()

        item = db.get_collocation("zdravo")
        assert item is not None
        assert item.anki_note_id is None
        assert item.directions[Direction.RECOGNITION].anki_card_id is None

    def test_unmatched_grave_is_a_no_op(self):
        """Graves for notes TT no longer points to (e.g. already-resurrected-away
        ids) delete nothing — the live collocation is untouched."""
        db, _ = _make_db_with_link("zdravo", note_id=999, rec_cid=998, prod_cid=997)
        # zdravo is still live in Anki; the grave is for a different, long-gone note.
        rec = make_note_record(
            anki_note_id=999,
            l2_text="zdravo",
            translation="zdravo_t",
            cards=[make_card_record(anki_card_id=998, ord=0), make_card_record(anki_card_id=997, ord=1)],
        )
        sync = AnkiSync(db=db, _reader=FakeReader([rec], grave_note_ids={123456}), _writer=FakeWriter())

        sync.detect_and_reset_orphans()

        item = db.get_collocation("zdravo")
        assert item is not None
        assert item.anki_note_id == 999


class TestOfflineReaderGraves:
    """OfflineReader.get_grave_note_ids reads Anki's graves table (type=1 notes)."""

    def test_reads_type1_note_graves_only(self):
        import sqlite3

        from app.anki.sync import OfflineReader

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE graves (oid INTEGER NOT NULL, type INTEGER NOT NULL, "
            "usn INTEGER NOT NULL, PRIMARY KEY (oid, type))"
        )
        # type: 0=card, 1=note, 2=deck — only note graves are returned.
        conn.executemany(
            "INSERT INTO graves (oid, type, usn) VALUES (?, ?, -1)",
            [(111, 1), (222, 1), (333, 0), (444, 2)],
        )
        conn.commit()
        reader = OfflineReader(conn, "0. Slovene")
        assert reader.get_grave_note_ids() == {111, 222}

    def test_empty_set_when_graves_table_absent(self):
        import sqlite3

        from app.anki.sync import OfflineReader

        conn = sqlite3.connect(":memory:")  # minimal collection, no graves table
        reader = OfflineReader(conn, "0. Slovene")
        assert reader.get_grave_note_ids() == set()
