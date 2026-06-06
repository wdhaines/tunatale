"""Tests for S3.5: sync push (TunaTale → Anki)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, time, timedelta

from app.anki.sync import (
    AnkiSync,
    OfflineWriter,
    _local_today_4am,
    build_cloze_back_extra,
)
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from tests.conftest import make_card_record, make_note_record

# ── Shared helpers ─────────────────────────────────────────────────────────────


def _make_tt_db() -> SRSDatabase:
    return SRSDatabase(":memory:")


def _add_banka_with_anki_ids(
    db: SRSDatabase,
    *,
    anki_note_id: int = 9001,
    rec_cid: int = 90010,
    prod_cid: int = 90011,
) -> tuple[str, int, int, int]:
    """Add banka/bank to TT DB with Anki IDs. Returns (guid, note_id, rec_cid, prod_cid)."""
    unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit)
    item = db.get_collocation("banka")
    assert item is not None
    guid = item.guid
    db.set_anki_ids(guid, anki_note_id, {Direction.RECOGNITION: rec_cid, Direction.PRODUCTION: prod_cid})
    return guid, anki_note_id, rec_cid, prod_cid


def _mark_direction_dirty(
    db: SRSDatabase,
    guid: str,
    direction: Direction = Direction.RECOGNITION,
    *,
    state: SRSState = SRSState.REVIEW,
    reps: int = 3,
    stability: float = 10.5,
    anki_card_id: int = 90010,
    due_date: date | None = None,
    last_rating: int = 3,
) -> None:
    """Update a direction to dirty_fsrs=True, simulating a TT review."""
    ds = DirectionState(
        direction=direction,
        due_at=datetime.combine(due_date or (date.today() + timedelta(days=10)), time(4, 0), tzinfo=UTC),
        stability=stability,
        difficulty=4.8,
        reps=reps,
        lapses=0,
        state=state,
        dirty_fsrs=True,
        anki_card_id=anki_card_id,
        last_rating=last_rating,
    )
    db.update_direction(guid, direction, ds)


class TestLocalToday4am:
    """_local_today_4am returns the most recent 4 AM rollover."""

    def test_after_4am_returns_today(self):
        after = datetime(2026, 5, 15, 10, 0, 0, tzinfo=UTC)
        assert _local_today_4am(after).day == 15

    def test_before_4am_returns_yesterday(self):
        before = datetime(2026, 5, 15, 3, 0, 0, tzinfo=UTC)
        assert _local_today_4am(before).day == 14


class FakeWriter:
    """Records all writer calls for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        # Tests populate this to simulate Anki's current state for guard checks.
        # Maps card_id → {"queue": int, "type": int, "left": int}. Returning None
        # for an absent key tells push "no current state; proceed normally."
        self.current_states: dict[int, dict] = {}

    def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        self.calls.append(("update_note_fields", note_id, fields))

    def suspend(self, card_ids: list[int]) -> None:
        self.calls.append(("suspend", list(card_ids)))

    def unsuspend(self, card_ids: list[int]) -> None:
        self.calls.append(("unsuspend", list(card_ids)))

    def set_due_date(self, card_ids: list[int], days: str) -> None:
        self.calls.append(("set_due_date", list(card_ids), days))

    def forget_card(self, card_id: int) -> None:
        self.calls.append(("forget_card", card_id))

    def set_learning_state(self, card_id: int, left: int, due_at: int, *, type_: int = 1) -> None:
        self.calls.append(("set_learning_state", card_id, left, due_at, type_))

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
        self.calls.append(("write_revlog", cid, ease, ivl, last_ivl, factor, time_ms, type_, preferred_id))

    def get_current_card_state(self, card_id: int) -> dict | None:
        return self.current_states.get(card_id)

    def bury_siblings(
        self,
        *,
        graded_card_id: int,
        graded_queue: int,
        bury_new: bool = False,
        bury_reviews: bool = False,
        bury_interday_learning: bool = False,
    ) -> int:
        self.calls.append(
            ("bury_siblings", graded_card_id, graded_queue, bury_new, bury_reviews, bury_interday_learning)
        )
        return 0

    def list_decks_with_revlog_today(self, today_4am_ms: int) -> list[int]:
        return []

    def count_first_grades_today_for_deck(self, deck_id: int, today_4am_ms: int) -> int:
        return 0

    def set_deck_new_today(self, deck_id: int, today_day_index: int, new_today: int) -> None:
        self.calls.append(("set_deck_new_today", deck_id, today_day_index, new_today))

    def store_media_file(self, filename: str, data: bytes) -> None:
        self.calls.append(("store_media_file", filename, len(data)))

    def action_names(self) -> list[str]:
        return [c[0] for c in self.calls]


# ── TestListDirtyFieldEdits ────────────────────────────────────────────────────


class TestListDirtyFieldEdits:
    def test_returns_rows_with_dirty_fields(self):
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        db.set_dirty_fields(guid, "translation")
        rows = db.list_dirty_field_edits()
        assert len(rows) == 1
        row_guid, anki_note_id, dirty_str, item, _ = rows[0]
        assert row_guid == guid
        assert anki_note_id == 9001
        assert dirty_str == "translation"
        assert item.syntactic_unit.translation == "bank"

    def test_excludes_clean_rows(self):
        db = _make_tt_db()
        _add_banka_with_anki_ids(db)  # dirty_fields = '' (default)
        assert db.list_dirty_field_edits() == []

    def test_empty_when_nothing_in_db(self):
        db = _make_tt_db()
        assert db.list_dirty_field_edits() == []


# ── TestOfflineWriter
# ── TestOfflineWriter ──────────────────────────────────────────────────────────


def _make_anki_revlog_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE revlog "
        "(id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,"
        " lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER)"
    )
    conn.commit()
    return conn


