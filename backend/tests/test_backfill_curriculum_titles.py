"""Tests for the one-shot curriculum-day-title backfill migration."""

from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import Lesson, Section, SectionType
from app.storage.backfill_curriculum_day_titles import backfill_curriculum_day_titles
from app.storage.store import ContentStore


def _lesson(title: str) -> Lesson:
    return Lesson(
        title=title,
        language_code="sl",
        sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
    )


class TestBackfillCurriculumDayTitles:
    def test_updates_mismatched_titles(self):
        store = ContentStore(":memory:")
        cur = Curriculum(
            id="c1",
            topic="Test",
            language_code="sl",
            cefr_level="A1",
            days=[
                CurriculumDay(day=1, title="Old Title", focus="grammar", collocations=[], learning_objective="obj"),
                CurriculumDay(day=2, title="Still Correct", focus="vocab", collocations=[], learning_objective="obj2"),
            ],
        )
        store.save_curriculum("c1", cur)
        store.save_lesson("l1", "c1", 1, _lesson("New Title"))
        store.save_lesson("l2", "c1", 2, _lesson("Still Correct"))

        count = backfill_curriculum_day_titles(store)

        assert count == 1
        reloaded = store.get_curriculum("c1")
        assert reloaded is not None
        assert reloaded.days[0].title == "New Title"
        assert reloaded.days[1].title == "Still Correct"

    def test_skips_curriculum_without_lessons(self):
        store = ContentStore(":memory:")
        cur = Curriculum(
            id="c1",
            topic="Test",
            language_code="sl",
            cefr_level="A1",
            days=[CurriculumDay(day=1, title="No Lesson", focus="grammar", collocations=[], learning_objective="obj")],
        )
        store.save_curriculum("c1", cur)

        count = backfill_curriculum_day_titles(store)

        assert count == 0

    def test_skips_curriculum_with_matching_titles(self):
        store = ContentStore(":memory:")
        cur = Curriculum(
            id="c1",
            topic="Test",
            language_code="sl",
            cefr_level="A1",
            days=[CurriculumDay(day=1, title="Match", focus="grammar", collocations=[], learning_objective="obj")],
        )
        store.save_curriculum("c1", cur)
        store.save_lesson("l1", "c1", 1, _lesson("Match"))

        count = backfill_curriculum_day_titles(store)

        assert count == 0

    def test_updates_only_the_stale_day(self):
        store = ContentStore(":memory:")
        cur = Curriculum(
            id="c1",
            topic="Test",
            language_code="sl",
            cefr_level="A1",
            days=[
                CurriculumDay(day=1, title="Stale", focus="grammar", collocations=[], learning_objective="obj"),
                CurriculumDay(day=2, title="Correct", focus="vocab", collocations=[], learning_objective="obj2"),
            ],
        )
        store.save_curriculum("c1", cur)
        store.save_lesson("l1", "c1", 1, _lesson("Fresh"))
        store.save_lesson("l2", "c1", 2, _lesson("Correct"))

        count = backfill_curriculum_day_titles(store)

        assert count == 1
        reloaded = store.get_curriculum("c1")
        assert reloaded is not None
        assert reloaded.days[0].title == "Fresh"
        assert reloaded.days[1].title == "Correct"
