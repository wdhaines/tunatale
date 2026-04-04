"""Mechanical section builders for Pimsleur-style lessons.

The LLM generates creative content (key phrases + dialogue). These builders
transform that raw data into the four structured Lesson sections deterministically.
"""

from __future__ import annotations

from app.generation.syllabify import syllabify_slovene_word
from app.models.lesson import Phrase, Section, SectionType

# Type aliases for plain-dict inputs from parsed LLM JSON
KeyPhrase = dict  # {"phrase": str, "translation": str}
DialogueLine = dict  # {"speaker": str, "text": str, "translation": str}
Scene = dict  # {"label": str, "lines": list[DialogueLine]}


def _resolve_voice(speaker: str, l2_voice_map: dict[str, str], narrator_voice: str) -> str:
    return l2_voice_map.get(speaker, l2_voice_map.get("female-1", narrator_voice))


def build_word_breakdown(phrase_text: str) -> list[str]:
    """Build a Pimsleur-style syllable-level backward buildup sequence.

    Processes words right-to-left. For each multi-syllable word the syllables
    are presented backward then progressively rebuilt before moving to the
    preceding word. Single-syllable words are presented as-is.

    The sequence always starts with the full phrase and ends with the full
    phrase repeated twice.

    Examples:
        "dan"     → ["dan", "dan"]
        "prosim"  → ["prosim", "sim", "pro", "prosim", "prosim"]
        "dober dan" → ["dober dan", "dan", "ber", "do", "dober",
                        "dober dan", "dober dan"]
    """
    phrase = " ".join(phrase_text.strip().split())
    words = phrase.split()
    if not words:
        return []

    breakdown: list[str] = [phrase]

    if len(words) == 1:
        syllables = syllabify_slovene_word(words[0])
        if len(syllables) <= 1:
            breakdown.append(phrase)
            return breakdown
        for i in range(len(syllables) - 1, -1, -1):
            breakdown.append(syllables[i])
            if i < len(syllables) - 1:
                breakdown.append("".join(syllables[i:]))
        breakdown.append(phrase)
        return breakdown

    for word_index in range(len(words) - 1, -1, -1):
        word = words[word_index]
        syllables = syllabify_slovene_word(word)

        if len(syllables) > 1:
            for i in range(len(syllables) - 1, -1, -1):
                breakdown.append(syllables[i])
                if i < len(syllables) - 1:
                    breakdown.append("".join(syllables[i:]))
                elif i == 0:
                    breakdown.append("".join(syllables))
        else:
            breakdown.append(word)

        if word_index < len(words) - 1:
            partial = " ".join(words[word_index:])
            if partial != phrase:
                breakdown.append(partial)

        if word_index == 0:
            breakdown.append(phrase)

    breakdown.append(phrase)
    return breakdown


def build_key_phrases_section(
    key_phrases: list[KeyPhrase],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
) -> Section:
    """Build the KEY_PHRASES section.

    For each phrase:
    1. L2 phrase (female-1)
    2. Narrator translation
    3. L2 phrase repeat (female-1)
    4. Word breakdown steps (female-1)
    """
    female_1_voice = l2_voice_map.get("female-1", narrator_voice)
    phrases: list[Phrase] = []

    for kp in key_phrases:
        phrase_text = kp["phrase"]
        translation = kp["translation"]

        phrases.append(Phrase(text=phrase_text, voice_id=female_1_voice, language_code=l2_code))
        phrases.append(Phrase(text=translation, voice_id=narrator_voice, language_code="en", role="narrator"))
        for step in build_word_breakdown(phrase_text):
            phrases.append(Phrase(text=step, voice_id=female_1_voice, language_code=l2_code))

    return Section(section_type=SectionType.KEY_PHRASES, phrases=phrases)


def build_natural_speed_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
) -> Section:
    """Build the NATURAL_SPEED section with scene labels and multi-speaker dialogue."""
    phrases: list[Phrase] = []

    for scene in scenes:
        phrases.append(Phrase(text=scene["label"], voice_id=narrator_voice, language_code="en", role="narrator"))
        for line in scene.get("lines", []):
            speaker = line["speaker"].lower()
            voice_id = _resolve_voice(speaker, l2_voice_map, narrator_voice)
            phrases.append(Phrase(text=line["text"], voice_id=voice_id, language_code=l2_code, role=speaker))

    return Section(section_type=SectionType.NATURAL_SPEED, phrases=phrases)


def build_slow_speed_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
) -> Section:
    """Build the SLOW_SPEED section — mirrors NATURAL_SPEED with '...' between words."""
    phrases: list[Phrase] = []

    for scene in scenes:
        phrases.append(Phrase(text=scene["label"], voice_id=narrator_voice, language_code="en", role="narrator"))
        for line in scene.get("lines", []):
            speaker = line["speaker"].lower()
            voice_id = _resolve_voice(speaker, l2_voice_map, narrator_voice)
            slowed = " ... ".join(line["text"].split())
            phrases.append(Phrase(text=slowed, voice_id=voice_id, language_code=l2_code, role=speaker))

    return Section(section_type=SectionType.SLOW_SPEED, phrases=phrases)


def build_translated_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
) -> Section:
    """Build the TRANSLATED section — every L2 line followed by narrator translation."""
    phrases: list[Phrase] = []

    for scene in scenes:
        phrases.append(Phrase(text=scene["label"], voice_id=narrator_voice, language_code="en", role="narrator"))
        for line in scene.get("lines", []):
            speaker = line["speaker"].lower()
            voice_id = _resolve_voice(speaker, l2_voice_map, narrator_voice)
            phrases.append(Phrase(text=line["text"], voice_id=voice_id, language_code=l2_code, role=speaker))
            phrases.append(
                Phrase(text=line["translation"], voice_id=narrator_voice, language_code="en", role="narrator")
            )

    return Section(section_type=SectionType.TRANSLATED, phrases=phrases)
