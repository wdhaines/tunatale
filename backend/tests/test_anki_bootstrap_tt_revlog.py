"""Tests for bootstrap_tt_revlog one-shot script."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from app.anki.bootstrap_tt_revlog import bootstrap_tt_revlog
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase

# ── Anki DB helpers ─────────────────────────────────────────────────────────


def _create_anki_db(tmp_path: Path, name: str = "collection.anki2") -> Path:
    """Create a file-based Anki collection with all tables (no data)."""
    path = tmp_path / name
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE col (
            id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,
            dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT,
            decks TEXT, dconf TEXT, tags TEXT
        );
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY, guid TEXT UNIQUE, mid INTEGER, mod INTEGER,
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
            id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER,
            ivl INTEGER, lastIvl INTEGER, factor INTEGER, time INTEGER,
            type INTEGER
        );
        CREATE TABLE notetypes (
            id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER,
            usn INTEGER, config BLOB
        );
        CREATE TABLE templates (
            ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER,
            usn INTEGER, config BLOB, PRIMARY KEY (ntid, ord)
        );
        CREATE TABLE fields (
            ntid INTEGER, ord INTEGER, name TEXT, config BLOB,
            PRIMARY KEY (ntid, ord)
        );
        CREATE TABLE decks (
            id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER,
            usn INTEGER, common BLOB
        );
    """)
    conn.commit()
    conn.close()
    return path


def _create_minimal_anki_db(tmp_path: Path) -> Path:
    """Create minimal but valid Anki DB (for TT-only tests where Part A is empty)."""
    path = _create_anki_db(tmp_path, "collection.anki2")
    conn = sqlite3.connect(str(path))
    conn.execute(
        "INSERT INTO col VALUES (1, 1704067200, 0, 1000, 18, 0, 0, 0, '{}', '{}', '{}', '{}', '{}')",
    )
    conn.commit()
    conn.close()
    return path


def _seed_col(conn: sqlite3.Connection, col_crt: int = 1704067200) -> None:
    conn.execute(
        "INSERT INTO col VALUES (1, ?, 0, 1000, 18, 0, 0, 0, '{}', '{}', '{}', '{}', '{}')",
        (col_crt,),
    )


def _seed_deck(conn: sqlite3.Connection, deck_id: int = 12345) -> None:
    conn.execute(
        "INSERT INTO decks VALUES (?, '0. Slovene', 0, 0, x'')",
        (deck_id,),
    )


def _seed_vocab_notetype(conn: sqlite3.Connection, mid: int = 1000001) -> None:
    conn.execute(
        "INSERT INTO notetypes VALUES (?, 'Slovene Vocabulary', 0, 0, x'')",
        (mid,),
    )
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?, ?, x'')",
        [
            (mid, i, name)
            for i, name in enumerate(
                [
                    "Front",
                    "Back",
                    "Slovene",
                    "English",
                    "Sentence",
                    "SentenceTranslation",
                    "Image",
                    "Audio",
                    "Grammar",
                    "Note",
                    "DisambigKey",
                    "Tags",
                    "Source",
                ]
            )
        ],
    )
    conn.executemany(
        "INSERT INTO templates VALUES (?, ?, ?, 0, 0, x'')",
        [(mid, 0, "Recognition"), (mid, 1, "Production")],
    )


def _seed_cloze_notetype(conn: sqlite3.Connection, mid: int = 1000002) -> None:
    conn.execute(
        "INSERT INTO notetypes VALUES (?, 'Cloze', 0, 0, x'')",
        (mid,),
    )
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?, ?, x'')",
        [(mid, i, name) for i, name in enumerate(["Text", "Back Extra"])],
    )
    conn.execute(
        "INSERT INTO templates VALUES (?, 0, 'Cloze', 0, 0, x'')",
        (mid,),
    )


