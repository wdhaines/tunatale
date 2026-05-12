"""Tests for app.anki.fix_html_concat_imports.

Cleans up TT collocations whose `text` was concatenated from an Anki
`<b>L2</b><br><i>EN</i>` field at import time (Layer 31).
"""

from __future__ import annotations

import sqlite3

from app.anki.fix_html_concat_imports import (
    PlanItem,
    apply_plan,
    main,
    plan_cleanup,
)


def _make_tt_db_with_collocations(*rows: dict) -> sqlite3.Connection:
    """Minimal TT DB with collocations + collocation_directions tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE collocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT UNIQUE NOT NULL,
            translation TEXT NOT NULL DEFAULT '',
            anki_note_id INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE collocation_directions (
            collocation_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            reps INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'new',
            PRIMARY KEY (collocation_id, direction)
        )"""
    )
    for row in rows:
        conn.execute(
            "INSERT INTO collocations (id, text, translation, anki_note_id) VALUES (?, ?, ?, ?)",
            (row["id"], row["text"], row.get("translation", ""), row.get("anki_note_id")),
        )
        for d in row.get("directions", []):
            conn.execute(
                "INSERT INTO collocation_directions (collocation_id, direction, reps, state) VALUES (?, ?, ?, ?)",
                (row["id"], d["direction"], d.get("reps", 0), d.get("state", "new")),
            )
    conn.commit()
    return conn


def _make_anki_db_with_notes(*notes: dict) -> sqlite3.Connection:
    """Minimal Anki DB with notes table."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, mid INTEGER, flds TEXT)")
    for n in notes:
        conn.execute(
            "INSERT INTO notes (id, mid, flds) VALUES (?, ?, ?)",
            (n["id"], n.get("mid", 1519651961633), n["flds"]),
        )
    conn.commit()
    return conn


def test_plan_renames_no_twin_row():
    """Row with `<b>X</b><br><i>Y</i>` Anki note and no clean-X twin → rename in place."""
    tt = _make_tt_db_with_collocations(
        {
            "id": 1,
            "text": "ničnothing",
            "translation": "[sound:sl_nic.mp3]",
            "anki_note_id": 999,
            "directions": [{"direction": "recognition", "reps": 3, "state": "review"}],
        }
    )
    anki = _make_anki_db_with_notes({"id": 999, "flds": "<b>nič</b><br><i>nothing</i>\x1f[sound:sl_nic.mp3]"})

    plan = plan_cleanup(tt, anki)
    assert len(plan) == 1
    assert plan[0] == PlanItem(action="rename", tt_id=1, new_text="nič", new_translation="nothing")


def test_plan_deletes_when_clean_twin_exists():
    """Row with `<b>X</b><br><i>Y</i>` Anki note AND a clean-X twin → delete the mangled row."""
    tt = _make_tt_db_with_collocations(
        {"id": 1, "text": "ulica", "anki_note_id": 100, "directions": [{"direction": "recognition", "reps": 5}]},
        {
            "id": 2,
            "text": "ulicastreet",
            "anki_note_id": 999,
            "directions": [{"direction": "recognition", "reps": 4}],
        },
    )
    anki = _make_anki_db_with_notes({"id": 999, "flds": "<b>ulica</b><br><i>street</i>\x1f[sound:sl_ulica.mp3]"})

    plan = plan_cleanup(tt, anki)
    assert len(plan) == 1
    assert plan[0] == PlanItem(action="delete", tt_id=2, new_text=None, new_translation=None)


def test_plan_skips_unaffected_rows():
    """Rows whose Anki note doesn't match the pattern are untouched."""
    tt = _make_tt_db_with_collocations(
        {"id": 1, "text": "banka", "anki_note_id": 200},
        {"id": 2, "text": "What sound is v?", "anki_note_id": 300},  # phonics question
    )
    anki = _make_anki_db_with_notes(
        {"id": 200, "flds": "banka\x1fbank"},
        {"id": 300, "flds": "What sound is <b>v</b> word-initial?\x1f[wː]"},
    )
    assert plan_cleanup(tt, anki) == []


def test_plan_skips_rows_with_null_anki_note_id():
    """User-added rows (not yet synced) are not touched."""
    tt = _make_tt_db_with_collocations({"id": 1, "text": "fresh_unsynced", "anki_note_id": None})
    anki = _make_anki_db_with_notes()
    assert plan_cleanup(tt, anki) == []


