"""SRS database tests."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from tests.conftest import anki_day_anchor, anki_prev_day_anchor


def _unit(text: str = "dober dan", translation: str = "good day") -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=2, difficulty=1, source="corpus")


def _id_for_text(srs_db, text: str) -> int:
    """Resolve a collocation id by its ``text`` (``_unit`` leaves ``lemma`` unset)."""
    with srs_db._get_conn() as conn:
        return conn.execute("SELECT id FROM collocations WHERE text = ?", (text,)).fetchone()[0]


class TestCRUD:
    """Tests for basic add/get/update collocation operations."""

    def test_add_and_get_collocation(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        retrieved = srs_db.get_collocation("dober dan")
        assert retrieved is not None
        assert retrieved.syntactic_unit.text == "dober dan"

    def test_article_round_trips(self, srs_db):
        unit = SyntacticUnit(text="orden", translation="order", word_count=1, difficulty=1, source="anki", article="en")
        srs_db.add_collocation(unit, language_code="no")
        retrieved = srs_db.get_collocation("orden")
        assert retrieved is not None
        assert retrieved.syntactic_unit.article == "en"

    def test_article_defaults_to_empty(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        assert srs_db.get_collocation("dober dan").syntactic_unit.article == ""

    def test_set_article_updates_existing_row(self, srs_db):
        srs_db.add_collocation(
            SyntacticUnit(text="orden", translation="order", word_count=1, difficulty=1, source="anki"),
            language_code="no",
        )
        coll_id = _id_for_text(srs_db, "orden")
        srs_db.set_article(coll_id, "en")
        assert srs_db.get_collocation("orden").syntactic_unit.article == "en"

    def test_set_article_is_idempotent_and_overwrites(self, srs_db):
        srs_db.add_collocation(
            SyntacticUnit(text="orden", translation="order", word_count=1, difficulty=1, source="anki", article="en"),
            language_code="no",
        )
        coll_id = _id_for_text(srs_db, "orden")
        srs_db.set_article(coll_id, "ei")
        assert srs_db.get_collocation("orden").syntactic_unit.article == "ei"

    def test_extras_round_trip(self, srs_db):
        from app.models.syntactic_unit import BackField

        extras = (
            BackField(label="IPA", html="/ˈʋæːɾə/", tier="summary"),
            BackField(label="Inflections", html="<table><tr><td>er</td></tr></table>", tier="details"),
        )
        unit = SyntacticUnit(text="være", translation="to be", word_count=1, difficulty=1, source="anki", extras=extras)
        srs_db.add_collocation(unit, language_code="no")
        assert srs_db.get_collocation("være").syntactic_unit.extras == extras

    def test_extras_defaults_to_empty(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        assert srs_db.get_collocation("dober dan").syntactic_unit.extras == ()

    def test_upsert_by_guid_round_trips_extras(self, srs_db):
        from app.models.syntactic_unit import BackField

        extras = (BackField(label="Meaning", html="exist", tier="summary"),)
        unit = SyntacticUnit(text="være", translation="to be", word_count=1, difficulty=1, source="anki", extras=extras)
        srs_db.upsert_by_guid(unit, "no", {})
        assert srs_db.get_collocation("være").syntactic_unit.extras == extras

    def test_update_collocation_for_sync_heals_extras(self, srs_db):
        from app.models.syntactic_unit import BackField, serialize_extras

        srs_db.add_collocation(
            SyntacticUnit(text="være", translation="to be", word_count=1, difficulty=1, source="anki"),
            language_code="no",
        )
        guid = srs_db.get_collocation("være").guid
        new_extras = (BackField(label="IPA", html="/ˈʋæːɾə/", tier="summary"),)
        srs_db.update_collocation_for_sync(
            guid,
            translation="to be",
            note="",
            dirty_fields_str="",
            extras=serialize_extras(new_extras),
        )
        assert srs_db.get_collocation("være").syntactic_unit.extras == new_extras

    def test_update_collocation_for_sync_leaves_extras_when_none(self, srs_db):
        from app.models.syntactic_unit import BackField

        existing = (BackField(label="IPA", html="/ˈʋæːɾə/", tier="summary"),)
        srs_db.add_collocation(
            SyntacticUnit(text="være", translation="to be", word_count=1, difficulty=1, source="anki", extras=existing),
            language_code="no",
        )
        guid = srs_db.get_collocation("være").guid
        # extras omitted (None) → stored extras untouched even as translation changes.
        srs_db.update_collocation_for_sync(guid, translation="be", note="", dirty_fields_str="")
        retrieved = srs_db.get_collocation("være")
        assert retrieved.syntactic_unit.translation == "be"
        assert retrieved.syntactic_unit.extras == existing


class TestVariantHelpers:
    """DB support for comma-separated spelling-variant cards (Norwegian 'mot, imot')."""

    def test_get_variant_candidates_with_items_returns_hydrated_separator_rows(self, srs_db):
        """Scans and hydrates in ONE query: (id, text, item) per separator row,
        no scan→refetch window (the old two-step shape needed a dead
        "row vanished between queries" branch on the caller)."""
        srs_db.add_collocation(
            SyntacticUnit(text="mot, imot", translation="against", word_count=2, difficulty=1, source="anki"),
            language_code="no",
        )
        srs_db.add_collocation(
            SyntacticUnit(text="politiet", translation="the police", word_count=1, difficulty=1, source="anki"),
            language_code="no",
        )
        results = srs_db.get_variant_candidates_with_items("no", ",")
        assert len(results) == 1
        cid, text, item = results[0]
        assert isinstance(cid, int)
        assert text == "mot, imot"
        assert item.syntactic_unit.text == "mot, imot"
        assert item.directions  # hydrated, not a bare row

    def test_get_variant_candidates_with_items_scoped_by_language(self, srs_db):
        srs_db.add_collocation(
            SyntacticUnit(text="mot, imot", translation="against", word_count=2, difficulty=1, source="anki"),
            language_code="no",
        )
        assert srs_db.get_variant_candidates_with_items("sl", ",") == []


class TestAmbiguousSurfaces:
    """get_ambiguous_surfaces returns casefolded surfaces with >=2 distinct POS."""

    def _add(self, srs_db, text, pos, lang="no", card_type="vocab"):
        srs_db.add_collocation(
            SyntacticUnit(
                text=text,
                translation="x",
                word_count=1,
                difficulty=1,
                source="anki",
                disambig_key=pos,
                card_type=card_type,
            ),
            language_code=lang,
        )

    def test_surface_with_two_pos_is_ambiguous(self, srs_db):
        self._add(srs_db, "fange", "noun")
        self._add(srs_db, "fange", "verb")
        self._add(srs_db, "bil", "noun")
        assert srs_db.get_ambiguous_surfaces("no") == {"fange"}

    def test_casefold_groups_norwegian_surfaces(self, srs_db):
        self._add(srs_db, "Vår", "noun")
        self._add(srs_db, "vår", "determiner")
        assert srs_db.get_ambiguous_surfaces("no") == {"vår"}

    def test_excludes_other_language_blank_pos_and_morph_clozes(self, srs_db):
        self._add(srs_db, "fange", "noun")
        self._add(srs_db, "fange", "verb", lang="sl")  # other language — not counted with the 'no' noun
        self._add(srs_db, "tom", "")  # blank POS ignored
        self._add(srs_db, "tom", "noun")
        self._add(srs_db, "gå", "morph:verb-pres", card_type="cloze")  # cloze morph key ignored
        self._add(srs_db, "gå", "verb")
        assert srs_db.get_ambiguous_surfaces("no") == set()

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

    def test_update_direction_round_trips_fsrs_force_next(self, srs_db):
        """update_direction then _load_directions round-trips fsrs_force_next.

        Guards the bury_kind-incident surface: the flag must land in all of
        _DIR_COLUMNS, the DirectionState field, the row→DirectionState
        construction, and update_direction's writer — miss one and the force
        silently reads back False.
        """
        unit = _unit("test_word", "test")
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("test_word")
        guid = item.guid
        rec_dir = item.directions[Direction.RECOGNITION]
        assert rec_dir.fsrs_force_next is False  # default
        rec_dir.fsrs_force_next = True
        srs_db.update_direction(guid, Direction.RECOGNITION, rec_dir)
        reloaded = srs_db.get_collocation("test_word")
        assert reloaded.directions[Direction.RECOGNITION].fsrs_force_next is True

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

    def test_upsert_by_guid_reps_gt_zero_refreshes_due_date(self, srs_db):
        """Invariant: a row's `due_date` and `anki_due` describe the SAME
        scheduling moment. When upsert_by_guid runs against an existing direction
        with reps>0, the reps>0 branch refreshes Anki-bookkeeping fields (state,
        anki_card_id, anki_due, left, due_at) but historically left `due_date`
        pinned to the first-import value.

        On a deck synced over weeks, this manifests as 100+ review rows whose
        `anki_due` advances correctly each sync while `due_date` stays at a
        long-past date. The badge query (count_review_due_collocations) filters
        on `due_date`, so every one of those rows is wrongly counted as due
        today, every day, until graded or backfilled.
        """
        unit = SyntacticUnit(
            text="banka",
            translation="bank",
            word_count=1,
            difficulty=1,
            source="anki",
            lemma="banka",
        )
        initial_due = date(2026, 5, 17)
        srs_db.upsert_by_guid(
            unit,
            "sl",
            {
                Direction.RECOGNITION: DirectionState(
                    direction=Direction.RECOGNITION,
                    due_at=datetime.combine(initial_due, time(4, 0), tzinfo=UTC),
                    stability=10.0,
                    difficulty=5.0,
                    reps=3,
                    state=SRSState.REVIEW,
                    anki_card_id=999,
                    anki_due=4516,
                )
            },
            anki_note_id=9001,
        )

        # Anki has since graded the card, advancing cards.due to 4526 and
        # stability to 15.0. Anki-side due_date is 2026-05-27. A re-import
        # (or any future upsert_by_guid pass) must propagate due_date,
        # NOT pin it at the original 2026-05-17.
        advanced_due = date(2026, 5, 27)
        srs_db.upsert_by_guid(
            unit,
            "sl",
            {
                Direction.RECOGNITION: DirectionState(
                    direction=Direction.RECOGNITION,
                    due_at=datetime.combine(advanced_due, time(4, 0), tzinfo=UTC),
                    stability=15.0,
                    difficulty=5.2,
                    reps=4,
                    state=SRSState.REVIEW,
                    anki_card_id=999,
                    anki_due=4526,
                )
            },
            anki_note_id=9001,
        )

        item = srs_db.get_collocation("banka")
        assert item is not None
        rec = item.directions[Direction.RECOGNITION]
        assert rec.anki_due == 4526, "anki_due refresh path is the obvious one"
        assert rec.due_at.date() == advanced_due, (
            f"due_date must follow anki_due. Got {rec.due_at.date()}, "
            f"expected {advanced_due}. Bug: reps>0 branch ignores due_date."
        )

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
                    due_at=datetime.combine(orig.due_at.date(), time(4, 0), tzinfo=UTC),
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
                due_at=datetime.combine(orig.due_at.date(), time(4, 0), tzinfo=UTC),
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

    def test_get_new_items_production_held_until_recognition_graduates(self, srs_db):
        """Phase 3 introduction gate: a PRODUCTION new card is withheld while its
        recognition sibling is still new/learning/relearning, and released once
        recognition reaches review (or there is no recognition sibling — cloze).

        This makes TT introduce recognition before production, matching Anki:
        Anki is direction-agnostic and orders new cards by deck position
        (recognition cards are created at a lower position than production), so
        recognition surfaces first. The recognition direction is never gated.
        See docs/anki-parity-layers.md.
        """

        def _add_paired(text: str, rec_state: SRSState) -> None:
            srs_db.add_collocation(_unit(text, text), language_code="sl")
            rows, _ = srs_db.list_collocations(search=text, limit=1)
            row_id, _, _ = rows[0]
            srs_db.update_direction_by_id(
                row_id,
                Direction.RECOGNITION,
                DirectionState(
                    direction=Direction.RECOGNITION,
                    state=rec_state,
                    due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                ),
            )

        _add_paired("paired_new", SRSState.NEW)
        _add_paired("paired_learning", SRSState.LEARNING)
        _add_paired("paired_review", SRSState.REVIEW)
        # Cloze note: production-only, no recognition sibling to gate on.
        srs_db.add_collocation(
            SyntacticUnit(
                text="vcloze",
                translation="",
                word_count=1,
                difficulty=1,
                source="llm",
                card_type="cloze",
                source_sentence="{{c1::vcloze}} doma.",
            ),
            language_code="sl",
        )

        prod = {
            item.syntactic_unit.text for _, item, _ in srs_db.get_new_items(direction=Direction.PRODUCTION, limit=50)
        }
        assert "paired_review" in prod  # recognition graduated → production introducible
        assert "vcloze" in prod  # cloze → no recognition sibling → always introducible
        assert "paired_new" not in prod  # recognition still NEW → held
        assert "paired_learning" not in prod  # recognition in learning → held

        # The recognition direction is never gated.
        rec = {
            item.syntactic_unit.text for _, item, _ in srs_db.get_new_items(direction=Direction.RECOGNITION, limit=50)
        }
        assert "paired_new" in rec

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
                    due_at=datetime.combine(orig.due_at.date(), time(4, 0), tzinfo=UTC),
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
                due_at=datetime.combine(today - timedelta(days=days_ago), time(4, 0), tzinfo=UTC),
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
                due_at=datetime.combine(due_date, time(4, 0), tzinfo=UTC),
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

    def test_set_state_by_id_to_new_resets_schedule(self, srs_db):
        """Reset-to-NEW clears the FSRS schedule, not just the state label.

        Regression (stuck reset): the popover "Reset" → set_state_by_id(NEW) used
        to flip state='new' while preserving the card's graduated due_at /
        last_review / reps / stability. The transcript then showed the card red
        (mastery keys off state=NEW) but NOT due (is_due keys off the stale future
        due_at), so a plain click hit the no-op branch — stuck red and unclickable.
        A reset must yield a fresh NEW card: due today, no review history, so it is
        re-learnable. Schedule columns mirror reset_collocation; dirty_fsrs stays
        set so the reset syncs to Anki.
        """
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        # Simulate a graduated card: future due_at + review history on both directions.
        future = datetime(2099, 1, 1, 4, 0, tzinfo=UTC)
        last = datetime(2026, 6, 2, 20, 59, tzinfo=UTC)
        with srs_db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET state='review', due_at=?, last_review=?,"
                " reps=2, lapses=1, stability=4.47, fsrs_difficulty=5.27 WHERE collocation_id=?",
                (future.isoformat(), last.isoformat(), row_id),
            )
            conn.commit()

        srs_db.set_state_by_id(row_id, SRSState.NEW)

        item = srs_db.get_collocation("banka")
        today_due = datetime.combine(date.today(), time(4, 0), tzinfo=UTC)
        for d in (Direction.RECOGNITION, Direction.PRODUCTION):
            ds = item.directions[d]
            assert ds.state == SRSState.NEW
            assert ds.due_at == today_due  # due today → is_due True → clickable again
            assert ds.last_review is None
            assert ds.reps == 0
            assert ds.lapses == 0
            assert ds.stability == 1.0
            assert ds.difficulty == 5.0
            assert ds.introduced_at is None
            assert ds.prior_state is None
            assert ds.dirty_fsrs is True

    def test_set_state_by_id_to_non_new_preserves_schedule(self, srs_db):
        """Label-only states (review/known/…) must NOT reset the schedule (srs.py:685).

        Guards the invariant that the NEW-reset path is the *only* one that touches
        FSRS columns — cycling a card to `known`/`review` keeps its real schedule.
        """
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        future = datetime(2099, 1, 1, 4, 0, tzinfo=UTC)
        last = datetime(2026, 6, 2, 20, 59, tzinfo=UTC)
        with srs_db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET state='review', due_at=?, last_review=?,"
                " reps=2, lapses=1, stability=4.47 WHERE collocation_id=?",
                (future.isoformat(), last.isoformat(), row_id),
            )
            conn.commit()
        srs_db.set_state_by_id(row_id, SRSState.KNOWN)
        item = srs_db.get_collocation("banka")
        for d in (Direction.RECOGNITION, Direction.PRODUCTION):
            ds = item.directions[d]
            assert ds.state == SRSState.KNOWN
            assert ds.due_at == future
            assert ds.last_review == last
            assert ds.reps == 2
            assert ds.stability == 4.47

    def test_set_state_by_id_stamps_introduced_at_entering_review_flow(self, srs_db):
        """A never-introduced NEW card forced into the review/learning flow must
        stamp introduced_at so count_new_introduced_today stays consistent (finding #8).

        Without the stamp the card leaves the new pool but the new-introduced quota
        never decrements (introduced_at NULL → count_new_introduced_today ignores it),
        and a review card with no FSRS history can surface with the quota miscounted.
        """
        today = date.today()
        for state in (SRSState.REVIEW, SRSState.LEARNING, SRSState.KNOWN):
            text = f"flow {state.value}"
            srs_db.add_collocation(_unit(text, "x"), language_code="sl")
            item = srs_db.get_collocation(text)
            row_id = _id_for_text(srs_db, text)
            # Fresh NEW card: no introduced_at yet.
            assert item.directions[Direction.RECOGNITION].introduced_at is None

            srs_db.set_state_by_id(row_id, state)

            refreshed = srs_db.get_collocation(text)
            for d in (Direction.RECOGNITION, Direction.PRODUCTION):
                assert refreshed.directions[d].state == state
                assert refreshed.directions[d].introduced_at is not None
        # Each of the three collocations counts once toward today's introductions.
        assert srs_db.count_new_introduced_today(today) == 3

    def test_set_state_by_id_suspended_does_not_stamp_introduced_at(self, srs_db):
        """Suspending a never-introduced NEW card is not an introduction — leave
        introduced_at NULL so it does not inflate count_new_introduced_today."""
        today = date.today()
        srs_db.add_collocation(_unit("paused", "x"), language_code="sl")
        row_id = _id_for_text(srs_db, "paused")

        srs_db.set_state_by_id(row_id, SRSState.SUSPENDED)

        item = srs_db.get_collocation("paused")
        for d in (Direction.RECOGNITION, Direction.PRODUCTION):
            assert item.directions[d].state == SRSState.SUSPENDED
            assert item.directions[d].introduced_at is None
        assert srs_db.count_new_introduced_today(today) == 0

    def test_set_state_by_id_preserves_existing_introduced_at(self, srs_db):
        """introduced_at is a one-shot stamp (Layer 26): a card already introduced
        on a prior day keeps its original timestamp when re-stated, not today's."""
        srs_db.add_collocation(_unit("already", "x"), language_code="sl")
        row_id = _id_for_text(srs_db, "already")
        stamp = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        with srs_db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET introduced_at=? WHERE collocation_id=?",
                (stamp.isoformat(), row_id),
            )
            conn.commit()

        srs_db.set_state_by_id(row_id, SRSState.REVIEW)

        item = srs_db.get_collocation("already")
        for d in (Direction.RECOGNITION, Direction.PRODUCTION):
            assert item.directions[d].introduced_at == stamp

    def test_mark_known_writes_far_future_schedule(self, srs_db):
        """mark_known sets due_at to today + 36500, matched stability, dirty_fsrs."""

        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        due_date = date.today() + timedelta(days=36500)
        due_at = datetime.combine(due_date, time(4, 0), tzinfo=UTC)
        srs_db.mark_known(row_id, due_at=due_at, stability=36500.0)

        item = srs_db.get_collocation("banka")
        for d in (Direction.RECOGNITION, Direction.PRODUCTION):
            ds = item.directions[d]
            assert ds.state == SRSState.KNOWN
            assert ds.due_at == due_at
            assert abs(ds.stability - 36500.0) < 0.01
            assert ds.dirty_fsrs is True

    def test_mark_known_stamps_introduced_at(self, srs_db):
        """A never-introduced card gets introduced_at stamped (COALESCE)."""
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        item = srs_db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].introduced_at is None

        due_date = date.today() + timedelta(days=36500)
        due_at = datetime.combine(due_date, time(4, 0), tzinfo=UTC)
        srs_db.mark_known(row_id, due_at=due_at, stability=36500.0)

        refreshed = srs_db.get_collocation("banka")
        for d in (Direction.RECOGNITION, Direction.PRODUCTION):
            assert refreshed.directions[d].introduced_at is not None

    def test_mark_known_preserves_existing_introduced_at(self, srs_db):
        """introduced_at is a one-shot stamp: existing stamp is preserved."""
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        stamp = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        with srs_db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET introduced_at=? WHERE collocation_id=?",
                (stamp.isoformat(), row_id),
            )
            conn.commit()

        due_date = date.today() + timedelta(days=36500)
        due_at = datetime.combine(due_date, time(4, 0), tzinfo=UTC)
        srs_db.mark_known(row_id, due_at=due_at, stability=36500.0)

        refreshed = srs_db.get_collocation("banka")
        for d in (Direction.RECOGNITION, Direction.PRODUCTION):
            assert refreshed.directions[d].introduced_at == stamp

    def test_mark_known_targets_specific_direction(self, srs_db):
        """When direction is provided, only that direction is marked."""
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        due_date = date.today() + timedelta(days=36500)
        due_at = datetime.combine(due_date, time(4, 0), tzinfo=UTC)
        srs_db.mark_known(row_id, due_at=due_at, stability=36500.0, direction=Direction.RECOGNITION)

        item = srs_db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.KNOWN
        assert item.directions[Direction.PRODUCTION].state != SRSState.KNOWN

    def _snapshot_cols(self, srs_db, row_id, direction):
        with srs_db._get_conn() as conn:
            return conn.execute(
                "SELECT known_prior_state, known_prior_stability, known_prior_due_at, fsrs_force_next "
                "FROM collocation_directions WHERE collocation_id = ? AND direction = ?",
                (row_id, direction.value),
            ).fetchone()

    def _set_review(self, srs_db, text, stability, due_at):
        """Push a freshly-added collocation's recognition direction into review."""
        item = srs_db.get_collocation(text)
        ds = item.directions[Direction.RECOGNITION]
        ds.state = SRSState.REVIEW
        ds.stability = stability
        ds.due_at = due_at
        srs_db.update_direction(item.guid, Direction.RECOGNITION, ds)

    def test_mark_known_snapshots_prior_schedule(self, srs_db):
        """mark_known captures the pre-known state/stability/due_at on entry."""
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        prior_due = datetime(2026, 3, 1, 4, 0, tzinfo=UTC)
        self._set_review(srs_db, "banka", 7.5, prior_due)

        known_due = datetime.combine(date.today() + timedelta(days=36500), time(4, 0), tzinfo=UTC)
        srs_db.mark_known(row_id, due_at=known_due, stability=36500.0, direction=Direction.RECOGNITION)

        snap = self._snapshot_cols(srs_db, row_id, Direction.RECOGNITION)
        assert snap["known_prior_state"] == "review"
        assert abs(snap["known_prior_stability"] - 7.5) < 0.01
        assert datetime.fromisoformat(snap["known_prior_due_at"]) == prior_due

    def test_mark_known_double_mark_preserves_snapshot(self, srs_db):
        """A second mark_known must NOT clobber the real snapshot with inflated values."""
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        prior_due = datetime(2026, 3, 1, 4, 0, tzinfo=UTC)
        self._set_review(srs_db, "banka", 7.5, prior_due)

        known_due = datetime.combine(date.today() + timedelta(days=36500), time(4, 0), tzinfo=UTC)
        srs_db.mark_known(row_id, due_at=known_due, stability=36500.0, direction=Direction.RECOGNITION)
        # Second mark while already known — snapshot must stay the first (real) values.
        srs_db.mark_known(row_id, due_at=known_due, stability=36500.0, direction=Direction.RECOGNITION)

        snap = self._snapshot_cols(srs_db, row_id, Direction.RECOGNITION)
        assert snap["known_prior_state"] == "review"
        assert abs(snap["known_prior_stability"] - 7.5) < 0.01
        assert datetime.fromisoformat(snap["known_prior_due_at"]) == prior_due

    def test_is_known_marked_true_after_mark_false_after_restore(self, srs_db):
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        assert srs_db.is_known_marked(row_id) is False

        known_due = datetime.combine(date.today() + timedelta(days=36500), time(4, 0), tzinfo=UTC)
        srs_db.mark_known(row_id, due_at=known_due, stability=36500.0)
        assert srs_db.is_known_marked(row_id) is True

        srs_db.restore_known(row_id)
        assert srs_db.is_known_marked(row_id) is False

    def test_restore_known_restores_schedule_and_sets_force(self, srs_db):
        """restore_known writes the snapshot back, clears it, sets dirty + force."""
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        prior_due = datetime(2026, 3, 1, 4, 0, tzinfo=UTC)
        self._set_review(srs_db, "banka", 7.5, prior_due)
        known_due = datetime.combine(date.today() + timedelta(days=36500), time(4, 0), tzinfo=UTC)
        srs_db.mark_known(row_id, due_at=known_due, stability=36500.0, direction=Direction.RECOGNITION)

        srs_db.restore_known(row_id, direction=Direction.RECOGNITION)

        item = srs_db.get_collocation("banka")
        ds = item.directions[Direction.RECOGNITION]
        assert ds.state == SRSState.REVIEW
        assert abs(ds.stability - 7.5) < 0.01
        assert ds.due_at == prior_due
        assert ds.dirty_fsrs is True
        assert ds.fsrs_force_next is True
        # Snapshot cleared.
        snap = self._snapshot_cols(srs_db, row_id, Direction.RECOGNITION)
        assert snap["known_prior_state"] is None
        assert snap["known_prior_stability"] is None
        assert snap["known_prior_due_at"] is None

    def test_restore_known_noop_without_snapshot(self, srs_db):
        """restore_known on a card never marked known leaves state untouched."""
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        prior_due = datetime(2026, 3, 1, 4, 0, tzinfo=UTC)
        self._set_review(srs_db, "banka", 7.5, prior_due)

        srs_db.restore_known(row_id)

        item = srs_db.get_collocation("banka")
        ds = item.directions[Direction.RECOGNITION]
        assert ds.state == SRSState.REVIEW
        assert ds.fsrs_force_next is False

    def test_mark_direction_clean_clears_fsrs_force_next(self, srs_db):
        """The force flag is one-shot: mark_direction_clean drops it after push."""
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        item = srs_db.get_collocation("banka")
        guid = item.guid
        ds = item.directions[Direction.RECOGNITION]
        ds.fsrs_force_next = True
        ds.dirty_fsrs = True
        srs_db.update_direction(guid, Direction.RECOGNITION, ds)

        srs_db.mark_direction_clean(guid, Direction.RECOGNITION)

        reloaded = srs_db.get_collocation("banka")
        rec = reloaded.directions[Direction.RECOGNITION]
        assert rec.fsrs_force_next is False
        assert rec.dirty_fsrs is False

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
                due_at=datetime.combine(orig.due_at.date(), time(4, 0), tzinfo=UTC),
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

    def test_unbury_idempotent_across_midnight_same_anki_day(self, srs_db):
        """unbury_if_needed(anki_today(now)) keyed on the Anki-day date is
        idempotent across local midnight. A call at 23:00 (Anki day D) sets
        last_unbury_day=D; a call at 02:00 the same Anki day (D, because 4 AM
        rollover hasn't hit yet) must return 0 and touch nothing.

        The pre-fix bug: using date.today() at 02:00 would see calendar day D+1,
        missing the cache and re-firing the sweep.
        """
        from app.srs.anki_mirror.rollover import anki_today

        # "now" = 23:00 on day D-1 — sets cached day to Anki day D.
        first_now = datetime(2026, 5, 7, 23, 0, tzinfo=UTC)
        today_d = anki_today(first_now)

        srs_db.add_collocation(_unit("midnight_crossing", "x"), language_code="sl")
        self._bury_direction(srs_db, "midnight_crossing", Direction.RECOGNITION, reps=2)

        first_count = srs_db.unbury_if_needed(today_d)
        assert first_count == 1

        # Re-bury for the second call scenario.
        self._bury_direction(srs_db, "midnight_crossing", Direction.RECOGNITION, reps=2)

        # "now" = 02:00 on day D — SAME Anki day D (before 4 AM rollover).
        second_now = datetime(2026, 5, 8, 2, 0, tzinfo=UTC)
        today_d_same = anki_today(second_now)
        assert today_d_same == today_d, "same Anki day before rollover"

        second_count = srs_db.unbury_if_needed(today_d_same)
        assert second_count == 0, "must be idempotent within the same Anki day"

        # Sanity: the buried row was NOT released (today's bury is preserved).
        item = srs_db.get_collocation("midnight_crossing")
        assert item.directions[Direction.RECOGNITION].state == SRSState.BURIED

        # Counter-case: using calendar date.today() would see a DIFFERENT day
        # at 02:00 and re-fire the sweep.
        srs_db.delete_anki_state_cache("last_unbury_day")
        calendar_today_d_plus_1 = date(2026, 5, 8)
        refire_count = srs_db.unbury_if_needed(calendar_today_d_plus_1)
        assert refire_count == 1, "calendar flip re-fires the sweep (the pre-fix bug)"


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
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
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
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
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
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
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
        import time as _time
        from datetime import datetime

        monkeypatch.setenv("TZ", "America/Los_Angeles")
        _time.tzset()

        srs_db.add_collocation(_unit("late_evening"), language_code="sl")
        rows, _ = srs_db.list_collocations(search="late_evening", limit=1)
        late_id, late_item, _ = rows[0]
        srs_db.add_collocation(_unit("early_morning"), language_code="sl")
        rows, _ = srs_db.list_collocations(search="early_morning", limit=1)
        early_id, early_item, _ = rows[0]

        today_local = date(2026, 5, 8)
        # 23:30 PDT on May 8 = 06:30 UTC on May 9. UTC date = May 9, but it falls inside
        # May 8's Anki day ([May 8 4 AM, May 9 4 AM) local) — must bucket to May 8.
        late_utc = datetime(2026, 5, 9, 6, 30, tzinfo=UTC)
        # 09:30 PDT on May 8 = 16:30 UTC on May 8 — a normal post-rollover morning,
        # squarely inside May 8's Anki day. Control: UTC and local dates agree.
        # (A pre-4 AM review would bucket to the prior Anki day — see
        # TestAnkiRolloverDayBoundary.)
        early_utc = datetime(2026, 5, 8, 16, 30, tzinfo=UTC)

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
                    due_at=datetime.combine(today_local, time(4, 0), tzinfo=UTC),
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