def _add_vocab_note_with_revlog(
    conn: sqlite3.Connection,
    *,
    note_id: int = 1001,
    guid: str = "guid_test_vocab",
    card_rec_id: int = 10001,
    card_prod_id: int = 10002,
    deck_id: int = 12345,
    mid: int = 1000001,
    rec_revlog: list[tuple] | None = None,
    prod_revlog: list[tuple] | None = None,
) -> None:
    """Add a vocab note with 2 cards and optional revlog rows."""
    conn.execute(
        "INSERT INTO notes VALUES (?, ?, ?, 0, 0, '', "
        "'banka\x1fbank\x1fbanka\x1fbank\x1f\x1f\x1f\x1f"
        "\x1f\x1f\x1f\x1f\x1f', 'banka', 0, 0, '')",
        (note_id, guid, mid),
    )
    conn.execute(
        'INSERT INTO cards VALUES (?, ?, ?, 0, 0, 0, 2, 2, 10, 21, 2500, 5, 0, 0, 0, 0, 0, \'{"s": 10.5, "d": 4.8}\')',
        (card_rec_id, note_id, deck_id),
    )
    conn.execute(
        'INSERT INTO cards VALUES (?, ?, ?, 1, 0, 0, 2, 2, 20, 14, 2500, 3, 0, 0, 0, 0, 0, \'{"s": 5.2, "d": 5.1}\')',
        (card_prod_id, note_id, deck_id),
    )
    if rec_revlog:
        conn.executemany(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)",
            [(rid, card_rec_id, *vals) for rid, *vals in rec_revlog],
        )
    if prod_revlog:
        conn.executemany(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)",
            [(rid, card_prod_id, *vals) for rid, *vals in prod_revlog],
        )


def _add_cloze_note_with_revlog(
    conn: sqlite3.Connection,
    *,
    note_id: int = 2001,
    guid: str = "guid_test_cloze",
    card_id: int = 20001,
    deck_id: int = 12345,
    mid: int = 1000002,
    revlog_rows: list[tuple] | None = None,
) -> None:
    """Add a cloze note with 1 card (ord=0) and optional revlog."""
    conn.execute(
        "INSERT INTO notes VALUES (?, ?, ?, 0, 0, '', '{{c1::test}}', 'test', 0, 0, '')",
        (note_id, guid, mid),
    )
    conn.execute(
        "INSERT INTO cards VALUES (?, ?, ?, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')",
        (card_id, note_id, deck_id),
    )
    if revlog_rows:
        conn.executemany(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) "
            "VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)",
            [(rid, card_id, *vals) for rid, *vals in revlog_rows],
        )


# ── TT DB helpers ────────────────────────────────────────────────────────────


def _build_tt_db(tmp_path: Path, name: str = "tunatale.db") -> Path:
    """Create a file-based TT DB with all migrations applied, no data."""
    path = tmp_path / name
    db = SRSDatabase(str(path))
    db.close()
    return path


def _today_dt() -> datetime:
    return datetime.combine(date.today(), time(4, 0), tzinfo=UTC)


def _set_vocab_direction_state(
    tt_path: Path,
    text: str,
    *,
    rec_anki_card_id: int | None = 10001,
    prod_anki_card_id: int | None = 10002,
    rec_reps: int = 5,
    prod_reps: int = 3,
    rec_last_review_ms: int = 1700000000001,
    prod_last_review_ms: int = 1700000000002,
) -> None:
    """Set FSRS state + Anki linkage on both directions of a vocab collocation."""
    with SRSDatabase(str(tt_path)) as db:
        item = db.get_collocation(text)
        assert item is not None, f"Collocation '{text}' not found"
        guid = item.guid

        rec_state = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_at=datetime(2024, 1, 5, 4, 0, tzinfo=UTC),
            stability=5.0,
            difficulty=5.0,
            reps=rec_reps,
            lapses=0,
            last_review=datetime(2024, 1, 4, 4, 0, tzinfo=UTC),
            last_review_time_ms=rec_last_review_ms,
            last_rating=3,
            anki_card_id=rec_anki_card_id,
            left=0,
            prior_state=None,
            prior_left=None,
            prior_stability=None,
            introduced_at=_today_dt(),
        )
        db.update_direction(guid, Direction.RECOGNITION, rec_state)

        prod_state = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.REVIEW,
            due_at=datetime(2024, 1, 5, 4, 0, tzinfo=UTC),
            stability=5.0,
            difficulty=5.0,
            reps=prod_reps,
            lapses=0,
            last_review=datetime(2024, 1, 4, 4, 0, tzinfo=UTC),
            last_review_time_ms=prod_last_review_ms,
            last_rating=3,
            anki_card_id=prod_anki_card_id,
            left=0,
            prior_state=None,
            prior_left=None,
            prior_stability=None,
            introduced_at=_today_dt(),
        )
        db.update_direction(guid, Direction.PRODUCTION, prod_state)


