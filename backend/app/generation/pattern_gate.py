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
  :data:`PASS_RATE` of all their reviews passed. Learning steps are not
  reviews: passing them shows the card was read the day it was added, not
  that the form came back on a later day. A review graded before it was due
  (the Grade page grades ahead) is a review like any other.

Which cards are a pattern's evidence (the owner, 2026-11-20, tunatale-ve4p.36):
a pattern taught as sentences (on-gi) counts its focus cards. A word pattern
(mo-/mi-, maka-/naka-) counts the sentence cards (``sent:*``: a Wolff line, a
drill card, the affix drill card) whose blank says one of its forms, read by
the language's lemmatizer and A1 features. A card whose blank is another word
of the line does not test the form and does not count. The pair cards
(``morph:pair-*``) count for nothing: the English of their blank made the only
decision, so they are no longer made, and the ones in a deck age out.

A ``review_only`` pattern needs one drill and no cards, on the OWNER's deck:
the flag marks what the owner already knows. Another learner is taught it like
any other pattern (the owner, 2026-10-23, for ma-/na-: tunatale-bi67), so every
caller says whose deck it is reading. Nothing else brings a
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
from app.srs.focus_card import focus_key
from app.srs.function_words import cloze_answers
from app.srs.pattern_forms import FormReader
from app.srs.sentence_card import SENTENCE_KEY_PREFIX
from app.storage.store import ContentStore

logger = logging.getLogger(__name__)

DRILL_DAYS = 2
REVIEW_ONLY_DRILL_DAYS = 1
FIRST_PASSES = 4
PASS_RATE = 0.8
STUCK_AFTER = 5

# revlog review_kinds of a review: due (1) or graded ahead (3, the Grade page; the owner,
# 2026-11-20, tunatale-ve4p.38). Not a learning step (0), a relearn (2) or a manual row.
_REVIEWS = frozenset({1, 3})
_AGAIN = 1


