"""Fill the dictionary fields on vocab notes that already exist (tunatale-h0q3.2).

``add_dictionary_fields`` gives the notetype its five fields, empty; cards TT
mints afterwards get them filled at mint time. This fills the notes that were
there before, from the same registry facet the mint path uses
(``app.languages.dictionary_note_fields``), so a backfilled card and a newly
minted one carry the same thing.

- **Dry run by default.** It prints what would be filled and lists the words
  the dictionary does not settle (tab-separated word and English), for the
  owner. ``--apply`` writes.
- **Never overwrites.** Only an empty field is filled; one that holds anything
  (a hand correction) is left as it is.
- **Data-only.** Touched notes get ``usn = -1`` and ``mod = now``, and
  ``col.mod`` is bumped; ``col.scm`` and ``col.usn`` are not touched
  (``.claude/rules/anki-sync.md``), so an ordinary sync carries it. Field 0 is
  never written, so ``sfld``/``csum``/``guid`` stay valid.

Quit Anki first (``safe_open`` takes a verified backup before writing).

Usage:
    uv run python -m app.plugins.anki_sync.backfill_dictionary_fields --language <code> [--apply]
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.cards.vocab_notetype import DICTIONARY_FIELDS, VocabNotetype
from app.config import settings
from app.languages import dictionary_note_fields, get_vocab_notetype
from app.plugins.anki_sync.safety import safe_open
from app.plugins.anki_sync.sqlite_reader import extract_l2, extract_translation

_FIELD_SEP = "\x1f"


@dataclass(frozen=True)
class Update:
    note_id: int
    word: str
    english: str
    fields: dict[str, str]  # only the empty fields this fills


@dataclass(frozen=True)
class Miss:
    note_id: int
    word: str
    english: str


@dataclass
class Plan:
    field_names: tuple[str, ...]
    updates: list[Update] = field(default_factory=list)
    misses: list[Miss] = field(default_factory=list)
    already_filled: int = 0


@dataclass(frozen=True)
class Result:
    updates: int
    misses: int
    already_filled: int


def plan_backfill(conn: sqlite3.Connection, vocab: VocabNotetype, language_code: str) -> Plan:
    """What filling *vocab*'s notes in *conn* would write. Reads only."""
    row = conn.execute("SELECT id FROM notetypes WHERE name = ?", (vocab.name,)).fetchone()
    if row is None:
        raise ValueError(f"Notetype {vocab.name!r} not found in notetypes table")
    mid = row[0]
    names = tuple(r[0] for r in conn.execute("SELECT name FROM fields WHERE ntid = ? ORDER BY ord", (mid,)))
    if not set(DICTIONARY_FIELDS) <= set(names):
        raise ValueError(f"{vocab.name!r} has no dictionary fields yet — run add_dictionary_fields first")

    plan = Plan(field_names=names)
    for nid, flds in conn.execute("SELECT id, flds FROM notes WHERE mid = ? ORDER BY id", (mid,)):
        by_name = dict(zip(names, flds.split(_FIELD_SEP), strict=False))
        word = extract_l2(by_name.get(vocab.l2_field, ""), vocab.l2_css_class)
        english = extract_translation(by_name.get("English", ""))
        found = dictionary_note_fields(language_code, word, english)
        if not found:
            plan.misses.append(Miss(nid, word, english))
            continue
        fill = {k: v for k, v in found.items() if v and not by_name.get(k, "").strip()}
        if fill:
            plan.updates.append(Update(nid, word, english, fill))
        else:
            plan.already_filled += 1
    return plan


def apply_backfill(conn: sqlite3.Connection, plan: Plan, *, now_ms: int | None = None) -> None:
    """Write *plan* into *conn*. The caller owns the ``safe_open`` envelope; this commits."""
    if not plan.updates:
        return
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    now_ts = now_ms // 1000
    idx = {name: i for i, name in enumerate(plan.field_names)}
    for update in plan.updates:
        parts = conn.execute("SELECT flds FROM notes WHERE id = ?", (update.note_id,)).fetchone()[0].split(_FIELD_SEP)
        for name, value in update.fields.items():
            parts[idx[name]] = value
        conn.execute(
            "UPDATE notes SET flds = ?, mod = ?, usn = -1 WHERE id = ?",
            (_FIELD_SEP.join(parts), now_ts, update.note_id),
        )
    # Data-only: col.mod, never col.scm or col.usn (Layer 61).
    conn.execute("UPDATE col SET mod = ?", (now_ms,))
    conn.commit()


def run(
    *,
    language_code: str,
    anki_collection_path: Path | None = None,
    anki_backup_dir: Path | None = None,
    apply: bool = False,
) -> Result:
    """Plan (and with *apply*, write) the backfill for *language_code*'s vocab notes."""
    vocab = get_vocab_notetype(language_code)
    if vocab is None or not vocab.dictionary:
        raise ValueError(f"Language {language_code!r} mints into a notetype with no dictionary layers")
    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir

    with safe_open(anki_collection_path, backup_dir=anki_backup_dir, mode="rw" if apply else "ro") as ctx:
        plan = plan_backfill(ctx.conn, vocab, language_code)
        if apply:
            apply_backfill(ctx.conn, plan)

    tag = "[DONE]" if apply else "[DRY RUN]"
    verb = "filled" if apply else "would fill"
    print(
        f"{tag} {vocab.name!r}: {verb} {len(plan.updates)} notes; {plan.already_filled} already filled; "
        f"{len(plan.misses)} not settled by the dictionary:",
        flush=True,
    )
    for miss in plan.misses:
        print(f"  {miss.word}\t{miss.english}", flush=True)
    return Result(len(plan.updates), len(plan.misses), plan.already_filled)


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Fill the dictionary fields on existing vocab notes (data-only)")
    parser.add_argument("--language", required=True, help="Language code whose vocab notes to fill (e.g. ceb)")
    parser.add_argument("--apply", action="store_true", help="Write; without it this is a dry run")
    args = parser.parse_args()
    run(language_code=args.language, apply=args.apply)


if __name__ == "__main__":  # pragma: no cover
    _cli()
