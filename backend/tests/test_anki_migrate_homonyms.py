"""Tests for app.anki.migrate_homonyms (Stage H3 one-shot migration)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from app.anki.migrate_homonyms import migrate_homonyms
from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME

_DECK_NAME = "0. Slovene"
_DECK_ID = 12345
_SVNT_MID = 999_000_001  # Slovene Vocabulary notetype id


def _build_db(tmp_path: Path, notes: list[tuple[int, str, str]]) -> Path:
    """Create minimal Anki DB with Slovene Vocabulary notetype.

    notes: [(note_id, slovene_field, english_field), ...]
    """
    db_path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(db_path))
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
    conn.execute(
        "INSERT INTO notetypes VALUES (?, ?, 0, 0, x'')",
        (_SVNT_MID, SLOVENE_VOCAB_NOTETYPE_NAME),
    )
    field_names = ["Slovene", "English", "Audio", "Image", "Grammar", "Note", "DisambigKey"]
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?, ?, x'')",
        [(_SVNT_MID, i, name) for i, name in enumerate(field_names)],
    )
    now_ts = int(time.time())
    for card_id, (nid, slovene, english) in enumerate(notes, start=1000):
        flds = "\x1f".join([slovene, english, "", "", "", "", ""])
        conn.execute(
            "INSERT INTO notes VALUES (?, ?, ?, ?, -1, '', ?, ?, 0, 0, '')",
            (nid, f"guid_{nid}", _SVNT_MID, now_ts, flds, slovene),
        )
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, 0, ?, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (card_id, nid, _DECK_ID, now_ts),
        )
    conn.commit()
    conn.close()
    return db_path


def _read_fields(db_path: Path, note_id: int) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT flds FROM notes WHERE id=?", (note_id,)).fetchone()
    finally:
        conn.close()
    return row[0].split("\x1f")


class TestMigrateHomonyms:
    def test_strips_suffix_from_slovene_field(self, tmp_path):
        db_path = _build_db(tmp_path, [(100, "barva (color)", "color / shade")])
        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
        )
        assert results["stripped"] == 1
        assert results["skipped"] == 0
        fields = _read_fields(db_path, 100)
        assert fields[0] == "barva"
        assert fields[6] == "color"

    def test_skips_note_without_suffix(self, tmp_path):
        db_path = _build_db(tmp_path, [(101, "pes", "dog")])
        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
        )
        assert results["stripped"] == 0
        assert results["skipped"] == 1
        fields = _read_fields(db_path, 101)
        assert fields[0] == "pes"
        assert fields[6] == ""

    def test_skips_when_disambig_not_in_english(self, tmp_path):
        """Ownership check: if suffix doesn't appear in English field, skip."""
        db_path = _build_db(tmp_path, [(102, "banka (bank)", "financial institution")])
        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
        )
        assert results["skipped"] == 1
        assert results["stripped"] == 0
        fields = _read_fields(db_path, 102)
        assert fields[0] == "banka (bank)"

    def test_dry_run_makes_no_changes(self, tmp_path):
        db_path = _build_db(tmp_path, [(103, "barva (color)", "color")])
        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=True,
        )
        assert results["stripped"] == 1
        fields = _read_fields(db_path, 103)
        assert fields[0] == "barva (color)"

    def test_notetype_not_found_returns_empty(self, tmp_path):
        db_path = tmp_path / "empty.anki2"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER,
                ver INTEGER, dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT,
                models TEXT, decks TEXT, dconf TEXT, tags TEXT);
            CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT,
                mtime_secs INTEGER, usn INTEGER, config BLOB);
            CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT,
                mtime_secs INTEGER, usn INTEGER, common BLOB, kind BLOB);
            CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER,
                mod INTEGER, usn INTEGER, tags TEXT, flds TEXT, sfld TEXT,
                csum INTEGER, flags INTEGER, data TEXT);
            CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER,
                ord INTEGER, mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER,
                due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER,
                lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER,
                flags INTEGER, data TEXT);
        """)
        conn.execute("INSERT INTO col VALUES (1,0,0,0,18,0,0,0,'{}','{}','{}','{}','{}')")
        conn.execute("INSERT INTO decks VALUES (?, ?, 0, 0, x'', x'')", (_DECK_ID, _DECK_NAME))
        conn.commit()
        conn.close()
        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
        )
        assert results == {"stripped": 0, "skipped": 0, "recovered": 0, "padded": 0}

    def test_raises_when_deck_not_found(self, tmp_path):
        db_path = _build_db(tmp_path, [])
        with pytest.raises(RuntimeError, match="Deck.*not found"):
            migrate_homonyms(
                deck_name="Nonexistent Deck",
                anki_collection_path=db_path,
                anki_backup_dir=tmp_path / "bak",
                dry_run=False,
            )

    def test_note_with_fewer_than_seven_fields_padded(self, tmp_path):
        """Notes with < 7 fields (pre-DisambigKey) are padded before processing."""
        db_path = _build_db(tmp_path, [])
        # Manually insert a note with only 6 fields (no DisambigKey column yet)
        conn = sqlite3.connect(str(db_path))
        now_ts = int(time.time())
        six_field_flds = "\x1f".join(["barva (color)", "color", "", "", "", ""])
        conn.execute(
            "INSERT INTO notes VALUES (?, ?, ?, ?, -1, '', ?, ?, 0, 0, '')",
            (300, "guid_300", _SVNT_MID, now_ts, six_field_flds, "barva (color)"),
        )
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, 0, ?, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (3000, 300, _DECK_ID, now_ts),
        )
        conn.commit()
        conn.close()
        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
        )
        assert results["stripped"] == 1
        fields = _read_fields(db_path, 300)
        assert fields[0] == "barva"
        assert fields[6] == "color"

    def test_six_field_note_without_suffix_is_padded_in_db(self, tmp_path):
        """Regression: a 6-field non-homonym note must be written back with 7 fields.

        Without this, notetype has 7 fields but 4000+ notes stay at 6 flds, and on
        next open Anki raises 'note has 6 fields, expected 7'.
        """
        db_path = _build_db(tmp_path, [])
        conn = sqlite3.connect(str(db_path))
        now_ts = int(time.time())
        six_field_flds = "\x1f".join(["pes", "dog", "", "", "", ""])
        conn.execute(
            "INSERT INTO notes VALUES (?, ?, ?, ?, -1, '', ?, ?, 0, 0, '')",
            (301, "guid_301", _SVNT_MID, now_ts, six_field_flds, "pes"),
        )
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, 0, ?, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (3001, 301, _DECK_ID, now_ts),
        )
        conn.commit()
        conn.close()
        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
        )
        assert results["stripped"] == 0
        assert results["padded"] == 1
        fields = _read_fields(db_path, 301)
        assert len(fields) == 7
        assert fields[0] == "pes"
        assert fields[6] == ""

    def test_uses_settings_defaults_when_args_are_none(self, tmp_path, monkeypatch):
        """deck_name=None, anki_collection_path=None, anki_backup_dir=None fall back to settings."""
        import app.anki.migrate_homonyms as mod

        db_path = _build_db(tmp_path, [(400, "pes (dog)", "dog")])
        backup_dir = tmp_path / "bak_settings"

        class _FakeSettings:
            anki_deck_name = _DECK_NAME
            anki_collection_path = db_path
            anki_backup_dir = backup_dir

        monkeypatch.setattr(mod, "settings", _FakeSettings())
        results = mod.migrate_homonyms(
            deck_name=None,
            anki_collection_path=None,
            anki_backup_dir=None,
            dry_run=False,
        )
        assert results["stripped"] == 1

    def test_mixed_batch(self, tmp_path):
        """Stripped + skipped counts in a batch of mixed notes."""
        db_path = _build_db(
            tmp_path,
            [
                (200, "barva (color)", "color"),
                (201, "barva (paint)", "paint"),
                (202, "pes", "dog"),
            ],
        )
        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
        )
        assert results["stripped"] == 2
        assert results["skipped"] == 1

    def test_adds_disambig_field_to_six_field_notetype(self, tmp_path):
        """When notetype has only 6 fields, DisambigKey is inserted into fields table."""
        db_path = _build_db(tmp_path, [(500, "barva (color)", "color")])
        # Remove the DisambigKey field row to simulate a pre-H3 notetype
        conn = sqlite3.connect(str(db_path))
        conn.execute("DELETE FROM fields WHERE ntid = ? AND ord = 6", (_SVNT_MID,))
        conn.commit()
        conn.close()

        migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
        )

        conn = sqlite3.connect(str(db_path))
        field_count = conn.execute("SELECT COUNT(*) FROM fields WHERE ntid = ?", (_SVNT_MID,)).fetchone()[0]
        disambig_row = conn.execute("SELECT name FROM fields WHERE ntid = ? AND ord = 6", (_SVNT_MID,)).fetchone()
        conn.close()
        assert field_count == 7
        assert disambig_row is not None
        assert disambig_row[0] == "DisambigKey"

    def test_adding_field_bumps_notetype_and_scm(self, tmp_path):
        """Regression: inserting DisambigKey must bump notetypes.mtime_secs/usn and col.scm.

        Without this, Anki's 'Check Database' on next open detects the field-count
        mismatch, patches it itself, and that patch bumps col.scm — which forces
        a full AnkiWeb resync of every card in the deck.
        """
        db_path = _build_db(tmp_path, [(501, "barva (color)", "color")])
        # Remove DisambigKey to simulate a pre-H3 6-field notetype, and pin known
        # pre-run metadata values so the test can detect that they were updated.
        conn = sqlite3.connect(str(db_path))
        conn.execute("DELETE FROM fields WHERE ntid = ? AND ord = 6", (_SVNT_MID,))
        conn.execute("UPDATE notetypes SET mtime_secs=1000, usn=0 WHERE id=?", (_SVNT_MID,))
        conn.execute("UPDATE col SET scm=1000")
        conn.commit()
        conn.close()

        migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
        )

        conn = sqlite3.connect(str(db_path))
        try:
            nt_mtime, nt_usn = conn.execute("SELECT mtime_secs, usn FROM notetypes WHERE id=?", (_SVNT_MID,)).fetchone()
            col_scm = conn.execute("SELECT scm FROM col").fetchone()[0]
        finally:
            conn.close()
        assert nt_mtime > 1000, "notetypes.mtime_secs must be bumped when a field is added"
        assert nt_usn == -1, "notetypes.usn must be -1 (dirty) when a field is added"
        assert col_scm > 1000, "col.scm must be bumped when the schema changes (field added)"

    def test_no_field_added_does_not_bump_scm(self, tmp_path):
        """Idempotency: when all 7 fields already exist, col.scm stays put.

        Ensures the schema bump only fires on actual schema change, so reruns
        don't each trigger a new full AnkiWeb sync.
        """
        db_path = _build_db(tmp_path, [(502, "barva (color)", "color")])
        # 7 fields are already present via _build_db; pin the pre-run scm.
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE col SET scm=777")
        conn.execute("UPDATE notetypes SET mtime_secs=777, usn=0 WHERE id=?", (_SVNT_MID,))
        conn.commit()
        conn.close()

        migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
        )

        conn = sqlite3.connect(str(db_path))
        try:
            nt_mtime, nt_usn = conn.execute("SELECT mtime_secs, usn FROM notetypes WHERE id=?", (_SVNT_MID,)).fetchone()
            col_scm = conn.execute("SELECT scm FROM col").fetchone()[0]
        finally:
            conn.close()
        assert col_scm == 777, "col.scm must not be bumped when no field is added"
        assert nt_mtime == 777, "notetypes.mtime_secs must not be bumped when no field is added"
        assert nt_usn == 0, "notetypes.usn must be left clean when no field is added"


class TestMigrateHomonymsAuditJson:
    """G1: audit-json recovery path for user-edited-back notes."""

    def test_recovers_edited_back_note_via_audit_json(self, tmp_path):
        """Note with suffix removed by user gets DisambigKey recovered from audit JSON."""
        # nid 500: user edited "barva (color)" → "barva" (no suffix, no DisambigKey)
        # nid 501: "barva (paint)" still has suffix — normal strip path
        db_path = _build_db(
            tmp_path,
            [
                (500, "barva", "color"),  # already edited back — no suffix
                (501, "barva (paint)", "paint"),  # still has suffix
            ],
        )

        audit_data = {
            "divergent": [
                {
                    "note_id": 500,
                    "stored_guid": "dummy_stored",
                    "expected_guid": "dummy_expected",
                    "current_slovene": "barva",
                    "classification": "edited_away_from_suffix",
                    "tt_stored_text": "barva (color)",
                }
            ]
        }
        audit_json_path = tmp_path / "audit.json"
        audit_json_path.write_text(json.dumps(audit_data))

        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            audit_json=audit_json_path,
            dry_run=False,
        )

        assert results["recovered"] == 1
        assert results["stripped"] == 1
        assert results["skipped"] == 0

        # nid 500: Slovene unchanged, DisambigKey recovered from tt_stored_text
        fields_500 = _read_fields(db_path, 500)
        assert fields_500[0] == "barva"
        assert fields_500[6] == "color"

        # nid 501: normal strip
        fields_501 = _read_fields(db_path, 501)
        assert fields_501[0] == "barva"
        assert fields_501[6] == "paint"

    def test_non_edited_away_note_not_recovered(self, tmp_path):
        """A note without suffix that is NOT in the audit JSON stays skipped."""
        db_path = _build_db(tmp_path, [(600, "pes", "dog")])

        audit_data = {"divergent": []}  # empty — nid 600 not listed
        audit_json_path = tmp_path / "audit.json"
        audit_json_path.write_text(json.dumps(audit_data))

        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            audit_json=audit_json_path,
            dry_run=False,
        )
        assert results["recovered"] == 0
        assert results["skipped"] == 1
        fields = _read_fields(db_path, 600)
        assert fields[6] == ""

    def test_audit_json_dry_run_reports_without_writing(self, tmp_path):
        """dry_run with audit_json reports counts but makes no DB changes."""
        db_path = _build_db(tmp_path, [(700, "barva", "color")])
        audit_data = {
            "divergent": [
                {
                    "note_id": 700,
                    "stored_guid": "x",
                    "expected_guid": "y",
                    "current_slovene": "barva",
                    "classification": "edited_away_from_suffix",
                    "tt_stored_text": "barva (color)",
                }
            ]
        }
        audit_json_path = tmp_path / "audit.json"
        audit_json_path.write_text(json.dumps(audit_data))

        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            audit_json=audit_json_path,
            dry_run=True,
        )
        assert results["recovered"] == 1
        # No write: DisambigKey still empty
        fields = _read_fields(db_path, 700)
        assert fields[6] == ""

    def test_audit_json_non_edited_away_classification_ignored(self, tmp_path):
        """Divergent entries with classification other than edited_away_from_suffix are ignored."""
        db_path = _build_db(tmp_path, [(800, "barva", "color")])
        audit_data = {
            "divergent": [
                {
                    "note_id": 800,
                    "stored_guid": "x",
                    "expected_guid": "y",
                    "current_slovene": "barva",
                    "classification": "both_orphans",  # not edited_away_from_suffix
                    "tt_stored_text": "barva (color)",
                }
            ]
        }
        audit_json_path = tmp_path / "audit.json"
        audit_json_path.write_text(json.dumps(audit_data))

        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            audit_json=audit_json_path,
            dry_run=False,
        )
        assert results["recovered"] == 0
        assert results["skipped"] == 1
        fields = _read_fields(db_path, 800)
        assert fields[6] == ""

    def test_audit_json_tt_stored_text_without_suffix_not_recovered(self, tmp_path):
        """edited_away_from_suffix entry whose tt_stored_text lacks a suffix is skipped."""
        db_path = _build_db(tmp_path, [(900, "barva", "color")])
        audit_data = {
            "divergent": [
                {
                    "note_id": 900,
                    "stored_guid": "x",
                    "expected_guid": "y",
                    "current_slovene": "barva",
                    "classification": "edited_away_from_suffix",
                    "tt_stored_text": "barva",  # no suffix — regex won't match
                }
            ]
        }
        audit_json_path = tmp_path / "audit.json"
        audit_json_path.write_text(json.dumps(audit_data))

        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            audit_json=audit_json_path,
            dry_run=False,
        )
        assert results["recovered"] == 0
        assert results["skipped"] == 1


class TestNestedParenRegex:
    """Regression: suffix containing nested parens (e.g. 'old (≠new)') must be split."""

    def test_strips_nested_paren_suffix(self, tmp_path):
        """'star (old (≠young))' → bare='star', disambig='old (≠young)'."""
        db_path = _build_db(tmp_path, [(1001, "star (old (≠young))", "old (≠young)")])
        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
        )
        assert results["stripped"] == 1
        fields = _read_fields(db_path, 1001)
        assert fields[0] == "star"
        assert fields[6] == "old (≠young)"

    def test_audit_json_recovery_with_nested_paren_tt_text(self, tmp_path):
        """Recovery path works when tt_stored_text has nested parens."""
        db_path = _build_db(tmp_path, [(1002, "nizek", "short (≠tall)")])
        audit_data = {
            "divergent": [
                {
                    "note_id": 1002,
                    "stored_guid": "x",
                    "expected_guid": "y",
                    "current_slovene": "nizek",
                    "classification": "edited_away_from_suffix",
                    "tt_stored_text": "nizek (short (≠tall))",
                }
            ]
        }
        audit_json_path = tmp_path / "audit.json"
        audit_json_path.write_text(json.dumps(audit_data))
        results = migrate_homonyms(
            deck_name=_DECK_NAME,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            audit_json=audit_json_path,
            dry_run=False,
        )
        assert results["recovered"] == 1
        fields = _read_fields(db_path, 1002)
        assert fields[0] == "nizek"
        assert fields[6] == "short (≠tall)"
