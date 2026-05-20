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
    card_type: int  # Anki's cards.type (0=New, 1=Learn, 2=Review, 3=Relearn)
    direction: Direction
    fsrs_state: DirectionState
    mod: int = 0  # Anki's cards.mod — needed for fnvhash(id, mod) sort tiebreak


def compute_due_at(queue: int, due_raw: int, col_crt: int, card_type: int = 0) -> datetime:
    """Convert Anki's queue-dependent due field to a UTC datetime.

    queue 2/3 (review/day-learn): due_raw is days since col.crt epoch → midnight UTC.
    queue 1 (learning): due_raw is an absolute unix timestamp (seconds).
    queue 0 (new): due_raw is a queue position → today at 04:00 UTC.

    queue -1/-2/-3 (suspended/buried): Anki preserves cards.due through bury and
    suspend; only the queue flips. We dispatch on ``card_type`` (the card's
    underlying type — 0=new, 1=learn, 2=review, 3=relearn) so the underlying due
    survives a sync round-trip. Without this, the daily unbury sweep would flip
    state back to review with a stale "today" due_at (Layer 44, 2026-05-20).

    Database corruption: some queue=2/3 cards have Unix timestamps in due_raw
    instead of days since col.crt. Detect this by checking if the value is too large
    to be days since col.crt (i.e., it's a Unix timestamp).
    """
    effective_queue = queue
    if queue in (-1, -2, -3):
        if card_type == 2:
            effective_queue = 2
        elif card_type == 3:
            effective_queue = 3
        elif card_type == 1:
            effective_queue = 1

    if effective_queue in (2, 3):
        if due_raw > 1000000000:
            return datetime.fromtimestamp(due_raw, tz=UTC)
        due_date = datetime.fromtimestamp(col_crt, tz=UTC).date() + timedelta(days=due_raw)
        return datetime.combine(due_date, time(4, 0), tzinfo=UTC)
    if effective_queue == 1:
        return datetime.fromtimestamp(due_raw, tz=UTC)
    return datetime.combine(date.today(), time(4, 0), tzinfo=UTC)


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
    # Also select `left` and `type` columns for learning step tracking
    rows = conn.execute(
        f"SELECT id, nid, did, ord, queue, reps, lapses, data, due, ivl, IFNULL(left,0), type, mod FROM cards WHERE nid IN ({placeholders})",
        note_ids,
    ).fetchall()
    cards = []
    for r in rows:
        card_id, note_id, deck_id, ord_, queue, reps, lapses, data_str, due_raw, ivl, left_val, card_type, card_mod = (
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
            r[11] or 0,
            r[12] or 0,
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
            card_type=card_type,
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
                card_type=card_type,
                direction=direction,
                fsrs_state=fsrs,
                mod=card_mod,
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
    card_type: int = 0,
) -> DirectionState:
    """Parse FSRS state from cards.data JSON. Falls back to NEW on missing/malformed data."""

    direction = Direction.RECOGNITION if ord == 0 else Direction.PRODUCTION
    due_at = compute_due_at(queue, due_raw, col_crt, card_type=card_type)

    # Compute last_review for review/relearning queues
    # BUT: if reps=0, the card is NEW regardless of queue, so last_review must be None
    last_review = None if reps == 0 else _compute_last_review(queue, due_raw, ivl, col_crt)

    if queue == -1:
        state = SRSState.SUSPENDED
    elif queue in (-2, -3):
        state = SRSState.BURIED
    elif queue == 1:
        # Anki uses queue=1 for both Learn and Relearn; distinguish by card_type
        # card_type=1 (Learn) -> LEARNING, card_type=3 (Relearn) -> RELEARNING
        state = SRSState.RELEARNING if card_type == 3 else SRSState.LEARNING
    elif queue == 3:
        state = SRSState.RELEARNING
    elif queue == 2:
        state = SRSState.REVIEW
    elif reps == 0:
        state = SRSState.NEW
    else:
        state = SRSState.REVIEW

    try:
        if data_str and data_str.strip():
            data = json.loads(data_str)
            stability = float(data["s"])
            difficulty = float(data["d"])
            # `lrt` (last_review_time, seconds since epoch) is Anki's authoritative
            # FSRS-scheduler-effective last-review timestamp, used by Anki's own
            # `extract_fsrs_retrievability` SQL function (rslib/src/storage/sqlite.rs).
            # For cards graded multiple times in one session (Again → relearning
            # step → graduate), `lrt` sticks to the FSRS-touched grade while
            # MAX(revlog.id) per card advances on every step. Using lrt is what
            # makes TT's R-asc order match Anki's. Fall back to the day-level
            # `_compute_last_review` value when lrt is absent (pre-FSRS cards).
            lrt_seconds = data.get("lrt")
            resolved_last_review = (
                datetime.fromtimestamp(lrt_seconds, tz=UTC) if lrt_seconds is not None else last_review
            )
            return DirectionState(
                direction=direction,
                due_at=due_at,
                stability=stability,
                difficulty=difficulty,
                reps=reps,
                lapses=lapses,
                state=state,
                anki_card_id=card_id,
                anki_due=due_raw,
                last_review=resolved_last_review,
                left=left if left != 0 else None,
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
        due_at=due_at,
        stability=1.0,
        difficulty=5.0,
        reps=reps,
        lapses=lapses,
        state=state,
        anki_card_id=card_id,
        anki_due=due_raw,
        last_review=last_review,
        left=left if left != 0 else None,
    )


