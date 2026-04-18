"""End-to-end apply tests for ``app.anki.merge_dupes``.

Runs against ``fake_anki_db_slovene_pairs`` — a DB with pairs, homonyms,
singletons and unknowns — and verifies every invariant listed in the plan:

  - card-note relational integrity (no orphans, ord→template valid)
  - revlog unchanged row-for-row
  - FSRS state preserved on recognition cards
  - production cards reparented to their keeper + ord flipped
  - homonym disambiguation produces distinct guids
  - ``col.mod`` bumped, ``col.usn`` set to -1
  - idempotent when run a second time
  - audit raises on any write outside the plan
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.anki.merge_dupes import (
    MergePlan,
    UnifiedFields,
    _audit_merge,
    _load_card_maps,
    apply_merge,
    merge_dupes,
)
from app.common.guid import compute_guid


def _count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _row(db_path: Path, sql: str, params: tuple = ()) -> tuple:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _rows(db_path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class TestApplyMerge:
    def test_notes_count_drops_by_expected_amount(self, fake_anki_db_slovene_pairs, tmp_path):
        pre_notes = _count(fake_anki_db_slovene_pairs, "notes")
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=True,
        )
        post_notes = _count(fake_anki_db_slovene_pairs, "notes")
        # 13 source notes → 8 keepers (3 paired + 2 homonym-paired + 2 singletons + 1 unknown)
        assert pre_notes == 13
        assert post_notes == 8

    def test_cards_count_unchanged(self, fake_anki_db_slovene_pairs, tmp_path):
        pre = _count(fake_anki_db_slovene_pairs, "cards")
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=True,
        )
        assert _count(fake_anki_db_slovene_pairs, "cards") == pre

    def test_revlog_untouched(self, fake_anki_db_slovene_pairs, tmp_path):
        pre_rows = _rows(fake_anki_db_slovene_pairs, "SELECT id, cid, ease FROM revlog ORDER BY id")
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=True,
        )
        post_rows = _rows(fake_anki_db_slovene_pairs, "SELECT id, cid, ease FROM revlog ORDER BY id")
        assert post_rows == pre_rows

    def test_every_card_points_to_surviving_note(self, fake_anki_db_slovene_pairs, tmp_path):
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=True,
        )
        orphans = _rows(
            fake_anki_db_slovene_pairs,
            "SELECT c.id, c.nid FROM cards c LEFT JOIN notes n ON n.id = c.nid WHERE n.id IS NULL",
        )
        assert orphans == []

    def test_every_card_ord_matches_a_template_of_its_notetype(self, fake_anki_db_slovene_pairs, tmp_path):
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=True,
        )
        dangling = _rows(
            fake_anki_db_slovene_pairs,
            """
            SELECT c.id, c.ord, n.mid
            FROM cards c
            JOIN notes n ON n.id = c.nid
            LEFT JOIN templates t ON t.ntid = n.mid AND t.ord = c.ord
            WHERE t.ntid IS NULL
            """,
        )
        assert dangling == []

    def test_recognition_fsrs_state_preserved(self, fake_anki_db_slovene_pairs, tmp_path):
        pre_jabolko = _row(
            fake_anki_db_slovene_pairs,
            "SELECT reps, ivl, factor, due, data FROM cards WHERE id=?",
            (2001 * 10,),
        )
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=True,
        )
        post_jabolko = _row(
            fake_anki_db_slovene_pairs,
            "SELECT reps, ivl, factor, due, data FROM cards WHERE id=?",
            (2001 * 10,),
        )
        assert post_jabolko == pre_jabolko

    def test_production_card_reparented_and_ord_flipped(self, fake_anki_db_slovene_pairs, tmp_path):
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=True,
        )
        # jabolko production card was 2002*10 = 20020, now belongs to keeper 2001, ord=1
        row = _row(
            fake_anki_db_slovene_pairs,
            "SELECT nid, ord FROM cards WHERE id=?",
            (2002 * 10,),
        )
        assert row == (2001, 1)

    def test_production_only_singleton_card_ord_flipped_to_one(self, fake_anki_db_slovene_pairs, tmp_path):
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=True,
        )
        row = _row(
            fake_anki_db_slovene_pairs,
            "SELECT nid, ord FROM cards WHERE id=?",
            (2012 * 10,),
        )
        assert row == (2012, 1)

    def test_homonym_disambiguation_distinct_guids(self, fake_anki_db_slovene_pairs, tmp_path):
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=True,
        )
        # After merge, the two homonym keepers share ``barva`` but their Slovene
        # fields carry "(color)" / "(paint)" suffixes → their computed guids differ.
        rows = _rows(
            fake_anki_db_slovene_pairs,
            "SELECT id, sfld, flds FROM notes WHERE id IN (?, ?)",
            (2007, 2009),
        )
        slovene_texts = set()
        for _id, _sfld, flds in rows:
            fields = flds.split("\x1f")
            slovene_texts.add(fields[0])  # new notetype has Slovene at ord=0
        assert slovene_texts == {"barva (color)", "barva (paint)"}
        guids = {compute_guid(s, "sl") for s in slovene_texts}
        assert len(guids) == 2

    def test_col_mod_bumped_and_usn_neg_one(self, fake_anki_db_slovene_pairs, tmp_path):
        pre_mod = _row(fake_anki_db_slovene_pairs, "SELECT mod FROM col")[0]
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=True,
        )
        post = _row(fake_anki_db_slovene_pairs, "SELECT mod, usn FROM col")
        assert post[0] > pre_mod
        assert post[1] == -1

    def test_rerun_is_noop(self, fake_anki_db_slovene_pairs, tmp_path):
        """Once merged, a second run must not add a second notetype, must not
        mutate notes, and must report 0 planned operations."""
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak1",
            dry_run=False,
            yes=True,
        )
        first_run_notetype_rows = _rows(
            fake_anki_db_slovene_pairs,
            "SELECT id, name FROM notetypes ORDER BY id",
        )
        first_run_notes = _rows(fake_anki_db_slovene_pairs, "SELECT id, flds FROM notes ORDER BY id")

        result = merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak2",
            dry_run=False,
            yes=True,
        )
        assert result["notes_migrated"] == 0
        assert result["cards_reparented"] == 0
        assert result["notes_deleted"] == 0
        # Notetype list unchanged (no second 'Slovene Vocabulary')
        assert (
            _rows(fake_anki_db_slovene_pairs, "SELECT id, name FROM notetypes ORDER BY id") == first_run_notetype_rows
        )
        # Notes text unchanged
        assert _rows(fake_anki_db_slovene_pairs, "SELECT id, flds FROM notes ORDER BY id") == first_run_notes

    def test_audit_raises_on_unexpected_write(self, fake_anki_db_slovene_pairs, tmp_path, monkeypatch):
        """If we sneak an extra UPDATE past the plan, post-run audit must fail."""
        from app.anki import merge_dupes as mod

        real_apply = mod.apply_merge

        def sneaky_apply(conn, plan, now_ts):
            real_apply(conn, plan, now_ts)
            # Mutate an unrelated note (unknown-direction one) past the plan
            conn.execute("UPDATE notes SET tags='rogue' WHERE id=?", (2013,))
            conn.commit()

        monkeypatch.setattr(mod, "apply_merge", sneaky_apply)
        with pytest.raises(RuntimeError, match="(?i)audit|unplanned|unexpected|rogue|2013"):
            merge_dupes(
                deck_name="0. Slovene",
                anki_collection_path=fake_anki_db_slovene_pairs,
                anki_backup_dir=tmp_path / "bak",
                dry_run=False,
                yes=True,
            )

    def test_backfill_finishes_cleanly_after_merge(self, fake_anki_db_slovene_pairs, tmp_path):
        """Stage 2b's backfill_guids --force should report 0 conflicts/duplicates after merge."""
        from app.anki.backfill_guids import backfill_guids

        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak_merge",
            dry_run=False,
            yes=True,
        )
        summary = backfill_guids(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak_backfill",
            dry_run=False,
            force=True,
        )
        assert summary["skipped_conflicts"] == 0
        assert summary["skipped_duplicates"] == 0

    def test_keeper_guid_untouched(self, fake_anki_db_slovene_pairs, tmp_path):
        """Merge must leave note guids alone — backfill_guids is a separate step."""
        pre = _rows(fake_anki_db_slovene_pairs, "SELECT id, guid FROM notes ORDER BY id")
        pre_map = dict(pre)
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=True,
        )
        post = dict(_rows(fake_anki_db_slovene_pairs, "SELECT id, guid FROM notes ORDER BY id"))
        # Every surviving keeper note must still have its pre-run guid
        for nid, guid in post.items():
            assert guid == pre_map[nid], f"note {nid} guid was rewritten by merge"


