"""The grammar lesson: one affix pattern, drilled on roots the learner understands (tunatale-ve4p.5).

A grammar lesson has no story and makes no model call. What goes into it is
read off two things the learner already has:

* **their cards**: a root is drilled once its RECOGNITION card is in review
  ("understanding is enough", the owner, 2026-10-07);
* **their lessons**: which forms they have already met, and the whole lines
  the drill ends on.

The oracle for the plan is the owner's own Cebuano deck as it stood on
2026-10-07 (read from a copy): five understood roots take mo-/mi-, the two
lessons had used exactly one form of four of them and neither form of lakaw.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from app.generation.affix_drill import build_affix_drill, drill_root
from app.generation.grammar_lesson import (
    GrammarPlan,
    build_grammar_lesson,
    grammar_day,
    plan_grammar_lesson,
    understood_roots,
)
from app.languages import get_a1_morphology, get_language
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.storage.store import ContentStore

_PATTERNS = {p.key: p for p in get_a1_morphology("ceb").patterns}
_MO_MI = _PATTERNS["mo-mi"]
_NARRATOR = "en-US-DavisMultilingualNeural"
# The owner's first mo-/mi- lesson: modelled roots, then the ones left to build.
_FORMS = ("mouban", "miuban", "moinom", "miinom", "moadto", "miadto", "moanhi", "mianhi", "molakaw", "milakaw")
_CHARON = "ceb-PH-CharonGemini"


def _card(db, root: str, *, recognition: SRSState, production: SRSState = SRSState.NEW) -> None:
    db.add_collocation(
        SyntacticUnit(text=root, translation=root, word_count=1, difficulty=1, source="test"), language_code="ceb"
    )
    guid = db.get_collocation(root).guid
    due = datetime.combine(date.today(), time(4, 0), tzinfo=UTC)
    for direction, state in ((Direction.RECOGNITION, recognition), (Direction.PRODUCTION, production)):
        db.update_direction(
            guid,
            direction,
            DirectionState(direction=direction, due_at=due, stability=5.0, difficulty=4.0, reps=5, state=state),
        )


def _story_lesson(title: str, lines: list[tuple[str, str]]) -> Lesson:
    """A thematic lesson as stored: the same lines natural, then each with its English."""
    natural = [Phrase(text="Natural Speed", voice_id=_NARRATOR, language_code="en", role="narrator")]
    translated = [Phrase(text="English After", voice_id=_NARRATOR, language_code="en", role="narrator")]
    for text, english in lines:
        line = Phrase(text=text, voice_id=_CHARON, language_code="ceb", role="male-1")
        natural.append(line)
        translated += [line, Phrase(text=english, voice_id=_NARRATOR, language_code="en", role="narrator")]
    return Lesson(
        title=title,
        language_code="ceb",
        sections=[
            Section(section_type=SectionType.NATURAL_SPEED, phrases=natural),
            Section(section_type=SectionType.TRANSLATED, phrases=translated),
        ],
    )


# The owner's two lessons, cut down to the lines that bear on mo-/mi-.
_WAKE = [
    ("Maayong gabii, Liza. Moadto ko sa haya.", "Good evening, Liza. I'm going to the wake."),
    ("Salamat kaayo, Paul. Nalipay ko nga mianhi ka.", "Thank you very much, Paul. I'm glad that you came."),
    ("Mouban ko ugma.", "I will come along tomorrow."),
    ("Miinom sila og kape ug nag-istorya sila.", "They drank coffee and they talked."),
    ("Mag-ampo pud ko, Nang.", "I will pray too, Nang."),
]
_BURIAL = [
    ("Buntag sa Jimenez. Naglakaw si Paul.", "Morning in Jimenez. Paul is walking."),
    ("Oo, moadto ko.", "Yes, I'll go."),
]


@pytest.fixture
def owner(srs_db):
    """The owner's deck and lessons, as far as mo-/mi- is concerned."""
    for root in ("inom", "uban"):
        _card(srs_db, root, recognition=SRSState.REVIEW, production=SRSState.REVIEW)
    for root in ("lakaw", "adto", "anhi"):
        _card(srs_db, root, recognition=SRSState.REVIEW)
    for root in ("balik", "kaon", "sulod", "sulti", "tan-aw"):
        _card(srs_db, root, recognition=SRSState.NEW)
    store = ContentStore(":memory:")
    store.save_curriculum(
        "funeral",
        Curriculum(
            id="funeral",
            topic="Attending a funeral",
            language_code="ceb",
            cefr_level="A1",
            days=[
                CurriculumDay(day=1, title="A wake", focus="f", collocations=["haya"], learning_objective="o"),
                CurriculumDay(day=2, title="A burial", focus="f", collocations=["lubong"], learning_objective="o"),
            ],
        ),
    )
    store.save_lesson("wake-1", "funeral", 1, _story_lesson("An Evening Wake", _WAKE))
    store.save_lesson("burial-1", "funeral", 2, _story_lesson("The Burial Morning", _BURIAL))
    return srs_db, store


