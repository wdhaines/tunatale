"""B2: `word_ratings`/`kp_ratings` values must be validated at the request
boundary (a `Literal` domain), not stored as free-form strings.

Without this, an out-of-domain rating string sails through `POST
/api/srs/listen` (which just stores it verbatim in `pending_listen_grades`),
and later blows up `commit_pending_grades`'s `_WORD_RATING_MAP[rating]` lookup
with an unhandled `KeyError` -> HTTP 500 that persists across restarts (the
poison row is never cleared) and blocks every other pending row in that
lesson's batch.

Kept in its own file: `test_api_listen_pending.py` is Stage-2
orchestrator-authored and pinned "may not be weakened, deleted, or shadowed"
— this adds coverage rather than touching it.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

LISTEN_URL = "/api/srs/listen"


def _lesson(phrases: list[str], key_phrases=None, language_code: str = "sl") -> Lesson:
    return Lesson(
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
        key_phrases=key_phrases or [],
    )


def _setup(lesson: Lesson):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    db.set_anki_state_cache("daily_new_cap", "0")
    return db


class TestRatingValidation:
    async def test_bogus_word_rating_is_422(self):
        _setup(_lesson(["banka riba"]))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                LISTEN_URL,
                json={"content_id": "lesson-1", "word_ratings": {"banka": "bogus"}},
            )
        assert resp.status_code == 422

    async def test_bogus_kp_rating_is_422(self):
        _setup(
            _lesson(
                ["banka riba"],
                key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
            )
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                LISTEN_URL,
                json={"content_id": "lesson-1", "kp_ratings": {"dober dan": "bogus"}},
            )
        assert resp.status_code == 422

    async def test_full_valid_domain_still_succeeds(self):
        _setup(
            _lesson(
                ["banka riba mesto hotel kava"],
                key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
            )
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                LISTEN_URL,
                json={
                    "content_id": "lesson-1",
                    "word_ratings": {
                        "banka": "again",
                        "riba": "hard",
                        "mesto": "good",
                        "hotel": "easy",
                        "kava": "skip",
                    },
                    "kp_ratings": {"dober dan": "again"},
                },
            )
        assert resp.status_code == 200
