"""Minimal protobuf wire-format helpers for Anki collection blobs.

Read/write helpers for the protobuf blobs stored in Anki's SQLite columns
(``decks.common``, ``decks.kind``, ``deck_config.config``, etc.).  Covers only
the wire types used by Anki: VARINT (0), LEN-delimited (2), FIXED32 (5),
and FIXED64 (1).
"""

from __future__ import annotations

import time as _time
from datetime import UTC, datetime, time, timedelta

from app.config import ANKI_ROLLOVER_HOUR

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


def compute_anki_day_index(col_crt: int, rollover_hour: int = ANKI_ROLLOVER_HOUR, now: datetime | None = None) -> int:
    """Return the col-day *index-domain* encoding of the instant *now*.

    ⚠️ **This is NOT Anki's ``col.sched.today``** — for "which study day is it
    right now?" use :func:`anki_today_col_day`. The two differ, and the
    difference is not a rounding detail: this function's day boundary sits at
    the UTC instant ``col_crt - rollover_hour`` hours, i.e. it silently assumes
    ``col.crt``'s time-of-day *is* the rollover hour in the reader's current
    zone. Anki instead counts **local calendar dates** and subtracts one until
    today's local rollover has passed, so its boundary is ``rollover_hour``
    local regardless of what time-of-day ``col.crt`` carries.

    For a real collection (``col.crt`` = 4 AM local on creation day) read back
    in the same zone the two agree only outside ``[local midnight, local
    rollover)``: this function rolls over at local midnight, Anki at 4 AM. That
    four-hour daily window is where every "off by one day" symptom in this
    codebase has come from. It widens further whenever ``col.crt``'s offset and
    the reader's current offset disagree — a DST change, a move, or CI running
    ``TZ=UTC`` against a collection created at UTC-5, which is the 04:00–05:00
    UTC band that broke ``anki-gates`` on 2026-09-03.

    What this function *is* good for, and why it survives: it is the exact
    inverse of the day-level ``last_review`` marker that
    ``_compute_last_review`` writes (Layer 45), so
    ``compute_anki_day_index(col_crt, h, _compute_last_review(...)) ==
    due_raw - ivl`` holds by construction. Decoding a stored marker back to the
    col-day Anki recorded is the index domain's job and stays here. Re-anchoring
    it on local dates would shift every stored ``last_review`` by a day and
    trigger a mass sync write-back for zero gain — the same trade
    ``tests/test_colday_helper_consistency.py`` refuses for
    ``review_due_at_for_col_day``.
    """
    now_ts = int(now.timestamp()) if now else int(_time.time())
    return (now_ts - col_crt + rollover_hour * 3600) // 86400


def anki_today_col_day(col_crt: int, now: datetime | None = None) -> int:
    """Return Anki's ``col.sched.today`` — the study-day index of *now*.

    Mirrors ``rslib``'s ``sched_timing_today`` / ``days_elapsed``: count whole
    **local calendar days** from ``col.crt``'s local date to the current Anki
    day, where the current Anki day is still *yesterday's* date until the local
    rollover has passed (that shift is what :func:`~app.srs.anki_mirror.rollover.anki_today`
    already encodes, so it is single-sourced there rather than re-derived).

    Crucially the result does **not** depend on ``col.crt``'s time-of-day — only
    on its local calendar date. That is the property
    :func:`compute_anki_day_index` lacks, and the reason this function exists.

    Measured against the real Anki backend (``col.sched.today`` via the oracle
    harness) at 20 combinations of four zones × five ``col.crt`` times-of-day,
    including a zone sitting before its rollover: this rule matched 20/20 while
    the index-domain arithmetic matched 13/20. See
    ``tests/test_parity_anki_today.py``.

    ``rollover_hour`` is not a parameter: the local-day domain single-sources it
    from ``app.config.ANKI_ROLLOVER_HOUR`` (Anki stores it per-collection; if it
    ever becomes configurable it moves there, once, for both domains).
    """
    from app.srs.anki_mirror.rollover import anki_today

    crt_local_date = datetime.fromtimestamp(col_crt, tz=UTC).astimezone().date()
    return (anki_today(now) - crt_local_date).days


def review_due_at_for_col_day(col_crt: int, col_day: int, rollover_hour: int = ANKI_ROLLOVER_HOUR) -> datetime:
    """Convert an Anki review-state col_day index to a UTC datetime (Layer 49).

    For queue 2/3 cards, ``cards.due`` is the col_day index when the card next
    surfaces. The actual UTC time of that surfacing is ``rollover_hour`` UTC on
    the calendar date matching ``col_crt``'s UTC date + ``col_day`` days.

    Single source of truth for the convention. Both ``compute_due_at`` (sync_pull
    writeback) and ``schedule()`` (TT-side grading) must use it — otherwise the
    derived and stored due_at disagree by ``rollover_hour`` hours plus any day
    offset from grading near the col_day boundary.
    """
    due_date = datetime.fromtimestamp(col_crt, tz=UTC).date() + timedelta(days=col_day)
    return datetime.combine(due_date, time(rollover_hour, 0), tzinfo=UTC)
