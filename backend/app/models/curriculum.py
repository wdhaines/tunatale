"""Curriculum domain models."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class CurriculumDay:
    """One day in the language learning curriculum."""

    day: int
    title: str
    focus: str
    collocations: list[str]
    learning_objective: str
    story_guidance: str = ""

    def __post_init__(self) -> None:
        if self.day < 1:
            raise ValueError(f"day must be ≥ 1, got {self.day}")


@dataclass
class Curriculum:
    """A complete language learning curriculum for a given topic."""

    id: str
    topic: str
    language_code: str
    cefr_level: str
    days: list[CurriculumDay] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> Curriculum:
        data = json.loads(json_str)
        days_data = data.pop("days", [])
        days = [CurriculumDay(**d) for d in days_data]
        return cls(days=days, **data)
