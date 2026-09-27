"""Grave named cards: remove specific collocations the Anki-safe way (tunatale-u8nz.20).

The Cebuano stage-2 conversion removes affixed vocab cards (matulog, magkita,
nagdala, mangape) whose roots are already cards. The Anki half reuses
``grave_ignored_lemma_cards.apply_graves`` (graves, not bare DELETEs; col.mod
only). A learner deck with no Anki is TT rows only, and the script refuses to
treat a pushed row that way — dropping it from TT alone would let the next sync
resurrect it from Anki.

tmp_path / in-memory DBs only; never a real collection.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.config import settings
from scripts.anki_archive.grave_named_cards import main, plan_by_texts

_GRAVE_KIND_CARD, _GRAVE_KIND_NOTE = 0, 1


def _anki(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS graves (oid INTEGER NOT NULL, type INTEGER NOT NULL, usn INTEGER NOT NULL,"
        " PRIMARY KEY (oid, type))"
    )
    conn.commit()
    return conn


def _seed_note(conn, nid: int, cids: tuple[int, ...]) -> None:
    conn.execute(
        "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data)"
        " VALUES (?, 'g', 1, 0, 0, '', 'f\x1fb', 'f', 0, 0, '')",
        (nid,),
    )
    for ord_, cid in enumerate(cids):
        conn.execute(
            "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, left,"
            " odue, odid, flags, data) VALUES (?, ?, 1, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (cid, nid, ord_),
        )
    conn.commit()


def _tt(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path) if path else ":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE collocations (id INTEGER PRIMARY KEY, text TEXT, language_code TEXT, anki_note_id INTEGER)"
    )
    conn.execute("CREATE TABLE collocation_directions (collocation_id INTEGER, direction TEXT)")
    conn.commit()
    return conn


def _seed_tt(conn, cid: int, text: str, nid: int | None = None, lang: str = "ceb") -> None:
    conn.execute("INSERT INTO collocations VALUES (?, ?, ?, ?)", (cid, text, lang, nid))
    for d in ("recognition", "production"):
        conn.execute("INSERT INTO collocation_directions VALUES (?, ?)", (cid, d))
    conn.commit()


# ── plan_by_texts ─────────────────────────────────────────────────────────────


def test_plans_exactly_the_named_rows_case_insensitively(fake_anki_db):
    anki = _anki(fake_anki_db)
    _seed_note(anki, 900, (901, 902))
    tt = _tt()
    _seed_tt(tt, 1, "matulog", nid=900)
    _seed_tt(tt, 2, "tulog")
    _seed_tt(tt, 3, "Magkita")
    plan, missing = plan_by_texts(anki, tt, "ceb", ["matulog", "magkita"])
    assert [(r.text, r.anki_nid, r.anki_cids, r.tt_collocation_id) for r in plan] == [
        ("matulog", 900, (901, 902), 1),
        ("Magkita", None, (), 3),
    ]
    assert missing == []


def test_a_name_with_no_row_is_reported_not_silently_skipped():
    tt = _tt()
    _seed_tt(tt, 1, "matulog")
    _plan, missing = plan_by_texts(None, tt, "ceb", ["matulog", "nagdala"])
    assert missing == ["nagdala"]


def test_only_the_named_language_is_touched():
    tt = _tt()
    _seed_tt(tt, 1, "kita", lang="tl")
    plan, missing = plan_by_texts(None, tt, "ceb", ["kita"])
    assert plan == [] and missing == ["kita"]


def test_without_a_collection_a_pushed_row_is_refused():
    """TT-only removal of a row Anki holds would be undone by the next sync."""
    tt = _tt()
    _seed_tt(tt, 1, "matulog", nid=900)
    with pytest.raises(ValueError, match="matulog"):
        plan_by_texts(None, tt, "ceb", ["matulog"])


# ── main, end to end ──────────────────────────────────────────────────────────


def test_main_without_anki_deletes_tt_rows_only(tmp_path, capsys):
    db = tmp_path / "u2.db"
    tt = _tt(db)
    _seed_tt(tt, 1, "matulog")
    _seed_tt(tt, 2, "tulog")
    tt.close()
    assert main(["--language", "ceb", "--tt-db", str(db), "--no-anki", "--texts", "matulog", "--dry-run"]) == 0
    assert "--dry-run" in capsys.readouterr().out
    assert main(["--language", "ceb", "--tt-db", str(db), "--no-anki", "--texts", "matulog"]) == 0
    left = [r[0] for r in sqlite3.connect(db).execute("SELECT text FROM collocations")]
    assert left == ["tulog"]


def test_main_with_anki_graves_and_deletes(tmp_path, fake_anki_db, monkeypatch, capsys):
    monkeypatch.setattr(settings, "anki_backup_dir", tmp_path / "backups")
    anki = _anki(fake_anki_db)
    _seed_note(anki, 900, (901, 902))
    usn_before = anki.execute("SELECT usn FROM col").fetchone()[0]
    anki.close()
    db = tmp_path / "owner.db"
    tt = _tt(db)
    _seed_tt(tt, 1, "matulog", nid=900)
    tt.close()

    assert main(["--language", "ceb", "--tt-db", str(db), "--anki-db", str(fake_anki_db), "--texts", "matulog"]) == 0

    anki = sqlite3.connect(fake_anki_db)
    assert anki.execute("SELECT count(*) FROM notes WHERE id = 900").fetchone()[0] == 0
    graves = sorted(anki.execute("SELECT oid, type, usn FROM graves").fetchall())
    assert graves == [(900, _GRAVE_KIND_NOTE, -1), (901, _GRAVE_KIND_CARD, -1), (902, _GRAVE_KIND_CARD, -1)]
    assert anki.execute("SELECT usn FROM col").fetchone()[0] == usn_before  # the sync anchor, untouched
    assert sqlite3.connect(db).execute("SELECT count(*) FROM collocations").fetchone()[0] == 0


def test_main_fails_loudly_on_a_missing_name(tmp_path, capsys):
    db = tmp_path / "u2.db"
    _tt(db).close()
    assert main(["--language", "ceb", "--tt-db", str(db), "--no-anki", "--texts", "nagdala"]) == 1
    assert "nagdala" in capsys.readouterr().err


def test_main_needs_a_collection_or_no_anki(tmp_path, monkeypatch, capsys):
    db = tmp_path / "u2.db"
    _tt(db).close()
    monkeypatch.setattr(settings, "tt_collection_path", tmp_path / "absent.anki2")
    assert main(["--language", "ceb", "--tt-db", str(db), "--texts", "x"]) == 1
    assert "not found" in capsys.readouterr().err
