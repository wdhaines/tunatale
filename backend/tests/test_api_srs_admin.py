"""Admin SRS API endpoint tests."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.models import (
    BackfillTranslationsResponse,
    BulkDeleteResponse,
    StatusResponse,
    TranslateMissingResponse,
    TranslateResponse,
)
from app.languages import get_language
from app.main import app
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore
from tests._helpers.srs_item_shape import DIRECTION_KEYS, DIRECTION_WITHOUT_LEFT, SRS_ITEM_KEYS


def _unit(text: str, translation: str = "") -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=1, difficulty=1, source="corpus")


def _assert_item_keys(data: dict) -> None:
    """Assert an SrsItemResponse payload's key-sets at all nesting levels.

    Used by the bare-item endpoints (create/patch/reset/restore-known/
    set-state/suspend) and the item element of list/untrack. Asserts the
    review/new direction branch (``left`` absent); the list_items key-set test
    pins the ``left``-present branch separately.
    """
    assert set(data.keys()) == SRS_ITEM_KEYS
    assert set(data["directions"].keys()) == {"recognition", "production"}
    assert data["directions"]["recognition"] is not None
    assert data["directions"]["production"] is not None
    assert set(data["directions"]["recognition"].keys()) == DIRECTION_WITHOUT_LEFT
    assert set(data["directions"]["production"].keys()) == DIRECTION_WITHOUT_LEFT


@pytest.fixture(autouse=True)
def _clean_app_state():
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    app.state.srs_db = db
    app.state.content_store = store
    app.state.language = get_language("sl")
    yield
    db.close()
    store.close()
    for attr in ("srs_db", "content_store", "language", "llm"):
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

    async def test_list_items_invalid_order_dir_returns_422(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/items", params={"order": "sideways"})
        assert response.status_code == 422

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

    async def test_list_items_handles_single_template_note_without_production(self):
        """Single-template Anki notes (e.g., Basic phonics, ord=0 only) have no
        production-direction row after the v15→v16 migration in d306311.
        Serializing them must not crash with KeyError(Direction.PRODUCTION) —
        the response should expose recognition normally and `production: null`.
        """
        db = _db()
        db.add_collocation(_unit("phonics_a", "ah"), language_code="sl")
        rows, _ = db.list_collocations(search="phonics_a", limit=1)
        coll_id = rows[0][0]
        # Simulate post-migration state: drop the phantom production row.
        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE collocation_id = ? AND direction = 'production'",
                (coll_id,),
            )
            db._commit(conn)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/items", params={"search": "phonics_a"})

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["directions"]["recognition"] is not None
        assert item["directions"]["production"] is None

    async def test_list_items_includes_image_url(self):
        db = _db()
        db.add_collocation(_unit("voda", "water"), language_code="sl")
        rows, _ = db.list_collocations(search="voda", limit=1)
        coll_id = rows[0][0]
        db.add_media(coll_id, "image", "voda.jpg", "/tmp/voda.jpg", "voda.jpg", "abc123", 1024)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/items", params={"search": "voda"})

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["image_url"] == "/api/srs/media/voda.jpg"

    async def test_list_items_image_url_null_when_no_image(self):
        db = _db()
        db.add_collocation(_unit("voda", "water"), language_code="sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/items", params={"search": "voda"})

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["image_url"] is None

    async def test_list_items_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (openapi ledger batch 6a).

        Seeds a LEARNING recognition direction with ``left`` set plus one
        ``extras`` element, so the ``left``-present branch of
        ``_direction_to_dict`` and the extras element shape are pinned here.
        """
        from app.api.models import ListItemsResponse
        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import BackField

        db = _db()
        extras = (BackField(label="IPA", html="/bɑŋkɑ/", tier="summary"),)
        db.add_collocation(
            SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus", extras=extras),
            language_code="sl",
        )
        item = db.get_collocation("banka")
        now = datetime.now(UTC)
        db.update_direction(
            item.guid,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.LEARNING,
                stability=1.0,
                left=2,
                due_at=now + timedelta(minutes=1),
            ),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/items", params={"search": "banka"})

        assert response.status_code == 200
        data = response.json()
        assert set(data.keys()) == {"items", "total"}
        assert set(ListItemsResponse.model_fields) == {"items", "total"}
        item_payload = data["items"][0]
        assert set(item_payload.keys()) == SRS_ITEM_KEYS
        assert set(item_payload["directions"].keys()) == {"recognition", "production"}
        assert set(item_payload["directions"]["recognition"].keys()) == DIRECTION_KEYS  # learning: left present
        assert item_payload["directions"]["recognition"]["left"] == 2
        assert set(item_payload["directions"]["production"].keys()) == DIRECTION_WITHOUT_LEFT
        assert set(item_payload["extras"][0].keys()) == {"label", "html", "tier"}


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

    async def test_patch_item_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (openapi ledger batch 6a)."""
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
        _assert_item_keys(response.json())


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

    async def test_delete_item_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (bp-ledger-burndown stage 3)."""
        db = _db()
        db.add_collocation(_unit("zdravo", "hello"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete(f"/api/srs/items/{row_id}")

        assert set(response.json().keys()) == {"status"}
        assert set(StatusResponse.model_fields) == {"status"}

    async def test_delete_item_returns_404_for_unknown_id(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/srs/items/99999")
        assert response.status_code == 404

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

    async def test_bulk_delete_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (openapi ledger batch 5)."""
        db = _db()
        db.add_collocation(_unit("a", "a"), language_code="sl")
        rows, _ = db.list_collocations()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/items/bulk-delete", json={"ids": [rows[0][0]]})

        assert set(response.json().keys()) == {"deleted"}
        assert set(BulkDeleteResponse.model_fields) == {"deleted"}


class TestCreateItem:
    """Tests for POST /api/srs/items."""

    async def test_create_item_returns_201_with_id(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/items",
                json={"text": "banka", "language_code": "sl", "word_count": 1},
            )
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["text"] == "banka"
        assert data["state"] == "new"

    async def test_create_item_with_translation(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/items",
                json={"text": "banka", "language_code": "sl", "word_count": 1, "translation": "bank"},
            )
        assert response.status_code == 201
        data = response.json()
        assert data["translation"] == "bank"

    async def test_create_item_with_collocation(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/items",
                json={"text": "prosim kavo", "language_code": "sl", "word_count": 2, "translation": "a coffee please"},
            )
        assert response.status_code == 201
        data = response.json()
        assert data["text"] == "prosim kavo"

    async def test_create_item_duplicate_text_returns_409(self):
        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/items",
                json={"text": "banka", "language_code": "sl", "word_count": 1},
            )
        assert response.status_code == 409

    async def test_create_item_invalid_word_count_returns_422(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/items",
                json={"text": "banka", "language_code": "sl", "word_count": 0},
            )
        assert response.status_code == 422

    async def test_create_item_persists_in_db(self):
        db = _db()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/srs/items",
                json={"text": "hvala", "language_code": "sl", "word_count": 1, "translation": "thank you"},
            )
        assert db.count_collocations() == 1
        item = db.get_collocation("hvala")
        assert item is not None
        assert item.syntactic_unit.translation == "thank you"

    async def test_create_item_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (openapi ledger batch 6a).

        create_item is the bare-item case. A fresh vocab card carries both NEW
        directions with no ``left``, so this also pins the review/new branch of
        ``_direction_to_dict`` and the model-vs-literal half for all four
        element models.
        """
        from app.api.models import DirectionStateResponse, ItemDirections, ItemExtra, SrsItemResponse

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/items",
                json={"text": "banka", "language_code": "sl", "word_count": 1, "translation": "bank"},
            )

        assert response.status_code == 201
        _assert_item_keys(response.json())
        assert response.json()["extras"] == []

        assert set(SrsItemResponse.model_fields) == SRS_ITEM_KEYS
        assert set(ItemDirections.model_fields) == {"recognition", "production"}
        assert set(DirectionStateResponse.model_fields) == DIRECTION_KEYS
        assert set(ItemExtra.model_fields) == {"label", "html", "tier"}


class TestTranscriptEnrichment:
    """Tests that GET /api/srs/content/{id}/transcript returns enriched WordToken fields."""

    async def test_transcript_includes_srs_item_id_for_known_word(self):
        db = _db()
        db.add_collocation(
            SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus", lemma="banka"),
            language_code="sl",
        )
        rows, _ = db.list_collocations()
        expected_id = rows[0][0]

        from app.models.lesson import Lesson, Phrase, Section, SectionType

        lesson = Lesson(title="Test", language_code="sl")
        lesson.sections = [
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
            )
        ]
        store = app.state.content_store
        store.save_lesson("test-lesson", "curr-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/content/test-lesson/transcript")

        assert response.status_code == 200
        word = response.json()["dialogue_lines"][0]["words"][0]
        assert word["srs_item_id"] == expected_id

    async def test_transcript_includes_translation_for_known_word(self):
        db = _db()
        db.add_collocation(
            SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus", lemma="banka"),
            language_code="sl",
        )

        from app.models.lesson import Lesson, Phrase, Section, SectionType

        lesson = Lesson(title="Test", language_code="sl")
        lesson.sections = [
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
            )
        ]
        store = app.state.content_store
        store.save_lesson("test-lesson2", "curr-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/content/test-lesson2/transcript")

        assert response.status_code == 200
        word = response.json()["dialogue_lines"][0]["words"][0]
        assert word["translation"] == "bank"

    async def test_transcript_null_srs_item_id_for_unknown_word(self):
        from app.models.lesson import Lesson, Phrase, Section, SectionType

        lesson = Lesson(title="Test", language_code="sl")
        lesson.sections = [
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
            )
        ]
        store = app.state.content_store
        store.save_lesson("test-lesson3", "curr-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/content/test-lesson3/transcript")

        assert response.status_code == 200
        word = response.json()["dialogue_lines"][0]["words"][0]
        assert word["srs_item_id"] is None
        assert word["translation"] is None


