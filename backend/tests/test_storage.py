"""ContentStore unit tests."""

import pytest

from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore


@pytest.fixture
def store():
    s = ContentStore(":memory:")
    yield s
    s.close()


def _make_curriculum(id: str = "c1") -> Curriculum:
    return Curriculum(
        id=id,
        topic="ordering coffee",
        language_code="sl",
        cefr_level="A2",
        days=[
            CurriculumDay(
                day=1,
                title="Day 1",
                focus="greetings",
                learning_objective="say hello",
                story_guidance="use dober dan",
                collocations=["dober dan"],
            )
        ],
    )


def _make_lesson() -> Lesson:
    return Lesson(
        title="Day 1",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.KEY_PHRASES,
                phrases=[
                    Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                ],
            )
        ],
    )


class TestCurriculumStorage:
    """Tests for curriculum save/get/list operations."""

    def test_save_and_get_curriculum(self, store):
        curriculum = _make_curriculum("c1")
        store.save_curriculum("c1", curriculum)
        restored = store.get_curriculum("c1")
        assert restored is not None
        assert restored.topic == "ordering coffee"
        assert restored.language_code == "sl"
        assert restored.cefr_level == "A2"
        assert len(restored.days) == 1
        assert restored.days[0].collocations == ["dober dan"]

    def test_get_curriculum_returns_none_when_missing(self, store):
        assert store.get_curriculum("nonexistent") is None

    def test_list_curricula_returns_all(self, store):
        store.save_curriculum("c1", _make_curriculum("c1"))
        store.save_curriculum("c2", Curriculum(id="c2", topic="shopping", language_code="sl", cefr_level="B1"))
        result = store.list_curricula()
        assert len(result) == 2
        topics = {r["topic"] for r in result}
        assert topics == {"ordering coffee", "shopping"}
        assert all("created_at" in r for r in result)

    def test_list_curricula_empty(self, store):
        assert store.list_curricula() == []


class TestLessonStorage:
    """Tests for lesson save/get operations."""

    def test_save_and_get_lesson(self, store):
        lesson = _make_lesson()
        store.save_lesson("l1", "c1", 1, lesson)
        restored = store.get_lesson("l1")
        assert restored is not None
        assert restored.title == "Day 1"
        assert len(restored.sections) == 1
        assert restored.sections[0].section_type == SectionType.KEY_PHRASES
        assert restored.sections[0].phrases[0].text == "dober dan"
        assert restored.sections[0].phrases[0].role == "female-1"

    def test_get_lesson_returns_none_when_missing(self, store):
        assert store.get_lesson("nonexistent") is None

    def test_get_lesson_row_returns_full_row(self, store):
        """get_lesson_row returns dict with id, curriculum_id, day, and data_json."""
        lesson = _make_lesson()
        store.save_lesson("l1", "c1", 3, lesson)
        row = store.get_lesson_row("l1")
        assert row is not None
        assert row["id"] == "l1"
        assert row["curriculum_id"] == "c1"
        assert row["day"] == 3
        assert "data_json" in row

    def test_get_lesson_row_returns_none_when_missing(self, store):
        """get_lesson_row returns None for unknown lesson_id."""
        assert store.get_lesson_row("nonexistent") is None

    def test_get_all_token_glosses_merges_lessons(self, store):
        """Merges token_glosses from all lessons; later rows win on duplicate lemmas."""
        lesson1 = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            generation_metadata={"token_glosses": {"banka": "bank", "hiša": "house"}},
        )
        lesson2 = Lesson(
            title="Day 2",
            language_code="sl",
            sections=[],
            generation_metadata={"token_glosses": {"banka": "bank (updated)", "miza": "table"}},
        )
        store.save_lesson("l1", "c1", 1, lesson1)
        store.save_lesson("l2", "c1", 2, lesson2)
        glosses = store.get_all_token_glosses()
        assert glosses["hiša"] == "house"
        assert glosses["miza"] == "table"
        assert glosses["banka"] == "bank (updated)"  # later lesson wins

    def test_get_all_token_glosses_empty_store(self, store):
        """Returns empty dict when no lessons exist."""
        assert store.get_all_token_glosses() == {}

    def test_get_all_token_glosses_skips_lessons_without_glosses(self, store):
        """Lessons without token_glosses in generation_metadata are safely skipped."""
        lesson = _make_lesson()  # no generation_metadata
        store.save_lesson("l1", "c1", 1, lesson)
        assert store.get_all_token_glosses() == {}

    def test_list_lessons_returns_all_with_ids(self, store):
        """list_lessons yields (lesson_id, curriculum_id, day, Lesson) for every row."""
        store.save_lesson("l1", "c1", 1, _make_lesson())
        store.save_lesson("l2", "c1", 2, _make_lesson())
        rows = store.list_lessons()
        assert {(lid, cid, day) for lid, cid, day, _ in rows} == {("l1", "c1", 1), ("l2", "c1", 2)}
        assert all(isinstance(lesson, Lesson) for _, _, _, lesson in rows)

    def test_list_lessons_empty(self, store):
        assert store.list_lessons() == []


class TestAudioFileStorage:
    """Tests for audio file path save/get operations."""

    def test_save_and_get_audio_file(self, store):
        store.save_audio_file("a1", "l1", "/output/audio/a1.wav")
        result = store.get_audio_file_row("a1")
        assert result["file_path"] == "/output/audio/a1.wav"

    def test_get_audio_file_returns_none_when_missing(self, store):
        assert store.get_audio_file_row("nonexistent") is None


