"""Ignored-lemma blocklist mixin for SRSDatabase.

Extracted verbatim from app/srs/database.py (god-module split, stage 4).
Card-less lemma ignore list (function words the user opted out of).
"""


class DbIgnoredLemmasMixin:
    """ignored_lemmas accessors. Mixed into SRSDatabase."""

    def add_ignored_lemma(self, language_code: str, lemma: str) -> None:
        """Add a lemma to the card-less ignore list (idempotent)."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO ignored_lemmas (language_code, lemma) VALUES (?, ?)",
                (language_code, lemma.lower()),
            )
            self._commit(conn)

    def remove_ignored_lemma(self, language_code: str, lemma: str) -> None:
        """Remove a lemma from the card-less ignore list (idempotent)."""
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM ignored_lemmas WHERE language_code = ? AND lemma = ?",
                (language_code, lemma.lower()),
            )
            self._commit(conn)

    def get_ignored_lemmas(self, language_code: str) -> set[str]:
        """Return the set of ignored lemmas for a language."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT lemma FROM ignored_lemmas WHERE language_code = ?",
                (language_code,),
            ).fetchall()
            return {r["lemma"] for r in rows}