def _set_tt_only_direction_state(
    tt_path: Path,
    text: str,
    direction: Direction = Direction.RECOGNITION,
    *,
    reps: int = 2,
    last_review_time_ms: int = 1700000001000,
    last_rating: int | None = 3,
) -> None:
    """Set FSRS state on a TT-only direction (no anki_card_id)."""
    with SRSDatabase(str(tt_path)) as db:
        item = db.get_collocation(text)
        assert item is not None, f"Collocation '{text}' not found"
        guid = item.guid

        state = DirectionState(
            direction=direction,
            state=SRSState.REVIEW,
            due_at=datetime(2024, 1, 5, 4, 0, tzinfo=UTC),
            stability=5.0,
            difficulty=5.0,
            reps=reps,
            lapses=0,
            last_review=datetime(2024, 1, 4, 4, 0, tzinfo=UTC),
            last_review_time_ms=last_review_time_ms,
            last_rating=last_rating,
            anki_card_id=None,
            left=0,
            prior_state=None,
            prior_left=None,
            prior_stability=None,
            introduced_at=_today_dt(),
        )
        db.update_direction(guid, direction, state)


# ── Tests ────────────────────────────────────────────────────────────────────


def _open_tt(tt_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tt_path))
    conn.row_factory = sqlite3.Row
    return conn


class TestBootstrapLinkedDirections:
    """Part A: Anki-linked directions."""

    @pytest.fixture
    def scenario(self, tmp_path):
        """Build Anki + TT DBs with 1 vocab note, 2 linked directions, revlog."""
        anki = _create_anki_db(tmp_path, "collection.anki2")
        conn = sqlite3.connect(str(anki))
        _seed_col(conn)
        _seed_deck(conn)
        _seed_vocab_notetype(conn)
        _add_vocab_note_with_revlog(
            conn,
            rec_revlog=[(1700000000001, 3, 21, 10, 2500, 1200, 1)],
            prod_revlog=[(1700000000002, 4, 14, 5, 2500, 800, 1)],
        )
        conn.commit()
        conn.close()

        tt = _build_tt_db(tmp_path)
        unit = SyntacticUnit(
            text="banka",
            translation="bank",
            word_count=1,
            difficulty=1,
            source="test",
        )
        with SRSDatabase(str(tt)) as db:
            db.add_collocation(unit)
        _set_vocab_direction_state(tt, "banka")

        return anki, tt

    def test_copies_revlog_rows(self, scenario):
        anki, tt = scenario
        result = bootstrap_tt_revlog(tt, anki)
        assert result["linked_directions"] == 2
        assert result["anki_rows"] == 2
        assert result["errors"] == []

        tconn = _open_tt(tt)
        rows = tconn.execute(
            "SELECT id, collocation_id, direction, button_chosen, "
            "interval, last_interval, factor, taken_millis, review_kind, "
            "anki_card_id FROM tt_revlog ORDER BY id"
        ).fetchall()
        tconn.close()

        assert len(rows) == 2
        assert rows[0]["id"] == 1700000000001
        assert rows[0]["direction"] == "recognition"
        assert rows[0]["button_chosen"] == 3
        assert rows[0]["interval"] == 21
        assert rows[0]["last_interval"] == 10
        assert rows[0]["factor"] == 2500
        assert rows[0]["taken_millis"] == 1200
        assert rows[0]["review_kind"] == 1
        assert rows[0]["anki_card_id"] == 10001
        assert rows[1]["id"] == 1700000000002
        assert rows[1]["direction"] == "production"
        assert rows[1]["button_chosen"] == 4
        assert rows[1]["anki_card_id"] == 10002

    def test_direction_from_ord(self, scenario):
        anki, tt = scenario
        result = bootstrap_tt_revlog(tt, anki)
        assert result["linked_directions"] == 2

        tconn = _open_tt(tt)
        rows = tconn.execute("SELECT id, direction FROM tt_revlog ORDER BY id").fetchall()
        tconn.close()
        dirs = {r["id"]: r["direction"] for r in rows}
        assert dirs[1700000000001] == "recognition"
        assert dirs[1700000000002] == "production"

    def test_idempotent(self, scenario):
        anki, tt = scenario
        bootstrap_tt_revlog(tt, anki)
        tconn = _open_tt(tt)
        count1 = tconn.execute("SELECT COUNT(*) FROM tt_revlog").fetchone()[0]
        tconn.close()
        bootstrap_tt_revlog(tt, anki)
        tconn = _open_tt(tt)
        count2 = tconn.execute("SELECT COUNT(*) FROM tt_revlog").fetchone()[0]
        tconn.close()
        assert count1 == count2
        assert count1 == 2

    def test_existing_rows_not_duplicated(self, scenario):
        """Rows already present from Stage 0 sync_pull are not duplicated."""
        anki, tt = scenario
        tconn = _open_tt(tt)
        coll_id = tconn.execute("SELECT id FROM collocations LIMIT 1").fetchone()[0]
        tconn.execute(
            "INSERT OR IGNORE INTO tt_revlog "
            "(id, collocation_id, direction, button_chosen, interval, "
            "last_interval, factor, taken_millis, review_kind, anki_card_id) "
            "VALUES (?, ?, 'recognition', 3, 21, 10, 2500, 1200, 1, 10001)",
            (1700000000001, coll_id),
        )
        tconn.commit()
        tconn.close()

        bootstrap_tt_revlog(tt, anki)
        tconn = _open_tt(tt)
        count = tconn.execute("SELECT COUNT(*) FROM tt_revlog").fetchone()[0]
        tconn.close()
        # Pre-seeded 1 row + 1 new from production = 2 total
        assert count == 2

    def test_dry_run_does_not_insert(self, scenario):
        anki, tt = scenario
        result = bootstrap_tt_revlog(tt, anki, dry_run=True)
        assert result["anki_rows_applied"] == 0
        tconn = _open_tt(tt)
        count = tconn.execute("SELECT COUNT(*) FROM tt_revlog").fetchone()[0]
        tconn.close()
        assert count == 0


