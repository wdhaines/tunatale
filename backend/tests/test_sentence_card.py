"""The sentence card (tunatale-h0q3.42): a lesson line said back, cued by the line before it.

Invented sentences only: the course text never enters the public repo.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.common.guid import compute_guid
from app.models.srs_item import Direction, DirectionState
from app.srs.sentence_card import (
    SENTENCE_KEY_PREFIX,
    Cue,
    Gloss,
    SentenceCard,
    blank_span,
    is_sentence_key,
    sentence_card,
)

CUE = Cue(
    speaker="Kari",
    text="Hvor bor du?",
    spoken="Hvor bor du?",
    english="Where do you live?",
    glosses=(Gloss("hvor", "where?"), Gloss("bo", "live")),
)
CARD = SentenceCard(
    lesson_id="a-visit-1234",
    line_index=3,
    speaker="Per",
    text="Jeg bor i Oslo.",
    spoken="Jeg bor i Oslo.",
    english="I live in Oslo.",
    glosses=(Gloss("jeg", "I"), Gloss("bor", "live(s)"), Gloss("i", "[preposition: in]")),
    cue=CUE,
)


class TestKey:
    def test_whole_sentence_key_names_the_lesson_line(self):
        assert CARD.key == "sent:a-visit-1234:3"

    def test_blank_key_adds_the_span(self):
        card = CARD.with_blank(blank_span(CARD.text, "bor"), "live(s)")
        assert card.key == "sent:a-visit-1234:3:4-7"

    def test_prefix_is_language_neutral_and_recognized(self):
        assert SENTENCE_KEY_PREFIX == "sent:"
        assert is_sentence_key(CARD.key)
        assert not is_sentence_key("morph:focus-verb-mi")
        assert not is_sentence_key("")


class TestBlankSpan:
    def test_whole_word_case_insensitive(self):
        assert blank_span("Jeg bor i Oslo.", "jeg") == (0, 3)

    def test_word_not_inside_another_word(self):
        # 'i' must not match the i in 'Oslo'-like words; first whole-word hit wins
        assert blank_span("Vi bor i Oslo.", "i") == (7, 8)

    def test_suffix_attaches_to_its_stem(self):
        # '-ng' blanks the linker joined to the word before: 'Godtng dag' -> 'Godt{{c1::ng}}'
        assert blank_span("Godtng dag.", "-ng") == (4, 6)

    def test_suffix_only_at_word_end(self):
        assert blank_span("Ingen ting.", "-ng") == (8, 10)

    def test_accents_are_part_of_the_word(self):
        assert blank_span("Ádto na kó.", "kó") == (8, 10)

    def test_missing_suffix_raises(self):
        with pytest.raises(ValueError, match="not in"):
            blank_span("Hei der.", "-ng")

    def test_missing_blank_raises(self):
        with pytest.raises(ValueError, match="not in"):
            blank_span("Jeg bor i Oslo.", "Bergen")


class TestCloze:
    def test_whole_sentence_is_the_blank(self):
        assert CARD.cloze() == "{{c1::Jeg bor i Oslo.}}"

    def test_one_word_blank(self):
        card = CARD.with_blank(blank_span(CARD.text, "bor"), "live(s)")
        assert card.cloze() == "Jeg {{c1::bor}} i Oslo."

    def test_suffix_blank_stays_attached(self):
        card = SentenceCard("l", 0, "Per", "Godtng dag.", "Godtng dag.", "Good day.", ())
        card = card.with_blank(blank_span(card.text, "-ng"), "linker")
        assert card.cloze() == "Godt{{c1::ng}} dag."


class TestNoteText:
    """Field 0 of the Anki Cloze note: the cue (Cebuano + its audio), then who answers, then the blank."""

    def test_reply_card_front(self):
        text = CARD.note_text(cue_audio="cue_1.mp3")
        assert text == (
            '<div class="tt-cue"><span class="tt-who">Kari</span> Hvor bor du?</div>[sound:cue_1.mp3]'
            '<div class="tt-say"><span class="tt-who">Per:</span> {{c1::Jeg bor i Oslo.}}</div>'
        )

    def test_no_english_on_the_front(self):
        text = CARD.note_text(cue_audio="cue_1.mp3")
        assert CARD.english not in text
        assert CUE.english not in text
        assert "live" not in text  # no gloss either

    def test_cue_without_audio_has_no_sound_tag(self):
        assert "[sound:" not in CARD.note_text(cue_audio=None)

    def test_opener_without_cue_or_picture_is_speaker_and_blank(self):
        card = SentenceCard("l", 0, "Per", "Hei.", "Hei.", "Hello.", ())
        assert card.note_text() == '<div class="tt-say"><span class="tt-who">Per:</span> {{c1::Hei.}}</div>'

    def test_opener_picture_replaces_the_cue(self):
        card = SentenceCard("l", 0, "Per", "Hei.", "Hei.", "Hello.", (), picture="door.svg")
        assert card.note_text().startswith('<div class="tt-pic"><img src="door.svg"></div>')

    def test_a_blank_card_clozes_only_its_blank(self):
        card = CARD.with_blank(blank_span(CARD.text, "bor"), "live(s)")
        assert card.note_text().endswith('<span class="tt-who">Per:</span> Jeg {{c1::bor}} i Oslo.</div>')

    def test_text_is_escaped(self):
        card = SentenceCard("l", 0, "Per <b>", "A & B.", "A & B.", "x", ())
        text = card.note_text()
        assert "Per &lt;b&gt;:" in text
        assert "{{c1::A &amp; B.}}" in text


class TestBackExtra:
    def test_english_always_visible_glosses_and_cue_closed(self):
        back = CARD.back_extra(answer_audio="ans_1.mp3")
        assert back.index("[sound:ans_1.mp3]") < back.index('<div class="tt-en">I live in Oslo.</div>')
        words = back[back.index('<details class="tt-words">') :]
        assert words.startswith('<details class="tt-words"><summary>Word by word</summary>')
        assert "<b>bor</b> live(s)" in words
        means = back[back.index('<details class="tt-cue-means">') :]
        assert "<b>hvor</b> where?" in means
        assert '<div class="tt-en">Where do you live?</div>' in means
        # neither disclosure is open by default
        assert "<details open" not in back

    def test_blank_card_calls_out_the_blanked_words_gloss_first(self):
        card = CARD.with_blank(blank_span(CARD.text, "bor"), "live(s)")
        back = card.back_extra(answer_audio=None)
        assert back.startswith('<div class="tt-blank"><b>bor</b> = live(s)</div>')
        assert "[sound:" not in back

    def test_no_glosses_no_word_by_word(self):
        """Wolff's line 1 has no build-up rows: an empty disclosure opens onto nothing (owner, 2026-11-06)."""
        card = SentenceCard("l", 0, "Per", "Hei.", "Hei.", "Hello.", ())
        back = card.back_extra()
        assert "tt-words" not in back
        assert '<div class="tt-en">Hello.</div>' in back

    def test_a_cue_without_glosses_still_says_what_it_means(self):
        """The cue's disclosure always has its English, so it stays."""
        card = SentenceCard("l", 1, "Per", "Ja.", "Ja.", "Yes.", (), cue=Cue("Kari", "Hei.", "Hei.", "Hello."))
        means = card.back_extra()[card.back_extra().index('<details class="tt-cue-means">') :]
        assert '<div class="tt-en">Hello.</div>' in means

    def test_no_cue_no_cue_disclosure(self):
        card = SentenceCard("l", 0, "Per", "Hei.", "Hei.", "Hello.", (Gloss("hei", "hi"),))
        assert "tt-cue-means" not in card.back_extra()

    def test_underscore_markup_is_italic(self):
        card = SentenceCard("l", 0, "Per", "Hei.", "Hei.", "Hello.", (Gloss("nga", "[linker between _a_ and _b_]"),))
        assert "[linker between <i>a</i> and <i>b</i>]" in card.back_extra()

    def test_gloss_text_is_escaped(self):
        card = SentenceCard("l", 0, "Per", "Hei.", "Hei.", "Hello.", (Gloss("x", "a < b"),))
        assert "a &lt; b" in card.back_extra()


