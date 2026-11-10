"""One TT Sentence note per lesson line: its blanks and its whole-line card as siblings (tunatale-h0q3.55).

The design (``.beads-tasks/briefs/design-sentence-notetype-siblings-2026-11.md``):
a line's cards share ONE Anki note of the "TT Sentence" notetype, so Anki buries
them as siblings. The whole line is always cloze 1, wrapping the blanks (nested
clozes; measured against the anki engine 2026-11-09), and the blanks take 2, 3, …
in the order the lesson lists them. Each blank's gloss sits in ``BlankGlosses``
tagged with its cloze number, so the card's CSS shows only the active one.

The fixture is Wolff 1, line 4, as it is live in the owner's deck.
"""

from __future__ import annotations

import pytest

from app.cards.sentence_notetype import FIELD_NAMES
from app.srs.sentence_card import Cue, Gloss, SentenceCard, blank_span
from app.srs.sentence_note import WHOLE_ORD, cloze_numbers_in, cloze_ords, sentence_note_fields, source_label

_BASE = SentenceCard(
    lesson_id="wolff-1",
    line_index=4,
    speaker="Ínting",
    text="Nâa ba si Místir Abáya?",
    spoken="Nâa ba si Místir Abáya?",
    english="Is Mr. Abaya there?",
    glosses=(
        Gloss("nâa", "is there, are there"),
        Gloss("ba", "question particle"),
        Gloss("si", "particle preceding names"),
    ),
    cue=Cue(
        speaker="Inday",
        text="Únsa may átò?",
        spoken="Únsa may átò?",
        english="What can I do for you? (Lit. What is ours?)",
        glosses=(Gloss("únsa", "what?"),),
    ),
)


def _blank(form: str, gloss: str) -> SentenceCard:
    return _BASE.with_blank(blank_span(_BASE.text, form), gloss)


NAA = _blank("nâa", "there (near you), not nîa")
BA = _blank("ba", "yes/no question particle")
SI = _blank("si", "particle preceding names")
WHOLE = _BASE
LINE = [NAA, BA, _BASE]  # the lesson's order: blanks first, the whole line last


def _fields(cards=LINE, **kw) -> dict[str, str]:
    kw.setdefault("cue_audio", "take_cue.mp3")
    kw.setdefault("answer_audio", "take_answer.mp3")
    kw.setdefault("source", "Wolff 1 · line 4")
    return dict(zip(FIELD_NAMES, sentence_note_fields(cards, **kw), strict=True))


class TestClozeOrds:
    def test_whole_line_is_cloze_one_blanks_follow_in_lesson_order(self):
        assert cloze_ords(LINE) == {_BASE.key: WHOLE_ORD, NAA.key: 2, BA.key: 3}
        assert WHOLE_ORD == 1

    def test_without_a_whole_card_cloze_one_is_unused(self):
        assert cloze_ords([NAA, BA]) == {NAA.key: 2, BA.key: 3}

    def test_cards_of_two_lines_are_refused(self):
        other = SentenceCard(**{**_BASE.__dict__, "line_index": 5})
        with pytest.raises(ValueError, match="one line"):
            cloze_ords([NAA, other])

    def test_the_same_card_twice_is_refused(self):
        with pytest.raises(ValueError, match="twice"):
            cloze_ords([NAA, NAA])

    def test_numbers_already_taken_are_kept_and_a_new_blank_takes_the_next(self):
        """A note's minted cards keep their cloze numbers (a card's ord is its number - 1);
        a blank joining later never renumbers them."""
        assert cloze_ords([NAA, BA, _BASE], taken={_BASE.key: 1, BA.key: 2}) == {
            _BASE.key: 1,
            BA.key: 2,
            NAA.key: 3,
        }

    def test_a_number_freed_by_a_deleted_card_is_not_reused_below_the_highest(self):
        assert cloze_ords([NAA, BA], taken={BA.key: 3}) == {BA.key: 3, NAA.key: 4}

    def test_a_taken_number_used_twice_is_refused(self):
        with pytest.raises(ValueError, match="cloze 2"):
            cloze_ords([NAA, BA], taken={NAA.key: 2, BA.key: 2})

    def test_fields_follow_the_given_numbers(self):
        ords = {_BASE.key: 1, BA.key: 2, NAA.key: 3}
        assert _fields(ords=ords)["Text"] == "{{c1::{{c3::Nâa}} {{c2::ba}} si Místir Abáya?}}"