def _minimal_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, "
        "usn INTEGER, tags TEXT, flds TEXT, sfld TEXT)"
    )
    conn.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, ord INTEGER, mod INTEGER, usn INTEGER)")
    conn.execute("CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER)")
    conn.execute(
        "CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
    )
    conn.execute("CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY(ntid, ord))")
    conn.execute(
        "CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, "
        "config BLOB, PRIMARY KEY(ntid, ord))"
    )
    conn.execute("CREATE TABLE col (id INTEGER PRIMARY KEY, mod INTEGER, usn INTEGER)")
    conn.execute("INSERT INTO col (id, mod, usn) VALUES (1, 0, 0)")


class TestApplyMergeDirect:
    """Unit-level ``apply_merge`` exercises for branches not reachable via the happy-path fixture."""

    def _setup(self, tmp_path, *, existing_notetype: bool) -> sqlite3.Connection:
        db = tmp_path / "c.anki2"
        conn = sqlite3.connect(str(db))
        _minimal_schema(conn)
        if existing_notetype:
            from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME, build_notetype_config

            conn.execute(
                "INSERT INTO notetypes VALUES (?, ?, 0, -1, ?)",
                (999, SLOVENE_VOCAB_NOTETYPE_NAME, build_notetype_config(css=".card {}")),
            )
        conn.commit()
        return conn

    def test_empty_plan_with_existing_notetype_is_noop(self, tmp_path):
        conn = self._setup(tmp_path, existing_notetype=True)
        empty_plan = MergePlan(new_notetype_mid=123)
        apply_merge(conn, empty_plan, now_ts=1)
        # mid should be the existing one (999), not the seeded 123
        assert empty_plan.new_notetype_mid == 999
        # col.mod must NOT be bumped (early return before the UPDATE)
        assert conn.execute("SELECT mod FROM col WHERE id=1").fetchone()[0] == 0

    def test_only_notes_to_update_still_applies(self, tmp_path):
        conn = self._setup(tmp_path, existing_notetype=True)
        conn.execute("INSERT INTO notes VALUES (1, 'g1', 5, 0, 0, '', 'old', 'old')")
        conn.commit()
        plan = MergePlan(
            new_notetype_mid=999,
            notes_to_update={1: UnifiedFields("sl", "en", "", "", "", "")},
        )
        apply_merge(conn, plan, now_ts=42)
        row = conn.execute("SELECT mid, sfld FROM notes WHERE id=1").fetchone()
        assert row == (999, "sl")
        assert conn.execute("SELECT mod FROM col WHERE id=1").fetchone()[0] == 42

    def test_only_cards_to_reparent_still_applies(self, tmp_path):
        conn = self._setup(tmp_path, existing_notetype=True)
        conn.execute("INSERT INTO cards VALUES (10, 1, 0, 0, 0)")
        conn.commit()
        plan = MergePlan(new_notetype_mid=999, cards_to_reparent={10: (2, 1)})
        apply_merge(conn, plan, now_ts=77)
        assert conn.execute("SELECT nid, ord FROM cards WHERE id=10").fetchone() == (2, 1)

    def test_only_notes_to_delete_still_applies(self, tmp_path):
        conn = self._setup(tmp_path, existing_notetype=True)
        conn.execute("INSERT INTO notes VALUES (1, 'g1', 5, 0, 0, '', 'x', 'x')")
        conn.commit()
        plan = MergePlan(new_notetype_mid=999, notes_to_delete=[1])
        apply_merge(conn, plan, now_ts=99)
        assert conn.execute("SELECT COUNT(*) FROM notes WHERE id=1").fetchone()[0] == 0

    def test_rolls_back_on_exception(self, tmp_path):
        conn = self._setup(tmp_path, existing_notetype=True)
        conn.execute("INSERT INTO notes VALUES (1, 'g1', 5, 0, 0, '', 'old', 'old')")
        conn.commit()

        class _BoomConn:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args, **kwargs):
                return self._real.execute(sql, *args, **kwargs)

            def executemany(self, sql, *args, **kwargs):
                if "UPDATE notes" in sql:
                    raise sqlite3.OperationalError("simulated failure")
                return self._real.executemany(sql, *args, **kwargs)

        plan = MergePlan(
            new_notetype_mid=999,
            notes_to_update={1: UnifiedFields("x", "y", "", "", "", "")},
        )
        with pytest.raises(sqlite3.OperationalError, match="simulated failure"):
            apply_merge(_BoomConn(conn), plan, now_ts=1)
        # Rollback must have fired — note sfld is still the pre-merge "old".
        assert conn.execute("SELECT sfld FROM notes WHERE id=1").fetchone()[0] == "old"


