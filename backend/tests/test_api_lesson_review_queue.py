"""Tests for lesson review queue endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from app.api.models import MarkLessonReviewedResponse
from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


class TestLessonReviewQueue:
    """GET /api/srs/content/{lesson_id}/review-queue — lesson-scoped "Check your
    work" queue.

    Scope narrowed 2026-07-27 to **exactly this lesson's pending_listen_grades
    rows**. It was previously a lesson-scoped study queue (D6 buckets: learning,
    tracked NEW in D2 rank order, REVIEW touched-today or due), which after the
    confirmed/staged split re-served cards the user had just confirmed in the
    preview and surfaced due cloze cards a listen can never autograde. Queue
    membership and what ``commit-pending`` releases now come from one query, so
    they cannot drift.

    Retired with the old scope, each because its premise no longer exists:
      * ``test_new_cards_in_d2_rank_order`` and
        ``test_new_card_unaffected_by_graded_since_listen_filter`` — a NEW card
        can never be staged (``_listen_grade_class`` returns None for NEW), so
        the D2 tap-to-introduce bucket is unreachable.
      * ``test_review_untouched_future_due_excluded`` — dueness no longer gates
        anything; ``TestPendingCardsAreServed.test_a_pending_ahead_card_is_served``
        covers the ahead case that does matter.
      * ``test_key_phrase_edge_cases_untracked_learning_duplicate`` and
        ``test_lemma_resolving_to_key_phrase_card_not_duplicated`` — the endpoint
        no longer walks lesson words or key phrases at all, and the
        one-row-per-card property is now enforced by
        ``pending_listen_grades``'s UNIQUE(collocation_id, direction).
      * ``TestReviewQueueTouchedTodayUsesAnkiRollover`` — mirrored a `_classify`
        closure that no longer exists. The 4 AM rollover convention still matters
        for the *listen* path and stays pinned by
        ``test_rollover_hour_single_source.py`` and the listen suites.
    """

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

    def _stage(self, db, text, direction="recognition", rating="good", grade_class="due", lesson_id="lesson-1"):
        """Put *text* in the lesson's pending bucket — the only way into the queue."""
        item = db.get_collocation(text)
        assert item is not None, f"collocation {text!r} not tracked"
        cid = db.get_collocation_id_by_guid(item.guid)
        assert cid is not None
        db.stage_pending_grade(lesson_id, cid, direction, rating, grade_class)
        return cid

    async def _get_queue(self, lesson_id="lesson-1"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(f"/api/srs/content/{lesson_id}/review-queue")

    async def test_404_unknown_lesson(self):
        self._setup(self._lesson(["banka"]))
        resp = await self._get_queue("no-such-lesson")
        assert resp.status_code == 404

    async def test_empty_queue_when_nothing_is_staged(self):
        db = self._setup(self._lesson(["banka center"]))
        self._track(db, "banka")
        self._set_dir(db, "banka", "recognition", "learning")

        resp = await self._get_queue()

        assert resp.status_code == 200
        assert resp.json()["queue"] == []

    async def test_learning_vocab_served_recognition_only(self):
        db = self._setup(self._lesson(["banka"]))
        self._track(db, "banka")
        self._set_dir(db, "banka", "recognition", "learning")
        self._stage(db, "banka", grade_class="learning")

        resp = await self._get_queue()

        queue = resp.json()["queue"]
        assert len(queue) == 1
        assert queue[0]["text"] == "banka"
        assert queue[0]["direction"] == "recognition"
        assert queue[0]["state"] == "learning"

    async def test_served_direction_comes_from_the_pending_row(self):
        """Direction used to be derived from card_type (cloze ⇒ production).
        It is now whatever the staged row says, so the queue serves exactly the
        direction that was autograded."""
        db = self._setup(self._lesson(["banka"]))
        self._track(db, "banka", card_type="cloze")
        self._set_dir(db, "banka", "production", "relearning")
        self._stage(db, "banka", direction="production", grade_class="learning")

        resp = await self._get_queue()

        queue = resp.json()["queue"]
        assert len(queue) == 1
        assert queue[0]["direction"] == "production"
        assert queue[0]["state"] == "relearning"

    async def test_item_shape_matches_main_review_queue(self):
        """A lesson item must carry everything a main-queue item does, so both
        render through the same component. It may carry MORE: `pending_rating`
        is lesson-only (the main queue never serves a card with a pending listen
        grade — Stage 3 excludes them). A *missing* key is the failure this
        guards; an extra one is not."""
        db = self._setup(self._lesson(["banka", "center"]))
        self._track(db, "banka")
        self._set_dir(db, "banka", "recognition", "learning")
        self._stage(db, "banka", grade_class="learning")
        # A second, unstaged card so the main queue has something to serve.
        self._track(db, "center")
        self._set_dir(db, "center", "recognition", "learning")

        lesson_resp = await self._get_queue()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            main_resp = await client.get("/api/srs/review-queue")

        lesson_item = lesson_resp.json()["queue"][0]
        main_item = main_resp.json()["queue"][0]
        assert set(main_item.keys()) <= set(lesson_item.keys())
        assert set(lesson_item.keys()) - set(main_item.keys()) == {"pending_rating"}

    async def test_response_key_sets_and_nested_direction_left(self):
        """Oracle for the response_model flip (openapi ledger batch 6c).

        Written against the UNFILTERED handler output BEFORE the flip, with
        LITERAL key sets — ``set(Model.model_fields)`` alone would be circular
        once ``response_model=`` filters the payload to the model's own fields.
        """
        from tests._helpers.srs_item_shape import DIRECTION_KEYS, DIRECTION_WITHOUT_LEFT, LESSON_QUEUE_ITEM_KEYS

        db = self._setup(self._lesson(["banka", "center"]))
        self._track(db, "banka")
        self._set_dir(db, "banka", "recognition", "learning")
        # `left` is what makes the nested-direction branch observable: it is the
        # only key `_direction_to_dict` conditionally omits.
        item = db.get_collocation("banka")
        ds = item.directions[Direction.RECOGNITION]
        ds.left = 1001
        db.update_direction(item.guid, Direction.RECOGNITION, ds)
        self._stage(db, "banka", grade_class="learning")
        self._track(db, "center")
        self._set_dir(db, "center", "recognition", "review")
        self._stage(db, "center", grade_class="due")

        resp = await self._get_queue()

        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"queue", "has_unreviewed_listen"}
        assert len(data["queue"]) == 2
        for entry in data["queue"]:
            assert set(entry.keys()) == LESSON_QUEUE_ITEM_KEYS

        # `_direction_to_dict` OMITS `left` when None; a plain `response_model=`
        # would put `"left": null` back in — a payload rewrite in the ADD
        # direction, which is exactly what `response_model_exclude_unset=True`
        # exists to prevent here. Pin BOTH branches, or the flag is unpinned and
        # stripping it leaves this file green.
        banka = next(q for q in data["queue"] if q["text"] == "banka")
        assert set(banka["directions"]["recognition"].keys()) == DIRECTION_KEYS, (
            "a LEARNING direction must carry `left`"
        )
        center = next(q for q in data["queue"] if q["text"] == "center")
        assert set(center["directions"]["recognition"].keys()) == DIRECTION_WITHOUT_LEFT, (
            "a REVIEW direction must not carry a `left` key"
        )

    async def test_lesson_queue_model_fields_match_the_literal(self):
        """Keeps the models honest against the same hand-written literals."""
        from app.api.models import LessonQueueItemResponse, LessonReviewQueueResponse
        from tests._helpers.srs_item_shape import LESSON_QUEUE_ITEM_KEYS

        assert set(LessonQueueItemResponse.model_fields) == LESSON_QUEUE_ITEM_KEYS
        assert set(LessonReviewQueueResponse.model_fields) == {"queue", "has_unreviewed_listen"}

    async def test_review_touched_today_excluded_without_a_pending_row(self):
        """Regression (om / vite, 2026-07-27). A card confirmed in the listen
        preview is applied immediately and holds no pending row; "touched today"
        used to re-admit it, asking the same question the user just answered."""
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

        assert resp.json()["queue"] == []

    async def test_review_due_excluded_without_a_pending_row(self):
        """Regression (noe / fra, 2026-07-27). Genuinely due, but never
        autograded — a listen stages RECOGNITION only and skips cloze rows, so
        these belong in the main review queue, not the correction pass."""
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

        assert resp.json()["queue"] == []

    async def test_a_suspended_card_with_a_pending_row_is_still_served(self):
        """Suspending between the listen and the correction pass must not strand
        the staged grade: ``commit-pending`` would release it regardless, so
        dropping it here would reopen the queue-vs-release-set divergence."""
        db = self._setup(self._lesson(["banka"]))
        self._track(db, "banka")
        self._set_dir(db, "banka", "recognition", "review", due_at="2026-01-01T04:00:00+00:00")
        self._stage(db, "banka")
        self._set_dir(db, "banka", "recognition", "suspended")

        resp = await self._get_queue()

        assert [i["text"] for i in resp.json()["queue"]] == ["banka"]

    async def test_bucket_order_learning_then_review(self):
        db = self._setup(self._lesson(["hotel center"]))
        self._track(db, "hotel")
        self._set_dir(db, "hotel", "recognition", "learning")
        self._stage(db, "hotel", grade_class="learning")
        self._track(db, "center")
        self._set_dir(
            db,
            "center",
            "recognition",
            "review",
            due_at="2026-01-01T04:00:00+00:00",
            last_review="2026-01-01T00:00:00+00:00",
        )
        self._stage(db, "center")

        resp = await self._get_queue()

        assert [i["text"] for i in resp.json()["queue"]] == ["hotel", "center"]

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

        # Stage BEFORE freezing: a pending row removes the card from the main
        # queue (Layer 81), so the frozen order must already account for it or
        # the comparison below would be measuring that exclusion, not this
        # endpoint's read-only-ness.
        self._stage(db, "banka")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            frozen = await client.get("/api/srs/review-queue?session_start=1")
        order_before = [(i["id"], i["direction"]) for i in frozen.json()["queue"]]
        cutoff_before = db.get_anki_state_cache("learning_cutoff")
        cache_before = db.get_anki_state_cache("session_main_queue")

        resp = await self._get_queue()
        assert resp.status_code == 200
        assert [i["text"] for i in resp.json()["queue"]] == ["banka"]

        assert db.get_anki_state_cache("learning_cutoff") == cutoff_before
        assert db.get_anki_state_cache("session_main_queue") == cache_before
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            after = await client.get("/api/srs/review-queue")
        assert [(i["id"], i["direction"]) for i in after.json()["queue"]] == order_before

    async def test_again_after_auto_good_is_normal_lapse(self):
        """Listen stages a REVIEW card; grading it Again from the lesson queue
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
            listen = await client.post("/api/srs/listen", json={"content_id": "lesson-1"})
        assert listen.json()["staged"] == 1

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

    async def test_grading_in_the_correction_pass_drops_the_card(self):
        """Regression (card 3005 'fra', reps=7): grading a card in the correction
        pass must leave the queue, or grading Good re-serves it forever. The
        mechanism is now the pending row itself — release clears it — rather than
        a separate "graded since the arming listen" timestamp filter."""
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
            listen = await client.post("/api/srs/listen", json={"content_id": "lesson-1"})
        assert listen.json()["staged"] == 1

        resp = await self._get_queue()
        queue = resp.json()["queue"]
        assert [i["text"] for i in queue] == ["banka"]
        item_id = queue[0]["id"]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                f"/api/srs/items/{item_id}/direction/recognition/feedback",
                json={"rating": "good"},
            )

        assert db.get_pending_grade(item_id, "recognition") is None
        resp2 = await self._get_queue()
        assert [i["text"] for i in resp2.json()["queue"]] == []

    async def test_card_without_served_direction_excluded(self):
        """Single-template rows (no recognition direction after v15→v16) can't be
        served recognition — excluded rather than crashing the page."""
        db = self._setup(self._lesson(["banka"]))
        self._track(db, "banka")
        self._stage(db, "banka")
        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE direction='recognition'"
                " AND collocation_id=(SELECT id FROM collocations WHERE text='banka')"
            )
            conn.commit()

        resp = await self._get_queue()

        assert resp.status_code == 200
        assert resp.json()["queue"] == []


class TestMarkLessonReviewed:
    """POST /api/srs/content/{lesson_id}/reviewed — records a review row for
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
            resp = await client.post("/api/srs/content/no-such-lesson/reviewed")
            assert resp.status_code == 404

    async def test_200_and_row_recorded(self):
        db = self._setup()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/content/lesson-1/reviewed")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}
        assert db.latest_review_at("lesson-1") is not None

    async def test_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (bp-ledger-burndown stage 3)."""
        self._setup()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/content/lesson-1/reviewed")
        assert set(resp.json().keys()) == {"ok"}
        assert set(MarkLessonReviewedResponse.model_fields) == {"ok"}
