"""The rotating drill card (tunatale-5i1p.4): one card per drill, its step chosen by the review count.

Design and oracle: ``.beads-tasks/briefs/design-rotating-drill-card-2026-11.md``
§ Build 1 and § Oracle (U1-U5 here). A drill card is stored whole in the row's
``card_data``; ``view(reps)`` is the one function that says what a review
shows (``step = reps mod steps``), so TT and Anki agree on the step without
storing it. A question-and-answer drill is not rotated: it makes one card per
step, whose view is always that step.

Invented Norwegian drills only: the course text never enters the public repo.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.common.guid import compute_guid
from app.srs.drill_card import (
    DrillCard,
    DrillStep,
    drill_card,
    drill_cards,
    is_drill_key,
)
from app.srs.sentence_card import SentenceCard, blank_span, sentence_card, sibling_group
from tests.test_sentence_card import CARD

SUBSTITUTION = {
    "kind": "substitution",
    "model": "Han er her.",
    "speaker": "male-1",
    "steps": [
        {"cue": "hun", "answer": "Hun er her."},
        {"cue": "de", "answer": "De er her."},
        {"cue": "der", "answer": "De er der."},
        {"cue": "vi", "answer": "Vi er der.", "english": "We are there."},
    ],
}
TRANSFORMATION = {
    "kind": "transformation",
    "instruction": "Ask where.",
    "steps": [
        {"cue": "Han er der.", "answer": "Hvor er han?"},
        {"cue": "Hun er her.", "answer": "Hvor er hun?"},
        {"cue": "De er hjemme.", "answer": "Hvor er de?"},
        {"cue": "Vi er ute.", "answer": "Hvor er vi?"},
    ],
}
TRUE_FALSE = {
    "kind": "true_false",
    "steps": [{"cue": "Oslo er i Norge.", "answer": "Ja."}, {"cue": "Bergen er i Sverige.", "answer": "Nei."}],
}
QUESTION_ANSWER = {
    "kind": "question_answer",
    "steps": [
        {"cue": "Hvor er han?", "answer": "Han er her."},
        {"cue": "Hvor er hun?", "answer": "Hun er der."},
        {"cue": "Hvem er det?", "answer": "Det er Per."},
    ],
}
DRILLS = [SUBSTITUTION, TRANSFORMATION, TRUE_FALSE, QUESTION_ANSWER]


def _card(drill: dict, n: int = 1) -> DrillCard:
    (card,) = drill_cards("test-01", n, drill)
    return card


# --------------------------------------------------------------------------
# U1-U3: which step a review shows
# --------------------------------------------------------------------------


class TestStep:
    def test_u1_reps_wrap_round_the_steps(self):
        card = _card(SUBSTITUTION)
        n = len(SUBSTITUTION["steps"])
        assert [card.view(reps).step for reps in range(n + 2)] == [*range(n), 0, 1]
        assert {card.view(reps).of for reps in range(n + 2)} == {n}

    def test_u1_each_step_asks_its_own_cue_and_answer(self):
        card = _card(SUBSTITUTION)
        view = card.view(5)  # 5 mod 4 = step 1
        assert (view.cue, view.answer) == ("de", "De er her.")

    def test_u2_substitution_step_0_follows_the_model(self):
        view = _card(SUBSTITUTION).view(0)
        assert view.before == "Han er her."
        assert view.before_cue is None

    def test_u2_substitution_step_3_follows_step_2s_answer(self):
        view = _card(SUBSTITUTION).view(3)
        assert view.before == "De er der."  # step 2's answer, not the model
        assert view.before_cue is None
        assert (view.cue, view.answer, view.english) == ("vi", "Vi er der.", "We are there.")

    def test_u3_transformation_step_3_shows_step_2s_worked_pair(self):
        view = _card(TRANSFORMATION).view(3)
        assert (view.before_cue, view.before) == ("De er hjemme.", "Hvor er de?")
        assert view.cue == "Vi er ute."

    def test_u3_transformation_step_0_takes_the_last_pair(self):
        view = _card(TRANSFORMATION).view(0)
        assert (view.before_cue, view.before) == ("Vi er ute.", "Hvor er vi?")
        assert view.cue == "Han er der."

    def test_true_false_shows_the_statement_alone(self):
        view = _card(TRUE_FALSE).view(1)
        assert (view.before_cue, view.before) == (None, None)
        assert (view.cue, view.answer) == ("Bergen er i Sverige.", "Nei.")

    def test_english_is_empty_where_the_lesson_gives_none(self):
        assert _card(SUBSTITUTION).view(0).english == ""


class TestMediaKinds:
    """Which of the row's clips each part of a view plays: one media row per clip, by step."""

    def test_substitution_step_0_plays_the_model_before(self):
        view = _card(SUBSTITUTION).view(0)
        assert view.before_media == "drill_model"
        assert view.before_cue_media is None
        assert (view.cue_media, view.answer_media) == ("drill_cue_0", "drill_answer_0")

    def test_substitution_later_steps_play_the_previous_answer_before(self):
        view = _card(SUBSTITUTION).view(2)
        assert view.before_media == "drill_answer_1"
        assert (view.cue_media, view.answer_media) == ("drill_cue_2", "drill_answer_2")

    def test_transformation_plays_the_worked_pairs_clips(self):
        view = _card(TRANSFORMATION).view(0)
        assert (view.before_cue_media, view.before_media) == ("drill_cue_3", "drill_answer_3")

    def test_a_statement_alone_has_no_before_clips(self):
        view = _card(TRUE_FALSE).view(0)
        assert (view.before_cue_media, view.before_media) == (None, None)

    def test_media_kinds_name_every_clip_the_card_needs(self):
        assert _card(SUBSTITUTION).media_kinds() == [
            ("drill_model", "Han er her."),
            ("drill_cue_0", "hun"),
            ("drill_answer_0", "Hun er her."),
            ("drill_cue_1", "de"),
            ("drill_answer_1", "De er her."),
            ("drill_cue_2", "der"),
            ("drill_answer_2", "De er der."),
            ("drill_cue_3", "vi"),
            ("drill_answer_3", "Vi er der."),
        ]

    def test_a_question_and_answer_step_needs_only_its_own_clips(self):
        card = drill_cards("test-01", 4, QUESTION_ANSWER)[1]
        assert card.media_kinds() == [("drill_cue_1", "Hvor er hun?"), ("drill_answer_1", "Hun er der.")]


