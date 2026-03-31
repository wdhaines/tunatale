"""Domain model unit tests."""

import json
from datetime import date

import pytest

from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Rating, SRSItem, SRSState
from app.models.strategy import (
    DEFAULT_STRATEGY_CONFIGS,
    ContentStrategy,
    DifficultyLevel,
    PedagogicalScoringConfig,
)
from app.models.syntactic_unit import SyntacticUnit

# ── PedagogicalScoringConfig ───────────────────────────────────────────────


def test_pedagogical_scoring_config_weights_sum_to_one():
    config = PedagogicalScoringConfig()
    total = (
        config.srs_readiness_weight
        + config.language_quality_weight
        + config.pedagogical_value_weight
        + config.diversity_weight
    )
    assert abs(total - 1.0) < 0.01


def test_pedagogical_scoring_config_default_weights():
    config = PedagogicalScoringConfig()
    assert config.srs_readiness_weight == 0.4
    assert config.language_quality_weight == 0.3
    assert config.pedagogical_value_weight == 0.2
    assert config.diversity_weight == 0.1


# ── SyntacticUnit ─────────────────────────────────────────────────────────


def test_syntactic_unit_valid():
    unit = SyntacticUnit(text="dober dan", translation="good day", word_count=2, difficulty=1, source="corpus")
    assert unit.text == "dober dan"
    assert unit.word_count == 2


def test_syntactic_unit_rejects_zero_word_count():
    with pytest.raises(ValueError, match="word_count"):
        SyntacticUnit(text="x", translation="y", word_count=0, difficulty=1, source="corpus")


def test_syntactic_unit_rejects_nine_word_count():
    with pytest.raises(ValueError, match="word_count"):
        SyntacticUnit(text="a b c d e f g h i", translation="...", word_count=9, difficulty=1, source="corpus")


def test_syntactic_unit_accepts_boundary_word_counts():
    for wc in (1, 8):
        unit = SyntacticUnit(text="x", translation="y", word_count=wc, difficulty=1, source="corpus")
        assert unit.word_count == wc


def test_syntactic_unit_rejects_invalid_difficulty():
    with pytest.raises(ValueError, match="difficulty"):
        SyntacticUnit(text="x", translation="y", word_count=1, difficulty=6, source="corpus")


# ── Language ──────────────────────────────────────────────────────────────


def test_language_slovene_code():
    lang = Language.slovene()
    assert lang.code == "sl"


def test_language_slovene_has_female_voice():
    lang = Language.slovene()
    assert "female" in lang.tts_voice_map
    assert "sl-SI" in lang.tts_voice_map["female"]


def test_language_slovene_has_male_voice():
    lang = Language.slovene()
    assert "male" in lang.tts_voice_map
    assert "sl-SI" in lang.tts_voice_map["male"]


def test_language_english_code():
    lang = Language.english()
    assert lang.code == "en"


# ── ContentStrategy configs ───────────────────────────────────────────────


def test_wider_strategy_max_new_collocations():
    config = DEFAULT_STRATEGY_CONFIGS[ContentStrategy.WIDER]
    assert config.max_new_collocations == 8


def test_deeper_strategy_max_new_collocations():
    config = DEFAULT_STRATEGY_CONFIGS[ContentStrategy.DEEPER]
    assert config.max_new_collocations == 3


def test_difficulty_level_progression():
    levels = list(DifficultyLevel)
    assert levels[0] == DifficultyLevel.BASIC
    assert levels[1] == DifficultyLevel.INTERMEDIATE
    assert levels[2] == DifficultyLevel.ADVANCED


# ── Curriculum ────────────────────────────────────────────────────────────


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


def test_curriculum_serializes_to_json():
    curriculum = _make_curriculum()
    data = json.loads(curriculum.to_json())
    assert data["topic"] == "ordering coffee in Ljubljana"
    assert data["language_code"] == "sl"
    assert len(data["days"]) == 1
    assert data["days"][0]["day"] == 1


def test_curriculum_roundtrip_json():
    curriculum = _make_curriculum()
    restored = Curriculum.from_json(curriculum.to_json())
    assert restored.id == curriculum.id
    assert restored.topic == curriculum.topic
    assert restored.language_code == curriculum.language_code
    assert len(restored.days) == 1
    assert restored.days[0].collocations == ["dober dan", "dober večer"]


def test_curriculum_day_rejects_non_positive_day():
    with pytest.raises(ValueError):
        CurriculumDay(day=0, title="x", focus="x", collocations=[], learning_objective="x")


# ── Lesson/Section ────────────────────────────────────────────────────────


def test_section_valid_type():
    from app.models.lesson import SectionType

    phrase = Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")
    section = Section(section_type=SectionType.KEY_PHRASES, phrases=[phrase])
    assert section.section_type.value == "key_phrases"


def test_section_rejects_invalid_type():
    with pytest.raises((ValueError, AttributeError)):
        Section(section_type="invalid_type", phrases=[])  # type: ignore[arg-type]


def test_lesson_has_four_section_types():
    from app.models.lesson import SectionType

    types = list(SectionType)
    assert SectionType.KEY_PHRASES in types
    assert SectionType.NATURAL_SPEED in types
    assert SectionType.SLOW_SPEED in types
    assert SectionType.TRANSLATED in types


# ── Phrase role field ────────────────────────────────────────────────────


def test_phrase_role_default_empty():
    phrase = Phrase(text="x", voice_id="v", language_code="sl")
    assert phrase.role == ""


def test_phrase_role_explicit():
    phrase = Phrase(text="x", voice_id="v", language_code="sl", role="narrator")
    assert phrase.role == "narrator"


def test_slovene_voice_map_has_role_keys():
    lang = Language.slovene()
    for key in ("narrator", "female-1", "male-1"):
        assert key in lang.tts_voice_map, f"missing key: {key}"


def test_slovene_voice_map_has_legacy_keys():
    lang = Language.slovene()
    assert "female" in lang.tts_voice_map
    assert "male" in lang.tts_voice_map


def test_english_voice_map_has_role_keys():
    lang = Language.english()
    for key in ("narrator", "female-1", "male-1"):
        assert key in lang.tts_voice_map, f"missing key: {key}"


# ── SRSItem ───────────────────────────────────────────────────────────────


def _make_srs_item() -> SRSItem:
    unit = SyntacticUnit(text="dober dan", translation="good day", word_count=2, difficulty=1, source="corpus")
    return SRSItem(syntactic_unit=unit, due_date=date.today())


def test_srs_item_initial_state_is_new():
    item = _make_srs_item()
    assert item.state == SRSState.NEW


def test_srs_item_initial_reps_zero():
    item = _make_srs_item()
    assert item.reps == 0
    assert item.lapses == 0


def test_srs_item_rating_values():
    assert Rating.AGAIN.value == 1
    assert Rating.HARD.value == 2
    assert Rating.GOOD.value == 3
    assert Rating.EASY.value == 4


def test_srs_item_state_enum_values():
    assert SRSState.NEW.value == "new"
    assert SRSState.LEARNING.value == "learning"
    assert SRSState.REVIEW.value == "review"
    assert SRSState.RELEARNING.value == "relearning"


# ── Lesson serialization ───────────────────────────────────────────────────


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


def test_lesson_serializes_to_json():
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


def test_lesson_roundtrip_json():
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
