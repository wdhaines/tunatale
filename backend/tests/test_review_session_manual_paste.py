"""Manual Claude-chat mode reaching a review session (bd tunatale-w1i3).

Manual mode shipped addressed by ``{curriculum_id, day}`` — the two things a
review session deliberately does not have. So the panel could not reach the one
surface whose generator is weakest. These two routes are the session-shaped
equivalents of ``GET /api/story/prompt`` and ``POST /api/story/import``.

⚠️ **PASTE PINS THE ORIGINAL REQUEST; REGENERATE RE-SELECTS. Both are right.**
The rule the codebase already follows is *generation paths re-select, import
paths pin*: ``import_lesson`` pins for exactly this reason (tunatale-fgeq.1 —
"recomputing here diverges exactly when time has passed between paste-out and
paste-back"), while ``regenerate_review_session`` re-selects because a session's
claim is that it is about what has decayed NOW. A paste is an import, so it
pins, and ``test_it_keeps_the_original_request_rather_than_reselecting`` is the
guard against someone "fixing" the inconsistency by making them agree.

⚠️ ``review_used`` is RECOMPUTED against the pasted text, never carried. Carrying
it reports the old model's score for the new text — a wrong number that looks
perfectly plausible, which is this epic's characteristic failure. The pair
``test_review_used_is_recomputed_against_the_pasted_text`` and
``test_a_paste_that_drops_words_reports_fewer`` are what discriminate: the first
alone would pass if ``review_used`` were simply copied from the request.

⚠️ The prompt route pins too. If it re-selected, the learner would paste text
written for one word set while coverage was measured against another, and the
meter would read low for a reason nothing on screen could explain.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.guid import compute_guid
from app.languages import get_language
from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

REQUESTED = ["kavo", "vlak", "postaja"]
WORD = "zapadla_beseda"


def _lesson(title: str = "A Missed Train") -> Lesson:
    return Lesson(
        title=title,
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="Dober dan!", voice_id="test-voice", language_code="sl")],
            )
        ],
        generation_metadata={"review_requested": REQUESTED, "review_used": ["kavo", "vlak"]},
    )


def _story(*lines: str, title: str = "A Better Train") -> dict:
    """Valid Story JSON whose dialogue is exactly *lines*.

    Scene ``label`` and line ``speaker`` are required, not decoration: the
    section builders skip an unlabelled scene, and the speaker-warning pass
    indexes ``line["speaker"]`` directly.
    """
    return {
        "title": title,
        "key_phrases": [{"phrase": "Dober dan", "translation": "Good day"}],
        "scenes": [
            {
                "label": "At the station",
                "lines": [{"speaker": "female-1", "text": t, "translation": "..."} for t in lines],
            }
        ],
    }


@pytest.fixture
def stored():
    store = ContentStore(":memory:")
    app.state.content_store = store
    app.state.language = get_language("sl")
    store.save_review_session(
        "sess-1",
        "sl",
        "2026-09-02",
        _lesson(),
        review_requested=REQUESTED,
        review_used=["kavo", "vlak"],
    )
    return store


@pytest.fixture
def seeded_db():
    """One overdue RECOGNITION card, so the deck attached to the paste is real."""
    with SRSDatabase(":memory:") as db:
        unit = SyntacticUnit(text=WORD, translation="gloss", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        db.update_direction(
            compute_guid(WORD, "sl", ""),
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_at=datetime.now(UTC) - timedelta(days=20),
            ),
        )
        yield db


async def _get(url: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url)


async def _post(url: str, payload: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(url, json=payload)


# ── the prompt half ──────────────────────────────────────────────────────────


class TestTheCopyPrompt:
    async def test_it_pins_the_sessions_own_request(self, stored):
        """Every word the session originally asked for reaches the prompt.

        Not a fresh selection: the learner is rewriting THIS session, so the
        text they paste back must target the same words its coverage is
        measured against.
        """
        resp = await _get("/api/review-sessions/sess-1/prompt")

        assert resp.status_code == 200
        body = resp.json()
        for word in REQUESTED:
            assert word in body["user_prompt"]

    async def test_an_unknown_session_is_404(self, stored):
        assert (await _get("/api/review-sessions/nope/prompt")).status_code == 404

    async def test_an_unmeasured_session_cannot_offer_a_prompt(self, stored):
        """A session stored without a request has nothing to ask the model for.

        409 rather than an empty prompt: ``_build_review_prompts`` refuses an
        empty set by design, because a REVIEW prompt with no words is not a
        smaller prompt, it is a prompt with no content at all.
        """
        stored.save_review_session("bare", "sl", "2026-09-02", _lesson())

        assert (await _get("/api/review-sessions/bare/prompt")).status_code == 409


# ── the paste half ───────────────────────────────────────────────────────────


class TestPastingReplacesInPlace:
    async def test_the_id_and_date_survive(self, stored):
        resp = await _post("/api/review-sessions/sess-1/import", {"story": _story("Kavo prosim.")})

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "sess-1"
        assert body["session_date"] == "2026-09-02"

    async def test_the_dated_list_shows_no_duplicate_row(self, stored):
        await _post("/api/review-sessions/sess-1/import", {"story": _story("Kavo prosim.")})

        sessions = (await _get("/api/review-sessions")).json()["sessions"]
        assert [s["id"] for s in sessions] == ["sess-1"]

    async def test_the_text_is_actually_replaced(self, stored):
        await _post("/api/review-sessions/sess-1/import", {"story": _story("Kavo prosim.")})

        body = (await _get("/api/review-sessions/sess-1")).json()
        assert body["title"] == "A Better Train"

    async def test_it_keeps_the_original_request_rather_than_reselecting(self, stored):
        """The denominator is fixed, so "reused N of 3" compares across a re-run.

        This is the deliberate divergence from ``regenerate``, which re-selects.
        Making the two agree would break the comparison this route exists for.
        """
        resp = await _post("/api/review-sessions/sess-1/import", {"story": _story("Kavo prosim.")})

        assert resp.json()["review_requested"] == REQUESTED

    async def test_review_used_is_recomputed_against_the_pasted_text(self, stored):
        """The stored session used kavo+vlak; this paste uses kavo+postaja.

        A carried-over ``review_used`` would still say ["kavo", "vlak"], so this
        discriminates between recomputing and copying.
        """
        resp = await _post(
            "/api/review-sessions/sess-1/import",
            {"story": _story("Kavo prosim.", "Postaja je zaprta.")},
        )

        assert resp.json()["review_used"] == ["kavo", "postaja"]

    async def test_a_paste_that_drops_words_reports_fewer(self, stored):
        """Acceptance 4: a worse paste must read worse, never be rounded up."""
        resp = await _post("/api/review-sessions/sess-1/import", {"story": _story("Kavo prosim.")})

        body = resp.json()
        assert body["review_used"] == ["kavo"]
        assert len(body["review_used"]) < len(body["review_requested"])

    async def test_it_accepts_raw_pasted_text_around_the_json(self, stored):
        """What a chat actually returns: prose, then a fenced JSON block."""
        import json

        raw = "Here you go!\n\n```json\n" + json.dumps(_story("Kavo prosim.")) + "\n```\n"

        resp = await _post("/api/review-sessions/sess-1/import", {"raw": raw})

        assert resp.status_code == 200
        assert resp.json()["review_used"] == ["kavo"]


class TestPasteRefusals:
    async def test_an_unknown_session_is_404(self, stored):
        resp = await _post("/api/review-sessions/nope/import", {"story": _story("Kavo prosim.")})
        assert resp.status_code == 404

    async def test_unparseable_raw_is_422(self, stored):
        assert (await _post("/api/review-sessions/sess-1/import", {"raw": "no json here"})).status_code == 422

    async def test_a_story_with_no_content_is_422(self, stored):
        resp = await _post("/api/review-sessions/sess-1/import", {"story": {"title": "Empty"}})
        assert resp.status_code == 422

    async def test_a_line_missing_its_speaker_is_422_not_500(self, stored):
        """Found while writing these: without ``validate_story`` the speaker
        warning pass indexes ``line["speaker"]`` and dies on a bare KeyError, so
        a malformed paste surfaced as a server error rather than a rejection.
        ``import_lesson`` has always validated first; this route now does too.
        """
        story = {
            "title": "No Speaker",
            "key_phrases": [{"phrase": "Dober dan", "translation": "Good day"}],
            "scenes": [{"label": "At the station", "lines": [{"text": "Kavo prosim.", "translation": "..."}]}],
        }

        resp = await _post("/api/review-sessions/sess-1/import", {"story": story})

        assert resp.status_code == 422

    async def test_supplying_both_story_and_raw_is_422(self, stored):
        resp = await _post(
            "/api/review-sessions/sess-1/import",
            {"story": _story("Kavo prosim."), "raw": "{}"},
        )
        assert resp.status_code == 422

    async def test_supplying_neither_is_422(self, stored):
        assert (await _post("/api/review-sessions/sess-1/import", {})).status_code == 422

    async def test_a_refused_paste_leaves_the_old_session_intact(self, stored):
        """Nothing is written until the rebuild succeeds — the same ordering
        guarantee ``_generate_and_store`` makes for regenerate."""
        await _post("/api/review-sessions/sess-1/import", {"raw": "no json here"})

        body = (await _get("/api/review-sessions/sess-1")).json()
        assert body["title"] == "A Missed Train"


class TestSupersededAudio:
    async def test_the_previous_renders_are_dropped_and_unlinked(self, stored, tmp_path, monkeypatch):
        """Audio rows key on the session id, so a render of the replaced
        dialogue would otherwise still be served for the new one — the player
        would read a script no longer on screen. Regenerate already does this;
        a paste replaces the text just as completely.
        """
        stale = tmp_path / "stale.mp3"
        stale.write_bytes(b"x")
        unlinked: list[str] = []
        monkeypatch.setattr(
            stored,
            "delete_review_session_audio",
            lambda sid, _seen=unlinked: (_seen.append(sid), [str(stale)])[1],
        )

        await _post("/api/review-sessions/sess-1/import", {"story": _story("Kavo prosim.")})

        assert unlinked == ["sess-1"]
        assert not stale.exists()


class TestWithAnSrsDatabaseAttached:
    """The paste path with SRS present — UPOS tagging and gloss pre-warm both run.

    Worth its own class because the no-SRS tests above cannot reach either: a
    session pasted with no deck attached skips both, so without this the tagging
    and pre-warm branches would be carried by nothing.
    """

    async def test_it_still_replaces_in_place(self, stored, seeded_db):
        app.state.srs_db = seeded_db

        resp = await _post("/api/review-sessions/sess-1/import", {"story": _story("Kavo prosim.")})

        assert resp.status_code == 200
        assert resp.json()["id"] == "sess-1"

    async def test_it_does_not_touch_scheduling(self, stored, seeded_db):
        """Out of scope by decision: replacing a session's text must not write
        scheduling state. The pre-warm is a read-through cache fill, not a review.
        """
        guid = compute_guid(WORD, "sl", "")

        def _sched():
            d = seeded_db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
            return (d.state, d.reps, d.due_at)

        before = _sched()
        app.state.srs_db = seeded_db

        await _post("/api/review-sessions/sess-1/import", {"story": _story("Kavo prosim.")})

        assert _sched() == before
