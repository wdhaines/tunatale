"""Which story lines a drill may end on: the ones the learner already has every word of.

An affix drill ends on a few whole lines from a story, asked for cold from their
English. The lines used to be chosen for containing a drilled form and nothing
else, so they asked for words the learner had never met and for affixes the
lesson does not teach (bd tunatale-ve4p.20, the owner on 2026-10-09: "The full
sentences at the end are too hard right now because we're introducing things I
don't know"). The rule decided that day: a line is admitted only when every
word in it is one the learner understands or one the lesson itself teaches, and
the drill ends with no lines when none qualify.

"Understands" is the reader's own answer, word by word:
``srs/transcript.py::extract_transcript`` run over the lesson, so a line is
judged exactly as the transcript would paint it — the same lemmatizer, the same
card resolution, the same sense choice. A second, simpler lookup here would
disagree with the reader on every inflected form, variant spelling and
function-word cloze, and the drill would ask for words the reader shows as new.

What counts as having a word:

* its card is in REVIEW or KNOWN (``UNDERSTOOD``; recognition for a vocab card,
  production for a cloze, which is what ``SRSItem.state`` reads);
* its card is buried for the day and was in review before that
  (:func:`understood`): a sibling was graded, nothing was forgotten;
* it sits inside a multi-word phrase whose own card is;
* the learner put it on the ignore list — names and loanwords they chose not to
  card, not words they are waiting to learn;
* it is in *taught*: what the caller says this lesson teaches (the drilled
  forms, and for a thematic lesson its key phrases).

A SUSPENDED card does not count: suspending says "stop showing me this", not "I
know this". A word with no card does not count, a proper noun included, unless
it is ignored or taught.
"""

from __future__ import annotations

from collections.abc import Collection

from app.models.lesson import Lesson, SectionType
from app.models.srs_item import DirectionState, SRSItem, SRSState
from app.srs.lemmatizer import Lemmatizer, get_lemmatizer
from app.srs.transcript import WordToken, extract_transcript

# The states that mean the learner understands a card. One definition for the
# drill: the roots it may drill (``grammar_lesson._understood``) and the words
# its closing lines may ask for.
UNDERSTOOD = (SRSState.REVIEW, SRSState.KNOWN)
_UNDERSTOOD_VALUES = frozenset(state.value for state in UNDERSTOOD)
# ``WordToken.srs_state`` for a word with no card that is on the ignore list.
_IGNORED = "ignored"
_BURIED = SRSState.BURIED.value


def understood(card: DirectionState | SRSItem | None) -> bool:
    """Whether *card* is one the learner understands. ``None`` is no card.

    REVIEW or KNOWN, and BURIED when what is buried is a card that has been
    reviewed. Grading one side of a card buries the other until the next
    rollover; that hides it for the day and takes nothing away. What a buried
    card is underneath is read the way the rollover sweep reads it when it
    gives the state back (``db_queue.py::unbury_if_needed``): REVIEW once it
    has reps or a last review, NEW otherwise.
    """
    if card is None:
        return False
    if card.state in UNDERSTOOD:
        return True
    return card.state is SRSState.BURIED and (card.reps > 0 or card.last_review is not None)


def fold(word: str) -> str:
    """*word* as the drill compares words: case and hyphens do not tell two apart."""
    return word.casefold().replace("-", "")


def _resting(srs_db, state: str | None, card_id: int | None) -> bool:
    """Whether a card the transcript shows as buried is one the learner understands."""
    return state == _BURIED and understood(srs_db.get_collocation_by_id(card_id)[1])


def _has(word: WordToken, taught: Collection[str], srs_db) -> bool:
    return (
        word.srs_state in _UNDERSTOOD_VALUES
        or word.srs_state == _IGNORED
        or word.collocation_srs_state in _UNDERSTOOD_VALUES
        or fold(word.surface) in taught
        or _resting(srs_db, word.srs_state, word.srs_item_id)
        or _resting(srs_db, word.collocation_srs_state, word.collocation_span_id)
    )


def admitted_lines(lesson: Lesson, *, srs_db, lemmatizer: Lemmatizer | None, taught: Collection[str]) -> set[str]:
    """The story lines of *lesson*, as written, that ask only for words the learner has.

    *taught* holds words already :func:`fold`-ed. *lemmatizer* of ``None``
    resolves the language's own, as the publish seam does. With no *srs_db*
    there is no learner to ask, and no line is admitted: the drill ends on its
    forms alone rather than on lines nobody vouched for.

    Reads the first natural-speed section, the one the reader shows; a grammar
    lesson has none and admits nothing.
    """
    if srs_db is None:
        return set()
    story = next((s for s in lesson.sections if s.section_type is SectionType.NATURAL_SPEED), None)
    if story is None:
        return set()
    said = [phrase.text for phrase in story.phrases if phrase.language_code == lesson.language_code]
    if lemmatizer is None:
        lemmatizer = get_lemmatizer(lesson.language_code)
    transcript = extract_transcript(lesson, srs_db, lemmatizer)
    # One transcript line per story line, in order: both walk the same section
    # with the same language test. strict, so a change to either walk fails here
    # instead of judging each line by its neighbour's words.
    return {
        text
        for text, line in zip(said, transcript.dialogue_lines, strict=True)
        if line.words and all(_has(word, taught, srs_db) for word in line.words)
    }
