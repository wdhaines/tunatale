"""Shared helpers for anki-sync pull tests."""

from __future__ import annotations

from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.sync import NoteRecord
from app.srs.database import SRSDatabase


class FakeWriter:
    """Minimal writer stub for tests that only need AnkiSync construction."""

    def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        pass

    def suspend(self, card_ids: list[int]) -> None:
        pass

    def unsuspend(self, card_ids: list[int]) -> None:
        pass

    def set_due_date(self, card_ids: list[int], days: str) -> None:
        pass

    def max_revlog_id_for_card(self, card_id: int) -> int:
        return 0

    def write_revlog(
        self,
        *,
        cid: int,
        ease: int,
        ivl: int,
        last_ivl: int,
        factor: int,
        time_ms: int,
        type_: int,
        preferred_id=None,
        is_lapse: bool = False,
        ds_reps: int | None = None,
        ds_lapses: int | None = None,
        reps_bump: int | None = None,
        lapses_bump: int | None = None,
    ) -> None:
        pass


# ── Shared helpers ────────────────────────────────────────────────────────────


def _make_tt_db() -> SRSDatabase:
    return SRSDatabase(":memory:")


def _add_banka(db: SRSDatabase, extras=()) -> str:
    """Insert banka/bank; return its computed GUID."""
    unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus", extras=extras)
    db.add_collocation(unit)
    item = db.get_collocation("banka")
    assert item is not None
    return item.guid  # type: ignore[return-value]


def _add_cloze_collocation(db: SRSDatabase, text: str = "vsak", sentence: str = "Odprto je vsak dan") -> str:
    """Insert a cloze collocation; return its computed GUID."""
    unit = SyntacticUnit(
        text=text,
        translation="",
        word_count=1,
        difficulty=1,
        source="cloze",
        lemma=text,
        source_sentence=sentence,
        card_type="cloze",
    )
    db.add_collocation(unit)
    item = db.get_collocation(text)
    assert item is not None
    return item.guid  # type: ignore[return-value]


class FakeReader:
    def __init__(self, records: list[NoteRecord]):
        self._records = records

    def get_note_records(self) -> list[NoteRecord]:
        return self._records

    def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
        return []
