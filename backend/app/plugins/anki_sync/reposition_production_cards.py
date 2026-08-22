"""One-shot repositioning of already-minted production cards into the reserved band.

Every production card TunaTale had ever minted was allocated ``MAX(due)+1`` — the
tail of the new-card position range. Under the Norwegian deck's DECK gather the tail
is where the queue arrives LAST, so all 290 of them sat behind 1400+ imported words
and none had ever been introduced (Layer 83, ``tunatale-uze6``).

``OfflineWriter`` allocates into the reserved band now, so cards minted from here on
land ahead of the deck. This module moves the ones that already exist. It is
idempotent: a card already sitting in its assigned slot is left alone, so a second
run reports zero moves.

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
    """What a run would do. ``moves`` is (card_id, new_due) for rows that change."""

    moves: list[tuple[int, int]]
    already_placed: int

    @property
    def total(self) -> int:
        return len(self.moves) + self.already_placed


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

    moves: list[tuple[int, int]] = []
    already_placed = 0
    for index, row in enumerate(rows):
        new_due = band_floor + index
        if row["due"] == new_due:
            already_placed += 1
        else:
            moves.append((row["card_id"], new_due))
    return RepositionPlan(moves=moves, already_placed=already_placed)


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


def mirror_positions_to_tt(tt_conn: sqlite3.Connection, plan: RepositionPlan) -> int:
    """Point TunaTale's ``anki_due`` mirror at the new positions. Returns rows updated.

    Without this the two sides disagree until the next ``sync_pull`` recomputes the
    mirror, and TunaTale keeps serving the old order in the meantime — which on a
    change whose whole purpose is queue position would read as "the fix did nothing".
    The values written are the ones just written to Anki, so no divergence is created.
    """
    updated = 0
    for card_id, new_due in plan.moves:
        cur = tt_conn.execute(
            "UPDATE collocation_directions SET anki_due = ? WHERE anki_card_id = ?",
            (new_due, card_id),
        )
        updated += cur.rowcount
    return updated
