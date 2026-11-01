"""What a live deck needs to change for first-class short forms (tunatale-0rlg). Read-only.

A shared card's comma headword ('dili, di') becomes its long form, since the short
form now has its own line. A second sense whose line tells its recognition front
apart no longer needs that card suspended. Both are read off the language's tables.
Writes go through ``SRSDatabase``, so the next sync pushes them.
"""

from __future__ import annotations

import sqlite3

from app.cards.pronoun_picture import has_short_form, is_second_sense
from app.common.guid import compute_guid
from app.languages import short_form_pairs


def plan_front_renames(tt_conn: sqlite3.Connection, language_code: str) -> list[tuple[int, str, str]]:
    """``(id, old, new)`` for each vocab card fronted 'long, short' of a pair that shares a card.

    Raises ``ValueError`` if a card already holds the long form: two cards for one
    word is the owner's call, and the rename would break the guid's uniqueness.
    """
    shared = {(p.long, p.short) for p in short_form_pairs(language_code) if p.shares_card}
    renames = []
    for row in tt_conn.execute(
        "SELECT id, text, disambig_key FROM collocations"
        " WHERE language_code = ? AND card_type = 'vocab' AND text LIKE '%,%' ORDER BY id",
        (language_code,),
    ).fetchall():
        parts = tuple(p.strip() for p in row["text"].casefold().split(","))
        if parts not in shared:
            continue
        new = row["text"].split(",")[0].strip()
        guid = compute_guid(new, language_code, row["disambig_key"] or "")
        if tt_conn.execute("SELECT 1 FROM collocations WHERE guid = ?", (guid,)).fetchone():
            raise ValueError(f"cannot rename {row['text']!r}: a card for {new!r} already exists")
        renames.append((row["id"], row["text"], new))
    return renames


def plan_unsuspends(tt_conn: sqlite3.Connection, language_code: str) -> list[tuple[int, str, str]]:
    """``(id, text, key)`` for each second sense suspended on recognition that its Short form line tells apart."""
    rows = tt_conn.execute(
        """
        SELECT c.id, c.text, c.disambig_key FROM collocations c
        JOIN collocation_directions d ON d.collocation_id = c.id
        WHERE c.language_code = ? AND c.card_type = 'vocab' AND d.direction = 'recognition' AND d.state = 'suspended'
        ORDER BY c.id
        """,
        (language_code,),
    ).fetchall()
    return [
        (r["id"], r["text"], r["disambig_key"])
        for r in rows
        if is_second_sense(r["text"], language_code, r["disambig_key"] or "")
        and has_short_form(r["text"], language_code, r["disambig_key"] or "")
    ]
