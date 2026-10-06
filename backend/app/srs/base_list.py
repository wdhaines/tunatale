"""A base-vocabulary list (Fluent Forever's "first 625") as picture cards.

The list is data: a TSV per language (``category, english, <word>, status``) in
list order. This module is the language-agnostic half: which rows become cards,
and where in the new-card queue they go. ``scripts/seed_base_list.py`` is the
wiring.

**List order.** The source list is grouped by theme, and served that way it gave
dog, cat, fish, bird and cow on one day, which is how similar words get confused.
``frequency_order`` is the order the file is kept in instead: the most used word
first, and no two neighbours from one theme while another theme has a word
waiting. ``scripts/order_base_list.py`` applies it to the file.

**Which rows.** Only rows whose ``status`` a person or the dictionary vouched for
(``MINTABLE``); ``unconfirmed`` rows wait for acceptance. A word already in the
target DB is skipped (its existing card wins), and so is a function word — those
are clozes in this app, and a cloze needs a sentence the list does not have.

**Where.** AFTER everything already waiting, in list order. TunaTale mints its own
additions at the FRONT of the new queue (``OfflineWriter._next_front_position``,
Layer 84), which is right for a word just met in a lesson and wrong here: a
625-word list minted in chunks would otherwise jump each chunk ahead of the
cards already waiting, and later chunks ahead of earlier ones. So each card gets
a fixed position from its RANK — ``BACK_BASE + 2 * rank + ord`` — far above both
TunaTale's front allocations (around -1,000,000 and below) and an imported
deck's own positions, and independent of when its chunk is minted. That assumes
ascending ("deck") gather; ``back_positions`` is only meaningful there, and the
script refuses a descending deck.

The list owns the position of every card it added (``source == SOURCE``), so a
list card moved by hand goes back to its slot on the next ``--position``. To move
one for good, give it another source as well.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from app.common.guid import compute_guid
from app.models.srs_item import Direction
from app.models.syntactic_unit import SyntacticUnit
from app.srs.cognate_seed import normalize

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Iterable, Iterator

    from app.models.srs_item import SRSItem
    from app.srs.database import SRSDatabase

SOURCE = "base-list"
MINTABLE = frozenset({"dictionary", "variant", "usage", "reviewed", "accepted"})
BACK_BASE = 1_000_000
_ORD = {Direction.RECOGNITION: 0, Direction.PRODUCTION: 1}
# States a never-reviewed card can be in while it still holds a new-queue position.
_WAITING = frozenset({"new", "buried"})


@dataclass(frozen=True)
class BaseWord:
    rank: int
    category: str
    english: str
    text: str
    status: str


def load_base_list(lines: Iterable[str]) -> list[BaseWord]:
    """Rows of a base-list TSV, ranked in file order. ``#`` lines are comments.

    A ``#rank_offset=N`` comment starts the ranks at N, so a list meant to follow
    another (the Cebuano frequency seed after the 625) queues behind it rather
    than beside it: positions are ``BACK_BASE + 2 * rank``.
    """
    lines = list(lines)
    offset = next((int(line.partition("=")[2]) for line in lines if line.startswith("#rank_offset=")), 0)
    body = [line for line in lines if line.strip() and not line.startswith("#")]
    reader = csv.DictReader(body, delimiter="\t")
    target = reader.fieldnames[2] if reader.fieldnames else ""
    return [
        BaseWord(rank, row["category"], row["english"], row[target].strip(), row["status"])
        for rank, row in enumerate(reader, start=offset)
    ]


def load_counts(lines: Iterable[str]) -> dict[str, int]:
    """``word -> occurrences`` from a corpus frequency TSV (``word, count``). ``#`` lines are comments."""
    counts: dict[str, int] = {}
    for line in lines:
        if not line.startswith("#"):
            word, count = line.rstrip("\n").split("\t")[:2]
            counts[word] = int(count)
    return counts


def spread_by_frequency(words: Iterable[BaseWord], counts: dict[str, int]) -> list[BaseWord]:
    """*words* in frequency order, ranks untouched: most used first, themes spread out.

    Sorted by corpus count, a word the corpus lacks counting zero and ties keeping
    their old order. Then each place goes to the most used word left whose theme
    differs from the word before it; when only that theme is left, its words run
    out in order.
    """
    waiting = sorted(words, key=lambda w: -counts.get(normalize(w.text), 0))
    ordered: list[BaseWord] = []
    while waiting:
        previous = ordered[-1].category if ordered else None
        pick = next((w for w in waiting if w.category != previous), waiting[0])
        waiting.remove(pick)
        ordered.append(pick)
    return ordered


