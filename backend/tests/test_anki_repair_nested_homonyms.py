"""Tests for app.anki.repair_nested_homonyms (one-shot fix for 3 nested-paren rows)."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

import pytest

from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME
from app.common.guid import compute_guid

_DECK_NAME = "0. Slovene"
_DECK_ID = 12345
_SVNT_MID = 999_000_001

# The three rows to repair: must match _MANIFEST in repair_nested_homonyms.py (sans flds_hash).
_REPAIR_ROWS = [
    {"anki_note_id": 1775264032874, "slovene_bare": "nizek", "disambig": "short (≠tall)", "tt_id": 15},
    {"anki_note_id": 1775264032898, "slovene_bare": "star", "disambig": "old (≠new)", "tt_id": 598},
    {"anki_note_id": 1775264032902, "slovene_bare": "star", "disambig": "old (≠young)", "tt_id": 600},
]

# Pre-repair Anki flds for each note (matches _make_anki_db state, not live DB).
_PRE_REPAIR_FLDS = {
    1775264032874: ["nizek", "short (≠tall)", "", "", "", "", ""],
    1775264032898: ["star", "old (≠new)", "", "", "", "", ""],
    1775264032902: ["star (old (≠young))", "old (≠young)", "", "", "", "", ""],
}


def _flds_hash(fields: list[str]) -> str:
    return hashlib.sha256("\x1f".join(fields).encode()).hexdigest()


def _test_manifest() -> list[dict]:
    """Build a manifest whose flds_hash values match _make_anki_db (not the live DB)."""
    return [
        {
            "anki_note_id": row["anki_note_id"],
            "flds_hash": _flds_hash(_PRE_REPAIR_FLDS[row["anki_note_id"]]),
            "slovene_bare": row["slovene_bare"],
            "disambig": row["disambig"],
            "tt_id": row["tt_id"],
        }
        for row in _REPAIR_ROWS
    ]


def _make_anki_db(tmp_path: Path) -> Path:
    """Minimal Anki DB with the three broken notes in their pre-repair state."""
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
    conn.execute("INSERT INTO notetypes VALUES (?, ?, 0, 0, x'')", (_SVNT_MID, SLOVENE_VOCAB_NOTETYPE_NAME))
    for i, name in enumerate(["Slovene", "English", "Audio", "Image", "Grammar", "Note", "DisambigKey"]):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, x'')", (_SVNT_MID, i, name))

    now_ts = int(time.time())
    for card_id, (nid, fields) in enumerate(sorted(_PRE_REPAIR_FLDS.items()), start=9000):
        flds = "\x1f".join(fields)
        conn.execute(
            "INSERT INTO notes VALUES (?, ?, ?, ?, 0, '', ?, ?, 0, 0, '')",
            (nid, f"old_guid_{nid}", _SVNT_MID, now_ts, flds, fields[0]),
        )
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (card_id, nid, _DECK_ID, now_ts),
        )
    conn.commit()
    conn.close()
    return db_path


def _make_tt_db(tmp_path: Path) -> Path:
    """Minimal TunaTale DB with the three collocations in their pre-repair state."""
    db_path = tmp_path / "tunatale.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE collocations (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            language_code TEXT NOT NULL DEFAULT 'sl',
            guid TEXT,
            disambig_key TEXT NOT NULL DEFAULT '',
            anki_note_id INTEGER
        )
    """)
    pre_repair_texts = {
        15: "nizek (short (≠tall))",
        598: "star (old (≠new))",
        600: "star (old (≠young))",
    }
    for row in _REPAIR_ROWS:
        conn.execute(
            "INSERT INTO collocations (id, text, language_code, guid, disambig_key, anki_note_id) VALUES (?,?,?,?,?,?)",
            (
                row["tt_id"],
                pre_repair_texts[row["tt_id"]],
                "sl",
                f"old_guid_{row['anki_note_id']}",
                "",
                row["anki_note_id"],
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def _read_note(db_path: Path, nid: int) -> dict:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT guid, flds, usn FROM notes WHERE id=?", (nid,)).fetchone()
    conn.close()
    return {"guid": row[0], "fields": row[1].split("\x1f"), "usn": row[2]}


def _read_tt_row(db_path: Path, tt_id: int) -> dict:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT text, disambig_key, guid FROM collocations WHERE id=?", (tt_id,)).fetchone()
    conn.close()
    return {"text": row[0], "disambig_key": row[1], "guid": row[2]}


class TestRepairNestedHomonyms:
    def test_repairs_all_three_rows(self, tmp_path):
        """All three notes get bare Slovene, DisambigKey set, guid updated."""
        from app.anki.repair_nested_homonyms import repair_nested_homonyms

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)
        results = repair_nested_homonyms(
            anki_collection_path=anki_db,
            anki_backup_dir=tmp_path / "bak",
            tt_db_path=tt_db,
            force=True,
        )
        assert results["repaired"] == 3
        assert results["skipped"] == 0

    def test_slovene_bare_after_repair(self, tmp_path):
        from app.anki.repair_nested_homonyms import repair_nested_homonyms

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)
        repair_nested_homonyms(
            anki_collection_path=anki_db, anki_backup_dir=tmp_path / "bak", tt_db_path=tt_db, force=True
        )
        for row in _REPAIR_ROWS:
            note = _read_note(anki_db, row["anki_note_id"])
            assert note["fields"][0] == row["slovene_bare"], f"nid={row['anki_note_id']}"

    def test_disambig_key_set_after_repair(self, tmp_path):
        from app.anki.repair_nested_homonyms import repair_nested_homonyms

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)
        repair_nested_homonyms(
            anki_collection_path=anki_db, anki_backup_dir=tmp_path / "bak", tt_db_path=tt_db, force=True
        )
        for row in _REPAIR_ROWS:
            note = _read_note(anki_db, row["anki_note_id"])
            assert note["fields"][6] == row["disambig"], f"nid={row['anki_note_id']}"

    def test_guids_match_on_both_sides(self, tmp_path):
        """Anki notes.guid and TT collocations.guid must match after repair."""
        from app.anki.repair_nested_homonyms import repair_nested_homonyms

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)
        repair_nested_homonyms(
            anki_collection_path=anki_db, anki_backup_dir=tmp_path / "bak", tt_db_path=tt_db, force=True
        )
        for row in _REPAIR_ROWS:
            note = _read_note(anki_db, row["anki_note_id"])
            tt_row = _read_tt_row(tt_db, row["tt_id"])
            expected_guid = compute_guid(row["slovene_bare"], "sl", row["disambig"])
            assert note["guid"] == expected_guid, f"nid={row['anki_note_id']} anki guid wrong"
            assert tt_row["guid"] == expected_guid, f"tt_id={row['tt_id']} TT guid wrong"

    def test_tt_text_and_disambig_stripped_after_repair(self, tmp_path):
        """TunaTale collocations.text is bare, disambig_key populated after repair."""
        from app.anki.repair_nested_homonyms import repair_nested_homonyms

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)
        repair_nested_homonyms(
            anki_collection_path=anki_db, anki_backup_dir=tmp_path / "bak", tt_db_path=tt_db, force=True
        )
        for row in _REPAIR_ROWS:
            tt_row = _read_tt_row(tt_db, row["tt_id"])
            assert tt_row["text"] == row["slovene_bare"], f"tt_id={row['tt_id']}"
            assert tt_row["disambig_key"] == row["disambig"], f"tt_id={row['tt_id']}"

    def test_usn_set_to_minus_one(self, tmp_path):
        """All repaired notes get usn=-1 (dirty → push on next sync)."""
        from app.anki.repair_nested_homonyms import repair_nested_homonyms

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)
        repair_nested_homonyms(
            anki_collection_path=anki_db, anki_backup_dir=tmp_path / "bak", tt_db_path=tt_db, force=True
        )
        for row in _REPAIR_ROWS:
            note = _read_note(anki_db, row["anki_note_id"])
            assert note["usn"] == -1, f"nid={row['anki_note_id']} usn not -1"

    def test_idempotent_on_second_run(self, tmp_path):
        """Running twice: second pass skips all rows (already repaired)."""
        from app.anki.repair_nested_homonyms import repair_nested_homonyms

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)
        repair_nested_homonyms(
            anki_collection_path=anki_db, anki_backup_dir=tmp_path / "bak", tt_db_path=tt_db, force=True
        )
        results2 = repair_nested_homonyms(
            anki_collection_path=anki_db, anki_backup_dir=tmp_path / "bak", tt_db_path=tt_db, force=True
        )
        assert results2["repaired"] == 0
        assert results2["skipped"] == 3

    def test_dry_run_makes_no_changes(self, tmp_path):
        from app.anki.repair_nested_homonyms import repair_nested_homonyms

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)
        results = repair_nested_homonyms(
            anki_collection_path=anki_db,
            anki_backup_dir=tmp_path / "bak",
            tt_db_path=tt_db,
            dry_run=True,
            force=True,
        )
        assert results["repaired"] == 3
        note = _read_note(anki_db, 1775264032902)
        assert note["fields"][0] == "star (old (≠young))"
        assert note["fields"][6] == ""

    def test_drift_detection_aborts_when_flds_changed(self, tmp_path):
        """If a note's flds hash doesn't match manifest, DriftError is raised."""
        from app.anki.repair_nested_homonyms import DriftError, repair_nested_homonyms

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)
        manifest = _test_manifest()

        # Corrupt one note's Slovene to a value whose hash won't match.
        conn = sqlite3.connect(str(anki_db))
        conn.execute(
            "UPDATE notes SET flds = ? WHERE id = ?",
            ("\x1f".join(["unexpected_value", "old (≠new)", "", "", "", "", ""]), 1775264032898),
        )
        conn.commit()
        conn.close()

        with pytest.raises(DriftError):
            repair_nested_homonyms(
                anki_collection_path=anki_db,
                anki_backup_dir=tmp_path / "bak",
                tt_db_path=tt_db,
                _manifest=manifest,
            )

    def test_drift_passes_when_hashes_match(self, tmp_path):
        """No DriftError when manifest hashes match the actual DB."""
        from app.anki.repair_nested_homonyms import repair_nested_homonyms

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)
        manifest = _test_manifest()

        results = repair_nested_homonyms(
            anki_collection_path=anki_db,
            anki_backup_dir=tmp_path / "bak",
            tt_db_path=tt_db,
            _manifest=manifest,
        )
        assert results["repaired"] == 3

    def test_force_skips_drift_check(self, tmp_path):
        """--force bypasses the drift hash check even when flds have changed."""
        from app.anki.repair_nested_homonyms import repair_nested_homonyms

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)
        conn = sqlite3.connect(str(anki_db))
        conn.execute(
            "UPDATE notes SET flds = ? WHERE id = ?",
            # This makes nizek bare with correct English — idempotent skip on that note
            ("\x1f".join(["unexpected", "old (≠new)", "", "", "", "", ""]), 1775264032898),
        )
        conn.commit()
        conn.close()

        # force=True should not raise, even with mismatched hash
        results = repair_nested_homonyms(
            anki_collection_path=anki_db,
            anki_backup_dir=tmp_path / "bak",
            tt_db_path=tt_db,
            force=True,
        )
        assert results["repaired"] + results["skipped"] == 3

    def test_note_not_found_in_anki_db_skips(self, tmp_path):
        """A manifest entry whose note_id doesn't exist in the DB is counted as skipped."""
        from app.anki.repair_nested_homonyms import repair_nested_homonyms

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)
        # Build a one-entry manifest pointing to a non-existent note id
        phantom_manifest = [
            {
                "anki_note_id": 9999999999,
                "flds_hash": "x" * 64,
                "slovene_bare": "phantom",
                "disambig": "ghost",
                "tt_id": 15,
            }
        ]
        results = repair_nested_homonyms(
            anki_collection_path=anki_db,
            anki_backup_dir=tmp_path / "bak",
            tt_db_path=tt_db,
            _manifest=phantom_manifest,
            force=True,
        )
        assert results["skipped"] == 1
        assert results["repaired"] == 0

    def test_note_with_fewer_than_seven_fields_is_padded(self, tmp_path):
        """Notes with < 7 fields are padded before repair without raising."""
        from app.anki.repair_nested_homonyms import repair_nested_homonyms

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)
        # Overwrite nid 1775264032902 with only 6 fields (no DisambigKey column)
        conn = sqlite3.connect(str(anki_db))
        six_flds = "\x1f".join(["star (old (≠young))", "old (≠young)", "", "", "", ""])
        conn.execute("UPDATE notes SET flds=? WHERE id=?", (six_flds, 1775264032902))
        conn.commit()
        conn.close()

        results = repair_nested_homonyms(
            anki_collection_path=anki_db,
            anki_backup_dir=tmp_path / "bak",
            tt_db_path=tt_db,
            force=True,
        )
        assert results["repaired"] == 3
        note = _read_note(anki_db, 1775264032902)
        assert note["fields"][0] == "star"
        assert note["fields"][6] == "old (≠young)"

    def test_uses_settings_defaults_when_args_are_none(self, tmp_path, monkeypatch):
        """anki_collection_path=None, anki_backup_dir=None, tt_db_path=None fall back to settings."""
        import app.anki.repair_nested_homonyms as mod

        anki_db = _make_anki_db(tmp_path)
        tt_db = _make_tt_db(tmp_path)

        class _FakeSettings:
            anki_collection_path = anki_db
            anki_backup_dir = tmp_path / "bak_settings"
            database_url = f"sqlite:///{tt_db}"

        monkeypatch.setattr(mod, "settings", _FakeSettings())
        results = mod.repair_nested_homonyms(force=True)
        assert results["repaired"] == 3
