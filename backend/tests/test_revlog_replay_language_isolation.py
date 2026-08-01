"""Per-language DB isolation on the REVLOG REPLAY path (Layer 82 follow-up).

Sibling of ``test_queue_stats_language_isolation.py``, which is a locked oracle
for the two *live* db-less call sites. This file pins the third one that brief
(``docs/briefs/done/deferred-ledger-work-2026-07.md``) deliberately left out of
scope:

    ``db_revlog.rebuild_from_revlog`` still calls ``schedule()`` without
    injected steps, so a revlog replay crossing a learning-step transition
    reads the singular setting.

Same defect class as Layer 82. ``settings.database_url`` is the SINGULAR
setting; the request's database comes from the PLURAL
``settings.database_urls[code]`` map, and the repo's dev ``.env`` sets
``DATABASE_URLS`` for both ``sl`` and ``no`` while leaving ``DATABASE_URL`` at
its ``tunatale_sl.db`` default. So replaying a **Norwegian** card's revlog
rebuilt its state on **Slovene** learning steps.

## What this file pins that the older oracle does not

The older file's own "KNOWN GAP" section says it pins the SEAM (``schedule()``
honours injected steps) but NOT the WIRING (that callers resolve from the
request db and pass them). These tests are wiring tests: they call
``rebuild_from_revlog`` on a *specific* database and assert the replay used
THAT database's steps. An implementation that adds parameters without wiring
them cannot pass.

``rebuild_from_revlog`` is a method on the request's own ``SRSDatabase``, so
the fix needs no new plumbing — ``self`` is already the right db.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest

from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase

_SL_LEARN_STEPS = [1.0, 10.0]
_NO_LEARN_STEPS = [25.0, 55.0]
_UNOPENABLE = "sqlite:////nonexistent/unopenable/dir/db.sqlite3"

# Same banding rationale as the sibling oracle: Anki fuzzes around the nominal
# step, so assert a BAND wide enough to separate the Norwegian 25-min first
# step from the Slovene 1-min one, without coupling to fuzz internals.
_NO_FIRST_STEP_BAND = (15.0, 40.0)
_SL_FIRST_STEP_BAND = (0.5, 5.0)


def _seed_card(db: SRSDatabase, text: str) -> int:
    unit = SyntacticUnit(text=text, translation="t", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    coll_id = db.get_collocation_id_by_guid(db.get_collocation(text).guid)
    assert coll_id is not None
    return coll_id


def _seed_again_grade(path, coll_id: int, when: datetime) -> None:
    """One AGAIN grade on a NEW card — the transition that walks learn_steps."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "INSERT INTO tt_revlog (id, collocation_id, direction, button_chosen, "
        "interval, last_interval, factor, taken_millis, review_kind, anki_card_id) "
        "VALUES (?, ?, 'recognition', 1, 0, 0, 0, 1000, 0, NULL)",
        (int(when.timestamp() * 1000), coll_id),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def two_language_dbs(tmp_path, monkeypatch):
    """A Slovene and a Norwegian cache with DELIBERATELY different steps.

    Mirrors the sibling oracle's fixture (and the repo's real dev `.env`): the
    singular setting points at Slovene, which is exactly what a db-less
    fallback would read while a Norwegian request is in flight.
    """
    sl_path, no_path = tmp_path / "sl.db", tmp_path / "no.db"
    db_sl, db_no = SRSDatabase(str(sl_path)), SRSDatabase(str(no_path))

    db_sl.set_anki_state_cache("learn_steps", json.dumps(_SL_LEARN_STEPS))
    db_sl.set_anki_state_cache("relearn_steps", "[10.0]")
    db_no.set_anki_state_cache("learn_steps", json.dumps(_NO_LEARN_STEPS))
    db_no.set_anki_state_cache("relearn_steps", "[45.0]")

    monkeypatch.setattr("app.srs.queue_stats.settings.database_url", f"sqlite:///{sl_path}")
    monkeypatch.setattr(
        "app.srs.queue_stats.settings.database_urls",
        {"sl": f"sqlite:///{sl_path}", "no": f"sqlite:///{no_path}"},
    )
    return (db_sl, sl_path), (db_no, no_path)


def _replay_first_step_minutes(db: SRSDatabase, path) -> float:
    """Replay one AGAIN grade and return the resulting step length in minutes."""
    coll_id = _seed_card(db, "dober dan")
    graded_at = datetime.now(UTC)
    _seed_again_grade(path, coll_id, graded_at)

    state = db.rebuild_from_revlog(coll_id, Direction.RECOGNITION)

    assert state.state == SRSState.LEARNING, "AGAIN on a NEW card must enter the learning arc"
    return (state.due_at - graded_at).total_seconds() / 60


class TestReplayUsesTheRequestLanguageSteps:
    def test_norwegian_replay_uses_norwegian_learning_steps(self, two_language_dbs):
        """A Norwegian replay must rebuild on Norwegian steps, not Slovene's.

        First Norwegian learning step is 25 min vs Slovene's 1 min. Under the
        defect the replay reads `settings.database_url` (Slovene) and lands
        ~1 min out.
        """
        _, (db_no, no_path) = two_language_dbs

        delta_min = _replay_first_step_minutes(db_no, no_path)

        lo, hi = _NO_FIRST_STEP_BAND
        assert lo <= delta_min <= hi, (
            f"expected the fuzzed Norwegian first step ({lo}-{hi} min), got "
            f"{delta_min:.1f} min — rebuild_from_revlog is replaying on another "
            "language's learn_steps"
        )

    def test_slovene_replay_still_uses_slovene_learning_steps(self, two_language_dbs):
        """The control. Both languages must read their OWN cache.

        Without this, an implementation that hardcoded the Norwegian steps —
        or that swapped one wrong singular source for another — would pass the
        test above on its own.
        """
        (db_sl, sl_path), _ = two_language_dbs

        delta_min = _replay_first_step_minutes(db_sl, sl_path)

        lo, hi = _SL_FIRST_STEP_BAND
        assert lo <= delta_min <= hi, f"expected the fuzzed Slovene first step ({lo}-{hi} min), got {delta_min:.1f} min"

    def test_replay_ignores_the_singular_database_url_setting(self, two_language_dbs, monkeypatch):
        """LOAD-BEARING. The replay must never open `settings.database_url`.

        Points the singular setting at an unopenable path. If a db-less
        fallback is ever reintroduced, `resolve_learning_steps(None)` silently
        yields the hardcoded [1.0, 10.0] default and the Norwegian band breaks.
        Mechanism-agnostic: it pins the ABSENCE of the read, not the shape of
        the fix.
        """
        _, (db_no, no_path) = two_language_dbs
        monkeypatch.setattr("app.srs.queue_stats.settings.database_url", _UNOPENABLE)

        delta_min = _replay_first_step_minutes(db_no, no_path)

        lo, hi = _NO_FIRST_STEP_BAND
        assert lo <= delta_min <= hi, (
            f"expected {lo}-{hi} min from the request db's steps, got "
            f"{delta_min:.1f} min — rebuild_from_revlog fell back to settings.database_url"
        )
