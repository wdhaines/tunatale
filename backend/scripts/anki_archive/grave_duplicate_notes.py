"""Grave the redundant Anki note when one word ships on two notes.

**The problem.** A TT guid is ``(text, language, disambig_key)`` and TT stores
disambig as the part of speech, so two Anki notes sharing the same **(text,
POS)** collapse to ONE collocation with TWO candidate cards — and nothing pins
which one ``anki_card_id`` references. ``foran`` (#518 / #5664, *both* tagged
``preposition``, same IPA, same Forvo file, same translation) re-pointed on
2026-07-14 and then alternated: from that date its ``tt_revlog`` rows land on
whichever card TT happened to reference, splitting one word's review history
across two cards and letting TT carry a due date belonging to the card it
wasn't tracking.

**This is NOT about words appearing twice.** The Norwegian deck has 18 such
words, but 17 are genuine POS homonyms — ``løfte`` noun "promise" / verb
"lift", ``vår`` noun "spring" / determinative "our", ``om`` conj/adv/prep —
and TT already handles them correctly: different POS ⇒ different disambig_key
⇒ different guid ⇒ a separate collocation pinned to its own card. Verified: all
17 have as many TT collocations as Anki notes. **Do not grave homonyms.**

``('foran', 'preposition')`` was the only collapsing group in the *Norwegian*
deck. Do not hand-roll this check over raw note fields — that misses **cloze**
notes, whose guid text is the cloze body with an empty disambig (a hand-rolled
scan reported Slovene clean; the real detector found two duplicate cloze notes
there). Use ``AnkiSync.warn_if_guid_collisions``, which keys on the same
extraction the guid does, and read its ``GUID_COLLISION`` lines.

**The repair.** Grave the twin TT does *not* track, so the word has exactly one
card again. Anki-safe per `.claude/rules/anki-sync.md` §Deletes: ``graves``
rows (never a bare DELETE), ``usn=-1`` on the graves, ``col.mod`` bumped,
``col.scm`` and ``col.usn`` untouched (Layer 61 — usn is the sync anchor; see
the 2026-08-02 forced-full-sync incident).

Add an op only for a genuine (text, POS) collapse — check the detector shape
above first, never the bare surface form.

Ops are scoped to a language and planned only against that language's TT db —
the guard below reads whichever db the CLI opened, so a Slovene op checked
during a Norwegian run would pass vacuously. When the twin worth keeping is the
one TT does *not* track, mark the op ``repoint=True``: TT's pointers move onto
the survivor first, and only then does the grave become legal.

Usage (dry run is the default; ``--language`` defaults to
``settings.target_language``)::

    uv run python -m scripts.anki_archive.grave_duplicate_notes --language sl
    uv run python -m scripts.anki_archive.grave_duplicate_notes --language sl --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from dataclasses import dataclass

_GRAVE_KIND_CARD = 0
_GRAVE_KIND_NOTE = 1


@dataclass(frozen=True)
class DuplicateOp:
    """One word, its redundant note, and the note that must survive.

    ``language`` names the TT db that holds this word — ops are filtered by it
    (``ops_for_language``) before planning, because ``plan_graves``' "TT points
    at the doomed note" guard reads whichever db the CLI opened and would pass
    vacuously against another language's.

    ``repoint`` inverts which twin TT tracks: normally the survivor is the note
    TT already references and the guard refuses to grave a tracked note. Set it
    when the twin worth keeping is the one TT does *not* track — then
    ``plan_repoints`` moves TT's pointers onto the survivor first, satisfying
    the guard. Re-pointing (rather than graving what TT tracks) is what keeps
    ``detect_and_reset_orphans`` from hard-deleting the collocation on the next
    pull.
    """

    word: str
    doomed_nid: int
    survivor_nid: int
    language: str
    repoint: bool = False


@dataclass(frozen=True)
class GraveRecord:
    word: str
    anki_nid: int
    anki_cids: tuple[int, ...]


@dataclass(frozen=True)
class RepointRecord:
    """A TT collocation's move from the doomed twin onto the survivor."""

    word: str
    tt_collocation_id: int
    from_nid: int
    to_nid: int
    card_moves: tuple[tuple[int, int], ...]


