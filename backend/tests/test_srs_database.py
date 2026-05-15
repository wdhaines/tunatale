"""SRS database tests."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


def _unit(text: str = "dober dan", translation: str = "good day") -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=2, difficulty=1, source="corpus")


class TestCRUD:
    """Tests for basic add/get/update collocation operations."""

    def test_add_and_get_collocation(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        retrieved = srs_db.get_collocation("dober dan")
        assert retrieved is not None
        assert retrieved.syntactic_unit.text == "dober dan"

    def test_add_duplicate_does_not_raise(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        srs_db.add_collocation(unit, language_code="sl")  # should not raise

    def test_add_collocation_backfills_empty_translation(self, srs_db):
        """Re-adding a word with a real translation fills the previously empty one."""
        srs_db.add_collocation(_unit("banka", ""), language_code="sl")
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        assert srs_db.get_collocation("banka").syntactic_unit.translation == "bank"

    def test_add_collocation_preserves_existing_nonempty_translation(self, srs_db):
        """Re-adding does NOT overwrite a translation the user already has."""
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        srs_db.add_collocation(_unit("banka", "financial institution"), language_code="sl")
        assert srs_db.get_collocation("banka").syntactic_unit.translation == "bank"

    def test_add_collocation_case_variant_upserts_no_error(self, srs_db):
        """Adding a case variant of an existing word upserts (does not raise IntegrityError)."""
        srs_db.add_collocation(
            SyntacticUnit(text="zdravo", translation="hello", word_count=1, difficulty=1, source="corpus"),
            language_code="sl",
        )
        # Must not raise IntegrityError:
        srs_db.add_collocation(
            SyntacticUnit(text="Zdravo", translation="", word_count=1, difficulty=1, source="corpus"),
            language_code="sl",
        )
        # Still exactly one row:
        rows, total = srs_db.list_collocations(search="zdravo")
        assert total == 1

    def test_add_collocation_tolerates_legacy_guid_row(self, srs_db):
        """Row already in DB under a stale guid must not crash a re-add.

        Pre-Phase-H guids and pre-disambig-default rows live on under stored guids
        that no longer match the current `compute_guid` output. The UNIQUE(text,
        disambig_key) constraint still applies, so the old ON CONFLICT(guid)-only
        path raised IntegrityError. New behavior: heal the stale guid in place and
        backfill an empty translation.
        """
        # Plant a row with a deliberately stale guid (mimicking legacy data).
        with srs_db._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO collocations (text, translation, language_code, word_count,
                    unit_difficulty, source, corpus_frequency, lemma, guid, disambig_key)
                VALUES ('ja', '', 'sl', 1, 1, 'anki', 0, 'ja', 'legacystaleguid', '')
                """
            )
            conn.commit()

        # Re-add via the normal /listen-style path — must not raise.
        srs_db.add_collocation(
            SyntacticUnit(
                text="ja",
                translation="yes",
                word_count=1,
                difficulty=1,
                source="llm",
                lemma="ja",
            ),
            language_code="sl",
        )

        item = srs_db.get_collocation("ja")
        assert item is not None
        # Backfilled the empty translation.
        assert item.syntactic_unit.translation == "yes"
        # Healed the stale guid to the current compute_guid output.
        from app.common.guid import compute_guid

        assert item.guid == compute_guid("ja", "sl", "")
        # Still exactly one row.
        rows, total = srs_db.list_collocations(search="ja")
        assert total == 1

    def test_backfill_translations_updates_empty_rows(self, srs_db):
        """backfill_translations fills in empty translations from a gloss map."""
        srs_db.add_collocation(_unit("banka", ""), language_code="sl")
        srs_db.add_collocation(_unit("hvala", "thank you"), language_code="sl")
        srs_db.backfill_translations({"banka": "bank", "hvala": "danke"})
        assert srs_db.get_collocation("banka").syntactic_unit.translation == "bank"
        assert srs_db.get_collocation("hvala").syntactic_unit.translation == "thank you"  # not overwritten

    def test_backfill_translations_returns_count(self, srs_db):
        """backfill_translations returns the number of rows updated."""
        srs_db.add_collocation(_unit("banka", ""), language_code="sl")
        srs_db.add_collocation(_unit("hvala", "thank you"), language_code="sl")
        n = srs_db.backfill_translations({"banka": "bank", "hvala": "danke"})
        assert n == 1  # only banka was empty

    def test_backfill_translations_skips_empty_string_values(self, srs_db):
        """Glosses entries with empty-string translations are skipped."""
        srs_db.add_collocation(_unit("banka", ""), language_code="sl")
        n = srs_db.backfill_translations({"banka": ""})
        assert n == 0
        assert srs_db.get_collocation("banka").syntactic_unit.translation == ""

    def test_get_nonexistent_returns_none(self, srs_db):
        assert srs_db.get_collocation("nonexistent") is None

    def test_update_collocation(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("dober dan")
        item.reps = 5
        item.stability = 20.0
        item.state = SRSState.REVIEW
        srs_db.update_collocation(item)

        updated = srs_db.get_collocation("dober dan")
        assert updated.reps == 5
        assert updated.stability == 20.0
        assert updated.state == SRSState.REVIEW

    def test_update_direction_round_trips_anki_due(self, srs_db):
        """update_direction then _load_directions round-trips anki_due."""
        unit = _unit("test_word", "test")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("test_word")
        guid = item.guid
        # Update recognition direction with anki_due
        rec_dir = item.directions[Direction.RECOGNITION]
        rec_dir.anki_due = 612
        srs_db.update_direction(guid, Direction.RECOGNITION, rec_dir)
        # Reload and check
        reloaded = srs_db.get_collocation("test_word")
        assert reloaded.directions[Direction.RECOGNITION].anki_due == 612

    def test_add_collocation_cloze_creates_only_production_direction(self, srs_db):
        """Cloze card_type creates only production direction (no recognition)."""
        unit = SyntacticUnit(
            text="ki",
            translation="",
            word_count=1,
            difficulty=1,
            source="cloze",
            lemma="ki",
            source_sentence="knjiga, ki je tam",
            card_type="cloze",
        )
        srs_db.add_collocation(unit, language_code="sl")
        with srs_db._get_conn() as conn:
            row = conn.execute("SELECT id FROM collocations WHERE text = 'ki'").fetchone()
            assert row is not None
            directions = conn.execute(
                "SELECT direction FROM collocation_directions WHERE collocation_id = ?",
                (row["id"],),
            ).fetchall()
            dirs = [d["direction"] for d in directions]
            assert dirs == ["production"]

    def test_add_collocation_vocab_creates_both_directions(self, srs_db):
        """Default vocab card_type creates both recognition and production directions."""
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("dober dan")
        assert Direction.RECOGNITION in item.directions
        assert Direction.PRODUCTION in item.directions

    def test_get_collocation_returns_card_type(self, srs_db):
        """Round-trip card_type through add_collocation and get_collocation."""
        unit = SyntacticUnit(
            text="je",
            translation="is",
            word_count=1,
            difficulty=1,
            source="cloze",
            lemma="je",
            source_sentence="je tam",
            card_type="cloze",
        )
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("je")
        assert item is not None
        assert item.syntactic_unit.card_type == "cloze"

    def test_add_collocation_empty_string_lemma_falls_back_to_casefold_text(self, srs_db):
        """Single-word units with lemma='' (not just None) still get a usable lemma.

        Regression: pre-Phase-F sync_pull paths sometimes wrote empty strings into
        the lemma column for converted cloze rows. The fallback used to check only
        for None, so empty strings slipped through and broke transcript lookups.
        """
        unit = SyntacticUnit(
            text="Sem",
            translation="I am",
            word_count=1,
            difficulty=1,
            source="anki",
            lemma="",  # explicit empty string, not None
            card_type="cloze",
            source_sentence="Jaz sem Janez",
        )
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation_by_lemma("sem")
        assert item is not None
        assert item.syntactic_unit.lemma == "sem"

    def test_upsert_by_guid_does_not_clobber_existing_lemma_with_empty(self, srs_db):
        """An incoming sync row with empty lemma must not wipe a stored good lemma."""
        unit = SyntacticUnit(
            text="vsak",
            translation="every",
            word_count=1,
            difficulty=1,
            source="anki",
            lemma="vsak",
            card_type="cloze",
            source_sentence="Odprto je vsak dan",
        )
        srs_db.add_collocation(unit, language_code="sl")
        # Subsequent sync_pull provides empty/garbage lemma
        bad_unit = SyntacticUnit(
            text="vsak",
            translation="every",
            word_count=1,
            difficulty=1,
            source="anki",
            lemma="",  # would corrupt; must be ignored
            card_type="cloze",
            source_sentence="Odprto je vsak dan",
        )
        srs_db.upsert_by_guid(bad_unit, "sl", {}, anki_note_id=12345)
        item = srs_db.get_collocation_by_lemma("vsak")
        assert item is not None
        assert item.syntactic_unit.lemma == "vsak"

    def test_cloze_item_state_reads_production_direction(self, srs_db):
        """Cloze items have only PRODUCTION direction; `item.state` must read from it.

        Regression: the legacy `_rec` shim hardcoded RECOGNITION, causing KeyError
        on every cloze-aware caller (transcript.py, _item_to_dict, etc.).
        """
        unit = SyntacticUnit(
            text="vsak",
            translation="every",
            word_count=1,
            difficulty=1,
            source="cloze",
            lemma="vsak",
            source_sentence="Odprto je vsak dan",
            card_type="cloze",
        )
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("vsak")
        assert item is not None
        assert Direction.RECOGNITION not in item.directions
        assert Direction.PRODUCTION in item.directions
        # These accessors must not raise — they should fall through to PRODUCTION.
        assert item.state == SRSState.NEW
        assert item.reps == 0
        assert item.lapses == 0
        # Setter should mutate the production direction.
        item.state = SRSState.LEARNING
        assert item.directions[Direction.PRODUCTION].state == SRSState.LEARNING

    def test_get_enable_cloze_cards_defaults_false(self, srs_db):
        """Fresh DB with no cache row returns False."""
        assert srs_db.get_enable_cloze_cards() is False

    def test_set_then_get_enable_cloze_cards(self, srs_db):
        """Round-trip set/get for the cloze flag."""
        srs_db.set_enable_cloze_cards(True)
        assert srs_db.get_enable_cloze_cards() is True
        srs_db.set_enable_cloze_cards(False)
        assert srs_db.get_enable_cloze_cards() is False


class TestDueQueries:
    """Tests for due/new collocation queries."""

    def test_get_due_collocations_includes_overdue(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("dober dan")
        item.due_date = date.today() - timedelta(days=1)
        item.state = SRSState.REVIEW
        srs_db.update_collocation(item)

        due = srs_db.get_due_collocations(date.today())
        assert any(i.syntactic_unit.text == "dober dan" for i in due)

    def test_get_due_collocations_excludes_future(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("dober dan")
        item.due_date = date.today() + timedelta(days=10)
        item.state = SRSState.REVIEW
        srs_db.update_collocation(item)

        due = srs_db.get_due_collocations(date.today())
        assert not any(i.syntactic_unit.text == "dober dan" for i in due)

    def test_get_new_collocations(self, srs_db):
        srs_db.add_collocation(_unit("dober dan"), language_code="sl")
        srs_db.add_collocation(_unit("hvala lepa", "thank you"), language_code="sl")

        new = srs_db.get_new_collocations(limit=10)
        assert len(new) == 2

    def test_get_new_items_returns_stable_order(self, srs_db):
        for t in ["word0", "word1", "word2", "word3", "word4"]:
            srs_db.add_collocation(_unit(t, f"trans_{t}"), language_code="sl")
        first = [item.syntactic_unit.text for _, item, _ in srs_db.get_new_items(limit=5)]
        second = [item.syntactic_unit.text for _, item, _ in srs_db.get_new_items(limit=5)]
        assert first == second

    def test_get_new_items_synced_orders_by_anki_due_desc(self, srs_db):
        """Synced rows sort by anki_due DESC to mirror Anki HighestPosition gather."""
        for t in ["word_a", "word_b", "word_c"]:
            srs_db.add_collocation(_unit(t, f"trans_{t}"), language_code="sl")
        due_map = {"word_a": 100, "word_b": 200, "word_c": 150}
        for text, due in due_map.items():
            rows, _ = srs_db.list_collocations(search=text, limit=1)
            row_id, item, _ = rows[0]
            orig = item.directions[Direction.RECOGNITION]
            srs_db.update_direction_by_id(
                row_id,
                Direction.RECOGNITION,
                DirectionState(
                    direction=Direction.RECOGNITION,
                    state=SRSState.NEW,
                    due_date=orig.due_date,
                    stability=orig.stability,
                    difficulty=orig.difficulty,
                    reps=orig.reps,
                    lapses=orig.lapses,
                    anki_card_id=due * 10,
                    anki_due=due,
                ),
            )
        texts = [item.syntactic_unit.text for _, item, _ in srs_db.get_new_items(limit=10)]
        # Highest anki_due first → b (200), c (150), a (100)
        assert texts == ["word_b", "word_c", "word_a"]

    def test_get_new_items_unsynced_rows_surface_above_synced(self, srs_db):
        """Unsynced rows (anki_due NULL) come BEFORE any synced row, even if synced has higher anki_due."""
        srs_db.add_collocation(_unit("synced_high", "s"), language_code="sl")
        rows, _ = srs_db.list_collocations(search="synced_high", limit=1)
        row_id, item, _ = rows[0]
        orig = item.directions[Direction.RECOGNITION]
        srs_db.update_direction_by_id(
            row_id,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.NEW,
                due_date=orig.due_date,
                stability=orig.stability,
                difficulty=orig.difficulty,
                reps=orig.reps,
                lapses=orig.lapses,
                anki_card_id=999_999,
                anki_due=1_000_000,
            ),
        )
        # Fresh auto-add: anki_due=NULL
        srs_db.add_collocation(_unit("fresh_unsynced", "f"), language_code="sl")
        texts = [item.syntactic_unit.text for _, item, _ in srs_db.get_new_items(limit=10)]
        assert texts == ["fresh_unsynced", "synced_high"], (
            "Unsynced TT-added rows must appear before all synced rows (NULLS FIRST)."
        )

    def test_get_new_items_within_unsynced_newest_created_first(self, srs_db):
        """Among unsynced rows (anki_due NULL), tiebreak by created_at DESC."""
        srs_db.add_collocation(_unit("older", "older"), language_code="sl")
        with srs_db._get_conn() as conn:
            conn.execute("UPDATE collocations SET created_at = '2024-01-01 00:00:00' WHERE text = 'older'")
            conn.commit()
        srs_db.add_collocation(_unit("newer", "newer"), language_code="sl")
        texts = [item.syntactic_unit.text for _, item, _ in srs_db.get_new_items(limit=10)]
        assert texts == ["newer", "older"]

    def test_get_new_items_tiebreakers_after_anki_due(self, srs_db):
        """When anki_due ties, fall back to anki_card_id ASC then c.id ASC."""
        for t in ["word_a", "word_b", "word_c"]:
            srs_db.add_collocation(_unit(t, f"trans_{t}"), language_code="sl")
        # Same anki_due, different anki_card_id
        cfg = {"word_a": 555, "word_b": 222, "word_c": 333}
        for text, aid in cfg.items():
            rows, _ = srs_db.list_collocations(search=text, limit=1)
            row_id, item, _ = rows[0]
            orig = item.directions[Direction.RECOGNITION]
            srs_db.update_direction_by_id(
                row_id,
                Direction.RECOGNITION,
                DirectionState(
                    direction=Direction.RECOGNITION,
                    state=SRSState.NEW,
                    due_date=orig.due_date,
                    stability=orig.stability,
                    difficulty=orig.difficulty,
                    reps=orig.reps,
                    lapses=orig.lapses,
                    anki_card_id=aid,
                    anki_due=42,
                ),
            )
        texts = [item.syntactic_unit.text for _, item, _ in srs_db.get_new_items(limit=10)]
        # anki_due ties → anki_card_id ASC: b(222), c(333), a(555)
        assert texts == ["word_b", "word_c", "word_a"]

    def test_get_due_items_returns_due_date_then_id_order(self, srs_db):
        today = date.today()
        # Insert in order word_a(id=1), word_b(id=2), word_c(id=3); none have anki_card_id
        for text in ["word_a", "word_b", "word_c"]:
            srs_db.add_collocation(_unit(text, f"trans_{text}"), language_code="sl")
        # word_a and word_c share the same due_date — no anki_card_id, so falls back to c.id ASC
        for text, days_ago in [("word_a", 5), ("word_b", 1), ("word_c", 5)]:
            item = srs_db.get_collocation(text)
            item.due_date = today - timedelta(days=days_ago)
            item.state = SRSState.REVIEW
            srs_db.update_collocation(item)
        result = srs_db.get_due_items(today)
        texts = [item.syntactic_unit.text for _, item, _ in result]
        # NULL anki_card_id falls back to c.id ASC:
        #   word_a (5d ago, id=1), word_c (5d ago, id=3), word_b (1d ago, id=2)
        assert texts == ["word_a", "word_c", "word_b"]

    def test_get_due_items_uses_anki_card_id_as_tiebreak(self, srs_db):
        today = date.today()
        for text in ["word_a", "word_b", "word_c"]:
            srs_db.add_collocation(_unit(text, f"trans_{text}"), language_code="sl")
        # word_a gets c.id=1 but anki_card_id=300; word_c gets c.id=3 but anki_card_id=100
        # Expected: word_c before word_a (anki_card_id 100 < 300), not word_a (c.id 1 < 3)
        anki_ids = {"word_a": 300, "word_b": 200, "word_c": 100}
        for text, days_ago in [("word_a", 5), ("word_b", 1), ("word_c", 5)]:
            rows, _ = srs_db.list_collocations(search=text, limit=1)
            row_id, item, _ = rows[0]
            orig = item.directions[Direction.RECOGNITION]
            new_dir = DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_date=today - timedelta(days=days_ago),
                stability=orig.stability,
                difficulty=orig.difficulty,
                reps=orig.reps,
                lapses=orig.lapses,
                anki_card_id=anki_ids[text],
            )
            srs_db.update_direction_by_id(row_id, Direction.RECOGNITION, new_dir)
        result = srs_db.get_due_items(today)
        texts = [item.syntactic_unit.text for _, item, _ in result]
        # ORDER BY due_date ASC, anki_card_id ASC:
        #   word_c (5d ago, anki_id=100), word_a (5d ago, anki_id=300), word_b (1d ago, anki_id=200)
        assert texts == ["word_c", "word_a", "word_b"]

    def test_get_due_items_orders_by_stability_ascending_within_same_due_date(self, srs_db):
        """Within same due_date, lower stability (lower retrievability) comes first."""
        today = date.today()
        due_date = today - timedelta(days=5)  # 5 days overdue

        # word_a: stability=0.086 (very low), anki_card_id=100 (low)
        # word_b: stability=0.4 (higher), anki_card_id=200 (higher)
        # Expected: word_a first (lower stability), even though anki_card_id is lower
        for text, stab, anki_id in [("word_a", 0.086, 100), ("word_b", 0.4, 200)]:
            srs_db.add_collocation(_unit(text, f"trans_{text}"), language_code="sl")
            rows, _ = srs_db.list_collocations(search=text, limit=1)
            row_id, item, _ = rows[0]
            orig = item.directions[Direction.RECOGNITION]
            new_dir = DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_date=due_date,
                stability=stab,
                difficulty=orig.difficulty,
                reps=orig.reps,
                lapses=orig.lapses,
                anki_card_id=anki_id,
                last_review=today - timedelta(days=1),
            )
            srs_db.update_direction_by_id(row_id, Direction.RECOGNITION, new_dir)

        result = srs_db.get_due_items(today)
        texts = [item.syntactic_unit.text for _, item, _ in result]
        # word_a (stability=0.086) should come before word_b (stability=0.4)
        assert texts.index("word_a") < texts.index("word_b")

    def test_set_state_by_id_marks_both_directions_dirty(self, srs_db):
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.set_state_by_id(row_id, SRSState.KNOWN)
        item = srs_db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is True
        assert item.directions[Direction.PRODUCTION].dirty_fsrs is True

    def test_set_state_by_id_marks_single_direction_dirty(self, srs_db):
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.set_state_by_id(row_id, SRSState.LEARNING, direction=Direction.RECOGNITION)
        item = srs_db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is True
        assert item.directions[Direction.PRODUCTION].dirty_fsrs is False

    def test_set_state_by_id_with_mark_dirty_false_does_not_mark_dirty(self, srs_db):
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.set_state_by_id(row_id, SRSState.KNOWN, mark_dirty=False)
        item = srs_db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is False
        assert item.directions[Direction.PRODUCTION].dirty_fsrs is False

    def test_set_state_by_id_to_new_clears_introduced_at(self, srs_db):
        """Resetting state to NEW must clear the introduced_at stamp.

        Regression: the WordSpan word-click cycles state new → learning → known →
        ignored → new. When sync_pull had previously stamped introduced_at (from
        Anki's revlog) and the user cycles back to NEW, leaving introduced_at set
        inflates count_new_introduced_today and the daily-new badge math.
        """
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        # Simulate a sync_pull that stamped introduced_at + state=REVIEW.
        now = datetime(2026, 5, 14, 13, 42, 59, tzinfo=UTC)
        with srs_db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET state='review', introduced_at=?,"
                " prior_state='new' WHERE collocation_id=?",
                (now.isoformat(), row_id),
            )
            conn.commit()
        # User cycles state back to NEW via WordSpan.
        srs_db.set_state_by_id(row_id, SRSState.NEW)
        item = srs_db.get_collocation("banka")
        for d in (Direction.RECOGNITION, Direction.PRODUCTION):
            assert item.directions[d].state == SRSState.NEW
            assert item.directions[d].introduced_at is None
            assert item.directions[d].prior_state is None

    def test_set_state_by_id_to_non_new_preserves_introduced_at(self, srs_db):
        """Other state transitions must NOT clear introduced_at (Layer 26 sticky stamp)."""
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        stamp = datetime(2026, 5, 14, 13, 42, 59, tzinfo=UTC)
        with srs_db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET state='review', introduced_at=? WHERE collocation_id=?",
                (stamp.isoformat(), row_id),
            )
            conn.commit()
        srs_db.set_state_by_id(row_id, SRSState.KNOWN)
        item = srs_db.get_collocation("banka")
        for d in (Direction.RECOGNITION, Direction.PRODUCTION):
            assert item.directions[d].state == SRSState.KNOWN
            assert item.directions[d].introduced_at == stamp

    def test_get_due_items_excludes_buried_state(self, srs_db):
        """Buried directions must not appear in get_due_items even if due_date <= today."""
        today = date.today()
        srs_db.add_collocation(_unit("review_word", "trans"), language_code="sl")
        srs_db.add_collocation(_unit("buried_word", "trans"), language_code="sl")
        srs_db.add_collocation(_unit("learning_word", "trans"), language_code="sl")

        # Set review_word → REVIEW, due today
        item_r = srs_db.get_collocation("review_word")
        item_r.due_date = today
        item_r.state = SRSState.REVIEW
        srs_db.update_collocation(item_r)

        # Set buried_word → BURIED, due today
        item_b = srs_db.get_collocation("buried_word")
        item_b.due_date = today
        item_b.state = SRSState.BURIED
        srs_db.update_collocation(item_b)

        # Set learning_word → LEARNING, due today
        item_l = srs_db.get_collocation("learning_word")
        item_l.due_date = today
        item_l.state = SRSState.LEARNING
        srs_db.update_collocation(item_l)

        result = srs_db.get_due_items(today)
        texts = [item.syntactic_unit.text for _, item, _ in result]

        assert "review_word" in texts
        assert "learning_word" in texts
        assert "buried_word" not in texts