class TestAnkiRolloverDayBoundary:
    """The 'today' window for graded-today / introduced-today / reviews-today
    must use Anki's 4 AM *local* rollover, not local midnight.

    Anki rolls the day over at `rollover` (default 4 AM local), so a grade
    timestamped between local midnight and 4 AM belongs to the PRIOR Anki day.
    `app.plugins.anki_sync.sync._local_today_4am` already does this for sync-side counts; the
    `database.py` badge counts must agree or TT under-counts the review badge by
    sibling-burying cards Anki considers "graded yesterday" (the 66-vs-73 bug,
    2026-06-02).
    """

    def test_anki_day_bounds_shifts_back_before_rollover(self, monkeypatch):
        """Before today's 4 AM local, the active Anki day starts at yesterday's
        4 AM; at/after 4 AM it starts at today's."""
        import time as _time

        from app.srs.database import _anki_day_bounds_utc

        monkeypatch.setenv("TZ", "America/Los_Angeles")
        _time.tzset()
        today = date(2026, 5, 8)

        # 02:00 PDT (before 4 AM rollover) → window anchored on May 7.
        start_before, end_before = _anki_day_bounds_utc(today, now=datetime(2026, 5, 8, 2, 0, tzinfo=UTC))
        # 09:00 PDT (after rollover) → window anchored on May 8.
        start_after, end_after = _anki_day_bounds_utc(today, now=datetime(2026, 5, 8, 16, 0, tzinfo=UTC))

        # 4 AM PDT == 11:00 UTC. May 8's day runs [May 8 11:00 UTC, May 9 11:00 UTC).
        assert start_after == datetime(2026, 5, 8, 11, 0, tzinfo=UTC).isoformat()
        assert end_after == datetime(2026, 5, 9, 11, 0, tzinfo=UTC).isoformat()
        # Before rollover, shifted back one day.
        assert start_before == datetime(2026, 5, 7, 11, 0, tzinfo=UTC).isoformat()
        assert end_before == datetime(2026, 5, 8, 11, 0, tzinfo=UTC).isoformat()

    def _make_review_pair(self, srs_db, text, rec_last_review):
        """Dual review-due collocation; recognition graded at `rec_last_review`,
        production graded long ago. Returns the collocation row id."""
        srs_db.add_collocation(_unit(text), language_code="sl")
        rows, _ = srs_db.list_collocations(search=text, limit=1)
        row_id, item, _ = rows[0]
        due = datetime(2026, 5, 8, 4, 0, tzinfo=UTC)  # due May 8
        for direction, last_review in (
            (Direction.RECOGNITION, rec_last_review),
            (Direction.PRODUCTION, datetime(2026, 4, 20, 18, 0, tzinfo=UTC)),
        ):
            orig = item.directions[direction]
            srs_db.update_direction_by_id(
                row_id,
                direction,
                DirectionState(
                    direction=direction,
                    state=SRSState.REVIEW,
                    due_at=due,
                    stability=orig.stability,
                    difficulty=orig.difficulty,
                    reps=5,
                    lapses=0,
                    last_review=last_review,
                    last_rating=3,
                ),
            )
        return row_id

    def test_review_graded_before_rollover_is_not_buried(self, srs_db, monkeypatch):
        """A sibling graded at 01:00 local (before 4 AM) is 'yesterday' for Anki,
        so the review-due note is NOT sibling-buried and IS counted (66→73)."""
        import time as _time

        monkeypatch.setenv("TZ", "America/Los_Angeles")
        _time.tzset()
        # 01:00 PDT May 8 == 08:00 UTC May 8: after local midnight, before 4 AM.
        self._make_review_pair(srs_db, "pred_rollover", datetime(2026, 5, 8, 8, 0, tzinfo=UTC))
        assert srs_db.count_review_due_collocations(date(2026, 5, 8)) == 1

    def test_review_graded_after_rollover_is_buried(self, srs_db, monkeypatch):
        """A sibling graded at 09:00 local (after 4 AM) IS 'today' for Anki, so
        the review-due note is sibling-buried and NOT counted (boundary guard)."""
        import time as _time

        monkeypatch.setenv("TZ", "America/Los_Angeles")
        _time.tzset()
        # 09:00 PDT May 8 == 16:00 UTC May 8: well after the 4 AM rollover.
        self._make_review_pair(srs_db, "post_rollover", datetime(2026, 5, 8, 16, 0, tzinfo=UTC))
        assert srs_db.count_review_due_collocations(date(2026, 5, 8)) == 0


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
            due_at=datetime.combine(orig.due_at.date(), time(4, 0), tzinfo=UTC),
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
        today = date.today()
        today_noon = anki_day_anchor(today).isoformat()
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
        today = date.today()
        today_noon = anki_day_anchor(today).isoformat()
        self._seed_with_introduction(srs_db, "dual_intro", today_noon, today_noon)
        item = srs_db.get_collocation("dual_intro")
        orig_prod = item.directions[Direction.PRODUCTION]
        srs_db.update_direction(
            item.guid,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                state=SRSState.REVIEW,
                due_at=orig_prod.due_at,
                stability=1.0,
                reps=1,
                last_review=datetime.fromisoformat(today_noon),
                prior_state=SRSState.NEW,
                introduced_at=datetime.fromisoformat(today_noon),
            ),
        )
        assert srs_db.count_new_introduced_today(today) == 1