class TestClozeOrdsAbove:
    """A new blank's number clears every card the note still has (tunatale-h0q3.60)."""

    def test_a_new_blank_goes_above_a_card_no_row_claims(self):
        """A row deleted in TT and not pushed yet: its card is still on the note. The next
        number above the rows' own would be that card's, handing it to another blank."""
        assert cloze_ords([NAA, WHOLE, SI], {WHOLE.key: 1, NAA.key: 2}, above=3) == {
            WHOLE.key: 1,
            NAA.key: 2,
            SI.key: 4,
        }

    def test_above_nothing_changes_nothing(self):
        assert cloze_ords([NAA, WHOLE], above=0) == cloze_ords([NAA, WHOLE])


class TestClozeNumbersIn:
    """Reading a note's own numbering back out of its Text field (tunatale-h0q3.60).

    The inverse of the Text ``sentence_note_fields`` writes: which cloze number each
    blank span carries, so a row with no card of its own takes the number, and the
    card, its blank already has.
    """

    def _text(self, cards, ords=None):
        return sentence_note_fields(cards, cue_audio=None, answer_audio=None, source="", ords=ords)[0]

    def test_whole_line_and_blanks(self):
        assert cloze_numbers_in(self._text([NAA, BA, WHOLE]), _BASE.text) == {None: 1, NAA.blank: 2, BA.blank: 3}

    def test_numbers_with_a_gap_come_back_as_written(self):
        text = self._text([BA, WHOLE], {WHOLE.key: 1, BA.key: 3})
        assert cloze_numbers_in(text, _BASE.text) == {None: 1, BA.blank: 3}

    def test_no_whole_card(self):
        assert cloze_numbers_in(self._text([NAA, BA]), _BASE.text) == {NAA.blank: 2, BA.blank: 3}

    def test_whole_card_alone(self):
        assert cloze_numbers_in(self._text([WHOLE]), _BASE.text) == {None: 1}

    def test_escaped_text_maps_back_to_the_lines_own_offsets(self):
        line = SentenceCard(
            lesson_id="x", line_index=1, speaker="A", text="R&D <b> ba?", spoken="x", english="x", glosses=(), cue=None
        )
        ba = line.with_blank(blank_span(line.text, "ba"), "particle")
        assert cloze_numbers_in(self._text([ba, line]), line.text) == {None: 1, ba.blank: 2}

    def test_a_field_that_is_not_this_line_is_unreadable(self):
        """Edited in Anki, or the line's text has changed since: the spans mean nothing."""
        assert cloze_numbers_in(self._text([NAA, WHOLE]), "Nâa ba si Mísis Abáya?") is None
        assert cloze_numbers_in("x", _BASE.text) is None

    def test_a_number_written_twice_is_unreadable(self):
        assert cloze_numbers_in("{{c2::Nâa}} {{c2::ba}} si Místir Abáya?", _BASE.text) is None


class TestSourceLabel:
    def test_the_set_slug_reads_as_a_title(self):
        assert source_label(NAA) == "Wolff 1 · line 4"
        assert source_label(SentenceCard(**{**NAA.__dict__, "lesson_id": "wolff-2a"})) == "Wolff 2a · line 4"


