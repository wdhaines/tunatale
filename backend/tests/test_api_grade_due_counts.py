"""The lesson list's per-lesson due count (tunatale-5i1p.3).

Contract: .beads-tasks/briefs/design-grade-page-2026-11.md § The page: "The
lesson list shows each lesson's count of cards due in range." The count is the
number of rows the Grade page would show for the same range, so the oracle is
differential: every case compares ``GET /api/srs/due-counts`` with
``len(grade-plan.cards)`` for the same lesson and range.
"""

from __future__ import annotations

import pytest

import app.api.srs as srs_mod
from app.main import app
from app.models.srs_item import SRSState
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests._helpers.lemmatizer import StubLemmatizer
from tests.test_api_grade_page import PLAN_URL, PROD, _card, _get, _install, _lesson, _review, _set_state

COUNTS_URL = "/api/srs/due-counts"

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _stub_lemmatizer(monkeypatch):
    monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: StubLemmatizer())


async def _counts(params) -> dict:
    resp = await _get(COUNTS_URL, params)
    assert resp.status_code == 200, resp.text
    return resp.json()["counts"]


def _seed(db) -> None:
    """Rows of every class: due, ahead in range, ahead past range, ripe learning, graded today, NEW."""
    for w in ("hus", "bil", "katt", "hund", "bok"):
        _card(db, w)
    _review(db, "hus", due_in=-1)
    _review(db, "bil", due_in=2)
    _review(db, "katt", due_in=5)
    _review(db, "katt", PROD, due_in=0)
    _set_state(db, "hund", PROD, SRSState.LEARNING)
    _review(db, "bok", due_in=0, graded_today=True)


class TestDueCounts:
    @pytest.mark.parametrize("range_days", [0, 3, 7, None])
    async def test_the_count_is_the_grade_pages_row_count_for_the_same_range(self, srs_db, range_days):
        db = _install(srs_db)
        _seed(db)
        params = {} if range_days is None else {"range_days": range_days}

        plan = (await _get(PLAN_URL, params)).json()
        counts = await _counts({"content_id": "lesson-1", **params})

        assert counts == {"lesson-1": len(plan["cards"])}

    async def test_measured_counts_per_range(self, srs_db):
        """Literal oracle beside the differential one, so a plan that drifts with the counter cannot pass."""
        db = _install(srs_db)
        _seed(db)

        assert await _counts({"content_id": "lesson-1", "range_days": 0}) == {"lesson-1": 3}
        assert await _counts({"content_id": "lesson-1", "range_days": 3}) == {"lesson-1": 4}
        assert await _counts({"content_id": "lesson-1"}) == {"lesson-1": 5}

    async def test_several_lessons_in_one_call_and_unknown_ids_omitted(self, srs_db):
        db = _install(srs_db)
        app.state.content_store.save_lesson("lesson-2", "curriculum-1", 2, _lesson("fisk"))
        _card(db, "hus")
        _review(db, "hus", due_in=0)

        counts = await _counts({"content_id": ["lesson-1", "lesson-2", "nope"], "range_days": 0})

        assert counts == {"lesson-1": 1, "lesson-2": 0}

    async def test_no_ids_is_an_empty_map(self, srs_db):
        _install(srs_db)
        assert await _counts({}) == {}

    async def test_a_negative_range_is_refused(self, srs_db):
        _install(srs_db)
        resp = await _get(COUNTS_URL, {"content_id": "lesson-1", "range_days": -1})
        assert resp.status_code == 422


class TestDueCountsReadsTheDeckOnce:
    """tunatale-5i1p.11: the deck-wide indexes are built once per request, not once per lesson."""

    @staticmethod
    def _spy(monkeypatch, names):
        calls: dict[str, list] = {n: [] for n in names}
        for name in names:
            real = getattr(srs_mod, name)

            def wrapper(*args, _real=real, _name=name, **kwargs):
                import threading

                calls[_name].append(threading.current_thread() is threading.main_thread())
                return _real(*args, **kwargs)

            monkeypatch.setattr(srs_mod, name, wrapper)
        return calls

    _BUILDERS = ("build_phrase_index", "_build_variant_index", "_build_inflection_index")

    async def test_each_index_is_built_once_for_three_lessons(self, srs_db, monkeypatch):
        db = _install(srs_db)
        store = app.state.content_store
        store.save_lesson("lesson-2", "curriculum-1", 2, _lesson("fisk"))
        store.save_lesson("lesson-3", "curriculum-1", 3, _lesson("hus"))
        _card(db, "hus")
        _review(db, "hus", due_in=0)
        calls = self._spy(monkeypatch, self._BUILDERS)

        counts = await _counts({"content_id": ["lesson-1", "lesson-2", "lesson-3"], "range_days": 0})

        assert counts == {"lesson-1": 1, "lesson-2": 0, "lesson-3": 1}
        assert {n: len(c) for n, c in calls.items()} == dict.fromkeys(self._BUILDERS, 1)

    async def test_the_indexes_are_built_off_the_event_loop(self, srs_db, monkeypatch):
        _install(srs_db)
        calls = self._spy(monkeypatch, self._BUILDERS)

        await _counts({"content_id": "lesson-1"})

        assert all(not on_main for c in calls.values() for on_main in c), calls

    async def test_the_grade_page_still_builds_its_own_indexes(self, srs_db, monkeypatch):
        """A control: the single-lesson path reads the deck once, as before."""
        _install(srs_db)
        calls = self._spy(monkeypatch, self._BUILDERS)

        assert (await _get(PLAN_URL, {})).status_code == 200

        assert {n: len(c) for n, c in calls.items()} == dict.fromkeys(self._BUILDERS, 1)
