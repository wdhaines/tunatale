"""Tests for the guid-divergence audit (Stage H1 — read-only diagnostic)."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from app.anki.audit_guids import run_audit
from app.common.guid import compute_guid

DECK_ID = 12345
DECK_NAME = "0. Slovene"
NOTETYPE_MID = 999_000_001


def _build_post_edit_anki_db(tmp_path: Path) -> Path:
    """Anki DB in post-merge/post-backfill state with one note edited back.

    Notes:
      1: Slovene field = "barva"          (user removed suffix; stored guid from "barva (color)")
      2: Slovene field = "barva (paint)"  (clean homonym, suffix intact)
      3: Slovene field = "pes"            (clean non-homonym)
    """
    db_path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(db_path))

    conn.execute("""CREATE TABLE col (
        id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,
        dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT,
        decks TEXT, dconf TEXT, tags TEXT)""")
    conn.execute("""CREATE TABLE notes (
        id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
        tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT)""")
    conn.execute("""CREATE TABLE cards (
        id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
        usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER,
        factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER,
        odue INTEGER, odid INTEGER, flags INTEGER, data TEXT)""")
    conn.execute("""CREATE TABLE revlog (
        id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,
        lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER)""")
    conn.execute("""CREATE TABLE decks (
        id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB)""")
    conn.execute("""CREATE TABLE notetypes (
        id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)""")
    conn.execute("""CREATE TABLE fields (
        ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord))""")
    conn.execute("""CREATE TABLE templates (
        ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB,
        PRIMARY KEY (ntid, ord))""")

    conn.execute("INSERT INTO col VALUES (1,1704067200,0,0,18,0,0,0,'{}','{}','{}','{}','{}')")
    conn.execute("INSERT INTO decks VALUES (?, ?, 0, 0, x'')", (DECK_ID, DECK_NAME))
    conn.execute("INSERT INTO notetypes VALUES (?, 'Slovene Vocabulary', 0, 0, x'')", (NOTETYPE_MID,))
    for i, name in enumerate(["Slovene", "English", "Audio", "Image", "Grammar", "Note"]):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, x'')", (NOTETYPE_MID, i, name))
    conn.execute("INSERT INTO templates VALUES (?, 0, 'Recognition', 0, 0, x'')", (NOTETYPE_MID,))
    conn.execute("INSERT INTO templates VALUES (?, 1, 'Production', 0, 0, x'')", (NOTETYPE_MID,))

    # Note 1: user edited "barva (color)" → "barva" in Anki desktop.
    # The stored guid was set by backfill from the original suffixed text.
    guid1 = compute_guid("barva (color)", "sl")
    conn.execute(
        "INSERT INTO notes VALUES (1, ?, ?, 0, 0, '', ?, 'barva', 0, 0, '')",
        (guid1, NOTETYPE_MID, "barva\x1fcolor\x1f\x1f\x1f\x1f"),
    )

    # Note 2: clean homonym — suffix is still intact; stored guid matches current field.
    guid2 = compute_guid("barva (paint)", "sl")
    conn.execute(
        "INSERT INTO notes VALUES (2, ?, ?, 0, 0, '', ?, 'barva (paint)', 0, 0, '')",
        (guid2, NOTETYPE_MID, "barva (paint)\x1fpaint\x1f\x1f\x1f\x1f"),
    )

    # Note 3: clean non-homonym — no suffix; stored guid matches current field.
    guid3 = compute_guid("pes", "sl")
    conn.execute(
        "INSERT INTO notes VALUES (3, ?, ?, 0, 0, '', ?, 'pes', 0, 0, '')",
        (guid3, NOTETYPE_MID, "pes\x1fdog\x1f\x1f\x1f\x1f"),
    )

    # Each note gets 2 cards (recognition + production) in the target deck.
    for nid in (1, 2, 3):
        for ord_ in (0, 1):
            conn.execute(
                "INSERT INTO cards VALUES (?, ?, ?, ?, 0, 0, 2, 2, 10, 21, 2500, 5, 0, 0, 0, 0, 0, '')",
                (nid * 100 + ord_, nid, DECK_ID, ord_),
            )

    conn.commit()
    conn.close()
    return db_path


def _init_tt_db(db_path: str) -> None:
    """Set up a TunaTale DB schema and insert rows for the audit test.

    Rows correspond to the original (pre-user-edit) state of the Anki notes.
    Guids are computed from the ORIGINAL Slovene texts (some have suffixes).
    """
    from app.srs.database import SRSDatabase

    SRSDatabase(db_path)  # run migrations → schema ready

    today = date.today().isoformat()
    conn = sqlite3.connect(db_path)

    for text, note_id in [
        ("barva (color)", 1),  # note 1 — user later edited back to bare
        ("barva (paint)", 2),  # note 2 — clean, untouched
        ("pes", 3),  # note 3 — non-homonym
    ]:
        g = compute_guid(text, "sl")
        conn.execute(
            """INSERT INTO collocations
               (text, translation, language_code, word_count, unit_difficulty, source, guid, anki_note_id)
               VALUES (?, '', 'sl', 1, 1, 'anki', ?, ?)""",
            (text, g, note_id),
        )
        coll_id = conn.execute("SELECT id FROM collocations WHERE text = ?", (text,)).fetchone()[0]
        for d in ("recognition", "production"):
            conn.execute(
                "INSERT OR IGNORE INTO collocation_directions (collocation_id, direction, due_date) VALUES (?, ?, ?)",
                (coll_id, d, today),
            )

    # Orphan: has " (X)" suffix pattern, anki_note_id IS NULL.
    orphan_text = "hiša (window)"
    orphan_guid = compute_guid(orphan_text, "sl")
    conn.execute(
        """INSERT INTO collocations
           (text, translation, language_code, word_count, unit_difficulty, source, guid, anki_note_id)
           VALUES (?, '', 'sl', 1, 1, 'anki', ?, NULL)""",
        (orphan_text, orphan_guid),
    )
    coll_id = conn.execute("SELECT id FROM collocations WHERE text = ?", (orphan_text,)).fetchone()[0]
    for d in ("recognition", "production"):
        conn.execute(
            "INSERT OR IGNORE INTO collocation_directions (collocation_id, direction, due_date) VALUES (?, ?, ?)",
            (coll_id, d, today),
        )

    conn.commit()
    conn.close()


class TestAuditGuids:
    @pytest.fixture
    def audit_setup(self, tmp_path):
        anki_path = _build_post_edit_anki_db(tmp_path)
        tt_path = str(tmp_path / "tunatale.db")
        _init_tt_db(tt_path)
        backup_dir = tmp_path / "bak"
        return anki_path, tt_path, backup_dir

    def test_single_divergent_note_found(self, audit_setup):
        anki_path, tt_path, backup_dir = audit_setup
        result = run_audit(
            deck_name=DECK_NAME,
            anki_collection_path=anki_path,
            anki_backup_dir=backup_dir,
            tunatale_db_path=tt_path,
        )
        assert len(result.divergent) == 1

    def test_edited_back_homonym_classified_correctly(self, audit_setup):
        anki_path, tt_path, backup_dir = audit_setup
        result = run_audit(
            deck_name=DECK_NAME,
            anki_collection_path=anki_path,
            anki_backup_dir=backup_dir,
            tunatale_db_path=tt_path,
        )
        note = result.divergent[0]
        assert note.note_id == 1
        assert note.current_slovene == "barva"
        assert note.classification == "edited_away_from_suffix"
        assert note.tt_stored_text == "barva (color)"

    def test_clean_notes_not_divergent(self, audit_setup):
        anki_path, tt_path, backup_dir = audit_setup
        result = run_audit(
            deck_name=DECK_NAME,
            anki_collection_path=anki_path,
            anki_backup_dir=backup_dir,
            tunatale_db_path=tt_path,
        )
        divergent_ids = {n.note_id for n in result.divergent}
        assert 2 not in divergent_ids
        assert 3 not in divergent_ids

    def test_tt_orphan_found(self, audit_setup):
        anki_path, tt_path, backup_dir = audit_setup
        result = run_audit(
            deck_name=DECK_NAME,
            anki_collection_path=anki_path,
            anki_backup_dir=backup_dir,
            tunatale_db_path=tt_path,
        )
        assert len(result.tt_orphans) == 1
        assert result.tt_orphans[0].text == "hiša (window)"

    def test_note_count_reported(self, audit_setup):
        anki_path, tt_path, backup_dir = audit_setup
        result = run_audit(
            deck_name=DECK_NAME,
            anki_collection_path=anki_path,
            anki_backup_dir=backup_dir,
            tunatale_db_path=tt_path,
        )
        assert result.note_count == 3

    def test_stored_and_expected_guids_on_divergent(self, audit_setup):
        anki_path, tt_path, backup_dir = audit_setup
        result = run_audit(
            deck_name=DECK_NAME,
            anki_collection_path=anki_path,
            anki_backup_dir=backup_dir,
            tunatale_db_path=tt_path,
        )
        note = result.divergent[0]
        assert note.stored_guid == compute_guid("barva (color)", "sl")
        assert note.expected_guid == compute_guid("barva", "sl")

    def test_both_orphans_classification(self, audit_setup, tmp_path):
        """Note whose stored guid resolves to nothing in TunaTale → both_orphans."""
        anki_path, tt_path, backup_dir = audit_setup
        # Plant a note with a stored guid that has NO matching TunaTale row.
        conn = sqlite3.connect(str(anki_path))
        conn.execute("DELETE FROM notes WHERE id = 1")
        orphan_guid = compute_guid("ghost (none)", "sl")  # not in TunaTale
        conn.execute(
            "INSERT INTO notes VALUES (1, ?, ?, 0, 0, '', ?, 'ghost', 0, 0, '')",
            (orphan_guid, NOTETYPE_MID, "ghost\x1fnone\x1f\x1f\x1f\x1f"),
        )
        conn.commit()
        conn.close()

        result = run_audit(
            deck_name=DECK_NAME,
            anki_collection_path=anki_path,
            anki_backup_dir=backup_dir,
            tunatale_db_path=tt_path,
        )
        assert any(n.classification == "both_orphans" for n in result.divergent)

    def test_edited_toward_different_text(self, audit_setup):
        """Stored guid resolves to a TunaTale row without a simple suffix, but field changed."""
        anki_path, tt_path, backup_dir = audit_setup
        # Replace note 3 (pes) so stored guid points to TunaTale row "pes" but Anki field now
        # says "kuža" — not a suffix edit, just a different word.
        conn = sqlite3.connect(str(anki_path))
        stored_guid_pes = compute_guid("pes", "sl")  # still points to TunaTale "pes" row
        conn.execute("DELETE FROM notes WHERE id = 3")
        conn.execute(
            "INSERT INTO notes VALUES (3, ?, ?, 0, 0, '', ?, 'kuža', 0, 0, '')",
            (stored_guid_pes, NOTETYPE_MID, "kuža\x1fpuppy\x1f\x1f\x1f\x1f"),
        )
        conn.commit()
        conn.close()

        result = run_audit(
            deck_name=DECK_NAME,
            anki_collection_path=anki_path,
            anki_backup_dir=backup_dir,
            tunatale_db_path=tt_path,
        )
        divergent_by_id = {n.note_id: n for n in result.divergent}
        assert 3 in divergent_by_id
        assert divergent_by_id[3].classification == "edited_toward_different_text"


class TestAuditGuidsMisc:
    """Settings-defaults, write-report, and error paths."""

    def test_settings_defaults_used_when_none_passed(self, tmp_path, monkeypatch):
        from app.anki import audit_guids as mod

        anki_path = _build_post_edit_anki_db(tmp_path)
        tt_path = str(tmp_path / "tunatale.db")
        _init_tt_db(tt_path)

        fake_settings = type(
            "S",
            (),
            {
                "anki_deck_name": DECK_NAME,
                "anki_collection_path": anki_path,
                "anki_backup_dir": tmp_path / "bak",
                "database_url": f"sqlite:///{tt_path}",
            },
        )()
        monkeypatch.setattr(mod, "settings", fake_settings)
        result = mod.run_audit()
        assert result.note_count == 3

    def test_write_report_creates_file(self, tmp_path, monkeypatch):
        from app.anki.audit_guids import AuditResult, _write_report

        monkeypatch.setattr("app.anki.audit_guids.Path.home", lambda: tmp_path)
        result = AuditResult(deck_name=DECK_NAME, note_count=5)
        out = _write_report(result)
        assert out.exists()
        import json

        data = json.loads(out.read_text())
        assert data["note_count"] == 5

    def test_deck_not_found_raises(self, tmp_path):
        anki_path = _build_post_edit_anki_db(tmp_path)
        tt_path = str(tmp_path / "tunatale.db")
        _init_tt_db(tt_path)
        import pytest

        with pytest.raises(RuntimeError, match="not found"):
            run_audit(
                deck_name="NoSuchDeck",
                anki_collection_path=anki_path,
                anki_backup_dir=tmp_path / "bak",
                tunatale_db_path=tt_path,
            )

    def test_non_suffix_orphan_not_included(self, tmp_path):
        """A TT row with anki_note_id=NULL but no suffix pattern is not counted as an orphan."""
        anki_path = _build_post_edit_anki_db(tmp_path)
        tt_path = str(tmp_path / "tunatale.db")
        _init_tt_db(tt_path)
        # Add an orphan without a suffix pattern
        conn = sqlite3.connect(tt_path)
        g = compute_guid("brez-suffiksa", "sl")
        conn.execute(
            "INSERT INTO collocations"
            " (text, translation, language_code, word_count, unit_difficulty, source, guid, anki_note_id)"
            " VALUES ('brez-suffiksa', '', 'sl', 1, 1, 'anki', ?, NULL)",
            (g,),
        )
        conn.commit()
        conn.close()

        result = run_audit(
            deck_name=DECK_NAME,
            anki_collection_path=anki_path,
            anki_backup_dir=tmp_path / "bak",
            tunatale_db_path=tt_path,
        )
        orphan_texts = {o.text for o in result.tt_orphans}
        assert "brez-suffiksa" not in orphan_texts
        assert "hiša (window)" in orphan_texts
