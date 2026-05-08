"""End-to-end bootstrap test: merge_dupes → backfill_guids → import_seed.

Exercises the full Stage 2 pipeline on a synthetic Anki collection and
verifies the structural invariants that Stage 3 sync depends on:

  - Every imported collocation has 1 or 2 direction rows, matching the
    Anki notetype's template count (single-template notes like the
    "Basic" notetype produce only a recognition direction).
  - Every GUID in TunaTale matches compute_guid(text, lang).
  - All imported notes have anki_note_id set.
  - GUID continuity: the Anki notes.guid written by backfill_guids matches
    the GUID TunaTale derives independently from the same L2 text.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.anki.backfill_guids import backfill_guids
from app.anki.import_seed import import_seed
from app.anki.merge_dupes import merge_dupes
from app.common.guid import compute_guid
from tests.conftest import build_slovene_pairs_anki_db


def _anki_guid_map(db_path: Path) -> dict[int, str]:
    """Return {note_id: guid} from the Anki collection after backfill."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT id, guid FROM notes").fetchall()
    finally:
        conn.close()
    return {r[0]: r[1] for r in rows}


def _anki_note_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    finally:
        conn.close()


class TestBootstrapE2E:
    """merge_dupes → backfill_guids → import_seed pipeline invariants."""

    @pytest.fixture
    def pipeline_result(self, tmp_path):
        """Run the full pipeline; return (anki_path, tunatale_db_path, results)."""
        anki_path = build_slovene_pairs_anki_db(tmp_path)
        backup_dir = tmp_path / "bak"
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        fallback_log = tmp_path / "fallback.log"
        tt_db_path = str(tmp_path / "tunatale.db")

        # Step 1: merge Basic pairs into two-card notes
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=anki_path,
            anki_backup_dir=backup_dir,
            dry_run=False,
            yes=True,
        )

        # Step 2: write deterministic GUIDs (force avoids syncKey prompt)
        backfill_guids(
            deck_name="0. Slovene",
            anki_collection_path=anki_path,
            anki_backup_dir=backup_dir,
            dry_run=False,
            force=True,
        )

        # Step 3: import into TunaTale
        results = import_seed(
            deck_name="0. Slovene",
            anki_collection_path=anki_path,
            anki_media_path=tmp_path / "anki_media",
            anki_backup_dir=backup_dir,
            tunatale_db_path=tt_db_path,
            media_dir=media_dir,
            fallback_log_path=fallback_log,
            dry_run=False,
        )

        return anki_path, tt_db_path, results

    def test_merge_reduces_note_count(self, tmp_path):
        anki_path = build_slovene_pairs_anki_db(tmp_path)
        assert _anki_note_count(anki_path) == 13

        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=anki_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=True,
        )
        # 13 notes → 8 keepers (3 pairs + 2 homonym-pairs + 2 singletons + 1 unknown)
        assert _anki_note_count(anki_path) == 8

    def test_import_creates_expected_parents(self, pipeline_result):
        _, tt_db_path, results = pipeline_result
        assert results["new_parents"] >= 7  # at minimum 7 vocab items (excluding skipped)
        # Every parent has 1 or 2 directions (single-template notes contribute 1).
        assert results["new_parents"] <= results["new_directions"] <= results["new_parents"] * 2

    def test_every_parent_has_one_or_two_direction_rows(self, pipeline_result):
        """Paired notetypes get both directions; single-template notes
        (recognition- or production-only singletons surfaced by merge_dupes)
        get just the one their Anki card backs."""
        _, tt_db_path, _ = pipeline_result
        conn = sqlite3.connect(tt_db_path)
        try:
            rows = conn.execute(
                """
                SELECT c.id, COUNT(d.direction) as dir_count
                FROM collocations c
                LEFT JOIN collocation_directions d ON d.collocation_id = c.id
                GROUP BY c.id
                """
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) > 0
        for row_id, dir_count in rows:
            assert dir_count in (1, 2), f"collocation id={row_id} has {dir_count} direction rows (expected 1 or 2)"

    def test_guid_matches_compute_guid(self, pipeline_result):
        """Every TunaTale GUID equals compute_guid(text, language_code, disambig_key)."""
        _, tt_db_path, _ = pipeline_result
        conn = sqlite3.connect(tt_db_path)
        try:
            rows = conn.execute("SELECT text, language_code, disambig_key, guid FROM collocations").fetchall()
        finally:
            conn.close()
        assert len(rows) > 0
        for text, lang, disambig_key, guid in rows:
            expected = compute_guid(text, lang, disambig_key or "")
            assert guid == expected, f"GUID mismatch for {text!r}: stored={guid!r} computed={expected!r}"

    def test_anki_note_ids_populated(self, pipeline_result):
        """All imported rows have anki_note_id set (not NULL)."""
        _, tt_db_path, _ = pipeline_result
        conn = sqlite3.connect(tt_db_path)
        try:
            null_count = conn.execute("SELECT COUNT(*) FROM collocations WHERE anki_note_id IS NULL").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]
        finally:
            conn.close()
        assert total > 0
        assert null_count == 0, f"{null_count}/{total} collocations have NULL anki_note_id"

    def test_anki_guid_continuity(self, pipeline_result):
        """Anki notes.guid after backfill matches TunaTale's guid for the same text."""
        anki_path, tt_db_path, _ = pipeline_result

        anki_guids = _anki_guid_map(anki_path)

        tt_conn = sqlite3.connect(tt_db_path)
        try:
            rows = tt_conn.execute("SELECT anki_note_id, guid FROM collocations").fetchall()
        finally:
            tt_conn.close()

        for anki_note_id, tt_guid in rows:
            assert anki_note_id is not None
            anki_guid = anki_guids.get(anki_note_id)
            assert anki_guid is not None, f"note_id={anki_note_id} missing from Anki notes table"
            assert tt_guid == anki_guid, (
                f"GUID mismatch for note_id={anki_note_id}: anki={anki_guid!r} tunatale={tt_guid!r}"
            )

    def test_pipeline_is_idempotent(self, pipeline_result, tmp_path):
        """Running import_seed again does not create duplicate parents."""
        anki_path, tt_db_path, first_results = pipeline_result
        backup_dir = tmp_path / "bak2"
        backup_dir.mkdir(exist_ok=True)

        second_results = import_seed(
            deck_name="0. Slovene",
            anki_collection_path=anki_path,
            anki_media_path=tmp_path / "anki_media",
            anki_backup_dir=backup_dir,
            tunatale_db_path=tt_db_path,
            media_dir=tmp_path / "media",
            fallback_log_path=tmp_path / "fallback2.log",
            dry_run=False,
        )
        assert second_results["new_parents"] == 0

    def test_hand_edit_then_reimport_does_not_duplicate(self, pipeline_result, tmp_path):
        """Clearing DisambigKey on an Anki note then reimporting must not create a dup row.

        This is the regression for the pre-H2 bug: a user clears or loses the
        DisambigKey field (field 6), so the guid computed from the current note
        no longer matches the stored TunaTale guid.  import_seed must fall back
        to the anki_note_id lookup and skip rather than creating a duplicate row.
        """
        anki_path, tt_db_path, _ = pipeline_result
        backup_dir = tmp_path / "bak_edit"
        backup_dir.mkdir(exist_ok=True)

        # Find the anki_note_id for the barva/color collocation in TunaTale.
        tt_conn = sqlite3.connect(tt_db_path)
        try:
            row = tt_conn.execute(
                "SELECT id, anki_note_id FROM collocations WHERE text = 'barva' AND disambig_key = 'color'"
            ).fetchone()
        finally:
            tt_conn.close()
        assert row is not None, "barva/color collocation not found after pipeline"
        original_coll_id, anki_note_id = row[0], row[1]

        parent_count_before = sqlite3.connect(tt_db_path).execute("SELECT COUNT(*) FROM collocations").fetchone()[0]

        # Simulate user clearing DisambigKey (field 6) — the dangerous edit that loses
        # the homonym disambiguation.
        anki_conn = sqlite3.connect(str(anki_path))
        try:
            flds_row = anki_conn.execute("SELECT flds FROM notes WHERE id = ?", (anki_note_id,)).fetchone()
            assert flds_row is not None
            fields = flds_row[0].split("\x1f")
            fields[6] = ""  # clear DisambigKey
            anki_conn.execute(
                "UPDATE notes SET flds = ? WHERE id = ?",
                ("\x1f".join(fields), anki_note_id),
            )
            anki_conn.commit()
        finally:
            anki_conn.close()

        # Reimport — with G3 fix this falls back to anki_note_id and skips creation.
        second_results = import_seed(
            deck_name="0. Slovene",
            anki_collection_path=anki_path,
            anki_media_path=tmp_path / "anki_media",
            anki_backup_dir=backup_dir,
            tunatale_db_path=tt_db_path,
            media_dir=tmp_path / "media",
            fallback_log_path=tmp_path / "fallback_edit.log",
            dry_run=False,
        )

        assert second_results["new_parents"] == 0, "reimport must not create a duplicate parent row"

        parent_count_after = sqlite3.connect(tt_db_path).execute("SELECT COUNT(*) FROM collocations").fetchone()[0]
        assert parent_count_after == parent_count_before

        # Original row still carries the anki_note_id link.
        tt_conn = sqlite3.connect(tt_db_path)
        try:
            surviving = tt_conn.execute(
                "SELECT id FROM collocations WHERE anki_note_id = ?", (anki_note_id,)
            ).fetchall()
        finally:
            tt_conn.close()
        assert len(surviving) == 1
        assert surviving[0][0] == original_coll_id
