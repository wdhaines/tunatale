"""Mechanical section builders for Pimsleur-style lessons.

The LLM generates creative content (key phrases + dialogue). These builders
transform that raw data into the four structured Lesson sections deterministically.
"""

from __future__ import annotations

import logging

from app.generation.syllabify import syllabify_word
from app.languages import get_breakdown, get_slow_word, uses_compound_word_breakdown
from app.models.lesson import Phrase, Section, SectionType

logger = logging.getLogger(__name__)

# Type aliases for plain-dict inputs from parsed LLM JSON
KeyPhrase = dict  # {"phrase": str, "translation": str}
DialogueLine = dict  # {"speaker": str, "text": str, "translation": str}
Scene = dict  # {"label": str, "lines": list[DialogueLine]}

# Narrator-spoken section titles matching the demo format
SECTION_TITLES: dict[SectionType, str] = {
    SectionType.KEY_PHRASES: "Key Phrases",
    SectionType.NATURAL_SPEED: "Natural Speed",
    SectionType.SLOW_SPEED: "Slow Speed",
    SectionType.TRANSLATED: "Translated",
    SectionType.SLOW_TRANSLATED: "Slow Translated",
    SectionType.EN_TRANSLATED: "English Translated",
    SectionType.SLOW_EN_TRANSLATED: "Slow English Translated",
}


def _resolve_voice(speaker: str, l2_voice_map: dict[str, str], narrator_voice: str) -> str:
    return l2_voice_map.get(speaker, l2_voice_map.get("female-1", narrator_voice))