class TestBootstrapCloze:
    """Cloze items: ord=0 -> production."""

    def test_cloze_production_at_ord_0(self, tmp_path):
        anki = _create_anki_db(tmp_path, "collection.anki2")
        conn = sqlite3.connect(str(anki))
        _seed_col(conn)
        _seed_deck(conn)
        _seed_cloze_notetype(conn)
        _add_cloze_note_with_revlog(
            conn,
            revlog_rows=[(1700000000010, 3, 10, 0, 2500, 1500, 0)],
        )
        conn.commit()
        conn.close()

        tt = _build_tt_db(tmp_path)
        unit = SyntacticUnit(
            text="test_cloze",
            translation="test",
            word_count=1,
            difficulty=1,
            source="test",
            card_type="cloze",
        )
        with SRSDatabase(str(tt)) as db:
            db.add_collocation(unit)
        _set_vocab_direction_state(
            tt,
            "test_cloze",
            rec_anki_card_id=None,
            prod_anki_card_id=20001,
            rec_reps=0,
            prod_reps=1,
            prod_last_review_ms=1700000000010,
        )

        result = bootstrap_tt_revlog(tt, anki)
        assert result["linked_directions"] == 1
        assert result["anki_rows"] == 1

        tconn = _open_tt(tt)
        rows = tconn.execute("SELECT direction, anki_card_id FROM tt_revlog").fetchall()
        tconn.close()
        assert len(rows) == 1
        assert rows[0]["direction"] == "production"
        assert rows[0]["anki_card_id"] == 20001


