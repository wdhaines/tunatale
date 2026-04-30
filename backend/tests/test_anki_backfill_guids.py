"""End-to-end tests for the Stage 2b GUID backfill CLI.

Exercises the full pipeline against the minimal Anki fixture:
    safe_open(rw) → plan_guid_backfill → apply_guid_backfill → audit_changes

Each test sets up a distinct fixture state and calls `backfill_guids(...)`
directly (bypassing argparse). The AnkiWeb preflight prompt is covered
separately in test_anki_syncKey_preflight.py.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from app.anki.backfill_guids import backfill_guids
from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME
from app.common.guid import compute_guid

_SVNT_MID = 999_000_099
_SVNT_DECK_ID = 54321


def _build_svnt_db(tmp_path: Path, notes: list[tuple[int, str, str]]) -> Path:
    """Minimal Anki DB with Slovene Vocabulary notetype.

    notes: [(note_id, slovene_field, english_field), ...]
    """
    db_path = tmp_path / "svnt.anki2"
    with closing(sqlite3.connect(str(db_path))) as conn:
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
            CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT,
                mtime_secs INTEGER, usn INTEGER, common BLOB, kind BLOB);
            CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT,
                mtime_secs INTEGER, usn INTEGER, config BLOB);
            CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB,
                PRIMARY KEY (ntid, ord));
        """)
        conn.execute("INSERT INTO col VALUES (1,0,0,0,18,0,0,0,'{}','{}','{}','{}','{}')")
        conn.execute("INSERT INTO decks VALUES (?, '0. Slovene', 0, 0, x'', x'')", (_SVNT_DECK_ID,))
        conn.execute("INSERT INTO notetypes VALUES (?, ?, 0, 0, x'')", (_SVNT_MID, SLOVENE_VOCAB_NOTETYPE_NAME))
        field_names = ["Slovene", "English", "Audio", "Image", "Grammar", "Note", "DisambigKey"]
        conn.executemany(
            "INSERT INTO fields VALUES (?, ?, ?, x'')",
            [(_SVNT_MID, i, name) for i, name in enumerate(field_names)],
        )
        now_ts = int(time.time())
        for card_id, (nid, slovene, english) in enumerate(notes, start=5000):
            flds = "\x1f".join([slovene, english, "", "", "", "", ""])
            conn.execute(
                "INSERT INTO notes VALUES (?, ?, ?, ?, -1, '', ?, ?, 0, 0, '')",
                (nid, f"guid_{nid}", _SVNT_MID, now_ts, flds, slovene),
            )
            conn.execute(
                "INSERT INTO cards VALUES (?, ?, ?, 0, ?, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')",
                (card_id, nid, _SVNT_DECK_ID, now_ts),
            )
        conn.commit()
    return db_path


def _set_note_guid(db_path: Path, note_id: int, guid: str) -> None:
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("UPDATE notes SET guid=? WHERE id=?", (guid, note_id))
        conn.commit()


def _set_note_fields(db_path: Path, note_id: int, fields: str) -> None:
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("UPDATE notes SET flds=? WHERE id=?", (fields, note_id))
        conn.commit()


def _all_guids(db_path: Path) -> dict[int, str]:
    with closing(sqlite3.connect(str(db_path))) as conn:
        rows = conn.execute("SELECT id, guid FROM notes").fetchall()
    return {r[0]: r[1] for r in rows}


def _all_notes_mod(db_path: Path) -> dict[int, int]:
    with closing(sqlite3.connect(str(db_path))) as conn:
        rows = conn.execute("SELECT id, mod FROM notes").fetchall()
    return {r[0]: r[1] for r in rows}


def _col_usn(db_path: Path) -> int:
    with closing(sqlite3.connect(str(db_path))) as conn:
        return conn.execute("SELECT usn FROM col").fetchone()[0]


def _col_mod(db_path: Path) -> int:
    with closing(sqlite3.connect(str(db_path))) as conn:
        return conn.execute("SELECT mod FROM col").fetchone()[0]


