"""Tests for the one-shot backfill of cloze `sentence_translation` from stored lessons."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.anki.backfill_cloze_sentence_translations import (
    BackfillPlan,
    LessonUpdate,
    apply_backfill,
    plan_backfill,
)
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore


def _seed(tmp_path: Path) -> tuple[SRSDatabase, ContentStore, str]:
    """Build a TT DB + ContentStore against a single sqlite file. Returns (db, store, db_path)."""
    db_path = tmp_path / "tt.db"
    db = SRSDatabase(str(db_path))
    store = ContentStore(str(db_path))
    return db, store, str(db_path)


def _add_translated_lesson(store: ContentStore, lesson_id: str, pairs: list[tuple[str, str]]) -> None:
    phrases = [Phrase(text="Translated", voice_id="v", language_code="en")]
    for sl, en in pairs:
        phrases.append(Phrase(text=sl, voice_id="v", language_code="sl"))
        phrases.append(Phrase(text=en, voice_id="v", language_code="en"))
    lesson = Lesson(
        title="t",
        language_code="sl",
        sections=[Section(section_type=SectionType.TRANSLATED, phrases=phrases)],
    )
    store.save_lesson(lesson_id, "c1", 1, lesson)


def _add_cloze(db: SRSDatabase, *, text: str, source_sentence: str, translation: str = "") -> str:
    unit = SyntacticUnit(
        text=text,
        translation=translation,
        word_count=1,
        difficulty=1,
        source="llm",
        lemma=text,
        card_type="cloze",
        source_sentence=source_sentence,
    )
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation_by_lemma(text)
    return item.guid


class TestPlanBackfill:
    def test_skips_lesson_when_get_lesson_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """plan_backfill handles missing lesson gracefully (defensive guard)."""
        db, store, _ = _seed(tmp_path)

        def patched_list_all(_store):
            return [("phantom", "c1", 1)]

        monkeypatch.setattr(
            "app.anki.backfill_cloze_sentence_translations._list_all_lessons",
            patched_list_all,
        )

        plan = plan_backfill(db, store)
        assert len(plan.lesson_updates) == 0

    def test_returns_unmatched_cloze_rows_separately(self, tmp_path: Path):
        db, store, _ = _seed(tmp_path)
        _add_translated_lesson(store, "l1", [("Kako si?", "How are you?")])
        _add_cloze(db, text="kako", source_sentence="Kako si?")
        _add_cloze(db, text="ne", source_sentence="Sentence not in any lesson.")

        plan = plan_backfill(db, store)

        # Lesson update: 1 lesson got new sentence_translations
        assert len(plan.lesson_updates) == 1
        assert plan.lesson_updates[0].lesson_id == "l1"
        assert plan.lesson_updates[0].new_pairs == {"Kako si?": "How are you?"}

        # Cloze updates: kako matches, ne doesn't
        matched = {u.text: u for u in plan.cloze_updates}
        assert "kako" in matched
        assert matched["kako"].new_sentence_translation == "How are you?"
        unmatched_texts = [t for t, _ in plan.cloze_unmatched]
        assert "ne" in unmatched_texts
        assert "kako" not in unmatched_texts

    def test_falls_back_to_punctuation_stripped_match(self, tmp_path: Path):
        """Cloze source_sentence with stripped trailing '?' still matches lesson text."""
        db, store, _ = _seed(tmp_path)
        _add_translated_lesson(store, "l1", [("Odprto je vsak dan?", "Is it open every day?")])
        _add_cloze(db, text="vsak", source_sentence="Odprto je vsak dan")

        plan = plan_backfill(db, store)

        assert len(plan.cloze_updates) == 1
        assert plan.cloze_updates[0].new_sentence_translation == "Is it open every day?"

    def test_matches_clozed_source_sentence_via_uncloze(self, tmp_path: Path):
        """Morphology clozes store clozed source_sentence; matching un-clozes it first."""
        db, store, _ = _seed(tmp_path)
        _add_translated_lesson(store, "l1", [("Kje boste ostali?", "Where will you stay?")])
        _add_cloze(db, text="boste", source_sentence="Kje {{c1::boste}} ostali")

        plan = plan_backfill(db, store)

        assert len(plan.cloze_updates) == 1
        assert plan.cloze_updates[0].new_sentence_translation == "Where will you stay?"

    def test_matches_ending_blank_cloze_via_uncloze(self, tmp_path: Path):
        """Fluent-Forever ending-blank cloze (stem visible) un-clozes to the full word."""
        db, store, _ = _seed(tmp_path)
        _add_translated_lesson(store, "l1", [("Grem v Ljubljano.", "I'm going to Ljubljana.")])
        _add_cloze(db, text="Ljubljano", source_sentence="Grem v Ljubljan{{c1::o}}.")

        plan = plan_backfill(db, store)

        assert len(plan.cloze_updates) == 1
        assert plan.cloze_updates[0].new_sentence_translation == "I'm going to Ljubljana."

    def test_matches_despite_internal_punctuation(self, tmp_path: Path):
        """Cloze source_sentence lacking the lesson key's internal comma still matches."""
        db, store, _ = _seed(tmp_path)
        _add_translated_lesson(store, "l1", [("Zdravo, kje ste?", "Hello, where are you from?")])
        _add_cloze(db, text="ste", source_sentence="Zdravo kje {{c1::ste}}")

        plan = plan_backfill(db, store)

        assert len(plan.cloze_updates) == 1
        assert plan.cloze_updates[0].new_sentence_translation == "Hello, where are you from?"

    def test_skips_already_populated_cloze_rows(self, tmp_path: Path):
        db, store, _ = _seed(tmp_path)
        _add_translated_lesson(store, "l1", [("Kako si?", "How are you?")])
        guid = _add_cloze(db, text="kako", source_sentence="Kako si?")
        # Pre-populate
        db.set_sentence_translation_dirty(guid, "Already populated")
        # Clear dirty so we can detect re-marking
        db.set_dirty_fields(guid, "")

        plan = plan_backfill(db, store)

        assert plan.cloze_updates == []


