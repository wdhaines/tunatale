"""Shared helpers for anki-sync push tests."""

from __future__ import annotations


class FakeReader:
    def get_note_records(self):
        return []

    def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
        return []


class FakeWriter:
    """Records all writer calls for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        # Tests populate this to simulate Anki's current state for guard checks.
        # Maps card_id → {"queue": int, "type": int, "left": int}. Returning None
        # for an absent key tells push "no current state; proceed normally."
        self.current_states: dict[int, dict] = {}

    def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        self.calls.append(("update_note_fields", note_id, fields))

    def suspend(self, card_ids: list[int]) -> None:
        self.calls.append(("suspend", list(card_ids)))

    def unsuspend(self, card_ids: list[int]) -> None:
        self.calls.append(("unsuspend", list(card_ids)))

    def set_due_date(self, card_ids: list[int], days: str) -> None:
        self.calls.append(("set_due_date", list(card_ids), days))

    def forget_card(self, card_id: int) -> None:
        self.calls.append(("forget_card", card_id))

    def set_learning_state(self, card_id: int, left: int, due_at: int, *, type_: int = 1) -> None:
        self.calls.append(("set_learning_state", card_id, left, due_at, type_))

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
        reps_bump: int | None = None,
        lapses_bump: int | None = None,
    ) -> None:
        self.calls.append(("write_revlog", cid, ease, ivl, last_ivl, factor, time_ms, type_, preferred_id))

    def get_current_card_state(self, card_id: int) -> dict | None:
        return self.current_states.get(card_id)

    def update_card_memory_state(
        self,
        card_id: int,
        *,
        stability: float,
        difficulty: float,
        last_review_secs: int | None = None,
        desired_retention: float | None = None,
    ) -> None:
        self.calls.append(
            ("update_card_memory_state", card_id, stability, difficulty, last_review_secs, desired_retention)
        )

    def bury_siblings(
        self,
        *,
        graded_card_id: int,
        graded_queue: int,
        bury_new: bool = False,
        bury_reviews: bool = False,
        bury_interday_learning: bool = False,
    ) -> int:
        self.calls.append(
            ("bury_siblings", graded_card_id, graded_queue, bury_new, bury_reviews, bury_interday_learning)
        )
        return 0

    def list_decks_with_revlog_today(self, today_4am_ms: int) -> list[int]:
        return []

    def count_first_grades_today_for_deck(self, deck_id: int, today_4am_ms: int) -> int:
        return 0

    def count_reviews_today_for_deck(self, deck_id: int, today_4am_ms: int) -> int:
        return 0

    def max_revlog_id_for_card(self, card_id: int) -> int:
        return 0

    def set_deck_studied_today(self, deck_id: int, today_day_index: int, new_today: int, review_today: int) -> None:
        self.calls.append(("set_deck_studied_today", deck_id, today_day_index, new_today, review_today))

    def store_media_file(self, filename: str, data: bytes) -> None:
        self.calls.append(("store_media_file", filename, len(data)))

    def action_names(self) -> list[str]:
        return [c[0] for c in self.calls]
