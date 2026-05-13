"""Clean up the historical LingQ-import mess.

A prior buggy version of `/listen` import created 36 Anki Basic-notetype notes
in deck "0. Slovene":

- **18 "twins"** — words that already had Slovene-Vocabulary notes in Anki.
  The import should have skipped these (matching by guid in `sync_create_new`);
  instead it created duplicate Basic notes, leaving TT's collocation with
  cross-note linking (recognition→Basic, production→Slovene-Voc — the Layer-33
  phantom signal).
- **18 "non-twins"** — words not yet in Anki. The import should have created
  proper Slovene-Voc notes with both Recognition and Production cards; instead
  it created Basic notes with only one card.

This script walks the affected notes and:

- **DELETE** the 18 twin Basic notes from Anki; relink TT's collocation to the
  existing Slovene-Voc note (and clear stale direction `anki_card_id`/`anki_due`
  so the next sync_pull repopulates them).
- **CONVERT** the 18 non-twin Basic notes in-place to Slovene-Voc notetype:
  reshape fields, recompute guid, keep the existing card (now ord=0
  Recognition with revlog preserved), add a new ord=1 Production card, and
  add the matching Production direction to TT.

The CONVERT path bumps `col.scm` (notetype assignment change is schema-
significant per Anki's sync model). After running, follow the 3-step workflow
documented in `.claude/rules/anki-sync.md`:

    1. Open Anki → File → Sync → Upload to AnkiWeb.
    2. After Anki closes, run: uv run python -m app.anki.normalize_usns

Usage::

    uv run python -m app.anki.fix_lingq_import_mess [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME
from app.anki.sqlite_reader import find_deck_id
from app.common.guid import compute_guid
from app.config import settings

_BASIC_VOCAB_GLOSS = re.compile(
    r"^\s*<b>([^<]+)</b>\s*<br\s*/?>\s*<i>([^<]+)</i>",
    re.IGNORECASE,
)
_BASIC_L1L2_PROMPT = re.compile(r'<div class="prompt">([^<]+)</div>', re.IGNORECASE)
_BASIC_LJUBLJANA = re.compile(r"^\s*<b>([^<]+)</b>\s*$", re.IGNORECASE)
_SOUND_TAG = re.compile(r"\[sound:[^\]]+\]")


@dataclass(frozen=True)
class DeleteItem:
    """Delete a redundant Basic note; relink TT to its Slovene-Voc twin."""

    basic_nid: int
    target_slovene_voc_nid: int
    tt_collocation_id: int


@dataclass(frozen=True)
class ConvertItem:
    """Convert a Basic note in-place to Slovene-Voc and add a Production card + TT direction."""

    basic_nid: int
    slovene: str
    english: str
    audio: str
    note_extra: str
    tt_collocation_id: int


def _parse_basic_front(flds: str) -> tuple[str, str] | None:
    """Return (slovene, english) for a vocab+gloss or single-word Basic Front, else None.

    Phonology Q's ("What sound is...?") and other non-vocab Fronts return None.
    """
    fields = flds.split("\x1f")
    front = fields[0]
    back = fields[1] if len(fields) > 1 else ""

    m = _BASIC_VOCAB_GLOSS.match(front)
    if m:
        return (m.group(1).strip(), m.group(2).strip())

    # L1→L2 prompt: Front is `<div class="prompt">[shop/store]</div>`, Back has
    # `<div class="slovene">trgovina</div><div class="english">store/shop</div>`.
    if _BASIC_L1L2_PROMPT.search(front):
        slovene_m = re.search(r'<div class="slovene">([^<]+)</div>', back)
        english_m = re.search(r'<div class="english">([^<]+)</div>', back)
        if slovene_m and english_m:
            return (slovene_m.group(1).strip(), english_m.group(1).strip())

    # Bare `<b>Ljubljana</b>` with no `<i>` — single-word vocab.
    bare = _BASIC_LJUBLJANA.match(front)
    if bare:
        return (bare.group(1).strip(), "")

    return None


def _extract_audio_and_note(back: str) -> tuple[str, str]:
    """From the Basic Back field, peel out `[sound:...]` tags into Audio; the
    rest becomes the Note field on the new Slovene-Voc layout."""
    audio_match = _SOUND_TAG.search(back)
    audio = audio_match.group(0) if audio_match else ""
    note_extra = _SOUND_TAG.sub("", back).strip()
    return audio, note_extra


def plan_cleanup(
    anki_conn: sqlite3.Connection,
    tt_conn: sqlite3.Connection,
    deck_id: int,
    sv_mid: int,
    basic_mid: int,
) -> tuple[list[DeleteItem], list[ConvertItem]]:
    """Classify every Basic-notetype note in the deck into DELETE or CONVERT.

    Pure: read-only on both connections. Caller is responsible for applying.
    """
    deletes: list[DeleteItem] = []
    converts: list[ConvertItem] = []

    rows = anki_conn.execute(
        """
        SELECT DISTINCT n.id, n.flds
        FROM notes n JOIN cards c ON c.nid = n.id
        WHERE n.mid = ? AND c.did = ?
        """,
        (basic_mid, deck_id),
    ).fetchall()

    for nid, flds in rows:
        parsed = _parse_basic_front(flds)
        if parsed is None:
            continue
        slovene, english = parsed
        # Look up TT collocation linked to this Basic note.
        tt_row = tt_conn.execute("SELECT id FROM collocations WHERE anki_note_id = ?", (nid,)).fetchone()
        if tt_row is None:
            continue
        tt_cid = tt_row[0]
        # Does a Slovene-Voc twin exist? Match case-insensitively (e.g. "bog" → "Bog")
        # and try each '/'-split variant for L1→L2 prompts ("ulica / cesta" → ["ulica", "cesta"]).
        candidates = [v.strip() for v in re.split(r"\s*/\s*", slovene) if v.strip()]
        twin = None
        for cand in candidates:
            twin = anki_conn.execute(
                "SELECT n.id FROM notes n JOIN cards c ON c.nid = n.id "
                "WHERE n.mid = ? AND LOWER(n.sfld) = LOWER(?) AND c.did = ? GROUP BY n.id",
                (sv_mid, cand, deck_id),
            ).fetchone()
            if twin is not None:
                break
        if twin is not None:
            deletes.append(DeleteItem(basic_nid=nid, target_slovene_voc_nid=twin[0], tt_collocation_id=tt_cid))
        else:
            back = flds.split("\x1f")[1] if "\x1f" in flds else ""
            audio, note_extra = _extract_audio_and_note(back)
            converts.append(
                ConvertItem(
                    basic_nid=nid,
                    slovene=slovene,
                    english=english,
                    audio=audio,
                    note_extra=note_extra,
                    tt_collocation_id=tt_cid,
                )
            )
    return deletes, converts


def apply_plan(
    anki_conn: sqlite3.Connection,
    tt_conn: sqlite3.Connection,
    deletes: list[DeleteItem],
    converts: list[ConvertItem],
    deck_id: int,
    sv_mid: int,
) -> dict[str, int]:
    """Apply DELETE + CONVERT operations. Returns counts.

    Uses raw SQL; both connections must be writable.
    """
    counts = {"deleted": 0, "converted": 0}
    now_secs = int(time.time())
    now_ms = now_secs * 1000

    # --- DELETE: remove Basic notes; relink TT to Slovene-Voc twin. ---
    for item in deletes:
        anki_conn.execute("DELETE FROM notes WHERE id = ?", (item.basic_nid,))
        anki_conn.execute("DELETE FROM cards WHERE nid = ?", (item.basic_nid,))
        tt_conn.execute(
            "UPDATE collocations SET anki_note_id = ? WHERE id = ?",
            (item.target_slovene_voc_nid, item.tt_collocation_id),
        )
        # Clear stale per-direction Anki linkage so sync_pull repopulates from the SV note.
        tt_conn.execute(
            "UPDATE collocation_directions SET anki_card_id = NULL, anki_due = NULL WHERE collocation_id = ?",
            (item.tt_collocation_id,),
        )
        counts["deleted"] += 1

    # --- CONVERT: change Basic → Slovene-Voc in place; add Production card + TT direction. ---
    if converts:
        # Bump col.scm — notetype-of-note change is schema-significant for Anki sync.
        anki_conn.execute("UPDATE col SET scm = ?, mod = ?, usn = -1", (now_ms, now_ms))

    for item in converts:
        new_guid = compute_guid(item.slovene, "sl", "")
        new_flds = "\x1f".join([item.slovene, item.english, item.audio, "", "", item.note_extra, ""])
        anki_conn.execute(
            "UPDATE notes SET mid = ?, flds = ?, sfld = ?, guid = ?, mod = ?, usn = -1 WHERE id = ?",
            (sv_mid, new_flds, item.slovene, new_guid, now_secs, item.basic_nid),
        )
        # Existing card (ord=0) becomes Recognition — re-stamp mod+usn so AnkiWeb sees the change.
        anki_conn.execute(
            "UPDATE cards SET mod = ?, usn = -1 WHERE nid = ? AND ord = 0",
            (now_secs, item.basic_nid),
        )
        # Add Production card (ord=1).
        max_cid = anki_conn.execute("SELECT MAX(id) FROM cards").fetchone()[0] or 0
        new_cid = max(now_ms, max_cid + 1)
        next_due_row = anki_conn.execute("SELECT COALESCE(MAX(due), 0) + 1 FROM cards WHERE queue = 0").fetchone()
        next_due = next_due_row[0]
        anki_conn.execute(
            "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, "
            "reps, lapses, left, odue, odid, flags, data) "
            "VALUES (?, ?, ?, 1, ?, -1, 0, 0, ?, 0, 2500, 0, 0, 0, 0, 0, 0, '')",
            (new_cid, item.basic_nid, deck_id, now_secs, next_due),
        )
        # TT: add the Production direction (if not present).
        tt_conn.execute(
            "INSERT OR IGNORE INTO collocation_directions "
            "(collocation_id, direction, state, due_date, stability, fsrs_difficulty, reps, lapses, anki_card_id, anki_due, dirty_fsrs) "
            "VALUES (?, 'production', 'new', ?, 1.0, 5.0, 0, 0, ?, ?, 0)",
            (item.tt_collocation_id, _today_iso(), new_cid, next_due),
        )
        # TT: re-stamp the recognition direction's anki_due to match the new card.due
        # (existing rec card stays at ord=0; its `due` value in Anki didn't change here,
        # but the TT-side `anki_due` value is whatever sync_pull last wrote — leave it.)
        counts["converted"] += 1

    # Bump col.mod even for delete-only so AnkiWeb picks up the deletes.
    if deletes and not converts:
        anki_conn.execute("UPDATE col SET mod = ?, usn = -1", (now_ms,))

    anki_conn.commit()
    tt_conn.commit()
    return counts


def _today_iso() -> str:
    from datetime import date

    return date.today().isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without writing")
    parser.add_argument("--anki-db", type=Path, default=None, help="override Anki collection path")
    parser.add_argument("--tt-db", type=Path, default=None, help="override TT database path")
    parser.add_argument("--deck-name", type=str, default=None, help="Anki deck name (default: settings.anki_deck_name)")
    args = parser.parse_args(argv)

    anki_path = args.anki_db or settings.anki_collection_path
    tt_path = args.tt_db or Path(settings.database_url.removeprefix("sqlite:///"))
    deck_name = args.deck_name or settings.anki_deck_name

    if not anki_path.exists():
        print(f"Anki collection not found: {anki_path}", file=sys.stderr)
        return 1
    if not tt_path.exists():
        print(f"TT database not found: {tt_path}", file=sys.stderr)
        return 1

    # For dry-run we open the Anki collection read-only directly (no backup needed).
    # For apply, use safe_open(mode='rw') for backup + integrity check + post-write audit.
    if args.dry_run:
        from app.anki.safety import _register_anki_collations

        anki_conn = sqlite3.connect(f"file:{anki_path}?mode=ro", uri=True)
        _register_anki_collations(anki_conn)
        tt_conn = sqlite3.connect(str(tt_path))
        try:
            deck_id = find_deck_id(anki_conn, deck_name)
            if deck_id is None:
                print(f"Deck not found: {deck_name!r}", file=sys.stderr)
                return 1
            sv_mid_row = anki_conn.execute(
                "SELECT id FROM notetypes WHERE name = ?", (SLOVENE_VOCAB_NOTETYPE_NAME,)
            ).fetchone()
            if sv_mid_row is None:
                print(f"Notetype not found: {SLOVENE_VOCAB_NOTETYPE_NAME!r}", file=sys.stderr)
                return 1
            basic_mid_row = anki_conn.execute("SELECT id FROM notetypes WHERE name = 'Basic'").fetchone()
            if basic_mid_row is None:
                print("Notetype not found: 'Basic'", file=sys.stderr)
                return 1
            deletes, converts = plan_cleanup(anki_conn, tt_conn, deck_id, sv_mid_row[0], basic_mid_row[0])
            _print_plan(deletes, converts)
            if not deletes and not converts:
                print("Nothing to apply.")
            else:
                print("--dry-run: no changes applied.")
            return 0
        finally:
            anki_conn.close()
            tt_conn.close()

    # --- Apply path: safe_open envelope. ---
    from app.anki.safety import safe_open

    tt_conn = sqlite3.connect(str(tt_path), isolation_level=None)
    try:
        with safe_open(anki_path, mode="rw") as ctx:
            anki_conn = ctx.conn
            deck_id = find_deck_id(anki_conn, deck_name)
            if deck_id is None:
                print(f"Deck not found: {deck_name!r}", file=sys.stderr)
                return 1
            sv_mid_row = anki_conn.execute(
                "SELECT id FROM notetypes WHERE name = ?", (SLOVENE_VOCAB_NOTETYPE_NAME,)
            ).fetchone()
            basic_mid_row = anki_conn.execute("SELECT id FROM notetypes WHERE name = 'Basic'").fetchone()
            if sv_mid_row is None or basic_mid_row is None:
                print("Required notetypes not found.", file=sys.stderr)
                return 1
            deletes, converts = plan_cleanup(anki_conn, tt_conn, deck_id, sv_mid_row[0], basic_mid_row[0])
            _print_plan(deletes, converts)
            if not deletes and not converts:
                print("Nothing to apply.")
                return 0
            counts = apply_plan(anki_conn, tt_conn, deletes, converts, deck_id, sv_mid_row[0])
            print(f"Applied: {counts}")
            if converts:
                print(
                    "\nNotetype change bumped col.scm. Required workflow:\n"
                    "  1. Open Anki → File → Sync → Upload to AnkiWeb.\n"
                    "  2. After Anki closes, run: uv run python -m app.anki.normalize_usns"
                )
    finally:
        tt_conn.close()
    return 0


def _print_plan(deletes: list[DeleteItem], converts: list[ConvertItem]) -> None:
    print(f"Plan: {len(deletes)} DELETE + {len(converts)} CONVERT")
    for d in deletes:
        print(
            f"  DELETE basic_nid={d.basic_nid} → relink TT cid={d.tt_collocation_id} to sv_nid={d.target_slovene_voc_nid}"
        )
    for c in converts:
        print(f"  CONVERT basic_nid={c.basic_nid} text={c.slovene!r} translation={c.english!r}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
