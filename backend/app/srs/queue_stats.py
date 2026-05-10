"""Resolve the daily new-card cap and FSRS params from the Anki state cache or config fallbacks."""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import settings
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, FSRSParams

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.srs.database import SRSDatabase

_CACHE_MAX_AGE_DAYS = 30

# Field numbers in DeckConfig.Config protobuf (Anki ≥24.04)
_FSRS5_WEIGHTS_FIELD = 5  # LEN-delimited packed f32; 19 floats for FSRS-5
_DESIRED_RETENTION_FIELD = 40  # FIXED32 float


def _register_unicase(conn: sqlite3.Connection) -> None:
    """Register the unicase collation so queries against Anki's COLLATE unicase columns work."""

    def _unicase(a: str, b: str) -> int:
        af, bf = a.casefold(), b.casefold()
        return (af > bf) - (af < bf)

    conn.create_collation("unicase", _unicase)


def _compute_today_col_day(conn: sqlite3.Connection) -> int:
    """Compute today's col-day integer from col.crt, matching Anki's formula.

    Anki's actual formula: col_day = (now - col.crt) / 86400
    """
    row = conn.execute("SELECT crt FROM col LIMIT 1").fetchone()
    if row is None:
        return 0
    return int((time.time() - row[0]) // 86400)


def _read_did_for_deck(conn: sqlite3.Connection, deck_name: str) -> int | None:
    """Return the deck ID for the given deck name, or None if not found."""
    row = conn.execute("SELECT id FROM decks WHERE name = ?", (deck_name,)).fetchone()
    return row[0] if row is not None else None


def _read_conf_id_for_deck(conn: sqlite3.Connection, deck_name: str) -> int | None:
    """Return the conf_id for the given deck name, or None if not found.

    Reads decks.kind blob → NormalKind (field 1 LEN) → conf_id (field 1 VARINT).
    """
    deck_row = conn.execute("SELECT kind FROM decks WHERE name = ?", (deck_name,)).fetchone()
    if deck_row is None or not deck_row[0]:
        return None

    kind_blob = bytes(deck_row[0]) if isinstance(deck_row[0], memoryview) else deck_row[0]
    normal_kind_bytes = _pb_find_len_field(kind_blob, 1)
    if normal_kind_bytes is None:
        return None

    return _pb_find_varint_field(normal_kind_bytes, 1)


def _read_review_caps(conn: sqlite3.Connection, conf_id: int) -> tuple[int, bool] | None:
    """Return (reviews_per_day, new_cards_ignore_review_limit) from deck_config blob.

    Fields in DeckConfig.Config protobuf:
      - field 10 (VARINT) = reviews_per_day (default 9999)
      - field 7 (VARINT/bool) = new_cards_ignore_review_limit (default False)

    Returns None if the config blob cannot be read.
    """
    config_row = conn.execute("SELECT config FROM deck_config WHERE id = ?", (conf_id,)).fetchone()
    if config_row is None or not config_row[0]:
        return None

    config_blob = bytes(config_row[0]) if isinstance(config_row[0], memoryview) else config_row[0]

    reviews_per_day = _pb_find_varint_field(config_blob, 10)
    if reviews_per_day is None:
        reviews_per_day = 9999

    ignore_limit = _pb_find_varint_field(config_blob, 7)
    new_cards_ignore_review_limit = bool(ignore_limit) if ignore_limit is not None else False

    return (reviews_per_day, new_cards_ignore_review_limit)


def _read_today_studied_counts(conn: sqlite3.Connection, did: int, today_col_day: int) -> tuple[int, int]:
    """Return (new_studied, review_studied) for today from decks.common blob.

    Reads DeckCommon protobuf (stored in decks.common blob):
      - field 3 (VARINT) = last_day_studied
      - field 4 (VARINT) = new_studied
      - field 5 (VARINT) = review_studied

    If last_day_studied != today_col_day, returns (0, 0) (rollover).
    """
    row = conn.execute("SELECT common FROM decks WHERE id = ?", (did,)).fetchone()
    if row is None or not row[0]:
        return (0, 0)

    common_blob = bytes(row[0]) if isinstance(row[0], memoryview) else row[0]

    last_day = _pb_find_varint_field(common_blob, 3)
    new_studied = _pb_find_varint_field(common_blob, 4) or 0
    review_studied = _pb_find_varint_field(common_blob, 5) or 0

    if last_day is None or last_day != today_col_day:
        return (0, 0)

    return (new_studied, review_studied)


def count_anki_review_remaining_today(deck_name: str | None = None, collection_path: Path | None = None) -> int | None:
    """Count reviews remaining today, matching Anki's deck-overview badge.

    Mirrors Anki's queue-builder logic:
      1. Apply sibling burying (COUNT(DISTINCT nid)) when bury_reviews=true
      2. Apply RemainingLimits cap (reviews_per_day - studied_today)

    Returns None (not 0) when the Anki collection is missing/unreadable, so
    callers can distinguish "Anki unavailable" from "0 reviews remaining".

    Uses read-only mode with immutable=1, safe while Anki is running.
    """
    path = collection_path if collection_path is not None else settings.anki_collection_path
    if path is None or not path.exists():
        return None

    conn = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        _register_unicase(conn)

        # Check required tables exist
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "col" not in tables or "decks" not in tables or "cards" not in tables:
            return None

        deck = deck_name or settings.anki_deck_name
        if not deck:
            return None

        # Resolve deck ID
        did = _read_did_for_deck(conn, deck)
        if did is None:
            return None

        # Compute today's col-day using Anki's formula: (now - crt) // 86400
        today_col_day = _compute_today_col_day(conn)

        # Check if bury_reviews is enabled (DeckConfig.Config field 28)
        bury_reviews = False
        conf_id = _read_conf_id_for_deck(conn, deck)
        if conf_id is not None:
            config_row = conn.execute("SELECT config FROM deck_config WHERE id = ?", (conf_id,)).fetchone()
            if config_row is not None and config_row[0]:
                config_blob = bytes(config_row[0]) if isinstance(config_row[0], memoryview) else config_row[0]
                bury_raw = _pb_find_varint_field(config_blob, 28)
                if bury_raw is not None:
                    bury_reviews = bool(bury_raw)

        # Count review cards due today (queue=2, due <= today_col_day).
        # Anki gathers intraday-learning before reviews (rslib/.../queue/builder/
        # gathering.rs:14-21) and tracks each card's note in `seen_note_ids`. With
        # `bury_reviews=true`, a queue=2 due card whose note is already in that
        # set is pre-buried via add_due_card and never enters the user's review
        # pool. Mirror it: when bury is on, exclude any review-due card whose
        # note has a sibling currently in queue=1.
        if bury_reviews:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT nid)
                FROM cards
                WHERE did=? AND queue=2 AND due<=?
                  AND nid NOT IN (
                      SELECT nid FROM cards WHERE did=? AND queue=1
                  )
                """,
                (did, today_col_day, did),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM cards WHERE did=? AND queue=2 AND due<=?",
                (did, today_col_day),
            ).fetchone()
        pool_count = row[0] if row is not None else 0

        # Apply RemainingLimits cap
        if conf_id is not None:
            caps = _read_review_caps(conn, conf_id)
            if caps is not None:
                reviews_per_day, ignore_limit = caps
                new_studied, review_studied = _read_today_studied_counts(conn, did, today_col_day)

                review_limit = reviews_per_day - review_studied
                if not ignore_limit:
                    review_limit -= new_studied
                review_limit = max(review_limit, 0)
                return min(pool_count, review_limit)

        # No conf_id or caps: return raw pool count
        return pool_count

    except sqlite3.Error as exc:
        _log.warning("count_anki_review_remaining_today: failed to read collection: %s", exc)
        return None
    finally:
        if conn is not None:
            conn.close()


def _pb_read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a protobuf varint from data at pos. Returns (value, new_pos)."""
    value = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not (b & 0x80):
            break
    return value, pos


