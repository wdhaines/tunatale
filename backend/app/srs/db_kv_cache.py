"""anki_state_cache key/value mixin for SRSDatabase.

Extracted verbatim from app/srs/database.py (god-module split, stage 4).
Plain KV storage; the parity semantics of individual keys live with their
consumers (queue_stats, sync).
"""

from typing import NamedTuple


class CachedClozeSentence(NamedTuple):
    """A cached LLM cloze sentence and the judge's verdict on it."""

    sentence: str
    status: str
    competitors: tuple[str, ...]


class DbKvCacheMixin:
    """anki_state_cache accessors. Mixed into SRSDatabase; relies on SRSDatabaseBase infra."""

    def set_anki_state_cache(self, key: str, value: str) -> None:
        """Upsert a key/value pair in the Anki state cache with the current UTC timestamp.

        Raises KeyError if the key is not registered in the cache_registry.
        """
        from datetime import UTC, datetime

        from app.srs.anki_mirror.cache_registry import REGISTRY

        if key not in REGISTRY:
            raise KeyError(f"unregistered cache key: {key!r}. Register it in cache_registry.py first.")

        updated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, updated_at),
            )
            self._commit(conn)

    def set_anki_state_cache_raw(self, key: str, value: str, updated_at: str) -> None:
        """Test helper: upsert a cache row with caller-specified updated_at.

        Production code uses set_anki_state_cache (stamps current UTC time).
        This variant is for tests that need to simulate stale or corrupt
        timestamps without reaching into the SQLite connection.

        Raises KeyError if the key is not registered in the cache_registry.
        """
        from app.srs.anki_mirror.cache_registry import REGISTRY

        if key not in REGISTRY:
            raise KeyError(f"unregistered cache key: {key!r}. Register it in cache_registry.py first.")

        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO anki_state_cache (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, updated_at),
            )
            self._commit(conn)

    def get_anki_state_cache(self, key: str) -> tuple[str, str] | None:
        """Return (value, updated_at) for the given key, or None if absent.

        Raises KeyError if the key is not registered in the cache_registry.
        """
        from app.srs.anki_mirror.cache_registry import REGISTRY

        if key not in REGISTRY:
            raise KeyError(f"unregistered cache key: {key!r}. Register it in cache_registry.py first.")

        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value, updated_at FROM anki_state_cache WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return (row["value"], row["updated_at"])

    def delete_anki_state_cache(self, key: str) -> None:
        """Remove the cache row for `key` (idempotent — no-op when absent).

        Raises KeyError if the key is not registered in the cache_registry.
        """
        from app.srs.anki_mirror.cache_registry import REGISTRY

        if key not in REGISTRY:
            raise KeyError(f"unregistered cache key: {key!r}. Register it in cache_registry.py first.")

        with self._get_conn() as conn:
            conn.execute("DELETE FROM anki_state_cache WHERE key = ?", (key,))
            self._commit(conn)

    def get_cached_cloze_sentence(
        self, word: str, language_code: str, *, model_version: str = ""
    ) -> CachedClozeSentence | None:
        """The LLM-written cloze sentence for *word*, or ``None``.

        Read by ``_fallback_to_cloze`` when the note's own examples carry no
        blankable form of the word — the "LLM tier" ``choose_cloze_sentence``'s
        docstring names but never had. Written off the critical path by
        ``prestage_cloze_sentences`` (tunatale-keb0).
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT sentence, status, competitors FROM cloze_sentence_cache "
                "WHERE word = ? AND language_code = ? AND model_version = ?",
                (word, language_code, model_version),
            ).fetchone()
        if row is None:
            return None
        return CachedClozeSentence(
            sentence=row["sentence"],
            status=row["status"],
            competitors=tuple(c for c in (row["competitors"] or "").split("\x1f") if c),
        )

    def set_cached_cloze_sentence(
        self,
        word: str,
        language_code: str,
        *,
        sentence: str,
        status: str,
        competitors: tuple[str, ...] = (),
        model_version: str = "",
    ) -> None:
        """Cache one generated cloze sentence and the judge's verdict on it.

        The verdict rides along so a reader can see WHY a sentence was kept
        without paying for the judgement again — and so the UI can offer "try
        again" on the ones that stayed underdetermined.

        Competitors are US-joined rather than comma-joined: a filler is a single
        word here, but the separator must not be one a word can contain, and
        comma is exactly what a model returns its list in.
        """
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cloze_sentence_cache "
                "(word, language_code, model_version, sentence, status, competitors, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                (word, language_code, model_version, sentence, status, "\x1f".join(competitors)),
            )
            self._commit(conn)
