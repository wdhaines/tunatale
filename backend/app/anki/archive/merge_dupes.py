"""Stage 2b-pre: merge duplicate Anki notes into a unified Slovene Vocabulary notetype.

The user historically stored two notes per Slovene word to get Recognition +
Production out of the built-in single-template ``Basic`` notetype. The GUID
backfill in ``app.anki.backfill_guids`` cannot succeed until those pairs are
collapsed into a single note under a two-template notetype.

This module:
  1. Creates a new ``Slovene Vocabulary`` notetype with fields
     ``Slovene / English / Audio / Image / Grammar / Note / DisambigKey`` and
     two templates (Recognition, Production).
  2. Rewrites every Basic-notetype note in the target deck into the new schema.
  3. Reparents any ``Production`` partner cards to their ``Recognition`` keeper
     and flips their ``ord`` so FSRS history is preserved byte-for-byte.
  4. Deletes the now-empty non-keeper notes.
  5. Disambiguates homonyms by writing the English meaning into the hidden
     ``DisambigKey`` field (field index 6); the Slovene display field stays bare.

Usage:
    uv run python -m app.anki.merge_dupes [--deck "0. Slovene"] [--dry-run] [--yes]
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.anki.archive.notetype_builders import (
    SLOVENE_VOCAB_CSS,
    build_field_config,
    build_notetype_config,
    build_template_config,
    slovene_vocab_fields,
    slovene_vocab_templates,
)
from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME
from app.anki.safety import safe_open
from app.anki.sqlite_reader import AnkiNote, fetch_notes_for_deck, find_deck_id
from app.anki.sqlite_writer import check_anki_web_sync_active
from app.config import settings

_Direction = Literal["recognition", "production", "unknown"]

_SOUND_RE = re.compile(r"\[sound:[^\]]+\]")
_IMG_RE = re.compile(r"<img[^>]*>")
_DIV_IMG_RE = re.compile(r'<div\s+class="img"[^>]*>')
_DIV_CLASS_RE = re.compile(r'<div\s+class="([^"]+)"[^>]*>(.*?)</div>', re.DOTALL)


@dataclass
class ParsedNote:
    note_id: int
    guid: str
    card_id: int
    card_ord: int
    slovene: str
    english: str
    audio: str
    image: str
    grammar: str
    note: str
    direction: _Direction


@dataclass
class UnifiedFields:
    slovene: str
    english: str
    audio: str
    image: str
    grammar: str
    note: str
    disambig_key: str = ""

    def to_flds(self) -> str:
        return "\x1f".join(
            [self.slovene, self.english, self.audio, self.image, self.grammar, self.note, self.disambig_key]
        )


@dataclass
class MergeGroup:
    slovene: str
    english: str
    recognition: ParsedNote | None = None
    production: ParsedNote | None = None
    extras: list[ParsedNote] = field(default_factory=list)


@dataclass
class MergePlan:
    new_notetype_mid: int
    groups: list[MergeGroup] = field(default_factory=list)
    singletons_unknown_direction: list[ParsedNote] = field(default_factory=list)
    homonyms_requiring_disambiguation: list[tuple[str, list[str]]] = field(default_factory=list)
    notes_to_update: dict[int, UnifiedFields] = field(default_factory=dict)
    cards_to_reparent: dict[int, tuple[int, int]] = field(default_factory=dict)
    notes_to_delete: list[int] = field(default_factory=list)


def _extract_div(text: str, class_name: str) -> str:
    for m in _DIV_CLASS_RE.finditer(text):
        if m.group(1).strip() == class_name:
            inner = m.group(2)
            # Strip any nested tags, then whitespace
            return re.sub(r"<[^>]+>", "", inner).strip()
    return ""


def _extract_audio(text: str) -> str:
    m = _SOUND_RE.search(text)
    return m.group(0) if m else ""


def _extract_img(text: str) -> str:
    m = _IMG_RE.search(text)
    return m.group(0) if m else ""


def _infer_direction(field0: str) -> _Direction:
    if field0.startswith("[sound:"):
        return "recognition"
    if '<div class="img"' in field0 and "[sound:" not in field0:
        return "production"
    return "unknown"


def parse_notes(
    notes: list[AnkiNote],
    card_ord_by_note: dict[int, int],
    card_id_by_note: dict[int, int],
) -> list[ParsedNote]:
    """Parse each ``AnkiNote`` into a ``ParsedNote`` with extracted fields + direction."""
    out: list[ParsedNote] = []
    for note in notes:
        combined = "\x1f".join(note.fields)
        front = note.fields[0] if note.fields else ""
        slovene = _extract_div(combined, "slovene")
        english = _extract_div(combined, "english")
        audio = _extract_audio(combined)
        image = _extract_img(combined)
        grammar = _extract_div(combined, "gram")
        note_text = _extract_div(combined, "note")
        direction = _infer_direction(front)
        out.append(
            ParsedNote(
                note_id=note.id,
                guid=note.anki_guid,
                card_id=card_id_by_note.get(note.id, 0),
                card_ord=card_ord_by_note.get(note.id, 0),
                slovene=slovene,
                english=english,
                audio=audio,
                image=image,
                grammar=grammar,
                note=note_text,
                direction=direction,
            )
        )
    return out


def group_by_meaning(parsed: list[ParsedNote]) -> list[MergeGroup]:
    """Partition parsed notes by ``(slovene_casefold, english_casefold)``.

    Homonyms (same Slovene, different English) fall into different buckets so the
    caller can disambiguate them. Unknown-direction notes are excluded — the
    caller handles them separately via ``build_plan``.
    """
    buckets: dict[tuple[str, str], MergeGroup] = {}
    for note in parsed:
        if note.direction == "unknown":
            continue
        key = (note.slovene.casefold(), note.english.casefold())
        group = buckets.setdefault(key, MergeGroup(slovene=note.slovene, english=note.english))
        if note.direction == "recognition":
            if group.recognition is not None:
                raise RuntimeError(
                    f"duplicate recognition note for ({note.slovene!r}, {note.english!r}): "
                    f"already have note {group.recognition.note_id}, got {note.note_id}"
                )
            group.recognition = note
        else:
            # direction is "production" — "unknown" was skipped above.
            if group.production is not None:
                raise RuntimeError(
                    f"duplicate production note for ({note.slovene!r}, {note.english!r}): "
                    f"already have note {group.production.note_id}, got {note.note_id}"
                )
            group.production = note
    return list(buckets.values())


def _unified_from_group(
    group: MergeGroup, disambiguate: bool
) -> tuple[int, UnifiedFields, list[tuple[int, tuple[int, int]]], list[int]]:
    """Turn a ``MergeGroup`` into (keeper_id, unified_fields, card_reparents, notes_to_delete)."""
    card_reparents: list[tuple[int, tuple[int, int]]] = []
    notes_to_delete: list[int] = []

    keeper = group.recognition or group.production
    assert keeper is not None, "empty MergeGroup should not be built"

    # Merge audio/image/grammar/note: prefer whichever partner supplies the non-empty value.
    def _first_nonempty(*vals: str) -> str:
        for v in vals:
            if v:
                return v
        return ""

    r = group.recognition
    p = group.production

    slovene = (r.slovene if r else p.slovene) if (r or p) else ""
    english = (r.english if r else p.english) if (r or p) else ""
    disambig_key = english if disambiguate else ""

    audio = _first_nonempty(r.audio if r else "", p.audio if p else "")
    image = _first_nonempty(r.image if r else "", p.image if p else "")
    grammar = _first_nonempty(r.grammar if r else "", p.grammar if p else "")
    note_text = _first_nonempty(r.note if r else "", p.note if p else "")

    unified = UnifiedFields(
        slovene=slovene,
        english=english,
        audio=audio,
        image=image,
        grammar=grammar,
        note=note_text,
        disambig_key=disambig_key,
    )

    if r is not None and p is not None:
        # Recognition keeper; production's card moves to keeper with ord=1
        card_reparents.append((p.card_id, (r.note_id, 1)))
        notes_to_delete.append(p.note_id)
    elif p is not None and r is None:
        # Production-only singleton: flip card ord to 1 in place
        card_reparents.append((p.card_id, (p.note_id, 1)))
    # Recognition-only singleton: no reparenting; Anki auto-generates Production

    return keeper.note_id, unified, card_reparents, notes_to_delete


def build_plan(
    groups: list[MergeGroup],
    unknowns: list[ParsedNote],
    new_notetype_mid: int,
) -> MergePlan:
    """Turn groups + unknowns into a concrete ``MergePlan``."""
    plan = MergePlan(new_notetype_mid=new_notetype_mid, groups=groups)

    # Detect homonyms: same Slovene casefold across multiple groups.
    slovene_buckets: dict[str, list[str]] = defaultdict(list)
    for g in groups:
        slovene_buckets[g.slovene.casefold()].append(g.english)
    disambiguation_keys: set[str] = set()
    for slovene_key, englishes in slovene_buckets.items():
        if len({e.casefold() for e in englishes}) > 1:
            disambiguation_keys.add(slovene_key)
            # Preserve a stable report order
            plan.homonyms_requiring_disambiguation.append(
                (next(g.slovene for g in groups if g.slovene.casefold() == slovene_key), sorted(englishes))
            )

    for group in groups:
        disambiguate = group.slovene.casefold() in disambiguation_keys
        keeper_id, unified, reparents, deletes = _unified_from_group(group, disambiguate)
        plan.notes_to_update[keeper_id] = unified
        for card_id, target in reparents:
            plan.cards_to_reparent[card_id] = target
        plan.notes_to_delete.extend(deletes)

    # Unknown-direction notes are left on their original notetype (Basic) untouched.
    # Extracting structured fields from shapes we don't recognize would silently
    # destroy the original HTML (pronunciation cards, prompt-style notes, etc.).
    # backfill_guids handles these in-place on the Basic notetype.
    for unk in unknowns:
        plan.singletons_unknown_direction.append(unk)

    return plan


def _notetype_id_by_name(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute("SELECT id FROM notetypes WHERE name=?", (name,)).fetchone()
    return int(row[0]) if row is not None else None


def _create_notetype(conn: sqlite3.Connection, mid: int, now_ts: int) -> None:
    conn.execute(
        "INSERT INTO notetypes (id, name, mtime_secs, usn, config) VALUES (?, ?, ?, -1, ?)",
        (mid, SLOVENE_VOCAB_NOTETYPE_NAME, now_ts, build_notetype_config(css=SLOVENE_VOCAB_CSS)),
    )
    for f in slovene_vocab_fields():
        conn.execute(
            "INSERT INTO fields (ntid, ord, name, config) VALUES (?, ?, ?, ?)",
            (mid, f.ord, f.name, build_field_config(f.name)),
        )
    for t in slovene_vocab_templates():
        conn.execute(
            "INSERT INTO templates (ntid, ord, name, mtime_secs, usn, config) VALUES (?, ?, ?, ?, -1, ?)",
            (mid, t.ord, t.name, now_ts, build_template_config(t.qfmt, t.afmt)),
        )


def apply_merge(conn: sqlite3.Connection, plan: MergePlan, now_ts: int) -> None:
    """Apply the plan in a single transaction. Idempotent over notetype creation."""
    existing_mid = _notetype_id_by_name(conn, SLOVENE_VOCAB_NOTETYPE_NAME)
    mid = existing_mid if existing_mid is not None else plan.new_notetype_mid
    plan.new_notetype_mid = mid

    if (
        not plan.notes_to_update
        and not plan.cards_to_reparent
        and not plan.notes_to_delete
        and existing_mid is not None
    ):
        return

    try:
        conn.execute("BEGIN")
        if existing_mid is None:
            _create_notetype(conn, mid, now_ts)

        if plan.notes_to_update:
            conn.executemany(
                "UPDATE notes SET mid=?, flds=?, sfld=?, mod=?, usn=-1 WHERE id=?",
                [(mid, u.to_flds(), u.slovene, now_ts, nid) for nid, u in plan.notes_to_update.items()],
            )

        if plan.cards_to_reparent:
            conn.executemany(
                "UPDATE cards SET nid=?, ord=?, mod=?, usn=-1 WHERE id=?",
                [
                    (target_nid, target_ord, now_ts, card_id)
                    for card_id, (target_nid, target_ord) in plan.cards_to_reparent.items()
                ],
            )

        if plan.notes_to_delete:
            conn.executemany(
                "DELETE FROM notes WHERE id=?",
                [(nid,) for nid in plan.notes_to_delete],
            )

        conn.execute("UPDATE col SET mod=?, usn=-1", (now_ts,))
        conn.execute("COMMIT")
    except Exception:
        import contextlib as _contextlib

        with _contextlib.suppress(sqlite3.OperationalError):
            conn.execute("ROLLBACK")
        raise


def _audit_merge(conn: sqlite3.Connection, backup_path: Path, plan: MergePlan) -> None:
    """Post-run integrity checks.

    Any of the following raises ``RuntimeError``:
      - cards pointing to deleted/missing notes
      - card ord does not map to a template of its notetype
      - revlog row count or per-cid count changed
      - note count drop does not match ``len(plan.notes_to_delete)``
      - note ``guid``/``tags`` mutated on any row
    """
    backup = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    try:
        bak_notes = backup.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        bak_cards = backup.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        bak_revlog = backup.execute("SELECT COUNT(*) FROM revlog").fetchone()[0]
        bak_guid_by_id: dict[int, str] = dict(backup.execute("SELECT id, guid FROM notes").fetchall())
        bak_tags_by_id: dict[int, str] = dict(backup.execute("SELECT id, tags FROM notes").fetchall())
    finally:
        backup.close()

    src_notes = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    src_cards = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    src_revlog = conn.execute("SELECT COUNT(*) FROM revlog").fetchone()[0]

    if src_cards != bak_cards:
        raise RuntimeError(f"audit: cards count changed: {bak_cards} → {src_cards}")
    if src_revlog != bak_revlog:
        raise RuntimeError(f"audit: revlog count changed: {bak_revlog} → {src_revlog}")
    expected_drop = len(plan.notes_to_delete)
    if bak_notes - src_notes != expected_drop:
        raise RuntimeError(f"audit: notes count drop {bak_notes - src_notes} != planned {expected_drop}")

    orphans = conn.execute(
        "SELECT c.id, c.nid FROM cards c LEFT JOIN notes n ON n.id=c.nid WHERE n.id IS NULL"
    ).fetchall()
    if orphans:
        raise RuntimeError(f"audit: orphan cards: {orphans}")

    dangling = conn.execute(
        """
        SELECT c.id, c.ord, n.mid
        FROM cards c
        JOIN notes n ON n.id=c.nid
        LEFT JOIN templates t ON t.ntid=n.mid AND t.ord=c.ord
        WHERE t.ntid IS NULL
        """
    ).fetchall()
    if dangling:
        raise RuntimeError(f"audit: cards with ord not matching a template: {dangling}")

    guid_by_id = dict(conn.execute("SELECT id, guid FROM notes").fetchall())
    guid_changes = {
        nid: (bak_guid_by_id[nid], guid_by_id[nid])
        for nid in guid_by_id
        if nid in bak_guid_by_id and bak_guid_by_id[nid] != guid_by_id[nid]
    }
    if guid_changes:
        raise RuntimeError(f"audit: unplanned guid rewrites: {guid_changes}")

    tags_by_id = dict(conn.execute("SELECT id, tags FROM notes").fetchall())
    tag_changes = {
        nid: (bak_tags_by_id[nid], tags_by_id[nid])
        for nid in tags_by_id
        if nid in bak_tags_by_id and bak_tags_by_id[nid] != tags_by_id[nid]
    }
    if tag_changes:
        raise RuntimeError(f"audit: unplanned tag changes (unexpected writes): {tag_changes}")


def _load_card_maps(
    conn: sqlite3.Connection, deck_id: int, note_ids: list[int]
) -> tuple[dict[int, int], dict[int, int]]:
    """Return ``(card_ord_by_note, card_id_by_note)`` for cards in the given deck."""
    if not note_ids:
        return {}, {}
    placeholders = ",".join("?" * len(note_ids))
    rows = conn.execute(
        f"SELECT nid, id, ord FROM cards WHERE did=? AND nid IN ({placeholders})",
        (deck_id, *note_ids),
    ).fetchall()
    card_id_by_note: dict[int, int] = {}
    card_ord_by_note: dict[int, int] = {}
    for nid, cid, ord_ in rows:
        # Basic notetype has 1 card per note — first one wins if anything else sneaks in.
        card_id_by_note.setdefault(nid, cid)
        card_ord_by_note.setdefault(nid, ord_)
    return card_ord_by_note, card_id_by_note


def _print_summary(plan: MergePlan, *, dry_run: bool) -> None:
    pair_count = sum(1 for g in plan.groups if g.recognition is not None and g.production is not None)
    rec_only = sum(1 for g in plan.groups if g.recognition is not None and g.production is None)
    prod_only = sum(1 for g in plan.groups if g.production is not None and g.recognition is None)
    prefix = "[DRY RUN]" if dry_run else "[MERGE]"
    print(
        f"{prefix} paired={pair_count} recognition-only singletons={rec_only} "
        f"production-only singletons={prod_only} "
        f"homonyms={len(plan.homonyms_requiring_disambiguation)} "
        f"unknown-direction={len(plan.singletons_unknown_direction)} (left on original notetype)",
        flush=True,
    )
    if plan.homonyms_requiring_disambiguation:
        sample = plan.homonyms_requiring_disambiguation[:5]
        for slovene, englishes in sample:
            print(f"  homonym: {slovene} → {', '.join(englishes)}", flush=True)


def merge_dupes(
    deck_name: str | None = None,
    anki_collection_path: Path | None = None,
    anki_backup_dir: Path | None = None,
    dry_run: bool = False,
    yes: bool = False,
) -> dict[str, Any]:
    """Plan and optionally apply the pre-backfill merge for the target deck."""
    if deck_name is None:
        deck_name = settings.anki_deck_name
    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir

    result: dict[str, Any] = {
        "notes_migrated": 0,
        "cards_reparented": 0,
        "notes_deleted": 0,
        "planned_notes_migrated": 0,
        "planned_cards_reparented": 0,
        "planned_notes_deleted": 0,
        "aborted": False,
    }

    with safe_open(anki_collection_path, backup_dir=anki_backup_dir, mode="rw") as ctx:
        deck_id = find_deck_id(ctx.conn, deck_name)
        if deck_id is None:
            raise RuntimeError(f"Deck '{deck_name}' not found in {anki_collection_path}")

        existing_mid = _notetype_id_by_name(ctx.conn, SLOVENE_VOCAB_NOTETYPE_NAME)

        all_notes = fetch_notes_for_deck(ctx.conn, deck_id)
        # On re-run, skip notes already on the new notetype.
        notes = [n for n in all_notes if n.mid != existing_mid] if existing_mid is not None else all_notes

        note_ids = [n.id for n in notes]
        card_ord_by_note, card_id_by_note = _load_card_maps(ctx.conn, deck_id, note_ids)
        parsed = parse_notes(notes, card_ord_by_note=card_ord_by_note, card_id_by_note=card_id_by_note)

        groups = group_by_meaning(parsed)
        unknowns = [p for p in parsed if p.direction == "unknown"]

        new_mid = existing_mid if existing_mid is not None else int(time.time() * 1000)
        plan = build_plan(groups, unknowns=unknowns, new_notetype_mid=new_mid)

        result["planned_notes_migrated"] = len(plan.notes_to_update)
        result["planned_cards_reparented"] = len(plan.cards_to_reparent)
        result["planned_notes_deleted"] = len(plan.notes_to_delete)

        _print_summary(plan, dry_run=dry_run)

        if dry_run:
            return result

        if check_anki_web_sync_active(ctx.conn) and not yes:
            print(
                "AnkiWeb sync is active; pass --yes to acknowledge the force-upload requirement.",
                flush=True,
            )
            result["aborted"] = True
            return result

        if not plan.notes_to_update and not plan.cards_to_reparent and not plan.notes_to_delete:
            print("[NO-OP] collection already migrated.", flush=True)
            return result

        now_ts = int(time.time())
        apply_merge(ctx.conn, plan, now_ts=now_ts)
        _audit_merge(ctx.conn, ctx.backup_path, plan)

        result["notes_migrated"] = len(plan.notes_to_update)
        result["cards_reparented"] = len(plan.cards_to_reparent)
        result["notes_deleted"] = len(plan.notes_to_delete)
        print(
            f"[DONE] migrated={result['notes_migrated']} reparented={result['cards_reparented']} "
            f"deleted={result['notes_deleted']}",
            flush=True,
        )

    return result


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Merge Basic-notetype pairs into the Slovene Vocabulary notetype (Stage 2b-pre)"
    )
    parser.add_argument("--deck", default=None, help="Anki deck name")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without writing (backup still created).")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the AnkiWeb preflight abort; required when col.conf.syncKey is set.",
    )
    args = parser.parse_args()
    merge_dupes(deck_name=args.deck, dry_run=args.dry_run, yes=args.yes)


if __name__ == "__main__":  # pragma: no cover
    _cli()


__all__ = [
    "MergeGroup",
    "MergePlan",
    "ParsedNote",
    "UnifiedFields",
    "apply_merge",
    "build_plan",
    "group_by_meaning",
    "merge_dupes",
    "parse_notes",
]
