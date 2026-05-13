"""Tests for app.anki.cleanup_function_word_notes.

Final cleanup pass for the 5 function-word notes left after delete_phonology_demos:
- ja: fix Production card by setting Image=<img src="img_yes.jpg">
- sem, vsak: convert from Slovene-Voc to Cloze (curriculum source sentence)
- že, njega: delete entirely (not in curriculum; regenerate later)

Plus removes "ja" from SLOVENE_FUNCTION_WORDS so future /listen creates it as
a vocab note, matching this cleanup's outcome.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.anki.cleanup_function_word_notes import (
    CONVERT_TO_CLOZE_OPS,
    DELETE_OPS,
    FIX_IMAGE_OPS,
    apply_convert_to_cloze,
    apply_delete,
    apply_fix_image,
    main,
)
from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME

_SV_MID = 1776536503963
_CLOZE_MID = 1000001
_DECK_ID = 1
_DECK_NAME = "0. Slovene"


def _make_anki_db(tmp_path: Path, *, include_cloze: bool = True) -> Path:
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
        CREATE TABLE graves (oid INTEGER NOT NULL, type INTEGER NOT NULL,
            usn INTEGER NOT NULL, PRIMARY KEY (oid, type)) WITHOUT ROWID;
        CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT,
            mtime_secs INTEGER, usn INTEGER, common BLOB, kind BLOB);
        CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT,
            mtime_secs INTEGER, usn INTEGER, config BLOB);
        CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT,
            mtime_secs INTEGER, usn INTEGER, config BLOB, PRIMARY KEY (ntid, ord));
        CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB,
            PRIMARY KEY (ntid, ord));
    """)
    conn.execute("INSERT INTO col VALUES (1,0,100,500,18,0,7,0,'{}','{}','{}','{}','{}')")
    conn.execute("INSERT INTO decks VALUES (?, ?, 0, 0, x'', x'')", (_DECK_ID, _DECK_NAME))
    conn.execute("INSERT INTO notetypes VALUES (?, ?, 0, 0, x'')", (_SV_MID, SLOVENE_VOCAB_NOTETYPE_NAME))
    for i, name in enumerate(["Slovene", "English", "Audio", "Image", "Grammar", "Note", "DisambigKey"]):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, x'')", (_SV_MID, i, name))
    if include_cloze:
        conn.execute("INSERT INTO notetypes VALUES (?, 'Cloze', 0, 0, x'')", (_CLOZE_MID,))
        for i, name in enumerate(["Text", "Back Extra"]):
            conn.execute("INSERT INTO fields VALUES (?, ?, ?, x'')", (_CLOZE_MID, i, name))
        conn.execute("INSERT INTO templates VALUES (?, 0, 'Cloze', 0, 0, x'')", (_CLOZE_MID,))
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
            guid TEXT,
            disambig_key TEXT NOT NULL DEFAULT '',
            anki_note_id INTEGER,
            card_type TEXT DEFAULT 'vocab',
            source_sentence TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE collocation_directions (
            collocation_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'new',
            due_date TEXT NOT NULL DEFAULT '2026-01-01',
            stability REAL NOT NULL DEFAULT 1.0,
            fsrs_difficulty REAL NOT NULL DEFAULT 5.0,
            reps INTEGER NOT NULL DEFAULT 0,
            lapses INTEGER NOT NULL DEFAULT 0,
            anki_card_id INTEGER,
            anki_due INTEGER,
            dirty_fsrs INTEGER DEFAULT 0,
            PRIMARY KEY (collocation_id, direction)
        );
        CREATE TABLE media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collocation_id INTEGER,
            kind TEXT NOT NULL,
            filename TEXT NOT NULL,
            path TEXT,
            anki_filename TEXT,
            sha256 TEXT,
            bytes INTEGER
        );
    """)
    conn.commit()
    conn.close()
    return db


def _add_sv_note(anki_path: Path, nid: int, slovene: str, english: str, note: str = "") -> tuple[int, int]:
    conn = sqlite3.connect(str(anki_path))
    flds = "\x1f".join([slovene, english, "", "", "", note, ""])
    conn.execute(
        "INSERT INTO notes VALUES (?, ?, ?, 0, 0, '', ?, ?, 0, 0, '')",
        (nid, f"guid_{nid}", _SV_MID, flds, slovene),
    )
    rec_cid, prod_cid = nid, nid + 100_000
    for ord_, cid in [(0, rec_cid), (1, prod_cid)]:
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, ?, 0, 0, 0, 0, ?, 0, 2500, 0, 0, 0, 0, 0, 0, '')",
            (cid, nid, _DECK_ID, ord_, ord_),
        )
    conn.commit()
    conn.close()
    return rec_cid, prod_cid


def _add_tt_coll(
    tt_path: Path,
    text: str,
    *,
    anki_note_id: int,
    rec_aid: int,
    prod_aid: int | None,
    translation: str = "",
) -> int:
    conn = sqlite3.connect(str(tt_path))
    cur = conn.execute(
        "INSERT INTO collocations (text, translation, anki_note_id) VALUES (?, ?, ?)",
        (text, translation, anki_note_id),
    )
    cid = cur.lastrowid
    conn.execute(
        "INSERT INTO collocation_directions (collocation_id, direction, state, anki_card_id, reps) "
        "VALUES (?, 'recognition', 'review', ?, 5)",
        (cid, rec_aid),
    )
    if prod_aid is not None:
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state, anki_card_id) "
            "VALUES (?, 'production', 'learning', ?)",
            (cid, prod_aid),
        )
    conn.commit()
    conn.close()
    return cid


# ── Constants ───────────────────────────────────────────────────────────────


def test_fix_image_ops_targets_ja_only():
    assert len(FIX_IMAGE_OPS) == 1
    assert FIX_IMAGE_OPS[0].anki_nid == 1774631982025
    assert FIX_IMAGE_OPS[0].image_filename == "img_yes.jpg"


def test_convert_to_cloze_ops_target_sem_and_vsak():
    assert {op.anki_nid for op in CONVERT_TO_CLOZE_OPS} == {1774631982040, 1774631982054}
    sem = next(op for op in CONVERT_TO_CLOZE_OPS if op.surface == "sem")
    assert sem.source_sentence == "Zdravo Ana, jaz sem Janez."
    assert sem.tt_collocation_id == 672
    vsak = next(op for op in CONVERT_TO_CLOZE_OPS if op.surface == "vsak")
    assert vsak.source_sentence == "Odprto je vsak dan"
    assert vsak.tt_collocation_id == 701


def test_delete_ops_target_že_and_njega():
    assert {op.anki_nid for op in DELETE_OPS} == {1774631982029, 1774631982048}


def test_ja_not_in_slovene_function_words():
    """ja should be removed from the curated function-word list."""
    from app.srs.function_words import SLOVENE_FUNCTION_WORDS

    assert "ja" not in SLOVENE_FUNCTION_WORDS


# ── apply_fix_image ─────────────────────────────────────────────────────────


def test_apply_fix_image_sets_image_field(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1774631982025
    _add_sv_note(anki_path, nid, "ja", "yes")

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_fix_image(anki, tt, FIX_IMAGE_OPS[0])

    row = anki.execute("SELECT flds, usn, mod FROM notes WHERE id=?", (nid,)).fetchone()
    fields = row[0].split("\x1f")
    assert fields[3] == '<img src="img_yes.jpg">'
    assert row[1] == -1
    assert row[2] > 0


def test_apply_fix_image_inserts_tt_media_row(tmp_path):
    """The TT media row is what DrillCard.svelte uses to render the production prompt."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1774631982025
    _add_sv_note(anki_path, nid, "ja", "yes")
    tt_cid = _add_tt_coll(tt_path, "ja", anki_note_id=nid, rec_aid=nid, prod_aid=nid + 100_000)

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_fix_image(anki, tt, FIX_IMAGE_OPS[0])

    media_rows = tt.execute(
        "SELECT collocation_id, kind, filename, anki_filename FROM media WHERE collocation_id=?", (tt_cid,)
    ).fetchall()
    assert media_rows == [(tt_cid, "image", "img_yes.jpg", "img_yes.jpg")]