def _compute_last_review(
    queue: int,
    due_raw: int,
    ivl: int,
    col_crt: int,
    rollover_hour: int = 4,
) -> datetime | None:
    """Compute last_review datetime (midnight UTC) for queue 2/3 cards.

    Anki only persists day-level last review; we promote to midnight UTC so the
    field type matches DirectionState.last_review and round-trips cleanly through
    DB writes that expect datetime.

    The datetime is placed at midnight UTC of the first calendar day that falls
    within the same Anki col_day as ``due_raw - ivl``.  This preserves
    ``compute_anki_day_index(col_crt, rollover_hour, result) == due_raw - ivl``
    — matching the review_col_day Anki stores — while keeping the midnight-UTC
    marker that signals the day-level branch in ``_elapsed_days_for_fsrs``.

    Layer 45 fix: old code stripped col_crt's time-of-day via ``.date()``,
    placing midnight in the *previous* col_day when col_crt is not on the
    col-day boundary, creating a persistent -1 offset against Anki.
    """
    if queue not in (2, 3):
        return None
    review_col_day = due_raw - ivl
    col_day_start = col_crt - rollover_hour * 3600
    first_midnight = col_day_start if col_day_start % 86400 == 0 else (col_day_start // 86400 + 1) * 86400
    last_review_ts = first_midnight + review_col_day * 86400
    return datetime.fromtimestamp(last_review_ts, tz=UTC)


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
    """Extract media filenames referenced in field HTML.

    The img-src pattern handles `>` characters inside quoted attribute values
    by matching either a non-quote chunk or a fully-quoted string between
    `<img` and `src="..."`. Without this, an alt attribute like
    `alt="Army Guard > National Guard"` would terminate `[^>]+` early and
    the src filename would be missed — silently breaking
    `_refresh_media_for_collocation` for any note whose alt text contains
    `>` (common for pasted images from web breadcrumbs).
    """
    refs: list[str] = []
    for field in fields:
        refs.extend(re.findall(r"\[sound:([^\]]+)\]", field))
        refs.extend(re.findall(r'<img(?:[^"]|"[^"]*")*?\bsrc="([^"]+)"', field))
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


_B_THEN_I_PATTERN = re.compile(
    r"^\s*<b>([^<]+)</b>\s*<br\s*/?>\s*<i>([^<]+)</i>",
    re.IGNORECASE,
)


def extract_gloss_from_fields(fields: list[str]) -> str | None:
    """Return the English gloss when a field uses the `<b>L2</b><br><i>EN</i>` pattern.

    Slovene Pronunciation/Basic notetype cards put both the L2 word and its
    English gloss in the same field with HTML formatting (e.g.
    ``<b>nič</b><br><i>nothing</i>``). The naive HTML-strip used by
    `extract_translation` joins them into ``ničnothing``; this helper recovers
    the English gloss cleanly. Returns None when no field matches the pattern.
    """
    for field in fields:
        m = _B_THEN_I_PATTERN.match(field)
        if m:
            return m.group(2).strip()
    return None


_QA_INTERROGATIVES = frozenset(
    [
        "what",
        "how",
        "where",
        "when",
        "why",
        "who",
        "which",
        "whose",
        "whom",
        "can",
        "could",
        "would",
        "should",
        "do",
        "does",
        "did",
        "is",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "will",
        "may",
        "might",
    ]
)


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

    # Second pass (Layer 31): `<b>L2</b><br><i>EN</i>` pattern used by the
    # Pronunciation/Basic notetype. The HTML-strip fallback would concatenate
    # the two inner texts (``ničnothing``); pick the `<b>` group instead.
    for field in fields:
        m = _B_THEN_I_PATTERN.match(field)
        if m:
            return m.group(1).strip()

    # Q&A pass: if Field 0 is an English question (starts with an interrogative
    # like What/How/Where/... and ends with "?"), it IS the L2-side prompt for
    # the card. Without this, an IPA-laden answer in Field 1 can outscore the
    # question on the Slovene-char heuristic — see the 11 phonology Q&A notes
    # (cid 790–801) that ended up reversed in TT before this rule.
    if fields:
        first = re.sub(r"<[^>]+>", "", fields[0]).strip()
        if first.endswith("?"):
            first_word = first.split()[0].lower().strip("'") if first.split() else ""
            if first_word in _QA_INTERROGATIVES:
                return first

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