def _pb_skip_field(data: bytes, pos: int, wire_type: int) -> int:
    """Skip a protobuf field. Returns new_pos."""
    if wire_type == 0:  # VARINT
        while pos < len(data) and (data[pos] & 0x80):
            pos += 1
        pos += 1
    elif wire_type == 1:  # 64-bit fixed
        pos += 8
    elif wire_type == 2:  # LEN-delimited
        length, pos = _pb_read_varint(data, pos)
        pos += length
    elif wire_type == 5:  # 32-bit fixed
        pos += 4
    return pos


def _pb_find_varint_field(data: bytes, target_field: int) -> int | None:
    """Scan protobuf bytes for the first VARINT with the given field number."""
    if isinstance(data, memoryview):
        data = bytes(data)
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _pb_read_varint(data, pos)
        except Exception:  # pragma: no cover
            return None  # pragma: no cover
        field_num = tag >> 3
        wire_type = tag & 0x7
        if field_num == target_field and wire_type == 0:
            value, _ = _pb_read_varint(data, pos)
            return value
        try:
            pos = _pb_skip_field(data, pos, wire_type)
        except Exception:  # pragma: no cover
            return None  # pragma: no cover
    return None


def _pb_find_len_field(data: bytes, target_field: int) -> bytes | None:
    """Scan protobuf bytes for the first LEN-delimited field with the given field number."""
    if isinstance(data, memoryview):
        data = bytes(data)
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _pb_read_varint(data, pos)
        except Exception:  # pragma: no cover
            return None  # pragma: no cover
        field_num = tag >> 3
        wire_type = tag & 0x7
        if field_num == target_field and wire_type == 2:
            try:
                length, pos = _pb_read_varint(data, pos)
                return data[pos : pos + length]
            except Exception:  # pragma: no cover
                return None  # pragma: no cover
        try:
            pos = _pb_skip_field(data, pos, wire_type)
        except Exception:  # pragma: no cover
            return None  # pragma: no cover
    return None


