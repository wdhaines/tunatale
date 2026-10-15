"""Which affix pattern the learner is on: a pace gate over the language's teaching order (tunatale-ve4p.26).

The language's patterns are its teaching order (``A1Morphology.patterns``).
The next pattern is the first one the learner has not passed, so a pattern
stays the day's pattern until it passes, and one that passed comes back once
it stops passing. The owner set the gate, 2026-10-14, faster than the
fortnight first proposed:

* its drill was heard on :data:`DRILL_DAYS` different study days, a lesson
  marked listened whose ``affix_drill`` is this pattern, a grammar lesson or a
  story's closing drill alike. Two listens on one day are one drill;
* :data:`FIRST_PASSES` of its cards passed their first review, and at least
  :data:`PASS_RATE` of all its card reviews passed: its contrast cards, or for
  a pattern taught as sentences its focus cards. Learning
  steps are not reviews: passing them shows the card was read the day it was
  added, not that the form came back on a later day.

A ``review_only`` pattern needs one drill and no cards. Nothing else brings a
passed pattern back: its cards' own spacing is the widening gap. With every
pattern passed, the one heard longest ago comes round.

The evidence for two drills: the owner's transfer probe read 5/10 after one
mo- / mi- drill and 10/10 after two, with an undrilled root among the ten.

A pattern still unpassed after :data:`STUCK_AFTER` drill days is logged as
``PATTERN_STUCK``: it stays the day's pattern, and something has to change
other than another drill.

Reads only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from app.languages import get_a1_morphology
from app.models.srs_item import Direction
from app.srs.a1_morphology import AffixPattern
from app.srs.anki_mirror.rollover import anki_today
from app.srs.contrast_card import pair_key
from app.srs.focus_card import focus_key
from app.storage.store import ContentStore

logger = logging.getLogger(__name__)

DRILL_DAYS = 2
REVIEW_ONLY_DRILL_DAYS = 1
FIRST_PASSES = 4
PASS_RATE = 0.8
STUCK_AFTER = 5

_REVIEW = 1  # revlog review_kind of a review: not a learning step, a relearn, a cram or a manual row
_AGAIN = 1


@dataclass(frozen=True)
class Progress:
    """How far the learner has come with one pattern."""

    pattern: AffixPattern
    drill_days: int
    first_passes: int  # contrast cards whose first review passed
    reviews: int  # every review of its contrast cards
    passes: int
    last_heard: date | None = None

    @property
    def met(self) -> bool:
        if self.pattern.review_only:
            return self.drill_days >= REVIEW_ONLY_DRILL_DAYS
        return (
            self.drill_days >= DRILL_DAYS
            and self.first_passes >= FIRST_PASSES
            and self.passes >= PASS_RATE * self.reviews
        )

    @property
    def stuck(self) -> bool:
        return not self.met and self.drill_days >= STUCK_AFTER

    def summary(self) -> dict:
        """Each part of the gate beside what it needs, for the meters under a lesson's drill (ve4p.30).

        A review-only pattern needs its drill days and nothing else, so its
        card targets are 0.
        """
        review_only = self.pattern.review_only
        return {
            "review_only": review_only,
            "drill_days": self.drill_days,
            "drill_days_needed": REVIEW_ONLY_DRILL_DAYS if review_only else DRILL_DAYS,
            "first_passes": self.first_passes,
            "first_passes_needed": 0 if review_only else FIRST_PASSES,
            "reviews": self.reviews,
            "passes": self.passes,
            "pass_rate_needed": 0.0 if review_only else PASS_RATE,
            "met": self.met,
            "stuck": self.stuck,
        }


def _drill_days(store: ContentStore, srs_db) -> dict[str, list[date]]:
    """Per pattern key, the study days on which a lesson drilling it was marked listened, in order."""
    drilled = {
        lesson_id: recorded["pattern"]
        for lesson_id, _, _, lesson in store.list_lessons()
        if (recorded := (lesson.generation_metadata or {}).get("affix_drill"))
    }
    days: dict[str, set[date]] = {}
    for lesson_id, listened_at in srs_db.get_listens():
        if lesson_id in drilled:
            days.setdefault(drilled[lesson_id], set()).add(anki_today(datetime.fromisoformat(listened_at)))
    return {key: sorted(heard) for key, heard in days.items()}


def _card_keys(pattern: AffixPattern) -> list[str]:
    """The keys of the cards that are *pattern*'s evidence.

    A sentence pattern's are its focus cards, actor and object focus alike:
    both test the choice (tunatale-ve4p.9). A word pattern's are its contrast cards.
    """
    if pattern.sentences:
        return [focus_key(feature) for feature in pattern.card_features]
    return [pair_key(feature) for feature in pattern.features]


def _progress(pattern: AffixPattern, heard: list[date], srs_db) -> Progress:
    first_passes = reviews = passes = 0
    for key in _card_keys(pattern):
        for collocation_id in srs_db.get_collocation_ids_by_disambig_key(key):
            # Every row of the card: no id is at or below 0, so "unpushed after 0" is all of them.
            rows = srs_db.get_unpushed_revlog_rows(collocation_id, Direction.PRODUCTION, 0)
            graded = [row.button_chosen > _AGAIN for row in rows if row.review_kind == _REVIEW]
            first_passes += bool(graded) and graded[0]
            reviews += len(graded)
            passes += sum(graded)
    return Progress(
        pattern=pattern,
        drill_days=len(heard),
        first_passes=first_passes,
        reviews=reviews,
        passes=passes,
        last_heard=heard[-1] if heard else None,
    )


def progress(pattern: AffixPattern, *, srs_db, store: ContentStore) -> Progress:
    """The learner's progress with *pattern*, read from their listens and their contrast cards."""
    return _progress(pattern, _drill_days(store, srs_db).get(pattern.key, []), srs_db)


def next_pattern(language_code: str, *, srs_db, store: ContentStore) -> AffixPattern | None:
    """The pattern the learner's next lesson drills, or ``None`` for a language with none."""
    bundle = get_a1_morphology(language_code)
    if bundle is None or not bundle.patterns:
        return None
    days = _drill_days(store, srs_db)
    every = [_progress(pattern, days.get(pattern.key, []), srs_db) for pattern in bundle.patterns]
    current = next((each for each in every if not each.met), None)
    if current is None:
        return min(every, key=lambda each: each.last_heard).pattern  # met means heard at least once
    if current.stuck:
        logger.warning(
            "PATTERN_STUCK language=%s pattern=%s drill_days=%d first_passes=%d reviews=%d passes=%d",
            language_code,
            current.pattern.key,
            current.drill_days,
            current.first_passes,
            current.reviews,
            current.passes,
        )
    return current.pattern
