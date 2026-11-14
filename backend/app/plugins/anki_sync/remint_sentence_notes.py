"""Re-mint a lesson line's separate sentence Cloze notes as ONE "TT Sentence" note (tunatale-h0q3.55 phase 3).

h0q3.42 put each sentence card (a line's blanks and its whole-line card) on its
own c1 Cloze note, so Anki never buried them as siblings. Phase 2 taught sync to
mint a line as one TT Sentence note with a card per row; this moves the rows
minted before that onto the new shape:

    per line: grave the old Cloze notes (one card grave + one note grave each)
              unlink the TT rows, set sibling_group = the line key,
              give each direction a fresh NEW row and drop its tt_revlog history
    then:     the next sync's sync_create_new mints the line (_mint_sentence_line)

Review history on those cards resets; the owner accepted that (h0q3.55).

⚠️ **TT is unlinked before any sync reads the graves, and the order is
load-bearing.** ``detect_and_reset_orphans`` honours a note grave by DELETING
every TT row still linked to the graved note (Layer 68). The Anki writes are made
first but left uncommitted, TT commits, then the collection commits: a failure
before the TT commit rolls both back. A failure between the two commits leaves TT
unlinked and the old notes live, which a re-run finishes from the capture file
(the old note ids are recorded nowhere else once TT is unlinked).

⚠️ **The old history must leave TT.** ``get_unpushed_revlog_rows`` pushes every
``tt_revlog`` row whose id is above the card's highest Anki revlog id, and a
freshly minted card has none, so kept rows would be replayed onto the new card at
its first TT grade. They go to the capture file instead.

⚠️ Refuses (exit 2, nothing written on either side) on anything it was not built
for: a line graded today, an unpushed grade or edit on either side, a staged
listen grade, a note that is not a one-card Cloze note, rows sharing a note, a
half-migrated line. Sync first, or wait for the 04:00 rollover.

⚠️ Runs against ``settings.tt_collection_path`` (TT's own mirror, which reaches
AnkiWeb through peer-sync), NOT the desktop collection. A data-only change: graves
and ``col.mod``; never ``col.usn`` (Layer 61), never ``col.scm``.

    uv run python -m app.plugins.anki_sync.remint_sentence_notes --language ceb --line sent:wolff-01b:4
    uv run python -m app.plugins.anki_sync.remint_sentence_notes --language ceb --line sent:wolff-01b:4 --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.config import settings, tt_home
from app.languages import resolve_language_context
from app.plugins.anki_sync.migrate_number_clozes import bump_col_mod
from app.plugins.anki_sync.migrate_number_clozes import grave_cloze_note as grave_note
from app.plugins.anki_sync.safety import safe_open
from app.srs.anki_mirror.queue_stats import clear_session_main_queue
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase, _anki_day_bounds_utc
from app.srs.drill_card import is_drill_key
from app.srs.sentence_card import SentenceCard

__all__ = ["apply_tt", "grave_note", "main", "plan", "run", "write_capture"]

DEFAULT_CAPTURE = "sentence-remint-capture.json"
_CLOZE_NOTETYPE = "Cloze"
TODO, TT_DONE = "todo", "tt_done"


@dataclass(frozen=True)
class RowPlan:
    row_id: int
    key: str
    note_id: int | None
    card_id: int | None
    state: str
    reps: int


@dataclass
class LinePlan:
    line: str
    rows: list[RowPlan] = field(default_factory=list)
    status: str = TODO
    #: Old notes to grave: every row's note for a TODO line; the capture's
    #: still-live ones for a line whose TT half is already done.
    graves: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _line_rows(tt_conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    """Every sentence row, by line key (derived from ``card_data``, as migration v50 does).

    A drill card (tunatale-5i1p.4) is a ``sent:`` row that is no lesson line:
    its ``card_data`` is not a SentenceCard. Skipped, so this script never plans
    or writes one.
    """
    out: dict[str, list[sqlite3.Row]] = {}
    for row in tt_conn.execute("SELECT * FROM collocations WHERE disambig_key LIKE 'sent:%' ORDER BY id"):
        if is_drill_key(row["disambig_key"]):
            continue
        out.setdefault(SentenceCard.from_json(row["card_data"]).line_key, []).append(row)
    return out


def select_lines(tt_conn: sqlite3.Connection, lines: list[str], lessons: list[str], capture: dict) -> list[str]:
    """*lines* as given, plus every line of *lessons* that still has a legacy row or a capture entry."""
    selected = list(dict.fromkeys(lines))
    by_line = _line_rows(tt_conn)
    for lesson in lessons:
        prefix = f"sent:{lesson}:"
        for line, rows in by_line.items():
            legacy = any(r["sibling_group"] is None for r in rows)
            if line.startswith(prefix) and (legacy or line in capture) and line not in selected:
                selected.append(line)
    return selected


def _check_legacy_row(
    tt_conn: sqlite3.Connection, anki_conn: sqlite3.Connection, row: sqlite3.Row, today_ms: tuple[int, int]
) -> tuple[RowPlan, list[str]]:
    rid, key = row["id"], row["disambig_key"]
    dirs = tt_conn.execute("SELECT * FROM collocation_directions WHERE collocation_id = ?", (rid,)).fetchall()
    problems: list[str] = []
    if [d["direction"] for d in dirs] != ["production"]:
        problems.append(f"{key}: directions {[d['direction'] for d in dirs]}, expected production only")
    d = next((d for d in dirs if d["direction"] == "production"), None)
    card_id = d["anki_card_id"] if d is not None else None
    note_id = row["anki_note_id"]
    rp = RowPlan(rid, key, note_id, card_id, d["state"] if d else "?", d["reps"] if d else 0)
    if any(x["dirty_fsrs"] for x in dirs) or row["dirty_fields"]:
        problems.append(f"{key}: unpushed TT grade or edit — sync first")
    start, end = today_ms
    if tt_conn.execute(
        "SELECT 1 FROM tt_revlog WHERE collocation_id = ? AND id >= ? AND id < ?", (rid, start, end)
    ).fetchone():
        problems.append(f"{key}: graded today — run after the 04:00 rollover")
    if tt_conn.execute("SELECT 1 FROM pending_listen_grades WHERE collocation_id = ?", (rid,)).fetchone():
        problems.append(f"{key}: a staged listen grade — release it first")
    if note_id is None:
        problems.append(f"{key}: not linked to any note")
        return rp, problems
    note = anki_conn.execute(
        "SELECT n.usn, m.name FROM notes n LEFT JOIN notetypes m ON m.id = n.mid WHERE n.id = ?", (note_id,)
    ).fetchone()
    if note is None:
        problems.append(f"{key}: note {note_id} is not in the collection — sync first")
        return rp, problems
    if note[1] != _CLOZE_NOTETYPE:
        problems.append(f"{key}: note {note_id} is not a Cloze note ({note[1]!r})")
    cards = anki_conn.execute("SELECT id, usn FROM cards WHERE nid = ?", (note_id,)).fetchall()
    if [c[0] for c in cards] != [card_id]:
        problems.append(f"{key}: note {note_id} has cards {[c[0] for c in cards]}, the row names {card_id}")
    if note[0] == -1 or any(c[1] == -1 for c in cards):
        problems.append(f"{key}: note {note_id} has an unpushed Anki change — sync first")
    return rp, problems


def plan(
    tt_conn: sqlite3.Connection, anki_conn: sqlite3.Connection, lines: list[str], capture: dict | None = None
) -> list[LinePlan]:
    """What to do for each line, and every reason not to. Read-only."""
    capture = capture or {}
    by_line = _line_rows(tt_conn)
    start_iso, end_iso = _anki_day_bounds_utc(anki_today())
    today_ms = (
        int(datetime.fromisoformat(start_iso).timestamp() * 1000),
        int(datetime.fromisoformat(end_iso).timestamp() * 1000),
    )
    plans: list[LinePlan] = []
    for line in lines:
        lp = LinePlan(line)
        plans.append(lp)
        rows = by_line.get(line, [])
        if not rows:
            lp.problems.append(f"{line}: no rows")
            continue
        groups = {r["sibling_group"] for r in rows}
        if groups == {line}:
            lp.status = TT_DONE
            lp.rows = [RowPlan(r["id"], r["disambig_key"], r["anki_note_id"], None, "", 0) for r in rows]
            if line not in capture:
                lp.problems.append(f"{line}: rows already unlinked but no capture entry — old notes unknown")
                continue
            old = [r["note_id"] for r in capture[line]["rows"]]
            lp.graves = [n for n in old if anki_conn.execute("SELECT 1 FROM notes WHERE id = ?", (n,)).fetchone()]
            continue
        if groups != {None}:
            lp.problems.append(f"{line}: mixed sibling_group {sorted(map(str, groups))} — half-migrated")
            continue
        for row in rows:
            rp, problems = _check_legacy_row(tt_conn, anki_conn, row, today_ms)
            lp.rows.append(rp)
            lp.problems.extend(problems)
        notes = [r.note_id for r in lp.rows if r.note_id is not None]
        if len(set(notes)) != len(notes):
            lp.problems.append(f"{line}: rows share a note ({notes})")
        lp.graves = list(dict.fromkeys(notes))
    return plans


def load_capture(path: Path) -> dict:
    """The capture's lines (``{}`` when there is no file yet)."""
    return json.loads(path.read_text())["lines"] if path.exists() else {}


