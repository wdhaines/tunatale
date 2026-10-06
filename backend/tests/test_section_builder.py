"""Tests for the mechanical section builder."""

import pytest

from app.generation.section_builder import (
    SECTION_TITLES,
    _resolve_voice,
    build_en_translated_section,
    build_key_phrases_section,
    build_natural_speed_section,
    build_slow_en_translated_section,
    build_slow_speed_section,
    build_slow_translated_section,
    build_translated_section,
    build_word_breakdown,
    key_phrase_groups,
)
from app.models.lesson import Phrase, Section, SectionType

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
        "prosim",  # rebuilt word — the closing rung, said ONCE
    ]


def test_build_word_breakdown_two_words():
    # "dober dan": "dan" is single-syllable; "dober" → ["do", "ber"]
    assert build_word_breakdown("dober dan") == [
        "dober dan",  # full phrase
        "dan",  # last word (single syllable)
        "ber",  # last syllable of "dober"
        "do",  # first syllable
        "dober",  # rebuilt word
        "dober dan",  # closing rung, said ONCE
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
        "eno kavo prosim",  # closing rung, said ONCE
    ]


def test_build_word_breakdown_starts_with_full_phrase():
    result = build_word_breakdown("hvala lepa")
    assert result[0] == "hvala lepa"


def test_build_word_breakdown_ends_with_full_phrase_once():
    """The buildup closes on the whole phrase, and does not repeat it.

    This test used to be named `..._twice` and assert `result[-2]` was ALSO the
    phrase. That was not a decision anyone made — the builder appended the
    closing rung twice (once at `word_index == 0`, once after the loop), and the
    test was written to whatever came out. The learner heard the whole phrase
    twice with a ~2.1 s gap and nothing in between; measured on the live `no`
    deck 2026-09-05, 5-8 times per lesson.

    The compound path never had the second append and closes on one rung, which
    is what `test_breakdown_compound_full_golden_sequence` pins as
    "human-confirmed line-for-line" — that is the discriminator that made this a
    bug rather than the design.
    """
    result = build_word_breakdown("hvala lepa")
    assert result[-1] == "hvala lepa"
    assert result[-2] != "hvala lepa"


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


def test_key_phrases_use_a_dedicated_key_phrases_voice_when_the_map_names_one():
    """tunatale-w4m7.16: Tagalog's breakdown is voiced outside the dialogue cast,
    by a voice that honours IPA. Every L2 line of the section, not just the
    fragments, so one voice says the whole drill."""
    voices = {**_VOICE_MAP, "key-phrases": "de-DE-FlorianMultilingualNeural"}
    section = build_key_phrases_section(_KEY_PHRASES, voices, NARRATOR_VOICE, L2_CODE)
    l2_voices = {p.voice_id for p in section.phrases if p.language_code == L2_CODE}
    assert l2_voices == {"de-DE-FlorianMultilingualNeural"}


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


def test_slow_speed_stores_each_line_as_written():
    """An Enunciated section stores the natural line (tunatale-tyfk).

    It used to store ``Dober ... dan!`` and send that to the voice as text. The
    pauses are now made at render time, by the provider's own means, from the
    line itself — so what is stored is the line, the same one natural speed has.
    """
    section = build_slow_speed_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    natural = build_natural_speed_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    dialogue = [p for p in section.phrases if p.role != "narrator"]
    assert dialogue[0].text == "Dober dan!"
    assert [p.text for p in dialogue] == [p.text for p in natural.phrases if p.role != "narrator"]


def test_slow_speed_scene_labels_not_slowed():
    section = build_slow_speed_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    narrator_phrases = [p for p in section.phrases if p.role == "narrator"]
    # narrator_phrases[0] is the section title; scene label is at [1]
    assert narrator_phrases[0].text == "Enunciated"
    assert narrator_phrases[1].text == "At the Riverside Café"


# ── build_translated_section ─────────────────────────────────────────────


def test_translated_interleaves_narrator():
    section = build_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    assert section.section_type == SectionType.TRANSLATED
    # Skip section title and scene label; then L2, narrator, L2, narrator...
    body = [p for p in section.phrases if p.text not in ("English After", "At the Riverside Café")]
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


