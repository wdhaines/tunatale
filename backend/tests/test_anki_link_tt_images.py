"""Tests for app.anki.link_tt_images.

Backfill missing TT-side image media rows for 7 Slovene-Voc notes where
Anki has an image but TT has no `media` row of kind='image'. Without the
TT row, DrillCard.svelte falls back to translation text on the Production
card (e.g., shows "name" for ``ime`` instead of an image).

Root cause: TT's `media` table is populated only at /listen-time card
creation. Anki Image field edits never propagate back to TT.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.anki.link_tt_images import (
    LINK_OPS,
    LinkImageOp,
    apply_link_image,
    main,
)
from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME

_SV_MID = 1776536503963
_DECK_ID = 1
_DECK_NAME = "0. Slovene"


def _make_anki_db(tmp_path: Path) -> Path:
    db = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER,
            ver INTEGER, dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT,
            models TEXT, decks TEXT, dconf TEXT, tags TEXT);
        CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT UNIQUE, mid INTEGER,
            mod INTEGER, usn INTEGER, tags TEXT, flds TEXT, sfld TEXT,
            csum INTEGER, flags INTEGER, data TEXT);
        CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER,
            ord INTEGER, mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER,
            due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER,
            lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER,
            flags INTEGER, data TEXT);
        CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT,
            mtime_secs INTEGER, usn INTEGER, common BLOB, kind BLOB);
        CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT,
            mtime_secs INTEGER, usn INTEGER, config BLOB);
        CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB,
            PRIMARY KEY (ntid, ord));
    """)
    conn.execute("INSERT INTO col VALUES (1,0,100,500,18,0,7,0,'{}','{}','{}','{}','{}')")
    conn.execute("INSERT INTO decks VALUES (?, ?, 0, 0, x'', x'')", (_DECK_ID, _DECK_NAME))
    conn.execute("INSERT INTO notetypes VALUES (?, ?, 0, 0, x'')", (_SV_MID, SLOVENE_VOCAB_NOTETYPE_NAME))
    for i, name in enumerate(["Slovene", "English", "Audio", "Image", "Grammar", "Note", "DisambigKey"]):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, x'')", (_SV_MID, i, name))
    conn.commit()
    conn.close()
    return db


def _make_tt_db(tmp_path: Path) -> Path:
    db = tmp_path / "tt.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE collocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT UNIQUE NOT NULL,
            translation TEXT NOT NULL DEFAULT '',
            language_code TEXT NOT NULL DEFAULT 'sl',
            anki_note_id INTEGER
        );
        CREATE TABLE media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collocation_id INTEGER,
            kind TEXT NOT NULL,
            filename TEXT NOT NULL,
            anki_filename TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db


def _add_anki_note(anki_path: Path, nid: int, slovene: str, english: str, image_html: str) -> int:
    conn = sqlite3.connect(str(anki_path))
    flds = "\x1f".join([slovene, english, "", image_html, "", "", ""])
    conn.execute(
        "INSERT INTO notes VALUES (?, ?, ?, 0, 0, '', ?, ?, 0, 0, '')",
        (nid, f"guid_{nid}", _SV_MID, flds, slovene),
    )
    conn.execute(
        "INSERT INTO cards VALUES (?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 2500, 0, 0, 0, 0, 0, 0, '')",
        (nid, nid, _DECK_ID),
    )
    conn.commit()
    conn.close()
    return nid


def _add_tt_coll(tt_path: Path, text: str, *, anki_note_id: int, translation: str = "") -> int:
    conn = sqlite3.connect(str(tt_path))
    cur = conn.execute(
        "INSERT INTO collocations (text, translation, anki_note_id) VALUES (?, ?, ?)",
        (text, translation, anki_note_id),
    )
    cid = cur.lastrowid
    conn.commit()
    conn.close()
    return cid


# ── LINK_OPS constant ────────────────────────────────────────────────────────


def test_link_ops_has_7_entries():
    assert len(LINK_OPS) == 7


def test_link_ops_targets_expected_nids():
    expected = {
        1774631982063,  # nič
        1775264031772,  # krilo (cid 719)
        1775264031808,  # ulica (cid 263)
        1775264032856,  # nositi (cid 717)
        1775264032872,  # visok (cid 718)
        1778267269399,  # ime
        1778267277826,  # časa
    }
    assert {op.anki_nid for op in LINK_OPS} == expected


