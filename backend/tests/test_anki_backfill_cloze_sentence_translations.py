"""Tests for the one-shot backfill of cloze `sentence_translation` from stored lessons."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.anki.backfill_cloze_sentence_translations import (
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


@pytest.mark.parametrize(
    "argv,expected_exit",
    [
        (["--dry-run"], 0),
        ([], 0),
    ],
)
def test_main_smoke(tmp_path: Path, argv: list[str], expected_exit: int):
    """CLI runs successfully against an empty DB."""
    from app.anki.backfill_cloze_sentence_translations import main

    db_path = tmp_path / "tt.db"
    SRSDatabase(str(db_path))  # create schema
    ContentStore(str(db_path))
    rc = main([*argv, "--tt-db", str(db_path)])
    assert rc == expected_exit
