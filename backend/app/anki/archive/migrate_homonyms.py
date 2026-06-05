"""Stage H3 — one-shot Anki migration: move suffix from Slovene field into DisambigKey.

For each note on the 'Slovene Vocabulary' notetype:
  - If Slovene field matches 'word (disambig)': split into bare word + DisambigKey.
  - If no suffix but note.id is in the audit JSON as 'edited_away_from_suffix': recover
    the disambiguator from tt_stored_text and write it into DisambigKey.
  - Else: leave unchanged (skipped).

After running, backfill_guids with --force will rewrite notes.guid to match the new
compute_guid(bare_text, lang, disambig_key) formula.

This migration bumps ``col.scm`` (adds the DisambigKey field), so AnkiWeb will
demand a full upload on next sync. See ``.claude/rules/anki-sync.md`` for the
required 3-step post-migration workflow (full upload → normalize_usns).

Usage:
    uv run python -m app.anki.migrate_homonyms [--deck "0. Slovene"] [--dry-run]
    uv run python -m app.anki.migrate_homonyms --audit-json ~/.tunatale/logs/guid-divergence-*.json
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from app.anki.archive.notetype_builders import build_field_config
from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME
from app.anki.safety import safe_open
from app.anki.sqlite_reader import find_deck_id
from app.config import settings

_SUFFIX_RE = re.compile(r"^(.+?)\s\((.+)\)$")


def migrate_homonyms(
    deck_name: str | None = None,
    anki_collection_path: Path | None = None,
    anki_backup_dir: Path | None = None,
    dry_run: bool = False,
    audit_json: Path | None = None,
) -> dict[str, int]:
    """Strip suffix from Slovene field and write DisambigKey for all matching notes.

    audit_json: optional path to a guid-divergence JSON produced by audit_guids.
    When provided, notes whose Slovene was edited back (no suffix) but whose
    note_id appears in the JSON as 'edited_away_from_suffix' get their
    DisambigKey recovered from tt_stored_text.

    Returns {'stripped': N, 'skipped': M, 'recovered': R} counts.
    """
    if deck_name is None:
        deck_name = settings.anki_deck_name
    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir

    # Build recovery map from audit JSON: {note_id: disambig}
    recovery_map: dict[int, str] = {}
    if audit_json is not None:
        data = json.loads(Path(audit_json).read_text())
        for entry in data.get("divergent", []):
            if entry.get("classification") == "edited_away_from_suffix":
                m = _SUFFIX_RE.match(entry.get("tt_stored_text", ""))
                if m:
                    recovery_map[entry["note_id"]] = m.group(2)

    results = {"stripped": 0, "skipped": 0, "recovered": 0, "padded": 0}

    with safe_open(anki_collection_path, backup_dir=anki_backup_dir, mode="rw") as ctx:
        conn = ctx.conn
        deck_id = find_deck_id(conn, deck_name)
        if deck_id is None:
            raise RuntimeError(f"Deck '{deck_name}' not found in {anki_collection_path}")

        # Find the Slovene Vocabulary notetype id
        mid_row = conn.execute("SELECT id FROM notetypes WHERE name = ?", (SLOVENE_VOCAB_NOTETYPE_NAME,)).fetchone()
        if mid_row is None:
            print(f"Notetype '{SLOVENE_VOCAB_NOTETYPE_NAME}' not found — nothing to migrate.")
            return results

        mid = mid_row["id"]

        # Ensure DisambigKey field (ord=6) exists in the notetype's fields table.
        # Without this, Anki would truncate the 7th flds value on next open.
        #
        # Adding a field is a schema change. Anki's consistency model requires
        # notetypes.mtime_secs and notetypes.usn to reflect the change, plus
        # col.scm bumped to force AnkiWeb to re-evaluate. Skip these bumps and
        # Anki's "Check Database" on next open does them itself, which triggers
        # a surprise full collection resync.
        now_secs = int(time.time())
        now_ms = now_secs * 1000
        field_count = conn.execute("SELECT COUNT(*) FROM fields WHERE ntid = ?", (mid,)).fetchone()[0]
        if field_count < 7:
            conn.execute(
                "INSERT OR IGNORE INTO fields (ntid, ord, name, config) VALUES (?, 6, 'DisambigKey', ?)",
                (mid, build_field_config("DisambigKey")),
            )
            conn.execute(
                "UPDATE notetypes SET mtime_secs = ?, usn = -1 WHERE id = ?",
                (now_secs, mid),
            )
            conn.execute("UPDATE col SET scm = ?", (now_ms,))
            print(f"  Added DisambigKey field (ord=6) to notetype id={mid}", flush=True)

        # Load notes on the Slovene Vocabulary notetype that are in the target deck
        notes = conn.execute(
            """
            SELECT DISTINCT n.id, n.flds, n.mod
            FROM notes n JOIN cards c ON c.nid = n.id
            WHERE n.mid = ? AND c.did = ?
            """,
            (mid, deck_id),
        ).fetchall()

        now_ts = now_secs
        updates: list[tuple[str, int, int]] = []

        for note in notes:
            nid = note["id"]
            fields = note["flds"].split("\x1f")
            was_padded = len(fields) < 7
            if was_padded:
                fields += [""] * (7 - len(fields))

            slovene = fields[0]
            m = _SUFFIX_RE.match(slovene)
            if m:
                bare = m.group(1)
                disambig = m.group(2)
                # Assert ownership: disambig must appear in English field (field 1)
                english = fields[1]
                if disambig.strip().lower() not in english.lower():
                    print(
                        f"  WARNING nid={nid}: suffix '{disambig}' not found in EN='{english}' — skipping",
                        flush=True,
                    )
                    results["skipped"] += 1
                    continue
                fields[0] = bare
                fields[6] = disambig
                new_flds = "\x1f".join(fields)
                print(f"  nid={nid}: '{slovene}' → bare='{bare}' disambig='{disambig}'", flush=True)
                updates.append((new_flds, now_ts, nid))
                results["stripped"] += 1
            elif nid in recovery_map:
                # Note was edited back by user (no suffix), recover DisambigKey from audit JSON
                recovered_disambig = recovery_map[nid]
                fields[6] = recovered_disambig
                new_flds = "\x1f".join(fields)
                print(
                    f"  nid={nid}: recovered disambig='{recovered_disambig}' for bare slovene='{slovene}'",
                    flush=True,
                )
                updates.append((new_flds, now_ts, nid))
                results["recovered"] += 1
            elif was_padded:
                # Persist the pad even when there's no suffix to strip — otherwise the
                # notetype has 7 fields but this row's flds has 6, and Anki raises
                # 'note has N fields, expected 7' on next open.
                new_flds = "\x1f".join(fields)
                updates.append((new_flds, now_ts, nid))
                results["padded"] += 1
            else:
                results["skipped"] += 1

        if dry_run:
            print(f"[DRY RUN] would strip {results['stripped']} padded {results['padded']} skip {results['skipped']}")
            return results

        if updates:
            conn.executemany(
                "UPDATE notes SET flds = ?, mod = ?, usn = -1 WHERE id = ?",
                updates,
            )
            conn.execute("UPDATE col SET mod = ?, usn = -1", (now_ts,))
            conn.commit()

        print(
            f"[DONE] stripped={results['stripped']} padded={results['padded']} skipped={results['skipped']}",
            flush=True,
        )

    return results


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="H3: strip suffix from Slovene field → DisambigKey")
    parser.add_argument("--deck", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--audit-json", default=None, type=Path, metavar="PATH")
    args = parser.parse_args()
    migrate_homonyms(deck_name=args.deck, dry_run=args.dry_run, audit_json=args.audit_json)


if __name__ == "__main__":  # pragma: no cover
    _cli()
