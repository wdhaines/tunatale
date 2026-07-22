"""LOCKED guardrail tests — Fable-authored, DO NOT EDIT (BP: copy verbatim).

Target path when integrating: append the two classes below to
`backend/tests/test_api_lesson_review_queue.py` (they reuse that file's
`TestLessonReviewQueue` setup helpers via a shared base, or copy the small
`_setup`/`_track`/`_set_dir` helpers if you keep this as its own module).

These pin the NEW "one-shot per listen" semantics (floor-shadow class). Your
implementation must satisfy them WITHOUT modifying them. Two anchors:

  1. `app.api.srs._has_unreviewed_listen(latest_listen, latest_review)` — a
     pure helper you MUST define with that exact name/signature. The endpoint
     computes `has_unreviewed_listen` by calling it with
     `db.latest_listen_at(lesson_id)` / `db.latest_review_at(lesson_id)`.
  2. The review-queue response carries `has_unreviewed_listen: bool`, and
     `POST /api/srs/lesson/{id}/reviewed` records a review row.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.srs import _has_unreviewed_listen
from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

_L = "2026-07-21T10:00:00+00:00"  # a listen
_R = "2026-07-21T11:00:00+00:00"  # a review one hour later
_L2 = "2026-07-21T12:00:00+00:00"  # a second listen after the review


class TestHasUnreviewedListenTruthTable:
    """Pure semantics of the gate. String ISO-8601 UTC timestamps compare
    lexicographically. The link arms iff there is a listen strictly newer than
    the last completed review."""

    @pytest.mark.parametrize(
        ("latest_listen", "latest_review", "expected"),
        [
            (None, None, False),  # 1. never listened → never armed
            (_L, None, True),  # 2. listened, never reviewed → armed
            (_L, _R, False),  # 3. reviewed after the listen → disarmed
            (_L2, _R, True),  # 4. listened AGAIN after reviewing → re-armed
            (None, _R, False),  # 5. defensive: a review with no listen → not armed
            (_L, _L, False),  # 6. equal timestamps are NOT "newer" (strict >)
        ],
    )
    def test_truth_table(self, latest_listen, latest_review, expected):
        assert _has_unreviewed_listen(latest_listen, latest_review) is expected


class TestCheckWorkArmingEndToEnd:
    """The gate through the real endpoints — no internal mocks. This is the
    'weird persistence' regression: a non-empty queue must report the link
    DISARMED once the lesson has been reviewed and no newer listen exists."""

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

    async def _get_flag(self, client):
        resp = await client.get("/api/srs/lesson/lesson-1/review-queue")
        assert resp.status_code == 200
        return resp.json()["has_unreviewed_listen"]

    async def test_arms_on_listen_disarms_on_review_rearms_on_relisten(self):
        db = self._setup()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # never listened → disarmed
            assert await self._get_flag(client) is False

            # a listen arms it
            db.record_listen("lesson-1")
            assert await self._get_flag(client) is True

            # completing the review disarms it
            r = await client.post("/api/srs/lesson/lesson-1/reviewed")
            assert r.status_code == 200
            assert await self._get_flag(client) is False

            # a NEW listen re-arms it
            db.record_listen("lesson-1")
            assert await self._get_flag(client) is True

    async def test_reviewed_endpoint_404_unknown_lesson(self):
        self._setup()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/api/srs/lesson/no-such-lesson/reviewed")
            assert r.status_code == 404
