"""Sentence mining on a lesson's lines: one read model, not-wanted marks, one write path (tunatale-h0q3.66).

Design: ``.beads-tasks/briefs/design-sentence-mining-2026-11.md`` § Design 1-4; the
oracle rows (L1, L2, L4, M1, M2, N1-N4) are named on the tests below.

* ``sentence_lines`` is the one read model of a lesson's lines: every written word
  is a token the learner may toggle, each ``on`` (a card exists, or the list will
  mint it at study), ``declined`` (a list card turned off) or ``off``.
* A list card turned off is a TT-only not-wanted mark under its card key, which
  ``mint_lesson_cards`` honours at every call site, so it is never minted again.
* ``plan_line_edits`` validates a Save's edits before anything is written, and
  ``write_marks`` / ``apply_rows`` write them on either side of the study pass.
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from app.common.guid import compute_guid
from app.models.lesson import Lesson
from app.models.srs_item import Direction
from app.srs import sentence_mining as mining
from app.srs.database import SRSDatabase
from app.srs.lesson_card_mint import mint_lesson_cards
from app.srs.lesson_cards import COURSE_SOURCE
from app.srs.sentence_card import sentence_card
from app.srs.sentence_lines import line_card, sentence_lines
from tests.test_lesson_card_list import _story

LANG = "no"
L2 = "sent:test-01:2"  # "Hei, hvordan går det?": whole + blank "går"
L3 = "sent:test-01:3"  # "Bra, takk, og du?": blanks "takk" and "du", no whole card


def _lesson(story: dict | None = None) -> Lesson:
    return Lesson(title="Dag 1", language_code=LANG, generation_metadata={"story": story or _story()})


def _line(lines, key):
    return next(line for line in lines if line.line_key == key)


def _token(line, word):
    return next(t for t in line.tokens if line.text[t.start : t.end] == word)


def _study(db, lesson):
    return mint_lesson_cards(db, LANG, lesson.generation_metadata["story"]["cards"])


def _keys(db, line_key) -> list[str]:
    return sorted(i.syntactic_unit.disambig_key for _, i, _ in db.list_sibling_rows(line_key))


def _span(text: str, word: str) -> tuple[int, int]:
    s = text.index(word)
    return (s, s + len(word))


def _reviewed(db, row_id: int) -> None:
    from tests.test_sentence_mining import _reviewed as reviewed

    reviewed(db, row_id)


def _link(db, line_key: str) -> None:
    """The line's note is in Anki: every row of it carries the note's id."""
    with db._get_conn() as conn:
        conn.execute("UPDATE collocations SET anki_note_id = 4242 WHERE sibling_group = ?", (line_key,))
        conn.commit()


# --- L1-L4: the read model ---------------------------------------------------------------


class TestReadModel:
    def test_l1_one_line_per_listed_sentence_keyed_by_the_lists_set(self):
        lines = sentence_lines(SRSDatabase(":memory:"), _lesson(), "lesson-1")
        assert [(line.line_key, line.position) for line in lines] == [
            ("sent:test-01:1", 1),
            (L2, 2),
            (L3, 3),
        ]
        line = _line(lines, L2)
        assert (line.text, line.english, line.speaker, line.linked) == (
            "Hei, hvordan går det?",
            "Hi, how are you?",
            "Per",
            False,
        )

    def test_l1_before_study_the_lists_cards_are_on_and_every_other_word_off(self):
        line = _line(sentence_lines(SRSDatabase(":memory:"), _lesson(), "lesson-1"), L3)
        assert [(line.text[t.start : t.end], t.state, t.origin) for t in line.tokens] == [
            ("Bra", "off", "mine"),
            ("takk", "on", "lesson"),
            ("og", "off", "mine"),
            ("du", "on", "lesson"),
        ]
        assert line.whole.state == "off"  # the list makes no whole card of this line
        assert _line(sentence_lines(SRSDatabase(":memory:"), _lesson(), "lesson-1"), L2).whole.state == "on"

    def test_l1_a_hyphen_or_apostrophe_inside_a_word_stays_in_its_token(self):
        story = _story()
        item = story["cards"]["items"][3]
        item.update(
            text="Per-Ola's bil, takk.", spoken="Per-Ola's bil, takk.", blanks=[{"form": "bil", "gloss": "car"}]
        )
        line = _line(sentence_lines(SRSDatabase(":memory:"), _lesson(story), "lesson-1"), L3)
        assert [line.text[t.start : t.end] for t in line.tokens] == ["Per-Ola's", "bil", "takk"]

    def test_l1_a_word_the_line_glosses_carries_its_gloss(self):
        line = _line(sentence_lines(SRSDatabase(":memory:"), _lesson(), "lesson-1"), L3)
        assert (_token(line, "Bra").gloss, _token(line, "og").gloss) == ("fine", "")

    def test_l2_a_list_blank_inside_a_word_is_its_tokens_part(self):
        story = _story()
        story["cards"]["items"][3].update(
            text="Bilen er rød.", spoken="Bilen er rød.", blanks=[{"form": "-en", "gloss": "the"}]
        )
        line = _line(sentence_lines(SRSDatabase(":memory:"), _lesson(story), "lesson-1"), L3)
        bilen, er = _token(line, "Bilen"), _token(line, "er")
        assert (bilen.start, bilen.end, bilen.part, bilen.state) == (0, 5, (3, 5), "on")
        assert er.part is None  # control: a whole word has no part

    def test_a_list_blank_spanning_two_words_is_one_token(self):
        """Review F3 (live: wolff-01b:25, list blank "na lang"): its words toggle that one card."""
        line = _line(sentence_lines(SRSDatabase(":memory:"), _lesson(_two_word_story()), "lesson-1"), L3)
        assert [(line.text[t.start : t.end], t.state, t.origin, t.part) for t in line.tokens] == [
            ("Bra", "off", "mine", None),
            ("takk", "on", "lesson", None),
            ("og du", "on", "lesson", None),
        ]

    def test_l4_spoken_map_pairs_the_lessons_words_with_the_cards_by_index(self):
        line = _line(sentence_lines(SRSDatabase(":memory:"), _lesson(), "lesson-1"), L2)
        assert line.spoken_map == [0, 1, 2, 3]  # "gar" (spoken) is word 2 of "går" (shown)

    def test_l4_counts_that_differ_give_no_map(self):
        story = _story()
        story["cards"]["items"][2]["spoken"] = "Hei, hvordan gar det med deg?"
        line = _line(sentence_lines(SRSDatabase(":memory:"), _lesson(story), "lesson-1"), L2)
        assert line.spoken_map is None

    def test_after_study_a_minted_card_is_on_and_a_mined_one_is_mine(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        card = line_card(lesson, 3)
        mining.add_blank(db, LANG, L3, _span(card.text, "og"), "and")
        line = _line(sentence_lines(db, lesson, "lesson-1"), L3)
        assert (_token(line, "takk").state, _token(line, "takk").origin) == ("on", "lesson")
        assert (_token(line, "og").state, _token(line, "og").origin) == ("on", "mine")
        assert _token(line, "takk").reviewed is False

    def test_a_card_says_whether_the_deck_holds_it(self):
        """Review F4: a listed card the deck lacks reads ``on`` (it mints at study) but is not in it."""
        db, lesson = SRSDatabase(":memory:"), _lesson()
        before = _line(sentence_lines(db, lesson, "lesson-1"), L3)
        assert (_token(before, "takk").state, _token(before, "takk").in_deck) == ("on", False)
        _study(db, lesson)
        after = _line(sentence_lines(db, lesson, "lesson-1"), L3)
        assert (_token(after, "takk").in_deck, _token(after, "og").in_deck) == (True, False)

    def test_a_reviewed_card_says_so(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        takk = next(r for _, i, r in db.list_sibling_rows(L3) if sentence_card(i.syntactic_unit).blank == (5, 9))
        _reviewed(db, takk)
        assert _token(_line(sentence_lines(db, lesson, "lesson-1"), L3), "takk").reviewed is True

    def test_a_linked_line_says_so(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        _link(db, L3)
        assert _line(sentence_lines(db, lesson, "lesson-1"), L3).linked is True

    def test_a_lesson_with_no_card_list_has_no_list_lines(self):
        story = _story()
        del story["cards"]
        assert sentence_lines(SRSDatabase(":memory:"), _lesson(story), "lesson-1") == []

    def test_line_card_is_the_lists_base_card(self):
        card = line_card(_lesson(), 2)
        assert (card.key, card.blank, card.text, card.spoken) == (
            L2,
            None,
            "Hei, hvordan går det?",
            "Hei, hvordan gar det?",
        )
        assert line_card(_lesson(), 9) is None


# --- N1, N2: not-wanted marks ------------------------------------------------------------


class TestMarks:
    def test_n1_a_marked_list_key_is_not_minted_and_unmarked_it_is(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        takk = line_card(lesson, 3).with_blank((5, 9), "").key
        db.add_sentence_card_mark(takk)
        _study(db, lesson)
        assert takk not in _keys(db, L3)
        db.remove_sentence_card_mark(takk)
        _study(db, lesson)
        assert takk in _keys(db, L3)

    def test_n1_control_with_no_mark_the_list_mints_as_today(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        assert len(_keys(db, L3)) == 2

    def test_a_mark_is_declined_in_the_read_model(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        db.add_sentence_card_mark(line_card(lesson, 3).with_blank((5, 9), "").key)
        assert _token(_line(sentence_lines(db, lesson, "lesson-1"), L3), "takk").state == "declined"

    def test_marks_are_idempotent(self):
        db = SRSDatabase(":memory:")
        db.add_sentence_card_mark("sent:x:1")
        db.add_sentence_card_mark("sent:x:1")
        assert db.sentence_card_marks() == {"sent:x:1"}
        db.remove_sentence_card_mark("sent:x:1")
        db.remove_sentence_card_mark("sent:x:1")
        assert db.sentence_card_marks() == set()

    def test_n2_a_minted_list_blank_turned_off_is_deleted_and_never_minted_again(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        plan = mining.plan_line_edits(db, lesson, "lesson-1", [{"line_key": L3, "on": [], "off": [[5, 9]]}])
        mining.write_marks(db, plan)
        _study(db, lesson)  # the study pass between the two halves of a Save
        mining.apply_rows(db, LANG, lesson, plan, {}, studied=True)
        _study(db, lesson)  # and the next Save's
        assert _keys(db, L3) == [line_card(lesson, 3).with_blank((14, 16), "").key]


# --- the edit path: plan, then marks, then rows --------------------------------------------


def _edit(db, lesson, edits, glosses=None, *, studied: bool, study_pass: bool | None = None):
    """A Save's sentence edits: plan, marks, the study pass (*study_pass*, default *studied*), rows."""
    plan = mining.plan_line_edits(db, lesson, "lesson-1", edits)
    mining.write_marks(db, plan)
    if studied if study_pass is None else study_pass:
        _study(db, lesson)
    return mining.apply_rows(db, LANG, lesson, plan, glosses or {}, studied=studied)


