"""Which affix pattern the learner is on: a gate in place of the rotation (tunatale-ve4p.26).

The owner approved a teaching order (2026-10-14) and asked for a faster pace
than one pattern a fortnight. The numbers are theirs, the same day: a pattern
stays the day's pattern until

* its drill was heard on two different days, and
* four of its contrast cards passed their first review, with at least 80% of
  its contrast-card reviews passed.

A review-only pattern (one the learner already has) needs one drill and no
cards. A pattern that passed comes back when its cards start failing; nothing
else brings it back, because the cards' own spacing is the widening gap.

Cebuano throughout: an affix pattern is a facet only its plugin registers.
Listen times are UTC noon on whole days apart, read in a declared zone,
because a drill day is a study day and a study day rolls over at 04:00 local.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.common.guid import compute_guid
from app.generation.pattern_gate import next_pattern, progress
from app.languages import get_a1_morphology
from app.models.lesson import Lesson
from app.models.srs_item import Direction, RevlogRow
from app.srs.contrast_card import contrast_card, pair_key
from app.srs.focus_card import focus_cards
from app.storage.store import ContentStore
from tests._helpers.localtz import local_timezone

_PATTERNS = {p.key: p for p in get_a1_morphology("ceb").patterns}
_LEARN, _REVIEW = 0, 1
_AGAIN, _HARD, _GOOD, _EASY = 1, 2, 3, 4


@pytest.fixture(autouse=True)
def _manila():
    with local_timezone("Asia/Manila"):
        yield


@pytest.fixture
def store():
    return ContentStore(":memory:")


def _at(day: int, hour: int = 12) -> datetime:
    """October *day*, 2026, at *hour* UTC."""
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def _heard(srs_db, store, pattern: str, *when: datetime, lesson_id: str | None = None) -> str:
    """A lesson whose drill is *pattern*, marked listened at each of *when*."""
    lesson_id = lesson_id or f"{pattern}-{len(store.list_lessons())}"
    lesson = Lesson(title=lesson_id, language_code="ceb", sections=[])
    lesson.generation_metadata["affix_drill"] = {"pattern": pattern, "roots": [], "new_roots": []}
    store.save_lesson(lesson_id, "funeral", len(store.list_lessons()) + 1, lesson)
    for moment in when:
        srs_db.record_listen(lesson_id, at=moment)
    return lesson_id


_ms = iter(range(1_791_500_000_000, 1_800_000_000_000, 60_000))


def _graded(srs_db, pattern: str, root: str, blank: int, *grades: tuple[int, int]) -> None:
    """*root*'s contrast card in *pattern*, graded ``(review_kind, button)`` in order."""
    unit = contrast_card("ceb", _PATTERNS[pattern], root, blank).unit()
    srs_db.add_collocation(unit, language_code="ceb")
    collocation_id = srs_db.get_collocation_id_by_guid(srs_db.get_collocation(unit.text).guid)
    for kind, button in grades:
        srs_db.append_revlog(
            RevlogRow(
                id=next(_ms),
                collocation_id=collocation_id,
                direction=Direction.PRODUCTION,
                button_chosen=button,
                interval=1,
                last_interval=0,
                factor=2500,
                taken_millis=5000,
                review_kind=kind,
            )
        )


def _passed_first_review(srs_db, pattern: str, roots: list[tuple[str, int]]) -> None:
    for root, blank in roots:
        _graded(srs_db, pattern, root, blank, (_LEARN, _GOOD), (_LEARN, _GOOD), (_REVIEW, _GOOD))


# One root per cell: actor and object focus cards both test the choice.
_ON_GI_FOUR = [("inom", 0), ("kaon", 1), ("dala", 2), ("tan-aw", 3)]


def _passed_focus(srs_db, cards: list[tuple[str, int]], *, grades=((_LEARN, _GOOD), (_LEARN, _GOOD), (_REVIEW, _GOOD))):
    """Focus cards ``(root, k)``, the k-th of each root, each passing its first review."""
    for root, k in cards:
        unit = focus_cards("ceb", _PATTERNS["on-gi"], root)[k].unit()
        srs_db.add_collocation(unit, language_code="ceb")
        collocation_id = srs_db.get_collocation_id_by_guid(compute_guid(unit.text, "ceb", unit.disambig_key))
        for kind, button in grades:
            srs_db.append_revlog(
                RevlogRow(
                    id=next(_ms),
                    collocation_id=collocation_id,
                    direction=Direction.PRODUCTION,
                    button_chosen=button,
                    interval=1,
                    last_interval=0,
                    factor=2500,
                    taken_millis=5000,
                    review_kind=kind,
                )
            )


