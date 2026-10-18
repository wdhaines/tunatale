"""Tests for app.plugins.anki_sync.backfill_dictionary_fields (tunatale-h0q3.2).

Data-only: after ``add_dictionary_fields`` gives the notetype its five fields,
this fills them on the notes that already exist, from the same registry facet
the mint path uses. A dry run is the default, and it lists the words Wolff
does not settle, for the owner. A field that already holds something is never
overwritten: the owner may have corrected it by hand.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.cards.vocab_notetype import CEBUANO_VOCAB, create_vocab_notetype
from app.languages import dictionary_note_fields
from app.plugins.anki_sync.backfill_dictionary_fields import apply_backfill, plan_backfill, run

MID = 1790361065576
NOW_MS = 1_800_000_000_000
NAMES = CEBUANO_VOCAB.field_names

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


def _flds(**values: str) -> str:
    return "\x1f".join(values.get(n.replace(" ", "_"), "") for n in NAMES)


def _populate(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO col VALUES (1, 0, 100, 1000, 18, 0, 5, 0, '{}', '{}', '{}', '{}', '{}')")
    create_vocab_notetype(conn, CEBUANO_VOCAB, MID, 50)
    notes = [
        (1, _flds(Cebuano="maayo", English="good")),
        (2, _flds(Cebuano="xyzzy", English="nothing at all")),
        # Already corrected by hand: its Spelling stays, the empty layers fill.
        (3, _flds(Cebuano="tan-aw", English="to look", Spelling="tán-aw (sic)")),
        (4, _flds(Cebuano="<b>tulo</b>", English="three<br>")),
    ]
    for nid, flds in notes:
        conn.execute(
            "INSERT INTO notes VALUES (?, ?, ?, 100, 7, '', ?, ?, 0, 0, '')", (nid, f"g{nid}", MID, flds, f"w{nid}")
        )
    conn.execute("INSERT INTO notetypes VALUES (2, 'Other', 50, 0, X'')")
    conn.execute("INSERT INTO notes VALUES (99, 'g99', 2, 100, 7, '', 'maayo\x1fgood', 'maayo', 0, 0, '')")
    conn.commit()


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _populate(conn)
    return conn


def _fields(conn: sqlite3.Connection, nid: int) -> dict[str, str]:
    return dict(
        zip(NAMES, conn.execute("SELECT flds FROM notes WHERE id=?", (nid,)).fetchone()[0].split("\x1f"), strict=True)
    )


class TestPlan:
    def test_plans_each_note_of_the_notetype(self):
        plan = plan_backfill(_make_conn(), CEBUANO_VOCAB, "ceb")
        assert sorted(u.note_id for u in plan.updates) == [1, 3, 4]
        assert [(m.word, m.english) for m in plan.misses] == [("xyzzy", "nothing at all")]

    def test_reads_the_word_and_english_as_the_pull_does(self):
        plan = plan_backfill(_make_conn(), CEBUANO_VOCAB, "ceb")
        tulo = next(u for u in plan.updates if u.note_id == 4)
        assert (tulo.word, tulo.english) == ("tulo", "three")
        assert tulo.fields == dictionary_note_fields("ceb", "tulo", "three")

    def test_never_overwrites_a_field_that_holds_something(self):
        plan = plan_backfill(_make_conn(), CEBUANO_VOCAB, "ceb")
        tan_aw = next(u for u in plan.updates if u.note_id == 3)
        assert "Spelling" not in tan_aw.fields
        assert tan_aw.fields["Meaning"]

    def test_a_note_already_filled_is_counted_not_planned(self):
        conn = _make_conn()
        apply_backfill(conn, plan_backfill(conn, CEBUANO_VOCAB, "ceb"), now_ms=NOW_MS)
        again = plan_backfill(conn, CEBUANO_VOCAB, "ceb")
        assert again.updates == []
        assert again.already_filled == 3
        assert [m.word for m in again.misses] == ["xyzzy"]

    def test_refuses_a_notetype_that_has_not_been_migrated(self):
        conn = _make_conn()
        conn.execute("DELETE FROM fields WHERE ntid=? AND ord >= 7", (MID,))
        with pytest.raises(ValueError, match="add_dictionary_fields"):
            plan_backfill(conn, CEBUANO_VOCAB, "ceb")

    def test_raises_for_a_missing_notetype(self):
        conn = _make_conn()
        conn.execute("DELETE FROM notetypes WHERE id=?", (MID,))
        with pytest.raises(ValueError, match="not found"):
            plan_backfill(conn, CEBUANO_VOCAB, "ceb")


class TestApply:
    def test_writes_the_planned_fields_and_leaves_the_rest(self):
        conn = _make_conn()
        apply_backfill(conn, plan_backfill(conn, CEBUANO_VOCAB, "ceb"), now_ms=NOW_MS)
        maayo = _fields(conn, 1)
        assert maayo["Spelling"] == "màáyo"
        assert {k: maayo[k] for k in dictionary_note_fields("ceb", "maayo", "good")} == dictionary_note_fields(
            "ceb", "maayo", "good"
        )
        assert (maayo["Cebuano"], maayo["English"]) == ("maayo", "good")
        assert _fields(conn, 3)["Spelling"] == "tán-aw (sic)"
        assert _fields(conn, 4)["Cebuano"] == "<b>tulo</b>"  # field 0 is never rewritten

    def test_marks_only_written_notes_dirty_and_bumps_col_mod_not_scm_or_usn(self):
        conn = _make_conn()
        apply_backfill(conn, plan_backfill(conn, CEBUANO_VOCAB, "ceb"), now_ms=NOW_MS)
        rows = {r["id"]: (r["usn"], r["mod"]) for r in conn.execute("SELECT id, usn, mod FROM notes")}
        assert rows == {
            1: (-1, NOW_MS // 1000),
            2: (7, 100),
            3: (-1, NOW_MS // 1000),
            4: (-1, NOW_MS // 1000),
            99: (7, 100),
        }
        # Data-only: no full upload (scm), and col.usn is the sync anchor (Layer 61).
        assert tuple(conn.execute("SELECT scm, mod, usn FROM col").fetchone()) == (1000, NOW_MS, 5)

    def test_an_empty_plan_writes_nothing(self):
        conn = _make_conn()
        apply_backfill(conn, plan_backfill(conn, CEBUANO_VOCAB, "ceb"), now_ms=NOW_MS)
        apply_backfill(conn, plan_backfill(conn, CEBUANO_VOCAB, "ceb"), now_ms=NOW_MS + 5000)
        assert conn.execute("SELECT mod FROM col").fetchone()[0] == NOW_MS

    def test_defaults_now_ms_to_wall_clock(self):
        conn = _make_conn()
        apply_backfill(conn, plan_backfill(conn, CEBUANO_VOCAB, "ceb"))
        assert conn.execute("SELECT mod FROM col").fetchone()[0] > 1_700_000_000_000


def _build_collection_file(tmp_path: Path) -> Path:
    db_path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    _populate(conn)
    conn.close()
    return db_path


def _spelling(db_path: Path, nid: int) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT flds FROM notes WHERE id=?", (nid,)).fetchone()[0].split("\x1f")[7]
    finally:
        conn.close()


class TestRun:
    def test_dry_run_is_the_default_and_lists_the_misses(self, tmp_path, capsys):
        db_path = _build_collection_file(tmp_path)
        result = run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak")
        assert (result.updates, result.misses) == (3, 1)
        assert _spelling(db_path, 1) == ""
        out = capsys.readouterr().out
        assert "[DRY RUN]" in out
        assert "xyzzy\tnothing at all" in out

    def test_apply_writes_the_collection_file(self, tmp_path, capsys):
        db_path = _build_collection_file(tmp_path)
        run(language_code="ceb", anki_collection_path=db_path, anki_backup_dir=tmp_path / "bak", apply=True)
        assert _spelling(db_path, 1) == "màáyo"
        assert "[DONE]" in capsys.readouterr().out

    def test_raises_for_a_language_whose_cards_have_no_layers(self, tmp_path):
        with pytest.raises(ValueError, match="no dictionary layers"):
            run(language_code="no", anki_collection_path=_build_collection_file(tmp_path), anki_backup_dir=tmp_path)

    def test_uses_settings_defaults_when_paths_none(self, tmp_path, monkeypatch):
        import app.plugins.anki_sync.backfill_dictionary_fields as mod

        db_path = _build_collection_file(tmp_path)

        class _FakeSettings:
            anki_collection_path = db_path
            anki_backup_dir = tmp_path / "bak_settings"

        monkeypatch.setattr(mod, "settings", _FakeSettings())
        assert run(language_code="ceb").updates == 3
