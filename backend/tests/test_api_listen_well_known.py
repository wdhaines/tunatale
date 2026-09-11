"""Item 2: well-known word suppression in the listen preview.

Tests the well_known flag and the commit-side parity: well-known rows
are returned but unchecked, and the server skips staging them unless the
client sends an explicit rating.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/content/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"


def _setup(phrases: list[str], language_code: str = "sl", key_phrases: list[str] | None = None):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    kp_list = [KeyPhraseInfo(phrase=kp, translation=f"t-{kp}") for kp in (key_phrases or [])]
    lesson = Lesson(
        title="Day 1",
        language_code=language_code,
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text=t, voice_id="female-1", language_code=language_code, role="female-1") for t in phrases
                ],
            )
        ],
        key_phrases=kp_list,
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


def _seed_review(db, text: str, *, stability: float = 10.0, days_until_due: int = 5) -> None:
    """Seed a REVIEW card with the given due offset from today."""
    from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

    unit = SyntacticUnit(text=text, translation=f"t-{text}", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.stability = stability
    rec.due_at = due_at_rollover_utc(anki_today() + timedelta(days=days_until_due))
    rec.last_review = datetime.now(UTC) - timedelta(days=5)
    rec.reps = 5
    db.update_collocation(item)


async def _get_preview() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()


async def _post_listen(payload: dict) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(LISTEN_URL, json=payload)
    assert resp.status_code == 200
    return resp.json()


class TestWellKnownIsStability:
    """Well known = the memory holds 180+ days (stability), not a far due date.

    Changed from a 365-day due-date horizon on 2026-09-10 (bd tunatale-yh47).
    Both cards below are "ahead" (not due), so only the rule separates them.
    """

    async def test_strong_memory_due_soon_is_well_known_and_weak_one_due_far_is_not(self):
        db = _setup(["anna boris"])
        _seed_review(db, "anna", stability=200, days_until_due=30)
        _seed_review(db, "boris", stability=100, days_until_due=400)

        preview = await _get_preview()
        by_text = {c["text"]: c for c in preview["candidates"]}
        assert by_text["anna"]["well_known"] is True
        assert by_text["boris"]["well_known"] is False

    async def test_boundary_is_180_days_inclusive(self):
        db = _setup(["anna boris"])
        _seed_review(db, "anna", stability=179.9, days_until_due=30)
        _seed_review(db, "boris", stability=180.0, days_until_due=30)

        preview = await _get_preview()
        by_text = {c["text"]: c for c in preview["candidates"]}
        assert by_text["anna"]["well_known"] is False
        assert by_text["boris"]["well_known"] is True


class TestMarkedKnownPostSyncShape:
    """Seed the exact post-sync shape of a marked-known card:
    state='review', due_at = today + 36500d, stability=79250, reps=0.
    Must be well_known=true."""

    async def test_post_sync_known_is_well_known(self):
        db = _setup(["anna"])
        from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

        unit = SyntacticUnit(text="anna", translation="t-anna", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("anna")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.stability = 79250
        rec.due_at = due_at_rollover_utc(anki_today() + timedelta(days=36500))
        rec.reps = 0
        db.update_collocation(item)

        preview = await _get_preview()
        assert len(preview["candidates"]) == 1
        assert preview["candidates"][0]["well_known"] is True


class TestDueCardNotWellKnown:
    """A due card (due_at <= today) with high stability is NOT well-known."""

    async def test_due_card_with_high_stability(self):
        db = _setup(["anna"])
        _seed_review(db, "anna", stability=500, days_until_due=-1)

        preview = await _get_preview()
        assert len(preview["candidates"]) == 1
        assert preview["candidates"][0]["grade_class"] == "due"
        assert preview["candidates"][0]["well_known"] is False


class TestWellKnownCommitParity:
    """POST the listen with word_ratings={} and assert the well-known lemma
    got NO pending row while the ordinary one did. Then POST with an explicit
    rating for the well-known lemma and assert it IS staged."""

    async def test_well_known_skipped_by_default(self):
        db = _setup(["anna boris"])
        _seed_review(db, "anna", stability=300, days_until_due=30)
        _seed_review(db, "boris", stability=10, days_until_due=1)

        result = await _post_listen({"content_id": "lesson-1", "word_ratings": {}})
        # Both are "ahead". boris (10 d) is staged; anna (300 d) is well known
        # and absent from word_ratings, so it is skipped.
        assert result["staged"] == 1

    async def test_well_known_staged_with_explicit_rating(self):
        db = _setup(["anna boris"])
        _seed_review(db, "anna", stability=300, days_until_due=30)
        _seed_review(db, "boris", stability=10, days_until_due=1)

        result = await _post_listen(
            {
                "content_id": "lesson-1",
                "word_ratings": {"anna": "good"},
            }
        )
        # Both should be staged now
        assert result["staged"] == 2


class TestKPIgnoredInPreviewAndCommit:
    """Key phrases that are ignored are excluded from both preview and commit."""

    async def test_ignored_kp_absent_from_preview(self):
        _setup(["anna"], key_phrases=["dober dan"])
        db = app.state.srs_db
        db.add_ignored_lemma("sl", "dober dan")

        preview = await _get_preview()
        texts = {c["text"] for c in preview["candidates"]}
        assert "dober dan" not in texts

    async def test_ignored_kp_not_staged_by_commit(self):
        _setup(["anna"], key_phrases=["dober dan"])
        db = app.state.srs_db
        db.add_ignored_lemma("sl", "dober dan")

        result = await _post_listen({"content_id": "lesson-1", "word_ratings": {}})
        assert result["created"] == 1

    @staticmethod
    def _seed_known_kp(stability: float) -> None:
        _setup(["anna"], key_phrases=["zdravo"])
        db = app.state.srs_db
        unit = SyntacticUnit(text="zdravo", translation="t-zdravo", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("zdravo")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.stability = stability
        rec.due_at = due_at_rollover_utc(anki_today() + timedelta(days=30))
        rec.last_review = datetime.now(UTC) - timedelta(days=5)
        rec.reps = 5
        db.update_collocation(item)

    async def test_well_known_kp_skipped_in_commit(self):
        # zdravo (300 d) is well known and absent from kp_ratings -> not staged.
        # The control below differs ONLY in stability, so a pass here means the
        # rule skipped it, not that key phrases are never staged.
        self._seed_known_kp(300.0)
        skipped = await _post_listen({"content_id": "lesson-1", "word_ratings": {}})
        self._seed_known_kp(10.0)
        staged = await _post_listen({"content_id": "lesson-1", "word_ratings": {}})
        assert skipped["created"] == staged["created"] == 1  # anna, both times
        assert staged["staged"] == skipped["staged"] + 1
