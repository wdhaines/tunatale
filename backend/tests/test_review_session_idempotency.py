"""One user intent must not become two review sessions.

bd tunatale-rwkz.1. On 2026-09-19 a single pasted story became two sessions on
prod: two POST /api/review-sessions/import calls ~75 s apart, each minting an id
and each starting a render, on a box that could not afford one render let alone
two. Neither request has a uvicorn access line, because uvicorn omits one when
the client has already disconnected — so from the server's side a lost request
and an absent request look identical.

⚠️ THE CLIENT GUARD IS NOT THE FIX, and that is the whole reason this file
exists. ``ManualStoryPanel`` already disables the button while the request is in
flight; it re-enables in its ``finally`` when the fetch FAILS on the network,
which is exactly what happened. The two requests were 75 s apart — far too slow
for a double tap on a disabled button — and the user could not say whether they
tapped twice, because a browser-level POST retry on a dropped mobile connection
produces the identical server-side trace. So the guarantee has to hold when the
second request is not a tap at all, which means it has to hold on the server.

The key is a HEADER rather than a body field for two reasons, both load-bearing:
``CreateReviewSessionRequest`` is ``extra="forbid"`` (a body field would have to
punch a hole in the interface that carries that decision), and a browser resends
headers verbatim on an automatic retry, which is the case a body field written by
the click handler would miss.

⚠️ WHAT IS DELIBERATELY NOT MEMOISED: failures. A first attempt that 429s or
502s must leave the key free, or a learner whose generation failed could never
retry it — they would be handed the failure forever. ``test_a_failed_attempt_
leaves_the_key_free`` is that guard, and it is the one that makes the difference
between "idempotent" and "stuck".
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.idempotency import _TTL_SECONDS, _Entry, _evict_expired
from app.common.guid import compute_guid
from app.generation.story import StoryGenerator
from app.languages import get_language
from app.llm.client import LLMError
from app.main import app
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

WORDS = ["fisk", "sjøen"]
KEY = "11111111-2222-3333-4444-555555555555"


def _story(title: str = "A Fishing Trip") -> dict:
    return {
        "title": title,
        "key_phrases": [{"phrase": "God dag", "translation": "Good day"}],
        "scenes": [
            {
                "label": "On the quay",
                "lines": [{"speaker": "female-1", "text": "Hei og velkommen om bord!", "translation": "..."}],
            }
        ],
    }


@pytest.fixture
def stored():
    store = ContentStore(":memory:")
    app.state.content_store = store
    app.state.language = get_language("no")
    # The UPOS pass runs inside publish_lesson; this is the sanctioned seam for
    # keeping Stanza out of the default gate (app.api.generation::_injected_lemmatizer).
    app.state.lemmatizer = object()
    return store


@pytest.fixture
def seeded_db():
    with SRSDatabase(":memory:") as db:
        for word in WORDS:
            unit = SyntacticUnit(text=word, translation="gloss", word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="no")
            db.update_direction(
                compute_guid(word, "no", ""),
                Direction.RECOGNITION,
                DirectionState(
                    direction=Direction.RECOGNITION,
                    state=SRSState.REVIEW,
                    due_at=datetime.now(UTC) - timedelta(days=20),
                ),
            )
        app.state.srs_db = db
        yield db


def _generator_returning_a_story():
    client = MagicMock()
    client.complete = AsyncMock(return_value=json.dumps(_story()))
    client.last_finish_reason = "stop"
    return StoryGenerator(llm_client=client)


async def _import(key: str | None = None, *, title: str = "A Fishing Trip"):
    headers = {"X-TT-Language": "no"}
    if key is not None:
        headers["Idempotency-Key"] = key
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/api/review-sessions/import",
            json={"story": _story(title), "review_words": WORDS},
            headers=headers,
        )


async def _create(key: str | None = None):
    headers = {"X-TT-Language": "no"}
    if key is not None:
        headers["Idempotency-Key"] = key
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/review-sessions", json={}, headers=headers)


def _session_ids(store) -> list[str]:
    return [row["id"] for row in store.list_review_sessions("no")]


class TestTheImportPath:
    async def test_the_same_key_twice_makes_one_session(self, stored, seeded_db):
        """The 2026-09-19 shape exactly: the same paste, sent twice, ~75 s apart."""
        first = await _import(KEY)
        second = await _import(KEY)

        assert first.status_code == 201
        assert second.status_code == 201
        assert second.json()["id"] == first.json()["id"]
        assert _session_ids(stored) == [first.json()["id"]]

    async def test_a_concurrent_duplicate_joins_the_first(self, stored, seeded_db):
        """The retry that arrives while the first is STILL RUNNING.

        This is the case a "was it already written?" check cannot catch: on
        2026-09-19 the second request arrived ~75 s before the first had written
        anything. Both callers must come back with the same session.
        """
        first, second = await asyncio.gather(_import(KEY), _import(KEY))

        assert {first.status_code, second.status_code} == {201}
        assert first.json()["id"] == second.json()["id"]
        assert len(_session_ids(stored)) == 1

    async def test_different_keys_make_different_sessions(self, stored, seeded_db):
        """Deliberately importing a second story is not a duplicate."""
        first = await _import("key-one")
        second = await _import("key-two", title="A Second Session")

        assert first.json()["id"] != second.json()["id"]
        assert len(_session_ids(stored)) == 2

    async def test_without_a_key_nothing_is_deduplicated(self, stored, seeded_db):
        """The header is OPTIONAL, and its absence must not silently collapse
        two genuine imports into one — a caller that sends no key is asking for
        the old behaviour and gets it."""
        first = await _import()
        second = await _import()

        assert first.json()["id"] != second.json()["id"]
        assert len(_session_ids(stored)) == 2


class TestTheGeneratePath:
    async def test_the_same_key_twice_makes_one_session(self, stored, seeded_db):
        """Generate mints a fresh story per call, so a lost response is worse
        here than on import: the retry would burn a second story call — the most
        expensive thing TT does — for a session the learner did not ask for."""
        app.state.story_generator = _generator_returning_a_story()

        first = await _create(KEY)
        second = await _create(KEY)

        assert second.json()["id"] == first.json()["id"]
        assert len(_session_ids(stored)) == 1

    async def test_a_failed_attempt_leaves_the_key_free(self, stored, seeded_db):
        """⚠️ A memoised FAILURE would wedge the button forever.

        The first attempt dies the way prod dies (an upstream refusal); the
        learner taps again with the same key, because from the client's side
        nothing has changed. That second attempt must really run.
        """
        failing = MagicMock()
        # The real prod shape: every Groq attempt 429s and the client gives up.
        # A bare RuntimeError would NOT do — it escapes as an unhandled 500
        # instead of the 502 the handler maps LLMError to, so the test would be
        # asserting against a failure mode this route does not have.
        failing.complete = AsyncMock(side_effect=LLMError("Groq returned 429 Too Many Requests"))
        failing.last_finish_reason = "stop"
        app.state.story_generator = StoryGenerator(llm_client=failing)

        first = await _create(KEY)
        assert first.status_code == 502

        app.state.story_generator = _generator_returning_a_story()
        second = await _create(KEY)

        assert second.status_code == 201
        assert _session_ids(stored) == [second.json()["id"]]

    async def test_the_key_is_scoped_per_route(self, stored, seeded_db):
        """An import and a generation that happen to share a key are two
        different intents; collapsing them would hand the paster a generated
        story, which is the silently-plausible wrong answer this surface is
        prone to."""
        app.state.story_generator = _generator_returning_a_story()

        generated = await _create(KEY)
        imported = await _import(KEY)

        assert generated.json()["id"] != imported.json()["id"]
        assert len(_session_ids(stored)) == 2


class TestTheRegistryDoesNotGrowForever:
    """The TTL sweep, driven directly.

    Through the API this branch is unreachable without either sleeping for the
    TTL or patching the clock inside ``app.`` — which the mock-boundary rule
    forbids and which would be testing the patch. The sweep is a small pure
    function over the registry, so it is checked as one; what the API tests
    above cover is the behaviour it protects.
    """

    async def _finished_task(self):
        task = asyncio.create_task(asyncio.sleep(0))
        await task
        return task

    async def test_a_finished_entry_past_the_ttl_is_dropped(self):
        task = await self._finished_task()
        registry = {("create", "no", KEY): _Entry(task, created=0.0)}

        _evict_expired(registry, now=_TTL_SECONDS + 1)

        assert registry == {}

    async def test_a_finished_entry_inside_the_ttl_is_kept(self):
        """The whole point of the window: a retry arriving a minute later must
        still find the first attempt's result."""
        task = await self._finished_task()
        registry = {("create", "no", KEY): _Entry(task, created=0.0)}

        _evict_expired(registry, now=60.0)

        assert list(registry) == [("create", "no", KEY)]

    async def test_an_unfinished_entry_is_never_dropped(self):
        """⚠️ An in-flight entry is the one a duplicate needs most, so age must
        not evict it. A sweep that dropped it would let the concurrent retry
        start a second generation — the exact bug this module prevents."""
        slow = asyncio.create_task(asyncio.sleep(3600))
        registry = {("create", "no", KEY): _Entry(slow, created=0.0)}
        try:
            _evict_expired(registry, now=_TTL_SECONDS * 10)

            assert list(registry) == [("create", "no", KEY)]
        finally:
            slow.cancel()