# foran: #518 (nid …300232, cid …300233, reps 45) survives — it is what TT
# tracks and its reps match TT's. #5664 (nid …305378, cid …305379, reps 49)
# is graved; its 4 extra reps are the grades that landed on the wrong twin.
#
# The two Slovene entries are cloze notes, and their collision has a different
# cause than foran's: `create_cloze_note` stamps `compute_guid(cloze_text, …)`
# and refuses a duplicate, but it minted these from UNPUNCTUATED source text —
# recovered from the stored guids, the creation-time texts were
# "Kako {{c1::si}}" and "To {{c1::je}} dobro Center {{c1::je}} zanimiv". The
# punctuation was restored afterwards, and Anki never rewrites a note's guid on
# edit, so each drifted into (text, disambig) equality with a pre-existing note
# while staying distinct at guid level. A creation-time guid guard structurally
# cannot see that; the GUID_COLLISION tripwire is what catches it after the fact.
#
# Survivors chosen on gloss accuracy + review history (2026-08-03):
#   "Kako {{c1::si}}?" — keep …7550779 ("you are"; si = 2sg biti). It is also
#   the history TT already carries (reps 3, last review 06-12), so this one
#   needs the re-point: TT's pointer is on the doomed …9239095.
#   "To {{c1::je}} dobro. …" — keep …9239083 ("is"; `to` is the subject, so `je`
#   alone is not "it is") with 17 revlog rows / 12 reps / 1 lapse against the
#   doomed twin's 4. TT already points here, so no re-point — the next sync_pull
#   re-reads this card and corrects the June state TT is holding.
DUPLICATE_OPS: tuple[DuplicateOp, ...] = (
    DuplicateOp(word="foran", doomed_nid=1696398305378, survivor_nid=1696398300232, language="no"),
    DuplicateOp(
        word="Kako {{c1::si}}?",
        doomed_nid=1778769239095,
        survivor_nid=1780517550779,
        language="sl",
        repoint=True,
    ),
    DuplicateOp(
        word="To {{c1::je}} dobro. Center {{c1::je}} zanimiv.",
        doomed_nid=1780596907048,
        survivor_nid=1778769239083,
        language="sl",
    ),
)


def ops_for_language(
    language: str,
    ops: tuple[DuplicateOp, ...] | list[DuplicateOp] = DUPLICATE_OPS,
) -> list[DuplicateOp]:
    """Ops belonging to *language*, so each is planned against its own TT db."""
    return [op for op in ops if op.language == language]


def plan_graves(
    anki_conn: sqlite3.Connection,
    tt_conn: sqlite3.Connection,
    ops: tuple[DuplicateOp, ...] | list[DuplicateOp] = DUPLICATE_OPS,
) -> list[GraveRecord]:
    """Resolve *ops* into grave records, refusing anything unsafe.

    Raises ValueError if the survivor is missing (we'd delete the word's only
    note) or if a TT collocation points at the doomed note (graving it would
    strand that collocation — ``detect_and_reset_orphans`` would then hard-delete
    it on the next pull). A doomed note that is already gone yields no record,
    so re-running is a no-op.
    """
    items: list[GraveRecord] = []
    for op in ops:
        survivor = anki_conn.execute("SELECT id FROM notes WHERE id = ?", (op.survivor_nid,)).fetchone()
        if survivor is None:
            raise ValueError(f"{op.word}: survivor note {op.survivor_nid} not found — refusing to grave the twin")

        doomed = anki_conn.execute("SELECT id FROM notes WHERE id = ?", (op.doomed_nid,)).fetchone()
        if doomed is None:
            continue  # already graved; idempotent re-run

        tracked = tt_conn.execute("SELECT id FROM collocations WHERE anki_note_id = ?", (op.doomed_nid,)).fetchone()
        if tracked is not None:
            # Armed even for repoint ops: the exemption is the re-point having
            # already landed, not the intent to do one. Running the grave first
            # must still refuse.
            raise ValueError(
                f"{op.word}: TT collocation {tracked[0]} points at doomed note {op.doomed_nid} —"
                " re-point it to the survivor before graving"
            )

        cids = tuple(
            r[0] for r in anki_conn.execute("SELECT id FROM cards WHERE nid = ? ORDER BY ord", (op.doomed_nid,))
        )
        items.append(GraveRecord(word=op.word, anki_nid=op.doomed_nid, anki_cids=cids))
    return items