def _pb_find_packed_float_field(data: bytes, target_field: int) -> list[float] | None:
    """Scan protobuf bytes for a LEN-delimited packed-float field."""
    if isinstance(data, memoryview):
        data = bytes(data)
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _pb_read_varint(data, pos)
        except Exception:  # pragma: no cover
            return None  # pragma: no cover
        field_num = tag >> 3
        wire_type = tag & 0x7
        if field_num == target_field and wire_type == 2:
            try:
                length, pos = _pb_read_varint(data, pos)
                if length % 4 != 0:
                    return None
                return list(struct.unpack(f"<{length // 4}f", data[pos : pos + length]))
            except Exception:  # pragma: no cover
                return None  # pragma: no cover
        try:
            pos = _pb_skip_field(data, pos, wire_type)
        except Exception:  # pragma: no cover
            return None  # pragma: no cover
    return None


def _pb_find_fixed32_float_field(data: bytes, target_field: int) -> float | None:
    """Scan protobuf bytes for a FIXED32 field and return its value as a float."""
    if isinstance(data, memoryview):
        data = bytes(data)
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _pb_read_varint(data, pos)
        except Exception:  # pragma: no cover
            return None  # pragma: no cover
        field_num = tag >> 3
        wire_type = tag & 0x7
        if field_num == target_field and wire_type == 5:
            if pos + 4 > len(data):  # pragma: no cover
                return None  # pragma: no cover
            return struct.unpack("<f", data[pos : pos + 4])[0]
        try:
            pos = _pb_skip_field(data, pos, wire_type)
        except Exception:  # pragma: no cover
            return None  # pragma: no cover
    return None


