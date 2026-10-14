"""A word the learner knows well is not built up syllable by syllable (the owner, 2026-10-14).

"Any word that is well recognized ... could also not be built up backwards.
That would save some length as well." And: "that could be everywhere,
including in the key phrases." Asked which threshold, the owner chose the
listen preview's own "well known" (``mastery.is_well_known``: recognition
marked known, or its next review 90+ days out and not due).

What goes is a known word's syllable pieces (``on``, ``ka`` of ``kaon``). The
word itself and the phrase built backwards from it stay: the learner still hears
the line put together, just not a word they own taken apart.

These run the real ``extract_transcript`` against a real in-memory deck, as
``test_drill_lines`` does, so "well known" is decided exactly as the preview
decides it. Cebuano, because the affix drill is: one lesson here carries both
kinds of buildup.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.generation import publishing
from app.generation.affix_drill import AffixDrill, DrillStep
from app.generation.known_buildups import trim_well_known_buildups
from app.generation.publishing import publish_lesson
from app.generation.section_builder import build_key_phrases_section, key_phrase_groups
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from tests._helpers.lemmatizer import StubLemmatizer

_NARRATOR = "en-US-DavisMultilingualNeural"
_CHARON = "ceb-PH-CharonGemini"
_LINES = ["Kaon na ta!", "Moadto ko sa balay."]


@pytest.fixture
def db():
    return SRSDatabase(":memory:")


def _card(db, text: str, *, known: bool) -> None:
    """A vocab card whose recognition is well known (due a year out) or not (due tomorrow)."""
    db.add_collocation(
        SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test"), language_code="ceb"
    )
    days = 365 if known else 1
    due = datetime.combine(date.today() + timedelta(days=days), time(4, 0), tzinfo=UTC)
    db.update_direction(
        db.get_collocation(text).guid,
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION, due_at=due, stability=90.0, difficulty=4.0, reps=6, state=SRSState.REVIEW
        ),
    )


def _drill(*steps: tuple[str, str | None, tuple[int, int] | None]) -> Section:
    from app.generation.section_builder import build_affix_drill_section

    drill = AffixDrill()
    drill.steps = [DrillStep("model", text, "ceb", source, span) for text, source, span in steps]
    return build_affix_drill_section(drill, narrator_voice=_NARRATOR, l2_voice=_CHARON)


def _lesson(drill: Section | None = None) -> Lesson:
    natural = [Phrase(text="Natural Speed", voice_id=_NARRATOR, language_code="en", role="narrator")]
    natural += [Phrase(text=line, voice_id=_CHARON, language_code="ceb", role="female-1") for line in _LINES]
    key_phrases = build_key_phrases_section(
        [{"phrase": "Kaon na ta!", "translation": "Let's eat!"}, {"phrase": "sa balay", "translation": "at the house"}],
        {"female-1": _CHARON},
        _NARRATOR,
        "ceb",
    )
    sections = [key_phrases, Section(section_type=SectionType.NATURAL_SPEED, phrases=natural)]
    return Lesson(
        title="Supper",
        language_code="ceb",
        sections=[*sections, *([drill] if drill else [])],
        key_phrases=[KeyPhraseInfo("Kaon na ta!", "Let's eat!"), KeyPhraseInfo("sa balay", "at the house")],
    )


def _texts(lesson: Lesson, section_type: SectionType) -> list[str]:
    section = next(s for s in lesson.sections if s.section_type is section_type)
    return [p.text for p in section.phrases]


def test_a_well_known_word_in_a_key_phrase_loses_its_syllable_pieces(db):
    _card(db, "kaon", known=True)
    lesson = _lesson()
    before = _texts(lesson, SectionType.KEY_PHRASES)
    assert [t for t in before if t in ("on", "ka")] == ["on", "ka"]

    trim_well_known_buildups(lesson, db, StubLemmatizer())

    after = _texts(lesson, SectionType.KEY_PHRASES)
    assert after == [t for t in before if t not in ("on", "ka")]
    assert "kaon" in after  # the word itself, whole
    assert after.count("Kaon na ta!") == before.count("Kaon na ta!")  # the phrase still built backwards


def test_a_word_that_is_known_but_not_well_known_keeps_its_buildup(db):
    _card(db, "balay", known=False)
    lesson = _lesson()
    before = _texts(lesson, SectionType.KEY_PHRASES)

    trim_well_known_buildups(lesson, db, StubLemmatizer())

    assert _texts(lesson, SectionType.KEY_PHRASES) == before
    assert "lay" in before


def test_the_key_phrase_groups_survive_the_trim(db):
    """Cues and annotation read groups off the section's shape; a trim must not move a boundary."""
    _card(db, "kaon", known=True)
    _card(db, "balay", known=True)
    lesson = _lesson()
    groups_before = len(key_phrase_groups(lesson.sections[0], "ceb"))

    trim_well_known_buildups(lesson, db, StubLemmatizer())

    assert len(key_phrase_groups(lesson.sections[0], "ceb")) == groups_before == 2