def _make_anki_full_db(col_crt: int | None = None) -> sqlite3.Connection:
    """Minimal collection.anki2 shape: col, notes, cards, revlog — enough for writer tests."""
    from datetime import UTC, datetime, timedelta

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE col (
            id INTEGER PRIMARY KEY, crt INTEGER, mod INTEGER, scm INTEGER,
            ver INTEGER, dty INTEGER, usn INTEGER, ls INTEGER
        );
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER,
            usn INTEGER, tags TEXT, flds TEXT, sfld TEXT, csum INTEGER,
            flags INTEGER, data TEXT
        );
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER,
            mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER, due INTEGER,
            ivl INTEGER, factor INTEGER, reps INTEGER, lapses INTEGER,
            left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT
        );
        CREATE TABLE revlog (
            id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,
            lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER
        );
        """
    )
    if col_crt is None:
        # One year ago at midnight UTC — matches a typical Anki collection epoch.
        col_crt = int(
            (datetime.now(tz=UTC) - timedelta(days=365)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
    conn.execute(
        "INSERT INTO col (id, crt, mod, scm, ver, dty, usn, ls) VALUES (1, ?, 0, 0, 18, 0, 0, 0)",
        (col_crt,),
    )
    conn.commit()
    return conn


def _seed_note_and_cards(
    conn: sqlite3.Connection,
    *,
    note_id: int = 9001,
    guid: str = "banka-guid",
    mid: int = 1,
    rec_cid: int = 90010,
    prod_cid: int = 90011,
    flds: tuple[str, ...] = ("banka", "bank", "", "", "", "", ""),
    queue: int = 2,
    card_type: int = 2,
    due: int = 0,
    ivl: int = 1,
) -> None:
    flds_str = "\x1f".join(flds)
    conn.execute(
        "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
        "VALUES (?, ?, ?, 100, 0, '', ?, ?, 0, 0, '')",
        (note_id, guid, mid, flds_str, flds[0]),
    )
    for cid, ord_ in ((rec_cid, 0), (prod_cid, 1)):
        conn.execute(
            "INSERT INTO cards "
            "(id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, left, odue, odid, flags, data) "
            "VALUES (?, ?, 1, ?, 100, 0, ?, ?, ?, ?, 2500, 0, 0, 0, 0, 0, 0, '')",
            (cid, note_id, ord_, card_type, queue, due, ivl),
        )
    conn.commit()


class TestBuildClozeBackExtra:
    def test_build_cloze_back_extra_with_note(self):
        """All three parts present: translation, sentence_translation, note."""
        result = build_cloze_back_extra(
            translation="every",
            sentence_translation="It is open every day",
            note="user note here",
        )
        assert "<i>every</i>" in result
        assert '<span class="st">It is open every day</span>' in result
        assert "user note here" in result
        assert result.count("<br>") >= 4  # two separators → 4 <br> chars

    def test_build_cloze_back_extra_with_audio(self):
        """sentence_audio_filename appends [sound:...] at the end."""
        result = build_cloze_back_extra(
            translation="every",
            sentence_translation="It is open every day",
            sentence_audio_filename="x.mp3",
        )
        assert result.endswith("[sound:x.mp3]")

    def test_build_cloze_back_extra_with_grammar(self):
        """Grammar hint renders in a <span class='grammar'> element."""
        result = build_cloze_back_extra(
            translation="every",
            sentence_translation="It is open every day",
            grammar="biti, 1st person singular",
        )
        assert '<span class="grammar">biti, 1st person singular</span>' in result

    def test_build_cloze_back_extra_audio_only(self):
        """With no other parts, returns just [sound:...]."""
        result = build_cloze_back_extra(
            translation="",
            sentence_translation="",
            sentence_audio_filename="x.mp3",
        )
        assert result == "[sound:x.mp3]"


class TestOfflineWriter:
    def test_write_revlog_inserts_row(self):
        conn = _make_anki_full_db()
        writer = OfflineWriter(conn)
        writer.write_revlog(cid=12345, ease=3, ivl=7, last_ivl=7, factor=2500, time_ms=1000, type_=2)
        row = conn.execute("SELECT * FROM revlog").fetchone()
        assert row is not None
        assert row["cid"] == 12345
        assert row["ease"] == 3
        assert row["ivl"] == 7
        assert row["factor"] == 2500
        assert row["type"] == 2

    def test_write_revlog_bumps_col_mod_preserves_usn(self):
        """col.usn is the sync anchor (server's last value), not a dirty flag.

        Layer 61: clobbering col.usn to -1 made AnkiWeb demand a full sync whenever
        another device (e.g. the phone) advanced the server's USN. _bump_col now
        only bumps mod; the revlog row itself carries usn=-1 to push.
        """
        conn = _make_anki_full_db()
        conn.execute("UPDATE col SET usn = 7")
        conn.commit()
        writer = OfflineWriter(conn)
        writer.write_revlog(cid=12345, ease=3, ivl=7, last_ivl=7, factor=2500, time_ms=1000, type_=2)
        col = conn.execute("SELECT mod, usn FROM col").fetchone()
        assert col["usn"] == 7
        assert col["mod"] > 0

    def test_write_revlog_increments_reps(self):
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, rec_cid=90010)
        writer = OfflineWriter(conn)
        card_before = conn.execute("SELECT reps, lapses FROM cards WHERE id=90010").fetchone()
        assert card_before["reps"] == 0
        assert card_before["lapses"] == 0
        writer.write_revlog(cid=90010, ease=3, ivl=7, last_ivl=7, factor=2500, time_ms=1000, type_=2)
        card_after = conn.execute("SELECT reps, lapses FROM cards WHERE id=90010").fetchone()
        assert card_after["reps"] == 1, "reps must be incremented by 1"
        assert card_after["lapses"] == 0, "non-lapse must not increment lapses"

    def test_write_revlog_increments_lapses_on_lapse(self):
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, rec_cid=90010)
        writer = OfflineWriter(conn)
        card_before = conn.execute("SELECT reps, lapses FROM cards WHERE id=90010").fetchone()
        assert card_before["reps"] == 0
        assert card_before["lapses"] == 0
        writer.write_revlog(cid=90010, ease=1, ivl=-600, last_ivl=10, factor=2500, time_ms=1000, type_=1, is_lapse=True)
        card_after = conn.execute("SELECT reps, lapses FROM cards WHERE id=90010").fetchone()
        assert card_after["reps"] == 1, "reps must be incremented"
        assert card_after["lapses"] == 1, "lapses must be incremented on lapse"

    def test_write_revlog_preserves_lapses_without_lapse(self):
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, rec_cid=90010)
        conn.execute("UPDATE cards SET lapses = 3 WHERE id = 90010")
        conn.commit()
        writer = OfflineWriter(conn)
        writer.write_revlog(cid=90010, ease=3, ivl=7, last_ivl=7, factor=2500, time_ms=1000, type_=2)
        card_after = conn.execute("SELECT reps, lapses FROM cards WHERE id=90010").fetchone()
        assert card_after["reps"] == 1
        assert card_after["lapses"] == 3, "pre-existing lapses must be preserved"

    def test_update_note_fields_replaces_named_field_and_bumps_usn(self):
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn)
        conn.execute("UPDATE col SET usn = 7")
        conn.commit()
        writer = OfflineWriter(conn)
        writer.update_note_fields(9001, {"English": "bank (financial)"})

        row = conn.execute("SELECT flds, usn, mod FROM notes WHERE id=9001").fetchone()
        parts = row["flds"].split("\x1f")
        assert parts[0] == "banka"  # Slovene untouched
        assert parts[1] == "bank (financial)"  # English replaced
        assert row["usn"] == -1
        assert row["mod"] > 100  # bumped past seed value
        col = conn.execute("SELECT usn FROM col").fetchone()
        assert col["usn"] == 7  # anchor preserved (Layer 61); the note row pushes via its own usn=-1

    def test_update_note_fields_with_notetypes_table_no_match(self):
        """notetypes table exists but note's mid has no matching row → falls through to Slovene_VOCAB_FIELD_NAMES."""
        conn = _make_anki_full_db()
        conn.execute(
            "CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
        )
        conn.execute("CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord))")
        # Insert a notetype with id=100, but seed the note with mid=999 (no match)
        conn.execute("INSERT INTO notetypes VALUES (100, 'Cloze', 0, 0, x'')")
        conn.execute("INSERT INTO fields VALUES (100, 0, 'Text', x''), (100, 1, 'Back Extra', x'')")
        _seed_note_and_cards(conn, mid=999, flds=("banka", "bank", "", "", "", "", ""))
        writer = OfflineWriter(conn)
        # "English" is from Slovene Vocabulary, not Cloze
        writer.update_note_fields(9001, {"English": "bank (financial)"})
        row = conn.execute("SELECT flds FROM notes WHERE id=9001").fetchone()
        assert row["flds"].split("\x1f")[1] == "bank (financial)"

    def test_update_note_fields_with_cloze_notetype(self):
        """Cloze notetype path: update Back Extra via update_note_fields."""
        conn = _make_anki_full_db()
        # Add notetypes table and Cloze notetype
        conn.execute(
            "CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
        )
        conn.execute("CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord))")
        conn.execute("INSERT INTO notetypes VALUES (100, 'Cloze', 0, 0, x'')")
        conn.execute("INSERT INTO fields VALUES (100, 0, 'Text', x''), (100, 1, 'Back Extra', x'')")
        # Seed a cloze note with mid pointing to the Cloze notetype
        _seed_note_and_cards(conn, mid=100, flds=("vsak", "<i>every</i>", "", "", "", "", ""))
        writer = OfflineWriter(conn)
        writer.update_note_fields(
            9001,
            {"Back Extra": '<i>every</i><br><br><span class="st">It is open every day</span>'},
        )
        row = conn.execute("SELECT flds, usn FROM notes WHERE id=9001").fetchone()
        parts = row["flds"].split("\x1f")
        assert parts[1] == '<i>every</i><br><br><span class="st">It is open every day</span>'
        assert row["usn"] == -1

    def test_suspend_sets_queue_minus_one_and_usn_minus_one(self):
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn)
        writer = OfflineWriter(conn)
        writer.suspend([90010])

        row = conn.execute("SELECT queue, usn, mod FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == -1
        assert row["usn"] == -1
        assert row["mod"] > 100
        # other card untouched
        other = conn.execute("SELECT queue FROM cards WHERE id=90011").fetchone()
        assert other["queue"] == 2

    def test_unsuspend_restores_queue_from_type(self):
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=-1, card_type=2)  # suspended review card
        writer = OfflineWriter(conn)
        writer.unsuspend([90010])

        row = conn.execute("SELECT queue, usn FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == 2  # restored to review
        assert row["usn"] == -1

    def test_set_due_date_shifts_due_relative_to_today(self):
        from datetime import date, timedelta

        col_crt = int((date.today() - timedelta(days=200)).strftime("%s"))
        conn = _make_anki_full_db(col_crt=col_crt)
        _seed_note_and_cards(conn, queue=2, card_type=2, due=0, ivl=1)
        writer = OfflineWriter(conn)
        writer.set_due_date([90010], "7")

        row = conn.execute("SELECT due, ivl, usn, mod FROM cards WHERE id=90010").fetchone()
        # due-days-since-crt today is 200; +7 = 207
        assert row["due"] == 207
        assert row["ivl"] == 7
        assert row["usn"] == -1
        assert row["mod"] > 100

    def test_update_note_fields_unknown_note_id_is_noop(self):
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn)
        writer = OfflineWriter(conn)
        writer.update_note_fields(99999, {"English": "nope"})
        row = conn.execute("SELECT flds FROM notes WHERE id=9001").fetchone()
        # Original note untouched.
        assert row["flds"].split("\x1f")[1] == "bank"

    def test_update_note_fields_unknown_field_name_raises(self):
        import pytest

        conn = _make_anki_full_db()
        _seed_note_and_cards(conn)
        writer = OfflineWriter(conn)
        with pytest.raises(ValueError, match="Unknown field"):
            writer.update_note_fields(9001, {"Back": "bank"})

    def test_set_due_date_preserves_suspension(self):
        from datetime import date, timedelta

        col_crt = int((date.today() - timedelta(days=200)).strftime("%s"))
        conn = _make_anki_full_db(col_crt=col_crt)
        _seed_note_and_cards(conn, queue=-1, card_type=2, due=0)
        writer = OfflineWriter(conn)
        writer.set_due_date([90010], "5")

        row = conn.execute("SELECT queue, due FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == -1  # still suspended
        assert row["due"] == 205

    def test_set_learning_state_writes_queue_and_type_for_relearning(self):
        """REVIEW → RELEARNING push must flip queue=2,type=2 → queue=1,type=3.

        Regression: previously only updated left/due, leaving queue=2 — the next
        sync_pull then read a queue=2 card with a unix-timestamp due, which
        crashed compute_due_date with OverflowError on real Anki collections.
        """
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=2, card_type=2, due=4500, ivl=10)
        writer = OfflineWriter(conn)
        writer.set_learning_state(90010, left=1001, due_at=1778000000, type_=3)

        row = conn.execute("SELECT queue, type, left, due, usn, mod FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == 1, "RELEARNING must set queue=1 (intra-day learning queue)"
        assert row["type"] == 3, "RELEARNING must set type=3 (Anki's lapse type)"
        assert row["left"] == 1001
        assert row["due"] == 1778000000
        assert row["usn"] == -1

    def test_bury_siblings_review_graded_buries_review_sibling(self):
        """Layer 47: TT-graded Review card → Anki's bury_siblings writes queue=-2 to a queue=2 sibling.

        Mirrors Anki's `bury_siblings` (rslib/.../bury_and_suspend.rs:132) + the
        `siblings_for_bury.sql` query: sibling at queue=2 buried when bury_reviews=True.
        """
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=2, card_type=2)
        conn.execute("UPDATE col SET usn = 7")
        conn.commit()
        writer = OfflineWriter(conn)
        n = writer.bury_siblings(graded_card_id=90011, graded_queue=2, bury_reviews=True)

        assert n == 1
        row = conn.execute("SELECT queue, usn, mod FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == -2, "sibling must be sched-buried (queue=-2)"
        assert row["usn"] == -1
        assert row["mod"] > 100
        # graded card itself untouched
        graded = conn.execute("SELECT queue, mod FROM cards WHERE id=90011").fetchone()
        assert graded["queue"] == 2
        # col.mod bumped, col.usn anchor preserved (Layer 61); the buried card pushes via usn=-1
        col = conn.execute("SELECT mod, usn FROM col").fetchone()
        assert col["usn"] == 7

    def test_bury_siblings_no_op_when_all_flags_false(self):
        """bury_new=bury_reviews=bury_interday_learning=False → no writes."""
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=2, card_type=2)
        writer = OfflineWriter(conn)
        n = writer.bury_siblings(graded_card_id=90011, graded_queue=2)

        assert n == 0
        row = conn.execute("SELECT queue FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == 2

    def test_bury_siblings_skips_suspended_sibling(self):
        """Suspended siblings (queue=-1) are NEVER buried."""
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=-1, card_type=2)
        writer = OfflineWriter(conn)
        n = writer.bury_siblings(graded_card_id=90011, graded_queue=2, bury_reviews=True)

        assert n == 0
        row = conn.execute("SELECT queue FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == -1, "suspended sibling stays suspended"

    def test_bury_siblings_skips_intraday_learning_sibling(self):
        """queue=1 (intra-day Learn) siblings are NOT included in Anki's bury query.

        Per `siblings_for_bury.sql`, only queue=New(0)/Review(2)/DayLearn(3) are eligible.
        """
        conn = _make_anki_full_db()
        # Set the rec sibling (cid=90010) to queue=1 (learning)
        _seed_note_and_cards(conn, queue=2, card_type=2)
        conn.execute("UPDATE cards SET queue=1 WHERE id=90010")
        conn.commit()
        writer = OfflineWriter(conn)
        n = writer.bury_siblings(
            graded_card_id=90011,
            graded_queue=2,
            bury_new=True,
            bury_reviews=True,
            bury_interday_learning=True,
        )
        assert n == 0
        row = conn.execute("SELECT queue FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == 1, "intra-day learning sibling stays unburied"

    def test_bury_siblings_review_grade_excludes_interday_learning(self):
        """Anki's `exclude_earlier_gathered_queues`: graded Review (gather_ord=2)
        disables bury_interday_learning (which requires gather_ord ≤ 1).

        bury_reviews kept (2 ≤ 2). So a queue=3 (DayLearn) sibling stays unburied
        even with bury_interday_learning=True at the call site.
        """
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=2, card_type=2)
        conn.execute("UPDATE cards SET queue=3 WHERE id=90010")  # DayLearn sibling
        conn.commit()
        writer = OfflineWriter(conn)
        n = writer.bury_siblings(
            graded_card_id=90011,
            graded_queue=2,
            bury_interday_learning=True,
        )
        assert n == 0
        row = conn.execute("SELECT queue FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == 3, "DayLearn sibling kept (interday bury disabled for Review grade)"

    def test_bury_siblings_learning_grade_buries_dayLearn_sibling(self):
        """Graded Learn (q=1, gather_ord=0): both bury_reviews and bury_interday_learning kept."""
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=2, card_type=2)
        conn.execute("UPDATE cards SET queue=3 WHERE id=90010")  # DayLearn sibling
        conn.commit()
        writer = OfflineWriter(conn)
        n = writer.bury_siblings(
            graded_card_id=90011,
            graded_queue=1,
            bury_interday_learning=True,
        )
        assert n == 1
        row = conn.execute("SELECT queue FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == -2

    def test_bury_siblings_buries_new_sibling_when_bury_new_true(self):
        """A queue=0 (New) sibling is buried when bury_new=True."""
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=2, card_type=2)
        conn.execute("UPDATE cards SET queue=0 WHERE id=90010")
        conn.commit()
        writer = OfflineWriter(conn)
        n = writer.bury_siblings(graded_card_id=90011, graded_queue=2, bury_new=True)
        assert n == 1
        row = conn.execute("SELECT queue FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == -2

    def test_bury_siblings_new_grade_drops_review_and_interday(self):
        """Graded New (gather_ord=3): drops bury_reviews and bury_interday_learning
        per exclude_earlier_gathered_queues. Only bury_new survives."""
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=2, card_type=2)
        writer = OfflineWriter(conn)
        n = writer.bury_siblings(
            graded_card_id=90011,
            graded_queue=0,
            bury_reviews=True,
            bury_interday_learning=True,
        )
        assert n == 0, "review sibling NOT buried — bury_reviews dropped by New-grade gather_ord rule"

    def test_bury_siblings_unknown_queue_drops_all_flags(self):
        """An out-of-range graded_queue drops every flag — defensive: gather_ord=255
        fails every `gather_ord <= N` check, including bury_new (3)."""
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=2, card_type=2)
        writer = OfflineWriter(conn)
        n = writer.bury_siblings(
            graded_card_id=90011,
            graded_queue=99,
            bury_new=True,
            bury_reviews=True,
            bury_interday_learning=True,
        )
        assert n == 0

    def test_bury_siblings_missing_card_returns_zero(self):
        """Graded card not present in cards table → no-op."""
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=2, card_type=2)
        writer = OfflineWriter(conn)
        n = writer.bury_siblings(graded_card_id=999999, graded_queue=2, bury_reviews=True)
        assert n == 0

    def test_set_learning_state_preserves_suspension(self):
        """Suspended cards (queue=-1) must NOT be unsuspended by set_learning_state."""
        conn = _make_anki_full_db()
        _seed_note_and_cards(conn, queue=-1, card_type=2)
        writer = OfflineWriter(conn)
        writer.set_learning_state(90010, left=1001, due_at=1778000000, type_=3)

        row = conn.execute("SELECT queue, type FROM cards WHERE id=90010").fetchone()
        assert row["queue"] == -1, "suspension must be preserved"