def test_apply_fix_image_is_idempotent_on_tt_media(tmp_path):
    """Re-running shouldn't create duplicate media rows."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1774631982025
    _add_sv_note(anki_path, nid, "ja", "yes")
    tt_cid = _add_tt_coll(tt_path, "ja", anki_note_id=nid, rec_aid=nid, prod_aid=nid + 100_000)

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_fix_image(anki, tt, FIX_IMAGE_OPS[0])
    apply_fix_image(anki, tt, FIX_IMAGE_OPS[0])

    assert tt.execute("SELECT COUNT(*) FROM media WHERE collocation_id=?", (tt_cid,)).fetchone()[0] == 1


def test_apply_fix_image_skips_tt_media_when_collocation_unlinked(tmp_path):
    """If no TT collocation references the Anki note, no media row is created."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    _add_sv_note(anki_path, 1774631982025, "ja", "yes")
    # No TT collocation added.

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_fix_image(anki, tt, FIX_IMAGE_OPS[0])

    assert tt.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0


def test_apply_fix_image_bumps_col_mod_not_scm(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    _add_sv_note(anki_path, 1774631982025, "ja", "yes")
    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    mod_before, scm_before = anki.execute("SELECT mod, scm FROM col").fetchone()
    apply_fix_image(anki, tt, FIX_IMAGE_OPS[0])
    mod_after, scm_after = anki.execute("SELECT mod, scm FROM col").fetchone()
    assert mod_after > mod_before
    assert scm_after == scm_before
    assert anki.execute("SELECT usn FROM col").fetchone()[0] == -1


def test_apply_fix_image_skips_when_note_missing(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    # Note 1774631982025 doesn't exist.
    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    mod_before = anki.execute("SELECT mod FROM col").fetchone()[0]
    applied = apply_fix_image(anki, tt, FIX_IMAGE_OPS[0])
    assert applied is False
    # No col.mod bump when the note is absent.
    assert anki.execute("SELECT mod FROM col").fetchone()[0] == mod_before


# ── apply_convert_to_cloze ──────────────────────────────────────────────────


def test_apply_convert_to_cloze_deletes_original_and_creates_cloze(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1774631982040  # sem
    rec, prod = _add_sv_note(anki_path, nid, "sem", "I am", note="[səm] phonology")
    tt_cid = _add_tt_coll(tt_path, "sem", anki_note_id=nid, rec_aid=rec, prod_aid=prod, translation="I am")

    # Use the real CONVERT_TO_CLOZE_OPS entry for sem but override the tt_cid for our test
    op = next(o for o in CONVERT_TO_CLOZE_OPS if o.surface == "sem")
    # In production op.tt_collocation_id is 672; our test stub has tt_cid from autoincrement.
    # Build a test-only op to keep the test self-contained.
    from app.anki.cleanup_function_word_notes import ConvertToClozeOp

    test_op = ConvertToClozeOp(
        anki_nid=nid,
        tt_collocation_id=tt_cid,
        surface="sem",
        source_sentence=op.source_sentence,
        translation="I am",
        note_text="[səm] phonology",
    )

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    result = apply_convert_to_cloze(anki, tt, test_op, deck_name=_DECK_NAME)
    assert result is not None  # returned new cloze nid

    # Original Slovene-Voc note + cards gone, graves added (1 note + 2 cards)
    assert anki.execute("SELECT COUNT(*) FROM notes WHERE id=?", (nid,)).fetchone()[0] == 0
    assert anki.execute("SELECT COUNT(*) FROM cards WHERE nid=?", (nid,)).fetchone()[0] == 0
    note_graves = anki.execute("SELECT COUNT(*) FROM graves WHERE oid=? AND type=1", (nid,)).fetchone()[0]
    card_graves = anki.execute("SELECT COUNT(*) FROM graves WHERE type=0 AND oid IN (?, ?)", (rec, prod)).fetchone()[0]
    assert note_graves == 1
    assert card_graves == 2

    # New Cloze note exists with c1 markup
    new_nid = result
    new_note = anki.execute("SELECT mid, flds FROM notes WHERE id=?", (new_nid,)).fetchone()
    assert new_note[0] == _CLOZE_MID
    cloze_text, back_extra = new_note[1].split("\x1f")
    assert "{{c1::sem}}" in cloze_text
    assert "Zdravo Ana, jaz" in cloze_text
    assert "I am" in back_extra
    # Single card on the new Cloze note (ord=0)
    new_cards = anki.execute("SELECT ord FROM cards WHERE nid=?", (new_nid,)).fetchall()
    assert new_cards == [(0,)]


def test_apply_convert_to_cloze_updates_tt(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1774631982040
    rec, prod = _add_sv_note(anki_path, nid, "sem", "I am")
    tt_cid = _add_tt_coll(tt_path, "sem", anki_note_id=nid, rec_aid=rec, prod_aid=prod, translation="I am")

    from app.anki.cleanup_function_word_notes import ConvertToClozeOp

    test_op = ConvertToClozeOp(
        anki_nid=nid,
        tt_collocation_id=tt_cid,
        surface="sem",
        source_sentence="Zdravo Ana, jaz sem Janez.",
        translation="I am",
        note_text="",
    )

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    new_nid = apply_convert_to_cloze(anki, tt, test_op, deck_name=_DECK_NAME)

    # TT collocation row: card_type=cloze, source_sentence, new anki_note_id
    coll = tt.execute(
        "SELECT card_type, source_sentence, anki_note_id FROM collocations WHERE id=?", (tt_cid,)
    ).fetchone()
    assert coll == ("cloze", "Zdravo Ana, jaz sem Janez.", new_nid)
    # Recognition direction removed; production kept and repointed
    dirs = tt.execute(
        "SELECT direction, anki_card_id FROM collocation_directions WHERE collocation_id=?", (tt_cid,)
    ).fetchall()
    assert len(dirs) == 1
    assert dirs[0][0] == "production"
    new_cid = anki.execute("SELECT id FROM cards WHERE nid=?", (new_nid,)).fetchone()[0]
    assert dirs[0][1] == new_cid


def test_apply_convert_to_cloze_skips_when_anki_note_missing(tmp_path):
    """If the source Slovene-Voc note isn't in Anki, the op is a no-op."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    from app.anki.cleanup_function_word_notes import ConvertToClozeOp

    op = ConvertToClozeOp(
        anki_nid=999999,  # not present
        tt_collocation_id=1,
        surface="sem",
        source_sentence="foo",
        translation="I am",
        note_text="",
    )
    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    result = apply_convert_to_cloze(anki, tt, op, deck_name=_DECK_NAME)
    assert result is None


def test_apply_convert_to_cloze_raises_if_cloze_notetype_missing(tmp_path):
    anki_path = _make_anki_db(tmp_path, include_cloze=False)
    tt_path = _make_tt_db(tmp_path)
    nid = 1774631982040
    rec, prod = _add_sv_note(anki_path, nid, "sem", "I am")
    tt_cid = _add_tt_coll(tt_path, "sem", anki_note_id=nid, rec_aid=rec, prod_aid=prod, translation="I am")
    from app.anki.cleanup_function_word_notes import ConvertToClozeOp

    op = ConvertToClozeOp(
        anki_nid=nid,
        tt_collocation_id=tt_cid,
        surface="sem",
        source_sentence="x",
        translation="y",
        note_text="",
    )
    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    import pytest

    with pytest.raises(ValueError, match="Cloze notetype"):
        apply_convert_to_cloze(anki, tt, op, deck_name=_DECK_NAME)


# ── apply_delete ────────────────────────────────────────────────────────────


def test_apply_delete_creates_graves_and_removes_anki_rows(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = 1774631982029
    rec, prod = _add_sv_note(anki_path, nid, "že", "already")
    tt_cid = _add_tt_coll(tt_path, "že", anki_note_id=nid, rec_aid=rec, prod_aid=prod)

    from app.anki.cleanup_function_word_notes import DeleteOp

    test_op = DeleteOp(anki_nid=nid, tt_collocation_id=tt_cid)
    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_delete(anki, tt, test_op)

    assert anki.execute("SELECT COUNT(*) FROM notes WHERE id=?", (nid,)).fetchone()[0] == 0
    assert anki.execute("SELECT COUNT(*) FROM cards WHERE nid=?", (nid,)).fetchone()[0] == 0
    assert anki.execute("SELECT COUNT(*) FROM graves WHERE oid=? AND type=1", (nid,)).fetchone()[0] == 1
    assert anki.execute("SELECT COUNT(*) FROM graves WHERE type=0 AND usn=-1").fetchone()[0] == 2
    # TT gone
    assert tt.execute("SELECT COUNT(*) FROM collocations WHERE id=?", (tt_cid,)).fetchone()[0] == 0
    assert (
        tt.execute("SELECT COUNT(*) FROM collocation_directions WHERE collocation_id=?", (tt_cid,)).fetchone()[0] == 0
    )


def test_apply_delete_skips_when_note_missing(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    from app.anki.cleanup_function_word_notes import DeleteOp

    op = DeleteOp(anki_nid=999999, tt_collocation_id=999)
    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    applied = apply_delete(anki, tt, op)
    assert applied is False


# ── main ────────────────────────────────────────────────────────────────────


def test_main_dry_run_does_not_mutate(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    _add_sv_note(anki_path, 1774631982025, "ja", "yes")

    rc = main(["--dry-run", "--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", _DECK_NAME])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Plan:" in out
    # No mutation: Image field still empty
    anki = sqlite3.connect(str(anki_path))
    flds = anki.execute("SELECT flds FROM notes WHERE id=1774631982025").fetchone()[0]
    assert flds.split("\x1f")[3] == ""


def test_main_apply_runs_all_three_op_types(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    # ja for FixImage
    ja_rec, ja_prod = _add_sv_note(anki_path, 1774631982025, "ja", "yes")
    ja_tt = _add_tt_coll(tt_path, "ja", anki_note_id=1774631982025, rec_aid=ja_rec, prod_aid=ja_prod)
    # sem + vsak for ConvertToCloze
    sem_rec, sem_prod = _add_sv_note(anki_path, 1774631982040, "sem", "I am")
    sem_tt = _add_tt_coll(tt_path, "sem", anki_note_id=1774631982040, rec_aid=sem_rec, prod_aid=sem_prod)
    vsak_rec, vsak_prod = _add_sv_note(anki_path, 1774631982054, "vsak", "every")
    vsak_tt = _add_tt_coll(tt_path, "vsak", anki_note_id=1774631982054, rec_aid=vsak_rec, prod_aid=vsak_prod)
    # že + njega for Delete
    že_rec, že_prod = _add_sv_note(anki_path, 1774631982029, "že", "already")
    že_tt = _add_tt_coll(tt_path, "že", anki_note_id=1774631982029, rec_aid=že_rec, prod_aid=že_prod)
    nj_rec, nj_prod = _add_sv_note(anki_path, 1774631982048, "njega", "him")
    nj_tt = _add_tt_coll(tt_path, "njega", anki_note_id=1774631982048, rec_aid=nj_rec, prod_aid=nj_prod)

    # Wire up the TT cids on the production ops to match our test rows
    import app.anki.cleanup_function_word_notes as mod

    orig_convert = mod.CONVERT_TO_CLOZE_OPS
    orig_delete = mod.DELETE_OPS
    mod.CONVERT_TO_CLOZE_OPS = tuple(
        type(orig_convert[0])(
            anki_nid=op.anki_nid,
            tt_collocation_id=(sem_tt if op.surface == "sem" else vsak_tt),
            surface=op.surface,
            source_sentence=op.source_sentence,
            translation=op.translation,
            note_text=op.note_text,
        )
        for op in orig_convert
    )
    mod.DELETE_OPS = tuple(
        type(orig_delete[0])(
            anki_nid=op.anki_nid,
            tt_collocation_id=(že_tt if op.anki_nid == 1774631982029 else nj_tt),
        )
        for op in orig_delete
    )
    try:
        rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", _DECK_NAME])
    finally:
        mod.CONVERT_TO_CLOZE_OPS = orig_convert
        mod.DELETE_OPS = orig_delete
    assert rc == 0
    out = capsys.readouterr().out
    assert "Applied:" in out

    anki = sqlite3.connect(str(anki_path))
    tt = sqlite3.connect(str(tt_path))
    # ja Image field filled
    flds = anki.execute("SELECT flds FROM notes WHERE id=1774631982025").fetchone()[0]
    assert flds.split("\x1f")[3] == '<img src="img_yes.jpg">'
    # ja media row linked on TT side so DrillCard renders the image
    ja_media = tt.execute("SELECT filename FROM media WHERE collocation_id=? AND kind='image'", (ja_tt,)).fetchone()
    assert ja_media == ("img_yes.jpg",)
    # sem original gone, new Cloze note exists
    assert anki.execute("SELECT COUNT(*) FROM notes WHERE id=1774631982040").fetchone()[0] == 0
    sem_now = tt.execute("SELECT card_type, anki_note_id FROM collocations WHERE id=?", (sem_tt,)).fetchone()
    assert sem_now[0] == "cloze"
    new_sem_note = anki.execute("SELECT mid FROM notes WHERE id=?", (sem_now[1],)).fetchone()
    assert new_sem_note[0] == _CLOZE_MID
    # že gone in Anki + TT
    assert anki.execute("SELECT COUNT(*) FROM notes WHERE id=1774631982029").fetchone()[0] == 0
    assert tt.execute("SELECT COUNT(*) FROM collocations WHERE id=?", (že_tt,)).fetchone()[0] == 0


def test_main_apply_skips_missing_ops_without_incrementing_counts(tmp_path, capsys):
    """All ops target nids that don't exist → main returns 0 with all counts at 0."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    # No notes added — every op's target nid is absent.
    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", _DECK_NAME])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Applied: {'fixed_image': 0, 'converted_to_cloze': 0, 'deleted': 0}" in out


def test_main_returns_1_when_anki_path_missing(tmp_path):
    rc = main(["--anki-db", str(tmp_path / "x.db"), "--tt-db", str(tmp_path / "y.db")])
    assert rc == 1


def test_main_returns_1_when_tt_path_missing(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tmp_path / "y.db")])
    assert rc == 1


def test_main_returns_1_when_cloze_notetype_missing(tmp_path, capsys):
    """If Cloze notetype is absent, the script refuses with instructions."""
    anki_path = _make_anki_db(tmp_path, include_cloze=False)
    tt_path = _make_tt_db(tmp_path)
    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", _DECK_NAME])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Cloze notetype" in err
    assert "Manage Note Types" in err


def test_main_dry_run_returns_1_when_cloze_notetype_missing(tmp_path, capsys):
    """Dry-run also reports the Cloze prereq."""
    anki_path = _make_anki_db(tmp_path, include_cloze=False)
    tt_path = _make_tt_db(tmp_path)
    rc = main(["--dry-run", "--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", _DECK_NAME])
    assert rc == 1
    assert "Cloze notetype" in capsys.readouterr().err
