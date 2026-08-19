"""/listen must not fetch media on the request's critical path (tunatale-byw follow-up)."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore


def _lesson() -> Lesson:
    return Lesson(
        title="Day 1",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1")],
            )
        ],
        key_phrases=[],
    )


class TestListenMediaIsOffTheCriticalPath:
    """A lesson's new words each cost a Pixabay round trip plus TTS, serially,
    inside the POST /listen handler. That is the same shape that made peer-sync
    take 46-101s (tunatale-byw): an LLM/image/audio fetch per card, awaited while
    the user waits. tunatale-6xa moved the production-card fetches to a
    BackgroundTask; this pins the same treatment for the /listen mint path.

    THE ORACLE IS ORDERING, NOT PRESENCE. Media still gets created either way —
    Starlette runs background tasks inside the same ASGI cycle, so an
    ASGITransport test observes the rows regardless and 'the media exists' can
    never discriminate. What DOES discriminate is WHEN the fetch runs relative to
    `db.record_listen`, which the handler calls after its creation loop:

        inline (before)  -> fetch runs during the loop, listen NOT yet recorded
        deferred (after) -> fetch runs after the handler body, listen recorded

    So the fake below asks the database whether the listen is already on record
    at the moment it is called. That flips exactly on the change being made.
    """

    async def _run(self, monkeypatch, tmp_path, *, lesson_id: str) -> dict:
        from app.audio import cloze_tts as cloze_tts_mod
        from app.cards.media import vocab_media as vocab_media_mod

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson(lesson_id, "curriculum-1", 1, _lesson())
        app.state.srs_db = db
        app.state.content_store = store

        seen: dict = {"tts_calls": 0, "vocab_calls": 0, "listen_recorded_when_called": None}

        def _note() -> None:
            recorded = any(row.get("lesson_id") == lesson_id for row in db.get_listened_lessons())
            # First observation wins: later calls in the same batch would see the
            # same state, and we care about the first fetch of the request.
            if seen["listen_recorded_when_called"] is None:
                seen["listen_recorded_when_called"] = recorded

        async def _fake_tts(*a, **k):
            seen["tts_calls"] += 1
            _note()
            return b"fake-mp3"

        async def _fake_vocab_media(*a, **k):
            seen["vocab_calls"] += 1
            _note()
            return None

        # Pin cloze_tts's media dir at tmp_path. It is the one _MEDIA_DIR view
        # conftest does NOT pin (tunatale-vnf.4), so unpinned it resolves to the
        # real backend/media — where "Kje je banka?" audio already exists, so
        # synthesis is skipped and generate_tts_audio is never called. That made
        # the call-count oracle read 0 for a reason having nothing to do with
        # this change.
        monkeypatch.setattr(cloze_tts_mod, "_MEDIA_DIR", tmp_path / "media")
        monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)
        monkeypatch.setattr(vocab_media_mod, "generate_vocab_media", _fake_vocab_media)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": lesson_id})
        assert resp.status_code == 200
        return seen

    async def test_media_is_fetched_after_the_handler_body_not_during_it(self, monkeypatch, tmp_path):
        """The first media fetch must see the listen already recorded.

        Inline, the fetch happens inside the creation loop and `record_listen`
        has not run yet, so this reads False. Deferred to a BackgroundTask it
        reads True. Nothing else about the response changes.
        """
        seen = await self._run(monkeypatch, tmp_path, lesson_id="lesson-async-1")

        assert seen["tts_calls"] + seen["vocab_calls"] > 0, "no media work happened; the fixture proves nothing"
        assert seen["listen_recorded_when_called"] is True, (
            "media was fetched while the request was still being served — "
            f"listen_recorded={seen['listen_recorded_when_called']!r}, "
            f"tts={seen['tts_calls']} vocab={seen['vocab_calls']}"
        )

    async def test_the_media_still_gets_created(self, monkeypatch, tmp_path):
        """Deferring must not drop the work — the same fetches still happen.

        Guards the obvious way to make the test above pass: stop fetching.
        """
        seen = await self._run(monkeypatch, tmp_path, lesson_id="lesson-async-2")

        assert seen["tts_calls"] > 0, "cloze audio was never synthesized"
        assert seen["vocab_calls"] > 0, "vocab media was never generated"

    async def test_one_failing_word_does_not_strand_the_rest_of_the_batch(self, monkeypatch, tmp_path):
        """A word whose media fetch raises must not abort the remaining words.

        Inline this mattered less: an exception surfaced in the response and the
        request failed loudly. In a BackgroundTask nothing carries it — Starlette
        has already sent the response — so an unguarded raise would silently drop
        every word queued behind the failing one, and the only symptom would be
        cards that never get pictures.

        ``_generate_add_time_media``'s own docstring says it never raises, so this
        guards a contract violation rather than an expected path. That is exactly
        why it is worth a test: the day that contract breaks, the failure is
        invisible.
        """
        from app.audio import cloze_tts as cloze_tts_mod
        from app.cards.media import vocab_media as vocab_media_mod

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1"),
                        Phrase(text="Kje je hotel?", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )
        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-async-3", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        attempted: list[str] = []

        async def _boom(_db, _coll_id, word, *a, **k):
            attempted.append(word)
            raise RuntimeError("pixabay exploded")

        async def _fake_tts(*a, **k):
            return b"fake-mp3"

        monkeypatch.setattr(cloze_tts_mod, "_MEDIA_DIR", tmp_path / "media")
        monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)
        monkeypatch.setattr(vocab_media_mod, "generate_vocab_media", _boom)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": "lesson-async-3"})

        # The request itself is unaffected — the failure happens after the response.
        assert resp.status_code == 200
        assert len(attempted) >= 2, (
            f"only {attempted!r} was attempted; a raise in the first word stranded the rest of the batch"
        )