_MO_MI_FOUR = [("uban", 1), ("inom", 0), ("adto", 1), ("anhi", 0)]
_MAG_NAG_FOUR = [("lakaw", 1), ("ampo", 1), ("dala", 1), ("luto", 1)]


def _key(srs_db, store) -> str | None:
    pattern = next_pattern("ceb", srs_db=srs_db, store=store, owner=True)
    return pattern.key if pattern is not None else None


# --- the order ----------------------------------------------------------------


def test_with_nothing_heard_the_first_pattern_in_the_languages_order_is_next(srs_db, store):
    assert _key(srs_db, store) == "mo-mi"


def test_the_owners_mo_mi_record_on_2026_10_14_passes_and_hands_over_to_mag_nag(srs_db, store):
    """Read from the laptop instance's database the day the numbers were set.

    The grammar lesson heard on 10-09 and 10-13 (UTC), and nine contrast
    cards. Five had passed a first review, and every review had passed.
    """
    _heard(srs_db, store, "mo-mi", datetime(2026, 10, 9, 19, 46, tzinfo=UTC), datetime(2026, 10, 13, 1, 52, tzinfo=UTC))
    for root, blank in (("uban", 1), ("inom", 0), ("anhi", 0)):
        _graded(srs_db, "mo-mi", root, blank, (_LEARN, _GOOD), (_LEARN, _GOOD))
    _graded(srs_db, "mo-mi", "adto", 1, (_LEARN, _EASY))
    _graded(srs_db, "mo-mi", "lakaw", 1, (_LEARN, _HARD), (_LEARN, _GOOD), (_LEARN, _GOOD), (_REVIEW, _GOOD))
    for root, blank in (("uban", 0), ("inom", 1), ("adto", 0), ("anhi", 1)):
        _graded(srs_db, "mo-mi", root, blank, (_LEARN, _GOOD), (_LEARN, _GOOD), (_REVIEW, _EASY))

    measured = progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True)
    assert (measured.drill_days, measured.first_passes, measured.reviews, measured.passes) == (2, 5, 5, 5)
    assert measured.met
    assert _key(srs_db, store) == "mag-nag"


# --- the drill count ------------------------------------------------------------


def test_one_drill_day_is_not_enough(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9))
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR)
    assert _key(srs_db, store) == "mo-mi"


def test_two_listens_on_one_day_are_one_drill_day(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9, 10), _at(9, 11))
    _heard(srs_db, store, "mo-mi", _at(9, 12))
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR)
    assert progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True).drill_days == 1
    assert _key(srs_db, store) == "mo-mi"


def test_a_drill_day_is_a_study_day_not_a_utc_date(srs_db, store):
    """Manila is UTC+8 and its study day rolls over at 04:00 local.

    23:00 UTC on the 8th is 07:00 on the 9th, and 19:30 UTC on the 9th is 03:30
    on the 10th, still the 9th's study day: two UTC dates, one drill day.
    """
    _heard(srs_db, store, "mo-mi", datetime(2026, 10, 8, 23, tzinfo=UTC), datetime(2026, 10, 9, 19, 30, tzinfo=UTC))
    assert progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True).drill_days == 1


def test_the_drill_counts_in_a_grammar_lesson_and_in_a_storys_closing_drill_alike(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9), lesson_id="affix-drill-mo-mi")
    _heard(srs_db, store, "mo-mi", _at(13), lesson_id="supper-after-the-beach")
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR)
    assert _key(srs_db, store) == "mag-nag"


def test_another_patterns_drill_does_not_count(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9))
    _heard(srs_db, store, "ma-na", _at(10))
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR)
    assert progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True).drill_days == 1


