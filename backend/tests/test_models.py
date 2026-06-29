"""Domain model unit tests."""

import json
from datetime import date

import pytest

from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Rating, SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers import assert_json_roundtrip


def _make_curriculum() -> Curriculum:
    day = CurriculumDay(
        day=1,
        title="Greetings",
        focus="Basic greetings",
        collocations=["dober dan", "dober večer"],
        learning_objective="Learn basic greetings",
        story_guidance="Café scene",
    )
    return Curriculum(
        id="test-id",
        topic="ordering coffee in Ljubljana",
        language_code="sl",
        cefr_level="A2",
        days=[day],
    )


def _make_lesson() -> Lesson:
    return Lesson(
        title="Day 1",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.KEY_PHRASES,
                phrases=[
                    Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                    Phrase(text="kako ste", voice_id="sl-SI-RokNeural", language_code="sl", role="male-1"),
                ],
            ),
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text="dober dan, kako ste", voice_id="sl-SI-PetraNeural", language_code="sl"),
                ],
            ),
        ],
    )


def _make_srs_item() -> SRSItem:
    unit = SyntacticUnit(text="dober dan", translation="good day", word_count=2, difficulty=1, source="corpus")
    return SRSItem(syntactic_unit=unit, due_date=date.today())


class TestSyntacticUnit:
    """Tests for SyntacticUnit validation: word count, difficulty bounds."""

    def test_valid(self):
        unit = SyntacticUnit(text="dober dan", translation="good day", word_count=2, difficulty=1, source="corpus")
        assert unit.text == "dober dan"
        assert unit.word_count == 2

    @pytest.mark.parametrize("wc", [-1, 0])
    def test_rejects_invalid_word_count(self, wc):
        with pytest.raises(ValueError, match="word_count"):
            SyntacticUnit(text="x", translation="y", word_count=wc, difficulty=1, source="corpus")

    @pytest.mark.parametrize("wc", [1, 8, 12, 50])
    def test_accepts_boundary_word_counts(self, wc):
        """Long word counts must be accepted — reference/Q&A Anki notes can
        produce 12+ word L2 extractions when the front field is a long English
        question. The upper bound was removed; only word_count < 1 is rejected.
        """
        unit = SyntacticUnit(text="x", translation="y", word_count=wc, difficulty=1, source="corpus")
        assert unit.word_count == wc

    def test_rejects_invalid_difficulty(self):
        with pytest.raises(ValueError, match="difficulty"):
            SyntacticUnit(text="x", translation="y", word_count=1, difficulty=6, source="corpus")

    def test_lemma_defaults_to_none(self):
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm")
        assert unit.lemma is None

    def test_lemma_stores_value(self):
        unit = SyntacticUnit(text="Banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        assert unit.lemma == "banka"

    def test_extras_default_empty(self):
        unit = SyntacticUnit(text="x", translation="y", word_count=1, difficulty=1, source="corpus")
        assert unit.extras == ()


class TestBackFieldSerialization:
    """`serialize_extras` / `deserialize_extras` round-trip + tolerance."""

    def test_round_trip(self):
        from app.models.syntactic_unit import BackField, deserialize_extras, serialize_extras

        extras = (
            BackField(label="IPA", html="/ˈʋæːɾə/", tier="summary"),
            BackField(label="Dictionary entry", html="<h2>være</h2>", tier="deep"),
        )
        assert deserialize_extras(serialize_extras(extras)) == extras

    def test_empty_serializes_to_blank_string(self):
        from app.models.syntactic_unit import serialize_extras

        assert serialize_extras(()) == ""

    @pytest.mark.parametrize("raw", ["", None])
    def test_deserialize_blank_yields_empty(self, raw):
        from app.models.syntactic_unit import deserialize_extras

        assert deserialize_extras(raw) == ()

    def test_deserialize_malformed_json_yields_empty(self):
        from app.models.syntactic_unit import deserialize_extras

        assert deserialize_extras("{not json") == ()

    def test_deserialize_non_list_yields_empty(self):
        from app.models.syntactic_unit import deserialize_extras

        assert deserialize_extras('{"label": "x"}') == ()

    def test_deserialize_skips_malformed_entries_and_defaults_tier(self):
        from app.models.syntactic_unit import BackField, deserialize_extras

        # One good entry (tier omitted → defaults to "details"); a non-dict and a
        # dict missing required keys are both dropped.
        raw = '[{"label": "Note", "html": "n"}, "junk", {"label": "x"}]'
        assert deserialize_extras(raw) == (BackField(label="Note", html="n", tier="details"),)


class TestLanguage:
    """Tests for Language factory methods and voice map structure."""

    def test_slovene_code(self):
        lang = Language.slovene()
        assert lang.code == "sl"

    def test_slovene_has_female_voice(self):
        lang = Language.slovene()
        assert "female" in lang.tts_voice_map
        assert "sl-SI" in lang.tts_voice_map["female"]

    def test_slovene_has_male_voice(self):
        lang = Language.slovene()
        assert "male" in lang.tts_voice_map
        assert "sl-SI" in lang.tts_voice_map["male"]

    def test_english_code(self):
        lang = Language.english()
        assert lang.code == "en"

    def test_slovene_voice_map_has_role_keys(self):
        lang = Language.slovene()
        for key in ("narrator", "female-1", "male-1"):
            assert key in lang.tts_voice_map, f"missing key '{key}' in {lang.code} voice map"

    def test_slovene_voice_map_has_legacy_keys(self):
        lang = Language.slovene()
        assert "female" in lang.tts_voice_map
        assert "male" in lang.tts_voice_map

    def test_english_voice_map_has_role_keys(self):
        lang = Language.english()
        for key in ("narrator", "female-1", "male-1"):
            assert key in lang.tts_voice_map, f"missing key '{key}' in {lang.code} voice map"

    def test_norwegian_code(self):
        lang = Language.norwegian()
        assert lang.code == "no"

    def test_norwegian_name(self):
        lang = Language.norwegian()
        assert lang.name == "Norwegian"
        assert lang.native_name == "norsk"

    def test_norwegian_has_all_voices(self):
        lang = Language.norwegian()
        for key in ("narrator", "female-1", "female-2", "male-1", "male-2"):
            assert key in lang.tts_voice_map, f"missing '{key}' in Norwegian voice map"

    def test_norwegian_has_legacy_aliases(self):
        lang = Language.norwegian()
        assert "female" in lang.tts_voice_map
        assert "male" in lang.tts_voice_map

    def test_norwegian_voices_are_nb_no(self):
        lang = Language.norwegian()
        assert "nb-NO" in lang.tts_voice_map["female-1"]
        assert "nb-NO" in lang.tts_voice_map["male-1"]


class TestCurriculum:
    """Tests for Curriculum JSON serialization and CurriculumDay validation."""

    def test_roundtrip_json(self):
        curriculum = _make_curriculum()
        assert_json_roundtrip(curriculum)

    def test_day_rejects_non_positive_day(self):
        with pytest.raises(ValueError):
            CurriculumDay(day=0, title="x", focus="x", collocations=[], learning_objective="x")


class TestLesson:
    """Tests for Lesson/Section structure and JSON serialization."""

    def test_section_valid_type(self):
        phrase = Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")
        section = Section(section_type=SectionType.KEY_PHRASES, phrases=[phrase])
        assert section.section_type.value == "key_phrases"

    def test_section_rejects_invalid_type(self):
        with pytest.raises((ValueError, AttributeError)):
            Section(section_type="invalid_type", phrases=[])  # type: ignore[arg-type]

    def test_has_four_section_types(self):
        types = list(SectionType)
        assert SectionType.KEY_PHRASES in types
        assert SectionType.NATURAL_SPEED in types
        assert SectionType.SLOW_SPEED in types
        assert SectionType.TRANSLATED in types

    def test_roundtrip_json(self):
        original = _make_lesson()
        assert_json_roundtrip(original)

    def test_lesson_with_key_phrases_roundtrip(self):
        lesson = _make_lesson()
        lesson.key_phrases = [
            KeyPhraseInfo(phrase="dober dan", translation="good day"),
            KeyPhraseInfo(phrase="kako ste", translation="how are you"),
        ]
        restored = Lesson.from_json(lesson.to_json())
        assert len(restored.key_phrases) == 2
        assert restored.key_phrases[0].phrase == "dober dan"
        assert restored.key_phrases[0].translation == "good day"
        assert restored.key_phrases[1].phrase == "kako ste"

    def test_lesson_without_key_phrases_deserializes_empty(self):
        """Old lessons serialized without key_phrases should deserialize with empty list."""
        lesson = _make_lesson()
        data = json.loads(lesson.to_json())
        data.pop("key_phrases", None)
        restored = Lesson.from_json(json.dumps(data))
        assert restored.key_phrases == []


class TestExtractSentenceTranslationsFromTranslated:
    """Helper used to backfill sentence_translations from a stored Lesson's TRANSLATED section."""

    def _lesson_with_translated(self, phrases: list[Phrase]) -> Lesson:
        return Lesson(
            title="t",
            language_code="sl",
            sections=[Section(section_type=SectionType.TRANSLATED, phrases=phrases)],
        )

    def test_pairs_sl_with_following_en(self):
        from app.models.lesson import extract_sentence_translations_from_translated

        lesson = self._lesson_with_translated(
            [
                Phrase(text="Kam greš?", voice_id="v", language_code="sl"),
                Phrase(text="Where are you going?", voice_id="v", language_code="en"),
                Phrase(text="Grem domov.", voice_id="v", language_code="sl"),
                Phrase(text="I'm going home.", voice_id="v", language_code="en"),
            ]
        )
        result = extract_sentence_translations_from_translated(lesson)
        assert result == {"Kam greš?": "Where are you going?", "Grem domov.": "I'm going home."}

    def test_skips_unpaired_sl(self):
        from app.models.lesson import extract_sentence_translations_from_translated

        lesson = self._lesson_with_translated(
            [
                Phrase(text="Kam greš?", voice_id="v", language_code="sl"),
                Phrase(text="Še ena fraza.", voice_id="v", language_code="sl"),  # SL after SL — not paired
                Phrase(text="Another phrase.", voice_id="v", language_code="en"),
            ]
        )
        result = extract_sentence_translations_from_translated(lesson)
        assert result == {"Še ena fraza.": "Another phrase."}

    def test_first_occurrence_wins_on_duplicate(self):
        from app.models.lesson import extract_sentence_translations_from_translated

        lesson = self._lesson_with_translated(
            [
                Phrase(text="Kako si?", voice_id="v", language_code="sl"),
                Phrase(text="How are you?", voice_id="v", language_code="en"),
                Phrase(text="Kako si?", voice_id="v", language_code="sl"),
                Phrase(text="(repeated translation)", voice_id="v", language_code="en"),
            ]
        )
        result = extract_sentence_translations_from_translated(lesson)
        assert result == {"Kako si?": "How are you?"}

    def test_returns_empty_when_no_translated_section(self):
        from app.models.lesson import extract_sentence_translations_from_translated

        lesson = Lesson(
            title="t",
            language_code="sl",
            sections=[Section(section_type=SectionType.NATURAL_SPEED, phrases=[])],
        )
        assert extract_sentence_translations_from_translated(lesson) == {}

    def test_ignores_english_label_lines(self):
        """The TRANSLATED section often opens with EN-EN label lines that we shouldn't pair."""
        from app.models.lesson import extract_sentence_translations_from_translated

        lesson = self._lesson_with_translated(
            [
                Phrase(text="Translated", voice_id="v", language_code="en"),
                Phrase(text="At the Cafe", voice_id="v", language_code="en"),
                Phrase(text="Dober dan!", voice_id="v", language_code="sl"),
                Phrase(text="Good day!", voice_id="v", language_code="en"),
            ]
        )
        result = extract_sentence_translations_from_translated(lesson)
        assert result == {"Dober dan!": "Good day!"}


class TestSRSItem:
    """Tests for SRSItem initial state and enum values."""

    def test_initial_state_is_new(self):
        item = _make_srs_item()
        assert item.state == SRSState.NEW

    def test_initial_reps_zero(self):
        item = _make_srs_item()
        assert item.reps == 0
        assert item.lapses == 0

    def test_rating_values(self):
        assert Rating.AGAIN.value == 1
        assert Rating.HARD.value == 2
        assert Rating.GOOD.value == 3
        assert Rating.EASY.value == 4

    def test_state_enum_values(self):
        assert SRSState.NEW.value == "new"
        assert SRSState.LEARNING.value == "learning"
        assert SRSState.REVIEW.value == "review"
        assert SRSState.RELEARNING.value == "relearning"
