"""Tests for app.anki.delete_phonology_demos.

One-shot cleanup that removes the 13 Slovene-Voc notes the LingQ fix-up script
created from phonology-demo Basic notes. Each surviving "How is X pronounced?"
sibling Basic note already carries the same IPA + Forvo + rule payload, so the
mis-converted notes are redundant. iskra and ovca have no sibling but the user
opted to delete them too.

This is an Anki delete (graves), not a schema change. col.mod is bumped but
col.scm is not — incremental sync, no forced full upload.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.anki.delete_phonology_demos import (
    PHONOLOGY_DEMO_NIDS,
    apply_deletes,
    main,
    plan_deletes,
)
from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME

_SV_MID = 1776536503963
_BASIC_MID = 1519651961633
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
        CREATE TABLE graves (oid INTEGER NOT NULL, type INTEGER NOT NULL,
            usn INTEGER NOT NULL, PRIMARY KEY (oid, type)) WITHOUT ROWID;
        CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER,
            usn INTEGER, common BLOB, kind BLOB);
        CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT,
            mtime_secs INTEGER, usn INTEGER, config BLOB);
    """)
    conn.execute("INSERT INTO col VALUES (1,0,100,500,18,0,7,0,'{}','{}','{}','{}','{}')")
    conn.execute("INSERT INTO decks VALUES (?, ?, 0, 0, x'', x'')", (_DECK_ID, _DECK_NAME))
    conn.execute("INSERT INTO notetypes VALUES (?, ?, 0, 0, x'')", (_SV_MID, SLOVENE_VOCAB_NOTETYPE_NAME))
    conn.execute("INSERT INTO notetypes VALUES (?, 'Basic', 0, 0, x'')", (_BASIC_MID,))
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


def _add_sv_note_with_two_cards(
    anki_path: Path, nid: int, text: str, *, rec_cid: int | None = None, prod_cid: int | None = None
) -> tuple[int, int]:
    conn = sqlite3.connect(str(anki_path))
    flds = "\x1f".join([text, "", "", "", "", "", ""])
    conn.execute(
        "INSERT INTO notes VALUES (?, ?, ?, 0, 0, '', ?, ?, 0, 0, '')",
        (nid, f"g_{nid}", _SV_MID, flds, text),
    )
    rec = rec_cid if rec_cid is not None else nid
    prod = prod_cid if prod_cid is not None else nid + 100_000
    for ord_, cid in [(0, rec), (1, prod)]:
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, ?, 0, 0, 0, 0, ?, 0, 2500, 0, 0, 0, 0, 0, 0, '')",
            (cid, nid, _DECK_ID, ord_, ord_),
        )
    conn.commit()
    conn.close()
    return rec, prod


def _add_tt_collocation(
    tt_path: Path,
    text: str,
    *,
    anki_note_id: int,
    rec_aid: int | None = None,
    prod_aid: int | None = None,
) -> int:
    conn = sqlite3.connect(str(tt_path))
    cur = conn.execute(
        "INSERT INTO collocations (text, anki_note_id) VALUES (?, ?)",
        (text, anki_note_id),
    )
    cid = cur.lastrowid
    if rec_aid is not None:
        conn.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, state, anki_card_id) "
            "VALUES (?, 'recognition', 'review', ?)",
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


# ── PHONOLOGY_DEMO_NIDS constant ─────────────────────────────────────────────


def test_phonology_demo_nids_has_13_entries():
    assert len(PHONOLOGY_DEMO_NIDS) == 13
    # Stable order, unique
    assert len(set(PHONOLOGY_DEMO_NIDS)) == 13


def test_phonology_demo_nids_excludes_function_words():
    """ja, že, sem, njega, vsak are function words — separate cleanup path."""
    func_word_nids = {1774631982025, 1774631982029, 1774631982040, 1774631982048, 1774631982054}
    assert not (func_word_nids & set(PHONOLOGY_DEMO_NIDS))


# ── plan_deletes ─────────────────────────────────────────────────────────────


