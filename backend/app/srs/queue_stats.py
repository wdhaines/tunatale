"""Resolve the daily new-card cap from the Anki state cache or config fallbacks."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from app.srs.database import SRSDatabase

_CACHE_MAX_AGE_DAYS = 30


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

    deck_row = conn.execute("SELECT kind FROM decks WHERE name = ?", (deck_name,)).fetchone()
    if deck_row is None or not deck_row[0]:
        return None

    kind_blob = deck_row[0]
    # NormalDeckKind: field 1 (LEN) contains the config sub-message
    normal_kind_bytes = _pb_find_len_field(kind_blob if isinstance(kind_blob, bytes) else bytes(kind_blob), 1)
    if normal_kind_bytes is None:
        return None

    # Within NormalDeckKind, field 1 (VARINT) = conf_id
    conf_id = _pb_find_varint_field(normal_kind_bytes, 1)
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


def refresh_daily_new_cap(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str) -> None:
    """Read the new-per-day cap from collection.anki2 and write it to the cache."""
    cap = _read_new_per_day_from_anki(conn, deck_name)
    if cap is not None:
        db.set_anki_state_cache("daily_new_cap", str(cap))


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
