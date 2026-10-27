"""Tests for app.plugins.anki_sync.refresh_dictionary_css (tunatale-iiwm.7).

The Cebuano Verb field became a table (the owner's layout B3, 2026-10-28), and
the table's look lives in the notetype's CSS. A notetype keeps the CSS it was
given when its dictionary fields were added, so the live one needs its block
brought up to date. That is a data-only change: Anki syncs a notetype's CSS
incrementally, so ``col.scm`` must not move (a moved scm forces a full upload).

The oracle for the end state is ``create_vocab_notetype``: after the refresh,
the old notetype's CSS is what a new one gets.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest

from app.cards.vocab_notetype import CEBUANO_VOCAB, VERB_TABLE_CSS, build_notetype_config, create_vocab_notetype
from app.plugins.anki_sync.refresh_dictionary_css import refresh_dictionary_css, run
from app.srs.anki_mirror.protobuf_wire import find_len_field

MID = 1790361065576
OTHER_MID = 1790000000001
NOW_MS = 1_800_000_000_000

# The dictionary CSS add_dictionary_fields appended to the live notetype (2026-10-18), before the verb table.
LIVE_DICTIONARY_CSS = (
    ".spelling { font-size: 22px; color: #333; margin-top: 2px; }\n"
    ".meaning { font-size: 18px; margin-top: 8px; }\n"
    ".verb    { font-size: 15px; color: #555; }\n"
    "details.dict { text-align: left; font-size: 16px; margin-top: 8px; }\n"
    "details.dict summary { color: #36c; cursor: pointer; }\n"
    ".wolff-pos, .wolff-h { font-size: 13px; font-weight: bold; color: #555; margin-top: 6px; }\n"
    ".wolff-cite { font-size: 13px; color: #777; }\n"
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


def _css(conn: sqlite3.Connection, mid: int) -> str:
    config = bytes(conn.execute("SELECT config FROM notetypes WHERE id=?", (mid,)).fetchone()[0])
    return find_len_field(config, 3).decode()


def _set_css(conn: sqlite3.Connection, mid: int, css: str) -> None:
    conn.execute("UPDATE notetypes SET config = ? WHERE id = ?", (build_notetype_config(css), mid))


def _populate(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO col VALUES (1, 0, 100, 1000, 18, 0, 5, 0, '{}', '{}', '{}', '{}', '{}')")
    create_vocab_notetype(conn, CEBUANO_VOCAB, MID, 50)
    # the live notetype: its own CSS, then the dictionary block as it was appended, no verb table
    _set_css(conn, MID, _css(conn, MID).replace(VERB_TABLE_CSS, "").split(".spelling")[0] + LIVE_DICTIONARY_CSS)
    create_vocab_notetype(conn, dataclasses.replace(CEBUANO_VOCAB, name="Other Vocabulary"), OTHER_MID, 50)
    conn.execute("UPDATE notetypes SET usn = 0, mtime_secs = 50")
    conn.execute("INSERT INTO notes VALUES (1000, 'g', ?, 100, 7, '', 'x', 'x', 0, 0, '')", (MID,))
    conn.execute("INSERT INTO cards VALUES (2000, 1000, 1, 0, 100, 7, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')")
    conn.commit()


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    _populate(conn)
    return conn


def _new_notetype_css() -> str:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    create_vocab_notetype(conn, CEBUANO_VOCAB, MID, 50)
    return _css(conn, MID)


def test_a_new_notetype_carries_the_verb_table_css():
    assert VERB_TABLE_CSS in _new_notetype_css()
    for selector in ("table.wolff-verb", ".wolff-trigger", ".wolff-here", ".wolff-dim", ".wolff-ex"):
        assert selector in VERB_TABLE_CSS


class TestRefresh:
    def test_brings_the_live_css_to_what_a_new_notetype_has(self):
        conn = _make_conn()
        assert refresh_dictionary_css(conn, CEBUANO_VOCAB, now_ms=NOW_MS) == "updated"
        assert _css(conn, MID) == _new_notetype_css()

    def test_is_data_only_the_notetype_is_dirty_and_the_schema_does_not_move(self):
        conn = _make_conn()
        refresh_dictionary_css(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert conn.execute("SELECT mtime_secs, usn FROM notetypes WHERE id=?", (MID,)).fetchone() == (
            NOW_MS // 1000,
            -1,
        )
        # col.mod moves; col.scm (a full upload) and col.usn (the sync anchor, Layer 61) do not
        assert conn.execute("SELECT mod, scm, usn FROM col").fetchone() == (NOW_MS, 1000, 5)

    def test_touches_no_other_notetype_note_card_or_template(self):
        conn = _make_conn()
        before = {
            t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
            for t in ("notes", "cards", "templates", "fields")
        }
        other = conn.execute("SELECT * FROM notetypes WHERE id=?", (OTHER_MID,)).fetchone()
        refresh_dictionary_css(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in before} == before
        assert conn.execute("SELECT * FROM notetypes WHERE id=?", (OTHER_MID,)).fetchone() == other

    def test_idempotent(self):
        conn = _make_conn()
        refresh_dictionary_css(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert refresh_dictionary_css(conn, CEBUANO_VOCAB, now_ms=NOW_MS + 5000) == "current"
        assert conn.execute("SELECT mtime_secs FROM notetypes WHERE id=?", (MID,)).fetchone()[0] == NOW_MS // 1000
        assert conn.execute("SELECT mod FROM col").fetchone()[0] == NOW_MS

    def test_replaces_an_older_verb_table_block_rather_than_adding_a_second(self):
        conn = _make_conn()
        start = VERB_TABLE_CSS.splitlines()[0]
        end = VERB_TABLE_CSS.splitlines()[-1]
        _set_css(conn, MID, _css(conn, MID) + f"{start}\ntable.wolff-verb {{ color: red; }}\n{end}\n")
        conn.commit()
        assert refresh_dictionary_css(conn, CEBUANO_VOCAB, now_ms=NOW_MS) == "updated"
        assert _css(conn, MID) == _new_notetype_css()

    def test_refuses_a_block_without_its_end_marker_and_changes_nothing(self):
        conn = _make_conn()
        _set_css(conn, MID, _css(conn, MID) + VERB_TABLE_CSS.splitlines()[0] + "\ntable.wolff-verb { color: red; }\n")
        conn.commit()
        before = _css(conn, MID)
        with pytest.raises(ValueError, match="no end marker"):
            refresh_dictionary_css(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert _css(conn, MID) == before

    def test_refuses_a_notetype_without_its_dictionary_fields(self):
        conn = _make_conn()
        conn.execute("DELETE FROM fields WHERE ntid=? AND name='Verb'", (MID,))
        before = _css(conn, MID)
        with pytest.raises(ValueError, match="no Verb field"):
            refresh_dictionary_css(conn, CEBUANO_VOCAB, now_ms=NOW_MS)
        assert _css(conn, MID) == before

    def test_raises_for_a_missing_notetype(self):
        conn = _make_conn()
        with pytest.raises(ValueError, match="not found"):
            refresh_dictionary_css(conn, dataclasses.replace(CEBUANO_VOCAB, name="Nope"), now_ms=NOW_MS)

    def test_raises_for_a_notetype_without_layers(self):
        with pytest.raises(ValueError, match="no dictionary layers"):
            refresh_dictionary_css(_make_conn(), dataclasses.replace(CEBUANO_VOCAB, dictionary=False))

    def test_defaults_now_ms_to_wall_clock(self):
        conn = _make_conn()
        refresh_dictionary_css(conn, CEBUANO_VOCAB)
        assert conn.execute("SELECT mod FROM col").fetchone()[0] > NOW_MS - 10**12


def _build_collection_file(tmp_path: Path) -> Path:
    db_path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(db_path))
    _populate(conn)
    conn.close()
    return db_path


def _file_css(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        return _css(conn, MID)
    finally:
        conn.close()


class TestRun:
    def test_updates_the_css_in_the_collection_file(self, tmp_path, capsys):
        db_path = _build_collection_file(tmp_path)
        assert run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak") == "updated"
        assert VERB_TABLE_CSS in _file_css(db_path)
        out = capsys.readouterr().out
        assert "[DONE]" in out and "no full upload" in out

    def test_second_run_reports_current(self, tmp_path, capsys):
        db_path = _build_collection_file(tmp_path)
        run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "1")
        assert run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "2") == "current"
        assert "[SKIP]" in capsys.readouterr().out

    def test_dry_run_reports_and_changes_nothing(self, tmp_path, capsys):
        db_path = _build_collection_file(tmp_path)
        before = _file_css(db_path)
        result = run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "1", dry_run=True)
        assert result == "dry-run"
        assert "would be: updated" in capsys.readouterr().out
        assert _file_css(db_path) == before

    def test_dry_run_on_a_current_notetype_says_so(self, tmp_path, capsys):
        db_path = _build_collection_file(tmp_path)
        run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "1")
        run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "2", dry_run=True)
        assert "would be: current" in capsys.readouterr().out

    def test_raises_for_a_language_whose_cards_have_no_layers(self, tmp_path):
        db_path = _build_collection_file(tmp_path)
        with pytest.raises(ValueError, match="no dictionary layers"):
            run(language_code="no", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak")

    def test_uses_settings_defaults_when_paths_none(self, tmp_path, monkeypatch):
        import app.plugins.anki_sync.refresh_dictionary_css as mod

        db_path = _build_collection_file(tmp_path)

        class _FakeSettings:
            anki_collection_path = db_path
            anki_backup_dir = tmp_path / "bak_settings"

        monkeypatch.setattr(mod, "settings", _FakeSettings())
        assert run(language_code="ceb") == "updated"
