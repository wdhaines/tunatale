"""A thematic base-vocabulary list (Fluent Forever's "first 625") as picture cards.

The list is data: a TSV per language (``category, english, <word>, status``) in
list order. This module is the language-agnostic half: which rows become cards,
and where in the new-card queue they go. ``scripts/seed_base_list.py`` is the
wiring.

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
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.common.guid import compute_guid
from app.models.srs_item import Direction
from app.models.syntactic_unit import SyntacticUnit
from app.srs.cognate_seed import normalize

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from app.srs.database import SRSDatabase

MINTABLE = frozenset({"dictionary", "variant", "usage", "reviewed"})
BACK_BASE = 1_000_000
_ORD = {Direction.RECOGNITION: 0, Direction.PRODUCTION: 1}


@dataclass(frozen=True)
class BaseWord:
    rank: int
    category: str
    english: str
    text: str
    status: str


def load_base_list(lines: Iterable[str]) -> list[BaseWord]:
    """Rows of a base-list TSV, ranked in file order. ``#`` lines are comments."""
    body = [line for line in lines if line.strip() and not line.startswith("#")]
    reader = csv.DictReader(body, delimiter="\t")
    target = reader.fieldnames[2] if reader.fieldnames else ""
    return [
        BaseWord(rank, row["category"], row["english"], row[target].strip(), row["status"])
        for rank, row in enumerate(reader)
    ]


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
            source="base-list",
            note=f"{list_name} · {word.category}",
        )
        added += db.add_collocation(unit, language_code)
    return added


def back_positions(db: SRSDatabase, words: Iterable[BaseWord], *, language_code: str) -> list[tuple[int, int]]:
    """``(anki_card_id, position)`` for every minted, still-NEW card of *words*.

    A card already introduced keeps its position: moving it would change nothing
    the learner sees and would re-push it for no reason.
    """
    out: list[tuple[int, int]] = []
    for word in words:
        item = db.get_collocation_by_guid(compute_guid(word.text, language_code, ""))
        if item is None:
            continue
        for direction, ds in item.directions.items():
            if ds.anki_card_id is None or ds.reps > 0 or ds.state.value != "new":
                continue
            out.append((ds.anki_card_id, BACK_BASE + 2 * word.rank + _ORD[direction]))
    return out
