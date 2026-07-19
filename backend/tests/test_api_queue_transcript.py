"""Tests for queue stats + transcript endpoints."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


class TestQueueStatsEndpoint:
    """Tests for GET /api/srs/queue-stats."""

    async def test_queue_stats_returns_200_with_shape(self):
        from unittest.mock import patch

        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        with patch("app.api.srs.resolve_daily_new_cap", return_value=(20, "default")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        assert response.status_code == 200
        data = response.json()
        assert "new" in data
        assert "learning" in data
        assert "review" in data
        assert "due" not in data
        assert "daily_new_cap" in data
        assert "cap_source" in data

    async def test_queue_stats_new_is_clamped_at_cap(self):
        from unittest.mock import patch

        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        for i in range(5):
            db.add_collocation(
                SyntacticUnit(text=f"word{i}", translation="t", word_count=1, difficulty=1, source="corpus"),
                language_code="sl",
            )
        app.state.srs_db = db

        with patch("app.api.srs.resolve_daily_new_cap", return_value=(3, "default")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        data = response.json()
        assert data["new"] == 3
        assert data["daily_new_cap"] == 3
        assert data["cap_source"] == "default"

    async def test_queue_stats_cap_source_from_anki(self):
        from unittest.mock import patch

        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        with patch("app.api.srs.resolve_daily_new_cap", return_value=(30, "anki")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        data = response.json()
        assert data["cap_source"] == "anki"
        assert data["daily_new_cap"] == 30

    async def test_queue_stats_review_uses_tt_distinct_collocation_count(self):
        """Review badge is driven by TT's distinct-collocation count
        (sibling-buried equivalent of Anki's COUNT(DISTINCT nid))."""
        from unittest.mock import patch

        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        with (
            patch("app.api.srs.resolve_daily_new_cap", return_value=(20, "default")),
            patch.object(db, "count_review_due_collocations", return_value=42),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        data = response.json()
        assert data["review"] == 42

    async def test_queue_stats_review_budget_excludes_new_introduced_today(self):
        """Regression (Layer 76): new cards introduced today consume the review
        daily limit, so the review badge subtracts count_new_introduced_today.

        Anki charges today's new-card intros against reviews_per_day
        (rslib/decks/limits.rs:104-108). Before the fix TT ignored this term and
        over-counted the review badge by introduced_today — the exact symptom of
        "create a new card in TT, study it, sync, review counts don't match."

        review_cap=50, reviews_today=0, introduced_today=3, review_due_raw=60 →
        review = min(60, 50 - 0 - 3) = 47 (was 50 before the fix).
        """
        from unittest.mock import patch

        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        db.set_anki_state_cache("daily_review_cap", "50")
        db.set_anki_state_cache("daily_new_cap", "20")
        app.state.srs_db = db

        with (
            patch.object(db, "count_review_due_collocations", return_value=60),
            patch.object(db, "count_reviews_completed_today", return_value=0),
            patch.object(db, "count_new_introduced_today", return_value=3),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        data = response.json()
        assert data["review"] == 47, f"expected 50 - 3 introduced = 47, got {data['review']}"


class TestTranscriptEndpoint:
    """Tests for GET /api/srs/lesson/{lesson_id}/transcript."""

    async def test_returns_200_with_correct_shape(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Zdravo.", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="Zdravo", translation="Hello")],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/lesson-1/transcript")

        assert response.status_code == 200
        data = response.json()
        assert data["lesson_id"] == "lesson-1"
        assert isinstance(data["key_phrases"], list)
        assert isinstance(data["dialogue_lines"], list)

    async def test_returns_404_for_missing_lesson(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        app.state.srs_db = SRSDatabase(":memory:")
        app.state.content_store = ContentStore(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/nonexistent/transcript")

        assert response.status_code == 404

    async def test_threads_lesson_language_to_lemmatizer(self, monkeypatch):
        """Guardrail (item #25): the transcript endpoint resolves the lemmatizer for
        the LESSON's language, not a process-wide default — so a Norwegian lesson is
        analyzed with the Norwegian engine even when both languages share one process.
        """
        import app.api.srs as srs_mod
        from app.srs.database import SRSDatabase
        from app.srs.lemmatizer import LowercaseLemmatizer
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Dag 1",
            language_code="no",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Hei.", voice_id="female-1", language_code="no", role="female-1")],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-no", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        captured: list[str] = []

        def _spy(code: str):
            captured.append(code)
            return LowercaseLemmatizer()

        monkeypatch.setattr(srs_mod, "get_lemmatizer", _spy)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/lesson-no/transcript")

        assert response.status_code == 200
        assert captured == ["no"], f"expected the lemmatizer resolved for 'no', got {captured}"

    async def test_l2_filter_excludes_english_narrator(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Scene: At the market", voice_id="narrator", language_code="en", role="narrator"),
                        Phrase(text="Zdravo.", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/lesson-1/transcript")

        data = response.json()
        assert len(data["dialogue_lines"]) == 1
        assert data["dialogue_lines"][0]["role"] == "female-1"

    async def test_known_word_has_correct_srs_state(self):
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        db.add_collocation(unit, language_code="sl")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/lesson-1/transcript")

        data = response.json()
        word = data["dialogue_lines"][0]["words"][0]
        assert word["srs_state"] == "new"
        assert word["lemma"] == "banka"
        assert word["surface"] == "banka"

    async def test_transcript_includes_recognition_fields(self):
        """Payload must include recognition_state and recognition_is_due for
        every word, enabling frontend recognition-based bucketing."""
        from datetime import UTC, datetime

        from app.models.srs_item import Direction, SRSState
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        db.add_collocation(unit, language_code="sl")
        # Set recognition to REVIEW, due in past
        item = db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.due_at = datetime(2026, 5, 1, tzinfo=UTC)
        rec.last_review = datetime(2026, 5, 1, tzinfo=UTC)
        db.update_direction(item.guid, Direction.RECOGNITION, rec)

        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/lesson-1/transcript")

        data = response.json()
        word = data["dialogue_lines"][0]["words"][0]
        assert "recognition_state" in word
        assert "recognition_is_due" in word
        assert word["recognition_state"] == "review"
        assert word["recognition_is_due"] is True

    async def test_transcript_unknown_word_recognition_fields(self):
        """Untracked word: recognition_state None, recognition_is_due False."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="xyzword", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/lesson-1/transcript")

        data = response.json()
        word = data["dialogue_lines"][0]["words"][0]
        assert word["recognition_state"] is None
        assert word["recognition_is_due"] is False