def plan_repoints(
    anki_conn: sqlite3.Connection,
    tt_conn: sqlite3.Connection,
    ops: tuple[DuplicateOp, ...] | list[DuplicateOp] = DUPLICATE_OPS,
) -> list[RepointRecord]:
    """Resolve the ``repoint=True`` ops into TT pointer moves.

    Card ids are matched by ``ord`` — the position within the note — since that
    is what a TT direction actually corresponds to. Raises ValueError if the
    survivor is missing, or if it has no card at an ord the collocation
    references (writing a dangling ``anki_card_id`` would be worse than
    stopping). An op whose collocation already sits on the survivor yields no
    record, so re-running is a no-op.
    """
    moves: list[RepointRecord] = []
    for op in ops:
        if not op.repoint:
            continue

        survivor = anki_conn.execute("SELECT id FROM notes WHERE id = ?", (op.survivor_nid,)).fetchone()
        if survivor is None:
            raise ValueError(f"{op.word}: survivor note {op.survivor_nid} not found — refusing to re-point")

        tracked = tt_conn.execute("SELECT id FROM collocations WHERE anki_note_id = ?", (op.doomed_nid,)).fetchone()
        if tracked is None:
            continue  # already re-pointed
        cid = tracked[0]

        doomed_ords = {
            r[0]: r[1] for r in anki_conn.execute("SELECT id, ord FROM cards WHERE nid = ?", (op.doomed_nid,))
        }
        survivor_ords = {
            r[1]: r[0] for r in anki_conn.execute("SELECT id, ord FROM cards WHERE nid = ?", (op.survivor_nid,))
        }

        card_moves: list[tuple[int, int]] = []
        rows = tt_conn.execute(
            "SELECT anki_card_id FROM collocation_directions WHERE collocation_id = ? AND anki_card_id IS NOT NULL",
            (cid,),
        ).fetchall()
        for (old_cid,) in rows:
            ord_ = doomed_ords.get(old_cid)
            if ord_ is None:
                raise ValueError(
                    f"{op.word}: TT direction references card {old_cid}, which is not on doomed note {op.doomed_nid}"
                )
            new_cid = survivor_ords.get(ord_)
            if new_cid is None:
                raise ValueError(
                    f"{op.word}: survivor note {op.survivor_nid} has no card at ord {ord_} — refusing to re-point"
                )
            card_moves.append((old_cid, new_cid))

        moves.append(
            RepointRecord(
                word=op.word,
                tt_collocation_id=cid,
                from_nid=op.doomed_nid,
                to_nid=op.survivor_nid,
                card_moves=tuple(card_moves),
            )
        )
    return moves


def apply_repoints(tt_conn: sqlite3.Connection, moves: list[RepointRecord]) -> dict[str, int]:
    """Move each planned collocation + its directions onto the survivor.

    TT-side only — no Anki row is touched, so none of the USN bookkeeping in
    `.claude/rules/anki-sync.md` applies here.
    """
    counts = {"collocations_repointed": 0, "directions_repointed": 0}
    if not moves:
        return counts

    for move in moves:
        tt_conn.execute(
            "UPDATE collocations SET anki_note_id = ? WHERE id = ?",
            (move.to_nid, move.tt_collocation_id),
        )
        counts["collocations_repointed"] += 1
        for old_cid, new_cid in move.card_moves:
            tt_conn.execute(
                "UPDATE collocation_directions SET anki_card_id = ? WHERE collocation_id = ? AND anki_card_id = ?",
                (new_cid, move.tt_collocation_id, old_cid),
            )
            counts["directions_repointed"] += 1
    tt_conn.commit()
    return counts


