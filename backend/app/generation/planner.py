"""Interactive chat-based curriculum planner.

One ``llm.complete`` call per turn with a deterministic user prompt.
The planner does NOT touch the DB — it is a pure turn function:
state in, ``PlannerTurn`` out.

Deferred story-steering seam
----------------------------
``story_guidance`` already flows into the story prompt (``story.py:88``);
``review_collocations="(none yet)"`` (``story.py:90``) is where the
snapshot's review sample should eventually flow.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.generation.json_parsing import split_reply_and_json
from app.generation.prompts import PLANNER_SYSTEM_PROMPT, build_planner_turn_prompt
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language
from app.storage.plan_io import get_planner_state, validate_plan_days


class PlannerError(Exception):
    """Raised when the LLM returns an invalid or unparseable proposal."""


@dataclass
class PlannerTurn:
    """Result of one planner turn.

    Attributes:
        reply: The LLM's prose reply with any JSON block removed.
        proposed_days: Validated, server-renumbered day objects, or ``None``
            when the LLM chose a pure-chat turn.
    """

    reply: str
    proposed_days: list[CurriculumDay] | None


def build_turn_prompt(
    *,
    curriculum: Curriculum,
    user_message: str,
    batch_size: int,
    learner_snapshot: str,
    language: Language,
) -> tuple[str, str]:
    """Build the system and user prompts for one planner turn.

    Pure function of its inputs — no LLM, no DB.  Returns
    ``(PLANNER_SYSTEM_PROMPT, user_prompt)``.
    """
    start_day = max(d.day for d in curriculum.days) + 1 if curriculum.days else 1

    state = get_planner_state(curriculum)

    chat = [*state.get("chat", []), {"role": "user", "content": user_message}]

    user_prompt = build_planner_turn_prompt(
        topic=curriculum.topic,
        cefr_level=curriculum.cefr_level,
        language_name=language.name,
        language_code=language.code,
        days=curriculum.days,
        learner_snapshot=learner_snapshot,
        feedback=state.get("feedback", []),
        chat=chat,
        batch_size=batch_size,
        start_day=start_day,
    )

    return (PLANNER_SYSTEM_PROMPT, user_prompt)


def parse_turn(
    raw: str,
    *,
    curriculum: Curriculum,
    batch_size: int,
) -> PlannerTurn:
    """Parse the raw LLM (or pasted) reply into a ``PlannerTurn``.

    Extracts the JSON block, validates day count and field schema, and
    renumbers days starting from ``curriculum.days``.  All validation is
    identical to what ``turn()`` applies — no weakening for manual mode.
    """
    start_day = max(d.day for d in curriculum.days) + 1 if curriculum.days else 1

    try:
        prose, data = split_reply_and_json(raw)
    except ValueError as e:
        raise PlannerError(str(e)) from e

    if data is None:
        return PlannerTurn(reply=prose, proposed_days=None)

    days_list = data.get("days")
    if not isinstance(days_list, list):
        raise PlannerError("LLM response JSON missing 'days' list")

    for i, d in enumerate(days_list):
        if not isinstance(d, dict):
            raise PlannerError(f"days[{i}] must be an object")
        d["day"] = start_day + i

    if len(days_list) != batch_size:
        raise PlannerError(f"Expected {batch_size} days, got {len(days_list)}")

    try:
        validate_plan_days(days_list, start_day=start_day)
    except ValueError as e:
        raise PlannerError(str(e)) from e

    proposed = [CurriculumDay(**d) for d in days_list]
    return PlannerTurn(reply=prose, proposed_days=proposed)


class CurriculumPlanner:
    """One-turn curriculum planner backed by an LLM client."""

    def __init__(self, llm) -> None:
        self._llm = llm

    async def turn(
        self,
        *,
        curriculum: Curriculum,
        user_message: str,
        batch_size: int,
        learner_snapshot: str,
        language: Language,
    ) -> PlannerTurn:
        """Run one planner turn.

        Args:
            curriculum: The current curriculum (committed days + metadata).
            user_message: The user's new chat message.
            batch_size: How many days to propose this turn (ignored for
                pure-chat turns).
            learner_snapshot: Pre-built vocabulary snapshot string.
            language: Target language configuration.

        Returns:
            A ``PlannerTurn`` with the LLM's reply and optionally a list of
            validated, server-renumbered ``CurriculumDay`` objects.

        Raises:
            PlannerError: When the LLM returns malformed JSON, wrong day
                count, or invalid day fields.
        """
        system_prompt, user_prompt = build_turn_prompt(
            curriculum=curriculum,
            user_message=user_message,
            batch_size=batch_size,
            learner_snapshot=learner_snapshot,
            language=language,
        )

        raw = await self._llm.complete(
            user_prompt,
            system_prompt=system_prompt,
            temperature=0.7,
            max_tokens=5500,
        )

        return parse_turn(raw, curriculum=curriculum, batch_size=batch_size)
