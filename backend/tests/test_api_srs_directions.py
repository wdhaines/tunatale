"""Tests for direction-aware SRS endpoints and drill feedback."""

from __future__ import annotations

import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.language import Language
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore


def _unit(text: str, translation: str = "test", word_count: int = 1) -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=word_count, difficulty=1, source="corpus")


@pytest.fixture(autouse=True)
def _clean_app_state():
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    app.state.srs_db = db
    app.state.content_store = store
    app.state.language = Language.slovene()
    yield
    db.close()
    store.close()
    for attr in ("srs_db", "content_store", "language", "llm"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def _db() -> SRSDatabase:
    return app.state.srs_db


def _advance_recognition_due(db: SRSDatabase, text: str) -> None:
    """Set recognition due_date to yesterday so item is due."""
    item = db.get_collocation(text)
    assert item is not None
    rec = item.directions[Direction.RECOGNITION]
    rec.due_date = datetime.date.today() - datetime.timedelta(days=1)
    rec.state = SRSState.REVIEW
    db.update_direction(item.guid, Direction.RECOGNITION, rec)


def _advance_production_due(db: SRSDatabase, text: str) -> None:
    """Set production due_date to yesterday so item is due."""
    item = db.get_collocation(text)
    assert item is not None
    prod = item.directions[Direction.PRODUCTION]
    prod.due_date = datetime.date.today() - datetime.timedelta(days=1)
    prod.state = SRSState.REVIEW
    db.update_direction(item.guid, Direction.PRODUCTION, prod)


class TestDirectionAwareDueEndpoint:
    """GET /api/srs/due?direction=... partitions correctly."""

    async def test_due_recognition_only(self):
        db = _db()
        db.add_collocation(_unit("beseda"), language_code="sl")
        _advance_recognition_due(db, "beseda")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "recognition"})
        assert r.status_code == 200
        texts = [i["text"] for i in r.json()["due"]]
        assert "beseda" in texts

    async def test_due_production_only(self):
        db = _db()
        db.add_collocation(_unit("beseda"), language_code="sl")
        _advance_production_due(db, "beseda")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "production"})
        assert r.status_code == 200
        texts = [i["text"] for i in r.json()["due"]]
        assert "beseda" in texts

    async def test_due_recognition_does_not_show_production_only(self):
        db = _db()
        db.add_collocation(_unit("beseda"), language_code="sl")
        _advance_production_due(db, "beseda")
        # recognition not advanced — state is 'new', which is excluded

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "recognition"})
        assert r.status_code == 200
        texts = [i["text"] for i in r.json()["due"]]
        assert "beseda" not in texts

    async def test_due_any_returns_union(self):
        db = _db()
        db.add_collocation(_unit("rec_only"), language_code="sl")
        db.add_collocation(_unit("prod_only"), language_code="sl")
        _advance_recognition_due(db, "rec_only")
        _advance_production_due(db, "prod_only")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "any"})
        assert r.status_code == 200
        texts = [i["text"] for i in r.json()["due"]]
        assert "rec_only" in texts
        assert "prod_only" in texts

    async def test_due_any_deduplicates(self):
        """Item due in both directions appears only once."""
        db = _db()
        db.add_collocation(_unit("both"), language_code="sl")
        _advance_recognition_due(db, "both")
        _advance_production_due(db, "both")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "any"})
        assert r.status_code == 200
        texts = [i["text"] for i in r.json()["due"]]
        assert texts.count("both") == 1

    async def test_due_default_is_recognition_compat(self):
        """No direction param → recognition (back-compat)."""
        db = _db()
        db.add_collocation(_unit("rec"), language_code="sl")
        db.add_collocation(_unit("prod"), language_code="sl")
        _advance_recognition_due(db, "rec")
        _advance_production_due(db, "prod")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due")
        assert r.status_code == 200
        texts = [i["text"] for i in r.json()["due"]]
        assert "rec" in texts
        assert "prod" not in texts


class TestDirectionAwareNewEndpoint:
    """GET /api/srs/new?direction=... respects direction."""

    async def test_new_recognition_returns_new_items(self):
        db = _db()
        db.add_collocation(_unit("nieuw"), language_code="sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/new", params={"direction": "recognition"})
        assert r.status_code == 200
        assert any(i["text"] == "nieuw" for i in r.json()["new"])

    async def test_new_default_is_recognition(self):
        db = _db()
        db.add_collocation(_unit("word"), language_code="sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/new")
        assert r.status_code == 200
        assert any(i["text"] == "word" for i in r.json()["new"])


