"""High-level builder for Anki collection.anki2 files consumed by the oracle subprocess.

Usage:
    coll = SyntheticCollection("/tmp/test.anki2")
    coll.add_note(id=1001, guid="g1", fields=["front", "back"])
    coll.add_card(id=10010, note_id=1001, ord=0, type=2, queue=2, due=0,
                  stability=10.0, difficulty=4.0)
    coll.save()

Builds a modern-format (schema ver 18) collection with separate ``deck_config``,
``decks``, ``notetypes``, ``fields``, ``templates`` tables suitable for consumption
by both Anki's Python ``Collection`` and TunaTale's SRS pipeline.
"""

from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path
from typing import Any

from tests._helpers.protobuf import encode_varint, pb_len_field, pb_varint_field

FSRS5_WEIGHTS_FIELD = 5
DESIRED_RETENTION_FIELD = 37
SCHEMA_VER = 18
BASIC_NOTETYPE_MID = 1519651961633
COL_CRT = 1704067200  # 2024-01-01 UTC

DEFAULT_WEIGHTS: tuple[float, ...] = (
    0.4072,
    1.1829,
    3.1262,
    15.4722,
    7.2102,
    0.5316,
    1.0651,
    0.0589,
    1.5330,
    0.1544,
    1.0050,
    1.9767,
    0.0967,
    0.2573,
    2.2930,
    0.8958,
    0.1222,
    0.3729,
    0.4435,
)

DEFAULT_DESIRED_RETENTION = 0.9

_SQL_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS col (
    id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,
    dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT,
    decks TEXT, dconf TEXT, tags TEXT);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
    tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
    usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER,
    factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER,
    odue INTEGER, odid INTEGER, flags INTEGER, data TEXT);
CREATE TABLE IF NOT EXISTS revlog (
    id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,
    lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER);
CREATE TABLE IF NOT EXISTS deck_config (
    id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER,
    usn INTEGER, common BLOB, kind BLOB);
