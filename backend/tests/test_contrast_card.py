"""The contrast card: one root across an affix pattern, one form blank (tunatale-ve4p.6).

The card is a cloze over a small paradigm. The forms the learner is NOT asked
for are the model; the blank is the same root one step over.

Cebuano throughout, not the Norwegian new tests default to: an affix pattern is
a facet only the Cebuano plugin registers today, and Norwegian's bundle has
``patterns=()``. Norwegian appears below as the language that registers none.

The oracle is the language's own table (``pattern_forms`` / ``pattern_glosses``)
written out as literals, so a change to how a card is spelled turns a line red
here rather than agreeing with itself.
"""

from __future__ import annotations

import pytest

from app.languages import get_a1_morphology
from app.models.syntactic_unit import SyntacticUnit
from app.srs.contrast_card import ContrastCard, cloze_note_text, contrast_card, pair_key, paradigm
from app.srs.function_words import make_cloze_text, uncloze_text

_PATTERNS = {p.key: p for p in get_a1_morphology("ceb").patterns}
_MO_MI = _PATTERNS["mo-mi"]
_MAG_NAG = _PATTERNS["mag-nag"]


def _lakaw() -> ContrastCard:
    card = contrast_card("ceb", _MO_MI, "lakaw", 1)
    assert card is not None
    return card


def _uban_with_line() -> ContrastCard:
    card = contrast_card("ceb", _MO_MI, "uban", 0, line=("Mouban ko ugma.", "I will go along tomorrow."))
    assert card is not None
    return card


class TestTheCard:
    def test_asks_for_the_blanked_form_by_its_own_english(self):
        card = _lakaw()
        assert (card.form, card.prompt) == ("milakaw", "walked")
        assert card.cells == (("molakaw", "will walk"), ("milakaw", "walked"))
        assert (card.root, card.english) == ("lakaw", "walk")

    def test_the_other_cell_can_be_the_blank(self):
        card = contrast_card("ceb", _MO_MI, "lakaw", 0)
        assert (card.form, card.prompt) == ("molakaw", "will walk")

    def test_a_root_the_language_cannot_vouch_for_gets_no_card(self):
        # kaon is a real root; native news does not use both mag-/nag- forms.
        assert contrast_card("ceb", _MAG_NAG, "kaon", 0) is None

    def test_a_language_with_no_patterns_gets_no_card(self):
        assert contrast_card("no", _MO_MI, "lakaw", 0) is None

    def test_a_line_that_does_not_say_the_form_is_refused(self):
        with pytest.raises(ValueError, match="mouban"):
            contrast_card("ceb", _MO_MI, "uban", 0, line=("Oo, moadto ko.", "Yes, I will go."))


class TestTheUnit:
    """What is stored in TT. It has to be the shape ``sync_create_new`` expects of a cloze."""

    def test_is_a_production_only_cloze_keyed_apart_from_a_sentence_cloze(self):
        unit = _lakaw().unit()
        assert unit.card_type == "cloze"
        assert (unit.text, unit.translation, unit.lemma) == ("milakaw", "walked", "lakaw")
        # 'morph:%' keeps it out of the mastery grouping; 'pair-' keeps it from
        # colliding with an Inflect cloze on the same surface (morph:verb-mi)
        # under UNIQUE(text, disambig_key).
        assert unit.disambig_key == "morph:pair-verb-mi" == pair_key("verb:mi")
        assert unit.grammar == "lakaw · walk"

    def test_the_two_cards_of_a_root_have_different_keys_and_texts(self):
        done, not_yet = _lakaw().unit(), contrast_card("ceb", _MO_MI, "lakaw", 0).unit()
        assert (not_yet.text, not_yet.disambig_key) == ("molakaw", "morph:pair-verb-mo")
        assert (done.text, done.disambig_key) != (not_yet.text, not_yet.disambig_key)

    def test_with_no_line_the_sentence_is_the_form_alone(self):
        unit = _lakaw().unit()
        assert unit.source_sentence == "{{c1::milakaw}}"
        assert unit.source_sentence_translation == ""

    def test_with_a_line_the_sentence_is_the_line_with_the_form_blank(self):
        unit = _uban_with_line().unit()
        assert unit.source_sentence == "{{c1::Mouban}} ko ugma."
        assert unit.source_sentence_translation == "I will go along tomorrow."

    @pytest.mark.parametrize(
        ("card", "spoken"),
        [(_lakaw, "milakaw"), (_uban_with_line, "Mouban ko ugma.")],
    )
    def test_what_the_audio_paths_would_say_is_speech_not_the_grid(self, card, spoken):
        # sync's mint and backfill_cloze_tts both voice uncloze_text(source_sentence).
        # The grid is never in that field, so neither can be handed a table to read.
        assert uncloze_text(card().unit().source_sentence) == spoken

    def test_a_hyphenated_form_is_blanked_whole(self):
        card = contrast_card("ceb", _MAG_NAG, "ampo", 1, line=("Nag-ampo sila sa simbahan.", "They prayed at church."))
        assert card.unit().source_sentence == "{{c1::Nag-ampo}} sila sa simbahan."
        assert card.unit().disambig_key == "morph:pair-verb-nag"