class TestItemDictShape:
    """_item_to_dict emits directions block + flat shims."""

    async def test_due_item_has_directions_block(self):
        db = _db()
        db.add_collocation(_unit("okno"), language_code="sl")
        _advance_recognition_due(db, "okno")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "recognition"})
        assert r.status_code == 200
        items = r.json()["due"]
        assert len(items) == 1
        item = items[0]

        # Flat shims still present
        assert "text" in item
        assert "state" in item
        assert "due_date" in item

        # directions block present
        assert "directions" in item
        dirs = item["directions"]
        assert "recognition" in dirs
        assert "production" in dirs

        rec = dirs["recognition"]
        for key in ("state", "due_date", "stability", "difficulty", "reps", "lapses", "last_review", "anki_card_id"):
            assert key in rec, f"Missing key {key!r} in recognition direction"

    async def test_due_item_has_id(self):
        db = _db()
        db.add_collocation(_unit("miza"), language_code="sl")
        _advance_recognition_due(db, "miza")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "recognition"})
        items = r.json()["due"]
        assert items[0]["id"] > 0

    async def test_due_item_has_image_url_field(self):
        db = _db()
        db.add_collocation(_unit("slika"), language_code="sl")
        _advance_recognition_due(db, "slika")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "recognition"})
        item = r.json()["due"][0]
        assert "image_url" in item  # null when no media row exists
        assert item["image_url"] is None

    async def test_due_item_has_word_count(self):
        db = _db()
        db.add_collocation(_unit("dober dan", word_count=2), language_code="sl")
        _advance_recognition_due(db, "dober dan")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "recognition"})
        item = r.json()["due"][0]
        assert item["word_count"] == 2


class TestDrillEndpoint:
    """POST /api/srs/items/{id}/direction/{direction}/feedback"""

    async def test_recognition_drill_advances_recognition_only(self):
        db = _db()
        db.add_collocation(_unit("voda"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        item_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"rating": "good"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["direction"] == "recognition"
        assert "new_due_date" in body
        assert "new_state" in body

        # production direction should still be NEW
        updated = db.get_collocation_by_id(item_id)
        assert updated is not None
        _, item, _ = updated
        assert item.directions[Direction.PRODUCTION].state == SRSState.NEW

    async def test_production_drill_advances_production_only(self):
        db = _db()
        db.add_collocation(_unit("voda"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        item_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/srs/items/{item_id}/direction/production/feedback",
                json={"rating": "good"},
            )
        assert r.status_code == 200
        assert r.json()["direction"] == "production"

        # recognition direction should still be NEW
        updated = db.get_collocation_by_id(item_id)
        assert updated is not None
        _, item, _ = updated
        assert item.directions[Direction.RECOGNITION].state == SRSState.NEW

    async def test_unknown_direction_returns_422(self):
        db = _db()
        db.add_collocation(_unit("voda"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        item_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/srs/items/{item_id}/direction/baddir/feedback",
                json={"rating": "good"},
            )
        assert r.status_code == 422

    async def test_missing_rating_and_signal_returns_400(self):
        db = _db()
        db.add_collocation(_unit("voda"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        item_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={},
            )
        assert r.status_code == 400

    async def test_signal_also_accepted(self):
        db = _db()
        db.add_collocation(_unit("voda"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        item_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"signal": "no_help"},
            )
        assert r.status_code == 200

    async def test_nonexistent_item_returns_404(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(
                "/api/srs/items/99999/direction/recognition/feedback",
                json={"rating": "good"},
            )
        assert r.status_code == 404


class TestInvalidDirectionParams:
    async def test_due_invalid_direction_422(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/due", params={"direction": "sideways"})
        assert r.status_code == 422

    async def test_new_invalid_direction_422(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/new", params={"direction": "sideways"})
        assert r.status_code == 422


class TestMediaEndpoint:
    async def test_missing_file_returns_404(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/srs/media/no_such_file_xyz.jpg")
        assert r.status_code == 404

    async def test_path_traversal_guard(self):
        from fastapi import HTTPException

        from app.api.srs import serve_media

        class _FakeReq:
            pass

        with pytest.raises(HTTPException) as exc_info:
            await serve_media("..", _FakeReq())  # type: ignore[arg-type]
        assert exc_info.value.status_code == 400

    async def test_existing_file_returns_200(self):
        from pathlib import Path

        media_dir = Path(__file__).parent.parent / "media"
        files = [f for f in media_dir.iterdir() if f.is_file()]
        if not files:
            pytest.skip("no media files present")
        filename = files[0].name
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"/api/srs/media/{filename}")
        assert r.status_code == 200


class TestUpdateDirectionByIdMissing:
    """update_direction_by_id is a no-op for unknown row_id."""

    def test_missing_id_is_no_op(self):
        db = _db()
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=datetime.date.today(),
            state=SRSState.NEW,
        )
        db.update_direction_by_id(99999, Direction.RECOGNITION, ds)  # should not raise
