"""One-shot repositioning of already-minted production cards into the reserved band.

Every production card TunaTale had ever minted was allocated ``MAX(due)+1`` — the
tail of the new-card position range. Under the Norwegian deck's DECK gather the tail
is where the queue arrives LAST, so all 290 of them sat behind 1400+ imported words
and none had ever been introduced (Layer 83, ``tunatale-uze6``).

``OfflineWriter`` allocates into the reserved band now, so cards minted from here on
land ahead of the deck. This module moves the ones that already exist.

⚠️ **It is idempotent only against a collection that has not been minted into since**,
and that qualifier is the whole of tunatale-qf6.15. A card already sitting in its
assigned slot is left alone — but "its assigned slot" is recomputed from note-id order
on every run, so ONE card minted at the head shifts every later index by one and a
second run reports ~the whole population as moves. ``plan.moves`` is therefore NOT the
question "has this already run?"; ``cards_outside_band`` is. Callers must consult it
before repositioning, because a gratuitous renumber races any concurrent mint (both
read-the-max-then-write) and lands two cards on one ``due``.

**Order is the point, not just the range.** Cards are assigned band slots by
``notes.id`` ascending, which for the imported deck is deck (frequency) order —
`Spearman(note_id, zipf) = -0.701`, measured 2026-08-21, head `være/og/i/en/det`.
Card id is NOT a substitute: it correlates the other way (+0.509). TunaTale's own
notes carry ms-epoch ids and therefore sort after the whole imported deck, which is
the intended precedence.

**Deck-scoped on purpose.** The band is only ahead of the queue under ascending
gather. The Slovene deck is on HighestPosition, where the tail already IS the front,
so running this against it would move its production cards to the BACK. The CLI
refuses unless the deck gathers ascending.

No schema change, no deletes: ``cards.due`` only, with ``usn = -1`` and ``mod = now``
per row and one ``col.mod`` bump after the batch (`.claude/rules/anki-sync.md`
§"Required writes for every mutation"). ``col.scm`` is untouched, so this does not
force a full sync.
"""

from __future__ import annotations

import sqlite3
import time as _time
from dataclasses import dataclass

from app.plugins.anki_sync.add_production_template import PRODUCTION_TEMPLATE
from app.plugins.anki_sync.sync_writer import _PRODUCTION_BAND_CEILING, _PRODUCTION_BAND_FLOOR

#: NEW cards TunaTale minted as a production direction: one at the notetype's
#: ``Production`` template, or the single card of a Cloze note (the non-imageable
#: production branch). Ordered by note id — see the module docstring.
_SELECT_MINTED_PRODUCTION = f"""
    SELECT c.id AS card_id, c.due AS due
    FROM cards c
    JOIN notes n ON n.id = c.nid
    LEFT JOIN templates t ON t.ntid = n.mid AND t.ord = c.ord
    LEFT JOIN notetypes nt ON nt.id = n.mid
    WHERE c.type = 0
      AND c.did = ?
      AND (t.name = '{PRODUCTION_TEMPLATE}' OR nt.name = 'Cloze')
    ORDER BY c.nid ASC, c.id ASC
"""


@dataclass(frozen=True)
class RepositionPlan:
    """The band slot every minted production card should hold, and which ones move.

    ``assignments`` is the full desired mapping; ``moves`` is the subset whose value
    in the collection differs from it.

    ⚠️ The two are NOT interchangeable, and conflating them cost a real repair on
    2026-08-22: the TunaTale mirror must be driven by ``assignments``, because a run
    where Anki is already correct but the mirror is stale has an empty ``moves`` and
    is exactly the case that needs repairing. Driving the mirror off ``moves`` makes
    such a run a silent no-op — the state it is least able to fix.
    """

    assignments: list[tuple[int, int]]
    moves: list[tuple[int, int]]

    @property
    def already_placed(self) -> int:
        return len(self.assignments) - len(self.moves)

    @property
    def total(self) -> int:
        return len(self.assignments)