def test_ulica_has_translation_override():
    """Only ulica's TT translation is empty in production; we backfill it."""
    ulica = next(op for op in LINK_OPS if op.anki_nid == 1775264031808)
    assert ulica.translation_override is not None
    assert ulica.translation_override.strip() != ""


def test_non_ulica_ops_have_no_translation_override():
    for op in LINK_OPS:
        if op.anki_nid != 1775264031808:
            assert op.translation_override is None


# ── apply_link_image ─────────────────────────────────────────────────────────


def test_apply_inserts_tt_media_row(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1778267269399
    _add_anki_note(anki_path, nid, "ime", "name", '<img src="img_time.jpg">')
    tt_cid = _add_tt_coll(tt_path, "ime", anki_note_id=nid, translation="name")

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    op = LinkImageOp(anki_nid=nid, image_filename="img_time.jpg")
    result = apply_link_image(anki, tt, op)
    assert result is True

    rows = tt.execute(
        "SELECT collocation_id, kind, filename, anki_filename FROM media WHERE collocation_id=?", (tt_cid,)
    ).fetchall()
    assert rows == [(tt_cid, "image", "img_time.jpg", "img_time.jpg")]


def test_apply_is_idempotent(tmp_path):
    """Running twice doesn't duplicate the media row."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1778267269399
    _add_anki_note(anki_path, nid, "ime", "name", '<img src="img_time.jpg">')
    tt_cid = _add_tt_coll(tt_path, "ime", anki_note_id=nid, translation="name")

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    op = LinkImageOp(anki_nid=nid, image_filename="img_time.jpg")
    apply_link_image(anki, tt, op)
    apply_link_image(anki, tt, op)
    assert tt.execute("SELECT COUNT(*) FROM media WHERE collocation_id=?", (tt_cid,)).fetchone()[0] == 1


def test_apply_skips_when_tt_collocation_missing(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    _add_anki_note(anki_path, 999, "x", "y", '<img src="z.jpg">')
    # No TT collocation.
    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    op = LinkImageOp(anki_nid=999, image_filename="z.jpg")
    result = apply_link_image(anki, tt, op)
    assert result is False
    assert tt.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0


def test_apply_does_not_overwrite_existing_image_row(tmp_path):
    """If TT already has an image media row (different filename), leave it.

    The DIVERGENT-75 case is out of scope here; this script only fills the
    BROKEN-7 gap. The user gets a separate decision on those.
    """
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1778267269399
    _add_anki_note(anki_path, nid, "ime", "name", '<img src="img_time.jpg">')
    tt_cid = _add_tt_coll(tt_path, "ime", anki_note_id=nid)
    conn = sqlite3.connect(str(tt_path))
    conn.execute(
        "INSERT INTO media (collocation_id, kind, filename, anki_filename) VALUES (?, 'image', 'old.jpg', 'old.jpg')",
        (tt_cid,),
    )
    conn.commit()
    conn.close()

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    op = LinkImageOp(anki_nid=nid, image_filename="img_time.jpg")
    apply_link_image(anki, tt, op)
    rows = tt.execute("SELECT filename FROM media WHERE collocation_id=?", (tt_cid,)).fetchall()
    assert rows == [("old.jpg",)]  # untouched


def test_apply_backfills_empty_translation(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1775264031808
    _add_anki_note(anki_path, nid, "ulica", "", '<img src="paste-x.jpg">')
    tt_cid = _add_tt_coll(tt_path, "ulica", anki_note_id=nid, translation="")

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    op = LinkImageOp(anki_nid=nid, image_filename="paste-x.jpg", translation_override="street")
    apply_link_image(anki, tt, op)

    tt_trans = tt.execute("SELECT translation FROM collocations WHERE id=?", (tt_cid,)).fetchone()[0]
    assert tt_trans == "street"


def test_apply_backfills_translation_in_anki_too(tmp_path):
    """Anki's English field gets the same translation override (was empty)."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1775264031808
    _add_anki_note(anki_path, nid, "ulica", "", '<img src="paste-x.jpg">')
    _add_tt_coll(tt_path, "ulica", anki_note_id=nid, translation="")

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    op = LinkImageOp(anki_nid=nid, image_filename="paste-x.jpg", translation_override="street")
    apply_link_image(anki, tt, op)

    flds = anki.execute("SELECT flds, usn FROM notes WHERE id=?", (nid,)).fetchone()
    assert flds[0].split("\x1f")[1] == "street"
    assert flds[1] == -1  # marked dirty


def test_apply_does_not_overwrite_existing_translation(tmp_path):
    """If TT translation is already populated, don't overwrite even if override is set."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1775264031808
    _add_anki_note(anki_path, nid, "ulica", "", '<img src="paste-x.jpg">')
    tt_cid = _add_tt_coll(tt_path, "ulica", anki_note_id=nid, translation="pre-existing")

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    op = LinkImageOp(anki_nid=nid, image_filename="paste-x.jpg", translation_override="street")
    apply_link_image(anki, tt, op)

    assert tt.execute("SELECT translation FROM collocations WHERE id=?", (tt_cid,)).fetchone()[0] == "pre-existing"


# ── main ─────────────────────────────────────────────────────────────────────


def test_main_dry_run_does_not_mutate(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1778267269399
    _add_anki_note(anki_path, nid, "ime", "name", '<img src="img_time.jpg">')
    _add_tt_coll(tt_path, "ime", anki_note_id=nid, translation="name")

    rc = main(["--dry-run", "--anki-db", str(anki_path), "--tt-db", str(tt_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Plan:" in out
    tt = sqlite3.connect(str(tt_path))
    assert tt.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0


def test_main_apply_writes(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1778267269399
    _add_anki_note(anki_path, nid, "ime", "name", '<img src="img_time.jpg">')
    tt_cid = _add_tt_coll(tt_path, "ime", anki_note_id=nid, translation="name")

    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path)])
    assert rc == 0
    assert "Applied:" in capsys.readouterr().out
    tt = sqlite3.connect(str(tt_path))
    rows = tt.execute("SELECT filename FROM media WHERE collocation_id=?", (tt_cid,)).fetchall()
    assert rows == [("img_time.jpg",)]


def test_apply_translation_skips_anki_when_note_deleted(tmp_path):
    """TT collocation points at a nid that no longer exists in Anki — translation
    still gets backfilled on TT side; Anki update silently skipped."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    # No Anki note added — just a TT collocation pointing at a dead nid.
    nid = 1775264031808
    tt_cid = _add_tt_coll(tt_path, "ulica", anki_note_id=nid, translation="")

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    op = LinkImageOp(anki_nid=nid, image_filename="paste-x.jpg", translation_override="street")
    apply_link_image(anki, tt, op)

    assert tt.execute("SELECT translation FROM collocations WHERE id=?", (tt_cid,)).fetchone()[0] == "street"


def test_apply_does_not_touch_anki_english_when_already_filled(tmp_path):
    """If Anki's English is non-empty, leave it alone even with translation_override set."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1775264031808
    _add_anki_note(anki_path, nid, "ulica", "pre-filled", '<img src="paste-x.jpg">')
    _add_tt_coll(tt_path, "ulica", anki_note_id=nid, translation="")

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    op = LinkImageOp(anki_nid=nid, image_filename="paste-x.jpg", translation_override="street")
    apply_link_image(anki, tt, op)

    flds = anki.execute("SELECT flds FROM notes WHERE id=?", (nid,)).fetchone()[0]
    assert flds.split("\x1f")[1] == "pre-filled"  # Anki untouched


def test_main_counts_translation_backfills_via_main(tmp_path, capsys):
    """End-to-end through main: empty TT translation → counted as backfilled."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1775264031808
    _add_anki_note(anki_path, nid, "ulica", "", '<img src="paste-532408af9dd4fbb6b3ef2be7433214251871a125.jpg">')
    _add_tt_coll(tt_path, "ulica", anki_note_id=nid, translation="")

    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path)])
    assert rc == 0
    assert "'translations_backfilled': 1" in capsys.readouterr().out


def test_main_returns_0_when_nothing_applicable(tmp_path, capsys):
    """All targets absent → main returns 0 with zero counts."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path)])
    assert rc == 0
    assert "Applied: {'media_linked': 0, 'translations_backfilled': 0}" in capsys.readouterr().out


def test_main_returns_1_when_anki_path_missing(tmp_path):
    rc = main(["--anki-db", str(tmp_path / "x.db"), "--tt-db", str(tmp_path / "y.db")])
    assert rc == 1


def test_main_returns_1_when_tt_path_missing(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tmp_path / "y.db")])
    assert rc == 1