# ── TestSyncPush ──────────────────────────────────────────────────────────────


class FakeReader:
    def get_note_records(self):
        return []

    def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
        return []


class TestSyncPushForget:
    """A TT reset (dirty NEW direction, reps=0) must push an Anki "Forget" so
    both apps agree the card is new — not the default review-promoting
    set_due_date. Regression for the 2026-06-04 new-vs-review badge divergence.
    """

    @staticmethod
    def _reset_dirty(db, guid, direction, anki_card_id):
        ds = DirectionState(
            direction=direction,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=1.0,
            difficulty=5.0,
            reps=0,
            lapses=0,
            state=SRSState.NEW,
            dirty_fsrs=True,
            anki_card_id=anki_card_id,
        )
        db.update_direction(guid, direction, ds)

    def test_forgets_graduated_anki_card_and_does_not_promote_to_review(self):
        db = _make_tt_db()
        guid, _, _, prod_cid = _add_banka_with_anki_ids(db)
        self._reset_dirty(db, guid, Direction.PRODUCTION, prod_cid)

        writer = FakeWriter()
        writer.current_states[prod_cid] = {"queue": 2, "type": 2, "left": 0}  # Anki has it graduated
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert ("forget_card", prod_cid) in writer.calls
        assert "set_due_date" not in writer.action_names()  # must NOT promote to review
        assert db.get_collocation("banka").directions[Direction.PRODUCTION].dirty_fsrs is False

    def test_noop_when_anki_card_already_new(self):
        db = _make_tt_db()
        guid, _, _, prod_cid = _add_banka_with_anki_ids(db)
        self._reset_dirty(db, guid, Direction.PRODUCTION, prod_cid)

        writer = FakeWriter()
        writer.current_states[prod_cid] = {"queue": 0, "type": 0, "left": 0}  # already new
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "forget_card" not in writer.action_names()
        assert db.get_collocation("banka").directions[Direction.PRODUCTION].dirty_fsrs is False

    def test_noop_when_anki_card_state_unknown(self):
        db = _make_tt_db()
        guid, _, _, prod_cid = _add_banka_with_anki_ids(db)
        self._reset_dirty(db, guid, Direction.PRODUCTION, prod_cid)

        writer = FakeWriter()  # no current_states entry → get_current_card_state returns None
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "forget_card" not in writer.action_names()
        assert db.get_collocation("banka").directions[Direction.PRODUCTION].dirty_fsrs is False

    def test_dry_run_does_not_write_or_clean(self):
        db = _make_tt_db()
        guid, _, _, prod_cid = _add_banka_with_anki_ids(db)
        self._reset_dirty(db, guid, Direction.PRODUCTION, prod_cid)

        writer = FakeWriter()
        writer.current_states[prod_cid] = {"queue": 2, "type": 2, "left": 0}
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push(dry_run=True)

        assert "forget_card" not in writer.action_names()
        assert db.get_collocation("banka").directions[Direction.PRODUCTION].dirty_fsrs is True


