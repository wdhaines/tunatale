"""Tests for missing coverage branches."""

from __future__ import annotations

from typing import Any

from app.anki.sync import NoteRecord
from app.models.srs_item import Direction
from app.srs.database import SRSDatabase


def _make_db_with_banka() -> tuple[SRSDatabase, str]:
    """Create in-memory DB with 'banka' collocation linked to Anki note 9001."""
    db = SRSDatabase(":memory:")
    from app.models.syntactic_unit import SyntacticUnit

    unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation("banka")
    assert item is not None
    guid = item.guid
    db.set_anki_ids(guid, 9001, {Direction.RECOGNITION: 90010, Direction.PRODUCTION: 90011})
    return db, guid


class _FakeReader:
    """Controlled reader returning preset NoteRecords."""

    def __init__(self, records: list[NoteRecord]) -> None:
        self._records = records

    def get_note_records(self) -> list[NoteRecord]:
        return self._records

    def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
        return []


class _FakeWriter:
    """Captures writer calls for assertions."""

    def __init__(self) -> None:
        self.set_due_date_calls: list[tuple[list[int], str]] = []
        self.write_revlog_calls: list[dict[str, Any]] = []
        self.suspend_calls: list[list[int]] = []
        self.unsuspend_calls: list[list[int]] = []

    def update_note_fields(self, note_id: int, fields: dict) -> None:
        pass

    def suspend(self, card_ids: list[int]) -> None:
        self.suspend_calls.append(list(card_ids))

    def unsuspend(self, card_ids: list[int]) -> None:
        self.unsuspend_calls.append(list(card_ids))

    def set_due_date(self, card_ids: list[int], days: str) -> None:
        self.set_due_date_calls.append((list(card_ids), days))

    def write_revlog(
        self,
        *,
        cid: int,
        ease: int,
        ivl: int,
        last_ivl: int,
        factor: int,
        time_ms: int,
        type_,
        preferred_id=None,
        is_lapse: bool = False,
        ds_reps: int | None = None,
        ds_lapses: int | None = None,
    ) -> None:
        self.write_revlog_calls.append(
            {"cid": cid, "ease": ease, "ivl": ivl, "time_ms": time_ms, "type_": type_, "preferred_id": preferred_id}
        )

    def set_specific_value_of_card(self, card_id: int, keys: list[str], new_values: list[str]) -> None:
        pass


class TestConflictRecordDryRun:
    """Tests for dry_run=True in conflict recording (614->687)."""