def test_a_lesson_never_marked_listened_is_no_drill(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9))
    _heard(srs_db, store, "mo-mi")
    assert progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True).drill_days == 1


def test_a_listen_of_a_lesson_with_no_drill_is_no_drill(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9))
    store.save_lesson("a-new-cafe", "funeral", 9, Lesson(title="A New Cafe", language_code="ceb", sections=[]))
    srs_db.record_listen("a-new-cafe", at=_at(10))
    srs_db.record_listen("a-review-session", at=_at(11))
    assert progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True).drill_days == 1


# --- the cards --------------------------------------------------------------------


def test_three_first_reviews_passed_are_not_enough(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9), _at(13))
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR[:3])
    assert _key(srs_db, store) == "mo-mi"


def test_grades_in_the_learning_steps_are_not_reviews(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9), _at(13))
    for root, blank in _MO_MI_FOUR:
        _graded(srs_db, "mo-mi", root, blank, (_LEARN, _GOOD), (_LEARN, _GOOD))
    measured = progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True)
    assert (measured.first_passes, measured.reviews) == (0, 0)
    assert _key(srs_db, store) == "mo-mi"


def test_a_first_review_failed_and_then_passed_is_not_a_first_pass(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9), _at(13))
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR[:3])
    _graded(srs_db, "mo-mi", "anhi", 0, (_LEARN, _GOOD), (_REVIEW, _AGAIN), (2, _GOOD), (_REVIEW, _GOOD))
    measured = progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True)
    assert (measured.first_passes, measured.reviews, measured.passes) == (3, 5, 4)
    assert _key(srs_db, store) == "mo-mi"


def test_four_first_passes_under_80_percent_of_reviews_passed_hold_the_pattern(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9), _at(13))
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR)
    _graded(srs_db, "mo-mi", "lakaw", 1, (_LEARN, _GOOD), (_REVIEW, _AGAIN), (_REVIEW, _AGAIN))
    measured = progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True)
    assert (measured.reviews, measured.passes) == (6, 4)  # 67%
    assert _key(srs_db, store) == "mo-mi"


def test_exactly_80_percent_passes(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9), _at(13))
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR)
    _graded(srs_db, "mo-mi", "lakaw", 1, (_LEARN, _GOOD), (_REVIEW, _AGAIN))
    measured = progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True)
    assert (measured.reviews, measured.passes) == (5, 4)
    assert _key(srs_db, store) == "mag-nag"


def test_another_patterns_cards_do_not_count(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9), _at(13))
    _passed_first_review(srs_db, "mag-nag", _MAG_NAG_FOUR)
    assert progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True).first_passes == 0


# --- after a pattern has passed ---------------------------------------------------


def test_a_passed_pattern_whose_cards_start_failing_comes_back(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9), _at(13))
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR)
    _heard(srs_db, store, "mag-nag", _at(15))
    assert _key(srs_db, store) == "mag-nag"

    _graded(srs_db, "mo-mi", "lakaw", 1, (_LEARN, _GOOD), (_REVIEW, _AGAIN), (_REVIEW, _AGAIN))
    assert _key(srs_db, store) == "mo-mi"


def test_a_review_only_pattern_needs_one_drill_and_no_cards(srs_db, store):
    assert _PATTERNS["ma-na"].review_only
    _heard(srs_db, store, "mo-mi", _at(9), _at(13))
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR)
    _heard(srs_db, store, "mag-nag", _at(15), _at(17))
    _passed_first_review(srs_db, "mag-nag", _MAG_NAG_FOUR)
    assert _key(srs_db, store) == "ma-na"

    _heard(srs_db, store, "ma-na", _at(19))
    assert progress(_PATTERNS["ma-na"], srs_db=srs_db, store=store, owner=True).met
    assert _key(srs_db, store) == "maka-naka"


_MA_NA_FOUR = [("tulog", 1), ("tulog", 0), ("lipay", 1), ("lipay", 0)]  # the roots the table pairs in ma-/na-


# --- whose deck (the owner, 2026-10-23, tunatale-bi67) -----------------------------
#
# ma-/na- is review-only because the OWNER has it from another language. The
# second learner does not, so on their deck it is taught like every pattern.


