"""Starter cards for a new language from a related one the learner already knows.

A learner moving from one language to a close relative already knows its
cognates: the word that is spelled the same and means the same thing. This
module finds them and decides how much of the old knowledge carries over. It is
language-agnostic — both codes and the dictionary come from the caller — and
pure: no database, no Anki, no clock. ``scripts/seed_cognate_cards.py`` is the
wiring.

The pipeline, and what each step decides
=========================================

1. **Find.** ``classify`` looks a known word up in a target-language dictionary
   (a kaikki.org extract, ``load_dictionary``). The dictionary is the review
   gate: a proposal the learner cannot judge must at least exist in the target
   language with a compatible English gloss.

   - same spelling, compatible gloss → ``COGNATE``
   - a similar spelling (``near_cognate_distance``), compatible gloss → ``NEAR_COGNATE``
   - same spelling, NO compatible gloss → ``FALSE_FRIEND``: a warning, never minted
   - nothing → ``UNRELATED``: not a starter card

2. **Discount.** A cognate's head start is half the known card's stability; a
   near-cognate's recognition is a quarter of it and its production is NOT
   seeded (the learner has never produced the new spelling). Only a direction
   the learner genuinely knows is carried over: REVIEW or KNOWN, stability at
   least ``MIN_SOURCE_STABILITY`` days. The result is capped at
   ``MAX_SEED_STABILITY`` days so an inflated KNOWN card does not vanish for a
   year in a language it was never reviewed in.

3. **Spread.** A seeded card is a REVIEW card, and the 10/day new-card budget
   does not cover reviews, so seeding sixty cards due tomorrow would be a wall.
   ``schedule`` places each direction on a day from tomorrow onward, at most
   ``per_day`` per day, never on the same day as its sibling (Anki buries a
   review whose sibling was answered that day, so the second would slip), and
   always strictly before its interval runs out: the implied last review is
   ``due - ivl``, and it must lie in the past, never today.

Why a seeded card carries no revlog
===================================
Anki's FSRS optimizer trains on the revlog. A fabricated review row would be
treated as a real answer the learner gave and bias every parameter fitted after
it. So the head start lives only in the card's memory state and schedule; see
``SRSDatabase.seed_review_state`` for how it reaches Anki.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from app.common.guid import compute_guid
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from app.srs.database import SRSDatabase

COGNATE_DISCOUNT = 0.5
NEAR_COGNATE_DISCOUNT = 0.25
MIN_SOURCE_STABILITY = 21.0
MAX_SEED_STABILITY = 60.0
DEFAULT_PER_DAY = 10
DEFAULT_DIFFICULTY = 5.0

_KNOWN_STATES = frozenset({SRSState.REVIEW, SRSState.KNOWN})

# Function words that make two glosses look alike without meaning the same
# thing: "to go" and "to eat" share only "to".
_GLOSS_STOPWORDS = frozenset(
    {
        "a", "an", "the", "to", "of", "be", "is", "are", "was", "in", "on", "at",
        "for", "with", "and", "or", "as", "by", "from", "it", "its", "that",
        "this", "one", "someone", "something", "some", "very", "not", "your",
        "my", "his", "her", "their", "our", "you", "i", "he", "she", "we", "they",
    }
)  # fmt: skip
_WORD = re.compile(r"[a-z]+")


class Relation(Enum):
    COGNATE = "cognate"
    NEAR_COGNATE = "near-cognate"
    FALSE_FRIEND = "false-friend"
    UNRELATED = "unrelated"


@dataclass(frozen=True)
class KnownDirection:
    """One direction of a card the learner already has, in the source language."""

    state: SRSState
    stability: float | None
    difficulty: float | None


@dataclass(frozen=True)
class KnownWord:
    text: str
    translation: str
    directions: dict[Direction, KnownDirection]


@dataclass(frozen=True)
class Match:
    word: KnownWord
    relation: Relation
    target_text: str | None
    target_glosses: tuple[str, ...] = ()


@dataclass(frozen=True)
class Seed:
    stability: float
    difficulty: float
    offset_days: int


@dataclass(frozen=True)
class Starter:
    """A card to mint in the target language, with the directions to seed."""

    match: Match
    seeds: dict[Direction, Seed]


_EDGE_PUNCT = ".,!?¡¿;:\"'()"


def normalize(text: str) -> str:
    """Casefold and trim the punctuation a flashcard front tends to carry."""
    return text.strip().strip(_EDGE_PUNCT).casefold()


def gloss_tokens(gloss: str) -> frozenset[str]:
    """Content words of an English gloss, crudely singularised."""
    words = _WORD.findall(gloss.casefold())
    return frozenset(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if w not in _GLOSS_STOPWORDS)


def glosses_compatible(translation: str, glosses: Iterable[str]) -> bool:
    """True when any dictionary gloss shares a content word with *translation*."""
    wanted = gloss_tokens(translation)
    return any(wanted & gloss_tokens(g) for g in glosses)


def load_dictionary(lines: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Normalised headword → every English gloss, from kaikki.org JSONL lines."""
    out: dict[str, list[str]] = {}
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        word = entry.get("word")
        if not word:
            continue
        glosses = [g for sense in entry.get("senses", []) for g in sense.get("glosses", [])]
        out.setdefault(normalize(word), []).extend(glosses)
    return {w: tuple(gs) for w, gs in out.items()}


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def near_cognate_distance(word: str) -> int:
    """How far a spelling may drift and still count as the same word.

    One edit per three letters, and never more than two: ``bahay``/``balay`` (1)
    passes, ``tatlo``/``tulo`` (2 in five letters) does not. Deliberately tight,
    because the learner cannot tell a near-cognate from a coincidence.
    """
    return min(2, len(word) // 3)


class Dictionary:
    """A target-language dictionary with a gloss index for near-cognate search."""

    def __init__(self, entries: dict[str, tuple[str, ...]]):
        self.entries = entries
        self._by_token: dict[str, set[str]] = {}
        for word, glosses in entries.items():
            for g in glosses:
                for tok in gloss_tokens(g):
                    self._by_token.setdefault(tok, set()).add(word)

    def classify(self, word: KnownWord) -> Match:
        text = normalize(word.text)
        glosses = self.entries.get(text)
        if glosses is not None:
            if glosses_compatible(word.translation, glosses):
                return Match(word, Relation.COGNATE, word.text.strip().strip(_EDGE_PUNCT), glosses)
            return Match(word, Relation.FALSE_FRIEND, text, glosses)
        limit = near_cognate_distance(text)
        candidates = {w for tok in gloss_tokens(word.translation) for w in self._by_token.get(tok, ())}
        scored = sorted((d, w) for w in candidates if 0 < (d := edit_distance(text, w)) <= limit)
        if scored:
            _, best = scored[0]
            return Match(word, Relation.NEAR_COGNATE, best, self.entries[best])
        return Match(word, Relation.UNRELATED, None)


def seed_for(relation: Relation, direction: Direction, known: KnownDirection | None) -> tuple[float, float] | None:
    """The (stability, difficulty) a direction starts with, or None to start it NEW."""
    if known is None or known.state not in _KNOWN_STATES or known.stability is None:
        return None
    if known.stability < MIN_SOURCE_STABILITY:
        return None
    if relation is Relation.COGNATE:
        discount = COGNATE_DISCOUNT
    elif relation is Relation.NEAR_COGNATE and direction is Direction.RECOGNITION:
        discount = NEAR_COGNATE_DISCOUNT
    else:
        return None
    stability = min(MAX_SEED_STABILITY, discount * known.stability)
    difficulty = known.difficulty if known.difficulty is not None else DEFAULT_DIFFICULTY
    return stability, difficulty


def _rank(match: Match) -> tuple[int, float, str]:
    """Cognates first, then the best-known source word, then alphabetical."""
    best = max((d.stability or 0.0 for d in match.word.directions.values()), default=0.0)
    return (0 if match.relation is Relation.COGNATE else 1, -best, normalize(match.word.text))


def schedule(matches: list[Match], *, per_day: int = DEFAULT_PER_DAY) -> tuple[list[Starter], int]:
    """Turn ranked matches into starters with a due day for every seeded direction.

    Days are offsets from today, from 1. Tightest deadline first (a direction must
    fall due strictly inside its interval), so a short near-cognate interval is
    not crowded out by long ones that could have waited. A direction with no free
    day inside its interval stays NEW. Returns the starters and that count.
    """
    tasks: list[tuple[int, int, int, Direction, float, float]] = []
    for idx, match in enumerate(matches):
        for order, direction in enumerate(Direction):
            seeded = seed_for(match.relation, direction, match.word.directions.get(direction))
            if seeded is None:
                continue
            stability, difficulty = seeded
            tasks.append((max(1, round(stability)) - 1, idx, order, direction, stability, difficulty))
    tasks.sort(key=lambda t: t[:3])

    load: Counter[int] = Counter()
    seeds: dict[int, dict[Direction, Seed]] = {}
    unplaced = 0
    for last_day, idx, _, direction, stability, difficulty in tasks:
        sibling_days = {s.offset_days for s in seeds.get(idx, {}).values()}
        day = next((d for d in range(1, last_day + 1) if load[d] < per_day and d not in sibling_days), None)
        if day is None:
            unplaced += 1
            continue
        load[day] += 1
        seeds.setdefault(idx, {})[direction] = Seed(stability, difficulty, day)
    return [Starter(m, seeds.get(i, {})) for i, m in enumerate(matches)], unplaced


@dataclass
class Plan:
    starters: list[Starter] = field(default_factory=list)
    false_friends: list[Match] = field(default_factory=list)
    duplicates: list[Match] = field(default_factory=list)
    unrelated: int = 0
    unplaced: int = 0


def plan(words: Iterable[KnownWord], dictionary: Dictionary, *, per_day: int = DEFAULT_PER_DAY) -> Plan:
    """Classify every known single word and schedule the cognates."""
    out = Plan()
    kept: list[Match] = []
    for word in words:
        match = dictionary.classify(word)
        if match.relation is Relation.FALSE_FRIEND:
            out.false_friends.append(match)
        elif match.relation is Relation.UNRELATED:
            out.unrelated += 1
        else:
            kept.append(match)
    seen: set[str] = set()
    ranked: list[Match] = []
    for match in sorted(kept, key=_rank):
        key = normalize(match.target_text or "")
        if key in seen:
            out.duplicates.append(match)
            continue
        seen.add(key)
        ranked.append(match)
    out.starters, out.unplaced = schedule(ranked, per_day=per_day)
    out.false_friends.sort(key=lambda m: normalize(m.word.text))
    return out


def known_words(db: SRSDatabase) -> list[KnownWord]:
    """Every single-word vocab card in *db*, with its per-direction state."""
    rows, _ = db.list_collocations(limit=1_000_000)
    words = []
    for _, item, _ in rows:
        unit = item.syntactic_unit
        if unit.word_count != 1 or unit.card_type != "vocab":
            continue
        directions = {d: KnownDirection(ds.state, ds.stability, ds.difficulty) for d, ds in item.directions.items()}
        words.append(KnownWord(unit.text, unit.translation, directions))
    return words


def mint(db: SRSDatabase, starters: Iterable[Starter], *, language_code: str, source_name: str) -> int:
    """Add each starter as a NEW card; the next sync mints it into Anki.

    Idempotent: a word already in *db* is left alone. Returns the number added.
    """
    added = 0
    for starter in starters:
        match = starter.match
        unit = SyntacticUnit(
            text=match.target_text or "",
            translation=match.word.translation,
            word_count=1,
            difficulty=1,
            source="cognate",
            note=f"{source_name} {match.relation.value}: {match.word.text}",
        )
        added += db.add_collocation(unit, language_code)
    return added


@dataclass
class SeedReport:
    seeded: int = 0
    not_minted: int = 0
    already_started: int = 0


def seed(db: SRSDatabase, starters: Iterable[Starter], *, language_code: str, now: datetime) -> SeedReport:
    """Seed every planned direction whose card is minted and still untouched."""
    report = SeedReport()
    for starter in starters:
        if not starter.seeds:
            continue
        guid = compute_guid(starter.match.target_text or "", language_code, "")
        item = db.get_collocation_by_guid(guid)
        row_id = db.get_collocation_id_by_guid(guid)
        for direction, s in starter.seeds.items():
            ds = item.directions.get(direction) if item is not None else None
            if row_id is None or ds is None or ds.anki_card_id is None:
                report.not_minted += 1
            elif db.seed_review_state(
                row_id,
                direction,
                stability=s.stability,
                difficulty=s.difficulty,
                due_in_days=s.offset_days,
                now=now,
            ):
                report.seeded += 1
            else:
                report.already_started += 1
    return report