class TestSectionAudioStorage:
    """Tests for per-section audio file save/list/get operations."""

    def test_save_audio_file_with_section_metadata(self, store):
        """save_audio_file accepts section_index and section_type kwargs."""
        store.save_audio_file("full1", "l1", "/output/full1.wav")
        store.save_audio_file("sec0", "l1", "/output/sec0.wav", section_index=0, section_type="key_phrases")
        store.save_audio_file("sec1", "l1", "/output/sec1.wav", section_index=1, section_type="natural_speed")

        assert store.get_audio_file_row("full1")["file_path"] == "/output/full1.wav"
        assert store.get_audio_file_row("sec0")["file_path"] == "/output/sec0.wav"

    def test_list_audio_files_for_lesson_ordering(self, store):
        """list_audio_files_for_lesson returns full row first, then sections in order."""
        store.save_audio_file("sec1", "l1", "/s1.wav", section_index=1, section_type="natural_speed")
        store.save_audio_file("full", "l1", "/full.wav")
        store.save_audio_file("sec0", "l1", "/s0.wav", section_index=0, section_type="key_phrases")

        rows = store.list_audio_files_for_lesson("l1")
        assert len(rows) == 3
        # Full row first (section_index is NULL)
        assert rows[0]["id"] == "full"
        assert rows[0]["section_index"] is None
        # Then sections in order
        assert rows[1]["section_index"] == 0
        assert rows[1]["section_type"] == "key_phrases"
        assert rows[2]["section_index"] == 1

    def test_list_audio_files_for_lesson_empty(self, store):
        """list_audio_files_for_lesson returns empty list when no audio exists."""
        assert store.list_audio_files_for_lesson("nonexistent") == []

    def test_get_audio_file_row(self, store):
        """get_audio_file_row returns dict with all fields including section metadata."""
        store.save_audio_file("full1", "l1", "/full.wav")
        store.save_audio_file("sec0", "l1", "/s0.wav", section_index=0, section_type="key_phrases")

        full_row = store.get_audio_file_row("full1")
        assert full_row is not None
        assert full_row["file_path"] == "/full.wav"
        assert full_row["lesson_id"] == "l1"
        assert full_row["section_index"] is None
        assert full_row["section_type"] is None

        sec_row = store.get_audio_file_row("sec0")
        assert sec_row is not None
        assert sec_row["section_index"] == 0
        assert sec_row["section_type"] == "key_phrases"

    def test_get_audio_file_row_returns_none_when_missing(self, store):
        """get_audio_file_row returns None for unknown audio_id."""
        assert store.get_audio_file_row("nonexistent") is None

    def test_schema_migration_adds_missing_columns(self, tmp_path):
        """ContentStore adds section_index/section_type columns when opening an old-schema DB."""
        import sqlite3

        db_file = str(tmp_path / "old.db")

        # Create DB with original schema (no section columns)
        conn = sqlite3.connect(db_file)
        conn.execute("""
            CREATE TABLE audio_files (
                id TEXT PRIMARY KEY,
                lesson_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("INSERT INTO audio_files (id, lesson_id, file_path) VALUES ('old1', 'l1', '/old.wav')")
        conn.commit()
        conn.close()

        # Opening with ContentStore should add the new columns
        with ContentStore(db_file) as store:
            row = store.get_audio_file_row("old1")
            assert row is not None
            assert row["section_index"] is None
            assert row["section_type"] is None

            # Can save new-style rows
            store.save_audio_file("new1", "l1", "/new.wav", section_index=0, section_type="key_phrases")
            new_row = store.get_audio_file_row("new1")
            assert new_row["section_index"] == 0


class TestLessonDays:
    """Tests for get_lesson_days bulk query."""

    def test_returns_all_days_with_lessons(self, store):
        lesson = _make_lesson()
        store.save_curriculum("c1", _make_curriculum("c1"))
        store.save_lesson("l1", "c1", 1, lesson)
        store.save_lesson("l3", "c1", 3, lesson)
        result = store.get_lesson_days("c1")
        days = [r["day"] for r in result]
        assert days == [1, 3]
        assert result[0]["lesson_id"] == "l1"
        assert result[1]["lesson_id"] == "l3"

    def test_returns_latest_lesson_per_day(self, store):
        lesson = _make_lesson()
        store.save_lesson("l1-old", "c1", 1, lesson)
        store.save_lesson("l1-new", "c1", 1, lesson)
        result = store.get_lesson_days("c1")
        assert len(result) == 1
        assert result[0]["lesson_id"] == "l1-new"

    def test_empty_for_unknown_curriculum(self, store):
        assert store.get_lesson_days("unknown") == []


class TestPersistence:
    """Tests for file-backed store persistence and multi-database coexistence."""

    def test_file_based_store_persists(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        curriculum = _make_curriculum("c1")

        with ContentStore(db_file) as s1:
            s1.save_curriculum("c1", curriculum)

        with ContentStore(db_file) as s2:
            restored = s2.get_curriculum("c1")

        assert restored is not None
        assert restored.topic == "ordering coffee"

    def test_file_db_save_lesson(self, tmp_path):
        """save_lesson with file-backed store covers if self._in_memory: False branch (150->exit)."""
        db_file = str(tmp_path / "lessons.db")
        lesson = _make_lesson()
        with ContentStore(db_file) as s:
            s.save_lesson("l1", "c1", 1, lesson)
            restored = s.get_lesson("l1")
        assert restored is not None
        assert restored.title == "Day 1"

    def test_shared_db_with_srs_database(self, tmp_path):
        from app.srs.database import SRSDatabase

        db_file = str(tmp_path / "shared.db")

        srs = SRSDatabase(db_file)
        content = ContentStore(db_file)

        # All 5 tables should exist
        with content._get_conn() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        table_names = {r[0] for r in rows}
        assert {"collocations", "violations", "curricula", "lessons", "audio_files"} <= table_names

        srs.close()
        content.close()