def test_apply_plan_renames():
    tt = _make_tt_db_with_collocations({"id": 5, "text": "rekariver", "translation": "[stale]", "anki_note_id": 42})
    plan = [PlanItem(action="rename", tt_id=5, new_text="reka", new_translation="river")]
    apply_plan(tt, plan)
    row = tt.execute("SELECT text, translation FROM collocations WHERE id=5").fetchone()
    assert row["text"] == "reka"
    assert row["translation"] == "river"


def test_apply_plan_deletes_and_cascades_directions():
    tt = _make_tt_db_with_collocations(
        {"id": 1, "text": "ulica", "anki_note_id": 100},
        {
            "id": 2,
            "text": "ulicastreet",
            "anki_note_id": 999,
            "directions": [{"direction": "recognition", "reps": 4}, {"direction": "production", "reps": 0}],
        },
    )
    plan = [PlanItem(action="delete", tt_id=2, new_text=None, new_translation=None)]
    apply_plan(tt, plan)
    assert tt.execute("SELECT COUNT(*) FROM collocations WHERE id=2").fetchone()[0] == 0
    assert tt.execute("SELECT COUNT(*) FROM collocation_directions WHERE collocation_id=2").fetchone()[0] == 0
    # Clean twin survives.
    assert tt.execute("SELECT text FROM collocations WHERE id=1").fetchone()["text"] == "ulica"


def test_apply_plan_rename_conflict_falls_back_to_delete():
    """Defensive: if a rename would violate UNIQUE, delete the mangled row instead.

    Catches the case where plan_cleanup couldn't see a twin but the DB has one
    via a race or pre-existing dup.
    """
    tt = _make_tt_db_with_collocations(
        {"id": 1, "text": "voda", "anki_note_id": 50},
        {"id": 2, "text": "vodawater", "anki_note_id": 51},
    )
    # Forge a plan that wants to rename id=2 → 'voda' (conflicts with id=1).
    plan = [PlanItem(action="rename", tt_id=2, new_text="voda", new_translation="water")]
    apply_plan(tt, plan)
    # Mangled row deleted; clean stays.
    assert tt.execute("SELECT COUNT(*) FROM collocations WHERE id=2").fetchone()[0] == 0
    assert tt.execute("SELECT text FROM collocations WHERE id=1").fetchone()["text"] == "voda"


def test_plan_cleanup_dry_run_does_not_mutate():
    tt = _make_tt_db_with_collocations(
        {"id": 1, "text": "ničnothing", "anki_note_id": 999, "directions": [{"direction": "recognition"}]}
    )
    anki = _make_anki_db_with_notes({"id": 999, "flds": "<b>nič</b><br><i>nothing</i>\x1f"})
    plan_cleanup(tt, anki)
    # plan_cleanup doesn't mutate TT.
    row = tt.execute("SELECT text FROM collocations WHERE id=1").fetchone()
    assert row["text"] == "ničnothing"


def test_plan_skips_already_clean_row():
    """Row whose text already matches the parsed L2 is a no-op."""
    tt = _make_tt_db_with_collocations({"id": 1, "text": "nič", "translation": "nothing", "anki_note_id": 999})
    anki = _make_anki_db_with_notes({"id": 999, "flds": "<b>nič</b><br><i>nothing</i>\x1f"})
    assert plan_cleanup(tt, anki) == []


def test_plan_skips_missing_anki_note():
    """anki_note_id pointing to a deleted Anki note → silently skip."""
    tt = _make_tt_db_with_collocations({"id": 1, "text": "ničnothing", "anki_note_id": 999})
    anki = _make_anki_db_with_notes()  # empty
    assert plan_cleanup(tt, anki) == []


# ── CLI tests ──────────────────────────────────────────────────────────────────


def _seed_dbs_for_cli(tmp_path):
    """Write a TT + Anki sqlite file pair under tmp_path; return (tt_path, anki_path)."""
    tt_path = tmp_path / "tt.db"
    anki_path = tmp_path / "anki.db"
    conn = sqlite3.connect(str(tt_path))
    conn.execute(
        """CREATE TABLE collocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT UNIQUE NOT NULL,
            translation TEXT NOT NULL DEFAULT '',
            anki_note_id INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE collocation_directions (
            collocation_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            reps INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'new',
            PRIMARY KEY (collocation_id, direction)
        )"""
    )
    conn.execute("INSERT INTO collocations (id, text, translation, anki_note_id) VALUES (1, 'ničnothing', '[s]', 999)")
    conn.commit()
    conn.close()

    anki_conn = sqlite3.connect(str(anki_path))
    anki_conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, mid INTEGER, flds TEXT)")
    anki_conn.execute("INSERT INTO notes (id, mid, flds) VALUES (999, 1, '<b>nič</b><br><i>nothing</i>\x1f')")
    anki_conn.commit()
    anki_conn.close()
    return tt_path, anki_path


