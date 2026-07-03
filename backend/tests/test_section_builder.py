"""Tests for the mechanical section builder."""

from app.generation.section_builder import (
    SECTION_TITLES,
    build_key_phrases_section,
    build_natural_speed_section,
    build_slow_speed_section,
    build_translated_section,
    build_word_breakdown,
)
from app.models.lesson import SectionType

# ── build_word_breakdown ──────────────────────────────────────────────────


def test_build_word_breakdown_empty():
    assert build_word_breakdown("") == []


def test_build_word_breakdown_single_word():
    # "dan" is a single syllable — just repeat
    assert build_word_breakdown("dan") == ["dan", "dan"]


def test_build_word_breakdown_single_multisyllable_word():
    # "prosim" → ["pro", "sim"]; breakdown does backward syllable buildup
    assert build_word_breakdown("prosim") == [
        "prosim",  # full phrase
        "sim",  # last syllable
        "pro",  # first syllable
        "prosim",  # rebuilt word
        "prosim",  # final repeat
    ]


def test_build_word_breakdown_two_words():
    # "dober dan": "dan" is single-syllable; "dober" → ["do", "ber"]
    assert build_word_breakdown("dober dan") == [
        "dober dan",  # full phrase
        "dan",  # last word (single syllable)
        "ber",  # last syllable of "dober"
        "do",  # first syllable
        "dober",  # rebuilt word
        "dober dan",  # full phrase
        "dober dan",  # final repeat
    ]


def test_build_word_breakdown_three_words():
    # "eno kavo prosim": prosim→[pro,sim], kavo→[ka,vo], eno→[e,no]
    assert build_word_breakdown("eno kavo prosim") == [
        "eno kavo prosim",  # full phrase
        "sim",  # last syllable of prosim
        "pro",  # first syllable
        "prosim",  # rebuilt
        "vo",  # last syllable of kavo
        "ka",  # first syllable
        "kavo",  # rebuilt
        "kavo prosim",  # partial phrase
        "no",  # last syllable of eno
        "e",  # first syllable
        "eno",  # rebuilt
        "eno kavo prosim",  # full phrase
        "eno kavo prosim",  # final repeat
    ]


def test_build_word_breakdown_starts_with_full_phrase():
    result = build_word_breakdown("hvala lepa")
    assert result[0] == "hvala lepa"


def test_build_word_breakdown_ends_with_full_phrase_twice():
    result = build_word_breakdown("hvala lepa")
    assert result[-1] == "hvala lepa"
    assert result[-2] == "hvala lepa"


def test_build_word_breakdown_whitespace_normalized():
    assert build_word_breakdown("  eno   kavo  ") == build_word_breakdown("eno kavo")


# ── build_key_phrases_section ─────────────────────────────────────────────

_KEY_PHRASES = [
    {"phrase": "dober dan", "translation": "good day"},
]

_VOICE_MAP = {
    "narrator": "en-US-GuyNeural",
    "female-1": "sl-SI-PetraNeural",
    "female-2": "sl-SI-PetraNeural",
    "male-1": "sl-SI-RokNeural",
}

NARRATOR_VOICE = "en-US-GuyNeural"
L2_CODE = "sl"


