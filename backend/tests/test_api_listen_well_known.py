"""Item 2: well-known word suppression in the listen preview.

Tests the well_known flag and the commit-side parity: well-known rows
are returned but unchecked, and the server skips staging them unless the
client sends an explicit rating.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

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


class TestWellKnownDueDistance:
    """Seed one REVIEW card due 300 days out and one due tomorrow.
    The first has well_known=true, the second false."""

    async def test_far_future_is_well_known(self):
        db = _setup(["anna boris"])
        _seed_review(db, "anna", days_until_due=400)
        _seed_review(db, "boris", days_until_due=1)

        preview = await _get_preview()
        by_text = {c["text"]: c for c in preview["candidates"]}
        assert by_text["anna"]["well_known"] is True
        assert by_text["boris"]["well_known"] is False

    async def test_boundary_at_horizon(self):
        """A card due exactly at the horizon is NOT well-known; horizon+1 is."""
        db = _setup(["anna boris"])
        _seed_review(db, "anna", days_until_due=365)
        _seed_review(db, "boris", days_until_due=366)

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
        _seed_review(db, "anna", days_until_due=400)
        _seed_review(db, "boris", days_until_due=1)

        result = await _post_listen({"content_id": "lesson-1", "word_ratings": {}})
        # boris is "ahead" (due tomorrow) but inside the horizon → staged;
        # anna is "ahead" beyond the horizon → well-known → not staged.
        assert result["staged"] == 1

    async def test_well_known_staged_with_explicit_rating(self):
        db = _setup(["anna boris"])
        _seed_review(db, "anna", days_until_due=400)
        _seed_review(db, "boris", days_until_due=1)

        result = await _post_listen(
            {
                "content_id": "lesson-1",
                "word_ratings": {"anna": "good"},
            }
        )
        # Both should be staged now
        assert result["staged"] == 2


class TestIsDueBeyondHorizonEdgeCases:
    """Direct unit tests for _is_due_beyond_horizon's string-due_at branch.

    ``due_at`` is datetime-or-string depending on load path (the same idiom
    ``_listen_grade_class`` uses), so the string parse is real. ``today`` is
    always ``anki_today()`` — a ``date`` — so there is no ordinal branch to
    test here; don't add one back.
    """

    def test_string_due_at(self):
        from app.api.srs import _is_due_beyond_horizon

        # String due_at parsed via fromisoformat
        assert _is_due_beyond_horizon("2028-01-01T00:00:00", date(2026, 1, 1), 365) is True
        assert _is_due_beyond_horizon("2026-06-01T00:00:00", date(2026, 1, 1), 365) is False

    def test_invalid_string_due_at_returns_false(self):
        from app.api.srs import _is_due_beyond_horizon

        assert _is_due_beyond_horizon("not-a-date", date(2026, 1, 1), 365) is False


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

    async def test_well_known_kp_skipped_in_commit(self):
        _setup(["anna"], key_phrases=["zdravo"])
        db = app.state.srs_db
        # Seed "zdravo" as well-known ahead
        unit = SyntacticUnit(text="zdravo", translation="t-zdravo", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("zdravo")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.due_at = due_at_rollover_utc(anki_today() + timedelta(days=400))
        rec.last_review = datetime.now(UTC) - timedelta(days=5)
        rec.reps = 5
        db.update_collocation(item)

        result = await _post_listen({"content_id": "lesson-1", "word_ratings": {}})
        # zdravo is well-known and not in word_ratings → skipped
        # Only anna is created
        assert result["created"] == 1