@dataclass(frozen=True)
class Progress:
    """How far the learner has come with one pattern."""

    pattern: AffixPattern
    review_only: bool  # the pattern's flag, on the owner's deck only
    drill_days: int
    first_passes: int  # evidence cards whose first review passed
    reviews: int  # every review of its evidence cards
    passes: int
    last_heard: date | None = None

    @property
    def drill_days_needed(self) -> int:
        return REVIEW_ONLY_DRILL_DAYS if self.review_only else DRILL_DAYS

    @property
    def met(self) -> bool:
        drilled = self.drill_days >= self.drill_days_needed
        if self.review_only:
            return drilled
        return drilled and self.first_passes >= FIRST_PASSES and self.passes >= PASS_RATE * self.reviews

    @property
    def stuck(self) -> bool:
        return not self.met and self.drill_days >= STUCK_AFTER

    def summary(self) -> dict:
        """Each part of the gate beside what it needs, for the meters under a lesson's drill (ve4p.30).

        A review-only pattern needs its drill days and nothing else, so its card targets are 0.
        """
        cards = not self.review_only
        return {
            "drill_days": self.drill_days,
            "drill_days_needed": self.drill_days_needed,
            "first_passes": self.first_passes,
            "first_passes_needed": FIRST_PASSES if cards else 0,
            "reviews": self.reviews,
            "passes": self.passes,
            "pass_rate_needed": PASS_RATE if cards else 0.0,
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


def _sentence_cards_saying(language_code: str, srs_db) -> dict[str, frozenset[int]]:
    """Per A1 feature, the sentence cards whose blank says a word of it.

    The blank is what the card asks the learner to say: the whole line on a
    line's own card, one word on its word cards. It is read by
    ``pattern_forms.FormReader``, the reader Grade's suggested blanks use too.
    """
    reader = FormReader(language_code)
    said: dict[str, set[int]] = {}
    for collocation_id, sentence in srs_db.get_cloze_sentences_by_key_prefix(SENTENCE_KEY_PREFIX):
        for answer in cloze_answers(sentence):
            for feature in reader.said(answer):
                said.setdefault(feature, set()).add(collocation_id)
    return {feature: frozenset(ids) for feature, ids in said.items()}


class _Deck:
    """What the gate reads from one learner's deck, each part read once and only when asked for."""

    def __init__(self, language_code: str, srs_db, store: ContentStore) -> None:
        self.language_code = language_code
        self.srs_db = srs_db
        self.days = _drill_days(store, srs_db)
        self._said: dict[str, frozenset[int]] | None = None

    def evidence(self, pattern: AffixPattern) -> list[int]:
        """The ids of the cards that are *pattern*'s evidence.

        A sentence pattern's are its focus cards, actor and object focus alike:
        both test the choice (tunatale-ve4p.9). A word pattern's are the
        sentence cards that say one of its forms, each once.
        """
        if pattern.sentences:
            return [
                collocation_id
                for feature in pattern.card_features
                for collocation_id in self.srs_db.get_collocation_ids_by_disambig_key(focus_key(feature))
            ]
        if self._said is None:
            self._said = _sentence_cards_saying(self.language_code, self.srs_db)
        return sorted(frozenset().union(*(self._said.get(feature, frozenset()) for feature in pattern.features)))

    def progress(self, pattern: AffixPattern, *, owner: bool) -> Progress:
        review_only = pattern.review_only and owner
        heard = self.days.get(pattern.key, [])
        first_passes = reviews = passes = 0
        for collocation_id in [] if review_only else self.evidence(pattern):
            # Every row of the card: no id is at or below 0, so "unpushed after 0" is all of them.
            rows = self.srs_db.get_unpushed_revlog_rows(collocation_id, Direction.PRODUCTION, 0)
            graded = [row.button_chosen > _AGAIN for row in rows if row.review_kind in _REVIEWS]
            first_passes += bool(graded) and graded[0]
            reviews += len(graded)
            passes += sum(graded)
        return Progress(
            pattern=pattern,
            review_only=review_only,
            drill_days=len(heard),
            first_passes=first_passes,
            reviews=reviews,
            passes=passes,
            last_heard=heard[-1] if heard else None,
        )


def progress(language_code: str, pattern: AffixPattern, *, srs_db, store: ContentStore, owner: bool) -> Progress:
    """The learner's progress with *pattern*, read from their listens and its evidence cards.

    *owner*: whether *srs_db* is the owner's deck, where a ``review_only`` pattern is reviewed, not taught.
    """
    return _Deck(language_code, srs_db, store).progress(pattern, owner=owner)


def taught_prefixes(language_code: str, *, srs_db, store: ContentStore, owner: bool) -> frozenset[str]:
    """The prefixes the learner has (tunatale-lat6): every affix of a pattern that passed the
    gate, and on the owner's deck of a ``review_only`` pattern, heard yet or not, since the
    owner brought it with them. A key phrase's word made of one of these and a well-known
    root is said whole (``known_buildups``). Suffixes come along (``on`` of -on) and match
    no prefix."""
    bundle = get_a1_morphology(language_code)
    if bundle is None:
        return frozenset()
    deck = _Deck(language_code, srs_db, store)
    found: set[str] = set()
    for pattern in bundle.patterns:
        each = deck.progress(pattern, owner=owner)
        if each.review_only or each.met:
            found |= {feature.split(":", 1)[-1] for feature in pattern.features}
    return frozenset(found)


def next_pattern(language_code: str, *, srs_db, store: ContentStore, owner: bool) -> AffixPattern | None:
    """The pattern the learner's next lesson drills, or ``None`` for a language with none."""
    bundle = get_a1_morphology(language_code)
    if bundle is None or not bundle.patterns:
        return None
    deck = _Deck(language_code, srs_db, store)
    every = [deck.progress(pattern, owner=owner) for pattern in bundle.patterns]
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
