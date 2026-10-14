"""``admitted_lines`` — the story lines a drill may end on (bd tunatale-ve4p.20).

The rule under test is the owner's, decided 2026-10-09: a closing line is asked
for only when every word in it is one the learner understands or one the lesson
teaches. "Understands" is the reader's answer, so these run the real
``extract_transcript`` against a real in-memory SRS database; only the
lemmatizer is a stub, and only where a test is about an inflected form.

Cebuano, because the affix drill is: the lines are the kind a drill ends on.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from app.generation.drill_lines import admitted_lines, fold, understood
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from tests._helpers.lemmatizer import StubLemmatizer

_NARRATOR = "en-US-DavisMultilingualNeural"
_CHARON = "ceb-PH-CharonGemini"
_LINE = "Mouban ko ugma."


@pytest.fixture
def db():
    return SRSDatabase(":memory:")


def _card(db, text: str, state: SRSState, *, lemma: str | None = None, words: int = 1) -> None:
    """A vocab card for *text* whose recognition direction is in *state*."""
    db.add_collocation(
        SyntacticUnit(text=text, lemma=lemma, translation=text, word_count=words, difficulty=1, source="test"),
        language_code="ceb",
    )
    due = datetime.combine(date.today(), time(4, 0), tzinfo=UTC)
    db.update_direction(
        db.get_collocation(text).guid,
        Direction.RECOGNITION,
        DirectionState(direction=Direction.RECOGNITION, due_at=due, stability=5.0, difficulty=4.0, reps=5, state=state),
    )


def _lesson(*lines: str, natural: bool = True) -> Lesson:
    """A stored thematic lesson: the narrator's cue in English, then the story's lines."""
    phrases = [Phrase(text="Natural Speed", voice_id=_NARRATOR, language_code="en", role="narrator")]
    phrases += [Phrase(text=line, voice_id=_CHARON, language_code="ceb", role="male-1") for line in lines]
    section_type = SectionType.NATURAL_SPEED if natural else SectionType.TRANSLATED
    return Lesson(title="Story", language_code="ceb", sections=[Section(section_type=section_type, phrases=phrases)])


def _admitted(db, *lines: str, taught=(), lemmatizer=None) -> set[str]:
    return admitted_lines(
        _lesson(*lines), srs_db=db, lemmatizer=lemmatizer or StubLemmatizer(), taught={fold(w) for w in taught}
    )


def test_a_line_of_understood_words_is_admitted(db):
    for word in ("mouban", "ko", "ugma"):
        _card(db, word, SRSState.REVIEW)

    assert _admitted(db, _LINE) == {_LINE}


def test_one_word_the_learner_has_not_met_keeps_the_line_out(db):
    """The reported defect: `ugma` asked for cold."""
    for word in ("mouban", "ko"):
        _card(db, word, SRSState.REVIEW)

    assert _admitted(db, _LINE) == set()


@pytest.mark.parametrize(
    ("state", "has_it"),
    [
        (SRSState.KNOWN, True),
        (SRSState.REVIEW, True),
        (SRSState.LEARNING, False),
        (SRSState.RELEARNING, False),
        (SRSState.NEW, False),
        # "Stop showing me this" is not "I know this".
        (SRSState.SUSPENDED, False),
    ],
)
def test_only_a_card_in_review_or_known_is_a_word_the_learner_has(db, state, has_it):
    for word in ("mouban", "ko"):
        _card(db, word, SRSState.REVIEW)
    _card(db, "ugma", state)

    assert (_admitted(db, _LINE) == {_LINE}) is has_it


def test_what_the_lesson_teaches_counts_without_a_card(db):
    """The drilled form itself, and a thematic lesson's key phrases: the caller's *taught*."""
    for word in ("ko", "ugma"):
        _card(db, word, SRSState.REVIEW)

    assert _admitted(db, _LINE) == set()
    assert _admitted(db, _LINE, taught=["mouban"]) == {_LINE}


def test_a_taught_word_is_matched_whatever_its_case_or_hyphen(db):
    _card(db, "sila", SRSState.REVIEW)

    assert _admitted(db, "Nag-istorya sila.", taught=["nagistorya"]) == {"Nag-istorya sila."}


def test_a_word_on_the_ignore_list_does_not_keep_a_line_out(db):
    """A name the learner chose not to card is not a word they are waiting to learn."""
    for word in ("mouban", "ugma"):
        _card(db, word, SRSState.REVIEW)
    db.add_ignored_lemma("ceb", "paul")

    assert _admitted(db, "Mouban si Paul ugma.") == set()  # `si` is still unmet
    _card(db, "si", SRSState.REVIEW)
    assert _admitted(db, "Mouban si Paul ugma.") == {"Mouban si Paul ugma."}


def test_a_name_with_no_card_and_no_ignore_keeps_the_line_out(db):
    for word in ("mouban", "si", "ugma"):
        _card(db, word, SRSState.REVIEW)

    assert _admitted(db, "Mouban si Paul ugma.") == set()


def test_an_inflected_word_is_judged_by_the_card_the_reader_finds_for_it(db):
    """The reader's resolution, not a spelling lookup: `mipalit` is the `palit` card."""
    for word in ("ko", "palit"):
        _card(db, word, SRSState.REVIEW)
    stub = StubLemmatizer()
    stub.set_lemma("Mipalit", "palit")

    assert _admitted(db, "Mipalit ko.") == set()  # no lemma: `mipalit` has no card
    assert _admitted(db, "Mipalit ko.", lemmatizer=stub) == {"Mipalit ko."}


