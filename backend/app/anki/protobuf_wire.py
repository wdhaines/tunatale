"""Minimal protobuf wire-format helpers for Anki collection blobs.

Read/write helpers for the protobuf blobs stored in Anki's SQLite columns
(``decks.common``, ``decks.kind``, ``deck_config.config``, etc.).  Covers only
the wire types used by Anki: VARINT (0), LEN-delimited (2), FIXED32 (5),
and FIXED64 (1).
"""

from __future__ import annotations

import time as _time
from datetime import datetime

# ── Encode ─────────────────────────────────────────────────────────────────────


def encode_varint(value: int) -> bytes:
    """Encode an unsigned integer as a protobuf varint."""
    out = bytearray()
    while True:
        to_write = value & 0x7F
        value >>= 7
        if value:
            out.append(to_write | 0x80)
        else:
            out.append(to_write)
            break
    return bytes(out)


def encode_tag(field_number: int, wire_type: int) -> bytes:
    return encode_varint((field_number << 3) | wire_type)


def encode_varint_field(field_number: int, value: int) -> bytes:
    return encode_tag(field_number, 0) + encode_varint(value)


# ── Decode ─────────────────────────────────────────────────────────────────────


def decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a protobuf varint from *data* at *pos*.  Returns ``(value, new_pos)``."""
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


def skip_field(data: bytes, pos: int, wire_type: int) -> int:
    """Skip a protobuf field.  Returns ``new_pos``."""
    if wire_type == 0:  # VARINT
        while pos < len(data) and (data[pos] & 0x80):
            pos += 1
        pos += 1
    elif wire_type == 1:  # 64-bit fixed
        pos += 8
    elif wire_type == 2:  # LEN-delimited
        length, pos = decode_varint(data, pos)
        pos += length
    elif wire_type == 5:  # 32-bit fixed
        pos += 4
    return pos


def find_varint_field(data: bytes, target_field: int) -> int | None:
    """Scan protobuf *data* for the first VARINT with the given field number."""
    if isinstance(data, memoryview):
        data = bytes(data)
    pos = 0
    while pos < len(data):
        try:
            tag, pos = decode_varint(data, pos)
        except Exception:  # pragma: no cover
            return None
        field_num = tag >> 3
        wire_type = tag & 0x7
        if field_num == target_field and wire_type == 0:
            value, _ = decode_varint(data, pos)
            return value
        try:
            pos = skip_field(data, pos, wire_type)
        except Exception:  # pragma: no cover
            return None
    return None


def find_len_field(data: bytes, target_field: int) -> bytes | None:
    """Scan protobuf *data* for the first LEN-delimited field with the given field number."""
    if isinstance(data, memoryview):
        data = bytes(data)
    pos = 0
    while pos < len(data):
        try:
            tag, pos = decode_varint(data, pos)
        except Exception:  # pragma: no cover
            return None
        field_num = tag >> 3
        wire_type = tag & 0x7
        if field_num == target_field and wire_type == 2:
            try:
                length, pos = decode_varint(data, pos)
                return data[pos : pos + length]
            except Exception:  # pragma: no cover
                return None
        try:
            pos = skip_field(data, pos, wire_type)
        except Exception:  # pragma: no cover
            return None
    return None


def find_fixed32_field(data: bytes, target_field: int) -> float | None:
    """Scan protobuf *data* for the first fixed32 (IEEE float) with the given field number."""
    import struct

    if isinstance(data, memoryview):
        data = bytes(data)
    pos = 0
    while pos < len(data):
        try:
            tag, pos = decode_varint(data, pos)
        except Exception:  # pragma: no cover
            return None
        field_num = tag >> 3
        wire_type = tag & 0x7
        if field_num == target_field and wire_type == 5:
            if pos + 4 > len(data):  # pragma: no cover
                return None
            return struct.unpack_from("<f", data, pos)[0]
        try:
            pos = skip_field(data, pos, wire_type)
        except Exception:  # pragma: no cover
            return None
    return None


# ── Mutation ───────────────────────────────────────────────────────────────────


def pb_replace_or_insert_varint(blob: bytes, field_number: int, new_value: int) -> bytes:
    """Return *blob* with the named varint field set to *new_value*.

    If the field already exists its value bytes are replaced in-place.
    If absent the new field is appended.
    """
    tag_wire0 = encode_tag(field_number, 0)
    new_value_bytes = encode_varint(new_value)
    pos = 0
    while pos < len(blob):
        try:
            tag, next_pos = decode_varint(blob, pos)
        except Exception:  # pragma: no cover
            break
        field_num = tag >> 3
        wire_type = tag & 0x7
        if field_num == field_number and wire_type == 0:
            _, val_end = decode_varint(blob, next_pos)
            return blob[:pos] + tag_wire0 + new_value_bytes + blob[val_end:]
        pos = skip_field(blob, next_pos, wire_type)
    return blob + tag_wire0 + new_value_bytes


def pb_remove_field(blob: bytes, field_number: int) -> bytes:
    """Return *blob* with all occurrences of *field_number* removed.

    If the field is absent the original blob is returned unchanged.
    """
    if not blob:
        return blob
    pos = 0
    out = bytearray()
    while pos < len(blob):
        try:
            tag, next_pos = decode_varint(blob, pos)
        except Exception:  # pragma: no cover
            out.extend(blob[pos:])
            break
        field_num = tag >> 3
        wire_type = tag & 0x7
        field_end = skip_field(blob, next_pos, wire_type)
        if field_num == field_number:
            pos = field_end
            continue
        out.extend(blob[pos:field_end])
        pos = field_end
    return bytes(out)


def compute_anki_day_index(col_crt: int, rollover_hour: int = 4, now: datetime | None = None) -> int:
    """Return the Anki day index for *now*, matching what Anki writes to ``decks.common`` field 3.

    Anki's day index increments at *rollover_hour* (default 4 AM) each day.
    """
    now_ts = int(now.timestamp()) if now else int(_time.time())
    return (now_ts - col_crt + rollover_hour * 3600) // 86400