class TestUnburyIfNeeded:
    """Tests for db.unbury_if_needed — Anki-parity daily queue=-2/-3 reset."""

    def _bury_direction(self, srs_db, text: str, direction: Direction, reps: int, bury_kind: str = "sched"):
        rows, _ = srs_db.list_collocations(search=text, limit=1)
        row_id, item, _ = rows[0]
        orig = item.directions[direction]
        srs_db.update_direction_by_id(
            row_id,
            direction,
            DirectionState(
                direction=direction,
                state=SRSState.BURIED,
                due_date=orig.due_date,
                stability=orig.stability,
                difficulty=orig.difficulty,
                reps=reps,
                lapses=orig.lapses,
                anki_card_id=42,
                bury_kind=bury_kind,
            ),
        )

    def test_unbury_restores_review_for_reps_gt_zero(self, srs_db):
        today = date.today()
        srs_db.add_collocation(_unit("graded_then_buried", "x"), language_code="sl")
        self._bury_direction(srs_db, "graded_then_buried", Direction.RECOGNITION, reps=5)
        count = srs_db.unbury_if_needed(today)
        assert count == 1
        item = srs_db.get_collocation("graded_then_buried")
        assert item.directions[Direction.RECOGNITION].state == SRSState.REVIEW

    def test_unbury_restores_new_for_reps_zero(self, srs_db):
        today = date.today()
        srs_db.add_collocation(_unit("never_graded_buried", "x"), language_code="sl")
        self._bury_direction(srs_db, "never_graded_buried", Direction.RECOGNITION, reps=0)
        srs_db.unbury_if_needed(today)
        item = srs_db.get_collocation("never_graded_buried")
        assert item.directions[Direction.RECOGNITION].state == SRSState.NEW

    def test_unbury_is_idempotent_within_same_day(self, srs_db):
        today = date.today()
        srs_db.add_collocation(_unit("buried_once", "x"), language_code="sl")
        self._bury_direction(srs_db, "buried_once", Direction.RECOGNITION, reps=3)
        first = srs_db.unbury_if_needed(today)
        # Bury again to simulate a fresh sync_pull that landed today's sibling-buries
        self._bury_direction(srs_db, "buried_once", Direction.RECOGNITION, reps=3)
        second = srs_db.unbury_if_needed(today)
        assert first == 1
        assert second == 0, "second call within the same day must be a no-op"
        # The row stays buried (today's bury is preserved)
        item = srs_db.get_collocation("buried_once")
        assert item.directions[Direction.RECOGNITION].state == SRSState.BURIED

    def test_unbury_re_sweeps_on_new_day(self, srs_db):
        today = date.today()
        yesterday = today - timedelta(days=1)
        srs_db.add_collocation(_unit("rebury_after_rollover", "x"), language_code="sl")
        self._bury_direction(srs_db, "rebury_after_rollover", Direction.RECOGNITION, reps=2)
        srs_db.unbury_if_needed(yesterday)
        # Bury again post-yesterday-sweep
        self._bury_direction(srs_db, "rebury_after_rollover", Direction.RECOGNITION, reps=2)
        count = srs_db.unbury_if_needed(today)
        assert count == 1, "rolling to a new day must re-sweep stale buried rows"

    def test_unbury_preserves_user_buried_rows(self, srs_db):
        """User-manual-buried rows (bury_kind='user') stay buried across the sweep.

        Anki's sched/sibling-bury auto-releases at rollover; manual user-bury
        doesn't. Mirroring that: only `bury_kind='sched'` rows get released.
        """
        today = date.today()
        srs_db.add_collocation(_unit("user_buried", "x"), language_code="sl")
        self._bury_direction(srs_db, "user_buried", Direction.RECOGNITION, reps=4, bury_kind="user")
        srs_db.add_collocation(_unit("sched_buried", "x"), language_code="sl")
        self._bury_direction(srs_db, "sched_buried", Direction.RECOGNITION, reps=4, bury_kind="sched")

        count = srs_db.unbury_if_needed(today)
        assert count == 1  # only the sched one
        assert srs_db.get_collocation("user_buried").directions[Direction.RECOGNITION].state == SRSState.BURIED
        assert srs_db.get_collocation("sched_buried").directions[Direction.RECOGNITION].state == SRSState.REVIEW

    def test_unbury_writes_last_unbury_day_cache(self, srs_db):
        today = date.today()
        srs_db.unbury_if_needed(today)
        cached = srs_db.get_anki_state_cache("last_unbury_day")
        assert cached is not None
        assert cached[0] == today.isoformat()


