"""A word built from a prefix the learner has and a root they know well is said whole (tunatale-lat6).

The owner, 2026-11-09: "We could treat various affixes as well-known once they
are taught and use that to have longer items introduced", and "similarly for
roots that are well-known". A prefix is taught when its pattern passed the pace
gate, or, on the owner's deck, when the pattern is one the owner brought from
Tagalog (``review_only``: ma-/na-, and mag-/nag- since the same day: "we can
consider mag/nag learned at this point"). A root is known by the same test as a
word (``mastery.is_well_known`` on its recognition card).

Like any known word, such a word keeps no pieces, and its lone rung goes unless
it is its phrase's last word, where the backward build starts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.generation.buildup_rules import BuildupRules
from app.generation.known_buildups import trim_well_known_buildups
from app.generation.pattern_gate import taught_prefixes
from app.generation.publishing import CurriculumDayTarget, ReviewSessionTarget
from app.generation.section_builder import build_key_phrases_section
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore
from tests._helpers.lemmatizer import StubLemmatizer
from tests._helpers.localtz import local_timezone
from tests.test_pattern_gate import _MO_MI_FOUR, _at, _heard, _passed_first_review

_NARRATOR = "en-US-DavisMultilingualNeural"
_CHARON = "ceb-PH-CharonGemini"


@pytest.fixture
def db():
    return SRSDatabase(":memory:")


@pytest.fixture
def store():
    return ContentStore(":memory:")


def _card(db, text: str, *, known: bool) -> None:
    db.add_collocation(
        SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test"), language_code="ceb"
    )
    due = datetime.combine(date.today() + timedelta(days=365 if known else 1), time(4, 0), tzinfo=UTC)
    db.update_direction(
        db.get_collocation(text).guid,
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION, due_at=due, stability=90.0, difficulty=4.0, reps=6, state=SRSState.REVIEW
        ),
    )


def _lesson(*phrases: str) -> Lesson:
    kps = [{"phrase": p, "translation": "x"} for p in phrases]
    section = build_key_phrases_section(kps, {"female-1": _CHARON}, _NARRATOR, "ceb", rules=BuildupRules(3))
    natural = [Phrase(text="Natural Speed", voice_id=_NARRATOR, language_code="en", role="narrator")]
    natural += [Phrase(text=p, voice_id=_CHARON, language_code="ceb", role="female-1") for p in phrases]
    return Lesson(
        title="t",
        language_code="ceb",
        sections=[section, Section(section_type=SectionType.NATURAL_SPEED, phrases=natural)],
        key_phrases=[KeyPhraseInfo(p, "x") for p in phrases],
    )


def _kp(lesson: Lesson) -> list[str]:
    return [p.text for p in lesson.sections[0].phrases]


# --- the trim --------------------------------------------------------------------------


def test_a_taught_prefix_on_a_well_known_root_is_said_whole(db):
    _card(db, "lisod", known=True)
    lesson = _lesson("Wa mo maglisod pag-anhi nganhi?")
    before = _kp(lesson)
    assert "lisod" in before and "maglisod" in before

    trim_well_known_buildups(lesson, db, StubLemmatizer(), taught_prefixes=frozenset({"mag"}))

    after = _kp(lesson)
    assert "lisod" not in after
    assert "maglisod" not in after  # not the last word: the next rung says it
    assert "maglisod pag-anhi nganhi?" in after
    assert "-anhi" in after  # pag- is not taught: pag-anhi is still built


def test_a_root_that_is_not_well_known_keeps_the_build(db):
    _card(db, "lisod", known=False)
    lesson = _lesson("Wa mo maglisod pag-anhi nganhi?")
    before = _kp(lesson)
    trim_well_known_buildups(lesson, db, StubLemmatizer(), taught_prefixes=frozenset({"mag"}))
    assert _kp(lesson) == before


def test_a_prefix_not_taught_keeps_the_build(db):
    _card(db, "lisod", known=True)
    lesson = _lesson("Wa mo maglisod pag-anhi nganhi?")
    before = _kp(lesson)
    trim_well_known_buildups(lesson, db, StubLemmatizer(), taught_prefixes=frozenset({"mo"}))
    assert _kp(lesson) == before


def test_the_last_word_keeps_its_lone_rung_where_the_build_starts(db):
    _card(db, "lisod", known=True)
    lesson = _lesson("Wa mo maglisod.")
    trim_well_known_buildups(lesson, db, StubLemmatizer(), taught_prefixes=frozenset({"mag"}))
    assert _kp(lesson)[3:] == ["Wa mo maglisod.", "maglisod", "mo maglisod.", "Wa mo maglisod."]


def test_a_well_known_word_that_is_not_last_loses_its_lone_rung_too(db):
    _card(db, "nganhi", known=True)
    lesson = _lesson("Nganhi ba ka?", "Wa mo maglisod pag-anhi.")
    trim_well_known_buildups(lesson, db, StubLemmatizer())
    texts = _kp(lesson)
    assert "ngan" not in texts and "nganhi" not in texts
    assert texts.count("Nganhi ba ka?") == 3  # head, opening rung, closing rung


# --- which prefixes are taught ---------------------------------------------------------


def test_the_owner_has_the_prefixes_brought_from_tagalog(db, store):
    assert taught_prefixes("ceb", srs_db=db, store=store, owner=True) == frozenset({"ma", "na", "mag", "nag"})


def test_another_learner_has_none_until_a_pattern_passes(db, store):
    assert taught_prefixes("ceb", srs_db=db, store=store, owner=False) == frozenset()


def test_a_passed_pattern_adds_its_prefixes(db, store):
    with local_timezone("Asia/Manila"):
        _heard(db, store, "mo-mi", _at(9), _at(13))
        _passed_first_review(db, "mo-mi", _MO_MI_FOUR)
        assert taught_prefixes("ceb", srs_db=db, store=store, owner=False) == frozenset({"mo", "mi"})


def test_a_language_with_no_patterns_has_no_prefixes(db, store):
    assert taught_prefixes("no", srs_db=db, store=store, owner=True) == frozenset()


def test_both_publish_targets_answer_from_the_gate(db, store):
    day = CurriculumDayTarget(store, "ceb", "c", 1, pipeline=None, user_id=None)
    session = ReviewSessionTarget(store, "ceb", "s", "2026-11-09", None, None, set(), {}, owner=False)
    assert day.taught_prefixes(db) == frozenset({"ma", "na", "mag", "nag"})
    assert session.taught_prefixes(db) == frozenset()


async def test_publishing_trims_by_the_targets_taught_prefixes(db):
    import asyncio

    from app.generation import publishing
    from app.generation.publishing import publish_lesson

    _card(db, "lisod", known=True)
    written: list[Lesson] = []

    class _Target:
        def prepare(self, lesson, *, srs_db, lemmatizer) -> None:
            return None

        def taught_prefixes(self, srs_db) -> frozenset[str]:
            return frozenset({"mag"})

        def write(self, lesson) -> str:
            written.append(lesson)
            return "written"

        def invalidate_audio(self, content_id) -> None:
            raise AssertionError("nothing is being replaced")

        async def schedule_render(self, content_id, lesson) -> None:
            return None

    await publish_lesson(
        _lesson("Wa mo maglisod pag-anhi nganhi?"),
        target=_Target(),
        srs_db=db,
        lemmatizer_kwargs={"lemmatizer": StubLemmatizer(), "model_version": "stub-1"},
        replace=False,
        llm=None,
    )
    await asyncio.gather(*publishing._background_tasks)

    assert "lisod" not in _kp(written[0])


def test_a_language_with_no_affix_morphology_has_no_prefixes(db, store):
    assert taught_prefixes("en", srs_db=db, store=store, owner=True) == frozenset()
