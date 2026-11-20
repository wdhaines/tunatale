"""Which affix cards a lesson's listen adds (tunatale-ve4p.6, tunatale-ve4p.36).

A word pattern (mo-/mi-, maka-/naka-) adds none. Its pair cards showed the
paradigm and the English of the blank, so the English made the only decision
and the owner found them easy (2026-11-05); they are no longer made, and the
ones in a deck age out. A pattern taught as whole sentences adds focus cards
(``test_focus_card_selection.py``).
"""

from __future__ import annotations

import pytest

from app.generation.grammar_lesson import build_grammar_lesson, contrast_cards, plan_grammar_lesson
from app.languages import get_a1_morphology, get_language
from app.models.lesson import Lesson
from tests.test_grammar_lesson import _WAKE, _story_lesson, owner  # noqa: F401

_MO_MI = next(p for p in get_a1_morphology("ceb").patterns if p.key == "mo-mi")


def _drill(srs_db, store) -> Lesson:
    """The owner's mo-/mi- lesson, built the way the pipeline builds it."""
    plan = plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=store, curriculum_id="funeral", lemmatizer=None)
    return build_grammar_lesson(get_language("ceb"), plan)


@pytest.mark.parametrize("pattern", ["mo-mi", "maka-naka"])
def test_a_word_patterns_drill_adds_no_cards(owner, pattern):  # noqa: F811
    srs_db, store = owner
    lesson = _drill(srs_db, store)
    lesson.generation_metadata["affix_drill"]["pattern"] = pattern
    assert lesson.generation_metadata["affix_drill"]["roots"]  # the control: the drill has roots to offer

    assert contrast_cards(lesson, srs_db=srs_db, store=store, curriculum_id="funeral") == []


def test_a_lesson_with_no_affix_drill_adds_none(owner):  # noqa: F811
    srs_db, store = owner
    story = _story_lesson("An Evening Wake", _WAKE)

    assert contrast_cards(story, srs_db=srs_db, store=store, curriculum_id="funeral") == []


@pytest.mark.parametrize("pattern", ["um-in", ""])
def test_a_drill_on_a_pattern_the_language_no_longer_has_adds_none(owner, pattern):  # noqa: F811
    srs_db, store = owner
    lesson = _drill(srs_db, store)
    lesson.generation_metadata["affix_drill"]["pattern"] = pattern

    assert contrast_cards(lesson, srs_db=srs_db, store=store, curriculum_id="funeral") == []
