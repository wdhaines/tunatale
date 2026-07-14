"""Media CRUD mixin for SRSDatabase.

Extracted verbatim from app/srs/database.py (god-module split, stage 4).
Read/write/dedupe accessors for the media table — no queue or FSRS logic.
"""

from typing import Any


class DbMediaMixin:
    """Media-table accessors. Mixed into SRSDatabase; relies on SRSDatabaseBase infra."""

    def get_image_filename(self, collocation_id: int) -> str | None:
        """Return the filename of the first image media row for a collocation, or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT filename FROM media WHERE collocation_id = ? AND kind = 'image' ORDER BY id DESC LIMIT 1",
                (collocation_id,),
            ).fetchone()
        return row["filename"] if row is not None else None

    def get_audio_filename(self, collocation_id: int) -> str | None:
        """Return the filename of the preferred audio media row (forvo > tts), or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT filename FROM media WHERE collocation_id = ? AND kind IN ('audio_forvo','audio_tts') "
                "ORDER BY CASE kind WHEN 'audio_forvo' THEN 0 ELSE 1 END LIMIT 1",
                (collocation_id,),
            ).fetchone()
        return row["filename"] if row is not None else None

    def get_sentence_audio_filename(self, collocation_id: int) -> str | None:
        """Return filename of the audio_tts_sentence media row, or None."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT filename FROM media WHERE collocation_id = ? AND kind = 'audio_tts_sentence' LIMIT 1",
                (collocation_id,),
            ).fetchone()
        return row["filename"] if row is not None else None

    def has_media_row(self, collocation_id: int, kind: str) -> bool:
        """Return True if at least one media row exists for (collocation_id, kind)."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM media WHERE collocation_id = ? AND kind = ? LIMIT 1",
                (collocation_id, kind),
            ).fetchone()
        return row is not None

    def add_media(
        self,
        collocation_id: int,
        kind: str,
        filename: str,
        path: str,
        anki_filename: str,
        sha256: str,
        size_bytes: int,
        *,
        mtime_ns: int | None = None,
    ) -> int:
        """Insert a media row. Returns the new media id."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO media (collocation_id, kind, filename, path, anki_filename, sha256, bytes, mtime_ns)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (collocation_id, kind, filename, path, anki_filename, sha256, size_bytes, mtime_ns),
            )
            self._commit(conn)
            return cursor.lastrowid

    def find_media_by_anki_filename(self, anki_filename: str, *, collocation_id: int) -> dict[str, Any] | None:
        """Return the media row for the given Anki filename on a specific collocation, or None.

        Scoped by ``collocation_id`` so that two collocations referencing the
        same filename (e.g. ``img_yes.jpg`` shared between ``ja`` and ``da``)
        don't cross-contaminate during sync.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM media WHERE anki_filename = ? AND collocation_id = ?",
                (anki_filename, collocation_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def delete_stale_media_for_kind(self, collocation_id: int, kind: str, keep_anki_filenames: set[str]) -> int:
        """Delete media rows on this collocation/kind whose anki_filename isn't in
        ``keep_anki_filenames``. Used by import_seed to collapse the row set down
        to what Anki currently references. Returns the number of rows deleted.

        Empty keep set is treated as a no-op — defense against accidentally
        nuking all rows when the caller's per-pass tracking failed to record
        anything. Use ``delete_all_media_for_kind`` for the intentional
        "kind vanished from the note" case.
        """
        if not keep_anki_filenames:
            return 0
        placeholders = ",".join("?" * len(keep_anki_filenames))
        with self._get_conn() as conn:
            cur = conn.execute(
                f"DELETE FROM media WHERE collocation_id = ? AND kind = ? AND anki_filename NOT IN ({placeholders})",
                (collocation_id, kind, *keep_anki_filenames),
            )
            self._commit(conn)
            return cur.rowcount

    def delete_all_media_for_kind(self, collocation_id: int, kind: str) -> int:
        """Delete every media row of ``kind`` on this collocation. Returns
        the number of rows deleted.

        Distinct from ``delete_stale_media_for_kind(..., set())`` (which is a
        defensive no-op): this method is the explicit collapse path used when
        a note no longer references any media of a given kind. The canonical
        case is a note whose image field switched from ``<img src="paste-…">``
        to ``<img src="data:…">`` (per RFC 2397 the latter has no file in
        ``collection.media/``); the prior file row must collapse so the UI
        stops serving the old picture.
        """
        with self._get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM media WHERE collocation_id = ? AND kind = ?",
                (collocation_id, kind),
            )
            self._commit(conn)
            return cur.rowcount

    def list_media_kinds_for_collocation(self, collocation_id: int) -> set[str]:
        """Return the set of distinct media kinds currently recorded on this
        collocation. Used by the refresh-media path to decide which kinds need
        a cleanup pass — including kinds that have vanished from the Anki note
        (otherwise their stale rows would persist forever).
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT kind FROM media WHERE collocation_id = ?",
                (collocation_id,),
            ).fetchall()
        return {r[0] for r in rows}

    def find_media_by_sha256(self, collocation_id: int, kind: str, sha256: str) -> dict[str, Any] | None:
        """Return the media row matching ``(collocation_id, kind, sha256)``, or None.

        Used by the refresh-media path to recognize inline (``data:`` URI)
        images on re-import: those have no Anki filename to dedupe against, so
        we identify them content-wise instead.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM media WHERE collocation_id = ? AND kind = ? AND sha256 = ?",
                (collocation_id, kind, sha256),
            ).fetchone()
        return dict(row) if row is not None else None

    def update_media_file(self, row_id: int, sha256: str, size_bytes: int, *, mtime_ns: int | None = None) -> None:
        """Update sha256, size_bytes, and optionally mtime_ns for an existing media row."""
        with self._get_conn() as conn:
            if mtime_ns is not None:
                conn.execute(
                    "UPDATE media SET sha256 = ?, bytes = ?, mtime_ns = ? WHERE id = ?",
                    (sha256, size_bytes, mtime_ns, row_id),
                )
            else:
                conn.execute(
                    "UPDATE media SET sha256 = ?, bytes = ? WHERE id = ?",
                    (sha256, size_bytes, row_id),
                )
            self._commit(conn)

    def list_media_by_collocation_and_filename(self) -> dict[tuple[int, str], dict[str, Any]]:
        """Return all media rows keyed by ``(collocation_id, anki_filename)``.

        Used by the media-refresh path to batch-load every media row in one
        query instead of per-file ``find_media_by_anki_filename`` calls.
        """
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM media WHERE anki_filename IS NOT NULL").fetchall()
        return {(row["collocation_id"], row["anki_filename"]): dict(row) for row in rows}

    def update_media_stat(self, row_id: int, *, mtime_ns: int, size_bytes: int) -> None:
        """Stamp mtime_ns and size_bytes on an existing media row without changing sha256.

        Used by the mtime-skip path when content hash matches but the stat
        metadata was stale or NULL (e.g. first post-migration warm-up).
        """
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE media SET mtime_ns = ?, bytes = ? WHERE id = ?",
                (mtime_ns, size_bytes, row_id),
            )
            self._commit(conn)

    def get_image_filenames(self, ids: list[int]) -> dict[int, str]:
        """Batched lookup: return ``{collocation_id: filename}`` for image media.

        Single query over the provided IDs; returns the most-recent image per
        collocation (highest ``id`` = ``ORDER BY id DESC``, first seen wins).
        Empty input returns ``{}``."""
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT collocation_id, filename FROM media "
                f"WHERE collocation_id IN ({placeholders}) AND kind = 'image' "
                f"ORDER BY id DESC",
                ids,
            ).fetchall()
        result: dict[int, str] = {}
        for row in rows:
            cid = row["collocation_id"]
            if cid not in result:
                result[cid] = row["filename"]
        return result

    def is_media_filename_referenced(self, filename: str) -> bool:
        """Return True if any media row (any collocation, any kind) references *filename*."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM media WHERE filename = ? LIMIT 1",
                (filename,),
            ).fetchone()
        return row is not None