class TestReviewedToday:
    """Tests for list_collocations_reviewed_today."""

    def test_returns_collocation_when_recognition_reviewed_today(self, srs_db):
        from datetime import date

        srs_db.add_collocation(_unit("word_a"), language_code="sl")
        rows, _ = srs_db.list_collocations(search="word_a", limit=1)
        row_id, item, _ = rows[0]

        # Update recognition direction to have last_review = today
        orig = item.directions[Direction.RECOGNITION]
        today = date.today()
        new_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            stability=orig.stability,
            difficulty=orig.difficulty,
            reps=orig.reps,
            lapses=orig.lapses,
            last_review=today,
        )
        srs_db.update_direction_by_id(row_id, Direction.RECOGNITION, new_dir)

        result = srs_db.list_collocations_reviewed_today(today)
        assert row_id in result

    def test_returns_empty_when_nothing_reviewed(self, srs_db):
        from datetime import date

        srs_db.add_collocation(_unit("word_b"), language_code="sl")
        result = srs_db.list_collocations_reviewed_today(date.today())
        assert len(result) == 0

    def test_returns_one_id_when_both_directions_reviewed(self, srs_db):
        from datetime import date

        srs_db.add_collocation(_unit("word_c"), language_code="sl")
        rows, _ = srs_db.list_collocations(search="word_c", limit=1)
        row_id, item, _ = rows[0]
        today = date.today()

        # Update both directions to have last_review = today
        for dir in [Direction.RECOGNITION, Direction.PRODUCTION]:
            orig = item.directions[dir]
            new_dir = DirectionState(
                direction=dir,
                state=SRSState.REVIEW,
                due_date=today,
                stability=orig.stability,
                difficulty=orig.difficulty,
                reps=orig.reps,
                lapses=orig.lapses,
                last_review=today,
            )
            srs_db.update_direction_by_id(row_id, dir, new_dir)

        result = srs_db.list_collocations_reviewed_today(today)
        assert len(result) == 1
        assert row_id in result

    def test_matches_when_last_review_is_iso_datetime(self, srs_db):
        """FSRS scheduling writes last_review as an ISO datetime. The query must
        still find rows graded through the FSRS code path — otherwise sibling-
        bury silently fails. Use `datetime.now(UTC)` (the actual production
        write site) which is by construction inside today's local-day window.
        """
        from datetime import datetime

        srs_db.add_collocation(_unit("word_d"), language_code="sl")
        rows, _ = srs_db.list_collocations(search="word_d", limit=1)
        row_id, item, _ = rows[0]
        today = date.today()
        last_review_dt = datetime.now(UTC)

        orig = item.directions[Direction.RECOGNITION]
        new_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            stability=orig.stability,
            difficulty=orig.difficulty,
            reps=orig.reps,
            lapses=orig.lapses,
            last_review=last_review_dt,
        )
        srs_db.update_direction_by_id(row_id, Direction.RECOGNITION, new_dir)

        result = srs_db.list_collocations_reviewed_today(today)
        assert row_id in result

    def test_count_collocations(self, srs_db):
        assert srs_db.count_collocations() == 0
        srs_db.add_collocation(_unit("dober dan"), language_code="sl")
        assert srs_db.count_collocations() == 1

    def test_buckets_by_local_day_when_review_crosses_utc_midnight(self, srs_db, monkeypatch):
        """Regression: a card reviewed at 23:30 local must bucket into today's
        local day even when its UTC date is tomorrow.

        SQLite's `date(last_review)` returns the UTC date of the stored ISO
        timestamp. Comparing that against `today.isoformat()` (a local date)
        misfires whenever local-midnight and UTC-midnight straddle the review
        moment, silently mis-burying / un-burying siblings near midnight.
        Force tz=PDT so the bug is deterministic regardless of host tz.
        """
        import time
        from datetime import datetime

        monkeypatch.setenv("TZ", "America/Los_Angeles")
        time.tzset()

        srs_db.add_collocation(_unit("late_evening"), language_code="sl")
        rows, _ = srs_db.list_collocations(search="late_evening", limit=1)
        late_id, late_item, _ = rows[0]
        srs_db.add_collocation(_unit("early_morning"), language_code="sl")
        rows, _ = srs_db.list_collocations(search="early_morning", limit=1)
        early_id, early_item, _ = rows[0]

        today_local = date(2026, 5, 8)
        # 23:30 PDT on May 8 = 06:30 UTC on May 9. UTC date = May 9, local date = May 8.
        late_utc = datetime(2026, 5, 9, 6, 30, tzinfo=UTC)
        # 00:30 PDT on May 8 = 07:30 UTC on May 8. Both UTC and local date = May 8 here,
        # so this case is the control — should match in any tz.
        early_utc = datetime(2026, 5, 8, 7, 30, tzinfo=UTC)

        for row_id, item, last_review in (
            (late_id, late_item, late_utc),
            (early_id, early_item, early_utc),
        ):
            orig = item.directions[Direction.RECOGNITION]
            srs_db.update_direction_by_id(
                row_id,
                Direction.RECOGNITION,
                DirectionState(
                    direction=Direction.RECOGNITION,
                    state=SRSState.REVIEW,
                    due_date=today_local,
                    stability=orig.stability,
                    difficulty=orig.difficulty,
                    reps=orig.reps,
                    lapses=orig.lapses,
                    last_review=last_review,
                ),
            )

        result = srs_db.list_collocations_reviewed_today(today_local)
        assert late_id in result, "late-evening review must bucket into local today"
        assert early_id in result, "morning review must bucket into local today"

        # And NOT into adjacent days
        assert late_id not in srs_db.list_collocations_reviewed_today(today_local + timedelta(days=1))
        assert early_id not in srs_db.list_collocations_reviewed_today(today_local - timedelta(days=1))


