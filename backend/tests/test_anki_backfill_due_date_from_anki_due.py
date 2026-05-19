"""Tests for backfill_due_date_from_anki_due (migrated to due_at)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, time
from pathlib import Path

from app.anki.backfill_due_date_from_anki_due import repair_due_dates
from app.anki.sqlite_reader import compute_due_at
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from tests.conftest import build_minimal_anki_db

_COL_CRT = 1704067200  # 2024-01-01 UTC — matches build_minimal_anki_db default.
_REC_CARDS_DUE = 10  # build_minimal_anki_db default for ord=0
_EXPECTED_DUE = compute_due_at(2, _REC_CARDS_DUE, _COL_CRT)


def _seed_tt(tt_path: Path, *, stale_due: date, anki_card_id: int) -> str:
    db = SRSDatabase(str(tt_path))
    unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="anki", lemma="banka")
    db.upsert_by_guid(
        unit,
        "sl",
        {
            Direction.RECOGNITION: DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(stale_due, time(4, 0), tzinfo=UTC),
                stability=10.0,
                difficulty=5.0,
                reps=3,
                state=SRSState.REVIEW,
                anki_card_id=anki_card_id,
                anki_due=10,
            )
        },
        anki_note_id=1001,
    )
    item = db.get_collocation("banka")
    return item.guid


class TestRepairDueDates:
    def test_rewrites_stale_due_at_to_match_anki_due(self, tmp_path: Path):
        anki_path = build_minimal_anki_db(tmp_path)
        tt_path = tmp_path / "tt.db"
        _seed_tt(tt_path, stale_due=date(2026, 5, 17), anki_card_id=10010)

        summary = repair_due_dates(tt_path, anki_path)

        assert summary["mismatched"] == 1
        assert summary["written"] == 1

        conn = sqlite3.connect(str(tt_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT due_at FROM collocation_directions WHERE anki_card_id = ?",
            (10010,),
        ).fetchone()
        conn.close()
        assert row["due_at"] == _EXPECTED_DUE.isoformat()

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
            "SELECT due_at FROM collocation_directions WHERE anki_card_id = ?",
            (10010,),
        ).fetchone()
        conn.close()
        expected_stale = datetime.combine(date(2026, 5, 17), time(4, 0), tzinfo=UTC).isoformat()
        assert row["due_at"] == expected_stale

    def test_already_consistent_rows_are_no_op(self, tmp_path: Path):
        anki_path = build_minimal_anki_db(tmp_path)
        tt_path = tmp_path / "tt.db"
        _seed_tt(tt_path, stale_due=_EXPECTED_DUE.date(), anki_card_id=10010)

        summary = repair_due_dates(tt_path, anki_path)

        assert summary["checked"] >= 1
        assert summary["mismatched"] == 0
        assert summary["written"] == 0

    def test_main_cli_dry_run(self, tmp_path: Path, monkeypatch, capsys):
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
        anki_path = build_minimal_anki_db(tmp_path)
        tt_path = tmp_path / "tt.db"
        _seed_tt(tt_path, stale_due=date(2026, 5, 17), anki_card_id=9_999_999)

        summary = repair_due_dates(tt_path, anki_path)
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
                    due_at=datetime(2026, 5, 17, 4, 0, tzinfo=UTC),
                    state=SRSState.REVIEW,
                    reps=1,
                    anki_card_id=None,
                )
            },
        )

        summary = repair_due_dates(tt_path, anki_path)
        assert summary["mismatched"] == 0
