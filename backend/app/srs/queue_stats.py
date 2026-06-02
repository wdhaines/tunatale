"""Resolve the daily new-card cap and FSRS params from the Anki state cache or config fallbacks."""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from app.anki.protobuf_wire import (
    compute_anki_day_index,
    decode_varint,
    find_fixed32_field,
    find_len_field,
    find_varint_field,
    skip_field,
)
from app.config import settings
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, FSRSParams

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.srs.database import SRSDatabase

_CACHE_MAX_AGE_DAYS = 30

# Field numbers in DeckConfig.Config protobuf (Anki ≥24.04)
_LEARN_STEPS_FIELD = 1  # VARINT uint32 → packed float (learn steps in minutes)
_RELEARN_STEPS_FIELD = 2  # VARINT uint32 → packed float (relearn steps in minutes)
_EASY_DAYS_FIELD = 4  # LEN-delimited packed f32; 7 per-weekday load percentages (FSRS load balancer)
_FSRS5_WEIGHTS_FIELD = 5  # LEN-delimited packed f32; 19 floats for FSRS-5
_FSRS6_WEIGHTS_FIELD = 6  # LEN-delimited packed f32; 21 floats for FSRS-6
_NEW_PER_DAY_FIELD = 9  # VARINT uint32
_REVIEWS_PER_DAY_FIELD = 10  # VARINT uint32
_BURY_NEW_FIELD = 27  # VARINT bool
_BURY_REVIEW_FIELD = 28  # VARINT bool
_NEW_SPREAD_FIELD = 30  # VARINT uint32 (0=mix, 1=after_reviews, 2=before_reviews)
_DESIRED_RETENTION_FIELD = 37  # FIXED32 float — per /tmp/anki-source/proto/anki/deck_config.proto:188
# Field 40 is historical_retention; pre-2026-05-16 code read 40 thinking it was desired_retention.

# Protobuf wire types
_WIRE_TYPE_VARINT = 0
_WIRE_TYPE_FIXED32 = 5

# Default values (Anki's built-in defaults, used when no cache or config override is available)
_DEFAULT_NEW_PER_DAY = 20
_DEFAULT_REVIEWS_PER_DAY = 200
_DEFAULT_NEW_SPREAD = 0
_DEFAULT_BURY_NEW = True
_DEFAULT_BURY_REVIEW = True
_DEFAULT_LEARN_STEPS: list[float] = [1.0, 10.0]
_DEFAULT_RELEARN_STEPS: list[float] = [10.0]
_DEFAULT_LOAD_BALANCER_ENABLED = False


def _read_conf_id_for_deck(conn: sqlite3.Connection, deck_name: str) -> int | None:
    """Return the conf_id for the given deck name, or None if not found.

    Reads decks.kind blob → NormalKind (field 1 LEN) → conf_id (field 1 VARINT).
    """
    deck_row = conn.execute("SELECT kind FROM decks WHERE name = ?", (deck_name,)).fetchone()
    if deck_row is None or not deck_row[0]:
        return None

    kind_blob = bytes(deck_row[0]) if isinstance(deck_row[0], memoryview) else deck_row[0]
    normal_kind_bytes = find_len_field(kind_blob, 1)
    if normal_kind_bytes is None:
        return None

    return find_varint_field(normal_kind_bytes, 1)