class TestParadigm:
    """Reading the card back from what was stored: the root and the key are enough."""

    def test_a_stored_unit_reads_back_as_the_same_card(self):
        card = _lakaw()
        assert paradigm("ceb", card.unit()) == card

    def test_the_line_comes_back_too(self):
        card = _uban_with_line()
        assert paradigm("ceb", card.unit()) == card

    @pytest.mark.parametrize("key", ["", "verb", "morph:verb-mi"])
    def test_any_other_cloze_is_not_a_contrast_card(self, key):
        unit = SyntacticUnit(
            text="milakaw",
            translation="walked",
            word_count=1,
            difficulty=1,
            source="test",
            lemma="lakaw",
            disambig_key=key,
            card_type="cloze",
            source_sentence="{{c1::Milakaw}} siya.",
        )
        assert paradigm("ceb", unit) is None

    def test_a_form_the_table_no_longer_spells_that_way_is_not_drawn_as_a_grid(self):
        # The table is read live. A stored card it stops vouching for falls
        # back to being an ordinary cloze instead of showing a wrong model.
        unit = SyntacticUnit(
            text="milakau",
            translation="walked",
            word_count=1,
            difficulty=1,
            source="test",
            lemma="lakaw",
            disambig_key="morph:pair-verb-mi",
            card_type="cloze",
            source_sentence="{{c1::milakau}}",
        )
        assert paradigm("ceb", unit) is None

    def test_a_language_with_no_patterns_has_no_paradigms(self):
        assert paradigm("no", _lakaw().unit()) is None


def _front(unit: SyntacticUnit) -> str:
    """Field 0 as the mint writes it: the stored sentence, blank marked, handed to the one function."""
    return cloze_note_text("ceb", unit, make_cloze_text(unit.text, unit.source_sentence))


def _sa(sentence: str) -> SyntacticUnit:
    return SyntacticUnit(
        text="sa",
        translation="to",
        word_count=1,
        difficulty=1,
        source="test",
        card_type="cloze",
        source_sentence=sentence,
    )


class TestClozeNoteText:
    """Field 0 of the Anki note. One function, so the mint and an edit-push cannot disagree."""

    def test_the_grid_shows_the_model_and_blanks_the_form(self):
        assert _front(_lakaw().unit()) == "lakaw · walk<br>will walk: molakaw<br>walked: {{c1::milakaw}}"

    def test_a_line_follows_the_grid_with_the_same_blank(self):
        assert _front(_uban_with_line().unit()) == (
            "uban · accompany<br>will go along: {{c1::mouban}}<br>went along: miuban<br><br>{{c1::Mouban}} ko ugma."
        )

    def test_every_blank_is_the_one_cloze_so_the_note_makes_one_card(self):
        text = _front(_uban_with_line().unit())
        assert text.count("{{c1::") == 2
        assert "{{c2::" not in text

    def test_a_rewritten_line_goes_under_the_same_grid(self):
        unit = _uban_with_line().unit()
        assert cloze_note_text("ceb", unit, "{{c1::Mouban}} sila karon.") == (
            "uban · accompany<br>will go along: {{c1::mouban}}<br>went along: miuban<br><br>{{c1::Mouban}} sila karon."
        )

    @pytest.mark.parametrize("sentence", ["Moadto ko {{c1::sa}} merkado.", "Moadto ko sa merkado.", ""])
    def test_any_other_cloze_gets_its_sentence_back_untouched(self, sentence):
        # Unmarked and empty included: this function adds a grid and does
        # nothing else, so it cannot become a second place that marks blanks.
        assert cloze_note_text("ceb", _sa(sentence), sentence) == sentence