class TestText:
    def test_blanks_nest_inside_the_whole_line(self):
        assert _fields()["Text"] == "{{c1::{{c2::Nâa}} {{c3::ba}} si Místir Abáya?}}"

    def test_no_whole_card_no_outer_cloze(self):
        assert _fields([NAA, BA])["Text"] == "{{c2::Nâa}} {{c3::ba}} si Místir Abáya?"

    def test_whole_card_alone(self):
        assert _fields([_BASE])["Text"] == "{{c1::Nâa ba si Místir Abáya?}}"

    def test_a_suffix_blank_stays_attached(self):
        base = SentenceCard(**{**_BASE.__dict__, "text": "Màáyong búntag."})
        ng = base.with_blank(blank_span(base.text, "-ng"), "linker")
        assert _fields([ng, base])["Text"] == "{{c1::Màáyo{{c2::ng}} búntag.}}"

    def test_text_is_escaped(self):
        base = SentenceCard(**{**_BASE.__dict__, "text": "A <b> & c"})
        assert _fields([base])["Text"] == "{{c1::A &lt;b&gt; &amp; c}}"

    def test_overlapping_blanks_are_refused(self):
        na_lang = _BASE.with_blank((0, 6), "x")
        with pytest.raises(ValueError, match="overlap"):
            sentence_note_fields([NAA, na_lang], cue_audio="", answer_audio="", source="")

    def test_cloze_syntax_in_the_text_is_refused(self):
        base = SentenceCard(**{**_BASE.__dict__, "text": "a :: b"})
        with pytest.raises(ValueError, match="cloze syntax"):
            sentence_note_fields([base], cue_audio="", answer_audio="", source="")


class TestFields:
    def test_speakers_cue_audio_and_english(self):
        f = _fields()
        assert f["Speaker"] == "Ínting"
        assert f["CueSpeaker"] == "Inday"
        assert f["Cue"] == "Únsa may átò?"
        assert f["CueAudio"] == "[sound:take_cue.mp3]"
        assert f["AnswerAudio"] == "[sound:take_answer.mp3]"
        assert f["English"] == "Is Mr. Abaya there?"
        assert f["Source"] == "Wolff 1 · line 4"

    def test_each_blank_gloss_is_tagged_with_its_cloze_number(self):
        assert _fields()["BlankGlosses"] == (
            '<div class="tt-bg" data-c="2"><b>Nâa</b> there (near you), not nîa</div>'
            '<div class="tt-bg" data-c="3"><b>ba</b> yes/no question particle</div>'
        )

    def test_word_by_word_rows(self):
        assert _fields()["Glosses"] == (
            '<div class="tt-w">nâa</div><div class="tt-g">is there, are there</div>'
            '<div class="tt-w">ba</div><div class="tt-g">question particle</div>'
            '<div class="tt-w">si</div><div class="tt-g">particle preceding names</div>'
        )

    def test_cue_means_holds_its_glosses_then_its_english(self):
        assert _fields()["CueMeans"] == (
            '<div class="tt-glosses"><div class="tt-w">únsa</div><div class="tt-g">what?</div></div>'
            '<div class="tt-en">What can I do for you? (Lit. What is ours?)</div>'
        )

    def test_wolff_italics_markup_in_glosses(self):
        base = SentenceCard(**{**_BASE.__dict__, "glosses": (Gloss("ba", "a _yes/no_ particle"),)})
        assert (
            _fields([base])["Glosses"] == '<div class="tt-w">ba</div><div class="tt-g">a <i>yes/no</i> particle</div>'
        )

    def test_an_opener_has_a_picture_and_no_cue(self):
        base = SentenceCard(**{**_BASE.__dict__, "cue": None, "picture": "opener_door.svg"})
        f = _fields([base], cue_audio=None)
        assert f["Picture"] == '<img src="opener_door.svg">'
        assert (f["Cue"], f["CueSpeaker"], f["CueAudio"], f["CueMeans"]) == ("", "", "", "")

    def test_missing_audio_leaves_the_field_empty(self):
        f = _fields(cue_audio=None, answer_audio=None)
        assert (f["CueAudio"], f["AnswerAudio"]) == ("", "")

    def test_a_line_without_glosses_has_empty_glosses(self):
        base = SentenceCard(**{**_BASE.__dict__, "glosses": ()})
        assert _fields([base])["Glosses"] == ""
