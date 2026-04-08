"""Admin SRS API endpoint tests."""

from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.srs_item import SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


def _unit(text: str, translation: str = "") -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=1, difficulty=1, source="corpus")


@pytest.fixture(autouse=True)
def _clean_app_state():
    db = SRSDatabase(":memory:")
    app.state.srs_db = db
    yield
    db.close()
    for attr in ("srs_db",):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def _db() -> SRSDatabase:
    return app.state.srs_db


class TestListItems:
    """Tests for GET /api/srs/items."""

    async def test_list_items_pagination_and_total(self):
        db = _db()
        for i in range(5):
            db.add_collocation(_unit(f"word{i}", f"trans{i}"), language_code="sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/items", params={"limit": 2, "offset": 0})

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2

    async def test_list_items_search_filter(self):
        db = _db()
        db.add_collocation(_unit("zdravo", "hello"), language_code="sl")
        db.add_collocation(_unit("nasvidenje", "goodbye"), language_code="sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/items", params={"search": "hello"})

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["text"] == "zdravo"

    async def test_list_items_state_filter(self):
        db = _db()
        db.add_collocation(_unit("a", "aa"), language_code="sl")
        db.add_collocation(_unit("b", "bb"), language_code="sl")
        item = db.get_collocation("a")
        item.state = SRSState.REVIEW
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/items", params={"state": "review"})

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["text"] == "a"
        assert data["items"][0]["state"] == "review"


class TestPatchItem:
    """Tests for PATCH /api/srs/items/{id}."""

    async def test_patch_item_updates_text_and_translation(self):
        db = _db()
        db.add_collocation(_unit("zdravo", "hello"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                f"/api/srs/items/{row_id}",
                json={"text": "Zdravo!", "translation": "Hello!"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["text"] == "Zdravo!"
        assert data["translation"] == "Hello!"

    async def test_patch_item_unknown_id_returns_404(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                "/api/srs/items/9999",
                json={"text": "x", "translation": "y"},
            )
        assert response.status_code == 404

    async def test_patch_item_duplicate_text_returns_409(self):
        db = _db()
        db.add_collocation(_unit("a", "aa"), language_code="sl")
        db.add_collocation(_unit("b", "bb"), language_code="sl")
        rows, _ = db.list_collocations(order_by="text")
        id_b = next(r[0] for r in rows if r[1].syntactic_unit.text == "b")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                f"/api/srs/items/{id_b}",
                json={"text": "a", "translation": "dup"},
            )
        assert response.status_code == 409


class TestDeleteItem:
    """Tests for DELETE /api/srs/items/{id}."""

    async def test_delete_item_returns_200_and_removes_row(self):
        db = _db()
        db.add_collocation(_unit("zdravo", "hello"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete(f"/api/srs/items/{row_id}")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        assert db.count_collocations() == 0

    async def test_bulk_delete_removes_all_listed(self):
        db = _db()
        for t in ["a", "b", "c"]:
            db.add_collocation(_unit(t, t), language_code="sl")
        rows, _ = db.list_collocations()
        ids = [r[0] for r in rows[:2]]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/items/bulk-delete", json={"ids": ids})

        assert response.status_code == 200
        assert response.json()["deleted"] == 2
        assert db.count_collocations() == 1


class TestResetSuspend:
    """Tests for reset and suspend endpoints."""

    async def test_reset_item_puts_it_back_in_new_state(self):
        db = _db()
        db.add_collocation(_unit("hvala", "thank you"), language_code="sl")
        item = db.get_collocation("hvala")
        item.reps = 5
        item.state = SRSState.REVIEW
        db.update_collocation(item)
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "new"
        assert data["reps"] == 0

    async def test_suspend_item_then_excluded_from_due_queue(self):
        db = _db()
        db.add_collocation(_unit("lep", "nice"), language_code="sl")
        item = db.get_collocation("lep")
        item.due_date = date.today() - timedelta(days=1)
        item.state = SRSState.REVIEW
        db.update_collocation(item)
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            suspend_resp = await client.post(f"/api/srs/items/{row_id}/suspend", json={"suspended": True})
            assert suspend_resp.status_code == 200
            assert suspend_resp.json()["state"] == "suspended"

            due_resp = await client.get("/api/srs/due")
            assert due_resp.status_code == 200
            due_texts = [i["text"] for i in due_resp.json()["due"]]
            assert "lep" not in due_texts
