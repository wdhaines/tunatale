"""Retire the cloze production cards minted for number words (tunatale-y27g).

``jeg har ___ barn`` admits every number, so a cloze on a numeral constrains
nothing and marks a right answer wrong. ``app.cards.number_image`` fixed the
ROUTING — new promotions draw a counting picture — but that serves new
promotions only. Words already carrying a cloze cannot self-heal, because
``_AWAITING_PRODUCTION_WHERE`` excludes any word a cloze points at, so they are
permanently outside the promotion queue.

This module removes those clozes so the base word re-enters it. The pre-stage
then renders the counting picture (locally — ``render_count_svg``, no network)
and the next sync mints an image production card.

⚠️ **TWO STEPS, AND THEY CANNOT BE ONE.** The new card does not exist until a
sync mints it, and the mint refuses any word that already has a production
direction. So the inherited state cannot be written up front — seeding it would
remove the word from the queue and no card would ever be minted. Hence:

    step 1  plan → capture state → delete TT cloze rows + grave the Anki notes
    ...     the user syncs; the pre-stage draws, the mint creates the card
    step 2  apply the captured state onto the newly minted production direction

The captured state is written to a file between the two so step 2 does not have
to re-derive anything from rows step 1 deleted.

⚠️ **Inheriting is the user's decision, taken 2026-09-03**: the new image card
carries the cloze card's scheduling rather than starting fresh, so a word already
known stays known and a word being drilled stays drilled.

**But most of these have nothing to inherit.** Four of the eight sit at
``state='new'``, ``reps=0`` with ``stability=1.0`` / ``fsrs_difficulty=5.0`` —
the SCHEMA DEFAULTS, not a measurement. Carrying those would fabricate a memory
state the learner never earned. :func:`is_inheritable` keys on
``state != 'new'``, never on the FSRS numbers, for exactly that reason: the
defaults are not None and reading them as history is a documented trap.
"""

from __future__ import annotations

import sqlite3
import time as _time
from dataclasses import asdict, dataclass, field

#: Anki grave kinds (``rslib/src/storage/graves/mod.rs``): a note delete writes
#: one row per card plus one for the note.
_GRAVE_KIND_CARD = 0
_GRAVE_KIND_NOTE = 1

#: The direction columns that carry scheduling history. Deliberately NOT every
#: column: ``anki_card_id`` / ``anki_due`` / ``anki_card_mod`` describe the OLD
#: cloze card and must not follow the state onto a different card, and
#: ``last_synced_at`` is bookkeeping about a sync that no longer applies.
_INHERITED_COLUMNS = (
    "stability",
    "fsrs_difficulty",
    "due_at",
    "reps",
    "lapses",
    "state",
    "last_review",
    "last_review_time_ms",
    "last_rating",
    "left",
    "prior_state",
    "prior_left",
    "prior_stability",
    "introduced_at",
)


@dataclass(frozen=True)
class InheritedState:
    """The cloze production card's scheduling, ready to write onto its successor."""

    values: dict[str, object] = field(default_factory=dict)

    @property
    def state(self) -> str | None:
        return self.values.get("state")  # type: ignore[return-value]

    @property
    def reps(self) -> int:
        return int(self.values.get("reps") or 0)


@dataclass(frozen=True)
class NumberClozeItem:
    """One number word's cloze, and what should become of it."""

    base_id: int
    base_text: str
    cloze_id: int
    cloze_note_id: int | None
    inherited: InheritedState | None

    @property
    def inherits(self) -> bool:
        return self.inherited is not None


def is_inheritable(state: str | None, reps: int | None) -> bool:
    """Whether this direction carries history worth moving.

    ⚠️ Keyed on ``state``/``reps``, NEVER on ``stability``/``fsrs_difficulty``.
    Those columns default to ``1.0`` and ``5.0`` rather than NULL, so a brand-new
    direction looks numerically identical to one with a genuine memory state.
    Reading them as evidence would fabricate history for four of the eight words
    this migration touches.
    """
    return (state or "new") != "new" or bool(reps)


def plan_migration(tt_conn: sqlite3.Connection, base_texts: list[str]) -> list[NumberClozeItem]:
    """What to migrate, and which items carry state. Read-only.

    ``base_texts`` is supplied by the caller rather than derived here so the
    selection is a decision made once, against the shipping vocabulary, and can
    be printed and reviewed before anything is written.
    """
    if not base_texts:
        return []
    placeholders = ",".join("?" * len(base_texts))
    rows = tt_conn.execute(
        f"""
        SELECT z.id AS cloze_id, z.anki_note_id AS cloze_note_id,
               b.id AS base_id, b.text AS base_text,
               d.*
        FROM collocations z
        JOIN collocations b ON b.id = z.base_collocation_id
        LEFT JOIN collocation_directions d
               ON d.collocation_id = z.id AND d.direction = 'production'
        WHERE z.card_type = 'cloze' AND b.text IN ({placeholders})
        ORDER BY b.text
        """,
        base_texts,
    ).fetchall()

    items: list[NumberClozeItem] = []
    for row in rows:
        keys = row.keys()
        state = row["state"] if "state" in keys else None
        reps = row["reps"] if "reps" in keys else None
        inherited = None
        if state is not None and is_inheritable(state, reps):
            inherited = InheritedState({c: row[c] for c in _INHERITED_COLUMNS if c in keys})
        items.append(
            NumberClozeItem(
                base_id=row["base_id"],
                base_text=row["base_text"],
                cloze_id=row["cloze_id"],
                cloze_note_id=row["cloze_note_id"],
                inherited=inherited,
            )
        )
    return items


