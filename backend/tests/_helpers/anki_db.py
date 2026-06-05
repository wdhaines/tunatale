"""In-memory Anki collection.anki2 builders for queue-stats tests."""

from __future__ import annotations

import json
import sqlite3
import struct

from tests._helpers.protobuf import encode_varint, pb_len_field, pb_varint_field

FSRS5_WEIGHTS_FIELD = 5
DESIRED_RETENTION_FIELD = 37  # per /tmp/anki-source/proto/anki/deck_config.proto:188
MAX_REVIEW_INTERVAL_FIELD = 16  # uint32

KNOWN_WEIGHTS: tuple[float, ...] = (
    0.1279,
    1.5785,
    16.497,
    100.0,
    6.9609,
    0.7344,
    1.8881,
    0.0010,
    1.2985,
    0.4768,
    0.8233,
    1.8872,
    0.1347,
    0.2200,
    2.3026,
    0.1944,
    2.4299,
    0.5872,
    0.8019,
)


def make_deck_config_blob(
    new_per_day: int, reviews_per_day: int = 200, max_review_interval: int | None = None
) -> bytes:
    """Build a DeckConfig.Config protobuf blob with new_per_day at field 9 and reviews_per_day at field 10.

    Optionally include max_review_interval at field 16.
    """
    blob = pb_varint_field(9, new_per_day) + pb_varint_field(10, reviews_per_day)
    if max_review_interval is not None:
        blob += pb_varint_field(16, max_review_interval)
    return blob


def make_deck_kind_blob(conf_id: int) -> bytes:
    """Build a NormalDeckKind protobuf blob: field 1 (LEN) containing conf_id at field 1 (VARINT)."""
    inner = pb_varint_field(1, conf_id)
    return pb_len_field(1, inner)


def make_fsrs_deck_config_blob(
    weights: tuple[float, ...] = KNOWN_WEIGHTS,
    retention: float = 0.85,
    new_per_day: int = 20,
) -> bytes:
    """Build a DeckConfig.Config protobuf blob with FSRS weights and desired_retention."""
    # Field 9 (VARINT): new_per_day
    blob = pb_varint_field(9, new_per_day)
    # Field 5 (LEN-delimited, packed f32): FSRS-5 weights
    payload = struct.pack(f"<{len(weights)}f", *weights)
    tag5 = encode_varint((FSRS5_WEIGHTS_FIELD << 3) | 2)
    blob += tag5 + encode_varint(len(payload)) + payload
    # Field 37 (FIXED32): desired_retention as little-endian f32
    tag = encode_varint((DESIRED_RETENTION_FIELD << 3) | 5)
    blob += tag + struct.pack("<f", retention)
    return blob


def make_anki_conn(
    new_per_day: int = 20, deck_name: str = "0. Slovene", reviews_per_day: int = 200
) -> sqlite3.Connection:
    """Build a minimal in-memory collection.anki2 with a deck config."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    deck_id = 12345
    dconf_id = 1

    # Build col.dconf JSON (legacy format)
    dconf_json = json.dumps(
        {
            str(dconf_id): {
                "id": dconf_id,
                "name": "Default",
                "new": {"perDay": new_per_day, "order": 0},
                "rev": {"perDay": reviews_per_day},
            }
        }
    )
    decks_json = json.dumps(
        {
            str(deck_id): {
                "id": deck_id,
                "name": deck_name,
                "conf": dconf_id,
            }
        }
    )

    conn.execute(
        "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
        "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
        "decks TEXT, dconf TEXT, tags TEXT)"
    )
    conn.execute(
        "INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', ?, ?, '{}')",
        (decks_json, dconf_json),
    )
    conn.commit()
    return conn


def make_modern_anki_conn(
    new_per_day: int = 20,
    deck_name: str = "0. Slovene",
    reviews_per_day: int = 200,
    max_review_interval: int | None = None,
) -> sqlite3.Connection:
    """Build a minimal in-memory collection.anki2 with modern deck_config/decks tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    config_id = 1774580286260
    deck_id = 12345

    conn.execute(
        "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
        "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
        "decks TEXT, dconf TEXT, tags TEXT)"
    )
    # col.decks and col.dconf are empty in modern Anki
    conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '', '', '{}')")

    conn.execute(
        "CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
    )
    conn.execute(
        "INSERT INTO deck_config VALUES (?, ?, 0, -1, ?)",
        (config_id, "Slovene", make_deck_config_blob(new_per_day, reviews_per_day, max_review_interval)),
    )

    conn.execute(
        "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
        "usn INTEGER, common BLOB, kind BLOB)"
    )
    conn.execute(
        "INSERT INTO decks VALUES (?, ?, 0, -1, NULL, ?)",
        (deck_id, deck_name, make_deck_kind_blob(config_id)),
    )
    conn.commit()
    return conn


def make_modern_anki_conn_with_fsrs(
    weights: tuple[float, ...] = KNOWN_WEIGHTS,
    retention: float = 0.85,
) -> sqlite3.Connection:
    """Build a modern Anki connection with FSRS params in deck_config."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    config_id = 1774580286260
    deck_id = 12345
    conn.execute(
        "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
        "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, "
        "decks TEXT, dconf TEXT, tags TEXT)"
    )
    conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '', '', '{}')")
    conn.execute(
        "CREATE TABLE deck_config (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
    )
    conn.execute(
        "INSERT INTO deck_config VALUES (?, 'Slovene', 0, -1, ?)",
        (config_id, make_fsrs_deck_config_blob(weights, retention)),
    )
    conn.execute(
        "CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, "
        "usn INTEGER, common BLOB, kind BLOB)"
    )
    conn.execute(
        "INSERT INTO decks VALUES (?, '0. Slovene', 0, -1, NULL, ?)",
        (deck_id, make_deck_kind_blob(config_id)),
    )
    conn.commit()
    return conn