class TestSetState:
    """Tests for POST /api/srs/items/{id}/state."""

    async def test_set_state_to_known(self):
        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/state", json={"state": "known"})

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "known"
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is True
        assert item.directions[Direction.PRODUCTION].dirty_fsrs is True

    async def test_set_state_to_known_far_future_due_at(self):
        """KNOWN sets due_at = today + max_ivl with matched stability."""
        from app.srs.fsrs import stability_for_interval

        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/state", json={"state": "known"})

        assert response.status_code == 200
        item = db.get_collocation("banka")
        ds = item.directions[Direction.RECOGNITION]
        assert ds.state == SRSState.KNOWN
        # Default max_ivl is 36500, default dr is 0.9
        expected_max_ivl = 36500
        expected_due = anki_today() + timedelta(days=expected_max_ivl)
        assert ds.due_at.date() == expected_due
        expected_stability = stability_for_interval(expected_max_ivl, 0.9)
        assert abs(ds.stability - expected_stability) < 1.0

    async def test_set_state_to_ignored_maps_to_suspended(self):
        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/state", json={"state": "ignored"})

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "suspended"

    async def test_set_state_to_learning(self):

        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/state", json={"state": "learning"})

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "learning"
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.LEARNING
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is True
        assert item.directions[Direction.PRODUCTION].dirty_fsrs is True
        assert item.directions[Direction.RECOGNITION].due_at.date() == anki_today()

    async def test_set_state_to_new(self):
        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/state", json={"state": "new"})

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "new"
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is True

    async def test_set_state_unknown_id_returns_404(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/items/9999/state", json={"state": "known"})
        assert response.status_code == 404

    async def test_set_state_invalid_state_returns_422(self):
        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/state", json={"state": "nonexistent"})
        assert response.status_code == 422

    async def test_known_state_excluded_from_due_queue(self):
        from datetime import date, timedelta

        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        item = db.get_collocation("banka")
        item.due_date = date.today() - timedelta(days=1)
        item.state = SRSState.REVIEW
        db.update_collocation(item)
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(f"/api/srs/items/{row_id}/state", json={"state": "known"})
            due_resp = await client.get("/api/srs/due")

        assert due_resp.status_code == 200
        due_texts = [i["text"] for i in due_resp.json()["due"]]
        assert "banka" not in due_texts
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is True

    async def test_set_state_item_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (openapi ledger batch 6a)."""
        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/state", json={"state": "known"})

        assert response.status_code == 200
        _assert_item_keys(response.json())


class TestRestoreKnown:
    """Tests for POST /api/srs/items/{id}/restore-known."""

    async def test_restore_known_reverses_mark(self):
        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        item = db.get_collocation("banka")
        # Pre-known: a review card worth restoring.
        ds = item.directions[Direction.RECOGNITION]
        ds.state = SRSState.REVIEW
        ds.stability = 7.5
        ds.due_at = datetime(2026, 3, 1, 4, 0, tzinfo=UTC)
        db.update_direction(item.guid, Direction.RECOGNITION, ds)
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(f"/api/srs/items/{row_id}/state", json={"state": "known"})
            assert db.is_known_marked(row_id) is True

            response = await client.post(f"/api/srs/items/{row_id}/restore-known")

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "review"
        assert db.is_known_marked(row_id) is False
        restored = db.get_collocation("banka").directions[Direction.RECOGNITION]
        assert restored.state == SRSState.REVIEW
        assert abs(restored.stability - 7.5) < 0.01
        assert restored.dirty_fsrs is True
        assert restored.fsrs_force_next is True

    async def test_restore_known_noop_without_snapshot_returns_200(self):
        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/restore-known")

        assert response.status_code == 200
        assert db.is_known_marked(row_id) is False

    async def test_restore_known_unknown_id_returns_404(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/items/9999/restore-known")
        assert response.status_code == 404

    async def test_restore_known_item_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (openapi ledger batch 6a)."""
        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        item = db.get_collocation("banka")
        ds = item.directions[Direction.RECOGNITION]
        ds.state = SRSState.REVIEW
        ds.stability = 7.5
        ds.due_at = datetime(2026, 3, 1, 4, 0, tzinfo=UTC)
        db.update_direction(item.guid, Direction.RECOGNITION, ds)
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(f"/api/srs/items/{row_id}/state", json={"state": "known"})
            response = await client.post(f"/api/srs/items/{row_id}/restore-known")

        assert response.status_code == 200
        _assert_item_keys(response.json())


class TestUntrack:
    """Tests for POST /api/srs/items/{id}/untrack."""

    async def test_untrack_never_synced_deletes(self):
        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/untrack")
            assert response.status_code == 200
            assert response.json() == {"action": "deleted"}

        assert db.get_collocation_by_id(row_id) is None

    async def test_untrack_synced_row_suspends(self):
        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET anki_note_id = 12345 WHERE id = ?", (row_id,))
            conn.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/untrack")

        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "suspended"
        assert data["item"]["state"] == "suspended"

        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.SUSPENDED
        assert item.directions[Direction.RECOGNITION].dirty_fsrs is True

    async def test_untrack_nonexistent_returns_404(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/items/9999/untrack")
        assert response.status_code == 404

    async def test_untrack_item_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (openapi ledger batch 6a).

        Two branches — ``{"action": "deleted"}`` (never-synced row) and
        ``{"action": "suspended", "item": ...}`` (synced row) — so both need
        pinning. ``response_model_exclude_unset`` leaves ``item`` off the short
        branch.
        """
        from app.api.models import UntrackItemResponse

        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            deleted = await client.post(f"/api/srs/items/{row_id}/untrack")

        assert deleted.status_code == 200
        assert set(deleted.json().keys()) == {"action"}
        assert deleted.json()["action"] == "deleted"

        db.add_collocation(_unit("kava", "coffee"), language_code="sl")
        rows, _ = db.list_collocations()
        synced_id = rows[0][0]
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET anki_note_id = 12345 WHERE id = ?", (synced_id,))
            conn.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            suspended = await client.post(f"/api/srs/items/{synced_id}/untrack")

        assert suspended.status_code == 200
        data = suspended.json()
        assert set(data.keys()) == {"action", "item"}
        assert data["action"] == "suspended"
        _assert_item_keys(data["item"])
        assert set(UntrackItemResponse.model_fields) == {"action", "item"}


class TestResetSuspend:
    """Tests for reset and suspend endpoints."""

    async def test_reset_item_returns_404_for_unknown_id(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/items/99999/reset")
        assert response.status_code == 404

    async def test_suspend_item_returns_404_for_unknown_id(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/items/99999/suspend", json={"suspended": True})
        assert response.status_code == 404

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

    async def test_reset_item_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (openapi ledger batch 6a)."""
        db = _db()
        db.add_collocation(_unit("hvala", "thank you"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/reset")

        assert response.status_code == 200
        _assert_item_keys(response.json())

    async def test_suspend_item_invalid_direction_returns_422(self):
        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/srs/items/{row_id}/suspend",
                json={"suspended": False, "direction": "baddir"},
            )
        assert response.status_code == 422

    async def test_suspend_item_with_direction_param(self):
        from app.models.srs_item import Direction, DirectionState, SRSState

        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]
        guid = db.get_collocation("banka").guid
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=15.0,
            difficulty=4.5,
            reps=5,
            lapses=0,
            state=SRSState.REVIEW,
            dirty_fsrs=False,
        )
        db.update_direction(guid, Direction.RECOGNITION, ds)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            suspend_resp = await client.post(
                f"/api/srs/items/{row_id}/suspend",
                json={"suspended": False, "direction": "recognition"},
            )
        assert suspend_resp.status_code == 200
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.REVIEW

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

    async def test_suspend_item_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (openapi ledger batch 6a).

        suspend_item returns a BARE item (``srs.py:1974``), not an
        ``{action, item}`` envelope — that shape belongs to untrack_item
        (brief corrected 2026-07-31).
        """
        db = _db()
        db.add_collocation(_unit("lep", "nice"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/suspend", json={"suspended": True})

        assert response.status_code == 200
        _assert_item_keys(response.json())


class TestBackfillTranslations:
    """Tests for POST /api/srs/backfill-translations."""

    async def test_backfill_fills_empty_translations(self):
        from app.models.lesson import Lesson

        db = _db()
        store = app.state.content_store
        db.add_collocation(_unit("banka", ""), language_code="sl")
        db.add_collocation(_unit("hiša", "house"), language_code="sl")

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            generation_metadata={"token_glosses": {"banka": "bank", "hiša": "dom"}},
        )
        store.save_lesson("l1", "c1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/backfill-translations")

        assert response.status_code == 200
        data = response.json()
        assert data["updated"] == 1  # only banka was empty
        assert data["glosses_found"] == 2
        assert db.get_collocation("banka").syntactic_unit.translation == "bank"
        assert db.get_collocation("hiša").syntactic_unit.translation == "house"  # not overwritten

    async def test_backfill_returns_zero_when_no_lessons(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/backfill-translations")

        assert response.status_code == 200
        assert response.json() == {"updated": 0, "glosses_found": 0}

    async def test_backfill_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (openapi ledger batch 5)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/backfill-translations")

        assert set(response.json().keys()) == {"updated", "glosses_found"}
        assert set(BackfillTranslationsResponse.model_fields) == {"updated", "glosses_found"}


class TestTranslateMissing:
    """Tests for POST /api/srs/translate-missing."""

    async def test_translates_untranslated_cards(self):
        from unittest.mock import AsyncMock, MagicMock

        db = _db()
        db.add_collocation(_unit("zdravo"), language_code="sl")
        db.add_collocation(_unit("hvala", "thank you"), language_code="sl")

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value='{"zdravo": "hello"}')
        app.state.llm = mock_llm

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/translate-missing")

        assert response.status_code == 200
        data = response.json()
        assert data["translated"] == 1
        assert data["skipped"] == 0
        assert db.get_collocation("zdravo").syntactic_unit.translation == "hello"
        assert db.get_collocation("hvala").syntactic_unit.translation == "thank you"

    async def test_returns_zero_when_all_translated(self):
        from unittest.mock import AsyncMock, MagicMock

        db = _db()
        db.add_collocation(_unit("hvala", "thank you"), language_code="sl")

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value="{}")
        app.state.llm = mock_llm

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/translate-missing")

        assert response.status_code == 200
        assert response.json() == {"translated": 0, "skipped": 0}
        mock_llm.complete.assert_not_called()

    async def test_skips_batch_on_invalid_json(self):
        from unittest.mock import AsyncMock, MagicMock

        db = _db()
        db.add_collocation(_unit("zdravo"), language_code="sl")

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value="not json")
        app.state.llm = mock_llm

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/translate-missing")

        assert response.status_code == 200
        data = response.json()
        assert data["translated"] == 0
        assert data["skipped"] == 1

    async def test_strips_markdown_code_fences(self):
        from unittest.mock import AsyncMock, MagicMock

        db = _db()
        db.add_collocation(_unit("zdravo"), language_code="sl")

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value='```json\n{"zdravo": "hello"}\n```')
        app.state.llm = mock_llm

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/translate-missing")

        assert response.status_code == 200
        assert db.get_collocation("zdravo").syntactic_unit.translation == "hello"

    async def test_translate_missing_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (openapi ledger batch 5).

        The handler has TWO returns — the "nothing untranslated" early return
        and the batch loop's — so both need pinning: `response_model=` filters,
        and a branch whose key-set was never asserted is unguarded.
        """
        from unittest.mock import AsyncMock, MagicMock

        db = _db()
        db.add_collocation(_unit("zdravo"), language_code="sl")
        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value='{"zdravo": "hello"}')
        app.state.llm = mock_llm

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            translated = await client.post("/api/srs/translate-missing")
            # Second call: everything is translated now, so this takes the early return.
            early = await client.post("/api/srs/translate-missing")

        assert set(translated.json().keys()) == {"translated", "skipped"}
        assert set(early.json().keys()) == {"translated", "skipped"}
        assert set(TranslateMissingResponse.model_fields) == {"translated", "skipped"}


