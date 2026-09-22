"""A create row must carry the key the ignore list is matched on (tunatale-qfoa).

The listen preview is getting an Ignore action on its create rows. The ignore
list is matched against the LEMMATIZER's lemma (``lemma.lower() in ignored``, in
both ``get_listen_preview`` and ``mark_lesson_listened``), but a create row's
``text`` is the card HEADWORD (``card_key_by_lemma.get(lemma, lemma)``, bd
tunatale-q5pl). Where the two differ, an Ignore sent with the row's text is
stored, reported as success, and changes nothing — the word keeps being offered
and a listen still creates it.

The fixture is the case where they differ for real: Norwegian ``Snømenn``,
which stanza lemmatizes to the fragment ``snøm`` and the card path re-keys onto
the surface ``snømenn`` (see test_api_listen_preview_lemma_parity.py).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.srs.lemmatizer import TokenAnalysis
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests._helpers.lemmatizer import StubLemmatizer

pytestmark = pytest.mark.anyio

PREVIEW_URL = "/api/srs/content/lesson-no/listen-preview"
SENTENCE = "Snømenn"


def _setup(monkeypatch, language_no):
    import app.api.srs as srs_mod
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    stub = StubLemmatizer()
    stub.set_sentence(
        SENTENCE,
        [TokenAnalysis(surface="Snømenn", lemma="snøm", upos="NOUN", case="", number="Plur", person="", gender="Masc")],
    )
    monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)
    lesson = Lesson(
        title="Day 7",
        language_code="no",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=SENTENCE, voice_id="female-1", language_code="no", role="female-1")],
            )
        ],
        key_phrases=[],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-no", "curriculum-no", 7, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    app.state.language = language_no
    return db


async def _creates() -> list[dict]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return [r for r in resp.json()["candidates"] if r["kind"] == "create"]


async def _ignore(lemma: str) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/srs/ignored-lemmas", json={"lemma": lemma, "language_code": "no"})
    assert resp.status_code == 200


async def _listen() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/srs/listen", json={"content_id": "lesson-no"})
    assert resp.status_code == 200
    return resp.json()


async def test_a_create_row_carries_the_lemma_the_ignore_list_matches(monkeypatch, language_no):
    _setup(monkeypatch, language_no)

    (row,) = await _creates()

    assert row["text"] == "snømenn"  # the headword the card would use
    assert row["lemma"] == "snøm"  # the key the ignore filter tests


async def test_ignoring_by_the_rows_lemma_removes_it_from_preview_and_listen(monkeypatch, language_no):
    """Preview and commit must agree (the 6a5c718 class): both skip it."""
    db = _setup(monkeypatch, language_no)
    (row,) = await _creates()

    await _ignore(row["lemma"])

    assert await _creates() == []
    listen = await _listen()
    assert listen["created"] == 0
    assert db.get_collocation_by_lemma("snømenn") is None


async def test_ignoring_by_the_rows_text_would_not_work(monkeypatch, language_no):
    """The control that makes the field necessary rather than decorative: the
    obvious client-side choice, the row's text, is silently ineffective here."""
    _setup(monkeypatch, language_no)
    (row,) = await _creates()

    await _ignore(row["text"])

    assert [r["text"] for r in await _creates()] == ["snømenn"]


async def test_tracked_rows_carry_no_lemma(monkeypatch, language_no):
    """The ignore list is creation-only: once a card exists the entry goes inert,
    so only create rows get a key to ignore by."""
    from app.models.syntactic_unit import SyntacticUnit

    db = _setup(monkeypatch, language_no)
    db.add_collocation(
        SyntacticUnit(text="snømenn", translation="snowmen", word_count=1, difficulty=1, source="test"),
        language_code="no",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = (await client.get(PREVIEW_URL)).json()

    tracked = [r for r in body["candidates"] if r["kind"] != "create"]
    assert tracked, body
    assert all(r["lemma"] is None for r in tracked)
