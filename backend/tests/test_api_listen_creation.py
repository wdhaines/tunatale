"""Tests for listen creation endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
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

    async def test_listen_no_longer_creates_key_phrase_cards(self):
        """Untracked key phrases are skipped; only lemmas are created."""
        db = self._setup(
            self._lesson(
                ["banka banka banka"],
                key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
            )
        )
        db.set_anki_state_cache("daily_new_cap", "10")

        data = await self._listen()

        assert db.get_collocation("dober dan") is None
        assert data["created"] == 1
        assert db.get_collocation_by_lemma("banka") is not None

    async def test_remaining_candidates_counts_only_untracked_lemmas(self):
        """remaining_candidates excludes untracked key phrases."""
        db = self._setup(
            self._lesson(
                ["banka banka banka"],
                key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
            )
        )
        db.set_anki_state_cache("daily_new_cap", "10")

        data = await self._listen()

        assert data["remaining_candidates"] == 0

    async def test_already_tracked_key_phrase_still_graded_when_budget_zero(self):
        """An ALREADY-tracked key-phrase card is still auto-graded by a listen."""
        from app.models.syntactic_unit import SyntacticUnit

        db = self._setup(
            self._lesson(
                [],
                key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
            )
        )
        unit = SyntacticUnit(
            text="dober dan",
            translation="good day",
            word_count=2,
            difficulty=1,
            source="test",
        )
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions.get(Direction.RECOGNITION)
        assert rec is not None
        rec.state = SRSState.LEARNING
        rec.reps = 1
        db.update_collocation(item)

        db.set_anki_state_cache("daily_new_cap", "0")

        data = await self._listen()

        assert data["created"] == 0
        item = db.get_collocation("dober dan")
        rec = item.directions[Direction.RECOGNITION]
        assert rec.reps == 2

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

        assert data["created"] == 1
        assert db.count_collocations() == 1


class TestListenReviewCap:
    """Step B: /listen respects the daily review cap for due-today cards;
    future-due cards are graded as review-ahead (revlog kind 3)."""

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

    async def test_budget_caps_due_today_grades(self):
        """With daily_review_cap=2 and 4 due-today tracked review cards, exactly 2 are graded."""
        from datetime import timedelta

        db = self._setup(self._lesson(["banka center hotel kava"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        for text in ("banka", "center", "hotel", "kava"):
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            item = db.get_collocation(text)
            rec = item.directions[Direction.RECOGNITION]
            rec.state = SRSState.REVIEW
            rec.last_review = datetime.now(UTC) - timedelta(days=10)
            rec.due_at = datetime.now(UTC) - timedelta(days=1)
            rec.reps = 5
            db.update_collocation(item)

        db.set_anki_state_cache("daily_review_cap", "2")

        data = await self._listen()

        assert data["graded"] == 2

        def _revlog_rows(text):
            item = db.get_collocation(text)
            with db._get_conn() as conn:
                return conn.execute(
                    "SELECT review_kind FROM tt_revlog WHERE collocation_id = ?",
                    (db.get_collocation_id_by_guid(item.guid),),
                ).fetchall()

        # The first two in iteration order (lemma first-appearance) get one
        # kind-1 row each; the two beyond the budget are skipped entirely —
        # no revlog row, state and due_at untouched, so they stay due for
        # the lesson review queue ("Check your work").
        for text in ("banka", "center"):
            rows = _revlog_rows(text)
            assert len(rows) == 1
            assert rows[0]["review_kind"] == 1
        for text in ("hotel", "kava"):
            assert _revlog_rows(text) == []
            rec = db.get_collocation(text).directions[Direction.RECOGNITION]
            assert rec.state == SRSState.REVIEW
            assert rec.due_at < datetime.now(UTC)
            assert rec.reps == 5

    async def test_ahead_grades_unlimited_with_kind_3(self):
        """Future-due review cards are graded even when budget is 0, with review_kind=3."""
        from datetime import timedelta

        db = self._setup(self._lesson(["banka center"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        for text in ("banka", "center"):
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            item = db.get_collocation(text)
            rec = item.directions[Direction.RECOGNITION]
            rec.state = SRSState.REVIEW
            rec.last_review = datetime.now(UTC) - timedelta(days=10)
            rec.due_at = datetime.now(UTC) + timedelta(days=5)
            rec.reps = 5
            db.update_collocation(item)

        db.set_anki_state_cache("daily_review_cap", "0")

        data = await self._listen()

        assert data["graded"] == 2
        for text in ("banka", "center"):
            item = db.get_collocation(text)
            with db._get_conn() as conn:
                rows = conn.execute(
                    "SELECT review_kind, factor FROM tt_revlog WHERE collocation_id = ?",
                    (db.get_collocation_id_by_guid(item.guid),),
                ).fetchall()
            assert len(rows) >= 1
            assert rows[-1]["review_kind"] == 3
            assert rows[-1]["factor"] > 0

    async def test_learning_card_graded_when_budget_zero(self):
        """LEARNING-state cards are always graded, even when budget is 0."""
        db = self._setup(self._lesson(["banka"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.LEARNING
        rec.reps = 1
        db.update_collocation(item)

        db.set_anki_state_cache("daily_review_cap", "0")

        data = await self._listen()

        assert data["graded"] == 1
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].reps == 2

    async def test_same_day_relisten_grades_zero(self):
        """Same-day re-listen grades nothing (once-per-day window)."""
        from datetime import timedelta

        db = self._setup(self._lesson(["banka"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(days=10)
        rec.reps = 5
        db.update_collocation(item)

        db.set_anki_state_cache("daily_review_cap", "100")

        first = await self._listen()
        assert first["graded"] == 1

        second = await self._listen()
        assert second["graded"] == 0

    async def _queue_stats(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        return resp.json()

    async def test_ahead_grade_moves_due_out_and_charges_no_counter(self):
        """An ahead grade reschedules the card further out but charges neither
        TT's completed-today counter nor the queue-stats badges (kind-3 rows
        are excluded from count_reviews_completed_today's kind IN (0,1,2))."""
        from datetime import timedelta

        from app.srs.anki_mirror.rollover import anki_today

        db = self._setup(self._lesson(["banka"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(days=10)
        rec.due_at = datetime.now(UTC) + timedelta(days=5)
        rec.stability = 9.0
        rec.reps = 5
        db.update_collocation(item)
        due_before = rec.due_at

        db.set_anki_state_cache("daily_review_cap", "0")
        badges_before = await self._queue_stats()

        data = await self._listen()

        assert data["graded"] == 1
        rec = db.get_collocation("banka").directions[Direction.RECOGNITION]
        assert rec.due_at > due_before
        assert db.count_reviews_completed_today(anki_today()) == 0
        badges_after = await self._queue_stats()
        for key in ("new", "learning", "review"):
            assert badges_after[key] == badges_before[key]

    async def test_field_regression_badge_never_below_budget_minus_graded(self):
        """Field pin (observed 2026-07-17, all-zero badges): a listen may drive
        the review badge down only by the due cards it actually graded — never
        further. With due cards ≥ remaining budget the post-listen badge equals
        previous_budget − graded_due (here 0: BY DESIGN), while the skipped due
        cards stay due for Check your work. Ahead grades move nothing."""
        from datetime import timedelta

        from app.srs.anki_mirror.rollover import anki_today

        db = self._setup(self._lesson(["banka center hotel kava mesto"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        from app.models.syntactic_unit import SyntacticUnit

        for text, due_delta in (
            ("banka", -1),
            ("center", -1),
            ("hotel", -1),
            ("kava", -1),
            ("mesto", +5),
        ):
            unit = SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            item = db.get_collocation(text)
            rec = item.directions[Direction.RECOGNITION]
            rec.state = SRSState.REVIEW
            rec.last_review = datetime.now(UTC) - timedelta(days=10)
            rec.due_at = datetime.now(UTC) + timedelta(days=due_delta)
            rec.reps = 5
            db.update_collocation(item)

        db.set_anki_state_cache("daily_review_cap", "2")

        previous_budget = (await self._queue_stats())["review"]
        assert previous_budget == 2  # min(4 due, budget 2)

        data = await self._listen()
        assert data["graded"] == 3  # 2 due (budget) + 1 ahead

        graded_due = db.count_reviews_completed_today(anki_today())
        assert graded_due == 2  # the ahead grade charged nothing
        badge_after = (await self._queue_stats())["review"]
        assert badge_after >= previous_budget - graded_due
        assert badge_after == 0  # due (2 skipped) ≥ remaining budget (0) → all-zero is by design
        assert db.count_review_due_collocations(anki_today()) == 2  # skipped cards still due

    async def test_skipped_due_cards_surface_in_lesson_review_queue(self):
        """Due cards skipped for budget stay due and appear in the lesson
        review queue, so "Check your work" picks them up."""
        from datetime import timedelta

        db = self._setup(self._lesson(["banka center hotel"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        from app.models.syntactic_unit import SyntacticUnit

        for text in ("banka", "center", "hotel"):
            unit = SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            item = db.get_collocation(text)
            rec = item.directions[Direction.RECOGNITION]
            rec.state = SRSState.REVIEW
            rec.last_review = datetime.now(UTC) - timedelta(days=10)
            rec.due_at = datetime.now(UTC) - timedelta(days=1)
            rec.reps = 5
            db.update_collocation(item)

        db.set_anki_state_cache("daily_review_cap", "1")

        data = await self._listen()
        assert data["graded"] == 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/lesson/lesson-1/review-queue")
        assert resp.status_code == 200
        texts = {q["text"] for q in resp.json()["queue"]}
        assert {"center", "hotel"} <= texts