def test_a_word_inside_an_understood_phrase_card_counts(db):
    """`Maayong buntag` is one card; neither word has its own."""
    _card(db, "maayong buntag", SRSState.REVIEW, words=2)

    assert _admitted(db, "Maayong buntag.") == {"Maayong buntag."}


def test_a_word_inside_a_phrase_card_still_being_learned_does_not(db):
    _card(db, "maayong buntag", SRSState.LEARNING, words=2)

    assert _admitted(db, "Maayong buntag.") == set()


def test_each_line_is_judged_on_its_own_words(db):
    """The lines come back as written, and a neighbour's words do not vouch for a line."""
    for word in ("mouban", "ko", "ugma", "oo"):
        _card(db, word, SRSState.REVIEW)

    assert _admitted(db, "Oo, moadto ko.", _LINE, "Oo.") == {_LINE, "Oo."}


def test_a_line_with_no_words_is_not_admitted(db):
    assert _admitted(db, "...") == set()


def test_with_no_learner_to_ask_no_line_is_admitted():
    assert (
        admitted_lines(_lesson(_LINE), srs_db=None, lemmatizer=StubLemmatizer(), taught={"mouban", "ko", "ugma"})
        == set()
    )


def test_a_lesson_with_no_story_admits_nothing(db):
    """A grammar lesson has no natural-speed section to read."""
    _card(db, "oo", SRSState.REVIEW)

    assert admitted_lines(_lesson("Oo.", natural=False), srs_db=db, lemmatizer=StubLemmatizer(), taught=()) == set()


def test_without_a_lemmatizer_the_languages_own_is_used(db):
    """What the publish seam passes when nothing is injected. Under the suite's
    lowercase pin that is a spelling match, which is all this line needs."""
    for word in ("mouban", "ko", "ugma"):
        _card(db, word, SRSState.REVIEW)

    assert admitted_lines(_lesson(_LINE), srs_db=db, lemmatizer=None, taught=()) == {_LINE}


# --- a card buried for the day (the owner's day 5, 2026-10-13) -------------------
#
# Grading one side of a card buries the other until the next rollover. The owner
# reviews the deck daily, so on any day a lesson is made some understood words
# are in that state, and reading it as "not understood" dropped their lines and,
# on day 5, every root the drill could have asked for unmodelled (`anhi`, whose
# recognition side was buried by that morning's review of its production side).
# What a buried card is underneath is what the rollover sweep will turn it back
# into (``db_queue.unbury_if_needed``): REVIEW once it has been reviewed.


def _bury(db, text: str, *, reps: int, last_review: date | None = None) -> None:
    """Put *text*'s recognition side where a sibling's grade puts it."""
    due = datetime.combine(date.today(), time(4, 0), tzinfo=UTC)
    db.update_direction(
        db.get_collocation(text).guid,
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            due_at=due,
            stability=5.0,
            difficulty=4.0,
            reps=reps,
            last_review=last_review,
            state=SRSState.BURIED,
            bury_kind="sched",
        ),
    )


def test_a_word_whose_card_is_buried_for_the_day_is_still_had(db):
    for word in ("mouban", "ko", "ugma"):
        _card(db, word, SRSState.REVIEW)
    _bury(db, "ugma", reps=5)

    assert _admitted(db, _LINE) == {_LINE}


def test_a_word_buried_before_it_was_ever_reviewed_is_not_had(db):
    """A NEW card is buried too, when its sibling is introduced. It comes back NEW."""
    for word in ("mouban", "ko"):
        _card(db, word, SRSState.REVIEW)
    _card(db, "ugma", SRSState.NEW)
    _bury(db, "ugma", reps=0)

    assert _admitted(db, _LINE) == set()


def test_a_word_inside_a_phrase_whose_card_is_buried_for_the_day_is_still_had(db):
    _card(db, "maayong buntag", SRSState.REVIEW, words=2)
    _bury(db, "maayong buntag", reps=5)

    assert _admitted(db, "Maayong buntag.") == {"Maayong buntag."}


@pytest.mark.parametrize(
    ("state", "reps", "last_review", "expected"),
    [
        (SRSState.REVIEW, 5, None, True),
        (SRSState.KNOWN, 0, None, True),
        (SRSState.BURIED, 5, None, True),
        # A seeded starter card has a schedule and no reps; last_review marks it.
        (SRSState.BURIED, 0, date(2026, 10, 1), True),
        (SRSState.BURIED, 0, None, False),
        (SRSState.NEW, 0, None, False),
        (SRSState.LEARNING, 1, None, False),
        (SRSState.RELEARNING, 6, None, False),
        (SRSState.SUSPENDED, 9, None, False),
    ],
)
def test_understood_reads_a_buried_card_as_what_the_rollover_will_make_of_it(state, reps, last_review, expected):
    due = datetime.combine(date.today(), time(4, 0), tzinfo=UTC)
    direction = DirectionState(
        direction=Direction.RECOGNITION,
        due_at=due,
        stability=5.0,
        difficulty=4.0,
        reps=reps,
        last_review=last_review,
        state=state,
        bury_kind="sched" if state is SRSState.BURIED else None,
    )

    assert understood(direction) is expected


def test_no_card_is_not_understood():
    assert understood(None) is False