class TestCountNewIntroducedToday:
    """count_new_introduced_today must reflect *real* first-grade introductions,
    not the sticky prior_state='new' marker on long-graduated cards.

    Layer 26 bug fix: filter on the explicit `introduced_at` column written by
    the grade endpoint / sync_pull on the first NEW→non-NEW transition.
    """

    def _seed_with_introduction(
        self,
        db: SRSDatabase,
        text: str,
        introduced_at_iso: str | None,
        last_review_iso: str,
        prior_state: SRSState = SRSState.NEW,
        reps: int = 1,
    ):
        """Insert a card whose recognition direction was introduced at `introduced_at_iso`."""
        from datetime import datetime as _dt

        db.add_collocation(_unit(text, "x"), language_code="sl")
        item = db.get_collocation(text)
        orig = item.directions[Direction.RECOGNITION]
        new_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=orig.due_date,
            stability=1.0,
            difficulty=5.0,
            reps=reps,
            lapses=0,
            last_review=_dt.fromisoformat(last_review_iso) if last_review_iso else None,
            prior_state=prior_state,
            introduced_at=_dt.fromisoformat(introduced_at_iso) if introduced_at_iso else None,
        )
        db.update_direction(item.guid, Direction.RECOGNITION, new_dir)

    def test_counts_card_introduced_today(self, srs_db):
        from datetime import datetime as _dt

        today = date.today()
        local = _dt.now().astimezone().tzinfo
        today_noon = _dt.combine(today, time(12), tzinfo=local).astimezone(UTC).isoformat()
        self._seed_with_introduction(srs_db, "intro_today", today_noon, today_noon)
        assert srs_db.count_new_introduced_today(today) == 1

    def test_does_not_count_card_introduced_on_prior_day_reviewed_today(self, srs_db):
        """Sticky-NEW card reviewed today but introduced days ago must NOT count.

        Anki's `newToday` increments only on the actual first-grade event; later
        reviews of the same card don't bump it. TT must mirror that.
        """
        from datetime import datetime as _dt

        today = date.today()
        local = _dt.now().astimezone().tzinfo
        intro_day = today - timedelta(days=5)
        intro_iso = _dt.combine(intro_day, time(9), tzinfo=local).astimezone(UTC).isoformat()
        today_iso = _dt.combine(today, time(8), tzinfo=local).astimezone(UTC).isoformat()
        self._seed_with_introduction(
            srs_db,
            "old_intro_reviewed_today",
            introduced_at_iso=intro_iso,
            last_review_iso=today_iso,
            prior_state=SRSState.NEW,
            reps=7,
        )
        assert srs_db.count_new_introduced_today(today) == 0

    def test_does_not_count_unintroduced_card(self, srs_db):
        """A card with no introduced_at (e.g., still NEW) doesn't count."""
        today = date.today()
        srs_db.add_collocation(_unit("never_graded", "x"), language_code="sl")
        assert srs_db.count_new_introduced_today(today) == 0

    def test_counts_distinct_collocations_when_both_directions_introduced(self, srs_db):
        """Both directions of the same colloc introduced today → still counts once."""
        from datetime import datetime as _dt

        today = date.today()
        local = _dt.now().astimezone().tzinfo
        today_noon = _dt.combine(today, time(12), tzinfo=local).astimezone(UTC).isoformat()
        self._seed_with_introduction(srs_db, "dual_intro", today_noon, today_noon)
        item = srs_db.get_collocation("dual_intro")
        orig_prod = item.directions[Direction.PRODUCTION]
        srs_db.update_direction(
            item.guid,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                state=SRSState.REVIEW,
                due_date=orig_prod.due_date,
                stability=1.0,
                reps=1,
                last_review=datetime.fromisoformat(today_noon),
                prior_state=SRSState.NEW,
                introduced_at=datetime.fromisoformat(today_noon),
            ),
        )
        assert srs_db.count_new_introduced_today(today) == 1


class TestViolations:
    """Tests for recording and querying SRS violations."""

    def test_record_violation(self, srs_db):
        srs_db.record_violation(
            collocation_text="dober dan", day_number=1, violation_type="unused", details="not used in story"
        )
        violations = srs_db.get_violations(collocation_text="dober dan")
        assert len(violations) == 1
        assert violations[0]["violation_type"] == "unused"

    def test_get_violations_empty(self, srs_db):
        assert srs_db.get_violations("nonexistent") == []