def _read_config_value_from_deck_config_table(
    conn: sqlite3.Connection,
    deck_name: str,
    *,
    proto_field: int,
    wire_type: int,
    legacy_keys: tuple[str, str] | None = None,
) -> int | float | None:
    """Read a deck-config value from Anki's collection.

    Tries the legacy JSON format (col.dconf) first when *legacy_keys* is set,
    then falls back to the modern protobuf format (deck_config table, Anki >=2.1.55).

    For VARINT fields (*wire_type* 0) returns ``int | None``.
    For FIXED32 fields (*wire_type* 5) returns ``float | None``.
    """
    # Legacy JSON path (pre-2.1.55 Anki)
    if legacy_keys is not None:
        row = conn.execute("SELECT decks, dconf FROM col LIMIT 1").fetchone()
        if row is None:
            return None

        dconf_raw = row[1] if row[1] else ""
        if dconf_raw:
            try:
                decks = json.loads(row[0] or "{}")
                dconf_json = json.loads(dconf_raw)
            except json.JSONDecodeError, TypeError:
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
                        val = deck_conf
                        for key in legacy_keys:
                            val = val[key]
                        return int(val)
                    except KeyError, TypeError, ValueError:
                        pass

    # Modern protobuf path (deck_config table)
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

    config_blob = bytes(config_row[0]) if isinstance(config_row[0], memoryview) else config_row[0]

    if wire_type == _WIRE_TYPE_VARINT:
        return find_varint_field(config_blob, proto_field)
    if wire_type == _WIRE_TYPE_FIXED32:
        return find_fixed32_field(config_blob, proto_field)
    return None  # pragma: no cover


def _pb_find_packed_float_field(data: bytes, target_field: int) -> list[float] | None:
    """Scan protobuf bytes for a LEN-delimited packed-float field."""
    if isinstance(data, memoryview):
        data = bytes(data)
    pos = 0
    while pos < len(data):
        try:
            tag, pos = decode_varint(data, pos)
        except Exception:  # pragma: no cover
            return None  # pragma: no cover
        field_num = tag >> 3
        wire_type = tag & 0x7
        if field_num == target_field and wire_type == 2:
            try:
                length, pos = decode_varint(data, pos)
                if length % 4 != 0:
                    return None
                return list(struct.unpack(f"<{length // 4}f", data[pos : pos + length]))
            except Exception:  # pragma: no cover
                return None  # pragma: no cover
        try:
            pos = skip_field(data, pos, wire_type)
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
            tag, pos = decode_varint(data, pos)
        except Exception:  # pragma: no cover
            return None  # pragma: no cover
        field_num = tag >> 3
        wire_type = tag & 0x7
        if field_num == target_field and wire_type == 5:
            if pos + 4 > len(data):  # pragma: no cover
                return None  # pragma: no cover
            return struct.unpack("<f", data[pos : pos + 4])[0]
        try:
            pos = skip_field(data, pos, wire_type)
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

    # Try field 6 first (FSRS-6: 21 floats)
    weights_6 = _pb_find_packed_float_field(config_blob, _FSRS6_WEIGHTS_FIELD)
    if weights_6 is not None and len(weights_6) == 21:
        retention_raw = _pb_find_fixed32_float_field(config_blob, _DESIRED_RETENTION_FIELD)
        retention = float(retention_raw) if retention_raw is not None else 0.9
        try:
            return FSRSParams(weights=tuple(weights_6), desired_retention=retention)
        except ValueError, TypeError:  # pragma: no cover
            pass  # fall through to field 5

    # Fall back to field 5 (FSRS-5: 19 floats)
    weights_5 = _pb_find_packed_float_field(config_blob, _FSRS5_WEIGHTS_FIELD)
    if weights_5 is not None and len(weights_5) == 19:
        retention_raw = _pb_find_fixed32_float_field(config_blob, _DESIRED_RETENTION_FIELD)
        retention = float(retention_raw) if retention_raw is not None else 0.9
        try:
            return FSRSParams(weights=tuple(weights_5), desired_retention=retention)
        except ValueError, TypeError:  # pragma: no cover
            return None  # pragma: no cover

    return None


def _read_new_per_day_from_anki(conn: sqlite3.Connection, deck_name: str) -> int | None:
    """Return new-cards-per-day from Anki's deck config, or None if unavailable.

    Tries the legacy JSON format (col.dconf) first, then the modern protobuf
    format (deck_config table, Anki =2.1.55).
    """
    return _read_config_value_from_deck_config_table(
        conn, deck_name, proto_field=_NEW_PER_DAY_FIELD, wire_type=_WIRE_TYPE_VARINT, legacy_keys=("new", "perDay")
    )


