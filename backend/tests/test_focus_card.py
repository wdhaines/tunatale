"""The focus card: one cell of an object-focus root, said as a whole sentence (tunatale-ve4p.9).

Stage 6 adds a second choice to the affix: which noun is the ``ang`` noun. A
lone verb cannot ask for it ("ate" is both ``mikaon`` and ``gikaon``), so the
card is a sentence. Its front is the English with the focus in bold (D3: the
narrator stresses the same words); the learner says the whole Cebuano
sentence; the back bolds the verb and the focus.

Each root gets four cards, one per cell, in the order gi-, mi-, -on, mo-: the
first two already test the choice, one object focus and one actor focus. The
k-th card takes ``objects[k % n]`` and ``DOERS[k % 2]`` from the checked row.

Cebuano throughout: an affix pattern is a facet only its plugin registers.
The oracle is ``ceb/data/focus_rows.tsv`` written out as literals.

Design: .beads-tasks/briefs/design-ve4p-stage6-object-focus-2026-10.md § 6.
"""

from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from app.languages import get_a1_morphology
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.languages.ceb.affix_patterns import pattern_cards
from app.srs.contrast_card import cloze_note_text, contrast_card, paradigm
from app.srs.focus_card import FocusCard, focus, focus_cards, focus_key

_PATTERNS = {p.key: p for p in get_a1_morphology("ceb").patterns}
_ON_GI = _PATTERNS["on-gi"]
_MO_MI = _PATTERNS["mo-mi"]

# (feature, sentence, English, the bold of the English, the bold of the sentence)
_INOM = [
    ("verb:gi", "Giinom ko ang tubig.", "I drank the water.", ["the water"], ["Giinom", "ang tubig"]),
    ("verb:mi", "Miinom siya og kape.", "He drank coffee.", ["He"], ["Miinom", "siya"]),
    ("verb:on", "Imnon ko ang gatas.", "I'll drink the milk.", ["the milk"], ["Imnon", "ang gatas"]),
    ("verb:mo", "Moinom siya og tubig.", "He'll drink water.", ["He'll"], ["Moinom", "siya"]),
]
_TAN_AW = [
    ("verb:gi", "Gitan-aw ko ang balay.", "I looked at the house."),
    ("verb:mi", "Mitan-aw siya og sakyanan.", "He looked at a vehicle."),
    ("verb:on", "Tan-awon ko ang balay.", "I'll look at the house."),
    ("verb:mo", "Motan-aw siya og sakyanan.", "He'll look at a vehicle."),
]


def _bold(text: str, spans: tuple[tuple[int, int], ...]) -> list[str]:
    return [text[start:end] for start, end in spans]


class TestTheCards:
    def test_a_root_has_four_cards_object_and_actor_focus_alternating(self):
        cards = focus_cards("ceb", _ON_GI, "inom")
        assert [
            (c.feature, c.text, c.english, _bold(c.english, (c.stressed,)), _bold(c.text, c.bold)) for c in cards
        ] == [(f, t, e, se, sb) for f, t, e, se, sb in _INOM]

    def test_the_kth_card_takes_the_kth_object_round_the_list(self):
        """tan-aw has two objects, so its third card is back on the first."""
        assert [(c.feature, c.text, c.english) for c in focus_cards("ceb", _ON_GI, "tan-aw")] == _TAN_AW

    def test_every_card_knows_its_root(self):
        card = focus_cards("ceb", _ON_GI, "kaon")[0]
        assert (card.root, card.root_english, card.text) == ("kaon", "eat", "Gikaon ko ang pan.")

    def test_the_cells_order_is_the_patterns_own(self):
        assert _ON_GI.card_features == ("verb:gi", "verb:mi", "verb:on", "verb:mo")

    @pytest.mark.parametrize(
        ("language", "pattern", "root"),
        [
            ("ceb", _ON_GI, "hulat"),  # no checked row: its actor forms are mag-/nag- only
            ("ceb", _MO_MI, "lakaw"),  # a word pattern: its card is the contrast card
            ("no", _ON_GI, "inom"),  # a language with no patterns
        ],
    )
    def test_no_cards_where_nobody_checked_the_sentences(self, language, pattern, root):
        assert focus_cards(language, pattern, root) == ()

    @pytest.mark.parametrize(("pattern", "root"), [(_ON_GI, "hulat"), (_MO_MI, "inom")])
    def test_the_languages_own_hook_has_none_either(self, pattern, root):
        """``pattern_cards`` guards itself, not only behind ``focus_cards``: it is the plugin's contract."""
        assert pattern_cards(root, pattern) == ()

    def test_the_key_is_the_cells_feature(self):
        assert [focus_key(c.feature) for c in focus_cards("ceb", _ON_GI, "inom")] == [
            "morph:focus-verb-gi",
            "morph:focus-verb-mi",
            "morph:focus-verb-on",
            "morph:focus-verb-mo",
        ]

    def test_an_actor_focus_card_is_not_keyed_like_the_mo_mi_contrast_card(self):
        """mo-mi's gate counts ``morph:pair-verb-mi``; an actor focus card must not land there."""
        mi = focus_cards("ceb", _ON_GI, "inom")[1]
        assert mi.feature == "verb:mi"
        assert mi.key != contrast_card("ceb", _MO_MI, "inom", 1).unit().disambig_key