def apply_graves(anki_conn: sqlite3.Connection, items: list[GraveRecord]) -> dict[str, int]:
    """Tombstone each planned note + its cards. Returns counts of rows touched.

    ``col.mod`` is bumped only when something was actually graved, so a no-op
    run leaves the collection byte-identical.
    """
    counts = {"notes_graved": 0, "cards_graved": 0}
    if not items:
        return counts

    for item in items:
        for cid in item.anki_cids:
            anki_conn.execute(
                "INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)",
                (cid, _GRAVE_KIND_CARD),
            )
            anki_conn.execute("DELETE FROM cards WHERE id = ?", (cid,))
            counts["cards_graved"] += 1
        anki_conn.execute(
            "INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)",
            (item.anki_nid, _GRAVE_KIND_NOTE),
        )
        anki_conn.execute("DELETE FROM notes WHERE id = ?", (item.anki_nid,))
        counts["notes_graved"] += 1

    # Data-only: col.mod signals the change; col.scm stays put so this remains
    # an incremental sync, and col.usn is the sync ANCHOR, never a dirty flag —
    # the grave rows carry their own usn=-1, which is what pushes. Layer 61.
    anki_conn.execute("UPDATE col SET mod = ?", (int(time.time() * 1000),))
    anki_conn.commit()
    return counts


def _print_plan(items: list[GraveRecord]) -> None:
    if not items:
        print("Nothing to grave (already applied, or no duplicates left).")
        return
    for item in items:
        print(f"  {item.word}: grave note {item.anki_nid} + cards {list(item.anki_cids)}")


def _print_repoints(moves: list[RepointRecord]) -> None:
    for move in moves:
        print(
            f"  {move.word}: re-point TT collocation {move.tt_collocation_id}"
            f" {move.from_nid} -> {move.to_nid}, cards {list(move.card_moves)}"
        )


def main() -> None:  # pragma: no cover - CLI glue
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the graves (default is a dry run)")
    parser.add_argument(
        "--language",
        default=None,
        help="language code whose TT db holds these notes (default: settings.target_language)",
    )
    args = parser.parse_args()

    from app.config import settings
    from app.languages import resolve_language_context
    from app.plugins.anki_sync.safety import safe_open
    from app.srs.database import SRSDatabase

    # `settings.database_url` is the single-language default and does NOT follow
    # target_language — resolving through the registry is what pins the right db.
    # Checking the wrong one makes plan_graves' "TT points at the doomed note"
    # guard pass vacuously, which is the whole point of the guard.
    lang = resolve_language_context(args.language or settings.target_language, settings)
    print(f"language={lang.code}  db={lang.db_url}  deck={lang.deck_name}")

    db = SRSDatabase(lang.db_url)
    mode = "rw" if args.apply else "ro"
    ops = ops_for_language(lang.code)
    with safe_open(settings.anki_collection_path, mode=mode) as ctx, db._get_conn() as tt_conn:
        print(f"Plan ({'APPLY' if args.apply else 'dry run'}):")
        # Re-points first: they are what satisfies plan_graves' tracked-note
        # guard, so planning the graves before they land would refuse.
        moves = plan_repoints(ctx.conn, tt_conn, ops)
        _print_repoints(moves)
        if args.apply:
            print(f"Re-pointed: {apply_repoints(tt_conn, moves)}")

        # On a dry run the re-points have NOT landed, so the guard would still
        # (correctly) refuse their graves. Defer those and report them instead
        # of crashing the preview.
        deferred = {m.word for m in moves} if not args.apply else set()
        items = plan_graves(ctx.conn, tt_conn, [op for op in ops if op.word not in deferred])
        _print_plan(items)
        for word in sorted(deferred):
            print(f"  {word}: grave deferred — needs the re-point above to land first")
        if args.apply:
            print(f"Applied: {apply_graves(ctx.conn, items)}")
        else:
            print("Re-run with --apply to write.")


if __name__ == "__main__":  # pragma: no cover - CLI guard
    main()