def grave_cloze_note(anki_conn: sqlite3.Connection, note_id: int) -> int:
    """Delete one Cloze note through ``graves``. Returns the number of cards removed.

    Mirrors Anki's own ``remove_notes_inner``: one ``type=0`` grave per card plus
    one ``type=1`` grave for the note, every grave ``usn=-1``.

    A note that is not there is a no-op returning 0 — two of the eight point at
    Anki notes that do not exist and carry no grave, so there is nothing to
    delete and nothing to record.

    ⚠️ The caller bumps ``col.mod`` once after the batch. This does NOT touch
    ``col.usn`` (the sync anchor — Layer 61) and does NOT touch ``col.scm``:
    a delete is data-only and must not force a full upload.
    """
    card_ids = [r[0] for r in anki_conn.execute("SELECT id FROM cards WHERE nid = ?", (note_id,))]
    if not card_ids and anki_conn.execute("SELECT 1 FROM notes WHERE id = ?", (note_id,)).fetchone() is None:
        return 0
    for cid in card_ids:
        anki_conn.execute("INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)", (cid, _GRAVE_KIND_CARD))
        anki_conn.execute("DELETE FROM cards WHERE id = ?", (cid,))
    anki_conn.execute("INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)", (note_id, _GRAVE_KIND_NOTE))
    anki_conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return len(card_ids)


def bump_col_mod(anki_conn: sqlite3.Connection) -> None:
    """One ``col.mod`` bump after the batch. Never ``col.usn``, never ``col.scm``."""
    anki_conn.execute("UPDATE col SET mod = ?", (int(_time.time() * 1000),))


def delete_tt_clozes(tt_conn: sqlite3.Connection, cloze_ids: list[int]) -> int:
    """Remove the TT cloze rows so the base words re-enter the promotion queue.

    The FK cascade drops their direction rows with them, which is why the state
    must be captured BEFORE this runs.
    """
    if not cloze_ids:
        return 0
    placeholders = ",".join("?" * len(cloze_ids))
    cur = tt_conn.execute(f"DELETE FROM collocations WHERE id IN ({placeholders})", cloze_ids)
    return cur.rowcount


def apply_inheritance(tt_conn: sqlite3.Connection, base_id: int, inherited: InheritedState) -> bool:
    """Write the captured scheduling onto the base word's production direction.

    Step 2, run after a sync has minted the card. Returns False when no
    production direction exists yet — the mint has not reached this word, and the
    caller should sync again rather than treat it as an error.

    ``dirty_fsrs = 1`` so the next push carries the state into Anki; without it
    TunaTale would know the card is a review card and Anki would still think it
    is new.
    """
    row = tt_conn.execute(
        "SELECT 1 FROM collocation_directions WHERE collocation_id = ? AND direction = 'production'",
        (base_id,),
    ).fetchone()
    if row is None:
        return False
    columns = [c for c in _INHERITED_COLUMNS if c in inherited.values]
    assignments = ", ".join(f'"{c}" = ?' for c in columns)
    tt_conn.execute(
        f"UPDATE collocation_directions SET {assignments}, dirty_fsrs = 1 "
        "WHERE collocation_id = ? AND direction = 'production'",
        [inherited.values[c] for c in columns] + [base_id],
    )
    return True


def state_to_json(items: list[NumberClozeItem]) -> list[dict]:
    """Serialise the plan for the file that carries step 1's capture into step 2."""
    return [
        {
            "base_id": item.base_id,
            "base_text": item.base_text,
            "cloze_id": item.cloze_id,
            "cloze_note_id": item.cloze_note_id,
            "inherited": asdict(item.inherited)["values"] if item.inherited else None,
        }
        for item in items
    ]


def state_from_json(payload: list[dict]) -> list[NumberClozeItem]:
    return [
        NumberClozeItem(
            base_id=d["base_id"],
            base_text=d["base_text"],
            cloze_id=d["cloze_id"],
            cloze_note_id=d["cloze_note_id"],
            inherited=InheritedState(d["inherited"]) if d["inherited"] else None,
        )
        for d in payload
    ]
