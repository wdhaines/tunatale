"""Manual mode at CREATE time — a session written by hand, never generated.

bd tunatale-jmwb. Manual mode already existed for sessions (tunatale-w1i3) but was
id-scoped, so it could only ever be a REWRITE: both `/{id}/prompt` and
`/{id}/import` need a session that exists, and the only way to make one always
called the LLM. The learner's objection, verbatim: "I don't like having to do auto
mode, then rewrite with Claude in the lesson page." The wasted story call is the
concrete cost — it is the most expensive thing TT does (cf. tunatale-yet7).

⚠️ STATELESS, NOT A DRAFT ROW. The rejected alternative was to mint a story-less
session at click time and reuse the id-scoped routes. That invents a persistent
state — a session with no story — that the list, detail and render surfaces have
never had to draw, and orphans one every time a learner asks for a prompt and
never pastes. Carrying the pinned word list in the CLIENT between the two calls is
exactly what "import paths pin, generation paths select" already means, and
``build_review_session_prompts(..., review_words=...)`` already supports pinning,
so nothing is duplicated to get it.

⚠️ THE ROUND TRIP IS THE POINT. `/prompt` selects and RETURNS the words; `/import`
is told them. If import re-selected, a learner who wrote their dialogue overnight
would have it scored against a set that had since decayed differently — the meter
reading low for a reason nothing on screen explains. That is the same trap
``test_it_keeps_the_original_request_rather_than_reselecting`` guards on the
rewrite path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.guid import compute_guid
from app.generation.story import StoryGenerator
from app.languages import get_language
from app.main import app
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

WORDS = ["zapadla_beseda", "druga_beseda"]


def _story(*lines: str, title: str = "A Hand-Written Session") -> dict:
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
    return store


@pytest.fixture
def seeded_db():
    """Two overdue RECOGNITION cards, so a real selection has something to return."""
    with SRSDatabase(":memory:") as db:
        for word in WORDS:
            unit = SyntacticUnit(text=word, translation="gloss", word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            db.update_direction(
                compute_guid(word, "sl", ""),
                Direction.RECOGNITION,
                DirectionState(
                    direction=Direction.RECOGNITION,
                    state=SRSState.REVIEW,
                    due_at=datetime.now(UTC) - timedelta(days=20),
                ),
            )
        app.state.srs_db = db
        yield db


async def _get(url: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url)


async def _post(url: str, payload: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(url, json=payload)


class TestTheDraftPrompt:
    async def test_it_selects_and_returns_the_words_it_asked_for(self, stored, seeded_db):
        """The returned list is the contract: import is told these, not asked to guess."""
        resp = await _get("/api/review-sessions/prompt")

        assert resp.status_code == 200
        body = resp.json()
        assert set(body["review_words"]) == set(WORDS)
        for word in WORDS:
            assert word in body["user_prompt"]
        assert body["system_prompt"]

    async def test_it_is_not_shadowed_by_the_session_detail_route(self, stored, seeded_db):
        """⚠️ REGRESSION GUARD FOR ROUTE ORDER, not a duplicate of the test above.

        `GET /{session_id}` is declared in the same router and would happily match
        "prompt" as a session id, answering 404. FastAPI resolves in declaration
        order, so this route only works while it is declared FIRST — an ordinary
        tidy-up that sorts the routes would silently break manual mode. The 404
        this pins against is indistinguishable from "no session called prompt".
        """
        resp = await _get("/api/review-sessions/prompt")
        assert resp.status_code != 404

    async def test_nothing_due_refuses_rather_than_offering_an_empty_prompt(self, stored):
        """409, the same refusal auto-create gives. A REVIEW prompt with no words
        is not a smaller prompt, it is a prompt with no content at all."""
        with SRSDatabase(":memory:") as empty:
            app.state.srs_db = empty
            resp = await _get("/api/review-sessions/prompt")
        assert resp.status_code == 409

    async def test_it_calls_no_llm(self, stored, seeded_db):
        """The whole point of the bead: a prompt must cost no generation.

        Asserted against a real double rather than by reading the handler, because
        "manual mode still burns a story call" is precisely the bug this route
        exists to remove and it would be invisible in the response.
        """
        spy = MagicMock()
        spy.complete = AsyncMock(return_value="{}")
        app.state.llm = spy
        app.state.story_generator = StoryGenerator(llm_client=spy)

        resp = await _get("/api/review-sessions/prompt")

        assert resp.status_code == 200
        assert spy.complete.await_count == 0


class TestCreatingFromAPaste:
    async def test_it_stores_a_session_and_returns_it(self, stored, seeded_db):
        resp = await _post(
            "/api/review-sessions/import",
            {"story": _story("Dober dan!"), "review_words": WORDS},
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["title"] == "A Hand-Written Session"
        assert stored.get_review_session(body["id"]) is not None

    async def test_it_pins_the_request_it_was_given(self, stored, seeded_db):
        """review_requested is what the PROMPT asked for, carried by the client.

        Re-selecting here would score an overnight rewrite against a set that had
        since moved — the meter reading low for a reason nothing on screen explains.
        """
        resp = await _post(
            "/api/review-sessions/import",
            {"story": _story("Dober dan!"), "review_words": WORDS},
        )
        assert set(resp.json()["review_requested"]) == set(WORDS)

    async def test_review_used_is_measured_against_the_pasted_text(self, stored, seeded_db):
        """Recomputed, never echoed back from the request."""
        resp = await _post(
            "/api/review-sessions/import",
            {"story": _story(f"Danes je {WORDS[0]} tukaj."), "review_words": WORDS},
        )
        used = resp.json()["review_used"]
        assert WORDS[0] in used
        assert WORDS[1] not in used

    async def test_a_paste_using_neither_word_reports_zero(self, stored, seeded_db):
        """The discriminator: without this, echoing the request would pass above."""
        resp = await _post(
            "/api/review-sessions/import",
            {"story": _story("Nič od tega."), "review_words": WORDS},
        )
        assert resp.json()["review_used"] == []

    async def test_raw_text_is_accepted_like_the_rewrite_route(self, stored, seeded_db):
        import json

        resp = await _post(
            "/api/review-sessions/import",
            {"raw": json.dumps(_story("Dober dan!")), "review_words": WORDS},
        )
        assert resp.status_code == 201

    async def test_malformed_raw_is_422(self, stored, seeded_db):
        resp = await _post(
            "/api/review-sessions/import",
            {"raw": "not json", "review_words": WORDS},
        )
        assert resp.status_code == 422

    async def test_both_story_and_raw_is_refused(self, stored, seeded_db):
        resp = await _post(
            "/api/review-sessions/import",
            {"story": _story("x"), "raw": "{}", "review_words": WORDS},
        )
        assert resp.status_code == 422

    async def test_an_empty_word_list_is_refused(self, stored, seeded_db):
        """A session with no request cannot report coverage, and its prompt route
        would then 409 forever — refuse at the door instead of storing one."""
        resp = await _post(
            "/api/review-sessions/import",
            {"story": _story("Dober dan!"), "review_words": []},
        )
        assert resp.status_code == 422

    async def test_a_malformed_story_is_422_not_500(self, stored, seeded_db):
        """validate_story runs before the build, exactly as the rewrite route does."""
        resp = await _post(
            "/api/review-sessions/import",
            {"story": {"title": "no scenes"}, "review_words": WORDS},
        )
        assert resp.status_code == 422

    async def test_it_works_with_no_srs_database_at_all(self, stored):
        """⚠️ REACHABLE HERE, unlike on the generate path.

        ``_generate_and_store`` can assert ``srs_db is not None`` because reaching
        its UPOS pass proves a selection succeeded. A paste selects nothing — the
        words arrive in the body — so this route genuinely runs with no SRS
        database, and the UPOS tagging and gloss pre-warm must both be skipped
        rather than crashing on None.
        """
        resp = await _post(
            "/api/review-sessions/import",
            {"story": _story("Dober dan!"), "review_words": WORDS},
        )

        assert resp.status_code == 201
        assert stored.get_review_session(resp.json()["id"]) is not None

    async def test_it_appears_in_the_list(self, stored, seeded_db):
        created = await _post(
            "/api/review-sessions/import",
            {"story": _story("Dober dan!"), "review_words": WORDS},
        )
        listing = await _get("/api/review-sessions")
        assert created.json()["id"] in [s["id"] for s in listing.json()["sessions"]]


class TestGlossWarningOnPaste:
    async def test_zero_glosses_warns_no_hover_translations(self, stored, seeded_db):
        from unittest.mock import AsyncMock

        client = MagicMock()
        client.complete = AsyncMock(return_value="not json at all")
        app.state.llm = client

        resp = await _post(
            "/api/review-sessions/import",
            {"story": _story("Dober dan!"), "review_words": WORDS},
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["gloss_entry_count"] == 0
        assert any("no hover translations" in w for w in body["warnings"])


class TestNoGlossWarningWhenGlossesArePresent:
    """The other side of the warning, which had no coverage at all.

    Every other create-from-paste test here posts a story with no
    ``dialogue_glosses``, so all of them take the warning branch. A warning that
    fires unconditionally is worse than none: the reader learns to ignore the
    row. A story that ARRIVES glossed short-circuits ``ensure_dialogue_glosses``,
    which is what makes this reachable without an LLM double.
    """

    async def test_glossed_paste_reports_a_count_and_no_warning(self, stored, seeded_db):
        story = _story("Dober dan!")
        story["dialogue_glosses"] = [
            {"word": "dober", "translation": "good"},
            {"word": "dan", "translation": "day"},
        ]
        app.state.srs_db = seeded_db

        resp = await _post(
            "/api/review-sessions/import",
            {"story": story, "review_words": WORDS},
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["gloss_entry_count"] == 2
        assert not any("no hover translations" in w for w in body["warnings"])