class TestBootstrapMixed:
    """Vocab + cloze in a single run."""

    def test_mixed_card_types(self, tmp_path):
        anki = _create_anki_db(tmp_path, "collection.anki2")
        conn = sqlite3.connect(str(anki))
        _seed_col(conn)
        _seed_deck(conn)
        _seed_vocab_notetype(conn)
        _seed_cloze_notetype(conn)
        _add_vocab_note_with_revlog(
            conn,
            note_id=1001,
            guid="guid_vocab",
            card_rec_id=10001,
            card_prod_id=10002,
            rec_revlog=[(1700000000100, 3, 30, 15, 2500, 2000, 1)],
            prod_revlog=[(1700000000101, 4, 20, 10, 2500, 1000, 1)],
        )
        _add_cloze_note_with_revlog(
            conn,
            note_id=2001,
            guid="guid_cloze",
            card_id=20001,
            revlog_rows=[(1700000000102, 3, 5, 0, 2500, 500, 0)],
        )
        conn.commit()
        conn.close()

        tt = _build_tt_db(tmp_path)
        unit1 = SyntacticUnit(
            text="banka",
            translation="bank",
            word_count=1,
            difficulty=1,
            source="test",
        )
        unit2 = SyntacticUnit(
            text="test_cloze",
            translation="test",
            word_count=1,
            difficulty=1,
            source="test",
            card_type="cloze",
        )
        with SRSDatabase(str(tt)) as db:
            db.add_collocation(unit1)
            db.add_collocation(unit2)
        _set_vocab_direction_state(
            tt,
            "banka",
            rec_anki_card_id=10001,
            prod_anki_card_id=10002,
        )
        _set_vocab_direction_state(
            tt,
            "test_cloze",
            rec_anki_card_id=None,
            prod_anki_card_id=20001,
            rec_reps=0,
            prod_reps=1,
            prod_last_review_ms=1700000000102,
        )

        result = bootstrap_tt_revlog(tt, anki)
        assert result["linked_directions"] == 3
        assert result["anki_rows"] == 3

        tconn = _open_tt(tt)
        rows = tconn.execute("SELECT collocation_id, direction, anki_card_id FROM tt_revlog ORDER BY id").fetchall()
        tconn.close()
        assert len(rows) == 3
        dir_map = {(r["collocation_id"], r["direction"]): r for r in rows}
        assert dir_map[(1, "recognition")]["anki_card_id"] == 10001
        assert dir_map[(1, "production")]["anki_card_id"] == 10002
        assert dir_map[(2, "production")]["anki_card_id"] == 20001


class TestBootstrapTTOnly:
    """Part B: directions without anki_card_id."""

    def _build_tt_only(
        self, tmp_path, *, text="tt_only", last_review_ms=1700000001000, last_rating=3, direction=Direction.RECOGNITION
    ):
        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        unit = SyntacticUnit(
            text=text,
            translation="test",
            word_count=1,
            difficulty=1,
            source="test",
        )
        with SRSDatabase(str(tt)) as db:
            db.add_collocation(unit)
        _set_tt_only_direction_state(
            tt,
            text,
            direction=direction,
            reps=2,
            last_review_time_ms=last_review_ms,
            last_rating=last_rating,
        )
        return anki, tt

    def test_synthetic_row_created(self, tmp_path):
        anki, tt = self._build_tt_only(tmp_path)
        result = bootstrap_tt_revlog(tt, anki)
        assert result["tt_only_directions"] == 1
        assert result["synthetic_rows"] == 1

        tconn = _open_tt(tt)
        rows = tconn.execute(
            "SELECT id, button_chosen, interval, last_interval, factor, "
            "taken_millis, review_kind, anki_card_id FROM tt_revlog"
        ).fetchall()
        tconn.close()
        assert len(rows) == 1
        r = rows[0]
        assert r["id"] == 1700000001000
        assert r["button_chosen"] == 3
        assert r["interval"] == 0
        assert r["last_interval"] == 0
        assert r["factor"] == 0
        assert r["taken_millis"] == 0
        assert r["review_kind"] == 4
        assert r["anki_card_id"] is None

    def test_synthetic_row_rating_fallback(self, tmp_path):
        """last_rating=None -> button_chosen=3 (Good)."""
        anki, tt = self._build_tt_only(tmp_path, last_rating=None)
        bootstrap_tt_revlog(tt, anki)
        tconn = _open_tt(tt)
        rows = tconn.execute("SELECT button_chosen FROM tt_revlog").fetchall()
        tconn.close()
        assert len(rows) == 1
        assert rows[0][0] == 3

    def test_production_pk_offset(self, tmp_path):
        """Sibling directions: production gets +1 to avoid PK collision."""
        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        unit = SyntacticUnit(
            text="both",
            translation="test",
            word_count=1,
            difficulty=1,
            source="test",
        )
        with SRSDatabase(str(tt)) as db:
            db.add_collocation(unit)
        _set_tt_only_direction_state(
            tt,
            "both",
            direction=Direction.RECOGNITION,
            reps=1,
            last_review_time_ms=1700000002000,
            last_rating=3,
        )
        _set_tt_only_direction_state(
            tt,
            "both",
            direction=Direction.PRODUCTION,
            reps=1,
            last_review_time_ms=1700000002000,
            last_rating=4,
        )

        bootstrap_tt_revlog(tt, anki)
        tconn = _open_tt(tt)
        rows = tconn.execute("SELECT id, direction, button_chosen FROM tt_revlog ORDER BY id").fetchall()
        tconn.close()
        assert len(rows) == 2
        assert rows[0]["id"] == 1700000002000
        assert rows[0]["direction"] == "recognition"
        assert rows[1]["id"] == 1700000002001
        assert rows[1]["direction"] == "production"
        assert rows[1]["button_chosen"] == 4

    def test_skips_zero_timestamp(self, tmp_path):
        """Skip TT-only with last_review_time_ms=0, log warning."""
        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        unit = SyntacticUnit(
            text="bad",
            translation="test",
            word_count=1,
            difficulty=1,
            source="test",
        )
        with SRSDatabase(str(tt)) as db:
            db.add_collocation(unit)
        _set_tt_only_direction_state(
            tt,
            "bad",
            reps=1,
            last_review_time_ms=0,
            last_rating=3,
        )

        result = bootstrap_tt_revlog(tt, anki)
        assert result["tt_only_directions"] == 1
        assert result["synthetic_rows"] == 0
        assert any("last_review_time_ms=0" in e for e in result["errors"])

    def test_idempotent_tt_only(self, tmp_path):
        anki, tt = self._build_tt_only(tmp_path)
        bootstrap_tt_revlog(tt, anki)
        tconn = _open_tt(tt)
        count1 = tconn.execute("SELECT COUNT(*) FROM tt_revlog").fetchone()[0]
        tconn.close()
        bootstrap_tt_revlog(tt, anki)
        tconn = _open_tt(tt)
        count2 = tconn.execute("SELECT COUNT(*) FROM tt_revlog").fetchone()[0]
        tconn.close()
        assert count1 == count2
        assert count1 == 1

    def test_dry_run_tt_only(self, tmp_path):
        anki, tt = self._build_tt_only(tmp_path)
        result = bootstrap_tt_revlog(tt, anki, dry_run=True)
        assert result["synthetic_rows_applied"] == 0
        tconn = _open_tt(tt)
        count = tconn.execute("SELECT COUNT(*) FROM tt_revlog").fetchone()[0]
        tconn.close()
        assert count == 0


