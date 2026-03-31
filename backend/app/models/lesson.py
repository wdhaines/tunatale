"""Lesson, Section, and Phrase domain models.

Pimsleur 4-section format ported from micro-demo-0.0/tunatale/core/models/.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum


class SectionType(Enum):
    """Four Pimsleur section types for each lesson."""

    KEY_PHRASES = "key_phrases"
    NATURAL_SPEED = "natural_speed"
    SLOW_SPEED = "slow_speed"
    TRANSLATED = "translated"


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

    def to_json(self) -> str:
        data = {
            "title": self.title,
            "language_code": self.language_code,
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
        return cls(title=data["title"], language_code=data["language_code"], sections=sections)