class TestEdits:
    def test_m1_a_blank_mined_on_a_line_with_rows_is_exactly_add_blanks_row(self):
        twin, db, lesson = SRSDatabase(":memory:"), SRSDatabase(":memory:"), _lesson()
        for each in (twin, db):
            _study(each, lesson)
        og = _span(line_card(lesson, 3).text, "og")
        expected = mining.add_blank(twin, LANG, L3, og, "and")
        result = _edit(db, lesson, [{"line_key": L3, "on": [list(og)], "off": []}], {(L3, og): "and"}, studied=True)
        assert result.made == [expected]
        assert (
            db.get_collocation_by_id(expected)[1].syntactic_unit
            == twin.get_collocation_by_id(expected)[1].syntactic_unit
        )

    def test_m2_a_blank_mined_before_study_shares_the_lists_base_card(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        og = _span(line_card(lesson, 3).text, "og")
        (row,) = _edit(
            db, lesson, [{"line_key": L3, "on": [list(og)], "off": []}], {(L3, og): "and"}, studied=False
        ).made
        _study(db, lesson)
        mined = sentence_card(db.get_collocation_by_id(row)[1].syntactic_unit)
        listed = [sentence_card(i.syntactic_unit) for _, i, r in db.list_sibling_rows(L3) if r != row]
        assert mined.blank == og and mined.blank_gloss == "and"
        for card in listed:
            assert replace(mined, blank=None, blank_gloss="") == replace(card, blank=None, blank_gloss="")
        assert db.get_collocation_by_id(row)[1].syntactic_unit.source == "user"

    def test_a_list_card_turned_off_before_study_is_marked_and_not_minted(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        result = _edit(db, lesson, [{"line_key": L2, "on": [], "off": ["whole"]}], studied=True)
        assert L2 in db.sentence_card_marks()
        assert _keys(db, L2) == [line_card(lesson, 2).with_blank((13, 16), "").key]
        assert result.removed == []

    def test_a_declined_list_card_turned_on_in_a_studied_lesson_mints_now(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        db.add_sentence_card_mark(L2)
        _study(db, lesson)
        result = _edit(db, lesson, [{"line_key": L2, "on": ["whole"], "off": []}], studied=True, study_pass=False)
        assert L2 not in db.sentence_card_marks()
        assert L2 in _keys(db, L2)
        (row,) = result.made
        assert db.get_collocation_by_id(row)[1].syntactic_unit.source == COURSE_SOURCE

    def test_a_list_card_deleted_by_hand_turned_on_again_mints_now(self):
        """Deleted on the Cards page, it has no row and no mark; on again, the mint makes it."""
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        takk = next(r for _, i, r in db.list_sibling_rows(L3) if sentence_card(i.syntactic_unit).blank == (5, 9))
        db.delete_collocation(takk)
        whole2 = next(r for _, i, r in db.list_sibling_rows(L2) if sentence_card(i.syntactic_unit).blank is None)
        db.delete_collocation(whole2)  # another list card deleted by hand: it waits for the next study pass
        plan = mining.plan_line_edits(db, lesson, "lesson-1", [{"line_key": L3, "on": [[5, 9]], "off": []}])
        assert (plan.list_on, plan.marks_off) == ([line_card(lesson, 3).with_blank((5, 9), "").key], [])
        (row,) = mining.apply_rows(db, LANG, lesson, plan, {}, studied=True).made
        assert sentence_card(db.get_collocation_by_id(row)[1].syntactic_unit).blank == (5, 9)
        assert L2 not in _keys(db, L2)

    def test_a_declined_list_card_turned_on_before_study_waits_for_the_study(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        db.add_sentence_card_mark(L2)
        result = _edit(db, lesson, [{"line_key": L2, "on": ["whole"], "off": []}], studied=False)
        assert result.made == [] and _keys(db, L2) == []
        assert L2 not in db.sentence_card_marks()

    def test_the_whole_line_card_of_a_line_the_list_does_not_make_whole_is_mined(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        (row,) = _edit(db, lesson, [{"line_key": L3, "on": ["whole"], "off": []}], studied=True).made
        unit = db.get_collocation_by_id(row)[1].syntactic_unit
        assert (unit.disambig_key, unit.source) == (L3, "user")

    def test_a_card_already_there_turned_on_is_left_alone(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        assert _edit(db, lesson, [{"line_key": L3, "on": [[5, 9]], "off": []}], studied=True).made == []

    def test_a_mined_blank_turned_off_is_deleted_and_not_marked(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        og = _span(line_card(lesson, 3).text, "og")
        (row,) = _edit(
            db, lesson, [{"line_key": L3, "on": [list(og)], "off": []}], {(L3, og): "and"}, studied=True
        ).made
        result = _edit(db, lesson, [{"line_key": L3, "on": [], "off": [list(og)]}], studied=True)
        assert result.removed == [row] and db.sentence_card_marks() == set()

    def test_a_word_with_no_card_turned_off_does_nothing(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        result = _edit(db, lesson, [{"line_key": L3, "on": [], "off": [[0, 3]]}], studied=False)
        assert (result.made, result.removed, db.sentence_card_marks()) == ([], [], set())

    def test_a_blank_with_no_gloss_is_not_made_and_the_rest_apply(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        og, bra = _span(line_card(lesson, 3).text, "og"), _span(line_card(lesson, 3).text, "Bra")
        result = _edit(
            db, lesson, [{"line_key": L3, "on": [list(og), list(bra)], "off": []}], {(L3, bra): "fine"}, studied=True
        )
        assert len(result.made) == 1
        assert result.not_made == [{"line_key": L3, "start": og[0], "end": og[1], "word": "og"}]

    def test_needs_gloss_lists_the_new_blanks_with_the_lines_own_gloss(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        og, bra = _span(line_card(lesson, 3).text, "og"), _span(line_card(lesson, 3).text, "Bra")
        plan = mining.plan_line_edits(
            db, lesson, "lesson-1", [{"line_key": L3, "on": [list(og), list(bra)], "off": []}]
        )
        assert [(n.line_key, n.span, n.word, n.own_gloss) for n in plan.needs_gloss] == [
            (L3, og, "og", ""),
            (L3, bra, "Bra", "fine"),
        ]
        assert plan.needs_gloss[0].sentence == "Bra, takk, og du?"


def _two_word_story() -> dict:
    story = _story()
    story["cards"]["items"][3]["blanks"] = [{"form": "takk", "gloss": "thanks"}, {"form": "og du", "gloss": "and you"}]
    return story


class TestRefusals:
    def test_a_word_inside_a_two_word_list_blank_is_not_a_span_the_line_takes(self):
        with pytest.raises(mining.NotWholeWords):
            mining.plan_line_edits(
                SRSDatabase(":memory:"),
                _lesson(_two_word_story()),
                "lesson-1",
                [{"line_key": L3, "on": [[11, 13]], "off": []}],
            )

    def test_the_two_word_list_blank_turns_off_as_one_card(self):
        db, lesson = SRSDatabase(":memory:"), _lesson(_two_word_story())
        _study(db, lesson)
        result = _edit(db, lesson, [{"line_key": L3, "on": [], "off": [[11, 16]]}], studied=True)
        assert (
            len(result.removed) == 1 and line_card(lesson, 3).with_blank((11, 16), "").key in db.sentence_card_marks()
        )

    def test_a_blank_overlapping_a_row_of_the_line_is_refused_before_anything_is_written(self):
        """Every blank the plan will add passes ``plan_blank`` in the plan, so ``apply_rows`` cannot raise."""
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        du = next(r for _, i, r in db.list_sibling_rows(L3) if sentence_card(i.syntactic_unit).blank == (14, 16))
        db.delete_collocation(du)
        mining.add_blank(db, LANG, L3, (11, 16), "and you")  # a two-word mined row, as the old route could make
        with pytest.raises(mining.BlankOverlaps):
            mining.plan_line_edits(db, lesson, "lesson-1", [{"line_key": L3, "on": [[11, 13]], "off": []}])

    def test_n3_a_reviewed_card_turned_off_without_confirm_names_it_and_nothing_applies(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        takk = next(r for _, i, r in db.list_sibling_rows(L3) if sentence_card(i.syntactic_unit).blank == (5, 9))
        _reviewed(db, takk)
        with pytest.raises(mining.NeedsConfirm) as refused:
            mining.plan_line_edits(db, lesson, "lesson-1", [{"line_key": L3, "on": [], "off": [[5, 9]]}])
        assert refused.value.status == 409
        assert refused.value.keys == [line_card(lesson, 3).with_blank((5, 9), "").key]

    def test_n3_with_confirm_it_goes(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        takk = next(r for _, i, r in db.list_sibling_rows(L3) if sentence_card(i.syntactic_unit).blank == (5, 9))
        _reviewed(db, takk)
        result = _edit(db, lesson, [{"line_key": L3, "on": [], "off": [[5, 9]], "confirm": True}], studied=True)
        assert result.removed == [takk]

    def test_n4_the_last_card_of_a_linked_line_cannot_go(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        _link(db, L3)
        with pytest.raises(mining.LastCardOfLine):
            mining.plan_line_edits(db, lesson, "lesson-1", [{"line_key": L3, "on": [], "off": [[5, 9], [14, 16]]}])
        assert len(_keys(db, L3)) == 2

    def test_n4_a_linked_line_may_trade_its_last_card_for_a_new_one(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        _link(db, L3)
        mining.plan_line_edits(db, lesson, "lesson-1", [{"line_key": L3, "on": ["whole"], "off": [[5, 9], [14, 16]]}])

    def test_n4_control_an_unlinked_lines_last_card_goes(self):
        db, lesson = SRSDatabase(":memory:"), _lesson()
        _study(db, lesson)
        result = _edit(db, lesson, [{"line_key": L3, "on": [], "off": [[5, 9], [14, 16]]}], studied=False)
        assert len(result.removed) == 2 and _keys(db, L3) == []

    def test_a_line_not_in_the_lesson_is_not_found(self):
        with pytest.raises(mining.LineNotFound):
            mining.plan_line_edits(
                SRSDatabase(":memory:"), _lesson(), "lesson-1", [{"line_key": "sent:x:1", "on": [[0, 3]], "off": []}]
            )

    @pytest.mark.parametrize("span", [[1, 3], [0, 9], [3, 0], [5, 7]])
    def test_a_span_that_is_not_a_word_of_the_line_is_refused(self, span):
        with pytest.raises(mining.NotWholeWords):
            mining.plan_line_edits(
                SRSDatabase(":memory:"), _lesson(), "lesson-1", [{"line_key": L3, "on": [span], "off": []}]
            )

    def test_the_part_of_a_word_the_list_blanks_is_a_span_the_line_takes(self):
        story = _story()
        story["cards"]["items"][3].update(
            text="Bilen er rød.", spoken="Bilen er rød.", blanks=[{"form": "-en", "gloss": "the"}]
        )
        lesson = _lesson(story)
        plan = mining.plan_line_edits(
            SRSDatabase(":memory:"), lesson, "lesson-1", [{"line_key": L3, "on": [], "off": [[3, 5]]}]
        )
        assert plan.marks_on == [line_card(lesson, 3).with_blank((3, 5), "").key]

    def test_a_word_both_on_and_off_is_refused(self):
        with pytest.raises(mining.MiningError):
            mining.plan_line_edits(
                SRSDatabase(":memory:"), _lesson(), "lesson-1", [{"line_key": L3, "on": [[0, 3]], "off": [[0, 3]]}]
            )

    def test_the_same_line_twice_is_refused(self):
        edit = {"line_key": L3, "on": [[0, 3]], "off": []}
        with pytest.raises(mining.MiningError):
            mining.plan_line_edits(SRSDatabase(":memory:"), _lesson(), "lesson-1", [edit, copy.deepcopy(edit)])


def test_a_mined_row_is_new_with_no_anki_ids():
    """The card-adding contract (anki-sync.md § When building a new UI that adds cards)."""
    db, lesson = SRSDatabase(":memory:"), _lesson()
    og = _span(line_card(lesson, 3).text, "og")
    (row,) = _edit(db, lesson, [{"line_key": L3, "on": [list(og)], "off": []}], {(L3, og): "and"}, studied=False).made
    _, item, _ = db.get_collocation_by_id(row)
    assert item.anki_note_id is None
    prod = item.directions[Direction.PRODUCTION]
    assert (prod.state.value, prod.anki_card_id, prod.last_review) == ("new", None, None)
    assert compute_guid(item.syntactic_unit.text, LANG, item.syntactic_unit.disambig_key) == item.guid


def test_the_marks_table_migration_is_idempotent():
    import sqlite3

    from app.srs.migrations import migrate_v51_to_v52

    conn = sqlite3.connect(":memory:")
    for _ in range(2):
        migrate_v51_to_v52(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 52
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'sentence_card_marks'").fetchone()
