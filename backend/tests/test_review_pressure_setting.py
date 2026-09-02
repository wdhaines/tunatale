"""Review pressure as a per-CURRICULUM setting (bd tunatale-po5s).

The gap 5ebebe0 shipped and named: `review_pressure` was settable on both HTTP
entry points, but NOT on the path most lessons actually take. Committing planner
days enqueues a pipeline job, and the pipeline passed only `srs_db` — so every
auto-generated story was locked at NATURAL with no way to change it.

WHERE IT LIVES, chosen from the three options on the bead:
`curriculum.metadata`, exactly like `generation_mode`. Not on the CurriculumDay
(a schema change, a migration, AND it would make review pressure a per-day
decision the planner LLM takes — changing the planner prompt and invalidating its
cassettes for a feature it never asked for). Not on the enqueue call (no UI
reaches it). One dial for the plan, which is what a reader of the bead's three
options would want.

⚠️ AN EXPLICIT REQUEST PARAMETER STILL WINS. Otherwise the two HTTP entry points
would ignore a dial the user had set, which is worse than not having one.
"""

from __future__ import annotations

import pytest

from app.models.curriculum import Curriculum, CurriculumDay
from app.models.strategy import ReviewPressure


def _curriculum(**metadata) -> Curriculum:
    return Curriculum(
        id="c1",
        topic="t",
        language_code="sl",
        cefr_level="A2",
        days=[CurriculumDay(day=1, title="D", focus="f", collocations=["x"], learning_objective="lo")],
        metadata=dict(metadata),
    )


class TestResolution:
    def test_unset_is_natural(self):
        """Today's behaviour is the floor: a curriculum that predates this
        setting must keep generating exactly as it did."""
        assert _curriculum().review_pressure() == ReviewPressure.NATURAL

    def test_the_stored_setting_is_used(self):
        assert _curriculum(review_pressure="INSISTENT").review_pressure() == ReviewPressure.INSISTENT

    def test_an_explicit_override_beats_the_stored_setting(self):
        curriculum = _curriculum(review_pressure="INSISTENT")
        assert curriculum.review_pressure("NATURAL") == ReviewPressure.NATURAL

    def test_an_absent_override_falls_through_rather_than_forcing_natural(self):
        """`None` means "not specified", NOT "NATURAL". Conflating them is the
        bug that would silently disable the setting on every call site that
        does not pass one — which is all of them by default."""
        curriculum = _curriculum(review_pressure="BALANCED")
        assert curriculum.review_pressure(None) == ReviewPressure.BALANCED

    def test_an_unrecognised_stored_value_degrades_to_natural(self):
        """Metadata is a free-form JSON blob that older builds and hand edits
        can write to. A bad value must not 500 a generation."""
        assert _curriculum(review_pressure="ENTHUSIASTIC").review_pressure() == ReviewPressure.NATURAL


class TestTheApi:
    @pytest.fixture
    def app_with_curriculum(self):
        from app.main import app
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_curriculum("c1", _curriculum())
        app.state.content_store = store
        return app, store

    async def test_setting_it_round_trips(self, app_with_curriculum):
        from httpx import ASGITransport, AsyncClient

        app, store = app_with_curriculum
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            post = await client.post("/api/curriculum/c1/review-pressure", json={"pressure": "INSISTENT"})
            get = await client.get("/api/curriculum/c1")

        assert post.status_code == 200
        assert post.json()["pressure"] == "INSISTENT"
        assert get.json()["review_pressure"] == "INSISTENT"
        assert store.get_curriculum("c1").metadata["review_pressure"] == "INSISTENT"

    async def test_an_untouched_curriculum_reports_natural(self, app_with_curriculum):
        from httpx import ASGITransport, AsyncClient

        app, _ = app_with_curriculum
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            get = await client.get("/api/curriculum/c1")
        assert get.json()["review_pressure"] == "NATURAL"

    async def test_setting_it_on_a_missing_curriculum_is_404(self, app_with_curriculum):
        from httpx import ASGITransport, AsyncClient

        app, _ = app_with_curriculum
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/curriculum/nope/review-pressure", json={"pressure": "NATURAL"})
        assert resp.status_code == 404