# --------------------------------------------------------------------------
# U4 and the key: the row never changes with the step
# --------------------------------------------------------------------------


class TestRow:
    def test_u4_row_text_key_and_guid_do_not_move_with_reps(self):
        card = _card(SUBSTITUTION)
        a, b = card.view(0), card.view(5)
        assert a.step != b.step  # control: the two views ARE different steps
        unit = card.unit()
        assert unit.text == "Han er her."  # the model
        assert unit.disambig_key == "sent:test-01:drill-1"
        guid = compute_guid(unit.text, "no", unit.disambig_key)
        assert compute_guid(card.unit().text, "no", card.unit().disambig_key) == guid

    def test_the_other_kinds_take_the_first_cue_as_their_text(self):
        assert _card(TRANSFORMATION, 2).unit().text == "Han er der."
        assert _card(TRUE_FALSE, 3).unit().text == "Oslo er i Norge."

    def test_the_row_is_a_production_cloze_carrying_the_whole_drill(self):
        unit = _card(SUBSTITUTION).unit()
        assert unit.card_type == "cloze"
        assert unit.source_lesson_id == "test-01"
        assert drill_card(unit) == _card(SUBSTITUTION)

    def test_its_sibling_group_is_its_own_key(self):
        unit = _card(SUBSTITUTION).unit()
        assert sibling_group(unit) == "sent:test-01:drill-1"

    def test_a_question_and_answer_drill_makes_one_card_per_step(self):
        cards = drill_cards("test-01", 4, QUESTION_ANSWER)
        assert [c.key for c in cards] == ["sent:test-01:drill-4:1", "sent:test-01:drill-4:2", "sent:test-01:drill-4:3"]
        assert [c.unit().text for c in cards] == ["Hvor er han?", "Hvor er hun?", "Hvem er det?"]

    def test_a_question_and_answer_step_does_not_rotate(self):
        card = drill_cards("test-01", 4, QUESTION_ANSWER)[1]
        assert {card.view(reps).step for reps in range(6)} == {1}
        view = card.view(7)
        assert (view.cue, view.answer, view.of) == ("Hvor er hun?", "Hun er der.", 3)
        assert (view.before_cue, view.before) == (None, None)

    def test_each_question_and_answer_step_is_its_own_sibling_group(self):
        cards = drill_cards("test-01", 4, QUESTION_ANSWER)
        groups = [sibling_group(c.unit()) for c in cards]
        assert groups == [c.key for c in cards]
        assert "sent:test-01:drill-4" not in groups


