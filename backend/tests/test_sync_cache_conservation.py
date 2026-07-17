"""Step 3: sync-refresh conservation test for cache_registry.

After a full sync, every cache key with source=ANKI_CONFIG must have a
freshly-written value. This test runs the real sync against a synthetic
collection (only the driver boundary faked) and asserts the conservation.

Sabotage-drill: comment out ONE refresh_* call in sync.py, watch this test
go red, restore, watch it go green.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config import settings
from app.srs.anki_mirror.cache_registry import REGISTRY, CacheSource
from app.srs.database import SRSDatabase
from tests.anki_oracle.synthetic_collection import SyntheticCollection

# Anki notetype constants (from test_anki_sync_orchestrator.py)
CLOZE_NOTETYPE_MID = 1704067201


# Re-export fixtures for use in this test file (copied from test_anki_sync_orchestrator.py)
# These are session-level and need the real collection setup


@pytest.fixture
def sociable_tt_collection(monkeypatch):
    """Create a real on-disk Anki collection at settings.tt_collection_path.

    Deck is set to ``settings.anki_deck_name`` (``0. Slovene``) with both
    ``Basic`` and ``Cloze`` notetypes. Pins ``anki_model_name`` so model
    discovery doesn't need notes in the collection.
    """
    coll = SyntheticCollection(settings.tt_collection_path)
    coll.set_deck(settings.anki_deck_name, 1)
    coll.add_notetype(CLOZE_NOTETYPE_MID, "Cloze", ("Text", "Back Extra"), template_count=1)
    # Seed every deck-config / config-table value the builder can express, so
    # the conservation test below can assert the corresponding cache keys were
    # written (refresh_* skips absent values). Bools are True because proto3
    # omits false fields; the blob builder always carries weights/retention/
    # daily limits.
    coll.set_daily_limits(new=20, reviews=200)
    coll.set_learning_steps(learn_steps=(1.0, 10.0), relearn_steps=(10.0,))
    coll.set_bury(bury_new=True, bury_reviews=True)
    coll.set_config_value("loadBalancerEnabled", True)
    coll.set_config_value("newCardsIgnoreReviewLimit", True)
    coll.set_config_value("fsrsShortTermWithStepsEnabled", True)
    coll.save()

    monkeypatch.setattr(settings, "anki_model_name", "Cloze")
    return coll


# Response constants (copied from test_anki_sync_orchestrator.py)
_AUTH_RESPONSE = {"auth": "token", "host": "sync-server", "hostKey": "123"}
_NORMAL_SYNC = {"server": 0, "data": b""}


@pytest.fixture
def fake_driver(monkeypatch):
    """Replace ``_run_driver`` with canned responses so auth/sync legs complete.

    Mirrors :func:`_run_driver`'s real signature exactly
    ``(command: dict, timeout: int = 120) -> dict`` and reuses the file's
    existing response constants (``AUTH_RESPONSE``, ``NORMAL_SYNC``) so the
    fake stays honest if those shapes change.

    Yields the op log (a list of commands received) for assertion use.
    """
    import app.plugins.anki_sync.sync_orchestrator as so

    op_log: list[dict] = []

    def _fake(command: dict, timeout: int = 120) -> dict:
        op_log.append(command)
        op = command.get("op", "")
        if op == "login":
            return _AUTH_RESPONSE
        if op == "sync":
            return _NORMAL_SYNC
        if op == "media_pending":
            return {"pending": 0}
        return {"error": f"unknown op: {op}"}

    monkeypatch.setattr(so, "_run_driver", _fake)
    return op_log


class TestSyncCacheConservation:
    """Conservation test: sync refreshes all ANKI_CONFIG cache keys."""

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_sync_refreshes_all_anki_config_keys(self, fake_driver):
        """After peer_sync, all ANKI_CONFIG keys have fresh cache values.

        This harness runs the real peer_sync pipeline against a real on-disk
        synthetic collection, with only the driver subprocess faked. Forgetting
        to add a refresh_* call for a new Anki-sourced cache key makes this
        test fail: the key will either be absent or stale.

        Sabotage-drill to verify the test catches missing refresh calls:
        - Comment out ONE refresh_* call in sync.py (e.g., refresh_col_crt)
        - Run this test; it MUST go red
        - Restore the call; test MUST go green
        """
        from app.models.syntactic_unit import SyntacticUnit
        from app.plugins.anki_sync.sync_orchestrator import peer_sync

        db = SRSDatabase(settings.database_url)

        # Seed one item so the sync has something to do
        unit = SyntacticUnit(
            text="test",
            translation="test",
            word_count=1,
            difficulty=1,
            source="test",
            source_sentence="test",
            card_type="cloze",
        )
        db.add_collocation(unit, language_code="sl")

        # Run the full sync
        peer_sync(dry_run=False)

        # Now verify all ANKI_CONFIG keys have been written
        anki_config_keys = {k for k, spec in REGISTRY.items() if spec.source == CacheSource.ANKI_CONFIG}

        # Track which keys are present and fresh
        fresh_keys = []
        missing_keys = []

        for key in anki_config_keys:
            row = db.get_anki_state_cache(key)
            if row is None:
                missing_keys.append(key)
                continue

            value, updated_at_str = row
            try:
                updated_at = datetime.fromisoformat(updated_at_str)
                # Consider "fresh" if updated in the last 10 seconds
                age = datetime.now(UTC) - updated_at.replace(tzinfo=UTC)
                if age.total_seconds() < 10:
                    fresh_keys.append(key)
                else:
                    missing_keys.append(f"{key} (stale: {age.total_seconds():.1f}s old)")
            except ValueError, TypeError:
                missing_keys.append(f"{key} (invalid updated_at: {updated_at_str!r})")

        # The conservation assertion: every ANKI_CONFIG key the synthetic
        # collection can express MUST be freshly written by the sync. The
        # exclusion list below is SHRINK-ONLY: a new ANKI_CONFIG registry key
        # is asserted by default — either seed it in the fixture above (and
        # add its refresh_* call to sync) or consciously add it here with a
        # reason a reviewer can check. Never widen this list to make a
        # forgotten refresh_* call pass.
        unseedable = {
            # _make_deck_config_blob has no builder parameter for these
            # DeckConfig.Config proto fields yet:
            "easy_days_percentages",  # field 4 (packed f32 x7)
            "new_spread",  # field 30
            "maximum_review_interval",  # field 16
        }
        expected = anki_config_keys - unseedable
        assert expected <= set(fresh_keys), (
            f"Sync failed to refresh ANKI_CONFIG cache keys: "
            f"{sorted(expected - set(fresh_keys))}; details: {missing_keys}. "
            f"A refresh_* call is missing from the sync sequence."
        )
