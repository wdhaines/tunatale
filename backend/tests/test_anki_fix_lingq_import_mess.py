"""Tests for app.anki.fix_lingq_import_mess.

Cleans up the historical leftover from a buggy /listen import: 36 Basic-notetype
Anki notes that should have been (a) skipped for twins or (b) created as Slovene
Vocabulary notes for non-twins.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from app.anki.fix_lingq_import_mess import (
    ConvertItem,
    DeleteItem,
    apply_plan,
    main,
    plan_cleanup,
)
from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME
from app.common.guid import compute_guid

_BASIC_MID = 1519651961633
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
        CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER,
            mod INTEGER, usn INTEGER, tags TEXT, flds TEXT, sfld TEXT,
            csum INTEGER, flags INTEGER, data TEXT);
        CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER,
            ord INTEGER, mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER,
            due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER,
            lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER,
            flags INTEGER, data TEXT);
        CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER,
            usn INTEGER, common BLOB, kind BLOB);
        CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT,
            mtime_secs INTEGER, usn INTEGER, config BLOB);
        CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB,
            PRIMARY KEY (ntid, ord));
    """)
    conn.execute("INSERT INTO col VALUES (1,0,0,0,18,0,0,0,'{}','{}','{}','{}','{}')")
    conn.execute("INSERT INTO decks VALUES (?, ?, 0, 0, x'', x'')", (_DECK_ID, _DECK_NAME))
    conn.execute("INSERT INTO notetypes VALUES (?, 'Basic', 0, 0, x'')", (_BASIC_MID,))
    conn.execute("INSERT INTO notetypes VALUES (?, ?, 0, 0, x'')", (_SV_MID, SLOVENE_VOCAB_NOTETYPE_NAME))
    for i, name in enumerate(["Front", "Back"]):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, x'')", (_BASIC_MID, i, name))
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
            guid TEXT,
            disambig_key TEXT NOT NULL DEFAULT '',
            anki_note_id INTEGER
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
    """)
    conn.commit()
    conn.close()
    return db


def _add_basic_note(anki_path: Path, nid: int, front: str, back: str, guid: str | None = None) -> int:
    conn = sqlite3.connect(str(anki_path))
    g = guid or f"basic_{nid}"
    conn.execute(
        "INSERT INTO notes VALUES (?, ?, ?, 0, 0, '', ?, ?, 0, 0, '')",
        (nid, g, _BASIC_MID, f"{front}\x1f{back}", front),
    )
    # Single card for Basic notetype (ord=0).
    cid = nid  # convention: Basic note's single card id = nid
    conn.execute(
        "INSERT INTO cards VALUES (?, ?, ?, 0, 0, 0, 2, 2, 4500, 1, 2500, 1, 0, 0, 0, 0, 0, '')",
        (cid, nid, _DECK_ID),
    )
    conn.commit()
    conn.close()
    return cid


def _add_slovene_voc_note(
    anki_path: Path, nid: int, slovene: str, english: str, guid: str | None = None
) -> tuple[int, int]:
    """Returns (rec_cid, prod_cid)."""
    conn = sqlite3.connect(str(anki_path))
    g = guid or compute_guid(slovene, "sl", "")
    flds = "\x1f".join([slovene, english, "", "", "", "", ""])
    conn.execute(
        "INSERT INTO notes VALUES (?, ?, ?, 0, 0, '', ?, ?, 0, 0, '')",
        (nid, g, _SV_MID, flds, slovene),
    )
    rec_cid = nid
    prod_cid = nid + 1
    for ord_, cid in [(0, rec_cid), (1, prod_cid)]:
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, ?, 0, 0, 0, 0, ?, 0, 2500, 0, 0, 0, 0, 0, 0, '')",
            (cid, nid, _DECK_ID, ord_, 100 + ord_),
        )
    conn.commit()
    conn.close()
    return rec_cid, prod_cid


def _add_tt_collocation(
    tt_path: Path,
    text: str,
    anki_note_id: int,
    *,
    guid: str | None = None,
    rec_aid: int | None = None,
    prod_aid: int | None = None,
    rec_state: str = "review",
    rec_due: int | None = 4500,
) -> int:
    conn = sqlite3.connect(str(tt_path))
    g = guid or compute_guid(text, "sl", "")
    cur = conn.execute(
        "INSERT INTO collocations (text, translation, guid, anki_note_id) VALUES (?, ?, ?, ?)",
        (text, "", g, anki_note_id),
    )
    cid = cur.lastrowid
    if rec_aid is not None:
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state, anki_card_id, anki_due, reps) "
            "VALUES (?, 'recognition', ?, ?, ?, 1)",
            (cid, rec_state, rec_aid, rec_due),
        )
    if prod_aid is not None:
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state, anki_card_id, anki_due) "
            "VALUES (?, 'production', 'new', ?, NULL)",
            (cid, prod_aid),
        )
    conn.commit()
    conn.close()
    return cid


# ── plan_cleanup tests ───────────────────────────────────────────────────────


def test_plan_marks_twin_basic_for_delete(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    # Basic note for trgovina
    basic_nid = 1000
    _add_basic_note(anki_path, basic_nid, "<b>trgovina</b><br><i>shop</i>", "[sound:sl_trgovina.mp3]")
    # Slovene-Voc twin
    sv_nid = 2000
    _add_slovene_voc_note(anki_path, sv_nid, "trgovina", "shop")
    # TT collocation linked to Basic
    _add_tt_collocation(tt_path, "trgovina", basic_nid, rec_aid=basic_nid)

    anki = sqlite3.connect(str(anki_path))
    tt = sqlite3.connect(str(tt_path))
    deletes, converts = plan_cleanup(anki, tt, _DECK_ID, _SV_MID, _BASIC_MID)
    assert len(deletes) == 1
    assert len(converts) == 0
    assert deletes[0].basic_nid == basic_nid
    assert deletes[0].target_slovene_voc_nid == sv_nid


def test_plan_marks_non_twin_basic_for_convert(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    # Basic vocab+gloss with NO Slovene-Voc twin
    basic_nid = 1500
    _add_basic_note(anki_path, basic_nid, "<b>nič</b><br><i>nothing</i>", "[sound:sl_nic.mp3][nətʃ]")
    _add_tt_collocation(tt_path, "nič", basic_nid, rec_aid=basic_nid)

    anki = sqlite3.connect(str(anki_path))
    tt = sqlite3.connect(str(tt_path))
    deletes, converts = plan_cleanup(anki, tt, _DECK_ID, _SV_MID, _BASIC_MID)
    assert len(deletes) == 0
    assert len(converts) == 1
    item = converts[0]
    assert item.basic_nid == basic_nid
    assert item.slovene == "nič"
    assert item.english == "nothing"
    assert item.audio == "[sound:sl_nic.mp3]"


def test_plan_handles_l1l2_prompt_pattern(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    # L1→L2 prompt Basic for ulica (twin exists)
    basic_nid = 1700
    _add_basic_note(
        anki_path,
        basic_nid,
        '<div class="prompt">[street/road]</div>',
        '[sound:sl_ulica.mp3]<div class="slovene">ulica</div><div class="english">street</div>',
    )
    sv_nid = 2700
    _add_slovene_voc_note(anki_path, sv_nid, "ulica", "street")
    _add_tt_collocation(tt_path, "ulica", basic_nid, rec_aid=basic_nid)

    anki = sqlite3.connect(str(anki_path))
    tt = sqlite3.connect(str(tt_path))
    deletes, converts = plan_cleanup(anki, tt, _DECK_ID, _SV_MID, _BASIC_MID)
    assert len(deletes) == 1
    assert deletes[0].basic_nid == basic_nid
    assert deletes[0].target_slovene_voc_nid == sv_nid


def test_plan_handles_single_word_pattern(tmp_path):
    """Bare `<b>Ljubljana</b>` Front (no <i> gloss) → CONVERT if no twin, DELETE if twin."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    basic_nid = 1800
    _add_basic_note(anki_path, basic_nid, "<b>Ljubljana</b>", "")
    _add_tt_collocation(tt_path, "Ljubljana", basic_nid, rec_aid=basic_nid)

    anki = sqlite3.connect(str(anki_path))
    tt = sqlite3.connect(str(tt_path))
    deletes, converts = plan_cleanup(anki, tt, _DECK_ID, _SV_MID, _BASIC_MID)
    assert len(converts) == 1
    assert converts[0].slovene == "Ljubljana"
    assert converts[0].english == ""