class TestBootstrapValidation:
    """Validation invariant: every reps>0 direction has >=1 tt_revlog row."""

    def test_no_orphans_after_bootstrap(self, tmp_path):
        """After full bootstrap, orphans = 0."""
        anki = _create_anki_db(tmp_path, "collection.anki2")
        conn = sqlite3.connect(str(anki))
        _seed_col(conn)
        _seed_deck(conn)
        _seed_vocab_notetype(conn)
        _add_vocab_note_with_revlog(
            conn,
            note_id=1001,
            guid="guid_v",
            card_rec_id=10001,
            card_prod_id=10002,
            rec_revlog=[(1700000000300, 3, 21, 10, 2500, 1200, 1)],
            prod_revlog=[(1700000000301, 4, 14, 5, 2500, 800, 1)],
        )
        conn.commit()
        conn.close()

        tt = _build_tt_db(tmp_path)
        unit1 = SyntacticUnit(
            text="banka",
            translation="bank",
            word_count=1,
            difficulty=1,
            source="test",
        )
        unit2 = SyntacticUnit(
            text="tt_only",
            translation="test",
            word_count=1,
            difficulty=1,
            source="test",
        )
        with SRSDatabase(str(tt)) as db:
            db.add_collocation(unit1)
            db.add_collocation(unit2)
        _set_vocab_direction_state(
            tt,
            "banka",
            rec_anki_card_id=10001,
            prod_anki_card_id=10002,
        )
        _set_tt_only_direction_state(
            tt,
            "tt_only",
            reps=2,
            last_review_time_ms=1700000000500,
        )

        result = bootstrap_tt_revlog(tt, anki)
        assert result["orphans"] == 0