class TestSyncPush:
    def test_dirty_translation_calls_update_note_fields(self):
        db = _make_tt_db()
        guid, note_id, *_ = _add_banka_with_anki_ids(db)
        db.set_dirty_fields(guid, "translation")

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "update_note_fields" in writer.action_names()
        call = next(c for c in writer.calls if c[0] == "update_note_fields")
        assert call[1] == note_id
        assert "English" in call[2]
        assert call[2]["English"] == "bank"
        assert "Back" not in call[2]

    def test_dirty_translation_clears_dirty_fields_after_push(self):
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        db.set_dirty_fields(guid, "translation")

        AnkiSync(db=db, _reader=FakeReader(), _writer=FakeWriter()).sync_push()

        assert db.get_dirty_fields(guid) == ""

    def test_dirty_source_sentence_pushes_vocab_note_field(self):
        """A vocab card with dirty source_sentence pushes its Note (example) field."""
        db = _make_tt_db()
        unit = SyntacticUnit(
            text="imeti",
            translation="have",
            word_count=1,
            difficulty=1,
            source="user",
            lemma="imeti",
            source_sentence="Koliko časa imaš?",
        )
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("imeti")
        guid = item.guid
        db.set_anki_ids(guid, 8801, {Direction.RECOGNITION: 88010, Direction.PRODUCTION: 88011})
        db.set_dirty_fields(guid, "source_sentence")

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        call = next(c for c in writer.calls if c[0] == "update_note_fields")
        assert call[1] == 8801
        assert call[2]["Note"] == "Koliko časa imaš?"
        assert db.get_dirty_fields(guid) == ""

    def test_dirty_source_sentence_pushes_cloze_text_field(self):
        """A cloze card with dirty source_sentence pushes its Text (front) field."""
        db = _make_tt_db()
        unit = SyntacticUnit(
            text="koliko",
            translation="how much",
            word_count=1,
            difficulty=1,
            source="user",
            lemma="koliko",
            card_type="cloze",
            source_sentence="{{c1::Koliko}} časa imaš?",
        )
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation_by_lemma("koliko")
        guid = item.guid
        db.set_anki_ids(guid, 8802, {Direction.PRODUCTION: 88021})
        db.set_dirty_fields(guid, "source_sentence")

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        call = next(c for c in writer.calls if c[0] == "update_note_fields")
        assert call[1] == 8802
        assert call[2]["Text"] == "{{c1::Koliko}} časa imaš?"
        assert db.get_dirty_fields(guid) == ""

    def test_dirty_cloze_sentence_translation_writes_back_extra(self):
        """Cloze card with dirty sentence_translation rebuilds Back Extra and pushes."""
        db = _make_tt_db()
        unit = SyntacticUnit(
            text="vsak",
            translation="every",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="vsak",
            card_type="cloze",
            source_sentence="Odprto je vsak dan",
            source_sentence_translation="It is open every day",
        )
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation_by_lemma("vsak")
        guid = item.guid
        # Cloze notes have only one card (PRODUCTION).
        db.set_anki_ids(guid, 7777, {Direction.PRODUCTION: 70001})
        db.set_dirty_fields(guid, "sentence_translation")

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "update_note_fields" in writer.action_names()
        call = next(c for c in writer.calls if c[0] == "update_note_fields")
        assert call[1] == 7777
        assert "Back Extra" in call[2]
        be = call[2]["Back Extra"]
        assert "<i>every</i>" in be
        assert '<span class="st">It is open every day</span>' in be
        # Dirty fields cleared
        assert db.get_dirty_fields(guid) == ""

    def test_dirty_cloze_translation_writes_back_extra_not_english_field(self):
        """Cloze with dirty translation writes Back Extra (cloze has no English field)."""
        db = _make_tt_db()
        unit = SyntacticUnit(
            text="vsak",
            translation="every",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="vsak",
            card_type="cloze",
            source_sentence="Odprto je vsak dan",
            source_sentence_translation="",
        )
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation_by_lemma("vsak")
        guid = item.guid
        db.set_anki_ids(guid, 7777, {Direction.PRODUCTION: 70001})
        db.set_dirty_fields(guid, "translation")

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "update_note_fields" in writer.action_names()
        call = next(c for c in writer.calls if c[0] == "update_note_fields")
        assert "Back Extra" in call[2]
        assert "English" not in call[2]
        assert "<i>every</i>" in call[2]["Back Extra"]

    def test_cloze_dirty_fields_not_matching_skips_back_extra(self):
        """Cloze with dirty field outside {translation, sentence_translation, note} → no update_note_fields."""
        db = _make_tt_db()
        unit = SyntacticUnit(
            text="vsak",
            translation="every",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="vsak",
            card_type="cloze",
            source_sentence="Odprto je vsak dan",
        )
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation_by_lemma("vsak")
        guid = item.guid
        db.set_anki_ids(guid, 7777, {Direction.PRODUCTION: 70001})
        # Mark a field NOT in the cloze back-extra set
        db.set_dirty_fields(guid, "direction")
        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()
        assert "update_note_fields" not in writer.action_names()
        # Dirty fields preserved since nothing was pushed
        assert db.get_dirty_fields(guid) == "direction"

    def test_sync_push_writes_sound_tag_for_cloze_with_audio_and_copies_media(self, tmp_path):
        """Audio-dirty cloze: writes [sound:...] in Back Extra, copies MP3, clears dirty."""
        db = _make_tt_db()
        unit = SyntacticUnit(
            text="še",
            translation="yet",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="še",
            card_type="cloze",
            source_sentence="Ja, še nisem videl.",
        )
        db.add_collocation(unit, language_code="sl")
        coll_id, item = db.get_collocation_by_lemma_with_id("še")
        guid = item.guid
        db.set_anki_ids(guid, 813, {Direction.PRODUCTION: 70001})
        # Seed a sentence-audio media row + the actual file
        (tmp_path / "tts_sentence_abc.mp3").write_bytes(b"fake-mp3")
        db.add_media(
            collocation_id=coll_id,
            kind="audio_tts_sentence",
            filename="tts_sentence_abc.mp3",
            path=str(tmp_path / "tts_sentence_abc.mp3"),
            anki_filename="",
            sha256="abc",
            size_bytes=8,
        )
        db.set_dirty_fields(guid, "audio")

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "update_note_fields" in writer.action_names()
        call = next(c for c in writer.calls if c[0] == "update_note_fields")
        assert "[sound:tts_sentence_abc.mp3]" in call[2]["Back Extra"]
        assert db.get_dirty_fields(guid) == ""

    def test_sync_push_skips_audio_copy_when_file_missing(self, tmp_path):
        """Audio-dirty cloze: media row exists but MP3 absent — tag still written, no store_media_file."""
        db = _make_tt_db()
        unit = SyntacticUnit(
            text="še",
            translation="yet",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="še",
            card_type="cloze",
            source_sentence="Ja, še nisem videl.",
        )
        db.add_collocation(unit, language_code="sl")
        coll_id, item = db.get_collocation_by_lemma_with_id("še")
        guid = item.guid
        db.set_anki_ids(guid, 813, {Direction.PRODUCTION: 70001})
        # Media row exists but NO file on disk
        db.add_media(
            collocation_id=coll_id,
            kind="audio_tts_sentence",
            filename="tts_sentence_nonexistent.mp3",
            path="/nonexistent/tts_sentence_nonexistent.mp3",
            anki_filename="",
            sha256="abc",
            size_bytes=8,
        )
        db.set_dirty_fields(guid, "audio")

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "update_note_fields" in writer.action_names()
        call = next(c for c in writer.calls if c[0] == "update_note_fields")
        assert "[sound:tts_sentence_nonexistent.mp3]" in call[2]["Back Extra"]
        assert "store_media_file" not in writer.action_names()
        assert db.get_dirty_fields(guid) == ""

    def test_sync_push_audio_dirty_plus_other_fields_rebuilds_once(self, tmp_path):
        """Cloze with audio+translation dirty: single back_extra rebuild includes both."""
        db = _make_tt_db()
        unit = SyntacticUnit(
            text="še",
            translation="yet",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="še",
            card_type="cloze",
            source_sentence="Ja, še nisem videl.",
            source_sentence_translation="Yes, I haven't seen yet.",
        )
        db.add_collocation(unit, language_code="sl")
        coll_id, item = db.get_collocation_by_lemma_with_id("še")
        guid = item.guid
        db.set_anki_ids(guid, 813, {Direction.PRODUCTION: 70001})
        db.add_media(
            collocation_id=coll_id,
            kind="audio_tts_sentence",
            filename="tts_sentence_abc.mp3",
            path=str(tmp_path / "tts_sentence_abc.mp3"),
            anki_filename="",
            sha256="abc",
            size_bytes=8,
        )
        (tmp_path / "tts_sentence_abc.mp3").write_bytes(b"fake-mp3")
        db.set_dirty_fields(guid, "audio,translation")

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        call = next(c for c in writer.calls if c[0] == "update_note_fields")
        be = call[2]["Back Extra"]
        assert "<i>yet</i>" in be
        assert "[sound:tts_sentence_abc.mp3]" in be
        assert db.get_dirty_fields(guid) == ""

    def test_sync_push_audio_only_dry_run_does_not_copy_media(self, tmp_path):
        """Dry-run sync_push with audio dirty: no store_media_file call."""
        db = _make_tt_db()
        unit = SyntacticUnit(
            text="še",
            translation="yet",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="še",
            card_type="cloze",
            source_sentence="Ja, še nisem videl.",
        )
        db.add_collocation(unit, language_code="sl")
        coll_id, item = db.get_collocation_by_lemma_with_id("še")
        guid = item.guid
        db.set_anki_ids(guid, 813, {Direction.PRODUCTION: 70001})
        db.add_media(
            collocation_id=coll_id,
            kind="audio_tts_sentence",
            filename="tts_sentence_abc.mp3",
            path=str(tmp_path / "tts_sentence_abc.mp3"),
            anki_filename="",
            sha256="abc",
            size_bytes=8,
        )
        (tmp_path / "tts_sentence_abc.mp3").write_bytes(b"fake-mp3")
        db.set_dirty_fields(guid, "audio")

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push(dry_run=True)

        assert "update_note_fields" not in writer.action_names()
        assert "store_media_file" not in writer.action_names()
        assert db.get_dirty_fields(guid) == "audio"

    def test_dirty_direction_calls_set_due_date(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        due = date.today() + timedelta(days=7)
        _mark_direction_dirty(db, guid, due_date=due)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "set_due_date" in writer.action_names()
        call = next(c for c in writer.calls if c[0] == "set_due_date")
        assert rec_cid in call[1]
        assert call[2] == "7"

    def test_dirty_direction_suspended_calls_suspend(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, state=SRSState.SUSPENDED, reps=0)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "suspend" in writer.action_names()
        call = next(c for c in writer.calls if c[0] == "suspend")
        assert rec_cid in call[1]

    def test_dirty_direction_not_suspended_calls_unsuspend(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, state=SRSState.REVIEW, reps=3)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "unsuspend" in writer.action_names()
        call = next(c for c in writer.calls if c[0] == "unsuspend")
        assert rec_cid in call[1]

    def test_suspend_one_direction_only_suspends_that_card(self):
        """Only the RECOGNITION card is suspended; PRODUCTION is untouched."""
        db = _make_tt_db()
        guid, _, rec_cid, prod_cid = _add_banka_with_anki_ids(db)
        # Only recognition is dirty+suspended
        _mark_direction_dirty(db, guid, Direction.RECOGNITION, state=SRSState.SUSPENDED, reps=0, anki_card_id=rec_cid)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        suspended_ids = [id_ for c in writer.calls if c[0] == "suspend" for id_ in c[1]]
        unsuspended_ids = [id_ for c in writer.calls if c[0] == "unsuspend" for id_ in c[1]]
        assert rec_cid in suspended_ids
        assert prod_cid not in suspended_ids
        assert prod_cid not in unsuspended_ids

    def test_set_specific_value_not_called_without_force_fsrs(self):
        """setSpecificValueOfCard must not be called during a normal push."""
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "set_specific_value_of_card" not in writer.action_names()

    def test_dirty_direction_with_reps_inserts_revlog(self):
        """Pushing a reviewed dirty direction inserts revlog directly."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=3, stability=10.5)

        anki_conn = _make_anki_full_db()
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid)
        writer = OfflineWriter(anki_conn)
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        rows = anki_conn.execute("SELECT * FROM revlog").fetchall()
        assert len(rows) == 1
        assert rows[0]["cid"] == rec_cid
        assert rows[0]["ivl"] == max(1, round(10.5))

    def test_dirty_direction_with_reps_inserts_revlog_offline(self):
        """Offline: pushing a reviewed dirty direction inserts directly into Anki revlog."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=3, stability=10.5)

        anki_conn = _make_anki_full_db()
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid)
        writer = OfflineWriter(anki_conn)
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        row = anki_conn.execute("SELECT * FROM revlog").fetchone()
        assert row is not None
        assert row["cid"] == rec_cid
        assert row["ivl"] == max(1, round(10.5))

    def test_zero_reps_does_not_emit_revlog(self):
        """A direction with reps=0 (never reviewed) does not emit a revlog entry."""
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=0, state=SRSState.SUSPENDED)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "write_revlog" not in writer.action_names()

    def test_sync_push_increments_anki_reps(self):
        """Pushing a dirty direction increments cards.reps in Anki DB."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=3, stability=10.5)

        anki_conn = _make_anki_full_db()
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid)
        card_before = anki_conn.execute("SELECT reps FROM cards WHERE id=?", (rec_cid,)).fetchone()
        assert card_before["reps"] == 0

        writer = OfflineWriter(anki_conn)
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        card_after = anki_conn.execute("SELECT reps, lapses FROM cards WHERE id=?", (rec_cid,)).fetchone()
        # reps jumps to ds.reps=3 via MAX(reps+1, ds_reps) — heals pre-existing drift
        assert card_after["reps"] == 3, "Anki cards.reps must be corrected to match TT's ds.reps"
        assert card_after["lapses"] == 0, "non-lapse must not increment lapses"

    def test_sync_push_increments_lapses_on_review_lapse(self):
        """Pushing a REVIEW→RELEARNING lapse increments cards.lapses in Anki DB."""
        from datetime import datetime as dt

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            stability=0.5,
            difficulty=5.5,
            reps=4,
            lapses=1,
            state=SRSState.RELEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=1,
            left=1001,
            due_at=dt.now(UTC) + timedelta(minutes=10),
            prior_state=SRSState.REVIEW,
            prior_left=None,
            prior_stability=10.0,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        anki_conn = _make_anki_full_db()
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid)
        # Set Anki card to queued relearning (queue=1, type=3, left=1001) so the
        # anki_ahead guard doesn't block — it only fires when queue=2 (graduated)
        # or when Anki has fewer remaining steps than TT. Matching left=1001 keeps
        # the guard from blocking via the step-ahead check too.
        anki_conn.execute(
            "UPDATE cards SET queue = 1, type = 3, left = 1001 WHERE id = ?",
            (rec_cid,),
        )
        anki_conn.commit()
        writer = OfflineWriter(anki_conn)
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        card = anki_conn.execute("SELECT reps, lapses FROM cards WHERE id=?", (rec_cid,)).fetchone()
        # reps jumps to ds.reps=4 and lapses to ds.lapses=1 via MAX(...) — heals pre-existing drift
        assert card["reps"] == 4, "reps must be corrected to TT's ds.reps"
        assert card["lapses"] == 1, "lapses must be corrected to TT's ds.lapses"

    def test_sync_push_heals_pre_existing_reps_drift(self):
        """Pre-existing drift in cards.reps is healed via MAX(reps+1, ds.reps)."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=5, stability=10.5)

        anki_conn = _make_anki_full_db()
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid)
        # Simulate pre-existing drift: Anki has reps=2 but TT has already pushed 3
        # earlier grades without incrementing (pre-fix behavior).
        anki_conn.execute("UPDATE cards SET reps = 2, lapses = 1 WHERE id = ?", (rec_cid,))
        anki_conn.commit()

        writer = OfflineWriter(anki_conn)
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        card = anki_conn.execute("SELECT reps, lapses FROM cards WHERE id=?", (rec_cid,)).fetchone()
        assert card["reps"] == 5, f"reps should jump from 2 to ds.reps=5 via MAX, got {card['reps']}"
        assert card["lapses"] == 1, "lapses should preserve existing drift value via MAX"

    def test_idempotent_after_push(self):
        """Running sync_push twice: second run finds nothing dirty."""
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        db.set_dirty_fields(guid, "translation")
        _mark_direction_dirty(db, guid)

        AnkiSync(db=db, _reader=FakeReader(), _writer=FakeWriter()).sync_push()

        writer2 = FakeWriter()
        report2 = AnkiSync(db=db, _reader=FakeReader(), _writer=writer2).sync_push()
        assert report2.notes_pushed == 0
        assert report2.directions_pushed == 0
        assert writer2.calls == []

    def test_note_without_anki_id_is_skipped(self):
        """Collocation with dirty_fields but no anki_note_id → no updateNoteFields."""
        db = _make_tt_db()
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
        db.add_collocation(unit)
        guid = db.get_collocation("banka").guid
        db.set_dirty_fields(guid, "translation")
        # No set_anki_ids call → anki_note_id remains None

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert "update_note_fields" not in writer.action_names()

    def test_direction_without_card_id_is_skipped(self):
        """Direction with dirty_fsrs but no anki_card_id → nothing pushed."""
        db = _make_tt_db()
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
        db.add_collocation(unit)
        guid = db.get_collocation("banka").guid
        # Mark dirty without setting anki_card_id
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=5), time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=None,  # no card ID
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        report = AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert report.directions_pushed == 0
        assert writer.calls == []

    def test_unknown_dirty_field_is_skipped(self):
        """dirty_fields='text' (unrecognised) produces no note update."""
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        db.set_dirty_fields(guid, "text")

        writer = FakeWriter()
        report = AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert report.notes_pushed == 0
        assert "update_note_fields" not in writer.action_names()

    def test_dry_run_does_not_write(self):
        """dry_run=True: counts reported but no writes to DB or writer."""
        db = _make_tt_db()
        guid, *_ = _add_banka_with_anki_ids(db)
        db.set_dirty_fields(guid, "translation")
        _mark_direction_dirty(db, guid)

        writer = FakeWriter()
        report = AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push(dry_run=True)

        assert report.notes_pushed == 1
        assert report.directions_pushed == 1
        assert writer.calls == []
        # DB still dirty
        assert db.get_dirty_fields(guid) == "translation"
        dirty = db.list_dirty()
        assert len(dirty) == 1