def test_plan_twin_match_is_case_insensitive(tmp_path):
    """User's Anki has Slovene-Voc sfld='Bog' (capitalized proper noun) while the
    Basic note has `<b>bog</b>` (lowercase). They should match as twins."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    basic_nid = 1900
    _add_basic_note(anki_path, basic_nid, "<b>bog</b><br><i>god</i>", "")
    # Twin with different case.
    sv_nid = 2900
    _add_slovene_voc_note(anki_path, sv_nid, "Bog", "God")
    _add_tt_collocation(tt_path, "bog", basic_nid, rec_aid=basic_nid)

    anki = sqlite3.connect(str(anki_path))
    tt = sqlite3.connect(str(tt_path))
    deletes, converts = plan_cleanup(anki, tt, _DECK_ID, _SV_MID, _BASIC_MID)
    assert len(deletes) == 1
    assert deletes[0].target_slovene_voc_nid == sv_nid


def test_plan_l1l2_prompt_with_slash_variants_matches_either_twin(tmp_path):
    """A Basic L1→L2 prompt whose Slovene contains 'ulica / cesta' should DELETE
    if EITHER variant has a Slovene-Voc twin in Anki."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    basic_nid = 1950
    _add_basic_note(
        anki_path,
        basic_nid,
        '<div class="prompt">[street/road]</div>',
        '<div class="slovene">ulica / cesta</div><div class="english">street/road</div>',
    )
    sv_nid = 2950
    _add_slovene_voc_note(anki_path, sv_nid, "ulica", "street")  # only one variant
    _add_tt_collocation(tt_path, "ulica / cesta", basic_nid, rec_aid=basic_nid)

    anki = sqlite3.connect(str(anki_path))
    tt = sqlite3.connect(str(tt_path))
    deletes, converts = plan_cleanup(anki, tt, _DECK_ID, _SV_MID, _BASIC_MID)
    assert len(deletes) == 1
    assert deletes[0].target_slovene_voc_nid == sv_nid


