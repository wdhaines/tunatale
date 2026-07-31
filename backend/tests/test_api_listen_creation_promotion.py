"""The server-side half of the creation-tail contract: skipping a shown create
row promotes the FIRST tail row, deterministically.

Orchestrator-authored for `docs/briefs/bp-listen-preview-creation-budget-ux-2026-07.md`
(Option B). ⚠️ DO NOT EDIT while implementing that brief — this is the oracle,
not a test of your implementation. If an assertion looks wrong, STOP and report.

Why this test carries weight beyond its own assertions: the modal derives the
divider CLIENT-side. It shows "the first N still-checked create rows, in the
order the server sent them" and never tells the server which lemmas to create.
That derivation is only honest if the server, re-ranking over the reduced
candidate set, lands on exactly the same rows. It does, because
`_rank_listen_candidates` sorts with a STABLE sort keyed on `-occurrences`:
removing one element never reorders the remainder. This file pins that property
at the API level, so a future change to the ranking (e.g. the frequency-ranking
brief) cannot silently invalidate the frontend without going red here.

The companion frontend test is
`frontend/src/routes/c/[curriculumId]/l/[lessonId]/ListenPreviewModal.creationTail.test.ts`.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/lesson/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"

# occurrences: banka=3, kava=2, hotel=1, center=1, mesto=1.
# First-appearance order: hotel, kava, banka, center, mesto.
# Stable sort on -occurrences therefore ranks:
#     banka, kava, hotel, center, mesto
# With a cap of 2: live = [banka, kava], tail = [hotel, center, mesto].
_SENTENCE = "hotel kava banka kava banka banka center mesto"
_EXPECTED_RANK = ["banka", "kava", "hotel", "center", "mesto"]


def _setup(language_code: str = "sl"):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code=language_code,
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(
                        text=_SENTENCE,
                        voice_id="female-1",
                        language_code=language_code,
                        role="female-1",
                    )
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


def _created(db, lemmas: list[str]) -> set[str]:
    return {lem for lem in lemmas if db.get_collocation_by_lemma(lem) is not None}


class TestSkippedCreatePromotesTheFirstTailRow:
    async def test_ranking_is_the_order_the_preview_disclosed(self):
        """Baseline the whole file depends on: the preview's create block is the
        full ranking, live block first."""
        db = _setup()
        db.set_anki_state_cache("daily_new_cap", "2")

        preview = await _get_preview()
        creates = [c for c in preview["candidates"] if c["kind"] == "create"]

        assert [c["text"] for c in creates] == _EXPECTED_RANK
        assert [c["text"] for c in creates if c["will_create"]] == ["banka", "kava"]
        assert [c["text"] for c in creates if not c["will_create"]] == ["hotel", "center", "mesto"]

    async def test_skipping_one_live_row_creates_the_first_tail_row_instead(self):
        """The residual the brief exists to disclose, pinned as behavior: the
        budget is spent by RANK, so a skipped live row frees its slot to the
        next-ranked lemma — the first row of the disclosed tail, never an
        arbitrary one."""
        db = _setup()
        db.set_anki_state_cache("daily_new_cap", "2")

        listen = await _post_listen({"lesson_id": "lesson-1", "word_ratings": {"banka": "skip"}})

        assert listen["created"] == 2
        # kava stays (it was live and untouched); hotel is promoted because it
        # led the tail. center and mesto, further down the tail, are not.
        assert _created(db, _EXPECTED_RANK) == {"kava", "hotel"}

    async def test_skipping_every_live_row_promotes_the_next_block_in_order(self):
        """Skip All. The freed slots go to the top of the tail, in tail order.

        NOTE: this is today's server behavior surfaced, not introduced. If it
        should instead CONSUME the slot (skip meaning "create nothing"), that is
        a separate decision the user has not made — do not change it here.
        """
        db = _setup()
        db.set_anki_state_cache("daily_new_cap", "2")

        listen = await _post_listen({"lesson_id": "lesson-1", "word_ratings": {"banka": "skip", "kava": "skip"}})

        assert listen["created"] == 2
        assert _created(db, _EXPECTED_RANK) == {"hotel", "center"}

    async def test_skipping_a_tail_row_changes_nothing(self):
        """A tail row is not actionable, so naming it must be inert: the same
        two live rows are created either way. This is what lets the modal send
        NOTHING for tail rows without changing the outcome."""
        db = _setup()
        db.set_anki_state_cache("daily_new_cap", "2")

        listen = await _post_listen({"lesson_id": "lesson-1", "word_ratings": {"mesto": "skip"}})

        assert listen["created"] == 2
        assert _created(db, _EXPECTED_RANK) == {"banka", "kava"}