class TestAuditMerge:
    """Direct tests for ``_audit_merge`` failure modes."""

    def _pair_of_dbs(self, tmp_path):
        """Return two sqlite connections (backup, current) wired with a minimal schema and one template."""
        bak_path = tmp_path / "bak.db"
        cur_path = tmp_path / "cur.db"
        bak = sqlite3.connect(str(bak_path))
        cur = sqlite3.connect(str(cur_path))
        for c in (bak, cur):
            _minimal_schema(c)
            c.execute("INSERT INTO templates VALUES (1, 0, 'R', 0, -1, X'')")
            c.commit()
        return bak, bak_path, cur, cur_path

    def test_raises_on_cards_count_change(self, tmp_path):
        bak, bak_path, cur, _ = self._pair_of_dbs(tmp_path)
        bak.execute("INSERT INTO cards VALUES (1, 1, 0, 0, 0)")
        bak.commit()
        bak.close()
        # current has zero cards — count mismatch
        cur.execute("INSERT INTO notes VALUES (1, 'g1', 1, 0, 0, '', '', '')")
        cur.commit()
        with pytest.raises(RuntimeError, match="cards count changed"):
            _audit_merge(cur, bak_path, MergePlan(new_notetype_mid=1))
        cur.close()

    def test_raises_on_revlog_count_change(self, tmp_path):
        bak, bak_path, cur, _ = self._pair_of_dbs(tmp_path)
        bak.execute("INSERT INTO notes VALUES (1, 'g1', 1, 0, 0, '', '', '')")
        bak.execute("INSERT INTO cards VALUES (1, 1, 0, 0, 0)")
        bak.commit()
        bak.close()
        cur.execute("INSERT INTO notes VALUES (1, 'g1', 1, 0, 0, '', '', '')")
        cur.execute("INSERT INTO cards VALUES (1, 1, 0, 0, 0)")
        cur.execute("INSERT INTO revlog VALUES (42, 1)")
        cur.commit()
        with pytest.raises(RuntimeError, match="revlog count changed"):
            _audit_merge(cur, bak_path, MergePlan(new_notetype_mid=1))
        cur.close()

    def test_raises_on_unplanned_notes_drop(self, tmp_path):
        bak, bak_path, cur, _ = self._pair_of_dbs(tmp_path)
        bak.execute("INSERT INTO notes VALUES (1, 'g1', 1, 0, 0, '', '', '')")
        bak.execute("INSERT INTO notes VALUES (2, 'g2', 1, 0, 0, '', '', '')")
        bak.commit()
        bak.close()
        # current has only note 1 — one note dropped, but the plan says delete zero
        cur.execute("INSERT INTO notes VALUES (1, 'g1', 1, 0, 0, '', '', '')")
        cur.execute("INSERT INTO cards VALUES (1, 1, 0, 0, 0)")
        # match the backup card count
        bak2 = sqlite3.connect(str(bak_path))
        bak2.execute("INSERT INTO cards VALUES (1, 1, 0, 0, 0)")
        bak2.commit()
        bak2.close()
        cur.commit()
        with pytest.raises(RuntimeError, match="notes count drop"):
            _audit_merge(cur, bak_path, MergePlan(new_notetype_mid=1))
        cur.close()

    def test_raises_on_orphan_card(self, tmp_path):
        bak, bak_path, cur, _ = self._pair_of_dbs(tmp_path)
        bak.execute("INSERT INTO notes VALUES (1, 'g1', 1, 0, 0, '', '', '')")
        bak.execute("INSERT INTO cards VALUES (1, 1, 0, 0, 0)")
        bak.commit()
        bak.close()
        # current has the card but no note
        cur.execute("INSERT INTO cards VALUES (1, 1, 0, 0, 0)")
        cur.commit()
        # plan says one note should have been deleted (so the drop check passes)
        plan = MergePlan(new_notetype_mid=1, notes_to_delete=[1])
        with pytest.raises(RuntimeError, match="orphan cards"):
            _audit_merge(cur, bak_path, plan)
        cur.close()

    def test_raises_on_dangling_card_ord(self, tmp_path):
        bak, bak_path, cur, _ = self._pair_of_dbs(tmp_path)
        bak.execute("INSERT INTO notes VALUES (1, 'g1', 1, 0, 0, '', '', '')")
        bak.execute("INSERT INTO cards VALUES (1, 1, 7, 0, 0)")  # ord=7 has no template
        bak.commit()
        bak.close()
        cur.execute("INSERT INTO notes VALUES (1, 'g1', 1, 0, 0, '', '', '')")
        cur.execute("INSERT INTO cards VALUES (1, 1, 7, 0, 0)")  # still ord=7
        cur.commit()
        with pytest.raises(RuntimeError, match="not matching a template"):
            _audit_merge(cur, bak_path, MergePlan(new_notetype_mid=1))
        cur.close()

    def test_load_card_maps_empty_input_returns_empty_dicts(self, tmp_path):
        """Covers the early-return when re-running the CLI on a collection where
        every non-new-notetype note has already been handled."""
        conn = sqlite3.connect(str(tmp_path / "c.db"))
        try:
            ord_map, id_map = _load_card_maps(conn, deck_id=1, note_ids=[])
            assert ord_map == {}
            assert id_map == {}
        finally:
            conn.close()

    def test_raises_on_guid_rewrite(self, tmp_path):
        bak, bak_path, cur, _ = self._pair_of_dbs(tmp_path)
        bak.execute("INSERT INTO notes VALUES (1, 'g_original', 1, 0, 0, '', '', '')")
        bak.execute("INSERT INTO cards VALUES (1, 1, 0, 0, 0)")
        bak.commit()
        bak.close()
        cur.execute("INSERT INTO notes VALUES (1, 'g_rewritten', 1, 0, 0, '', '', '')")
        cur.execute("INSERT INTO cards VALUES (1, 1, 0, 0, 0)")
        cur.commit()
        with pytest.raises(RuntimeError, match="unplanned guid rewrites"):
            _audit_merge(cur, bak_path, MergePlan(new_notetype_mid=1))
        cur.close()
