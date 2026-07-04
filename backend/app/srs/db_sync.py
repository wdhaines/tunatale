"""Sync-coordination mixin for SRSDatabase.

Extracted verbatim from app/srs/database.py (god-module split, stage 5).
Dirty-FSRS / dirty-field tracking, Anki ID mapping, orphan/grave
handling — the TT side of the sync seam. Anki-parity danger zone — see
.claude/rules/anki-queue-parity.md and .claude/rules/anki-sync.md
before changing anything here.
"""

from datetime import UTC, datetime

from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState
from app.srs.db_base import _DIR_COLUMNS, _parse_last_review


class DbSyncMixin:
    """Dirty tracking + Anki ID mapping. Mixed into SRSDatabase; relies on SRSDatabaseBase infra."""

    # ── Read operations ────────────────────────────────────────────────

    def list_linked_anki_note_ids(self) -> dict[int, int]:
        """Return {anki_note_id: collocation_id} for all linked notes.

        Used by sync_create_new to determine which Anki notes already have
        a TT row (reverse-import skips already-linked notes).
        """
        with self._get_conn() as conn:
            rows = conn.execute("SELECT id, anki_note_id FROM collocations WHERE anki_note_id IS NOT NULL").fetchall()
            return {row["anki_note_id"]: row["id"] for row in rows}

    def set_article(self, coll_id: int, article: str) -> None:
        """Set the gender/indefinite article (en/ei/et) on a collocation.

        Display-only: the headword ``text`` is untouched, so the GUID is stable.
        TT-local; no USN, no sync, no Anki write. Used by the article backfill and
        the Anki→TT reverse-import to keep existing rows current.
        """
        with self._get_conn() as conn:
            conn.execute("UPDATE collocations SET article = ? WHERE id = ?", (article, coll_id))

    def get_ambiguous_surfaces(self, language_code: str) -> set[str]:
        """Return casefolded surfaces sharing >=2 distinct parts of speech.

        A surface is "ambiguous" when two vocab collocations spell it the same
        but carry different ``disambig_key`` POS (e.g. "fange" noun vs verb) —
        the cards where the POS earns its keep as a disambiguator. Blank POS and
        ``morph:`` cloze keys don't count; grouping is by Python ``casefold`` so
        Norwegian æ/ø/å fold correctly (SQLite ``LOWER`` is ASCII-only).
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT text, disambig_key FROM collocations
                WHERE language_code = ? AND card_type = 'vocab'
                  AND disambig_key != '' AND disambig_key NOT LIKE 'morph:%'
                """,
                (language_code,),
            ).fetchall()
        by_surface: dict[str, set[str]] = {}
        for row in rows:
            by_surface.setdefault(row["text"].casefold(), set()).add(row["disambig_key"])
        return {surface for surface, pos_set in by_surface.items() if len(pos_set) >= 2}

    def set_anki_ids(
        self,
        guid: str,
        note_id: int,
        card_ids: dict[Direction, int],
    ) -> None:
        """Set anki_note_id on the parent and anki_card_id on each direction row."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                return
            coll_id = row["id"]
            conn.execute(
                "UPDATE collocations SET anki_note_id = ? WHERE id = ?",
                (note_id, coll_id),
            )
            for direction, card_id in card_ids.items():
                conn.execute(
                    "UPDATE collocation_directions SET anki_card_id = ? WHERE collocation_id = ? AND direction = ?",
                    (card_id, coll_id, direction.value),
                )
            self._commit(conn)

    def list_anki_card_ids(self) -> set[int]:
        """Return all anki_card_ids currently linked on directions.

        Used by sync to diff against the Anki collection's live cards and detect
        orphans (TT rows pointing at cards that no longer exist in Anki).
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT anki_card_id FROM collocation_directions WHERE anki_card_id IS NOT NULL"
            ).fetchall()
            return {row["anki_card_id"] for row in rows}

    def delete_collocations_for_graves(self, *, grave_note_ids: set[int]) -> list[str]:
        """Hard-delete collocations whose Anki note is in the graves table.

        A note grave means the user deleted the note in Anki on purpose; honor
        it by removing the TT collocation (FK cascade also drops its directions
        and media) rather than resurrecting it on the next push. Counterpart to
        the recovery path in ``reset_orphaned_anki_ids`` — the caller routes a
        missing note here only when it carries a grave. Returns deleted guids.
        """
        if not grave_note_ids:
            return []
        placeholders = ",".join("?" * len(grave_note_ids))
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT id, guid, text FROM collocations WHERE anki_note_id IN ({placeholders})",
                list(grave_note_ids),
            ).fetchall()
            if not rows:
                return []
            row_ids = [r["id"] for r in rows]
            texts = [r["text"] for r in rows]
            text_ph = ",".join("?" * len(texts))
            conn.execute(f"DELETE FROM violations WHERE collocation_text IN ({text_ph})", texts)
            id_ph = ",".join("?" * len(row_ids))
            conn.execute(f"DELETE FROM collocations WHERE id IN ({id_ph})", row_ids)
            self._commit(conn)
            return [r["guid"] for r in rows]

    def reset_orphaned_anki_ids(
        self,
        *,
        live_card_ids: set[int],
        live_note_ids: set[int],
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Clear anki_card_id / anki_note_id on rows whose Anki target is gone.

        Behaviour:
        - For each direction with `anki_card_id NOT IN live_card_ids`: set
          `anki_card_id`, `anki_card_mod`, `anki_due`, `last_synced_at` to NULL.
          If the row has `reps > 0`, also set `dirty_fsrs = 1` so the next
          `sync_push` rewrites the FSRS state (force_fsrs) and a revlog entry
          onto the freshly-created Anki card.
        - For each collocation with `anki_note_id NOT IN live_note_ids`: clear
          `anki_note_id` and `last_synced_at`. The next `sync_create_new` then
          creates a fresh Anki note.

        Returns (direction_resets, note_resets) where direction_resets is a list
        of (guid, direction_str) and note_resets is a list of guid.
        """
        direction_resets: list[tuple[str, str]] = []
        note_resets: list[str] = []
        with self._get_conn() as conn:
            dir_rows = conn.execute(
                """
                SELECT c.guid, d.direction, d.anki_card_id, d.collocation_id, d.reps
                FROM collocation_directions d
                JOIN collocations c ON c.id = d.collocation_id
                WHERE d.anki_card_id IS NOT NULL
                """,
            ).fetchall()
            for row in dir_rows:
                if row["anki_card_id"] in live_card_ids:
                    continue
                make_dirty = row["reps"] > 0
                conn.execute(
                    """
                    UPDATE collocation_directions SET
                      anki_card_id = NULL,
                      anki_card_mod = NULL,
                      anki_due = NULL,
                      last_synced_at = NULL,
                      dirty_fsrs = CASE WHEN ? THEN 1 ELSE dirty_fsrs END
                    WHERE collocation_id = ? AND direction = ?
                    """,
                    (1 if make_dirty else 0, row["collocation_id"], row["direction"]),
                )
                direction_resets.append((row["guid"], row["direction"]))

            note_rows = conn.execute(
                "SELECT id, guid, anki_note_id FROM collocations WHERE anki_note_id IS NOT NULL"
            ).fetchall()
            for row in note_rows:
                if row["anki_note_id"] in live_note_ids:
                    continue
                conn.execute(
                    "UPDATE collocations SET anki_note_id = NULL, last_synced_at = NULL WHERE id = ?",
                    (row["id"],),
                )
                note_resets.append(row["guid"])

            self._commit(conn)
        return direction_resets, note_resets

    def list_dirty(
        self,
        direction: Direction | None = None,
    ) -> list[tuple[str, Direction, DirectionState]]:
        """Return (guid, direction, DirectionState) tuples for dirty FSRS rows.

        A row is dirty when dirty_fsrs=1 (set by schedule() after a review).
        Pass direction to restrict to one direction; omit for both.
        """
        with self._get_conn() as conn:
            if direction is None:
                rows = conn.execute(
                    f"""
                    SELECT c.guid, d.direction, {", ".join(f"d.{col}" for col in _DIR_COLUMNS)}
                    FROM collocations c
                    JOIN collocation_directions d ON d.collocation_id = c.id
                    WHERE d.dirty_fsrs = 1
                    """,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT c.guid, d.direction, {", ".join(f"d.{col}" for col in _DIR_COLUMNS)}
                    FROM collocations c
                    JOIN collocation_directions d ON d.collocation_id = c.id
                    WHERE d.dirty_fsrs = 1 AND d.direction = ?
                    """,
                    (direction.value,),
                ).fetchall()
            result = []
            for row in rows:
                d = Direction(row["direction"])
                due_at = datetime.fromisoformat(row["due_at"])
                prior_state_raw = row["prior_state"]
                ds = DirectionState(
                    direction=d,
                    due_at=due_at,
                    stability=row["stability"],
                    difficulty=row["fsrs_difficulty"],
                    reps=row["reps"],
                    lapses=row["lapses"],
                    state=SRSState(row["state"]),
                    last_review=_parse_last_review(row["last_review"]),
                    last_review_time_ms=row["last_review_time_ms"] or 0,
                    anki_card_id=row["anki_card_id"],
                    dirty_fsrs=bool(row["dirty_fsrs"]),
                    last_synced_at=row["last_synced_at"],
                    last_rating=row["last_rating"],
                    left=row["left"],
                    prior_state=SRSState(prior_state_raw) if prior_state_raw else None,
                    prior_left=row["prior_left"],
                    prior_stability=row["prior_stability"],
                    bury_kind=row["bury_kind"],
                    # Load-bearing for the push loop's row_force_fsrs decision —
                    # without it a restored direction never force-writes its
                    # stability to Anki (the same silent-False trap as bury_kind).
                    fsrs_force_next=bool(row["fsrs_force_next"]),
                )
                result.append((row["guid"], d, ds))
        return result

    def list_recently_graded_clean(
        self,
        direction: Direction | None = None,
    ) -> list[tuple[str, Direction, DirectionState]]:
        """Return (guid, direction, DirectionState) for clean rows that need revlog.

        These are directions where:
        - last_review > last_synced_at (a grade happened after last sync)
        - dirty_fsrs = 0 (already synced schedule)
        - last_rating IS NOT NULL (there is a grade to write)
        """
        with self._get_conn() as conn:
            if direction is None:
                rows = conn.execute(
                    f"""
                    SELECT c.guid, d.direction, {", ".join(f"d.{col}" for col in _DIR_COLUMNS)}
                    FROM collocations c
                    JOIN collocation_directions d ON d.collocation_id = c.id
                    WHERE d.dirty_fsrs = 0
                      AND d.last_rating IS NOT NULL
                    """,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT c.guid, d.direction, {", ".join(f"d.{col}" for col in _DIR_COLUMNS)}
                    FROM collocations c
                    JOIN collocation_directions d ON d.collocation_id = c.id
                    WHERE d.dirty_fsrs = 0
                      AND d.last_rating IS NOT NULL
                      AND d.direction = ?
                    """,
                    (direction.value,),
                ).fetchall()
            result = []
            for row in rows:
                d = Direction(row["direction"])
                # SQL already filters: dirty_fsrs = 0 AND last_rating IS NOT NULL
                # No need for last_review > last_synced_at check since last_rating
                # being non-NULL indicates a pending revlog write.
                last_review_dt = _parse_last_review(row["last_review"])
                last_synced_at = row["last_synced_at"]
                due_at = datetime.fromisoformat(row["due_at"])
                prior_state_raw = row["prior_state"]
                ds = DirectionState(
                    direction=d,
                    due_at=due_at,
                    stability=row["stability"],
                    difficulty=row["fsrs_difficulty"],
                    reps=row["reps"],
                    lapses=row["lapses"],
                    state=SRSState(row["state"]),
                    last_review=last_review_dt,
                    last_review_time_ms=row["last_review_time_ms"] or 0,
                    anki_card_id=row["anki_card_id"],
                    dirty_fsrs=bool(row["dirty_fsrs"]),
                    last_synced_at=last_synced_at,
                    last_rating=row["last_rating"],
                    left=row["left"],
                    prior_state=SRSState(prior_state_raw) if prior_state_raw else None,
                    prior_left=row["prior_left"],
                    prior_stability=row["prior_stability"],
                    bury_kind=row["bury_kind"],
                    # fsrs_force_next is load-bearing here: the push loop reads it
                    # off the list_recently_graded_clean DirectionState for the
                    # row_force_fsrs decision. Same silent-False trap as bury_kind.
                    fsrs_force_next=bool(row["fsrs_force_next"]),
                )
                result.append((row["guid"], d, ds))
        return result

    def mark_direction_clean(self, guid: str, direction: Direction) -> None:
        """Clear dirty_fsrs and set last_synced_at to now for one direction."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                return
            conn.execute(
                """
                UPDATE collocation_directions SET
                    dirty_fsrs = 0,
                    fsrs_force_next = 0,
                    last_rating = NULL,
                    last_synced_at = ?,
                    prior_state = NULL,
                    prior_left = NULL,
                    prior_stability = NULL
                WHERE collocation_id = ? AND direction = ?
                """,
                (datetime.now(UTC).isoformat(), row["id"], direction.value),
            )
            self._commit(conn)

    def set_dirty_fields(self, guid: str, fields_str: str) -> None:
        """Set dirty_fields for the collocation identified by guid."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE collocations SET dirty_fields = ? WHERE guid = ?",
                (fields_str, guid),
            )
            self._commit(conn)

    def add_dirty_field(self, guid: str, field: str) -> None:
        """Append *field* to the comma-separated dirty_fields set (no dupes)."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT dirty_fields FROM collocations WHERE guid = ?", (guid,)).fetchone()
            if row is None:
                return
            existing = {f for f in (row["dirty_fields"] or "").split(",") if f}
            existing.add(field)
            conn.execute(
                "UPDATE collocations SET dirty_fields = ? WHERE guid = ?",
                (",".join(sorted(existing)), guid),
            )
            self._commit(conn)

    def get_dirty_fields(self, guid: str) -> str:
        """Return dirty_fields for the collocation identified by guid, or ''."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT dirty_fields FROM collocations WHERE guid = ?",
                (guid,),
            ).fetchone()
        return (row["dirty_fields"] or "") if row else ""

    def set_sentence_translation_dirty(self, guid: str, sentence_translation: str) -> None:
        """Update sentence_translation for `guid` and append `sentence_translation` to dirty_fields.

        Called by the /listen backfill path and the one-shot backfill script.
        Marks dirty so sync_push will rewrite the cloze note's Back Extra field
        on the next push.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT dirty_fields FROM collocations WHERE guid = ?",
                (guid,),
            ).fetchone()
            if row is None:
                return
            existing = {f for f in (row["dirty_fields"] or "").split(",") if f}
            existing.add("sentence_translation")
            conn.execute(
                "UPDATE collocations SET sentence_translation = ?, dirty_fields = ?, "
                "updated_at = datetime('now') WHERE guid = ?",
                (sentence_translation, ",".join(sorted(existing)), guid),
            )
            self._commit(conn)

    def update_collocation_for_sync(
        self,
        guid: str,
        *,
        translation: str,
        note: str,
        sentence_translation: str = "",
        dirty_fields_str: str,
        article: str | None = None,
        extras: str | None = None,
    ) -> None:
        """Update translation, note, sentence_translation, and dirty_fields after a sync pull.

        ``article`` and ``extras`` are Anki-sourced display data (never edited in
        TT) — when provided each is set unconditionally (Anki wins). ``None``
        leaves that stored column untouched. ``extras`` is the serialized JSON
        string (see ``serialize_extras``), not a ``BackField`` tuple.
        """
        now_iso = datetime.now(UTC).isoformat()
        # Always-written columns, then any Anki-sourced display columns that were
        # actually provided (None ⇒ leave untouched, so we don't clobber on a sync
        # whose reader didn't supply that field).
        set_cols = ["translation = ?", "note = ?", "sentence_translation = ?"]
        params: list[object] = [translation, note, sentence_translation]
        for col, value in (("article", article), ("extras", extras)):
            if value is not None:
                set_cols.append(f"{col} = ?")
                params.append(value)
        set_cols += ["dirty_fields = ?", "last_synced_at = ?", "updated_at = ?"]
        params += [dirty_fields_str, now_iso, now_iso, guid]
        with self._get_conn() as conn:
            conn.execute(f"UPDATE collocations SET {', '.join(set_cols)} WHERE guid = ?", params)
            self._commit(conn)

    def list_items_without_anki_note(self) -> list[tuple[str, SRSItem, int]]:
        """Return (guid, SRSItem, id) for collocations with no anki_note_id set."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM collocations WHERE anki_note_id IS NULL").fetchall()
            return [(row["guid"], self._row_to_item(conn, row), row["id"]) for row in rows]

    def list_dirty_field_edits(self) -> list[tuple[str, int | None, str, SRSItem, int]]:
        """Return (guid, anki_note_id, dirty_fields_str, SRSItem, id) for rows with non-empty dirty_fields."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM collocations WHERE dirty_fields IS NOT NULL AND dirty_fields != ''"
            ).fetchall()
            return [
                (row["guid"], row["anki_note_id"], row["dirty_fields"], self._row_to_item(conn, row), row["id"])
                for row in rows
            ]