def _read_fsrs_params_from_deck_config_table(conn: sqlite3.Connection, deck_name: str) -> FSRSParams | None:
    """Return FSRSParams from Anki's deck_config protobuf, or None if absent."""
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except sqlite3.Error:  # pragma: no cover
        return None  # pragma: no cover

    if "deck_config" not in tables or "decks" not in tables:
        return None

    conf_id = _read_conf_id_for_deck(conn, deck_name)
    if conf_id is None:
        return None

    config_row = conn.execute("SELECT config FROM deck_config WHERE id = ?", (conf_id,)).fetchone()
    if config_row is None or not config_row[0]:
        return None

    config_blob = config_row[0]
    config_blob = bytes(config_blob) if isinstance(config_blob, memoryview) else config_blob

    weights = _pb_find_packed_float_field(config_blob, _FSRS5_WEIGHTS_FIELD)
    if weights is None or len(weights) != 19:
        return None

    retention_raw = _pb_find_fixed32_float_field(config_blob, _DESIRED_RETENTION_FIELD)
    retention = float(retention_raw) if retention_raw is not None else 0.9

    try:
        return FSRSParams(weights=tuple(weights), desired_retention=retention)
    except (ValueError, TypeError):  # pragma: no cover
        return None  # pragma: no cover


def _read_new_per_day_from_deck_config_table(conn: sqlite3.Connection, deck_name: str) -> int | None:
    """Read new-per-day from modern Anki's deck_config table (Anki ≥2.1.55).

    Modern Anki stores deck configs as protobuf BLOBs in the deck_config table.
    The deck's conf_id is found via decks.kind (protobuf: field 1 LEN → field 1 VARINT).
    The cap is at field 9 (VARINT) in deck_config.config.
    """
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except sqlite3.Error:  # pragma: no cover
        return None  # pragma: no cover

    if "deck_config" not in tables or "decks" not in tables:
        return None

    conf_id = _read_conf_id_for_deck(conn, deck_name)
    if conf_id is None:
        return None

    config_row = conn.execute("SELECT config FROM deck_config WHERE id = ?", (conf_id,)).fetchone()
    if config_row is None or not config_row[0]:
        return None

    config_blob = config_row[0]
    # DeckConfig.Config: field 9 (VARINT) = new_per_day
    return _pb_find_varint_field(config_blob if isinstance(config_blob, bytes) else bytes(config_blob), 9)


def _read_new_per_day_from_anki(conn: sqlite3.Connection, deck_name: str) -> int | None:
    """Return new-cards-per-day from Anki's deck config, or None if unavailable.

    Tries the legacy JSON format (col.dconf) first, then the modern protobuf
    format (deck_config table, Anki ≥2.1.55).
    """
    row = conn.execute("SELECT decks, dconf FROM col LIMIT 1").fetchone()
    if row is None:
        return None

    dconf_raw = row[1] if row[1] else ""
    if dconf_raw:
        try:
            decks = json.loads(row[0] or "{}")
            dconf_json = json.loads(dconf_raw)
        except (json.JSONDecodeError, TypeError):
            return None

        deck_info = next(
            (v for v in decks.values() if isinstance(v, dict) and v.get("name") == deck_name),
            None,
        )
        if deck_info is not None:
            conf_id = str(deck_info.get("conf", 1))
            deck_conf = dconf_json.get(conf_id)
            if isinstance(deck_conf, dict):
                try:
                    return int(deck_conf["new"]["perDay"])
                except (KeyError, TypeError, ValueError):
                    pass

    # Modern format: deck_config table with protobuf BLOBs
    return _read_new_per_day_from_deck_config_table(conn, deck_name)