class TestJson:
    def test_round_trip(self):
        card = CARD.with_blank(blank_span(CARD.text, "bor"), "live(s)")
        assert SentenceCard.from_json(card.to_json()) == card

    def test_round_trip_without_cue(self):
        card = SentenceCard("l", 0, "Per", "Hei.", "Hei.", "Hello.", (), picture="door.svg")
        assert SentenceCard.from_json(card.to_json()) == card

    @pytest.mark.parametrize("raw", ["", None, "not json", "[]", '{"text": "x"}'])
    def test_bad_data_reads_as_none(self, raw):
        assert SentenceCard.from_json(raw) is None


class TestUnit:
    def test_unit_is_a_production_cloze_carrying_the_card(self):
        card = CARD.with_blank(blank_span(CARD.text, "bor"), "live(s)")
        unit = card.unit()
        assert unit.card_type == "cloze"
        assert unit.text == "Jeg bor i Oslo."
        assert unit.translation == "I live in Oslo."
        assert unit.source_sentence == "Jeg {{c1::bor}} i Oslo."
        assert unit.disambig_key == "sent:a-visit-1234:3:4-7"
        assert unit.source_lesson_id == "a-visit-1234"
        assert unit.source_line_index == 3
        assert unit.word_count == 4
        assert SentenceCard.from_json(unit.card_data) == card

    def test_sentence_card_reads_a_unit_back(self):
        unit = CARD.unit()
        assert sentence_card(unit) == CARD

    def test_other_cards_are_not_sentence_cards(self):
        unit = CARD.unit()
        unit.disambig_key = "morph:focus-x"
        assert sentence_card(unit) is None


