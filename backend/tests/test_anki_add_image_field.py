"""Tests for app.plugins.anki_sync.add_image_field.

Schema migration: give an imported two-card notetype that has no ``Image`` field
(Pimsleur Tagalog's genanki export) somewhere to hold a picture, and show it on
the cards. Without the field, drawn pictures are stored in TT and silently
dropped by the next sync (tunatale-ejwh).

The synthetic collection names its notetype exactly as the real one so the real
Tagalog ``field_map`` profile is resolved — no internal function is faked.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.cards.field_map import get_profile
from app.cards.vocab_notetype import build_notetype_config, build_template_config
from app.plugins.anki_sync.add_image_field import IMAGE_BLOCK, IMAGE_CSS, add_image_field, run
from app.plugins.anki_sync.add_production_template import IMAGE_FIELD
from app.srs.anki_mirror.protobuf_wire import find_len_field

PIMSLEUR_MID = 1695000000001
OTHER_MID = 1695000000002
NOTETYPE = "Basic (and reversed card) (genanki)"
LANGUAGE = "tl"

PROFILE = get_profile(NOTETYPE, LANGUAGE)
assert PROFILE is not None, "Tagalog must keep its profile for the Pimsleur notetype"
assert PROFILE.recognition_ord == 1, "Pimsleur's ord 0 is English -> Tagalog (production)"

# Shaped like the genanki export: ord 0 English -> Tagalog, ord 1 the reverse.
PROD_QFMT = "{{Front}}"
PROD_AFMT = '{{FrontSide}}<hr id="answer">{{Back}}{{Audio}}'
RECOG_QFMT = "{{Back}}{{Audio}}"
RECOG_AFMT = '{{FrontSide}}<hr id="answer">{{Front}}'
CSS = ".card { font-family: arial; }"

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


def _populate(conn: sqlite3.Connection, *, notes: int = 3) -> None:
    conn.execute("INSERT INTO col VALUES (1, 0, 100, 1000, 18, 0, 5, 0, '{}', '{}', '{}', '{}', '{}')")
    conn.execute("INSERT INTO notetypes VALUES (?, ?, 50, 0, ?)", (PIMSLEUR_MID, NOTETYPE, build_notetype_config(CSS)))
    for ord_, name in enumerate(("Front", "Back", "Audio")):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, X'')", (PIMSLEUR_MID, ord_, name))
    conn.execute(
        "INSERT INTO templates VALUES (?, 0, 'Card 1', 50, 0, ?)",
        (PIMSLEUR_MID, build_template_config(PROD_QFMT, PROD_AFMT)),
    )
    conn.execute(
        "INSERT INTO templates VALUES (?, 1, 'Card 2', 50, 0, ?)",
        (PIMSLEUR_MID, build_template_config(RECOG_QFMT, RECOG_AFMT)),
    )

    # Control: another notetype whose notes and templates must not move.
    conn.execute("INSERT INTO notetypes VALUES (?, 'Other', 50, 0, ?)", (OTHER_MID, build_notetype_config(CSS)))
    for ord_, name in enumerate(("Front", "Back")):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, X'')", (OTHER_MID, ord_, name))
    conn.execute(
        "INSERT INTO templates VALUES (?, 0, 'Card 1', 50, 0, ?)",
        (OTHER_MID, build_template_config("{{Front}}", "{{Back}}")),
    )
    conn.execute("INSERT INTO notes VALUES (9000, 'g-other', ?, 100, 7, '', 'a\x1fb', 'a', 0, 0, '')", (OTHER_MID,))

    for i in range(notes):
        flds = "\x1f".join((f"english {i}", f"tagalog {i}<br>", f"[sound:{i}.mp3]"))
        conn.execute(
            "INSERT INTO notes VALUES (?, ?, ?, 100, 7, '', ?, ?, 0, 0, '')",
            (1000 + i, f"g{i}", PIMSLEUR_MID, flds, f"english {i}"),
        )
        for ord_ in (0, 1):
            conn.execute(
                "INSERT INTO cards VALUES (?, ?, 1, ?, 100, 7, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
                (2000 + 10 * i + ord_, 1000 + i, ord_, i),
            )
    conn.commit()


def _make_conn(*, notes: int = 3) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _populate(conn, notes=notes)
    return conn


def _template(conn: sqlite3.Connection, mid: int, ord_: int) -> tuple[str, str]:
    cfg = bytes(conn.execute("SELECT config FROM templates WHERE ntid=? AND ord=?", (mid, ord_)).fetchone()[0])
    return find_len_field(cfg, 1).decode(), find_len_field(cfg, 2).decode()


def _css(conn: sqlite3.Connection, mid: int) -> str:
    cfg = bytes(conn.execute("SELECT config FROM notetypes WHERE id=?", (mid,)).fetchone()[0])
    return find_len_field(cfg, 3).decode()


class TestAddImageField:
    def test_appends_the_image_field_last(self):
        conn = _make_conn()
        assert add_image_field(conn, NOTETYPE, PROFILE, now_ms=1_700_000_000_000) == "created"
        names = [r[0] for r in conn.execute("SELECT name FROM fields WHERE ntid=? ORDER BY ord", (PIMSLEUR_MID,))]
        assert names == ["Front", "Back", "Audio", IMAGE_FIELD]

    def test_every_note_gains_exactly_one_empty_trailing_field(self):
        conn = _make_conn()
        add_image_field(conn, NOTETYPE, PROFILE, now_ms=1_700_000_000_000)
        for row in conn.execute("SELECT id, flds FROM notes WHERE mid=?", (PIMSLEUR_MID,)):
            parts = row["flds"].split("\x1f")
            i = row["id"] - 1000
            assert parts == [f"english {i}", f"tagalog {i}<br>", f"[sound:{i}.mp3]", ""]

    def test_marks_touched_notes_dirty_and_leaves_others_alone(self):
        conn = _make_conn()
        add_image_field(conn, NOTETYPE, PROFILE, now_ms=1_700_000_000_000)
        touched = conn.execute("SELECT DISTINCT usn, mod FROM notes WHERE mid=?", (PIMSLEUR_MID,)).fetchall()
        assert [tuple(r) for r in touched] == [(-1, 1_700_000_000)]
        other = conn.execute("SELECT flds, usn, mod FROM notes WHERE id=9000").fetchone()
        assert tuple(other) == ("a\x1fb", 7, 100)

    def test_production_card_is_fronted_by_the_picture_above_the_english(self):
        conn = _make_conn()
        add_image_field(conn, NOTETYPE, PROFILE, now_ms=1_700_000_000_000)
        qfmt, afmt = _template(conn, PIMSLEUR_MID, 0)
        assert qfmt == IMAGE_BLOCK + PROD_QFMT
        # The back already repeats the front via {{FrontSide}}; nothing added twice.
        assert afmt == PROD_AFMT

    def test_recognition_card_shows_the_picture_only_on_the_answer(self):
        conn = _make_conn()
        add_image_field(conn, NOTETYPE, PROFILE, now_ms=1_700_000_000_000)
        qfmt, afmt = _template(conn, PIMSLEUR_MID, 1)
        assert qfmt == RECOG_QFMT
        assert afmt == RECOG_AFMT + IMAGE_BLOCK

    def test_image_block_is_conditional_so_pictureless_cards_render_as_before(self):
        assert IMAGE_BLOCK.startswith("{{#" + IMAGE_FIELD + "}}")
        assert IMAGE_BLOCK.endswith("{{/" + IMAGE_FIELD + "}}")

    def test_css_gains_the_image_size_rule(self):
        conn = _make_conn()
        add_image_field(conn, NOTETYPE, PROFILE, now_ms=1_700_000_000_000)
        assert _css(conn, PIMSLEUR_MID) == CSS + IMAGE_CSS

    def test_bumps_template_notetype_and_col_schema(self):
        conn = _make_conn()
        add_image_field(conn, NOTETYPE, PROFILE, now_ms=1_700_000_000_000)
        tmpl = conn.execute("SELECT DISTINCT mtime_secs, usn FROM templates WHERE ntid=?", (PIMSLEUR_MID,)).fetchall()
        assert [tuple(r) for r in tmpl] == [(1_700_000_000, -1)]
        nt = conn.execute("SELECT mtime_secs, usn FROM notetypes WHERE id=?", (PIMSLEUR_MID,)).fetchone()
        assert tuple(nt) == (1_700_000_000, -1)
        col = conn.execute("SELECT scm, mod, usn FROM col").fetchone()
        # col.usn is the sync anchor and must survive (Layer 61).
        assert tuple(col) == (1_700_000_000_000, 1_700_000_000_000, 5)

    def test_other_notetypes_templates_and_css_do_not_move(self):
        conn = _make_conn()
        add_image_field(conn, NOTETYPE, PROFILE, now_ms=1_700_000_000_000)
        assert _template(conn, OTHER_MID, 0) == ("{{Front}}", "{{Back}}")
        assert _css(conn, OTHER_MID) == CSS
        assert tuple(conn.execute("SELECT mtime_secs, usn FROM notetypes WHERE id=?", (OTHER_MID,)).fetchone()) == (
            50,
            0,
        )

    def test_mints_no_cards_and_touches_no_card_rows(self):
        conn = _make_conn()
        before = conn.execute("SELECT * FROM cards ORDER BY id").fetchall()
        add_image_field(conn, NOTETYPE, PROFILE, now_ms=1_700_000_000_000)
        assert [tuple(r) for r in conn.execute("SELECT * FROM cards ORDER BY id")] == [tuple(r) for r in before]

    def test_idempotent(self):
        conn = _make_conn()
        add_image_field(conn, NOTETYPE, PROFILE, now_ms=1_700_000_000_000)
        assert add_image_field(conn, NOTETYPE, PROFILE, now_ms=1_800_000_000_000) == "exists"
        assert conn.execute("SELECT scm FROM col").fetchone()[0] == 1_700_000_000_000
        assert _template(conn, PIMSLEUR_MID, 0)[0] == IMAGE_BLOCK + PROD_QFMT
        assert all(r[0].count("\x1f") == 3 for r in conn.execute("SELECT flds FROM notes WHERE mid=?", (PIMSLEUR_MID,)))

    def test_raises_for_unknown_notetype(self):
        with pytest.raises(ValueError, match="not found"):
            add_image_field(_make_conn(), "Nope", PROFILE, now_ms=1)

    def test_refuses_a_single_template_notetype(self):
        # One template means no production card to front: that is the other
        # migration's job (add_production_template), not this one.
        conn = _make_conn()
        conn.execute("DELETE FROM templates WHERE ntid=? AND ord=1", (PIMSLEUR_MID,))
        with pytest.raises(ValueError, match="add_production_template"):
            add_image_field(conn, NOTETYPE, PROFILE, now_ms=1)
        assert conn.execute("SELECT COUNT(*) FROM fields WHERE ntid=?", (PIMSLEUR_MID,)).fetchone()[0] == 3

    def test_raises_when_a_template_has_no_text_to_edit(self):
        conn = _make_conn()
        conn.execute("UPDATE templates SET config = X'' WHERE ntid=? AND ord=0", (PIMSLEUR_MID,))
        with pytest.raises(ValueError, match="template text"):
            add_image_field(conn, NOTETYPE, PROFILE, now_ms=1)
        assert conn.execute("SELECT COUNT(*) FROM fields WHERE ntid=?", (PIMSLEUR_MID,)).fetchone()[0] == 3

    def test_defaults_now_ms_to_wall_clock(self):
        conn = _make_conn()
        add_image_field(conn, NOTETYPE, PROFILE)
        assert conn.execute("SELECT scm FROM col").fetchone()[0] > 1_700_000_000_000


def _build_collection_file(tmp_path: Path) -> Path:
    db_path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    _populate(conn)
    conn.close()
    return db_path


class TestRun:
    def test_adds_the_field_in_the_collection_file(self, tmp_path, capsys):
        db_path = _build_collection_file(tmp_path)
        result = run(
            notetype_name=NOTETYPE,
            language_code=LANGUAGE,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
        )
        assert result == "created"
        conn = sqlite3.connect(str(db_path))
        try:
            assert conn.execute("SELECT COUNT(*) FROM fields WHERE ntid=?", (PIMSLEUR_MID,)).fetchone()[0] == 4
        finally:
            conn.close()
        out = capsys.readouterr().out
        assert "Upload to AnkiWeb" in out
        assert "normalize_usns" in out
        assert "--bootstrap" in out

    def test_second_run_reports_exists(self, tmp_path, capsys):
        db_path = _build_collection_file(tmp_path)
        run(
            notetype_name=NOTETYPE, language_code=LANGUAGE, anki_collection_path=db_path, anki_backup_dir=tmp_path / "1"
        )
        result = run(
            notetype_name=NOTETYPE, language_code=LANGUAGE, anki_collection_path=db_path, anki_backup_dir=tmp_path / "2"
        )
        assert result == "exists"
        assert "[SKIP]" in capsys.readouterr().out

    @pytest.mark.parametrize(("migrated_first", "expected"), [(False, "created"), (True, "exists")])
    def test_dry_run_reports_and_changes_nothing(self, tmp_path, capsys, migrated_first, expected):
        db_path = _build_collection_file(tmp_path)
        if migrated_first:
            run(
                notetype_name=NOTETYPE,
                language_code=LANGUAGE,
                anki_collection_path=db_path,
                anki_backup_dir=tmp_path / "1",
            )
        conn = sqlite3.connect(str(db_path))
        scm_before = conn.execute("SELECT scm FROM col").fetchone()[0]
        conn.close()

        result = run(
            notetype_name=NOTETYPE,
            language_code=LANGUAGE,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "2",
            dry_run=True,
        )

        assert result == "dry-run"
        assert f"would be: {expected}" in capsys.readouterr().out
        conn = sqlite3.connect(str(db_path))
        try:
            assert conn.execute("SELECT scm FROM col").fetchone()[0] == scm_before
        finally:
            conn.close()

    def test_raises_when_the_language_has_no_profile_for_the_notetype(self, tmp_path):
        # The genanki name is generic; only the language that owns the deck may
        # claim it (tunatale-w4m7.8), so a wrong --language must not resolve.
        db_path = _build_collection_file(tmp_path)
        with pytest.raises(ValueError, match="no field-role profile"):
            run(
                notetype_name=NOTETYPE,
                language_code="no",
                anki_collection_path=db_path,
                anki_backup_dir=tmp_path / "bak",
            )

    def test_uses_settings_defaults_when_paths_none(self, tmp_path, monkeypatch):
        import app.plugins.anki_sync.add_image_field as mod

        db_path = _build_collection_file(tmp_path)

        class _FakeSettings:
            anki_collection_path = db_path
            anki_backup_dir = tmp_path / "bak_settings"

        monkeypatch.setattr(mod, "settings", _FakeSettings())
        assert run(notetype_name=NOTETYPE, language_code=LANGUAGE) == "created"