def write_capture(path: Path, tt_conn: sqlite3.Connection, plans: list[LinePlan]) -> None:
    """Record each TODO line's old notes, direction rows and tt_revlog rows before anything is written.

    Merged into the file: other lines' entries are kept. The note ids are what a
    re-run needs after a stop between the two commits; the rest makes the reset
    reversible by hand.
    """
    lines = load_capture(path)
    for lp in plans:
        if lp.status != TODO:
            continue
        rows = []
        for rp in lp.rows:
            direction = tt_conn.execute(
                "SELECT * FROM collocation_directions WHERE collocation_id = ? AND direction = 'production'",
                (rp.row_id,),
            ).fetchone()
            revlog = tt_conn.execute(
                "SELECT * FROM tt_revlog WHERE collocation_id = ? ORDER BY id", (rp.row_id,)
            ).fetchall()
            rows.append(
                {
                    "row_id": rp.row_id,
                    "key": rp.key,
                    "note_id": rp.note_id,
                    "card_id": rp.card_id,
                    "direction": dict(direction),
                    "revlog": [dict(r) for r in revlog],
                }
            )
        lines[lp.line] = {"rows": rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "lines": lines}, indent=2, ensure_ascii=False))


def apply_tt(tt_conn: sqlite3.Connection, plans: list[LinePlan]) -> int:
    """Unlink and group each TODO line's rows; each direction becomes the row a fresh card gets.

    The direction row is replaced rather than updated: the insert below is the
    one ``add_collocation`` makes, so a re-minted card cannot carry a column a
    hand-written reset forgot. Its ``tt_revlog`` rows go first (their foreign key
    cascades from the direction). The caller commits. Returns rows reset.
    """
    due_at = due_at_rollover_utc(anki_today()).isoformat()
    n = 0
    for lp in plans:
        if lp.status != TODO:
            continue
        for rp in lp.rows:
            tt_conn.execute("DELETE FROM tt_revlog WHERE collocation_id = ?", (rp.row_id,))
            tt_conn.execute("DELETE FROM collocation_directions WHERE collocation_id = ?", (rp.row_id,))
            tt_conn.execute(
                "INSERT INTO collocation_directions (collocation_id, direction, due_at, state, dirty_fsrs) "
                "VALUES (?, 'production', ?, 'new', 0)",
                (rp.row_id, due_at),
            )
            tt_conn.execute(
                "UPDATE collocations SET anki_note_id = NULL, sibling_group = ?, last_synced_at = NULL, "
                "updated_at = datetime('now') WHERE id = ?",
                (lp.line, rp.row_id),
            )
            n += 1
    return n