def _read_rollover_hour(conn: sqlite3.Connection) -> int:
    """Read Anki's `rollover` setting (hour of day, 0-23) — when "today" begins.

    Modern Anki stores it JSON-encoded in `config` table (key='rollover');
    legacy collections store it in `col.conf` JSON. Defaults to 4 (Anki's
    own default) when neither is present or parseable. See Anki rslib
    `scheduler/timing.rs::sched_timing_today`.
    """
    try:
        row = conn.execute("SELECT val FROM config WHERE KEY = 'rollover'").fetchone()
        if row and row[0] is not None:
            try:
                val = json.loads(bytes(row[0]) if isinstance(row[0], (bytes, memoryview)) else row[0])
                if isinstance(val, int) and 0 <= val <= 23:
                    return val
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
    except sqlite3.OperationalError:
        pass

    try:
        row = conn.execute("SELECT conf FROM col").fetchone()
        if row and row[0]:
            try:
                conf = json.loads(row[0])
                val = conf.get("rollover")
                if isinstance(val, int) and 0 <= val <= 23:
                    return val
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass
    except sqlite3.OperationalError:
        pass

    return 4


def count_anki_introduced_today(
    today: date,
    collection_path: Path | None = None,
    deck_name: str | None = None,
) -> int:
    """Count cards whose *first* revlog entry in Anki is on or after `today`.

    This is Anki's definition of "new today": when a card transitions out of NEW
    (Again/Hard/Good/Easy on a fresh card), Anki writes a revlog row, and the
    deck's newToday counter increments. We mirror it by counting the rows whose
    earliest revlog `id` (millis-since-epoch) is past Anki's day boundary —
    `today` at the configured rollover hour in the user's local timezone, not
    local-midnight (Anki's default rollover is 4, so a grade made at 02:00
    local belongs to *yesterday*, not today).

    When `deck_name` is supplied, the count is scoped to cards in that deck.
    This (a) excludes activity in unrelated decks (Tagalog, Norwegian, …) and
    (b) drops orphan revlog rows whose `cid` no longer points at a `cards`
    row — both of which Anki's own deck-scoped counter ignores. Without this
    filter, a single orphan grade silently shaves one off TT's daily quota
    relative to Anki's. Defaults to the configured `settings.anki_deck_name`.

    TT mirror state is unreliable as a source for this — TT and Anki dual-grade
    the same card and the resulting `cards.reps` (and TT's `reps` mirror) is no
    longer 1 after the second grade, so a reps-based heuristic misses everything
    except single-graded new cards.

    Returns 0 if the collection cannot be opened (Anki not configured / file
    missing). Read-only mode with `immutable=1` is safe while Anki is running.
    """
    path = collection_path if collection_path is not None else settings.anki_collection_path
    if not path.exists():
        return 0
    deck = deck_name if deck_name is not None else getattr(settings, "anki_deck_name", None)
    try:
        with sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True) as conn:
            from app.anki.sqlite_reader import find_deck_id

            rollover = _read_rollover_hour(conn)
            start_ms = int(datetime.combine(today, dt_time(rollover, 0)).timestamp() * 1000)
            deck_id = find_deck_id(conn, deck) if deck else None
            if deck_id is not None:
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM (
                      SELECT r.cid FROM revlog r
                      JOIN cards c ON c.id = r.cid AND c.did = ?
                      GROUP BY r.cid HAVING MIN(r.id) >= ?
                    )
                    """,
                    (deck_id, start_ms),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM (SELECT cid FROM revlog GROUP BY cid HAVING MIN(id) >= ?)",
                    (start_ms,),
                ).fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error as exc:
        _log.warning("count_anki_introduced_today: failed to read revlog: %s", exc)
        return 0


def refresh_daily_new_cap(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    """Read the new-per-day cap from collection.anki2 and write it to the cache."""
    cap = _read_new_per_day_from_anki(conn, deck_name)
    if cap is not None:
        db.set_anki_state_cache("daily_new_cap", str(cap))


def refresh_review_settings(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    """Read newSpread/bury flags from Anki's deck_config protobuf and write to cache."""
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except sqlite3.Error:
        return

    if "deck_config" not in tables or "decks" not in tables:
        return

    conf_id = _read_conf_id_for_deck(conn, deck_name)
    if conf_id is None:
        return

    config_row = conn.execute("SELECT config FROM deck_config WHERE id = ?", (conf_id,)).fetchone()
    if config_row is None or not config_row[0]:
        return

    config_blob = bytes(config_row[0]) if isinstance(config_row[0], memoryview) else config_row[0]

    # new_mix (newSpread): field 30 (VARINT) — 0=mix, 1=after_reviews, 2=before_reviews
    new_spread = _pb_find_varint_field(config_blob, 30)
    if new_spread is not None and new_spread in (0, 1, 2):
        db.set_anki_state_cache("new_spread", str(new_spread))

    # bury_new: field 27 (VARINT/bool) — default false
    bury_new_raw = _pb_find_varint_field(config_blob, 27)
    if bury_new_raw is not None:
        db.set_anki_state_cache("bury_new", str(bool(bury_new_raw)))

    # bury_reviews: field 28 (VARINT/bool) — default false
    bury_reviews_raw = _pb_find_varint_field(config_blob, 28)
    if bury_reviews_raw is not None:
        db.set_anki_state_cache("bury_review", str(bool(bury_reviews_raw)))


