"""Tests for app.plugins.anki_sync.add_short_form_field (tunatale-0rlg).

The live "Cebuano Vocabulary" notetype has its dictionary layers but no Short
form field. The oracle is ``create_vocab_notetype``: after the migration, the old
notetype must render exactly as a new one would.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest

from app.cards.vocab_notetype import CEBUANO_VOCAB, build_template_config, create_vocab_notetype
from app.plugins.anki_sync.add_short_form_field import add_short_form_field, run
from app.srs.anki_mirror.protobuf_wire import find_len_field

MID = 1790361065576
OTHER_MID = 1790000000001
NOW_MS = 1_800_000_000_000
BEFORE = dataclasses.replace(CEBUANO_VOCAB, short_form=False)

_SCHEMA = """
    CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, dty INTEGER,
        usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT);
    CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
        tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT);
    CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
        usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER,
        lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT);
    CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,
        lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER);
    CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
    CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord));
    CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB,
        PRIMARY KEY (ntid, ord));
"""


def _populate(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO col VALUES (1, 0, 100, 1000, 18, 0, 5, 0, '{}', '{}', '{}', '{}', '{}')")
    create_vocab_notetype(conn, BEFORE, MID, 50)
    conn.execute("UPDATE notetypes SET usn = 0")
    conn.execute("UPDATE templates SET usn = 0")
    # Control: another notetype, whose rows must not move.
    create_vocab_notetype(conn, dataclasses.replace(BEFORE, name="Other Vocabulary"), OTHER_MID, 50)
    conn.execute("UPDATE notetypes SET usn = 0 WHERE id = ?", (OTHER_MID,))
    conn.execute("UPDATE templates SET usn = 0 WHERE ntid = ?", (OTHER_MID,))
    conn.execute(
        "INSERT INTO notes VALUES (9000, 'g-other', ?, 100, 7, '', ?, 'a', 0, 0, '')", (OTHER_MID, "a" + "\x1f" * 11)
    )
    for i in range(3):
        flds = "\x1f".join((f"pulong{i}", f"word {i}", *[""] * 10))
        conn.execute(
            "INSERT INTO notes VALUES (?, ?, ?, 100, 7, '', ?, ?, 0, 0, '')",
            (1000 + i, f"g{i}", MID, flds, f"pulong{i}"),
        )
    conn.execute("INSERT INTO cards VALUES (1, 1000, 1, 0, 100, 7, 2, 2, 5, 3, 2500, 4, 0, 0, 0, 0, 0, '')")
    conn.commit()


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _populate(conn)
    return conn


def _fresh() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    create_vocab_notetype(conn, CEBUANO_VOCAB, MID, 50)
    return conn


def _names(conn: sqlite3.Connection, mid: int = MID) -> tuple[str, ...]:
    return tuple(r[0] for r in conn.execute("SELECT name FROM fields WHERE ntid=? ORDER BY ord", (mid,)))


def _templates(conn: sqlite3.Connection, mid: int = MID) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT config FROM templates WHERE ntid=? ORDER BY ord", (mid,)).fetchall()
    return [(find_len_field(bytes(r[0]), 1).decode(), find_len_field(bytes(r[0]), 2).decode()) for r in rows]


def _css(conn: sqlite3.Connection, mid: int = MID) -> str:
    return find_len_field(
        bytes(conn.execute("SELECT config FROM notetypes WHERE id=?", (mid,)).fetchone()[0]), 3
    ).decode()


class TestAddShortFormField:
    def test_the_notetype_ends_up_exactly_as_a_new_one_has_it(self):
        conn = _make_conn()
        assert _names(conn) != CEBUANO_VOCAB.field_names  # the control: it starts without
        assert add_short_form_field(conn, CEBUANO_VOCAB, now_ms=NOW_MS) == "created"
        fresh = _fresh()
        assert _names(conn) == _names(fresh)
        assert _templates(conn) == _templates(fresh)
        assert _css(conn) == _css(fresh)

    def test_every_note_gains_one_empty_trailing_field_and_keeps_its_own(self):
        conn = _make_conn()
        add_short_form_field(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        for row in conn.execute("SELECT id, flds, sfld FROM notes WHERE mid=?", (MID,)):
            i = row["id"] - 1000
            assert row["flds"].split("\x1f") == [f"pulong{i}", f"word {i}", *[""] * 11]
            assert row["sfld"] == f"pulong{i}"

    def test_marks_touched_rows_dirty_and_bumps_schema_but_not_col_usn(self):
        conn = _make_conn()
        add_short_form_field(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        now = NOW_MS // 1000
        assert [tuple(r) for r in conn.execute("SELECT DISTINCT usn, mod FROM notes WHERE mid=?", (MID,))] == [
            (-1, now)
        ]
        assert [
            tuple(r) for r in conn.execute("SELECT DISTINCT mtime_secs, usn FROM templates WHERE ntid=?", (MID,))
        ] == [(now, -1)]
        assert tuple(conn.execute("SELECT mtime_secs, usn FROM notetypes WHERE id=?", (MID,)).fetchone()) == (now, -1)
        # col.usn is the sync anchor and must survive (Layer 61).
        assert tuple(conn.execute("SELECT scm, mod, usn FROM col").fetchone()) == (NOW_MS, NOW_MS, 5)

    def test_other_notetypes_and_all_cards_do_not_move(self):
        conn = _make_conn()
        before = (_names(conn, OTHER_MID), _templates(conn, OTHER_MID), _css(conn, OTHER_MID))
        cards = [tuple(r) for r in conn.execute("SELECT * FROM cards")]
        add_short_form_field(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert (_names(conn, OTHER_MID), _templates(conn, OTHER_MID), _css(conn, OTHER_MID)) == before
        assert tuple(conn.execute("SELECT usn, mod FROM notes WHERE id=9000").fetchone()) == (7, 100)
        assert tuple(conn.execute("SELECT mtime_secs, usn FROM notetypes WHERE id=?", (OTHER_MID,)).fetchone()) == (
            50,
            0,
        )
        assert [tuple(r) for r in conn.execute("SELECT * FROM cards")] == cards

    def test_idempotent(self):
        conn = _make_conn()
        add_short_form_field(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert add_short_form_field(conn, CEBUANO_VOCAB, now_ms=NOW_MS + 1000) == "exists"
        assert conn.execute("SELECT scm FROM col").fetchone()[0] == NOW_MS
        assert all(r[0].count("\x1f") == 12 for r in conn.execute("SELECT flds FROM notes WHERE mid=?", (MID,)))

    def test_raises_for_a_missing_notetype(self):
        with pytest.raises(ValueError, match="not found"):
            add_short_form_field(_make_conn(), dataclasses.replace(CEBUANO_VOCAB, name="Nope"), now_ms=NOW_MS)

    def test_raises_for_a_vocab_without_the_field(self):
        with pytest.raises(ValueError, match="no Short form"):
            add_short_form_field(_make_conn(), BEFORE, now_ms=NOW_MS)

    def test_refuses_a_field_roster_it_does_not_recognise(self):
        conn = _make_conn()
        conn.execute("UPDATE fields SET name = 'Gloss' WHERE ntid=? AND ord=1", (MID,))
        with pytest.raises(ValueError, match="roster"):
            add_short_form_field(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert conn.execute("SELECT scm FROM col").fetchone()[0] == 1000

    @pytest.mark.parametrize("ord_", [0, 1])
    def test_refuses_a_template_without_the_spelling_block_and_changes_nothing(self, ord_):
        conn = _make_conn()
        conn.execute(
            "UPDATE templates SET config=? WHERE ntid=? AND ord=?",
            (build_template_config("{{Cebuano}}", "{{FrontSide}}"), MID, ord_),
        )
        with pytest.raises(ValueError, match="under the word"):
            add_short_form_field(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert _names(conn) == BEFORE.field_names
        assert conn.execute("SELECT DISTINCT usn FROM notes WHERE mid=?", (MID,)).fetchall()[0][0] == 7

    def test_raises_when_a_template_has_no_text(self):
        conn = _make_conn()
        conn.execute("UPDATE templates SET config = X'' WHERE ntid=? AND ord=1", (MID,))
        with pytest.raises(ValueError, match="template text"):
            add_short_form_field(conn, CEBUANO_VOCAB, now_ms=NOW_MS)

    def test_defaults_now_ms_to_wall_clock(self):
        conn = _make_conn()
        add_short_form_field(conn, CEBUANO_VOCAB)
        assert conn.execute("SELECT scm FROM col").fetchone()[0] > 1_700_000_000_000


def _collection_file(tmp_path: Path) -> Path:
    db_path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    _populate(conn)
    conn.close()
    return db_path


def _field_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM fields WHERE ntid=?", (MID,)).fetchone()[0]
    finally:
        conn.close()


class TestRun:
    def test_adds_the_field_in_the_collection_file(self, tmp_path, capsys):
        db_path = _collection_file(tmp_path)
        assert run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak") == "created"
        assert _field_count(db_path) == 13
        out = capsys.readouterr().out
        assert "Upload to AnkiWeb" in out and "normalize_usns" in out and "--bootstrap" in out

    def test_second_run_reports_exists(self, tmp_path, capsys):
        db_path = _collection_file(tmp_path)
        run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "1")
        assert run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "2") == "exists"
        assert "[SKIP]" in capsys.readouterr().out

    def test_uses_the_settings_paths_by_default(self, tmp_path, monkeypatch):
        from app.config import settings

        db_path = _collection_file(tmp_path)
        monkeypatch.setattr(settings, "anki_collection_path", db_path)
        monkeypatch.setattr(settings, "anki_backup_dir", tmp_path / "bak")
        assert run(language_code="ceb") == "created"

    @pytest.mark.parametrize(("migrated_first", "expected"), [(False, "created"), (True, "exists")])
    def test_dry_run_reports_and_changes_nothing(self, tmp_path, capsys, migrated_first, expected):
        db_path = _collection_file(tmp_path)
        if migrated_first:
            run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "1")
        before = _field_count(db_path)
        result = run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "2", dry_run=True)
        assert result == "dry-run"
        assert f"would be: {expected}" in capsys.readouterr().out
        assert _field_count(db_path) == before

    @pytest.mark.parametrize("code", ["no", "en"])
    def test_refuses_a_language_with_no_short_form_field(self, tmp_path, code):
        with pytest.raises(ValueError, match="no Short form"):
            run(language_code=code, anki_collection_path=tmp_path / "x", anki_backup_dir=tmp_path / "b")