CREATE TABLE IF NOT EXISTS notetypes (
    id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
CREATE TABLE IF NOT EXISTS fields (
    ntid INTEGER, ord INTEGER, name TEXT, config BLOB,
    PRIMARY KEY (ntid, ord));
CREATE TABLE IF NOT EXISTS templates (
    ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER,
    usn INTEGER, config BLOB,
    PRIMARY KEY (ntid, ord));
CREATE TABLE IF NOT EXISTS config (
    key TEXT NOT NULL PRIMARY KEY,
    usn INTEGER NOT NULL,
    mtime_secs INTEGER NOT NULL,
    val BLOB NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS graves (
    oid INTEGER, type INTEGER, usn INTEGER);
CREATE TABLE IF NOT EXISTS tags (
    tag TEXT PRIMARY KEY, usn INTEGER, mtime_secs INTEGER);
"""


def _packed_float_field(field_num: int, values: tuple[float, ...] | list[float]) -> bytes:
    """Encode a ``repeated float`` proto field as a packed LEN-delimited block."""
    if not values:
        return b""
    payload = struct.pack(f"<{len(values)}f", *values)
    tag = encode_varint((field_num << 3) | 2)
    return tag + encode_varint(len(payload)) + payload


REVIEW_ORDER_FIELD = 33  # DeckConfig.Config.review_order (enum)
REVIEW_ORDER_RETRIEVABILITY_ASCENDING = 7  # ReviewCardOrder.RETRIEVABILITY_ASCENDING


def _make_deck_config_blob(
    weights: tuple[float, ...] = DEFAULT_WEIGHTS,
    retention: float = DEFAULT_DESIRED_RETENTION,
    new_per_day: int = 20,
    reviews_per_day: int = 200,
    learn_steps: tuple[float, ...] = (),
    relearn_steps: tuple[float, ...] = (),
    review_order: int = REVIEW_ORDER_RETRIEVABILITY_ASCENDING,
) -> bytes:
    """Build a DeckConfig.Config protobuf blob.

    Fields follow the anki/deck_config.proto schema. Only fields relevant to
    queue-order and FSRS scheduling are emitted; callers that need additional
    protobuf fields (bury, new_spread, etc.) can append them after calling this
    helper.

    ``learn_steps`` / ``relearn_steps`` are protobuf ``repeated float`` fields
    — encoded packed (LEN-delimited, little-endian f32 payload). Earlier code
    wrote them as VARINTs which Anki silently ignored, falling back to the
    default [1.0, 10.0] / [10.0] steps.

    ``review_order`` defaults to RETRIEVABILITY_ASCENDING (TT's mode), not
    Anki's app-default DAY — otherwise parity tests against TT's R-asc queue
    assembly would compare different orderings on the two sides.
    """
    blob = pb_varint_field(9, new_per_day)
    blob += pb_varint_field(10, reviews_per_day)
    blob += _packed_float_field(1, learn_steps)
    blob += _packed_float_field(2, relearn_steps)
    blob += _packed_float_field(FSRS5_WEIGHTS_FIELD, weights)
    tag37 = encode_varint((DESIRED_RETENTION_FIELD << 3) | 5)
    blob += tag37 + struct.pack("<f", retention)
    blob += pb_varint_field(REVIEW_ORDER_FIELD, review_order)
    return blob


def _make_deck_kind_blob(conf_id: int) -> bytes:
    """Build a NormalDeckKind protobuf blob: field 1 (LEN) → conf_id at field 1 (VARINT)."""
    inner = pb_varint_field(1, conf_id)
    return pb_len_field(1, inner)


class SyntheticCollection:
    """Build a modern-format Anki collection.anki2 file from a high-level spec.

    Each call to :meth:`save` rewrites the SQLite file from scratch, so it is
    safe to add items incrementally between saves.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.deck_name: str = "Default"
        self.deck_id: int = 1
        self.config_id: int = 1
        self.fsrs_enabled: bool = True
        self.weights: tuple[float, ...] = DEFAULT_WEIGHTS
        self.desired_retention: float = DEFAULT_DESIRED_RETENTION
        self.new_per_day: int = 20
        self.reviews_per_day: int = 200
        self.learn_steps: tuple[float, ...] = ()
        self.relearn_steps: tuple[float, ...] = ()
        self.col_crt: int = COL_CRT
        self.notetypes: list[dict[str, Any]] = []
        self.fields: list[tuple[int, int, str]] = []
        self.templates: list[tuple[int, int, str]] = []
        self.notes: list[dict[str, Any]] = []
        self.cards: list[dict[str, Any]] = []
        self.revlogs: list[dict[str, Any]] = []
        self.col_conf: dict[str, Any] = {}
        self.config_entries: dict[str, bytes] = {}

    def set_deck(self, name: str, deck_id: int) -> None:
        self.deck_name = name
        self.deck_id = deck_id

    def enable_fsrs(
        self,
        weights: tuple[float, ...] = DEFAULT_WEIGHTS,
        retention: float = DEFAULT_DESIRED_RETENTION,
    ) -> None:
        self.fsrs_enabled = True
        self.weights = weights
        self.desired_retention = retention

    def disable_fsrs(self) -> None:
        self.fsrs_enabled = False

    def set_daily_limits(self, new: int = 20, reviews: int = 200) -> None:
        self.new_per_day = new
        self.reviews_per_day = reviews

    def set_learning_steps(
        self,
        learn_steps: tuple[float, ...] | list[float] = (),
        relearn_steps: tuple[float, ...] | list[float] = (),
    ) -> None:
        """Override Anki's default learning/relearning steps (in minutes).

        When either tuple is empty Anki falls back to its defaults:
        ``[1.0, 10.0]`` for ``learn_steps`` and ``[10.0]`` for ``relearn_steps``.
        """
        self.learn_steps = tuple(learn_steps)
        self.relearn_steps = tuple(relearn_steps)

    def add_notetype(self, id: int, name: str, field_names: tuple[str, ...], template_count: int = 1) -> None:
        self.notetypes.append({"id": id, "name": name})
        for ord, fname in enumerate(field_names):
            self.fields.append((id, ord, fname))
        for ord in range(template_count):
            self.templates.append((id, ord, f"Card {ord + 1}"))

    def add_note(
        self,
        id: int,
        guid: str,
        fields: list[str],
        mid: int = BASIC_NOTETYPE_MID,
    ) -> None:
        self.notes.append(
            {
                "id": id,
                "guid": guid,
                "mid": mid,
                "fields": "\x1f".join(fields),
                "sfld": fields[0],
            }
        )

    def add_card(
        self,
        id: int,
        note_id: int,
        ord: int = 0,
        type: int = 0,
        queue: int = 0,
        due: int = 0,
        ivl: int = 0,
        reps: int = 0,
        lapses: int = 0,
        stability: float | None = None,
        difficulty: float | None = None,
        left: int = 0,
        odue: int = 0,
        odid: int = 0,
        factor: int = 0,
        last_review_secs: int | None = None,
        mod: int = 0,
        empty_fsrs_data: bool = False,
        desired_retention: float | None = None,
    ) -> None:
        # cards.data JSON carries the FSRS memory state. Several keys must be
        # present for Anki's queue-order machinery to pick up the FSRS path:
        #   - ``s`` / ``d``: FSRS memory state (stability/difficulty).
        #   - ``lrt``: last review time in seconds. Without it Anki's
        #     ``next_states()`` sees elapsed=0 and uses ``stability_short_term``
        #     instead of ``stability_after_success``.
        #   - ``dr``: per-card desired_retention. Without it
        #     ``extract_fsrs_relative_retrievability`` falls back to SM2
        #     ordering — every FSRS-enabled card ties at the same SM2 score
        #     and the queue order becomes pseudo-random.
        #
        # ``empty_fsrs_data=True`` writes the JSON literal ``'{}'`` — Anki's
        # storage shape for cards that have been through "Forget" or were
        # imported without FSRS state. Distinct from ``data=''`` (raw new card,
        # no JSON at all) for Layer-38-style NULL-R placement.
        if stability is not None and difficulty is not None:
            data_obj: dict[str, Any] = {"s": stability, "d": difficulty}
            if last_review_secs is not None:
                data_obj["lrt"] = last_review_secs
            if desired_retention is not None:
                data_obj["dr"] = desired_retention
            data = json.dumps(data_obj)
        elif empty_fsrs_data:
            data = "{}"
        else:
            data = ""
        self.cards.append(
            {
                "id": id,
                "nid": note_id,
                "did": self.deck_id,
                "ord": ord,
                "mod": mod,
                "usn": 0,
                "type": type,
                "queue": queue,
                "due": due,
                "ivl": ivl,
                "factor": factor,
                "reps": reps,
                "lapses": lapses,
                "left": left,
                "odue": odue,
                "odid": odid,
                "flags": 0,
                "data": data,
            }
        )

    def add_revlog(
        self,
        id: int,
        card_id: int,
        ease: int,
        ivl: int,
        last_ivl: int,
        time: int,
        type: int = 0,
    ) -> None:
        self.revlogs.append(
            {
                "id": id,
                "cid": card_id,
                "usn": 0,
                "ease": ease,
                "ivl": ivl,
                "lastIvl": last_ivl,
                "time": time,
                "type": type,
            }
        )

    def set_col_config(self, key: str, value: Any) -> None:
        self.col_conf[key] = value

    def set_config_value(self, key: str, value: Any) -> None:
        """Add an entry to the ``config`` table.

        *value* is JSON-encoded using Python's ``json.dumps``, matching Anki's
        encoding for ``col.set_config(key, value)``.
        """
        if isinstance(value, bool):
            self.config_entries[key] = b"true" if value else b"false"
        elif isinstance(value, int):
            self.config_entries[key] = str(value).encode("utf-8")
        elif isinstance(value, str):
            self.config_entries[key] = json.dumps(value).encode("utf-8")
        else:
            self.config_entries[key] = json.dumps(value).encode("utf-8")

    def save(self) -> None:
        """Rewrite the collection file from scratch.

        All state accumulated via ``add_note``, ``add_card``, ``set_deck``, etc.
        is serialised to a fresh SQLite file. Safe to call multiple times.
        """
        self._ensure_defaults()
        if self.path.exists():
            self.path.unlink()
        conn = sqlite3.connect(str(self.path))
        try:
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.executescript(_SQL_CREATE_TABLES)
            self._write_col(conn)
            self._write_deck_config(conn)
            self._write_decks(conn)
            self._write_notetypes(conn)
            self._write_fields_templates(conn)
            self._write_notes(conn)
            self._write_cards(conn)
            self._write_revlogs(conn)
            self._write_config(conn)
            conn.commit()
        finally:
            conn.close()

    def _ensure_defaults(self) -> None:
        if not self.notetypes:
            self.add_notetype(BASIC_NOTETYPE_MID, "Basic", ("Front", "Back"), template_count=1)
        if self.fsrs_enabled:
            # `col.conf` JSON column is legacy; modern Anki's ConfigManager reads
            # everything from the `config` table via the Rust backend. Set both
            # `schedVer=2` (required before set_v3_scheduler(True) will succeed)
            # and `fsrs=true` in the modern table. Leave the legacy JSON entries
            # too so older Anki versions still see the right values.
            self.set_col_config("schedVer", 2)
            self.set_col_config("fsrs", True)
            self.set_config_value("schedVer", 2)
            self.set_config_value("fsrs", True)

    def _write_config(self, conn: sqlite3.Connection) -> None:
        for key, val_bytes in self.config_entries.items():
            conn.execute(
                "INSERT INTO config (key, usn, mtime_secs, val) VALUES (?, 0, 0, ?)",
                (key, val_bytes),
            )

    def _write_col(self, conn: sqlite3.Connection) -> None:
        conf_json = json.dumps(self.col_conf)
        conn.execute(
            "INSERT INTO col VALUES (1, ?, 0, 0, ?, 0, 0, 0, ?, '{}', '{}', '{}', '{}')",
            (self.col_crt, SCHEMA_VER, conf_json),
        )

    def _write_deck_config(self, conn: sqlite3.Connection) -> None:
        blob = _make_deck_config_blob(
            weights=self.weights,
            retention=self.desired_retention,
            new_per_day=self.new_per_day,
            reviews_per_day=self.reviews_per_day,
            learn_steps=self.learn_steps,
            relearn_steps=self.relearn_steps,
        )
        conn.execute(
            "INSERT INTO deck_config VALUES (?, ?, 0, -1, ?)",
            (self.config_id, self.deck_name, blob),
        )

    def _write_decks(self, conn: sqlite3.Connection) -> None:
        kind_blob = _make_deck_kind_blob(self.config_id)
        conn.execute(
            "INSERT INTO decks VALUES (?, ?, 0, -1, x'', ?)",
            (self.deck_id, self.deck_name, kind_blob),
        )

    def _write_notetypes(self, conn: sqlite3.Connection) -> None:
        for nt in self.notetypes:
            conn.execute(
                "INSERT INTO notetypes VALUES (?, ?, 0, 0, x'')",
                (nt["id"], nt["name"]),
            )

    def _write_fields_templates(self, conn: sqlite3.Connection) -> None:
        for ntid, ord, name in self.fields:
            conn.execute(
                "INSERT INTO fields VALUES (?, ?, ?, x'')",
                (ntid, ord, name),
            )
        for ntid, ord, name in self.templates:
            conn.execute(
                "INSERT INTO templates VALUES (?, ?, ?, 0, 0, x'')",
                (ntid, ord, name),
            )

    def _write_notes(self, conn: sqlite3.Connection) -> None:
        for note in self.notes:
            conn.execute(
                "INSERT INTO notes VALUES (?, ?, ?, 0, 0, '', ?, ?, 0, 0, '')",
                (note["id"], note["guid"], note["mid"], note["fields"], note["sfld"]),
            )

    def _write_cards(self, conn: sqlite3.Connection) -> None:
        for card in self.cards:
            conn.execute(
                "INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    card["id"],
                    card["nid"],
                    card["did"],
                    card["ord"],
                    card["mod"],
                    card["usn"],
                    card["type"],
                    card["queue"],
                    card["due"],
                    card["ivl"],
                    card["factor"],
                    card["reps"],
                    card["lapses"],
                    card["left"],
                    card["odue"],
                    card["odid"],
                    card["flags"],
                    card["data"],
                ),
            )

    def _write_revlogs(self, conn: sqlite3.Connection) -> None:
        for revlog in self.revlogs:
            conn.execute(
                "INSERT INTO revlog VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    revlog["id"],
                    revlog["cid"],
                    revlog["usn"],
                    revlog["ease"],
                    revlog["ivl"],
                    revlog["lastIvl"],
                    revlog["time"],
                    revlog["type"],
                ),
            )
