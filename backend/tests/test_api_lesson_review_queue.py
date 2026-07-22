"""Tests for lesson review queue endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


class TestLessonReviewQueue:
    """GET /api/srs/lesson/{lesson_id}/review-queue — lesson-scoped "Check your
    work" queue (plan Step 4 / D6).

    Include: learning/relearning; tracked NEW in D2 rank order; REVIEW touched
    today (the auto-Good correction set) or due. Exclude known/suspended/
    buried/untracked and REVIEW untouched+future-due. Vocab serves recognition
    only; cloze serves production only. Strictly read-only w.r.t. parity state."""

    def _lesson(self, phrases, key_phrases=None):
        return Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text=t, voice_id="female-1", language_code="sl", role="female-1") for t in phrases],
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

    def _track(self, db, text, card_type="vocab"):
        from app.models.syntactic_unit import SyntacticUnit

        db.add_collocation(
            SyntacticUnit(
                text=text,
                translation="x",
                word_count=len(text.split()),
                difficulty=1,
                source="llm",
                card_type=card_type,
            ),
            language_code="sl",
        )

    def _set_dir(self, db, text, direction, state, due_at=None, last_review=None):
        item = db.get_collocation(text)
        assert item is not None, f"collocation {text!r} not tracked"
        dir_ = Direction(direction)
        ds = item.directions[dir_]
        ds.state = SRSState(state)
        if due_at is not None:
            ds.due_at = datetime.fromisoformat(due_at)
        if last_review is not None:
            ds.last_review = datetime.fromisoformat(last_review)
        db.update_direction(item.guid, dir_, ds)

    async def _get_queue(self, lesson_id="lesson-1"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(f"/api/srs/lesson/{lesson_id}/review-queue")

    async def test_404_unknown_lesson(self):
        self._setup(self._lesson(["banka"]))
        resp = await self._get_queue("no-such-lesson")
        assert resp.status_code == 404

    async def test_empty_queue_when_all_words_untracked(self):
        self._setup(self._lesson(["banka center"]))
        resp = await self._get_queue()
        assert resp.status_code == 200
        assert resp.json()["queue"] == []

    async def test_learning_vocab_served_recognition_only(self):
        db = self._setup(self._lesson(["banka"]))
        self._track(db, "banka")
        self._set_dir(db, "banka", "recognition", "learning")

        resp = await self._get_queue()

        queue = resp.json()["queue"]
        assert len(queue) == 1
        assert queue[0]["text"] == "banka"
        assert queue[0]["direction"] == "recognition"
        assert queue[0]["state"] == "learning"

    async def test_relearning_cloze_served_production(self):
        db = self._setup(self._lesson(["banka"]))
        self._track(db, "banka", card_type="cloze")
        self._set_dir(db, "banka", "production", "relearning")

        resp = await self._get_queue()

        queue = resp.json()["queue"]
        assert len(queue) == 1
        assert queue[0]["direction"] == "production"
        assert queue[0]["state"] == "relearning"

    async def test_item_shape_matches_main_review_queue(self):
        db = self._setup(self._lesson(["banka"]))
        self._track(db, "banka")
        self._set_dir(db, "banka", "recognition", "learning")

        lesson_resp = await self._get_queue()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            main_resp = await client.get("/api/srs/review-queue")

        lesson_item = lesson_resp.json()["queue"][0]
        main_item = main_resp.json()["queue"][0]
        assert set(lesson_item.keys()) == set(main_item.keys())

    async def test_new_cards_in_d2_rank_order(self):
        # banka appears twice, center once; key phrase outranks both.
        db = self._setup(
            self._lesson(
                ["banka center banka"],
                key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
            )
        )
        self._track(db, "dober dan")
        self._track(db, "banka")
        self._track(db, "center")

        resp = await self._get_queue()

        texts = [i["text"] for i in resp.json()["queue"]]
        assert texts == ["dober dan", "banka", "center"]

    async def test_review_touched_today_included(self):
        db = self._setup(self._lesson(["banka"]))
        self._track(db, "banka")
        self._set_dir(
            db,
            "banka",
            "recognition",
            "review",
            due_at="2027-01-01T04:00:00+00:00",
            last_review=datetime.now(UTC).isoformat(),
        )

        resp = await self._get_queue()

        assert [i["text"] for i in resp.json()["queue"]] == ["banka"]

    async def test_review_due_but_untouched_included(self):
        db = self._setup(self._lesson(["banka"]))
        self._track(db, "banka")
        self._set_dir(
            db,
            "banka",
            "recognition",
            "review",
            due_at="2026-01-01T04:00:00+00:00",
            last_review="2026-01-01T00:00:00+00:00",
        )

        resp = await self._get_queue()

        assert [i["text"] for i in resp.json()["queue"]] == ["banka"]

    async def test_review_untouched_future_due_excluded(self):
        db = self._setup(self._lesson(["banka"]))
        self._track(db, "banka")
        self._set_dir(
            db,
            "banka",
            "recognition",
            "review",
            due_at="2027-01-01T04:00:00+00:00",
            last_review="2026-01-01T00:00:00+00:00",
        )

        resp = await self._get_queue()

        assert resp.json()["queue"] == []

    async def test_known_suspended_buried_and_untracked_excluded(self):
        db = self._setup(self._lesson(["banka center hotel kava"]))
        self._track(db, "banka")
        self._set_dir(db, "banka", "recognition", "known")
        self._track(db, "center")
        self._set_dir(db, "center", "recognition", "suspended")
        self._track(db, "hotel")
        self._set_dir(db, "hotel", "recognition", "buried")
        # kava stays untracked.

        resp = await self._get_queue()

        assert resp.json()["queue"] == []

    async def test_bucket_order_learning_then_new_then_review(self):
        db = self._setup(self._lesson(["hotel banka center"]))
        self._track(db, "hotel")
        self._set_dir(db, "hotel", "recognition", "learning")
        self._track(db, "banka")  # stays NEW
        self._track(db, "center")
        self._set_dir(
            db,
            "center",
            "recognition",
            "review",
            due_at="2027-01-01T04:00:00+00:00",
            last_review=datetime.now(UTC).isoformat(),
        )

        resp = await self._get_queue()

        assert [i["text"] for i in resp.json()["queue"]] == ["hotel", "banka", "center"]

    async def test_parity_guard_endpoint_is_read_only(self):
        """The lesson queue writes neither learning_cutoff nor session_main_queue,
        and the frozen main-queue order survives it unchanged (plan callout #3)."""
        db = self._setup(self._lesson(["banka center"]))
        for text in ("banka", "center"):
            self._track(db, text)
            self._set_dir(
                db,
                text,
                "recognition",
                "review",
                due_at="2026-01-01T04:00:00+00:00",
                last_review="2026-01-01T00:00:00+00:00",
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            frozen = await client.get("/api/srs/review-queue?session_start=1")
        order_before = [(i["id"], i["direction"]) for i in frozen.json()["queue"]]
        cutoff_before = db.get_anki_state_cache("learning_cutoff")
        cache_before = db.get_anki_state_cache("session_main_queue")

        resp = await self._get_queue()
        assert resp.status_code == 200

        assert db.get_anki_state_cache("learning_cutoff") == cutoff_before
        assert db.get_anki_state_cache("session_main_queue") == cache_before
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            after = await client.get("/api/srs/review-queue")
        assert [(i["id"], i["direction"]) for i in after.json()["queue"]] == order_before

    async def test_again_after_auto_good_is_normal_lapse(self):
        """Listen auto-Goods a REVIEW card; grading it Again from the lesson queue
        via the normal feedback endpoint is an ordinary same-day lapse: state →
        relearning, revlog logs button=1 from the pre-answer REVIEW state
        (review_kind=1), no revlog rewriting."""
        db = self._setup(self._lesson(["banka"]))
        self._track(db, "banka")
        self._set_dir(
            db,
            "banka",
            "recognition",
            "review",
            due_at="2026-01-01T04:00:00+00:00",
            last_review="2026-01-01T00:00:00+00:00",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            listen = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert listen.json()["graded"] == 1

        resp = await self._get_queue()
        queue = resp.json()["queue"]
        assert [i["text"] for i in queue] == ["banka"]
        item_id = queue[0]["id"]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            graded = await client.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"rating": "again"},
            )
        assert graded.json()["new_state"] == "relearning"
        with db._get_conn() as conn:
            row = conn.execute(
                "SELECT button_chosen, review_kind FROM tt_revlog WHERE collocation_id=? ORDER BY id DESC LIMIT 1",
                (item_id,),
            ).fetchone()
        assert row["button_chosen"] == 1
        assert row["review_kind"] == 1

    async def test_key_phrase_edge_cases_untracked_learning_duplicate(self):
        """Untracked key phrases are skipped, a learning key phrase lands in the
        learning bucket (not the NEW ranking), and a duplicate key phrase entry
        doesn't produce a duplicate item."""
        db = self._setup(
            self._lesson(
                [],
                key_phrases=[
                    KeyPhraseInfo(phrase="dober dan", translation="good day"),
                    KeyPhraseInfo(phrase="dober dan", translation="good day"),
                    KeyPhraseInfo(phrase="na svidenje", translation="goodbye"),
                    KeyPhraseInfo(phrase="prosim kavo", translation="a coffee please"),
                ],
            )
        )
        self._track(db, "dober dan")
        self._set_dir(db, "dober dan", "recognition", "learning")
        self._track(db, "prosim kavo")  # stays NEW

        resp = await self._get_queue()

        assert [i["text"] for i in resp.json()["queue"]] == ["dober dan", "prosim kavo"]

    async def test_lemma_resolving_to_key_phrase_card_not_duplicated(self):
        """A single-word key phrase and the same word in the phrase text resolve
        to one card — served once (key-phrase pass wins, lemma pass sees it)."""
        db = self._setup(self._lesson(["banka"], key_phrases=[KeyPhraseInfo(phrase="banka", translation="bank")]))
        self._track(db, "banka")
        self._set_dir(db, "banka", "recognition", "learning")

        resp = await self._get_queue()

        assert [i["text"] for i in resp.json()["queue"]] == ["banka"]

    async def test_card_without_served_direction_excluded(self):
        """Single-template rows (no recognition direction after v15→v16) can't be
        served recognition — excluded rather than crashing."""
        db = self._setup(self._lesson(["banka"]))
        self._track(db, "banka")
        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE direction='recognition'"
                " AND collocation_id=(SELECT id FROM collocations WHERE text='banka')"
            )
            conn.commit()

        resp = await self._get_queue()

        assert resp.json()["queue"] == []