def _two_patterns_passed_and_ma_na_heard_once(srs_db, store):
    _heard(srs_db, store, "mo-mi", _at(9), _at(13))
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR)
    _heard(srs_db, store, "mag-nag", _at(15), _at(17))
    _passed_first_review(srs_db, "mag-nag", _MAG_NAG_FOUR)
    _heard(srs_db, store, "ma-na", _at(19))


def test_on_a_learners_deck_one_ma_na_drill_does_not_pass_it(srs_db, store):
    _two_patterns_passed_and_ma_na_heard_once(srs_db, store)
    assert not progress(_PATTERNS["ma-na"], srs_db=srs_db, store=store, owner=False).met
    assert next_pattern("ceb", srs_db=srs_db, store=store, owner=False).key == "ma-na"
    # the control: the same deck read as the owner's moves on
    assert next_pattern("ceb", srs_db=srs_db, store=store, owner=True).key == "maka-naka"


def test_on_a_learners_deck_ma_na_passes_on_the_full_gate(srs_db, store):
    _two_patterns_passed_and_ma_na_heard_once(srs_db, store)
    _heard(srs_db, store, "ma-na", _at(21))
    _passed_first_review(srs_db, "ma-na", _MA_NA_FOUR)
    assert progress(_PATTERNS["ma-na"], srs_db=srs_db, store=store, owner=False).met
    assert next_pattern("ceb", srs_db=srs_db, store=store, owner=False).key == "maka-naka"


def test_a_learners_ma_na_meters_ask_for_the_full_gate(srs_db, store):
    _heard(srs_db, store, "ma-na", _at(9))
    summary = progress(_PATTERNS["ma-na"], srs_db=srs_db, store=store, owner=False).summary()
    assert {k: summary[k] for k in ("review_only", "drill_days_needed", "first_passes_needed", "met")} == {
        "review_only": False,
        "drill_days_needed": 2,
        "first_passes_needed": 4,
        "met": False,
    }


_MAKA_NAKA_FOUR = [("kita", 1), ("sulod", 0), ("gawas", 1), ("inom", 0)]


def test_with_every_pattern_passed_the_one_heard_longest_ago_comes_round(srs_db, store):
    _heard(srs_db, store, "ma-na", _at(8))
    _heard(srs_db, store, "mo-mi", _at(9), _at(13))
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR)
    _heard(srs_db, store, "mag-nag", _at(15), _at(17))
    _passed_first_review(srs_db, "mag-nag", _MAG_NAG_FOUR)
    _heard(srs_db, store, "maka-naka", _at(19), _at(21))
    _passed_first_review(srs_db, "maka-naka", _MAKA_NAKA_FOUR)
    _heard(srs_db, store, "on-gi", _at(25), _at(27))
    _passed_focus(srs_db, _ON_GI_FOUR)
    assert _key(srs_db, store) == "ma-na"

    _heard(srs_db, store, "ma-na", _at(23))
    assert _key(srs_db, store) == "mo-mi"


def test_on_gi_counts_its_focus_cards(srs_db, store):
    for key, when in (("mo-mi", (5, 7)), ("mag-nag", (11, 13)), ("ma-na", (15,)), ("maka-naka", (19, 21))):
        _heard(srs_db, store, key, *(_at(day) for day in when))
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR)
    _passed_first_review(srs_db, "mag-nag", _MAG_NAG_FOUR)
    _passed_first_review(srs_db, "maka-naka", _MAKA_NAKA_FOUR)
    _heard(srs_db, store, "on-gi", _at(25), _at(27))

    _passed_focus(srs_db, _ON_GI_FOUR[:3])
    assert progress(_PATTERNS["on-gi"], srs_db=srs_db, store=store, owner=True).first_passes == 3
    _passed_focus(srs_db, _ON_GI_FOUR[3:])
    assert progress(_PATTERNS["on-gi"], srs_db=srs_db, store=store, owner=True).met