def refresh_daily_new_cap(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    """Read the new-per-day cap from collection.anki2 and write it to the cache."""
    cap = _read_new_per_day_from_anki(conn, deck_name)
    if cap is not None:
        db.set_anki_state_cache("daily_new_cap", str(cap))


def _read_reviews_per_day_from_anki(conn: sqlite3.Connection, deck_name: str) -> int | None:
    """Return reviews-per-day from Anki's deck config, or None if unavailable.

    Tries the legacy JSON format (col.dconf) first, then the modern protobuf
    format (deck_config table, Anki =2.1.55). Mirrors _read_new_per_day_from_anki
    but reads rev.perDay instead of new.perDay.
    """
    return _read_config_value_from_deck_config_table(
        conn, deck_name, proto_field=_REVIEWS_PER_DAY_FIELD, wire_type=_WIRE_TYPE_VARINT, legacy_keys=("rev", "perDay")
    )


# Layer 36: daily review cap (render-only).
def refresh_daily_review_cap(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    """Read the reviews-per-day cap from collection.anki2 and write it to the cache."""
    cap = _read_reviews_per_day_from_anki(conn, deck_name)
    if cap is not None:
        db.set_anki_state_cache("daily_review_cap", str(cap))


def _read_desired_retention_from_deck_config_table(conn: sqlite3.Connection, deck_name: str) -> float | None:
    """Read FSRS desired_retention (field 37 FIXED32) from deck_config.config."""
    return _read_config_value_from_deck_config_table(
        conn, deck_name, proto_field=_DESIRED_RETENTION_FIELD, wire_type=_WIRE_TYPE_FIXED32
    )


def refresh_desired_retention(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    """Read FSRS desired_retention from collection.anki2 and write it to the cache."""
    dr = _read_desired_retention_from_deck_config_table(conn, deck_name)
    if dr is not None:
        db.set_anki_state_cache("desired_retention", repr(dr))


def resolve_daily_review_cap(db: SRSDatabase | None = None) -> tuple[int, str]:
    """Return (cap, source) where source is 'cache', 'config', or 'default'.

    Priority:
    1. anki_state_cache (written during sync) — 'cache'
    2. settings.anki_reviews_per_day_default — 'config'
    3. Hard default 200 — 'default'
    """
    if db is None:
        try:
            from app.srs.database import SRSDatabase

            db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        except Exception:
            db = None

    if db is not None:
        row = db.get_anki_state_cache("daily_review_cap")
        if row is not None:
            value_str, updated_at = row
            try:
                age = datetime.now(UTC) - datetime.fromisoformat(updated_at).replace(tzinfo=UTC)
                if age < timedelta(days=_CACHE_MAX_AGE_DAYS):
                    return (int(value_str), "cache")
            except ValueError, TypeError, OverflowError:
                pass

    config_default = getattr(settings, "anki_reviews_per_day_default", 0)
    if config_default:
        return (config_default, "config")

    return (_DEFAULT_REVIEWS_PER_DAY, "default")


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

    new_spread = find_varint_field(config_blob, _NEW_SPREAD_FIELD)
    if new_spread is not None and new_spread in (0, 1, 2):
        db.set_anki_state_cache("new_spread", str(new_spread))

    bury_new_raw = find_varint_field(config_blob, _BURY_NEW_FIELD)
    if bury_new_raw is not None:
        db.set_anki_state_cache("bury_new", str(bool(bury_new_raw)))

    bury_reviews_raw = find_varint_field(config_blob, _BURY_REVIEW_FIELD)
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
            except ValueError, TypeError, OverflowError:
                pass

    config_default = getattr(settings, "anki_new_per_day_default", 0)
    if config_default:
        return (config_default, "config")

    return (_DEFAULT_NEW_PER_DAY, "default")


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
            except ValueError, TypeError, OverflowError:
                pass

    return (_DEFAULT_NEW_SPREAD, "default")


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
            except ValueError, TypeError, OverflowError:
                pass

    return (_DEFAULT_BURY_NEW, "default")


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
            except ValueError, TypeError, OverflowError:
                pass

    return (_DEFAULT_BURY_REVIEW, "default")


def refresh_col_crt(db: SRSDatabase, conn: sqlite3.Connection) -> None:
    """Read ``col.crt`` from collection.anki2 and write it to ``anki_state_cache``.

    Used by Layer 45's col-day-aware elapsed calculation so request handlers
    don't open ``collection.anki2``.
    """
    try:
        row = conn.execute("SELECT crt FROM col LIMIT 1").fetchone()
        if row:
            db.set_anki_state_cache("col_crt", str(row[0]))
    except sqlite3.Error:  # pragma: no cover - defensive
        pass


def resolve_col_crt(db: SRSDatabase | None = None) -> int | None:
    """Return the cached ``col.crt`` (epoch seconds), or *None* if unavailable.

    Pre-sync (no cache entry) or a corrupt value falls through to *None*;
    callers must accept the UTC-date fallback.
    """
    if db is None:  # pragma: no cover - convenience for ad-hoc scripts
        try:
            from app.srs.database import SRSDatabase as _SRSDatabase

            db = _SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        except Exception:
            return None
    row = db.get_anki_state_cache("col_crt")
    if row is None:
        return None
    try:
        return int(row[0])
    except ValueError, TypeError:  # pragma: no cover - defensive
        return None


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
    except ValueError, TypeError:
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


_SESSION_MAIN_QUEUE_KEY = "session_main_queue"


def get_session_main_queue(db: SRSDatabase, today: date) -> list[tuple[int, str]] | None:
    """Return the cached frozen main-queue order (review+new mix) if it was built today.

    Mirrors Anki's behavior of building `main` once at deck-open and popping from
    the head as cards are graded — the intersperser order does not change between
    grades. Without this freeze, TT recomputes the order on every `/review-queue`
    call and always serves the lowest-R review next, diverging from Anki whenever
    the intersperser would have placed a new card mid-sequence.

    Returns a list of `(collocation_id, direction_str)` keys in build-time order,
    or None if no cache exists for today (caller should build and cache).
    """
    row = db.get_anki_state_cache(_SESSION_MAIN_QUEUE_KEY)
    if row is None:
        return None
    try:
        data = json.loads(row[0])
    except json.JSONDecodeError, TypeError:
        return None
    if data.get("day") != today.isoformat():
        return None
    items = data.get("items", [])
    return [(int(item["cid"]), str(item["dir"])) for item in items]


def set_session_main_queue(db: SRSDatabase, today: date, items: list[tuple[int, str]]) -> None:
    """Cache the frozen main-queue order keyed by today's date."""
    payload = {
        "day": today.isoformat(),
        "items": [{"cid": cid, "dir": d} for cid, d in items],
    }
    db.set_anki_state_cache(_SESSION_MAIN_QUEUE_KEY, json.dumps(payload))


def clear_session_main_queue(db: SRSDatabase) -> None:
    """Invalidate the frozen main-queue cache so the next /review-queue rebuilds.

    Mirrors Anki's `requires_study_queue_rebuild` (queue/mod.rs:211-215) which
    flags a rebuild on sync completion, deck-config change, deck switch, and
    options changes. TT call sites: sync_pull (post-ingest), deck-config writes.
    Idempotent — safe to call when no cache exists.
    """
    db.delete_anki_state_cache(_SESSION_MAIN_QUEUE_KEY)


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
        json.dumps(
            {
                "weights": list(params.weights),
                "desired_retention": params.desired_retention,
                "version": params.version,
            }
        ),
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
                    # Backward compat: old cache rows lack "version"; infer from weight count
                    return (
                        FSRSParams(
                            weights=tuple(cached["weights"]),
                            desired_retention=float(cached["desired_retention"]),
                        ),
                        "cache",
                    )
            except ValueError, TypeError, KeyError:
                pass

    return (DEFAULT_FSRS5_PARAMS, "default")


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
            except ValueError, TypeError, OverflowError:
                pass

    return (_DEFAULT_LEARN_STEPS, "default")


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
            except ValueError, TypeError, OverflowError:
                pass

    return (_DEFAULT_RELEARN_STEPS, "default")


def _read_easy_days_from_deck_config_table(conn: sqlite3.Connection, deck_name: str) -> list[float] | None:
    """Return the 7 easy_days_percentages from Anki's deck_config protobuf, or None.

    Field 4, ``repeated float`` (packed f32) — same encoding as learn/relearn steps.
    """
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except sqlite3.Error:  # pragma: no cover - defensive
        return None  # pragma: no cover

    if "deck_config" not in tables or "decks" not in tables:
        return None

    conf_id = _read_conf_id_for_deck(conn, deck_name)
    if conf_id is None:
        return None

    config_row = conn.execute("SELECT config FROM deck_config WHERE id = ?", (conf_id,)).fetchone()
    if config_row is None or not config_row[0]:  # pragma: no cover - defensive
        return None  # pragma: no cover

    config_blob = config_row[0]
    config_blob = bytes(config_blob) if isinstance(config_blob, memoryview) else config_blob
    return _pb_find_packed_float_field(config_blob, _EASY_DAYS_FIELD)


def refresh_easy_days(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    """Read easy_days_percentages from collection.anki2 and cache it (JSON list)."""
    days = _read_easy_days_from_deck_config_table(conn, deck_name)
    if days is not None:
        db.set_anki_state_cache("easy_days_percentages", json.dumps(days))


def resolve_easy_days(db: SRSDatabase | None = None) -> list[float] | None:
    """Return the cached easy_days_percentages list, or None (→ all-Normal).

    None means the deck has no EasyDay overrides; LoadBalancer(None, ...) then
    treats every weekday as Normal (load_balancer.rs:284-298).
    """
    if db is None:
        try:
            from app.srs.database import SRSDatabase as _SRSDatabase

            db = _SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        except Exception:
            return None

    row = db.get_anki_state_cache("easy_days_percentages")
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except ValueError, TypeError:  # pragma: no cover - defensive
        return None  # pragma: no cover


def _read_load_balancer_enabled_from_config_table(conn: sqlite3.Connection) -> bool | None:
    """Read loadBalancerEnabled from Anki's config table.

    Global collection preference, stored as JSON bool bytes (b'true' / b'false').
    Returns None if the key or table is absent (Anki's default is false).
    """
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except sqlite3.Error:  # pragma: no cover - defensive
        return None  # pragma: no cover

    if "config" not in tables:
        return None

    row = conn.execute("SELECT val FROM config WHERE key = 'loadBalancerEnabled'").fetchone()
    if not row:
        return None
    return row[0] == b"true"


def refresh_load_balancer_enabled(db: SRSDatabase, conn: sqlite3.Connection) -> None:
    """Read loadBalancerEnabled from Anki's config table and cache it."""
    val = _read_load_balancer_enabled_from_config_table(conn)
    if val is not None:
        db.set_anki_state_cache("load_balancer_enabled", "true" if val else "false")


def resolve_load_balancer_enabled(db: SRSDatabase | None = None) -> bool:
    """Return the cached loadBalancerEnabled flag. Defaults to False (Anki's default)."""
    if db is None:
        try:
            from app.srs.database import SRSDatabase as _SRSDatabase

            db = _SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        except Exception:
            return _DEFAULT_LOAD_BALANCER_ENABLED

    row = db.get_anki_state_cache("load_balancer_enabled")
    if row is not None:
        return row[0] == "true"
    return _DEFAULT_LOAD_BALANCER_ENABLED


def warn_if_multi_deck_preset(conn: sqlite3.Connection, deck_name: str) -> None:
    """Log a WARNING if more than one deck shares *deck_name*'s preset (Layer 55).

    The load-balancer histogram is bit-exact ONLY because the Slovene deck is the
    sole deck on its preset — so TT's own ``collocation_directions`` IS the whole
    same-preset due histogram. If a second deck joins the preset, Anki's histogram
    gains cards TT can't see and the live-balanced pick silently drifts. This is a
    cheap sync-time tripwire, not a correctness gate.
    """
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except sqlite3.Error:  # pragma: no cover - defensive
        return  # pragma: no cover
    if "decks" not in tables:
        return

    target_conf = _read_conf_id_for_deck(conn, deck_name)
    if target_conf is None:
        return
    names = [r[0] for r in conn.execute("SELECT name FROM decks").fetchall()]
    sharing = [n for n in names if _read_conf_id_for_deck(conn, n) == target_conf]
    if len(sharing) > 1:
        _log.warning(
            "Load-balancer single-preset invariant broken: %d decks share %r's preset (%s). "
            "The live-grade due histogram will diverge from Anki until they're split.",
            len(sharing),
            deck_name,
            ", ".join(sorted(sharing)),
        )


def build_live_load_balancer(
    db: SRSDatabase,
    *,
    now: datetime | None = None,
    col_crt: int | None = None,
) -> object | None:
    """Construct the per-request FSRS load balancer from TT state (Layer 55).

    Returns ``None`` (→ pure-fuzz, unchanged behaviour) when the deck's
    ``loadBalancerEnabled`` is off or ``col.crt`` hasn't been synced yet.

    Faithful to Anki's model (build once at queue-build, ``add_card`` each answer,
    never remove): the histogram is the sync-frozen ``anki_due`` snapshot — which IS
    Anki's queue-build input, since TT grades never touch ``anki_due`` — plus this
    session's TT-native grades replayed via ``add_card`` (rule: a TT grade moves
    ``due_at`` but not ``anki_due``, so the new position must be added explicitly).
    The caller threads the returned object into ``schedule()`` and ``add_card``s each
    card it grades, so later grades in a multi-card request see earlier ones.
    """
    if not resolve_load_balancer_enabled(db):
        return None
    if now is None:  # pragma: no cover - convenience; callers pass an explicit now
        now = datetime.now(UTC)  # pragma: no cover
    if col_crt is None:
        col_crt = resolve_col_crt(db)
    if col_crt is None:
        return None

    from app.srs.load_balancer import LOAD_BALANCE_DAYS, LoadBalancer

    today = compute_anki_day_index(col_crt, 4, now)
    next_day_at = col_crt + (today + 1) * 86400
    bury_reviews, _ = resolve_bury_review(db)
    easy_days = resolve_easy_days(db)

    lb = LoadBalancer(easy_days, next_day_at, bury_reviews=bury_reviews)
    for akid, nid, due in db.get_load_balancer_histogram(today, LOAD_BALANCE_DAYS):
        lb.add_card(akid or 0, nid or 0, due - today)
    for akid, nid, interval in db.get_load_balancer_session_replay():
        lb.add_card(akid or 0, nid or 0, interval)
    return lb


def _read_fsrs_short_term_from_config_table(conn: sqlite3.Connection) -> bool | None:
    """Read fsrsShortTermWithStepsEnabled from Anki's config table.

    Global user preference, stored as JSON bool bytes (b'true' / b'false').
    Returns None if the key or table is absent (Anki's default is false).
    """
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except sqlite3.Error:  # pragma: no cover
        return None

    if "config" not in tables:
        return None

    row = conn.execute("SELECT val FROM config WHERE key = 'fsrsShortTermWithStepsEnabled'").fetchone()
    if not row:
        return None
    return row[0] == b"true"


def refresh_fsrs_short_term_flag(db: SRSDatabase, conn: sqlite3.Connection) -> None:
    """Read fsrsShortTermWithStepsEnabled from Anki's config table and cache it."""
    val = _read_fsrs_short_term_from_config_table(conn)
    if val is not None:
        db.set_anki_state_cache("fsrs_short_term_with_steps_enabled", "true" if val else "false")