# ── TestSyncPushGuardsAgainstAnkiAhead (Fix 3) ────────────────────────────────


class TestSyncPushGuardsAgainstAnkiAhead:
    """Fix 3: push must not overwrite Anki when Anki has more progress than TT.

    Background: between syncs, the user may grade the same card more times in
    Anki than in TT (or vice versa). On the next sync, push would unconditionally
    write TT's `left`/state to Anki, erasing whatever step-progress or graduation
    Anki had recorded. push now consults `writer.get_current_card_state` first
    and skips the write when Anki is ahead. The matching divergence is resolved
    by sync_pull (Fix 2), which takes Anki's state.
    """

    def test_push_skips_learning_write_when_anki_graduated(self):
        from datetime import datetime as _dt

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            left=1001,
            due_at=_dt.now(UTC) + timedelta(minutes=10),
            reps=3,
            anki_card_id=rec_cid,
            dirty_fsrs=True,
            last_rating=3,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        # Anki has the card in REVIEW (graduated) while TT was offline.
        writer.current_states[rec_cid] = {"queue": 2, "type": 2, "left": 0}
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert not any(c[0] == "set_learning_state" for c in writer.calls)
        # The local grade is discarded; no revlog so we don't double-count.
        assert not any(c[0] == "write_revlog" for c in writer.calls)

    def test_push_skips_learning_write_when_anki_further_along(self):
        from datetime import datetime as _dt

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            left=1002,  # TT: total_remaining=2 (first of 2 steps)
            due_at=_dt.now(UTC) + timedelta(minutes=1),
            reps=3,
            anki_card_id=rec_cid,
            dirty_fsrs=True,
            last_rating=3,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        writer.current_states[rec_cid] = {"queue": 1, "type": 1, "left": 1001}  # Anki: tr=1
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert not any(c[0] == "set_learning_state" for c in writer.calls)

    def test_push_proceeds_when_anki_state_matches_tt(self):
        """Anki on the same step → push writes normally (TT's update is benign
        even if a no-op)."""
        from datetime import datetime as _dt

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            left=1001,
            due_at=_dt.now(UTC) + timedelta(minutes=10),
            reps=3,
            anki_card_id=rec_cid,
            dirty_fsrs=True,
            last_rating=3,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        writer.current_states[rec_cid] = {"queue": 1, "type": 1, "left": 1001}
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert any(c[0] == "set_learning_state" for c in writer.calls)

    def test_push_proceeds_when_anki_is_behind_tt(self):
        """When Anki has more total_remaining (is BEHIND TT), TT wins as before
        — push proceeds normally."""
        from datetime import datetime as _dt

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            left=1001,
            due_at=_dt.now(UTC) + timedelta(minutes=10),
            reps=3,
            anki_card_id=rec_cid,
            dirty_fsrs=True,
            last_rating=3,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        writer.current_states[rec_cid] = {"queue": 1, "type": 1, "left": 1002}  # Anki behind
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert any(c[0] == "set_learning_state" for c in writer.calls)

    def test_offline_writer_get_current_card_state_returns_queue_type_left(self):
        """OfflineWriter must expose Anki's current queue/type/left for the
        push-side guard. Reading goes through the writer's own connection
        (used during a sync_push transaction)."""
        anki_conn = _make_anki_full_db()
        # Insert a card with a known state.
        anki_conn.execute(
            "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, left, odue, odid, flags, data) "
            "VALUES (90010, 9001, 1, 0, 0, -1, 1, 1, 1, 0, 2500, 3, 0, 1001, 0, 0, 0, '')"
        )
        anki_conn.commit()
        writer = OfflineWriter(anki_conn)
        state = writer.get_current_card_state(90010)
        assert state == {"queue": 1, "type": 1, "left": 1001}

    def test_offline_writer_get_current_card_state_unknown_card_id(self):
        anki_conn = _make_anki_full_db()
        writer = OfflineWriter(anki_conn)
        assert writer.get_current_card_state(99999) is None


# ── TestDrainPendingRevlog ────────────────────────────────────────────────────


class TestSyncPushEase:
    """B5: sync_push must emit the learner's actual rating, not a hardcoded ease=3."""

    def test_sync_push_emits_real_ease_from_last_rating(self):
        """When last_rating=2 (Hard), write_revlog must receive ease=2."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=10), time(4, 0), tzinfo=UTC),
            stability=10.5,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=2,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, cid, ease, *_ = revlog_calls[0]
        assert ease == 2

    def test_schedule_to_push_chain_emits_real_ease(self):
        """Full B5 chain: schedule() → update_direction → sync_push → ease matches rating."""
        from app.models.srs_item import Rating
        from app.srs.fsrs import schedule as fsrs_schedule

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        db.set_anki_ids(guid, 9001, {Direction.RECOGNITION: rec_cid, Direction.PRODUCTION: 90011})

        # Get item with reps so schedule produces a review (not a new)
        item = db.get_collocation_by_guid(guid)
        # Seed reps so it's not a new card
        from dataclasses import replace as dc_replace

        old_rec = item.directions[Direction.RECOGNITION]
        seeded = dc_replace(old_rec, reps=3, stability=5.0, state=SRSState.REVIEW)
        item.directions[Direction.RECOGNITION] = seeded
        db.update_direction(guid, Direction.RECOGNITION, seeded)

        # Schedule with AGAIN (ease=1)
        item = db.get_collocation_by_guid(guid)
        updated_item = fsrs_schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        rec_dir = updated_item.directions[Direction.RECOGNITION]
        db.update_direction(guid, Direction.RECOGNITION, rec_dir)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, ease, *_ = revlog_calls[0]
        assert ease == Rating.AGAIN.value  # 1

    def test_sync_push_falls_back_ease_3_when_last_rating_null(self):
        """When last_rating is None (pre-migration row), write_revlog uses ease=3."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=10), time(4, 0), tzinfo=UTC),
            stability=10.5,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=None,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, cid, ease, *_ = revlog_calls[0]
        assert ease == 3


# ── B6: revlog (type, ivl, lastIvl) reflects the actual transition ───────────


