"""Tests for session_main_queue logic_version field (Step 2)."""

from __future__ import annotations

import json
from datetime import date

from app.srs.anki_mirror.queue_stats import (
    get_session_main_queue,
    set_session_main_queue,
)
from app.srs.database import SRSDatabase


class TestSessionQueueLogicVersion:
    """Test that session_main_queue payload includes and respects logic_version."""

    def test_set_session_main_queue_includes_version(self, srs_db: SRSDatabase):
        """set_session_main_queue stamps the payload with logic_version=1."""
        today = date(2026, 7, 17)
        items = [(1, "recognition"), (2, "production")]

        set_session_main_queue(srs_db, today, items)

        # Read the raw payload from cache
        cached = srs_db.get_anki_state_cache("session_main_queue")
        assert cached is not None
        payload = json.loads(cached[0])

        # Payload should have "v" field
        assert "v" in payload
        assert payload["v"] == 1  # logic_version from registry

    def test_get_session_main_queue_rebuilds_on_version_mismatch(self, srs_db: SRSDatabase):
        """get_session_main_queue returns None when cached v doesn't match registry."""
        today = date(2026, 7, 17)
        items = [(1, "recognition"), (2, "production")]

        # Set queue with current version
        set_session_main_queue(srs_db, today, items)

        # Verify it reads successfully first
        result = get_session_main_queue(srs_db, today)
        assert result == items

        # Now manually set a mismatched version in cache to simulate a version bump
        cached = srs_db.get_anki_state_cache("session_main_queue")
        assert cached is not None
        payload = json.loads(cached[0])
        payload["v"] = 2  # Simulate a version bump in the code
        srs_db.set_anki_state_cache("session_main_queue", json.dumps(payload))

        # Now reading should return None (treat like day mismatch → rebuild)
        result = get_session_main_queue(srs_db, today)
        assert result is None

    def test_get_session_main_queue_respects_matching_version(self, srs_db: SRSDatabase):
        """get_session_main_queue returns items when v matches registry (=1)."""
        today = date(2026, 7, 17)
        items = [(1, "recognition"), (2, "production")]

        set_session_main_queue(srs_db, today, items)
        result = get_session_main_queue(srs_db, today)

        assert result == items

    def test_version_mismatch_treated_like_day_mismatch(self, srs_db: SRSDatabase):
        """Version mismatch has same effect as date mismatch → rebuild needed."""
        today = date(2026, 7, 17)
        yesterday = date(2026, 7, 16)
        items = [(1, "recognition"), (2, "production")]

        # Write queue for yesterday
        set_session_main_queue(srs_db, yesterday, items)

        # Reading with today's date returns None (day mismatch)
        result_day_mismatch = get_session_main_queue(srs_db, today)
        assert result_day_mismatch is None

        # Write queue for today with current version
        set_session_main_queue(srs_db, today, items)

        # Manually create version mismatch
        cached = srs_db.get_anki_state_cache("session_main_queue")
        payload = json.loads(cached[0])
        payload["v"] = 999  # Version mismatch
        srs_db.set_anki_state_cache("session_main_queue", json.dumps(payload))

        # Reading with version mismatch also returns None (version mismatch)
        result_version_mismatch = get_session_main_queue(srs_db, today)
        assert result_version_mismatch is None

        # Both paths (day mismatch, version mismatch) converge on "return None"