def test_section_titles_say_what_happens():
    """tunatale-v3ri — the renamed set is DECIDED; regressions get no debate.

    The "Slow" pass is enunciated speech (respelled text, no rate change), and
    the English gloss sits before/after the L2 line. No value may still describe
    the slow pass.
    """
    renamed = {
        SectionType.SLOW_SPEED: "Enunciated",
        SectionType.TRANSLATED: "English After",
        SectionType.EN_TRANSLATED: "English Before",
        SectionType.SLOW_TRANSLATED: "Enunciated, English After",
        SectionType.SLOW_EN_TRANSLATED: "Enunciated, English Before",
    }
    for sec_type, title in renamed.items():
        assert SECTION_TITLES[sec_type] == title
    for title in SECTION_TITLES.values():
        assert "Slow" not in title, f"{title!r} still describes the slow pass"
    assert SECTION_TITLES[SectionType.KEY_PHRASES] == "Key Phrases"
    assert SECTION_TITLES[SectionType.NATURAL_SPEED] == "Natural Speed"


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
    assert first.text == "Enunciated"
    assert first.role == "narrator"
    assert first.voice_id == NARRATOR_VOICE
    assert first.language_code == "en"


def test_translated_section_starts_with_title_phrase():
    section = build_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    first = section.phrases[0]
    assert first.text == "English After"
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
        {"label": "Good", "lines": [{"speaker": "female-1", "text": "Dober dan", "translation": "Good day"}]},
        {"not_a_label": 42, "lines": []},
        {"label": "", "lines": [{"speaker": "female-1", "text": "Empty label"}]},
        42,
        {
            "label": "Bad lines",
            "lines": [
                {"speaker": "female-1", "text": "Hello", "translation": "Zdravo"},
                {"speaker": "", "text": "No speaker"},
                {"speaker": "female-1", "text": "", "translation": "No text"},
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
        {"label": "Scene", "lines": [{"speaker": "female-1", "text": "Kava prosim", "translation": "Coffee please"}]},
        {"not_a_label": True},
        {"label": "", "lines": []},
        42,
        {
            "label": "Bad lines",
            "lines": [
                {"missing": "speaker"},
                {"speaker": "female-1", "text": ""},
                "not a dict",
            ],
        },
    ]
    section = build_slow_speed_section(scenes, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    texts = [p.text for p in section.phrases]
    assert "Kava prosim" in texts
    assert "Scene" in texts
    assert "Bad lines" in texts


# ── Norwegian dispatch ──────────────────────────────────────────────────────


L2_CODE_NO = "no"


def test_build_word_breakdown_routes_norwegian():
    """build_word_breakdown with l2_code='no' should route to build_norwegian_breakdown."""
    result = build_word_breakdown("etterforskningsteamet", "no")
    assert result[0] == "etterforskningsteamet"
    assert result[-1] == "etterforskningsteamet"
    # Compound parts should appear
    assert "etterforsknings" in result or any("etterforsknings" in r for r in result)
    assert "team" in " ".join(result)
    assert "et" in " ".join(result)


def test_build_word_breakdown_norwegian_non_compound():
    """Single-stem Norwegian words use morpheme-aware syllabification dispatch."""
    result = build_word_breakdown("jeg", "no")
    assert result == ["jeg", "jeg"]


def test_build_word_breakdown_norwegian_multi_word():
    result = build_word_breakdown("p\u00e5 plassen", "no")
    assert result[0] == "p\u00e5 plassen"
    assert result[-1] == "p\u00e5 plassen"
    assert "plassen" in result or any("plassen" in r for r in result)


def test_slovene_behavior_unchanged():
    """Slovene ('sl') should continue to use the classic syllable buildup."""
    result = build_word_breakdown("prosim", "sl")
    assert result == ["prosim", "sim", "pro", "prosim"]


def test_slow_speed_stores_a_norwegian_compound_whole():
    """The compound's cut is not stored either. It is made when the line is
    rendered (``test_renderer_enunciation``), so a better rule reaches every
    lesson on its next render instead of only the ones generated after it."""
    slow_text = build_slow_speed_section(
        [{"label": "Test", "lines": [{"speaker": "female-1", "text": "Flyplassen er her.", "translation": "x"}]}],
        _VOICE_MAP,
        NARRATOR_VOICE,
        "no",
    )
    texts = [p.text for p in slow_text.phrases if p.role != "narrator"]
    assert texts == ["Flyplassen er her."]


# ── Malformed-input resilience (backlog #5) ──────────────────────────────


def test_translated_skips_line_without_translation():
    """build_translated_section skips malformed scenes and lines (non-dict, missing fields)."""
    scenes = [
        {"label": "S1", "lines": [{"speaker": "female-1", "text": "Dober dan", "translation": "Good day"}]},
        {"not_a_label": True},
        {"label": "", "lines": []},
        42,
        {
            "label": "S2",
            "lines": [
                {"speaker": "female-1", "text": "Has it", "translation": "Ima"},
                {"speaker": "female-1", "text": "No translation"},
                "not a dict",
            ],
        },
    ]
    section = build_translated_section(scenes, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    l2_texts = [p.text for p in section.phrases if p.language_code == "sl"]
    assert "Dober dan" in l2_texts
    assert "Has it" in l2_texts
    assert "No translation" not in l2_texts


# ── build_slow_translated_section ────────────────────────────────────────


def test_slow_translated_section_type():
    section = build_slow_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    assert section.section_type == SectionType.SLOW_TRANSLATED


def test_slow_translated_starts_with_title_phrase():
    section = build_slow_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    first = section.phrases[0]
    assert first.text == "Enunciated, English After"
    assert first.role == "narrator"
    assert first.voice_id == NARRATOR_VOICE
    assert first.language_code == "en"


def test_slow_translated_stores_each_line_as_written():
    section = build_slow_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    l2_phrases = [p for p in section.phrases if p.language_code == L2_CODE]
    assert [p.text for p in l2_phrases] == ["Dober dan!", "Prosim kavo."]


def test_slow_translated_interleaves_narrator_after_l2():
    section = build_slow_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    body = [p for p in section.phrases if p.text not in ("Enunciated, English After", "At the Riverside Café")]
    for i, phrase in enumerate(body):
        if i % 2 == 0:
            assert phrase.language_code == L2_CODE
        else:
            assert phrase.role == "narrator"
            assert phrase.language_code == "en"


def test_slow_translated_preserves_scene_labels():
    section = build_slow_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    narrator_phrases = [p for p in section.phrases if p.role == "narrator"]
    scene_labels = [p for p in narrator_phrases if "Riverside" in p.text]
    assert len(scene_labels) == 1


def test_slow_translated_skips_line_without_translation():
    """Lines without translation are skipped, same as translated."""
    scenes = [
        {
            "label": "Scene",
            "lines": [
                {"speaker": "female-1", "text": "Dober dan", "translation": "Good day"},
                {"speaker": "female-1", "text": "No translation here"},
                {"speaker": "female-1", "text": "Prosim kavo", "translation": "A coffee please"},
            ],
        }
    ]
    section = build_slow_translated_section(scenes, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    l2_texts = [p.text for p in section.phrases if p.language_code == L2_CODE]
    assert any("Dober" in t for t in l2_texts)
    assert any("Prosim" in t for t in l2_texts)
    assert not any("No translation" in t for t in l2_texts)


def test_slow_translated_skips_malformed_input():
    """Malformed scenes/lines are skipped without crashing."""
    scenes = [
        {"label": "Good", "lines": [{"speaker": "female-1", "text": "Dober dan", "translation": "Good day"}]},
        {"not_a_label": True},
        {"label": "", "lines": []},
        42,
        {
            "label": "Bad",
            "lines": [
                {"speaker": "female-1", "text": "Kava prosim", "translation": "Coffee please"},
                {"missing": "speaker"},
                {"speaker": "female-1", "text": ""},
                "not a dict",
            ],
        },
    ]
    section = build_slow_translated_section(scenes, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    texts = [p.text for p in section.phrases]
    assert "Good" in texts
    assert "Bad" in texts
    l2_texts = [p.text for p in section.phrases if p.language_code == L2_CODE]
    assert len(l2_texts) == 2


def test_slow_translated_mirrors_translated_line_count():
    """Slow translated has the same L2 line count as translated (both skip missing translations)."""
    nat = build_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    slow = build_slow_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    nat_l2 = [p for p in nat.phrases if p.language_code == L2_CODE]
    slow_l2 = [p for p in slow.phrases if p.language_code == L2_CODE]
    assert len(slow_l2) == len(nat_l2)


# ── build_en_translated_section (English-first) ──────────────────────────


def test_en_translated_section_type():
    section = build_en_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    assert section.section_type == SectionType.EN_TRANSLATED


def test_en_translated_starts_with_title_phrase():
    section = build_en_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    first = section.phrases[0]
    assert first.text == "English Before"
    assert first.role == "narrator"
    assert first.voice_id == NARRATOR_VOICE
    assert first.language_code == "en"


def test_en_translated_narrator_before_l2():
    """Each dialogue line is the English narrator translation FIRST, then the L2 line."""
    section = build_en_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    body = [p for p in section.phrases if p.text not in ("English Before", "At the Riverside Café")]
    for i, phrase in enumerate(body):
        if i % 2 == 0:
            assert phrase.role == "narrator"
            assert phrase.language_code == "en"
        else:
            assert phrase.language_code == L2_CODE
    # First line: English "Good day!" narration precedes the L2 "Dober dan!".
    assert body[0].text == "Good day!"
    assert body[1].text == "Dober dan!"


def test_en_translated_preserves_scene_labels():
    section = build_en_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    narrator_phrases = [p for p in section.phrases if p.role == "narrator"]
    scene_labels = [p for p in narrator_phrases if "Riverside" in p.text]
    assert len(scene_labels) == 1


def test_en_translated_mirrors_translated_line_count():
    en = build_en_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    l2 = build_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    en_l2 = [p for p in en.phrases if p.language_code == L2_CODE]
    l2_l2 = [p for p in l2.phrases if p.language_code == L2_CODE]
    assert len(en_l2) == len(l2_l2)


def test_en_translated_skips_line_without_translation():
    scenes = [
        {
            "label": "Scene",
            "lines": [
                {"speaker": "female-1", "text": "Dober dan", "translation": "Good day"},
                {"speaker": "female-1", "text": "No translation here"},
                {"speaker": "female-1", "text": "Prosim kavo", "translation": "A coffee please"},
            ],
        }
    ]
    section = build_en_translated_section(scenes, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    l2_texts = [p.text for p in section.phrases if p.language_code == L2_CODE]
    assert "Dober dan" in l2_texts
    assert "Prosim kavo" in l2_texts
    assert "No translation here" not in l2_texts


def test_en_translated_skips_malformed_input():
    scenes = [
        {"label": "Good", "lines": [{"speaker": "female-1", "text": "Dober dan", "translation": "Good day"}]},
        {"not_a_label": True},
        {"label": "", "lines": []},
        42,
        {
            "label": "Bad",
            "lines": [
                {"speaker": "female-1", "text": "Kava prosim", "translation": "Coffee please"},
                {"missing": "speaker"},
                {"speaker": "female-1", "text": ""},
                "not a dict",
            ],
        },
    ]
    section = build_en_translated_section(scenes, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    texts = [p.text for p in section.phrases]
    assert "Good" in texts
    assert "Bad" in texts
    l2_texts = [p.text for p in section.phrases if p.language_code == L2_CODE]
    assert len(l2_texts) == 2


# ── build_slow_en_translated_section (English-first, slowed L2) ───────────


def test_slow_en_translated_section_type():
    section = build_slow_en_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    assert section.section_type == SectionType.SLOW_EN_TRANSLATED


def test_slow_en_translated_starts_with_title_phrase():
    section = build_slow_en_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    first = section.phrases[0]
    assert first.text == "Enunciated, English Before"
    assert first.role == "narrator"
    assert first.language_code == "en"


def test_slow_en_translated_narrator_before_l2():
    section = build_slow_en_translated_section(_SCENES, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    body = [p for p in section.phrases if p.text not in ("Enunciated, English Before", "At the Riverside Café")]
    for i, phrase in enumerate(body):
        if i % 2 == 0:
            assert phrase.role == "narrator"
            assert phrase.language_code == "en"
        else:
            assert phrase.language_code == L2_CODE
    l2_phrases = [p for p in section.phrases if p.language_code == L2_CODE]
    assert l2_phrases[0].text == "Dober dan!"
    assert l2_phrases[1].text == "Prosim kavo."


def test_slow_en_translated_skips_line_without_translation():
    scenes = [
        {
            "label": "Scene",
            "lines": [
                {"speaker": "female-1", "text": "Dober dan", "translation": "Good day"},
                {"speaker": "female-1", "text": "No translation here"},
            ],
        }
    ]
    section = build_slow_en_translated_section(scenes, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    l2_texts = [p.text for p in section.phrases if p.language_code == L2_CODE]
    assert l2_texts == ["Dober dan"]


def test_slow_en_translated_skips_malformed_input():
    scenes = [
        {"label": "Good", "lines": [{"speaker": "female-1", "text": "Dober dan", "translation": "Good day"}]},
        {"not_a_label": True},
        {"label": "", "lines": []},
        42,
        {
            "label": "Bad",
            "lines": [
                {"speaker": "female-1", "text": "Kava prosim", "translation": "Coffee please"},
                {"missing": "speaker"},
                {"speaker": "female-1", "text": ""},
                "not a dict",
            ],
        },
    ]
    section = build_slow_en_translated_section(scenes, _VOICE_MAP, NARRATOR_VOICE, L2_CODE)
    texts = [p.text for p in section.phrases]
    assert "Good" in texts
    assert "Bad" in texts
    l2_texts = [p.text for p in section.phrases if p.language_code == L2_CODE]
    assert len(l2_texts) == 2


# ── slow-word resolution ──────────────────────────────────────────────────


def test_resolve_voice_raises_for_unknown_speaker():
    """An unknown speaker is a loud failure (ValueError), not a silent swap.

    Until rag.6, ``_resolve_voice`` returned the map's ``female-1`` for any
    unknown role, so a 'female-3' line was simply spoken by the female lead —
    the silent-collapse defect the role widening exists to remove. Returning a
    value at all is the failure mode: pytest.raises fails the test unless an
    exception is raised, so no substituted voice can pass here.
    """
    voice_map = {
        "narrator": "en-US-GuyNeural",
        "female-1": "sl-SI-PetraNeural",
        "male-1": "sl-SI-RokNeural",
    }
    with pytest.raises(ValueError, match="ghost-9"):
        _resolve_voice("ghost-9", voice_map)


def _register_slow_word_only(monkeypatch, slow_fn):
    """Register a throwaway language whose ONLY wiring is a slow-word function."""
    from app.languages import _CONFIGS as configs
    from app.languages import LanguageConfig, discover
    from app.models.language import Language

    discover()
    monkeypatch.setitem(
        configs,
        "zz",
        LanguageConfig(
            language=Language(code="zz", name="Test", native_name="Test", script="latin"),
            slow_word_fn=slow_fn,
        ),
    )


@pytest.mark.parametrize(
    "build",
    [build_slow_speed_section, build_slow_translated_section, build_slow_en_translated_section],
)
def test_no_enunciated_builder_applies_the_languages_cut(monkeypatch, build):
    """The cut belongs to the renderer, and to the renderer alone. A builder
    that also applied it would store ``<en> <to>``, the renderer would cut that
    again, and the two rules would have to agree forever."""
    _register_slow_word_only(monkeypatch, lambda w: f"<{w}>")
    scenes = [{"label": "S", "lines": [{"speaker": "female-1", "text": "en to", "translation": "one two"}]}]

    section = build(scenes, {"female-1": "v"}, "narr", "zz")

    assert [p.text for p in section.phrases if p.language_code == "zz"] == ["en to"]


# ── key_phrase_groups ───────────────────────────────────────────────────
#
# The inverse of build_key_phrases_section. It reads the section's own shape
# rather than re-running today's breakdown rules, so a lesson stored under
# older rules still segments correctly. A group HEAD is an L2 phrase whose
# SUCCESSOR is a narrator phrase; a group runs to the next head, or the end.


def _kp_section(*, chunks_per_kp: list[int], l2: str = "no", narrator_l2: bool = False) -> Section:
    """A hand-built KEY_PHRASES section: title, then (L2, translation, chunks…) per key phrase.

    *narrator_l2* puts the translation in the L2 language while still marking it
    ``role="narrator"`` — the shape some stored lessons actually have, and the
    case where "is L2" alone is not enough to recognise a head.
    """
    phrases = [Phrase(text="Key Phrases", voice_id="en", language_code="en", role="narrator")]
    for kp, n_chunks in enumerate(chunks_per_kp):
        phrases.append(Phrase(text=f"kp{kp}", voice_id="l2", language_code=l2))
        phrases.append(
            Phrase(text=f"tr{kp}", voice_id="en", language_code=l2 if narrator_l2 else "en", role="narrator")
        )
        phrases.extend(Phrase(text=f"c{kp}_{c}", voice_id="l2", language_code=l2) for c in range(n_chunks))
    return Section(section_type=SectionType.KEY_PHRASES, phrases=phrases)


def test_key_phrase_groups_title_only_section_is_empty():
    assert key_phrase_groups(_kp_section(chunks_per_kp=[]), "no") == []


def test_key_phrase_groups_key_phrase_with_zero_chunks_is_a_valid_group():
    groups = key_phrase_groups(_kp_section(chunks_per_kp=[0]), "no")
    assert groups == [range(1, 3)]
    assert len(groups[0]) == 2  # head + translation, no breakdown at all


def test_key_phrase_groups_three_key_phrases_with_3_0_5_chunks():
    groups = key_phrase_groups(_kp_section(chunks_per_kp=[3, 0, 5]), "no")
    assert groups == [range(1, 6), range(6, 8), range(8, 15)]
    assert [len(g) for g in groups] == [5, 2, 7]  # 2 + chunks


def test_key_phrase_groups_l2_chunk_followed_by_l2_chunk_is_not_a_head():
    """Chunks are plain L2 phrases, so a chunk+chunk pair must stay inside its group."""
    assert key_phrase_groups(_kp_section(chunks_per_kp=[2]), "no") == [range(1, 5)]


def test_key_phrase_groups_last_l2_phrase_has_no_successor_so_is_not_a_head():
    """The section's final phrase cannot be a head — a head needs a narrator after it.

    It is a chunk of the last group, which is how a stored section carrying one
    more chunk than today's rules would read.
    """
    section = _kp_section(chunks_per_kp=[0])
    section.phrases.append(Phrase(text="stray", voice_id="l2", language_code="no"))
    assert key_phrase_groups(section, "no") == [range(1, 4)]


def test_key_phrase_groups_english_non_narrator_phrase_is_not_a_head():
    """An English phrase with no narrator role, even followed by a narrator, is not a head."""
    section = _kp_section(chunks_per_kp=[0])
    section.phrases.insert(1, Phrase(text="en stray", voice_id="en", language_code="en"))
    assert key_phrase_groups(section, "no") == [range(2, 4)]


def test_key_phrase_groups_narrator_followed_by_narrator_is_not_a_head():
    """A narrator phrase preceded by a narrator phrase is not a head.

    Guards the inverse error: an implementation that treats a narrator phrase as
    a head on the strength of its ROLE alone, without the L2 check. The successor
    test alone is not enough either.
    """
    section = _kp_section(chunks_per_kp=[1])
    section.phrases.insert(1, Phrase(text="scene label", voice_id="en", language_code="en", role="narrator"))
    # 0 title(en,narrator) 1 scene label(en,narrator) 2 kp0(no) 3 tr0(en,narrator) 4 c0_0(no)
    assert key_phrase_groups(section, "no") == [range(2, 5)]


def test_key_phrase_groups_l2_code_translation_keeps_its_group():
    """A translation carrying the L2 language code does not add a group.

    The head test is ``language_code == l2_code`` plus a narrator successor. A
    translation is a narrator phrase, so the real builder's ``en`` translation is
    never a head — but a stored section can carry ``language_code == l2`` on it
    (the shape the UPOS fixtures use), and as long as nothing narrator-role
    follows, it stays inside its group rather than splitting it in two.
    """
    section = _kp_section(chunks_per_kp=[2], narrator_l2=True)
    assert key_phrase_groups(section, "no") == [range(1, 5)]  # 2 + 2 chunks


def test_key_phrase_groups_ignores_a_different_l2_code():
    """The same section read as another language has no heads at all."""
    section = _kp_section(chunks_per_kp=[1, 1])
    assert key_phrase_groups(section, "sl") == []


def test_key_phrase_groups_inverts_build_key_phrases_section():
    """Every group the builder produces is a contiguous run covering the section body."""
    section = build_key_phrases_section(
        [
            {"phrase": "sporet er kaldt", "translation": "the track is cold"},
            {"phrase": "jeg vil ha en kaffe", "translation": "I would like a coffee"},
        ],
        {"female-1": "nb-NO-PernilleNeural"},
        "en-US-GuyNeural",
        "no",
    )
    groups = key_phrase_groups(section, "no")
    assert groups == [range(1, 11), range(11, 25)]
    # Contiguous, ordered, and together they cover every phrase after the title.
    assert [i for g in groups for i in g] == list(range(1, len(section.phrases)))
