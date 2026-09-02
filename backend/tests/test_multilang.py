"""Phase 5 — simultaneous multi-language: per-request connection resolution."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.models import LanguageItem, LanguagesResponse
from app.languages import get_language
from app.main import _language_db_map, app
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore


class TestLanguageDbMap:
    def test_single_language_when_no_database_urls(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "database_urls", {})
        monkeypatch.setattr(settings, "target_language", "sl")
        monkeypatch.setattr(settings, "database_url", "sqlite:///./tunatale_sl.db")
        assert _language_db_map() == {"sl": "sqlite:///./tunatale_sl.db"}

    def test_multi_language_when_database_urls_set(self, monkeypatch):
        from app.config import settings

        urls = {"sl": "sqlite:///./tunatale_sl.db", "no": "sqlite:///./tunatale_no.db"}
        monkeypatch.setattr(settings, "database_urls", urls)
        assert _language_db_map() == urls


async def test_lifespan_opens_a_connection_per_language(tmp_path, monkeypatch):
    """Multi-language lifespan: a connection set per configured language, and the
    singular defaults bind to target_language (or the first entry when it's absent)."""
    from app.config import settings
    from app.main import lifespan

    urls = {
        "sl": f"sqlite:///{tmp_path / 'sl.db'}",
        "no": f"sqlite:///{tmp_path / 'no.db'}",
    }
    monkeypatch.setattr(settings, "database_urls", urls)
    monkeypatch.setattr(settings, "llm_mode", "mock")
    # target_language NOT in the map → default_code falls back to the first entry.
    monkeypatch.setattr(settings, "target_language", "zz")

    test_app = FastAPI()
    async with lifespan(test_app):
        assert set(test_app.state.srs_dbs) == {"sl", "no"}
        assert set(test_app.state.content_stores) == {"sl", "no"}
        assert test_app.state.languages["no"].code == "no"
        # default singular binds to the first configured language (sl).
        assert test_app.state.srs_db is test_app.state.srs_dbs["sl"]
        assert test_app.state.language.code == "sl"


class TestPerRequestIsolation:
    """The X-TT-Language header selects which connection serves the request —
    isolation is the connection, not a WHERE clause."""

    @pytest.fixture
    def two_language_app(self):
        db_sl = SRSDatabase(":memory:")
        db_no = SRSDatabase(":memory:")
        db_sl.add_collocation(
            SyntacticUnit(text="voda", translation="water", word_count=1, difficulty=1, source="corpus")
        )
        db_no.add_collocation(
            SyntacticUnit(text="vann", translation="water", word_count=1, difficulty=1, source="corpus")
        )
        app.state.srs_dbs = {"sl": db_sl, "no": db_no}
        app.state.content_stores = {"sl": ContentStore(":memory:"), "no": ContentStore(":memory:")}
        app.state.languages = {"sl": get_language("sl"), "no": get_language("no")}
        # Singular defaults (the active language) — used by the no-header path.
        app.state.srs_db = db_sl
        app.state.content_store = app.state.content_stores["sl"]
        app.state.language = get_language("sl")
        try:
            yield
        finally:
            db_sl.close()
            db_no.close()
            for attr in (
                "srs_dbs",
                "content_stores",
                "languages",
                "srs_db",
                "content_store",
                "language",
            ):
                if hasattr(app.state, attr):
                    delattr(app.state, attr)

    async def _texts(self, client, headers=None):
        resp = await client.get("/api/srs/items", headers=headers or {})
        assert resp.status_code == 200
        return {item["text"] for item in resp.json()["items"]}

    async def test_header_selects_norwegian_connection(self, two_language_app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert await self._texts(client, {"X-TT-Language": "no"}) == {"vann"}

    async def test_header_selects_slovene_connection(self, two_language_app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert await self._texts(client, {"X-TT-Language": "sl"}) == {"voda"}

    async def test_no_header_uses_default_language(self, two_language_app, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "target_language", "sl")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert await self._texts(client) == {"voda"}

    async def test_unknown_language_falls_back_to_default(self, two_language_app, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "target_language", "no")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # "de" isn't configured → falls back to target_language (no).
            assert await self._texts(client, {"X-TT-Language": "de"}) == {"vann"}

    async def test_languages_endpoint_lists_configured_languages(self, two_language_app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/languages", headers={"X-TT-Language": "no"})
        assert resp.status_code == 200
        body = resp.json()
        assert {lang["code"] for lang in body["languages"]} == {"sl", "no"}
        assert {lang["name"] for lang in body["languages"]} == {"Slovene", "Norwegian"}
        assert body["active"] == "no"
        assert set(body.keys()) == {"languages", "active", "sync_available"}
        assert set(LanguagesResponse.model_fields) == {"languages", "active", "sync_available"}
        for lang in body["languages"]:
            assert set(lang.keys()) == {"code", "name"}
            assert set(LanguageItem.model_fields) == {"code", "name"}

    async def test_grade_in_one_language_does_not_touch_the_other(self, two_language_app):
        """A write through one connection is invisible to the other (no shared DB)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Add a Norwegian item via the "no" connection.
            resp = await client.post(
                "/api/srs/items",
                headers={"X-TT-Language": "no"},
                json={"text": "hund", "translation": "dog", "language_code": "no", "word_count": 1},
            )
            assert resp.status_code == 201
            # Slovene connection is unaffected.
            assert await self._texts(client, {"X-TT-Language": "sl"}) == {"voda"}
            assert await self._texts(client, {"X-TT-Language": "no"}) == {"vann", "hund"}

    async def test_curriculum_imports_as_norwegian_into_the_norwegian_store(self, two_language_app):
        """A Norwegian plan import comes back as `no` AND lands only in the `no` store.

        Ported down from `frontend/tests/generate-norwegian.spec.ts` on
        2026-08-15 (`tunatale-vnf.10`), which asserted the first half against a
        second uvicorn on port 8002 while never opening a browser. The second
        half is new and is the part worth having: the e2e version could not see
        which store the curriculum landed in, because it only ever talked to one
        backend at a time.
        """
        day = {
            "day": 1,
            "title": "Kaffe på norsk",
            "focus": "Basic coffee ordering",
            "collocations": ["Jeg vil gjerne en kaffe", "En espresso takk"],
            "learning_objective": "Order a coffee and express simple preferences",
            "story_guidance": "Learner visits a busy café in Oslo for the first time",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/curriculum/import",
                headers={"X-TT-Language": "no"},
                json={
                    "topic": "ordering coffee",
                    "language_code": "no",
                    "cefr_level": "A2",
                    "days": [day],
                },
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["language_code"] == "no"
            assert body["days"] == 1

        # The isolation claim: the `no` store has it, the `sl` store does not.
        assert app.state.content_stores["no"].get_curriculum(body["id"]) is not None
        assert app.state.content_stores["sl"].get_curriculum(body["id"]) is None


class TestLanguagesEndpointFallbacks:
    """The /api/languages singular + empty fallbacks (no per-language maps)."""

    def _cleanup(self):
        for attr in ("languages", "language", "srs_dbs", "srs_db"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)

    async def test_single_language_uses_singular_app_state(self):
        app.state.language = get_language("sl")
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                body = (await client.get("/api/languages")).json()
            assert body["languages"] == [{"code": "sl", "name": "Slovene"}]
            assert set(body.keys()) == {"languages", "active", "sync_available"}
            assert set(LanguagesResponse.model_fields) == {"languages", "active", "sync_available"}
            assert set(body["languages"][0].keys()) == {"code", "name"}
            assert set(LanguageItem.model_fields) == {"code", "name"}
        finally:
            self._cleanup()

    async def test_no_language_configured_returns_empty_list(self):
        # Neither maps nor singular language set → empty list (no crash).
        self._cleanup()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            body = (await client.get("/api/languages")).json()
        assert body["languages"] == []
        assert set(body.keys()) == {"languages", "active", "sync_available"}
        assert set(LanguagesResponse.model_fields) == {"languages", "active", "sync_available"}


class TestGenerateStoryUsesTheRequestsDatabase:
    """``/api/story/generate`` must annotate against the REQUEST's SRS db.

    bd tunatale-pf4i. ``generate_story`` read ``request.app.state.srs_db`` —
    the DEFAULT language's connection, set once at startup — while ``store``,
    ``language`` and the pipeline enqueue in the SAME function all read
    ``request.state``. Three of four resolved per-request; the fourth did not.
    Generating a story in the non-default language therefore wrote UPOS chunk
    annotations and prewarmed sentence analyses into the OTHER language's
    database. Both are text-keyed caches, so nothing raised: the symptom is
    plausible annotation drawn from, and cached into, the wrong deck.

    ⚠️ THIS TEST IS ONLY MEANINGFUL WITH TWO LANGUAGES CONFIGURED. With one,
    ``app.state.srs_db`` and ``request.state.srs_db`` are the SAME OBJECT, so
    the bug is invisible and any single-language test passes either way. That is
    almost certainly why it survived. Do not "simplify" this fixture to one
    language.

    The lemmatizer is injected through ``app.state.lemmatizer`` — the seam
    ``_injected_lemmatizer`` exists for — so the sentence-analysis cache write
    is reachable without loading Stanza and without patching into ``app.``.
    """

    SENTENCE = "kako ste"

    @pytest.fixture
    def two_language_generate_app(self):
        from unittest.mock import AsyncMock

        from app.models.curriculum import Curriculum, CurriculumDay
        from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
        from app.srs.lemmatizer import TokenAnalysis

        db_sl = SRSDatabase(":memory:")
        db_no = SRSDatabase(":memory:")
        store_no = ContentStore(":memory:")
        store_no.save_curriculum(
            "c-no",
            Curriculum(
                id="c-no",
                topic="coffee",
                language_code="no",
                cefr_level="A2",
                days=[
                    CurriculumDay(
                        day=1,
                        title="Day 1",
                        focus="greetings",
                        learning_objective="greet",
                        story_guidance="cafe",
                        collocations=["hei"],
                    )
                ],
            ),
        )

        generator = AsyncMock()
        generator.generate = AsyncMock(
            return_value=Lesson(
                title="Day 1",
                language_code="no",
                key_phrases=[KeyPhraseInfo(phrase=self.SENTENCE, translation="how are you")],
                sections=[
                    # KEY_PHRASES specifically: annotate_chunk_upos walks that
                    # section and no other, and iterates lesson.key_phrases
                    # against it, needing 2 + len(breakdown) phrases per key
                    # phrase or it warns and skips. A lesson missing either half
                    # caches nothing, and the assertion below would then pass
                    # vacuously in BOTH directions — a clean negative, not an
                    # oracle. Padded generously; the exact count is not the point.
                    Section(
                        section_type=SectionType.KEY_PHRASES,
                        phrases=[
                            Phrase(
                                text=self.SENTENCE,
                                role="male-1",
                                voice_id="nb-NO-FinnNeural",
                                language_code="no",
                            )
                            for _ in range(24)
                        ],
                    )
                ],
            )
        )

        class _Lemmatizer:
            """Deterministic stand-in: one analysis per whitespace token."""

            def analyze_sentence(self, sentence, language_code):
                return [TokenAnalysis(surface=t, lemma=t, upos="NOUN") for t in sentence.split()]

        app.state.srs_dbs = {"sl": db_sl, "no": db_no}
        app.state.content_stores = {"sl": ContentStore(":memory:"), "no": store_no}
        app.state.languages = {"sl": get_language("sl"), "no": get_language("no")}
        # The singular attributes bind to the DEFAULT language (sl) — exactly
        # the startup state that made the bug reachable.
        app.state.srs_db = db_sl
        app.state.content_store = app.state.content_stores["sl"]
        app.state.language = get_language("sl")
        app.state.story_generator = generator
        app.state.lemmatizer = _Lemmatizer()
        app.state.model_version = "test-v1"
        try:
            yield db_sl, db_no
        finally:
            db_sl.close()
            db_no.close()
            for attr in (
                "srs_dbs",
                "content_stores",
                "languages",
                "srs_db",
                "content_store",
                "language",
                "story_generator",
                "lemmatizer",
                "model_version",
            ):
                if hasattr(app.state, attr):
                    delattr(app.state, attr)

    async def test_annotation_lands_in_the_requested_languages_db(self, two_language_generate_app, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "target_language", "sl")
        db_sl, db_no = two_language_generate_app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "c-no", "day": 1, "strategy": "WIDER"},
                headers={"X-TT-Language": "no"},
            )
        assert resp.status_code == 201, resp.text

        # The Norwegian request must cache its analysis in the Norwegian db...
        assert db_no.get_sentence_analysis(self.SENTENCE, "no", "test-v1") is not None
        # ...and must not touch the default (Slovene) one. This is the assertion
        # that fails before the fix: the annotation went to app.state.srs_db.
        assert db_sl.get_sentence_analysis(self.SENTENCE, "no", "test-v1") is None

    async def test_import_lands_in_the_requested_languages_db(self, two_language_generate_app, monkeypatch):
        """The SAME defect existed at a SECOND call site — /import, not just
        /generate — and both were fixed together.

        ⚠️ This test exists because a guard on one path leaves the other broken
        forever with no error. `annotate_chunk_upos_for_lesson` is called from
        BOTH handlers; a single-path test cannot tell you the other one still
        reads app.state. That is the same shape as tunatale-fgeq's M1 note.
        """
        from app.config import settings

        monkeypatch.setattr(settings, "target_language", "sl")
        db_sl, db_no = two_language_generate_app

        story = {
            "title": "Day 1",
            "key_phrases": [{"phrase": self.SENTENCE, "translation": "how are you"}],
            "scenes": [
                {
                    "label": "Cafe",
                    "lines": [{"speaker": "male-1", "text": self.SENTENCE, "translation": "how are you"}],
                }
            ],
            "dialogue_glosses": [],
            "morphology_focus": [],
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/story/import",
                json={"curriculum_id": "c-no", "day": 1, "story": story},
                headers={"X-TT-Language": "no"},
            )
        assert resp.status_code == 201, resp.text

        assert db_no.get_sentence_analysis(self.SENTENCE, "no", "test-v1") is not None
        assert db_sl.get_sentence_analysis(self.SENTENCE, "no", "test-v1") is None
