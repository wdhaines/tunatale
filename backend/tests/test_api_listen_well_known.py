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


def _coll_id(db, text: str) -> int:
    """Collocation id for a tracked row's text — the /listen key domain."""
    item = db.get_collocation(text)
    cid = db.get_collocation_id_by_guid(item.guid)
    assert cid is not None, text
    return cid


class TestWellKnownIsDueHorizon:
    """Well known = the next review is WELL_KNOWN_DUE_DAYS_AHEAD days out or more.

    Reverted to a due-date horizon on 2026-09-13 (bd tunatale-38z9), at 90 days
    rather than the 365 the rule carried before 2026-09-10. Stability still
    names the COLOUR band (``direction_band``); it no longer decides what the
    listen preview asks about. The two questions are different: "how well do
    you know this" is a property of the memory, "when will I next see this" is
    a property of the schedule, and only the second should silence a prompt.

    Both cards below are "ahead" (not due), so only the rule separates them —
    and the pair INVERTS against the stability rule, which is what makes this
    a discriminating oracle rather than one that both rules would pass.
    """

    async def test_far_due_is_well_known_and_strong_memory_due_soon_is_not(self):
        db = _setup(["anna boris"])
        _seed_review(db, "anna", stability=200, days_until_due=30)
        _seed_review(db, "boris", stability=100, days_until_due=400)

        preview = await _get_preview()
        by_text = {c["text"]: c for c in preview["candidates"]}
        # Exactly the inverse of the stability rule: anna has the stronger
        # memory but comes up in 30 days, so the preview still asks about it.
        assert by_text["anna"]["well_known"] is False
        assert by_text["boris"]["well_known"] is True

    async def test_boundary_is_90_days_inclusive(self):
        db = _setup(["anna boris"])
        _seed_review(db, "anna", stability=500, days_until_due=89)
        _seed_review(db, "boris", stability=1.0, days_until_due=90)

        preview = await _get_preview()
        by_text = {c["text"]: c for c in preview["candidates"]}
        # Stability is deliberately back-to-front across the boundary so the
        # assertion cannot pass by reading stability instead of the due date.
        assert by_text["anna"]["well_known"] is False
        assert by_text["boris"]["well_known"] is True

    async def test_learning_card_due_far_out_is_never_well_known(self):
        """A card being acquired is never silenced, however far out it is due.

        Carried over from the stability rule, where LEARNING was excluded by
        ``direction_band``. The due-date rule has no band to inherit that from,
        so it needs its own guard — without one, a learning card parked far in
        the future would drop out of the preview and hide work that is owed.
        """
        db = _setup(["anna"])
        _seed_review(db, "anna", stability=1.0, days_until_due=400)
        item = db.get_collocation("anna")
        item.directions[Direction.RECOGNITION].state = SRSState.LEARNING
        db.update_collocation(item)

        preview = await _get_preview()
        assert preview["candidates"][0]["well_known"] is False


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
        _seed_review(db, "anna", stability=300, days_until_due=200)
        _seed_review(db, "boris", stability=10, days_until_due=1)

        result = await _post_listen({"content_id": "lesson-1", "word_ratings": {}})
        # Both are "ahead". boris (due in 1 d) is staged; anna (due in 200 d)
        # is past the horizon and absent from word_ratings, so it is skipped.
        assert result["staged"] == 1

    async def test_well_known_staged_with_explicit_rating(self):
        db = _setup(["anna boris"])
        _seed_review(db, "anna", stability=300, days_until_due=200)
        _seed_review(db, "boris", stability=10, days_until_due=1)

        result = await _post_listen(
            {
                "content_id": "lesson-1",
                "word_ratings": {str(_coll_id(db, "anna")): "good"},
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
    def _seed_known_kp(days_until_due: int) -> None:
        """Seed the key phrase at a given horizon. Stability is held FIXED so
        the pair below differs only in the due date the rule actually reads."""
        _setup(["anna"], key_phrases=["zdravo"])
        db = app.state.srs_db
        unit = SyntacticUnit(text="zdravo", translation="t-zdravo", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("zdravo")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.stability = 50.0
        rec.due_at = due_at_rollover_utc(anki_today() + timedelta(days=days_until_due))
        rec.last_review = datetime.now(UTC) - timedelta(days=5)
        rec.reps = 5
        db.update_collocation(item)

    async def test_well_known_kp_skipped_in_commit(self):
        # zdravo (due in 200 d) is past the horizon and absent from kp_ratings
        # -> not staged. The control below differs ONLY in the due date, so a
        # pass here means the rule skipped it, not that key phrases are never
        # staged.
        self._seed_known_kp(200)
        skipped = await _post_listen({"content_id": "lesson-1", "word_ratings": {}})
        self._seed_known_kp(30)
        staged = await _post_listen({"content_id": "lesson-1", "word_ratings": {}})
        assert skipped["created"] == staged["created"] == 1  # anna, both times
        assert staged["staged"] == skipped["staged"] + 1