# --- which roots -----------------------------------------------------------


def test_a_root_is_understood_once_its_recognition_card_is_in_review(owner):
    """Production of the bare root is not asked for: three of these five are still NEW there."""
    srs_db, _ = owner
    assert understood_roots("ceb", _MO_MI, srs_db) == ["lakaw", "inom", "adto", "anhi", "uban"]


def test_a_known_card_counts_and_a_learning_one_does_not(srs_db):
    _card(srs_db, "inom", recognition=SRSState.KNOWN)
    _card(srs_db, "adto", recognition=SRSState.LEARNING)
    assert understood_roots("ceb", _MO_MI, srs_db) == ["inom"]


def test_a_root_with_no_card_is_not_understood(srs_db):
    assert understood_roots("ceb", _MO_MI, srs_db) == []


def test_only_roots_the_pattern_can_drill_are_offered(owner):
    """ampo is understood too, but nobody has worded it for mo-/mi-."""
    srs_db, _ = owner
    _card(srs_db, "ampo", recognition=SRSState.REVIEW)
    assert "ampo" not in understood_roots("ceb", _MO_MI, srs_db)
    assert understood_roots("ceb", _PATTERNS["mag-nag"], srs_db) == ["lakaw", "ampo", "uban"]


# --- modelled, or new ------------------------------------------------------


def test_a_form_whose_english_is_not_built_on_the_roots_cannot_be_left_to_the_learner():
    """uban is "accompany"; mouban is "will go along". Nothing lets the learner guess that."""
    assert drill_root("ceb", "lakaw", _MO_MI).transparent
    assert drill_root("ceb", "adto", _MO_MI).transparent  # go: will go / went
    assert not drill_root("ceb", "uban", _MO_MI).transparent
    assert not drill_root("ceb", "kita", _PATTERNS["mag-nag"]).transparent  # see: will meet / met
    assert drill_root("ceb", "lipay", _PATTERNS["ma-na"]).transparent  # happy: will be happy / is happy


def test_the_owners_first_lesson(owner):
    """uban has to be modelled; the best-met roots join it; the least-met are left to build."""
    srs_db, store = owner

    plan = plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=store, curriculum_id="funeral")

    assert plan == GrammarPlan(
        pattern=_MO_MI,
        roots=("uban", "inom", "adto"),
        new_roots=("anhi", "lakaw"),
        lines=(
            ("Mouban ko ugma.", "I will come along tomorrow."),
            ("Oo, moadto ko.", "Yes, I'll go."),
            ("Miinom sila og kape ug nag-istorya sila.", "They drank coffee and they talked."),
        ),
        forms=_FORMS,
    )


def test_the_new_roots_end_on_the_one_never_met_in_this_pattern(owner):
    """lakaw has only been heard as maglakaw / naglakaw: getting molakaw right is the affix."""
    srs_db, store = owner
    plan = plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=store, curriculum_id="funeral")
    assert plan.new_roots[-1] == "lakaw"


def test_with_no_lessons_the_plan_falls_back_to_the_tables_order(owner):
    srs_db, _ = owner
    empty = ContentStore(":memory:")

    plan = plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=empty, curriculum_id="funeral")

    assert (plan.roots, plan.new_roots, plan.lines) == (("uban", "lakaw", "inom"), ("adto", "anhi"), ())
    assert plan.forms[:4] == ("mouban", "miuban", "molakaw", "milakaw")


def test_too_few_understood_roots_is_refused_with_the_count(srs_db):
    _card(srs_db, "inom", recognition=SRSState.REVIEW)
    with pytest.raises(ValueError, match="needs at least 2 roots you understand, and mo-mi has 1: inom"):
        plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=ContentStore(":memory:"), curriculum_id="x")


def test_a_language_without_patterns_is_refused(srs_db):
    with pytest.raises(ValueError, match="registers no affix patterns"):
        understood_roots("no", _MO_MI, srs_db)


# --- which lines -----------------------------------------------------------


def test_a_line_is_one_sentence_that_uses_a_drilled_form(owner):
    """Two-sentence lines are left out: the prompt would ask for both."""
    srs_db, store = owner
    plan = plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=store, curriculum_id="funeral")
    texts = [text for text, _ in plan.lines]
    assert "Maayong gabii, Liza. Moadto ko sa haya." not in texts
    assert "Mag-ampo pud ko, Nang." not in texts  # one sentence, but not this pattern


