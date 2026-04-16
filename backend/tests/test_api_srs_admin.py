"""Admin SRS API endpoint tests."""

from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.language import Language
from app.models.srs_item import SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore


def _unit(text: str, translation: str = "") -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=1, difficulty=1, source="corpus")


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

    async def test_create_item_returns_500_when_db_list_fails(self, monkeypatch):
        """Defensive guard: if list_collocations returns nothing after add, return 500."""
        db = _db()
        monkeypatch.setattr(db, "list_collocations", lambda **kwargs: ([], 0))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/items",
                json={"text": "hvala", "language_code": "sl", "word_count": 1},
            )
        assert response.status_code == 500


class TestTranscriptEnrichment:
    """Tests that GET /api/srs/lesson/{id}/transcript returns enriched WordToken fields."""

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
            response = await client.get("/api/srs/lesson/test-lesson/transcript")

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
            response = await client.get("/api/srs/lesson/test-lesson2/transcript")

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
            response = await client.get("/api/srs/lesson/test-lesson3/transcript")

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
        assert response.json()["state"] == "learning"

    async def test_set_state_to_new(self):
        db = _db()
        db.add_collocation(_unit("banka", "bank"), language_code="sl")
        rows, _ = db.list_collocations()
        row_id = rows[0][0]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(f"/api/srs/items/{row_id}/state", json={"state": "new"})

        assert response.status_code == 200
        assert response.json()["state"] == "new"

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
