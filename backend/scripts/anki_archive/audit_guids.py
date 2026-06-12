"""Stage H1 — read-only GUID-divergence diagnostic.

Usage:
    uv run python -m app.anki.audit_guids [--deck "0. Slovene"]

Outputs a JSON report to ~/.tunatale/logs/guid-divergence-<ts>.json.
No writes to any database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.anki.safety import safe_open
from app.anki.sqlite_reader import extract_l2_from_fields, fetch_notes_for_deck, find_deck_id
from app.config import settings

_SUFFIX_RE = re.compile(r"^(.+?)\s\(.+\)$")


def _compute_guid_legacy(text: str, lang: str) -> str:
    """Pre-H2 guid formula: sha1(lang + NFC(casefold(text)))[:16].

    Used so the audit compares against the formula that backfill_guids used when
    the user's real Anki collection was last written — not the new H2 formula.
    Without this, every note on the deck would appear divergent simply because
    the formula changed, making the audit useless as a "which notes did I edit" tool.
    """
    normalized = unicodedata.normalize("NFC", text.casefold())
    return hashlib.sha1((lang + normalized).encode()).hexdigest()[:16]


@dataclass
class DivergentNote:
    note_id: int
    stored_guid: str
    expected_guid: str
    current_slovene: str
    classification: str  # edited_away_from_suffix | both_orphans | edited_toward_different_text
    tt_stored_text: str  # text from the TunaTale row that owns stored_guid (or "")


@dataclass
class TtOrphan:
    text: str
    guid: str


@dataclass
class AuditResult:
    deck_name: str
    note_count: int
    divergent: list[DivergentNote] = field(default_factory=list)
    tt_orphans: list[TtOrphan] = field(default_factory=list)


def _classify(current_slovene: str, tt_text: str | None) -> str:
    if tt_text is None:
        return "both_orphans"
    if _SUFFIX_RE.match(tt_text) and not _SUFFIX_RE.match(current_slovene):
        return "edited_away_from_suffix"
    return "edited_toward_different_text"


def run_audit(
    deck_name: str | None = None,
    anki_collection_path: Path | None = None,
    anki_backup_dir: Path | None = None,
    tunatale_db_path: str | None = None,
    language_code: str = "sl",
) -> AuditResult:
    if deck_name is None:
        deck_name = settings.anki_deck_name
    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir
    if tunatale_db_path is None:
        tunatale_db_path = settings.database_url.replace("sqlite:///", "")

    # Read Anki notes (read-only, with backup)
    with safe_open(anki_collection_path, backup_dir=anki_backup_dir, mode="ro") as ctx:
        deck_id = find_deck_id(ctx.conn, deck_name)
        if deck_id is None:
            raise RuntimeError(f"Deck '{deck_name}' not found in {anki_collection_path}")
        notes = fetch_notes_for_deck(ctx.conn, deck_id)

    # Read TunaTale directly — avoid SRSDatabase to prevent triggering migrations
    tt_conn = sqlite3.connect(tunatale_db_path)
    try:
        tt_by_guid: dict[str, str] = {
            row[0]: row[1] for row in tt_conn.execute("SELECT guid, text FROM collocations").fetchall()
        }
        tt_orphan_rows = tt_conn.execute("SELECT text, guid FROM collocations WHERE anki_note_id IS NULL").fetchall()
    finally:
        tt_conn.close()

    result = AuditResult(deck_name=deck_name, note_count=len(notes))

    for note in notes:
        current_slovene = extract_l2_from_fields(note.fields)
        expected_guid = _compute_guid_legacy(current_slovene, language_code)
        stored_guid = note.anki_guid
        if stored_guid == expected_guid:
            continue
        tt_text = tt_by_guid.get(stored_guid)
        classification = _classify(current_slovene, tt_text)
        result.divergent.append(
            DivergentNote(
                note_id=note.id,
                stored_guid=stored_guid,
                expected_guid=expected_guid,
                current_slovene=current_slovene,
                classification=classification,
                tt_stored_text=tt_text or "",
            )
        )

    for text, guid in tt_orphan_rows:
        if _SUFFIX_RE.match(text):
            result.tt_orphans.append(TtOrphan(text=text, guid=guid))

    return result


def _write_report(result: AuditResult) -> Path:
    log_dir = Path.home() / ".tunatale" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out = log_dir / f"guid-divergence-{ts}.json"
    out.write_text(json.dumps(asdict(result), indent=2))
    return out


def _cli() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="H1 audit: report GUID divergence (read-only)")
    parser.add_argument("--deck", default=None)
    args = parser.parse_args()

    result = run_audit(deck_name=args.deck)
    out = _write_report(result)

    print(f"Notes scanned : {result.note_count}")
    print(f"Divergent     : {len(result.divergent)}")
    print(f"TT orphans    : {len(result.tt_orphans)}")
    print(f"Report written: {out}")
    if result.divergent:
        print("\nDivergent notes:")
        for n in result.divergent:
            print(f"  nid={n.note_id}  [{n.classification}]  anki={n.current_slovene!r}  tt={n.tt_stored_text!r}")


if __name__ == "__main__":  # pragma: no cover
    _cli()
