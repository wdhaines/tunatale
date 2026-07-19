"""Fable-authored guardrail tests: listen/queue must resolve variant cards.

Contract file for `docs/briefs/bp-ui-feedback-2026-07.md` (resolution-drift
item). RED against HEAD: the transcript resolves comma-variant imported cards
('mot, imot' — `transcript.py::_build_variant_index`, registry-driven via
`get_variant_separator`/`card_surface_variants`), but `/listen` and the
lesson review-queue resolve through `_resolve_card_for_lemma`, which only
tries lemma/surface keys → variant cards are never auto-graded and never
served in "Check your work", while the mastery line shows them due forever
(live repro 2026-07-18: 'mot, imot' cid=1651, 'gjennom, igjennom' cid=1746).

BP: move to `backend/tests/test_api_listen_variant_resolution.py` at
activation and implement until green. Do NOT edit these tests — `git diff`
on this file must be empty at delivery.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


def _setup_no_lesson(phrase_text: str):
    """Norwegian lesson (the only language with a variant separator today)."""
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code="no",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text=phrase_text, voice_id="female-1", language_code="no", role="female-1"),
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
    return db


def _seed_variant_card_review_due(db) -> None:
    """Imported-deck shape: comma-variant front, no lemma key, review overdue.

    due_at uses the 04:00-UTC day convention (rollover.py::due_at_rollover_utc)
    — never seed instants (see test_kp_arm_budget_skip's 2026-07-18 fix).
    """
    unit = SyntacticUnit(text="mot, imot", translation="against", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="no")
    item = db.get_collocation("mot, imot")
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.last_review = datetime.now(UTC) - timedelta(days=5)
    rec.due_at = due_at_rollover_utc(anki_today() - timedelta(days=1))
    rec.reps = 5
    db.update_collocation(item)


async def _transcript_resolves_mot(client) -> None:
    """Premise pin: the transcript's variant index DOES resolve 'mot'.

    If THIS assertion fails the seed shape is wrong — fix the test setup,
    not the invariant."""
    resp = await client.get("/api/srs/lesson/lesson-1/transcript")
    assert resp.status_code == 200
    words = [w for line in resp.json()["dialogue_lines"] for w in line["words"]]
    mot = next(w for w in words if w["surface"].lower() == "mot")
    assert mot["srs_item_id"] is not None, "transcript variant resolution broke — test premise invalid"
    assert mot["recognition_state"] == "review"


class TestVariantCardResolutionConservation:
    """One resolver outcome, three surfaces: what the transcript resolves,
    /listen must grade and the lesson review-queue must serve."""

    async def test_listen_grades_variant_card(self):
        db = _setup_no_lesson("Han gikk mot huset")
        _seed_variant_card_review_due(db)
        db.set_anki_state_cache("daily_new_cap", "0")
        db.set_anki_state_cache("daily_review_cap", "10")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _transcript_resolves_mot(client)
            resp = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert resp.status_code == 200

        rec = db.get_collocation("mot, imot").directions[Direction.RECOGNITION]
        assert rec.reps == 6, "listen missed the variant card the transcript shows as due (resolution drift)"

    async def test_lesson_review_queue_serves_variant_card(self):
        db = _setup_no_lesson("Han gikk mot huset")
        _seed_variant_card_review_due(db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _transcript_resolves_mot(client)
            resp = await client.get("/api/srs/lesson/lesson-1/review-queue")
        assert resp.status_code == 200
        texts = [i["text"] for i in resp.json().get("queue", [])]
        assert "mot, imot" in texts, "scoped queue missed the variant card (resolution drift)"

    async def test_listen_does_not_duplicate_variant_card(self):
        """The untracked-word branch must not create a second card for a word
        already tracked by a variant front (creation budget available)."""
        db = _setup_no_lesson("Han gikk mot huset")
        _seed_variant_card_review_due(db)
        db.set_anki_state_cache("daily_new_cap", "10")
        db.set_anki_state_cache("daily_review_cap", "10")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        with db._get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM collocations WHERE text = 'mot' OR lemma = 'mot'").fetchone()[0]
        assert n == 0, "listen created a duplicate card for a variant-tracked word"
