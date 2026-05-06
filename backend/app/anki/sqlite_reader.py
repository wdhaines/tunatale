"""Read-only helpers for querying a collection.anki2 SQLite database."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from app.models.srs_item import Direction, DirectionState, SRSState


@dataclass
class AnkiNote:
    id: int
    anki_guid: str
    mid: int
    mod: int
    tags: list[str]
    fields: list[str]  # split on \x1f


@dataclass
class AnkiCard:
    id: int
    note_id: int
    deck_id: int
    ord: int
    queue: int
    reps: int
    lapses: int
    direction: Direction
    fsrs_state: DirectionState


def compute_due_date(queue: int, due_raw: int, col_crt: int) -> date:
    """Convert Anki's queue-dependent due field to a Python date.

    queue 2/3 (review/day-learn): due_raw is days since col.crt epoch.
    queue 1 (learning): due_raw is an absolute unix timestamp (seconds).
    queue 0 (new) or -1 (suspended): due_raw is a queue position — fall back to today.

    Database corruption: some queue=2/3 cards have Unix timestamps in due_raw
    instead of days since col.crt. Detect this by checking if the value is too large
    to be days since col.crt (i.e., it's a Unix timestamp).
    """
    if queue in (2, 3):
        # Heuristic: if due_raw looks like a Unix timestamp (seconds since epoch),
        # treat it as such. Otherwise, treat as days since col.crt.
        # Unix timestamp for year 2000 ≈ 946684800
        if due_raw > 1000000000:  # Likely a Unix timestamp in seconds
            return datetime.fromtimestamp(due_raw).date()
        return date.fromtimestamp(col_crt) + timedelta(days=due_raw)
    if queue == 1:
        return datetime.fromtimestamp(due_raw).date()
    return date.today()


def find_deck_id(conn: sqlite3.Connection, deck_name: str) -> int | None:
    """Find deck id by name. Tries col.decks JSON (legacy) then decks table (modern)."""
    row = conn.execute("SELECT decks FROM col").fetchone()
    if row:
        try:
            deck_data = json.loads(row[0])
            for did, info in deck_data.items():
                if isinstance(info, dict) and info.get("name") == deck_name:
                    return int(did)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            pass

    try:
        rows = conn.execute("SELECT id, name FROM decks").fetchall()
        for r in rows:
            if r[1] == deck_name:
                return r[0]
    except sqlite3.OperationalError:
        pass

    return None


def fetch_notes_for_deck(conn: sqlite3.Connection, deck_id: int) -> list[AnkiNote]:
    """Fetch all notes that have at least one card in the given deck."""
    rows = conn.execute(
        """
        SELECT DISTINCT n.id, n.guid, n.mid, n.mod, n.tags, n.flds
        FROM notes n
        JOIN cards c ON c.nid = n.id
        WHERE c.did = ?
        """,
        (deck_id,),
    ).fetchall()
    notes = []
    for r in rows:
        fields = r[5].split("\x1f")
        tags = [t for t in r[4].strip().split() if t]
        notes.append(AnkiNote(id=r[0], anki_guid=r[1], mid=r[2], mod=r[3], tags=tags, fields=fields))
    return notes


def fetch_cards_for_notes(
    conn: sqlite3.Connection,
    note_ids: list[int],
    fallback_log_path: Path | None = None,
) -> list[AnkiCard]:
    """Fetch all cards for the given note IDs, parsing FSRS state from cards.data."""
    if not note_ids:
        return []

    col_row = conn.execute("SELECT crt FROM col LIMIT 1").fetchone()
    col_crt: int = int(col_row[0]) if col_row else 0

    placeholders = ",".join("?" * len(note_ids))
    # Also select `left` column for learning step tracking
    rows = conn.execute(
        f"SELECT id, nid, did, ord, queue, reps, lapses, data, due, ivl, IFNULL(left, 0) FROM cards WHERE nid IN ({placeholders})",
        note_ids,
    ).fetchall()
    cards = []
    for r in rows:
        card_id, note_id, deck_id, ord_, queue, reps, lapses, data_str, due_raw, ivl, left_val = (
            r[0],
            r[1],
            r[2],
            r[3],
            r[4],
            r[5],
            r[6],
            r[7] or "",
            r[8] or 0,
            r[9] or 0,
            r[10] or 0,
        )
        fsrs = parse_fsrs_data(
            card_id=card_id,
            ord=ord_,
            data_str=data_str,
            queue=queue,
            reps=reps,
            lapses=lapses,
            fallback_log_path=fallback_log_path,
            col_crt=col_crt,
            due_raw=due_raw,
            ivl=ivl,
            left=left_val,
        )
        direction = Direction.RECOGNITION if ord_ == 0 else Direction.PRODUCTION
        cards.append(
            AnkiCard(
                id=card_id,
                note_id=note_id,
                deck_id=deck_id,
                ord=ord_,
                queue=queue,
                reps=reps,
                lapses=lapses,
                direction=direction,
                fsrs_state=fsrs,
            )
        )
    return cards


def parse_fsrs_data(
    card_id: int,
    ord: int,
    data_str: str,
    queue: int,
    reps: int,
    lapses: int,
    fallback_log_path: Path | None = None,
    col_crt: int = 0,
    due_raw: int = 0,
    ivl: int = 0,
    left: int = 0,
) -> DirectionState:
    """Parse FSRS state from cards.data JSON. Falls back to NEW on missing/malformed data."""

    direction = Direction.RECOGNITION if ord == 0 else Direction.PRODUCTION
    due_date = compute_due_date(queue, due_raw, col_crt)

    # Compute last_review for review/relearning queues
    # BUT: if reps=0, the card is NEW regardless of queue, so last_review must be None
    last_review = None if reps == 0 else _compute_last_review(queue, due_raw, ivl, col_crt)

    # Compute due_at for learning/relearning cards (sub-day learning)
    due_at = None
    if queue == 1:
        # queue=1: due_raw is an absolute unix timestamp (seconds)
        due_at = datetime.fromtimestamp(due_raw, tz=UTC)
    elif queue == 3:
        # queue=3: due_raw is days since col_crt epoch; set to midnight UTC
        due_at = datetime.fromtimestamp(col_crt, tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=due_raw
        )

    if queue == -1:
        state = SRSState.SUSPENDED
    elif queue in (-2, -3):
        state = SRSState.BURIED
    elif queue == 1:
        state = SRSState.LEARNING
    elif queue == 3:
        state = SRSState.RELEARNING
    elif reps == 0:
        state = SRSState.NEW
    else:
        state = SRSState.REVIEW

    try:
        if data_str and data_str.strip():
            data = json.loads(data_str)
            stability = float(data["s"])
            difficulty = float(data["d"])
            return DirectionState(
                direction=direction,
                due_date=due_date,
                stability=stability,
                difficulty=difficulty,
                reps=reps,
                lapses=lapses,
                state=state,
                anki_card_id=card_id,
                anki_due=due_raw,
                last_review=last_review,
                left=left if left != 0 else None,
                due_at=due_at,
            )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass

    # Fallback
    if fallback_log_path is not None:
        fallback_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(fallback_log_path, "a") as f:
            f.write(f"{card_id}\n")

    return DirectionState(
        direction=direction,
        due_date=due_date,
        stability=1.0,
        difficulty=5.0,
        reps=reps,
        lapses=lapses,
        state=state,
        anki_card_id=card_id,
        anki_due=due_raw,
        last_review=last_review,
        left=left if left != 0 else None,
        due_at=due_at,
    )


def _compute_last_review(queue: int, due_raw: int, ivl: int, col_crt: int) -> datetime | None:
    """Compute last_review datetime (midnight UTC) for queue 2/3 cards.

    Anki only persists day-level last review; we promote to midnight UTC so the
    field type matches DirectionState.last_review and round-trips cleanly through
    DB writes that expect datetime.
    """
    if queue in (2, 3):
        d = datetime.fromtimestamp(col_crt, tz=UTC).date() + timedelta(days=due_raw - ivl)
        return datetime.combine(d, time.min, tzinfo=UTC)
    return None


def read_fsrs_state_for_cards(collection_path: str | Path, card_ids: list[int]) -> dict[int, tuple[float, float]]:
    """Read FSRS (stability, difficulty) from cards.data JSON for the given card IDs.

    Opens the collection.anki2 file with `mode=ro&immutable=1` so it works while Anki is running.
    Returns a dict {card_id: (stability, difficulty)} containing only cards whose `data`
    parses as JSON with both `s` and `d` keys; cards with malformed data are silently omitted
    so callers can fall back to whatever default state they already had.
    """
    path = Path(collection_path)
    if not path.exists():
        raise FileNotFoundError(f"Anki collection not found: {path}")
    if not card_ids:
        return {}

    uri = f"file:{path}?mode=ro&immutable=1"
    placeholders = ",".join("?" * len(card_ids))
    result: dict[int, tuple[float, float]] = {}
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(f"SELECT id, data FROM cards WHERE id IN ({placeholders})", card_ids).fetchall()
    for cid, data_str in rows:
        if not data_str:
            continue
        try:
            data = json.loads(data_str)
            result[cid] = (float(data["s"]), float(data["d"]))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return result


def list_media_refs(fields: list[str]) -> list[str]:
    """Extract media filenames referenced in field HTML."""
    refs: list[str] = []
    for field in fields:
        refs.extend(re.findall(r"\[sound:([^\]]+)\]", field))
        refs.extend(re.findall(r'<img[^>]+src="([^"]+)"', field))
    return refs


def extract_translation(field_html: str) -> str:
    """Strip HTML from a translation field, returning plain text."""
    return re.sub(r"<[^>]+>", "", field_html).strip()


def extract_l2(field_html: str) -> str:
    """Extract L2 text from a field, preferring elements with class="slovene"."""
    m = re.search(r'class="slovene"[^>]*>\s*([^<]+?)\s*<', field_html)
    if m:
        return m.group(1).strip()
    clean = re.sub(r"<[^>]+>", "", field_html)
    return clean.strip()


def extract_l2_from_fields(fields: list[str]) -> str:
    """Return the L2 text from fields, preferring class="slovene" markup.

    Anki notes with inverse card layouts put the image in ``fields[0]`` and the
    target-language word in ``fields[1]``; callers should pass the whole list so
    we find the L2 text regardless of which slot it lives in.

    When no field has ``class="slovene"``, falls back to the field whose stripped
    text contains Slovene characters (č, š, ž, etc.) or fewer English stopwords,
    to avoid returning long English questions from phonics cards.
    """
    # First pass: find field with class="slovene"
    for field in fields:
        m = re.search(r'class="slovene"[^>]*>\s*([^<]+?)\s*<', field)
        if m:
            return m.group(1).strip()

    # Second pass: no field has class="slovene".
    # Score each field: prefer fields with Slovene-specific or IPA phonetic characters
    # (since phonics cards have answers with IPA like [ɛ], [bɛˈseːda], etc.)
    # Slovene-specific characters (not in English) plus dictionary stress diacritics
    # used in pronunciation hints (besêda, oblákov).
    _SLOVENE_CHARS = set("čšžđćČŠŽĐĆáàâäéèêëíìîïóòôöúùûüŕÁÀÂÄÉÈÊËÍÌÎÏÓÒÔÖÚÙÛÜŔ")
    _IPA_CHARS = set("ɛəɔɪʊæθðŋɲʃʒɕʑɯɰʔˈˌːˈ́")
    _ENGLISH_STOPWORDS = {
        "what",
        "where",
        "when",
        "how",
        "why",
        "is",
        "are",
        "does",
        "do",
        "did",
        "was",
        "were",
        "the",
        "a",
        "an",
        "per",
        "after",
        "before",
        "of",
        "in",
        "on",
        "to",
        "with",
        "for",
    }

    def _l2_score(clean: str) -> float:
        score = 0.0
        for ch in clean:
            if ch in _SLOVENE_CHARS:
                score += 1
            elif ch in _IPA_CHARS:
                score += 0.5
        for w in clean.lower().split():
            if w.strip("?,.!:;") in _ENGLISH_STOPWORDS:
                score -= 2
        return score

    best_field = ""
    best_score = float("-inf")
    for field in fields:
        clean = re.sub(r"<[^>]+>", "", field).strip()
        if not clean:
            continue
        score = _l2_score(clean)
        # Strict `>`: on a true score tie, the EARLIER field wins. The deck
        # has mixed forward (L2 in fields[0]) and inverse (L2 in fields[1+])
        # layouts, but the inverse case is handled in the first pass via
        # class="slovene". When no class marker exists, forward layout is the
        # safe default — picking later would regress vocab cards like
        # "banka"/"bank" where neither has Slovene-specific characters.
        if score > best_score:
            best_score = score
            best_field = clean

    return best_field


def extract_disambig_from_fields(fields: list[str]) -> str:
    """Return field index 6 (DisambigKey), stripped, or '' if absent."""
    return fields[6].strip() if len(fields) > 6 else ""
