"""Declarative registry of anki_state_cache invalidation contracts.

Every cache key must be registered with its source (ANKI_CONFIG, TT_SESSION,
or TT_STATE) and invalidation semantics (day-scoped, max-age, logic-version).
The registry is load-bearing: set_anki_state_cache / get_anki_state_cache
raise KeyError on unregistered keys, and the sync-refresh harness derives
conservation tests from it.

This replaces hand-maintained lists (the refresh_* calls in sync_engine.py,
the 30-day max-age pattern) with a single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Final


class CacheSource(Enum):
    """Origin of the cached value.

    ANKI_CONFIG: value mirrors Anki deck-config / collection state. Synced at
                sync_pull via refresh_* calls.

    TT_SESSION: session_main_queue, learning_cutoff. Mirrors the app's frozen
                queue state and learning-cutoff timestamp. Frozen at session
                start, updated on grade/sync.

    TT_STATE: application-scoped state (last_unbury_day, last_grade_undo).
             Not synced; self-invalidated or cleared on sync.
    """

    ANKI_CONFIG = auto()
    TT_SESSION = auto()
    TT_STATE = auto()


@dataclass(frozen=True)
class CacheKeySpec:
    """Specification of a single cache key's invalidation contract.

    Fields:
        name: cache key string (must be unique, immutable).
        source: CacheSource enum indicating the key's origin.
        day_scoped: True if the payload embeds an Anki day (date) or timestamp.
                   Readers ignore cache mismatches on date changes (implicit
                   invalidation at day rollover). Examples:
                   - session_main_queue: {"day": "2026-07-17", "items": [...]}
                   - learning_cutoff: "2026-07-17T15:30:45"
                   - last_unbury_day: "2026-07-17"
        max_age_days: int | None. If set, cache is stale after N days.
                     resolve_* functions check age before use (falls back to
                     default/compute on stale). Examples: 30 for fsrs_params,
                     learn_steps, relearn_steps, maximum_review_interval.
        logic_version: int | None. When the PRODUCING algorithm changes, bump
                      this integer. session_main_queue starts at 1; others None
                      (step 2 only adds this field to session_main_queue for
                      now). Readers discard cache when version mismatches
                      (treated like day mismatch → rebuild path).
    """

    name: str
    source: CacheSource
    day_scoped: bool = False
    max_age_days: int | None = None
    logic_version: int | None = None


REGISTRY: Final[dict[str, CacheKeySpec]] = {
    # TT_STATE keys (2 total)
    "last_unbury_day": CacheKeySpec(
        name="last_unbury_day",
        source=CacheSource.TT_STATE,
        day_scoped=True,
    ),
    "last_grade_undo": CacheKeySpec(
        name="last_grade_undo",
        source=CacheSource.TT_STATE,
    ),
    # TT_SESSION keys (2 total)
    "learning_cutoff": CacheKeySpec(
        name="learning_cutoff",
        source=CacheSource.TT_SESSION,
        day_scoped=True,
    ),
    "session_main_queue": CacheKeySpec(
        name="session_main_queue",
        source=CacheSource.TT_SESSION,
        day_scoped=True,
        logic_version=1,
    ),
    # ANKI_CONFIG keys (15 total)
    "daily_new_cap": CacheKeySpec(
        name="daily_new_cap",
        source=CacheSource.ANKI_CONFIG,
    ),
    "daily_review_cap": CacheKeySpec(
        name="daily_review_cap",
        source=CacheSource.ANKI_CONFIG,
    ),
    "desired_retention": CacheKeySpec(
        name="desired_retention",
        source=CacheSource.ANKI_CONFIG,
    ),
    "new_spread": CacheKeySpec(
        name="new_spread",
        source=CacheSource.ANKI_CONFIG,
    ),
    "bury_new": CacheKeySpec(
        name="bury_new",
        source=CacheSource.ANKI_CONFIG,
    ),
    "bury_review": CacheKeySpec(
        name="bury_review",
        source=CacheSource.ANKI_CONFIG,
    ),
    "col_crt": CacheKeySpec(
        name="col_crt",
        source=CacheSource.ANKI_CONFIG,
    ),
    "fsrs_params": CacheKeySpec(
        name="fsrs_params",
        source=CacheSource.ANKI_CONFIG,
        max_age_days=30,
    ),
    "learn_steps": CacheKeySpec(
        name="learn_steps",
        source=CacheSource.ANKI_CONFIG,
        max_age_days=30,
    ),
    "relearn_steps": CacheKeySpec(
        name="relearn_steps",
        source=CacheSource.ANKI_CONFIG,
        max_age_days=30,
    ),
    "easy_days_percentages": CacheKeySpec(
        name="easy_days_percentages",
        source=CacheSource.ANKI_CONFIG,
    ),
    "load_balancer_enabled": CacheKeySpec(
        name="load_balancer_enabled",
        source=CacheSource.ANKI_CONFIG,
    ),
    "new_cards_ignore_review_limit": CacheKeySpec(
        name="new_cards_ignore_review_limit",
        source=CacheSource.ANKI_CONFIG,
    ),
    "fsrs_short_term_with_steps_enabled": CacheKeySpec(
        name="fsrs_short_term_with_steps_enabled",
        source=CacheSource.ANKI_CONFIG,
    ),
    "maximum_review_interval": CacheKeySpec(
        name="maximum_review_interval",
        source=CacheSource.ANKI_CONFIG,
        max_age_days=30,
    ),
}
