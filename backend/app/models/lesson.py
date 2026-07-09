"""Lesson, Section, and Phrase domain models.

Pimsleur 4-section format ported from micro-demo-0.0/tunatale/core/models/.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum

from app.models.language import NARRATOR_VOICE


@dataclass
class KeyPhraseInfo:
    """A key phrase with its L1 translation, stored on the Lesson for deferred SRS registration."""

    phrase: str
    translation: str


class SectionType(Enum):
    """Four Pimsleur section types for each lesson."""

    KEY_PHRASES = "key_phrases"
    NATURAL_SPEED = "natural_speed"
    SLOW_SPEED = "slow_speed"
    TRANSLATED = "translated"
    SLOW_TRANSLATED = "slow_translated"


@dataclass
class Phrase:
    """A single phrase with TTS voice settings."""

    text: str
    voice_id: str
    language_code: str
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    role: str = ""


@dataclass
class Section:
    """A section within a lesson, grouping phrases of the same Pimsleur type."""

    section_type: SectionType
    phrases: list[Phrase] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.section_type, SectionType):
            raise ValueError(f"section_type must be a SectionType enum, got {type(self.section_type)}")


@dataclass
class Lesson:
    """A complete TunaTale audio lesson."""

    title: str
    language_code: str
    sections: list[Section] = field(default_factory=list)
    narrator_voice: str = NARRATOR_VOICE
    key_phrases: list[KeyPhraseInfo] = field(default_factory=list)
    generation_metadata: dict = field(default_factory=dict)

    def to_json(self) -> str:
        data = {
            "title": self.title,
            "language_code": self.language_code,
            "narrator_voice": self.narrator_voice,
            "key_phrases": [{"phrase": kp.phrase, "translation": kp.translation} for kp in self.key_phrases],
            "sections": [
                {
                    "section_type": s.section_type.value,
                    "phrases": [
                        {
                            "text": p.text,
                            "voice_id": p.voice_id,
                            "language_code": p.language_code,
                            "rate": p.rate,
                            "pitch": p.pitch,
                            "volume": p.volume,
                            "role": p.role,
                        }
                        for p in s.phrases
                    ],
                }
                for s in self.sections
            ],
            "generation_metadata": self.generation_metadata,
        }
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> Lesson:
        data = json.loads(json_str)
        sections = [
            Section(
                section_type=SectionType(s["section_type"]),
                phrases=[Phrase(**p) for p in s["phrases"]],
            )
            for s in data.get("sections", [])
        ]
        key_phrases = [KeyPhraseInfo(**kp) for kp in data.get("key_phrases", [])]
        return cls(
            title=data["title"],
            language_code=data["language_code"],
            sections=sections,
            narrator_voice=data.get("narrator_voice", NARRATOR_VOICE),
            key_phrases=key_phrases,
            generation_metadata=data.get("generation_metadata", {}),
        )


def extract_sentence_translations_from_translated(lesson: Lesson) -> dict[str, str]:
    """Recover {L2_sentence: EN_translation} from a stored Lesson's TRANSLATED section.

    Used to backfill `generation_metadata['sentence_translations']` on lessons
    generated before that field existed. The TRANSLATED section emits
    alternating L2/EN phrases (with stray EN-EN label lines like
    "Translated"/"At the Cafe" at the top); we pair each L2 phrase with the
    immediately-following EN phrase. First occurrence wins on duplicate L2 keys.
    """
    out: dict[str, str] = {}
    l2_code = lesson.language_code
    for section in lesson.sections:
        if section.section_type is not SectionType.TRANSLATED:
            continue
        phrases = section.phrases
        for i in range(len(phrases) - 1):
            cur, nxt = phrases[i], phrases[i + 1]
            if cur.language_code == l2_code and nxt.language_code == "en" and cur.text and cur.text not in out:
                out[cur.text] = nxt.text
    return out
