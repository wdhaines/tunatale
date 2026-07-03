"""Tests for the one-shot lowercase-token_glosses migration."""

from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.lowercase_glosses import _is_lowercase, _lowercase_keys, lowercase_glosses
from app.storage.store import ContentStore


def _lesson(token_glosses: dict[str, str]) -> Lesson:
    return Lesson(
        title="Test",
        language_code="sl",
        sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
        generation_metadata={"token_glosses": token_glosses},
    )


class TestIsLowercase:
    def test_all_lowercase(self):
        assert _is_lowercase({"a": "1", "b": "2"})

    def test_mixed_case(self):
        assert not _is_lowercase({"Hvala": "thanks"})

    def test_empty(self):
        assert _is_lowercase({})


class TestLowercaseKeys:
    def test_lowercases_keys(self):
        assert _lowercase_keys({"Hvala": "thanks"}) == {"hvala": "thanks"}

    def test_first_wins_on_collision(self):
        result = _lowercase_keys({"Hvala": "thanks", "hvala": "collision"})
        assert result == {"hvala": "thanks"}

    def test_identity_when_already_lowercase(self):
        result = _lowercase_keys({"a": "1", "b": "2"})
        assert result == {"a": "1", "b": "2"}

    def test_empty(self):
        assert _lowercase_keys({}) == {}


class TestLowercaseGlosses:
    def test_lowercases_stored_lessons(self):
        store = ContentStore(":memory:")
        store.save_lesson("l1", "c1", 1, _lesson({"Hvala": "thanks", "Lepo": "nice"}))
        store.save_lesson("l2", "c1", 2, _lesson({"a": "1"}))  # already lowercase

        count = lowercase_glosses(store)

        assert count == 1
        l1 = store.get_lesson("l1")
        assert l1.generation_metadata["token_glosses"] == {"hvala": "thanks", "lepo": "nice"}
        l2 = store.get_lesson("l2")
        assert l2.generation_metadata["token_glosses"] == {"a": "1"}

    def test_skips_lesson_without_metadata(self):
        store = ContentStore(":memory:")
        lesson = Lesson(
            title="No meta",
            language_code="sl",
            sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
        )
        store.save_lesson("l1", "c1", 1, lesson)

        count = lowercase_glosses(store)
        assert count == 0

    def test_skips_lesson_without_token_glosses(self):
        store = ContentStore(":memory:")
        lesson = Lesson(
            title="No glosses",
            language_code="sl",
            sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
            generation_metadata={"other": "data"},
        )
        store.save_lesson("l1", "c1", 1, lesson)

        count = lowercase_glosses(store)
        assert count == 0

    def test_first_wins_on_collision_during_migration(self):
        store = ContentStore(":memory:")
        store.save_lesson("l1", "c1", 1, _lesson({"Hvala": "thanks", "hvala": "collision"}))

        count = lowercase_glosses(store)

        assert count == 1
        l1 = store.get_lesson("l1")
        assert l1.generation_metadata["token_glosses"] == {"hvala": "thanks"}