class TestCountReviewsCompletedToday:
    """count_reviews_completed_today mirrors Anki's per-deck ``review_today``
    counter from ``tt_revlog`` (Layer 73): an interday-queue answer today, i.e.
    ``review_kind IN (0,1,2) AND last_interval >= 1`` within the 4am window.

    Keys on the *pre-answer* interval sign (``last_interval``, days-positive /
    seconds-negative) — the discriminator that distinguishes interday from
    intraday (re)learning, which current direction state cannot recover. Counts
    both TT-native rows (written at grade time) and Anki-pulled rows (ingested at
    sync_pull), with no ``last_rating`` dependency.
    """

    def _seed_revlog(
        self,
        srs_db,
        text: str,
        *,
        review_kind: int,
        last_interval: int,
        when: datetime,
        direction: Direction = Direction.RECOGNITION,
    ) -> None:
        from app.models.srs_item import RevlogRow

        srs_db.add_collocation(_unit(text, "x"), language_code="sl")
        srs_db.append_revlog(
            RevlogRow(
                id=int(when.timestamp() * 1000),
                collocation_id=_id_for_text(srs_db, text),
                direction=direction,
                button_chosen=3,
                interval=30,
                last_interval=last_interval,
                factor=0,
                taken_millis=1500,
                review_kind=review_kind,
            )
        )

    def test_counts_interday_review_today(self, srs_db):
        """A review answer (kind=1) on interday footing (lastIvl≥1) today counts."""
        today = date.today()
        self._seed_revlog(srs_db, "review_today", review_kind=1, last_interval=30, when=anki_day_anchor(today))
        assert srs_db.count_reviews_completed_today(today) == 1

    def test_counts_interday_relearning_today(self, srs_db):
        """An interday relearning answer (kind=2, lastIvl≥1) counts — Anki answers
        it from the DayLearn queue, which bumps review_today."""
        today = date.today()
        self._seed_revlog(srs_db, "relearn_today", review_kind=2, last_interval=10, when=anki_day_anchor(today))
        assert srs_db.count_reviews_completed_today(today) == 1

    def test_counts_interday_learning_today(self, srs_db):
        """The under-count fix: an interday LEARNING step (kind=0, lastIvl≥1) counts
        — Anki answers it from DayLearn. The old state-based query excluded
        state='learning' entirely and missed this."""
        today = date.today()
        self._seed_revlog(srs_db, "interday_learn", review_kind=0, last_interval=4, when=anki_day_anchor(today))
        assert srs_db.count_reviews_completed_today(today) == 1

    def test_excludes_intraday_relearning_today(self, srs_db):
        """The over-count fix: an intraday relearning step (kind=2, lastIvl<1) is
        answered from the Learn queue → Anki does NOT bump review_today. The old
        state-based query counted every state='relearning' graded today."""
        today = date.today()
        self._seed_revlog(srs_db, "intraday_relearn", review_kind=2, last_interval=-600, when=anki_day_anchor(today))
        assert srs_db.count_reviews_completed_today(today) == 0

    def test_excludes_new_intro_today(self, srs_db):
        """A new-card first answer (kind=0, lastIvl=0) is newToday, not revToday."""
        today = date.today()
        self._seed_revlog(srs_db, "intro_today", review_kind=0, last_interval=0, when=anki_day_anchor(today))
        assert srs_db.count_reviews_completed_today(today) == 0

    def test_excludes_filtered_and_manual(self, srs_db):
        """Filtered/cram (kind=3) and manual (kind=4) are never answer-driven
        counter events even with lastIvl≥1."""
        today = date.today()
        self._seed_revlog(srs_db, "filtered", review_kind=3, last_interval=30, when=anki_day_anchor(today))
        self._seed_revlog(srs_db, "manual", review_kind=4, last_interval=30, when=anki_day_anchor(today))
        assert srs_db.count_reviews_completed_today(today) == 0

    def test_excludes_review_from_yesterday(self, srs_db):
        """A review on a prior day does NOT count (4am-window lower bound)."""
        today = date.today()
        self._seed_revlog(srs_db, "yesterday", review_kind=1, last_interval=30, when=anki_prev_day_anchor(today))
        assert srs_db.count_reviews_completed_today(today) == 0

    def test_counts_rows_not_distinct_cards(self, srs_db):
        """Anki increments per answer — two interday reviews of the same card today
        count as 2."""
        today = date.today()
        from app.models.srs_item import RevlogRow

        srs_db.add_collocation(_unit("twice", "x"), language_code="sl")
        cid = _id_for_text(srs_db, "twice")
        base = int(anki_day_anchor(today).timestamp() * 1000)
        for offset in (0, 1000):
            srs_db.append_revlog(
                RevlogRow(
                    id=base + offset,
                    collocation_id=cid,
                    direction=Direction.RECOGNITION,
                    button_chosen=3,
                    interval=30,
                    last_interval=20,
                    factor=0,
                    taken_millis=1500,
                    review_kind=1,
                )
            )
        assert srs_db.count_reviews_completed_today(today) == 2

    def test_counts_both_directions_individually(self, srs_db):
        """Both directions of the same collocation reviewed today → counts each."""
        today = date.today()
        self._seed_revlog(
            srs_db,
            "dual",
            review_kind=1,
            last_interval=30,
            when=anki_day_anchor(today),
            direction=Direction.RECOGNITION,
        )
        # Same collocation already added; append the production-direction row directly.
        from app.models.srs_item import RevlogRow

        srs_db.append_revlog(
            RevlogRow(
                id=int(anki_day_anchor(today).timestamp() * 1000) + 5,
                collocation_id=_id_for_text(srs_db, "dual"),
                direction=Direction.PRODUCTION,
                button_chosen=3,
                interval=30,
                last_interval=25,
                factor=0,
                taken_millis=1500,
                review_kind=1,
            )
        )
        assert srs_db.count_reviews_completed_today(today) == 2