class TestFileBased:
    """Tests for file-backed SRS database persistence."""

    def test_file_based_database(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = SRSDatabase(str(db_path))
        unit = _unit()
        db.add_collocation(unit, language_code="sl")
        assert db.get_collocation("dober dan") is not None


class TestOrphanReset:
    """Tests for list_anki_card_ids and reset_orphaned_anki_ids."""

    def _link(self, srs_db, text: str, *, note_id: int, rec_cid: int, prod_cid: int):
        srs_db.add_collocation(_unit(text, text + "_t"), language_code="sl")
        item = srs_db.get_collocation(text)
        srs_db.set_anki_ids(
            item.guid,
            note_id,
            {Direction.RECOGNITION: rec_cid, Direction.PRODUCTION: prod_cid},
        )
        return item.guid

    def test_list_anki_card_ids_returns_all_non_null(self, srs_db):
        self._link(srs_db, "a", note_id=10, rec_cid=100, prod_cid=101)
        self._link(srs_db, "b", note_id=11, rec_cid=110, prod_cid=111)
        # Add a row with no anki ids set
        srs_db.add_collocation(_unit("c", "ct"), language_code="sl")

        ids = srs_db.list_anki_card_ids()
        assert ids == {100, 101, 110, 111}

    def test_reset_clears_card_id_when_missing_from_live_set(self, srs_db):
        guid_a = self._link(srs_db, "a", note_id=10, rec_cid=100, prod_cid=101)
        guid_b = self._link(srs_db, "b", note_id=11, rec_cid=110, prod_cid=111)

        # Live: only b's note + cards exist; a is fully orphaned.
        dir_resets, note_resets = srs_db.reset_orphaned_anki_ids(
            live_card_ids={110, 111},
            live_note_ids={11},
        )

        assert (guid_a, "recognition") in dir_resets
        assert (guid_a, "production") in dir_resets
        assert (guid_b, "recognition") not in dir_resets
        assert guid_a in note_resets
        assert guid_b not in note_resets

        # Verify state: a's card_ids/note_id are NULL, b's preserved.
        item_a = srs_db.get_collocation("a")
        item_b = srs_db.get_collocation("b")
        assert item_a.anki_note_id is None
        assert item_a.directions[Direction.RECOGNITION].anki_card_id is None
        assert item_a.directions[Direction.PRODUCTION].anki_card_id is None
        assert item_b.anki_note_id == 11
        assert item_b.directions[Direction.RECOGNITION].anki_card_id == 110

    def test_reset_clears_only_card_id_when_note_still_live(self, srs_db):
        """If the note exists in Anki but a card was deleted (rare, e.g. ord
        removed from notetype), only the card_id resets — the note linkage stays."""
        guid_a = self._link(srs_db, "a", note_id=10, rec_cid=100, prod_cid=101)
        # Live: note 10 exists, but production card 101 is gone.
        dir_resets, note_resets = srs_db.reset_orphaned_anki_ids(
            live_card_ids={100},
            live_note_ids={10},
        )

        assert dir_resets == [(guid_a, "production")]
        assert note_resets == []
        item_a = srs_db.get_collocation("a")
        assert item_a.anki_note_id == 10
        assert item_a.directions[Direction.RECOGNITION].anki_card_id == 100
        assert item_a.directions[Direction.PRODUCTION].anki_card_id is None

    def test_reset_marks_dirty_fsrs_for_rows_with_reps(self, srs_db):
        """A reset row with reps>0 has TT-side state worth preserving — flip
        dirty_fsrs=1 so sync_push processes it (force_fsrs writes cards.data
        on the freshly-created Anki card)."""
        guid_a = self._link(srs_db, "a", note_id=10, rec_cid=100, prod_cid=101)
        # Bump recognition reps
        item = srs_db.get_collocation("a")
        rec = item.directions[Direction.RECOGNITION]
        rec.reps = 3
        rec.state = SRSState.LEARNING
        rec.dirty_fsrs = False
        srs_db.update_direction(guid_a, Direction.RECOGNITION, rec)

        srs_db.reset_orphaned_anki_ids(live_card_ids=set(), live_note_ids=set())

        item_after = srs_db.get_collocation("a")
        rec_after = item_after.directions[Direction.RECOGNITION]
        prod_after = item_after.directions[Direction.PRODUCTION]
        assert rec_after.dirty_fsrs is True, "reps>0 reset row should be dirty"
        assert prod_after.dirty_fsrs is False, "reps==0 reset row stays clean"

    def test_reset_clears_last_synced_at(self, srs_db):
        """Reset rows lose their last_synced_at — the new Anki card has no sync
        history yet."""
        from datetime import UTC, datetime

        guid_a = self._link(srs_db, "a", note_id=10, rec_cid=100, prod_cid=101)
        # Set last_synced_at
        item = srs_db.get_collocation("a")
        rec = item.directions[Direction.RECOGNITION]
        rec.last_synced_at = datetime.now(UTC).isoformat()
        srs_db.update_direction(guid_a, Direction.RECOGNITION, rec)

        srs_db.reset_orphaned_anki_ids(live_card_ids=set(), live_note_ids=set())

        item_after = srs_db.get_collocation("a")
        assert item_after.directions[Direction.RECOGNITION].last_synced_at is None


class TestAdminMutations:
    """Tests for admin mutation methods."""

    def test_get_collocation_by_id(self, srs_db):
        srs_db.add_collocation(_unit("zdravo", "hello"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id, item, lang = rows[0]
        result = srs_db.get_collocation_by_id(row_id)
        assert result is not None
        rid, ritem, rlang = result
        assert rid == row_id
        assert ritem.syntactic_unit.text == "zdravo"
        assert rlang == "sl"

    def test_get_collocation_by_id_missing_returns_none(self, srs_db):
        assert srs_db.get_collocation_by_id(9999) is None

    def test_update_collocation_fields_changes_text_and_translation(self, srs_db):
        srs_db.add_collocation(_unit("zdravo", "hello"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id, _, _ = rows[0]
        srs_db.update_collocation_fields(row_id, text="zdravo!", translation="hello!")
        result = srs_db.get_collocation_by_id(row_id)
        assert result[1].syntactic_unit.text == "zdravo!"
        assert result[1].syntactic_unit.translation == "hello!"

    def test_update_collocation_fields_duplicate_text_raises(self, srs_db):
        srs_db.add_collocation(_unit("a", "aa"), language_code="sl")
        srs_db.add_collocation(_unit("b", "bb"), language_code="sl")
        rows, _ = srs_db.list_collocations(order_by="text")
        id_b = next(r[0] for r in rows if r[1].syntactic_unit.text == "b")
        import pytest

        with pytest.raises(ValueError, match="already exists"):
            srs_db.update_collocation_fields(id_b, text="a", translation="dup")

    def test_delete_collocation_removes_row_and_violations(self, srs_db):
        srs_db.add_collocation(_unit("nasvidenje", "goodbye"), language_code="sl")
        srs_db.record_violation("nasvidenje", 1, "unused")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.delete_collocation(row_id)
        assert srs_db.get_collocation("nasvidenje") is None
        assert srs_db.get_violations("nasvidenje") == []

    def test_bulk_delete_returns_count_and_removes_rows(self, srs_db):
        srs_db.add_collocation(_unit("a", "aa"), language_code="sl")
        srs_db.add_collocation(_unit("b", "bb"), language_code="sl")
        srs_db.add_collocation(_unit("c", "cc"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        ids = [r[0] for r in rows[:2]]
        deleted = srs_db.delete_collocations(ids)
        assert deleted == 2
        assert srs_db.count_collocations() == 1

    def test_reset_collocation_zeros_scheduling_fields(self, srs_db):
        srs_db.add_collocation(_unit("hvala", "thank you"), language_code="sl")
        item = srs_db.get_collocation("hvala")
        item.reps = 5
        item.lapses = 2
        item.state = SRSState.REVIEW
        item.stability = 30.0
        srs_db.update_collocation(item)

        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.reset_collocation(row_id)
        reset = srs_db.get_collocation("hvala")
        assert reset.reps == 0
        assert reset.lapses == 0
        assert reset.state == SRSState.NEW
        assert reset.last_review is None

    def test_suspend_then_unsuspend_flow(self, srs_db):
        srs_db.add_collocation(_unit("lep", "nice"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]

        srs_db.set_suspended(row_id, True)
        item = srs_db.get_collocation("lep")
        assert item.state == SRSState.SUSPENDED

        srs_db.set_suspended(row_id, False)
        item = srs_db.get_collocation("lep")
        assert item.state == SRSState.NEW


class TestUnsuspendRestoresState:
    """Fix 2: unsuspend must restore REVIEW for mature cards, not always NEW."""

    def _add_with_reps(self, db: SRSDatabase, text: str, reps: int, stability: float = 15.0) -> int:
        db.add_collocation(_unit(text, "trans"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]
        guid = db.get_collocation(text).guid
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=stability,
            difficulty=4.5,
            reps=reps,
            lapses=0,
            state=SRSState.REVIEW if reps > 0 else SRSState.NEW,
            dirty_fsrs=False,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)
        return row_id

    def test_unsuspend_mature_direction_restores_review(self):
        db = SRSDatabase(":memory:")
        row_id = self._add_with_reps(db, "banka", reps=5, stability=15.0)
        db.set_suspended(row_id, True, direction=Direction.RECOGNITION)
        db.set_suspended(row_id, False, direction=Direction.RECOGNITION)
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.REVIEW

    def test_unsuspend_fresh_direction_stays_new(self):
        db = SRSDatabase(":memory:")
        row_id = self._add_with_reps(db, "banka", reps=0)
        db.set_suspended(row_id, True, direction=Direction.RECOGNITION)
        db.set_suspended(row_id, False, direction=Direction.RECOGNITION)
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.NEW

    def test_unsuspend_recognition_only_leaves_production_unchanged(self):
        db = SRSDatabase(":memory:")
        row_id = self._add_with_reps(db, "banka", reps=5)
        # Suspend only recognition
        db.set_suspended(row_id, True, direction=Direction.RECOGNITION)
        prod_before = db.get_collocation("banka").directions[Direction.PRODUCTION].state
        # Unsuspend only recognition
        db.set_suspended(row_id, False, direction=Direction.RECOGNITION)
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.REVIEW
        assert item.directions[Direction.PRODUCTION].state == prod_before

    def test_unsuspend_marks_direction_dirty_fsrs(self):
        db = SRSDatabase(":memory:")
        row_id = self._add_with_reps(db, "banka", reps=5)
        db.set_suspended(row_id, True, direction=Direction.RECOGNITION)
        db.set_suspended(row_id, False, direction=Direction.RECOGNITION)
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is True

    def test_reps_and_stability_unchanged_after_unsuspend(self):
        db = SRSDatabase(":memory:")
        row_id = self._add_with_reps(db, "banka", reps=5, stability=15.0)
        db.set_suspended(row_id, True, direction=Direction.RECOGNITION)
        db.set_suspended(row_id, False, direction=Direction.RECOGNITION)
        item = db.get_collocation("banka")
        ds = item.directions[Direction.RECOGNITION]
        assert ds.reps == 5
        assert ds.stability == 15.0

    def test_unsuspend_nonexistent_direction_is_noop(self):
        db = SRSDatabase(":memory:")
        db.set_suspended(9999, False, direction=Direction.RECOGNITION)  # should not raise


class TestListCollocations:
    """Tests for the paginated list_collocations admin method."""

    def _seed(self, srs_db, texts):
        for t in texts:
            srs_db.add_collocation(_unit(t, f"trans_{t}"), language_code="sl")

    def test_list_collocations_pagination(self, srs_db):
        self._seed(srs_db, ["a", "b", "c", "d", "e"])
        rows, total = srs_db.list_collocations(limit=2, offset=2)
        assert len(rows) == 2
        assert total == 5

    def test_list_collocations_search_matches_text_or_translation(self, srs_db):
        srs_db.add_collocation(_unit("zdravo", "hello"), language_code="sl")
        srs_db.add_collocation(_unit("nasvidenje", "goodbye"), language_code="sl")
        rows, total = srs_db.list_collocations(search="hello")
        assert total == 1
        assert rows[0][1].syntactic_unit.text == "zdravo"

    def test_list_collocations_filter_by_state(self, srs_db):
        srs_db.add_collocation(_unit("a", "a"), language_code="sl")
        srs_db.add_collocation(_unit("b", "b"), language_code="sl")
        item = srs_db.get_collocation("a")
        item.state = SRSState.REVIEW
        srs_db.update_collocation(item)

        rows, total = srs_db.list_collocations(state=SRSState.REVIEW)
        assert total == 1
        assert rows[0][1].syntactic_unit.text == "a"

    def test_list_collocations_sort_by_due_date_desc(self, srs_db):
        self._seed(srs_db, ["a", "b", "c"])
        item_a = srs_db.get_collocation("a")
        item_a.due_date = date.today() - timedelta(days=5)
        srs_db.update_collocation(item_a)
        item_c = srs_db.get_collocation("c")
        item_c.due_date = date.today() + timedelta(days=5)
        srs_db.update_collocation(item_c)

        rows, _ = srs_db.list_collocations(order_by="due_date", order_dir="desc")
        texts = [r[1].syntactic_unit.text for r in rows]
        assert texts.index("c") < texts.index("a")

    def test_list_collocations_returns_total_count_independent_of_limit(self, srs_db):
        self._seed(srs_db, ["a", "b", "c", "d", "e"])
        rows, total = srs_db.list_collocations(limit=2, offset=0)
        assert total == 5
        assert len(rows) == 2

    def test_list_collocations_rejects_unknown_order_by(self, srs_db):
        import pytest

        with pytest.raises(ValueError):
            srs_db.list_collocations(order_by="injected_column")


class TestUntranslated:
    """Tests for get_untranslated_collocations."""

    def test_returns_items_with_empty_translation(self, srs_db):
        srs_db.add_collocation(_unit("zdravo", ""), language_code="sl")
        srs_db.add_collocation(_unit("hvala", "thank you"), language_code="sl")
        rows = srs_db.get_untranslated_collocations()
        texts = [r[0] for r in rows]
        assert "zdravo" in texts
        assert "hvala" not in texts

    def test_returns_empty_when_all_translated(self, srs_db):
        srs_db.add_collocation(_unit("hvala", "thank you"), language_code="sl")
        assert srs_db.get_untranslated_collocations() == []

    def test_includes_language_code(self, srs_db):
        srs_db.add_collocation(_unit("zdravo", ""), language_code="sl")
        rows = srs_db.get_untranslated_collocations()
        assert rows[0] == ("zdravo", "sl")


class TestSuspended:
    """Tests for SUSPENDED state filtering."""

    def test_suspended_items_excluded_from_due_queue(self, srs_db):
        unit = _unit("hvala", "thank you")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("hvala")
        item.due_date = date.today() - timedelta(days=1)
        item.state = SRSState.REVIEW
        srs_db.update_collocation(item)

        before = srs_db.count_due_collocations(date.today())
        assert before == 1

        item.state = SRSState.SUSPENDED
        srs_db.update_collocation(item)

        due = srs_db.get_due_collocations(date.today())
        assert not any(i.syntactic_unit.text == "hvala" for i in due)
        assert srs_db.count_due_collocations(date.today()) == 0

    def test_suspended_state_roundtrip(self, srs_db):
        unit = _unit("nasvidenje", "goodbye")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("nasvidenje")
        item.state = SRSState.SUSPENDED
        srs_db.update_collocation(item)

        retrieved = srs_db.get_collocation("nasvidenje")
        assert retrieved.state == SRSState.SUSPENDED

    def test_set_suspended_true_marks_dirty(self, srs_db):
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.set_suspended(row_id, True)
        item = srs_db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is True
        assert item.directions[Direction.PRODUCTION].dirty_fsrs is True


class TestPromoteToLearning:
    """Tests for promote_to_learning helper."""

    def test_promote_both_directions(self, srs_db):
        from datetime import date

        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.promote_to_learning(row_id)
        item = srs_db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.LEARNING
        assert item.directions[Direction.PRODUCTION].state == SRSState.LEARNING
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is True
        assert item.directions[Direction.PRODUCTION].dirty_fsrs is True
        assert item.directions[Direction.RECOGNITION].due_date == date.today()
        assert item.directions[Direction.RECOGNITION].last_review is not None

    def test_promote_single_direction(self, srs_db):
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.promote_to_learning(row_id, direction=Direction.RECOGNITION)
        item = srs_db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.LEARNING
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is True
        assert item.directions[Direction.PRODUCTION].state == SRSState.NEW
        assert item.directions[Direction.PRODUCTION].dirty_fsrs is False


class TestUntrackCollocation:
    """Tests for untrack_collocation helper."""

    def test_untrack_never_synced_deletes_row(self, srs_db):
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        result = srs_db.untrack_collocation(row_id)
        assert result == {"action": "deleted"}
        assert srs_db.get_collocation_by_id(row_id) is None
        assert srs_db.get_collocation("banka") is None

    def test_untrack_synced_row_suspends(self, srs_db):
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        with srs_db._get_conn() as conn:
            conn.execute("UPDATE collocations SET anki_note_id = 12345 WHERE id = ?", (row_id,))
            conn.commit()
        result = srs_db.untrack_collocation(row_id)
        assert result["action"] == "suspended"
        item = srs_db.get_collocation("banka")
        assert item is not None
        assert item.directions[Direction.RECOGNITION].state == SRSState.SUSPENDED
        assert item.directions[Direction.PRODUCTION].state == SRSState.SUSPENDED
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is True
        assert item.directions[Direction.PRODUCTION].dirty_fsrs is True

    def test_untrack_nonexistent_id_noop(self, srs_db):
        result = srs_db.untrack_collocation(9999)
        assert result == {"action": "deleted"}


class TestLemmaSupport:
    """Tests for lemma column and get_collocation_by_lemma."""

    def test_add_with_lemma_and_retrieve_by_lemma(self, srs_db):
        unit = SyntacticUnit(
            text="zdravo", translation="hello", word_count=1, difficulty=1, source="llm", lemma="zdravo"
        )
        srs_db.add_collocation(unit, language_code="sl")
        retrieved = srs_db.get_collocation_by_lemma("zdravo")
        assert retrieved is not None
        assert retrieved.syntactic_unit.text == "zdravo"
        assert retrieved.syntactic_unit.lemma == "zdravo"

    def test_get_by_lemma_returns_none_for_unknown(self, srs_db):
        assert srs_db.get_collocation_by_lemma("unknown_lemma") is None

    def test_add_without_lemma_not_found_by_lemma(self, srs_db):
        unit = _unit("dober dan")  # no lemma set
        srs_db.add_collocation(unit, language_code="sl")
        # lemma is NULL → get_collocation_by_lemma should not return it
        assert srs_db.get_collocation_by_lemma("dober dan") is None

    def test_init_schema_is_idempotent(self, tmp_path):
        db_path = tmp_path / "test.db"
        db1 = SRSDatabase(str(db_path))
        unit = _unit()
        db1.add_collocation(unit, language_code="sl")
        # Re-opening triggers _init_schema again (runs ALTER TABLE again — should not error)
        db2 = SRSDatabase(str(db_path))
        assert db2.get_collocation("dober dan") is not None

    def test_lemma_on_retrieved_item_without_lemma_is_none(self, srs_db):
        unit = _unit("banka")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("banka")
        assert item.syntactic_unit.lemma is None

    def test_delete_collocations_returns_zero_for_empty_list(self, srs_db):
        assert srs_db.delete_collocations([]) == 0

    def test_list_collocations_raises_for_invalid_order_dir(self, srs_db):
        import pytest

        with pytest.raises(ValueError, match="Invalid order_dir"):
            srs_db.list_collocations(order_dir="sideways")


class TestDeleteEdgeCases:
    """Tests for delete edge-case branches."""

    def test_delete_nonexistent_collocation_is_noop(self, srs_db):
        """delete_collocation with a nonexistent ID silently does nothing."""
        srs_db.delete_collocation(99999)  # should not raise

    def test_delete_collocations_with_all_nonexistent_ids(self, srs_db):
        """delete_collocations with IDs that don't match any rows returns 0."""
        deleted = srs_db.delete_collocations([99999, 88888])
        assert deleted == 0


class TestFileDatabaseWriteOperations:
    """Exercise all write methods with a file-backed DB to cover if self._in_memory: False branches."""

    def test_file_db_write_operations(self, tmp_path):
        db = SRSDatabase(str(tmp_path / "test.db"))

        # add_collocation (covers else: self._conn.commit() via False path already handled)
        db.add_collocation(_unit("zdravo", "hello"), language_code="sl")

        # update_collocation (167->exit False branch: file-DB skips self._conn.commit())
        item = db.get_collocation("zdravo")
        item.reps = 1
        db.update_collocation(item)

        # record_violation (179->exit False branch)
        db.record_violation("zdravo", 1, "unused")

        # update_collocation_fields (237->exit False branch)
        rows, _ = db.list_collocations()
        row_id = rows[0][0]
        db.update_collocation_fields(row_id, text="zdravo!", translation="hello!")

        # delete_collocation (249->exit False branch)
        db.record_violation("zdravo!", 2, "unused")
        db.delete_collocation(row_id)

        # delete_collocations (264->266 False branch)
        db.add_collocation(_unit("hvala", "thanks"), language_code="sl")
        rows, _ = db.list_collocations()
        ids = [r[0] for r in rows]
        db.delete_collocations(ids)

        # reset_collocation (281->exit False branch)
        db.add_collocation(_unit("prosim", "please"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]
        db.reset_collocation(row_id)

        # set_suspended (292->exit False branch)
        db.set_suspended(row_id, True)
        db.set_suspended(row_id, False)

        # set_state_by_id (312->exit False branch)
        db.set_state_by_id(row_id, SRSState.KNOWN)

        # backfill_translations (file-backed DB path: covers 164->166 False branch)
        db.add_collocation(_unit("hvala", ""), language_code="sl")
        n = db.backfill_translations({"hvala": "thanks", "unknown": "x", "": ""})
        assert n == 1

        # Verify persistence works
        assert db.get_collocation("prosim") is not None


# ── B5: last_rating round-trip ────────────────────────────────────────────────


def _add_banka(db: SRSDatabase) -> str:
    unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit)
    return db.get_collocation("banka").guid


class TestLastRatingPersistence:
    """B5: update_direction/list_dirty must round-trip last_rating through the DB."""

    def test_update_direction_persists_last_rating(self):
        db = SRSDatabase(":memory:")
        guid = _add_banka(db)

        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=5.0,
            difficulty=4.5,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            last_rating=2,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        dirty = db.list_dirty()
        assert len(dirty) == 1
        _, _, fetched = dirty[0]
        assert fetched.last_rating == 2

    def test_list_dirty_returns_null_last_rating_for_old_rows(self):
        """Rows without last_rating (pre-migration) come back as None."""
        db = SRSDatabase(":memory:")
        guid = _add_banka(db)

        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=5.0,
            difficulty=4.5,
            reps=3,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=True,
            last_rating=None,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        dirty = db.list_dirty()
        assert len(dirty) == 1
        _, _, fetched = dirty[0]
        assert fetched.last_rating is None


class TestQueueStatHelpers:
    """Tests for count_new_available and count_due_today_total."""

    def _seed(self, db: SRSDatabase, text: str, rec_state: SRSState, prod_state: SRSState, due_offset_days: int = 0):
        """Add one collocation and set both directions' states and due_date."""
        unit = SyntacticUnit(text=text, translation="t", word_count=2, difficulty=1, source="corpus")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation(text)
        assert item is not None
        today = date.today()
        due = today + timedelta(days=due_offset_days)
        for direction, state in [(Direction.RECOGNITION, rec_state), (Direction.PRODUCTION, prod_state)]:
            ds = DirectionState(
                direction=direction,
                due_date=due,
                stability=1.0,
                difficulty=5.0,
                reps=0 if state == SRSState.NEW else 1,
                lapses=0,
                state=state,
            )
            db.update_direction(item.guid, direction, ds)

    @pytest.mark.parametrize(
        "collocations,due_offset,expected_new,expected_due",
        [
            ([("hvala", SRSState.NEW, SRSState.NEW)], 0, 2, 0),
            ([("hvala", SRSState.SUSPENDED, SRSState.NEW)], 0, 1, 0),
            ([("hvala", SRSState.NEW, SRSState.SUSPENDED)], 0, 1, 0),
            ([("hvala", SRSState.NEW, SRSState.NEW), ("banka", SRSState.NEW, SRSState.REVIEW)], 0, 3, 1),
            ([], 0, 0, 0),
            ([("hvala", SRSState.REVIEW, SRSState.REVIEW)], 0, 0, 2),
            ([("hvala", SRSState.REVIEW, SRSState.REVIEW)], 1, 0, 0),
            ([("hvala", SRSState.SUSPENDED, SRSState.SUSPENDED)], 0, 0, 0),
            ([("hvala", SRSState.KNOWN, SRSState.KNOWN)], 0, 0, 0),
            ([("hvala", SRSState.BURIED, SRSState.BURIED)], 0, 0, 0),
            ([("hvala", SRSState.REVIEW, SRSState.NEW)], 0, 1, 1),
            ([("hvala", SRSState.REVIEW, SRSState.REVIEW), ("banka", SRSState.REVIEW, SRSState.SUSPENDED)], 0, 0, 3),
        ],
    )
    def test_queue_stats(self, collocations, due_offset, expected_new, expected_due):
        db = SRSDatabase(":memory:")
        for text, rec_state, prod_state in collocations:
            self._seed(db, text, rec_state, prod_state, due_offset_days=due_offset)
        assert db.count_new_available() == expected_new
        assert db.count_due_today_total(date.today()) == expected_due

    @pytest.mark.parametrize(
        "collocations,due_offset,expected_learning",
        [
            ([("hvala", SRSState.LEARNING, SRSState.LEARNING)], 0, 2),
            ([("hvala", SRSState.LEARNING, SRSState.REVIEW)], 0, 1),
            ([("hvala", SRSState.RELEARNING, SRSState.RELEARNING)], 0, 2),
            ([("hvala", SRSState.LEARNING, SRSState.RELEARNING)], 0, 2),
            ([("hvala", SRSState.NEW, SRSState.LEARNING)], 0, 1),
            ([("hvala", SRSState.LEARNING, SRSState.LEARNING), ("banka", SRSState.REVIEW, SRSState.NEW)], 0, 2),
            # Anki parity: queue=1 cards stay in the badge regardless of due_date.
            # FSRS scheduling a 10-min step late at night will roll due_date to
            # tomorrow; Anki still counts them.
            ([("hvala", SRSState.LEARNING, SRSState.LEARNING)], 1, 2),
            ([("hvala", SRSState.SUSPENDED, SRSState.SUSPENDED)], 0, 0),
            ([], 0, 0),
        ],
    )
    def test_count_learning_includes_relearning(self, collocations, due_offset, expected_learning):
        db = SRSDatabase(":memory:")
        for text, rec_state, prod_state in collocations:
            self._seed(db, text, rec_state, prod_state, due_offset_days=due_offset)
        assert db.count_learning() == expected_learning

    def test_count_learning_includes_pending_step(self):
        """Learning cards with future due_at are still counted (Anki deck-browser semantics).

        Anki's deck-browser learning count includes cards whose learning step
        hasn't elapsed yet (the in-countdown cards). The /review-queue endpoint
        filters by due_at for "what to show next" — the badge count is different.
        """
        from datetime import datetime

        db = SRSDatabase(":memory:")
        self._seed(db, "hvala", SRSState.LEARNING, SRSState.LEARNING, due_offset_days=0)
        item = db.get_collocation("hvala")
        now = datetime.now(tz=UTC)
        future_due_at = now + timedelta(minutes=10)
        for direction in [Direction.RECOGNITION, Direction.PRODUCTION]:
            ds = item.directions[direction]
            ds.due_at = future_due_at
        item.directions[Direction.RECOGNITION].due_at = now - timedelta(seconds=1)
        db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])
        assert db.count_learning() == 2

    @pytest.mark.parametrize(
        "collocations,due_offset,expected_review",
        [
            ([("hvala", SRSState.REVIEW, SRSState.REVIEW)], 0, 2),
            ([("hvala", SRSState.REVIEW, SRSState.LEARNING)], 0, 1),
            ([("hvala", SRSState.REVIEW, SRSState.REVIEW), ("banka", SRSState.NEW, SRSState.NEW)], 0, 2),
            ([("hvala", SRSState.REVIEW, SRSState.REVIEW)], 1, 0),  # future due date
            ([("hvala", SRSState.LEARNING, SRSState.LEARNING)], 0, 0),
            ([("hvala", SRSState.RELEARNING, SRSState.RELEARNING)], 0, 0),
            ([], 0, 0),
        ],
    )
    def test_count_review_due(self, collocations, due_offset, expected_review):
        db = SRSDatabase(":memory:")
        for text, rec_state, prod_state in collocations:
            self._seed(db, text, rec_state, prod_state, due_offset_days=due_offset)
        assert db.count_review_due(date.today()) == expected_review


class TestGetAudioFilename:
    """Tests for get_audio_filename."""

    def test_prefers_audio_forvo_over_audio_tts(self, srs_db):
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState

        dirs = {Direction.RECOGNITION: DirectionState(Direction.RECOGNITION, date.today(), state=SRSState.NEW)}
        coll_id = srs_db.upsert_by_guid(_unit("stol", "chair"), "sl", dirs)
        srs_db.add_media(
            coll_id,
            kind="audio_tts",
            filename="tts_stol.mp3",
            path="/tmp/tts_stol.mp3",
            anki_filename="tts_stol.mp3",
            sha256="t1",
            size_bytes=100,
        )
        srs_db.add_media(
            coll_id,
            kind="audio_forvo",
            filename="sl_stol.mp3",
            path="/tmp/sl_stol.mp3",
            anki_filename="sl_stol.mp3",
            sha256="f1",
            size_bytes=200,
        )
        assert srs_db.get_audio_filename(coll_id) == "sl_stol.mp3"

    def test_falls_back_to_audio_tts_when_no_forvo(self, srs_db):
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState

        dirs = {Direction.RECOGNITION: DirectionState(Direction.RECOGNITION, date.today(), state=SRSState.NEW)}
        coll_id = srs_db.upsert_by_guid(_unit("stol", "chair"), "sl", dirs)
        srs_db.add_media(
            coll_id,
            kind="audio_tts",
            filename="tts_stol.mp3",
            path="/tmp/tts_stol.mp3",
            anki_filename="tts_stol.mp3",
            sha256="t1",
            size_bytes=100,
        )
        assert srs_db.get_audio_filename(coll_id) == "tts_stol.mp3"

    def test_returns_none_when_only_image_exists(self, srs_db):
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState

        dirs = {Direction.RECOGNITION: DirectionState(Direction.RECOGNITION, date.today(), state=SRSState.NEW)}
        coll_id = srs_db.upsert_by_guid(_unit("stol", "chair"), "sl", dirs)
        srs_db.add_media(
            coll_id,
            kind="image",
            filename="stol.jpg",
            path="/tmp/stol.jpg",
            anki_filename="stol.jpg",
            sha256="i1",
            size_bytes=300,
        )
        assert srs_db.get_audio_filename(coll_id) is None

    def test_returns_none_for_unknown_collocation(self, srs_db):
        assert srs_db.get_audio_filename(99999) is None


class TestUpdateMediaFile:
    """Tests for update_media_file."""

    def test_updates_sha_and_size(self, srs_db):
        """update_media_file changes sha256 and bytes."""
        db = srs_db
        # Add a media row using add_media (which handles the transaction)
        # First need a collocation to reference
        from datetime import date

        from app.models.srs_item import Direction, DirectionState
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="test_media", translation="test", word_count=2, difficulty=1, source="test")
        dirs = {Direction.RECOGNITION: DirectionState(direction=Direction.RECOGNITION, due_date=date.today())}
        coll_id = db.upsert_by_guid(unit, "sl", dirs)
        db.add_media(
            coll_id,
            kind="audio_forvo",
            filename="test.mp3",
            path="/tmp/test.mp3",
            anki_filename="test.mp3",
            sha256="old_sha",
            size_bytes=100,
        )

        row = db.find_media_by_anki_filename("test.mp3", collocation_id=coll_id)
        assert row["sha256"] == "old_sha"
        assert row["bytes"] == 100

        db.update_media_file(row["id"], sha256="new_sha", size_bytes=200)

        updated = db.find_media_by_anki_filename("test.mp3", collocation_id=coll_id)
        assert updated["sha256"] == "new_sha"
        assert updated["bytes"] == 200

    def test_updates_nothing_for_invalid_id(self, srs_db):
        """Calling with unknown id should not raise."""
        db = srs_db
        db.update_media_file(99999, sha256="x", size_bytes=0)  # should not raise


class TestGetImageFilename:
    """Tests for get_image_filename."""

    def test_returns_image_when_one_exists(self, srs_db):
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState

        dirs = {Direction.RECOGNITION: DirectionState(Direction.RECOGNITION, date.today(), state=SRSState.NEW)}
        coll_id = srs_db.upsert_by_guid(_unit("ptica", "bird"), "sl", dirs)
        srs_db.add_media(
            coll_id,
            kind="image",
            filename="bird.jpg",
            path="/tmp/bird.jpg",
            anki_filename="bird.jpg",
            sha256="i1",
            size_bytes=300,
        )
        assert srs_db.get_image_filename(coll_id) == "bird.jpg"

    def test_returns_newest_image_when_multiple_exist(self, srs_db):
        """When a collocation has multiple images, the most recently inserted one should be returned."""
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState

        dirs = {Direction.RECOGNITION: DirectionState(Direction.RECOGNITION, date.today(), state=SRSState.NEW)}
        coll_id = srs_db.upsert_by_guid(_unit("ptica", "bird"), "sl", dirs)
        # Add first image
        srs_db.add_media(
            coll_id,
            kind="image",
            filename="img_old.jpg",
            path="/tmp/img_old.jpg",
            anki_filename="img_old.jpg",
            sha256="old",
            size_bytes=100,
        )
        # Add second (newer) image
        srs_db.add_media(
            coll_id,
            kind="image",
            filename="paste-new.jpg",
            path="/tmp/paste-new.jpg",
            anki_filename="paste-new.jpg",
            sha256="new",
            size_bytes=200,
        )
        assert srs_db.get_image_filename(coll_id) == "paste-new.jpg"

    def test_returns_none_when_no_image(self, srs_db):
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState

        dirs = {Direction.RECOGNITION: DirectionState(Direction.RECOGNITION, date.today(), state=SRSState.NEW)}
        coll_id = srs_db.upsert_by_guid(_unit("miza", "table"), "sl", dirs)
        assert srs_db.get_image_filename(coll_id) is None

    def test_returns_none_for_unknown_collocation(self, srs_db):
        assert srs_db.get_image_filename(99999) is None


class TestSourceContextFields:
    """Tests for source context fields (source_sentence, source_lesson_id, source_line_index)."""

    def test_add_collocation_with_source_context(self, srs_db):
        """Storing a unit with source context preserves all three fields."""
        unit = SyntacticUnit(
            text="kako si",
            translation="how are you",
            word_count=2,
            difficulty=1,
            source="user",
            source_sentence="Kako si? Jaz sem dobro.",
            source_lesson_id="lesson-123",
            source_line_index=5,
        )
        srs_db.add_collocation(unit, language_code="sl")
        retrieved = srs_db.get_collocation("kako si")
        assert retrieved is not None
        assert retrieved.syntactic_unit.source_sentence == "Kako si? Jaz sem dobro."
        assert retrieved.syntactic_unit.source_lesson_id == "lesson-123"
        assert retrieved.syntactic_unit.source_line_index == 5

    def test_add_collocation_without_source_context(self, srs_db):
        """Storing a unit without source context defaults to empty/None."""
        unit = SyntacticUnit(
            text="dober dan",
            translation="good day",
            word_count=2,
            difficulty=1,
            source="corpus",
        )
        srs_db.add_collocation(unit, language_code="sl")
        retrieved = srs_db.get_collocation("dober dan")
        assert retrieved.syntactic_unit.source_sentence == ""
        assert retrieved.syntactic_unit.source_lesson_id is None
        assert retrieved.syntactic_unit.source_line_index is None

    def test_source_context_round_trip_via_guid(self, srs_db):
        """Source context survives get_collocation_by_guid round-trip."""
        unit = SyntacticUnit(
            text="test phrase",
            translation="test",
            word_count=2,
            difficulty=1,
            source="user",
            source_sentence="This is a test sentence.",
            source_lesson_id="lesson-456",
            source_line_index=10,
        )
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("test phrase")
        guid = item.guid
        retrieved = srs_db.get_collocation_by_guid(guid)
        assert retrieved is not None
        assert retrieved.syntactic_unit.source_sentence == "This is a test sentence."
        assert retrieved.syntactic_unit.source_lesson_id == "lesson-456"
        assert retrieved.syntactic_unit.source_line_index == 10

    def test_list_items_without_anki_note_includes_source_context(self, srs_db):
        """list_items_without_anki_note returns items with source context."""
        unit = SyntacticUnit(
            text="nova fraza",
            translation="new phrase",
            word_count=2,
            difficulty=1,
            source="user",
            source_sentence="Nova fraza v kontekstu.",
            source_lesson_id="lesson-789",
            source_line_index=3,
        )
        srs_db.add_collocation(unit, language_code="sl")
        items = srs_db.list_items_without_anki_note()
        assert len(items) > 0
        # Find our item
        for _, item in items:
            if item.syntactic_unit.text == "nova fraza":
                assert item.syntactic_unit.source_sentence == "Nova fraza v kontekstu."
                assert item.syntactic_unit.source_lesson_id == "lesson-789"
                assert item.syntactic_unit.source_line_index == 3
                break
        else:
            pytest.fail("nova fraza not found in items without anki note")


class TestSetSentenceTranslationDirty:
    """Tests for set_sentence_translation_dirty edge cases."""

    def test_nonexistent_guid_does_not_raise(self, srs_db):
        """Calling set_sentence_translation_dirty with a non-existent guid is a no-op."""
        srs_db.set_sentence_translation_dirty(
            guid="nonexistent-guid",
            sentence_translation="some translation",
        )


class TestDatabaseURLParsing:
    """Tests for sqlite:// URL parsing in SRSDatabase."""

    def test_sqlite_url_format_parsing(self, tmp_path):
        """Test that sqlite:/// URLs are correctly parsed."""

        # Create a test database with the sqlite:/// URL format
        db_path = tmp_path / "test.db"
        url = f"sqlite:///{db_path}"

        db = SRSDatabase(url)
        with db._get_conn() as conn:
            # Should connect to the correct database, not create a new one
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_names = [t["name"] for t in tables]
            # The database should be initialized with the schema
            assert "collocations" in table_names
        db.close()

    def test_sqlite_url_with_relative_path(self, srs_db):
        """Test that relative paths in sqlite:// URLs work correctly."""
        # srs_db fixture uses :memory: which doesn't test the path parsing
        # This test ensures the parsing logic works
        from app.srs.database import SRSDatabase

        # Test with the actual format used in settings
        url = "sqlite:///./tunatale.db"
        # Just verify it doesn't raise an error
        try:
            db = SRSDatabase(url)
            # Try to connect
            with db._get_conn() as conn:
                conn.execute("SELECT 1")
            db.close()
        except Exception as e:
            pytest.fail(f"Failed to parse sqlite:/// URL: {e}")


class TestListRecentlyGradedCleanWithDirection:
    """Tests for list_recently_graded_clean with direction parameter (line 1121)."""

    def test_filters_by_direction(self, srs_db):
        """When direction is provided, only that direction is returned (line 1121)."""
        from datetime import UTC, datetime

        unit = SyntacticUnit(text="test word", translation="test", word_count=1, difficulty=1, source="corpus")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("test word")
        assert item is not None
        guid = item.guid

        # Grade recognition only
        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        srs_db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_date=grade_time.date(),
                stability=5.0,
                difficulty=4.5,
                reps=1,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=grade_time,
                last_review_time_ms=3000,
                dirty_fsrs=False,
                last_rating=3,
            ),
        )

        # list_recently_graded_clean with Direction.RECOGNITION should return it
        result = srs_db.list_recently_graded_clean(direction=Direction.RECOGNITION)
        assert len(result) == 1
        assert result[0][0] == guid
        assert result[0][1] == Direction.RECOGNITION

        # list_recently_graded_clean with Direction.PRODUCTION should not return it
        result_prod = srs_db.list_recently_graded_clean(direction=Direction.PRODUCTION)
        assert len(result_prod) == 0


class TestListRecentlyGradedCleanDueAt:
    """list_recently_graded_clean parses due_at when present (line 1156)."""

    def test_due_at_populated_when_set(self, srs_db):
        from datetime import UTC, datetime

        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(
            text="learning card",
            translation="test",
            word_count=2,
            difficulty=1,
            source="corpus",
        )
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("learning card")
        assert item is not None
        guid = item.guid

        grade_time = datetime(2026, 5, 4, 10, 0, 0, tzinfo=UTC)
        due_at = datetime(2026, 5, 4, 10, 10, 0, tzinfo=UTC)
        srs_db.update_direction(
            guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                due_date=grade_time.date(),
                stability=1.0,
                difficulty=5.0,
                reps=1,
                lapses=0,
                state=SRSState.LEARNING,
                last_review=grade_time,
                last_review_time_ms=3000,
                dirty_fsrs=False,
                last_rating=3,
                left=1002,
                due_at=due_at,
            ),
        )

        result = srs_db.list_recently_graded_clean()
        assert len(result) == 1
        assert result[0][2].due_at == due_at
        assert result[0][2].left == 1002


class TestTouchLastSyncedAtNonExistentGuid:
    """Tests for touch_last_synced_at with non-existent GUID (line 1163)."""

    def test_returns_early_for_missing_guid(self, srs_db):
        """When GUID doesn't exist, touch_last_synced_at returns early (line 1163)."""
        # Should not raise even though GUID doesn't exist
        srs_db.touch_last_synced_at("nonexistent-guid-123", Direction.RECOGNITION)
        # If we get here, it returned early (no crash on missing GUID)

    def test_actually_calls_the_function(self, srs_db):
        """Verify the function is actually called and returns early."""
        # First add a collocation so we have a valid GUID
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="test", translation="test", word_count=1, difficulty=1, source="corpus")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("test")
        assert item is not None

        # Call with non-existent GUID - should return early (line 1163-1164)
        srs_db.touch_last_synced_at("totally-fake-guid", Direction.RECOGNITION)

        # Call with valid GUID - should NOT return early (lines 1165-1173)
        srs_db.touch_last_synced_at(item.guid, Direction.RECOGNITION)
        # If we get here without error, the function works


class TestMigrateV9toV10ColumnExists:
    """Tests for migrate_v9_to_v10 when column already exists (line 418->422)."""

    def test_migrate_skips_if_column_exists(self):
        """When last_review_time_ms already exists, migration skips ALTER (line 418)."""
        import sqlite3

        from app.srs.migrations import migrate_v9_to_v10

        # Create a DB that already has the column (simulating already-migrated state)
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE collocation_directions (id INTEGER PRIMARY KEY)")
        conn.execute("ALTER TABLE collocation_directions ADD COLUMN last_review_time_ms INTEGER NOT NULL DEFAULT 0")
        conn.commit()

        # Migration should not fail even though column exists
        migrate_v9_to_v10(conn)

        # Verify version is set
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 10  # specifically tests v9→v10 idempotence
        conn.close()
