"""Tests for listen history endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.languages import get_language
from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


class TestListensEndpoints:
    """Tests for GET /api/srs/listens and POST /api/srs/listens/import."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        self.db = SRSDatabase(":memory:")
        self.store = ContentStore(":memory:")
        app.state.srs_db = self.db
        app.state.content_store = self.store
        # Create some lessons in the store so has_listen / import can resolve them.

        for lid in ("lesson-a", "lesson-b", "lesson-c"):
            lesson = Lesson(
                title=lid,
                language_code="sl",
                sections=[
                    Section(
                        section_type=SectionType.KEY_PHRASES,
                        phrases=[
                            Phrase(text="test", role="female-1", voice_id="sl-SI-PetraNeural", language_code="sl"),
                        ],
                    )
                ],
                key_phrases=[KeyPhraseInfo(phrase="test", translation="test")],
            )
            self.store.save_lesson(lid, "curriculum-1", 1, lesson)
        yield
        self.db.close()
        for attr in ("srs_db", "content_store"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)

    async def test_get_empty(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/listens")
        assert resp.status_code == 200
        assert resp.json() == {"lessons": []}

    async def test_get_after_record_listen(self):
        self.db.record_listen("lesson-a")
        self.db.record_listen("lesson-a")
        self.db.record_listen("lesson-b")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/listens")
        assert resp.status_code == 200
        lessons = resp.json()["lessons"]
        assert len(lessons) == 2
        by_id = {item["lesson_id"]: item for item in lessons}
        assert by_id["lesson-a"]["listen_count"] == 2
        assert by_id["lesson-b"]["listen_count"] == 1
        # Ordered by last_listened_at DESC — lesson-b was inserted after lesson-a.
        assert lessons[0]["lesson_id"] == "lesson-b"
        assert lessons[1]["lesson_id"] == "lesson-a"

    async def test_import_split(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/listens/import",
                json={"lesson_ids": ["lesson-a", "lesson-x", "lesson-b"]},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == ["lesson-a", "lesson-b"]
        assert body["already_present"] == []
        assert body["unknown"] == ["lesson-x"]

    async def test_import_already_present(self):
        self.db.record_listen("lesson-a", source="listen")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/listens/import",
                json={"lesson_ids": ["lesson-a", "lesson-b"]},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == ["lesson-b"]
        assert body["already_present"] == ["lesson-a"]
        assert body["unknown"] == []

    async def test_import_idempotent(self):
        """Re-POSTing the same body: everything previously imported becomes already_present."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp1 = await client.post(
                "/api/srs/listens/import",
                json={"lesson_ids": ["lesson-a", "lesson-b"]},
            )
        assert resp1.status_code == 200
        assert resp1.json()["imported"] == ["lesson-a", "lesson-b"]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp2 = await client.post(
                "/api/srs/listens/import",
                json={"lesson_ids": ["lesson-a", "lesson-b"]},
            )
        assert resp2.status_code == 200
        body = resp2.json()
        assert body["imported"] == []
        assert body["already_present"] == ["lesson-a", "lesson-b"]
        assert body["unknown"] == []
        # Row count must not grow.
        assert self.db.count_listens("lesson-a") == 1
        assert self.db.count_listens("lesson-b") == 1

    async def test_import_empty_list(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listens/import", json={"lesson_ids": []})
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == []
        assert body["already_present"] == []
        assert body["unknown"] == []

    async def test_import_unknown_inserts_nothing(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/listens/import",
                json={"lesson_ids": ["lesson-x", "lesson-y"]},
            )
        assert resp.status_code == 200
        assert resp.json()["unknown"] == ["lesson-x", "lesson-y"]
        assert self.db.get_listened_lessons() == []

    async def test_import_preserves_input_order(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/listens/import",
                json={"lesson_ids": ["lesson-c", "lesson-a", "lesson-b", "lesson-x"]},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == ["lesson-c", "lesson-a", "lesson-b"]
        assert body["unknown"] == ["lesson-x"]

    async def test_import_dedupes_input(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/listens/import",
                json={"lesson_ids": ["lesson-a", "lesson-a", "lesson-b"]},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == ["lesson-a", "lesson-b"]
        assert self.db.count_listens("lesson-a") == 1


class TestListensLanguageIsolation:
    """Listens recorded under language A's DB are invisible via GET with X-TT-Language=B."""

    @pytest.fixture
    def two_lang(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        db_sl = SRSDatabase(":memory:")
        db_no = SRSDatabase(":memory:")
        app.state.srs_dbs = {"sl": db_sl, "no": db_no}
        app.state.srs_db = db_sl
        store_sl = ContentStore(":memory:")
        store_no = ContentStore(":memory:")
        app.state.content_stores = {"sl": store_sl, "no": store_no}
        app.state.content_store = store_sl

        app.state.languages = {"sl": get_language("sl"), "no": get_language("no")}
        app.state.language = get_language("sl")
        try:
            yield db_sl, db_no
        finally:
            db_sl.close()
            db_no.close()
            for attr in (
                "srs_dbs",
                "srs_db",
                "content_stores",
                "content_store",
                "languages",
                "language",
            ):
                if hasattr(app.state, attr):
                    delattr(app.state, attr)

    async def test_listens_in_one_lang_invisible_in_other(self, two_lang):
        db_sl, db_no = two_lang
        db_sl.record_listen("lesson-sl")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Slovene sees its listen.
            resp_sl = await client.get("/api/srs/listens", headers={"X-TT-Language": "sl"})
            # Norwegian sees nothing.
            resp_no = await client.get("/api/srs/listens", headers={"X-TT-Language": "no"})
        assert resp_sl.status_code == 200
        assert resp_no.status_code == 200
        assert len(resp_sl.json()["lessons"]) == 1
        assert resp_sl.json()["lessons"][0]["lesson_id"] == "lesson-sl"
        assert resp_no.json()["lessons"] == []