class TestCountInterdayLearningDue:
    """Layer 79: interday learning cards (Anki queue=3, DayLearn) due today
    charge the review-per-day budget — Anki gathers them under
    ``LimitKind::Review`` (gathering.rs:35-61), oracle-pinned by
    ``test_parity_daily_caps.py::test_anki_interday_learning_charges_review_limit``.

    TT's discriminator for "interday footing": the scheduled step spans >= 1 day
    (``due_at - last_review``), the same sign convention as ``lastIvl``
    (interval_kind.rs). Intraday steps (queue=1) are exempt from the budget.
    """

    def _seed_learning(
        self,
        srs_db,
        text: str,
        *,
        due_at: datetime,
        last_review: datetime | None,
        state: SRSState = SRSState.LEARNING,
    ) -> None:
        srs_db.add_collocation(_unit(text, "x"), language_code="sl")
        item = srs_db.get_collocation(text)
        srs_db.update_direction(
            item.guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=state,
                due_at=due_at,
                last_review=last_review,
                left=1001,
            ),
        )

    def test_counts_interday_learning_due_today(self, srs_db):
        today = date.today()
        due = anki_day_anchor(today) + timedelta(hours=8)
        self._seed_learning(srs_db, "interday", due_at=due, last_review=due - timedelta(days=1))
        assert srs_db.count_interday_learning_due(today) == 1

    def test_counts_overdue_interday_from_yesterday(self, srs_db):
        """Anki gathers queue=3 with due <= today — overdue still charges."""
        today = date.today()
        due = anki_day_anchor(today) - timedelta(hours=8)
        self._seed_learning(srs_db, "overdue", due_at=due, last_review=due - timedelta(days=2))
        assert srs_db.count_interday_learning_due(today) == 1

    def test_counts_interday_relearning(self, srs_db):
        """Day-scale RELEARN steps are queue=3 too (DAY_LEARN_RELEARN)."""
        today = date.today()
        due = anki_day_anchor(today) + timedelta(hours=8)
        self._seed_learning(
            srs_db, "relearn", due_at=due, last_review=due - timedelta(days=1), state=SRSState.RELEARNING
        )
        assert srs_db.count_interday_learning_due(today) == 1

    def test_excludes_intraday_step(self, srs_db):
        """A sub-day step is queue=1 — exempt from the review budget."""
        today = date.today()
        due = anki_day_anchor(today) + timedelta(hours=8)
        self._seed_learning(srs_db, "intraday", due_at=due, last_review=due - timedelta(minutes=10))
        assert srs_db.count_interday_learning_due(today) == 0

    def test_excludes_interday_due_later(self, srs_db):
        """A day-scale step not yet due today doesn't charge today's budget."""
        today = date.today()
        due = anki_day_anchor(today) + timedelta(days=3)
        self._seed_learning(srs_db, "future", due_at=due, last_review=due - timedelta(days=3))
        assert srs_db.count_interday_learning_due(today) == 0

    def test_excludes_promoted_without_last_review(self, srs_db):
        """listen-first ``promote_to_learning`` rows (no last_review) stay out —
        Anki keeps those cards at queue=0, so they never charge its budget."""
        today = date.today()
        due = anki_day_anchor(today) + timedelta(hours=8)
        self._seed_learning(srs_db, "promoted", due_at=due, last_review=None)
        assert srs_db.count_interday_learning_due(today) == 0

    def test_excludes_review_state(self, srs_db):
        """REVIEW cards charge via reviews_today/due-count paths, not this one."""
        today = date.today()
        due = anki_day_anchor(today) + timedelta(hours=8)
        self._seed_learning(srs_db, "review_st", due_at=due, last_review=due - timedelta(days=5), state=SRSState.REVIEW)
        assert srs_db.count_interday_learning_due(today) == 0


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

    def test_delete_collocation_removes_row(self, srs_db):
        srs_db.add_collocation(_unit("nasvidenje", "goodbye"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.delete_collocation(row_id)
        assert srs_db.get_collocation("nasvidenje") is None

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

    def test_reset_collocation_marks_dirty_for_anki_forget(self, srs_db):
        """Reset must mark directions dirty so sync_push forgets the card in Anki.

        Regression (2026-06-04): reset_collocation wrote dirty_fsrs=0, so a reset
        never reached Anki — Anki kept the graduated review while TT showed a
        fresh NEW card (a permanent new-vs-review badge divergence), and the next
        pull (queue=2→REVIEW) silently clobbered the reset. Mirrors
        set_state_by_id(NEW), which already marks dirty.
        """
        srs_db.add_collocation(_unit("hvala", "thank you"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.reset_collocation(row_id)
        item = srs_db.get_collocation("hvala")
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is True
        assert item.directions[Direction.PRODUCTION].dirty_fsrs is True

    def test_reset_collocation_single_direction_marks_only_that_dirty(self, srs_db):
        srs_db.add_collocation(_unit("hvala", "thank you"), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.reset_collocation(row_id, direction=Direction.PRODUCTION)
        item = srs_db.get_collocation("hvala")
        assert item.directions[Direction.PRODUCTION].dirty_fsrs is True
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is False

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
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
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
        expected_due = datetime.combine(date.today(), time(4, 0), tzinfo=UTC)
        assert ds.due_at == expected_due

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

    def test_promote_cloze_production_only_no_fk_error(self, srs_db):
        """A cloze collocation has only a production direction.

        promote_to_learning must not write a Manual revlog for the missing
        recognition direction: tt_revlog's (collocation_id, direction) FK would
        raise sqlite3.IntegrityError and 500 the promote-to-learning request.
        """
        unit = SyntacticUnit(
            text="hvala lepa",
            translation="thank you",
            word_count=2,
            difficulty=1,
            source="corpus",
            card_type="cloze",
        )
        srs_db.add_collocation(unit, language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]

        # Regression: previously raised IntegrityError (FK constraint failed).
        srs_db.promote_to_learning(row_id)

        item = srs_db.get_collocation("hvala lepa")
        assert item.directions[Direction.PRODUCTION].state == SRSState.LEARNING

        # Exactly one Manual (review_kind=4) revlog row, production direction only.
        with srs_db._get_conn() as conn:
            revlogs = conn.execute(
                "SELECT direction, review_kind FROM tt_revlog WHERE collocation_id = ?",
                (row_id,),
            ).fetchall()
        assert len(revlogs) == 1
        assert revlogs[0][0] == "production"
        assert revlogs[0][1] == 4

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
        assert item.directions[Direction.RECOGNITION].due_at.date() == date.today()
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

        # update_collocation_fields (237->exit False branch)
        rows, _ = db.list_collocations()
        row_id = rows[0][0]
        db.update_collocation_fields(row_id, text="zdravo!", translation="hello!")

        # delete_collocation (249->exit False branch)
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
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
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
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
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
    """Tests for count_new_available."""

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
                due_at=datetime.combine(due, time(4, 0), tzinfo=UTC),
                stability=1.0,
                difficulty=5.0,
                reps=0 if state == SRSState.NEW else 1,
                lapses=0,
                state=state,
            )
            db.update_direction(item.guid, direction, ds)

    @pytest.mark.parametrize(
        "collocations,expected_new",
        [
            ([("hvala", SRSState.NEW, SRSState.NEW)], 2),
            ([("hvala", SRSState.SUSPENDED, SRSState.NEW)], 1),
            ([("hvala", SRSState.NEW, SRSState.SUSPENDED)], 1),
            ([("hvala", SRSState.NEW, SRSState.NEW), ("banka", SRSState.NEW, SRSState.REVIEW)], 3),
            ([], 0),
            ([("hvala", SRSState.REVIEW, SRSState.REVIEW)], 0),
            ([("hvala", SRSState.SUSPENDED, SRSState.SUSPENDED)], 0),
            ([("hvala", SRSState.KNOWN, SRSState.KNOWN)], 0),
            ([("hvala", SRSState.BURIED, SRSState.BURIED)], 0),
            ([("hvala", SRSState.REVIEW, SRSState.NEW)], 1),
            ([("hvala", SRSState.REVIEW, SRSState.REVIEW), ("banka", SRSState.REVIEW, SRSState.SUSPENDED)], 0),
        ],
    )
    def test_queue_stats(self, collocations, expected_new):
        db = SRSDatabase(":memory:")
        for text, rec_state, prod_state in collocations:
            self._seed(db, text, rec_state, prod_state)
        assert db.count_new_available() == expected_new

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
        "collocations,due_offset,expected",
        [
            # Both directions review + due → one distinct collocation.
            ([("hvala", SRSState.REVIEW, SRSState.REVIEW)], 0, 1),
            # Review + learning sibling → excluded. Anki's bury_reviews buries the
            # review card whenever its sibling sits in the learning queue, even when
            # the learning card was graded on a *prior* day (interday learning step).
            # The old "graded today" filter missed this; the badge over-counted.
            ([("hvala", SRSState.REVIEW, SRSState.LEARNING)], 0, 0),
            # Relearning is a learning queue too → excluded.
            ([("hvala", SRSState.REVIEW, SRSState.RELEARNING)], 0, 0),
            # New sibling does NOT exclude — Anki's new-sibling bury is a separate
            # mechanism we don't mirror here (the measured 214→208 gap was learning).
            ([("hvala", SRSState.REVIEW, SRSState.NEW)], 0, 1),
            # Future due date → not yet due.
            ([("hvala", SRSState.REVIEW, SRSState.REVIEW)], 1, 0),
            # Mixed: clean review collocation counts; the one with a learning sibling drops.
            (
                [("hvala", SRSState.REVIEW, SRSState.REVIEW), ("banka", SRSState.REVIEW, SRSState.LEARNING)],
                0,
                1,
            ),
            ([], 0, 0),
        ],
    )
    def test_count_review_due_collocations_excludes_learning_sibling(self, collocations, due_offset, expected):
        """The review badge mirrors Anki's sibling-bury: a collocation with a
        sibling direction in learning/relearning is removed from today's review
        pool, not just collocations graded today (rule 3 tightening)."""
        db = SRSDatabase(":memory:")
        for text, rec_state, prod_state in collocations:
            self._seed(db, text, rec_state, prod_state, due_offset_days=due_offset)
        assert db.count_review_due_collocations(date.today()) == expected


class TestCountNewAvailableCollocations:
    """Bury-aware new-card count: mirrors Anki's new-sibling bury for the badge.

    Anki buries a new card at queue-build when ``bury_new`` is set and a sibling
    was already gathered into *today's* queue (gather order: learning → review →
    new). So a new card is excluded when a sibling is in learning/relearning, is
    a review due today, or was graded today (grade-time sibling-bury persists as
    queue=-2 until rollover). A *future*-due review sibling is NOT gathered and
    does NOT bury — verified against the Anki binary. COUNT(DISTINCT
    collocation_id) collapses a both-new note to one (Anki buries the second new
    sibling). This is the mirror image of ``count_review_due_collocations``.
    """

    def _seed(
        self,
        db: SRSDatabase,
        text: str,
        rec_state: SRSState,
        prod_state: SRSState,
        *,
        due_offset_days: int = 0,
        prod_last_review: datetime | None = None,
    ) -> None:
        unit = SyntacticUnit(text=text, translation="t", word_count=2, difficulty=1, source="corpus")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation(text)
        assert item is not None
        today = date.today()
        due = today + timedelta(days=due_offset_days)
        for direction, state in [(Direction.RECOGNITION, rec_state), (Direction.PRODUCTION, prod_state)]:
            ds = DirectionState(
                direction=direction,
                due_at=datetime.combine(due, time(4, 0), tzinfo=UTC),
                stability=1.0,
                difficulty=5.0,
                reps=0 if state == SRSState.NEW else 1,
                lapses=0,
                state=state,
                last_review=prod_last_review if direction == Direction.PRODUCTION else None,
            )
            db.update_direction(item.guid, direction, ds)

    @pytest.mark.parametrize(
        "collocations,expected",
        [
            ([], 0),
            # Lone new direction (sibling suspended → not gathered) → counted.
            ([("hvala", SRSState.NEW, SRSState.SUSPENDED, 0)], 1),
            # Both directions new → one distinct collocation (Anki buries the 2nd new sibling).
            ([("hvala", SRSState.NEW, SRSState.NEW, 0)], 1),
            # New + review due TODAY → buried (the reported 2-vs-0 divergence).
            ([("hvala", SRSState.NEW, SRSState.REVIEW, 0)], 0),
            # New + review due in the FUTURE → NOT buried (sibling isn't gathered today).
            ([("hvala", SRSState.NEW, SRSState.REVIEW, 5)], 1),
            # New + learning/relearning sibling → buried (learning is gathered first).
            ([("hvala", SRSState.NEW, SRSState.LEARNING, 0)], 0),
            ([("hvala", SRSState.NEW, SRSState.RELEARNING, 0)], 0),
            # No new direction at all → 0.
            ([("hvala", SRSState.REVIEW, SRSState.REVIEW, 0)], 0),
            # Mix: clean new-pair counts (1); pair with review-due-today sibling buried (0) → 1.
            ([("hvala", SRSState.NEW, SRSState.NEW, 0), ("banka", SRSState.NEW, SRSState.REVIEW, 0)], 1),
            # Mix: clean new-pair (1) + new with a FUTURE review sibling (1) → 2.
            ([("hvala", SRSState.NEW, SRSState.NEW, 0), ("banka", SRSState.NEW, SRSState.REVIEW, 5)], 2),
        ],
    )
    def test_count_new_available_collocations(self, collocations, expected):
        db = SRSDatabase(":memory:")
        for text, rec_state, prod_state, off in collocations:
            self._seed(db, text, rec_state, prod_state, due_offset_days=off)
        assert db.count_new_available_collocations(date.today()) == expected

    def test_graded_today_sibling_buries_new(self):
        """A new card whose sibling was graded today is buried even when that
        sibling's review was pushed to a future due date (grade-time bury
        persists as queue=-2 until the day rollover). Caught by the
        ``last_review today`` clause, not the ``review due today`` clause."""
        db = SRSDatabase(":memory:")
        self._seed(
            db,
            "hvala",
            SRSState.NEW,
            SRSState.REVIEW,
            due_offset_days=5,
            prod_last_review=datetime.now(UTC),
        )
        # Future due → "review due today" clause misses it; "graded today" catches it.
        assert db.count_new_available_collocations(date.today()) == 0


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


class TestGetSentenceAudioFilename:
    """Tests for get_sentence_audio_filename."""

    def test_returns_sentence_audio_when_present(self, srs_db):
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState

        dirs = {Direction.RECOGNITION: DirectionState(Direction.RECOGNITION, date.today(), state=SRSState.NEW)}
        coll_id = srs_db.upsert_by_guid(_unit("stol", "chair"), "sl", dirs)
        srs_db.add_media(
            coll_id,
            kind="audio_tts_sentence",
            filename="tts_sentence_abc123.mp3",
            path="/tmp/tts_sentence_abc123.mp3",
            anki_filename="",
            sha256="s1",
            size_bytes=100,
        )
        assert srs_db.get_sentence_audio_filename(coll_id) == "tts_sentence_abc123.mp3"

    def test_returns_none_when_missing(self, srs_db):
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
        assert srs_db.get_sentence_audio_filename(coll_id) is None

    def test_ignores_word_audio_rows(self, srs_db):
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
        assert srs_db.get_sentence_audio_filename(coll_id) is None


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
        dirs = {
            Direction.RECOGNITION: DirectionState(
                direction=Direction.RECOGNITION, due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC)
            )
        }
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
        for _, item, _ in items:
            if item.syntactic_unit.text == "nova fraza":
                assert item.syntactic_unit.source_sentence == "Nova fraza v kontekstu."
                assert item.syntactic_unit.source_lesson_id == "lesson-789"
                assert item.syntactic_unit.source_line_index == 3
                break
        else:
            pytest.fail("nova fraza not found in items without anki note")


class TestAddDirtyField:
    """Tests for add_dirty_field on collocations."""

    def test_add_dirty_field_appends_to_empty(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("dober dan")
        assert item is not None
        guid = item.guid

        srs_db.add_dirty_field(guid, "x")
        assert srs_db.get_dirty_fields(guid) == "x"

    def test_add_dirty_field_no_duplicate(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("dober dan")
        assert item is not None
        guid = item.guid

        srs_db.set_dirty_fields(guid, "x,y")
        srs_db.add_dirty_field(guid, "x")
        assert srs_db.get_dirty_fields(guid) == "x,y"

    def test_add_dirty_field_nonexistent_guid_does_not_raise(self, srs_db):
        """Calling add_dirty_field with a non-existent guid is a no-op."""
        srs_db.add_dirty_field(guid="nonexistent-guid", field="x")

    def test_add_dirty_field_sorts(self, srs_db):
        unit = _unit()
        srs_db.add_collocation(unit, language_code="sl")
        item = srs_db.get_collocation("dober dan")
        assert item is not None
        guid = item.guid

        srs_db.set_dirty_fields(guid, "y")
        srs_db.add_dirty_field(guid, "a")
        assert srs_db.get_dirty_fields(guid) == "a,y"


class TestAddDirtyFieldById:
    """Tests for add_dirty_field_by_id on collocations."""

    def _id_for_text(self, srs_db, text: str) -> int:
        with srs_db._get_conn() as conn:
            return conn.execute("SELECT id FROM collocations WHERE text = ?", (text,)).fetchone()[0]

    def test_add_dirty_field_by_id_sets_flag(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        coll_id = self._id_for_text(srs_db, "dober dan")
        srs_db.add_dirty_field_by_id(coll_id, "image")
        with srs_db._get_conn() as conn:
            row = conn.execute("SELECT dirty_fields FROM collocations WHERE id = ?", (coll_id,)).fetchone()
        assert row["dirty_fields"] == "image"

    def test_add_dirty_field_by_id_is_idempotent(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        coll_id = self._id_for_text(srs_db, "dober dan")
        srs_db.add_dirty_field_by_id(coll_id, "image")
        srs_db.add_dirty_field_by_id(coll_id, "image")
        with srs_db._get_conn() as conn:
            row = conn.execute("SELECT dirty_fields FROM collocations WHERE id = ?", (coll_id,)).fetchone()
        assert row["dirty_fields"] == "image"

    def test_add_dirty_field_by_id_coexists_with_existing_flag(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        coll_id = self._id_for_text(srs_db, "dober dan")
        srs_db.add_dirty_field_by_id(coll_id, "translation")
        srs_db.add_dirty_field_by_id(coll_id, "image")
        with srs_db._get_conn() as conn:
            row = conn.execute("SELECT dirty_fields FROM collocations WHERE id = ?", (coll_id,)).fetchone()
        assert row["dirty_fields"] == "image,translation"

    def test_add_dirty_field_by_id_unknown_id_is_noop(self, srs_db):
        """Calling add_dirty_field_by_id with a non-existent id is a no-op."""
        srs_db.add_dirty_field_by_id(999999, "image")


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

    def test_sqlite_url_with_relative_path(self, srs_db, tmp_path):
        """Test that relative paths in sqlite:// URLs work correctly."""
        # srs_db fixture uses :memory: which doesn't test the path parsing
        # This test ensures the parsing logic works
        from app.srs.database import SRSDatabase

        db_path = tmp_path / "tunatale.db"
        url = f"sqlite:///{db_path}"
        # Just verify it doesn't raise an error
        try:
            db = SRSDatabase(url)
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
                due_at=datetime.combine(grade_time.date(), time(4, 0), tzinfo=UTC),
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


class TestRevlog:
    """Tests for revlog row creation and querying."""

    def test_append_revlog_and_query_latest(self, srs_db):
        """append_revlog stores a row; latest_revlog_id_for_direction returns MAX(id)."""
        from app.models.srs_item import RevlogRow

        srs_db.add_collocation(
            SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus"),
            language_code="sl",
        )
        row = RevlogRow(
            id=5000,
            collocation_id=1,
            direction=Direction.RECOGNITION,
            button_chosen=3,
            interval=1,
            last_interval=0,
            factor=0,
            taken_millis=1500,
            review_kind=1,
            anki_card_id=100,
        )
        srs_db.append_revlog(row)

        latest = srs_db.latest_revlog_id_for_direction(1, Direction.RECOGNITION)
        assert latest == 5000

    def test_latest_revlog_id_for_direction_returns_none(self, srs_db):
        """When no revlog rows exist, latest_revlog_id_for_direction returns None."""
        assert srs_db.latest_revlog_id_for_direction(999, Direction.RECOGNITION) is None

    def test_latest_revlog_id_for_direction_includes_unlinked_rows(self, srs_db):
        """Layer 71: rows with anki_card_id=NULL (graded pre-link) count toward
        the anchor — they belong to the direction's replay domain."""
        from app.models.srs_item import RevlogRow

        srs_db.add_collocation(
            SyntacticUnit(text="morje", translation="sea", word_count=1, difficulty=1, source="corpus"),
            language_code="sl",
        )
        for rid, akid in ((7001, None), (7002, 400)):
            srs_db.append_revlog(
                RevlogRow(
                    id=rid,
                    collocation_id=1,
                    direction=Direction.RECOGNITION,
                    button_chosen=3,
                    interval=1,
                    last_interval=0,
                    factor=0,
                    taken_millis=500,
                    review_kind=1,
                    anki_card_id=akid,
                )
            )
        srs_db.append_revlog(
            RevlogRow(
                id=7003,
                collocation_id=1,
                direction=Direction.PRODUCTION,
                button_chosen=3,
                interval=1,
                last_interval=0,
                factor=0,
                taken_millis=500,
                review_kind=1,
                anki_card_id=None,
            )
        )
        # NULL-akid row 7001 counts; the other direction's 7003 does not.
        assert srs_db.latest_revlog_id_for_direction(1, Direction.RECOGNITION) == 7002

    def test_append_revlog_insert_or_ignore(self, srs_db):
        """Duplicate id is silently ignored (INSERT OR IGNORE)."""
        from app.models.srs_item import RevlogRow

        srs_db.add_collocation(
            SyntacticUnit(text="hiša", translation="house", word_count=1, difficulty=1, source="corpus"),
            language_code="sl",
        )
        row = RevlogRow(
            id=5001,
            collocation_id=1,
            direction=Direction.RECOGNITION,
            button_chosen=3,
            interval=1,
            last_interval=0,
            factor=0,
            taken_millis=500,
            review_kind=1,
            anki_card_id=200,
        )
        srs_db.append_revlog(row)
        srs_db.append_revlog(row)  # same id, should be ignored
        assert srs_db.latest_revlog_id_for_direction(1, Direction.RECOGNITION) == 5001

    def test_get_tt_revlog_ids_returns_held_ids_for_direction(self, srs_db):
        """get_tt_revlog_ids returns the set of ids held for (collocation_id, direction)."""
        from app.models.srs_item import RevlogRow

        srs_db.add_collocation(
            SyntacticUnit(text="reka", translation="river", word_count=1, difficulty=1, source="corpus"),
            language_code="sl",
        )

        def _row(rid: int, direction: Direction) -> RevlogRow:
            return RevlogRow(
                id=rid,
                collocation_id=1,
                direction=direction,
                button_chosen=3,
                interval=1,
                last_interval=0,
                factor=0,
                taken_millis=500,
                review_kind=1,
                anki_card_id=None,
            )

        srs_db.append_revlog(_row(6001, Direction.RECOGNITION))
        srs_db.append_revlog(_row(6002, Direction.RECOGNITION))
        srs_db.append_revlog(_row(6003, Direction.PRODUCTION))

        assert srs_db.get_tt_revlog_ids(1, Direction.RECOGNITION) == {6001, 6002}
        assert srs_db.get_tt_revlog_ids(1, Direction.PRODUCTION) == {6003}
        assert srs_db.get_tt_revlog_ids(1, Direction.RECOGNITION) and isinstance(
            srs_db.get_tt_revlog_ids(1, Direction.RECOGNITION), set
        )

    def test_get_tt_revlog_ids_empty_when_none(self, srs_db):
        """get_tt_revlog_ids returns an empty set when no rows exist."""
        srs_db.add_collocation(
            SyntacticUnit(text="gora", translation="mountain", word_count=1, difficulty=1, source="corpus"),
            language_code="sl",
        )
        assert srs_db.get_tt_revlog_ids(1, Direction.RECOGNITION) == set()

    def test_append_manual_revlog_writes_row(self, srs_db):
        """append_manual_revlog writes a row with review_kind=4."""
        srs_db.add_collocation(
            SyntacticUnit(text="avto", translation="car", word_count=1, difficulty=1, source="corpus"),
            language_code="sl",
        )
        srs_db.append_manual_revlog(collocation_id=1, direction=Direction.RECOGNITION, anki_card_id=300)

        latest = srs_db.latest_revlog_id_for_direction(1, Direction.RECOGNITION)
        assert latest is not None
        row = srs_db._conn.execute("SELECT * FROM tt_revlog WHERE id = ?", (latest,)).fetchone()
        assert row is not None
        assert row["review_kind"] == 4
        assert row["collocation_id"] == 1
        assert row["direction"] == "recognition"

    def test_has_revision_near_detects_duplicate(self, srs_db):
        """has_revision_near returns True for a row within 5000ms with same ease."""
        from app.models.srs_item import RevlogRow

        srs_db.add_collocation(
            SyntacticUnit(text="voda", translation="water", word_count=1, difficulty=1, source="corpus"),
            language_code="sl",
        )
        existing = RevlogRow(
            id=50000,
            collocation_id=1,
            direction=Direction.RECOGNITION,
            button_chosen=3,
            interval=10,
            last_interval=5,
            factor=0,
            taken_millis=1000,
            review_kind=1,
            anki_card_id=400,
        )
        srs_db.append_revlog(existing)

        # Within 5000ms, same ease → duplicate
        assert srs_db.has_revision_near(1, "recognition", 50200, 3)
        # Different ease → not a duplicate
        assert not srs_db.has_revision_near(1, "recognition", 50200, 4)
        # Far timestamp → not a duplicate
        assert not srs_db.has_revision_near(1, "recognition", 100000, 3)
        # Different direction → not a duplicate
        assert not srs_db.has_revision_near(1, "production", 50200, 3)

    def test_has_revision_near_ignore_ids_excludes_anki_origin_rows(self, srs_db):
        """``ignore_ids`` removes already-ingested Anki rows from the near-match.

        Layer 60: the near-match guard exists to suppress a TT-written grade's
        Anki copy (different id, same event). But an already-ingested *Anki* row
        is a distinct grade, not a mirror — it must not suppress a second Anki
        grade a few seconds later (rapid learning steps). The ingest passes the
        card's Anki revlog ids as ``ignore_ids`` so Anki-origin near rows can't
        suppress.
        """
        from app.models.srs_item import RevlogRow

        srs_db.add_collocation(
            SyntacticUnit(text="voda", translation="water", word_count=1, difficulty=1, source="corpus"),
            language_code="sl",
        )
        srs_db.append_revlog(
            RevlogRow(
                id=50000,
                collocation_id=1,
                direction=Direction.RECOGNITION,
                button_chosen=3,
                interval=10,
                last_interval=5,
                factor=0,
                taken_millis=1000,
                review_kind=1,
                anki_card_id=400,
            )
        )

        # Baseline: the row at 50000 is a near match for a grade at 50200.
        assert srs_db.has_revision_near(1, "recognition", 50200, 3)
        # The near row is Anki-origin (its id is in ignore_ids) → not a suppressor.
        assert not srs_db.has_revision_near(1, "recognition", 50200, 3, ignore_ids={50000})
        # An ignore set that does not cover the near row leaves it a match.
        assert srs_db.has_revision_near(1, "recognition", 50200, 3, ignore_ids={99999})
        # Empty ignore set behaves like the default.
        assert srs_db.has_revision_near(1, "recognition", 50200, 3, ignore_ids=set())


class TestGetInflectionClozesForLemma:
    """get_inflection_clozes_for_lemma returns only morph clozes for a lemma."""

    def test_returns_morph_clozes_with_hydrated_directions(self, srs_db):
        """Two morph clozes for the same lemma are returned with directions."""
        srs_db.add_collocation(
            SyntacticUnit(
                text="sem",
                translation="I was",
                word_count=1,
                difficulty=1,
                source="cloze",
                lemma="biti",
                disambig_key="morph:1sg-past",
                source_sentence="jaz sem bil",
                card_type="cloze",
            ),
            language_code="sl",
        )
        srs_db.add_collocation(
            SyntacticUnit(
                text="si",
                translation="you were",
                word_count=1,
                difficulty=1,
                source="cloze",
                lemma="biti",
                disambig_key="morph:2sg-past",
                source_sentence="ti si bil",
                card_type="cloze",
            ),
            language_code="sl",
        )

        results = srs_db.get_inflection_clozes_for_lemma("biti")
        assert len(results) == 2
        texts = {item.syntactic_unit.text for _, item in results}
        assert texts == {"sem", "si"}
        for _, item in results:
            # Hydration proof: production direction has state + stability populated
            prod = item.directions.get(Direction.PRODUCTION)
            assert prod is not None
            assert prod.state == SRSState.NEW
            assert prod.stability >= 1.0

    def test_excludes_base_function_word_cloze(self, srs_db):
        """A base cloze (disambig_key='') for the same lemma is not returned."""
        srs_db.add_collocation(
            SyntacticUnit(
                text="sem",
                translation="I was",
                word_count=1,
                difficulty=1,
                source="cloze",
                lemma="biti",
                disambig_key="morph:1sg-past",
                source_sentence="jaz sem bil",
                card_type="cloze",
            ),
            language_code="sl",
        )
        srs_db.add_collocation(
            SyntacticUnit(
                text="biti",
                translation="to be",
                word_count=1,
                difficulty=1,
                source="cloze",
                lemma="biti",
                source_sentence="biti ali ne biti",
                card_type="cloze",
            ),
            language_code="sl",
        )

        results = srs_db.get_inflection_clozes_for_lemma("biti")
        assert len(results) == 1
        assert results[0][1].syntactic_unit.text == "sem"

    def test_excludes_vocab_items(self, srs_db):
        """A vocab item (card_type='vocab') for the same lemma is not returned."""
        srs_db.add_collocation(
            SyntacticUnit(
                text="sem",
                translation="I was",
                word_count=1,
                difficulty=1,
                source="cloze",
                lemma="biti",
                disambig_key="morph:1sg-past",
                source_sentence="jaz sem bil",
                card_type="cloze",
            ),
            language_code="sl",
        )
        srs_db.add_collocation(
            SyntacticUnit(
                text="bil",
                translation="was",
                word_count=1,
                difficulty=1,
                source="test",
                lemma="biti",
            ),
            language_code="sl",
        )

        results = srs_db.get_inflection_clozes_for_lemma("biti")
        assert len(results) == 1
        assert results[0][1].syntactic_unit.text == "sem"

    def test_excludes_other_lemma_morph_clozes(self, srs_db):
        """A morph cloze for a different lemma is not returned."""
        srs_db.add_collocation(
            SyntacticUnit(
                text="sem",
                translation="I was",
                word_count=1,
                difficulty=1,
                source="cloze",
                lemma="biti",
                disambig_key="morph:1sg-past",
                source_sentence="jaz sem bil",
                card_type="cloze",
            ),
            language_code="sl",
        )
        srs_db.add_collocation(
            SyntacticUnit(
                text="bom",
                translation="I will",
                word_count=1,
                difficulty=1,
                source="cloze",
                lemma="biti-future",
                disambig_key="morph:1sg-fut",
                source_sentence="jaz bom",
                card_type="cloze",
            ),
            language_code="sl",
        )

        results = srs_db.get_inflection_clozes_for_lemma("biti")
        assert len(results) == 1
        assert results[0][1].syntactic_unit.text == "sem"

    def test_empty_for_lemma_with_no_matches(self, srs_db):
        """A lemma with no items at all returns empty list."""
        assert srs_db.get_inflection_clozes_for_lemma("neobstojeci") == []


class TestIgnoredLemmas:
    def test_add_and_get(self, srs_db):
        srs_db.add_ignored_lemma("sl", "Ana")
        result = srs_db.get_ignored_lemmas("sl")
        assert result == {"ana"}

    def test_add_and_get_idempotent(self, srs_db):
        srs_db.add_ignored_lemma("sl", "ana")
        srs_db.add_ignored_lemma("sl", "ana")
        result = srs_db.get_ignored_lemmas("sl")
        assert result == {"ana"}

    def test_add_lowercases(self, srs_db):
        srs_db.add_ignored_lemma("sl", "AnA")
        assert srs_db.get_ignored_lemmas("sl") == {"ana"}

    def test_remove(self, srs_db):
        srs_db.add_ignored_lemma("sl", "ana")
        srs_db.remove_ignored_lemma("sl", "ana")
        assert srs_db.get_ignored_lemmas("sl") == set()

    def test_remove_idempotent(self, srs_db):
        srs_db.remove_ignored_lemma("sl", "ana")
        assert srs_db.get_ignored_lemmas("sl") == set()

    def test_remove_lowercases(self, srs_db):
        srs_db.add_ignored_lemma("sl", "ana")
        srs_db.remove_ignored_lemma("sl", "AnA")
        assert srs_db.get_ignored_lemmas("sl") == set()

    def test_scoped_by_language(self, srs_db):
        srs_db.add_ignored_lemma("sl", "ana")
        srs_db.add_ignored_lemma("en", "the")
        assert srs_db.get_ignored_lemmas("sl") == {"ana"}
        assert srs_db.get_ignored_lemmas("en") == {"the"}


class TestLemmaAnalysisCache:
    """Persistent sentence-analysis cache round-trips, misses, and invalidation."""

    def test_miss_returns_none(self, srs_db):
        assert srs_db.get_sentence_analysis("Dober dan", "sl", "test-v1") is None

    def test_round_trip(self, srs_db):
        srs_db.set_sentence_analysis("Dober dan", "sl", "test-v1", '[{"surface": "Dober", "lemma": "dober"}]')
        cached = srs_db.get_sentence_analysis("Dober dan", "sl", "test-v1")
        assert cached == '[{"surface": "Dober", "lemma": "dober"}]'

    def test_model_version_mismatch_is_miss(self, srs_db):
        srs_db.set_sentence_analysis("Dober dan", "sl", "v1", '[{"surface": "Dober", "lemma": "dober"}]')
        assert srs_db.get_sentence_analysis("Dober dan", "sl", "v2") is None

    def test_language_code_mismatch_is_miss(self, srs_db):
        srs_db.set_sentence_analysis("Dober dan", "sl", "test-v1", '[{"surface": "Dober", "lemma": "dober"}]')
        assert srs_db.get_sentence_analysis("Dober dan", "en", "test-v1") is None


class TestImageQueryCache:
    """Persistent per-word image-search-query cache: round-trips, misses, skip-sentinel."""

    def test_miss_returns_none(self, srs_db):
        assert srs_db.get_image_query("sodišče", "court", "img-v1") is None

    def test_round_trip(self, srs_db):
        srs_db.set_image_query("sodišče", "court", "img-v1", "courtroom interior")
        assert srs_db.get_image_query("sodišče", "court", "img-v1") == "courtroom interior"

    def test_empty_string_is_a_cached_skip_not_a_miss(self, srs_db):
        # "" is the sentinel for "abstract word, no image"; it must round-trip
        # as "" (distinct from a None miss) so we don't re-query every sync.
        srs_db.set_image_query("zato", "therefore", "img-v1", "")
        assert srs_db.get_image_query("zato", "therefore", "img-v1") == ""

    def test_upsert_overwrites(self, srs_db):
        srs_db.set_image_query("sodišče", "court", "img-v1", "tennis court")
        srs_db.set_image_query("sodišče", "court", "img-v1", "courtroom interior")
        assert srs_db.get_image_query("sodišče", "court", "img-v1") == "courtroom interior"

    def test_model_version_mismatch_is_miss(self, srs_db):
        srs_db.set_image_query("sodišče", "court", "img-v1", "courtroom interior")
        assert srs_db.get_image_query("sodišče", "court", "img-v2") is None


class TestConnectionPragmas:
    """WAL + busy_timeout keep live reads responsive during a sync's write txn."""

    def test_file_db_uses_wal_and_busy_timeout(self, tmp_path):
        """Regression: file connections had no WAL/busy_timeout, so /queue-stats and
        /review-queue reads got 'database is locked' the instant a peer-sync held a
        write transaction (exposed by the slow Norwegian first-sync)."""
        db = SRSDatabase(str(tmp_path / "wal.db"))
        try:
            with db._file_conn() as conn:
                assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
                assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        finally:
            db.close()


class TestGetImageFilenames:
    """Tests for DbMediaMixin.get_image_filenames (batched lookup)."""

    def test_returns_empty_for_no_ids(self, srs_db):
        assert srs_db.get_image_filenames([]) == {}

    def test_returns_empty_when_no_media(self, srs_db):
        srs_db.add_collocation(_unit("voda", "water"), language_code="sl")
        cid = _id_for_text(srs_db, "voda")
        assert srs_db.get_image_filenames([cid]) == {}

    def test_returns_single_image(self, srs_db):
        srs_db.add_collocation(_unit("voda", "water"), language_code="sl")
        cid = _id_for_text(srs_db, "voda")
        srs_db.add_media(cid, "image", "voda.jpg", "/tmp/voda.jpg", "voda.jpg", "abc123", 1024)
        assert srs_db.get_image_filenames([cid]) == {cid: "voda.jpg"}

    def test_returns_multiple_images(self, srs_db):
        srs_db.add_collocation(_unit("voda", "water"), language_code="sl")
        srs_db.add_collocation(_unit("ogenj", "fire"), language_code="sl")
        c1 = _id_for_text(srs_db, "voda")
        c2 = _id_for_text(srs_db, "ogenj")
        srs_db.add_media(c1, "image", "voda.jpg", "/tmp/voda.jpg", "voda.jpg", "aaa", 100)
        srs_db.add_media(c2, "image", "ogenj.png", "/tmp/ogenj.png", "ogenj.png", "bbb", 200)
        result = srs_db.get_image_filenames([c1, c2])
        assert result == {c1: "voda.jpg", c2: "ogenj.png"}

    def test_uses_most_recent_per_collocation(self, srs_db):
        srs_db.add_collocation(_unit("voda", "water"), language_code="sl")
        cid = _id_for_text(srs_db, "voda")
        srs_db.add_media(cid, "image", "old.jpg", "/tmp/old.jpg", "old.jpg", "aaa", 100)
        srs_db.add_media(cid, "image", "new.jpg", "/tmp/new.jpg", "new.jpg", "bbb", 200)
        result = srs_db.get_image_filenames([cid])
        assert result == {cid: "new.jpg"}

    def test_ignores_non_image_media(self, srs_db):
        srs_db.add_collocation(_unit("voda", "water"), language_code="sl")
        cid = _id_for_text(srs_db, "voda")
        srs_db.add_media(cid, "audio_forvo", "voda.mp3", "/tmp/voda.mp3", "voda.mp3", "ccc", 300)
        assert srs_db.get_image_filenames([cid]) == {}

    def test_concurrent_read_during_write_transaction_does_not_lock(self, tmp_path):
        """A live read on a separate connection reads the committed snapshot (WAL)
        instead of erroring while a write transaction is held open."""
        import sqlite3

        db_path = str(tmp_path / "concurrent.db")
        db = SRSDatabase(db_path)
        reader = sqlite3.connect(db_path)
        try:
            with db.begin_transaction():
                db.set_anki_state_cache("last_unbury_day", "2026-06-26")  # holds the write lock
                reader.execute("SELECT COUNT(*) FROM anki_state_cache").fetchone()
        finally:
            reader.close()
            db.close()


class TestCountNewCreatedToday:
    """count_new_created_today: distinct collocations created inside today's
    Anki-day window that still have at least one NEW direction. Feeds the
    per-listen creation budget (staged listen, plan D1) so same-day re-listens
    don't re-fill a budget already spent on cards nobody has graded yet."""

    def test_zero_on_empty_db(self, srs_db):
        assert srs_db.count_new_created_today(date.today()) == 0

    def test_counts_fresh_collocation_once_not_per_direction(self, srs_db):
        # Vocab rows get two NEW directions; each collocation counts once.
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        srs_db.add_collocation(_unit("center", "center"), language_code="sl")
        assert srs_db.count_new_created_today(date.today()) == 2

    def test_excludes_rows_created_before_today(self, srs_db):
        srs_db.add_collocation(_unit("stara beseda", "old word"), language_code="sl")
        with srs_db._get_conn() as conn:
            conn.execute("UPDATE collocations SET created_at = datetime('now', '-2 days')")
            conn.commit()
        assert srs_db.count_new_created_today(date.today()) == 0

    def test_excludes_collocation_with_no_new_direction_left(self, srs_db):
        # Introduced same-day: the card charges introduced_today instead, so
        # counting it here would double-subtract from the listen budget.
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        row_id = _id_for_text(srs_db, "banka")
        with srs_db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET state='learning' WHERE collocation_id=?",
                (row_id,),
            )
            conn.commit()
        assert srs_db.count_new_created_today(date.today()) == 0

    def test_counts_collocation_with_one_direction_still_new(self, srs_db):
        srs_db.add_collocation(_unit("banka", "bank"), language_code="sl")
        row_id = _id_for_text(srs_db, "banka")
        with srs_db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET state='learning' WHERE collocation_id=? AND direction='recognition'",
                (row_id,),
            )
            conn.commit()
        assert srs_db.count_new_created_today(date.today()) == 1
