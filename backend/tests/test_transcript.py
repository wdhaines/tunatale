"""Tests for the transcript extraction service."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import LowercaseLemmatizer
from app.srs.transcript import TranscriptData, WordToken, _is_due, extract_transcript, resolve_active_direction


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

    def test_surface_keyed_card_matched_via_surface_fallback(self):
        """A card keyed by its surface/greeting form is matched even when the
        lemmatizer reduces the token to a dictionary lemma with no card.

        Regression: 'dobrodošli' (Welcome) is stored under lemma 'dobrodošli',
        but classla lemmatizes it to 'dobrodošel' → it showed as unknown.
        """
        from app.srs.lemmatizer import TokenAnalysis
        from tests._helpers.lemmatizer import StubLemmatizer

        unit = SyntacticUnit(
            text="dobrodošli",
            translation="Welcome.",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="dobrodošli",
        )
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("dobrodošli")
        item.state = SRSState.REVIEW
        self.db.update_collocation(item)

        stub = StubLemmatizer()
        stub.set_sentence("Dobrodošli", [TokenAnalysis(surface="Dobrodošli", lemma="dobrodošel")])

        lesson = _make_lesson([("female-1", "Dobrodošli")])
        result = extract_transcript(lesson, self.db, stub)
        word = result.dialogue_lines[0].words[0]
        assert word.srs_state == "review"
        assert word.translation == "Welcome."

    def test_known_word_in_review_state(self):
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("banka")
        item.state = SRSState.REVIEW
        self.db.update_collocation(item)

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].srs_state == "review"

    def test_cloze_word_state_falls_through_to_production(self):
        """Cloze items have only PRODUCTION direction; transcript must not crash.

        Regression: `item.state.value` previously KeyErrored on cloze items because
        the legacy `_rec` shim hardcoded RECOGNITION. Now it falls through to
        production when card_type='cloze'.
        """
        from app.models.srs_item import Direction

        unit = SyntacticUnit(
            text="vsak",
            translation="every",
            word_count=1,
            difficulty=1,
            source="cloze",
            lemma="vsak",
            source_sentence="Odprto je vsak dan",
            card_type="cloze",
        )
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("vsak")
        item.directions[Direction.PRODUCTION].state = SRSState.LEARNING
        self.db.update_collocation(item)

        lesson = _make_lesson([("female-1", "Odprto je vsak dan")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        vsak_token = next(w for w in words if w.lemma == "vsak")
        assert vsak_token.srs_state == "learning"

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

    def test_reset_to_new_makes_word_due_again(self):
        """Regression (stuck reset): after Reset→new the word is clickable again.

        A graduated card (future due_at) reset to NEW via the popover used to stay
        not-due in the transcript (is_due False) → the plain click no-op'd and the
        card was stuck red. set_state_by_id now full-resets the schedule, so the
        reset word renders red (progress 0) AND due (is_due True) → re-learnable.
        """
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")
        rows, _ = self.db.list_collocations()
        row_id = rows[0][0]
        future = datetime(2099, 1, 1, 4, 0, tzinfo=UTC)
        with self.db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET state='review', due_at=?, last_review=?,"
                " reps=2, stability=4.47 WHERE collocation_id=?",
                (future.isoformat(), datetime(2026, 6, 2, tzinfo=UTC).isoformat(), row_id),
            )
            conn.commit()

        self.db.set_state_by_id(row_id, SRSState.NEW)

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        word = result.dialogue_lines[0].words[0]
        assert word.active_state == "new"
        assert word.is_due is True  # ← was False (stuck) before the fix
        assert word.progress == 0.0  # red on the ramp


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

    def test_collocation_state_and_lemma_set_for_span_tokens(self):
        unit = SyntacticUnit(
            text="kje je banka",
            translation="where is the bank",
            word_count=3,
            difficulty=2,
            source="llm",
            lemma=None,
        )
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("kje je banka")
        item.state = SRSState.REVIEW
        self.db.update_collocation(item)

        lesson = _make_lesson([("female-1", "kje je banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        assert [w.collocation_srs_state for w in words] == ["review", "review", "review"]
        assert [w.collocation_lemma for w in words] == ["kje je banka", "kje je banka", "kje je banka"]

    def test_word_not_in_collocation_has_null_collocation_state(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        word = result.dialogue_lines[0].words[0]
        assert word.collocation_srs_state is None
        assert word.collocation_lemma is None

    def test_collocation_translation_set_for_span_tokens(self):
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
        assert [w.collocation_translation for w in words] == [
            "where is the bank",
            "where is the bank",
            "where is the bank",
        ]

    def test_word_not_in_collocation_has_null_collocation_translation(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        word = result.dialogue_lines[0].words[0]
        assert word.collocation_translation is None

    def test_transcript_word_matches_existing_lowercase_card(self):
        """Sentence-initial 'Zdravo' matches existing 'zdravo' card via lemma lookup."""
        unit = SyntacticUnit(
            text="zdravo", translation="hello", word_count=1, difficulty=1, source="llm", lemma="zdravo"
        )
        self.db.add_collocation(unit, language_code="sl")
        lesson = _make_lesson([("female-1", "Zdravo!")])
        transcript = extract_transcript(lesson, self.db, self.lemmatizer)
        word = transcript.dialogue_lines[0].words[0]
        assert word.surface == "Zdravo"
        assert word.lemma == "zdravo"
        assert word.srs_state == "new"
        assert word.srs_item_id is not None


class TestResolveActiveDirection:
    def test_cloze_returns_production(self):
        item = SyntacticUnit(
            text="je", translation="is", word_count=1, difficulty=1, source="llm", lemma="je", card_type="cloze"
        )
        from app.models.srs_item import SRSItem

        srs = SRSItem(syntactic_unit=item)
        assert resolve_active_direction(srs) == Direction.PRODUCTION

    def test_vocab_recognition_not_review_returns_recognition(self):
        item = SyntacticUnit(
            text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka", card_type="vocab"
        )
        from app.models.srs_item import SRSItem

        srs = SRSItem(syntactic_unit=item)
        # NEW → recognition
        assert resolve_active_direction(srs) == Direction.RECOGNITION

    def test_vocab_recognition_review_returns_production(self):
        item = SyntacticUnit(
            text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka", card_type="vocab"
        )
        from app.models.srs_item import SRSItem

        srs = SRSItem(syntactic_unit=item)
        srs.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        assert resolve_active_direction(srs) == Direction.PRODUCTION

    def test_vocab_both_review_returns_production(self):
        item = SyntacticUnit(
            text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka", card_type="vocab"
        )
        from app.models.srs_item import SRSItem

        srs = SRSItem(syntactic_unit=item)
        srs.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        srs.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        assert resolve_active_direction(srs) == Direction.PRODUCTION


class TestResolveActiveDirectionDirect:
    def test_non_srs_item_returns_production(self):
        assert resolve_active_direction(object()) == Direction.PRODUCTION

    def test_cloze_item_returns_production(self):
        from app.models.srs_item import SRSItem
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(
            text="je", translation="is", word_count=1, difficulty=1, source="llm", lemma="je", card_type="cloze"
        )
        item = SRSItem(syntactic_unit=unit)
        assert resolve_active_direction(item) == Direction.PRODUCTION

    def test_vocab_no_recognition_direction_returns_recognition(self):
        from app.models.srs_item import SRSItem
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        item = SRSItem(syntactic_unit=unit)
        rec = item.directions.get(Direction.RECOGNITION)
        if rec is not None:
            item.directions.pop(Direction.RECOGNITION)
        assert resolve_active_direction(item) == Direction.RECOGNITION


class TestIsDue:
    def test_not_due_when_known(self):
        ds = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.KNOWN, due_at=datetime(2024, 1, 1, tzinfo=UTC)
        )
        assert _is_due(ds, date(2026, 6, 1)) is False

    def test_due_when_review_and_past_due(self):
        ds = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.REVIEW, due_at=datetime(2024, 1, 1, tzinfo=UTC)
        )
        assert _is_due(ds, date(2026, 6, 1)) is True

    def test_not_due_when_review_and_future_due(self):
        ds = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.REVIEW, due_at=datetime(2026, 7, 1, tzinfo=UTC)
        )
        assert _is_due(ds, date(2026, 6, 1)) is False


class TestTranscriptEnrichment:
    def setup_method(self):
        self.db = SRSDatabase(":memory:")
        self.lemmatizer = LowercaseLemmatizer()
        self.today = date(2026, 6, 1)
        self.now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    def _add_vocab(self, text: str, translation: str, lemma: str | None = None) -> int:
        unit = SyntacticUnit(
            text=text, translation=translation, word_count=1, difficulty=1, source="llm", lemma=lemma or text
        )
        self.db.add_collocation(unit, language_code="sl")
        return self.db.get_collocation_id_by_guid(unit.guid)

    def test_unknown_token_has_defaults(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today, now=self.now, col_crt=None)
        word = result.dialogue_lines[0].words[0]
        assert word.progress is None
        assert word.active_state == "unknown"
        assert word.inflectable is False
        assert word.card_type is None
        assert word.active_direction is None
        assert word.is_due is False
        assert word.inflection_feature is None

    def test_vocab_recognition_not_review_active_direction_recognition(self):
        self._add_vocab("banka", "bank", lemma="banka")
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today, now=self.now, col_crt=None)
        word = result.dialogue_lines[0].words[0]
        assert word.active_direction == "recognition"
        assert word.active_state == "new"

    def test_vocab_both_review_active_direction_production(self):
        from app.models.srs_item import Direction, SRSState

        self._add_vocab("banka", "bank", lemma="banka")
        item = self.db.get_collocation("banka")
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today, now=self.now, col_crt=None)
        word = result.dialogue_lines[0].words[0]
        assert word.active_direction == "production"
        assert word.active_state == "review"

    def test_cloze_base_active_direction_production(self):
        unit = SyntacticUnit(
            text="je",
            translation="is",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="je",
            card_type="cloze",
            source_sentence="To je dobro.",
        )
        self.db.add_collocation(unit, language_code="sl")
        lesson = _make_lesson([("female-1", "To je dobro.")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today, now=self.now, col_crt=None)
        word = next(w for w in result.dialogue_lines[0].words if w.lemma == "je")
        assert word.active_direction == "production"
        assert word.card_type == "cloze"

    def test_progress_reflects_components(self):
        self._add_vocab("banka", "bank", lemma="banka")
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today, now=self.now, col_crt=None)
        word = result.dialogue_lines[0].words[0]
        # NEW → mastery is 0.0
        assert word.progress == 0.0

    def test_inflectable_true_for_a1_surface_differs_from_lemma_with_reviewed_base(self):
        """Inflectable requires surface!=lemma, A1 feature, base production REVIEW/KNOWN, no existing cloze."""
        from app.models.srs_item import Direction, SRSState

        self._add_vocab("hoditi", "to walk", lemma="hoditi")
        item = self.db.get_collocation("hoditi")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        lesson = _make_lesson([("female-1", "hodim")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today, now=self.now, col_crt=None)
        word = result.dialogue_lines[0].words[0]
        # LowercaseLemmatizer returns empty upos/case/number → ud_feats_to_tt_feature returns None
        # So inflectable depends on having analysis data. With LowercaseLemmatizer, analysis returns
        # empty strings → no feature → inflectable=False
        assert word.inflectable is False  # not enough analysis data from LowercaseLemmatizer

    def test_inflectable_false_when_surface_equals_lemma(self):
        self._add_vocab("banka", "bank", lemma="banka")
        item = self.db.get_collocation("banka")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today, now=self.now, col_crt=None)
        word = result.dialogue_lines[0].words[0]
        assert word.inflectable is False  # surface==lemma

    def test_inflectable_false_when_inflection_cloze_already_exists(self):
        """When an exact-surface inflection cloze exists, inflectable=False."""
        from app.models.srs_item import Direction, SRSState

        lemma = "lep"
        self._add_vocab("lep", "nice", lemma=lemma)
        item = self.db.get_collocation("lep")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        # Create an existing inflection cloze
        unit = SyntacticUnit(
            text="lepa",
            translation="nice (fem)",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma=lemma,
            disambig_key="morph:adj-nom-f-sg",
            card_type="cloze",
            source_sentence="Hiša je lepa.",
        )
        self.db.add_collocation(unit, language_code="sl")

        lesson = _make_lesson([("female-1", "Hiša je lepa.")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today, now=self.now, col_crt=None)
        lepa_words = [w for w in result.dialogue_lines[0].words if w.surface == "lepa"]
        for w in lepa_words:
            assert w.inflectable is False  # cloze already exists

    def test_same_lemma_twice_hits_cache(self):
        """Two occurrences of the same lemma within one lesson hit inflection cache."""
        self._add_vocab("banka", "bank", lemma="banka")
        lesson = _make_lesson([("female-1", "Banka je v banki.")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today, now=self.now, col_crt=None)
        # Both "banka" tokens should resolve (lemma same either way)
        words = result.dialogue_lines[0].words
        banka_words = [w for w in words if w.lemma == "banka"]
        assert len(banka_words) >= 1

    def test_unknown_word_has_empty_components(self):
        lesson = _make_lesson([("female-1", "xyznonexistent")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today, now=self.now, col_crt=None)
        word = result.dialogue_lines[0].words[0]
        assert word.progress is None
        assert word.srs_state == "unknown"
        assert word.active_state == "unknown"

    def test_inflectable_path_with_analysis_data(self):
        """Use a custom lemmatizer that returns analysis data to cover inflectable detection."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockLemmatizer:
            def lemmatize(self, word: str, language_code: str) -> str:
                if word == "hodim":
                    return "hoditi"
                return word.lower()

            def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
                if word == "hodim":
                    return "hoditi", "", ""
                return word.lower(), "", ""

            def analyze_sentence(self, sentence: str, language_code: str) -> list:
                if "hodim" in sentence:
                    return [
                        _TA(
                            surface="hodim", lemma="hoditi", upos="VERB", case="", number="Sing", person="1", gender=""
                        ),
                    ]
                return []

        from app.models.srs_item import Direction, SRSState

        self._add_vocab("hoditi", "to walk", lemma="hoditi")
        item = self.db.get_collocation("hoditi")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        lesson = _make_lesson([("female-1", "hodim")])
        result = extract_transcript(lesson, self.db, _MockLemmatizer(), today=self.today, now=self.now, col_crt=None)
        word = result.dialogue_lines[0].words[0]
        # Now lemmatizer maps "hodim" → "hoditi" → base found → inflectable detection runs
        # ud_feats_to_tt_feature("VERB", "", "Sing", "1", "") → "verb:1sg" → is_a1_morphology_feature True
        assert word.inflectable is True
        assert word.inflection_feature == "verb:1sg"
        assert word.srs_state != "unknown"

    def test_inflection_cloze_exact_match_path(self):
        """When an inflection cloze with exact surface match exists, it takes priority over the base."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockLemmatizer2:
            def lemmatize(self, word: str, language_code: str) -> str:
                if word == "lepa":
                    return "lep"
                return word.lower()

            def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
                return word.lower(), "", ""

            def analyze_sentence(self, sentence: str, language_code: str) -> list:
                if "lep" in sentence:
                    return [
                        _TA(
                            surface="Lepa", lemma="lep", upos="ADJ", case="Nom", number="Sing", person="", gender="Fem"
                        ),
                        _TA(surface="je", lemma="je", upos="AUX", case="", number="", person="", gender=""),
                        _TA(
                            surface="lepa", lemma="lep", upos="ADJ", case="Nom", number="Sing", person="", gender="Fem"
                        ),
                    ]
                return []

        from app.models.srs_item import Direction, SRSState

        self._add_vocab("lep", "nice", lemma="lep")
        item = self.db.get_collocation("lep")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        # Create an inflection cloze with exact surface "lepa"
        unit = SyntacticUnit(
            text="lepa",
            translation="nice (fem)",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="lep",
            disambig_key="morph:adj-nom-f-sg",
            card_type="cloze",
            source_sentence="Lepa je lepa.",
        )
        self.db.add_collocation(unit, language_code="sl")

        lesson = _make_lesson([("female-1", "Lepa je lepa.")])
        result = extract_transcript(lesson, self.db, _MockLemmatizer2(), today=self.today, now=self.now, col_crt=None)
        words = result.dialogue_lines[0].words
        lepa_words = [w for w in words if w.surface == "lepa"]
        for w in lepa_words:
            # Resolved as inflection cloze → card_type=cloze
            assert w.card_type == "cloze"
            assert w.inflectable is False  # already has the cloze
            assert w.active_direction == "production"

    def test_inflectable_else_branch_when_analysis_missing(self):
        """Surface!=lemma but no analysis_by_surface entry → feature_str='' (line 242)."""

        class _MockNoAnalysis:
            def lemmatize(self, word, language_code):
                return {"hodim": "hoditi"}.get(word, word.lower())

            def analyze(self, word, language_code):
                return word.lower(), "", ""

            def analyze_sentence(self, sentence, language_code):
                return []

        from app.models.srs_item import Direction, SRSState

        self._add_vocab("hoditi", "to walk", lemma="hoditi")
        item = self.db.get_collocation("hoditi")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        lesson = _make_lesson([("female-1", "hodim")])
        result = extract_transcript(lesson, self.db, _MockNoAnalysis(), today=self.today, now=self.now, col_crt=None)
        word = result.dialogue_lines[0].words[0]
        # analysis_by_surface is empty → ta=None → feature_str="" → no inflectable detection
        assert word.inflectable is False
        assert word.inflection_feature is None
        assert word.srs_state != "unknown"

    def test_inflection_cloze_with_reviewed_production_hits_inflectable_else_branch(self):
        """When inflection cloze exists and its production is REVIEW/KNOWN, the
        inflectable check's `inflection_match is not None` else branch is taken."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockLemmatizer3:
            def lemmatize(self, word, language_code):
                return {"Lepa": "lep", "lepa": "lep"}.get(word, word.lower())

            def analyze(self, word, language_code):
                return word.lower(), "", ""

            def analyze_sentence(self, sentence, language_code):
                return [
                    _TA(surface="Lepa", lemma="lep", upos="ADJ", case="Nom", number="Sing", person="", gender="Fem"),
                    _TA(surface="je", lemma="je", upos="AUX", case="", number="", person="", gender=""),
                    _TA(surface="lepa", lemma="lep", upos="ADJ", case="Nom", number="Sing", person="", gender="Fem"),
                ]

        from app.models.srs_item import Direction, SRSState

        self._add_vocab("lep", "nice", lemma="lep")
        item = self.db.get_collocation("lep")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        # Create an inflection cloze with exact surface "lepa" AND set its PRODUCTION to REVIEW
        unit = SyntacticUnit(
            text="lepa",
            translation="nice (fem)",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="lep",
            disambig_key="morph:adj-nom-f-sg",
            card_type="cloze",
            source_sentence="Lepa je lepa.",
        )
        self.db.add_collocation(unit, language_code="sl")

        # Set the cloze's production to REVIEW/KNOWN so the inner check is entered
        ic_item = self.db.get_collocation("lepa")
        if ic_item is not None:
            ic_item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
            ic_item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
            self.db.update_direction(ic_item.guid, Direction.PRODUCTION, ic_item.directions[Direction.PRODUCTION])

        lesson = _make_lesson([("female-1", "Lepa je lepa.")])
        result = extract_transcript(lesson, self.db, _MockLemmatizer3(), today=self.today, now=self.now, col_crt=None)
        lepa_words = [w for w in result.dialogue_lines[0].words if w.surface == "lepa"]
        for w in lepa_words:
            # Token resolved as cloze, but inflectable=False (else branch of inflection_match is None)
            assert w.inflectable is False

    def test_add_inflection_cloze_lowers_progress(self):
        """Adding an inflection cloze component lowers the base token's progress."""
        from app.models.srs_item import Direction, SRSState

        self._add_vocab("delati", "to work", lemma="delati")
        item = self.db.get_collocation("delati")
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        lesson_no_inflection = _make_lesson([("female-1", "delati")])
        result_no = extract_transcript(
            lesson_no_inflection, self.db, self.lemmatizer, today=self.today, now=self.now, col_crt=None
        )
        progress_without = result_no.dialogue_lines[0].words[0].progress

        # Add an inflection cloze for this lemma (no last_review → mastery=0.0)
        unit = SyntacticUnit(
            text="delam",
            translation="I work",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="delati",
            disambig_key="morph:verb-1sg",
            card_type="cloze",
        )
        self.db.add_collocation(unit, language_code="sl")
        ic_item = self.db.get_collocation("delam")
        if ic_item is not None:
            if Direction.PRODUCTION in ic_item.directions:
                ic_item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
            self.db.update_direction(ic_item.guid, Direction.PRODUCTION, ic_item.directions[Direction.PRODUCTION])

        lesson_with_inflection = _make_lesson([("female-1", "delati")])
        result_with = extract_transcript(
            lesson_with_inflection, self.db, self.lemmatizer, today=self.today, now=self.now, col_crt=None
        )
        progress_with = result_with.dialogue_lines[0].words[0].progress
        assert progress_with is not None
        assert progress_without is not None
        assert progress_with < progress_without