class TestApplyBackfill:
    def test_apply_skips_orphan_lesson(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """apply_backfill skips lesson_updates whose lesson_id no longer exists."""
        db, store, _ = _seed(tmp_path)

        def fake_plan(*_a, **_kw):
            return BackfillPlan(
                lesson_updates=[LessonUpdate(lesson_id="orphan", new_pairs={"Kako si?": "How are you?"})],
                cloze_updates=[],
            )

        monkeypatch.setattr(
            "app.anki.backfill_cloze_sentence_translations.plan_backfill",
            fake_plan,
        )

        result = apply_backfill(db, store, dry_run=False)
        # The orphan is counted in lessons_updated (linen count from the plan)
        # but the write was skipped because get_lesson returned None.
        assert result.lessons_updated == 1
        assert result.cloze_updated == 0

    def test_apply_skips_lesson_when_row_disappears(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """apply_backfill skips lesson when curriculum_id/day query returns no row."""
        db, store, db_path = _seed(tmp_path)
        _add_translated_lesson(store, "l1", [("Kako si?", "How are you?")])

        def fake_plan(*_a, **_kw):
            return BackfillPlan(
                lesson_updates=[LessonUpdate(lesson_id="l1", new_pairs={"Kako si?": "How are you?"})],
                cloze_updates=[],
            )

        monkeypatch.setattr(
            "app.anki.backfill_cloze_sentence_translations.plan_backfill",
            fake_plan,
        )

        from app.storage.store import ContentStore

        original_get_conn = ContentStore._get_conn
        call_count: list[int] = [0]

        # must be a @contextmanager to work with 'with' blocks
        from contextlib import contextmanager

        @contextmanager
        def patched_get_conn(self):
            call_count[0] += 1
            if call_count[0] == 2:
                raw = sqlite3.connect(db_path)
                raw.execute("DELETE FROM lessons WHERE id = 'l1'")
                raw.commit()
                raw.close()
            with original_get_conn(self) as conn:
                yield conn

        monkeypatch.setattr(ContentStore, "_get_conn", patched_get_conn)

        result = apply_backfill(db, store, dry_run=False)
        assert result.lessons_updated == 1

    def test_writes_cloze_sentence_translation_and_marks_dirty(self, tmp_path: Path):
        db, store, _ = _seed(tmp_path)
        _add_translated_lesson(store, "l1", [("Kako si?", "How are you?")])
        guid = _add_cloze(db, text="kako", source_sentence="Kako si?")

        result = apply_backfill(db, store, dry_run=False)

        assert result.cloze_updated == 1
        item = db.get_collocation_by_lemma("kako")
        assert item.syntactic_unit.source_sentence_translation == "How are you?"
        dirty = db.get_dirty_fields(guid).split(",")
        assert "sentence_translation" in dirty

    def test_persists_sentence_translations_into_lesson_metadata(self, tmp_path: Path):
        db, store, db_path = _seed(tmp_path)
        _add_translated_lesson(store, "l1", [("Kako si?", "How are you?")])

        apply_backfill(db, store, dry_run=False)

        # Re-open store and verify the lesson row's data_json now carries sentence_translations
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT data_json FROM lessons WHERE id = 'l1'").fetchone()
        meta = json.loads(row["data_json"])["generation_metadata"]
        assert meta["sentence_translations"] == {"Kako si?": "How are you?"}

    def test_dry_run_changes_nothing(self, tmp_path: Path):
        db, store, _ = _seed(tmp_path)
        _add_translated_lesson(store, "l1", [("Kako si?", "How are you?")])
        guid = _add_cloze(db, text="kako", source_sentence="Kako si?")

        result = apply_backfill(db, store, dry_run=True)

        assert result.cloze_updated == 1  # plan count, not actual writes
        item = db.get_collocation_by_lemma("kako")
        assert item.syntactic_unit.source_sentence_translation == ""
        assert db.get_dirty_fields(guid) == ""

    def test_does_not_overwrite_existing_metadata_keys(self, tmp_path: Path):
        """`token_glosses` already present in metadata is preserved; `sentence_translations` first-key wins."""
        db, store, _ = _seed(tmp_path)
        # Lesson with TRANSLATED + pre-existing partial sentence_translations
        phrases = [
            Phrase(text="Kako si?", voice_id="v", language_code="sl"),
            Phrase(text="How are you?", voice_id="v", language_code="en"),
        ]
        lesson = Lesson(
            title="t",
            language_code="sl",
            sections=[Section(section_type=SectionType.TRANSLATED, phrases=phrases)],
            generation_metadata={
                "token_glosses": {"a": "b"},
                "sentence_translations": {"Kako si?": "PRE-EXISTING"},
            },
        )
        store.save_lesson("l1", "c1", 1, lesson)

        apply_backfill(db, store, dry_run=False)

        restored = store.get_lesson("l1")
        meta = restored.generation_metadata
        assert meta["token_glosses"] == {"a": "b"}
        assert meta["sentence_translations"] == {"Kako si?": "PRE-EXISTING"}


class TestMainCLI:
    def test_main_missing_db(self, tmp_path: Path):
        """CLI returns 1 when TT database file does not exist."""
        from app.anki.backfill_cloze_sentence_translations import main

        nonexistent = tmp_path / "nope.db"
        rc = main(["--tt-db", str(nonexistent)])
        assert rc == 1

    def test_main_dry_run_with_empty_db(self, tmp_path: Path):
        """CLI dry-run against empty DB returns 0 with no updates."""
        from app.anki.backfill_cloze_sentence_translations import main

        db_path = tmp_path / "tt.db"
        SRSDatabase(str(db_path))  # create schema
        ContentStore(str(db_path))
        rc = main(["--dry-run", "--tt-db", str(db_path)])
        assert rc == 0

    def test_main_wet_run_updates_cloze(self, tmp_path: Path):
        """CLI wet run against a DB with a matchable cloze returns 0 and updates the row."""
        from app.anki.backfill_cloze_sentence_translations import main

        db_path = tmp_path / "tt.db"
        db = SRSDatabase(str(db_path))
        store = ContentStore(str(db_path))
        _add_translated_lesson(store, "l1", [("Kako si?", "How are you?")])
        _add_cloze(db, text="kako", source_sentence="Kako si?")
        rc = main(["--tt-db", str(db_path)])
        assert rc == 0
        # verify the cloze was updated
        item = db.get_collocation_by_lemma("kako")
        assert item is not None
        assert item.syntactic_unit.source_sentence_translation == "How are you?"

    def test_main_with_unmatched_cloze(self, tmp_path: Path):
        """CLI prints unmatched rows when no lesson translation exists."""
        from app.anki.backfill_cloze_sentence_translations import main

        db_path = tmp_path / "tt.db"
        db = SRSDatabase(str(db_path))
        ContentStore(str(db_path))
        _add_cloze(db, text="vsak", source_sentence="Odprto je vsak dan")
        rc = main(["--tt-db", str(db_path)])
        assert rc == 0
        # cloze should still have empty sentence_translation
        item = db.get_collocation_by_lemma("vsak")
        assert item.syntactic_unit.source_sentence_translation == ""

    def test_main_dry_run_with_data(self, tmp_path: Path):
        """CLI dry-run shows plan but does not write."""
        from app.anki.backfill_cloze_sentence_translations import main

        db_path = tmp_path / "tt.db"
        db = SRSDatabase(str(db_path))
        store = ContentStore(str(db_path))
        _add_translated_lesson(store, "l1", [("Kako si?", "How are you?")])
        _add_cloze(db, text="kako", source_sentence="Kako si?")
        rc = main(["--dry-run", "--tt-db", str(db_path)])
        assert rc == 0
        # nothing written
        item = db.get_collocation_by_lemma("kako")
        assert item.syntactic_unit.source_sentence_translation == ""
