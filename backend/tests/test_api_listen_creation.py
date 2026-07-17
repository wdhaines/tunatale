"""Tests for listen creation endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


class TestRankListenCandidates:
    """_rank_listen_candidates (plan D2): untracked key phrases first (lesson
    order), then untracked lemmas by in-lesson occurrence count descending,
    ties broken by first appearance (the order of the input list)."""

    def test_key_phrases_before_lemmas(self):
        from app.api.srs import _rank_listen_candidates

        ranked = _rank_listen_candidates(["kp1", "kp2"], ["a", "b"], {"a": 5, "b": 1})
        assert ranked == [("kp", "kp1"), ("kp", "kp2"), ("lemma", "a"), ("lemma", "b")]

    def test_lemmas_sorted_by_occurrence_desc(self):
        from app.api.srs import _rank_listen_candidates

        ranked = _rank_listen_candidates([], ["a", "b", "c"], {"a": 1, "b": 3, "c": 2})
        assert ranked == [("lemma", "b"), ("lemma", "c"), ("lemma", "a")]

    def test_ties_keep_first_appearance_order(self):
        from app.api.srs import _rank_listen_candidates

        ranked = _rank_listen_candidates([], ["x", "y", "z"], {"x": 2, "y": 2, "z": 2})
        assert ranked == [("lemma", "x"), ("lemma", "y"), ("lemma", "z")]

    def test_empty_inputs(self):
        from app.api.srs import _rank_listen_candidates

        assert _rank_listen_candidates([], [], {}) == []


class TestListenStagedCreation:
    """Staged, budget-capped creation in POST /api/srs/listen (plan Step 3).

    Budget = max(0, daily_new_cap − introduced_today − created_today_still_new);
    candidates ranked by _rank_listen_candidates. Grading and backfills of
    existing cards stay unconditional (plan D5)."""

    def _lesson(self, phrases, key_phrases=None, language_code="sl"):
        return Lesson(
            title="Day 1",
            language_code=language_code,
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text=t, voice_id="female-1", language_code=language_code, role="female-1")
                        for t in phrases
                    ],
                )
            ],
            key_phrases=key_phrases or [],
        )

    def _setup(self, lesson):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store
        return db

    async def _listen(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert resp.status_code == 200
        return resp.json()

    async def test_budget_caps_creation_to_highest_frequency_lemmas(self):
        # occurrences: banka 3, center 2, hotel 1
        db = self._setup(self._lesson(["banka center hotel", "banka center", "banka"]))
        db.set_anki_state_cache("daily_new_cap", "2")

        data = await self._listen()

        assert set(data) == {"status", "registered", "created", "graded", "remaining_candidates", "listen_count"}
        assert data["status"] == "ok"
        assert data["created"] == 2
        assert data["graded"] == 0
        assert data["registered"] == data["created"] + data["graded"]
        assert data["remaining_candidates"] == 1
        assert data["listen_count"] == 1
        assert db.get_collocation_by_lemma("banka") is not None
        assert db.get_collocation_by_lemma("center") is not None
        assert db.get_collocation_by_lemma("hotel") is None

    async def test_key_phrases_outrank_lemmas_under_tight_budget(self):
        db = self._setup(
            self._lesson(
                ["banka banka banka"],
                key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
            )
        )
        db.set_anki_state_cache("daily_new_cap", "1")

        data = await self._listen()

        assert data["created"] == 1
        assert db.get_collocation("dober dan") is not None
        assert db.get_collocation_by_lemma("banka") is None

    async def test_same_day_relisten_creates_zero(self):
        db = self._setup(self._lesson(["banka center hotel kava mesto"]))
        db.set_anki_state_cache("daily_new_cap", "2")

        first = await self._listen()
        assert first["created"] == 2
        assert first["listen_count"] == 1

        second = await self._listen()
        assert second["created"] == 0
        assert second["listen_count"] == 2
        assert second["remaining_candidates"] == 3
        assert db.count_collocations() == 2

    async def test_introduced_today_netting_shrinks_budget(self):
        from app.models.syntactic_unit import SyntacticUnit

        db = self._setup(self._lesson(["banka center hotel kava mesto"]))
        db.set_anki_state_cache("daily_new_cap", "3")
        # One collocation introduced today (left NEW, introduced_at stamped):
        # charges the budget via introduced_today, not created_today_still_new.
        db.add_collocation(
            SyntacticUnit(text="stara", translation="old", word_count=1, difficulty=1, source="llm"),
            language_code="sl",
        )
        now_iso = datetime.now(UTC).isoformat()
        with db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET state='learning', introduced_at=?"
                " WHERE collocation_id=(SELECT id FROM collocations WHERE text='stara')",
                (now_iso,),
            )
            conn.commit()

        data = await self._listen()

        assert data["created"] == 2

    async def test_fully_acquired_lesson_creates_and_remains_zero(self):
        db = self._setup(self._lesson(["banka center"]))
        db.set_anki_state_cache("daily_new_cap", "10")

        first = await self._listen()
        assert first["created"] == 2

        second = await self._listen()
        assert second["created"] == 0
        assert second["remaining_candidates"] == 0
        assert second["listen_count"] == 2

    async def test_backfills_still_run_at_budget_zero(self):
        from app.storage.store import ContentStore

        db = self._setup(self._lesson(["Kje je banka?"]))
        db.set_anki_state_cache("daily_new_cap", "10")
        await self._listen()
        kje = db.get_collocation_by_lemma("kje")
        assert kje is not None
        assert kje.syntactic_unit.source_sentence_translation == ""

        db.set_anki_state_cache("daily_new_cap", "0")
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.generation_metadata = {"sentence_translations": {"Kje je banka?": "Where is the bank?"}}
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        data = await self._listen()

        assert data["created"] == 0
        kje = db.get_collocation_by_lemma("kje")
        assert kje.syntactic_unit.source_sentence_translation == "Where is the bank?"

    async def test_every_listen_appends_source_listen_row(self):
        db = self._setup(self._lesson(["banka"]))

        await self._listen()
        await self._listen()

        assert db.count_listens("lesson-1") == 2
        with db._get_conn() as conn:
            rows = conn.execute("SELECT source FROM lesson_listens WHERE lesson_id='lesson-1'").fetchall()
        assert [r["source"] for r in rows] == ["listen", "listen"]

    async def test_404_records_no_listen_row(self):
        db = self._setup(self._lesson(["banka"]))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": "no-such-lesson"})

        assert resp.status_code == 404
        assert db.count_listens("no-such-lesson") == 0

    async def test_single_word_key_phrase_shared_with_lemma_not_double_created(self):
        db = self._setup(self._lesson(["banka"], key_phrases=[KeyPhraseInfo(phrase="banka", translation="bank")]))
        db.set_anki_state_cache("daily_new_cap", "10")

        data = await self._listen()

        assert data["created"] == 1
        assert db.count_collocations() == 1

    async def test_duplicate_key_phrase_not_double_created(self):
        db = self._setup(
            self._lesson(
                ["banka"],
                key_phrases=[
                    KeyPhraseInfo(phrase="dober dan", translation="good day"),
                    KeyPhraseInfo(phrase="dober dan", translation="good day"),
                ],
            )
        )
        db.set_anki_state_cache("daily_new_cap", "10")

        data = await self._listen()

        assert data["created"] == 2
        assert db.count_collocations() == 2