def test_main_dry_run_does_not_mutate(tmp_path, capsys):
    tt_path, anki_path = _seed_dbs_for_cli(tmp_path)
    rc = main(["--dry-run", "--tt-db", str(tt_path), "--anki-db", str(anki_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Found 1 mangled rows" in out
    assert "--dry-run" in out

    # No mutation.
    conn = sqlite3.connect(str(tt_path))
    row = conn.execute("SELECT text FROM collocations WHERE id=1").fetchone()
    assert row[0] == "ničnothing"


def test_main_applies_plan_when_not_dry_run(tmp_path, capsys):
    tt_path, anki_path = _seed_dbs_for_cli(tmp_path)
    rc = main(["--tt-db", str(tt_path), "--anki-db", str(anki_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Applied:" in out

    conn = sqlite3.connect(str(tt_path))
    row = conn.execute("SELECT text, translation FROM collocations WHERE id=1").fetchone()
    assert row[0] == "nič"
    assert row[1] == "nothing"


def test_main_missing_tt_db_returns_1(tmp_path, capsys):
    rc = main(["--tt-db", str(tmp_path / "does_not_exist.db"), "--anki-db", str(tmp_path / "also_no.db")])
    assert rc == 1
    assert "TT database not found" in capsys.readouterr().err


def test_main_missing_anki_db_returns_1(tmp_path, capsys):
    tt_path = tmp_path / "tt.db"
    sqlite3.connect(str(tt_path)).close()  # touch file
    rc = main(["--tt-db", str(tt_path), "--anki-db", str(tmp_path / "no.db")])
    assert rc == 1
    assert "Anki collection not found" in capsys.readouterr().err


def test_main_prints_delete_lines_for_twinned_rows(tmp_path, capsys):
    """CLI prints `DELETE id=N` for mangled rows that have a clean twin."""
    tt_path = tmp_path / "tt.db"
    anki_path = tmp_path / "anki.db"
    conn = sqlite3.connect(str(tt_path))
    conn.execute(
        """CREATE TABLE collocations (
            id INTEGER PRIMARY KEY,
            text TEXT UNIQUE NOT NULL,
            translation TEXT NOT NULL DEFAULT '',
            anki_note_id INTEGER
        )"""
    )
    conn.execute("CREATE TABLE collocation_directions (collocation_id INTEGER, direction TEXT)")
    # Clean twin + mangled row → cleanup should DELETE the mangled one.
    conn.execute("INSERT INTO collocations VALUES (1, 'ulica', '', 100)")
    conn.execute("INSERT INTO collocations VALUES (2, 'ulicastreet', '[s]', 999)")
    conn.commit()
    conn.close()

    anki_conn = sqlite3.connect(str(anki_path))
    anki_conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, mid INTEGER, flds TEXT)")
    anki_conn.execute("INSERT INTO notes VALUES (999, 1, '<b>ulica</b><br><i>street</i>\x1f[s]')")
    anki_conn.commit()
    anki_conn.close()

    rc = main(["--dry-run", "--tt-db", str(tt_path), "--anki-db", str(anki_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DELETE id=2" in out


def test_main_empty_plan_prints_nothing_to_apply(tmp_path, capsys):
    """Run with TT/Anki databases that have no mangled rows → exit 0 with 'Nothing to apply'."""
    tt_path = tmp_path / "tt.db"
    anki_path = tmp_path / "anki.db"
    conn = sqlite3.connect(str(tt_path))
    conn.execute(
        """CREATE TABLE collocations (
            id INTEGER PRIMARY KEY,
            text TEXT UNIQUE NOT NULL,
            translation TEXT NOT NULL DEFAULT '',
            anki_note_id INTEGER
        )"""
    )
    conn.execute("CREATE TABLE collocation_directions (collocation_id INTEGER, direction TEXT)")
    conn.commit()
    conn.close()
    anki_conn = sqlite3.connect(str(anki_path))
    anki_conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, mid INTEGER, flds TEXT)")
    anki_conn.commit()
    anki_conn.close()
    rc = main(["--tt-db", str(tt_path), "--anki-db", str(anki_path)])
    assert rc == 0
    assert "Nothing to apply" in capsys.readouterr().out
