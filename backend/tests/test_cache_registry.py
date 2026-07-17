"""Tests for cache_registry module and write/read guards."""

from __future__ import annotations

import pytest

from app.srs.anki_mirror.cache_registry import REGISTRY, CacheSource
from app.srs.database import SRSDatabase


class TestRegistryStructure:
    """Test the registry itself."""

    def test_registry_has_19_keys(self):
        """All 19 cache keys are registered."""
        assert len(REGISTRY) == 19

    def test_registry_keys_sorted(self):
        """Registry keys (for visual clarity in diffs)."""
        expected_keys = {
            "bury_new",
            "bury_review",
            "col_crt",
            "daily_new_cap",
            "daily_review_cap",
            "desired_retention",
            "easy_days_percentages",
            "fsrs_params",
            "fsrs_short_term_with_steps_enabled",
            "last_grade_undo",
            "last_unbury_day",
            "learn_steps",
            "learning_cutoff",
            "load_balancer_enabled",
            "maximum_review_interval",
            "new_cards_ignore_review_limit",
            "new_spread",
            "relearn_steps",
            "session_main_queue",
        }
        assert set(REGISTRY.keys()) == expected_keys

    def test_anki_config_keys_count(self):
        """15 keys have source ANKI_CONFIG."""
        anki_config_count = sum(1 for spec in REGISTRY.values() if spec.source == CacheSource.ANKI_CONFIG)
        assert anki_config_count == 15

    def test_tt_session_keys_count(self):
        """2 keys have source TT_SESSION."""
        tt_session_count = sum(1 for spec in REGISTRY.values() if spec.source == CacheSource.TT_SESSION)
        assert tt_session_count == 2

    def test_tt_state_keys_count(self):
        """2 keys have source TT_STATE."""
        tt_state_count = sum(1 for spec in REGISTRY.values() if spec.source == CacheSource.TT_STATE)
        assert tt_state_count == 2

    def test_day_scoped_keys(self):
        """3 keys are day_scoped: last_unbury_day, learning_cutoff, session_main_queue."""
        day_scoped = {k for k, spec in REGISTRY.items() if spec.day_scoped}
        assert day_scoped == {"last_unbury_day", "learning_cutoff", "session_main_queue"}

    def test_max_age_keys(self):
        """4 keys have max_age_days=30."""
        max_age_keys = {k for k, spec in REGISTRY.items() if spec.max_age_days is not None}
        assert max_age_keys == {"fsrs_params", "learn_steps", "relearn_steps", "maximum_review_interval"}

    def test_logic_version_only_on_session_main_queue(self):
        """Only session_main_queue has logic_version (=1)."""
        versioned = {k: spec.logic_version for k, spec in REGISTRY.items() if spec.logic_version is not None}
        assert versioned == {"session_main_queue": 1}

    def test_all_specs_have_matching_names(self):
        """Each spec's name field matches its key."""
        for key, spec in REGISTRY.items():
            assert spec.name == key


class TestCacheGuards:
    """Test the KeyError guards on set/get/delete."""

    def test_set_unregistered_key_raises_keyerror(self, srs_db: SRSDatabase):
        """set_anki_state_cache raises KeyError for unregistered key."""
        with pytest.raises(KeyError, match="unregistered cache key: 'garbage_key'"):
            srs_db.set_anki_state_cache("garbage_key", "value")

    def test_set_registered_key_succeeds(self, srs_db: SRSDatabase):
        """set_anki_state_cache succeeds with registered key."""
        srs_db.set_anki_state_cache("daily_new_cap", "25")
        result = srs_db.get_anki_state_cache("daily_new_cap")
        assert result is not None
        assert result[0] == "25"

    def test_get_unregistered_key_raises_keyerror(self, srs_db: SRSDatabase):
        """get_anki_state_cache raises KeyError for unregistered key."""
        with pytest.raises(KeyError, match="unregistered cache key: 'fake_key'"):
            srs_db.get_anki_state_cache("fake_key")

    def test_get_registered_key_absent_returns_none(self, srs_db: SRSDatabase):
        """get_anki_state_cache returns None for absent registered key."""
        result = srs_db.get_anki_state_cache("daily_new_cap")
        assert result is None

    def test_set_raw_unregistered_key_raises_keyerror(self, srs_db: SRSDatabase):
        """set_anki_state_cache_raw raises KeyError for unregistered key."""
        with pytest.raises(KeyError, match="unregistered cache key: 'bad_key'"):
            srs_db.set_anki_state_cache_raw("bad_key", "value", "2026-07-17 12:00:00")

    def test_set_raw_registered_key_succeeds(self, srs_db: SRSDatabase):
        """set_anki_state_cache_raw succeeds with registered key."""
        srs_db.set_anki_state_cache_raw("daily_new_cap", "30", "2026-07-17 12:00:00")
        result = srs_db.get_anki_state_cache("daily_new_cap")
        assert result is not None
        assert result[0] == "30"

    def test_delete_unregistered_key_raises_keyerror(self, srs_db: SRSDatabase):
        """delete_anki_state_cache raises KeyError for unregistered key."""
        with pytest.raises(KeyError, match="unregistered cache key: 'junk_key'"):
            srs_db.delete_anki_state_cache("junk_key")

    def test_delete_registered_key_succeeds(self, srs_db: SRSDatabase):
        """delete_anki_state_cache succeeds for registered key."""
        srs_db.set_anki_state_cache("daily_new_cap", "25")
        srs_db.delete_anki_state_cache("daily_new_cap")
        result = srs_db.get_anki_state_cache("daily_new_cap")
        assert result is None