def frequency_order(words: Iterable[BaseWord], counts: dict[str, int]) -> list[BaseWord]:
    """``spread_by_frequency`` with the ranks renumbered to match, from the list's first rank.

    Ordering an ordered list changes nothing, which is what lets a test hold the
    committed file to this rule.
    """
    words = list(words)
    start = min((w.rank for w in words), default=0)
    return [replace(w, rank=rank) for rank, w in enumerate(spread_by_frequency(words, counts), start=start)]


@dataclass
class BasePlan:
    to_add: list[BaseWord]
    already_have: list[BaseWord]
    function_words: list[BaseWord]
    unconfirmed: list[BaseWord]


def plan_base_list(
    words: Iterable[BaseWord],
    *,
    have: frozenset[str],
    function_word: Callable[[str], bool],
    accept: frozenset[str] = frozenset(),
) -> BasePlan:
    """Split the list into cards to add and the rows that must wait or be skipped.

    *have* holds normalised texts already in the target DB. *accept* holds
    normalised ``unconfirmed`` words a person has vouched for.
    """
    plan = BasePlan([], [], [], [])
    for word in words:
        key = normalize(word.text)
        if key in have:
            plan.already_have.append(word)
        elif function_word(key):
            plan.function_words.append(word)
        elif word.status in MINTABLE or key in accept:
            plan.to_add.append(word)
        else:
            plan.unconfirmed.append(word)
    return plan


def mint_base_words(db: SRSDatabase, words: Iterable[BaseWord], *, language_code: str, list_name: str) -> int:
    """Add each word as a NEW picture card. Idempotent; returns how many were added."""
    added = 0
    for word in words:
        unit = SyntacticUnit(
            text=word.text,
            translation=word.english,
            word_count=1,
            difficulty=1,
            source=SOURCE,
            note=f"{list_name} · {word.category}",
        )
        added += db.add_collocation(unit, language_code)
    return added


def _waiting_slots(
    db: SRSDatabase, words: Iterable[BaseWord], language_code: str
) -> Iterator[tuple[SRSItem, Direction, int]]:
    """``(card, direction, position)`` for every card the list added that is still waiting.

    A card already introduced keeps its position: moving it would change nothing
    the learner sees and would re-push it for no reason. A never-reviewed card
    buried for the day (a sibling of today's reviews) is still waiting, so it is
    placed too; left out, it would come back tomorrow at whatever slot it had.
    """
    for word in words:
        item = db.get_collocation_by_guid(compute_guid(word.text, language_code, ""))
        # Only cards the LIST added. A word that was already a card keeps its place
        # among the cards waiting: moving it behind the list is the opposite of
        # "after the waiting cards" (23 were moved that way live, 2026-09-26).
        if item is None or item.syntactic_unit.source != SOURCE:
            continue
        for direction, ds in item.directions.items():
            if ds.reps == 0 and ds.state.value in _WAITING:
                yield item, direction, BACK_BASE + 2 * word.rank + _ORD[direction]


def back_positions(db: SRSDatabase, words: Iterable[BaseWord], *, language_code: str) -> list[tuple[int, int]]:
    """``(anki_card_id, position)`` for every minted, still-waiting card the list added.

    A card with no Anki id yet is left out: the next sync mints it at the front,
    and the ``--position`` after that places it.
    """
    return [
        (item.directions[direction].anki_card_id, position)
        for item, direction, position in _waiting_slots(db, words, language_code)
        if item.directions[direction].anki_card_id is not None
    ]


def tt_only_positions(
    db: SRSDatabase, words: Iterable[BaseWord], *, language_code: str
) -> list[tuple[str, Direction, int]]:
    """``(guid, direction, position)`` for the same cards, in a deck with no Anki behind it.

    Such a deck (a second learner's) has no card ids to key on and no collection
    to write: TunaTale's own ``anki_due`` is the queue position. A deck that IS
    linked raises, because moving only TunaTale's copy would leave Anki serving
    the old order until the next pull put it back.
    """
    out: list[tuple[str, Direction, int]] = []
    for item, direction, position in _waiting_slots(db, words, language_code):
        if item.directions[direction].anki_card_id is not None:
            raise ValueError(
                f"{item.syntactic_unit.text!r} is linked to an Anki card; position this deck without --no-anki"
            )
        out.append((item.guid, direction, position))
    return out


def apply_tt_only_positions(conn: sqlite3.Connection, positions: Iterable[tuple[str, Direction, int]]) -> int:
    """Write ``tt_only_positions`` into TunaTale's mirror. Returns how many cards moved."""
    moved = 0
    for guid, direction, position in positions:
        cur = conn.execute(
            "UPDATE collocation_directions SET anki_due = ? WHERE direction = ? AND anki_due IS NOT ?"
            " AND collocation_id = (SELECT id FROM collocations WHERE guid = ?)",
            (position, direction.value, position, guid),
        )
        moved += cur.rowcount
    return moved
