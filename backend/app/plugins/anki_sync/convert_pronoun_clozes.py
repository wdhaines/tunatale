"""Turn a language's pronoun clozes into picture cards, keeping their history (tunatale-37c1).

Cebuano ``ko`` and ``ka`` were clozes from 2026-09-28. On 2026-10-16 the owner
made them picture cards whose drawn sentence names the short-form slot, and
said to keep their review history, as with the number clozes (2026-09-03).
The same change gives ``ko``, ``mo`` and ``ta`` a second note, the owner sense
keyed ``genitive``. Everything here is read off the language's pronoun table,
so another language opts in by data.

⚠️ **THREE STEPS, EACH FOLLOWED BY A SYNC, AND THEY CANNOT BE FEWER.**

    retire   capture the cloze's history, grave its note, delete the TT row
    ...      sync: the grave reaches AnkiWeb
    add      the new subject and owner cards, the both-senses glosses
    ...      sync (twice: the pre-stage draws, then the mint)
    inherit  the captured history onto the minted subject card
    ...      sync: the history reaches Anki

The new subject card has the SAME guid as the cloze it replaces
(``compute_guid(text, lang, "")``), which is why ``add`` waits for ``retire``'s
sync: while the cloze note exists, the mint finds it by guid and links the new
card to the old note (``DuplicateNoteError`` → linked). ``inherit`` waits for the
mint because review state on a production direction with no Anki card pushes
nothing and leaves the two apps disagreeing.

The graves, the TT delete and the inheritance write are
``migrate_number_clozes``' primitives, reused as they are.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from app.cards.pronoun_picture import is_pronoun_word, load_senses, pronoun_gloss
from app.common.guid import compute_guid
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.migrate_number_clozes import _INHERITED_COLUMNS, InheritedState, is_inheritable


@dataclass(frozen=True)
class PronounCloze:
    """One pronoun cloze, and the history its replacement inherits (or None)."""

    cloze_id: int
    text: str
    cloze_note_id: int | None
    inherited: InheritedState | None


def plan_pronoun_clozes(tt_conn: sqlite3.Connection, language_code: str) -> list[PronounCloze]:
    """Every cloze whose word is now a drawn pronoun, with its history. Read-only.

    The selector is the pronoun table (``is_pronoun_word``), never a typed list.
    """
    rows = tt_conn.execute(
        """
        SELECT z.id AS cloze_id, z.text AS text, z.anki_note_id AS cloze_note_id, d.*
        FROM collocations z
        LEFT JOIN collocation_directions d ON d.collocation_id = z.id AND d.direction = 'production'
        WHERE z.card_type = 'cloze'
        ORDER BY z.text, z.id
        """
    ).fetchall()
    plan = []
    for row in rows:
        if not is_pronoun_word(row["text"], language_code):
            continue
        inherited = None
        if row["state"] is not None and is_inheritable(row["state"], row["reps"]):
            inherited = InheritedState({c: row[c] for c in _INHERITED_COLUMNS})
        plan.append(PronounCloze(row["cloze_id"], row["text"], row["cloze_note_id"], inherited))
    return plan


def _guid_taken(tt_conn: sqlite3.Connection, text: str, language_code: str, disambig: str) -> bool:
    guid = compute_guid(text, language_code, disambig)
    return tt_conn.execute("SELECT 1 FROM collocations WHERE guid = ?", (guid,)).fetchone() is not None


def _card(text: str, language_code: str, disambig: str) -> SyntacticUnit:
    return SyntacticUnit(
        text=text,
        translation=pronoun_gloss(text, language_code, disambig) or "",
        word_count=1,
        difficulty=1,
        source="user",
        lemma=text,
        card_type="vocab",
        disambig_key=disambig,
    )


def plan_new_cards(
    tt_conn: sqlite3.Connection, language_code: str, converted: Iterable[str] = ()
) -> list[SyntacticUnit]:
    """The vocab cards to add: a subject card per *converted* word, and each second sense.

    A word's subject card is planned only when its guid is free, which it is not
    while the cloze it replaces still exists. A second sense needs its word's
    subject card, existing or planned here: an owner ``ta`` with no ``ta`` would
    be a second sense of nothing. Idempotent: a card that exists is not planned.
    """
    units = [
        _card(text, language_code, "")
        for text in sorted(set(converted))
        if not _guid_taken(tt_conn, text, language_code, "")
    ]
    subjects = {u.text for u in units}
    for text, key in sorted(load_senses(language_code)):
        has_subject = text in subjects or _guid_taken(tt_conn, text, language_code, "")
        if has_subject and not _guid_taken(tt_conn, text, language_code, key):
            units.append(_card(text, language_code, key))
    return units


def gloss_updates(tt_conn: sqlite3.Connection, language_code: str) -> list[tuple[int, str, str, str]]:
    """``(id, text, old, new)`` for each subject card of a word with a second sense
    whose gloss is not the table's.

    Only those words: their subject gloss now names both senses, because the
    subject card's recognition side shows the bare word (the owner's R2). Any
    other pronoun's gloss is left as the owner set it.
    """
    updates = []
    for text in sorted({text for text, _ in load_senses(language_code)}):
        row = tt_conn.execute(
            "SELECT id, translation FROM collocations WHERE guid = ? AND card_type = 'vocab'",
            (compute_guid(text, language_code, ""),),
        ).fetchone()
        wanted = pronoun_gloss(text, language_code)
        if row is not None and wanted and row["translation"] != wanted:
            updates.append((row["id"], text, row["translation"], wanted))
    return updates


def inheritance_target(tt_conn: sqlite3.Connection, language_code: str, text: str) -> int | None:
    """The minted subject card *text*'s history goes onto, or None while it is unminted."""
    row = tt_conn.execute(
        """
        SELECT c.id FROM collocations c
        JOIN collocation_directions d ON d.collocation_id = c.id AND d.direction = 'production'
        WHERE c.guid = ? AND c.card_type = 'vocab' AND d.anki_card_id IS NOT NULL
        """,
        (compute_guid(text, language_code, ""),),
    ).fetchone()
    return row["id"] if row is not None else None
