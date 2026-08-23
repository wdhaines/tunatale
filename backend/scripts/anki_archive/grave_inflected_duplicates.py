"""Grave a TT card that duplicates an inflected form its own deck already teaches.

**The problem.** ``/listen`` decides a lemma is untracked by looking it up in
``collocations.lemma``, so a word whose lemmatizer output *is* the inflected
surface misses the card that teaches it and gets a second one. Stanza returns
``lemma='ferskt'`` for the neuter of ``fersk`` (while reducing the neuter
``helt`` to ``hel`` in the same sentence), so on 2026-08-20 the Norwegian deck
grew cid 3054 ``ferskt``: empty gloss, empty image query — the Pixabay query is
built from the translation — pushed to Anki, and failed 12 times before the user
reported it. ``fersk`` (cid 196) already carries the full entry, its
``Inflections`` table lists ``ferskt``, and its example sentence is *"Dette
brødet er ferskt."*

The creation path is fixed separately (``transcript._build_inflection_index``
consults exactly that table, so this shape cannot recur). This script is the
one-shot cleanup of the row that already exists.

**The repair.** Grave the duplicate's Anki note and drop its TT rows. Anki-safe
per `.claude/rules/anki-sync.md` §Deletes: one ``type=0`` grave per card plus one
``type=1`` grave for the note, all ``usn=-1``, ``col.mod`` bumped, ``col.usn``
and ``col.scm`` untouched (Layer 61 — ``usn`` is the sync anchor; see the
2026-08-02 forced-full-sync incident).

**Add an op only when the survivor's own inflection table lists the doomed
text.** That check is the reason this is safe to run: every other guard here
(the row exists, the language matches, the recorded note id agrees) can pass
against a mistyped id, and a mistyped id silently destroys a real card together
with its review history. ``plan_graves`` raises rather than skipping when a
guard fails, because a partially-justified batch is not a smaller batch — it is
an unreviewed one.

Media *files* are left on disk; only the ``media`` rows go. Anki's Check Media
is the tool for orphaned files, and deleting them here would race the media
pipeline for a filename another row may yet claim (``img_.jpg`` is the
empty-query name, so it is not distinctive).

Usage (dry run is the default)::

    uv run python -m scripts.anki_archive.grave_inflected_duplicates --language no
    uv run python -m scripts.anki_archive.grave_inflected_duplicates --language no --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.cards.cloze_source import parse_inflection_forms
from app.cards.field_map import inflection_labels
from app.config import settings
from app.models.syntactic_unit import deserialize_extras

_GRAVE_KIND_CARD = 0
_GRAVE_KIND_NOTE = 1


@dataclass(frozen=True)
class DuplicateOp:
    """One duplicate card, the card that already teaches it, and the language.

    ``doomed_text`` and ``doomed_nid`` are redundant with ``doomed_cid`` on
    purpose: they are the check digits that turn a mistyped id into a refusal
    instead of a deletion.
    """

    language: str
    doomed_text: str
    doomed_cid: int
    survivor_cid: int
    doomed_nid: int


#: The ops this script has been reviewed for. One entry, from the 2026-08-22
#: audit of ``translation = '' AND extras = ''`` over the Norwegian deck — cid
#: 3054 was the only such row in 3052.
OPS: tuple[DuplicateOp, ...] = (
    DuplicateOp(
        language="no",
        doomed_text="ferskt",
        doomed_cid=3054,
        survivor_cid=196,
        doomed_nid=1787228884083,
    ),
)


@dataclass(frozen=True)
class GraveRecord:
    """One planned grave: the Anki rows to remove and the TT row to drop."""

    text: str
    anki_nid: int | None
    anki_cids: tuple[int, ...]
    tt_collocation_id: int


def _inflected_forms(extras_raw: str) -> set[str]:
    """Every surface the row's inflection table lists, casefolded."""
    labels = inflection_labels()
    fields = deserialize_extras(extras_raw)
    return {form.casefold() for field in fields if field.label in labels for form in parse_inflection_forms(field.html)}


def ops_for_language(ops: list[DuplicateOp] | tuple[DuplicateOp, ...], language: str) -> list[DuplicateOp]:
    """Ops scoped to *language* — the guards read whichever TT db the CLI opened,
    so another language's op would be checked against the wrong rows."""
    return [op for op in ops if op.language == language]


def plan_graves(
    anki_conn: sqlite3.Connection,
    tt_conn: sqlite3.Connection,
    ops: list[DuplicateOp] | tuple[DuplicateOp, ...],
) -> list[GraveRecord]:
    """Validate every op and return what to remove. Raises on any failed guard.

    An op whose doomed row is already gone plans nothing — that is what a second
    run of a successful pass looks like, and it must not raise.
    """
    plan: list[GraveRecord] = []
    for op in ops:
        doomed = tt_conn.execute(
            "SELECT id, text, anki_note_id FROM collocations WHERE id = ? AND language_code = ?",
            (op.doomed_cid, op.language),
        ).fetchone()
        if doomed is None:
            continue  # already applied, or never present in this language's db

        survivor = tt_conn.execute(
            "SELECT id, text, extras FROM collocations WHERE id = ? AND language_code = ?",
            (op.survivor_cid, op.language),
        ).fetchone()
        if survivor is None:
            raise ValueError(f"{op.doomed_text!r}: survivor cid={op.survivor_cid} not found in the {op.language} db")
        if doomed["text"] != op.doomed_text:
            raise ValueError(
                f"cid={op.doomed_cid} text is {doomed['text']!r}, not {op.doomed_text!r} — wrong row, refusing"
            )
        if doomed["anki_note_id"] != op.doomed_nid:
            raise ValueError(
                f"{op.doomed_text!r}: recorded note id is {doomed['anki_note_id']}, not {op.doomed_nid} — refusing"
            )
        if op.doomed_text.casefold() not in _inflected_forms(survivor["extras"] or ""):
            raise ValueError(
                f"survivor {survivor['text']!r} does not list {op.doomed_text!r} in its inflection table — "
                "the duplicate would take a form nothing else teaches"
            )

        cids = tuple(
            r[0] for r in anki_conn.execute("SELECT id FROM cards WHERE nid = ? ORDER BY ord", (op.doomed_nid,))
        )
        note_present = anki_conn.execute("SELECT 1 FROM notes WHERE id = ?", (op.doomed_nid,)).fetchone() is not None
        plan.append(
            GraveRecord(
                text=op.doomed_text,
                anki_nid=op.doomed_nid if note_present else None,
                anki_cids=cids,
                tt_collocation_id=op.doomed_cid,
            )
        )
    return plan


