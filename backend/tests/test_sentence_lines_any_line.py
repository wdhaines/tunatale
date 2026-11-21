"""Sentence mining on ANY lesson line, not only the lines a card list names (tunatale-h0q3.68).

Design: ``.beads-tasks/briefs/design-sentence-mining-2026-11.md`` § 2 (the scene-line
source), § 7, oracle rows L3, M4, M5 and G2. Owner decision 11-10 c: any lesson.

* A line the list does not name is built from the story's scene line: the key's set is
  the list's ``set`` when the lesson has a list, else the content id; text and spoken are
  the line; English is its translation; the glosses are the lesson's word glosses
  (``token_glosses``) for its words; the cue is the previous line of the same scene, and
  a scene's first line has none.
* Every scene line is a line of the read model, in story order; a lesson with no card
  list has every token off.
"""

from __future__ import annotations

import copy

import pytest

from app.models.lesson import Lesson
from app.srs import sentence_mining as mining
from app.srs.database import SRSDatabase
from app.srs.lesson_card_mint import mint_lesson_cards
from app.srs.sentence_card import Cue, Gloss, sentence_card
from app.srs.sentence_lines import line_card, sentence_lines
from tests.test_lesson_card_list import _story

LANG = "no"
CID = "dag-1-ab12cd34"  # a content id: a slug, as all 33 live ids are

GLOSSES = {"hei": "hi", "hvordan": "how", "bra": "fine", "takk": "thanks", "du": "you", "vi": "we", "ses": "see"}


def _scenes_story() -> dict:
    """``_story`` with no card list and a second scene, whose first line has no cue."""
    story = _story()
    del story["cards"]
    story["scenes"].append(
        {
            "label": "Later",
            "lines": [
                {"speaker": "male-1", "text": "Vi ses!", "translation": "See you!"},
                {"speaker": "female-1", "text": "Ha det, Per!", "translation": "Bye, Per!"},
            ],
        }
    )
    return story


def _lesson(story: dict) -> Lesson:
    return Lesson(
        title="Dag 1",
        language_code=LANG,
        generation_metadata={"story": story, "token_glosses": dict(GLOSSES)},
    )


def _line(lines, key):
    return next(line for line in lines if line.line_key == key)


def _span(text: str, word: str) -> tuple[int, int]:
    s = text.index(word)
    return (s, s + len(word))


def _keys(db, line_key) -> list[str]:
    return sorted(i.syntactic_unit.disambig_key for _, i, _ in db.list_sibling_rows(line_key))


# --- L3: a lesson with no card list -------------------------------------------------------


class TestNoList:
    def test_l3_every_scene_line_is_a_line_keyed_by_the_content_id(self):
        lines = sentence_lines(SRSDatabase(":memory:"), _lesson(_scenes_story()), CID)
        assert [(line.line_key, line.position) for line in lines] == [(f"sent:{CID}:{n}", n) for n in range(1, 6)]

    def test_l3_every_token_and_the_whole_card_is_off(self):
        lines = sentence_lines(SRSDatabase(":memory:"), _lesson(_scenes_story()), CID)
        for line in lines:
            assert {(t.state, t.origin, t.in_deck) for t in line.tokens} == {("off", "mine", False)}
            assert line.whole.state == "off"
            assert line.linked is False

    def test_l3_a_line_reads_the_scene_lines_own_text_and_translation(self):
        line = _line(sentence_lines(SRSDatabase(":memory:"), _lesson(_scenes_story()), CID), f"sent:{CID}:3")
        assert (line.text, line.english, line.speaker) == ("Bra, takk, og du?", "Fine, thanks, and you?", "female-1")
        assert [line.text[t.start : t.end] for t in line.tokens] == ["Bra", "takk", "og", "du"]
        assert line.spoken_map == [0, 1, 2, 3]

    def test_l3_a_token_carries_the_lessons_gloss_of_its_word(self):
        line = _line(sentence_lines(SRSDatabase(":memory:"), _lesson(_scenes_story()), CID), f"sent:{CID}:3")
        assert [t.gloss for t in line.tokens] == ["fine", "thanks", "", "you"]

    def test_l3_control_a_lesson_with_no_scenes_has_no_lines(self):
        story = _scenes_story()
        story["scenes"] = []
        assert sentence_lines(SRSDatabase(":memory:"), _lesson(story), CID) == []