def test_key_phrases_section_structure():
    """Section should have: L2 phrase, narrator translation, L2 repeat, breakdown words, full phrase."""
    section = build_key_phrases_section(_KEY_PHRASES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    assert section.section_type == SectionType.KEY_PHRASES
    texts = [p.text for p in section.phrases]
    # Must contain the phrase (at least twice) and translation
    assert "dober dan" in texts
    assert "good day" in texts
    assert texts.count("dober dan") >= 2


def test_key_phrases_uses_female_1_only():
    """All L2 phrases should use the female-1 voice."""
    section = build_key_phrases_section(_KEY_PHRASES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    l2_phrases = [p for p in section.phrases if p.language_code == L2_CODE]
    for phrase in l2_phrases:
        assert phrase.voice_id == _VOICE_MAP["female-1"]


def test_key_phrases_narrator_uses_english():
    """Narrator phrases should have narrator voice and role='narrator'."""
    section = build_key_phrases_section(_KEY_PHRASES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    narrator_phrases = [p for p in section.phrases if p.role == "narrator"]
    assert len(narrator_phrases) >= 1
    for phrase in narrator_phrases:
        assert phrase.voice_id == NARRATOR_VOICE


# ── build_natural_speed_section ───────────────────────────────────────────

_SCENES = [
    {
        "label": "At the Riverside Café",
        "lines": [
            {"speaker": "female-1", "text": "Dober dan!", "translation": "Good day!"},
            {"speaker": "male-1", "text": "Prosim kavo.", "translation": "A coffee please."},
        ],
    }
]


def test_natural_speed_has_scene_labels():
    section = build_natural_speed_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    assert section.section_type == SectionType.NATURAL_SPEED
    narrator_phrases = [p for p in section.phrases if p.role == "narrator"]
    assert any("Riverside" in p.text for p in narrator_phrases)


def test_natural_speed_resolves_speaker_to_voice():
    section = build_natural_speed_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    female_phrases = [p for p in section.phrases if p.role == "female-1"]
    assert all(p.voice_id == _VOICE_MAP["female-1"] for p in female_phrases)


def test_natural_speed_preserves_dialogue_order():
    section = build_natural_speed_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    dialogue = [p for p in section.phrases if p.role != "narrator"]
    assert dialogue[0].text == "Dober dan!"
    assert dialogue[1].text == "Prosim kavo."


# ── build_slow_speed_section ─────────────────────────────────────────────


def test_slow_speed_mirrors_natural_speed_line_count():
    nat = build_natural_speed_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    slow = build_slow_speed_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    nat_dialogue = [p for p in nat.phrases if p.role != "narrator"]
    slow_dialogue = [p for p in slow.phrases if p.role != "narrator"]
    assert len(slow_dialogue) == len(nat_dialogue)


def test_slow_speed_adds_ellipsis_between_words():
    section = build_slow_speed_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    dialogue = [p for p in section.phrases if p.role != "narrator"]
    assert " ... " in dialogue[0].text
    assert dialogue[0].text == "Dober ... dan!"


def test_slow_speed_scene_labels_not_slowed():
    section = build_slow_speed_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    narrator_phrases = [p for p in section.phrases if p.role == "narrator"]
    # narrator_phrases[0] is the section title; scene label is at [1]
    assert narrator_phrases[0].text == "Slow Speed"
    assert narrator_phrases[1].text == "At the Riverside Café"


# ── build_translated_section ─────────────────────────────────────────────


def test_translated_interleaves_narrator():
    section = build_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    assert section.section_type == SectionType.TRANSLATED
    # Skip section title and scene label; then L2, narrator, L2, narrator...
    body = [p for p in section.phrases if p.text not in ("Translated", "At the Riverside Café")]
    for i, phrase in enumerate(body):
        if i % 2 == 0:
            assert phrase.language_code == L2_CODE
        else:
            assert phrase.role == "narrator"


def test_translated_preserves_scene_labels():
    section = build_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    narrator_phrases = [p for p in section.phrases if p.role == "narrator"]
    scene_labels = [p for p in narrator_phrases if "Riverside" in p.text]
    assert len(scene_labels) == 1


# ── Section title phrases ─────────────────────────────────────────────────


def test_section_titles_maps_all_types():
    assert set(SECTION_TITLES.keys()) == set(SectionType)


def test_key_phrases_section_starts_with_title_phrase():
    section = build_key_phrases_section(_KEY_PHRASES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    first = section.phrases[0]
    assert first.text == "Key Phrases"
    assert first.role == "narrator"
    assert first.voice_id == NARRATOR_VOICE
    assert first.language_code == "en"


def test_natural_speed_section_starts_with_title_phrase():
    section = build_natural_speed_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    first = section.phrases[0]
    assert first.text == "Natural Speed"
    assert first.role == "narrator"
    assert first.voice_id == NARRATOR_VOICE
    assert first.language_code == "en"


def test_slow_speed_section_starts_with_title_phrase():
    section = build_slow_speed_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    first = section.phrases[0]
    assert first.text == "Slow Speed"
    assert first.role == "narrator"
    assert first.voice_id == NARRATOR_VOICE
    assert first.language_code == "en"


def test_translated_section_starts_with_title_phrase():
    section = build_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    first = section.phrases[0]
    assert first.text == "Translated"
    assert first.role == "narrator"
    assert first.voice_id == NARRATOR_VOICE
    assert first.language_code == "en"


# ── Malformed-input resilience (backlog #5) ──────────────────────────────


def test_key_phrases_skips_missing_fields():
    """A key phrase entry missing phrase/translation or a non-dict entry is skipped; good ones survive."""
    items = [
        {"phrase": "hvala", "translation": "thank you"},
        {"phrase": "", "translation": "empty"},
        {"not_a_phrase": "broken"},
        42,
        {"phrase": "prosim", "translation": "please"},
    ]
    section = build_key_phrases_section(items, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    texts = [p.text for p in section.phrases]
    assert "hvala" in texts
    assert "prosim" in texts
    assert "empty" not in texts


def test_natural_speed_skips_malformed_scene_and_line():
    """A scene missing its label or a line missing speaker/text is skipped, plus non-dict entries."""
    scenes = [
        {"label": "Good", "lines": [{"speaker": "f1", "text": "Dober dan", "translation": "Good day"}]},
        {"not_a_label": 42, "lines": []},
        {"label": "", "lines": [{"speaker": "f1", "text": "Empty label"}]},
        42,
        {
            "label": "Bad lines",
            "lines": [
                {"speaker": "f1", "text": "Hello", "translation": "Zdravo"},
                {"speaker": "", "text": "No speaker"},
                {"speaker": "f1", "text": "", "translation": "No text"},
                "not a dict",
            ],
        },
    ]
    section = build_natural_speed_section(scenes, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    texts = [p.text for p in section.phrases]
    assert "Good" in texts  # scene label
    assert "Dober dan" in texts
    assert "Hello" in texts
    assert "Empty label" not in texts
    assert "Bad lines" in texts
    assert "No speaker" not in texts
    assert "No text" not in texts


def test_slow_speed_skips_malformed_line():
    """Slow-speed builder skips malformed scenes and lines (non-dict, missing label, missing fields)."""
    scenes = [
        {"label": "Scene", "lines": [{"speaker": "f1", "text": "Kava prosim", "translation": "Coffee please"}]},
        {"not_a_label": True},
        {"label": "", "lines": []},
        42,
        {
            "label": "Bad lines",
            "lines": [
                {"missing": "speaker"},
                {"speaker": "f1", "text": ""},
                "not a dict",
            ],
        },
    ]
    section = build_slow_speed_section(scenes, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    texts = [p.text for p in section.phrases]
    assert "Kava ... prosim" in texts
    assert "Scene" in texts
    assert "Bad lines" in texts


def test_translated_skips_line_without_translation():
    """Translated-section builder skips malformed scenes and lines (non-dict, missing fields)."""
    scenes = [
        {"label": "S1", "lines": [{"speaker": "f1", "text": "Dober dan", "translation": "Good day"}]},
        {"not_a_label": True},
        {"label": "", "lines": []},
        42,
        {
            "label": "S2",
            "lines": [
                {"speaker": "f1", "text": "Has it", "translation": "Ima"},
                {"speaker": "f1", "text": "No translation"},
                "not a dict",
            ],
        },
    ]
    section = build_translated_section(scenes, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    l2_texts = [p.text for p in section.phrases if p.language_code == "sl"]
    assert "Dober dan" in l2_texts
    assert "Has it" in l2_texts
    assert "No translation" not in l2_texts