def apply_graves(
    anki_conn: sqlite3.Connection,
    tt_conn: sqlite3.Connection,
    plan: list[GraveRecord],
) -> dict[str, int]:
    """Apply *plan*. ``col.mod`` is bumped only when something was graved, so a
    no-op run leaves the collection byte-identical."""
    counts = {"notes_graved": 0, "cards_graved": 0, "tt_collocations_deleted": 0}
    if not plan:
        return counts

    for item in plan:
        for cid in item.anki_cids:
            anki_conn.execute(
                "INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)",
                (cid, _GRAVE_KIND_CARD),
            )
            anki_conn.execute("DELETE FROM cards WHERE id = ?", (cid,))
            counts["cards_graved"] += 1
        if item.anki_nid is not None:
            anki_conn.execute(
                "INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)",
                (item.anki_nid, _GRAVE_KIND_NOTE),
            )
            anki_conn.execute("DELETE FROM notes WHERE id = ?", (item.anki_nid,))
            counts["notes_graved"] += 1

        for table in ("collocation_directions", "tt_revlog", "media"):
            tt_conn.execute(f"DELETE FROM {table} WHERE collocation_id = ?", (item.tt_collocation_id,))
        tt_conn.execute("DELETE FROM collocations WHERE id = ?", (item.tt_collocation_id,))
        counts["tt_collocations_deleted"] += 1

    if counts["notes_graved"] or counts["cards_graved"]:
        # Data-only: col.mod tells Anki the collection changed; col.scm stays put
        # so this remains an incremental sync, and col.usn is the sync ANCHOR,
        # never a dirty flag (Layer 61 — anki-sync.md §Deletes).
        anki_conn.execute("UPDATE col SET mod = ?", (int(time.time() * 1000),))
    anki_conn.commit()
    tt_conn.commit()
    return counts


def _print_plan(plan: list[GraveRecord]) -> None:
    print(f"Plan: grave {len(plan)} inflected duplicate(s)")
    for it in plan:
        if it.anki_nid is None:
            print(f"  {it.text!r}: tt_cid={it.tt_collocation_id} (no Anki note — TT row only)")
        else:
            cids = ", ".join(str(c) for c in it.anki_cids)
            print(f"  {it.text!r}: nid={it.anki_nid} cards=[{cids}] tt_cid={it.tt_collocation_id}")


def _open_tt(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_tt_db_path(override: Path | None, language_code: str) -> Path:
    """TT db for *language_code*, via the registry — never ``settings.database_url``,
    which is the single-language default and does not follow ``--language``
    (the silent "Nothing to grave" of 2026-08-03)."""
    if override is not None:
        return override
    from app.languages import resolve_language_context

    ctx = resolve_language_context(language_code, settings)
    return Path(ctx.db_url.removeprefix("sqlite:///"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grave TT cards duplicating an inflected form.")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    parser.add_argument("--language", default=None, help="language code (default: settings.target_language)")
    parser.add_argument("--anki-db", type=Path, default=None, help="override Anki collection path")
    parser.add_argument("--tt-db", type=Path, default=None, help="override TT database path")
    args = parser.parse_args(argv)

    anki_path = args.anki_db or settings.anki_collection_path
    language_code = args.language or settings.target_language
    tt_path = _resolve_tt_db_path(args.tt_db, language_code)

    if not Path(anki_path).exists() or not Path(tt_path).exists():
        missing = anki_path if not Path(anki_path).exists() else tt_path
        print(f"Database not found: {missing}", file=sys.stderr)
        return 1

    ops = ops_for_language(OPS, language_code)
    if not ops:
        print(f"No ops for language {language_code!r}.")
        return 0

    if not args.apply:
        from app.plugins.anki_sync.safety import _register_anki_collations

        anki_conn = sqlite3.connect(f"file:{anki_path}?mode=ro", uri=True)
        anki_conn.row_factory = sqlite3.Row
        _register_anki_collations(anki_conn)
        tt_conn = _open_tt(tt_path)
        try:
            plan = plan_graves(anki_conn, tt_conn, ops)
            if not plan:
                print("Nothing to grave.")
            else:
                _print_plan(plan)
                print("dry run: no changes applied. Re-run with --apply.")
            return 0
        finally:
            anki_conn.close()
            tt_conn.close()

    from app.plugins.anki_sync.safety import safe_open

    tt_conn = _open_tt(tt_path)
    try:
        with safe_open(Path(anki_path), mode="rw") as ctx:
            plan = plan_graves(ctx.conn, tt_conn, ops)
            if not plan:
                print("Nothing to grave.")
                return 0
            _print_plan(plan)
            counts = apply_graves(ctx.conn, tt_conn, plan)
            print(f"Applied: {counts}")
    finally:
        tt_conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