def resolve_daily_new_cap(db: SRSDatabase | None = None) -> tuple[int, str]:
    """Return (cap, source) where source is 'cache', 'config', or 'default'.

    Priority:
    1. anki_state_cache (written during sync) — 'cache'
    2. settings.anki_new_per_day_default — 'config'
    3. Hard default 20 — 'default'
    """
    if db is None:
        try:
            from app.srs.database import SRSDatabase

            db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        except Exception:
            db = None

    if db is not None:
        row = db.get_anki_state_cache("daily_new_cap")
        if row is not None:
            value_str, updated_at = row
            try:
                age = datetime.now(UTC) - datetime.fromisoformat(updated_at).replace(tzinfo=UTC)
                if age < timedelta(days=_CACHE_MAX_AGE_DAYS):
                    return (int(value_str), "cache")
            except (ValueError, TypeError, OverflowError):
                pass

    config_default = getattr(settings, "anki_new_per_day_default", 0)
    if config_default:
        return (config_default, "config")

    return (20, "default")


def resolve_new_spread(db: SRSDatabase | None = None) -> tuple[int, str]:
    """Return (new_spread, source) where source is 'cache' or 'default'.

    new_spread: 0=mix, 1=after_reviews, 2=before_reviews
    Default is 0 (mix).
    """
    if db is None:
        try:
            from app.srs.database import SRSDatabase

            db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        except Exception:
            db = None

    if db is not None:
        row = db.get_anki_state_cache("new_spread")
        if row is not None:
            value_str, updated_at = row
            try:
                age = datetime.now(UTC) - datetime.fromisoformat(updated_at).replace(tzinfo=UTC)
                if age < timedelta(days=_CACHE_MAX_AGE_DAYS):
                    val = int(value_str)
                    if val in (0, 1, 2):
                        return (val, "cache")
            except (ValueError, TypeError, OverflowError):
                pass

    return (0, "default")


def resolve_bury_new(db: SRSDatabase | None = None) -> tuple[bool, str]:
    """Return (bury_new, source) where source is 'cache' or 'default'.

    Default is True (bury new siblings).
    """
    if db is None:
        try:
            from app.srs.database import SRSDatabase

            db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        except Exception:
            db = None

    if db is not None:
        row = db.get_anki_state_cache("bury_new")
        if row is not None:
            value_str, updated_at = row
            try:
                age = datetime.now(UTC) - datetime.fromisoformat(updated_at).replace(tzinfo=UTC)
                if age < timedelta(days=_CACHE_MAX_AGE_DAYS):
                    return (value_str == "True", "cache")
            except (ValueError, TypeError, OverflowError):
                pass

    return (True, "default")


