"""A deck that can never sync keeps its deck config past the 30-day expiry (tunatale-98zf.5).

The 30-day max age on mirrored Anki deck config is right for a deck that syncs:
every sync_pull re-stamps every ANKI_CONFIG row, so a 30-day-old row means sync
is broken and the defaults are the safer guess. A non-owner's deck
(``app.storage.user_dbs``) never syncs, so nothing re-stamps it and the expiry
is guaranteed — the second learner's new-card cap would silently double on
~2026-10-26. For such a deck the seeded config is authoritative.

The owner's synced deck must behave exactly as before, so every oracle here is
run on BOTH kinds of deck.
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.srs.anki_mirror import queue_stats
from app.srs.anki_mirror.cache_registry import REGISTRY
from app.srs.database import SRSDatabase
from app.storage.user_dbs import UserDatabases

_FSRS = json.dumps({"weights": [0.5] * 19, "desired_retention": 0.85})

# key -> (resolver, stored value, the value that resolver returns from the cache).
# Every stored value differs from the resolver's fallback, so "cache" is
# distinguishable from "default" by value as well as by source.
RESOLVERS = {
    "daily_review_cap": (queue_stats.resolve_daily_review_cap, "77", 77),
    "daily_new_cap": (queue_stats.resolve_daily_new_cap, "7", 7),
    "new_spread": (queue_stats.resolve_new_spread, "2", 2),
    "new_card_sort_order": (queue_stats.resolve_new_card_sort_order, "1", 1),
    "new_card_gather_priority": (queue_stats.resolve_new_card_gather_priority, "2", 2),
    "bury_new": (queue_stats.resolve_bury_new, "False", False),
    "bury_review": (queue_stats.resolve_bury_review, "False", False),
    "fsrs_params": (queue_stats.resolve_fsrs_params, _FSRS, None),
    "learn_steps": (queue_stats.resolve_learning_steps, "[3.0]", [3.0]),
    "relearn_steps": (queue_stats.resolve_relearning_steps, "[4.0]", [4.0]),
    "maximum_review_interval": (queue_stats.resolve_maximum_review_interval, "400", 400),
}

MAX_AGED = sorted(k for k, spec in REGISTRY.items() if spec.max_age_days is not None)


def _stamp(days_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _resolve(db: SRSDatabase, key: str):
    resolver, stored, _ = RESOLVERS[key]
    db.set_anki_state_cache_raw(key, stored, _stamp(31))
    return resolver(db)


def test_the_table_covers_exactly_the_keys_the_registry_expires():
    # Enumerated from the registry, not hand-listed: a key that gains a
    # max_age_days without a row here fails, and so does a stale row.
    assert sorted(RESOLVERS) == MAX_AGED


@pytest.mark.parametrize("key", MAX_AGED)
def test_a_sync_less_deck_keeps_a_31_day_old_config_row(key):
    db = SRSDatabase(":memory:", anki_config_expires=False)
    value, source = _resolve(db, key)
    assert source == "cache"
    expected = RESOLVERS[key][2]
    if expected is not None:
        assert value == expected
    else:
        assert value.desired_retention == 0.85


@pytest.mark.parametrize("key", MAX_AGED)
def test_a_synced_deck_still_drops_a_31_day_old_config_row(key):
    value, source = _resolve(SRSDatabase(":memory:"), key)
    assert source != "cache"
    assert value != RESOLVERS[key][2]


@pytest.mark.parametrize("key", MAX_AGED)
def test_a_synced_deck_keeps_a_29_day_old_config_row(key):
    resolver, stored, _ = RESOLVERS[key]
    db = SRSDatabase(":memory:")
    db.set_anki_state_cache_raw(key, stored, _stamp(29))
    assert resolver(db)[1] == "cache"


def test_every_age_check_reads_the_registry():
    """The registry's max_age_days is the only place a max age can come from.

    Before this, eight resolvers enforced a 30-day expiry the registry did not
    declare. Now a resolver can only expire a row through ``_config_row_fresh``,
    which reads the registry — so a bare ``timedelta(days=…)`` anywhere else in
    the module is an undeclared expiry.
    """
    tree = ast.parse(inspect.getsource(queue_stats))
    owners = [
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "timedelta"
        and any(kw.arg == "days" for kw in node.keywords)
    ]
    assert owners == ["_config_row_fresh"]


def test_learner_decks_are_opened_sync_less(tmp_path):
    (tmp_path / "2").mkdir()
    SRSDatabase(str(tmp_path / "2" / "tunatale_no.db"))
    srs_db, _ = UserDatabases(tmp_path, ["no"]).get(2, "no")
    assert srs_db.anki_config_expires is False


def test_decks_expire_config_by_default():
    assert SRSDatabase(":memory:").anki_config_expires is True