class TestStored:
    """The card survives the database: both insert paths store ``card_data`` and every read returns it."""

    def test_add_collocation_round_trip(self, srs_db):
        unit = CARD.unit()
        assert srs_db.add_collocation(unit, "no")
        item = srs_db.get_collocation_by_guid(compute_guid(unit.text, "no", unit.disambig_key))
        assert sentence_card(item.syntactic_unit) == CARD

    def test_upsert_by_guid_round_trip(self, srs_db):
        card = CARD.with_blank(blank_span(CARD.text, "bor"), "live(s)")
        unit = card.unit()
        srs_db.upsert_by_guid(
            unit, "no", {Direction.PRODUCTION: DirectionState(direction=Direction.PRODUCTION, due_at=datetime.now(UTC))}
        )
        item = srs_db.get_collocation_by_guid(compute_guid(unit.text, "no", unit.disambig_key))
        assert sentence_card(item.syntactic_unit) == card

    def test_whole_and_blank_cards_of_one_sentence_are_two_rows(self, srs_db):
        whole = CARD.unit()
        blank = CARD.with_blank(blank_span(CARD.text, "bor"), "live(s)").unit()
        assert srs_db.add_collocation(whole, "no")
        assert srs_db.add_collocation(blank, "no")
        assert compute_guid(whole.text, "no", whole.disambig_key) != compute_guid(blank.text, "no", blank.disambig_key)

    def test_ordinary_rows_carry_no_card(self, srs_db):
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="hus", translation="house", word_count=1, difficulty=1, source="test")
        srs_db.add_collocation(unit, "no")
        item = srs_db.get_collocation_by_guid(compute_guid("hus", "no", ""))
        assert item.syntactic_unit.card_data == ""
        assert sentence_card(item.syntactic_unit) is None


class TestServed:
    """The review screen gets the card's parts, not its Anki HTML: it draws the cue, the
    speaker and the blank itself, and plays the cue's own take."""

    async def test_review_queue_serves_the_sentence_card(self, srs_db):
        from httpx import ASGITransport, AsyncClient

        from app.languages import get_language
        from app.main import app

        card = SentenceCard(
            "a-visit-1234",
            3,
            "Per",
            "Jeg bor i Oslo.",
            "Jeg bor i Oslo.",
            "I live in Oslo.",
            (Gloss("bor", "live(s) [_bo_]"),),
            cue=Cue("Kari", "Hvor bor du?", "Hvor bor du?", "Where do you live?", (Gloss("hvor", "where?"),)),
        ).with_blank((4, 7), "live(s)")
        unit = card.unit()
        srs_db.add_collocation(unit, "no")
        with srs_db._get_conn() as conn:
            cid = conn.execute("SELECT id FROM collocations WHERE disambig_key = ?", (card.key,)).fetchone()["id"]
        srs_db.add_media(cid, "audio_cue", "take_cue.mp3", "media/take_cue.mp3", "take_cue.mp3", "x", 1)
        srs_db.add_media(cid, "audio_tts_sentence", "take_ans.mp3", "media/take_ans.mp3", "take_ans.mp3", "x", 1)
        app.state.srs_db = srs_db
        app.state.language = get_language("no")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers={"X-TT-Language": "no"}
        ) as client:
            queue = (await client.get("/api/srs/review-queue")).json()["queue"]

        [served] = [e for e in queue if e["text"] == card.text]
        assert served["audio_url"] == "/api/srs/media/take_ans.mp3"
        assert served["sentence"] == {
            "speaker": "Per",
            "text": "Jeg bor i Oslo.",
            "blank_start": 4,
            "blank_end": 7,
            "blank_gloss_html": "live(s)",
            "english": "I live in Oslo.",
            "glosses": [{"word": "bor", "gloss_html": "live(s) [<i>bo</i>]"}],
            "picture_url": None,
            "cue": {
                "speaker": "Kari",
                "text": "Hvor bor du?",
                "english": "Where do you live?",
                "glosses": [{"word": "hvor", "gloss_html": "where?"}],
                "audio_url": "/api/srs/media/take_cue.mp3",
            },
        }
        assert {e["sentence"] is None for e in queue if e["text"] != card.text} <= {True}