class TestBackfillGuidsCLI:
    def test_default_run_skips_conflicts(self, fake_anki_db, tmp_path, capsys):
        """Without --force, existing non-matching guids are logged and skipped."""
        pre = _all_guids(fake_anki_db)
        result = backfill_guids(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            force=False,
        )
        post = _all_guids(fake_anki_db)

        assert post == pre, "no guids should change in default (no --force) mode"
        assert result["updated"] == 0
        assert result["skipped_conflicts"] == 5
        assert _col_usn(fake_anki_db) == 0

    def test_force_updates_differing_guids(self, fake_anki_db, tmp_path):
        result = backfill_guids(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            force=True,
        )
        post = _all_guids(fake_anki_db)

        assert post[1001] == compute_guid("banka", "sl")
        assert post[1002] == compute_guid("hiša", "sl")
        assert post[1003] == compute_guid("miza", "sl")
        assert post[1004] == compute_guid("stol", "sl")
        assert post[1005] == compute_guid("knjiga", "sl")
        assert result["updated"] == 5
        assert _col_usn(fake_anki_db) == -1

    def test_force_bumps_notes_mod_only_on_updated_rows(self, fake_anki_db, tmp_path):
        # Pre-seed: note 1001 already has matching guid (= noop)
        _set_note_guid(fake_anki_db, 1001, compute_guid("banka", "sl"))
        # Seed distinct mod so we can detect untouched rows
        with closing(sqlite3.connect(str(fake_anki_db))) as conn:
            for nid in (1001, 1002, 1003, 1004, 1005):
                conn.execute("UPDATE notes SET mod=? WHERE id=?", (111_111, nid))
            conn.commit()

        backfill_guids(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            force=True,
        )
        mods = _all_notes_mod(fake_anki_db)
        assert mods[1001] == 111_111, "noop row must not have mod bumped"
        for nid in (1002, 1003, 1004, 1005):
            assert mods[nid] != 111_111, f"updated row {nid} must have mod bumped"

    def test_force_sets_notes_usn_minus_one_on_updated_rows(self, fake_anki_db, tmp_path):
        """Regression: apply_guid_backfill must mark updated rows as dirty (usn=-1).

        Without this, Anki's integrity checks re-detect the bumped mod and flip usn
        itself on next open, bumping col.scm and forcing a full AnkiWeb resync.
        """
        # Pre-seed: note 1001 already has matching guid (= noop); all rows usn=0.
        _set_note_guid(fake_anki_db, 1001, compute_guid("banka", "sl"))
        with closing(sqlite3.connect(str(fake_anki_db))) as conn:
            for nid in (1001, 1002, 1003, 1004, 1005):
                conn.execute("UPDATE notes SET usn=0 WHERE id=?", (nid,))
            conn.commit()

        backfill_guids(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            force=True,
        )

        with closing(sqlite3.connect(str(fake_anki_db))) as conn:
            usn_by_id = dict(conn.execute("SELECT id, usn FROM notes").fetchall())
        assert usn_by_id[1001] == 0, "noop row must not have usn touched"
        for nid in (1002, 1003, 1004, 1005):
            assert usn_by_id[nid] == -1, f"updated row {nid} must have usn=-1 (dirty)"

    def test_dry_run_does_not_modify_source(self, fake_anki_db, tmp_path):
        from app.anki.safety import _sha256_file

        pre_guids = _all_guids(fake_anki_db)
        pre_sha = _sha256_file(fake_anki_db)

        result = backfill_guids(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db,
            anki_backup_dir=tmp_path / "bak",
            dry_run=True,
            force=True,
        )
        post_guids = _all_guids(fake_anki_db)
        post_sha = _sha256_file(fake_anki_db)

        assert post_guids == pre_guids
        assert post_sha == pre_sha
        assert result["updated"] == 0, "dry-run reports 0 actually applied"
        assert result.get("planned_updates", 0) == 5

    def test_dry_run_still_creates_backup(self, fake_anki_db, tmp_path):
        backup_dir = tmp_path / "bak"
        backfill_guids(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db,
            anki_backup_dir=backup_dir,
            dry_run=True,
            force=True,
        )
        # Safety envelope runs even for --dry-run
        assert backup_dir.exists()
        backups = list(backup_dir.glob("collection.anki2.bak_*"))
        assert len(backups) == 1

    def test_rerun_after_force_is_idempotent_noop(self, fake_anki_db, tmp_path):
        # First run: force all updates
        backfill_guids(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db,
            anki_backup_dir=tmp_path / "bak1",
            dry_run=False,
            force=True,
        )
        pre_rerun_mods = _all_notes_mod(fake_anki_db)
        pre_rerun_col_mod = _col_mod(fake_anki_db)

        # Second run: default mode should see noops
        result = backfill_guids(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db,
            anki_backup_dir=tmp_path / "bak2",
            dry_run=False,
            force=False,
        )
        assert result["updated"] == 0
        assert result["skipped_conflicts"] == 0
        assert result["noops"] == 5
        # Nothing should have been re-bumped
        assert _all_notes_mod(fake_anki_db) == pre_rerun_mods
        assert _col_mod(fake_anki_db) == pre_rerun_col_mod

    def test_duplicate_text_skipped_even_with_force(self, fake_anki_db, tmp_path):
        """Two notes with identical l2_text → neither is updated; no UNIQUE violation."""
        _set_note_fields(fake_anki_db, 1002, "banka\x1fbank2")
        pre = _all_guids(fake_anki_db)

        result = backfill_guids(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            force=True,
        )
        post = _all_guids(fake_anki_db)

        assert post[1001] == pre[1001], "duplicate 1001 must not be updated"
        assert post[1002] == pre[1002], "duplicate 1002 must not be updated"
        assert result["skipped_duplicates"] == 2
        assert 1001 not in [nid for nid, g in post.items() if g == compute_guid("banka", "sl")]

    def test_audit_detects_out_of_plan_write(self, fake_anki_db, tmp_path, monkeypatch):
        """If something writes a row not in plan.updates, audit must raise."""
        from app.anki import backfill_guids as mod

        real_apply = mod.apply_guid_backfill

        def sneaky_apply(conn, plan, now_ts):
            real_apply(conn, plan, now_ts)
            # Sneak in an unplanned UPDATE outside the plan
            conn.execute("UPDATE notes SET guid=? WHERE id=?", ("ROGUE", 1003))
            conn.commit()

        monkeypatch.setattr(mod, "apply_guid_backfill", sneaky_apply)

        with pytest.raises(RuntimeError, match="1003|unplanned|unexpected|audit"):
            backfill_guids(
                deck_name="0. Slovene",
                anki_collection_path=fake_anki_db,
                anki_backup_dir=tmp_path / "bak",
                dry_run=False,
                force=True,
            )

    def test_raises_when_deck_not_found(self, fake_anki_db, tmp_path):
        with pytest.raises(RuntimeError, match="[Dd]eck"):
            backfill_guids(
                deck_name="Nonexistent Deck",
                anki_collection_path=fake_anki_db,
                anki_backup_dir=tmp_path / "bak",
                dry_run=True,
                force=True,
            )

    def test_defaults_from_settings_when_kwargs_none(self, fake_anki_db, tmp_path, monkeypatch):
        """All kwargs None → values are read from app.config.settings."""
        from app.anki import backfill_guids as mod

        fake_settings = type(
            "S",
            (),
            {
                "anki_deck_name": "0. Slovene",
                "anki_collection_path": fake_anki_db,
                "anki_backup_dir": tmp_path / "bak",
            },
        )()
        monkeypatch.setattr(mod, "settings", fake_settings)

        result = backfill_guids(dry_run=True, force=True)
        assert result["planned_updates"] == 5


