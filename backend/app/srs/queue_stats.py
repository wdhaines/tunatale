"""Resolve the daily new-card cap and FSRS params from the Anki state cache or config fallbacks."""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from app.anki.protobuf_wire import decode_varint, find_len_field, find_varint_field, skip_field
from app.config import settings
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, FSRSParams

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.srs.database import SRSDatabase

_CACHE_MAX_AGE_DAYS = 30

# Field numbers in DeckConfig.Config protobuf (Anki ≥24.04)
_FSRS5_WEIGHTS_FIELD = 5  # LEN-delimited packed f32; 19 floats for FSRS-5
_DESIRED_RETENTION_FIELD = 40  # FIXED32 float


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
    return find_varint_field(config_blob if isinstance(config_blob, bytes) else bytes(config_blob), 9)


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


def refresh_daily_new_cap(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    """Read the new-per-day cap from collection.anki2 and write it to the cache."""
    cap = _read_new_per_day_from_anki(conn, deck_name)
    if cap is not None:
        db.set_anki_state_cache("daily_new_cap", str(cap))


_REVIEWS_PER_DAY_FIELD = 10  # VARINT uint32 in DeckConfig.Config


def _read_reviews_per_day_from_deck_config_table(conn: sqlite3.Connection, deck_name: str) -> int | None:
    """Read reviews-per-day from modern Anki's deck_config table (Anki ≥2.1.55).

    Mirrors _read_new_per_day_from_deck_config_table but reads field 10
    (reviews_per_day) instead of field 9 (new_per_day).
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
    return find_varint_field(
        config_blob if isinstance(config_blob, bytes) else bytes(config_blob), _REVIEWS_PER_DAY_FIELD
    )


def _read_reviews_per_day_from_anki(conn: sqlite3.Connection, deck_name: str) -> int | None:
    """Return reviews-per-day from Anki's deck config, or None if unavailable.

    Tries the legacy JSON format (col.dconf) first, then the modern protobuf
    format (deck_config table, Anki ≥2.1.55). Mirrors _read_new_per_day_from_anki
    but reads rev.perDay instead of new.perDay.
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
                    return int(deck_conf["rev"]["perDay"])
                except (KeyError, TypeError, ValueError):
                    pass

    return _read_reviews_per_day_from_deck_config_table(conn, deck_name)


# Layer 36: daily review cap (render-only).
def refresh_daily_review_cap(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    """Read the reviews-per-day cap from collection.anki2 and write it to the cache."""
    cap = _read_reviews_per_day_from_anki(conn, deck_name)
    if cap is not None:
        db.set_anki_state_cache("daily_review_cap", str(cap))


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
            except (ValueError, TypeError, OverflowError):
                pass

    config_default = getattr(settings, "anki_reviews_per_day_default", 0)
    if config_default:
        return (config_default, "config")

    return (200, "default")


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
    new_spread = find_varint_field(config_blob, 30)
    if new_spread is not None and new_spread in (0, 1, 2):
        db.set_anki_state_cache("new_spread", str(new_spread))

    # bury_new: field 27 (VARINT/bool) — default false
    bury_new_raw = find_varint_field(config_blob, 27)
    if bury_new_raw is not None:
        db.set_anki_state_cache("bury_new", str(bool(bury_new_raw)))

    # bury_reviews: field 28 (VARINT/bool) — default false
    bury_reviews_raw = find_varint_field(config_blob, 28)
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
    except (json.JSONDecodeError, TypeError):
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