def build_word_breakdown(phrase_text: str, language_code: str = "sl") -> list[str]:
    """Build a Pimsleur-style syllable-level backward buildup sequence.

    Processes words right-to-left. For each multi-syllable word the syllables
    are presented backward then progressively rebuilt before moving to the
    preceding word. Single-syllable words are presented as-is.

    The sequence always starts with the full phrase and ends with the full
    phrase repeated twice. Syllabification uses the rules for *language_code*
    (defaults to Slovene for back-compat).

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

    # Compound/morpheme-aware breakdown (Norwegian) vs. generic syllable buildup.
    if uses_compound_word_breakdown(language_code):
        fn = get_breakdown(language_code)
        return fn(phrase)

    breakdown: list[str] = [phrase]

    if len(words) == 1:
        syllables = syllabify_word(words[0], language_code)
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
        syllables = syllabify_word(word, language_code)

        if len(syllables) > 1:
            for i in range(len(syllables) - 1, -1, -1):
                breakdown.append(syllables[i])
                if i < len(syllables) - 1:
                    breakdown.append("".join(syllables[i:]))
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
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.KEY_PHRASES], voice_id=narrator_voice, language_code="en", role="narrator"
        )
    ]

    for kp in key_phrases:
        if not isinstance(kp, dict):
            logger.warning("Skipping non-dict key phrase: %r", kp)
            continue
        phrase_text = kp.get("phrase", "")
        translation = kp.get("translation", "")
        if not phrase_text or not translation:
            logger.warning("Skipping key phrase with missing phrase or translation: %r", kp)
            continue

        phrases.append(Phrase(text=phrase_text, voice_id=female_1_voice, language_code=l2_code))
        phrases.append(Phrase(text=translation, voice_id=narrator_voice, language_code="en", role="narrator"))
        for step in build_word_breakdown(phrase_text, l2_code):
            phrases.append(Phrase(text=step, voice_id=female_1_voice, language_code=l2_code))

    return Section(section_type=SectionType.KEY_PHRASES, phrases=phrases)


def build_natural_speed_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
) -> Section:
    """Build the NATURAL_SPEED section with scene labels and multi-speaker dialogue."""
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.NATURAL_SPEED], voice_id=narrator_voice, language_code="en", role="narrator"
        )
    ]

    for scene in scenes:
        if not isinstance(scene, dict):
            logger.warning("Skipping non-dict scene: %r", scene)
            continue
        scene_label = scene.get("label", "")
        if not scene_label:
            logger.warning("Skipping scene with missing label: %r", scene)
            continue
        phrases.append(Phrase(text=scene_label, voice_id=narrator_voice, language_code="en", role="narrator"))
        for line in scene.get("lines", []):
            if not isinstance(line, dict):
                logger.warning("Skipping non-dict dialogue line: %r", line)
                continue
            speaker = line.get("speaker", "").lower()
            text = line.get("text", "")
            if not speaker or not text:
                logger.warning("Skipping dialogue line with missing speaker or text: %r", line)
                continue
            voice_id = _resolve_voice(speaker, l2_voice_map, narrator_voice)
            phrases.append(Phrase(text=text, voice_id=voice_id, language_code=l2_code, role=speaker))

    return Section(section_type=SectionType.NATURAL_SPEED, phrases=phrases)


def build_slow_speed_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
) -> Section:
    """Build the SLOW_SPEED section — mirrors NATURAL_SPEED with '...' between words."""
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.SLOW_SPEED], voice_id=narrator_voice, language_code="en", role="narrator"
        )
    ]

    for scene in scenes:
        if not isinstance(scene, dict):
            logger.warning("Skipping non-dict scene: %r", scene)
            continue
        scene_label = scene.get("label", "")
        if not scene_label:
            logger.warning("Skipping scene with missing label: %r", scene)
            continue
        phrases.append(Phrase(text=scene_label, voice_id=narrator_voice, language_code="en", role="narrator"))
        for line in scene.get("lines", []):
            if not isinstance(line, dict):
                logger.warning("Skipping non-dict dialogue line: %r", line)
                continue
            speaker = line.get("speaker", "").lower()
            text = line.get("text", "")
            if not speaker or not text:
                logger.warning("Skipping dialogue line with missing speaker or text: %r", line)
                continue
            voice_id = _resolve_voice(speaker, l2_voice_map, narrator_voice)
            if uses_compound_word_breakdown(l2_code):
                slow_fn = get_slow_word(l2_code)
                slowed = " ... ".join(slow_fn(w) for w in text.split())
            else:
                slowed = " ... ".join(text.split())
            phrases.append(Phrase(text=slowed, voice_id=voice_id, language_code=l2_code, role=speaker))

    return Section(section_type=SectionType.SLOW_SPEED, phrases=phrases)


def _build_translated_phrases(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
    *,
    en_first: bool,
    slow: bool,
) -> list[Phrase]:
    """Shared scene-loop for the four translated section builders.

    *en_first*: ``True`` → narrator translation precedes L2 line; ``False`` → L2 first.
    *slow*: ``True`` → L2 text is '...'-separated (language-aware); ``False`` → raw.
    """
    phrases: list[Phrase] = []

    for scene in scenes:
        if not isinstance(scene, dict):
            logger.warning("Skipping non-dict scene: %r", scene)
            continue
        scene_label = scene.get("label", "")
        if not scene_label:
            logger.warning("Skipping scene with missing label: %r", scene)
            continue
        phrases.append(Phrase(text=scene_label, voice_id=narrator_voice, language_code="en", role="narrator"))
        for line in scene.get("lines", []):
            if not isinstance(line, dict):
                logger.warning("Skipping non-dict dialogue line: %r", line)
                continue
            speaker = line.get("speaker", "").lower()
            text = line.get("text", "")
            translation = line.get("translation", "")
            if not speaker or not text or not translation:
                logger.warning("Skipping dialogue line with missing speaker, text, or translation: %r", line)
                continue
            voice_id = _resolve_voice(speaker, l2_voice_map, narrator_voice)
            if slow:
                if uses_compound_word_breakdown(l2_code):
                    slow_fn = get_slow_word(l2_code)
                    l2_text = " ... ".join(slow_fn(w) for w in text.split())
                else:
                    l2_text = " ... ".join(text.split())
            else:
                l2_text = text
            narrator_phrase = Phrase(text=translation, voice_id=narrator_voice, language_code="en", role="narrator")
            l2_phrase = Phrase(text=l2_text, voice_id=voice_id, language_code=l2_code, role=speaker)
            if en_first:
                phrases.append(narrator_phrase)
                phrases.append(l2_phrase)
            else:
                phrases.append(l2_phrase)
                phrases.append(narrator_phrase)

    return phrases


def build_translated_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
) -> Section:
    """Build the TRANSLATED section — every L2 line followed by narrator translation."""
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.TRANSLATED], voice_id=narrator_voice, language_code="en", role="narrator"
        ),
        *_build_translated_phrases(scenes, l2_voice_map, narrator_voice, l2_code, en_first=False, slow=False),
    ]
    return Section(section_type=SectionType.TRANSLATED, phrases=phrases)


def build_slow_translated_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
) -> Section:
    """Build the SLOW_TRANSLATED section — slowed L2 lines with trailing narrator translation."""
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.SLOW_TRANSLATED],
            voice_id=narrator_voice,
            language_code="en",
            role="narrator",
        ),
        *_build_translated_phrases(scenes, l2_voice_map, narrator_voice, l2_code, en_first=False, slow=True),
    ]
    return Section(section_type=SectionType.SLOW_TRANSLATED, phrases=phrases)


def build_en_translated_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
) -> Section:
    """Build the EN_TRANSLATED section — narrator translation FIRST, then the L2 line."""
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.EN_TRANSLATED],
            voice_id=narrator_voice,
            language_code="en",
            role="narrator",
        ),
        *_build_translated_phrases(scenes, l2_voice_map, narrator_voice, l2_code, en_first=True, slow=False),
    ]
    return Section(section_type=SectionType.EN_TRANSLATED, phrases=phrases)


def build_slow_en_translated_section(
    scenes: list[Scene],
    l2_voice_map: dict[str, str],
    narrator_voice: str,
    l2_code: str,
) -> Section:
    """Build the SLOW_EN_TRANSLATED section — narrator translation FIRST, then slowed L2."""
    phrases: list[Phrase] = [
        Phrase(
            text=SECTION_TITLES[SectionType.SLOW_EN_TRANSLATED],
            voice_id=narrator_voice,
            language_code="en",
            role="narrator",
        ),
        *_build_translated_phrases(scenes, l2_voice_map, narrator_voice, l2_code, en_first=True, slow=True),
    ]
    return Section(section_type=SectionType.SLOW_EN_TRANSLATED, phrases=phrases)