class TestKeys:
    @pytest.mark.parametrize(
        "key",
        ["sent:test-01:drill-1", "sent:who-is-here-1:drill-12", "sent:a:drill-4:3"],
    )
    def test_drill_keys(self, key):
        assert is_drill_key(key)

    @pytest.mark.parametrize(
        "key",
        [
            "sent:test-01:1",  # a lesson line
            "sent:test-01:2:4-7",  # a blank
            "sent:test-01:drill-0",  # drills count from 1
            "sent:test-01:drill-1:0",  # and so do their steps
            "sent:test-01:drill-x",
            "sent:test-01:drill-1:2:3",
            "xsent:test-01:drill-1",
            "sent:test-01:drill-1 ",
            "morph:drill-1",
            "",
        ],
    )
    def test_not_drill_keys(self, key):
        assert not is_drill_key(key)


# --------------------------------------------------------------------------
# JSON: two shapes that never read as each other
# --------------------------------------------------------------------------


class TestJson:
    @pytest.mark.parametrize("drill", DRILLS)
    def test_round_trip(self, drill):
        for card in drill_cards("test-01", 2, drill):
            assert DrillCard.from_json(card.to_json()) == card

    def test_a_drill_row_is_not_a_sentence_card(self):
        unit = _card(SUBSTITUTION).unit()
        assert SentenceCard.from_json(unit.card_data) is None
        assert sentence_card(unit) is None

    def test_a_sentence_row_is_not_a_drill_card(self):
        assert DrillCard.from_json(CARD.to_json()) is None
        assert drill_card(CARD.unit()) is None

    @pytest.mark.parametrize("raw", [None, "", "[]", "{}", '{"drill": {}}', '{"drill": 3}', "not json"])
    def test_anything_else_is_none(self, raw):
        assert DrillCard.from_json(raw) is None

    def test_a_drill_key_alone_does_not_make_a_drill_card(self):
        unit = replace(CARD.unit(), disambig_key="sent:test-01:drill-1")
        assert drill_card(unit) is None

    def test_drill_json_off_a_sentence_key_is_not_a_drill_card(self):
        unit = replace(_card(SUBSTITUTION).unit(), disambig_key="vocab-key")
        assert drill_card(unit) is None

    def test_steps_come_back_as_steps(self):
        card = DrillCard.from_json(_card(SUBSTITUTION).to_json())
        assert card.steps[3] == DrillStep("vi", "Vi er der.", "We are there.")


# --------------------------------------------------------------------------
# U5: a plain sentence card's stored JSON did not move by a byte
# --------------------------------------------------------------------------

