"""Caller wiring for Layer 82 on the two LIVE paths (queue sort + grade).

Sibling of ``test_queue_stats_language_isolation.py`` (LOCKED ORACLE — do not
edit it, and do not import from it). That file is explicit about its own
scope in its "KNOWN GAP" section: it pins the SEAM (the two resolvers and
``schedule()`` honour an injected db/steps/params correctly) but NOT the
WIRING (that the live callers actually resolve from the request's own db and
pass the result in). It also names a third call site already closed by this
file's other sibling, ``test_revlog_replay_language_isolation.py`` (the
revlog-replay path).

This file closes the wiring gap for the two remaining LIVE call sites:

  - ``app.srs.anki_mirror.queue_engine._compute_live_main`` — the
    retrievability sort must read the REQUEST db's FSRS params
    (``desired_retention``), not a db-less fallback to
    ``settings.database_url``.
  - ``app.api.srs.drill_feedback`` (``POST
    /items/{id}/direction/{dir}/feedback``) — the grade path must read the
    REQUEST db's ``learn_steps``/``relearn_steps``, not a db-less fallback.

Both call sites already resolve from the db they are handed (confirmed by
inspection: ``queue_engine._compute_live_main`` calls
``resolve_fsrs_params(db)``; ``api/srs.py::drill_feedback`` calls
``resolve_learning_steps(db)``/``resolve_relearning_steps(db)``). These tests
pin that wiring behaviourally, through the actual live entry points, so a
future refactor that quietly drops the ``db`` argument at either call site
turns red here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS, compute_retrievability
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

_UNOPENABLE = "sqlite:////nonexistent/unopenable/dir/db.sqlite3"

# Anki fuzzes the interval around the nominal step (seeded per card, so it is
# deterministic per input). Assert a BAND, not the fuzzed value — pinning the
# exact number would couple this to fuzz internals. The band's only job is to
# separate the Norwegian 25-min first step from the Slovene 1-min one, which
# it does with room to spare.
_NO_FIRST_STEP_BAND = (15.0, 40.0)
_SL_FIRST_STEP_BAND = (0.5, 5.0)


def _fsrs_params_json(desired_retention: float) -> str:
    """A cache row shaped like ``refresh_fsrs_params`` writes it.

    NOTE: the resolver reads the ``fsrs_params`` JSON blob's
    ``desired_retention`` key, not a bare top-level key. Seeding the wrong
    shape makes BOTH languages fall back to the 0.9 default, at which point
    the order test below can't fail regardless of which db was actually read.
    """
    return json.dumps(
        {
            "weights": list(DEFAULT_FSRS5_PARAMS.weights),
            "desired_retention": desired_retention,
        }
    )


@pytest.fixture
def two_language_dbs(tmp_path, monkeypatch):
    """A Slovene and a Norwegian cache with DELIBERATELY different settings.

    Mirrors both sibling oracles' fixture (and the repo's real dev ``.env``):
    the singular ``settings.database_url`` points at Slovene — exactly what a
    db-less fallback would read while a Norwegian request is in flight — and
    the plural ``settings.database_urls`` map points at both, which is what
    the request path actually uses.
    """
    sl_path, no_path = tmp_path / "sl.db", tmp_path / "no.db"
    db_sl, db_no = SRSDatabase(str(sl_path)), SRSDatabase(str(no_path))

    db_sl.set_anki_state_cache("learn_steps", "[1.0, 10.0]")
    db_sl.set_anki_state_cache("relearn_steps", "[10.0]")
    db_sl.set_anki_state_cache("fsrs_params", _fsrs_params_json(0.85))

    db_no.set_anki_state_cache("learn_steps", "[25.0, 55.0]")
    db_no.set_anki_state_cache("relearn_steps", "[45.0]")
    db_no.set_anki_state_cache("fsrs_params", _fsrs_params_json(0.70))

    monkeypatch.setattr("app.srs.queue_stats.settings.database_url", f"sqlite:///{sl_path}")
    monkeypatch.setattr(
        "app.srs.queue_stats.settings.database_urls",
        {"sl": f"sqlite:///{sl_path}", "no": f"sqlite:///{no_path}"},
    )
    return db_sl, db_no


# ── Class 1: the queue sort reads the request db's FSRS params ─────────────


def _midnight_utc_days_ago(days: int) -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)


def _seed_review_card(
    db: SRSDatabase,
    text: str,
    *,
    stability: float,
    last_review: datetime | None,
) -> tuple[int, DirectionState]:
    """One collocation with a REVIEW-due recognition direction.

    Production stays NEW (``add_collocation``'s default), which
    ``get_due_items`` excludes — so each collocation contributes exactly one
    entry to the due pool, keeping the R-ascending order assertion clean
    (no same-collocation direction pairs to disambiguate).
    """
    unit = SyntacticUnit(text=text, translation="t", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    assert item is not None
    ds = DirectionState(
        direction=Direction.RECOGNITION,
        due_at=due_at_rollover_utc(anki_today()),
        stability=stability,
        difficulty=5.0,
        reps=1,
        lapses=0,
        state=SRSState.REVIEW,
        last_review=last_review,
    )
    db.update_direction(item.guid, Direction.RECOGNITION, ds)
    cid = db.get_collocation_id_by_guid(item.guid)
    assert cid is not None
    return cid, ds


def _first_seen_order(results) -> list[int]:
    """Collocation ids in first-appearance order (dedupes direction pairs)."""
    seen: list[int] = []
    for row_id, *_rest in results:
        if row_id not in seen:
            seen.append(row_id)
    return seen


class TestQueueSortReadsTheRequestDatabaseFsrsParams:
    """``_compute_live_main`` must sort by the REQUEST db's ``desired_retention``.

    Card K ("known-R": stability=10.0, midnight-UTC ``last_review`` 27 days
    ago) lands on the day-level integer-elapsed branch of
    ``compute_retrievability``, so its R is deterministic (~0.78) and
    independent of which language's params are read. Card N ("null-R": no
    ``last_review``) has no memory state, so ``compute_retrievability``
    returns ``desired_retention`` verbatim — R(N) == dr.

    So the *order* of N vs K flips depending on which db's cache the sort
    actually reads:

      db_sl (dr=0.85): R(N)=0.85 > R(K)=0.78  -> ascending: K before N
      db_no (dr=0.70): R(N)=0.70 < R(K)=0.78  -> ascending: N before K

    An implementation reading the wrong db's params (or falling back to the
    hardcoded 0.9 default) collapses both languages onto the same order.
    """

    def test_order_flips_by_language(self, two_language_dbs):
        from app.srs.anki_mirror.queue_engine import _compute_live_main

        db_sl, db_no = two_language_dbs

        cid_n_sl, _ = _seed_review_card(db_sl, "null-r", stability=1.0, last_review=None)
        cid_k_sl, ds_k_sl = _seed_review_card(db_sl, "known-r", stability=10.0, last_review=_midnight_utc_days_ago(27))
        cid_n_no, _ = _seed_review_card(db_no, "null-r", stability=1.0, last_review=None)
        cid_k_no, ds_k_no = _seed_review_card(db_no, "known-r", stability=10.0, last_review=_midnight_utc_days_ago(27))

        # Setup guard (REQUIRED): if the fixture ever drifts, fail loud here
        # instead of letting the order assertions below pass for the wrong
        # reason. Both K instances are built identically, so one check covers
        # both — but check both anyway since they're independent seed calls.
        r_k_sl = compute_retrievability(ds_k_sl, anki_today())
        r_k_no = compute_retrievability(ds_k_no, anki_today())
        for r_k in (r_k_sl, r_k_no):
            assert 0.70 < r_k < 0.85, (
                f"fixture drift: R(K)={r_k:.4f} is no longer strictly between "
                "the Norwegian (0.70) and Slovene (0.85) desired_retention — "
                "the order assertions below can no longer distinguish the "
                "two languages"
            )

        order_sl = _first_seen_order(_compute_live_main(db_sl))
        assert order_sl == [cid_k_sl, cid_n_sl], (
            f"expected K before N under db_sl (dr=0.85, R(K)~0.78 < R(N)=0.85), got {order_sl}"
        )

        order_no = _first_seen_order(_compute_live_main(db_no))
        assert order_no == [cid_n_no, cid_k_no], (
            f"expected N before K under db_no (dr=0.70, R(N)=0.70 < R(K)~0.78), got {order_no}"
        )

    def test_ignores_the_singular_database_url_setting(self, two_language_dbs, monkeypatch):
        """LOAD-BEARING. ``_compute_live_main`` must never open ``settings.database_url``.

        Points the singular setting at an unopenable path while sorting under
        db_no. If a db-less fallback is ever reintroduced,
        ``resolve_fsrs_params(None)`` silently yields the hardcoded 0.9
        default and the N-before-K order (which needs dr=0.70) breaks.
        Mechanism-agnostic: it pins the ABSENCE of the read, not the shape of
        the fix.
        """
        from app.srs.anki_mirror.queue_engine import _compute_live_main

        _, db_no = two_language_dbs
        monkeypatch.setattr("app.srs.queue_stats.settings.database_url", _UNOPENABLE)

        cid_n, _ = _seed_review_card(db_no, "null-r", stability=1.0, last_review=None)
        cid_k, ds_k = _seed_review_card(db_no, "known-r", stability=10.0, last_review=_midnight_utc_days_ago(27))

        r_k = compute_retrievability(ds_k, anki_today())
        assert 0.70 < r_k < 0.85, f"fixture drift: R(K)={r_k:.4f}"

        order = _first_seen_order(_compute_live_main(db_no))
        assert order == [cid_n, cid_k], (
            f"db_no's N-before-K order broke under an unopenable "
            f"settings.database_url, got {order} — _compute_live_main fell "
            "back to the singular setting"
        )


# ── Class 2: the grade path uses the request db's learning steps ───────────


async def _grade_again_via_route(db: SRSDatabase, text: str) -> tuple[datetime, datetime]:
    """POST an AGAIN grade for a fresh NEW-state card; return (before, due_at).

    Drives ``app.api.srs.drill_feedback`` (``POST
    /api/srs/items/{id}/direction/recognition/feedback``) over ASGI rather
    than calling it directly — it's a FastAPI route handler that reads
    ``request.state.srs_db``, so an HTTP round-trip is the natural way to pin
    that the REQUEST db (not a db-less resolver fallback) is what feeds
    ``resolve_learning_steps``/``resolve_relearning_steps`` at this call site.
    """
    unit = SyntacticUnit(text=text, translation="t", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    assert item is not None
    cid = db.get_collocation_id_by_guid(item.guid)
    assert cid is not None

    app.state.srs_db = db
    before = datetime.now(UTC)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/srs/items/{cid}/direction/recognition/feedback",
            json={"rating": "again"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["new_state"] == "learning", "AGAIN on a NEW card must enter the learning arc"
    return before, datetime.fromisoformat(body["new_due_at"])


class TestGradingUsesTheRequestLanguageSteps:
    """``drill_feedback`` must resolve learn/relearn steps from the request db.

    ``app/api/srs.py::drill_feedback`` is the plain single-grade HTTP entry
    point (``POST /items/{id}/direction/{dir}/feedback``) — of the several
    ``learn_steps=`` call sites in ``api/srs.py``, it's the one drivable
    end-to-end with a seeded db and no lesson/listen scaffolding, so it
    isolates the wiring under test without pulling in unrelated machinery
    (pending-grade staging, budget accounting, load-balancer histograms)
    that the listen/commit-pending sites also touch.
    """

    async def test_norwegian_grade_uses_norwegian_first_step(self, two_language_dbs):
        """A Norwegian grade must schedule on Norwegian's 25-min first step.

        Under the defect this call would fall back to
        ``settings.database_url`` (Slovene, 1-min first step) and land far
        outside this band.
        """
        _, db_no = two_language_dbs

        before, due_at = await _grade_again_via_route(db_no, "norsk kort")

        delta_min = (due_at - before).total_seconds() / 60
        lo, hi = _NO_FIRST_STEP_BAND
        assert lo <= delta_min <= hi, (
            f"expected the fuzzed Norwegian first step ({lo}-{hi} min), got "
            f"{delta_min:.1f} min — the grade route is reading another "
            "language's learn_steps"
        )

    async def test_slovene_grade_uses_slovene_first_step_control(self, two_language_dbs):
        """The control. Without it, a hardcoded-Norwegian implementation
        would pass the test above on its own."""
        db_sl, _ = two_language_dbs

        before, due_at = await _grade_again_via_route(db_sl, "slovenska kartica")

        delta_min = (due_at - before).total_seconds() / 60
        lo, hi = _SL_FIRST_STEP_BAND
        assert lo <= delta_min <= hi, f"expected the fuzzed Slovene first step ({lo}-{hi} min), got {delta_min:.1f} min"