def test_the_drill_ends_on_three_lines_at_most_the_shortest_first(owner):
    srs_db, store = owner
    extra = [
        ("Moinom ko og tubig karon dayon.", "I will drink water right now."),
        ("Miadto siya.", "He went."),
    ]
    store.save_lesson("more-1", "funeral", 3, _story_lesson("More", extra))

    plan = plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=store, curriculum_id="funeral")

    assert [text for text, _ in plan.lines] == ["Miadto siya.", "Mouban ko ugma.", "Oo, moadto ko."]


def test_an_earlier_grammar_lesson_is_not_a_source_of_lines(owner):
    """Lines are read from stories. A drill's whole lines came from those same
    stories, and its section is not one the planner reads."""
    srs_db, store = owner
    plan = plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=store, curriculum_id="funeral")
    store.save_lesson("drill-1", "funeral", 3, build_grammar_lesson(get_language("ceb"), plan))

    again = plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=store, curriculum_id="funeral")

    assert again == plan


def test_a_superseded_lesson_is_not_read(owner):
    srs_db, store = owner
    store.save_lesson("wake-0", "funeral", 1, _story_lesson("An older wake", [("Moanhi siya.", "He will come.")]))
    store.save_lesson("wake-2", "funeral", 1, _story_lesson("An Evening Wake", _WAKE))

    plan = plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=store, curriculum_id="funeral")

    assert "Moanhi siya." not in [text for text, _ in plan.lines]


# --- the lesson ------------------------------------------------------------


def test_the_lesson_is_one_drill_section_and_says_what_it_is(owner):
    srs_db, store = owner
    plan = plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=store, curriculum_id="funeral")

    lesson = build_grammar_lesson(get_language("ceb"), plan)

    assert (lesson.kind, lesson.language_code, lesson.title) == ("grammar", "ceb", "Affix drill: mo- / mi-")
    assert [s.section_type for s in lesson.sections] == [SectionType.AFFIX_DRILL]
    assert lesson.key_phrases == []
    assert lesson.generation_metadata["affix_drill"] == {
        "pattern": "mo-mi",
        "roots": ["uban", "inom", "adto"],
        "new_roots": ["anhi", "lakaw"],
    }
    drill = build_affix_drill(
        "ceb", _MO_MI, roots=list(plan.roots), new_roots=list(plan.new_roots), lines=list(plan.lines)
    )
    assert [p.text for p in lesson.sections[0].phrases[1:]] == [step.text for step in drill.steps]


def test_the_forms_are_said_by_the_voice_that_reads_the_key_phrases(owner):
    srs_db, store = owner
    language = get_language("ceb")
    plan = plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=store, curriculum_id="funeral")

    lesson = build_grammar_lesson(language, plan)

    voices = {p.voice_id for p in lesson.sections[0].phrases if p.language_code == "ceb"}
    assert voices == {language.tts_voice_map["female-1"]}
    assert lesson.narrator_voice == language.tts_voice_map["narrator"]


def test_a_lesson_keeps_its_kind_in_storage():
    assert Lesson.from_json(Lesson(title="t", language_code="ceb", kind="grammar").to_json()).kind == "grammar"


def test_a_lesson_stored_before_kinds_existed_is_thematic():
    assert Lesson.from_json('{"title": "t", "language_code": "ceb"}').kind == "thematic"


# --- the day ---------------------------------------------------------------


def test_the_day_names_the_pattern_and_lists_the_forms(owner):
    srs_db, store = owner
    plan = plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=store, curriculum_id="funeral")

    day = grammar_day(3, plan)

    assert (day.day, day.kind, day.pattern, day.title) == (3, "grammar", "mo-mi", "Affix drill: mo- / mi-")
    assert day.collocations == list(_FORMS)


def test_a_day_is_thematic_unless_it_says_otherwise():
    day = CurriculumDay(day=1, title="t", focus="f", collocations=["c"], learning_objective="o")
    assert (day.kind, day.pattern) == ("thematic", "")


def test_a_day_of_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="kind must be one of thematic, grammar"):
        CurriculumDay(day=1, title="t", focus="f", collocations=["c"], learning_objective="o", kind="quiz")


def test_a_curriculum_stored_before_kinds_existed_still_loads():
    stored = (
        '{"id": "c", "topic": "t", "language_code": "ceb", "cefr_level": "A1", "metadata": {}, "days": '
        '[{"day": 1, "title": "t", "focus": "f", "collocations": ["c"], "learning_objective": "o", "story_guidance": ""}]}'
    )
    assert Curriculum.from_json(stored).days[0].kind == "thematic"