def resolve_bury_review(db: SRSDatabase | None = None) -> tuple[bool, str]:
    """Return (bury_review, source) where source is 'cache' or 'default'.

    Default is True (bury review siblings).
    """
    if db is None:
        try:
            from app.srs.database import SRSDatabase

            db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        except Exception:
            db = None

    if db is not None:
        row = db.get_anki_state_cache("bury_review")
        if row is not None:
            value_str, updated_at = row
            try:
                age = datetime.now(UTC) - datetime.fromisoformat(updated_at).replace(tzinfo=UTC)
                if age < timedelta(days=_CACHE_MAX_AGE_DAYS):
                    return (value_str == "True", "cache")
            except (ValueError, TypeError, OverflowError):
                pass

    return (True, "default")


_LEARNING_CUTOFF_KEY = "learning_cutoff"


def resolve_learning_cutoff(db: SRSDatabase, fallback: datetime) -> datetime:
    """Return the snapshot time used to split learning cards into ready vs pending buckets.

    Mirrors Anki's `current_learning_cutoff` (rslib scheduler/queue/mod.rs): a frozen
    timestamp that only advances on grade events (and on sync ingest of remote revlogs).
    Between grades, intraday-learning cards whose due timer expires are NOT preempted
    into the head of the queue — they remain pending until the next grade advances the
    cutoff. Without this snapshot, TT recomputes ready/pending against live `now` on every
    poll and surfaces past-due learning cards mid-screen, diverging from Anki.

    `fallback` is used when no cutoff is cached (no grades or sync ingests yet); typical
    callers pass the current UTC time.
    """
    row = db.get_anki_state_cache(_LEARNING_CUTOFF_KEY)
    if row is None:
        return fallback
    value_str, _ = row
    try:
        return datetime.fromisoformat(value_str)
    except (ValueError, TypeError):
        return fallback


def advance_learning_cutoff(db: SRSDatabase, when: datetime) -> None:
    """Advance the cached learning cutoff to `when`, never moving it backwards.

    Called from the feedback endpoint after each grade and from sync ingest after each
    Anki revlog row is applied locally. Idempotent: a stale `when` is silently ignored.
    """
    row = db.get_anki_state_cache(_LEARNING_CUTOFF_KEY)
    if row is not None and when <= datetime.fromisoformat(row[0]):
        return
    db.set_anki_state_cache(_LEARNING_CUTOFF_KEY, when.isoformat())