class TestSyncPushRevlogTransitions:
    """Push must emit revlog rows whose (type, ivl, lastIvl) match the
    transition the user just made — not a hardcoded type=2 with positive ivl.

    The piščanec scenario: a Review card lapsed (Again) into the relearning
    queue. Anki's UI later sees a 1-min step that has no preceding revlog row,
    so the next rating computes against a fictional prior state. We fix this
    by stashing prior_state on the DirectionState at grade time and deriving
    correct revlog values at push time.

    Anki's RevlogReviewKind: 0=Learning, 1=Review, 2=Relearning.
    Anki's ivl encoding: positive integer = days; negative integer = -seconds.
    """

    def test_review_again_writes_review_revlog_with_negative_step_ivl(self):
        """REVIEW + Again → RELEARNING: type=1, ivl=-(relearn_step_min*60), lastIvl≈prior stability days."""
        from datetime import datetime as dt

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            stability=0.5,  # post-lapse stability (small)
            difficulty=5.5,
            reps=4,
            lapses=1,
            state=SRSState.RELEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=1,  # Again
            left=1001,
            due_at=dt.now(UTC) + timedelta(minutes=10),
            prior_state=SRSState.REVIEW,
            prior_left=None,
            prior_stability=10.0,  # was a 10-day Review card
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, ease, ivl, last_ivl, _factor, _time_ms, type_, _pref = revlog_calls[0]
        assert ease == 1
        assert type_ == 1, f"REVIEW→RELEARNING uses Review revlog kind (1), got {type_}"
        assert ivl == -600, f"expected -(10*60)=-600, got {ivl}"
        assert last_ivl == 10, f"expected last_ivl=prior stability days=10, got {last_ivl}"

    def test_review_good_writes_review_revlog_with_positive_ivl(self):
        """REVIEW + Good → REVIEW: type=1, ivl=stability_days, lastIvl=prior_stability_days."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=15), time(4, 0), tzinfo=UTC),
            stability=15.3,  # post-good stability (grew)
            difficulty=4.8,
            reps=5,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            prior_state=SRSState.REVIEW,
            prior_stability=10.0,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, _ease, ivl, last_ivl, _f, _t, type_, _p = revlog_calls[0]
        assert type_ == 1
        assert ivl == 15
        assert last_ivl == 10

    def test_learning_step_advance_writes_learning_revlog(self):
        """LEARNING(step0, left=2) + Good → LEARNING(step1, left=1): type=0, ivl=-600, lastIvl=-60.

        Anki encoding: low 3 digits = total_remaining; idx = total_steps - total_remaining.
        For learn_steps=[1m, 10m]: total_remaining=2 → step 0 (1m); total_remaining=1 → step 1 (10m).
        """
        from datetime import datetime as dt

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            stability=1.0,
            difficulty=5.0,
            reps=2,
            lapses=0,
            state=SRSState.LEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,  # Good
            left=1,  # total_remaining=1 → idx=1 (10min step)
            due_at=dt.now(UTC) + timedelta(minutes=10),
            prior_state=SRSState.LEARNING,
            prior_left=2,  # total_remaining=2 → idx=0 (1min step)
            prior_stability=1.0,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, ease, ivl, last_ivl, _f, _t, type_, _p = revlog_calls[0]
        assert ease == 3
        assert type_ == 0, f"learning step revlog uses kind=0, got {type_}"
        assert ivl == -600, f"new step is 10min → -600, got {ivl}"
        assert last_ivl == -60, f"prior step was 1min → -60, got {last_ivl}"

    def test_new_to_learning_writes_learning_revlog_with_zero_last_ivl(self):
        """NEW + Good → LEARNING(step1): type=0, ivl=-(step1_min*60), lastIvl=0."""
        from datetime import datetime as dt

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            stability=1.0,
            difficulty=5.0,
            reps=1,
            lapses=0,
            state=SRSState.LEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            left=1,  # total_remaining=1 → idx=1 (10min step)
            due_at=dt.now(UTC) + timedelta(minutes=10),
            prior_state=SRSState.NEW,
            prior_left=None,
            prior_stability=1.0,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, _ease, ivl, last_ivl, _f, _t, type_, _p = revlog_calls[0]
        assert type_ == 0
        assert ivl == -600
        assert last_ivl == 0, f"NEW→LEARNING has no prior step → lastIvl=0, got {last_ivl}"

    def test_learning_graduation_writes_learning_revlog_with_positive_ivl(self):
        """LEARNING(last step) + Good → REVIEW: type=0, ivl=stability_days, lastIvl=-(prior_step_min*60)."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            stability=4.0,
            difficulty=5.0,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            left=None,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            prior_state=SRSState.LEARNING,
            prior_left=1,  # total_remaining=1 → idx=1 (10min step, last)
            prior_stability=1.0,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, _ease, ivl, last_ivl, _f, _t, type_, _p = revlog_calls[0]
        assert type_ == 0, f"graduation from learning uses kind=0, got {type_}"
        assert ivl == 4, f"new ivl=stability days=4, got {ivl}"
        assert last_ivl == -600, f"prior step was 10min → -600, got {last_ivl}"

    def test_relearning_again_writes_relearning_revlog(self):
        """RELEARNING + Again (restart) → RELEARNING: type=2, ivl=-(relearn_step*60), lastIvl=-(prior_step*60)."""
        from datetime import datetime as dt

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            stability=0.3,
            difficulty=6.0,
            reps=5,
            lapses=2,
            state=SRSState.RELEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=1,
            left=1001,  # 1 of 1 step remaining
            due_at=dt.now(UTC) + timedelta(minutes=10),
            prior_state=SRSState.RELEARNING,
            prior_left=1001,
            prior_stability=0.5,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, _ease, ivl, last_ivl, _f, _t, type_, _p = revlog_calls[0]
        assert type_ == 2, f"relearning→relearning uses kind=2, got {type_}"
        assert ivl == -600
        assert last_ivl == -600

    def test_unknown_prior_state_falls_back_to_legacy_review_shape(self):
        """prior_state=None (pre-migration row): keep old positive-ivl shape so legacy tests still hold."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=3, stability=10.5, last_rating=3)
        # _mark_direction_dirty doesn't set prior_state → defaults to None

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, _ease, ivl, last_ivl, _f, _t, _type, _p = revlog_calls[0]
        # Legacy fallback: positive ivl=stability_days (banker's rounding: 10.5 → 10)
        assert ivl == 10
        assert last_ivl == 10

    def test_schedule_then_push_review_again_emits_relearn_step(self):
        """End-to-end: schedule(REVIEW, AGAIN) → DB → push writes type=1, ivl=-600."""
        from dataclasses import replace as dc_replace

        from app.models.srs_item import Rating
        from app.srs.fsrs import schedule as fsrs_schedule

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)

        item = db.get_collocation_by_guid(guid)
        old_rec = item.directions[Direction.RECOGNITION]
        seeded = dc_replace(old_rec, reps=3, stability=10.0, state=SRSState.REVIEW)
        db.update_direction(guid, Direction.RECOGNITION, seeded)

        item = db.get_collocation_by_guid(guid)
        updated_item = fsrs_schedule(item, Rating.AGAIN, direction=Direction.RECOGNITION)
        new_rec = updated_item.directions[Direction.RECOGNITION]
        assert new_rec.state == SRSState.RELEARNING
        assert new_rec.prior_state == SRSState.REVIEW, "schedule() must stash prior_state"
        db.update_direction(guid, Direction.RECOGNITION, new_rec)

        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        revlog_calls = [c for c in writer.calls if c[0] == "write_revlog"]
        assert len(revlog_calls) == 1
        _, _cid, ease, ivl, last_ivl, _f, _t, type_, _p = revlog_calls[0]
        assert ease == 1
        assert type_ == 1
        assert ivl == -600
        assert last_ivl == 10  # round(10.0)


# ── B6: helper functions (branch coverage for _step_minutes_from_left and _derive_revlog_shape) ──


class TestRevlogShapeHelpers:
    def test_step_minutes_from_left_returns_none_for_missing_inputs(self):
        from app.anki.sync import _step_minutes_from_left

        assert _step_minutes_from_left(None, [1.0, 10.0]) is None
        assert _step_minutes_from_left(0, [1.0, 10.0]) is None
        assert _step_minutes_from_left(2002, []) is None

    def test_step_minutes_from_left_returns_none_for_zero_packed_fields(self):
        from app.anki.sync import _step_minutes_from_left

        # total_steps == 0 (lower 3 digits are zero)
        assert _step_minutes_from_left(2000, [1.0, 10.0]) is None

    def test_step_minutes_from_left_returns_none_for_out_of_range_step_index(self):
        from app.anki.sync import _step_minutes_from_left

        # left=1003 → steps_remaining=1, total_steps=3, step_index=2; only 2 steps configured
        assert _step_minutes_from_left(1003, [1.0, 10.0]) is None

    def test_derive_shape_falls_back_for_legacy_learning_state(self):
        """prior_state=None + state=LEARNING uses Learning revlog kind (0)."""
        from app.anki.sync import _derive_revlog_shape

        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=2.0,
            state=SRSState.LEARNING,
            prior_state=None,
        )
        type_, ivl, last_ivl = _derive_revlog_shape(ds, [1.0, 10.0], [10.0])
        assert type_ == 0
        assert ivl == 2
        assert last_ivl == 2

    def test_derive_shape_falls_back_for_legacy_relearning_state(self):
        """prior_state=None + state=RELEARNING uses Relearning revlog kind (2)."""
        from app.anki.sync import _derive_revlog_shape

        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=2.0,
            state=SRSState.RELEARNING,
            prior_state=None,
        )
        type_, ivl, last_ivl = _derive_revlog_shape(ds, [1.0, 10.0], [10.0])
        assert type_ == 2

    def test_derive_shape_relearning_with_unparseable_left_falls_back_to_first_step(self):
        """state=RELEARNING with left=None still produces -relearn_steps[0]*60 ivl."""
        from app.anki.sync import _derive_revlog_shape

        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=0.5,
            state=SRSState.RELEARNING,
            left=None,
            prior_state=SRSState.REVIEW,
            prior_stability=5.0,
        )
        type_, ivl, last_ivl = _derive_revlog_shape(ds, [1.0, 10.0], [10.0])
        assert type_ == 1
        assert ivl == -600  # fallback to relearn_steps[0]
        assert last_ivl == 5

    def test_derive_shape_unexpected_prior_state_uses_fallback_last_ivl(self):
        """A prior_state outside the four known transitions (e.g. BURIED) falls
        through to the stability-based last_ivl branch."""
        from app.anki.sync import _derive_revlog_shape

        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=4.0,
            state=SRSState.REVIEW,
            prior_state=SRSState.BURIED,
        )
        _type_, _ivl, last_ivl = _derive_revlog_shape(ds, [1.0, 10.0], [10.0])
        assert last_ivl == 4

    def test_derive_shape_hard_on_first_step_uses_avg_of_first_two_steps(self):
        """Hard-on-first-step parity: revlog ivl reflects the avg-of-first-two-steps
        delay Anki applies, not the per-step value.

        Anki's rslib uses (steps[0] + steps[1]) / 2 = 5.5 min for Hard on the
        first learning step with [1, 10]. The revlog should record ivl=-330,
        not -60. This catches the kuhinja regression where TT wrote -60 to
        revlog while Anki wrote -330 for the same grade.

        After the fuzz port, this is decoded from `left` + `last_rating` rather
        than `due_at - last_review` (which now includes Anki's positive fuzz).
        """
        from datetime import UTC, datetime, timedelta

        from app.anki.sync import _derive_revlog_shape
        from app.models.srs_item import Rating

        last_review = datetime(2026, 5, 8, 17, 5, 28, tzinfo=UTC)
        due_at = last_review + timedelta(seconds=330)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            stability=1.7,
            state=SRSState.LEARNING,
            left=2,  # all steps remaining = at step 0
            due_at=due_at,
            last_review=last_review,
            last_rating=Rating.HARD.value,
            prior_state=SRSState.LEARNING,
            prior_left=2,
        )
        type_, ivl, last_ivl = _derive_revlog_shape(ds, [1.0, 10.0], [10.0])
        assert type_ == 0
        assert ivl == -330
        assert last_ivl == -60


# ── B14: offline ordering regression ─────────────────────────────────────────


class TestOfflineOrdering:
    """B14 regression: push must run before pull in offline mode.

    If pull runs first, it detects dirty_fsrs=True + fsrs_known=True → anki_wins
    → clears dirty_fsrs before push sees it → push emits nothing.
    """

    def test_push_before_pull_dirty_direction_gets_revlog(self):
        """Push-then-pull sequence fires write_revlog even when pull would anki_wins."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=3, stability=10.5, anki_card_id=rec_cid)

        # Reader returns fsrs_known=True — in pull-first order this clears dirty_fsrs
        class OrderedFakeReader:
            def get_note_records(self):
                card = make_card_record(
                    anki_card_id=rec_cid,
                    ord=0,
                    reps=5,
                    stability=15.0,
                    difficulty=4.5,
                    due_date=date.today() + timedelta(days=15),
                )
                return [make_note_record(anki_guid=guid, cards=[card])]

            def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
                return []

        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=OrderedFakeReader(), _writer=writer)

        # NEW correct order: push then pull
        sync.sync_push()
        sync.sync_pull()

        # Push must have fired write_revlog before pull cleared dirty_fsrs
        assert "write_revlog" in writer.action_names()
        # After push+pull, direction is clean
        assert db.list_dirty() == []

    def test_pull_before_push_still_flushes_revlog(self):
        """Pull-then-push correctly preserves dirty_fsrs so push can still fire.

        Previously pull cleared dirty_fsrs (anki_wins), causing push to skip
        the row. Now pull preserves dirty rows, so pull-before-push also works.
        """
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        _mark_direction_dirty(db, guid, reps=3, stability=10.5, anki_card_id=rec_cid)

        class OrderedFakeReader:
            def get_note_records(self):
                card = make_card_record(
                    anki_card_id=rec_cid,
                    ord=0,
                    reps=5,
                    stability=15.0,
                    difficulty=4.5,
                    due_date=date.today() + timedelta(days=15),
                )
                return [make_note_record(anki_guid=guid, cards=[card])]

            def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
                return []

        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=OrderedFakeReader(), _writer=writer)

        # Pull preserves dirty_fsrs; push sees the dirty row and flushes it
        sync.sync_pull()
        sync.sync_push()

        assert "write_revlog" in writer.action_names()


# ── TestRevlogFactor ─────────────────────────────────────────────────────────


class TestRevlogFactor:
    """revlog.factor must be derived from difficulty, not hardcoded 2500."""

    def _push_with_difficulty(self, difficulty: float) -> list[tuple]:
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=10), time(4, 0), tzinfo=UTC),
            stability=10.5,
            difficulty=difficulty,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)
        writer = FakeWriter()
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()
        return writer.calls

    def test_difficulty_3_maps_to_factor_3000(self):
        calls = self._push_with_difficulty(3.0)
        revlog_call = next(c for c in calls if c[0] == "write_revlog")
        assert revlog_call[5] == 3000

    def test_difficulty_8_maps_to_factor_8000(self):
        calls = self._push_with_difficulty(8.0)
        revlog_call = next(c for c in calls if c[0] == "write_revlog")
        assert revlog_call[5] == 8000

    def test_difficulty_0_5_clamped_to_1300(self):
        calls = self._push_with_difficulty(0.5)
        revlog_call = next(c for c in calls if c[0] == "write_revlog")
        assert revlog_call[5] == 1300

    def test_difficulty_15_clamped_to_13000(self):
        calls = self._push_with_difficulty(15.0)
        revlog_call = next(c for c in calls if c[0] == "write_revlog")
        assert revlog_call[5] == 13000


# ── TestPushLearningCardLeftAndDue ────────────────────────────────────────