def plan_repositioning(
    conn: sqlite3.Connection,
    deck_id: int,
    *,
    band_floor: int = _PRODUCTION_BAND_FLOOR,
    band_ceiling: int = _PRODUCTION_BAND_CEILING,
) -> RepositionPlan:
    """Assign consecutive band slots to the deck's minted production cards.

    Read-only. Raises ValueError if the deck holds more minted production cards than
    the band has room for — a million slots against a 3009-word deck, so reaching it
    means something upstream is minting in a loop.

    The band defaults to ``OfflineWriter``'s, so the cards this moves and the cards it
    mints from here on share one range. It is a parameter only so the exhaustion guard
    is reachable from a test without a narrow band being a production possibility.
    """
    rows = conn.execute(_SELECT_MINTED_PRODUCTION, (deck_id,)).fetchall()
    capacity = band_ceiling - band_floor
    if len(rows) > capacity:
        raise ValueError(f"{len(rows)} minted production cards exceed the band's {capacity} slots")

    assignments: list[tuple[int, int]] = []
    moves: list[tuple[int, int]] = []
    for index, row in enumerate(rows):
        new_due = band_floor + index
        assignments.append((row["card_id"], new_due))
        if row["due"] != new_due:
            moves.append((row["card_id"], new_due))
    return RepositionPlan(assignments=assignments, moves=moves)


def cards_outside_band(
    conn: sqlite3.Connection,
    deck_id: int,
    *,
    band_floor: int = _PRODUCTION_BAND_FLOOR,
    band_ceiling: int = _PRODUCTION_BAND_CEILING,
) -> list[int]:
    """Minted production cards sitting OUTSIDE the reserved band — the repair still owed.

    This, not ``plan.moves``, is the question "has the one-shot migration already
    run?". The two come apart the moment a sync mints anything: a new production
    card gets ``MAX(due in band) + 1`` from ``OfflineWriter._next_production_position``
    so it lands INSIDE the band and is fine, while a fresh ``plan_repositioning``
    renumbers by note id — and one new card at the head shifts every later index by
    one, making ``moves`` almost the whole population. A guard keyed on ``moves``
    would therefore never fire on precisely the runs it exists to stop
    (tunatale-qf6.15; the observed re-run reported "already placed 1, to move 309").

    Empty means the collection half is done. It does NOT mean the TT mirror is —
    see ``read_band_positions``.
    """
    rows = conn.execute(_SELECT_MINTED_PRODUCTION, (deck_id,)).fetchall()
    return [row["card_id"] for row in rows if not band_floor <= row["due"] < band_ceiling]


def read_band_positions(conn: sqlite3.Connection, deck_id: int) -> list[tuple[int, int]]:
    """Every minted production card paired with the position the collection ACTUALLY holds.

    The mirror source for a run that is NOT repositioning. ``plan.assignments`` is
    only truthful when it is about to be written; on the refusal path it is a
    hypothetical the collection does not hold, so mirroring it would MANUFACTURE
    the divergence the mirror exists to prevent — writing an ``anki_due`` no Anki
    card has.
    """
    rows = conn.execute(_SELECT_MINTED_PRODUCTION, (deck_id,)).fetchall()
    return [(row["card_id"], row["due"]) for row in rows]


def apply_repositioning(conn: sqlite3.Connection, plan: RepositionPlan) -> None:
    """Write the planned positions. Caller owns the transaction boundary.

    A no-op plan touches nothing — not even ``col.mod`` — so a second run leaves the
    collection byte-identical rather than looking like a change to the next sync.
    """
    if not plan.moves:
        return
    ts = int(_time.time())
    conn.executemany(
        "UPDATE cards SET due = ?, mod = ?, usn = -1 WHERE id = ?",
        [(new_due, ts, card_id) for card_id, new_due in plan.moves],
    )
    # col.mod only — never col.usn, which is the sync anchor (Layer 61).
    conn.execute("UPDATE col SET mod = ?", (ts,))


def mirror_positions_to_tt(tt_conn: sqlite3.Connection, positions: list[tuple[int, int]]) -> int:
    """Point TunaTale's ``anki_due`` mirror at ``positions``. Returns rows updated.

    Without this the two sides disagree until the next ``sync_pull`` recomputes the
    mirror, and TunaTale keeps serving the old order in the meantime — which on a
    change whose whole purpose is queue position would read as "the fix did nothing".

    ⚠️ **Pass positions Anki holds or is about to hold, never a hypothetical.** Two
    callers, and choosing wrongly between them creates the exact divergence this
    repairs:

    - repositioning: ``plan.assignments`` — the values ``apply_repositioning`` just
      wrote. NOT ``plan.moves``; a run whose collection half already landed still
      has a mirror to repair, and that is precisely the run whose ``moves`` is empty
      (the regression that cost a real repair on 2026-08-22).
    - refusing to reposition: ``read_band_positions(conn, deck_id)`` — what the
      collection actually holds. A fresh plan's assignments are wrong here.
    """
    updated = 0
    for card_id, new_due in positions:
        cur = tt_conn.execute(
            "UPDATE collocation_directions SET anki_due = ? WHERE anki_card_id = ?",
            (new_due, card_id),
        )
        updated += cur.rowcount
    return updated
