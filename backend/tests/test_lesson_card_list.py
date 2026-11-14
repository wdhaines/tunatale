"""A lesson file's card list: its format and the cards it builds (tunatale-h0q3.43).

A Story JSON may carry ``cards``: the lesson's own card list, in the order the
cards should reach the deck (the author decides n+1 order: words before the
sentences that use them). ``validate_story`` refuses a malformed list by name,
at import, rather than at mint or render time. ``lesson_cards`` turns a valid
list into the units the mint writes, without writing anything.

Format::

    "cards": {
      "set": "<slug>",          # stable across re-imports: card keys use it, not the lesson id
      "items": [
        {"kind": "word", "form": ..., "translation": ..., "lemma"?: ...},
        {"kind": "sentence", "line": <int >= 1>, "speaker": ..., "role": <voice-map key>,
         "text": ..., "spoken": <a scenes line's text>, "english": ...,
         "glosses": [{"word": ..., "gloss": ...}],
         "cue": null | {"speaker", "role", "text", "spoken", "english", "glosses"},
         "picture"?: "<media filename>", "whole": <bool>, "blanks": [{"form": ..., "gloss": ...}]},
        {"kind": "drill", "drill": <int >= 1: the story's drills[n - 1]>, "role": <voice-map key>}
      ]
    }

A ``drill`` item (tunatale-5i1p.4) is tested in ``test_lesson_drill_cards.py``.

``text`` is what the card shows (it may carry accents the lesson line lacks);
``spoken`` is the lesson line whose take is the card's audio, so it must be one
of the story's own lines.
"""

import copy

import pytest

from app.common.guid import compute_guid
from app.models.curriculum import Curriculum, CurriculumDay
from app.srs.lesson_cards import COURSE_SOURCE, LessonCard, lesson_cards
from app.srs.sentence_card import SentenceCard, blank_span, sentence_card
from app.storage.lesson_io import export_lesson, import_lesson, validate_story
from app.storage.store import ContentStore

_HEI = {"speaker": "Kari", "role": "female-1", "text": "Hei!", "spoken": "Hei!", "english": "Hi!", "glosses": []}
_HVORDAN = {
    "speaker": "Per",
    "role": "male-1",
    "text": "Hei, hvordan går det?",
    "spoken": "Hei, hvordan gar det?",
    "english": "Hi, how are you?",
    "glosses": [{"word": "går", "gloss": "goes"}],
}


def _cards() -> dict:
    return {
        "set": "test-01",
        "items": [
            {"kind": "word", "form": "takk", "translation": "thanks"},
            {
                "kind": "sentence",
                "line": 1,
                **_HEI,
                "glosses": [{"word": "hei", "gloss": "hi"}],
                "cue": None,
                "picture": "door.svg",
                "whole": True,
                "blanks": [],
            },
            {
                "kind": "sentence",
                "line": 2,
                **_HVORDAN,
                "cue": copy.deepcopy(_HEI),
                "whole": True,
                "blanks": [{"form": "går", "gloss": "_goes_ (å gå)"}],
            },
            {
                "kind": "sentence",
                "line": 3,
                "speaker": "Kari",
                "role": "female-1",
                "text": "Bra, takk, og du?",
                "spoken": "Bra, takk, og du?",
                "english": "Fine, thanks, and you?",
                "glosses": [{"word": "bra", "gloss": "fine"}, {"word": "takk", "gloss": "thanks"}],
                "cue": copy.deepcopy(_HVORDAN),
                "whole": False,
                "blanks": [{"form": "takk", "gloss": "thanks"}, {"form": "du", "gloss": "you"}],
            },
        ],
    }


def _story() -> dict:
    return {
        "title": "Hei",
        "key_phrases": [{"phrase": "Bra, takk.", "translation": "Fine, thanks."}],
        "scenes": [
            {
                "label": "On the street",
                "lines": [
                    {"speaker": "female-1", "text": "Hei!", "translation": "Hi!"},
                    {"speaker": "male-1", "text": "Hei, hvordan gar det?", "translation": "Hi, how are you?"},
                    {"speaker": "female-1", "text": "Bra, takk, og du?", "translation": "Fine, thanks, and you?"},
                ],
            }
        ],
        "cards": _cards(),
    }


def _item(story: dict, line: int) -> dict:
    return next(i for i in story["cards"]["items"] if i.get("line") == line)


# --------------------------------------------------------------------------
# validate_story: the cards block
# --------------------------------------------------------------------------