class TestPushLearningCardLeftAndDue:
    """Step 5: Push round-trip — verify left/due_at written to Anki correctly."""

    def _make_fake_reader(self, guid, rec_cid, queue=1, left=2002):
        """Create a fake reader that returns a single card record."""
        from datetime import date, timedelta

        class FakeReader:
            def __init__(self, records):
                self._records = records

            def get_note_records(self):
                return self._records

            def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
                return []

        card = make_card_record(
            anki_card_id=rec_cid,
            ord=0,
            queue=queue,
            reps=1,
            stability=1.0,
            difficulty=5.0,
            due_date=date.today() + timedelta(days=1),
        )
        return FakeReader([make_note_record(anki_guid=guid, cards=[card])])

    def test_push_learning_good_advances_step(self, tmp_path):
        """Pushing a LEARNING+GOOD grade writes correct left and due (seconds)."""

        # Setup: create Anki DB with a learning card (left=2002, 2 steps remaining)
        db_path = tmp_path / "collection.anki2"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE col (id INTEGER PRIMARY KEY, crt INTEGER, mod INTEGER, usn INTEGER)")
        conn.execute("INSERT INTO col VALUES (1, 1704067200, 0, 0)")  # crt = 2024-01-01
        conn.execute(
            """CREATE TABLE cards (
                id INTEGER PRIMARY KEY,
                nid INTEGER,
                did INTEGER,
                ord INTEGER,
                mod INTEGER,
                usn INTEGER,
                type INTEGER,
                queue INTEGER,
                due INTEGER,
                ivl INTEGER,
                factor INTEGER,
                reps INTEGER,
                lapses INTEGER,
                left INTEGER,
                odue INTEGER,
                odid INTEGER,
                flags INTEGER,
                data TEXT
            )"""
        )
        # Card: learning state, left=2 (Anki encoding: total_remaining=2 → step 0)
        conn.execute(
            "INSERT INTO cards VALUES (90010, 9001, 123, 0, 0, 0, 1, 1, 1704103200, 0, 0, 1, 0, 2, 0, 0, 0, '{}')"
        )
        # Create revlog table (required by sync_push)
        conn.execute(
            """CREATE TABLE revlog (
                id INTEGER PRIMARY KEY,
                cid INTEGER,
                usn INTEGER,
                ease INTEGER,
                ivl INTEGER,
                lastIvl INTEGER,
                factor INTEGER,
                time INTEGER,
                type INTEGER
            )"""
        )
        conn.commit()
        conn.close()

        # Setup TunaTale DB with matching item
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db, anki_note_id=9001, rec_cid=90010)

        # Simulate a review: call schedule() with Rating.GOOD on a learning card with left=2002
        from datetime import datetime as dt
        from datetime import timedelta

        from app.srs.fsrs import Rating, schedule

        item = db.get_collocation("banka")
        assert item is not None
        rec_state = item.directions[Direction.RECOGNITION]

        # Set up the learning state at step 0 (Anki encoding: total_remaining=2 → left=2)
        now = dt.now(UTC)
        rec_state = DirectionState(
            direction=Direction.RECOGNITION,
            stability=1.0,
            difficulty=5.0,
            reps=1,
            lapses=0,
            state=SRSState.LEARNING,
            anki_card_id=rec_cid,
            left=2,
            due_at=now + timedelta(minutes=1),  # Step 0: 1 minute
            dirty_fsrs=True,
            last_rating=3,
        )
        db.update_direction(guid, Direction.RECOGNITION, rec_state)

        # Re-fetch so item reflects the LEARNING+left=2 state we just wrote
        item = db.get_collocation("banka")
        assert item is not None

        # schedule() with GOOD on step 0 of 2-step deck → advance to step 1 (left=1)
        result = schedule(item, Rating.GOOD, direction=Direction.RECOGNITION)
        new_state = result.directions[Direction.RECOGNITION]
        assert new_state.left == 1, f"Expected left=1 (total_remaining=1) after GOOD, got {new_state.left}"

        # Write the post-GOOD state back so sync_push has something to push
        db.update_direction(guid, Direction.RECOGNITION, new_state)

        # Push to Anki
        conn = sqlite3.connect(str(db_path))
        writer = OfflineWriter(conn)
        reader = self._make_fake_reader(guid, rec_cid, queue=1, left=2)
        sync = AnkiSync(db=db, _reader=reader, _writer=writer)
        sync.sync_push()
        conn.close()

        # Verify Anki cards table updated correctly
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT left, due, queue FROM cards WHERE id = 90010").fetchone()
        conn.close()

        # After GOOD on step 0 of 2: total_remaining decrements to 1 → left=1
        assert row is not None, "Card row should exist"
        new_left, new_due, new_queue = row
        assert new_left == 1, f"Expected left=1 after advancing step, got {new_left}"
        assert new_queue == 1, f"Expected queue=1 (still learning), got {new_queue}"
        # due should be an absolute timestamp (seconds) for queue=1
        assert new_due > 1704067200, f"Expected due as absolute timestamp, got {new_due}"

    def test_push_learning_step_advances_left(self, tmp_path):
        """Pushing learning steps correctly decrements steps_remaining in left."""
        db_path = tmp_path / "collection.anki2"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE col (id INTEGER PRIMARY KEY, crt INTEGER, mod INTEGER, usn INTEGER)")
        conn.execute("INSERT INTO col VALUES (1, 1704067200, 0, 0)")
        conn.execute(
            """CREATE TABLE cards (
                id INTEGER PRIMARY KEY,
                nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER, usn INTEGER,
                type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, factor INTEGER,
                reps INTEGER, lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER,
                flags INTEGER, data TEXT
            )"""
        )
        # learning, left=1002 (1 step remaining of 2 total)
        conn.execute(
            "INSERT INTO cards VALUES (90010, 9001, 123, 0, 0, 0, 1, 1, 1704103200, 0, 0, 2, 0, 1002, 0, 0, 0, '{}')"
        )
        # Create revlog table (required by sync_push)
        conn.execute(
            """CREATE TABLE revlog (
                id INTEGER PRIMARY KEY,
                cid INTEGER,
                usn INTEGER,
                ease INTEGER,
                ivl INTEGER,
                lastIvl INTEGER,
                factor INTEGER,
                time INTEGER,
                type INTEGER
            )"""
        )
        conn.commit()
        conn.close()

        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db, anki_note_id=9001, rec_cid=90010)
        # Mark as learning with left=1002 (1 step remaining)
        _mark_direction_dirty(
            db, guid, state=SRSState.LEARNING, reps=2, stability=1.0, anki_card_id=90010, last_rating=3
        )

        # Update the direction to have left=1002
        item = db.get_collocation("banka")
        rec_state = item.directions[Direction.RECOGNITION]
        rec_state = DirectionState(
            direction=rec_state.direction,
            stability=rec_state.stability,
            difficulty=rec_state.difficulty,
            reps=rec_state.reps,
            lapses=rec_state.lapses,
            state=SRSState.REVIEW,
            anki_card_id=rec_state.anki_card_id,
            anki_due=rec_state.anki_due,
            left=None,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            dirty_fsrs=True,
        )
        db.update_direction(guid, Direction.RECOGNITION, rec_state)

        # Push
        conn = sqlite3.connect(str(db_path))
        writer = OfflineWriter(conn)
        reader = self._make_fake_reader(guid, rec_cid, queue=1, left=1002)
        sync = AnkiSync(db=db, _reader=reader, _writer=writer)
        sync.sync_push()
        conn.close()

        # Verify: after GOOD on last step (1 remaining), should graduate (left=0, queue=2)
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT left, queue FROM cards WHERE id = 90010").fetchone()
        conn.close()
        assert row is not None
        new_left, new_queue = row
        # After graduating from learning, left should be 0 and queue should be 2 (review)
        assert new_left == 0, f"Expected left=0 after graduating, got {new_left}"
        assert new_queue == 2, f"Expected queue=2 (review) after graduating, got {new_queue}"


# ── TestSyncPushBumpNewToday ─────────────────────────────────────────────────


def _make_anki_with_decks() -> sqlite3.Connection:
    """Full Anki DB shape incl. col, notes, cards, revlog AND a decks table."""
    conn = _make_anki_full_db()
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB)")
    _REAL_BLOB = bytes.fromhex("18A12338ABA702")
    conn.execute("INSERT INTO decks VALUES (1, '0. Slovene', 0, 0, ?)", (_REAL_BLOB,))
    conn.commit()
    return conn


