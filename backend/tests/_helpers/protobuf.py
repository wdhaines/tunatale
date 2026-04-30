"""Protobuf wire-format builders shared across queue-stats tests."""

from __future__ import annotations


def encode_varint(value: int) -> bytes:
    """Encode an unsigned integer as a protobuf varint."""
    parts = []
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            parts.append(b | 0x80)
        else:
            parts.append(b)
            break
    return bytes(parts)


def pb_varint_field(field_num: int, value: int) -> bytes:
    """Build a protobuf VARINT-typed field (wire type 0)."""
    tag = encode_varint((field_num << 3) | 0)
    return tag + encode_varint(value)


def pb_len_field(field_num: int, payload: bytes) -> bytes:
    """Build a protobuf LEN-typed field (wire type 2)."""
    tag = encode_varint((field_num << 3) | 2)
    return tag + encode_varint(len(payload)) + payload