class TestValidateCards:
    def test_accepts_a_valid_card_list(self, language_no):
        validate_story(_story(), language=language_no)

    def test_cards_are_optional(self, language_no):
        story = _story()
        del story["cards"]
        validate_story(story, language=language_no)

    @pytest.mark.parametrize("bad", [[], "cards", 3, None])
    def test_cards_must_be_an_object(self, bad):
        story = _story()
        story["cards"] = bad
        with pytest.raises(ValueError, match=r"^cards must be an object"):
            validate_story(story)

    @pytest.mark.parametrize("bad", [None, "", "Wolff 1", "wolff_1", "-wolff", "Wolff-1", 7])
    def test_set_must_be_a_slug(self, bad):
        story = _story()
        story["cards"]["set"] = bad
        with pytest.raises(ValueError, match=r"^cards\.set "):
            validate_story(story)

    def test_set_is_required(self):
        story = _story()
        del story["cards"]["set"]
        with pytest.raises(ValueError, match=r"^cards\.set "):
            validate_story(story)

    @pytest.mark.parametrize("bad", [[], {}, "x", None])
    def test_items_must_be_a_non_empty_list(self, bad):
        story = _story()
        story["cards"]["items"] = bad
        with pytest.raises(ValueError, match=r"^cards\.items must be a non-empty list"):
            validate_story(story)

    @pytest.mark.parametrize("bad", ["pattern", "", None])
    def test_unknown_kind_is_refused_by_path(self, bad):
        story = _story()
        story["cards"]["items"][2]["kind"] = bad
        with pytest.raises(ValueError, match=r"^cards\.items\[2\]\.kind "):
            validate_story(story)

    def test_an_item_must_be_an_object(self):
        story = _story()
        story["cards"]["items"][1] = "Hei!"
        with pytest.raises(ValueError, match=r"^cards\.items\[1\] must be an object"):
            validate_story(story)

    # -- word items --

    @pytest.mark.parametrize("field", ["form", "translation"])
    def test_word_needs_its_strings(self, field):
        story = _story()
        story["cards"]["items"][0][field] = "  "
        with pytest.raises(ValueError, match=rf"^cards\.items\[0\]\.{field} must be a non-empty string"):
            validate_story(story)

    def test_word_lemma_when_present_is_a_non_empty_string(self):
        story = _story()
        story["cards"]["items"][0]["lemma"] = ""
        with pytest.raises(ValueError, match=r"^cards\.items\[0\]\.lemma must be a non-empty string"):
            validate_story(story)

    def test_a_word_twice_is_refused(self):
        story = _story()
        story["cards"]["items"].append({"kind": "word", "form": "takk", "translation": "thank you"})
        with pytest.raises(ValueError, match=r"^cards\.items\[4\]\.form 'takk' is already listed"):
            validate_story(story)

    # -- sentence items --

    @pytest.mark.parametrize("field", ["speaker", "role", "text", "spoken", "english"])
    def test_sentence_needs_its_strings(self, field):
        story = _story()
        _item(story, 2)[field] = ""
        with pytest.raises(ValueError, match=rf"^cards\.items\[2\]\.{field} must be a non-empty string"):
            validate_story(story)

    @pytest.mark.parametrize("field", ["speaker", "role", "text", "spoken", "english"])
    def test_cue_needs_its_strings(self, field):
        story = _story()
        _item(story, 2)["cue"][field] = ""
        with pytest.raises(ValueError, match=rf"^cards\.items\[2\]\.cue\.{field} must be a non-empty string"):
            validate_story(story)

    @pytest.mark.parametrize("bad", [0, -1, "1", 1.0, True, None])
    def test_line_is_a_positive_int(self, bad):
        story = _story()
        _item(story, 2)["line"] = bad
        with pytest.raises(ValueError, match=r"^cards\.items\[2\]\.line must be a positive integer"):
            validate_story(story)

    def test_a_line_twice_is_refused(self):
        story = _story()
        _item(story, 3)["line"] = 2
        with pytest.raises(ValueError, match=r"^cards\.items\[3\]\.line 2 is already listed"):
            validate_story(story)

    def test_unknown_role_is_refused_when_language_given(self, language_no):
        story = _story()
        _item(story, 2)["role"] = "robot-8"
        with pytest.raises(ValueError, match=r"^cards\.items\[2\]\.role 'robot-8' is not in the .* voice map"):
            validate_story(story, language=language_no)

    def test_unknown_cue_role_is_refused_when_language_given(self, language_no):
        story = _story()
        _item(story, 2)["cue"]["role"] = "robot-8"
        with pytest.raises(ValueError, match=r"^cards\.items\[2\]\.cue\.role 'robot-8' is not in the .* voice map"):
            validate_story(story, language=language_no)

    def test_unknown_role_is_accepted_without_language(self):
        story = _story()
        _item(story, 2)["role"] = "robot-8"
        validate_story(story)

    def test_spoken_must_be_one_of_the_storys_lines(self):
        """The audio is the lesson's take of ``spoken``: a line the lesson never says has none."""
        story = _story()
        _item(story, 2)["spoken"] = "Hei, hvordan går det?"
        with pytest.raises(ValueError, match=r"^cards\.items\[2\]\.spoken 'Hei, hvordan går det\?' is not a line"):
            validate_story(story)

    def test_cue_spoken_must_be_one_of_the_storys_lines(self):
        story = _story()
        _item(story, 3)["cue"]["spoken"] = "Hei hei"
        with pytest.raises(ValueError, match=r"^cards\.items\[3\]\.cue\.spoken 'Hei hei' is not a line"):
            validate_story(story)

    def test_cue_is_null_or_an_object(self):
        story = _story()
        _item(story, 2)["cue"] = "Hei!"
        with pytest.raises(ValueError, match=r"^cards\.items\[2\]\.cue must be null or an object"):
            validate_story(story)

    def test_cue_is_required_even_when_null(self):
        story = _story()
        del _item(story, 1)["cue"]
        with pytest.raises(ValueError, match=r"^cards\.items\[1\] is missing required field 'cue'"):
            validate_story(story)

    @pytest.mark.parametrize("bad", ["yes", 1, None])
    def test_whole_is_a_bool(self, bad):
        story = _story()
        _item(story, 2)["whole"] = bad
        with pytest.raises(ValueError, match=r"^cards\.items\[2\]\.whole must be true or false"):
            validate_story(story)

    def test_blanks_is_a_list(self):
        story = _story()
        _item(story, 2)["blanks"] = {"form": "går"}
        with pytest.raises(ValueError, match=r"^cards\.items\[2\]\.blanks must be a list"):
            validate_story(story)

    @pytest.mark.parametrize("field", ["form", "gloss"])
    def test_blank_needs_its_strings(self, field):
        story = _story()
        _item(story, 3)["blanks"][1][field] = ""
        with pytest.raises(ValueError, match=rf"^cards\.items\[3\]\.blanks\[1\]\.{field} must be a non-empty string"):
            validate_story(story)

    def test_a_blank_must_be_in_the_text(self):
        story = _story()
        _item(story, 3)["blanks"][0]["form"] = "tusen"
        with pytest.raises(
            ValueError, match=r"^cards\.items\[3\]\.blanks\[0\]\.form 'tusen' is not in 'Bra, takk, og du\?'"
        ):
            validate_story(story)

    def test_the_same_blank_twice_is_refused(self):
        """Two blanks on one span would be one card key twice."""
        story = _story()
        _item(story, 3)["blanks"][1]["form"] = "takk"
        with pytest.raises(ValueError, match=r"^cards\.items\[3\]\.blanks\[1\]\.form 'takk' is already a blank"):
            validate_story(story)

    def test_a_sentence_must_make_a_card(self):
        story = _story()
        _item(story, 1)["whole"] = False
        with pytest.raises(ValueError, match=r"^cards\.items\[1\] makes no card"):
            validate_story(story)

    @pytest.mark.parametrize(
        "bad",
        [
            "hei",
            ["hei"],
            [{"word": "hei"}],
            [{"word": "", "gloss": "hi"}],
            [{"word": "hei", "gloss": 3}],
        ],
    )
    def test_glosses_are_word_gloss_pairs(self, bad):
        story = _story()
        _item(story, 1)["glosses"] = bad
        with pytest.raises(ValueError, match=r"^cards\.items\[1\]\.glosses"):
            validate_story(story)

    def test_cue_glosses_are_word_gloss_pairs(self):
        story = _story()
        _item(story, 2)["cue"]["glosses"] = [{"word": "hei"}]
        with pytest.raises(ValueError, match=r"^cards\.items\[2\]\.cue\.glosses"):
            validate_story(story)

    def test_picture_when_present_is_a_string(self):
        story = _story()
        _item(story, 1)["picture"] = 4
        with pytest.raises(ValueError, match=r"^cards\.items\[1\]\.picture must be a string"):
            validate_story(story)