def _report(plans: list[LinePlan]) -> None:
    for lp in plans:
        print(f"{lp.line}: {len(lp.rows)} row(s), {lp.status}")
        if lp.status == TODO:
            for rp in lp.rows:
                print(f"    {rp.key:28} {rp.state:10} reps={rp.reps:<3} note={rp.note_id}")
        if lp.graves:
            print(f"    old note(s) to grave: {lp.graves}")
        for p in lp.problems:
            print(f"    REFUSED: {p}")


def run(
    db: SRSDatabase,
    *,
    collection_path: Path,
    backup_dir: Path,
    capture_path: Path,
    lines: list[str],
    lessons: list[str],
    apply: bool,
) -> int:
    """Plan, refuse or (with *apply*) re-mint. Exit code: 0 done / dry run, 2 refused."""
    capture = load_capture(capture_path)
    with safe_open(collection_path, backup_dir=backup_dir, mode="rw" if apply else "ro") as ctx, db._get_conn() as tt:
        selected = select_lines(tt, lines, lessons, capture)
        if not selected:
            print("REFUSED: nothing selected (--line sent:<lesson>:<line> or --lesson <lesson>)")
            return 2
        plans = plan(tt, ctx.conn, selected, capture)
        _report(plans)
        if any(lp.problems for lp in plans):
            print("\nREFUSED — nothing written.")
            return 2
        todo = [lp for lp in plans if lp.status == TODO]
        graves = [n for lp in plans for n in lp.graves]
        reviews = sum(rp.reps for lp in todo for rp in lp.rows)
        if not apply:
            print(f"\nDRY RUN — would grave {len(graves)} note(s) and reset {reviews} review(s) of history.")
            return 0
        if not todo and not graves:
            print("\nNothing to do: every selected line is already re-minted.")
            return 0
        if todo:
            write_capture(capture_path, tt, todo)
        # Anki first but uncommitted, TT committed, then Anki: see the module docstring.
        # Never empty here: a TODO line graves every row's note, and a run with
        # neither TODO lines nor graves returned above.
        cards = sum(grave_note(ctx.conn, n) for n in graves)
        bump_col_mod(ctx.conn)
        reset = apply_tt(tt, todo)
        tt.commit()
        ctx.conn.commit()
    if reset:
        clear_session_main_queue(db)
    print(f"\nAnki: graved {len(graves)} note(s), {cards} card(s); col.mod bumped, col.usn and col.scm untouched.")
    print(f"TunaTale: {reset} row(s) unlinked and reset to NEW; capture -> {capture_path}")
    print("NEXT: one sync mints each line as a TT Sentence note.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--language", default=None, help="language code (picks the TT database)")
    parser.add_argument("--db-url", default=None, help="TT database url (default: the language's)")
    parser.add_argument("--line", action="append", default=[], help="a line key, e.g. sent:wolff-01b:4")
    parser.add_argument("--lesson", action="append", default=[], help="every legacy line of a lesson")
    parser.add_argument("--collection", type=Path, default=None, help="default: settings.tt_collection_path")
    parser.add_argument("--backup-dir", type=Path, default=None, help="default: settings.anki_backup_dir")
    parser.add_argument("--capture-file", type=Path, default=None, help=f"default: <tt_home>/{DEFAULT_CAPTURE}")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)

    db_url = args.db_url or resolve_language_context(args.language, settings).db_url
    collection = args.collection or settings.tt_collection_path
    capture = args.capture_file or tt_home() / DEFAULT_CAPTURE
    print(f"TT database: {db_url}\ncollection:  {collection}\ncapture:     {capture}\n")
    return run(
        SRSDatabase(db_url),
        collection_path=collection,
        backup_dir=args.backup_dir or settings.anki_backup_dir,
        capture_path=capture,
        lines=args.line,
        lessons=args.lesson,
        apply=args.apply,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