class TestCLIEntrypoint:
    def test_cli_dry_run_invokes_backfill_guids(self, fake_anki_db, tmp_path, monkeypatch, capsys):
        """_cli() parses argv and calls backfill_guids — ensures the console entrypoint works."""
        from app.anki import backfill_guids as mod

        monkeypatch.setattr("sys.argv", ["backfill_guids", "--dry-run", "--force"])

        captured: dict[str, object] = {}

        def fake_backfill(**kwargs):
            captured.update(kwargs)
            return {
                "updated": 0,
                "planned_updates": 5,
                "noops": 0,
                "skipped_conflicts": 0,
                "skipped_duplicates": 0,
                "aborted": False,
            }

        monkeypatch.setattr(mod, "backfill_guids", fake_backfill)
        mod._cli()

        assert captured["dry_run"] is True
        assert captured["force"] is True
        assert captured["deck_name"] is None


class TestBackfillGuidsSuffixPreflight:
    """Preflight: refuse --force when Slovene Vocabulary notes still carry suffix."""

    def test_force_raises_when_suffix_notes_present(self, tmp_path):
        db_path = _build_svnt_db(tmp_path, [(300, "barva (color)", "color")])
        with pytest.raises(RuntimeError, match="migrate_homonyms"):
            backfill_guids(
                deck_name="0. Slovene",
                anki_collection_path=db_path,
                anki_backup_dir=tmp_path / "bak",
                force=True,
                dry_run=False,
            )

    def test_force_raises_on_dry_run_too(self, tmp_path):
        """--force --dry-run also blocked: prevents previewing wrong-GUID plan."""
        db_path = _build_svnt_db(tmp_path, [(301, "pes (stray)", "stray dog")])
        with pytest.raises(RuntimeError, match="migrate_homonyms"):
            backfill_guids(
                deck_name="0. Slovene",
                anki_collection_path=db_path,
                anki_backup_dir=tmp_path / "bak",
                force=True,
                dry_run=True,
            )

    def test_no_suffix_notes_force_proceeds(self, tmp_path):
        """No suffix in Slovene field → preflight passes, backfill runs."""
        db_path = _build_svnt_db(tmp_path, [(302, "barva", "color")])
        result = backfill_guids(
            deck_name="0. Slovene",
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            force=True,
            dry_run=False,
        )
        assert result["updated"] >= 1

    def test_no_svnt_notetype_force_proceeds(self, tmp_path):
        """notetypes table present but SVNT entry absent → preflight is a no-op."""
        db_path = _build_svnt_db(tmp_path, [])
        # Remove the SVNT notetype entry so the notetype is unknown
        with closing(sqlite3.connect(str(db_path))) as conn:
            conn.execute("DELETE FROM notetypes WHERE name = ?", (SLOVENE_VOCAB_NOTETYPE_NAME,))
            conn.commit()

        result = backfill_guids(
            deck_name="0. Slovene",
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            force=True,
            dry_run=True,
        )
        assert result["planned_updates"] == 0
