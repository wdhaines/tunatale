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

        assert set(data) == {
            "status",
            "staged",
            "applied",
            "created",
            "remaining_candidates",
            "listen_count",
        }
        assert data["status"] == "ok"
        assert data["created"] == 2
        assert data["staged"] == 0
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

    async def test_already_tracked_key_phrase_still_staged_when_budget_zero(self):
        """An ALREADY-tracked key-phrase card is still staged by a listen (no grade)."""
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
        assert data["staged"] == 1
        item = db.get_collocation("dober dan")
        rec = item.directions[Direction.RECOGNITION]
        assert rec.reps == 1  # staging does not grade

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
    """Staging: /listen stages all eligible cards — no listen-time review budget cap."""

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

    async def test_stages_all_eligible_cards_no_budget_cap(self):
        """With daily_review_cap=2 and 4 due-today tracked review cards, all 4 are staged."""
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

        assert data["staged"] == 4
        for text in ("banka", "center", "hotel", "kava"):
            item = db.get_collocation(text)
            coll_id = db.get_collocation_id_by_guid(item.guid)
            with db._get_conn() as conn:
                rows = conn.execute(
                    "SELECT COUNT(*) FROM tt_revlog WHERE collocation_id = ?",
                    (coll_id,),
                ).fetchall()
            assert rows[0][0] == 0  # no revlog rows
            rec = item.directions[Direction.RECOGNITION]
            assert rec.state == SRSState.REVIEW
            assert rec.reps == 5  # FSRS untouched

    async def test_ahead_cards_stage_with_grade_class_ahead(self):
        """Future-due review cards are staged with grade_class='ahead'."""
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

        assert data["staged"] == 2
        for text in ("banka", "center"):
            item = db.get_collocation(text)
            coll_id = db.get_collocation_id_by_guid(item.guid)
            pg = db.get_pending_grade(coll_id, Direction.RECOGNITION.value)
            assert pg is not None
            assert pg["grade_class"] == "ahead"

    async def test_learning_card_stages_when_budget_zero(self):
        """LEARNING-state cards are staged even when budget is 0."""
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

        assert data["staged"] == 1
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].reps == 1  # staging does not grade

    async def test_same_day_relisten_upserts_provisional_grade(self):
        """Same-day re-listen upserts the pending grade (still staged, not applied)."""
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
        assert first["staged"] == 1

        second = await self._listen()
        assert second["staged"] == 1  # upsert, still staged

        rows = db.get_pending_grades("lesson-1")
        assert len(rows) == 1  # upserted, not duplicated

    async def _queue_stats(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        return resp.json()

    async def test_ahead_card_staging_leaves_fsrs_and_badges_unchanged(self):
        """Staging an ahead card does not move due_at or charge counters."""
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

        assert data["staged"] == 1
        rec = db.get_collocation("banka").directions[Direction.RECOGNITION]
        assert rec.due_at == due_before  # FSRS untouched
        assert db.count_reviews_completed_today(anki_today()) == 0
        badges_after = await self._queue_stats()
        for key in ("new", "learning", "review"):
            assert badges_after[key] == badges_before[key]

    async def test_staging_charges_nothing_but_hides_the_staged_cards(self):
        """Staging completes no reviews, yet empties the review pool (stage 3).

        Two separate facts, and the distinction is the whole model: FSRS is
        untouched (``count_reviews_completed_today`` stays 0, no budget charged),
        but the staged cards leave the due pool and the badge, because they are
        now served by "Check your work" instead. Anki, which has no notion of a
        pending grade, keeps counting all 4 — the documented divergence, which
        resolves as each card is applied.
        """
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
        assert previous_budget == 2

        data = await self._listen()
        assert data["staged"] == 5  # all eligible (4 due + 1 ahead)

        graded_due = db.count_reviews_completed_today(anki_today())
        assert graded_due == 0  # nothing applied — no FSRS movement, no budget charged
        # ...but all 4 due cards are now pending, so they leave the review pool.
        assert db.count_review_due_collocations(anki_today()) == 0
        assert (await self._queue_stats())["review"] == 0

        # Releasing one puts it straight back in the pool the badge counts.
        released = db.get_collocation_id_by_guid(db.get_collocation("banka").guid)
        db.clear_pending_grade(released, Direction.RECOGNITION.value)
        assert db.count_review_due_collocations(anki_today()) == 1

    async def test_all_staged_cards_surface_in_lesson_review_queue(self):
        """All staged cards (no cap) remain due in FSRS and appear in Check your work."""
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
        assert data["staged"] == 3

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/lesson/lesson-1/review-queue")
        assert resp.status_code == 200
        texts = {q["text"] for q in resp.json()["queue"]}
        assert {"banka", "center", "hotel"} <= texts
