"""Tests for backfill_due_date_from_anki_due."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from app.anki.backfill_due_date_from_anki_due import repair_due_dates
from app.anki.sqlite_reader import compute_due_date
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from tests.conftest import build_minimal_anki_db

_COL_CRT = 1704067200  # 2024-01-01 UTC — matches build_minimal_anki_db default.
# Compute the same way the production code does, so the test isn't tz-flaky.
_REC_CARDS_DUE = 10  # build_minimal_anki_db default for ord=0
_EXPECTED_DUE = compute_due_date(2, _REC_CARDS_DUE, _COL_CRT)


def _seed_tt(tt_path: Path, *, stale_due: date, anki_card_id: int) -> str:
    """Add a single review-state row whose stored due_date is stale vs anki_due."""
    db = SRSDatabase(str(tt_path))
    unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="anki", lemma="banka")
    db.upsert_by_guid(
        unit,
        "sl",
        {
            Direction.RECOGNITION: DirectionState(
                direction=Direction.RECOGNITION,
                due_date=stale_due,
                stability=10.0,
                difficulty=5.0,
                reps=3,
                state=SRSState.REVIEW,
                anki_card_id=anki_card_id,
                anki_due=10,  # ← matches build_minimal_anki_db default for ord=0
            )
        },
        anki_note_id=1001,
    )
    item = db.get_collocation("banka")
    return item.guid


class TestRepairDueDates:
    def test_rewrites_stale_due_date_to_match_anki_due(self, tmp_path: Path):
        """Live anki cards.due for note 1001 ord=0 is 10 → due_date should be
        2024-01-01 + 10 days = 2024-01-11. A TT row pinned at 2026-05-17 must
        be repaired to 2024-01-11."""
        anki_path = build_minimal_anki_db(tmp_path)
        tt_path = tmp_path / "tt.db"
        _seed_tt(tt_path, stale_due=date(2026, 5, 17), anki_card_id=10010)

        summary = repair_due_dates(tt_path, anki_path)

        assert summary["mismatched"] == 1
        assert summary["written"] == 1

        # Verify the actual write.
        conn = sqlite3.connect(str(tt_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT due_date FROM collocation_directions WHERE anki_card_id = ?",
            (10010,),
        ).fetchone()
        conn.close()
        assert row["due_date"] == _EXPECTED_DUE.isoformat()

    def test_dry_run_reports_but_does_not_write(self, tmp_path: Path):
        anki_path = build_minimal_anki_db(tmp_path)
        tt_path = tmp_path / "tt.db"
        _seed_tt(tt_path, stale_due=date(2026, 5, 17), anki_card_id=10010)

        summary = repair_due_dates(tt_path, anki_path, dry_run=True)

        assert summary["mismatched"] == 1
        assert summary["written"] == 0

        conn = sqlite3.connect(str(tt_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT due_date FROM collocation_directions WHERE anki_card_id = ?",
            (10010,),
        ).fetchone()
        conn.close()
        assert row["due_date"] == "2026-05-17"  # untouched

    def test_already_consistent_rows_are_no_op(self, tmp_path: Path):
        anki_path = build_minimal_anki_db(tmp_path)
        tt_path = tmp_path / "tt.db"
        _seed_tt(tt_path, stale_due=_EXPECTED_DUE, anki_card_id=10010)

        summary = repair_due_dates(tt_path, anki_path)

        assert summary["checked"] >= 1
        assert summary["mismatched"] == 0
        assert summary["written"] == 0

    def test_main_cli_dry_run(self, tmp_path: Path, monkeypatch, capsys):
        """End-to-end CLI smoke: argparse + settings wiring + summary print."""
        from app.anki import backfill_due_date_from_anki_due as mod

        anki_path = build_minimal_anki_db(tmp_path)
        tt_path = tmp_path / "tt.db"
        _seed_tt(tt_path, stale_due=date(2026, 5, 17), anki_card_id=10010)

        monkeypatch.setattr(mod.settings, "database_url", f"sqlite:///{tt_path}")
        monkeypatch.setattr(mod.settings, "anki_collection_path", anki_path)

        rc = mod.main(["--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        assert "mismatched=1" in out
        assert "written=0" in out

    def test_main_cli_apply(self, tmp_path: Path, monkeypatch, capsys):
        from app.anki import backfill_due_date_from_anki_due as mod

        anki_path = build_minimal_anki_db(tmp_path)
        tt_path = tmp_path / "tt.db"
        _seed_tt(tt_path, stale_due=date(2026, 5, 17), anki_card_id=10010)

        monkeypatch.setattr(mod.settings, "database_url", f"sqlite:///{tt_path}")
        monkeypatch.setattr(mod.settings, "anki_collection_path", anki_path)

        rc = mod.main([])
        assert rc == 0
        out = capsys.readouterr().out
        assert "APPLIED" in out
        assert "written=1" in out

    def test_skips_rows_whose_anki_card_no_longer_exists(self, tmp_path: Path):
        """A TT row pointing at a deleted Anki card is silently skipped — orphan
        reset is a different code path's job (reset_orphaned_anki_ids)."""
        anki_path = build_minimal_anki_db(tmp_path)
        tt_path = tmp_path / "tt.db"
        _seed_tt(tt_path, stale_due=date(2026, 5, 17), anki_card_id=9_999_999)

        summary = repair_due_dates(tt_path, anki_path)
        # The row is "checked" (matched the SELECT filter) but no Anki match → skip.
        assert summary["checked"] == 1
        assert summary["mismatched"] == 0
        assert summary["written"] == 0

    def test_skips_rows_without_anki_card_id(self, tmp_path: Path):
        anki_path = build_minimal_anki_db(tmp_path)
        tt_path = tmp_path / "tt.db"
        db = SRSDatabase(str(tt_path))
        unit = SyntacticUnit(
            text="orphan", translation="orph", word_count=1, difficulty=1, source="corpus", lemma="orphan"
        )
        db.upsert_by_guid(
            unit,
            "sl",
            {
                Direction.RECOGNITION: DirectionState(
                    direction=Direction.RECOGNITION,
                    due_date=date(2026, 5, 17),
                    state=SRSState.REVIEW,
                    reps=1,
                    anki_card_id=None,  # orphan — can't look up in Anki
                )
            },
        )

        summary = repair_due_dates(tt_path, anki_path)
        # Row excluded by query filter (anki_card_id IS NOT NULL).
        assert summary["mismatched"] == 0