class TestTheStoredCard:
    def test_is_a_production_cloze_on_the_whole_sentence(self):
        unit = focus_cards("ceb", _ON_GI, "kaon")[0].unit()
        assert {
            key: value
            for key, value in asdict(unit).items()
            if key
            in {
                "text",
                "translation",
                "word_count",
                "lemma",
                "disambig_key",
                "card_type",
                "source_sentence",
                "source_sentence_translation",
                "grammar",
            }
        } == {
            "text": "Gikaon ko ang pan.",
            "translation": "I ate the bread.",
            "word_count": 4,
            "lemma": "kaon",
            "disambig_key": "morph:focus-verb-gi",
            "card_type": "cloze",
            "source_sentence": "{{c1::Gikaon ko ang pan.}}",
            # The English is the front; a second copy on the back would only repeat it.
            "source_sentence_translation": "",
            "grammar": "kaon · eat",
        }

    def test_no_bold_is_stored(self):
        """The bold is derived from the table when served or written to Anki, like the contrast grid."""
        unit = focus_cards("ceb", _ON_GI, "inom")[0].unit()
        assert not any("<b>" in str(value) for value in asdict(unit).values())

    def test_four_cards_of_one_root_are_four_rows(self):
        units = [card.unit() for card in focus_cards("ceb", _ON_GI, "dala")]
        assert len({(u.text, u.disambig_key) for u in units}) == 4


class TestReadBack:
    @pytest.mark.parametrize("k", range(4))
    def test_a_stored_card_comes_back_as_itself(self, k):
        card = focus_cards("ceb", _ON_GI, "inom")[k]
        assert focus("ceb", card.unit()) == card

    def test_a_sentence_the_table_no_longer_makes_comes_back_as_a_plain_cloze(self):
        unit = replace(focus_cards("ceb", _ON_GI, "inom")[0].unit(), text="Giinom ko ang beer.")
        assert focus("ceb", unit) is None

    def test_a_card_under_another_cells_key_is_not_that_card(self):
        unit = replace(focus_cards("ceb", _ON_GI, "inom")[0].unit(), disambig_key=focus_key("verb:on"))
        assert focus("ceb", unit) is None

    @pytest.mark.parametrize("key", ["", "morph:pair-verb-gi", "morph:verb:gi"])
    def test_any_other_cloze_is_not_a_focus_card(self, key):
        unit = SyntacticUnit(
            text="Giinom ko ang tubig.",
            translation="I drank the water.",
            word_count=4,
            difficulty=1,
            source="test",
            lemma="inom",
            disambig_key=key,
            card_type="cloze",
        )
        assert focus("ceb", unit) is None

    def test_a_focus_card_is_not_a_contrast_card(self):
        assert paradigm("ceb", focus_cards("ceb", _ON_GI, "inom")[0].unit()) is None

    def test_a_card_with_no_root_is_not_a_focus_card(self):
        unit = replace(focus_cards("ceb", _ON_GI, "inom")[0].unit(), lemma=None)
        assert focus("ceb", unit) is None

    def test_a_language_with_no_patterns_reads_nothing_back(self):
        assert focus("no", focus_cards("ceb", _ON_GI, "inom")[0].unit()) is None


class TestTheAnkiFront:
    """Field 0 of the Cloze note: the English with its focus in bold, then the sentence as the blank."""

    def test_object_focus(self):
        unit = focus_cards("ceb", _ON_GI, "inom")[0].unit()
        assert cloze_note_text("ceb", unit, unit.source_sentence) == (
            "I drank <b>the water</b>.<br><br>{{c1::<b>Giinom</b> ko <b>ang tubig</b>.}}"
        )

    def test_actor_focus(self):
        unit = focus_cards("ceb", _ON_GI, "inom")[3].unit()
        assert cloze_note_text("ceb", unit, unit.source_sentence) == (
            "<b>He'll</b> drink water.<br><br>{{c1::<b>Moinom</b> <b>siya</b> og tubig.}}"
        )

    def test_a_sentence_the_table_no_longer_makes_is_written_as_stored(self):
        unit = replace(focus_cards("ceb", _ON_GI, "inom")[0].unit(), text="Giinom ko ang beer.")
        assert cloze_note_text("ceb", unit, "{{c1::Giinom ko ang beer.}}") == "{{c1::Giinom ko ang beer.}}"


class TestTheReviewScreen:
    def test_the_prompt_and_the_answer_as_runs_of_plain_and_bold(self):
        card = focus_cards("ceb", _ON_GI, "inom")[0]
        assert card.prompt_parts() == [("I drank ", False), ("the water", True), (".", False)]
        assert card.answer_parts() == [("Giinom", True), (" ko ", False), ("ang tubig", True), (".", False)]

    def test_adjacent_bold_runs_keep_the_space_between_them_plain(self):
        card = focus_cards("ceb", _ON_GI, "inom")[1]
        assert card.prompt_parts() == [("He", True), (" drank coffee.", False)]
        assert card.answer_parts() == [("Miinom", True), (" ", False), ("siya", True), (" og kape.", False)]


def test_a_focus_card_is_frozen():
    card = focus_cards("ceb", _ON_GI, "inom")[0]
    assert isinstance(card, FocusCard)
    with pytest.raises(AttributeError):
        card.text = "x"  # type: ignore[misc]