class TestLineCardFromAScene:
    def test_the_card_is_the_scene_line(self):
        card = line_card(_lesson(_scenes_story()), 3, CID)
        assert (card.key, card.lesson_id, card.line_index, card.blank) == (f"sent:{CID}:3", CID, 3, None)
        assert (card.speaker, card.text, card.spoken, card.english) == (
            "female-1",
            "Bra, takk, og du?",
            "Bra, takk, og du?",
            "Fine, thanks, and you?",
        )
        assert card.picture == ""

    def test_its_glosses_are_the_lessons_for_its_words_in_order_once_each(self):
        card = line_card(_lesson(_scenes_story()), 3, CID)
        assert card.glosses == (Gloss("Bra", "fine"), Gloss("takk", "thanks"), Gloss("du", "you"))

    def test_its_cue_is_the_line_before_in_the_same_scene(self):
        card = line_card(_lesson(_scenes_story()), 3, CID)
        assert card.cue == Cue(
            speaker="male-1",
            text="Hei, hvordan gar det?",
            spoken="Hei, hvordan gar det?",
            english="Hi, how are you?",
            glosses=(Gloss("Hei", "hi"), Gloss("hvordan", "how")),
        )

    @pytest.mark.parametrize("position", [1, 4])  # the story's first line; the second scene's first line
    def test_a_scenes_first_line_has_no_cue(self, position):
        assert line_card(_lesson(_scenes_story()), position, CID).cue is None

    def test_control_the_second_line_of_the_second_scene_is_cued(self):
        assert line_card(_lesson(_scenes_story()), 5, CID).cue.text == "Vi ses!"

    def test_a_position_past_the_last_line_has_no_card(self):
        assert line_card(_lesson(_scenes_story()), 6, CID) is None
        assert line_card(_lesson(_scenes_story()), 0, CID) is None


# --- a lesson with a list: the lines it does not name ---------------------------------------


def _list_story_with_an_unlisted_line() -> dict:
    story = _story()
    story["scenes"][0]["lines"].append({"speaker": "male-1", "text": "Vi ses!", "translation": "See you!"})
    return story


class TestUnlistedLineOfAListLesson:
    def test_it_is_a_line_keyed_by_the_lists_set(self):
        lines = sentence_lines(SRSDatabase(":memory:"), _lesson(_list_story_with_an_unlisted_line()), CID)
        assert [line.line_key for line in lines] == [f"sent:test-01:{n}" for n in range(1, 5)]
        line = _line(lines, "sent:test-01:4")
        assert (line.text, line.whole.state) == ("Vi ses!", "off")
        assert {t.state for t in line.tokens} == {"off"}

    def test_its_card_is_built_from_the_scene_line(self):
        card = line_card(_lesson(_list_story_with_an_unlisted_line()), 4, CID)
        assert (card.key, card.text, card.english) == ("sent:test-01:4", "Vi ses!", "See you!")
        assert card.cue.text == "Bra, takk, og du?"

    def test_control_a_listed_line_is_still_the_lists_card(self):
        card = line_card(_lesson(_list_story_with_an_unlisted_line()), 2, CID)
        assert (card.speaker, card.text, card.spoken) == ("Per", "Hei, hvordan går det?", "Hei, hvordan gar det?")

    def test_the_study_pass_mints_nothing_on_it(self):
        db, story = SRSDatabase(":memory:"), _list_story_with_an_unlisted_line()
        mint_lesson_cards(db, LANG, story["cards"])
        assert _keys(db, "sent:test-01:4") == []


# --- M4: mining an uncarded line ---------------------------------------------------------------


def _edit(db, lesson, edits, glosses=None):
    plan = mining.plan_line_edits(db, lesson, CID, edits)
    mining.check_glossed(plan, glosses or {})
    mining.write_marks(db, plan)
    return mining.apply_rows(db, LANG, lesson, plan, glosses or {}, studied=True)