# --------------------------------------------------------------------------
# export: the card list survives import -> export unchanged
# --------------------------------------------------------------------------


@pytest.fixture
def store():
    with ContentStore(":memory:") as s:
        yield s


def test_export_returns_the_card_list_unchanged(store, language_no):
    store.save_curriculum(
        "c1",
        Curriculum(
            id="c1",
            topic="Test",
            language_code="no",
            cefr_level="A1",
            days=[CurriculumDay(day=1, title="d1", focus="f", collocations=[], learning_objective="o")],
        ),
    )
    lesson_id, _ = import_lesson(store, {"curriculum_id": "c1", "day": 1, "story": _story()}, language_no)
    assert export_lesson(store, lesson_id)["story"]["cards"] == _cards()


# --------------------------------------------------------------------------
# lesson_cards: the units the mint writes, in mint order
# --------------------------------------------------------------------------


class TestLessonCards:
    def test_order_is_the_lists_order_with_blanks_before_the_whole_sentence(self):
        """One unknown at a time: a sentence's blanks come before the whole sentence."""
        keys = [c.unit.disambig_key or c.unit.text for c in lesson_cards(_cards())]
        s2, e2 = blank_span("Hei, hvordan går det?", "går")
        t3 = blank_span("Bra, takk, og du?", "takk")
        d3 = blank_span("Bra, takk, og du?", "du")
        assert keys == [
            "takk",
            "sent:test-01:1",
            f"sent:test-01:2:{s2}-{e2}",
            "sent:test-01:2",
            f"sent:test-01:3:{t3[0]}-{t3[1]}",
            f"sent:test-01:3:{d3[0]}-{d3[1]}",
        ]

    def test_every_card_is_a_course_card(self):
        assert {c.unit.source for c in lesson_cards(_cards())} == {COURSE_SOURCE}
        assert COURSE_SOURCE == "course"

    def test_a_word_is_a_vocab_card(self):
        word = lesson_cards(_cards())[0]
        assert isinstance(word, LessonCard)
        u = word.unit
        assert (u.text, u.translation, u.lemma, u.card_type, u.word_count, u.disambig_key) == (
            "takk",
            "thanks",
            "takk",
            "vocab",
            1,
            "",
        )
        assert (word.role, word.cue_role) == ("", "")

    def test_a_words_lemma_when_given_is_its_lemma(self):
        cards = _cards()
        cards["items"][0]["lemma"] = "takke"
        assert lesson_cards(cards)[0].unit.lemma == "takke"

    def test_a_sentence_card_carries_itself(self):
        """The unit's card_data is the whole card; the set, not a lesson id, keys it."""
        card = lesson_cards(_cards())[3]  # line 2, whole
        assert card.unit.card_type == "cloze"
        assert (card.role, card.cue_role) == ("male-1", "female-1")
        sc = sentence_card(card.unit)
        assert isinstance(sc, SentenceCard)
        assert (sc.lesson_id, sc.line_index, sc.speaker, sc.text, sc.spoken, sc.english) == (
            "test-01",
            2,
            "Per",
            "Hei, hvordan går det?",
            "Hei, hvordan gar det?",
            "Hi, how are you?",
        )
        assert sc.blank is None
        assert [(g.word, g.gloss) for g in sc.glosses] == [("går", "goes")]
        assert sc.cue is not None
        assert (sc.cue.speaker, sc.cue.text, sc.cue.spoken, sc.cue.english, sc.cue.glosses) == (
            "Kari",
            "Hei!",
            "Hei!",
            "Hi!",
            (),
        )
        assert sc.picture == ""
        assert card.unit.translation == "Hi, how are you?"
        assert card.unit.source_line_index == 2

    def test_a_blank_card_carries_its_span_and_gloss(self):
        card = lesson_cards(_cards())[2]  # line 2, blank 'går'
        sc = sentence_card(card.unit)
        assert sc.blank == blank_span("Hei, hvordan går det?", "går")
        assert sc.blank_gloss == "_goes_ (å gå)"
        assert card.unit.source_sentence == "Hei, hvordan {{c1::går}} det?"

    def test_an_opener_has_its_picture_and_no_cue(self):
        card = lesson_cards(_cards())[1]
        sc = sentence_card(card.unit)
        assert (sc.picture, sc.cue, card.cue_role) == ("door.svg", None, "")

    def test_keys_do_not_depend_on_the_lesson_id(self):
        """A re-import mints a new lesson id; the cards' guids must not move with it."""
        a = [compute_guid(c.unit.text, "no", c.unit.disambig_key) for c in lesson_cards(_cards())]
        b = [compute_guid(c.unit.text, "no", c.unit.disambig_key) for c in lesson_cards(copy.deepcopy(_cards()))]
        assert a == b
        assert len(set(a)) == len(a)

    def test_builds_nothing_it_was_not_given(self):
        cards = _cards()
        cards["items"] = cards["items"][:1]
        assert len(lesson_cards(cards)) == 1
