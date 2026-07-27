"""Title normalization shared by ``Lesson`` and ``CurriculumDay``.

The planner and story LLMs habitually prefix their titles with their own day
number ("Day 5: The Trail Ends in the Garden"). That number is not
authoritative: ``CurriculumDay.day`` is a stable key that is deliberately never
renumbered when a day is deleted (lessons, pipeline jobs and planner feedback
all key off it), so an embedded title number drifts out of step with both the
key and the position the UI shows — a day keyed 6 can carry the title "Day 5",
which then renders as "Day 6 · Day 5: …".

Strip the prefix on construction and let the UI supply the number from
``Curriculum.day_positions()``.
"""

from __future__ import annotations

import re

# Requires punctuation after the number so ordinary titles survive:
# "Day 5 Reasons to Visit" is a title, "Day 5 - Reasons to Visit" is a prefix.
_DAY_PREFIX_RE = re.compile(r"^\s*day\s*\d+\s*[:.)\-–—]\s*", re.IGNORECASE)


def strip_day_prefix(title: str) -> str:
    """Remove a leading "Day N:" (or "Day N -", "Day N.", "Day N)") from a title.

    A title that is *only* a prefix is returned unchanged — stripping it would
    leave nothing to show.
    """
    stripped = _DAY_PREFIX_RE.sub("", title).strip()
    return stripped or title
