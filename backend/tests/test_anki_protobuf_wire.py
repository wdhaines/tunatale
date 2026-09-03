"""Tests for app.srs.anki_mirror.protobuf_wire."""

from __future__ import annotations

from app.srs.anki_mirror.protobuf_wire import (
    compute_anki_day_index,
    decode_varint,
    encode_varint,
    encode_varint_field,
    find_varint_field,
    pb_remove_field,
    pb_replace_or_insert_varint,
    skip_field,
)

_REAL_BLOB = bytes.fromhex("18A12338ABA702")


class TestVarintRoundtrip:
    def test_varint_roundtrip(self):
        for val in (0, 1, 127, 128, 300, 1 << 28):
            encoded = encode_varint(val)
            decoded, pos = decode_varint(encoded, 0)
            assert decoded == val
            assert pos == len(encoded)

    def test_encode_varint_field(self):
        encoded = encode_varint_field(3, 4513)
        tag = encoded[0]
        assert tag == (3 << 3) | 0


class TestFindVarint:
    def test_find_varint_in_present_field(self):
        assert find_varint_field(_REAL_BLOB, 3) == 4513

    def test_find_varint_in_absent_field(self):
        assert find_varint_field(_REAL_BLOB, 4) is None

    def test_find_varint_in_absent_field_nonzero_blob(self):
        encoded = encode_varint_field(3, 100) + encode_varint_field(7, 200)
        assert find_varint_field(encoded, 4) is None


class TestReplaceOrInsertVarint:
    def test_replace_existing_field(self):
        blob = encode_varint_field(3, 100) + encode_varint_field(4, 5) + encode_varint_field(7, 200)
        result = pb_replace_or_insert_varint(blob, 4, 10)
        assert find_varint_field(result, 4) == 10
        assert find_varint_field(result, 3) == 100
        assert find_varint_field(result, 7) == 200

    def test_insert_new_field_appends(self):
        blob = encode_varint_field(3, 100) + encode_varint_field(7, 200)
        result = pb_replace_or_insert_varint(blob, 4, 1)
        assert find_varint_field(result, 4) == 1
        assert find_varint_field(result, 3) == 100
        assert find_varint_field(result, 7) == 200


class TestRemoveField:
    def test_remove_field(self):
        blob = encode_varint_field(3, 100) + encode_varint_field(4, 5) + encode_varint_field(7, 200)
        result = pb_remove_field(blob, 4)
        assert find_varint_field(result, 4) is None
        assert find_varint_field(result, 7) == 200

    def test_remove_field_idempotent_when_absent(self):
        blob = encode_varint_field(3, 100)
        result = pb_remove_field(blob, 4)
        assert result == blob

    def test_remove_field_empty_blob(self):
        assert pb_remove_field(b"", 4) == b""


class TestSkipField:
    def test_skip_varint(self):
        data = encode_varint(300) + encode_varint(42)
        pos = skip_field(data, 0, 0)
        decoded, _ = decode_varint(data, pos)
        assert decoded == 42


class TestComputeAnkiDayIndex:
    def test_day_index_zero_on_col_crt(self):
        from datetime import datetime

        now = datetime.fromtimestamp(1704067200)  # same as col_crt
        idx = compute_anki_day_index(1704067200, rollover_hour=4, now=now)
        assert idx == 0

    def test_day_index_increments_after_rollover(self):
        from datetime import datetime

        col_crt = 1704067200  # 2024-01-01 00:00:00 UTC
        # Day index at rollover time: col_crt + 1 day
        now = datetime.fromtimestamp(col_crt + 86400 + 4 * 3600)
        idx = compute_anki_day_index(col_crt, rollover_hour=4, now=now)
        assert idx == 1


class TestAnkiTodayColDay:
    """`anki_today_col_day` is Anki's `col.sched.today`, not TT's index domain.

    Parity against the real Anki backend lives in
    ``test_parity_anki_today.py`` (oracle-gated). These pin the properties that
    distinguish the rule from ``compute_anki_day_index`` without needing a
    subprocess, so they also hold the line when the oracle gate is not run.
    """

    def test_rolls_over_at_4am_local_not_local_midnight(self):
        from datetime import UTC, datetime

        from app.srs.anki_mirror.protobuf_wire import anki_today_col_day
        from tests._helpers.localtz import local_timezone

        col_crt = -572400  # 1969-12-25 09:00 UTC == 4 AM at UTC-5

        with local_timezone("UTC"):
            # 03:59 local still belongs to the previous study day...
            before = anki_today_col_day(col_crt, datetime(2026, 5, 20, 3, 59, tzinfo=UTC))
            # ...and 04:00 is the turn.
            at_rollover = anki_today_col_day(col_crt, datetime(2026, 5, 20, 4, 0, tzinfo=UTC))
            # Local midnight is NOT a boundary — this is what the old arithmetic
            # got wrong for a collection whose crt is 4 AM in some other zone.
            just_after_midnight = anki_today_col_day(col_crt, datetime(2026, 5, 20, 0, 1, tzinfo=UTC))

        assert at_rollover == before + 1
        assert just_after_midnight == before

    def test_ignores_col_crt_time_of_day(self):
        """The whole point: Anki counts crt's local *date*, not its clock time.

        ``compute_anki_day_index`` is measured alongside to show it lacks this
        property — without that contrast the test would pass just as well
        against the implementation it replaced.
        """
        from datetime import UTC, datetime

        from app.srs.anki_mirror.protobuf_wire import anki_today_col_day, compute_anki_day_index
        from tests._helpers.localtz import local_timezone

        now = datetime(2026, 5, 20, 2, 30, tzinfo=UTC)  # inside the [midnight, 04:00) window
        with local_timezone("UTC"):
            same_date_crts = [int(datetime(2024, 1, 1, h, tzinfo=UTC).timestamp()) for h in (0, 4, 9, 20)]
            new_rule = {anki_today_col_day(crt, now) for crt in same_date_crts}
            old_rule = {compute_anki_day_index(crt, 4, now) for crt in same_date_crts}

        assert len(new_rule) == 1, f"day index varied with col_crt's time-of-day: {new_rule}"
        assert len(old_rule) > 1, "compute_anki_day_index no longer differs — this contrast has gone stale"

    def test_zone_shifts_the_study_day(self):
        """Same instant, two zones, two study days — because the rule is local."""
        from datetime import UTC, datetime

        from app.srs.anki_mirror.protobuf_wire import anki_today_col_day
        from tests._helpers.localtz import local_timezone

        col_crt = int(datetime(2024, 1, 1, 4, tzinfo=UTC).timestamp())
        now = datetime(2026, 5, 20, 2, 30, tzinfo=UTC)

        with local_timezone("UTC"):
            utc_day = anki_today_col_day(col_crt, now)  # 02:30 local → before rollover
        with local_timezone("Etc/GMT-3"):
            plus3_day = anki_today_col_day(col_crt, now)  # 05:30 local → after rollover

        assert plus3_day == utc_day + 1

    def test_defaults_to_now(self):
        from datetime import UTC, datetime

        from app.srs.anki_mirror.protobuf_wire import anki_today_col_day

        col_crt = int(datetime(2024, 1, 1, 4, tzinfo=UTC).timestamp())
        assert anki_today_col_day(col_crt) == anki_today_col_day(col_crt, datetime.now(tz=UTC))