# Captured from main at c96faba2 (before the drill card) by running each
# fixture's to_json there. Literal bytes, not a round trip: a round trip stays
# green when the writer and the reader change together.
_PLAIN_BYTES = [
    '{"lesson_id": "a-visit-1234", "line_index": 3, "speaker": "Per", "text": "Jeg bor i Oslo.", "spoken": "Jeg bor i Oslo.", "english": "I live in Oslo.", "glosses": [{"word": "jeg", "gloss": "I"}, {"word": "bor", "gloss": "live(s)"}, {"word": "i", "gloss": "[preposition: in]"}], "cue": {"speaker": "Kari", "text": "Hvor bor du?", "spoken": "Hvor bor du?", "english": "Where do you live?", "glosses": [{"word": "hvor", "gloss": "where?"}, {"word": "bo", "gloss": "live"}]}, "blank": null, "blank_gloss": "", "picture": ""}',
    '{"lesson_id": "a-visit-1234", "line_index": 3, "speaker": "Per", "text": "Jeg bor i Oslo.", "spoken": "Jeg bor i Oslo.", "english": "I live in Oslo.", "glosses": [{"word": "jeg", "gloss": "I"}, {"word": "bor", "gloss": "live(s)"}, {"word": "i", "gloss": "[preposition: in]"}], "cue": {"speaker": "Kari", "text": "Hvor bor du?", "spoken": "Hvor bor du?", "english": "Where do you live?", "glosses": [{"word": "hvor", "gloss": "where?"}, {"word": "bo", "gloss": "live"}]}, "blank": [4, 7], "blank_gloss": "live(s)", "picture": ""}',
    '{"lesson_id": "a-visit-1234", "line_index": 3, "speaker": "Per", "text": "Jeg bor i Oslo.", "spoken": "Jeg bor i Oslo.", "english": "I live in Oslo.", "glosses": [{"word": "jeg", "gloss": "I"}, {"word": "bor", "gloss": "live(s)"}, {"word": "i", "gloss": "[preposition: in]"}], "cue": null, "blank": null, "blank_gloss": "", "picture": "pic.png"}',
    '{"lesson_id": "test-01", "line_index": 1, "speaker": "Kari", "text": "Hei!", "spoken": "Hei!", "english": "Hi!", "glosses": [{"word": "hei", "gloss": "hi"}], "cue": null, "blank": null, "blank_gloss": "", "picture": "door.svg"}',
    '{"lesson_id": "test-01", "line_index": 2, "speaker": "Per", "text": "Hei, hvordan går det?", "spoken": "Hei, hvordan gar det?", "english": "Hi, how are you?", "glosses": [{"word": "går", "gloss": "goes"}], "cue": {"speaker": "Kari", "text": "Hei!", "spoken": "Hei!", "english": "Hi!", "glosses": []}, "blank": [13, 16], "blank_gloss": "_goes_ (å gå)", "picture": ""}',
    '{"lesson_id": "test-01", "line_index": 2, "speaker": "Per", "text": "Hei, hvordan går det?", "spoken": "Hei, hvordan gar det?", "english": "Hi, how are you?", "glosses": [{"word": "går", "gloss": "goes"}], "cue": {"speaker": "Kari", "text": "Hei!", "spoken": "Hei!", "english": "Hi!", "glosses": []}, "blank": null, "blank_gloss": "", "picture": ""}',
    '{"lesson_id": "test-01", "line_index": 3, "speaker": "Kari", "text": "Bra, takk, og du?", "spoken": "Bra, takk, og du?", "english": "Fine, thanks, and you?", "glosses": [{"word": "bra", "gloss": "fine"}, {"word": "takk", "gloss": "thanks"}], "cue": {"speaker": "Per", "text": "Hei, hvordan går det?", "spoken": "Hei, hvordan gar det?", "english": "Hi, how are you?", "glosses": [{"word": "går", "gloss": "goes"}]}, "blank": [5, 9], "blank_gloss": "thanks", "picture": ""}',
    '{"lesson_id": "test-01", "line_index": 3, "speaker": "Kari", "text": "Bra, takk, og du?", "spoken": "Bra, takk, og du?", "english": "Fine, thanks, and you?", "glosses": [{"word": "bra", "gloss": "fine"}, {"word": "takk", "gloss": "thanks"}], "cue": {"speaker": "Per", "text": "Hei, hvordan går det?", "spoken": "Hei, hvordan gar det?", "english": "Hi, how are you?", "glosses": [{"word": "går", "gloss": "goes"}]}, "blank": [14, 16], "blank_gloss": "you", "picture": ""}',
]


def _plain_fixture_bytes() -> list[str]:
    from app.srs.lesson_cards import lesson_cards
    from tests.test_lesson_card_list import _cards

    out = [
        CARD.to_json(),
        CARD.with_blank(blank_span(CARD.text, "bor"), "live(s)").to_json(),
        replace(CARD, cue=None, picture="pic.png").to_json(),
    ]
    out += [c.unit.card_data for c in lesson_cards(_cards()) if c.unit.card_data]
    return out


def test_u5_plain_sentence_cards_serialise_byte_for_byte_as_before():
    assert _plain_fixture_bytes() == _PLAIN_BYTES