class TestReviewQueueTouchedTodayUsesAnkiRollover:
    """Regression (docs/master-cleanup-list-2026-07.md item 1): the lesson
    review-queue's "touched today" REVIEW bucketing (get_lesson_review_queue's
    `_classify`) must use the same Anki-day-rollover window as
    mark_lesson_listened's grade-eligibility check, not local midnight.

    `_classify` is a route-local closure, so this test mirrors its exact
    comparison (`today_start <= lr.astimezone(UTC) < today_end`) against the
    window the shared rollover helper produces — the same technique
    test_fsrs.py's Layer-50 tests use to track internal derivation logic.
    """

    def test_late_evening_review_buckets_as_touched_before_rollover(self):
        from datetime import timedelta

        from app.srs.anki_mirror.rollover import anki_day_bounds_utc_dt, anki_today

        # "now" = 02:00 on day D — inside [midnight, 4 AM), before rollover.
        now = datetime(2026, 5, 8, 2, 0, tzinfo=UTC)
        # Reviewed at 23:00 the prior evening — same active Anki day as `now`.
        last_review = datetime(2026, 5, 7, 23, 0, tzinfo=UTC)

        today = anki_today(now)
        today_start, today_end = anki_day_bounds_utc_dt(today, now)

        touched_today = today_start <= last_review.astimezone(UTC) < today_end
        assert touched_today is True, (
            "a review graded late the prior evening is still 'today' by "
            "Anki's 4 AM rollover and must bucket as touched-today"
        )

        # Sanity: the OLD local-midnight window would have excluded this
        # last_review, proving the scenario actually distinguishes the two
        # conventions (a revert to date.today()-keyed midnight flips this).
        old_today_start = datetime(2026, 5, 8, 0, 0, tzinfo=UTC)
        old_today_end = old_today_start + timedelta(days=1)
        old_touched_today = old_today_start <= last_review.astimezone(UTC) < old_today_end
        assert old_touched_today is False


class TestMarkLessonReviewed:
    """POST /api/srs/lesson/{lesson_id}/reviewed — records a review row for
    the one-shot-per-listen gate."""

    def _lesson(self):
        return Lesson(
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

    def _setup(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, self._lesson())
        app.state.srs_db = db
        app.state.content_store = store
        return db

    async def test_404_unknown_lesson(self):
        self._setup()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/lesson/no-such-lesson/reviewed")
            assert resp.status_code == 404

    async def test_200_and_row_recorded(self):
        db = self._setup()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/lesson/lesson-1/reviewed")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}
        assert db.latest_review_at("lesson-1") is not None