class TestTranslate:
    """Tests for POST /api/srs/translate."""

    async def test_translate_returns_llm_translation(self):
        from unittest.mock import AsyncMock, MagicMock

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value="in the city centre")
        app.state.llm = mock_llm

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/translate",
                json={"text": "centru mesta", "language_code": "sl"},
            )

        assert response.status_code == 200
        data = response.json()
        assert "translation" in data
        assert data["translation"] == "in the city centre"
        mock_llm.complete.assert_awaited_once()

    async def test_translate_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (openapi ledger batch 5)."""
        from unittest.mock import AsyncMock, MagicMock

        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value="in the city centre")
        app.state.llm = mock_llm

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/translate",
                json={"text": "centru mesta", "language_code": "sl"},
            )

        assert set(response.json().keys()) == {"translation"}
        assert set(TranslateResponse.model_fields) == {"translation"}

    async def test_translate_empty_text_returns_422(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/translate",
                json={"text": "", "language_code": "sl"},
            )
        assert response.status_code == 422

    async def test_translate_blank_text_returns_422(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/translate",
                json={"text": "   ", "language_code": "sl"},
            )
        assert response.status_code == 422

    async def test_translate_invalid_language_code_returns_422(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/translate",
                json={"text": "centru mesta", "language_code": "invalid"},
            )
        assert response.status_code == 422
        data = response.json()
        assert "language_code" in data["detail"]

    async def test_translate_llm_not_configured_returns_503(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/translate",
                json={"text": "centru mesta", "language_code": "sl"},
            )
        assert response.status_code == 503
