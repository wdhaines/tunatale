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


class TestAudioFileStorage:
    """Tests for audio file path save/get operations."""

    def test_save_and_get_audio_file(self, store):
        store.save_audio_file("a1", "l1", "/output/audio/a1.wav")
        result = store.get_audio_file("a1")
        assert result == "/output/audio/a1.wav"

    def test_get_audio_file_returns_none_when_missing(self, store):
        assert store.get_audio_file("nonexistent") is None


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
