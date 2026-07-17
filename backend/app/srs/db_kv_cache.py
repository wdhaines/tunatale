"""anki_state_cache key/value mixin for SRSDatabase.

Extracted verbatim from app/srs/database.py (god-module split, stage 4).
Plain KV storage; the parity semantics of individual keys live with their
consumers (queue_stats, sync).
"""


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