def test_plan_returns_only_existing_target_nids(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    # First 3 phonology nids exist; rest absent.
    present = PHONOLOGY_DEMO_NIDS[:3]
    for nid in present:
        rec, prod = _add_sv_note_with_two_cards(anki_path, nid, f"word_{nid}")
        _add_tt_collocation(tt_path, f"word_{nid}", anki_note_id=nid, rec_aid=rec, prod_aid=prod)

    anki = sqlite3.connect(str(anki_path))
    tt = sqlite3.connect(str(tt_path))
    items = plan_deletes(anki, tt)
    assert {it.anki_nid for it in items} == set(present)


def test_plan_includes_card_ids_and_tt_cid(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = PHONOLOGY_DEMO_NIDS[0]
    rec, prod = _add_sv_note_with_two_cards(anki_path, nid, "iskra", rec_cid=9001, prod_cid=9002)
    tt_cid = _add_tt_collocation(tt_path, "iskra", anki_note_id=nid, rec_aid=rec, prod_aid=prod)

    anki = sqlite3.connect(str(anki_path))
    tt = sqlite3.connect(str(tt_path))
    items = plan_deletes(anki, tt)
    assert len(items) == 1
    item = items[0]
    assert item.anki_nid == nid
    assert set(item.anki_cids) == {9001, 9002}
    assert item.tt_collocation_id == tt_cid


def test_plan_returns_empty_when_no_targets_present(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    anki = sqlite3.connect(str(anki_path))
    tt = sqlite3.connect(str(tt_path))
    assert plan_deletes(anki, tt) == []


def test_plan_handles_nid_not_in_tt(tmp_path):
    """Anki note exists for a target nid but TT has no collocation linked — still plan it."""
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = PHONOLOGY_DEMO_NIDS[0]
    _add_sv_note_with_two_cards(anki_path, nid, "iskra")
    # TT empty.
    anki = sqlite3.connect(str(anki_path))
    tt = sqlite3.connect(str(tt_path))
    items = plan_deletes(anki, tt)
    assert len(items) == 1
    assert items[0].tt_collocation_id is None


# ── apply_deletes: Anki side ─────────────────────────────────────────────────


def test_apply_inserts_card_grave_per_card_and_one_note_grave(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = PHONOLOGY_DEMO_NIDS[0]
    rec, prod = _add_sv_note_with_two_cards(anki_path, nid, "iskra", rec_cid=5001, prod_cid=5002)
    tt_cid = _add_tt_collocation(tt_path, "iskra", anki_note_id=nid, rec_aid=rec, prod_aid=prod)

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    items = plan_deletes(anki, tt)
    counts = apply_deletes(anki, tt, items)

    assert counts == {"notes_deleted": 1, "cards_deleted": 2, "tt_collocations_deleted": 1}
    graves = anki.execute("SELECT oid, type, usn FROM graves ORDER BY type, oid").fetchall()
    # Two type=0 (card) graves, one type=1 (note) grave; all usn=-1
    assert graves == [(5001, 0, -1), (5002, 0, -1), (nid, 1, -1)]
    assert tt_cid is not None  # noqa: S101


def test_apply_removes_notes_and_cards_rows(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = PHONOLOGY_DEMO_NIDS[0]
    _add_sv_note_with_two_cards(anki_path, nid, "iskra")
    _add_tt_collocation(tt_path, "iskra", anki_note_id=nid, rec_aid=nid, prod_aid=nid + 100_000)

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_deletes(anki, tt, plan_deletes(anki, tt))

    assert anki.execute("SELECT COUNT(*) FROM notes WHERE id=?", (nid,)).fetchone()[0] == 0
    assert anki.execute("SELECT COUNT(*) FROM cards WHERE nid=?", (nid,)).fetchone()[0] == 0


def test_apply_preserves_unrelated_notes_and_cards(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    target_nid = PHONOLOGY_DEMO_NIDS[0]
    other_nid = 9999999
    _add_sv_note_with_two_cards(anki_path, target_nid, "iskra")
    _add_sv_note_with_two_cards(anki_path, other_nid, "untouched")
    _add_tt_collocation(tt_path, "iskra", anki_note_id=target_nid, rec_aid=target_nid)
    other_cid = _add_tt_collocation(tt_path, "untouched", anki_note_id=other_nid, rec_aid=other_nid)

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_deletes(anki, tt, plan_deletes(anki, tt))

    assert anki.execute("SELECT COUNT(*) FROM notes WHERE id=?", (other_nid,)).fetchone()[0] == 1
    assert anki.execute("SELECT COUNT(*) FROM cards WHERE nid=?", (other_nid,)).fetchone()[0] == 2
    # Other TT collocation untouched
    assert tt.execute("SELECT COUNT(*) FROM collocations WHERE id=?", (other_cid,)).fetchone()[0] == 1
    # No graves for the untouched note/cards
    assert anki.execute("SELECT COUNT(*) FROM graves WHERE oid=?", (other_nid,)).fetchone()[0] == 0


def test_apply_bumps_col_mod_not_scm(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = PHONOLOGY_DEMO_NIDS[0]
    _add_sv_note_with_two_cards(anki_path, nid, "iskra")
    _add_tt_collocation(tt_path, "iskra", anki_note_id=nid, rec_aid=nid)

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    mod_before, scm_before = anki.execute("SELECT mod, scm FROM col").fetchone()
    apply_deletes(anki, tt, plan_deletes(anki, tt))
    mod_after, scm_after = anki.execute("SELECT mod, scm FROM col").fetchone()
    usn_after = anki.execute("SELECT usn FROM col").fetchone()[0]

    assert mod_after > mod_before
    assert scm_after == scm_before  # deletes are NOT a schema change
    assert usn_after == -1  # marked dirty for next sync


def test_apply_with_empty_plan_is_noop(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    mod_before = anki.execute("SELECT mod FROM col").fetchone()[0]
    counts = apply_deletes(anki, tt, [])
    assert counts == {"notes_deleted": 0, "cards_deleted": 0, "tt_collocations_deleted": 0}
    # col.mod NOT bumped when there's nothing to delete
    mod_after = anki.execute("SELECT mod FROM col").fetchone()[0]
    assert mod_after == mod_before


# ── apply_deletes: TT side ───────────────────────────────────────────────────


def test_apply_removes_tt_collocation_and_all_directions(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = PHONOLOGY_DEMO_NIDS[0]
    rec, prod = _add_sv_note_with_two_cards(anki_path, nid, "iskra")
    tt_cid = _add_tt_collocation(tt_path, "iskra", anki_note_id=nid, rec_aid=rec, prod_aid=prod)

    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_deletes(anki, tt, plan_deletes(anki, tt))

    assert tt.execute("SELECT COUNT(*) FROM collocations WHERE id=?", (tt_cid,)).fetchone()[0] == 0
    assert (
        tt.execute("SELECT COUNT(*) FROM collocation_directions WHERE collocation_id=?", (tt_cid,)).fetchone()[0] == 0
    )


def test_apply_handles_nid_not_in_tt_gracefully(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = PHONOLOGY_DEMO_NIDS[0]
    _add_sv_note_with_two_cards(anki_path, nid, "iskra")
    # TT has no row for this nid.
    anki = sqlite3.connect(str(anki_path), isolation_level=None)
    tt = sqlite3.connect(str(tt_path), isolation_level=None)
    counts = apply_deletes(anki, tt, plan_deletes(anki, tt))
    assert counts == {"notes_deleted": 1, "cards_deleted": 2, "tt_collocations_deleted": 0}


# ── CLI / main ───────────────────────────────────────────────────────────────


def test_main_dry_run_does_not_mutate(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = PHONOLOGY_DEMO_NIDS[0]
    _add_sv_note_with_two_cards(anki_path, nid, "iskra")
    _add_tt_collocation(tt_path, "iskra", anki_note_id=nid, rec_aid=nid)

    rc = main(["--dry-run", "--anki-db", str(anki_path), "--tt-db", str(tt_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Plan:" in out
    assert str(nid) in out

    anki = sqlite3.connect(str(anki_path))
    assert anki.execute("SELECT COUNT(*) FROM notes WHERE id=?", (nid,)).fetchone()[0] == 1


def test_main_apply_writes_to_dbs(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    nid = PHONOLOGY_DEMO_NIDS[0]
    _add_sv_note_with_two_cards(anki_path, nid, "iskra")
    _add_tt_collocation(tt_path, "iskra", anki_note_id=nid, rec_aid=nid)

    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Applied:" in out

    anki = sqlite3.connect(str(anki_path))
    assert anki.execute("SELECT COUNT(*) FROM notes WHERE id=?", (nid,)).fetchone()[0] == 0
    assert anki.execute("SELECT COUNT(*) FROM graves WHERE oid=? AND type=1", (nid,)).fetchone()[0] == 1


def test_main_returns_0_with_empty_plan(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tt_path)])
    assert rc == 0
    assert "Nothing to delete" in capsys.readouterr().out


def test_main_dry_run_with_empty_plan(tmp_path, capsys):
    anki_path = _make_anki_db(tmp_path)
    tt_path = _make_tt_db(tmp_path)
    rc = main(["--dry-run", "--anki-db", str(anki_path), "--tt-db", str(tt_path)])
    assert rc == 0
    assert "Nothing to delete" in capsys.readouterr().out


def test_main_returns_1_when_anki_path_missing(tmp_path):
    rc = main(["--anki-db", str(tmp_path / "missing.db"), "--tt-db", str(tmp_path / "missing2.db")])
    assert rc == 1


def test_main_returns_1_when_tt_path_missing(tmp_path):
    anki_path = _make_anki_db(tmp_path)
    rc = main(["--anki-db", str(anki_path), "--tt-db", str(tmp_path / "missing.db")])
    assert rc == 1