def test_a_contrast_card_keyed_like_gi_does_not_count_toward_on_gi(srs_db, store):
    """The sentence pattern counts ``morph:focus-*``; a ``morph:pair-verb-gi`` cloze is no focus card."""
    _heard(srs_db, store, "on-gi", _at(25), _at(27))
    for root, form in (("inom", "giinom"), ("kaon", "gikaon"), ("dala", "gidala"), ("tan-aw", "gitan-aw")):
        unit = replace(
            focus_cards("ceb", _PATTERNS["on-gi"], root)[0].unit(), text=form, disambig_key=pair_key("verb:gi")
        )
        srs_db.add_collocation(unit, language_code="ceb")
        _graded_existing(srs_db, compute_guid(form, "ceb", unit.disambig_key))
    assert progress(_PATTERNS["on-gi"], srs_db=srs_db, store=store, owner=True).first_passes == 0


def test_actor_focus_cards_do_not_count_toward_mo_mi(srs_db, store):
    """An actor focus card says mi- or mo-, but it is on-gi's evidence, not mo-mi's."""
    _heard(srs_db, store, "mo-mi", _at(5), _at(7))
    _passed_focus(srs_db, [("inom", 1), ("kaon", 1), ("dala", 3), ("tan-aw", 3)])
    assert progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True).first_passes == 0
    assert _key(srs_db, store) == "mo-mi"


def _graded_existing(srs_db, guid: str) -> None:
    collocation_id = srs_db.get_collocation_id_by_guid(guid)
    for kind, button in ((_LEARN, _GOOD), (_REVIEW, _GOOD)):
        srs_db.append_revlog(
            RevlogRow(
                id=next(_ms),
                collocation_id=collocation_id,
                direction=Direction.PRODUCTION,
                button_chosen=button,
                interval=1,
                last_interval=0,
                factor=2500,
                taken_millis=5000,
                review_kind=kind,
            )
        )


# --- a pattern that does not pass ---------------------------------------------------


def test_a_pattern_unpassed_after_five_drill_days_says_so(srs_db, store, caplog):
    _heard(srs_db, store, "mo-mi", *(_at(day) for day in (5, 6, 7, 8)))
    with caplog.at_level(logging.WARNING, logger="app.generation.pattern_gate"):
        assert _key(srs_db, store) == "mo-mi"
    assert not caplog.records

    _heard(srs_db, store, "mo-mi", _at(9))
    with caplog.at_level(logging.WARNING, logger="app.generation.pattern_gate"):
        assert _key(srs_db, store) == "mo-mi"
    assert progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True).stuck
    [record] = caplog.records
    assert record.getMessage() == (
        "PATTERN_STUCK language=ceb pattern=mo-mi drill_days=5 first_passes=0 reviews=0 passes=0"
    )


# --- what the lesson page shows (tunatale-ve4p.30, option B) ---------------------


def test_the_summary_says_how_far_each_part_of_the_gate_is_and_what_it_needs(srs_db, store):
    _heard(srs_db, store, "mo-mi", *(_at(day) for day in (5, 6, 7, 8, 9)))
    _passed_first_review(srs_db, "mo-mi", _MO_MI_FOUR[:2])
    _graded(srs_db, "mo-mi", "lakaw", 1, (_LEARN, _GOOD), (_REVIEW, _AGAIN), (_REVIEW, _GOOD), (_REVIEW, _AGAIN))

    assert progress(_PATTERNS["mo-mi"], srs_db=srs_db, store=store, owner=True).summary() == {
        "review_only": False,
        "drill_days": 5,
        "drill_days_needed": 2,
        "first_passes": 2,
        "first_passes_needed": 4,
        "reviews": 5,
        "passes": 3,
        "pass_rate_needed": 0.8,
        "met": False,
        "stuck": True,
    }


def test_a_review_only_summary_needs_one_drill_day_and_nothing_else(srs_db, store):
    _heard(srs_db, store, "ma-na", _at(9))
    summary = progress(_PATTERNS["ma-na"], srs_db=srs_db, store=store, owner=True).summary()
    assert {k: summary[k] for k in ("review_only", "drill_days", "drill_days_needed", "met", "stuck")} == {
        "review_only": True,
        "drill_days": 1,
        "drill_days_needed": 1,
        "met": True,
        "stuck": False,
    }


def test_a_language_with_no_patterns_has_no_next_pattern(srs_db, store):
    assert next_pattern("no", srs_db=srs_db, store=store, owner=True) is None