def test_plan_skips_basic_with_no_tt_collocation(tmp_path):
    """A Basic vocab-gloss note that has no matching TT collocation is silently skipped."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    _add_basic_note(anki_path, 4242, "<b>foo</b><br><i>bar</i>", "")
    # No TT collocation added.
    anki = sqlite3.connect(str(anki_path))
    tt = sqlite3.connect(str(tt_path))
    deletes, converts = plan_cleanup(anki, tt, _DECK_ID, _SV_MID, _BASIC_MID)
    assert deletes == []
    assert converts == []


def test_plan_skips_phonology_notes(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    # Phonology question — should NOT be in the plan
    _add_basic_note(anki_path, 3000, "What sound is <b>v</b> word-initial?", "[wː] voiced bilabial")
    _add_tt_collocation(tt_path, "What sound is v word-initial?", 3000, rec_aid=3000)

    anki = sqlite3.connect(str(anki_path))
    tt = sqlite3.connect(str(tt_path))
    deletes, converts = plan_cleanup(anki, tt, _DECK_ID, _SV_MID, _BASIC_MID)
    assert deletes == []
    assert converts == []


# ── apply_plan: DELETE ──────────────────────────────────────────────────────


def test_apply_delete_removes_basic_and_relinks_tt(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    basic_nid = 1000
    _add_basic_note(anki_path, basic_nid, "<b>trgovina</b><br><i>shop</i>", "[sound:sl_trgovina.mp3]")
    sv_nid = 2000
    sv_rec_cid, _ = _add_slovene_voc_note(anki_path, sv_nid, "trgovina", "shop")
    tt_cid = _add_tt_collocation(tt_path, "trgovina", basic_nid, rec_aid=basic_nid)

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    deletes = [DeleteItem(basic_nid=basic_nid, target_slovene_voc_nid=sv_nid, tt_collocation_id=tt_cid)]
    counts = apply_plan(anki, tt, deletes, [], _DECK_ID, _SV_MID)
    assert counts["deleted"] == 1

    # Anki: Basic note + card gone
    assert anki.execute("SELECT COUNT(*) FROM notes WHERE id=?", (basic_nid,)).fetchone()[0] == 0
    assert anki.execute("SELECT COUNT(*) FROM cards WHERE nid=?", (basic_nid,)).fetchone()[0] == 0
    # TT collocation now points to the Slovene-Voc note; direction aids cleared so sync_pull will refresh.
    row = tt.execute("SELECT anki_note_id FROM collocations WHERE id=?", (tt_cid,)).fetchone()
    assert row[0] == sv_nid
    rec = tt.execute(
        "SELECT anki_card_id, anki_due FROM collocation_directions WHERE collocation_id=? AND direction='recognition'",
        (tt_cid,),
    ).fetchone()
    assert rec == (None, None)


# ── apply_plan: CONVERT ────────────────────────────────────────────────────


def test_apply_convert_changes_mid_and_adds_production_card(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    basic_nid = 1500
    rec_cid = _add_basic_note(anki_path, basic_nid, "<b>nič</b><br><i>nothing</i>", "[sound:sl_nic.mp3][nətʃ]")
    tt_cid = _add_tt_collocation(tt_path, "nič", basic_nid, rec_aid=rec_cid)

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    converts = [
        ConvertItem(
            basic_nid=basic_nid,
            slovene="nič",
            english="nothing",
            audio="[sound:sl_nic.mp3]",
            note_extra="[nətʃ]",
            tt_collocation_id=tt_cid,
        )
    ]
    counts = apply_plan(anki, tt, [], converts, _DECK_ID, _SV_MID)
    assert counts["converted"] == 1

    # Anki: note.mid is now Slovene-Voc, 7 fields, guid recomputed
    note = anki.execute("SELECT mid, flds, sfld, guid, usn FROM notes WHERE id=?", (basic_nid,)).fetchone()
    assert note[0] == _SV_MID
    fields = note[1].split("\x1f")
    assert len(fields) == 7
    assert fields[0] == "nič"
    assert fields[1] == "nothing"
    assert fields[2] == "[sound:sl_nic.mp3]"
    assert note[2] == "nič"
    assert note[3] == compute_guid("nič", "sl", "")
    assert note[4] == -1  # usn=-1 marks for sync
    # Anki: two cards now (ord=0 kept, ord=1 added)
    cards = anki.execute("SELECT id, ord, usn FROM cards WHERE nid=? ORDER BY ord", (basic_nid,)).fetchall()
    assert len(cards) == 2
    assert cards[0][1] == 0
    assert cards[0][0] == rec_cid  # Recognition card preserved
    assert cards[1][1] == 1  # Production card added
    # TT: both directions exist
    dirs = tt.execute(
        "SELECT direction, anki_card_id FROM collocation_directions WHERE collocation_id=? ORDER BY direction",
        (tt_cid,),
    ).fetchall()
    dir_names = [d[0] for d in dirs]
    assert "production" in dir_names
    assert "recognition" in dir_names
    prod_row = [d for d in dirs if d[0] == "production"][0]
    assert prod_row[1] == cards[1][0]  # production aid = new ord=1 card id


def test_apply_bumps_col_scm_when_converting(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    basic_nid = 1500
    _add_basic_note(anki_path, basic_nid, "<b>nič</b><br><i>nothing</i>", "")
    tt_cid = _add_tt_collocation(tt_path, "nič", basic_nid, rec_aid=basic_nid)
    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    scm_before = anki.execute("SELECT scm FROM col").fetchone()[0]
    converts = [
        ConvertItem(
            basic_nid=basic_nid, slovene="nič", english="nothing", audio="", note_extra="", tt_collocation_id=tt_cid
        )
    ]
    apply_plan(anki, tt, [], converts, _DECK_ID, _SV_MID)
    scm_after = anki.execute("SELECT scm FROM col").fetchone()[0]
    assert scm_after > scm_before


def test_apply_does_not_bump_col_scm_for_delete_only(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    basic_nid = 1000
    _add_basic_note(anki_path, basic_nid, "<b>trgovina</b><br><i>shop</i>", "")
    sv_nid = 2000
    _add_slovene_voc_note(anki_path, sv_nid, "trgovina", "shop")
    tt_cid = _add_tt_collocation(tt_path, "trgovina", basic_nid, rec_aid=basic_nid)
    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    scm_before = anki.execute("SELECT scm FROM col").fetchone()[0]
    deletes = [DeleteItem(basic_nid=basic_nid, target_slovene_voc_nid=sv_nid, tt_collocation_id=tt_cid)]
    apply_plan(anki, tt, deletes, [], _DECK_ID, _SV_MID)
    scm_after = anki.execute("SELECT scm FROM col").fetchone()[0]
    # Deletion only: col.mod bumped but col.scm unchanged (data-only mutation).
    assert scm_after == scm_before


# ── CLI / main ─────────────────────────────────────────────────────────────


def test_main_dry_run_does_not_mutate(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    _add_basic_note(anki_path, 1500, "<b>nič</b><br><i>nothing</i>", "")
    _add_tt_collocation(tt_path, "nič", 1500, rec_aid=1500)

    rc = main(["--dry-run", "--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", _DECK_NAME])
    assert rc == 0
    out = capsys.readouterr().out
    assert "CONVERT" in out
    # No mutation
    anki = sqlite3.connect(str(anki_path))
    n = anki.execute("SELECT mid FROM notes WHERE id=1500").fetchone()
    assert n[0] == _BASIC_MID


def test_main_applies_plan(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    _add_basic_note(anki_path, 1500, "<b>nič</b><br><i>nothing</i>", "")
    _add_tt_collocation(tt_path, "nič", 1500, rec_aid=1500)

    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", _DECK_NAME])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Applied:" in out
    anki = sqlite3.connect(str(anki_path))
    assert anki.execute("SELECT mid FROM notes WHERE id=1500").fetchone()[0] == _SV_MID


def test_main_returns_1_when_anki_path_missing(tmp_path, capsys):
    rc = main(["--anki-db", str(tmp_path / "no.db"), "--tt-db", str(tmp_path / "no2.db")])
    assert rc == 1


def test_main_returns_1_when_tt_path_missing(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tmp_path / "no2.db")])
    assert rc == 1


def test_main_returns_0_with_empty_plan(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path)])
    assert rc == 0
    assert "Nothing to apply" in capsys.readouterr().out


def test_main_dry_run_with_empty_plan(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    rc = main(["--dry-run", "--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", _DECK_NAME])
    assert rc == 0
    assert "Nothing to apply" in capsys.readouterr().out


def test_main_dry_run_with_missing_sv_notetype(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    # Drop the Slovene-Voc notetype.
    conn = sqlite3.connect(str(anki_path))
    conn.execute("DELETE FROM notetypes WHERE name = ?", (SLOVENE_VOCAB_NOTETYPE_NAME,))
    conn.commit()
    conn.close()
    rc = main(["--dry-run", "--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", _DECK_NAME])
    assert rc == 1
    assert "Notetype not found" in capsys.readouterr().err


def test_main_dry_run_with_missing_basic_notetype(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    conn = sqlite3.connect(str(anki_path))
    conn.execute("DELETE FROM notetypes WHERE name = 'Basic'")
    conn.commit()
    conn.close()
    rc = main(["--dry-run", "--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", _DECK_NAME])
    assert rc == 1
    assert "Notetype not found: 'Basic'" in capsys.readouterr().err


def test_main_apply_with_missing_notetypes(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    conn = sqlite3.connect(str(anki_path))
    conn.execute("DELETE FROM notetypes WHERE name = ?", (SLOVENE_VOCAB_NOTETYPE_NAME,))
    conn.commit()
    conn.close()
    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", _DECK_NAME])
    assert rc == 1
    assert "Required notetypes not found" in capsys.readouterr().err


def test_main_apply_prints_delete_plan_lines(tmp_path, capsys):
    """Apply path prints DELETE lines and skips the col.scm workflow message when no converts."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    basic_nid = 1000
    _add_basic_note(anki_path, basic_nid, "<b>trgovina</b><br><i>shop</i>", "")
    sv_nid = 2000
    _add_slovene_voc_note(anki_path, sv_nid, "trgovina", "shop")
    _add_tt_collocation(tt_path, "trgovina", basic_nid, rec_aid=basic_nid)
    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", _DECK_NAME])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DELETE basic_nid=1000" in out
    # Workflow message only printed when CONVERT happened.
    assert "Notetype change bumped col.scm" not in out


def test_main_apply_empty_plan_returns_0(tmp_path, capsys):
    """Apply path with no actionable notes still goes through safe_open + returns 0."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", _DECK_NAME])
    assert rc == 0
    assert "Nothing to apply" in capsys.readouterr().out


def test_parse_basic_front_returns_none_for_empty_prompt_back(tmp_path):
    """L1→L2 prompt Front but Back has no <div class='slovene'/'english'> markers → None."""
    from app.anki.fix_lingq_import_mess import _parse_basic_front

    flds = '<div class="prompt">[street/road]</div>\x1f[sound:sl_x.mp3]'
    assert _parse_basic_front(flds) is None


def test_main_returns_1_when_deck_missing(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", "nonexistent"])
    assert rc == 1


def test_main_dry_run_returns_1_when_deck_missing(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    rc = main(["--dry-run", "--anki-db", str(anki_path), "--tt-db", str(tt_path), "--deck-name", "nonexistent"])
    assert rc == 1


# Tiny helper to keep imports tidy
_now = int(time.time())
