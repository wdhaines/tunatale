"""A multi-word card matches a Tagalog lesson phrase only on its exact form (tunatale-w4m7.17).

Tagalog keys every verb form on its root, and phrase spans used to match on
lemmas, so the wake lesson's "Magdadala ako" (I will bring) lit up — and graded —
the Pimsleur card "magdala ako" (let me bring). The user's decision (2026-09-27):
a multi-word card matches only on the exact verb form; single words still match
by root. A language that does not opt in keeps lemma matching.

Built on the REAL lemma tables: the test suite's default lowercase lemmatizer
makes lemma == surface, so it cannot tell the two matching modes apart.
"""

from __future__ import annotations

import pytest

from app.languages import get_lemma_table_path, get_phrase_match_exact_form
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.srs.lemma_table import TableLemmatizer
from app.srs.transcript import extract_transcript


def _lesson(text: str, lang: str) -> Lesson:
    lesson = Lesson(title="t", language_code=lang)
    phrase = Phrase(text=text, voice_id="female-1", language_code=lang, role="female-1")
    lesson.sections = [Section(section_type=SectionType.NATURAL_SPEED, phrases=[phrase])]
    return lesson


def _span_ids(card: str, line: str, lang: str) -> tuple[int, list[int | None]]:
    db = SRSDatabase(":memory:")
    db.add_collocation(
        SyntacticUnit(text=card, translation="x", word_count=len(card.split()), difficulty=1, source="test"),
        language_code=lang,
    )
    card_id = db.list_collocations()[0][0][0]
    lemmatizer = TableLemmatizer(lang, get_lemma_table_path(lang))
    try:
        words = extract_transcript(_lesson(line, lang), db, lemmatizer).dialogue_lines[0].words
    finally:
        lemmatizer.close()
    return card_id, [w.collocation_span_id for w in words]


def test_the_measured_mismatch_no_longer_matches():
    """The wake lesson's own phrase and card (measured 2026-09-24)."""
    _, spans = _span_ids("magdala ako", "Magdadala ako ng pagkain.", "tl")
    assert spans == [None, None, None, None]


def test_a_different_aspect_of_the_same_root_does_not_match():
    _, spans = _span_ids("Pasok ka!", "Pumasok ka.", "tl")
    assert spans == [None, None]


def test_the_exact_form_still_matches():
    card_id, spans = _span_ids("magdadala ako", "Magdadala ako ng pagkain.", "tl")
    assert spans == [card_id, card_id, None, None]


def test_case_and_punctuation_are_not_part_of_the_form():
    card_id, spans = _span_ids("Salamat po.", "salamat po!", "tl")
    assert spans == [card_id, card_id]


def test_a_language_that_does_not_opt_in_still_matches_by_lemma():
    """Control: Norwegian's gikk and går both lemmatize to gå, and still match."""
    card_id, spans = _span_ids("går hjem", "Han gikk hjem.", "no")
    assert spans == [None, card_id, card_id]


@pytest.mark.parametrize(("code", "expected"), [("tl", True), ("ceb", False), ("sl", False), ("no", False)])
def test_the_facet_is_opt_in(code, expected):
    assert get_phrase_match_exact_form(code) is expected


def test_an_unknown_code_keeps_lemma_matching():
    assert get_phrase_match_exact_form("zz") is False