class TestBootstrapEdgeCases:
    """Edge cases and uncovered branches."""

    def test_card_not_found_error(self, tmp_path):
        """Linked direction with anki_card_id that doesn't exist in Anki."""
        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        unit = SyntacticUnit(
            text="orphan",
            translation="test",
            word_count=1,
            difficulty=1,
            source="test",
        )
        with SRSDatabase(str(tt)) as db:
            db.add_collocation(unit)
        _set_vocab_direction_state(
            tt,
            "orphan",
            rec_anki_card_id=99999,
            prod_anki_card_id=None,
            rec_reps=1,
            prod_reps=0,
        )

        result = bootstrap_tt_revlog(tt, anki)
        assert result["linked_directions"] == 1
        assert result["anki_rows"] == 0
        assert any("99999" in e for e in result["errors"])

    def test_direction_mismatch_warning(self, tmp_path):
        """TT says recognition but Anki card ord=1 -> logged, TT's value wins."""
        anki = _create_anki_db(tmp_path, "collection.anki2")
        conn = sqlite3.connect(str(anki))
        _seed_col(conn)
        _seed_deck(conn)
        _seed_vocab_notetype(conn)
        # Create a card with ord=1 (production) but we'll link it as recognition
        _add_cloze_note_with_revlog(
            conn,
            note_id=3001,
            guid="guid_mismatch",
            card_id=30001,
            revlog_rows=[(1700000005000, 3, 10, 0, 2500, 500, 0)],
        )
        conn.commit()
        conn.close()

        tt = _build_tt_db(tmp_path)
        unit = SyntacticUnit(
            text="mismatch",
            translation="test",
            word_count=1,
            difficulty=1,
            source="test",
        )
        with SRSDatabase(str(tt)) as db:
            db.add_collocation(unit)
        # Link as recognition, but card has ord=0 (cloze always has ord=0).
        # Since card_type='vocab' (default), expected_dir = recognition.
        # This gives ord=0 -> recognition, which matches. Need a different setup.
        # Instead: use a regular vocab card with ord=1 but link as recognition.
        with SRSDatabase(str(tt)) as db:
            item = db.get_collocation("mismatch")
            assert item is not None
            guid = item.guid
            state = DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_at=datetime(2024, 1, 5, 4, 0, tzinfo=UTC),
                stability=5.0,
                difficulty=5.0,
                reps=1,
                lapses=0,
                last_review=datetime(2024, 1, 4, 4, 0, tzinfo=UTC),
                last_review_time_ms=1700000005000,
                last_rating=3,
                anki_card_id=30001,
                left=0,
                prior_state=None,
                prior_left=None,
                prior_stability=None,
                introduced_at=_today_dt(),
            )
            db.update_direction(guid, Direction.RECOGNITION, state)

        # Now overwrite card ord to 1 (production) in the Anki DB
        conn = sqlite3.connect(str(anki))
        conn.execute("UPDATE cards SET ord = 1 WHERE id = 30001")
        conn.commit()
        conn.close()

        result = bootstrap_tt_revlog(tt, anki)
        assert result["linked_directions"] == 1
        assert result["anki_rows"] == 1
        tconn = _open_tt(tt)
        row = tconn.execute("SELECT direction FROM tt_revlog").fetchone()
        tconn.close()
        assert row["direction"] == "recognition"  # TT's value wins

    def test_main_dry_run(self, tmp_path, monkeypatch):
        """Call main() with --dry-run via CLI entry point."""
        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        monkeypatch.setattr(
            "app.config.settings.anki_collection_path",
            str(anki),
        )
        monkeypatch.setattr(
            "app.config.settings.database_url",
            f"sqlite:///{tt}",
        )
        from app.anki.bootstrap_tt_revlog import main

        assert main(["--dry-run"]) == 0

    def test_main_with_errors(self, tmp_path, monkeypatch):
        """Call main() with data that produces errors (covers error-print path)."""
        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        # Seed a TT-only direction with last_review_time_ms=0 to trigger an error
        unit = SyntacticUnit(
            text="bad",
            translation="test",
            word_count=1,
            difficulty=1,
            source="test",
        )
        with SRSDatabase(str(tt)) as db:
            db.add_collocation(unit)
        _set_tt_only_direction_state(
            tt,
            "bad",
            reps=1,
            last_review_time_ms=0,
            last_rating=3,
        )

        monkeypatch.setattr(
            "app.config.settings.anki_collection_path",
            str(anki),
        )
        monkeypatch.setattr(
            "app.config.settings.database_url",
            f"sqlite:///{tt}",
        )
        from app.anki.bootstrap_tt_revlog import main

        assert main([]) == 0
