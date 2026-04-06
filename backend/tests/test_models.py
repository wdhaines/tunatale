"""Domain model unit tests."""

import json
from datetime import date

import pytest

from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Rating, SRSItem, SRSState
from app.models.strategy import (
    DEFAULT_STRATEGY_CONFIGS,
    ContentStrategy,
    DifficultyLevel,
    PedagogicalScoringConfig,
)
from app.models.syntactic_unit import SyntacticUnit


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


class TestPedagogicalScoringConfig:
    """Tests for PedagogicalScoringConfig weight defaults and constraints."""

    def test_weights_sum_to_one(self):
        config = PedagogicalScoringConfig()
        total = (
            config.srs_readiness_weight
            + config.language_quality_weight
            + config.pedagogical_value_weight
            + config.diversity_weight
        )
        assert abs(total - 1.0) < 0.01

    def test_default_weights(self):
        config = PedagogicalScoringConfig()
        assert config.srs_readiness_weight == 0.4
        assert config.language_quality_weight == 0.3
        assert config.pedagogical_value_weight == 0.2
        assert config.diversity_weight == 0.1


class TestSyntacticUnit:
    """Tests for SyntacticUnit validation: word count, difficulty bounds."""

    def test_valid(self):
        unit = SyntacticUnit(text="dober dan", translation="good day", word_count=2, difficulty=1, source="corpus")
        assert unit.text == "dober dan"
        assert unit.word_count == 2

    @pytest.mark.parametrize("wc", [0, 9])
    def test_rejects_invalid_word_count(self, wc):
        with pytest.raises(ValueError, match="word_count"):
            SyntacticUnit(text="x", translation="y", word_count=wc, difficulty=1, source="corpus")

    @pytest.mark.parametrize("wc", [1, 8])
    def test_accepts_boundary_word_counts(self, wc):
        unit = SyntacticUnit(text="x", translation="y", word_count=wc, difficulty=1, source="corpus")
        assert unit.word_count == wc

    def test_rejects_invalid_difficulty(self):
        with pytest.raises(ValueError, match="difficulty"):
            SyntacticUnit(text="x", translation="y", word_count=1, difficulty=6, source="corpus")


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


class TestContentStrategy:
    """Tests for ContentStrategy configs and DifficultyLevel ordering."""

    def test_wider_strategy_max_new_collocations(self):
        config = DEFAULT_STRATEGY_CONFIGS[ContentStrategy.WIDER]
        assert config.max_new_collocations == 8

    def test_deeper_strategy_max_new_collocations(self):
        config = DEFAULT_STRATEGY_CONFIGS[ContentStrategy.DEEPER]
        assert config.max_new_collocations == 3

    def test_difficulty_level_progression(self):
        levels = list(DifficultyLevel)
        assert levels[0] == DifficultyLevel.BASIC
        assert levels[1] == DifficultyLevel.INTERMEDIATE
        assert levels[2] == DifficultyLevel.ADVANCED


class TestCurriculum:
    """Tests for Curriculum JSON serialization and CurriculumDay validation."""

    def test_serializes_to_json(self):
        curriculum = _make_curriculum()
        data = json.loads(curriculum.to_json())
        assert data["topic"] == "ordering coffee in Ljubljana"
        assert data["language_code"] == "sl"
        assert len(data["days"]) == 1
        assert data["days"][0]["day"] == 1

    def test_roundtrip_json(self):
        curriculum = _make_curriculum()
        restored = Curriculum.from_json(curriculum.to_json())
        assert restored.id == curriculum.id
        assert restored.topic == curriculum.topic
        assert restored.language_code == curriculum.language_code
        assert len(restored.days) == 1
        assert restored.days[0].collocations == ["dober dan", "dober večer"]

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

    def test_serializes_to_json(self):
        lesson = _make_lesson()
        data = json.loads(lesson.to_json())
        assert data["title"] == "Day 1"
        assert data["language_code"] == "sl"
        assert len(data["sections"]) == 2
        assert data["sections"][0]["section_type"] == "key_phrases"
        assert data["sections"][1]["section_type"] == "natural_speed"
        phrase = data["sections"][0]["phrases"][0]
        assert phrase["text"] == "dober dan"
        assert phrase["role"] == "female-1"
        assert phrase["voice_id"] == "sl-SI-PetraNeural"
        assert phrase["language_code"] == "sl"

    def test_roundtrip_json(self):
        original = _make_lesson()
        restored = Lesson.from_json(original.to_json())
        assert restored.title == original.title
        assert restored.language_code == original.language_code
        assert len(restored.sections) == len(original.sections)
        assert restored.sections[0].section_type == SectionType.KEY_PHRASES
        assert restored.sections[1].section_type == SectionType.NATURAL_SPEED
        assert restored.sections[0].phrases[0].text == "dober dan"
        assert restored.sections[0].phrases[0].role == "female-1"
        assert restored.sections[0].phrases[1].text == "kako ste"

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


class TestKeyPhraseInfo:
    """Tests for KeyPhraseInfo dataclass."""

    def test_stores_phrase_and_translation(self):
        kp = KeyPhraseInfo(phrase="dober dan", translation="good day")
        assert kp.phrase == "dober dan"
        assert kp.translation == "good day"


class TestPhrase:
    """Tests for Phrase role field defaults and explicit values."""

    def test_role_default_empty(self):
        phrase = Phrase(text="x", voice_id="v", language_code="sl")
        assert phrase.role == ""

    def test_role_explicit(self):
        phrase = Phrase(text="x", voice_id="v", language_code="sl", role="narrator")
        assert phrase.role == "narrator"


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