def refresh_fsrs_params(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    """Read FSRS params from collection.anki2 and write them to the cache."""
    # Check if decks table exists (modern Anki format)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except sqlite3.Error:  # pragma: no cover
        return  # pragma: no cover

    if "decks" in tables:
        deck_row = conn.execute("SELECT 1 FROM decks WHERE name = ?", (deck_name,)).fetchone()
        if deck_row is None:
            return  # deck missing; expected for misconfigured deck name — no warning

    params = _read_fsrs_params_from_deck_config_table(conn, deck_name)
    if params is None:
        if "decks" in tables:
            _log.warning(
                "refresh_fsrs_params: deck %r has a deck_config blob but no readable FSRS params "
                "(likely FSRS-6 21-weight or unexpected field numbers); TunaTale will use FSRS defaults",
                deck_name,
            )
        return

    db.set_anki_state_cache(
        "fsrs_params",
        json.dumps({"weights": list(params.weights), "desired_retention": params.desired_retention}),
    )


def resolve_fsrs_params(db: SRSDatabase | None = None) -> tuple[FSRSParams, str]:
    """Return (params, source) where source is 'cache' or 'default'."""
    if db is None:
        try:
            from app.srs.database import SRSDatabase as _SRSDatabase

            db = _SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        except Exception:
            db = None

    if db is not None:
        row = db.get_anki_state_cache("fsrs_params")
        if row is not None:
            value_str, updated_at = row
            try:
                age = datetime.now(UTC) - datetime.fromisoformat(updated_at).replace(tzinfo=UTC)
                if age < timedelta(days=_CACHE_MAX_AGE_DAYS):
                    cached = json.loads(value_str)
                    return (
                        FSRSParams(
                            weights=tuple(cached["weights"]),
                            desired_retention=float(cached["desired_retention"]),
                        ),
                        "cache",
                    )
            except (ValueError, TypeError, KeyError):
                pass

    return (DEFAULT_FSRS5_PARAMS, "default")


# Field numbers in DeckConfig.Config protobuf for learning steps
_LEARN_STEPS_FIELD = 1  # packed float: learn steps in minutes
_RELEARN_STEPS_FIELD = 2  # packed float: relearn steps in minutes


def _read_learning_steps_from_deck_config_table(
    conn: sqlite3.Connection, deck_name: str
) -> tuple[list[float], list[float]] | None:
    """Return (learn_steps, relearn_steps) from Anki's deck_config protobuf, or None if absent."""
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except sqlite3.Error:
        return None

    if "deck_config" not in tables or "decks" not in tables:
        return None

    conf_id = _read_conf_id_for_deck(conn, deck_name)
    if conf_id is None:
        return None

    config_row = conn.execute("SELECT config FROM deck_config WHERE id = ?", (conf_id,)).fetchone()
    if config_row is None or not config_row[0]:
        return None

    config_blob = config_row[0]
    config_blob = bytes(config_blob) if isinstance(config_blob, memoryview) else config_blob

    learn_steps = _pb_find_packed_float_field(config_blob, _LEARN_STEPS_FIELD)
    relearn_steps = _pb_find_packed_float_field(config_blob, _RELEARN_STEPS_FIELD)

    if learn_steps is None and relearn_steps is None:
        return None

    return (learn_steps or [], relearn_steps or [])


def refresh_learning_steps(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    """Read learning steps from collection.anki2 and write them to the cache."""
    steps = _read_learning_steps_from_deck_config_table(conn, deck_name)
    if steps is not None:
        learn_steps, relearn_steps = steps
        db.set_anki_state_cache("learn_steps", json.dumps(learn_steps))
        db.set_anki_state_cache("relearn_steps", json.dumps(relearn_steps))


def resolve_learning_steps(db: SRSDatabase | None = None) -> tuple[list[float], str]:
    """Return (steps, source) where source is 'cache' or 'default'.

    Steps are in minutes (float). Default is [1.0, 10.0] (Anki's default).
    """
    if db is None:
        try:
            from app.srs.database import SRSDatabase as _SRSDatabase

            db = _SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        except Exception:
            db = None

    if db is not None:
        row = db.get_anki_state_cache("learn_steps")
        if row is not None:
            value_str, updated_at = row
            try:
                age = datetime.now(UTC) - datetime.fromisoformat(updated_at).replace(tzinfo=UTC)
                if age < timedelta(days=_CACHE_MAX_AGE_DAYS):
                    return (json.loads(value_str), "cache")
            except (ValueError, TypeError, OverflowError):
                pass

    return ([1.0, 10.0], "default")


def resolve_relearning_steps(db: SRSDatabase | None = None) -> tuple[list[float], str]:
    """Return (steps, source) where source is 'cache' or 'default'.

    Steps are in minutes (float). Default is [10.0] (Anki's default).
    """
    if db is None:
        try:
            from app.srs.database import SRSDatabase as _SRSDatabase

            db = _SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        except Exception:
            db = None

    if db is not None:
        row = db.get_anki_state_cache("relearn_steps")
        if row is not None:
            value_str, updated_at = row
            try:
                age = datetime.now(UTC) - datetime.fromisoformat(updated_at).replace(tzinfo=UTC)
                if age < timedelta(days=_CACHE_MAX_AGE_DAYS):
                    return (json.loads(value_str), "cache")
            except (ValueError, TypeError, OverflowError):
                pass

    return ([10.0], "default")