def test_a_well_known_word_in_the_drill_loses_its_pieces_too(db):
    _card(db, "balay", known=True)
    drill = _drill(
        ("Moadto ko sa balay.", None, None),
        ("lay", "balay", (1, 2)),
        ("ba", "balay", (0, 1)),
        ("balay", "balay", (0, 2)),
        ("sa balay.", None, None),
    )
    lesson = _lesson(drill)

    trim_well_known_buildups(lesson, db, StubLemmatizer())

    assert _texts(lesson, SectionType.AFFIX_DRILL) == ["Affix Drill", "Moadto ko sa balay.", "balay", "sa balay."]


def test_a_word_the_story_never_says_is_left_alone(db):
    """Well known is the transcript's answer, and the transcript reads the story: a
    word it never meets is not judged, and keeps its buildup."""
    _card(db, "tubig", known=True)
    lesson = _lesson(_drill(("tubig", None, None), ("big", "tubig", (1, 2)), ("tu", "tubig", (0, 1))))

    trim_well_known_buildups(lesson, db, StubLemmatizer())

    assert "big" in _texts(lesson, SectionType.AFFIX_DRILL)


def test_the_story_itself_is_never_trimmed(db):
    _card(db, "kaon", known=True)
    lesson = _lesson()

    trim_well_known_buildups(lesson, db, StubLemmatizer())

    assert _texts(lesson, SectionType.NATURAL_SPEED) == ["Natural Speed", *_LINES]


def test_the_count_trimmed_is_reported(db):
    _card(db, "kaon", known=True)

    assert trim_well_known_buildups(_lesson(), db, StubLemmatizer()) == 2


async def test_publishing_trims_what_it_writes(db):
    _card(db, "kaon", known=True)
    written: list[Lesson] = []

    class _Target:
        def prepare(self, lesson, *, srs_db, lemmatizer) -> None:
            return None

        def write(self, lesson) -> str:
            written.append(lesson)
            return "written"

        def invalidate_audio(self, content_id) -> None:
            raise AssertionError("nothing is being replaced")

        async def schedule_render(self, content_id, lesson) -> None:
            return None

    await publish_lesson(
        _lesson(),
        target=_Target(),
        srs_db=db,
        lemmatizer_kwargs={"lemmatizer": StubLemmatizer(), "model_version": "stub-1"},
        replace=False,
        llm=None,
    )
    await asyncio.gather(*publishing._background_tasks)

    assert "on" not in _texts(written[0], SectionType.KEY_PHRASES)


async def test_publishing_with_no_deck_trims_nothing():
    written: list[Lesson] = []

    class _Target:
        def prepare(self, lesson, *, srs_db, lemmatizer) -> None:
            return None

        def write(self, lesson) -> str:
            written.append(lesson)
            return "written"

        def invalidate_audio(self, content_id) -> None:
            return None

        async def schedule_render(self, content_id, lesson) -> None:
            return None

    lesson = _lesson()
    before = _texts(lesson, SectionType.KEY_PHRASES)
    await publish_lesson(lesson, target=_Target(), srs_db=None, lemmatizer_kwargs={}, replace=False, llm=None)

    assert _texts(written[0], SectionType.KEY_PHRASES) == before


async def test_a_trim_that_fails_never_sinks_the_publish(db, caplog):
    import logging

    written: list[Lesson] = []

    class _Target:
        def prepare(self, lesson, *, srs_db, lemmatizer) -> None:
            return None

        def write(self, lesson) -> str:
            written.append(lesson)
            return "written"

        def invalidate_audio(self, content_id) -> None:
            return None

        async def schedule_render(self, content_id, lesson) -> None:
            return None

    class _Broken(StubLemmatizer):
        def analyze_sentence(self, *args, **kwargs):
            raise RuntimeError("lemmatizer down")

    lesson = _lesson()
    before = _texts(lesson, SectionType.KEY_PHRASES)
    with caplog.at_level(logging.WARNING):
        await publish_lesson(
            lesson, target=_Target(), srs_db=db, lemmatizer_kwargs={"lemmatizer": _Broken()}, replace=False, llm=None
        )
        await asyncio.gather(*publishing._background_tasks)

    assert _texts(written[0], SectionType.KEY_PHRASES) == before
    assert "Known-word trim failed; the lesson keeps every buildup" in caplog.text