class TestMineAnUncardedLine:
    KEY = f"sent:{CID}:3"

    def test_m4_a_blank_is_built_by_line_card_new_with_no_anki_ids(self):
        db, lesson = SRSDatabase(":memory:"), _lesson(_scenes_story())
        og = _span("Bra, takk, og du?", "og")
        (row,) = _edit(db, lesson, [{"line_key": self.KEY, "on": [list(og)], "off": []}], {(self.KEY, og): "and"}).made
        item = db.get_collocation_by_id(row)[1]
        assert sentence_card(item.syntactic_unit) == line_card(lesson, 3, CID).with_blank(og, "and")
        assert item.syntactic_unit.source == "user"
        assert item.anki_note_id is None
        assert {d.anki_card_id for d in item.directions.values()} == {None}
        assert {d.state.value for d in item.directions.values()} == {"new"}

    def test_m4_the_token_reads_on_and_mine_after(self):
        db, lesson = SRSDatabase(":memory:"), _lesson(_scenes_story())
        og = _span("Bra, takk, og du?", "og")
        _edit(db, lesson, [{"line_key": self.KEY, "on": [list(og)], "off": []}], {(self.KEY, og): "and"})
        line = _line(sentence_lines(db, lesson, CID), self.KEY)
        token = next(t for t in line.tokens if (t.start, t.end) == og)
        assert (token.state, token.origin, token.in_deck) == ("on", "mine", True)

    def test_m4_the_same_edit_again_adds_nothing(self):
        db, lesson = SRSDatabase(":memory:"), _lesson(_scenes_story())
        og = _span("Bra, takk, og du?", "og")
        edit = [{"line_key": self.KEY, "on": [list(og)], "off": []}]
        _edit(db, lesson, edit, {(self.KEY, og): "and"})
        again = _edit(db, lesson, copy.deepcopy(edit), {(self.KEY, og): "and"})
        assert again.made == []
        assert len(_keys(db, self.KEY)) == 1

    def test_m4_the_whole_sentence_card_mines_too(self):
        db, lesson = SRSDatabase(":memory:"), _lesson(_scenes_story())
        (row,) = _edit(db, lesson, [{"line_key": self.KEY, "on": ["whole"], "off": []}]).made
        assert sentence_card(db.get_collocation_by_id(row)[1].syntactic_unit) == line_card(lesson, 3, CID)

    def test_a_blank_with_the_lessons_own_gloss_needs_no_model(self):
        db, lesson = SRSDatabase(":memory:"), _lesson(_scenes_story())
        du = _span("Bra, takk, og du?", "du")
        plan = mining.plan_line_edits(db, lesson, CID, [{"line_key": self.KEY, "on": [list(du)], "off": []}])
        assert [n.own_gloss for n in plan.needs_gloss] == ["you"]

    def test_an_unlinked_mined_lines_last_card_may_go(self):
        db, lesson = SRSDatabase(":memory:"), _lesson(_scenes_story())
        _edit(db, lesson, [{"line_key": self.KEY, "on": ["whole"], "off": []}])
        result = _edit(db, lesson, [{"line_key": self.KEY, "on": [], "off": ["whole"]}])
        assert len(result.removed) == 1
        assert _keys(db, self.KEY) == []
        assert db.sentence_card_marks() == set()  # not a list card: nothing to mark

    def test_a_line_key_the_lesson_lacks_is_refused(self):
        db, lesson = SRSDatabase(":memory:"), _lesson(_scenes_story())
        with pytest.raises(mining.LineNotFound):
            mining.plan_line_edits(db, lesson, CID, [{"line_key": f"sent:{CID}:9", "on": ["whole"], "off": []}])

    def test_the_rows_say_which_voices_say_the_line_and_its_cue(self):
        """M5's input: a line's first mined row is voiced in the media step by its line's roles."""
        db, lesson = SRSDatabase(":memory:"), _lesson(_scenes_story())
        result = _edit(db, lesson, [{"line_key": self.KEY, "on": ["whole"], "off": []}])
        ((row, card),) = result.voice
        assert row == result.made[0]
        assert (card.role, card.cue_role) == ("female-1", "male-1")
        assert sentence_card(card.unit) == line_card(lesson, 3, CID)

    def test_a_scenes_first_line_is_voiced_with_no_cue_role(self):
        db, lesson = SRSDatabase(":memory:"), _lesson(_scenes_story())
        result = _edit(db, lesson, [{"line_key": f"sent:{CID}:4", "on": ["whole"], "off": []}])
        ((_row, card),) = result.voice
        assert (card.role, card.cue_role) == ("male-1", "")
