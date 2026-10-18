"""Tests for app.plugins.anki_sync.add_dictionary_fields (tunatale-h0q3.2).

Schema migration: the live "Cebuano Vocabulary" notetype predates the
dictionary layers, so it gains the five fields and the template blocks that a
freshly created one has. The oracle is ``create_vocab_notetype`` itself: after
the migration, the old notetype must render exactly as a new one would.

The base notetype is built from ``CEBUANO_VOCAB`` with the layers turned off.
Its template text is pinned below as read from the laptop live instance's
tt_collection on 2026-10-18, so a generator change that stops matching the
live deck shows up here rather than as a refused migration.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest

from app.cards.vocab_notetype import CEBUANO_VOCAB, DICTIONARY_FIELDS, create_vocab_notetype
from app.plugins.anki_sync.add_dictionary_fields import add_dictionary_fields, run
from app.srs.anki_mirror.protobuf_wire import find_len_field

MID = 1790361065576
OTHER_MID = 1790000000001
NOW_MS = 1_800_000_000_000
BASE = dataclasses.replace(CEBUANO_VOCAB, dictionary=False)

# As read from the live notetype (2026-10-18).
LIVE_RECOGNITION_QFMT = '{{Audio}}<div class="cebuano">{{Cebuano}}</div>'
LIVE_PRODUCTION_AFMT = (
    '{{FrontSide}}<hr id="answer">{{Audio}}<div class="cebuano">{{Cebuano}}</div>'
    '<div class="english">{{English}}</div><div class="gram">{{Grammar}}</div><div class="note">{{Note}}</div>'
)

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
    create_vocab_notetype(conn, BASE, MID, 50)
    conn.execute("UPDATE notetypes SET usn = 0")
    conn.execute("UPDATE templates SET usn = 0")
    # Control: another TT notetype, whose rows must not move.
    create_vocab_notetype(conn, dataclasses.replace(BASE, name="Other Vocabulary"), OTHER_MID, 50)
    conn.execute("UPDATE notetypes SET usn = 0 WHERE id = ?", (OTHER_MID,))
    conn.execute(
        "INSERT INTO notes VALUES (9000, 'g-other', ?, 100, 7, '', ?, 'a', 0, 0, '')", (OTHER_MID, "a" + "\x1f" * 6)
    )
    for i in range(3):
        flds = "\x1f".join((f"pulong{i}", f"word {i}", "", "", "", "", ""))
        conn.execute(
            "INSERT INTO notes VALUES (?, ?, ?, 100, 7, '', ?, ?, 0, 0, '')",
            (1000 + i, f"g{i}", MID, flds, f"pulong{i}"),
        )
        for ord_ in (0, 1):
            conn.execute(
                "INSERT INTO cards VALUES (?, ?, 1, ?, 100, 7, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
                (2000 + 10 * i + ord_, 1000 + i, ord_, i),
            )
    conn.commit()


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _populate(conn)
    return conn


def _templates(conn: sqlite3.Connection, mid: int) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT config FROM templates WHERE ntid = ? ORDER BY ord", (mid,)).fetchall()
    return [(find_len_field(bytes(r[0]), 1).decode(), find_len_field(bytes(r[0]), 2).decode()) for r in rows]


def _css(conn: sqlite3.Connection, mid: int) -> str:
    return find_len_field(
        bytes(conn.execute("SELECT config FROM notetypes WHERE id=?", (mid,)).fetchone()[0]), 3
    ).decode()


def _fresh() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    create_vocab_notetype(conn, CEBUANO_VOCAB, MID, 50)
    return conn


def test_the_base_notetype_matches_the_live_one():
    (rq, _), (_, pa) = _templates(_make_conn(), MID)
    assert (rq, pa) == (LIVE_RECOGNITION_QFMT, LIVE_PRODUCTION_AFMT)


class TestAddDictionaryFields:
    def test_appends_the_five_fields_after_disambigkey(self):
        conn = _make_conn()
        assert add_dictionary_fields(conn, CEBUANO_VOCAB, now_ms=NOW_MS) == "created"
        names = [r[0] for r in conn.execute("SELECT name FROM fields WHERE ntid=? ORDER BY ord", (MID,))]
        assert tuple(names) == CEBUANO_VOCAB.field_names
        assert tuple(names[-5:]) == DICTIONARY_FIELDS

    def test_templates_and_css_end_up_exactly_as_a_new_notetype_has_them(self):
        conn = _make_conn()
        add_dictionary_fields(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        fresh = _fresh()
        assert _templates(conn, MID) == _templates(fresh, MID)
        assert _css(conn, MID) == _css(fresh, MID)

    def test_every_note_gains_five_empty_trailing_fields_and_keeps_its_own(self):
        conn = _make_conn()
        add_dictionary_fields(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        for row in conn.execute("SELECT id, flds, sfld FROM notes WHERE mid=?", (MID,)):
            i = row["id"] - 1000
            assert row["flds"].split("\x1f") == [f"pulong{i}", f"word {i}", "", "", "", "", "", "", "", "", "", ""]
            assert row["sfld"] == f"pulong{i}"

    def test_marks_touched_notes_dirty_and_leaves_others_alone(self):
        conn = _make_conn()
        add_dictionary_fields(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        touched = conn.execute("SELECT DISTINCT usn, mod FROM notes WHERE mid=?", (MID,)).fetchall()
        assert [tuple(r) for r in touched] == [(-1, NOW_MS // 1000)]
        assert tuple(conn.execute("SELECT usn, mod FROM notes WHERE id=9000").fetchone()) == (7, 100)

    def test_bumps_templates_notetype_and_col_schema_but_not_col_usn(self):
        conn = _make_conn()
        add_dictionary_fields(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        tmpl = conn.execute("SELECT DISTINCT mtime_secs, usn FROM templates WHERE ntid=?", (MID,)).fetchall()
        assert [tuple(r) for r in tmpl] == [(NOW_MS // 1000, -1)]
        assert tuple(conn.execute("SELECT mtime_secs, usn FROM notetypes WHERE id=?", (MID,)).fetchone()) == (
            NOW_MS // 1000,
            -1,
        )
        # col.usn is the sync anchor and must survive (Layer 61).
        assert tuple(conn.execute("SELECT scm, mod, usn FROM col").fetchone()) == (NOW_MS, NOW_MS, 5)

    def test_other_notetypes_do_not_move(self):
        conn = _make_conn()
        before = (_templates(conn, OTHER_MID), _css(conn, OTHER_MID))
        add_dictionary_fields(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert (_templates(conn, OTHER_MID), _css(conn, OTHER_MID)) == before
        assert conn.execute("SELECT COUNT(*) FROM fields WHERE ntid=?", (OTHER_MID,)).fetchone()[0] == 7
        assert tuple(conn.execute("SELECT mtime_secs, usn FROM notetypes WHERE id=?", (OTHER_MID,)).fetchone()) == (
            50,
            0,
        )

    def test_touches_no_card_rows(self):
        conn = _make_conn()
        before = [tuple(r) for r in conn.execute("SELECT * FROM cards ORDER BY id")]
        add_dictionary_fields(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert [tuple(r) for r in conn.execute("SELECT * FROM cards ORDER BY id")] == before

    def test_idempotent(self):
        conn = _make_conn()
        add_dictionary_fields(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert add_dictionary_fields(conn, CEBUANO_VOCAB, now_ms=NOW_MS + 1000) == "exists"
        assert conn.execute("SELECT scm FROM col").fetchone()[0] == NOW_MS
        assert all(r[0].count("\x1f") == 11 for r in conn.execute("SELECT flds FROM notes WHERE mid=?", (MID,)))

    def test_raises_for_a_missing_notetype(self):
        conn = _make_conn()
        with pytest.raises(ValueError, match="not found"):
            add_dictionary_fields(conn, dataclasses.replace(CEBUANO_VOCAB, name="Nope"), now_ms=NOW_MS)

    def test_raises_for_a_notetype_without_layers(self):
        with pytest.raises(ValueError, match="no dictionary layers"):
            add_dictionary_fields(_make_conn(), BASE, now_ms=NOW_MS)

    def test_refuses_a_field_roster_it_does_not_recognise(self):
        # A hand-edited notetype (a field renamed, or some of the five added by
        # hand) is not something to append to blindly.
        conn = _make_conn()
        conn.execute("UPDATE fields SET name = 'Gloss' WHERE ntid=? AND ord=1", (MID,))
        with pytest.raises(ValueError, match="roster"):
            add_dictionary_fields(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert conn.execute("SELECT scm FROM col").fetchone()[0] == 1000

    def test_refuses_a_template_without_the_word_and_changes_nothing(self):
        conn = _make_conn()
        from app.cards.vocab_notetype import build_template_config

        conn.execute(
            "UPDATE templates SET config=? WHERE ntid=? AND ord=0",
            (build_template_config("{{Cebuano}}", "{{FrontSide}}"), MID),
        )
        with pytest.raises(ValueError, match="word"):
            add_dictionary_fields(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert conn.execute("SELECT COUNT(*) FROM fields WHERE ntid=?", (MID,)).fetchone()[0] == 7
        assert conn.execute("SELECT DISTINCT usn FROM notes WHERE mid=?", (MID,)).fetchall()[0][0] == 7

    def test_raises_when_a_template_has_no_text(self):
        conn = _make_conn()
        conn.execute("UPDATE templates SET config = X'' WHERE ntid=? AND ord=1", (MID,))
        with pytest.raises(ValueError, match="template text"):
            add_dictionary_fields(conn, CEBUANO_VOCAB, now_ms=NOW_MS)

    def test_defaults_now_ms_to_wall_clock(self):
        conn = _make_conn()
        add_dictionary_fields(conn, CEBUANO_VOCAB)
        assert conn.execute("SELECT scm FROM col").fetchone()[0] > 1_700_000_000_000


def _build_collection_file(tmp_path: Path) -> Path:
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
    def test_adds_the_fields_in_the_collection_file(self, tmp_path, capsys):
        db_path = _build_collection_file(tmp_path)
        assert run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak") == "created"
        assert _field_count(db_path) == 12
        out = capsys.readouterr().out
        assert "Upload to AnkiWeb" in out and "normalize_usns" in out and "--bootstrap" in out

    def test_second_run_reports_exists(self, tmp_path, capsys):
        db_path = _build_collection_file(tmp_path)
        run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "1")
        assert run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "2") == "exists"
        assert "[SKIP]" in capsys.readouterr().out

    @pytest.mark.parametrize(("migrated_first", "expected"), [(False, "created"), (True, "exists")])
    def test_dry_run_reports_and_changes_nothing(self, tmp_path, capsys, migrated_first, expected):
        db_path = _build_collection_file(tmp_path)
        if migrated_first:
            run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "1")
        before = _field_count(db_path)
        result = run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "2", dry_run=True)
        assert result == "dry-run"
        assert f"would be: {expected}" in capsys.readouterr().out
        assert _field_count(db_path) == before

    def test_raises_for_a_language_whose_cards_have_no_layers(self, tmp_path):
        db_path = _build_collection_file(tmp_path)
        with pytest.raises(ValueError, match="no dictionary layers"):
            run(language_code="no", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak")

    def test_uses_settings_defaults_when_paths_none(self, tmp_path, monkeypatch):
        import app.plugins.anki_sync.add_dictionary_fields as mod

        db_path = _build_collection_file(tmp_path)

        class _FakeSettings:
            anki_collection_path = db_path
            anki_backup_dir = tmp_path / "bak_settings"

        monkeypatch.setattr(mod, "settings", _FakeSettings())
        assert run(language_code="ceb") == "created"
