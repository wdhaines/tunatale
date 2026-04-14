"""Tests for the transcript extraction service."""

from __future__ import annotations

from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import LowercaseLemmatizer
from app.srs.transcript import TranscriptData, WordToken, extract_transcript


def _make_lesson(l2_phrases: list[tuple[str, str]] | None = None) -> Lesson:
    """Build a minimal lesson with a NATURAL_SPEED section.

    l2_phrases is a list of (role, text) tuples for L2 dialogue lines.
    """
    lesson = Lesson(title="Test Lesson", language_code="sl")
    phrases = []
    if l2_phrases:
        for role, text in l2_phrases:
            phrases.append(Phrase(text=text, voice_id="female-1", language_code="sl", role=role))
    lesson.sections = [Section(section_type=SectionType.NATURAL_SPEED, phrases=phrases)]
    return lesson


class TestExtractTranscript:
    def setup_method(self):
        self.db = SRSDatabase(":memory:")
        self.lemmatizer = LowercaseLemmatizer()

    def test_returns_transcript_data(self):
        lesson = _make_lesson([("female-1", "Zdravo.")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert isinstance(result, TranscriptData)

    def test_key_phrases_passed_through(self):
        lesson = _make_lesson()
        lesson.key_phrases = [KeyPhraseInfo(phrase="Zdravo", translation="Hello")]
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert len(result.key_phrases) == 1
        assert result.key_phrases[0].phrase == "Zdravo"

    def test_unknown_word_has_srs_state_unknown(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].srs_state == "unknown"

    def test_known_word_has_correct_srs_state(self):
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].srs_state == "new"

    def test_known_word_in_review_state(self):
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("banka")
        item.state = SRSState.REVIEW
        self.db.update_collocation(item)

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].srs_state == "review"

    def test_english_narrator_lines_excluded(self):
        lesson = Lesson(title="Test", language_code="sl")
        lesson.sections = [
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text="Scene: At the market", voice_id="narrator", language_code="en", role="narrator"),
                    Phrase(text="Zdravo.", voice_id="female-1", language_code="sl", role="female-1"),
                ],
            )
        ]
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert len(result.dialogue_lines) == 1
        assert result.dialogue_lines[0].role == "female-1"

    def test_punctuation_stripped_from_surface_and_lemma(self):
        lesson = _make_lesson([("female-1", "Zdravo,")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        assert words[0].surface == "Zdravo"
        assert words[0].lemma == "zdravo"

    def test_empty_lesson_no_natural_speed_section(self):
        lesson = Lesson(title="Empty", language_code="sl")
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines == []
        assert result.key_phrases == []

    def test_multiple_words_per_line(self):
        lesson = _make_lesson([("female-1", "Kje je banka?")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        assert len(words) == 3
        assert [w.surface for w in words] == ["Kje", "je", "banka"]
        assert [w.lemma for w in words] == ["kje", "je", "banka"]

    def test_role_preserved_on_dialogue_line(self):
        lesson = _make_lesson([("male-1", "Zdravo.")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].role == "male-1"

    def test_word_token_fields(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        word = result.dialogue_lines[0].words[0]
        assert isinstance(word, WordToken)
        assert word.surface == "banka"
        assert word.lemma == "banka"
        assert word.srs_state == "unknown"


class TestWordTokenEnrichment:
    """Tests for new srs_item_id, translation, and collocation_span_id fields."""

    def setup_method(self):
        self.db = SRSDatabase(":memory:")
        self.lemmatizer = LowercaseLemmatizer()

    def test_unknown_word_has_null_srs_item_id(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].srs_item_id is None

    def test_known_word_has_srs_item_id(self):
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")
        rows, _ = self.db.list_collocations()
        expected_id = rows[0][0]

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].srs_item_id == expected_id

    def test_translation_from_db_when_present(self):
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].translation == "bank"

    def test_translation_from_gloss_map_when_no_db_entry(self):
        lesson = _make_lesson([("female-1", "banka")])
        lesson.generation_metadata = {"token_glosses": {"banka": "bank"}}
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].translation == "bank"

    def test_db_translation_wins_over_gloss_map(self):
        unit = SyntacticUnit(
            text="banka", translation="bank (db)", word_count=1, difficulty=1, source="llm", lemma="banka"
        )
        self.db.add_collocation(unit, language_code="sl")
        lesson = _make_lesson([("female-1", "banka")])
        lesson.generation_metadata = {"token_glosses": {"banka": "bank (gloss)"}}
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].translation == "bank (db)"

    def test_unknown_word_no_translation_is_none(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].translation is None

    def test_collocation_span_id_set_for_multi_word_srs_item(self):
        unit = SyntacticUnit(
            text="kje je banka",
            translation="where is the bank",
            word_count=3,
            difficulty=2,
            source="llm",
            lemma=None,
        )
        self.db.add_collocation(unit, language_code="sl")
        rows, _ = self.db.list_collocations()
        coll_id = rows[0][0]

        lesson = _make_lesson([("female-1", "kje je banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        assert words[0].collocation_span_id == coll_id
        assert words[1].collocation_span_id == coll_id
        assert words[2].collocation_span_id == coll_id

    def test_collocation_start_true_only_for_first_token(self):
        unit = SyntacticUnit(
            text="kje je banka",
            translation="where is the bank",
            word_count=3,
            difficulty=2,
            source="llm",
            lemma=None,
        )
        self.db.add_collocation(unit, language_code="sl")

        lesson = _make_lesson([("female-1", "kje je banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        assert words[0].collocation_start is True
        assert words[1].collocation_start is False
        assert words[2].collocation_start is False

    def test_word_not_in_collocation_has_null_span_id(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].collocation_span_id is None
        assert result.dialogue_lines[0].words[0].collocation_start is False

    def test_single_word_entry_in_collocations_table_not_matched_as_span(self):
        # word_count=1 entries should not produce collocation spans
        unit = SyntacticUnit(
            text="banka",
            translation="bank",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="banka",
        )
        self.db.add_collocation(unit, language_code="sl")
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        # Should not get a span (word_count=1 entries are excluded from span matching)
        assert result.dialogue_lines[0].words[0].collocation_span_id is None