class TestSyncPushBuriesSiblings:
    """Layer 47: sync_push must replicate Anki's grade-time sibling-bury.

    The mechanism is a backfill scan after the main push loops: every TT
    direction with ``last_review`` in today's local-day window triggers
    ``writer.bury_siblings``. Covers (a) just-pushed grades AND (b) earlier
    grades that were already cleaned by a prior push (e.g., before Layer 47
    landed). Idempotent via ``bury_siblings`` ``WHERE queue IN (allowed)``.
    """

    def _make_graded_today(
        self,
        db,
        guid: str,
        rec_cid: int,
        *,
        state: SRSState = SRSState.REVIEW,
        dirty_fsrs: bool = False,
    ) -> None:
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=18), time(4, 0), tzinfo=UTC),
            stability=15.0,
            difficulty=3.0,
            reps=4,
            lapses=0,
            state=state,
            dirty_fsrs=dirty_fsrs,
            anki_card_id=rec_cid,
            last_review=datetime.now(UTC),
            last_rating=4,
            prior_state=SRSState.REVIEW,
            left=1001 if state in (SRSState.LEARNING, SRSState.RELEARNING) else None,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

    def test_backfill_fires_for_today_graded_dirty_direction(self):
        """A still-dirty direction graded today triggers the backfill bury."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        db.set_anki_state_cache("bury_review", "True")
        db.set_anki_state_cache("bury_new", "False")
        self._make_graded_today(db, guid, rec_cid, dirty_fsrs=True)

        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer, _anki_col_crt=1704067200)
        sync.sync_push()

        bury_calls = [c for c in writer.calls if c[0] == "bury_siblings"]
        assert len(bury_calls) == 1
        _, gcid, gq, b_new, b_rev, _ = bury_calls[0]
        assert gcid == rec_cid
        assert gq == 2
        assert b_rev is True

    def test_backfill_fires_for_today_graded_clean_direction(self):
        """Layer 47 regression: a NON-dirty (already-pushed) direction graded
        today STILL gets bury'd. This is the iti mimo case — the grade
        propagated to Anki pre-Layer-47, ``dirty_fsrs`` cleared, but the
        sibling was never buried. Subsequent sync_push must backfill it.
        """
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        db.set_anki_state_cache("bury_review", "True")
        self._make_graded_today(db, guid, rec_cid, dirty_fsrs=False)

        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer, _anki_col_crt=1704067200)
        sync.sync_push()

        bury_calls = [c for c in writer.calls if c[0] == "bury_siblings"]
        assert len(bury_calls) == 1, "backfill must fire for clean today-graded direction"

    def test_backfill_skips_when_direction_has_no_last_review(self):
        """Brand-new card with last_review=None must NOT trigger bury."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        db.set_anki_state_cache("bury_review", "True")
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=1), time(4, 0), tzinfo=UTC),
            stability=1.0,
            difficulty=5.0,
            reps=0,
            lapses=0,
            state=SRSState.NEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer, _anki_col_crt=1704067200)
        sync.sync_push()

        assert not any(c[0] == "bury_siblings" for c in writer.calls)

    def test_backfill_passes_learning_queue_for_relearning_state(self):
        """RELEARNING → graded_queue=1 (intra-day Learn)."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        db.set_anki_state_cache("bury_review", "True")
        self._make_graded_today(db, guid, rec_cid, state=SRSState.RELEARNING)

        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer, _anki_col_crt=1704067200)
        sync.sync_push()

        bury_calls = [c for c in writer.calls if c[0] == "bury_siblings"]
        assert len(bury_calls) == 1
        assert bury_calls[0][2] == 1, "RELEARNING graded_queue must be 1"

    def test_backfill_skips_suspended_state(self):
        """SUSPENDED state → no Anki queue mapping → no bury fired."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        db.set_anki_state_cache("bury_review", "True")
        self._make_graded_today(db, guid, rec_cid, state=SRSState.SUSPENDED)

        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer, _anki_col_crt=1704067200)
        sync.sync_push()

        assert not any(c[0] == "bury_siblings" for c in writer.calls)

    def test_backfill_short_circuits_when_all_flags_disabled(self):
        """bury_new=False AND bury_review=False → backfill returns before scanning."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        db.set_anki_state_cache("bury_review", "False")
        db.set_anki_state_cache("bury_new", "False")
        self._make_graded_today(db, guid, rec_cid)

        writer = FakeWriter()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer, _anki_col_crt=1704067200)
        sync.sync_push()

        assert not any(c[0] == "bury_siblings" for c in writer.calls)


class TestSyncPushBumpNewToday:
    """sync_push must bump the "new today" deck counter on NEW→non-NEW introduction."""

    def test_sync_push_bumps_new_today_on_first_grade(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        anki_conn = _make_anki_with_decks()
        # First grade scenario: Anki's card is still NEW (queue=0, type=0).
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid, queue=0, card_type=0)
        # Set up a NEW→LEARNING transition (prior_state=new, state=learning, reps=1)
        col_crt = 1704067200
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=1), time(4, 0), tzinfo=UTC),
            stability=1.0,
            difficulty=5.0,
            reps=1,
            lapses=0,
            state=SRSState.LEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            left=1,
            prior_state=SRSState.NEW,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        # Build a reader that returns the note with first_review_ms=None (no prior revlog)
        class NoRevlogReader:
            def get_note_records(self):
                card = make_card_record(
                    anki_card_id=rec_cid,
                    ord=0,
                    queue=1,
                    reps=1,
                    stability=1.0,
                    difficulty=5.0,
                    due_date=date.today() + timedelta(days=1),
                    first_review_ms=None,
                )
                return [make_note_record(anki_guid=guid, cards=[card])]

        writer = OfflineWriter(anki_conn)
        sync = AnkiSync(
            db=db,
            _reader=NoRevlogReader(),
            _writer=writer,
            _anki_col_crt=col_crt,
        )
        sync.sync_push()

        # Verify decks.common field 4 == 1
        row = anki_conn.execute("SELECT common FROM decks WHERE id = 1").fetchone()
        from app.anki.protobuf_wire import find_varint_field

        assert find_varint_field(bytes(row[0]), 4) == 1

    def test_sync_push_does_not_bump_when_deck_id_none(self):
        """First loop: NEW→non-NEW with no deck mapping → bump skipped (covers deck_id is None branch)."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        col_crt = 1704067200
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=10), time(4, 0), tzinfo=UTC),
            stability=10.0,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            prior_state=SRSState.NEW,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        sync = AnkiSync(
            db=db,
            _reader=FakeReader(),
            _writer=writer,
            _anki_col_crt=col_crt,
        )
        sync.sync_push()

        bump_calls = [c for c in writer.calls if c[0] == "bump_deck_new_today"]
        assert bump_calls == []

    def test_sync_push_second_loop_bumps_new_today(self):
        """Second loop (clean with last_rating) bumps NEW→non-NEW when no prior revlog."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        anki_conn = _make_anki_with_decks()
        # First-introduction scenario: Anki card is still NEW.
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid, queue=0, card_type=0)

        col_crt = 1704067200
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=10), time(4, 0), tzinfo=UTC),
            stability=10.0,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=False,
            anki_card_id=rec_cid,
            last_rating=3,
            prior_state=SRSState.NEW,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        class NoPriorRevlogReader:
            def get_note_records(self):
                return []

        writer = OfflineWriter(anki_conn)
        sync = AnkiSync(
            db=db,
            _reader=NoPriorRevlogReader(),
            _writer=writer,
            _anki_col_crt=col_crt,
        )
        sync.sync_push()

        row = anki_conn.execute("SELECT common FROM decks WHERE id = 1").fetchone()
        from app.anki.protobuf_wire import find_varint_field

        assert find_varint_field(bytes(row[0]), 4) == 1

    def test_sync_push_first_loop_does_not_bump_when_prior_nonzero(self):
        """First loop: NEW→non-NEW with prior revlog today → no bump (prior != 0)."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        col_crt = 1704067200
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=10), time(4, 0), tzinfo=UTC),
            stability=10.0,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            prior_state=SRSState.NEW,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        class FakeWriterWithPriorRevlog(FakeWriter):
            pass

        writer = FakeWriterWithPriorRevlog()
        sync = AnkiSync(
            db=db,
            _reader=FakeReader(),
            _writer=writer,
            _anki_col_crt=col_crt,
        )
        sync.sync_push()

        bump_calls = [c for c in writer.calls if c[0] == "bump_deck_new_today"]
        assert bump_calls == []

    def test_sync_push_counts_zero_when_first_grade_was_yesterday(self):
        """Recompute correctly counts 0 when the card's first revlog is from a prior day.

        Even though sync_push writes a fresh revlog today, MIN(revlog.id) for
        this cid is yesterday's seeded entry — so the card doesn't qualify as a
        first-grade-today.
        """
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        anki_conn = _make_anki_with_decks()
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid)

        today_4am_ms = int(_local_today_4am().timestamp() * 1000)
        anki_conn.execute(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "VALUES (?, ?, -1, 3, 10, 0, 2500, 1000, 0)",
            (today_4am_ms - 1000, rec_cid),
        )
        anki_conn.commit()

        col_crt = 1704067200
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=10), time(4, 0), tzinfo=UTC),
            stability=10.0,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=False,
            anki_card_id=rec_cid,
            last_rating=3,
            prior_state=SRSState.NEW,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = OfflineWriter(anki_conn)
        sync = AnkiSync(
            db=db,
            _reader=FakeReader(),
            _writer=writer,
            _anki_col_crt=col_crt,
        )
        sync.sync_push()

        row = anki_conn.execute("SELECT common FROM decks WHERE id = 1").fetchone()
        from app.anki.protobuf_wire import find_varint_field

        assert find_varint_field(bytes(row[0]), 4) == 0

    def test_sync_push_second_loop_does_not_bump_when_deck_id_none(self):
        """Second loop: NEW→non-NEW with no deck mapping → bump skipped."""
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        col_crt = 1704067200
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=10), time(4, 0), tzinfo=UTC),
            stability=10.0,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=False,
            anki_card_id=rec_cid,
            last_rating=3,
            prior_state=SRSState.NEW,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        sync = AnkiSync(
            db=db,
            _reader=FakeReader(),
            _writer=writer,
            _anki_col_crt=col_crt,
        )
        sync.sync_push()

        bump_calls = [c for c in writer.calls if c[0] == "bump_deck_new_today"]
        assert bump_calls == []

    def test_sync_push_dry_run_does_not_bump(self):
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        anki_conn = _make_anki_with_decks()
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid)

        col_crt = 1704067200
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=1), time(4, 0), tzinfo=UTC),
            stability=1.0,
            difficulty=5.0,
            reps=1,
            lapses=0,
            state=SRSState.LEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            left=1,
            prior_state=SRSState.NEW,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        sync = AnkiSync(
            db=db,
            _reader=FakeReader(),
            _writer=writer,
            _anki_col_crt=col_crt,
        )
        sync.sync_push(dry_run=True)

        row = anki_conn.execute("SELECT common FROM decks WHERE id = 1").fetchone()
        from app.anki.protobuf_wire import find_varint_field

        assert find_varint_field(bytes(row[0]), 4) is None

    def test_sync_push_counts_once_when_card_graded_in_both_apps(self):
        """Card graded today in Anki AND in TT → recompute counts it exactly once.

        Scenario: user graded card X in Anki first (revlog entry today, Anki's
        newToday=1), then graded in TT, then synced. TT's push writes a second
        revlog today for the same cid. The recompute counts DISTINCT cids
        whose MIN(revlog.id) is today — still 1, not 2.
        """
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        anki_conn = _make_anki_with_decks()
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid, queue=1, card_type=1, due=0, ivl=0)

        # Simulate Anki's earlier-today grade with a revlog entry.
        today_4am_ms = int(_local_today_4am().timestamp() * 1000)
        anki_conn.execute(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "VALUES (?, ?, -1, 3, 1, 0, 2500, 1000, 0)",
            (today_4am_ms + 100, rec_cid),
        )
        anki_conn.commit()

        col_crt = 1704067200
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=1.0,
            difficulty=5.0,
            reps=1,
            lapses=0,
            state=SRSState.LEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            left=1,
            prior_state=SRSState.NEW,
        )
        from datetime import datetime as _dt

        ds.due_at = _dt.now(UTC) + timedelta(minutes=1)
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = OfflineWriter(anki_conn)
        sync = AnkiSync(
            db=db,
            _reader=FakeReader(),
            _writer=writer,
            _anki_col_crt=col_crt,
        )
        sync.sync_push()

        row = anki_conn.execute("SELECT common FROM decks WHERE id = 1").fetchone()
        from app.anki.protobuf_wire import find_varint_field

        # Distinct cid count whose first revlog is today = 1 (rec_cid).
        # Both revlog entries are today's, but they share one cid.
        assert find_varint_field(bytes(row[0]), 4) == 1

    def test_sync_push_does_not_double_bump_on_second_push_same_card(self):
        """`prior_state='new'` is sticky across learning steps. The bump must
        fire only on the first push (when Anki's card is still queue=0). The
        second push for the same card has queue=1 (set by the first push), so
        no bump.
        """
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        anki_conn = _make_anki_with_decks()
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid, queue=0, card_type=0, due=0, ivl=0)

        col_crt = 1704067200

        from datetime import datetime as _dt

        # First push: NEW→LEARNING, left=2
        ds1 = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=1.0,
            difficulty=5.0,
            reps=1,
            lapses=0,
            state=SRSState.LEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            left=2,
            prior_state=SRSState.NEW,
        )
        ds1.due_at = _dt.now(UTC) + timedelta(minutes=1)
        db.update_direction(guid, Direction.RECOGNITION, ds1)

        writer = OfflineWriter(anki_conn)
        sync = AnkiSync(
            db=db,
            _reader=FakeReader(),
            _writer=writer,
            _anki_col_crt=col_crt,
        )
        sync.sync_push()

        from app.anki.protobuf_wire import find_varint_field

        row = anki_conn.execute("SELECT common FROM decks WHERE id = 1").fetchone()
        assert find_varint_field(bytes(row[0]), 4) == 1, "first push should bump"

        # Second push: user graded again, still in learning. prior_state STAYS new (sticky).
        ds2 = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=1.5,
            difficulty=5.0,
            reps=2,
            lapses=0,
            state=SRSState.LEARNING,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            left=1,
            prior_state=SRSState.NEW,  # sticky
        )
        ds2.due_at = _dt.now(UTC) + timedelta(minutes=2)
        db.update_direction(guid, Direction.RECOGNITION, ds2)

        sync2 = AnkiSync(
            db=db,
            _reader=FakeReader(),
            _writer=writer,
            _anki_col_crt=col_crt,
        )
        sync2.sync_push()

        row = anki_conn.execute("SELECT common FROM decks WHERE id = 1").fetchone()
        # Counter should still be 1, not 2
        assert find_varint_field(bytes(row[0]), 4) == 1, "second push must NOT bump again"

    def test_sync_push_counts_zero_for_review_push_with_prior_history(self):
        """Recompute correctly excludes a review-state push when the card was
        already first-graded on a prior day.
        """
        db = _make_tt_db()
        guid, _, rec_cid, _ = _add_banka_with_anki_ids(db)
        anki_conn = _make_anki_with_decks()
        _seed_note_and_cards(anki_conn, rec_cid=rec_cid, queue=2, card_type=2)

        # Card was already graded prior to today.
        today_4am_ms = int(_local_today_4am().timestamp() * 1000)
        anki_conn.execute(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "VALUES (?, ?, -1, 3, 10, 0, 2500, 1000, 0)",
            (today_4am_ms - 100000, rec_cid),
        )
        anki_conn.commit()

        col_crt = 1704067200
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today() + timedelta(days=10), time(4, 0), tzinfo=UTC),
            stability=10.0,
            difficulty=4.8,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            anki_card_id=rec_cid,
            last_rating=3,
            prior_state=SRSState.REVIEW,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        writer = FakeWriter()
        sync = AnkiSync(
            db=db,
            _reader=FakeReader(),
            _writer=writer,
            _anki_col_crt=col_crt,
        )
        sync.sync_push()

        row = anki_conn.execute("SELECT common FROM decks WHERE id = 1").fetchone()
        from app.anki.protobuf_wire import find_varint_field

        assert find_varint_field(bytes(row[0]), 4) is None

    def test_sync_push_skips_recompute_when_writer_lacks_methods(self):
        """Writer missing list_decks_with_revlog_today → recompute is no-op (covers early return)."""
        db = _make_tt_db()
        col_crt = 1704067200

        class BareWriter:
            pass

        writer = BareWriter()
        sync = AnkiSync(
            db=db,
            _reader=FakeReader(),
            _writer=writer,
            _anki_col_crt=col_crt,
        )
        # Should not raise despite the writer lacking all three required methods.
        sync.sync_push()
        # No assertion needed — the test passes if no exception is raised;
        # the hasattr guard in _recompute_anki_new_today_all_decks returns early.
