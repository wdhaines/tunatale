"""Not-wanted marks on lesson cards (tunatale-h0q3.66): the list cards the learner turned off.

A mark is a card key. ``mint_lesson_cards`` skips a marked key, so a list card
turned off is never minted again, whichever path mints (the listen, the Grade
page's study pass, the import of a day already studied). TT-only: never synced.
"""


class DbSentenceMarksMixin:
    """sentence_card_marks accessors. Mixed into SRSDatabase."""

    def add_sentence_card_mark(self, card_key: str) -> None:
        with self._get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO sentence_card_marks (card_key) VALUES (?)", (card_key,))
            self._commit(conn)

    def remove_sentence_card_mark(self, card_key: str) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM sentence_card_marks WHERE card_key = ?", (card_key,))
            self._commit(conn)

    def sentence_card_marks(self) -> set[str]:
        with self._get_conn() as conn:
            return {r["card_key"] for r in conn.execute("SELECT card_key FROM sentence_card_marks")}
