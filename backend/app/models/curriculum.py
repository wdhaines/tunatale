"""Curriculum domain models."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from app.common.titles import strip_day_prefix
from app.models.strategy import ReviewPressure


@dataclass
class CurriculumDay:
    """One day in the language learning curriculum.

    ``day`` is a stable key, not a display ordinal: lessons, pipeline jobs and
    planner feedback all reference it, so deleting a day leaves a permanent gap
    (``[1, 2, 3, 4, 6]``). Use ``Curriculum.day_positions()`` for anything the
    learner sees.
    """

    day: int
    title: str
    focus: str
    collocations: list[str]
    learning_objective: str
    story_guidance: str = ""

    def __post_init__(self) -> None:
        if self.day < 1:
            raise ValueError(f"day must be ≥ 1, got {self.day}")
        # Also normalizes titles already persisted with a stale prefix, since
        # from_json rebuilds every day through this constructor.
        self.title = strip_day_prefix(self.title)


@dataclass
class Curriculum:
    """A complete language learning curriculum for a given topic."""

    id: str
    topic: str
    language_code: str
    cefr_level: str
    days: list[CurriculumDay] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def review_pressure(self, override: str | None = None) -> ReviewPressure:
        """How hard this plan's stories should push to use the learner's review words.

        One dial for the whole curriculum, stored on ``metadata`` exactly like
        ``generation_mode``. NOT on a CurriculumDay: that would need a schema
        change and a migration, and it would make review pressure a per-day
        decision the PLANNER takes — changing the planner prompt and invalidating
        its cassettes for a feature it never asked for.

        ⚠️ *override* of ``None`` means "the caller did not specify", NOT
        "NATURAL". Conflating the two silently disables the setting at every call
        site that does not pass one, which is all of them by default.

        An unrecognised stored value degrades to NATURAL rather than raising:
        ``metadata`` is a free-form JSON blob that hand edits and older builds
        can write to, and a bad value there must not 500 a generation.
        """
        name = override or self.metadata.get("review_pressure") or ReviewPressure.NATURAL.name
        try:
            return ReviewPressure[name]
        except KeyError:
            return ReviewPressure.NATURAL

    def record_review_request(self, day: int, words: Sequence[str]) -> None:
        """Remember which review words a prompt for *day* actually asked for.

        Export and import are joined only by curriculum_id + day, so without this
        the manual path has no way to answer "did the story include what we asked
        for?" — and recomputing at import gives a different answer precisely when
        time has passed, which is the only case worth asking about (fgeq.1).

        Last export wins: the user pastes the most recent prompt, so an older
        request is not evidence about the story they bring back.
        """
        self.metadata.setdefault("review_requests", {})[str(day)] = list(words)

    def review_request(self, day: int) -> tuple[str, ...]:
        """What the last exported prompt for *day* asked for; empty if unknown.

        ⚠️ The key is `str(day)`. `metadata` round-trips through JSON, where an
        int dict key comes back as a string — keying by the int would look
        correct and silently find nothing after exactly one reload.

        Empty means UNMEASURABLE (never exported, or exported before this
        existed), not "nothing was requested". Callers must not treat it as a
        failed generation.
        """
        return tuple(self.metadata.get("review_requests", {}).get(str(day), ()))

    def day_positions(self) -> dict[int, int]:
        """Map each ``day`` key to its 1-based position in the ordered plan.

        Day keys go gappy as days are deleted; positions never do. Every
        learner-facing "Day N" comes from here so the sequence stays contiguous.
        """
        return {d.day: i for i, d in enumerate(sorted(self.days, key=lambda d: d.day), start=1)}

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> Curriculum:
        data = json.loads(json_str)
        days_data = data.pop("days", [])
        days = [CurriculumDay(**d) for d in days_data]
        return cls(days=days, **data)
