"""Two cards for one word become one (tunatale-h0q3.71; design-short-forms-and-linkers slice 4).

An interchangeable short/long pair shares one card (Wolff, Beginning Cebuano 3 B 4),
but a deck can hold both: Cebuano 'nako' and 'ko' keyed genitive, both 'my; by me'.
The owner keeps the card with the stronger production memory and renames it to the
main spelling; the other card is removed. Its review history goes with it.

Three steps, and the sync between them is what carries them:

    apply_anki   in tt_collection, under ``safe_open``: grave the loser's note and
                 cards; rename the survivor's note (text, key, the guid derived from
                 both, and the dictionary fields of the new word)
    apply_tt     the TT survivor takes the same text and key without a dirty text
                 flag, the loser's recording and the new word's picture (both
                 flagged for the push); the loser's row is deleted
    ...          one peer-sync: the graves and the renamed note reach AnkiWeb

``apply_anki`` writes data only: ``usn = -1`` on every touched row and grave,
``col.mod`` bumped, ``col.usn`` and ``col.scm`` untouched
(``.claude/rules/anki-sync.md``). The note keeps its id and cards, so the
survivor's scheduling and revlog are not touched.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from app.cards.media import vocab_media
from app.cards.picture_redraw import apply_redraws, plan_redraws
from app.common.guid import compute_guid
from app.languages import dictionary_note_fields
from app.plugins.anki_sync.migrate_number_clozes import bump_col_mod, grave_cloze_note
from app.plugins.anki_sync.sync_writer import OfflineWriter

_AUDIO_KINDS = ("audio_forvo", "audio_tts")


@dataclass(frozen=True)
class Merge:
    """Keep *survivor_id*, remove *loser_id*; rename the survivor to *text* / *disambig* if given."""

    survivor_id: int
    loser_id: int
    text: str | None = None
    disambig: str | None = None


@dataclass(frozen=True)
class MergeStep:
    survivor_id: int
    survivor_note_id: int
    loser_id: int
    loser_note_id: int
    text: str
    disambig: str
    translation: str
    renamed: bool


def plan_merges(tt_conn: sqlite3.Connection, language_code: str, merges: list[Merge]) -> list[MergeStep]:
    """Resolve each merge against the TT rows. Reads only; raises ``ValueError`` before any write."""
    steps = []
    for m in merges:
        if m.survivor_id == m.loser_id:
            raise ValueError(f"card {m.survivor_id} cannot be merged into itself")
        rows = {}
        for coll_id in (m.survivor_id, m.loser_id):
            row = tt_conn.execute(
                "SELECT text, disambig_key, translation, anki_note_id FROM collocations"
                " WHERE id = ? AND language_code = ? AND card_type = 'vocab'",
                (coll_id, language_code),
            ).fetchone()
            if row is None:
                raise ValueError(f"no card {coll_id} in {language_code!r}")
            if row["anki_note_id"] is None:
                raise ValueError(f"card {coll_id} ({row['text']!r}) has no Anki note")
            rows[coll_id] = row
        survivor = rows[m.survivor_id]
        old = (survivor["text"], survivor["disambig_key"] or "")
        new = (m.text if m.text is not None else old[0], m.disambig if m.disambig is not None else old[1])
        if new != old:
            holder = tt_conn.execute(
                "SELECT id FROM collocations WHERE guid = ?", (compute_guid(new[0], language_code, new[1]),)
            ).fetchone()
            if holder is not None and holder["id"] not in (m.survivor_id, m.loser_id):
                raise ValueError(f"card {holder['id']} already holds {new!r}")
        steps.append(
            MergeStep(
                survivor_id=m.survivor_id,
                survivor_note_id=survivor["anki_note_id"],
                loser_id=m.loser_id,
                loser_note_id=rows[m.loser_id]["anki_note_id"],
                text=new[0],
                disambig=new[1],
                translation=survivor["translation"],
                renamed=new != old,
            )
        )
    return steps


def apply_anki(anki_conn: sqlite3.Connection, steps: list[MergeStep], language_code: str) -> None:
    """Grave each loser and rename each renamed survivor. The caller owns the ``safe_open`` envelope.

    Every rename's fields are resolved before the first write, so a note that cannot
    be read stops the batch with nothing written.
    """
    writer = OfflineWriter(anki_conn)
    renames = {}
    for step in (s for s in steps if s.renamed):
        mid = anki_conn.execute("SELECT mid FROM notes WHERE id = ?", (step.survivor_note_id,)).fetchone()
        if mid is None:
            raise ValueError(f"note {step.survivor_note_id} is not in this collection")
        names = writer._field_names_for_mid(mid[0])
        # Field 0 is the word (the L2 field on TT's vocab notetype).
        found = dictionary_note_fields(language_code, step.text, step.translation, step.disambig)
        fields = {names[0]: step.text}
        fields.update({k: v for k, v in {"DisambigKey": step.disambig, **found}.items() if k in names})
        renames[step.survivor_note_id] = fields
    # Every loser first: a survivor's new guid is its loser's.
    for step in steps:
        grave_cloze_note(anki_conn, step.loser_note_id)
    for note_id, fields in renames.items():
        writer.update_note_fields(note_id, fields, language_code=language_code)
    bump_col_mod(anki_conn)
    anki_conn.commit()


def apply_tt(db: Any, steps: list[MergeStep], language_code: str) -> None:
    """Bring each TT survivor in line with its note and delete each loser's row."""
    for step in steps:
        if step.renamed and any(db.has_media_row(step.loser_id, kind) for kind in _AUDIO_KINDS):
            for kind in _AUDIO_KINDS:
                vocab_media._drop_image_rows(db, step.survivor_id, kind)
                if db.has_media_row(step.loser_id, kind):
                    db.copy_media_row(step.loser_id, step.survivor_id, kind)
            db.add_dirty_field_by_id(step.survivor_id, "audio")
        # The loser goes before the survivor takes its name, which is the loser's guid.
        db.delete_collocation(step.loser_id)
        if step.renamed:
            db.relabel_collocation(step.survivor_id, text=step.text, disambig_key=step.disambig)
    survivors = {s.survivor_id for s in steps if s.renamed}
    apply_redraws(db, [r for r in plan_redraws(db, language_code) if r.collocation_id in survivors], language_code)
